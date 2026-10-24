from types import SimpleNamespace
from uuid import UUID
import pytest

from eva.application.conversation import ConversationTurn
from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
from eva.bootstrap import dashboard_housemate as module

OWNER = "owner"
SOURCE_ID = "11111111-1111-4111-8111-111111111111"


def source(message, at, *, previous=None):
    turn = ConversationTurn.private("tenant", "installation", OWNER, "conversation", message, "A real fixture correction", at)
    args = {"kind": "person_hypothesis", "interpretation": message,
            "uncertainty_reason": "Limited explicit feedback", "review_at": None,
            "outcome": "confirmed" if previous else "open",
            "previous_source_ref": None if previous is None else previous.source_ref,
            "previous_evidence_ref": None if previous is None else previous.evidence_ref}
    doc = build_human_understanding_document(turn, args)
    return doc, {"source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
                 "record": parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)}


@pytest.fixture
def rig(monkeypatch):
    first, a = source("old", "2026-10-01T10:00:00Z")
    latest, b = source("corrected", "2026-10-02T10:00:00Z", previous=first)
    context = {"contract": "EvaHumanUnderstandingContext/v1", "records": [b, a]}
    service = SimpleNamespace(owner_principal_id=OWNER, _private_bridge_principal_loader=lambda: None)
    calls = []
    def validate(turn): calls.append("final_authority")
    service.validate_authority = validate
    class Reader:
        _owner, _tenant, _installation, _binding = OWNER, "tenant", "installation", UUID(SOURCE_ID)
        erased = False
        foreign = False
        wrong_document = False
        revoked = False
        def selected_private_sources(self, query, refs, *, now):
            assert refs == () and query.principal_id == OWNER
            assert query.audience.kind == "private_person" and query.scope == {"kind": "private"}
            calls.append("existing_current_head_reader")
            return tuple({"principal_id": "other" if self.foreign else OWNER,
                          "source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
                          "learning_context": context} for doc in (first, latest))
        def document_reference(self, principal, source_ref, evidence_ref, *, now):
            assert (principal, source_ref, evidence_ref) == (OWNER, latest.source_ref, latest.evidence_ref)
            return None if self.erased else (SOURCE_ID, first if self.wrong_document else latest, "retained")
        def require_learning_query(self, query, *, now):
            calls.append("fresh_source_authority")
            if self.revoked: raise RuntimeError("owner revoked")
    reader = Reader()
    runtime = SimpleNamespace(_owner_memory_sources=lambda: reader)
    monkeypatch.setattr(module, "owner_turn", lambda runtime: (service, object()))
    return SimpleNamespace(runtime=runtime, reader=reader, service=service, calls=calls)


def test_current_heads_deduplicate_and_preserve_meaning_without_models(rig):
    result = module.housemate_state(rig.runtime)
    state = result
    assert len(state["current_learning"]) == 1
    item = state["current_learning"][0]
    assert item["text"] == "corrected" and item["revised"] is True
    assert item["source_id"] == SOURCE_ID and item["kind"] == "person_hypothesis"
    assert item["is_execution_permission"] is False
    assert state["personality_deltas"] == []
    assert state["personality_deltas_state"] == "no_separate_typed_delta_route"
    assert state["shared_trace"] is None
    assert state["curiosity_source"] == "current_open_project_questions"
    assert rig.calls.count("existing_current_head_reader") == 1
    assert rig.calls[-2:] == ["fresh_source_authority", "final_authority"]


def test_erased_current_head_never_falls_back_to_old_learning(rig):
    rig.reader.erased = True
    assert module.housemate_state(rig.runtime)["current_learning"] == []


@pytest.mark.parametrize("attribute", ["foreign", "wrong_document", "revoked"])
def test_invalid_or_revoked_source_fails_closed(rig, attribute):
    setattr(rig.reader, attribute, True)
    with pytest.raises(RuntimeError): module.housemate_state(rig.runtime)


def test_owner_alias_changed_while_reading_is_not_returned(rig):
    rig.reader.require_learning_query = lambda *a, **k: setattr(rig.service, "_private_bridge_principal_loader", lambda: "telegram:new")
    with pytest.raises(RuntimeError, match="alias changed"):
        module.housemate_state(rig.runtime)
