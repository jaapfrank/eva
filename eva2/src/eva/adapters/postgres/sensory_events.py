"""Append-only persistence for normalized connector sensory events."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.sensory import SensoryEvent
from eva.application.free_will import (
    FreeWillDecision,
    ProactiveExpressionClaim,
    SensoryDecisionClaim,
)
from eva.contracts.base import JsonValue


class PostgresSensoryEventStore:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        tenant_id: str,
        installation_id: str,
        decision_lease_seconds: int = 60,
    ) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        if not 10 <= decision_lease_seconds <= 300:
            raise ValueError("sensory decision lease is outside 10..300 seconds")
        self._decision_lease_seconds = decision_lease_seconds

    def retain(self, event: SensoryEvent) -> str:
        thread_id = f"sensory:{event.source_event_id}"
        data: dict[str, JsonValue] = {
            "event_type": event.event_type,
            "payload": event.payload,
            "source": event.source,
            "source_event_id": event.source_event_id,
        }
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"checkpoint:{event.source_event_id}:observed",
                occurred_at=event.occurred_at,
                data=data,
                event_type="SENSORY_EVENT_OBSERVED",
                phase="sensory_observed",
                status="completed",
                operation_id=event.source_event_id,
            )
            return "retained"
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            durable = events[0].payload.get("data") if events else None
            if durable != data:
                raise RuntimeError("sensory event identity collision") from None
            return "already_retained"

    def claim_next(self, *, now: datetime) -> SensoryDecisionClaim | None:
        states = self._timeline.list_checkpoints(
            thread_prefix="sensory:home-assistant:",
            phases=("sensory_observed", "will_claimed"),
            statuses=("completed", "active", "failed"),
            updated_before=now,
            limit=64,
        )
        for state in reversed(states):
            if state.phase == "will_claimed" and state.status == "active":
                if state.deadline is None or state.deadline > now:
                    continue
                state = self._timeline.record_recovery_failure(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:will-recovery:{state.retry_count + 1}",
                    operation_id=state.operation_id,
                    error_text="sensory will evaluation lease expired",
                    occurred_at=now,
                )
                if state.status == "needs_help":
                    continue
            if state.status == "failed" and state.retry_count >= 3:
                continue
            event = self._event_from_thread(state.thread_id)
            try:
                claimed = self._timeline.transition(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:will-claimed:{state.revision + 1}",
                    operation_id=state.operation_id,
                    phase="will_claimed",
                    status="active",
                    event_type=(
                        "WILL_EVALUATION_CLAIMED"
                        if state.phase == "sensory_observed"
                        else "WILL_EVALUATION_RECLAIMED"
                    ),
                    occurred_at=now,
                    deadline=now + timedelta(seconds=self._decision_lease_seconds),
                    data={
                        "source_event_id": event.source_event_id,
                        "shadow_mode": True,
                    },
                    retry_count=state.retry_count,
                    last_error_fingerprint=state.last_error_fingerprint,
                )
            except CheckpointConflict:
                continue
            return SensoryDecisionClaim(
                event,
                state.thread_id,
                state.operation_id or event.source_event_id,
                claimed.revision,
                claimed.retry_count,
            )
        return None

    def complete(
        self,
        claim: SensoryDecisionClaim,
        decision: FreeWillDecision,
        *,
        now: datetime,
    ) -> None:
        if decision.disposition == "respond" and self._cooldown_applies(
            claim, decision, now=now
        ):
            decision = FreeWillDecision(
                "silence",
                "Een recente proactieve melding dekt deze gebeurtenis; cooldown actief.",
                0,
                "eva:proactive-cooldown",
            )
        expression_pending = decision.disposition == "respond"
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-decided",
            operation_id=claim.operation_id,
            phase="expression_pending" if expression_pending else "will_decided",
            status="completed",
            event_type=(
                "PROACTIVE_EXPRESSION_PREPARED"
                if expression_pending
                else "WILL_DECIDED"
            ),
            occurred_at=now,
            data={
                "disposition": decision.disposition,
                "entity_id": claim.event.payload.get("entity_id"),
                "expression": decision.expression,
                "provider": decision.provider,
                "reason": decision.reason,
                "shadow_mode": True,
                "source_event_id": claim.event.source_event_id,
                "urgency": decision.urgency,
            },
            retry_count=0,
            last_error_fingerprint=None,
        )

    def claim_next_expression(
        self, *, now: datetime
    ) -> ProactiveExpressionClaim | None:
        states = self._timeline.list_checkpoints(
            thread_prefix="sensory:home-assistant:",
            phases=("expression_pending", "expression_claimed"),
            statuses=("completed", "active", "failed"),
            updated_before=now,
            limit=64,
        )
        for state in reversed(states):
            if state.phase == "expression_claimed" and state.status == "active":
                if state.deadline is None or state.deadline > now:
                    continue
                fingerprint = hashlib.sha256(
                    b"proactive expression acceptance is ambiguous"
                ).hexdigest()
                try:
                    self._timeline.transition(
                        thread_id=state.thread_id,
                        expected_revision=state.revision,
                        checkpoint_id=f"{state.thread_id}:expression-ambiguous",
                        operation_id=state.operation_id,
                        phase="expression_ambiguous",
                        status="needs_help",
                        event_type="PROACTIVE_EXPRESSION_AMBIGUOUS",
                        occurred_at=now,
                        data={
                            "reason": (
                                "transport acceptance cannot be proven after "
                                "claim expiry"
                            )
                        },
                        retry_count=3,
                        last_error_fingerprint=fingerprint,
                    )
                except CheckpointConflict:
                    pass
                continue
            if state.status == "failed" and state.retry_count >= 3:
                continue
            prepared = self._prepared_expression(state.thread_id)
            try:
                claimed = self._timeline.transition(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=(
                        f"{state.thread_id}:expression-claimed:{state.revision + 1}"
                    ),
                    operation_id=state.operation_id,
                    phase="expression_claimed",
                    status="active",
                    event_type="PROACTIVE_EXPRESSION_CLAIMED",
                    occurred_at=now,
                    deadline=now + timedelta(seconds=self._decision_lease_seconds),
                    data={
                        "source_event_id": prepared["source_event_id"],
                        "urgency": prepared["urgency"],
                    },
                    retry_count=state.retry_count,
                    last_error_fingerprint=state.last_error_fingerprint,
                )
            except CheckpointConflict:
                continue
            return ProactiveExpressionClaim(
                state.thread_id,
                state.operation_id or str(prepared["source_event_id"]),
                claimed.revision,
                claimed.retry_count,
                str(prepared["source_event_id"]),
                str(prepared["expression"]),
                int(prepared["urgency"]),
            )
        return None

    def acknowledge_expression(
        self,
        claim: ProactiveExpressionClaim,
        *,
        channel: str,
        provider_message_id: str,
        now: datetime,
    ) -> None:
        if channel not in {"telegram", "test"}:
            raise ValueError("proactive expression channel is invalid")
        if not provider_message_id.strip() or len(provider_message_id) > 240:
            raise ValueError("proactive expression receipt is invalid")
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:expression-delivered",
            operation_id=claim.operation_id,
            phase="expression_delivered",
            status="completed",
            event_type="PROACTIVE_EXPRESSION_DELIVERED",
            occurred_at=now,
            data={
                "channel": channel,
                "provider_message_id": provider_message_id,
                "source_event_id": claim.source_event_id,
            },
            retry_count=0,
            last_error_fingerprint=None,
        )

    def acknowledge_expression_receipt(
        self,
        *,
        thread_id: str,
        revision: int,
        channel: str,
        provider_message_id: str,
        now: datetime,
    ) -> None:
        state = self._timeline.load(thread_id)
        if (
            state.phase != "expression_claimed"
            or state.status != "active"
            or state.revision != revision
        ):
            raise CheckpointConflict("proactive expression claim is stale")
        prepared = self._prepared_expression(thread_id)
        claim = ProactiveExpressionClaim(
            thread_id,
            state.operation_id or str(prepared["source_event_id"]),
            state.revision,
            state.retry_count,
            str(prepared["source_event_id"]),
            str(prepared["expression"]),
            int(prepared["urgency"]),
        )
        self.acknowledge_expression(
            claim,
            channel=channel,
            provider_message_id=provider_message_id,
            now=now,
        )

    def fail_expression(
        self, claim: ProactiveExpressionClaim, error: Exception, *, now: datetime
    ) -> None:
        error_text = f"{type(error).__name__}: {error}"
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:expression-ambiguous",
            operation_id=claim.operation_id,
            phase="expression_ambiguous",
            status="needs_help",
            event_type="PROACTIVE_EXPRESSION_AMBIGUOUS",
            occurred_at=now,
            data={"reason": error_text},
            retry_count=3,
            last_error_fingerprint=hashlib.sha256(error_text.encode()).hexdigest(),
        )

    def fail(
        self,
        claim: SensoryDecisionClaim,
        error: Exception,
        *,
        now: datetime,
    ) -> None:
        self._timeline.record_recovery_failure(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-failed:{claim.retry_count + 1}",
            operation_id=claim.operation_id,
            error_text=f"{type(error).__name__}: {error}",
            occurred_at=now,
        )

    def _event_from_thread(self, thread_id: str) -> SensoryEvent:
        events = self._timeline.events(thread_id)
        if not events or events[0].event_type != "SENSORY_EVENT_OBSERVED":
            raise RuntimeError("sensory event admission is missing")
        data = events[0].payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("sensory event admission is malformed")
        payload = data.get("payload")
        source = data.get("source")
        source_event_id = data.get("source_event_id")
        event_type = data.get("event_type")
        if (
            not isinstance(payload, dict)
            or not isinstance(source, str)
            or not isinstance(source_event_id, str)
            or not isinstance(event_type, str)
        ):
            raise RuntimeError("sensory event fields are malformed")
        return SensoryEvent(
            source,
            source_event_id,
            event_type,
            events[0].occurred_at,
            payload,
        )

    def _prepared_expression(self, thread_id: str) -> dict[str, object]:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "PROACTIVE_EXPRESSION_PREPARED":
                continue
            data = event.payload.get("data")
            if not isinstance(data, dict):
                break
            expression = data.get("expression")
            source_event_id = data.get("source_event_id")
            urgency = data.get("urgency")
            if (
                isinstance(expression, str)
                and expression.strip()
                and isinstance(source_event_id, str)
                and isinstance(urgency, int)
                and not isinstance(urgency, bool)
            ):
                return {
                    "expression": expression,
                    "source_event_id": source_event_id,
                    "urgency": urgency,
                }
            break
        raise RuntimeError("proactive expression preparation is missing")

    def _cooldown_applies(
        self,
        claim: SensoryDecisionClaim,
        decision: FreeWillDecision,
        *,
        now: datetime,
    ) -> bool:
        states = self._timeline.list_checkpoints(
            thread_prefix="sensory:home-assistant:",
            phases=("expression_pending", "expression_claimed", "expression_delivered"),
            statuses=("completed", "active", "failed"),
            updated_before=now,
            limit=64,
        )
        entity_id = claim.event.payload.get("entity_id")
        for state in states:
            age = now - state.updated_at
            if age > timedelta(minutes=30):
                continue
            try:
                self._prepared_expression(state.thread_id)
            except RuntimeError:
                continue
            events = self._timeline.events(state.thread_id)
            prepared_data = next(
                (
                    event.payload.get("data")
                    for event in reversed(events)
                    if event.event_type == "PROACTIVE_EXPRESSION_PREPARED"
                ),
                None,
            )
            prior_entity = (
                prepared_data.get("entity_id")
                if isinstance(prepared_data, dict)
                else None
            )
            if entity_id is not None and entity_id == prior_entity:
                return True
            if decision.urgency < 3 and age <= timedelta(minutes=5):
                return True
        return False


__all__ = ["PostgresSensoryEventStore"]
