"""Lifecycle checks for the same memory records used by real contact/dream paths."""
import json
from types import SimpleNamespace

import pytest

from eva.application.housemate_development import select_development, resolve_development
from eva.application.human_understanding import (
    build_human_understanding_document, parse_human_understanding_record,
)
from eva.application.memory_audience import MemoryAudience

OWNER = "parent"
NOW = "2026-10-24T12:00:00Z"


def source(text, *, kind="personality_delta", previous=None, at="2026-10-24T09:00:00Z", principal=OWNER,
           outcome="open", review_at=None):
    turn = SimpleNamespace(tenant_id="house", installation_id="eva", principal_id=principal,
        audience=MemoryAudience.private_person(principal_id=principal), observation_binding=None, project_id=None,
        text=text, message_id="message:" + at, occurred_at=at, conversation_id=principal)
    record_args = dict(kind=kind, interpretation=text, uncertainty_reason="Only this person's explicit feedback",
        review_at=review_at, outcome=outcome,
        previous_source_ref=previous["source_ref"] if previous else None,
        previous_evidence_ref=previous["evidence_ref"] if previous else None)
    doc = build_human_understanding_document(turn, record_args)
    record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
    entry = dict(source_ref=doc.source_ref, evidence_ref=doc.evidence_ref, record=record)
    return dict(principal_id=principal, source_ref=doc.source_ref, evidence_ref=doc.evidence_ref,
        occurred_at=at, learning_context={"contract":"EvaHumanUnderstandingContext/v1",
            "records":[entry, *(previous["learning_context"]["records"] if previous else [])]})


def project(**changes):
    return dict(intent_id="curiosity:one", title="A real chosen line", motivation="Try an idea",
        question="Can I improve my existing project?", status="working", phase="personal_exploring",
        updated_at="2026-10-24T10:00:00+00:00", **changes)


def select(sources=(), projects=()):
    return select_development(owner_id=OWNER, principals=(OWNER,), sources=sources, projects=projects, now=NOW)


def resolve(selection, sources=(), projects=(), **changes):
    return resolve_development(selection, owner_id=OWNER, principals=(OWNER,), sources=sources,
        projects=projects, now=changes.get("now", NOW))


def test_selection_keeps_references_only_and_resolves_same_real_feedback():
    feedback = source("Give me a short recommendation before details")
    own_project = project()
    selection = select([feedback], [own_project])
    encoded = json.dumps(selection)
    assert "short recommendation" not in encoded and "improve my existing" not in encoded
    state = resolve(selection, [feedback], [own_project])
    assert state["personality_deltas"][0]["adjustment"] == feedback["learning_context"]["records"][0]["record"]["interpretation"]
    assert state["personality_deltas"][0]["applies_to"] == OWNER
    assert state["inner_state"]["current_curiosity"] == own_project["question"]
    assert state["inner_state"]["last_updated"] == "2026-10-24T10:00:00Z"
    assert state["shared_trace"] is None and state["is_execution_permission"] is False


def test_current_correction_invalidates_processed_old_delta_before_next_dream():
    first = source("Keep answers short")
    selection = select([first])
    revised = source("For complex research include the reasoning", previous=first, at="2026-10-24T11:00:00Z")
    assert resolve(selection, [revised]) is None
    current = resolve(select([revised]), [revised])
    assert current["personality_deltas"][0]["version"] == 2
    assert current["personality_deltas"][0]["previous"]["source_ref"] == first["source_ref"]
    # A missing predecessor invalidates the revised chain; no fallback to old text.
    revised["learning_context"]["records"].pop()
    assert resolve(selection, [revised]) is None


def test_erasure_and_person_boundary_do_not_revive_private_meaning():
    feedback = source("No unasked washing advice")
    selection = select([feedback])
    assert resolve(selection, []) is None
    child = source("Always use jokes", principal="daughter")
    assert resolve(selection, [child]) is None
    with pytest.raises(ValueError):
        resolve_development(selection, owner_id="daughter", principals=("daughter",),
            sources=[feedback], projects=[], now=NOW)


