"""Append-only PostgreSQL state for connector-neutral fan-out delivery."""

from __future__ import annotations

from datetime import datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.delivery import (
    FanoutDeliveryError,
    FanoutDeliveryRequest,
    FanoutDeliveryResult,
    FanoutRecipientReceipt,
)
from eva.contracts.canonical_json import canonical_json_bytes


class PostgresFanoutDeliveryStore:
    def __init__(
        self, engine: sa.Engine, *, tenant_id: str, installation_id: str
    ) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("fan-out store scope is required")
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
            ),
        )

    @staticmethod
    def _identity_thread_id(*, operation_id: str, requested_by: str) -> str:
        identity = {
            "operation_id": operation_id,
            "requested_by": requested_by,
        }
        return "fanout:" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()

    @classmethod
    def _thread_id(cls, request: FanoutDeliveryRequest) -> str:
        return cls._identity_thread_id(
            operation_id=request.operation_id, requested_by=request.requested_by
        )

    @staticmethod
    def _recipient_key(recipient: str) -> str:
        return hashlib.sha256(recipient.encode()).hexdigest()[:16]

    def admit(self, request: FanoutDeliveryRequest) -> str:
        thread_id = self._thread_id(request)
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"checkpoint:{thread_id}:admitted",
                occurred_at=request.created_at,
                data=request.to_payload(),
                event_type="FANOUT_DELIVERY_ADMITTED",
                phase="fanout_admitted",
                status="active",
                operation_id=request.operation_id,
            )
            return "admitted"
        except CheckpointConflict:
            durable = self._request(thread_id)
            if durable != request:
                raise FanoutDeliveryError("fan-out operation identity collision") from None
            return "already_admitted"

    def find(
        self, *, operation_id: str, requested_by: str
    ) -> FanoutDeliveryRequest | None:
        thread_id = self._identity_thread_id(
            operation_id=operation_id, requested_by=requested_by
        )
        events = self._timeline.events(thread_id)
        if not events:
            return None
        return self._request(thread_id)

    def result_for(
        self, request: FanoutDeliveryRequest, recipient: str
    ) -> FanoutRecipientReceipt | None:
        self._require_request(request)
        matches = [
            event
            for event in self._timeline.events(self._thread_id(request))
            if event.event_type == "FANOUT_RECIPIENT_RECORDED"
            and isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("recipient") == recipient
        ]
        if len(matches) > 1:
            raise FanoutDeliveryError("fan-out recipient has duplicate results")
        if not matches:
            return None
        return FanoutRecipientReceipt.from_payload(matches[0].payload.get("data"))

    def prepare(self, request: FanoutDeliveryRequest, recipient: str) -> str:
        self._require_request(request)
        if recipient not in request.recipients:
            raise FanoutDeliveryError("fan-out recipient is outside request")
        if self.result_for(request, recipient) is not None:
            return "already_recorded"
        thread_id = self._thread_id(request)
        data = {
            "idempotency_key": request.idempotency_key(recipient),
            "message_sha256": request.message_sha256,
            "recipient": recipient,
            "request_sha256": request.request_sha256,
        }
        existing = [
            event
            for event in self._timeline.events(thread_id)
            if event.event_type == "FANOUT_RECIPIENT_PREPARED"
            and isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("recipient") == recipient
        ]
        if existing:
            if len(existing) != 1 or existing[0].payload.get("data") != data:
                raise FanoutDeliveryError("fan-out preparation differs")
            return "already_prepared"
        state = self._timeline.load(thread_id)
        try:
            self._timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=(
                    f"checkpoint:{thread_id}:recipient-"
                    f"{self._recipient_key(recipient)}-prepared"
                ),
                occurred_at=datetime.now(tz=request.created_at.tzinfo).replace(
                    microsecond=0
                ),
                data=data,
                event_type="FANOUT_RECIPIENT_PREPARED",
                phase="fanout_recipient_prepared",
                status="active",
                operation_id=request.operation_id,
            )
        except CheckpointConflict as error:
            raise FanoutDeliveryError("fan-out preparation raced another worker") from error
        return "prepared"

    def record(
        self, request: FanoutDeliveryRequest, receipt: FanoutRecipientReceipt
    ) -> str:
        self._require_request(request)
        if (
            receipt.recipient not in request.recipients
            or receipt.idempotency_key
            != request.idempotency_key(receipt.recipient)
            or receipt.message_sha256 != request.message_sha256
        ):
            raise FanoutDeliveryError("fan-out receipt authority differs")
        existing = self.result_for(request, receipt.recipient)
        if existing is not None:
            if existing != receipt:
                raise FanoutDeliveryError("fan-out provider result changed")
            return "already_recorded"
        thread_id = self._thread_id(request)
        prepared = [
            event
            for event in self._timeline.events(thread_id)
            if event.event_type == "FANOUT_RECIPIENT_PREPARED"
            and isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("recipient") == receipt.recipient
        ]
        if len(prepared) != 1:
            raise FanoutDeliveryError("fan-out recipient was not durably prepared")
        state = self._timeline.load(thread_id)
        try:
            self._timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=(
                    f"checkpoint:{thread_id}:recipient-"
                    f"{self._recipient_key(receipt.recipient)}-recorded"
                ),
                occurred_at=receipt.observed_at,
                data=receipt.to_payload(),
                event_type="FANOUT_RECIPIENT_RECORDED",
                phase="fanout_recipient_recorded",
                status="active",
                operation_id=request.operation_id,
            )
        except CheckpointConflict as error:
            raise FanoutDeliveryError("fan-out result raced another worker") from error
        return "recorded"

    def complete(self, request: FanoutDeliveryRequest) -> FanoutDeliveryResult:
        self._require_request(request)
        receipts = tuple(
            self.result_for(request, recipient) for recipient in request.recipients
        )
        if any(item is None for item in receipts):
            raise FanoutDeliveryError("fan-out delivery is not complete")
        result = FanoutDeliveryResult(request, receipts)  # type: ignore[arg-type]
        thread_id = self._thread_id(request)
        completed = [
            event
            for event in self._timeline.events(thread_id)
            if event.event_type == "FANOUT_DELIVERY_COMPLETED"
        ]
        if completed:
            if len(completed) != 1 or completed[0].payload.get("data") != (
                result.to_payload()
            ):
                raise FanoutDeliveryError("fan-out completion differs")
            return result
        state = self._timeline.load(thread_id)
        self._timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"checkpoint:{thread_id}:completed",
            occurred_at=max(item.observed_at for item in result.receipts),
            data=result.to_payload(),
            event_type="FANOUT_DELIVERY_COMPLETED",
            phase="fanout_completed",
            status="completed",
            operation_id=request.operation_id,
        )
        return result

    def _require_request(self, request: FanoutDeliveryRequest) -> None:
        if self._request(self._thread_id(request)) != request:
            raise FanoutDeliveryError("fan-out durable request differs")

    def _request(self, thread_id: str) -> FanoutDeliveryRequest:
        events = self._timeline.events(thread_id)
        if not events or events[0].event_type != "FANOUT_DELIVERY_ADMITTED":
            raise FanoutDeliveryError("fan-out request does not exist")
        return FanoutDeliveryRequest.from_payload(events[0].payload.get("data"))


__all__ = ["PostgresFanoutDeliveryStore"]
