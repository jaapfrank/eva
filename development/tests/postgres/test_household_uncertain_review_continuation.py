"""Durable household recovery boundaries; isolated PostgreSQL, no model calls."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker
from eva.application.household_initiative import HouseholdInitiativeContext
from tests.postgres.test_household_observation_initiative import _binding, _empty_shared_memory, _shared, _signal
from tests.postgres.test_owner_initiatives import NOW, costless_native  # noqa: F401
from tests.postgres.test_provider_failure_retry import database  # noqa: F401


@pytest.fixture
def postgres_engine(database):
    return database


@pytest.mark.parametrize("failure_recorded", [False, True], ids=["active-local-request", "legacy-no-close-proof"])
def test_unproven_request_still_blocks_a_different_fresh_source(postgres_engine, failure_recorded):
    tenant = "bounded-household-recovery-" + str(failure_recorded)
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    shared = _shared(postgres_engine, tenant)
    first = _signal(tenant + "-original")
    originals.retain(first)
    assert shared.schedule_household_observation(first, now=NOW) == "scheduled"
    claim = shared.mark_model_started(shared.claim_next(now=NOW), now=NOW)
    assert claim is not None
    if failure_recorded:
        error = RuntimeError("unproven provider outcome")
        error.local_model_end = {"contract": "SensoryLocalModelReturn/v1"}
        error.local_model_returned = True
        shared.fail(claim, error, now=NOW + timedelta(seconds=1))

    before = shared._timeline.events(claim.thread_id)
    state = shared._timeline.load(claim.thread_id)
    assert state.phase == ("will_usage_uncertain" if failure_recorded else "will_model_started")
    if failure_recorded:
        assert before[-1].payload["data"]["usage"] is None
        assert "local_model_end" not in before[-1].payload["data"]

    # A new ordinary source and the elapsed 300s window alone are insufficient.
    later = NOW + timedelta(seconds=301)
    second = _signal(tenant + "-different-source", at=later)
    assert second.source_event_id != first.source_event_id
    originals.retain(second)
    assert shared.next_household_observation(now=later) is None
    assert shared.schedule_household_observation(second, now=later) == "window_pending"
    assert shared._timeline.events(claim.thread_id) == before
    assert shared._timeline.load(claim.thread_id) == state
    assert originals._timeline.load("sensory:" + second.source_event_id).phase == "sensory_observed"


def _scenario(costless_native):
    authority, old, _, owner, originals, policy_path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    authority.policy()
    binding = _binding(old.tenant_id)
    shared = _shared(owner._engine, old.tenant_id, binding, authority.policy, old.principal_id)
    state = SimpleNamespace(originals=originals, shared=shared, authority=authority, tenant=old.tenant_id,
        policy_path=policy_path, policy=policy, engine=owner._engine, owner=owner,
        calls=[], admissions=[], outcomes=[], budget_allowed=True, memory_error=False,
        access_ready=True, sequence=0)

    class Model:
        def credential_status(self):
            return SimpleNamespace(ready=state.access_ready)

        def evaluate_sensory_event(self, event, *args, **kwargs):
            state.calls.append(event)
            outcome = state.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    def memory(query):
        if state.memory_error:
            raise RuntimeError("context preparation failed before the model")
        return _empty_shared_memory(query)

    def admission(claim, *, now):
        # This controlled port tests that existing admission remains compulsory;
        # it does not attest to a production mandate or provider budget.
        authority.policy()
        assert shared.context_review_claim_is_current(claim, now=now)
        state.admissions.append(claim)
        return SimpleNamespace(admitted=state.budget_allowed)

    state.worker = SensoryDecisionWorker(shared, Model(), SimpleNamespace(recall_for_presence=memory),
        IdentityProfile("Eva", "Family companion", ("Be honest",), 80),
        tenant_id=old.tenant_id, installation_id="home", principal_id=old.principal_id,
        household_context=HouseholdInitiativeContext(binding, authority.policy),
        context_review_authorizer=authority.policy, context_review_admission=admission)
    return state


def _source(state, *, seconds):
    state.sequence += 1
    at = NOW + timedelta(seconds=seconds)
    event = _signal(state.tenant + str(state.sequence), at=at)
    state.originals.retain(event)
    return event, at


def _first_closed_failure(state):
    event, at = _source(state, seconds=0)
    assert state.shared.schedule_household_observation(event, now=at) == "scheduled"
    state.outcomes.append(RuntimeError("synthetic model returned with unknown usage"))
    assert state.worker.run_once(now=at) == "failed"
    claim = state.admissions[-1]
    events = state.shared._timeline.events(claim.thread_id)
    assert events[-1].event_type == "WILL_USAGE_UNCERTAIN"
    assert events[-1].payload["data"]["usage"] is None
    proof = events[-1].payload["data"].get("local_model_end")
    assert isinstance(proof, dict), "a synchronous model return needs a bound local-end proof"
    assert proof["contract"] == "SensoryLocalModelReturn/v1"
    assert proof["source_event_id"] == claim.event.source_event_id
    assert proof["operation_id"] == claim.operation_id
    assert proof["claim_revision"] == claim.revision
    assert proof["error_fingerprint"] == events[-1].payload["data"]["failure_fingerprint"]
    return claim, events


def _measured_silence():
    return FreeWillDecision("silence", "No useful expression is needed", 0, "synthetic",
        usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=40, output_tokens=10))


def _assert_link(state, predecessor_claim, predecessor_events, successor_claim):
    started = next(event for event in state.shared._timeline.events(successor_claim.thread_id)
        if event.event_type == "WILL_MODEL_STARTED")
    prior_start = next(event for event in predecessor_events if event.event_type == "WILL_MODEL_STARTED")
    assert started.payload["data"]["recovery_predecessor"] == {
        "contract": "HouseholdProspectiveRecovery/v1",
        "thread_id": predecessor_claim.thread_id,
        "revision": predecessor_events[-1].sequence,
        "failure_sha256": predecessor_events[-1].payload_sha256,
        "source_sha256": predecessor_events[0].payload_sha256,
        "source_event_id": predecessor_claim.event.source_event_id,
        "owner_mandate_sha256": prior_start.payload["data"]["owner_mandate_sha256"],
        "household_policy_sha256": predecessor_claim.event.payload["household_binding"]["policy_sha256"],
        "failed_at": predecessor_events[-1].occurred_at.isoformat(),
    }


def test_closed_failure_allows_one_different_source_and_a_second_uncertainty_blocks(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    early, early_at = _source(state, seconds=299)
    assert state.shared.schedule_household_observation(early, now=early_at) == "window_pending"
    second, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(second, now=at) == "scheduled"
    state.outcomes.append(RuntimeError("second unknown provider outcome"))
    assert state.worker.run_once(now=at) == "failed"
    _assert_link(state, first_claim, original_events, state.admissions[-1])
    assert len(state.calls) == len(state.admissions) == 2
    assert len({event.source_event_id for event in state.calls}) == 2
    third, later = _source(state, seconds=602)
    assert state.shared.next_household_observation(now=later) is None
    assert state.shared.schedule_household_observation(third, now=later) == "window_pending"
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert state.shared._timeline.load(first_claim.thread_id).phase == "will_usage_uncertain"
    assert len(state.calls) == 2


def test_only_a_measured_linked_success_reopens_the_ordinary_cycle(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    second, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(second, now=at) == "scheduled"
    state.outcomes.append(_measured_silence())
    assert state.worker.run_once(now=at) == "silence"
    _assert_link(state, first_claim, original_events, state.admissions[-1])
    third, later = _source(state, seconds=602)
    assert state.shared.schedule_household_observation(third, now=later) == "scheduled"
    state.outcomes.append(_measured_silence())
    assert state.worker.run_once(now=later) == "silence"
    assert len(state.calls) == len(state.admissions) == 3
    assert len({event.source_event_id for event in state.calls}) == 3
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert original_events[-1].payload["data"]["usage"] is None


def test_context_preparation_failure_does_not_claim_a_local_model_return(costless_native):
    state = _scenario(costless_native)
    state.memory_error = True
    event, at = _source(state, seconds=0)
    assert state.shared.schedule_household_observation(event, now=at) == "scheduled"
    assert state.worker.run_once(now=at) == "failed"
    assert state.calls == []
    events = state.shared._timeline.events(state.admissions[-1].thread_id)
    assert all("local_model_end" not in event.payload["data"] for event in events)


def test_revoked_owner_cannot_admit_the_prospective_source(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    state.authority.revoke()
    second, at = _source(state, seconds=301)
    with pytest.raises(InitiativeMandateDenied):
        state.shared.schedule_household_observation(second, now=at)
    assert len(state.calls) == len(state.admissions) == 1
    assert state.shared._timeline.events(first_claim.thread_id) == original_events


def test_different_household_binding_cannot_consume_the_continuation(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    other = _shared(state.engine, state.tenant, _binding(state.tenant, "c" * 32),
        state.authority.policy, costless_native[1].principal_id)
    second, at = _source(state, seconds=301)
    assert other.schedule_household_observation(second, now=at) == "window_pending"
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert len(state.calls) == 1


def test_budget_denial_is_not_a_measured_success_and_cannot_open_another_slot(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    second, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(second, now=at) == "scheduled"
    state.budget_allowed = False
    assert state.worker.run_once(now=at) == "waiting_for_budget"
    assert len(state.calls) == 1 and len(state.admissions) == 2
    third, later = _source(state, seconds=602)
    assert state.shared.schedule_household_observation(third, now=later) == "window_pending"
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert original_events[-1].payload["data"]["usage"] is None


def test_model_access_denial_prevents_a_second_provider_call(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    second, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(second, now=at) == "scheduled"
    state.access_ready = False
    assert state.worker.run_once(now=at) == "waiting_for_model_access"
    assert len(state.calls) == len(state.admissions) == 1
    assert state.shared._timeline.events(first_claim.thread_id) == original_events


def test_disabled_owner_policy_cannot_admit_a_continuation(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    state.policy_path.write_text(json.dumps({**state.policy, "enabled": False}))
    second, at = _source(state, seconds=301)
    with pytest.raises(InitiativeMandateDenied):
        state.shared.schedule_household_observation(second, now=at)
    assert len(state.calls) == len(state.admissions) == 1
    assert state.shared._timeline.events(first_claim.thread_id) == original_events


def test_competing_fresh_sources_cannot_consume_two_prospective_slots(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    for _ in range(2):
        event, at = _source(state, seconds=301)
        assert state.shared.schedule_household_observation(event, now=at) == "scheduled"
    claims = [state.shared.claim_next(now=at), state.shared.claim_next(now=at)]
    assert all(claim is not None for claim in claims)
    assert len({claim.thread_id for claim in claims}) == 2
    with ThreadPoolExecutor(max_workers=2) as executor:
        started = tuple(executor.map(lambda claim: state.shared.mark_model_started(claim, now=at), claims))
    assert sum(claim is not None for claim in started) == 1
    _assert_link(state, first_claim, original_events, next(claim for claim in started if claim is not None))
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert len(state.calls) == 1  # Store admission never invokes a provider.


def test_failure_after_a_model_return_does_not_forge_a_model_exception_proof(costless_native):
    state = _scenario(costless_native)
    event, at = _source(state, seconds=0)
    assert state.shared.schedule_household_observation(event, now=at) == "scheduled"
    state.outcomes.append(_measured_silence())

    def commit_failed(*args, **kwargs):
        raise RuntimeError("a separate failure after the successful model return")

    state.shared.complete = commit_failed
    assert state.worker.run_once(now=at) == "failed"
    assert len(state.calls) == 1
    events = state.shared._timeline.events(state.admissions[-1].thread_id)
    assert events[-1].event_type == "WILL_USAGE_UNCERTAIN"
    assert "local_model_end" not in events[-1].payload["data"]


def test_a_new_denial_during_atomic_revalidation_cannot_start_an_unlinked_review(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    event, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(event, now=at) == "scheduled"
    claim = state.shared.claim_next(now=at)
    assert claim is not None
    original_check = state.shared._household_recovery_state
    checks = []

    def authority_changes(connection, **kwargs):
        checks.append(True)
        if len(checks) == 1:
            blocked, predecessor = original_check(connection, **kwargs)
            assert blocked is False and predecessor is not None
            return blocked, predecessor
        # The newly authoritative result denies admission and offers no anchor.
        return True, None

    state.shared._household_recovery_state = authority_changes
    assert state.shared.mark_model_started(claim, now=at) is None
    assert len(checks) == 2
    assert not any(event.event_type == "WILL_MODEL_STARTED"
        for event in state.shared._timeline.events(claim.thread_id))
    assert state.shared._timeline.events(first_claim.thread_id) == original_events
    assert len(state.calls) == 1


def test_abandoning_the_first_failure_does_not_refund_its_consumed_successor_slot(costless_native):
    state = _scenario(costless_native)
    first_claim, original_events = _first_closed_failure(state)
    second, at = _source(state, seconds=301)
    assert state.shared.schedule_household_observation(second, now=at) == "scheduled"
    state.outcomes.append(RuntimeError("the linked successor also lost its usage"))
    assert state.worker.run_once(now=at) == "failed"
    successor_claim = state.admissions[-1]
    _assert_link(state, first_claim, original_events, successor_claim)
    successor_events = state.shared._timeline.events(successor_claim.thread_id)
    first_state = state.shared._timeline.load(first_claim.thread_id)
    result = state.owner.abandon_uncertain_review(first_claim.thread_id, first_state.revision,
        now=NOW + timedelta(seconds=602))
    assert result["disposition"] == "abandoned" and result["usage"] == "unknown"
    closed = state.shared._timeline.events(first_claim.thread_id)
    assert closed[:-1] == original_events
    assert closed[-1].event_type == "WILL_REVIEW_ABANDONED"
    assert closed[-1].payload["data"]["usage"] is None
    third, later = _source(state, seconds=603)
    assert state.shared.schedule_household_observation(third, now=later) == "window_pending"
    assert state.shared._timeline.events(successor_claim.thread_id) == successor_events
    assert len(state.calls) == 2
