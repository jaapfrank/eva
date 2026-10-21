"""Bounded, durable shadow decisions for passive sensory events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import datetime
from typing import Callable, Literal, Protocol

from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.application.sensory import SensoryEvent, SensoryEventRevalidatorPort
from eva.application.resident_attention import ResidentAttentionDecision, NextAttention


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
    parent_intent_id: str | None = None
    parent_result_sha256: str | None = None

    def __post_init__(self) -> None:
        import re
        if ((self.parent_intent_id is None) != (self.parent_result_sha256 is None)
                or (self.parent_intent_id is not None and (
                    not isinstance(self.parent_intent_id, str)
                    or re.fullmatch(r"personal-interest:[0-9a-f]{64}", self.parent_intent_id) is None
                    or not isinstance(self.parent_result_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", self.parent_result_sha256) is None))):
            raise ValueError("personal follow-up needs its exact completed parent and result")
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
            "conversation_id": "initiative:" + claim.event.source,
            "source_message_id": "sensory:" + source_id,
            "source_event_id": source_id,
            "source_thread_id": claim.thread_id,
            "source_operation_id": claim.operation_id,
            "title": self.title.strip(),
            "motivation": self.motivation.strip(),
            "next_exploration": self.next_exploration.strip(),
            "authority": "current_host_costless_mandate_required",
            "execution_mode": "autonomous_task",
            **({"followup_source": {"intent_id": self.parent_intent_id,
                "content_sha256": self.parent_result_sha256}} if self.parent_intent_id is not None else {}),
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
    source_context_sha256: str | None = None
    next_attention: NextAttention | None = None
    memory_search_context: dict | None = None

    def __post_init__(self) -> None:
        if self.memory_search_context is not None:
            from eva.contracts.canonical_json import canonical_json_bytes
            if (not isinstance(self.memory_search_context, dict)
                    or len(canonical_json_bytes(self.memory_search_context)) > 8192):
                raise ValueError("private memory search context is invalid")
        if self.next_attention is not None and not isinstance(self.next_attention, NextAttention):
            raise ValueError("next attention must be a validated choice")
        if self.source_context_sha256 is not None:
            import re
            if re.fullmatch(r"[0-9a-f]{64}", self.source_context_sha256) is None:
                raise ValueError("source outcome context hash is invalid")
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
        before_send: Callable[[ProactiveExpressionClaim], None] | None = None,
    ) -> None:
        if channel not in {"telegram", "test"}:
            raise ValueError("proactive delivery channel is invalid")
        self._store = store
        self._delivery = delivery
        self._channel = channel
        self._before_send = before_send

    def run_once(self, *, now: datetime) -> str:
        claim = self._store.claim_next_expression(now=now)
        if claim is None:
            return "idle"
        if self._before_send is not None:
            try:
                self._before_send(claim)
            except Exception as error:
                reject = getattr(self._store, "fail_before_expression_send", None)
                if callable(reject):
                    reject(claim, error, now=now)
                else:
                    self._store.fail_expression(claim, error, now=now)
                return "failed"
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

    def fail_before_dispatch(
        self, claim: SensoryDecisionClaim, error: Exception, *, now: datetime
    ) -> None: ...

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

    def defer_before_dispatch(
        self, claim: SensoryDecisionClaim, *, reason: str, now: datetime
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
        initiative_source_outcomes: tuple[dict, ...] = (),
        private_context_search: Callable[[str], dict] | None = None,
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
        context_review_budget=None,
        context_review_authorizer=None,
        context_review_admission=None,
        household_context=None,
        dispatch_ready: Callable[[], bool] | None = None,
        source_outcome_reader: Callable[..., tuple[dict, ...]] | None = None,
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
        self._context_review_budget = context_review_budget
        self._context_review_authorizer = context_review_authorizer
        self._context_review_admission = context_review_admission
        self._household_context = household_context
        self._dispatch_ready = dispatch_ready
        self._source_outcome_reader = source_outcome_reader
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
        household_review = event.source == "household-initiative"
        context_review = event.source in {"initiative-review", "household-initiative"}
        model_entered = False
        try:
            if household_review != (self._household_context is not None):
                raise RuntimeError("initiative context audience differs from configured worker")
            if context_review:
                if self._context_review_authorizer is None:
                    raise RuntimeError("private review authority is unavailable")
                self._context_review_authorizer()
            cancel_attention = getattr(self._store, "cancel_superseded_attention", None)
            if context_review and callable(cancel_attention) and cancel_attention(claim, now=now):
                return "cancelled"
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
            if context_review:
                # Preserve the existing admission claim before reserving budget.
                # A crash after this marker stays ambiguous; a live preparation
                # failure can still prove that the model adapter was never entered.
                started = self._store.mark_model_started(claim, now=self._current_time(now))
                if started is None:
                    return "idle"
                claim = started
                if self._context_review_admission is not None:
                    admission = self._context_review_admission(claim, now=self._current_time(now))
                else:
                    admission = None if self._context_review_budget is None else self._context_review_budget.admit_background_reflection(
                        tenant_id=self._tenant_id, installation_id=self._installation_id,
                        principal_id=self._principal_id, now=self._current_time(now),
                        reservation_id=claim.thread_id, spread_over_day=True)
                if admission is None or not admission.admitted:
                    self._store.complete(claim, FreeWillDecision("silence",
                        "Het bestaande achtergrondbudget laat nu geen nieuwe beoordeling toe.",
                        0, "eva:background-budget"),
                        now=self._current_time(now))
                    return "waiting_for_budget"
            current_state = event.payload.get("current_state")
            state_description = (
                f"actuele toestand: {current_state}"
                if event.event_type == "observation_revalidated"
                and isinstance(current_state, str) and current_state
                else f"{event.payload.get('old_state', '')} naar "
                f"{event.payload.get('new_state', '')}"
            )
            if household_review:
                memory, binding = self._household_context(event, now, self._memory)
                if binding != event.payload.get("household_binding"):
                    raise RuntimeError("household initiative provenance changed")
                # Capability names are host authority metadata, not owner memory.
                # A household proposal still needs the existing owner grant before
                # any native work executes; its result remains on the private route.
                history = ()
                capability_reader = getattr(self._store, "native_capabilities", None)
                capabilities = () if not callable(capability_reader) else capability_reader()
                if not memory.confirmed_facts and event.event_type not in {
                        "household_observation_review", "household_attention_review"}:
                    self._store.complete(claim, FreeWillDecision("silence",
                        "Er is geen expliciet gedeelde context voor een nuttige huisbijdrage.",
                        0, "eva:household-context-empty"), now=self._current_time(now))
                    return "silence"
            else:
                # Resolve the trusted owner alias afresh so revocation takes effect
                # without rewriting existing private memories or widening their audience.
                memory_principal = (self._principal_id if self._memory_principal_resolver is None
                                    else self._memory_principal_resolver())
                private_query = RecallCurrentFacts(
                    recall_id=f"free-will:{event.source_event_id}",
                    installation_id=self._installation_id,
                    tenant_id=self._tenant_id,
                    principal_id=memory_principal,
                    audience=MemoryAudience.private_person(
                        principal_id=memory_principal
                    ),
                    conversation_id="sensory:" + event.source,
                    project_id=None,
                    query_purpose="private_opportunity_review" if event.source == "initiative-review" else "passive_event_decision",
                    requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    scope={
                        "event_type": event.event_type,
                        "kind": "private_opportunity" if event.source == "initiative-review" else "household_event",
                        "source": event.source,
                    },
                    subject_ids=(memory_principal,),
                    query_text=(
                        "Persoonlijke plannen, dagelijkse behoeften, interesses en voorkeuren "
                        "voor een nuttige eigen bijdrage. "
                        + (_private_review_recall_context(event) if event.source == "initiative-review" else
                           "Context bij deze waarneming: "
                           f"{event.source} {event.event_type} "
                           f"{event.payload.get('entity_id', '')} {state_description}")
                    ),
                    maximum_facts=8,
                )
                memory = self._memory.recall_for_presence(private_query)
                history_reader = getattr(self._store, "recent_initiatives", None)
                history = () if not callable(history_reader) else history_reader()
                contact_history = getattr(self._store, "recent_private_contacts", None)
                if context_review and callable(contact_history):
                    history += contact_history(now=self._current_time(now))
                capability_reader = getattr(self._store, "native_capabilities", None)
                capabilities = () if not callable(capability_reader) else capability_reader()
            # Native outcomes stay private, with original observation times and
            # verified source receipts. This read never invokes an external tool.
            source_outcomes = ()
            if event.source == "initiative-review" and self._source_outcome_reader is not None:
                trigger = event.payload.get("native_completion_source")
                if isinstance(trigger, dict):
                    source_outcomes = self._source_outcome_reader(
                        now=self._current_time(now), work_id=trigger.get("work_id"))
                    # A concurrent outcome must not replace the source that
                    # caused this private review or displace its verified pages.
                    source_outcomes = tuple(value for value in source_outcomes
                        if all(value.get(key) == expected for key, expected in trigger.items()))
                    if not source_outcomes:
                        source_outcomes = ({"work_id": trigger.get("work_id"), "status": "unavailable",
                            "reason": "original_native_outcome_no_longer_available", "verified_reads": []},)
                elif isinstance(event.payload.get("attention_origin"), dict):
                    # A later attention may return to the same exact completed
                    # source. Ordinary clock reviews must not re-litigate every
                    # old failure instead of considering today's opportunity.
                    source_reader = getattr(self._store, "attention_source_binding", None)
                    binding = None if not callable(source_reader) else source_reader(claim)
                    if binding is not None:
                        source_outcomes = tuple(value for value in self._source_outcome_reader(
                            now=self._current_time(now), work_id=binding["work_id"])
                            if all(value.get(key) == expected for key, expected in binding.items()))
                        if not source_outcomes:
                            source_outcomes = ({"work_id": binding["work_id"], "status": "unavailable",
                                "reason": "original_native_outcome_no_longer_available", "verified_reads": []},)
            # Persist the ambiguity boundary before entering the model adapter.
            # A process can die without calling fail(), including after the
            # provider response but before complete() records the decision.
            now = self._current_time(now)
            if context_review:
                self._context_review_authorizer()
                if callable(cancel_attention) and cancel_attention(claim, now=now):
                    return "cancelled"
                if not self._store.context_review_claim_is_current(claim, now=now):
                    raise RuntimeError("context review claim expired before provider dispatch")
            else:
                started = self._store.mark_model_started(claim, now=now)
                if started is None:
                    return "idle"  # Expired or competing owner: never dispatch.
                claim = started
            expire_observation = getattr(self._store, "expire_stale_household_observation", None)
            if household_review and callable(expire_observation) and expire_observation(claim, now=now):
                return "expired"
            # Recall/authority can block while an ordinary conversation starts.
            # This host-only handoff proves the provider has not been entered;
            # a later attempt reuses the same durable admission identity.
            if self._dispatch_ready is not None and not self._dispatch_ready():
                self._store.defer_before_dispatch(claim, reason="foreground_active",
                    now=self._current_time(now))
                return "deferred"
            contact_reader = getattr(self._store, "private_contact_allowed", None)
            private_contact = (context_review and not household_review
                and callable(contact_reader) and contact_reader())
            search_context = None
            search_used = False
            private_context_search = None
            if (context_review and not household_review and event.source == "initiative-review"
                    and event.event_type == "private_opportunity_review"
                    and getattr(self._model, "supports_private_context_search", False) is True):
                def revalidate_search():
                    at = self._current_time(now)
                    self._context_review_authorizer()
                    principal = (self._principal_id if self._memory_principal_resolver is None
                                 else self._memory_principal_resolver())
                    if principal != private_query.principal_id:
                        raise RuntimeError("private memory search owner binding changed")
                    if callable(cancel_attention) and cancel_attention(claim, now=at):
                        raise RuntimeError("private memory search attention was cancelled")
                    if not self._store.context_review_claim_is_current(claim, now=at):
                        raise RuntimeError("private memory search claim is no longer current")
                    if self._dispatch_ready is not None and not self._dispatch_ready():
                        raise RuntimeError("private memory search deferred by foreground activity")

                def private_context_search(query_text):
                    nonlocal search_used, search_context
                    if search_used:
                        raise RuntimeError("private memory search is already consumed")
                    search_used = True
                    if (not isinstance(query_text, str) or not query_text.strip()
                            or len(query_text.encode("utf-8")) > 1024):
                        raise ValueError("private memory search query is invalid")
                    revalidate_search()
                    query = replace(private_query,
                        recall_id=private_query.recall_id + ":memorysearch",
                        query_text="Semantische zoekconcepten: " + query_text)
                    expanded = self._memory.recall_for_presence(query)
                    revalidate_search()
                    search_context = _bounded_private_memory_search_context(expanded, query_text)
                    return deepcopy(search_context)

            model_entered = True  # All later failures remain potentially dispatched.
            decision = self._model.evaluate_sensory_event(
                event,
                self._identity,
                memory,
                attention,
                **({"private_contact_allowed": True} if private_contact else {}),
                **({"initiative_history": history} if history else {}),
                **({"initiative_capabilities": capabilities} if callable(capability_reader) else {}),
                **({"initiative_source_outcomes": source_outcomes} if source_outcomes else {}),
                **({"private_context_search": private_context_search} if private_context_search is not None else {}),
            )
            # Only actual host lookup output can be committed, never model-supplied metadata.
            if search_context is not None:
                try:
                    revalidate_search()
                except Exception as error:
                    if (isinstance(decision.usage, ConversationModelUsage)
                            and decision.usage.measurement == "measured"):
                        error.model_usage = decision.usage
                        error.validation_failure_stage = "sensory_decision"
                        error.validation_failure_code = "private_context_changed_before_commit"
                    raise
            decision = replace(decision, memory_search_context=deepcopy(search_context))
            if source_outcomes:
                from eva.contracts.canonical_json import canonical_sha256
                decision = replace(decision, source_context_sha256=canonical_sha256(list(source_outcomes)))
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
            fail_before_dispatch = getattr(self._store, "fail_before_dispatch", None)
            if (not model_entered and not isinstance(error, AutonomousUsageUncertain)
                    and callable(fail_before_dispatch)):
                fail_before_dispatch(claim, error, now=self._current_time(now))
            else:
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



def private_orientation_context(event: SensoryEvent, history: tuple[dict, ...]) -> dict | None:
    """Distinguish an actual chosen follow-up from an open orientation.

    This is a view of the supplied bounded history, not a new goal store or a
    relevance decision. Keep original history, uncertainty and authority intact.
    """
    if event.source != "initiative-review" or event.event_type != "private_opportunity_review":
        return None
    mode = ("chosen_attention" if isinstance(event.payload.get("attention_origin"), dict)
            else "source_followup" if isinstance(event.payload.get("native_completion_source"), dict)
            else "pending_attention" if isinstance(event.payload.get("pending_attention"), dict)
            else "open_orientation")
    non_pending = {"completed", "initiative_completed", "personal_stopped", "release_ready", "initiative_blocked"}
    counts, pending = {}, []
    for item in history:
        if not isinstance(item, dict) or not isinstance(item.get("intent_id"), str):
            continue  # Earlier contact is not a selected project.
        phase = item.get("phase")
        if not isinstance(phase, str):
            continue
        counts[phase] = counts.get(phase, 0) + 1
        if phase not in non_pending:
            pending.append({key: item.get(key) for key in ("intent_id", "title", "phase")})
    return {"mode": mode, "visible_project_phase_counts": counts,
            "visible_pending_projects": pending, "phase_counts_do_not_prove_goal_completion": True,
            "coverage": "supplied_bounded_history_not_all_goals_or_family_knowledge",
            "history_selects_no_current_goal": True, "is_execution_permission": False}


def _private_review_recall_context(event: SensoryEvent) -> str:
    """Search for personal opportunity, not recurring telemetry or closed test work.

    The full stamped household snapshot still reaches the decision. Only a
    pending attention reason or live commitment belongs in this search hint.
    """
    if event.source != "initiative-review":
        return ""
    import json
    attention = event.payload.get("attention_origin")
    if isinstance(attention, dict) and isinstance(attention.get("reason"), str):
        return "Eerder zelfgekozen aandacht (historische bedoeling, geen nieuwe opdracht): " + attention["reason"][:400]
    context = event.payload.get("life_context")
    if not isinstance(context, dict):
        return ""
    commitments = [{key: item.get(key) for key in ("title", "phase", "deadline")}
                   for item in context.get("own_work", ()) if isinstance(item, dict)
                   and item.get("status") in {"active", "waiting_for_proof"}]
    if not commitments:
        return ""
    return " Lopende eigen bedoelingen (bronhistorie, geen actuele bevestiging): " + json.dumps(
        commitments, ensure_ascii=False, separators=(",", ":"))[:1600]


def _bounded_private_memory_search_context(memory: PresenceMemory, query_text: str) -> dict:
    """Retain exact recall provenance within the existing private context budget."""
    import hashlib
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    facts = [fact.model_dump(mode="json") for fact in memory.confirmed_facts]
    recall = memory.recall.model_dump(mode="json")
    result = {"result_count": len(facts), "recalled_context": list(facts), "recall": recall,
        "recall_sha256": canonical_sha256(recall),
        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "is_execution_permission": False, "truncated": False, "omitted_count": 0}
    while len(canonical_json_bytes(result)) > 8192 and result["recalled_context"]:
        result["recalled_context"].pop()
        result["omitted_count"] += 1
        result["truncated"] = True
    if len(canonical_json_bytes(result)) > 8192:
        result["recall"] = {"sha256": result["recall_sha256"], "metadata_omitted": True}
        result["recalled_context"] = list(facts)
        result["omitted_count"] = 0
        result["truncated"] = True
        while len(canonical_json_bytes(result)) > 8192 and result["recalled_context"]:
            result["recalled_context"].pop()
            result["omitted_count"] += 1
    return result


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
