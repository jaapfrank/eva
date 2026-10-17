from __future__ import annotations

import pytest

from eva.application.memory import (
    InvalidMemoryAssertion,
    MemoryAuthority,
    MemoryRetentionReceipt,
    MemoryWriteReceipt,
    PresenceMemory,
    RecallCurrentFacts,
    RecalledMemoryFact,
    RememberFact,
)
from eva.application.memory_audience import MemoryAudience
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact


TENANT_ID = "66a4aad5-0550-5211-8b36-398eabe5b7be"
INSTALLATION_ID = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
PRINCIPAL_ID = "8d0ce6a5-da38-51a8-81a6-f2a99d1652d3"
SOURCE_ID = "34ffb415-bd66-5d29-8a79-6c3ed8523848"
FACT_ID = "43ecfc54-af4b-5848-95ae-2fb38f1b0c42"
CURRENT_ID = "877ea196-f816-54ba-9d4e-3c483607d58c"
EVIDENCE_ID = "52752881-f9e4-58e7-a398-8c4a982d6794"
NOW = "2026-08-23T10:00:00Z"
RETENTION = "2027-08-23T10:00:00Z"
HASH = "1" * 64


@pytest.mark.parametrize(
    "kwargs",
    (
        {"bank_id": "", "document_id": "doc", "content_sha256": HASH},
        {"bank_id": "bank", "document_id": "", "content_sha256": HASH},
        {"bank_id": "bank", "document_id": "doc", "content_sha256": "NOT-A-HASH"},
        {
            "bank_id": "bank",
            "document_id": "doc",
            "content_sha256": HASH,
            "disposition": "dropped",
        },
    ),
)
def test_memory_retention_receipt_rejects_invalid_wire_values(
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        MemoryRetentionReceipt(**kwargs)  # type: ignore[arg-type]


def test_presence_rejects_invalid_style_mirror() -> None:
    with pytest.raises(ValueError, match="style mirror"):
        PresenceMemory(None, (), 17)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="style mirror"):
        PresenceMemory(None, (), "   ")  # type: ignore[arg-type]


def _source(**changes: object) -> ExperienceSourceEvent:
    values = {
        "schema_version": 1,
        "contract": "ExperienceSourceEvent/v1",
        "source_event_id": SOURCE_ID,
        "tenant_id": TENANT_ID,
        "principal_id": PRINCIPAL_ID,
        "subject_id": PRINCIPAL_ID,
        "event_type": "preference_stated",
        "source_kind": "direct_user_input",
        "source_ref": "telegram:message:5001",
        "content_sha256": HASH,
        "provenance_refs": [EVIDENCE_ID],
        "observed_at": NOW,
        "valid_from": NOW,
        "valid_until": None,
        "ingested_at": NOW,
        "trust": "confirmed_direct_user",
        "privacy_class": "internal",
        "purpose": "conversation_continuity",
        "retention_until": RETENTION,
        "delete_sla_at": RETENTION,
        "corrects_source_event_id": None,
        "supersedes_source_event_id": None,
        "retracts_source_event_id": None,
        "erasure_request_id": None,
        **changes,
    }
    return ExperienceSourceEvent.model_validate(values)


def _fact(**changes: object) -> MemoryFact:
    values = {
        "schema_version": 1,
        "contract": "MemoryFact/v1",
        "fact_id": FACT_ID,
        "tenant_id": TENANT_ID,
        "subject_id": PRINCIPAL_ID,
        "predicate": "preference.outdoor_temperature",
        "value": {"preference": "warm", "context": "covered_terrace"},
        "source_event_ids": [SOURCE_ID],
        "observed_at": NOW,
        "valid_from": NOW,
        "valid_until": None,
        "trust": "confirmed_direct_user",
        "content_sha256": "2" * 64,
        "assertion_status": "active",
        "superseded_by_fact_id": None,
        "retraction_source_event_id": None,
        **changes,
    }
    return MemoryFact.model_validate(values)


def _command(**changes: object) -> RememberFact:
    values = {
        "installation_id": INSTALLATION_ID,
        "current_fact_id": CURRENT_ID,
        "source_event": _source(),
        "fact": _fact(),
        "scope": {"kind": "person", "principal_id": PRINCIPAL_ID},
        "retention_policy_id": "resident-memory-v1",
        "committed_at": NOW,
        **changes,
    }
    return RememberFact(**values)


class _Repository:
    def __init__(self, recalled: tuple[RecalledMemoryFact, ...] = ()) -> None:
        self.commands: list[RememberFact] = []
        self.recalled = recalled

    def remember(self, command: RememberFact) -> MemoryWriteReceipt:
        self.commands.append(command)
        return MemoryWriteReceipt(
            disposition="inserted",
            source_event_id=command.source_event.source_event_id,
            fact_id=command.fact.fact_id,
            current_fact_id=command.current_fact_id,
            current_revision=1,
        )

    def recall_current(
        self, _query: RecallCurrentFacts
    ) -> tuple[RecalledMemoryFact, ...]:
        return self.recalled


