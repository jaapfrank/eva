"""The deliberately thin bridge from a final voice turn to Eva's core.

Media adapters may call this only after creating the server-bound V01
``VoiceSession`` and a final ``VoiceTurn``.  They do not get a parallel voice
conversation, memory path or tool path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
import time
from typing import Literal, Protocol

from eva.application.conversation import ConversationTurn
from eva.contracts.voice import BargeIn, VoiceSession, VoiceTurn


def admit_final_voice_turn(
    *, session: VoiceSession, turn: VoiceTurn
) -> ConversationTurn:
    """Convert one verified final transcript into the ordinary private turn.

    This performs no persistence and no memory admission.  The normal
    ConversationTurn receiver remains responsible for exactly those steps,
    just as it is for text from any other connector.
    """

    session.validate_final_turn(turn)
    return ConversationTurn.private(
        tenant_id=turn.tenant_id,
        installation_id=turn.installation_id,
        principal_id=turn.principal_id,
        conversation_id=turn.conversation_id,
        message_id=turn.conversation_message_id,
        text=turn.final_transcript,
        occurred_at=turn.finalized_at,
    )


class VoicePlaybackSink(Protocol):
    """The small, replaceable boundary which actually plays audio.

    The sink can be a Wyoming speaker, a browser playback bridge or a later
    provider adapter.  It owns audio bytes transiently; the Living Core owns
    only the session, generation and stop decision.
    """

    async def play(self, audio_chunk: bytes) -> None: ...

    async def stop(self) -> None: ...


class VoiceReplyGenerationCanceller(Protocol):
    """Stop only the local, not-yet-finished reply generation.

    This is deliberately narrower than an action-chain canceller.  A media
    adapter may use it to cancel its model/TTS stream, but it receives neither
    a tool grant nor an effect attempt and therefore cannot undo or repeat an
    external action.
    """

    async def cancel_reply_generation(
        self,
        *,
        session: VoiceSession,
        voice_turn_id: str,
        reply_generation: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class VoicePlaybackReceipt:
    """Non-audio outcome of one answer delivery attempt."""

    status: Literal["completed", "interrupted"]
    delivered_chunk_count: int
    delivered_byte_count: int
    interrupt_to_sink_stop_ms: int | None


class VoicePlaybackController:
    """Deliver one answer stream and fail closed on a bound barge-in.

    This deliberately is not a jitter buffer, speech engine or second
    conversation loop.  It forwards chunks to one injected media sink and
    records no chunk bytes.  A valid barge-in cancels an unfinished sink write
    and then awaits the sink's explicit stop operation.  It cannot cancel an
    Eva tool effect: ``BargeIn.effect_cancel_authorized`` remains false in the
    contract and no action-chain authority enters this class.
    """

    _MAX_CHUNK_BYTES = 512 * 1024

    def __init__(
        self,
        *,
        session: VoiceSession,
        voice_turn_id: str,
        reply_generation: int,
        sink: VoicePlaybackSink,
        reply_canceller: VoiceReplyGenerationCanceller,
    ) -> None:
        if not voice_turn_id or not voice_turn_id.strip():
            raise ValueError("voice turn id must be non-empty")
        if reply_generation < 1:
            raise ValueError("voice reply generation must be positive")
        self._session = session
        self._voice_turn_id = voice_turn_id
        self._reply_generation = reply_generation
        self._sink = sink
        self._reply_canceller = reply_canceller
        self._lock = asyncio.Lock()
        self._state: Literal[
            "ready", "streaming", "completed", "interrupted", "failed"
        ] = "ready"
        self._current_play: asyncio.Task[None] | None = None
        self._delivered_chunk_count = 0
        self._delivered_byte_count = 0
        self._interrupt_to_sink_stop_ms: int | None = None
        self._interruption_finished = asyncio.Event()
        self._interruption_error: RuntimeError | None = None

    async def deliver(self, chunks: AsyncIterable[bytes]) -> VoicePlaybackReceipt:
        """Forward a bounded stream until it ends or a barge-in stops it."""

        async with self._lock:
            if self._state != "ready":
                raise RuntimeError("voice playback may only start once")
            self._state = "streaming"

        try:
            async for chunk in chunks:
                if not await self._play_chunk(chunk):
                    break
        except BaseException:
            async with self._lock:
                if self._state == "streaming":
                    self._state = "failed"
                    self._interruption_finished.set()
            raise

        async with self._lock:
            if self._state == "streaming":
                self._state = "completed"
            interrupted = self._state == "interrupted"
            failed = self._state == "failed"
        if interrupted or failed:
            await self._interruption_finished.wait()
        async with self._lock:
            if self._interruption_error is not None:
                raise self._interruption_error
            if self._state == "completed":
                return VoicePlaybackReceipt(
                    status="completed",
                    delivered_chunk_count=self._delivered_chunk_count,
                    delivered_byte_count=self._delivered_byte_count,
                    interrupt_to_sink_stop_ms=None,
                )
            return VoicePlaybackReceipt(
                status="interrupted",
                delivered_chunk_count=self._delivered_chunk_count,
                delivered_byte_count=self._delivered_byte_count,
                interrupt_to_sink_stop_ms=self._interrupt_to_sink_stop_ms,
            )

    async def interrupt(self, barge_in: BargeIn) -> None:
        """Stop only the matching unfinished playback generation."""

        self._session.validate_barge_in(barge_in)
        if barge_in.interrupted_voice_turn_id != self._voice_turn_id:
            raise ValueError("barge-in targets another voice turn")
        if barge_in.cancel_generation != self._reply_generation:
            raise ValueError("barge-in targets another reply generation")

        started = time.monotonic_ns()
        async with self._lock:
            if self._state != "streaming":
                raise RuntimeError("voice playback is not interruptible")
            self._state = "interrupted"
            current_play = self._current_play
            if current_play is not None and not current_play.done():
                current_play.cancel()
        # Start both cancellation paths before awaiting either one.  Speaker
        # silence must not wait for a slow model provider, and a silently
        # continuing model/TTS stream must never be reported as interrupted.
        sink_stop = asyncio.create_task(self._sink.stop())
        reply_cancel = asyncio.create_task(
            self._reply_canceller.cancel_reply_generation(
                session=self._session,
                voice_turn_id=self._voice_turn_id,
                reply_generation=self._reply_generation,
            )
        )
        sink_error: Exception | None = None
        reply_error: Exception | None = None
        try:
            await sink_stop
        except Exception as error:
            sink_error = error
        sink_stop_ms = (time.monotonic_ns() - started) // 1_000_000
        try:
            await reply_cancel
        except Exception as error:
            reply_error = error

        if sink_error is not None or reply_error is not None:
            failure = RuntimeError(
                "voice playback sink did not stop"
                if sink_error is not None
                else "voice reply generation did not stop"
            )
            async with self._lock:
                self._state = "failed"
                self._interruption_error = failure
                self._interruption_finished.set()
            raise failure from (sink_error or reply_error)
        async with self._lock:
            self._interrupt_to_sink_stop_ms = sink_stop_ms
            self._interruption_finished.set()

    async def _play_chunk(self, chunk: bytes) -> bool:
        if not isinstance(chunk, bytes) or not chunk:
            raise ValueError("voice playback chunk must be non-empty bytes")
        if len(chunk) > self._MAX_CHUNK_BYTES:
            raise ValueError("voice playback chunk exceeds the fixed bound")

        async with self._lock:
            if self._state == "interrupted":
                return False
            if self._state != "streaming":
                raise RuntimeError("voice playback is not streaming")
            current_play = asyncio.create_task(self._sink.play(chunk))
            self._current_play = current_play
        try:
            await current_play
        except asyncio.CancelledError:
            async with self._lock:
                if self._state in {"interrupted", "failed"}:
                    return False
            raise
        finally:
            async with self._lock:
                if self._current_play is current_play:
                    self._current_play = None

        async with self._lock:
            if self._state == "interrupted":
                return False
            self._delivered_chunk_count += 1
            self._delivered_byte_count += len(chunk)
        return True


__all__ = [
    "VoicePlaybackController",
    "VoicePlaybackReceipt",
    "VoiceReplyGenerationCanceller",
    "VoicePlaybackSink",
    "admit_final_voice_turn",
]
