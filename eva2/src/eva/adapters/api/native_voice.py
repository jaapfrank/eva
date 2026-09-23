"""Native HA HTTP route and cancellable request lifecycle.

An installation-bound service is supplied by bootstrap only after household
admission is configured. No owner/dashboard identity is inferred here. The
responder must be cancellation-aware; wrapping synchronous work in to_thread
alone does not provide model/tool cancellation. One service owns a single
installation in a single API process; multi-worker serving is not supported.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hmac
import json
import hashlib
import re
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    satellite_id: str = Field(pattern=r"^assist_satellite\.[a-z0-9_]+$", max_length=200)
    device_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    conversation_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=16384)
    language: str = Field(min_length=1, max_length=32)


class VoiceInterrupt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    satellite_id: str = Field(pattern=r"^assist_satellite\.[a-z0-9_]+$", max_length=200)
    device_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class PlaybackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    generation: int = Field(ge=1, le=0xFFFFFFFF)
    status: Literal["completed", "interrupted", "unconfirmed"]
    offered_frames: int = Field(ge=0, le=24000 * 180)
    confirmed_frames: int | None = Field(ge=0, le=24000 * 180)
    sample_rate: int = Field(ge=24000, le=24000)


class VoicePlayback(VoiceInterrupt):
    reply_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: PlaybackPayload


@dataclass(frozen=True)
class VoiceReply:
    text: str
    continue_conversation: bool = False


class RequestLedger(Protocol):
    def begin(self, *, device_id: str, request_id: str, now: datetime) -> bool: ...
    def interrupt(self, *, device_id: str, request_id: str, now: datetime) -> None: ...
    def claim_reply(self, *, device_id: str, request_id: str, now: datetime,
                    reply_sha256: str) -> bool: ...
    def record_playback(self, *, device_id: str, request_id: str, reply_sha256: str,
                        result: AudioPlaybackResult, now: datetime) -> None: ...


@dataclass
class _Running:
    request_id: str
    task: asyncio.Task[VoiceReply] | None = None
    stopped: bool = False


class NativeVoiceService:
    def __init__(self, *, token: str, devices: Mapping[str, str], ledger: RequestLedger,
                 respond: Callable[[VoiceRequest], Awaitable[VoiceReply]],
                 validate_current: Callable[[], None]):
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or not devices or len(devices) > 64
                or len(set(devices.values())) != len(devices)
                or any(not re.fullmatch(r"assist_satellite\.[a-z0-9_]{1,183}", satellite)
                       or not re.fullmatch(r"[0-9a-f]{32}", device)
                       for satellite, device in devices.items())):
            raise ValueError("Invalid native voice configuration")
        self._token = token
        self._devices = dict(devices)
        self._ledger = ledger
        self._respond = respond
        self._validate_current = validate_current
        self._locks = {device: asyncio.Lock() for device in devices.values()}
        self._active: dict[str, _Running] = {}
        self._pending: dict[tuple[str, str], _Running] = {}
        self._workers: set[asyncio.Task] = set()
        self._cleanups: set[asyncio.Task] = set()
        self._closed = False

    def authorize(self, authorization: str, satellite_id: str, device_id: str,
                  *, stopping: bool = False) -> None:
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + self._token).encode()):
            raise HTTPException(401, "Voice authentication required")
        if self._closed:
            raise HTTPException(503, "Voice service unavailable")
        # A revoked grant blocks new work, but its authenticated device can
        # still stop an already admitted request. This grants no new effects.
        if not stopping:
            self._validate_current()
        if self._devices.get(satellite_id) != device_id:
            raise HTTPException(403, "Voice device is not admitted")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(microsecond=0)

    async def interrupt(self, device_id: str, request_id: str) -> None:
        current = self._active.get(device_id)
        if current is not None and current.request_id == request_id and not current.stopped:
            current.stopped = True
            if current.task is not None:
                current.task.cancel()
        pending = self._pending.get((device_id, request_id))
        if pending is not None:
            pending.stopped = True
        await asyncio.to_thread(self._ledger.interrupt, device_id=device_id,
                                request_id=request_id, now=self._now())

    def _worker_finished(self, task: asyncio.Task) -> None:
        self._workers.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures even after HTTP disconnect.

    def _cleanup_finished(self, task: asyncio.Task) -> None:
        self._cleanups.discard(task)
        if not task.cancelled():
            task.exception()

    async def _cleanup(self, device_id: str, request_id: str) -> None:
        task = asyncio.create_task(self.interrupt(device_id, request_id))
        self._cleanups.add(task)
        task.add_done_callback(self._cleanup_finished)
        await asyncio.shield(task)

    async def run(self, request: VoiceRequest) -> VoiceReply:
        device, request_id = request.device_id, request.request_id
        entry = None
        delivered = False
        cleanup_required = False
        try:
            async with self._locks[device]:
                if self._closed:
                    raise HTTPException(503, "Voice service unavailable")
                self._validate_current()
                key = (device, request_id)
                previous = self._active.get(device)
                if key in self._pending or (previous is not None and previous.request_id == request_id):
                    raise HTTPException(409, "Voice request already running")
                entry = _Running(request_id)
                self._pending[key] = entry
                cleanup_required = True
                admitted = await asyncio.to_thread(self._ledger.begin, device_id=device,
                                                    request_id=request_id, now=self._now())
                if not admitted:
                    cleanup_required = False
                    raise HTTPException(409, "Voice request already seen or stopped")
                if entry.stopped or self._closed:
                    raise HTTPException(409, "Voice request stopped during admission")
                previous = self._active.get(device)
                if previous is not None:
                    await self.interrupt(device, previous.request_id)
                if entry.stopped or self._closed:
                    raise HTTPException(409, "Voice request stopped during admission")
                self._validate_current()
                entry.task = asyncio.create_task(self._respond(request))
                self._workers.add(entry.task)
                entry.task.add_done_callback(self._worker_finished)
                self._active[device] = entry
                self._pending.pop(key, None)
            # Cancellation propagates into the owned responder. Even a responder
            # that returns after cancellation must pass the stopped/current check.
            assert entry.task is not None
            reply = await asyncio.wait_for(asyncio.shield(entry.task), timeout=32)
            self._validate_current()
            if (entry.stopped or self._active.get(device) is not entry
                    or not isinstance(reply, VoiceReply) or not isinstance(reply.text, str)
                    or not reply.text.strip() or len(reply.text) > 16384
                    or type(reply.continue_conversation) is not bool):
                raise HTTPException(409, "Voice reply no longer deliverable")
            claimed = await asyncio.to_thread(self._ledger.claim_reply, device_id=device,
                request_id=request_id, now=self._now(),
                reply_sha256=hashlib.sha256(reply.text.encode("utf-8")).hexdigest())
            if not claimed or entry.stopped or self._active.get(device) is not entry:
                raise HTTPException(409, "Voice reply no longer deliverable")
            self._validate_current()
            delivered = True
            return reply
        finally:
            try:
                if not delivered and cleanup_required:
                    await self._cleanup(device, request_id)
            finally:
                if entry is not None and self._pending.get((device, request_id)) is entry:
                    self._pending.pop((device, request_id), None)
                if entry is not None and self._active.get(device) is entry:
                    self._active.pop(device, None)

    async def close(self) -> None:
        self._closed = True
        requests = set(self._pending) | {(device, entry.request_id)
                                             for device, entry in self._active.items()}
        failures = []
        try:
            results = await asyncio.gather(*(self._cleanup(device, request_id)
                                             for device, request_id in requests),
                                           return_exceptions=True)
            failures.extend(result for result in results if isinstance(result, BaseException))
        finally:
            try:
                if self._cleanups:
                    results = await asyncio.gather(*tuple(self._cleanups), return_exceptions=True)
                    failures.extend(result for result in results if isinstance(result, BaseException))
            finally:
                if self._workers:
                    await asyncio.gather(*tuple(self._workers), return_exceptions=True)
        if failures:
            raise RuntimeError("Voice shutdown could not commit every stop") from None


def install_native_voice(app: FastAPI, service: NativeVoiceService | None) -> None:
    if service is None:
        return

    def authorize(request: Request, satellite: str, device: str, *, stopping: bool = False) -> None:
        try:
            service.authorize(request.headers.get("authorization", ""), satellite, device,
                              stopping=stopping)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Voice admission unavailable") from None

    @app.post("/internal/v1/ha-voice/turns/{request_id}/interrupt")
    async def interrupt(request_id: str, payload: VoiceInterrupt, request: Request):
        authorize(request, payload.satellite_id, payload.device_id, stopping=True)
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise HTTPException(422, "Invalid voice request identity")
        try:
            await service._cleanup(payload.device_id, request_id)
        except Exception:
            raise HTTPException(503, "Voice stop could not be committed") from None
        return {"request_id": request_id, "stopped": True}

    @app.get("/internal/v1/ha-voice/capabilities")
    async def capabilities(satellite_id: str, device_id: str, request: Request):
        authorize(request, satellite_id, device_id)
        if not callable(getattr(service._ledger, "record_playback", None)):
            raise HTTPException(503, "Playback reporting unavailable")
        return {"playback_reporting": 1}

    @app.post("/internal/v1/ha-voice/turns/{request_id}/playback")
    async def playback(request_id: str, payload: VoicePlayback, request: Request):
        # Audit for an admitted answer remains possible after revocation. This
        # route cannot start/reopen a turn or grant household/person authority.
        authorize(request, payload.satellite_id, payload.device_id, stopping=True)
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise HTTPException(422, "Invalid voice request identity")
        try:
            result = AudioPlaybackResult.from_wire(payload.result.model_dump())
        except PlaybackConflict:
            raise HTTPException(422, "Invalid playback result") from None
        task = asyncio.create_task(asyncio.to_thread(service._ledger.record_playback,
            device_id=payload.device_id, request_id=request_id,
            reply_sha256=payload.reply_sha256, result=result, now=service._now()))
        service._cleanups.add(task)
        task.add_done_callback(service._cleanup_finished)
        try:
            await asyncio.shield(task)
        except PlaybackConflict:
            raise HTTPException(409, "Playback evidence conflicts with admitted reply") from None
        except Exception:
            raise HTTPException(503, "Playback result unavailable") from None
        return {"request_id": request_id, "recorded": True}

    @app.post("/internal/v1/ha-voice/turns")
    async def respond(payload: VoiceRequest, request: Request):
        authorize(request, payload.satellite_id, payload.device_id)
        if not payload.text.strip():
            raise HTTPException(422, "Voice text is empty")
        if "application/x-ndjson" in request.headers.get("accept", ""):
            return native_stream_response(service, payload)
        worker = asyncio.create_task(service.run(payload))
        try:
            # ASGI does not automatically cancel a handler on HTTP disconnect.
            while not worker.done():
                if await request.is_disconnected():
                    worker.cancel()
                    await service._cleanup(payload.device_id, payload.request_id)
                    raise HTTPException(409, "Voice caller disconnected")
                await asyncio.wait({worker}, timeout=0.05)
            reply = worker.result()
            return {"request_id": payload.request_id, "reply": reply.text,
                    "continue_conversation": reply.continue_conversation}
        except asyncio.CancelledError:
            worker.cancel()
            await service._cleanup(payload.device_id, payload.request_id)
            raise HTTPException(409, "Voice request stopped") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Voice response unavailable") from None
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


def native_stream_response(service, payload):
    """Stream the existing admitted/cancellable request, preserving final fencing."""
    async def events():
        from eva.application.conversation import stream_conversation_text
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        accepting = True
        delivered = False
        size = 0

        def publish(text):
            if accepting:
                loop.call_soon_threadsafe(queue.put_nowait, text)

        with stream_conversation_text(publish):
            worker = asyncio.create_task(service.run(payload))
        worker.add_done_callback(lambda _: queue.put_nowait(None))
        try:
            while True:
                delta = await queue.get()
                if delta is None:
                    break
                if worker.done() and (worker.cancelled() or worker.exception() is not None):
                    break
                service._validate_current()
                size += len(delta)
                if size > 16384:
                    raise ValueError("voice stream exceeds bound")
                yield (json.dumps({"request_id": payload.request_id,
                    "type": "delta", "text": delta}, ensure_ascii=False) + "\n").encode()
            reply = worker.result()
            service._validate_current()
            delivered = True
            yield (json.dumps({"request_id": payload.request_id, "type": "final",
                "reply": reply.text, "continue_conversation": reply.continue_conversation},
                ensure_ascii=False) + "\n").encode()
        except asyncio.CancelledError:
            raise
        except Exception:
            yield (json.dumps({"request_id": payload.request_id, "type": "error"}) + "\n").encode()
        finally:
            accepting = False
            if not delivered:
                worker.cancel()
                cleanup = asyncio.create_task(service._cleanup(payload.device_id, payload.request_id))
                service._cleanups.add(cleanup)
                cleanup.add_done_callback(service._cleanup_finished)
                await asyncio.shield(cleanup)
            await asyncio.gather(worker, return_exceptions=True)

    return StreamingResponse(events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
