"""Build the model-visible Presence context from earned application state.

Memory is admitted by :mod:`eva.application.memory`; this module only joins
that admitted recall with the rest of the current turn.  It deliberately does
not load fixture snapshots or let a model select its own remembered facts.
"""

from __future__ import annotations

from dataclasses import dataclass

from eva.application.memory import PresenceMemory
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorResult
from eva.contracts.execution import (
    DeliveryIntent,
    DeliveryReceipt,
    EffectReadback,
    EffectReceipt,
    ReadObservation,
)
from eva.contracts.memory import RelationshipBinding
from eva.contracts.planning import (
    ActiveConsentGrant,
    ConversationExcerpt,
    CurrentIdentityPack,
    CurrentUtterance,
    DocumentDraftContextSelection,
    EpisodeGoal,
    MissingHumanChoice,
    ModelPurpose,
    OutcomeView,
    PlanContextSelection,
    PlannerRequest,
    PresenceContext,
    ReplyContextSelection,
    ReplyModelInput,
)


class PresenceContextError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PresenceTurn:
    tenant_id: str
    principal_id: str
    conversation_id: str
    episode_id: str
    generation: int
    identity_pack: CurrentIdentityPack
    relationships: tuple[RelationshipBinding, ...]
    current_utterance: CurrentUtterance
    episode_goal: EpisodeGoal
    memory: PresenceMemory
    conversation: tuple[ConversationExcerpt, ...] = ()
    valid_consent_grants: tuple[ActiveConsentGrant, ...] = ()
    connector_results: tuple[ConnectorResult, ...] = ()
    read_observations: tuple[ReadObservation, ...] = ()
    effect_receipts: tuple[EffectReceipt, ...] = ()
    effect_readbacks: tuple[EffectReadback, ...] = ()
    delivery_intents: tuple[DeliveryIntent, ...] = ()
    delivery_receipts: tuple[DeliveryReceipt, ...] = ()
    unresolved_evidence_refs: tuple[str, ...] = ()
    missing_human_choice: MissingHumanChoice | None = None
    token_budget: int = 8_000


@dataclass(frozen=True, slots=True)
class SelectedPresence:
    context: PresenceContext
    selection: (
        PlanContextSelection | DocumentDraftContextSelection | ReplyContextSelection
    )


def _wire(values: tuple[object, ...]) -> list[object]:
    return [value.model_dump(mode="json") for value in values]  # type: ignore[attr-defined]


def build_presence_context(
    turn: PresenceTurn, *, purpose: ModelPurpose
) -> PresenceContext:
    """Construct one complete model context from current, admitted state."""

    recall = turn.memory.recall
    if (
        recall.tenant_id != turn.tenant_id
        or recall.principal_id != turn.principal_id
        or recall.conversation_id != turn.conversation_id
        or recall.query_purpose != purpose
    ):
        raise PresenceContextError(
            "memory recall does not belong to this principal, conversation and purpose"
        )
    selected_fact_ids = [item.fact_id for item in recall.selected]
    confirmed_fact_ids = [item.fact_id for item in turn.memory.confirmed_facts]
    if selected_fact_ids != confirmed_fact_ids:
        raise PresenceContextError(
            "memory recall selection and confirmed Presence facts differ"
        )

    return PresenceContext.model_validate(
        {
            "schema_version": 1,
            "contract": "PresenceContext/v1",
            "tenant_id": turn.tenant_id,
            "principal_id": turn.principal_id,
            "conversation_id": turn.conversation_id,
            "episode_id": turn.episode_id,
            "generation": turn.generation,
            "identity_pack": turn.identity_pack.model_dump(mode="json"),
            "identity_pack_sha256": canonical_sha256(turn.identity_pack),
            "relationships": _wire(turn.relationships),
            "current_utterance": turn.current_utterance.model_dump(mode="json"),
            "episode_goal": turn.episode_goal.model_dump(mode="json"),
            "confirmed_facts": _wire(turn.memory.confirmed_facts),
            "conversation": _wire(turn.conversation),
            "valid_consent_grants": _wire(turn.valid_consent_grants),
            "connector_results": _wire(turn.connector_results),
            "read_observations": _wire(turn.read_observations),
            "effect_receipts": _wire(turn.effect_receipts),
            "effect_readbacks": _wire(turn.effect_readbacks),
            "delivery_intents": _wire(turn.delivery_intents),
            "delivery_receipts": _wire(turn.delivery_receipts),
            "unresolved_evidence_refs": list(turn.unresolved_evidence_refs),
            "missing_human_choice": (
                None
                if turn.missing_human_choice is None
                else turn.missing_human_choice.model_dump(mode="json")
            ),
            "token_budget": turn.token_budget,
        }
    )