def test_stopped_or_changed_project_cannot_reappear_from_old_selection():
    own_project = project()
    selection = select(projects=[own_project])
    stopped = {**own_project, "status":"stopped", "phase":"personal_stopped"}
    assert resolve(selection, projects=[stopped]) is None
    newer = {**own_project, "question":"A genuinely changed next step"}
    assert resolve(selection, projects=[newer]) is None
    assert resolve(select(projects=[newer]), projects=[newer])["inner_state"]["current_curiosity"] == newer["question"]


def test_generic_hypotheses_missing_evidence_and_expired_review_are_not_personality():
    assert resolve(select([source("Maybe enjoys research", kind="person_hypothesis")]), []) is None
    assert not select([source("Try concise answers", review_at=NOW)])["delta_refs"]
    assert resolve(select(), []) is None
    future = source("Be warmer", at="2026-10-24T13:00:00Z")
    assert not select([future])["delta_refs"]


def test_existing_project_and_delta_bounds_do_not_create_extra_work():
    projects = [{**project(), "intent_id":f"curiosity:{i}"} for i in range(8)]
    sources = [source(f"Context-specific adjustment {i}", at=f"2026-10-24T09:00:0{i}Z") for i in range(7)]
    selection = select(sources, projects)
    assert len(selection["project_refs"]) == 2 and len(selection["delta_refs"]) == 3
    state = resolve(selection, sources, projects)
    assert len(state["inner_state"]["open_threads"]) == 2
    assert len(state["personality_deltas"]) == 3


def test_refuted_delta_is_not_a_positive_new_preference():
    first = source("A tentative style")
    rejected = source("This was not my preference", previous=first, outcome="refuted", at="2026-10-24T11:00:00Z")
    assert not select([rejected])["delta_refs"]


@pytest.mark.parametrize("tampered", [False, True])
def test_old_semantic_reference_with_valid_current_head_does_not_duplicate_delta(tampered):
    from copy import deepcopy
    from dataclasses import replace
    from eva.adapters.projection.hindsight import HindsightMemoryEngine
    from eva.application.housemate_development import with_current_development
    from eva.application.memory import RecallCurrentFacts
    from eva.contracts.canonical_json import canonical_sha256
    from eva.contracts.planning import ConfirmedFact
    from types import SimpleNamespace

    first=source("Keep simple replies short")
    current=source("For research give the reasoning",previous=first,at="2026-10-24T11:00:00Z")
    selected=select([current]);state=resolve(selected,[current])
    state["selection_sha256"]=canonical_sha256(selected);state["selection_revision"]=1
    query=RecallCurrentFacts(recall_id="dedup",tenant_id="house",installation_id="eva",principal_id=OWNER,
        audience=MemoryAudience.private_person(principal_id=OWNER),conversation_id=OWNER,project_id=None,
        query_purpose="conversation",requested_valid_at=NOW,scope={"kind":"person","principal_id":OWNER},
        subject_ids=(OWNER,),query_text="Hi",maximum_facts=8)
    memory=HindsightMemoryEngine._empty_presence(query)
    learning=deepcopy(current["learning_context"])
    if tampered:
        learning["records"][0]["record"]["interpretation"]="Unchecked payload pretending to be the head"
    old_semantic_fact=ConfirmedFact(fact_id="earlier-semantic-fact",subject=OWNER,predicate="memory.observation",
        value={"learning_context":learning},source_ref=first["source_ref"],evidence_ref=first["evidence_ref"],
        observed_at=first["occurred_at"],trust="host_policy")
    projected=with_current_development(replace(memory,confirmed_facts=(old_semantic_fact,)),query,
        SimpleNamespace(read_current=lambda q:state))
    rendered=next(f.value["text"] for f in projected.confirmed_facts if f.predicate=="eva.development_state")
    payload=json.loads(rendered)
    assert bool(payload["personality_deltas"]) is tampered
    if tampered:
        assert payload["personality_deltas"][0]["adjustment"] == "For research give the reasoning"
