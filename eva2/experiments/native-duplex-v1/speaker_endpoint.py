"""TLS/device admission for the experimental speaker; no installed service.

The composition root supplies verified HA household bindings, credential hashes,
existing HA PCM synthesis and a session coroutine. Remote fields cannot select
a person, device or conversation. Microphone routing requires explicit opt-in
and a session owner, such as HouseholdSpeakerSession, that handles native controls.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
import hmac
from http import HTTPStatus
import math
from pathlib import Path
import re
import ssl
import traceback
from weakref import WeakKeyDictionary

from websockets.asyncio.server import serve

from reply_lane import HouseholdBinding
from speaker_wire import FramedSpeakerOutput


@dataclass(frozen=True)
class SpeakerGrant:
    binding: HouseholdBinding
    credential_sha256: str = field(repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.credential_sha256):
            raise ValueError("invalid speaker credential hash")
        if not all(isinstance(v, str) and v.strip() and len(v) <= 256 for v in (
                self.binding.device_id, self.binding.satellite_id,
                self.binding.conversation_id, self.binding.language)):
            raise ValueError("invalid configured household binding")


class SpeakerEndpoint:
    """One active owner per configured device, with explicit revocation.

    A fresh socket must prove OPENED after local cleanup before the supplied
    session may issue replies. Session cleanup owns cancellation and closes the
    output before releasing its device. Session nonces are never credentials.
    """

    def __init__(self, *, grants: tuple[SpeakerGrant, ...],
                 pcm: Callable[[str], AsyncIterator[bytes]],
                 session: Callable[[HouseholdBinding, FramedSpeakerOutput], Awaitable[None]],
                 max_session_seconds: float = 1800, receipt_timeout: float = 3.5,
                 microphone_enabled: bool = False):
        if type(microphone_enabled) is not bool:
            raise ValueError("microphone enablement must be explicit")
        if not 1 <= len(grants) <= 64:
            raise ValueError("configure between one and 64 household speakers")
        for values in ([g.binding.device_id for g in grants],
                       [g.binding.satellite_id for g in grants],
                       [g.credential_sha256 for g in grants]):
            if len(set(values)) != len(values):
                raise ValueError("speaker grants must be unique")
        if not math.isfinite(max_session_seconds) or not 0 < max_session_seconds <= 1800:
            raise ValueError("invalid speaker session lifetime")
        if not math.isfinite(receipt_timeout) or not 0 < receipt_timeout <= 3.5:
            raise ValueError("invalid receipt deadline")
        self._grants = tuple(grants)
        self._pcm, self._session = pcm, session
        self._microphone_enabled = microphone_enabled
        self._max_session_seconds = max_session_seconds
        self._receipt_timeout = receipt_timeout
        self._admitted = WeakKeyDictionary()
        self._active: dict[str, asyncio.Task] = {}
        self._revoked: set[str] = set()
        self._history = {g.binding.device_id: deque(maxlen=8) for g in grants}
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self._failures = deque(maxlen=8)

    def diagnostics(self) -> dict:
        """Bounded local status, without identities, transcripts or error text."""
        return {"closed": self._closed, "activeDevices": len(self._active),
                "recentFailures": list(self._failures)}

    def _record_failure(self, stage: str, error: Exception) -> None:
        self._failures.append({"stage": stage, "type": type(error).__name__[:64],
            "frames": [{"file": Path(frame.filename).name, "function": frame.name,
                        "line": frame.lineno}
                       for frame in traceback.extract_tb(error.__traceback__)[-5:]]})

    def _authenticate(self, connection, request):
        if self._closed:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Speaker service stopping\n")
        if request.path != "/eva-speaker/v1":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        headers = request.headers
        tokens = headers.get_all("Authorization")
        # This endpoint is for the provisioned device, not browser cookies or
        # ambient browser credentials. Query-string tokens aren't accepted.
        if headers.get_all("Origin") or len(tokens) != 1:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", tokens[0])
        if not match:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        digest = hashlib.sha256(match[1].encode("ascii")).hexdigest()
        grant = None
        for candidate in self._grants:
            if hmac.compare_digest(candidate.credential_sha256, digest):
                grant = candidate
        if grant is None or grant.binding.device_id in self._revoked:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        if grant.binding.device_id in self._active:
            return connection.respond(HTTPStatus.CONFLICT, "Speaker already connected\n")
        self._admitted[connection] = grant
        return None

    def listen(self, *, host: str, port: int, tls: ssl.SSLContext):
        if not isinstance(tls, ssl.SSLContext) or tls.protocol != ssl.PROTOCOL_TLS_SERVER:
            raise ValueError("speaker endpoint requires a TLS server context")
        if tls.minimum_version < ssl.TLSVersion.TLSv1_2:
            raise ValueError("speaker endpoint requires TLS 1.2 or newer")
        return serve(self._handle, host, port, ssl=tls, process_request=self._authenticate,
                     compression=None, max_size=2048, max_queue=8, write_limit=4096,
                     open_timeout=5, close_timeout=1, ping_interval=20, ping_timeout=20,
                     server_header=None)

    async def _handle(self, socket):
        grant = self._admitted.pop(socket, None)
        if (self._closed or grant is None or grant.binding.device_id in self._revoked
                or grant.binding.device_id in self._active):
            await socket.close(code=1008, reason="Speaker unavailable")
            return
        device = grant.binding.device_id
        owned = asyncio.create_task(self._run(socket, grant))
        self._active[device] = owned
        try:
            await asyncio.shield(owned)
        except asyncio.CancelledError:
            if not owned.done() and not owned.cancelling():
                owned.cancel()
            await asyncio.gather(asyncio.shield(owned), return_exceptions=True)
            raise
        finally:
            if self._active.get(device) is owned and owned.done():
                del self._active[device]

    async def _run(self, socket, grant):
        device = grant.binding.device_id
        output = FramedSpeakerOutput(socket=socket, pcm=self._pcm,
                    retired_sessions=tuple(self._history[device]), receipt_timeout=self._receipt_timeout,
                    microphone_enabled=self._microphone_enabled)
        work = disconnected = None
        stage = "opening"
        try:
            async with asyncio.timeout(self._max_session_seconds):
                await output.start()
                if device in self._revoked:
                    return
                stage = "conversation"
                work = asyncio.create_task(self._session(grant.binding, output))
                disconnected = asyncio.create_task(socket.wait_closed())
                await asyncio.wait((work, disconnected), return_when=asyncio.FIRST_COMPLETED)
                if work.done():
                    await work
        except Exception as error:
            # Don't log raw headers, private bindings, transcripts or provider
            # errors through the WebSocket server's default traceback handler.
            # Keep a live transport available for STOP/STOPPED during cleanup.
            self._record_failure(stage, error)
        finally:
            for task in (work, disconnected):
                if task is not None and not task.done() and not task.cancelling():
                    task.cancel()
            try:
                await output.close()
            except Exception as error:
                self._record_failure("output-close", error)
                await socket.close(code=1011, reason="Speaker stop unconfirmed")
            finally:
                await asyncio.gather(*(t for t in (work, disconnected) if t is not None),
                                     return_exceptions=True)
                self._history[device].append(output.session)

    async def revoke(self, *, device_id: str) -> None:
        if device_id not in self._history:
            raise ValueError("unknown speaker")
        self._revoked.add(device_id)  # future admission stops before any await
        owned = self._active.get(device_id)
        if owned is not None:
            if not owned.done() and not owned.cancelling():
                owned.cancel()
            await asyncio.gather(asyncio.shield(owned), return_exceptions=True)

    async def close(self) -> None:
        """Stop admission first; retain sockets for physical cleanup receipts."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_task)

    async def _close_all(self) -> None:
        await asyncio.gather(*(self.revoke(device_id=device) for device in self._history))
