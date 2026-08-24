"""Append-only commercial publication state separate from product creation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.application.commerce import (
    CommerceError,
    DigitalFulfillmentGrant,
    ProductPublicationApproval,
    PaidOrderReceipt,
    ProductPublicationPlan,
    ProductPublicationReceipt,
)
from eva.contracts.base import JsonValue


class PostgresProductPublicationStore:
    """One publication thread per immutable plan hash."""

    _PREFIX = "commerce:publication:"

    def __init__(self, engine: sa.Engine, *, tenant_id: str, installation_id: str) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    @classmethod
    def thread_id(cls, plan_sha256: str) -> str:
        if len(plan_sha256) != 64:
            raise ValueError("publication plan hash is invalid")
        return cls._PREFIX + plan_sha256

    def retain_plan(self, plan: ProductPublicationPlan) -> str:
        thread_id = self.thread_id(plan.sha256)
        events = self._timeline.events(thread_id)
        if events:
            existing = self._plan_from_event(events[0])
            if existing != plan:
                raise CommerceError("publication plan hash collides with other content")
            return "already_planned"
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:planned",
                operation_id=plan.operation_id,
                phase="publication_planned",
                status="active",
                event_type="PRODUCT_PUBLICATION_PLANNED",
                occurred_at=plan.created_at,
                data={"plan": plan.to_payload(), "plan_sha256": plan.sha256},
            )
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            if not events or self._plan_from_event(events[0]) != plan:
                raise
            return "already_planned"
        return "planned"

    def plan(self, plan_sha256: str) -> ProductPublicationPlan:
        events = self._timeline.events(self.thread_id(plan_sha256))
        if not events:
            raise CommerceError("publication plan does not exist")
        plan = self._plan_from_event(events[0])
        if plan.sha256 != plan_sha256:
            raise CommerceError("publication plan hash differs from timeline")
        return plan

    def approve(
        self,
        plan: ProductPublicationPlan,
        approval: ProductPublicationApproval,
        *,
        now: datetime,
    ) -> str:
        approval.authorize(plan, now=now)
        thread_id = self.thread_id(plan.sha256)
        events = self._timeline.events(thread_id)
        if not events or self._plan_from_event(events[0]) != plan:
            raise CommerceError("publication approval has no exact durable plan")
        prior = self._approval_event(events)
        if prior is not None:
            if self._approval_from_event(prior) != approval:
                raise CommerceError("publication plan already has another approval")
            return "already_approved"
        state = self._timeline.load(thread_id)
        if state.phase != "publication_planned" or state.status != "active":
            raise CommerceError("publication plan is not approvable")
        self._timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:approved",
            operation_id=plan.operation_id,
            phase="publication_approved",
            status="active",
            event_type="PRODUCT_PUBLICATION_APPROVED",
            occurred_at=now,
            deadline=approval.expires_at,
            data={
                "approval": approval.to_payload(),
                "approval_sha256": approval.sha256,
                "plan_sha256": plan.sha256,
            },
        )
        return "approved"

    def open_call(
        self,
        *,
        plan_sha256: str,
        approval_sha256: str,
        now: datetime,
    ) -> str:
        plan = self.plan(plan_sha256)
        events = self._timeline.events(self.thread_id(plan_sha256))
        approval_event = self._approval_event(events)
        if approval_event is None:
            raise CommerceError("publication call has no durable approval")
        approval = self._approval_from_event(approval_event)
        if approval.sha256 != approval_sha256:
            raise CommerceError("publication approval hash differs")
        approval.authorize(plan, now=now)
        receipt_event = self._receipt_event(events)
        if receipt_event is not None:
            self._receipt_from_event(receipt_event)
            return "already_verified"
        state = self._timeline.load(self.thread_id(plan_sha256))
        if state.status == "needs_help":
            raise CommerceError("publication needs human reconciliation")
        if state.phase == "publication_dispatch_opened":
            return "resume_idempotent"
        if state.phase not in {"publication_approved", "publication_dispatch_opened"}:
            raise CommerceError("publication call is not authorized")
        self._timeline.transition(
            thread_id=state.thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{state.thread_id}:dispatch:{state.revision + 1}",
            operation_id=plan.operation_id,
            phase="publication_dispatch_opened",
            status="waiting_for_proof",
            event_type="PRODUCT_PUBLICATION_CALL_OPENED",
            occurred_at=now,
            deadline=approval.expires_at,
            retry_count=state.retry_count,
            last_error_fingerprint=state.last_error_fingerprint,
            data={
                "approval_sha256": approval.sha256,
                "idempotency_key": plan.operation_id,
                "plan_sha256": plan.sha256,
            },
        )
        return "opened"

    def record_verified(
        self,
        plan: ProductPublicationPlan,
        approval: ProductPublicationApproval,
        receipt: ProductPublicationReceipt,
        *,
        now: datetime,
    ) -> str:
        approval.authorize(plan, now=now)
        if (
            receipt.plan_sha256 != plan.sha256
            or receipt.release_sha256 != plan.release_sha256
            or receipt.provider_account_id != plan.provider_account_id
            or receipt.live_mode is not plan.live_mode
            or receipt.price_eur_cents != plan.price_eur_cents
            or not receipt.active
        ):
            raise CommerceError("publication receipt does not prove the approved plan")
        thread_id = self.thread_id(plan.sha256)
        events = self._timeline.events(thread_id)
        prior = self._receipt_event(events)
        if prior is not None:
            if self._receipt_from_event(prior) != receipt:
                raise CommerceError("publication already has another provider receipt")
            return "already_verified"
        state = self._timeline.load(thread_id)
        if state.phase != "publication_dispatch_opened":
            raise CommerceError("publication receipt arrived before durable call fence")
        self._timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:verified",
            operation_id=plan.operation_id,
            phase="publication_verified",
            status="completed",
            event_type="PRODUCT_PUBLICATION_VERIFIED",
            occurred_at=now,
            data={
                "approval_sha256": approval.sha256,
                "plan_sha256": plan.sha256,
                "receipt": receipt.to_payload(),
                "receipt_sha256": receipt.sha256,
            },
        )
        return "verified"

    def record_failure(
        self, *, plan_sha256: str, error: Exception, now: datetime
    ) -> str:
        plan = self.plan(plan_sha256)
        thread_id = self.thread_id(plan_sha256)
        state = self._timeline.load(thread_id)
        if state.phase != "publication_dispatch_opened":
            raise CommerceError("publication failure has no durable call fence")
        failed = self._timeline.record_recovery_failure(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:failed:{state.revision + 1}",
            operation_id=plan.operation_id,
            error_text=f"{type(error).__name__}:{error}",
            occurred_at=now,
        )
        return "needs_help" if failed.status == "needs_help" else "failed"

    def approval(self, plan_sha256: str) -> ProductPublicationApproval:
        events = self._timeline.events(self.thread_id(plan_sha256))
        event = self._approval_event(events)
        if event is None:
            raise CommerceError("publication approval does not exist")
        return self._approval_from_event(event)

    def receipt(self, plan_sha256: str) -> ProductPublicationReceipt | None:
        event = self._receipt_event(
            self._timeline.events(self.thread_id(plan_sha256))
        )
        return None if event is None else self._receipt_from_event(event)

    @staticmethod
    def _data(event: TimelineEvent) -> dict[str, JsonValue]:
        data = event.payload.get("data")
        if not isinstance(data, dict):
            raise CommerceError("publication timeline event is malformed")
        return data

    @classmethod
    def _plan_from_event(cls, event: TimelineEvent) -> ProductPublicationPlan:
        if event.event_type != "PRODUCT_PUBLICATION_PLANNED":
            raise CommerceError("publication timeline does not start with a plan")
        raw = cls._data(event).get("plan")
        if not isinstance(raw, dict):
            raise CommerceError("publication plan event is malformed")
        try:
            plan = ProductPublicationPlan(
                operation_id=str(raw["operation_id"]),
                tenant_id=str(raw["tenant_id"]),
                installation_id=str(raw["installation_id"]),
                principal_id=str(raw["principal_id"]),
                release_intent_id=str(raw["release_intent_id"]),
                release_sha256=str(raw["release_sha256"]),
                headline=str(raw["headline"]),
                description=str(raw["description"]),
                price_eur_cents=int(raw["price_minor_units"]),
                provider=str(raw["provider"]),
                provider_account_id=str(raw["provider_account_id"]),
                live_mode=raw["live_mode"],  # type: ignore[arg-type]
                created_at=_parse_timestamp(str(raw["created_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommerceError("publication plan event is invalid") from error
        if (
            raw.get("contract") != "EvaProductPublicationPlan/v1"
            or raw.get("price_currency") != "EUR"
            or cls._data(event).get("plan_sha256") != plan.sha256
        ):
            raise CommerceError("publication plan event binding differs")
        return plan

    @staticmethod
    def _approval_event(events: tuple[TimelineEvent, ...]) -> TimelineEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.event_type == "PRODUCT_PUBLICATION_APPROVED"
            ),
            None,
        )

    @classmethod
    def _approval_from_event(
        cls, event: TimelineEvent
    ) -> ProductPublicationApproval:
        raw = cls._data(event).get("approval")
        if not isinstance(raw, dict):
            raise CommerceError("publication approval event is malformed")
        try:
            approval = ProductPublicationApproval(
                approval_id=str(raw["approval_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                release_sha256=str(raw["release_sha256"]),
                provider_account_id=str(raw["provider_account_id"]),
                live_mode=raw["live_mode"],  # type: ignore[arg-type]
                approved_by=str(raw["approved_by"]),
                approved_at=_parse_timestamp(str(raw["approved_at"])),
                expires_at=_parse_timestamp(str(raw["expires_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommerceError("publication approval event is invalid") from error
        if (
            raw.get("contract") != "EvaProductPublicationApproval/v1"
            or cls._data(event).get("approval_sha256") != approval.sha256
        ):
            raise CommerceError("publication approval event binding differs")
        return approval

    @staticmethod
    def _receipt_event(events: tuple[TimelineEvent, ...]) -> TimelineEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.event_type == "PRODUCT_PUBLICATION_VERIFIED"
            ),
            None,
        )

    @classmethod
    def _receipt_from_event(cls, event: TimelineEvent) -> ProductPublicationReceipt:
        raw = cls._data(event).get("receipt")
        if not isinstance(raw, dict):
            raise CommerceError("publication receipt event is malformed")
        try:
            receipt = ProductPublicationReceipt(
                provider=str(raw["provider"]),
                provider_account_id=str(raw["provider_account_id"]),
                provider_resource_id=str(raw["provider_resource_id"]),
                public_url=str(raw["public_url"]),
                plan_sha256=str(raw["plan_sha256"]),
                release_sha256=str(raw["release_sha256"]),
                price_eur_cents=int(raw["price_minor_units"]),
                live_mode=raw["live_mode"],  # type: ignore[arg-type]
                active=raw["active"],  # type: ignore[arg-type]
                observed_at=_parse_timestamp(str(raw["observed_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommerceError("publication receipt event is invalid") from error
        if (
            raw.get("contract") != "EvaProductPublicationReceipt/v1"
            or raw.get("price_currency") != "EUR"
            or cls._data(event).get("receipt_sha256") != receipt.sha256
        ):
            raise CommerceError("publication receipt event binding differs")
        return receipt


class PostgresPaidOrderStore:
    """Register a paid Checkout Session exactly once, before fulfillment."""

    _PREFIX = "commerce:order:"

    def __init__(self, engine: sa.Engine, *, tenant_id: str, installation_id: str) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    @classmethod
    def thread_id(cls, checkout_session_id: str) -> str:
        if not checkout_session_id.startswith("cs_") or len(checkout_session_id) > 168:
            raise ValueError("checkout session ID is invalid")
        return cls._PREFIX + hashlib.sha256(
            checkout_session_id.encode("utf-8")
        ).hexdigest()

    def retain_paid(
        self,
        receipt: PaidOrderReceipt,
        publication: ProductPublicationReceipt,
    ) -> str:
        if (
            receipt.provider_resource_id != publication.provider_resource_id
            or receipt.plan_sha256 != publication.plan_sha256
            or receipt.release_sha256 != publication.release_sha256
            or receipt.amount_eur_cents != publication.price_eur_cents
            or receipt.live_mode is not publication.live_mode
            or not publication.active
        ):
            raise CommerceError("paid order does not bind the verified publication")
        thread_id = self.thread_id(receipt.checkout_session_id)
        events = self._timeline.events(thread_id)
        if events:
            existing = self._receipt_from_event(events[0])
            if not self._same_payment(existing, receipt):
                raise CommerceError("checkout session collides with another paid order")
            return "already_recorded"
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:paid",
                operation_id=f"commerce:fulfill:{receipt.checkout_session_id}",
                phase="payment_verified",
                status="active",
                event_type="PAYMENT_VERIFIED",
                occurred_at=receipt.paid_at,
                data={
                    "publication_receipt_sha256": publication.sha256,
                    "receipt": receipt.to_payload(),
                    "receipt_sha256": receipt.sha256,
                },
            )
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            if not events or not self._same_payment(
                self._receipt_from_event(events[0]), receipt
            ):
                raise
            return "already_recorded"
        return "recorded"

    def load(self, checkout_session_id: str) -> PaidOrderReceipt:
        events = self._timeline.events(self.thread_id(checkout_session_id))
        if not events:
            raise CommerceError("paid order does not exist")
        return self._receipt_from_event(events[0])

    def grant_fulfillment(
        self,
        *,
        checkout_session_id: str,
        release_sha256: str,
        filename: str,
        byte_length: int,
        now: datetime,
    ) -> tuple[str, DigitalFulfillmentGrant]:
        thread_id = self.thread_id(checkout_session_id)
        events = self._timeline.events(thread_id)
        if not events:
            raise CommerceError("fulfillment has no paid order")
        paid = self._receipt_from_event(events[0])
        if paid.release_sha256 != release_sha256:
            raise CommerceError("fulfillment release differs from paid order")
        prior = self._fulfillment_event(events)
        if prior is not None:
            grant = self._grant_from_event(prior)
            if (
                grant.checkout_session_id != checkout_session_id
                or grant.paid_order_sha256 != paid.sha256
                or grant.release_sha256 != release_sha256
                or grant.filename != filename
                or grant.byte_length != byte_length
            ):
                raise CommerceError("paid order already grants another artifact")
            return "already_granted", grant
        grant = DigitalFulfillmentGrant(
            checkout_session_id=checkout_session_id,
            paid_order_sha256=paid.sha256,
            release_sha256=release_sha256,
            filename=filename,
            byte_length=byte_length,
            granted_at=now,
        )
        state = self._timeline.load(thread_id)
        if state.phase != "payment_verified" or state.status != "active":
            raise CommerceError("paid order is not fulfillable")
        self._timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:fulfillment",
            operation_id=f"commerce:fulfill:{checkout_session_id}",
            phase="fulfillment_granted",
            status="completed",
            event_type="FULFILLMENT_GRANTED",
            occurred_at=now,
            data={
                "grant": grant.to_payload(),
                "grant_sha256": grant.sha256,
            },
        )
        return "granted", grant

    @staticmethod
    def _same_payment(first: PaidOrderReceipt, second: PaidOrderReceipt) -> bool:
        return (
            first.checkout_session_id == second.checkout_session_id
            and first.provider_resource_id == second.provider_resource_id
            and first.plan_sha256 == second.plan_sha256
            and first.release_sha256 == second.release_sha256
            and first.amount_eur_cents == second.amount_eur_cents
            and first.live_mode is second.live_mode
        )

    @staticmethod
    def _receipt_from_event(event: TimelineEvent) -> PaidOrderReceipt:
        if event.event_type != "PAYMENT_VERIFIED":
            raise CommerceError("order timeline does not start with payment proof")
        data = event.payload.get("data")
        raw = data.get("receipt") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise CommerceError("paid order event is malformed")
        try:
            receipt = PaidOrderReceipt(
                provider=str(raw["provider"]),
                provider_event_id=str(raw["provider_event_id"]),
                checkout_session_id=str(raw["checkout_session_id"]),
                provider_resource_id=str(raw["provider_resource_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                release_sha256=str(raw["release_sha256"]),
                amount_eur_cents=int(raw["amount_minor_units"]),
                live_mode=raw["live_mode"],  # type: ignore[arg-type]
                buyer_email_sha256=(
                    None
                    if raw.get("buyer_email_sha256") is None
                    else str(raw["buyer_email_sha256"])
                ),
                paid_at=_parse_timestamp(str(raw["paid_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommerceError("paid order event is invalid") from error
        if (
            raw.get("contract") != "EvaPaidOrderReceipt/v1"
            or raw.get("amount_currency") != "EUR"
            or not isinstance(data, dict)
            or data.get("receipt_sha256") != receipt.sha256
        ):
            raise CommerceError("paid order event binding differs")
        return receipt

    @staticmethod
    def _fulfillment_event(
        events: tuple[TimelineEvent, ...],
    ) -> TimelineEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.event_type == "FULFILLMENT_GRANTED"
            ),
            None,
        )

    @staticmethod
    def _grant_from_event(event: TimelineEvent) -> DigitalFulfillmentGrant:
        data = event.payload.get("data")
        raw = data.get("grant") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise CommerceError("fulfillment event is malformed")
        try:
            grant = DigitalFulfillmentGrant(
                checkout_session_id=str(raw["checkout_session_id"]),
                paid_order_sha256=str(raw["paid_order_sha256"]),
                release_sha256=str(raw["release_sha256"]),
                filename=str(raw["filename"]),
                byte_length=int(raw["byte_length"]),
                granted_at=_parse_timestamp(str(raw["granted_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommerceError("fulfillment event is invalid") from error
        if (
            raw.get("contract") != "EvaDigitalFulfillmentGrant/v1"
            or raw.get("media_type") != "application/zip"
            or not isinstance(data, dict)
            or data.get("grant_sha256") != grant.sha256
        ):
            raise CommerceError("fulfillment event binding differs")
        return grant


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if parsed.microsecond:
        raise ValueError("timestamp is not second precise")
    return parsed


__all__ = ["PostgresPaidOrderStore", "PostgresProductPublicationStore"]
