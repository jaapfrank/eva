from types import SimpleNamespace
from uuid import UUID
import pytest

from eva.application.conversation import ConversationTurn
from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
from eva.bootstrap import dashboard_housemate as module

OWNER = "owner"
SOURCE_ID = "11111111-1111-4111-8111-111111111111"


def source(message, at, *, previous=None, kind="person_hypothesis"):
    turn = ConversationTurn.private("tenant", "installation", OWNER, "conversation", message, "A real fixture correction", at)
    args = {"kind": kind, "interpretation": message,
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
    assert state["personality_deltas_state"] == "no_current_typed_sources"
    assert state["development_state"] == "not_configured"
    assert state["inner_state"] is None
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


@pytest.fixture
def development_rig(rig):
    from datetime import UTC, datetime
    from eva.bootstrap.housemate_development import HousemateDevelopment
    from tests.bootstrap.test_e110_housemate_development import SelectionFixture
    from tests.application.test_housemate_development import project
    first,a=source("Give a short recommendation first", "2026-10-23T09:00:00Z",kind="personality_delta")
    latest,b=source("Give reasoning for complex choices", "2026-10-23T10:00:00Z",previous=first,kind="personality_delta")
    rows=[dict(principal_id=OWNER,source_ref=latest.source_ref,evidence_ref=latest.evidence_ref,
        occurred_at=latest.occurred_at,learning_context={"contract":"EvaHumanUnderstandingContext/v1","records":[b,a]})]
    state={"rows":rows,"document":latest,"source_id":SOURCE_ID,"context":[b,a]}
    rig.reader._bindings=None
    rig.reader.selected_private_sources=lambda *args,**kwargs:state["rows"]
    rig.reader.document_reference=lambda *args,**kwargs:None if rig.reader.erased else (
        state["source_id"],state["document"],"retained")
    projects=[project()];store=SelectionFixture();store.projects=projects
    owner=SimpleNamespace(page=lambda **kwargs:dict(cards=projects))
    dev=HousemateDevelopment(owner=owner,source_reader=lambda:rig.reader,
        clock=lambda:datetime(2026,10,24,12,tzinfo=UTC),selections=store)
    assert dev.after_reflection("tenant","installation",OWNER,SimpleNamespace(observation_error_type=None))
    rig.runtime._memory=SimpleNamespace(_development_projection=dev)
    return SimpleNamespace(rig=rig,state=state,dev=dev,store=store,projects=projects)


def test_actual_e110_reader_supplies_processed_state_and_distinct_delta(development_rig):
    data=development_rig;result=module.housemate_state(data.rig.runtime)
    assert result["current_learning"] == []  # Never renamed to a person's hypothesis.
    assert result["development_state"] == "available"
    assert result["inner_state"]["current_curiosity"] == data.projects[0]["question"]
    assert len(result["inner_state"]["open_threads"]) == 1
    delta=result["personality_deltas"][0]
    assert delta["kind"] == "personality_delta" and delta["version"] == 2
    assert delta["text"] == "Give reasoning for complex choices"
    assert delta["source_id"] == SOURCE_ID and delta["processing_state"] == "processed"
    assert delta["epistemic_status"] == "model_inference_unconfirmed" and not delta["is_execution_permission"]
    assert result["shared_trace"] is None


def test_stop_and_erasure_remove_actual_development_without_old_fallback(development_rig):
    data=development_rig
    data.store.projects=[{**data.projects[0],"status":"stopped","phase":"personal_stopped"}]
    assert module.housemate_state(data.rig.runtime)["inner_state"]["open_threads"] == []
    data.rig.reader.erased=True;data.state["rows"]=[]
    result=module.housemate_state(data.rig.runtime)
    assert result["inner_state"] is None and result["personality_deltas"] == []


def test_current_correction_appears_as_pending_not_old_processed_personality(development_rig):
    data=development_rig
    current=data.state["document"]
    doc,item=source("For this kind of research be more direct", "2026-10-24T11:00:00Z",previous=current,kind="personality_delta")
    data.state["document"]=doc;data.state["source_id"]="22222222-2222-4222-8222-222222222222"
    data.state["rows"]=[dict(principal_id=OWNER,source_ref=doc.source_ref,evidence_ref=doc.evidence_ref,
        occurred_at=doc.occurred_at,learning_context={"contract":"EvaHumanUnderstandingContext/v1",
            "records":[item,*data.state["context"]]})]
    result=module.housemate_state(data.rig.runtime)
    delta=result["personality_deltas"][0]
    assert delta["source_id"] == data.state["source_id"] and delta["version"] == 3
    assert delta["processing_state"] == "awaiting_dream" and delta["text"] == item["record"]["interpretation"]
    assert "Give reasoning for complex choices" not in str(result)


def test_optional_development_failure_does_not_hide_valid_personal_insights(rig):
    rig.runtime._memory=SimpleNamespace(_development_projection=SimpleNamespace(
        read_current=lambda query:(_ for _ in ()).throw(RuntimeError("private diagnostic must not leak"))))
    result=module.housemate_state(rig.runtime)
    assert result["current_learning"][0]["text"] == "corrected"
    assert result["development_state"] == "unavailable" and result["inner_state"] is None
    assert "private diagnostic" not in str(result)


def test_development_cannot_switch_owner_even_if_other_shape_looks_valid(rig):
    rig.runtime._memory=SimpleNamespace(_development_projection=SimpleNamespace(last_error=None,
        read_current=lambda query:dict(contract="EvaHousemateDevelopment/v1",audience="own_private_context",
            owner_id="daughter",is_execution_permission=False,shared_trace=None,inner_state={},personality_deltas=[])))
    with pytest.raises(RuntimeError,match="owner"):
        module.housemate_state(rig.runtime)


def test_erasure_between_projection_and_source_lookup_clears_derived_state(development_rig):
    data=development_rig
    # Simulate an earlier projection snapshot still holding its references. The
    # subsequent authoritative document lookup already knows the source is gone.
    data.rig.reader.erased=True
    result=module.housemate_state(data.rig.runtime)
    assert result["inner_state"] is None and result["development_state"] == "source_changed"
    assert result["personality_deltas"] == []
