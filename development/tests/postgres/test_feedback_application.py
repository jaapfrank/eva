"""Actual feedback event -> changed decision -> owner-visible provenance."""
from datetime import timedelta
import pytest
from eva.application.free_will import FreeWillDecision
from eva.bootstrap.dashboard_learning import _feedback_application
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.model.test_feedback_application_contract import assessment


@pytest.mark.parametrize("stale", [False, "before", "during"])
def test_feedback_application_is_retained_and_visible_only_for_exact_current_source(review_setup, stale, monkeypatch):
    auth, old, control, budget, store, factory, *_ = review_setup
    control.feedback(old.intent_id, "This list did not help", now=NOW)
    source = next(item for item in store.recent_initiatives() if item["intent_id"] == old.intent_id)
    choice = {**assessment(), "intent_id": old.intent_id,
        "feedback_sha256": source["owner_feedback_sha256"]}
    claim = store.claim_next(now=NOW)
    claim = store.mark_model_started(claim, now=NOW)
    if stale:
        if stale == "before":
            control.feedback(old.intent_id, "Newer feedback", now=NOW+timedelta(seconds=1))
        else:
            original = store._event_from_thread
            def correction_before_commit(*args, **kwargs):
                value = original(*args, **kwargs)
                control.feedback(old.intent_id, "Newer feedback", now=NOW+timedelta(seconds=1),
                    request_key="concurrent-correction")
                return value
            monkeypatch.setattr(store, "_event_from_thread", correction_before_commit)
        with pytest.raises(ValueError, match="source differs"):
            store.complete(claim, FreeWillDecision("silence", "Changed choice", 0,
                "synthetic", feedback_application=choice), now=NOW+timedelta(seconds=2))
        assert store._timeline.load(claim.thread_id).phase == "will_model_started"
        return
    store.complete(claim, FreeWillDecision("silence", "Changed choice", 0,
        "synthetic", feedback_application=choice), now=NOW+timedelta(seconds=2))
    decision = store._timeline.events(claim.thread_id)[-1].payload["data"]
    assert decision["feedback_application"] == choice
    feedback = store._timeline.events("intent:"+old.intent_id)[-1]
    def view(feedback):
        with control._engine.begin() as conn:
            control._authorize(conn, NOW+timedelta(seconds=3))
            return _feedback_application(conn, control, store._timeline,
                {"intent_id": old.intent_id}, feedback, NOW+timedelta(seconds=3))
    result = view(feedback)
    assert result["adjustment"] == choice["adjustment"]
    assert result["timing"] == result["nuisance"] == "unknown"
    assert result["meaning"] == "provisional_decision_link_not_proven_improvement"
    control.feedback(old.intent_id, "Different feedback", now=NOW+timedelta(seconds=3))
    assert view(store._timeline.events("intent:"+old.intent_id)[-1]) is None


def test_new_feedback_gets_one_review_without_cancelling_future_attention(review_setup):
    from tests.postgres.test_next_attention import finish
    from eva.application.resident_attention import NextAttention
    auth, old, control, budget, store, factory, *_ = review_setup
    future = NOW + timedelta(hours=2)
    origin = finish(store, choice=NextAttention(future, "Check later"))
    control.feedback(old.intent_id, "This list did not help", now=NOW+timedelta(seconds=1), request_key="once")
    at = NOW+timedelta(seconds=2)
    assert factory().schedule_context_review(now=at) == "scheduled"
    claim = store.claim_next(now=at)
    claim = store.mark_model_started(claim, now=at)
    source = claim.event.payload["feedback_source"]
    assert claim.event.payload["review_cause"] == "owner_feedback"
    assert source["intent_id"] == old.intent_id
    assert store.context_review_claim_is_current(claim, now=at)
    control.feedback(old.intent_id, "This list did not help", now=at, request_key="once")
    assert store.context_review_claim_is_current(claim, now=at)
    store.complete(claim, FreeWillDecision("silence", "Keep the later appointment", 0,
        "synthetic", feedback_application={**assessment(), **source}), now=at)
    assert factory().schedule_context_review(now=at+timedelta(seconds=1)) == "idle"
    assert factory().schedule_context_review(now=future) == "scheduled"
    assert store.claim_next(now=future).event.payload["attention_origin"]["source_thread_id"] == origin.thread_id


