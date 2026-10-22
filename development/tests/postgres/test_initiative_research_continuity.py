"""Existing research receipts survive the private initiative handoff."""
from datetime import timedelta
import hashlib
import pytest

from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.initiative_source_outcomes import read_recent_source_outcomes
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.contracts.canonical_json import canonical_json_bytes
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_initiative_source_outcomes import _finish
from tests.postgres.test_subscription_initiative_reviews import review_setup


def research_read(f, work, fault=None):
    _, turn = f.auth.bind_work(work.spec)
    port = object.__new__(PostgresExternalConversationTools)
    port._engine, port._action_names = f.control._engine, ("research_current_options",)
    value = {"title": "How reminders affect attention", "question": "What do primary sources say?",
        "content": "Indirect evidence, not a household preference. " * 90,
        "sources": [{"title": "Original research", "url": "https://example.org/research"}],
        "researched_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "provider": "synthetic-research",
        "verification_scope": "research_artifact_only", "semantic_claims_independently_verified": False}
    value["content_sha256"] = hashlib.sha256(value["content"].encode()).hexdigest()
    if fault == "malformed_content": value["content"] = None
    if fault == "malformed_source": value["sources"][0]["title"] = []
    if fault == "content_hash": value["content_sha256"] = "f"*64
    if fault == "future": value["researched_at"] = (NOW+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if fault == "semantic_claim": value["semantic_claims_independently_verified"] = True
    with background_execution(BackgroundExecutionScope(turn, work.spec.execution_id)):
        thread = port._thread_id(turn, "research_current_options", 0)
        timeline = port._timeline(turn)
        timeline.create_thread(thread_id=thread, checkpoint_id=thread+":admitted",
            event_type="TOOL_REQUEST_ADMITTED", phase="admitted", status="active", occurred_at=NOW,
            data={"action_call_id": "research-0", "action_name": "research_current_options",
                "action_arguments": {"title": value["title"], "question": "Wrong question" if fault=="question" else value["question"]},
                "step_index": 0, "principal_id": turn.principal_id,
                "conversation_id": turn.conversation_id, "source_message_id": turn.message_id,
                "execution_namespace": "background-work:"+"f"*64 if fault=="namespace" else BackgroundExecutionScope(turn, work.spec.execution_id).namespace,
                "receipt_output": {"verification_scope": "research_artifact_only"},
                "observation_output_key": "result"})
        timeline.transition(thread_id=thread, expected_revision=1, checkpoint_id=thread+":done",
            event_type="STATE_VERIFICATION_TIMED_OUT" if fault=="failed" else "STATE_VERIFIED",
            phase="failed" if fault=="failed" else "verified",
            status="failed" if fault=="failed" else "completed", occurred_at=NOW,
            data={"proof": {"evidence": {"observation": value,
                "verification_scope": "research_artifact_only", "external_state_verified": False}}})
    return timeline, thread, value


def test_cited_research_reaches_next_decision_without_research_dispatch(native_digital, monkeypatch):
    f=native_digital; _,work=admitted(f);call=started(f,work)
    timeline,thread,original=research_read(f,work);_finish(f,work,call)
    before=timeline.events(thread)
    monkeypatch.setattr(PostgresExternalConversationTools,"execute",lambda *a,**k:pytest.fail("no research replay"))
    values=read_recent_source_outcomes(f.auth,now=NOW)
    read=values[0]["verified_reads"][0];observed=read["observation"]
    assert observed["content"].startswith("Indirect evidence")
    assert observed["sources"]==original["sources"]
    assert observed["content_sha256"]==original["content_sha256"]
    assert observed["content_truncated"]
    assert observed["semantic_claims_independently_verified"] is False
    assert read["is_current_proof"] is False
    assert read["observation_projection"]=="bounded_original_research_artifact"
    assert timeline.events(thread)==before
    assert len(canonical_json_bytes(list(values)))<=8192
    later=read_recent_source_outcomes(f.auth,now=NOW+timedelta(minutes=5))
    assert later[0]["research_evidence_sha256"] == values[0]["research_evidence_sha256"]


@pytest.mark.parametrize("fault",["namespace","content_hash","question","future","semantic_claim","failed","malformed_content","malformed_source"])
def test_invalid_research_never_becomes_source_context(native_digital,fault):
    f=native_digital;_,work=admitted(f);call=started(f,work)
    research_read(f,work,fault);_finish(f,work,call)
    value=read_recent_source_outcomes(f.auth,now=NOW)[0]
    assert value["verified_reads"]==[]
    assert value["unavailable_reads"]
    assert "example.org/research" not in str(value)


def test_malformed_evidence_is_rejected_without_context_crash():
    from types import SimpleNamespace
    from eva.adapters.postgres.initiative_source_outcomes import _research_artifact
    from eva.application.conversation import ConversationConflict
    for evidence in (None, []):
        with pytest.raises(ConversationConflict):
            _research_artifact(SimpleNamespace(output={"status":"verified", "result":{},
                "proof":{"evidence":evidence}}), NOW)


def test_newly_available_research_returns_once_without_repeating_completed_work(review_setup):
    from tests.postgres.test_initiative_source_feedback import finish, SOURCE
    *_, store, factory, before, hashes = review_setup
    finish(store)
    old = {**SOURCE[0], "verified_reads": [], "work_outcome": {"state":"succeeded"},
        "reported_output":{"text":"A conclusion without its original sources", "output_sha256":"d"*64}}
    at = NOW + timedelta(seconds=1)
    assert store.schedule_context_review(now=at, source_outcomes=(old,)) == "scheduled"
    original = finish(store, at)
    restored = {**old, "research_evidence_sha256":"e"*64}
    at += timedelta(seconds=1)
    assert store.schedule_context_review(now=at, source_outcomes=(restored,)) == "scheduled"
    corrected = finish(store, at)
    assert corrected.thread_id != original.thread_id
    assert corrected.event.payload["native_completion_source"]["research_evidence_sha256"] == "e"*64
    assert store.schedule_context_review(now=at+timedelta(seconds=1), source_outcomes=(restored,)) == "idle"
    assert store.schedule_context_review(now=at+timedelta(seconds=2), source_outcomes=(old,)) == "idle"


def test_new_research_does_not_replay_an_uncertain_decision(review_setup):
    from tests.postgres.test_initiative_source_feedback import SOURCE
    *_, store, factory, before, hashes = review_setup
    claim=store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.fail(claim, RuntimeError("lost provider response"), now=NOW)
    assert store.schedule_context_review(now=NOW+timedelta(hours=2),
        source_outcomes=({**SOURCE[0], "research_evidence_sha256":"e"*64},)) == "idle"
