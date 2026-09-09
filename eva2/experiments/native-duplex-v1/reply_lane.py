"""Experimental household reply coordination; no listener or provider registration.

The caller authenticates one satellite before constructing a lane. Speech/VAD
and final-transcript callbacks are server-owned. MediaOutput must invalidate
queued and in-flight playback before interrupt returns. This module does not
claim that the existing Pipecat wire protocol fulfils that media contract.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HouseholdBinding:
    satellite_id: str
    device_id: str
    conversation_id: str
    language: str


class Answer(Protocol):
    text: str
    continue_conversation: bool


class NativeClient(Protocol):
    async def respond(self, *, satellite_id: str, device_id: str,
                      conversation_id: str, text: str, language: str) -> Answer: ...


class MediaOutput(Protocol):
    async def speak(self, *, generation: int, text: str) -> None:
        """Return only after actual playback completion, not queue acceptance."""
        ...

    async def interrupt(self, *, generation: int) -> None:
        """Invalidate this generation, stop all of its audio and reject late writes."""
        ...


class HouseholdDuplexReplyLane:
    """One conversation/device; reuse EvaVoiceClient for core request cancellation.

    Generation tokens distinguish stale final transcripts and completions.
    Caller owns VAD, microphone pre-roll, authentication and session timeout.
    No alternate memory, person identity, model or tool execution is created.
    """
    def __init__(self, *, binding: HouseholdBinding, client: NativeClient,
                 output: MediaOutput) -> None:
        self.binding = binding
        self._client = client
        self._output = output
        self._generation = 0
        self._submitted = False
        self._closed = False
        self._transition = asyncio.Lock()
        self._worker: asyncio.Task | None = None
        self._worker_generation = 0
        self._retired: set[asyncio.Task] = set()
        self._failure: BaseException | None = None
        self._closed_event = asyncio.Event()
        self._stops: dict[int, asyncio.Task] = {}

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def closed(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        await self._closed_event.wait()

    def _close_admission(self) -> None:
        self._closed = True
        self._closed_event.set()

    def _record_failure(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = error
        self._close_admission()

    def _ensure_stop(self, generation: int) -> asyncio.Task:
        # Stop ownership outlives a cancelled reply, new-speech callback or
        # close caller. Closed admission is not confirmation of silent output.
        if generation not in self._stops:
            stop = asyncio.create_task(self._output.interrupt(generation=generation))
            self._stops[generation] = stop
            stop.add_done_callback(self._observe_stop)
        return self._stops[generation]

    async def speech_started(self) -> int:
        """Stop old output immediately; do not await slow HTTP stop cleanup."""
        async with self._transition:
            if self._closed:
                raise RuntimeError("duplex lane is closed")
            previous = self._generation
            self._generation += 1
            self._submitted = False
            self._cancel_worker()
            try:
                if previous:
                    await asyncio.shield(self._ensure_stop(previous))
            except BaseException:
                # An unconfirmed speaker stop must not permit a new answer.
                self._close_admission()
                raise
            return self._generation

    async def submit_final(self, *, generation: int, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 16_384:
            raise ValueError("invalid final transcript")
        async with self._transition:
            if self._closed:
                raise RuntimeError("duplex lane is closed")
            if generation != self._generation or generation < 1:
                raise ValueError("stale transcript generation")
            if self._submitted:
                raise ValueError("final transcript already submitted")
            self._submitted = True
            self._worker = asyncio.create_task(self._reply(generation, text))
            self._worker_generation = generation
            self._worker.add_done_callback(lambda task: self._observe_reply(task, generation))

    async def _reply(self, generation: int, text: str) -> None:
        b = self.binding
        answer = await self._client.respond(
            satellite_id=b.satellite_id, device_id=b.device_id,
            conversation_id=b.conversation_id, language=b.language, text=text)
        if self._closed or generation != self._generation:
            return
        try:
            await self._output.speak(generation=generation, text=answer.text)
        except Exception as error:
            # Failed synthesis/transport may already have queued some audio.
            # Close this lane if it is still current; never stop a newer turn.
            if generation == self._generation:
                self._record_failure(error)
            await asyncio.shield(self._ensure_stop(generation))
            raise
        # Continuation policy belongs to the session owner. A late callback
        # must never reopen the microphone or start another model request here.

    def _observe_stop(self, task: asyncio.Task) -> None:
        # Stop failure is fatal even for an old generation: silence is unknown.
        if task.cancelled():
            self._record_failure(RuntimeError("owned speaker stop was cancelled"))
        elif (error := task.exception()) is not None:
            self._record_failure(error)

    def _observe_reply(self, task: asyncio.Task, generation: int) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None and generation == self._generation:
                self._record_failure(error)
        self._retired.discard(task)

    def _cancel_worker(self) -> None:
        if self._worker is not None:
            if not self._worker.done():
                self._retired.add(self._worker)
                self._worker.cancel()
            else:
                # A completed task's scheduled done callback may not have run
                # yet. Closing must expose its failure before returning.
                self._observe_reply(self._worker, self._worker_generation)
            self._worker = None

    async def close(self) -> None:
        async with self._transition:
            self._close_admission()
            self._cancel_worker()
            if self._generation:
                self._ensure_stop(self._generation)
            try:
                # Join even if an earlier caller already closed admission.
                # A cancelled closer must not cancel the owned physical stop.
                await asyncio.gather(*(asyncio.shield(stop)
                                       for stop in self._stops.values()))
            finally:
                # EvaVoiceClient retains bounded request-specific stop cleanup;
                # cancellation does not close a shared client for other rooms.
                await asyncio.gather(*tuple(self._retired), return_exceptions=True)


class SpeechInput(Protocol):
    async def transcribe(
        self, chunks: AsyncIterable[bytes], *,
        on_speech_start: Callable[[], Awaitable[None]],
    ) -> str: ...


async def transcribe_new_turn(
    *, speech_input: SpeechInput, lane: HouseholdDuplexReplyLane,
    chunks: AsyncIterable[bytes],
) -> int:
    """One admitted microphone turn through existing HA STT and Eva HTTP.

    Session owner supplies an authenticated PCM source, keeps the microphone
    alive during playback and serializes listen calls. HA VAD starts the new
    generation before its final transcript. No second STT/VAD provider is used.
    Cancel, missing VAD or failed STT closes the lane and stops prior output.
    """
    generation: int | None = None

    async def speech_started() -> None:
        nonlocal generation
        if generation is None:
            generation = await lane.speech_started()

    transcript = asyncio.create_task(
        speech_input.transcribe(chunks, on_speech_start=speech_started))
    closed = asyncio.create_task(lane.wait_closed())
    try:
        await asyncio.wait((transcript, closed), return_when=asyncio.FIRST_COMPLETED)
        if lane.closed:
            raise RuntimeError("duplex reply failed or lane closed while listening")
        text = await transcript
        if generation is None:
            raise RuntimeError("final transcript has no speech-start binding")
        await lane.submit_final(generation=generation, text=text)
        return generation
    except BaseException:
        if not transcript.done():
            transcript.cancel()
        await asyncio.gather(transcript, return_exceptions=True)
        await lane.close()
        raise
    finally:
        for task in (transcript, closed):
            if not task.done():
                task.cancel()
        await asyncio.gather(transcript, closed, return_exceptions=True)
