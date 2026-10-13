"""Session-owned PCM capture for the authenticated experimental speaker route.

No listener, device activation or model provider lives here. The caller must
open the echo-cancelled microphone only after native wake/consent, and cancel
this session on mute, local stop, disconnect or authority revocation.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable
import math

from eva.adapters.connectors.home_assistant_voice import VoiceMediaError


FRAME_BYTES = 320  # HA 2026.8.1: PCM16 mono 16 kHz, 10 ms per VAD chunk.


class MicrophoneLease:
    """One HA reader; closing it never closes the session's microphone."""

    def __init__(self, owner, start: int):
        self.owner = owner
        self.start = self.cursor = start
        self.end: int | None = None
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        while True:
            changed = self.owner._changed
            self.owner._check()
            if self.closed or self.end is not None:
                raise StopAsyncIteration
            if self.cursor < self.owner._next:
                frame = self.owner._frames[self.cursor]
                self.cursor += 1
                return frame
            await changed.wait()

    async def aclose(self) -> None:
        self.closed = True
        self.owner._notify()


class BufferedMicrophone:
    """Bounded in-memory PCM with exact, audio-relative HA segment boundaries.

    During idle we retain pre-roll only. During STT we retain every offered
    frame, including audio speculatively transmitted beyond HA's VAD boundary.
    That tail is scanned again for the next onset; no previous utterance is
    replayed. Overrun/disconnect/invalid boundaries fail instead of losing words.
    """

    def __init__(self, chunks: AsyncIterable[bytes], *, detector_factory: Callable,
                 confidence: float = .7, speech_frames: int = 12,
                 pre_roll_frames: int = 50, max_frames: int = 12_000):
        if (not 0 < confidence <= 1 or type(speech_frames) is not int
                or not 1 <= speech_frames <= 30
                or type(pre_roll_frames) is not int
                or not speech_frames <= pre_roll_frames <= 100
                or type(max_frames) is not int
                or not pre_roll_frames < max_frames <= 12_000):
            raise ValueError("invalid microphone session bounds")
        self._source = aiter(chunks)
        self._factory = detector_factory
        self._confidence, self._threshold = confidence, speech_frames
        self._pre_roll, self._limit = pre_roll_frames, max_frames
        self._frames: dict[int, bytes] = {}
        self._base = self._next = self._scan_cursor = self._consecutive = 0
        self._active: MicrophoneLease | None = None
        self._onset: MicrophoneLease | None = None
        self._changed = asyncio.Event()
        self._ended = asyncio.Event()
        self._failure: Exception | None = None
        self._closed = False
        self._task: asyncio.Task | None = None
        self._closer: asyncio.Task | None = None
        self._source_closer: asyncio.Task | None = None

    def _notify(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def _check(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise VoiceMediaError("microphone session is closed")

    async def __aenter__(self):
        if self._task is not None or self._closed:
            raise RuntimeError("microphone session cannot be reused")
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def wait_ended(self) -> None:
        await self._ended.wait()
        self._check()

    @property
    def onset_pending(self) -> bool:
        return self._onset is not None

    async def next_utterance(self) -> MicrophoneLease:
        while True:
            changed = self._changed
            self._check()
            if self._onset is not None:
                onset, self._onset = self._onset, None
                return onset
            await changed.wait()

    async def finish(self, lease: MicrophoneLease, timestamp_ms: int) -> None:
        self._check()
        if (lease is not self._active or lease.end is not None
                or type(timestamp_ms) is not int or timestamp_ms < 0
                or timestamp_ms % 10):
            raise VoiceMediaError("invalid microphone segment boundary")
        end = lease.start + timestamp_ms // 10
        # HA reports the start of the first frame NOT passed to the STT engine.
        # It cannot end before local onset or beyond the reader's offered audio.
        if not self._scan_cursor <= end < lease.cursor:
            raise VoiceMediaError("microphone boundary outside offered segment")
        lease.end = end
        self._active = None
        self._discard_before(end)
        self._scan_cursor, self._consecutive = end, 0
        self._detector = self._factory()
        self._notify()

    def _discard_before(self, end: int) -> None:
        while self._base < end:
            del self._frames[self._base]
            self._base += 1

    async def _capture(self) -> None:
        carry = bytearray()
        async for chunk in self._source:
            if (not isinstance(chunk, bytes) or not chunk or len(chunk) % 2
                    or len(chunk) > 65_536):
                raise VoiceMediaError("invalid microphone PCM frame")
            carry.extend(chunk)
            while len(carry) >= FRAME_BYTES:
                if len(self._frames) >= self._limit:
                    raise VoiceMediaError("microphone buffer overrun")
                self._frames[self._next] = bytes(carry[:FRAME_BYTES])
                del carry[:FRAME_BYTES]
                self._next += 1
            self._notify()
            await asyncio.sleep(0)
        # A physical capture closes only through the session's explicit cancel.
        raise VoiceMediaError("microphone disconnected")

    async def _scan(self) -> None:
        while True:
            changed = self._changed
            scanned = 0
            while self._active is None and self._scan_cursor < self._next:
                probability = self._detector.Process10ms(self._frames[self._scan_cursor])
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise VoiceMediaError("invalid speech detection result")
                self._scan_cursor += 1
                self._consecutive = self._consecutive + 1 if probability >= self._confidence else 0
                self._discard_before(max(self._base, self._scan_cursor - self._pre_roll))
                if self._consecutive >= self._threshold:
                    self._active = self._onset = MicrophoneLease(self, self._base)
                    self._notify()
                scanned += 1
                if scanned >= 32:
                    break  # Retained tails must not monopolize the event loop.
            if self._active is None and self._scan_cursor < self._next:
                await asyncio.sleep(0)
            else:
                await changed.wait()

    async def _run(self) -> None:
        try:
            self._detector = self._factory()
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._capture())
                tasks.create_task(self._scan())
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failure = VoiceMediaError("continuous microphone input failed")
        finally:
            # Wake the session before potentially slow device cleanup, so
            # capture failure can stop playback immediately.
            self._ended.set()
            self._notify()
            await self._close_source()

    async def _close_source(self) -> None:
        async def close_owned():
            try:
                close = getattr(self._source, "aclose", None)
                if close is not None:
                    await close()
            except Exception:
                self._failure = VoiceMediaError("microphone cleanup failed")
        if self._source_closer is None:
            self._source_closer = asyncio.create_task(close_owned())
        await asyncio.shield(self._source_closer)

    async def close(self) -> None:
        self._closed = True
        if self._closer is None:
            self._closer = asyncio.create_task(self._close())
        await asyncio.shield(self._closer)

    async def _close(self) -> None:
        if self._task is not None:
            if not self._task.done() and not self._task.cancelling():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        # Also runs if capture was cancelled before its coroutine started.
        await self._close_source()
        self._ended.set()
        self._frames.clear()
        self._onset = self._active = None
        self._notify()
        if self._failure is not None:
            raise self._failure


async def run_conversation(*, microphone: BufferedMicrophone, speech_input, lane,
                           idle_seconds: float = 15, max_turns: int = 128) -> None:
    """Compose active capture, existing HA STT and the existing household lane.

    HA STT starts at local onset, so waiting through an answer never starts an
    idle STT run. Listening times out only before speech or after confirmed
    playback. New onset takes priority over old transcripts and completions.
    """
    if not 0 < idle_seconds <= 60 or type(max_turns) is not int or not 1 <= max_turns <= 512:
        raise ValueError("invalid conversation session bounds")
    tasks: set[asyncio.Task] = set()
    retired: set[asyncio.Task] = set()

    def task(coro):
        result = asyncio.create_task(coro)
        tasks.add(result)
        return result

    def retire(current):
        if current is not None:
            if not current.done() and not current.cancelling():
                current.cancel()
            retired.add(current)

    async def transcribe(lease):
        async def end(timestamp):
            await microphone.finish(lease, timestamp)
        text = await speech_input.transcribe(lease, on_speech_end=end)
        if lease.end is None:
            raise VoiceMediaError("final transcript lacks microphone boundary")
        return text

    try:
        await microphone.__aenter__()
        onset = task(microphone.next_utterance())
        ended = task(microphone.wait_ended())
        closed = task(lane.wait_closed())
        completed = task(lane.playback_completed())
        idle = task(asyncio.sleep(idle_seconds))
        transcription = None
        generation = turns = 0
        while True:
            waiting = [onset, ended, closed, completed]
            if idle is not None:
                waiting.append(idle)
            if transcription is not None:
                waiting.append(transcription)
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            for old in tuple(retired):
                if old.done():
                    await asyncio.gather(old, return_exceptions=True)
                    retired.remove(old)
                    tasks.discard(old)
            if ended in done:
                ended.result()
            if closed in done:
                raise VoiceMediaError("household reply lane closed") from lane.failure
            # Check task state too: onset may arrive as another event wakes us.
            if onset.done() or microphone.onset_pending:
                lease = await onset
                tasks.discard(onset)
                turns += 1
                if turns > max_turns or len(retired) >= 8:
                    raise VoiceMediaError("conversation session bound reached")
                retire(idle)
                idle = None
                retire(transcription)
                generation = await lane.speech_started()
                transcription = task(transcribe(lease))
                onset = task(microphone.next_utterance())
                continue
            if transcription is not None and transcription.done():
                text = transcription.result()
                tasks.discard(transcription)
                transcription = None
                await lane.submit_final(generation=generation, text=text)
            if completed.done():
                result = completed.result()
                tasks.discard(completed)
                completed = task(lane.playback_completed())
                if result.generation == lane.generation:
                    if not result.continue_conversation:
                        return
                    retire(idle)
                    idle = task(asyncio.sleep(idle_seconds))
            if idle is not None and idle.done():
                return
    finally:
        async def cleanup():
            for pending in tasks:
                if not pending.done() and not pending.cancelling():
                    pending.cancel()
            # Stop output before joining slow STT and microphone cleanup.
            try:
                await lane.close()
            finally:
                try:
                    await microphone.close()
                finally:
                    await asyncio.gather(*tasks, return_exceptions=True)
        closing = asyncio.create_task(cleanup())
        # Repeated parent cancellation must not cancel cleanup or return an
        # apparent completed session while owned media tasks are still running.
        cancelled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        closing.result()
        if cancelled:
            raise asyncio.CancelledError
