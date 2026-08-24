"""Connector-neutral fan-out delivery with idempotent provider readback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import Literal, Protocol

from eva.contracts.canonical_json import canonical_sha256


class FanoutDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FanoutDeliveryRequest:
    operation_id: str
    requested_by: str
    message: str
    recipients: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or len(self.operation_id) > 240:
            raise ValueError("fan-out operation identity is invalid")
        if not self.requested_by.strip() or len(self.requested_by) > 240:
            raise ValueError("fan-out requester is invalid")
        if not self.message.strip() or len(self.message) > 4096:
            raise ValueError("fan-out message is invalid")
        if not 1 <= len(self.recipients) <= 8:
            raise ValueError("fan-out recipient count is outside 1..8")
        if len(set(self.recipients)) != len(self.recipients) or any(
            not value.strip() or len(value) > 160 for value in self.recipients
        ):
            raise ValueError("fan-out recipients are invalid or duplicated")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != UTC.utcoffset(
            self.created_at
        ):
            raise ValueError("fan-out creation time must be UTC")
        if self.created_at.microsecond:
            raise ValueError("fan-out creation time must use whole seconds")

    @property
    def message_sha256(self) -> str:
        return hashlib.sha256(self.message.encode()).hexdigest()

    @property
    def request_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def idempotency_key(self, recipient: str) -> str:
        if recipient not in self.recipients:
            raise ValueError("fan-out recipient is outside the request")
        return "fanout:" + canonical_sha256(
            {
                "operation_id": self.operation_id,
                "recipient": recipient,
                "request_sha256": self.request_sha256,
            }
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaFanoutDeliveryRequest/v1",
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "message": self.message,
            "message_sha256": self.message_sha256,
            "operation_id": self.operation_id,
            "recipients": list(self.recipients),
            "requested_by": self.requested_by,
        }

    @classmethod
    def from_payload(cls, raw: object) -> FanoutDeliveryRequest:
        if not isinstance(raw, dict) or raw.get("contract") != (
            "EvaFanoutDeliveryRequest/v1"
        ):
            raise FanoutDeliveryError("fan-out request is malformed")
        try:
            recipients = raw["recipients"]
            if not isinstance(recipients, list):
                raise TypeError
            value = cls(
                operation_id=str(raw["operation_id"]),
                requested_by=str(raw["requested_by"]),
                message=str(raw["message"]),
                recipients=tuple(str(item) for item in recipients),
                created_at=datetime.fromisoformat(
                    str(raw["created_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FanoutDeliveryError("fan-out request is malformed") from error
        if value.to_payload() != raw:
            raise FanoutDeliveryError("fan-out request shape or hash differs")
        return value


@dataclass(frozen=True, slots=True)
class FanoutRecipientReceipt:
    recipient: str
    idempotency_key: str
    message_sha256: str
    status: Literal["accepted", "rejected"]
    provider_receipt_id: str | None
    reason_code: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.recipient.strip() or len(self.recipient) > 160:
            raise ValueError("fan-out receipt recipient is invalid")
        if not self.idempotency_key.startswith("fanout:"):
            raise ValueError("fan-out receipt idempotency key is invalid")
        if len(self.message_sha256) != 64:
            raise ValueError("fan-out receipt content hash is invalid")
        if self.status == "accepted":
            if not self.provider_receipt_id or self.reason_code is not None:
                raise ValueError("accepted fan-out receipt is incomplete")
        elif self.provider_receipt_id is not None or not self.reason_code:
            raise ValueError("rejected fan-out receipt is incomplete")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != (
            UTC.utcoffset(self.observed_at)
        ):
            raise ValueError("fan-out receipt time must be UTC")
        if self.observed_at.microsecond:
            raise ValueError("fan-out receipt time must use whole seconds")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaFanoutRecipientReceipt/v1",
            "idempotency_key": self.idempotency_key,
            "message_sha256": self.message_sha256,
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "provider_receipt_id": self.provider_receipt_id,
            "reason_code": self.reason_code,
            "recipient": self.recipient,
            "status": self.status,
        }

    @classmethod
    def from_payload(cls, raw: object) -> FanoutRecipientReceipt:
        if not isinstance(raw, dict) or raw.get("contract") != (
            "EvaFanoutRecipientReceipt/v1"
        ):
            raise FanoutDeliveryError("fan-out receipt is malformed")
        try:
            value = cls(
                recipient=str(raw["recipient"]),
                idempotency_key=str(raw["idempotency_key"]),
                message_sha256=str(raw["message_sha256"]),
                status=str(raw["status"]),  # type: ignore[arg-type]
                provider_receipt_id=(
                    None
                    if raw["provider_receipt_id"] is None
                    else str(raw["provider_receipt_id"])
                ),
                reason_code=(
                    None if raw["reason_code"] is None else str(raw["reason_code"])
                ),
                observed_at=datetime.fromisoformat(
                    str(raw["observed_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FanoutDeliveryError("fan-out receipt is malformed") from error
        if value.to_payload() != raw:
            raise FanoutDeliveryError("fan-out receipt shape differs")
        return value


@dataclass(frozen=True, slots=True)
class FanoutDeliveryResult:
    request: FanoutDeliveryRequest
    receipts: tuple[FanoutRecipientReceipt, ...]

    def __post_init__(self) -> None:
        if tuple(item.recipient for item in self.receipts) != self.request.recipients:
            raise ValueError("fan-out result order or coverage differs")

    @property
    def accepted_count(self) -> int:
        return sum(item.status == "accepted" for item in self.receipts)

    @property
    def rejected_count(self) -> int:
        return sum(item.status == "rejected" for item in self.receipts)

    def to_payload(self) -> dict[str, object]:
        return {
            "accepted_count": self.accepted_count,
            "contract": "EvaFanoutDeliveryResult/v1",
            "receipts": [item.to_payload() for item in self.receipts],
            "rejected_count": self.rejected_count,
            "request_sha256": self.request.request_sha256,
            "status": "completed_with_rejections"
            if self.rejected_count
            else "completed",
        }


class FanoutDeliverySinkPort(Protocol):
    async def readback(
        self, *, recipient: str, idempotency_key: str
    ) -> FanoutRecipientReceipt | None: ...

    async def deliver(
        self,
        *,
        recipient: str,
        message: str,
        message_sha256: str,
        idempotency_key: str,
    ) -> FanoutRecipientReceipt: ...


class FanoutDeliveryStorePort(Protocol):
    def admit(self, request: FanoutDeliveryRequest) -> str: ...

    def result_for(
        self, request: FanoutDeliveryRequest, recipient: str
    ) -> FanoutRecipientReceipt | None: ...

    def prepare(self, request: FanoutDeliveryRequest, recipient: str) -> str: ...

    def record(
        self, request: FanoutDeliveryRequest, receipt: FanoutRecipientReceipt
    ) -> str: ...

    def complete(self, request: FanoutDeliveryRequest) -> FanoutDeliveryResult: ...


class FanoutDeliveryWorker:
    def __init__(
        self, store: FanoutDeliveryStorePort, sink: FanoutDeliverySinkPort
    ) -> None:
        self._store = store
        self._sink = sink

    async def run(self, request: FanoutDeliveryRequest) -> FanoutDeliveryResult:
        self._store.admit(request)
        for recipient in request.recipients:
            if self._store.result_for(request, recipient) is not None:
                continue
            self._store.prepare(request, recipient)
            key = request.idempotency_key(recipient)
            receipt = await self._sink.readback(
                recipient=recipient, idempotency_key=key
            )
            if receipt is None:
                receipt = await self._sink.deliver(
                    recipient=recipient,
                    message=request.message,
                    message_sha256=request.message_sha256,
                    idempotency_key=key,
                )
            if (
                receipt.recipient != recipient
                or receipt.idempotency_key != key
                or receipt.message_sha256 != request.message_sha256
            ):
                raise FanoutDeliveryError("fan-out provider receipt differs")
            self._store.record(request, receipt)
        return self._store.complete(request)


__all__ = [
    "FanoutDeliveryError",
    "FanoutDeliveryRequest",
    "FanoutDeliveryResult",
    "FanoutDeliverySinkPort",
    "FanoutDeliveryStorePort",
    "FanoutDeliveryWorker",
    "FanoutRecipientReceipt",
]
