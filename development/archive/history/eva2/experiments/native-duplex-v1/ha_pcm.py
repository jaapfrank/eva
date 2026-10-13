"""Bounded in-memory decoding of the existing HA Cloud TTS adapter's output.

No second TTS provider, disk audio, or fire-and-forget decoder process. Consumers
own/close this iterator; backpressure propagates through stdout and stdin pipes.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from speaker_wire import MAX_PCM_BYTES, MAX_REPLY_FRAMES, WireError


class HomeAssistantPCM:
    def __init__(self, speech_output, *, ffmpeg: str = "/usr/bin/ffmpeg",
                 read_timeout: float = 60, max_frames: int = MAX_REPLY_FRAMES) -> None:
        self._output, self._ffmpeg = speech_output, ffmpeg
        self._read_timeout, self._max_frames = read_timeout, max_frames

    async def synthesize_pcm(self, text: str) -> AsyncIterator[bytes]:
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", "pipe:0", "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "24000", "-ac", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16_384,
        )
        async def feed() -> None:
            mime = None
            total = 0
            source = None
            try:
                source = self._output.synthesize(text).__aiter__()
                async for chunk in source:
                    if (chunk.mime_type not in {"audio/mpeg", "audio/wav", "audio/ogg", "audio/flac"}
                            or not isinstance(chunk.data, bytes) or not chunk.data
                            or len(chunk.data) > 65_536):
                        raise WireError("invalid encoded HA audio chunk")
                    if mime is not None and mime != chunk.mime_type:
                        raise WireError("HA audio type changed within reply")
                    mime = chunk.mime_type
                    total += len(chunk.data)
                    if total > 20_000_000:
                        raise WireError("encoded HA audio exceeds its bound")
                    process.stdin.write(chunk.data)
                    await process.stdin.drain()
            finally:
                process.stdin.close()
                if source is not None and hasattr(source, "aclose"):
                    await source.aclose()

        async def drain_errors() -> None:
            # Do not retain stderr: decoder diagnostics can echo provider input.
            while await process.stderr.read(4096):
                pass

        feeder = asyncio.create_task(feed())
        errors = asyncio.create_task(drain_errors())
        total = 0
        pending = b""
        try:
            while True:
                chunk = await asyncio.wait_for(process.stdout.read(MAX_PCM_BYTES), self._read_timeout)
                if not chunk:
                    break
                pending += chunk
                whole_bytes = len(pending) - len(pending) % 2
                if whole_bytes:
                    data, pending = pending[:whole_bytes], pending[whole_bytes:]
                    total += len(data)
                    if total > self._max_frames * 2:
                        raise WireError("decoded HA audio exceeds its duration bound")
                    yield data
            await feeder
            await errors
            if await process.wait() != 0 or not total or pending:
                raise WireError("HA audio decoding failed or was empty")
        finally:
            if not feeder.done():
                feeder.cancel()
            if process.returncode is None:
                process.kill()
            await process.wait()
            await asyncio.gather(feeder, errors, return_exceptions=True)
