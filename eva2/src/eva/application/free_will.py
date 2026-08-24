"""Bounded, durable shadow decisions for passive sensory events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from eva.application.conversation import IdentityProfile
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.application.sensory import SensoryEvent
from eva.application.resident_attention import ResidentAttentionDecision


WillDisposition = Literal["silence", "respond"]


@dataclass(frozen=True, slots=True)
class SensoryDecisionClaim:
    event: SensoryEvent
    thread_id: str
    operation_id: str
    revision: int
    retry_count: int


@dataclass(frozen=True, slots=True)
class FreeWillDecision:
    disposition: WillDisposition
    reason: str
    urgency: int
    provider: str
    expression: str | None = None

    def __post_init__(self) -> None:
        if self.disposition not in {"silence", "respond"}:
            raise ValueError("free-will disposition is invalid")
        if not self.reason.strip() or len(self.reason) > 600:
            raise ValueError("free-will reason is invalid")
        if not 0 <= self.urgency <= 3:
            raise ValueError("free-will urgency is outside 0..3")
        if not self.provider.strip():
            raise ValueError("free-will provider is required")
        if self.disposition == "silence" and self.expression is not None:
            raise ValueError("silent free-will decision cannot have an expression")
        if self.disposition == "respond":
            if self.urgency < 1:
                raise ValueError("expressed free-will decision requires urgency")
            if (
                not isinstance(self.expression, str)
                or not self.expression.strip()
                or len(self.expression) > 600
            ):
                raise ValueError("free-will expression is invalid")


@dataclass(frozen=True, slots=True)
class ProactiveExpressionClaim:
    thread_id: str
    operation_id: str
    revision: int
    retry_count: int
    source_event_id: str
    text: str
    urgency: int


class ProactiveExpressionStorePort(Protocol):
    def claim_next_expression(
        self, *, now: datetime
    ) -> ProactiveExpressionClaim | None: ...

    def acknowledge_expression(
        self,
        claim: ProactiveExpressionClaim,
        *,
        channel: str,
        provider_message_id: str,
        now: datetime,
    ) -> None: ...

    def fail_expression(
        self, claim: ProactiveExpressionClaim, error: Exception, *, now: datetime
    ) -> None: ...


class ProactiveDeliveryPort(Protocol):
    def deliver(self, claim: ProactiveExpressionClaim) -> str: ...


class ProactiveExpressionWorker:
    """Deliver one committed expression, then persist the provider receipt."""

    def __init__(
        self,
        store: ProactiveExpressionStorePort,
        delivery: ProactiveDeliveryPort,
        *,
        channel: str,
    ) -> None:
        if channel not in {"telegram", "test"}:
            raise ValueError("proactive delivery channel is invalid")
        self._store = store
        self._delivery = delivery
        self._channel = channel

    def run_once(self, *, now: datetime) -> str:
        claim = self._store.claim_next_expression(now=now)
        if claim is None:
            return "idle"
        try:
            provider_message_id = self._delivery.deliver(claim)
            self._store.acknowledge_expression(
                claim,
                channel=self._channel,
                provider_message_id=provider_message_id,
                now=now,
            )
            return "delivered"
        except Exception as error:
            self._store.fail_expression(claim, error, now=now)
            return "failed"


class FreeWillStorePort(Protocol):
    def claim_next(self, *, now: datetime) -> SensoryDecisionClaim | None: ...

    def complete(
        self,
        claim: SensoryDecisionClaim,
        decision: FreeWillDecision,
        *,
        now: datetime,
    ) -> None: ...

    def fail(
        self, claim: SensoryDecisionClaim, error: Exception, *, now: datetime
    ) -> None: ...


class FreeWillModelPort(Protocol):
    def evaluate_sensory_event(
        self,
        event: SensoryEvent,
        identity: IdentityProfile,
        memory: PresenceMemory,
        attention: ResidentAttentionDecision | None = None,
    ) -> FreeWillDecision: ...


class ResidentAttentionPort(Protocol):
    def evaluate(
        self, event: SensoryEvent, *, now: datetime
    ) -> ResidentAttentionDecision | None: ...


class FreeWillMemoryPort(Protocol):
    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory: ...


class SensoryDecisionWorker:
    def __init__(
        self,
        store: FreeWillStorePort,
        model: FreeWillModelPort,
        memory: FreeWillMemoryPort,
        identity: IdentityProfile,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        resident_attention: ResidentAttentionPort | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._memory = memory
        self._identity = identity
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._principal_id = principal_id
        self._resident_attention = resident_attention

    def run_once(self, *, now: datetime) -> str:
        claim = self._store.claim_next(now=now)
        if claim is None:
            return "idle"
        event = claim.event
        try:
            bounded_decision = _bounded_routine_decision(event)
            if bounded_decision is not None:
                self._store.complete(claim, bounded_decision, now=now)
                return bounded_decision.disposition
            attention = (
                None
                if self._resident_attention is None
                else self._resident_attention.evaluate(event, now=now)
            )
            if attention is not None and attention.disposition == "silence":
                decision = FreeWillDecision(
                    "silence",
                    attention.reason,
                    0,
                    f"eva:resident-attention:{attention.policy_rule}",
                )
                self._store.complete(claim, decision, now=now)
                return "silence"
            memory = self._memory.recall_for_presence(
                RecallCurrentFacts(
                    recall_id=f"free-will:{event.source_event_id}",
                    installation_id=self._installation_id,
                    tenant_id=self._tenant_id,
                    principal_id=self._principal_id,
                    audience=MemoryAudience.private_person(
                        principal_id=self._principal_id
                    ),
                    conversation_id="sensory:home-assistant",
                    project_id=None,
                    query_purpose="passive_event_decision",
                    requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    scope={
                        "event_type": event.event_type,
                        "kind": "household_event",
                        "source": event.source,
                    },
                    subject_ids=(self._principal_id,),
                    query_text=(
                        f"{event.source} {event.event_type} "
                        f"{event.payload.get('entity_id', '')} "
                        f"{event.payload.get('old_state', '')} naar "
                        f"{event.payload.get('new_state', '')}"
                    ),
                    maximum_facts=8,
                )
            )
            decision = self._model.evaluate_sensory_event(
                event,
                self._identity,
                memory,
                attention,
            )
            if attention is not None and (
                decision.disposition != "respond"
                or decision.urgency < attention.urgency_floor
            ):
                raise RuntimeError(
                    "sensory model violated the resident attention eligibility boundary"
                )
            self._store.complete(claim, decision, now=now)
            return decision.disposition
        except Exception as error:
            self._store.fail(claim, error, now=now)
            return "failed"


def _bounded_routine_decision(event: SensoryEvent) -> FreeWillDecision | None:
    """Silence ordinary actuator telemetry without spending recall/model budget."""

    entity_id = event.payload.get("entity_id")
    old_state = event.payload.get("old_state")
    new_state = event.payload.get("new_state")
    if (
        isinstance(entity_id, str)
        and entity_id.split(".", 1)[0] in {"light", "switch"}
        and old_state in {"on", "off"}
        and new_state in {"on", "off"}
    ):
        return FreeWillDecision(
            "silence",
            "Gewone bediening van een lamp of schakelaar vraagt niet om aandacht.",
            0,
            "eva:bounded-prefilter",
        )
    return None


__all__ = [
    "FreeWillDecision",
    "ProactiveDeliveryPort",
    "ProactiveExpressionClaim",
    "ProactiveExpressionStorePort",
    "ProactiveExpressionWorker",
    "SensoryDecisionClaim",
    "SensoryDecisionWorker",
    "ResidentAttentionPort",
]