def select_presence_context(
    turn: PresenceTurn,
    *,
    purpose: ModelPurpose,
    selected_at: str,
    additional_source_refs: tuple[str, ...] = (),
) -> SelectedPresence:
    """Bind the complete context and its auditable source selection."""

    context = build_presence_context(turn, purpose=purpose)
    source_refs = {
        turn.current_utterance.turn_ref,
        turn.current_utterance.message_id,
        turn.episode_goal.source_message_ref,
        turn.memory.recall.recall_id,
        *additional_source_refs,
    }
    for fact in turn.memory.confirmed_facts:
        source_refs.update((fact.fact_id, fact.source_ref, fact.evidence_ref))
    for relationship in turn.relationships:
        source_refs.update(
            (
                relationship.relationship_binding_id,
                relationship.source_event_id,
                *relationship.channel_binding_evidence_refs,
            )
        )
    source_refs.update(item.message_id for item in turn.conversation)
    selection_type = {
        "plan": PlanContextSelection,
        "document_draft": DocumentDraftContextSelection,
        "reply": ReplyContextSelection,
    }[purpose]
    selection = selection_type.model_validate(
        {
            "schema_version": 1,
            "contract": "ContextSelection/v1",
            "tenant_id": turn.tenant_id,
            "principal_id": turn.principal_id,
            "conversation_id": turn.conversation_id,
            "purpose": purpose,
            "selected_source_refs": sorted(source_refs),
            "excluded_source_reasons": {},
            "context_sha256": canonical_sha256(context),
            "selected_at": selected_at,
        }
    )
    return SelectedPresence(context, selection)


def build_reply_model_input(
    selected: SelectedPresence,
    *,
    policy_pack_sha256: str,
    outcome_view: OutcomeView,
    allowed_claim_refs: tuple[str, ...],
) -> ReplyModelInput:
    """Create the exact reply input that may cross the model boundary."""

    if not isinstance(selected.selection, ReplyContextSelection):
        raise PresenceContextError("reply input requires a reply context selection")
    return ReplyModelInput.model_validate(
        {
            "schema_version": 1,
            "contract": "ReplyModelInput/v1",
            "purpose": "reply",
            "output_contract": "ReplyCandidate/v1",
            "presence_context": selected.context.model_dump(mode="json"),
            "context_selection": selected.selection.model_dump(mode="json"),
            "policy_pack_sha256": policy_pack_sha256,
            "outcome_view": outcome_view.model_dump(mode="json"),
            "allowed_claim_refs": list(allowed_claim_refs),
        }
    )


def build_reply_planner_request(
    selected: SelectedPresence,
    *,
    request_id: str,
    deadline: str,
    policy_pack_sha256: str,
    outcome_view: OutcomeView,
    allowed_claim_refs: tuple[str, ...],
) -> PlannerRequest:
    """Produce canonical, self-contained input for a replaceable model adapter."""

    model_input = build_reply_model_input(
        selected,
        policy_pack_sha256=policy_pack_sha256,
        outcome_view=outcome_view,
        allowed_claim_refs=allowed_claim_refs,
    )
    return PlannerRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "PlannerRequest/v1",
            "request_id": request_id,
            "model_input": model_input.model_dump(mode="json"),
            "deadline": deadline,
        }
    )


__all__ = [
    "PresenceContextError",
    "PresenceTurn",
    "SelectedPresence",
    "build_reply_model_input",
    "build_reply_planner_request",
    "build_presence_context",
    "select_presence_context",
]
