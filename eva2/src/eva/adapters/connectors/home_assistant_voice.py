"""Home Assistant as a speech media provider, never a conversation authority.

Only final STT text leaves this adapter. The authenticated voice session and
ordinary Eva conversation admission remain the caller's responsibility.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
import json
from urllib.parse import urlsplit, urlunsplit

import httpx2 as httpx
from websockets.asyncio.client import connect


class VoiceMediaError(RuntimeError):
    """A sanitized media failure, without credentials, frames or transcripts."""


class HomeAssistantSpeechInput:
    """Stream mono signed 16-bit PCM to an explicitly selected STT-only run."""

    def __init__(self, *, base_url: str, token: str, pipeline_id: str,
                 timeout_seconds: float = 120, max_audio_bytes: int = 3_840_000):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("voice media endpoint is invalid")
        if not token or not pipeline_id or not 0 < timeout_seconds <= 300:
            raise ValueError("voice media configuration is incomplete")
        if type(max_audio_bytes) is not int or not 0 < max_audio_bytes <= 9_600_000:
            raise ValueError("voice media input bound is invalid")
        self._url = urlunsplit(("wss" if parsed.scheme == "https" else "ws",
                               parsed.netloc, parsed.path.rstrip("/") + "/api/websocket", "", ""))
        self._token = token
        self._pipeline = pipeline_id
        self._timeout = timeout_seconds
        self._max_audio_bytes = max_audio_bytes

    async def transcribe(self, chunks: AsyncIterable[bytes]) -> str:
        """Return final text only after successful STT and pipeline termination.

        Pipeline/provider failures discard partial results. Disconnect/cancel
        closes media transport and its producer; raw audio is never persisted.
        """
        try:
            iterator = aiter(chunks)
            try:
                async with asyncio.timeout(self._timeout):
                    async with connect(self._url, open_timeout=10, close_timeout=2,
                                       max_size=65_536, proxy=None) as socket:
                        return await self._transcribe(socket, iterator)
            finally:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
        except asyncio.CancelledError:
            raise
        except VoiceMediaError:
            raise
        except Exception:
            raise VoiceMediaError("speech input failed") from None

    @staticmethod
    async def _message(socket) -> dict:
        raw = await socket.recv()
        if not isinstance(raw, str):
            raise VoiceMediaError("speech provider sent an invalid message")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise VoiceMediaError("speech provider sent an invalid message")
        return value

    async def _transcribe(self, socket, chunks: AsyncIterable[bytes]) -> str:
        if (await self._message(socket)).get("type") != "auth_required":
            raise VoiceMediaError("speech provider authentication failed")
        await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
        if (await self._message(socket)).get("type") != "auth_ok":
            raise VoiceMediaError("speech provider authentication failed")
        await socket.send(json.dumps({"id": 1, "type": "assist_pipeline/run",
                                      "pipeline": self._pipeline, "start_stage": "stt",
                                      "end_stage": "stt", "input": {"sample_rate": 16000},
                                      "timeout": self._timeout}))
        sender = None
        handler = None
        started = False
        acknowledged = False
        transcript = None
        try:
            while True:
                incoming = asyncio.create_task(self._message(socket))
                try:
                    if sender is not None and not sender.done():
                        done, _ = await asyncio.wait((incoming, sender),
                                                    return_when=asyncio.FIRST_COMPLETED)
                        if sender in done:
                            sender.result()  # Do not hide invalid PCM/producer errors.
                    elif sender is not None:
                        sender.result()
                    message = await incoming
                finally:
                    if not incoming.done():
                        incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
                if message.get("id") != 1:
                    raise VoiceMediaError("speech provider response binding differs")
                if message.get("type") == "result":
                    if acknowledged or message.get("success") is not True:
                        raise VoiceMediaError("speech provider rejected the run")
                    acknowledged = True
                    continue
                event = message.get("event")
                if message.get("type") != "event" or not isinstance(event, dict):
                    raise VoiceMediaError("speech provider sent an invalid event")
                kind, data = event.get("type"), event.get("data", {})
                if kind == "run-end" and data is None:
                    data = {}
                if not isinstance(data, dict):
                    raise VoiceMediaError("speech provider sent invalid event data")
                if kind == "run-start":
                    if handler is not None:
                        raise VoiceMediaError("speech provider repeated run start")
                    handler = data.get("runner_data", {}).get("stt_binary_handler_id")
                    if type(handler) is not int or not 1 <= handler <= 255:
                        raise VoiceMediaError("speech provider audio binding is invalid")
                elif kind == "stt-start":
                    if handler is None or started:
                        raise VoiceMediaError("speech provider start order differs")
                    started = True
                    sender = asyncio.create_task(self._send_audio(socket, chunks, handler))
                elif kind in {"stt-vad-start", "stt-vad-end"}:
                    if not started or transcript is not None:
                        raise VoiceMediaError("speech provider activity order differs")
                elif kind == "stt-end":
                    text = data.get("stt_output", {}).get("text")
                    if (not started or transcript is not None or not isinstance(text, str)
                            or not text.strip() or len(text) > 16_384):
                        raise VoiceMediaError("speech provider final transcript is invalid")
                    transcript = text
                elif kind == "run-end":
                    if not acknowledged or transcript is None:
                        raise VoiceMediaError("speech provider ended without final text")
                    return transcript
                else:
                    # In particular, never accept HA native intent or TTS here.
                    raise VoiceMediaError("speech provider left the STT-only route")
        finally:
            if sender is not None:
                if not sender.done():
                    sender.cancel()
                results = await asyncio.gather(sender, return_exceptions=True)
                if any(isinstance(result, BaseException)
                       and not isinstance(result, asyncio.CancelledError) for result in results):
                    raise VoiceMediaError("speech input producer failed") from None

    async def _send_audio(self, socket, chunks: AsyncIterable[bytes], handler: int) -> None:
        total = 0
        async for chunk in chunks:
            if (not isinstance(chunk, bytes) or not chunk or len(chunk) % 2
                    or len(chunk) > 65_536):
                raise VoiceMediaError("speech input PCM chunk is invalid")
            total += len(chunk)
            if total > self._max_audio_bytes:
                raise VoiceMediaError("speech input exceeds its session bound")
            await socket.send(bytes([handler]) + chunk)
        if not total:
            raise VoiceMediaError("speech input is empty")
        await socket.send(bytes([handler]))


@dataclass(frozen=True, slots=True)
class SpeechAudioChunk:
    """Transient encoded audio and decoder type; never stored by this adapter."""

    data: bytes
    mime_type: str


class HomeAssistantSpeechOutput(HomeAssistantSpeechInput):
    """Request TTS alone and relay the returned audio in bounded chunks.

    HA may prepare the entire synthesis before returning its audio URL. This
    interface streams retrieval, not a claim of incremental provider synthesis.
    """

    async def synthesize(self, text: str) -> AsyncIterator[SpeechAudioChunk]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16_384:
            raise ValueError("speech output text is invalid")
        queue: asyncio.Queue[SpeechAudioChunk | VoiceMediaError | None] = asyncio.Queue(maxsize=1)
        producer = asyncio.create_task(self._produce_speech(text, queue))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, VoiceMediaError):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _produce_speech(
        self, text: str, queue: asyncio.Queue[SpeechAudioChunk | VoiceMediaError | None],
    ) -> None:
        # One task owns timeout and transport throughout. No timeout scope
        # crosses a consumer yield, even with a different task per anext().
        try:
            async with asyncio.timeout(self._timeout):
                async with connect(self._url, open_timeout=10, close_timeout=2,
                                   max_size=65_536, proxy=None) as socket:
                    path, mime_type = await self._prepare_speech(socket, text)
                parsed = urlsplit(self._url)
                origin = urlunsplit(("https" if parsed.scheme == "wss" else "http",
                                    parsed.netloc, "", "", ""))
                async with httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                             timeout=10) as client:
                    async with client.stream("GET", origin + path,
                                             headers={"Authorization": "Bearer " + self._token}) as response:
                        actual_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                        if response.status_code != 200 or actual_type != mime_type:
                            raise VoiceMediaError("speech output retrieval failed")
                        total = 0
                        async for chunk in response.aiter_bytes(chunk_size=16_384):
                            total += len(chunk)
                            if total > 20_000_000:
                                raise VoiceMediaError("speech output exceeds its session bound")
                            if chunk:
                                await queue.put(SpeechAudioChunk(chunk, mime_type))
                        if total == 0:
                            raise VoiceMediaError("speech output is empty")
        except asyncio.CancelledError:
            raise
        except VoiceMediaError as error:
            await queue.put(error)
            return
        except Exception:
            await queue.put(VoiceMediaError("speech output failed"))
            return
        await queue.put(None)

    async def _prepare_speech(self, socket, text: str) -> tuple[str, str]:
        if (await self._message(socket)).get("type") != "auth_required":
            raise VoiceMediaError("speech provider authentication failed")
        await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
        if (await self._message(socket)).get("type") != "auth_ok":
            raise VoiceMediaError("speech provider authentication failed")
        await socket.send(json.dumps({"id": 1, "type": "assist_pipeline/run",
                                      "pipeline": self._pipeline, "start_stage": "tts",
                                      "end_stage": "tts", "input": {"text": text},
                                      "timeout": self._timeout}))
        acknowledged = run_started = tts_started = False
        audio = None
        while True:
            message = await self._message(socket)
            if message.get("id") != 1:
                raise VoiceMediaError("speech provider response binding differs")
            if message.get("type") == "result":
                if acknowledged or message.get("success") is not True:
                    raise VoiceMediaError("speech provider rejected the run")
                acknowledged = True
                continue
            event = message.get("event")
            if message.get("type") != "event" or not isinstance(event, dict):
                raise VoiceMediaError("speech provider sent an invalid event")
            kind, data = event.get("type"), event.get("data", {})
            if kind == "run-end" and data is None:
                data = {}
            if not isinstance(data, dict):
                raise VoiceMediaError("speech provider sent invalid event data")
            if kind == "run-start" and not run_started:
                run_started = True
            elif kind == "tts-start" and run_started and not tts_started:
                tts_started = True
            elif kind == "tts-end" and tts_started and audio is None:
                output = data.get("tts_output", {})
                path, mime_type = output.get("url"), output.get("mime_type")
                # This credential is sent only to the configured HA origin.
                # No external URL, redirect, query token or encoded path escape.
                if (not isinstance(path, str) or not path.startswith("/api/tts_proxy/")
                        or any(c in path for c in ("%", "?", "#", "\\"))
                        or any(segment in {".", ".."} for segment in path.split("/"))
                        or any(ord(c) <= 32 or ord(c) == 127 for c in path)
                        or mime_type not in {"audio/mpeg", "audio/wav", "audio/ogg", "audio/flac"}):
                    raise VoiceMediaError("speech output reference is invalid")
                audio = (path, mime_type)
            elif kind == "run-end" and acknowledged and audio is not None:
                return audio
            else:
                raise VoiceMediaError("speech provider left the TTS-only route")
