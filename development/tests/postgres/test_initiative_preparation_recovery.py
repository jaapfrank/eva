"""Exact preparation/dispatch/owner recovery boundaries in disposable PostgreSQL."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import CheckpointConflict, operational_events
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import IdentityProfile
from eva.application.free_will import SensoryDecisionWorker, FreeWillDecision
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_household_observation_initiative import _shared, _binding, _signal, _empty_shared_memory


def _review(costless_native, *, household=True, source_kind="clock"):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    if household:
        binding = _binding(old.tenant_id)
        store = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    else:
        binding = None
        store = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id,
            installation_id="home", initiative_principal_id=old.principal_id,
            initiative_authorizer=auth.policy, context_reviews_only=True)
    if source_kind == "observation":
        signal = _signal("exact-abandon-observation")
        originals.retain(signal)
        assert store.schedule_household_observation(signal, now=NOW) == "scheduled"
    else:
        assert store.schedule_context_review(now=NOW) == "scheduled"
    return auth, old, owner, store, binding


@pytest.mark.parametrize("stage", ["memory", "changing_memory", "admission", "authority", "expired_memory"])
@pytest.mark.parametrize("household", [False, True])
def test_pre_provider_failure_is_durable_and_bounded(costless_native, stage, household):
    auth, old, owner, store, binding = _review(costless_native, household=household)
    called, clock, authorities, memory_attempts = [], [NOW], [0], [0]
    def authority():
        authorities[0] += 1
        if stage == "authority" and authorities[0] % 2 == 0:
            raise RuntimeError("synthetic authority unavailable")
        return auth.policy()
    def memory(query):
        if stage == "changing_memory":
            error_type = (ValueError, RuntimeError, LookupError)[memory_attempts[0]]
            memory_attempts[0] += 1
            raise error_type("synthetic changing context failure")
        if stage == "memory":
            raise RuntimeError("synthetic context unavailable")
        if stage == "expired_memory":
            clock[0] += timedelta(minutes=6)
        return SimpleNamespace(confirmed_facts=("shared",)) if household else _empty_shared_memory(query)
    def admission(claim, *, now):
        if stage == "admission":
            raise ValueError("synthetic admission unavailable")
        return SimpleNamespace(admitted=True)
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            called.append(True)
            raise AssertionError("provider must not be entered")
    def shared(event, now, memory_port):
        return memory_port.recall_for_presence(SimpleNamespace()), binding.to_payload()
    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=memory),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
        installation_id="home", principal_id=old.principal_id,
        context_review_authorizer=authority, context_review_admission=admission,
        household_context=shared if household else None, clock=lambda: clock[0])
    for attempt in range(3):
        assert worker.run_once(now=clock[0]) == "failed"
        states = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix)
        assert len(states) == 1
        state = states[0]
        assert state.phase == "will_preparation_failed" and state.retry_count == attempt + 1
        event = store._timeline.events(state.thread_id)[-1]
        assert event.event_type == "WILL_PREPARATION_FAILED"
        assert event.payload["data"]["provider_dispatched"] is False
        assert event.payload["data"]["model_dispatch_started"] is False
        assert event.payload["data"]["usage"] is None
    assert state.status == "needs_help" and not called
    if stage == "changing_memory":
        failures = [e.payload["data"]["failure_fingerprint"] for e in store._timeline.events(state.thread_id)
                    if e.event_type == "WILL_PREPARATION_FAILED"]
        assert len(set(failures)) == 3  # A changed error must not reset the source's retry bound.
    assert worker.run_once(now=clock[0]) == "idle"
    assert store.schedule_context_review(now=clock[0] + timedelta(hours=2)) == "scheduled"
    fresh = store.claim_next(now=clock[0] + timedelta(hours=2))
    assert fresh is not None and fresh.thread_id != state.thread_id


@pytest.mark.parametrize("failure", ["provider", "commit"])
def test_post_dispatch_failure_stays_uncertain_and_cannot_be_reclassified(costless_native, failure):
    auth, old, owner, store, binding = _review(costless_native)
    called = []
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            called.append(True)
            if failure == "provider":
                raise RuntimeError("synthetic lost response")
            return FreeWillDecision("silence", "No action", 0, "synthetic")
    if failure == "commit":
        store.complete = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic commit failure"))
    worker = SensoryDecisionWorker(store, Model(), object(),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
        installation_id="home", principal_id=old.principal_id,
        context_review_authorizer=auth.policy,
        context_review_admission=lambda *a, **kw: SimpleNamespace(admitted=True),
        household_context=lambda *a: (SimpleNamespace(confirmed_facts=("shared",)), binding.to_payload()))
    assert worker.run_once(now=NOW) == "failed" and len(called) == 1
    state = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix)[0]
    assert state.phase == "will_usage_uncertain"
    before = store._timeline.events(state.thread_id)
    from eva.application.free_will import SensoryDecisionClaim
    claim = SensoryDecisionClaim(store._event_from_thread(state.thread_id), state.thread_id,
        state.operation_id, state.revision, state.retry_count)
    with pytest.raises(CheckpointConflict):
        store.fail_before_dispatch(claim, RuntimeError("cannot undo dispatch"), now=NOW)
    assert store._timeline.events(state.thread_id) == before
    assert store.claim_next(now=NOW + timedelta(days=1)) is None
    assert store.schedule_context_review(now=NOW + timedelta(days=1)) == "idle"


@pytest.mark.parametrize("source_kind", ["clock", "observation"])
def test_household_blocker_visible_beside_newer_private_silence_and_exact_owner_abandon(costless_native, source_kind):
    auth, old, owner, shared, binding = _review(costless_native, source_kind=source_kind)
    claim = shared.mark_model_started(shared.claim_next(now=NOW), now=NOW)
    shared.fail(claim, RuntimeError("synthetic lost outcome"), now=NOW)
    state = shared._timeline.load(claim.thread_id)
    history = shared._timeline.events(claim.thread_id)
    private = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id,
        installation_id="home", initiative_principal_id=old.principal_id,
        initiative_authorizer=auth.policy, context_reviews_only=True)
    later = NOW + timedelta(hours=2)
    assert private.schedule_context_review(now=later) == "scheduled"
    own = private.claim_next(now=later)
    private.complete(own, FreeWillDecision("silence", "Quiet private context", 0, "synthetic"), now=later)
    page = owner.page(now=later)
    assert page["latest_review"]["state"] == "silence"
    assert page["review_lanes"]["private"]["unresolved_review"] is None
    blocker = page["review_lanes"]["household"]["unresolved_review"]
    assert blocker["thread_id"] == claim.thread_id and blocker["revision"] == state.revision
    assert blocker["blocker"] == "review_needs_help"
    with pytest.raises(ValueError):
        owner.abandon_uncertain_review(claim.thread_id, state.revision - 1, now=later)
    assert shared.schedule_context_review(now=later) == "idle"
    result = owner.abandon_uncertain_review(claim.thread_id, state.revision, now=later)
    assert result["usage"] == "unknown" and result["replayed"] is False
    after = shared._timeline.events(claim.thread_id)
    assert after[:-1] == history
    assert after[-1].payload["data"]["source_payload_sha256"] == history[0].payload_sha256
    assert after[-1].payload["data"]["previous_payload_sha256"] == history[-1].payload_sha256
    assert after[-1].payload["data"]["review_lane"] == "household"
    assert shared.claim_next(now=later) is None
    assert shared.schedule_context_review(now=later) == "scheduled"
    assert shared.claim_next(now=later).thread_id != claim.thread_id
    assert owner.page(now=later)["review_lanes"]["household"]["unresolved_review"] is None


@pytest.mark.parametrize("corrupt", ["original_hash", "scope", "source_identity", "unverified_owner"])
def test_household_abandon_rejects_changed_provenance_or_owner(costless_native, corrupt):
    auth, old, owner, store, binding = _review(costless_native)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.fail(claim, RuntimeError("synthetic lost outcome"), now=NOW)
    state = store._timeline.load(claim.thread_id)
    if corrupt == "unverified_owner":
        bindings = metadata.tables["eva_core.principal_bindings"]
        with owner._engine.begin() as conn:
            conn.execute(sa.update(bindings).where(bindings.c.tenant_id == owner._authority.tenant_id,
                bindings.c.principal_binding_id == owner._binding).values(status="revoked"))
        with pytest.raises(RuntimeError, match="binding is not current"):
            owner.abandon_uncertain_review(claim.thread_id, state.revision, now=NOW)
        return
    with owner._engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
        first = conn.execute(sa.select(operational_events).where(
            operational_events.c.tenant_id == store._timeline._scope_value.tenant_id,
            operational_events.c.installation_id == store._timeline._scope_value.installation_id,
            operational_events.c.thread_id == claim.thread_id,
            operational_events.c.sequence == 1)).one()
        import copy
        payload = copy.deepcopy(first.payload)
        if corrupt == "scope":
            payload["data"]["payload"]["household_binding"]["installation_id"] = "elsewhere"
        elif corrupt == "source_identity":
            payload["data"]["payload"]["household_binding"]["household_id"] = "different-family"
        digest = bytes(32) if corrupt == "original_hash" else bytes.fromhex(canonical_sha256(payload))
        conn.execute(sa.update(operational_events).where(
            operational_events.c.tenant_id == first.tenant_id,
            operational_events.c.installation_id == first.installation_id,
            operational_events.c.event_id == first.event_id).values(payload=payload, payload_sha256=digest))
        conn.exec_driver_sql("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")
    with pytest.raises(ValueError):
        owner.abandon_uncertain_review(claim.thread_id, state.revision, now=NOW)
