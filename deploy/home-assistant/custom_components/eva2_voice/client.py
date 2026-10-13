"""Cancellable HTTP transport for a native HA conversation agent.

The core endpoint must bind the integration token and device to a household
audience. Neither HA user names nor a caller-supplied Eva principal enter this
protocol. This client alone does not establish that server-side admission.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import hashlib
import re
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp


class EvaVoiceUnavailable(RuntimeError):
    """Sanitized failure safe to expose without transcript or credentials."""


class EvaVoiceBusy(EvaVoiceUnavailable):
    """An automation must not supersede an active household request."""


@dataclass(frozen=True)
class EvaVoiceAnswer:
    text: str
    continue_conversation: bool
    request_id: str | None = None


class EvaVoiceClient:
    @staticmethod
    def validate_config(*, url: str, token: str, satellite_ids: frozenset[str]) -> None:
        if not isinstance(url, str) or not isinstance(token, str):
            raise ValueError("invalid Eva voice connection configuration")
        parsed = urlsplit(url)
        _ = parsed.port  # Validate malformed and out-of-range ports before saving.
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or not token
                or any(ord(c) < 32 or ord(c) == 127 for c in token)
                or not satellite_ids or len(satellite_ids) > 64
                or any(not isinstance(s, str)
                       or not re.fullmatch(r"assist_satellite\.[a-z0-9_]+", s)
                       for s in satellite_ids)):
            raise ValueError("invalid Eva voice connection configuration")

    def __init__(self, session: aiohttp.ClientSession, *, url: str, token: str,
                 satellite_ids: frozenset[str]) -> None:
        self.validate_config(url=url, token=token, satellite_ids=satellite_ids)
        self._session = session
        self._url = url.rstrip("/") + "/internal/v1/ha-voice/turns"
        self._headers = {"Authorization": "Bearer " + token}
        self._satellites = satellite_ids
        self._active: dict[str, asyncio.Task] = {}
        self._cleanups: set[asyncio.Task] = set()
        self._reports: dict[str, set[asyncio.Task]] = {}
        self._closed = False

    async def respond(self, *, satellite_id: str, device_id: str,
                      conversation_id: str, text: str, language: str, on_delta=None, on_receipt=None,
                      interrupt_existing: bool = True, delivery_mode: str = "speaker") -> EvaVoiceAnswer:
        if (self._closed or satellite_id not in self._satellites
                or not isinstance(delivery_mode, str) or delivery_mode not in {"speaker", "automation"}
                or not re.fullmatch(r"[0-9a-f]{32}", device_id)
                or not conversation_id or len(conversation_id) > 200
                or not text.strip() or len(text) > 16_384
                or not language or len(language) > 32):
            raise EvaVoiceUnavailable("Eva voice request is not admitted")
        if not interrupt_existing and satellite_id in self._active:
            raise EvaVoiceBusy("Eva voice speaker already has an active request")
        request_id = uuid4().hex
        # Physical stop and microphone capture need not wait for HTTP auditing.
        # A new model turn waits for already scheduled evidence; a failed report
        # leaves the durable history explicitly unconfirmed.
        await asyncio.gather(*(asyncio.shield(task) for task in
            tuple(self._reports.get(device_id, ()))), return_exceptions=True)
        if self._closed:
            raise EvaVoiceUnavailable("Eva voice client is closed")
        payload = {"request_id": request_id, "satellite_id": satellite_id,
                   "device_id": device_id, "conversation_id": conversation_id,
                   "text": text, "language": language}
        if delivery_mode == "automation":
            payload["delivery_mode"] = delivery_mode
        previous = self._active.get(satellite_id)
        if previous is not None:
            # Recheck after playback-report waits, with no await before admission.
            if not interrupt_existing:
                raise EvaVoiceBusy("Eva voice speaker already has an active request")
            previous.cancel()
        worker = asyncio.create_task(self._respond(payload, on_delta=on_delta, on_receipt=on_receipt))
        self._active[satellite_id] = worker
        try:
            answer = await worker
            if self._closed or self._active.get(satellite_id) is not worker:
                raise asyncio.CancelledError
            return answer
        finally:
            if self._active.get(satellite_id) is worker:
                del self._active[satellite_id]

    async def _respond(self, payload: dict[str, str], *, on_delta=None, on_receipt=None) -> EvaVoiceAnswer:
        automation = payload.get("delivery_mode", "speaker") == "automation"
        # Automation work uses server budgets, not the interactive audio deadline.
        # Override the shared HA session timeout for this request only.
        request_options = {}
        if automation:
            session_timeout = self._session.timeout
            request_options["timeout"] = aiohttp.ClientTimeout(
                total=None, connect=session_timeout.connect, sock_read=None,
                sock_connect=session_timeout.sock_connect,
                ceil_threshold=session_timeout.ceil_threshold)
        try:
            async with asyncio.timeout(None if automation else 32):
                async with self._session.post(
                    self._url, json=payload, headers={**self._headers, **({"Accept": "application/x-ndjson"} if on_delta else {})},
                    allow_redirects=False, **request_options,
                ) as response:
                    if response.status != 200:
                        raise EvaVoiceUnavailable("Eva voice response failed")
                    if on_delta is not None and response.content_type == "application/x-ndjson":
                        receipt = response.headers.get("X-Eva-Input-Receipt", "")
                        if on_receipt is not None and receipt:
                            if len(receipt) > 160 or not receipt.strip():
                                raise EvaVoiceUnavailable("Eva input receipt is invalid")
                            # Transport receipt only; canonical reply and playback hash stay unchanged.
                            await on_receipt(receipt)
                        data = await self._stream_reply(response, payload, on_delta)
                    else:
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(8192):
                            raw.extend(chunk)
                            if len(raw) > 131_072:
                                raise EvaVoiceUnavailable("Eva voice response exceeds its bound")
                        data = json.loads(raw)
            if (not isinstance(data, dict)
                    or set(data) != {"request_id", "reply", "continue_conversation"}
                    or data["request_id"] != payload["request_id"]
                    or not isinstance(data["reply"], str) or not data["reply"].strip()
                    or len(data["reply"]) > 16_384
                    or type(data["continue_conversation"]) is not bool):
                raise EvaVoiceUnavailable("Eva voice response binding differs")
            if on_delta is not None and response.content_type != "application/x-ndjson":
                # Compatibility after validating the same request; never retry a turn.
                await on_delta(data["reply"])
            return EvaVoiceAnswer(data["reply"], data["continue_conversation"], data["request_id"])
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            # Cancellation is request-specific, including a response that may
            # have started remotely before its HTTP connection was established.
            cleanup = asyncio.create_task(self._interrupt(payload))
            self._cleanups.add(cleanup)
            cleanup.add_done_callback(self._cleanups.discard)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A second cancel must not cancel the bounded remote cleanup.
                # The client retains it; unload waits for it as well.
                cancelled = True
            if cancelled:
                raise asyncio.CancelledError from None
            if not isinstance(error, Exception):
                raise
            raise EvaVoiceUnavailable("Eva voice response unavailable") from None

    @staticmethod
    async def _stream_reply(response, payload, on_delta):
        buffer, streamed, size = b"", "", 0
        async for chunk in response.content.iter_chunked(8192):
            size += len(chunk)
            if size > 262_144:
                raise EvaVoiceUnavailable("Eva voice stream exceeds its bound")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                data = json.loads(line)
                if not isinstance(data, dict) or data.get("request_id") != payload["request_id"]:
                    raise EvaVoiceUnavailable("Eva voice stream binding differs")
                if data.get("type") == "delta" and isinstance(data.get("text"), str):
                    streamed += data["text"]
                    if len(streamed) > 16384:
                        raise EvaVoiceUnavailable("Eva voice text exceeds its bound")
                    await on_delta(data["text"])
                elif data.get("type") == "final":
                    reply = data.get("reply")
                    if (set(data) != {"type", "request_id", "reply", "continue_conversation"}
                            or not isinstance(reply, str) or not reply.strip() or len(reply) > 16384
                            or type(data.get("continue_conversation")) is not bool
                            or not reply.startswith(streamed)):
                        raise EvaVoiceUnavailable("Eva final voice text differs from its stream")
                    if reply[len(streamed):]:
                        await on_delta(reply[len(streamed):])
                    return {key: data[key] for key in ("request_id", "reply", "continue_conversation")}
                else:
                    raise EvaVoiceUnavailable("Eva voice stream interrupted")
        raise EvaVoiceUnavailable("Eva voice stream ended without a final reply")

    async def _interrupt(self, payload: dict[str, str]) -> None:
        try:
            async with asyncio.timeout(2):
                async with self._session.post(
                    self._url + "/" + payload["request_id"] + "/interrupt",
                    json={key: payload[key] for key in ("satellite_id", "device_id")},
                    headers=self._headers, allow_redirects=False,
                ):
                    pass
        except (TimeoutError, aiohttp.ClientError):
            # No remote-stop receipt is claimed. The server must enforce its
            # own deadline and suppress a disconnected/expired response.
            pass

    async def verify_playback_support(self, *, satellite_id: str, device_id: str) -> None:
        if self._closed or satellite_id not in self._satellites:
            raise EvaVoiceUnavailable("Eva voice device is not admitted")
        data = await self._evidence_request("GET", self._url.rsplit("/", 1)[0] + "/capabilities",
            params={"satellite_id": satellite_id, "device_id": device_id})
        if data != {"playback_reporting": 1} or type(data.get("playback_reporting")) is not int:
            raise EvaVoiceUnavailable("Eva playback reporting is unavailable")

    async def _evidence_request(self, method: str, url: str, **kwargs) -> dict:
        try:
            async with asyncio.timeout(2):
                async with self._session.request(method, url, headers=self._headers,
                        allow_redirects=False, **kwargs) as response:
                    if response.status != 200:
                        raise EvaVoiceUnavailable("Eva playback evidence request failed")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(1024):
                        raw.extend(chunk)
                        if len(raw) > 4096:
                            raise EvaVoiceUnavailable("Eva playback evidence exceeds its bound")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise EvaVoiceUnavailable("Invalid Eva playback evidence response")
            return data
        except Exception:
            raise EvaVoiceUnavailable("Eva playback evidence unavailable") from None

    def record_playback(self, *, satellite_id: str, device_id: str,
                        answer: EvaVoiceAnswer, result: dict) -> asyncio.Task:
        """Own a bounded idempotent report without holding up physical stop.

        The authenticated core validates the result and immutable reply hash.
        Keep this HA-package transport independent of Eva's Python packages.
        """
        if (self._closed or satellite_id not in self._satellites
                or not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{32}", device_id)
                or not isinstance(answer.request_id, str)
                or not re.fullmatch(r"[0-9a-f]{32}", answer.request_id)):
            raise EvaVoiceUnavailable("Eva playback reply binding is invalid")
        request_id = answer.request_id
        payload = {"satellite_id": satellite_id, "device_id": device_id,
            "reply_sha256": hashlib.sha256(answer.text.encode("utf-8")).hexdigest(),
            "result": dict(result)}

        async def report():
            data = await self._evidence_request("POST", self._url + "/" + request_id + "/playback",
                                               json=payload)
            if data != {"request_id": request_id, "recorded": True} or data.get("recorded") is not True:
                raise EvaVoiceUnavailable("Eva playback receipt binding differs")

        task = asyncio.create_task(report())
        pending = self._reports.setdefault(device_id, set())
        pending.add(task)
        self._cleanups.add(task)
        def finished(task):
            pending.discard(task)
            self._cleanups.discard(task)
            if not pending:
                self._reports.pop(device_id, None)
            if not task.cancelled():
                task.exception()
        task.add_done_callback(finished)
        return task

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*tuple(self._cleanups), return_exceptions=True)
