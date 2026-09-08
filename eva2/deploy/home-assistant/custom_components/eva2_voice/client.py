"""Cancellable HTTP transport for a native HA conversation agent.

The core endpoint must bind the integration token and device to a household
audience. Neither HA user names nor a caller-supplied Eva principal enter this
protocol. This client alone does not establish that server-side admission.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp


class EvaVoiceUnavailable(RuntimeError):
    """Sanitized failure safe to expose without transcript or credentials."""


@dataclass(frozen=True)
class EvaVoiceAnswer:
    text: str
    continue_conversation: bool


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
        self._closed = False

    async def respond(self, *, satellite_id: str, device_id: str,
                      conversation_id: str, text: str, language: str) -> EvaVoiceAnswer:
        if (self._closed or satellite_id not in self._satellites
                or not re.fullmatch(r"[0-9a-f]{32}", device_id)
                or not conversation_id or len(conversation_id) > 200
                or not text.strip() or len(text) > 16_384
                or not language or len(language) > 32):
            raise EvaVoiceUnavailable("Eva voice request is not admitted")
        request_id = uuid4().hex
        payload = {"request_id": request_id, "satellite_id": satellite_id,
                   "device_id": device_id, "conversation_id": conversation_id,
                   "text": text, "language": language}
        previous = self._active.get(satellite_id)
        if previous is not None:
            previous.cancel()
        worker = asyncio.create_task(self._respond(payload))
        self._active[satellite_id] = worker
        try:
            answer = await worker
            if self._closed or self._active.get(satellite_id) is not worker:
                raise asyncio.CancelledError
            return answer
        finally:
            if self._active.get(satellite_id) is worker:
                del self._active[satellite_id]

    async def _respond(self, payload: dict[str, str]) -> EvaVoiceAnswer:
        try:
            async with asyncio.timeout(32):
                async with self._session.post(
                    self._url, json=payload, headers=self._headers,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise EvaVoiceUnavailable("Eva voice response failed")
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
            return EvaVoiceAnswer(data["reply"], data["continue_conversation"])
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

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*tuple(self._cleanups), return_exceptions=True)
