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
    EpisodeGoal,
    MissingHumanChoice,
    ModelPurpose,
    PresenceContext,
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


__all__ = [
    "PresenceContextError",
    "PresenceTurn",
    "build_presence_context",
]
