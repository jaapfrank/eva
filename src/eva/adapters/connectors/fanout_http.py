"""Bounded HTTP delivery sink with idempotency-key readback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from eva.application.delivery import (
    FanoutDeliveryError,
    FanoutRecipientReceipt,
)


class HttpFanoutDeliverySink:
    def __init__(
        self,
        *,
        recipient_endpoints: dict[str, str],
        allow_insecure_http: bool = False,
    ) -> None:
        if not 1 <= len(recipient_endpoints) <= 8:
            raise ValueError("fan-out endpoint count is outside 1..8")
        endpoints: dict[str, str] = {}
        for recipient, raw_url in recipient_endpoints.items():
            parsed = urlsplit(raw_url)
            if (
                not recipient.strip()
                or len(recipient) > 160
                or not parsed.netloc
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and not allow_insecure_http)
            ):
                raise ValueError("fan-out endpoint configuration is invalid")
            endpoints[recipient] = raw_url.rstrip("/")
        self._endpoints = endpoints

    async def readback(
        self, *, recipient: str, idempotency_key: str
    ) -> FanoutRecipientReceipt | None:
        return await asyncio.to_thread(
            self._readback_sync,
            recipient,
            idempotency_key,
        )

    async def deliver(
        self,
        *,
        recipient: str,
        message: str,
        message_sha256: str,
        idempotency_key: str,
    ) -> FanoutRecipientReceipt:
        endpoint = self._endpoint(recipient)
        body = {
            "contract": "EvaFanoutSinkRequest/v1",
            "idempotency_key": idempotency_key,
            "message": message,
            "message_sha256": message_sha256,
            "recipient": recipient,
        }
        raw = await asyncio.to_thread(
            self._request_sync,
            endpoint + "/v1/deliveries",
            "POST",
            body,
        )
        return self._receipt(raw, recipient, idempotency_key, message_sha256)

    def _readback_sync(
        self, recipient: str, idempotency_key: str
    ) -> FanoutRecipientReceipt | None:
        endpoint = self._endpoint(recipient)
        try:
            raw = self._request_sync(
                endpoint + "/v1/deliveries/" + quote(idempotency_key, safe=""),
                "GET",
                None,
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        receipt = FanoutRecipientReceipt.from_payload(raw)
        if receipt.recipient != recipient or receipt.idempotency_key != idempotency_key:
            raise FanoutDeliveryError("fan-out sink readback scope differs")
        return receipt

    def _endpoint(self, recipient: str) -> str:
        try:
            return self._endpoints[recipient]
        except KeyError as error:
            raise FanoutDeliveryError("fan-out recipient is not configured") from error

    @staticmethod
    def _receipt(
        raw: object, recipient: str, idempotency_key: str, message_sha256: str
    ) -> FanoutRecipientReceipt:
        receipt = FanoutRecipientReceipt.from_payload(raw)
        if (
            receipt.recipient != recipient
            or receipt.idempotency_key != idempotency_key
            or receipt.message_sha256 != message_sha256
        ):
            raise FanoutDeliveryError("fan-out sink response differs")
        return receipt

    @staticmethod
    def _request_sync(
        url: str, method: str, payload: dict[str, object] | None
    ) -> object:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                raw = response.read(65_537)
        except urllib.error.HTTPError:
            raise
        except (OSError, TimeoutError) as error:
            raise FanoutDeliveryError("fan-out sink is unavailable") from error
        if len(raw) > 65_536:
            raise FanoutDeliveryError("fan-out sink response is too large")
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError as error:
            raise FanoutDeliveryError("fan-out sink response is invalid") from error
        return value


def observed_now() -> datetime:
    """Public clock helper for small connector contractservers."""

    return datetime.now(tz=UTC).replace(microsecond=0)


__all__ = ["HttpFanoutDeliverySink"]
