"""Measured invalid decisions terminate their source without accepting effects or replay."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.free_will import AutonomousUsageUncertain, SensoryDecisionWorker
from tests.postgres.test_initiative_preparation_recovery import _review, costless_native, NOW
from tests.postgres.test_household_observation_initiative import _empty_shared_memory


def invalid_response(*, kind="measured", code="invalid_decision"):
    usage = (ConversationModelUsage.unmeasured() if kind == "unmeasured" else
        ConversationModelUsage.partial(model_attempts=1, tool_calls=1, input_tokens=120, output_tokens=30)
        if kind == "partial" else ConversationModelUsage.measured(tool_calls=1, input_tokens=120, output_tokens=30))
    error = (AutonomousUsageUncertain("synthetic unverified provider usage") if kind == "provider" else
        ConversationModelError("synthetic private response detail", model_usage=usage))
    if kind == "provider": error.model_usage = usage
    if kind != "no_marker":
        error.validation_failure_stage = "other_stage" if kind == "other_stage" else "sensory_decision"
    error.validation_failure_code = code
    return error


@pytest.mark.parametrize("household", [False, True])
def test_measured_rejection_is_terminal_no_effects_no_replay_and_future_lane_proceeds(costless_native, household):
    auth, old, owner, store, binding = _review(costless_native, household=household)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    before = store._timeline.events(claim.thread_id)
    error = invalid_response()
    store.fail(claim, error, now=NOW)
    state = store._timeline.load(claim.thread_id)
    assert state.phase == "will_decision_rejected" and state.status == "completed"
    events = store._timeline.events(claim.thread_id)
    assert events[:-1] == before
    event = events[-1]
    assert event.event_type == "WILL_DECISION_REJECTED"
    data = event.payload["data"]
    assert data["usage"] == error.model_usage.to_wire()
    assert data["decision_valid"] is False and data["effects_applied"] is False
    assert data["automatic_retry"] is False
    assert data["validation_failure_code"] == "invalid_decision"
    assert "synthetic private response detail" not in str(event.payload)
    assert not any(e.event_type in {"WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED", "PERSONAL_INTENT_ADOPTED"}
        for e in events)
    assert store.claim_next(now=NOW + timedelta(seconds=1)) is None
    assert store.claim_next_expression(now=NOW + timedelta(seconds=1)) is None
    lane = owner.page(now=NOW)["review_lanes"]["household" if household else "private"]
    assert lane["latest_review"]["state"] == "rejected"
    assert lane["latest_review"]["blocker"] is None
    assert lane["unresolved_review"] is None
    assert store.schedule_context_review(now=NOW + timedelta(seconds=1)) == "idle"
    assert store.schedule_context_review(now=NOW + timedelta(hours=1)) == "scheduled"
    assert store.claim_next(now=NOW + timedelta(hours=1)).thread_id != claim.thread_id


@pytest.mark.parametrize("kind", ["unmeasured", "partial", "provider", "no_marker", "other_stage"])
def test_unknown_or_unrelated_failure_keeps_original_no_replay_fence(costless_native, kind):
    auth, old, owner, store, binding = _review(costless_native, household=False)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.fail(claim, invalid_response(kind=kind), now=NOW)
    state = store._timeline.load(claim.thread_id)
    assert state.phase == "will_usage_uncertain" and state.status == "needs_help"
    event = store._timeline.events(claim.thread_id)[-1]
    assert event.event_type == "WILL_USAGE_UNCERTAIN" and event.payload["data"]["usage"] is None
    assert store.claim_next(now=NOW + timedelta(hours=1)) is None
    assert store.schedule_context_review(now=NOW + timedelta(hours=1)) == "idle"


@pytest.mark.parametrize("change", ["revision", "source", "operation"])
def test_measured_rejection_requires_exact_live_claim(costless_native, change):
    auth, old, owner, store, binding = _review(costless_native, household=False)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    before = store._timeline.events(claim.thread_id)
    if change == "revision": claim = replace(claim, revision=claim.revision - 1)
    elif change == "operation": claim = replace(claim, operation_id="other")
    else: claim = replace(claim, event=replace(claim.event, payload={"changed": True}))
    with pytest.raises(CheckpointConflict): store.fail(claim, invalid_response(), now=NOW)
    assert store._timeline.events(claim.thread_id) == before


def test_worker_persists_measured_rejection_without_second_model_attempt(costless_native):
    auth, old, owner, store, binding = _review(costless_native, household=False)
    called = []
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            called.append(True)
            raise invalid_response(code="private cause text must not persist")
    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=_empty_shared_memory),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
        installation_id="home", principal_id=old.principal_id,
        context_review_authorizer=auth.policy,
        context_review_admission=lambda *a, **k: SimpleNamespace(admitted=True), clock=lambda: NOW)
    assert worker.run_once(now=NOW) == "failed" and called == [True]
    state = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix)[0]
    assert state.phase == "will_decision_rejected"
    assert store._timeline.events(state.thread_id)[-1].payload["data"]["validation_failure_code"] == "invalid_decision"
    assert worker.run_once(now=NOW) == "idle" and called == [True]
