from __future__ import annotations

import pytest

from eva.application.memory import PresenceMemory
from eva.application.presence import (
    PresenceContextError,
    PresenceTurn,
    build_reply_model_input,
    build_reply_planner_request,
    build_presence_context,
    select_presence_context,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import (
    ConfirmedFact,
    CurrentIdentityPack,
    CurrentUtterance,
    EpisodeGoal,
    OutcomeView,
    ReplyModelInput,
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


def test_correction_changes_the_exact_reply_model_input_not_a_response_rule() -> None:
    old = _memory(fact_id="fact:cool:001")
    old_confirmed = old.confirmed_facts[0].model_copy(
        update={"value": {"preference": "cool", "context": "overkapping"}}
    )
    old = PresenceMemory(old.recall, (old_confirmed,))
    corrected = _memory(fact_id="fact:warm:002")
    outcome = OutcomeView(
        schema_version=1,
        contract="OutcomeView/v1",
        episode_id="episode:telegram:5002",
        generation=1,
        steps=[
            {
                "step_id": "step:answer",
                "status": "completed",
                "human_summary": "Actuele voorkeur opgehaald",
                "evidence_refs": ["evidence:message:5001"],
                "unresolved_refs": [],
            }
        ],
        overall_status="completed",
        created_at=NOW,
    )

    def model_input(memory: PresenceMemory) -> ReplyModelInput:
        selected = select_presence_context(
            _turn(memory), purpose="reply", selected_at=NOW
        )
        return build_reply_model_input(
            selected,
            policy_pack_sha256=HASH,
            outcome_view=outcome,
            allowed_claim_refs=("claim:memory:preference",),
        )

    before = model_input(old)
    after = model_input(corrected)

    assert before.presence_context.confirmed_facts[0].value["preference"] == "cool"
    assert after.presence_context.confirmed_facts[0].value["preference"] == "warm"
    assert canonical_sha256(before) != canonical_sha256(after)
    assert after.context_selection.context_sha256 == canonical_sha256(
        after.presence_context
    )

    request = build_reply_planner_request(
        select_presence_context(_turn(corrected), purpose="reply", selected_at=NOW),
        request_id="model-request:telegram:5002",
        deadline="2026-08-23T10:00:30Z",
        policy_pack_sha256=HASH,
        outcome_view=outcome,
        allowed_claim_refs=("claim:memory:preference",),
    )
    assert request.model_input.presence_context.confirmed_facts[0].fact_id == (
        "fact:warm:002"
    )
    assert canonical_sha256(request.model_input.presence_context) == (
        request.model_input.context_selection.context_sha256
    )
