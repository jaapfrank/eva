"""Early speech onset with HA's pinned MicroVad; no listener or audio storage.

This adapter requires an authenticated, echo-cancelled 16kHz mono PCM16 source.
It detects speech independently of HA connection setup and relays every byte to
HomeAssistantSpeechInput. A bounded queue fails closed instead of dropping words.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Protocol

from eva.adapters.connectors.home_assistant_voice import VoiceMediaError


class Detector(Protocol):
    def Process10ms(self, audio: bytes) -> float: ...


class LocalOnsetSpeechInput:
    def __init__(self, speech_input, *, detector_factory: Callable[[], Detector],
                 confidence: float = 0.7, speech_frames: int = 12,
                 buffer_frames: int = 100) -> None:
        if not 0 < confidence <= 1 or type(speech_frames) is not int or not 1 <= speech_frames <= 30:
            raise ValueError("invalid early speech detection settings")
        if type(buffer_frames) is not int or not 10 <= buffer_frames <= 300:
            raise ValueError("invalid microphone buffer bound")
        self._input = speech_input
        self._factory = detector_factory
        self._confidence = confidence
        self._frames = speech_frames
        self._buffer_frames = buffer_frames

    async def transcribe(self, chunks: AsyncIterable[bytes], *,
                         on_speech_start: Callable[[], Awaitable[None]]) -> str:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(self._buffer_frames)
        onset = asyncio.Event()
        source = aiter(chunks)

        async def notify():
            await onset.wait()
            await on_speech_start()

        async def produce():
            carry = bytearray()
            consecutive = 0
            try:
                async for chunk in source:
                    if (not isinstance(chunk, bytes) or not chunk or len(chunk) % 2
                            or len(chunk) > 65536):
                        raise VoiceMediaError("invalid microphone PCM frame")
                    carry.extend(chunk)
                    while len(carry) >= 320:
                        frame = bytes(carry[:320])
                        del carry[:320]
                        if not onset.is_set():
                            probability = detector.Process10ms(frame)
                            consecutive = consecutive + 1 if probability >= self._confidence else 0
                            if consecutive >= self._frames:
                                onset.set()
                        # Audio capture must not wait for HTTP stop or STT setup.
                        # On overflow the entire turn fails; no silent truncation.
                        queue.put_nowait(frame)
                    await asyncio.sleep(0)
                if carry:
                    queue.put_nowait(bytes(carry))
                await queue.put(None)
            except asyncio.QueueFull:
                raise VoiceMediaError("microphone buffer overrun") from None

        async def audio():
            while (item := await queue.get()) is not None:
                yield item

        try:
            detector = self._factory()  # Model history must not cross sessions.
            async with asyncio.TaskGroup() as tasks:
                notification = tasks.create_task(notify())

                async def ha_onset():
                    onset.set()  # HA is a fallback if local detection misses.
                    await asyncio.shield(notification)

                producer = tasks.create_task(produce())
                transcription = tasks.create_task(
                    self._input.transcribe(audio(), on_speech_start=ha_onset))
                text = await transcription
                producer.cancel()  # HA may end after a natural sentence pause.
                if not onset.is_set():
                    raise VoiceMediaError("speech ended without an onset")
                await notification
            return text
        except asyncio.CancelledError:
            raise
        except Exception:
            raise VoiceMediaError("early speech input failed") from None
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()
            while not queue.empty():
                queue.get_nowait()
