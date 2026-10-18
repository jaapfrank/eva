"""A changed installed decision contract is distinct from this hour's old source."""
from datetime import timedelta
from eva.application.free_will import FreeWillDecision
from eva.application.resident_attention import NextAttention
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_initiative_memory_search import store


def complete_review(source, at, *, attention=None):
    assert source.schedule_context_review(now=at) == "scheduled"
    claim = source.mark_model_started(source.claim_next(now=at), now=at)
    source.complete(claim, FreeWillDecision("silence", "No message or forced task", 0,
        "synthetic", next_attention=attention), now=at)
    return claim


def test_changed_profile_in_same_hour_has_immutable_source_and_restart_dedup(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    first = store(auth, old, "previous-contract")
    original = complete_review(first, NOW)
    history = first._timeline.events(original.thread_id)
    updated = store(auth, old, "changed-contract")
    at = NOW + timedelta(minutes=1)
    replacement = complete_review(updated, at)
    assert replacement.thread_id != original.thread_id
    assert replacement.thread_id.rsplit(":", 1)[1].isdigit()
    assert replacement.event.payload["review_cause"] == "installed_profile_change"
    assert replacement.event.payload["review_window"] == original.event.payload["review_window"]
    assert replacement.event.payload["review_profile"] == "changed-contract"
    assert first._timeline.events(original.thread_id) == history
    restarted = store(auth, old, "changed-contract")
    assert restarted.schedule_context_review(now=at + timedelta(minutes=1)) == "idle"
    assert updated._timeline.events(replacement.thread_id)[0].payload["data"]["payload"]["review_profile"] == "changed-contract"


def test_changed_profile_keeps_model_selected_future_attention(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    first = store(auth, old, "previous-contract")
    original = complete_review(first, NOW, attention=NextAttention(
        NOW + timedelta(hours=2), "A meaningful later occasion"))
    history = first._timeline.events(original.thread_id)
    updated = store(auth, old, "changed-contract")
    assert updated.schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"
    assert first._timeline.events(original.thread_id) == history


def test_profile_change_does_not_bypass_inflight_decision(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    first = store(auth, old, "previous-contract")
    assert first.schedule_context_review(now=NOW) == "scheduled"
    original = first.mark_model_started(first.claim_next(now=NOW), now=NOW)
    history = first._timeline.events(original.thread_id)
    assert store(auth, old, "changed-contract").schedule_context_review(
        now=NOW + timedelta(minutes=1)) == "idle"
    assert first._timeline.events(original.thread_id) == history


def test_absent_profile_keeps_hourly_source_and_does_not_replay(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    first = store(auth, old)
    original = complete_review(first, NOW)
    assert original.thread_id.rsplit(":", 1)[1] == str(int(NOW.timestamp()) // 3600)
    assert "review_cause" not in original.event.payload
    assert store(auth, old).schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"
