"""Measured retained WILL_DECIDED survives the next normal review context read."""
from datetime import timedelta

import pytest

from eva.application.conversation import ConversationModelUsage
from eva.application.free_will import FreeWillDecision
from eva.application.resident_attention import NextAttention
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_source_attention import sources


@pytest.mark.parametrize("source_bound", [False, True], ids=["legacy", "watch"])
def test_measured_decision_is_read_by_subsequent_ordinary_review(sources, source_bound):
    auth, old, owner, originals, stream, first, factory, store = sources
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.complete(claim, FreeWillDecision("silence", "Revisit a retained information gap", 0,
        "synthetic-fixture-no-model-call",
        usage=ConversationModelUsage.measured(tool_calls=0, input_tokens=8, output_tokens=4),
        next_attention=NextAttention(NOW + timedelta(hours=1), "Review the existing source",
            first.source_event_id if source_bound else None)), now=NOW)
    events = store._timeline.events(claim.thread_id)
    decision = events[-1]
    assert decision.event_type == "WILL_DECIDED"
    selected = decision.payload["data"]["next_attention"]
    assert set(selected) == {"requested_at", "at", "reason", "selection_sequence"} | (
        {"source_watch"} if source_bound else set())
    if source_bound:
        assert selected["source_watch"]["source_event_id"] == first.source_event_id
        assert selected["source_watch"]["selected_at"] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    hashes = [(event.sequence, event.payload_sha256) for event in events]

    # This invokes the normal schedule -> retained private context -> model-input
    # path. No model/provider is entered; the new claim is inspected only.
    due = NOW + timedelta(hours=1)
    restarted = factory()
    assert restarted.schedule_context_review(now=due) == "scheduled"
    subsequent = restarted.claim_next(now=due)
    history = subsequent.event.payload["life_context"]["own_orientation_history"]
    assert len(history) == 1
    assert history[0]["next_attention"] == selected
    assert history[0]["decision_sha256"] == decision.payload_sha256
    assert history[0]["classification"] == "historical_model_decision_not_owner_preference_or_execution_permission"
    assert [(event.sequence, event.payload_sha256) for event in
        restarted._timeline.events(claim.thread_id)] == hashes