def test_direct_experience_becomes_one_current_fact_without_model_write_authority() -> (
    None
):
    repository = _Repository()
    receipt = MemoryAuthority(repository).remember(_command())

    assert receipt.disposition == "inserted"
    assert receipt.fact_id == FACT_ID
    assert repository.commands == [_command()]


def test_untrusted_external_content_cannot_become_memory() -> None:
    repository = _Repository()
    command = _command(
        source_event=_source(
            source_kind="untrusted_external_content",
            trust="untrusted_external",
        )
    )
    with pytest.raises(InvalidMemoryAssertion, match="cannot become"):
        MemoryAuthority(repository).remember(command)
    assert repository.commands == []


def test_presence_recall_is_built_from_current_fact_and_complete_provenance() -> None:
    source = _source()
    fact = _fact()
    repository = _Repository(
        (RecalledMemoryFact(fact, (source,), "postgres_full_text_match"),)
    )
    query = RecallCurrentFacts(
        recall_id="recall:telegram:5002",
        installation_id=INSTALLATION_ID,
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL_ID),
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        query_purpose="reply",
        requested_valid_at=NOW,
        scope={"kind": "person", "principal_id": PRINCIPAL_ID},
        subject_ids=(PRINCIPAL_ID,),
        query_text="warm overkapping",
    )

    context = MemoryAuthority(repository).recall_for_presence(query)

    assert [item.fact_id for item in context.recall.selected] == [FACT_ID]
    assert context.recall.selected[0].source_event_ids == [SOURCE_ID]
    assert context.confirmed_facts[0].predicate == fact.predicate
    assert context.confirmed_facts[0].value == fact.value
    assert context.confirmed_facts[0].source_ref == source.source_ref
    assert context.confirmed_facts[0].evidence_ref == EVIDENCE_ID
    assert context.confirmed_facts[0].trust == "direct_user"


def test_repository_cannot_widen_recall_subject_scope() -> None:
    other = _fact(subject_id="other-person")
    repository = _Repository(
        (RecalledMemoryFact(other, (_source(subject_id="other-person"),), "rank"),)
    )
    query = RecallCurrentFacts(
        recall_id="recall:telegram:5003",
        installation_id=INSTALLATION_ID,
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL_ID),
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        query_purpose="reply",
        requested_valid_at=NOW,
        scope={"kind": "person", "principal_id": PRINCIPAL_ID},
        subject_ids=(PRINCIPAL_ID,),
        query_text="warm",
    )
    with pytest.raises(InvalidMemoryAssertion, match="widened"):
        MemoryAuthority(repository).recall_for_presence(query)


@pytest.mark.parametrize("trust,wire_trust,qualifier", [
    ("confirmed_direct_user", "direct_user", None),
    ("verified_external", "verified_external", None),
    ("model_inference_unconfirmed", "host_policy", "afgeleid patroon; niet als bewezen feit behandelen"),
    ("legacy_unverified", "host_policy", "historische Eva1-herinnering; onbevestigd"),
])
@pytest.mark.parametrize("value", [{"text": "Synthetic preference"}, ["synthetic", True], None])
def test_presence_preserves_semantic_trust_through_model_context(tmp_path, trust, wire_trust, qualifier, value):
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import IdentityProfile

    fact = _fact(trust=trust, value=value)
    repository = _Repository((RecalledMemoryFact(fact, (_source(),), "scoped-current"),))
    query = RecallCurrentFacts(recall_id="recall:semantic-trust", installation_id=INSTALLATION_ID,
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID,
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL_ID),
        conversation_id="conversation:synthetic", project_id=None, query_purpose="reply",
        requested_valid_at=NOW, scope={"kind": "person", "principal_id": PRINCIPAL_ID},
        subject_ids=(PRINCIPAL_ID,), query_text="Synthetic preference")
    memory = MemoryAuthority(repository).recall_for_presence(query)
    confirmed = memory.confirmed_facts[0]
    assert confirmed.trust == wire_trust
    assert memory.recall.selected[0].trust == trust
    assert confirmed.source_ref == _source().source_ref
    assert confirmed.evidence_ref == EVIDENCE_ID
    if qualifier is None:
        assert confirmed.value == value
    else:
        assert confirmed.value["semantic_trust"] == trust
        if isinstance(value, dict):
            assert {key: confirmed.value[key] for key in value} == value
        else:
            assert confirmed.value["value"] == value
    # Formatting only: no credentials, provider request or model execution.
    model = PydanticConversationModel(auth_file=tmp_path / "unused.json")
    context = model._instructions(IdentityProfile("Eva", "Synthetic identity", ("Be honest",)), memory, ())
    if qualifier is not None:
        assert qualifier in context
    else:
        assert ("directe gebruiker" if wire_trust == "direct_user" else "geverifieerde bron") in context
    assert fact.value == value
