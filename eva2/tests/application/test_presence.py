from __future__ import annotations

import pytest

from eva.application.memory import PresenceMemory
from eva.application.presence import (
    PresenceContextError,
    PresenceTurn,
    build_presence_context,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import (
    ConfirmedFact,
    CurrentIdentityPack,
    CurrentUtterance,
    EpisodeGoal,
)


NOW = "2026-08-23T10:00:00Z"
HASH = "1" * 64


def _identity() -> CurrentIdentityPack:
    return CurrentIdentityPack.model_validate(
        {
            "schema_version": 1,
            "contract": "IdentityPack/v1",
            "identity_id": "identity:eva:001",
            "pack_version": 1,
            "state": "current",
            "display_name": "Eva",
            "pronouns": ["zij", "haar"],
            "language": "nl-NL",
            "voice_style": {
                "name": "warm",
                "qualities": ["menselijk", "duidelijk"],
                "preferred_wording": [],
                "forbidden_wording": [],
                "maximum_paragraphs": 3,
            },
            "values": ["eerlijk", "behulpzaam"],
            "temperament": ["nieuwsgierig"],
            "humour": "licht wanneer passend",
            "conversation_boundaries": ["geen verzonnen zekerheden"],
            "channel_presentations": [
                {
                    "channel_class": "telegram",
                    "greeting": None,
                    "signoff": None,
                    "concise_by_default": True,
                }
            ],
            "privacy_preferences": [],
            "consent_preferences": [],
            "initiative_preferences": ["bied concrete hulp aan"],
            "unknown_relation_preferences": [],
            "issuer": "eva:host",
            "source_sha256": HASH,
            "signature_algorithm": "Ed25519",
            "signature": "A" * 86,
            "issued_at": NOW,
        }
    )


def _memory(*, fact_id: str = "fact:warm:001") -> PresenceMemory:
    confirmed = ConfirmedFact(
        fact_id=fact_id,
        subject="principal:jaap",
        predicate="preference.outdoor_temperature",
        value={"preference": "warm", "context": "overkapping"},
        source_ref="telegram:message:5001",
        evidence_ref="evidence:message:5001",
        observed_at=NOW,
        trust="direct_user",
    )
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:telegram:5002",
        query_purpose="reply",
        tenant_id="tenant:owner:001",
        principal_id="principal:jaap",
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([fact_id]),
        selected=[
            RecalledFact(
                rank=1,
                fact_id=fact_id,
                source_event_ids=["source:message:5001"],
                trust="confirmed_direct_user",
                ranking_reason="current direct preference",
            ).model_dump(mode="json")
        ],
        excluded_reasons={},
        projection_version="postgres-simple-v1",
        scope_sha256=HASH,
        created_at=NOW,
    )
    return PresenceMemory(recall, (confirmed,))


def _turn(memory: PresenceMemory) -> PresenceTurn:
    return PresenceTurn(
        tenant_id="tenant:owner:001",
        principal_id="principal:jaap",
        conversation_id="conversation:telegram:jaap",
        episode_id="episode:telegram:5002",
        generation=1,
        identity_pack=_identity(),
        relationships=(),
        current_utterance=CurrentUtterance(
            turn_ref="telegram:update:5002",
            message_id="telegram:message:5002",
            text="Het is koud onder de overkapping.",
            received_at=NOW,
        ),
        episode_goal=EpisodeGoal(
            goal_id="goal:telegram:5002",
            source_message_ref="telegram:message:5002",
            description="Reageer behulpzaam met relevante actuele context.",
        ),
        memory=memory,
    )


def test_current_memory_is_embedded_in_the_model_visible_presence_context() -> None:
    context = build_presence_context(_turn(_memory()), purpose="reply")

    assert context.identity_pack.display_name == "Eva"
    assert context.confirmed_facts[0].predicate == "preference.outdoor_temperature"
    assert context.confirmed_facts[0].value == {
        "preference": "warm",
        "context": "overkapping",
    }
    assert context.confirmed_facts[0].source_ref == "telegram:message:5001"


def test_presence_rejects_memory_from_another_turn_or_selection() -> None:
    wrong_purpose = _memory().recall.model_copy(update={"query_purpose": "plan"})
    with pytest.raises(PresenceContextError, match="does not belong"):
        build_presence_context(
            _turn(PresenceMemory(wrong_purpose, _memory().confirmed_facts)),
            purpose="reply",
        )

    with pytest.raises(PresenceContextError, match="differ"):
        build_presence_context(
            _turn(PresenceMemory(_memory().recall, ())), purpose="reply"
        )


def test_identity_hash_is_computed_by_the_host_not_supplied_by_the_model() -> None:
    context = build_presence_context(_turn(_memory()), purpose="reply")
    assert context.identity_pack_sha256 == canonical_sha256(_identity())
    assert isinstance(context.identity_pack, IdentityPack)