def test_newer_feedback_invalidates_queued_review_and_old_feedback_is_not_replayed(review_setup):
    from tests.postgres.test_next_attention import finish
    auth, old, control, budget, store, factory, *_ = review_setup
    finish(store)
    control.feedback(old.intent_id, "First response", now=NOW+timedelta(seconds=1))
    at = NOW+timedelta(seconds=2)
    assert store.schedule_context_review(now=at) == "scheduled"
    claim = store.claim_next(now=at)
    claim = store.mark_model_started(claim, now=at)
    assert store.context_review_claim_is_current(claim, now=at)
    control.feedback(old.intent_id, "Corrected response", now=at+timedelta(seconds=1))
    assert not store.context_review_claim_is_current(claim, now=at+timedelta(seconds=2))
    with control._engine.begin() as connection:
        store._timeline._scope(connection)
        assert store._latest_owner_feedback(connection, now=NOW+timedelta(hours=25)) is None


def test_feedback_corrected_during_recall_cancels_without_model_and_new_source_runs(review_setup):
    from tests.postgres.test_next_attention import finish, worker
    auth, old, control, budget, store, factory, *_ = review_setup
    finish(store)
    control.feedback(old.intent_id, "First response", now=NOW+timedelta(seconds=1))
    at = NOW+timedelta(seconds=2)
    assert store.schedule_context_review(now=at) == "scheduled"
    calls = []
    class Model:
        def evaluate_sensory_event(self, event, *args, **kwargs):
            calls.append(event)
            return FreeWillDecision("silence", "Considered corrected response", 0, "synthetic")
    class Memory:
        def recall_for_presence(self, query):
            control.feedback(old.intent_id, "Corrected response", now=at, request_key="correction")
            return object()
    assert worker(store, auth, Model(), memory=Memory()).run_once(now=at) == "cancelled"
    assert calls == []
    assert factory().schedule_context_review(now=at+timedelta(seconds=1)) == "scheduled"
    assert worker(factory(), auth, Model()).run_once(now=at+timedelta(seconds=1)) == "silence"
    assert len(calls) == 1


def test_feedback_changed_during_model_retains_measured_rejection(review_setup):
    from tests.postgres.test_next_attention import finish, worker
    from eva.application.conversation import ConversationModelUsage
    auth, old, control, budget, store, factory, *_ = review_setup
    finish(store)
    control.feedback(old.intent_id, "First response", now=NOW+timedelta(seconds=1))
    at = NOW+timedelta(seconds=2)
    assert store.schedule_context_review(now=at) == "scheduled"
    seen = []
    class Model:
        def evaluate_sensory_event(self, event, *args, **kwargs):
            seen.append(event)
            control.feedback(old.intent_id, "Changed during model", now=at)
            return FreeWillDecision("silence", "Stale judgment", 0, "synthetic",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=100, output_tokens=20))
    assert worker(store, auth, Model()).run_once(now=at) == "failed"
    assert len(seen) == 1
    events = store._timeline.events("sensory:"+seen[0].source_event_id)
    assert events[-1].event_type == "WILL_DECISION_REJECTED"
    assert events[-1].payload["data"]["usage"]["input_tokens"] == 100
    assert store.claim_next(now=at) is None
    assert store.schedule_context_review(now=at+timedelta(seconds=1)) == "scheduled"


def test_feedback_correction_at_commit_rejects_unannotated_proposal(review_setup, monkeypatch):
    from tests.postgres.test_next_attention import finish, worker
    from eva.application.conversation import ConversationModelUsage
    from eva.application.free_will import PersonalInitiativeProposal
    auth, old, control, budget, store, factory, *_ = review_setup
    finish(store)
    control.feedback(old.intent_id, "First response", now=NOW+timedelta(seconds=1))
    at = NOW+timedelta(seconds=2)
    assert store.schedule_context_review(now=at) == "scheduled"
    seen = []
    original = store._event_from_thread
    class Model:
        def evaluate_sensory_event(self, event, *args, **kwargs):
            seen.append(event)
            def correct_before_transaction(*args, **kwargs):
                value = original(*args, **kwargs)
                control.feedback(old.intent_id, "Corrected at commit", now=at, request_key="commit-race")
                return value
            monkeypatch.setattr(store, "_event_from_thread", correct_before_transaction)
            return FreeWillDecision("silence", "Stale proposal", 0, "synthetic",
                initiative=PersonalInitiativeProposal("Stale work", "Old feedback", "Research something"),
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=100, output_tokens=20))
    before = {x["intent_id"] for x in store.recent_initiatives()}
    assert worker(store, auth, Model()).run_once(now=at) == "failed"
    assert {x["intent_id"] for x in store.recent_initiatives()} == before
    events = store._timeline.events("sensory:"+seen[0].source_event_id)
    assert events[-1].event_type == "WILL_DECISION_REJECTED"
    assert events[-1].payload["data"]["usage"]["input_tokens"] == 100
