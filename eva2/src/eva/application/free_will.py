"""Bounded, durable shadow decisions for passive sensory events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Protocol

from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.application.sensory import SensoryEvent, SensoryEventRevalidatorPort
from eva.application.resident_attention import ResidentAttentionDecision


WillDisposition = Literal["silence", "respond"]
AutonomousAdmissionBlocker = Literal["unverified_model_cost"]


class AutonomousUsageUncertain(RuntimeError):
    """A possibly dispatched decision has no durable outcome; do not repeat it."""


@dataclass(frozen=True, slots=True)
class SensoryDecisionClaim:
    event: SensoryEvent
    thread_id: str
    operation_id: str
    revision: int
    retry_count: int


@dataclass(frozen=True, slots=True)
class PersonalInitiativeProposal:
    """Eva's own bounded research idea; never an owner instruction or mandate."""

    title: str
    motivation: str
    next_exploration: str

    def __post_init__(self) -> None:
        for name, limit in (("title", 160), ("motivation", 800), ("next_exploration", 800)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("personal initiative proposal is invalid")

    def adoption(self, claim: SensoryDecisionClaim, *, principal_id: str) -> dict[str, str]:
        """Use a separate source namespace, retaining durable event provenance."""
        import hashlib
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("personal initiative needs the configured owner scope")
        source_id = claim.event.source_event_id
        digest = hashlib.sha256((principal_id + "\0" + source_id).encode()).hexdigest()
        return {
            "action_name": "adopt_personal_interest",
            "action_call_id": "initiative:" + digest,
            "intent_id": "personal-interest:" + digest,
            "kind": "personal_interest",
            "origin": "eva_initiative",
            "principal_id": principal_id,
            "conversation_id": "initiative:home-assistant",
            "source_message_id": "sensory:" + source_id,
            "source_event_id": source_id,
            "source_thread_id": claim.thread_id,
            "source_operation_id": claim.operation_id,
            "title": self.title.strip(),
            "motivation": self.motivation.strip(),
            "next_exploration": self.next_exploration.strip(),
            "authority": "current_host_costless_mandate_required",
            "execution_mode": "autonomous_task",
        }


@dataclass(frozen=True, slots=True)
class FreeWillDecision:
    disposition: WillDisposition
    reason: str
    urgency: int
    provider: str
    expression: str | None = None
    usage: ConversationModelUsage | None = None
    initiative: PersonalInitiativeProposal | None = None

    def __post_init__(self) -> None:
        if self.initiative is not None and not isinstance(self.initiative, PersonalInitiativeProposal):
            raise ValueError("personal initiative must be a validated proposal")
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
    def mark_model_started(
        self, claim: SensoryDecisionClaim, *, now: datetime
    ) -> SensoryDecisionClaim | None: ...

    def claim_next_revalidation(self, *, now: datetime) -> SensoryDecisionClaim | None: ...

    def complete_revalidation(
        self, claim: SensoryDecisionClaim, event: SensoryEvent, *, now: datetime
    ) -> None: ...

    def claim_revalidated(
        self, claim: SensoryDecisionClaim, *, now: datetime
    ) -> SensoryDecisionClaim | None: ...

    def claim_next(
        self, *, now: datetime, include_waiting: bool = True
    ) -> SensoryDecisionClaim | None: ...

    def wait_for_admission(
        self, claim: SensoryDecisionClaim, reason: AutonomousAdmissionBlocker,
        *, now: datetime,
    ) -> None: ...

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
        initiative_history: tuple[dict[str, str], ...] = (),
        initiative_capabilities: tuple[str, ...] = (),
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
        memory_principal_resolver: Callable[[], str] | None = None,
        resident_attention: ResidentAttentionPort | None = None,
        admission_blocker: Callable[[], AutonomousAdmissionBlocker | None] | None = None,
        revalidator: SensoryEventRevalidatorPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._memory = memory
        self._identity = identity
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._principal_id = principal_id
        self._memory_principal_resolver = memory_principal_resolver
        self._resident_attention = resident_attention
        self._admission_blocker = admission_blocker
        self._revalidator = revalidator
        # Runtime supplies a live UTC clock. Without one, run_once(now=...)
        # deliberately operates on the caller's fixed logical/test clock.
        self._clock = clock
        self._prefer_revalidation = False

    def _current_time(self, previous: datetime) -> datetime:
        current = previous if self._clock is None else self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("sensory worker clock must be timezone-aware")
        return max(previous, current)

    def run_once(self, *, now: datetime) -> str:
        now = self._current_time(now)
        blocker = None if self._admission_blocker is None else self._admission_blocker()
        if self._prefer_revalidation and blocker is None and self._revalidator is not None:
            self._prefer_revalidation = False
            recovered = self._revalidate_once(now=now)
            if recovered != "idle":
                return recovered
        claim = self._store.claim_next(now=now, include_waiting=blocker is None)
        if claim is None:
            if blocker is None and self._revalidator is not None:
                return self._revalidate_once(now=now)
            return "idle"
        self._prefer_revalidation = True
        return self._decide_claim(claim, now=now, blocker=blocker)

    def _decide_claim(
        self, claim: SensoryDecisionClaim, *, now: datetime,
        blocker: AutonomousAdmissionBlocker | None,
    ) -> str:
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
            if blocker is not None:
                self._store.wait_for_admission(claim, blocker, now=now)
                return "waiting_for_admission"
            current_state = event.payload.get("current_state")
            state_description = (
                f"actuele toestand: {current_state}"
                if event.event_type == "observation_revalidated"
                and isinstance(current_state, str) and current_state
                else f"{event.payload.get('old_state', '')} naar "
                f"{event.payload.get('new_state', '')}"
            )
            # Resolve the trusted owner alias afresh so revocation takes effect
            # without rewriting existing private memories or widening their audience.
            memory_principal = (self._principal_id if self._memory_principal_resolver is None
                                else self._memory_principal_resolver())
            memory = self._memory.recall_for_presence(
                RecallCurrentFacts(
                    recall_id=f"free-will:{event.source_event_id}",
                    installation_id=self._installation_id,
                    tenant_id=self._tenant_id,
                    principal_id=memory_principal,
                    audience=MemoryAudience.private_person(
                        principal_id=memory_principal
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
                    subject_ids=(memory_principal,),
                    query_text=(
                        "Persoonlijke plannen, dagelijkse behoeften, interesses en voorkeuren "
                        "voor een nuttige eigen bijdrage. Context bij deze waarneming: "
                        f"{event.source} {event.event_type} "
                        f"{event.payload.get('entity_id', '')} "
                        f"{state_description}"
                    ),
                    maximum_facts=8,
                )
            )
            history_reader = getattr(self._store, "recent_initiatives", None)
            history = () if not callable(history_reader) else history_reader()
            capability_reader = getattr(self._store, "native_capabilities", None)
            capabilities = () if not callable(capability_reader) else capability_reader()
            # Persist the ambiguity boundary before entering the model adapter.
            # A process can die without calling fail(), including after the
            # provider response but before complete() records the decision.
            now = self._current_time(now)
            started = self._store.mark_model_started(claim, now=now)
            if started is None:
                return "idle"  # Expired or competing owner: never dispatch.
            claim = started
            decision = self._model.evaluate_sensory_event(
                event,
                self._identity,
                memory,
                attention,
                **({"initiative_history": history} if history else {}),
                **({"initiative_capabilities": capabilities} if callable(capability_reader) else {}),
            )
            if attention is not None and (
                decision.disposition != "respond"
                or decision.urgency < attention.urgency_floor
            ):
                raise RuntimeError(
                    "sensory model violated the resident attention eligibility boundary"
                )
            self._store.complete(claim, decision, now=self._current_time(now))
            return decision.disposition
        except Exception as error:
            self._store.fail(claim, error, now=self._current_time(now))
            return "failed"


    def _revalidate_once(self, *, now: datetime) -> str:
        claim = self._store.claim_next_revalidation(now=now)
        if claim is None:
            return "idle"
        try:
            bounded_decision = _bounded_routine_decision(claim.event)
            if bounded_decision is not None:
                self._store.complete(claim, bounded_decision, now=now)
                return bounded_decision.disposition
            assert self._revalidator is not None
            event = self._revalidator.revalidate(claim.event, now=now)
            now = self._current_time(now)
            self._store.complete_revalidation(claim, event, now=now)
        except Exception as error:
            self._store.fail(claim, error, now=self._current_time(now))
            return "failed"
        # Continue only this exact durable read, without another idle interval
        # or an unbounded worker loop. A competing/restarted worker must win
        # the same revision claim before any recall/model work can happen.
        now = self._current_time(now)
        decision_claim = self._store.claim_revalidated(claim, now=now)
        if decision_claim is None:
            return "revalidated"
        blocker = None if self._admission_blocker is None else self._admission_blocker()
        return self._decide_claim(decision_claim, now=now, blocker=blocker)


def _bounded_routine_decision(event: SensoryEvent) -> FreeWillDecision | None:
    """Silence ordinary actuator telemetry without spending recall/model budget."""

    if event.event_type == "historical_observation_batch":
        members = event.payload.get("historical_observations")
        if not isinstance(members, list) or not members:
            return None
        for member in members:
            if (not isinstance(member, dict) or member.get("event_type") != "state_changed"
                    or not isinstance(member.get("payload"), dict)):
                return None
            decision = _bounded_routine_decision(SensoryEvent(
                event.source, event.source_event_id, "state_changed", event.occurred_at,
                member["payload"],
            ))
            if decision is None:
                return None
        return decision  # Every original already qualified for the same no-model silence.

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
