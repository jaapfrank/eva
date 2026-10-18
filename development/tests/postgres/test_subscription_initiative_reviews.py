"""Real isolated PostgreSQL proof; synthetic decisions and no external model calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.adapters.postgres.runtime_resource_usage import PostgresDailyReflectionBudget
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker
from eva.bootstrap.native_initiative_work import admit_initiative_review
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_runtime_resource_usage import _budget


@pytest.fixture
def review_setup(costless_native):
    auth, old, interests, control, source, path, policy = costless_native
    control.stop(old.intent_id, now=NOW)
    auth.policy()
    budget = PostgresDailyReflectionBudget(control._engine, tenant_id=old.tenant_id,
        installation_id=old.installation_id,
        resource_budget=replace(_budget(), tenant_id=old.tenant_id, installation_id=old.installation_id))
    # Both legacy memory quotas exhausted; source JSON/hash/tallies must remain unchanged.
    for principal in (old.principal_id,) * 4 + ("other:synthetic",) * 2:
        assert budget.admit_background_reflection(tenant_id=old.tenant_id,
            installation_id=old.installation_id, principal_id=principal, now=NOW).admitted
    def reviews():
        return PostgresSensoryEventStore(control._engine, tenant_id=old.tenant_id,
            installation_id=old.installation_id, initiative_principal_id=old.principal_id,
            initiative_authorizer=auth.policy, context_reviews_only=True)
    store = reviews()
    assert store.schedule_context_review(now=NOW) == "scheduled"
    before = budget._timeline.load("runtime-reflections:" + NOW.date().isoformat())
    hashes = tuple(event.payload_sha256 for event in budget._timeline.events(before.thread_id))
    return auth, old, control, budget, store, reviews, before, hashes


def _assert_old_ledger_unchanged(setup):
    auth, old, control, budget, store, reviews, before, hashes = setup
    assert budget._timeline.load(before.thread_id) == before
    assert tuple(event.payload_sha256 for event in budget._timeline.events(before.thread_id)) == hashes
    assert budget.read_usage_counts(principal_id=old.principal_id, now=NOW) == (4, 6)
    assert budget.has_installation_capacity(now=NOW) is False


@pytest.mark.parametrize("mode", ["success", "lost_response", "revoked_during_recall", "owner_expired", "expired_claim", "provider_blocked"])
def test_native_review_uses_claim_once_preserves_memory_ledger_and_revalidates_authority(review_setup, mode):
    auth, old, control, budget, store, reviews, before, hashes = review_setup
    called, recalled = [], []
    clock = [NOW]
    class Model:
        def existing_subscription_autonomy(self, **scope):
            assert scope == {"tenant_id": old.tenant_id, "installation_id": old.installation_id}
            return True
        def evaluate_sensory_event(self, event, identity, memory, attention=None, **kwargs):
            called.append(event)
            if mode == "lost_response": raise RuntimeError("response lost after dispatch")
            return FreeWillDecision("silence", "Nothing new needed", 0, "synthetic-subscription",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=100, output_tokens=20))
    class Memory:
        def recall_for_presence(self, query):
            recalled.append(query)
            assert query.audience.kind == "private_person" and query.principal_id == old.principal_id
            if mode == "revoked_during_recall": auth.revoke()
            if mode == "expired_claim": clock[0] += timedelta(minutes=6)
            if mode == "owner_expired":
                bindings = metadata.tables["eva_core.principal_bindings"]
                with control._engine.begin() as conn:
                    conn.execute(sa.update(bindings).where(bindings.c.tenant_id == control._authority.tenant_id,
                        bindings.c.principal_binding_id == control._binding).values(status="revoked"))
            return object()
    model = Model()
    def worker():
        return SensoryDecisionWorker(reviews(), model, Memory(),
            IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
            installation_id=old.installation_id, principal_id=old.principal_id,
            context_review_budget=budget, context_review_authorizer=auth.policy,
            context_review_admission=lambda claim, now: admit_initiative_review(auth, model, budget, claim, now=now),
            admission_blocker=lambda: "unverified_model_cost" if mode == "provider_blocked" else None,
            clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker().run_once(now=NOW), range(2)))
    assert len(called) == int(mode in {"success", "lost_response"})
    preparation_failure = mode in {"revoked_during_recall", "owner_expired", "expired_claim"}
    if preparation_failure:
        # These host-known failures precede provider entry. Retrying only the
        # same preparation is safe, bounded, and must never spend a model call.
        assert 1 <= len(recalled) <= 3
        for _ in range(3):
            result = worker().run_once(now=clock[0] + timedelta(seconds=1))
            if result == "idle":
                break
            assert result == "failed"
        states = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix, limit=4)
        events = store._timeline.events(states[0].thread_id)
        assert states[0].phase == "will_preparation_failed"
        assert states[0].status == "needs_help" and states[0].retry_count == 3
        assert sum(e.event_type == "WILL_PREPARATION_FAILED" for e in events) == 3
        assert events[-1].payload["data"]["provider_dispatched"] is False
        assert events[-1].payload["data"]["usage"] is None
        assert len(called) == 0
        assert worker().run_once(now=clock[0] + timedelta(seconds=1)) == "idle"
        assert reviews().schedule_context_review(now=clock[0] + timedelta(days=1)) == (
            "scheduled" if mode == "expired_claim" else "mandate_unavailable")
    else:
        assert len(recalled) == int(mode != "provider_blocked")
        assert worker().run_once(now=clock[0] + timedelta(seconds=1)) in {"idle", "waiting_for_admission"}
        assert len(called) == int(mode in {"success", "lost_response"})
        states = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix, limit=4)
        events = store._timeline.events(states[0].thread_id)
        assert sum(e.event_type == "WILL_MODEL_STARTED" for e in events) == int(mode != "provider_blocked")
        if mode == "success":
            assert states[0].phase == "will_decided"
            assert events[-1].payload["data"]["usage"]["model_attempts"] == 1
        elif mode != "provider_blocked":
            assert states[0].phase == "will_usage_uncertain"
            assert reviews().schedule_context_review(now=clock[0] + timedelta(days=1)) in {"idle", "mandate_unavailable"}
    _assert_old_ledger_unchanged(review_setup)


@pytest.mark.parametrize("variant", ["different_owner", "source_changed", "stale_revision", "expired_claim", "non_subscription"])
def test_review_admission_rejects_borrowed_or_stale_sources_and_non_subscription_keeps_old_budget(review_setup, variant):
    auth, old, control, budget, store, reviews, before, hashes = review_setup
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    model = SimpleNamespace(existing_subscription_autonomy=lambda **_: variant != "non_subscription")
    when = NOW
    if variant == "different_owner":
        claim = replace(claim, thread_id=claim.thread_id.replace(store._decision_prefix, "sensory:initiative-review:" + "a" * 24 + ":"))
    if variant == "source_changed":
        claim = replace(claim, event=replace(claim.event, payload={"review_window": -1}))
    if variant == "stale_revision": claim = replace(claim, revision=claim.revision - 1)
    if variant == "expired_claim": when += timedelta(minutes=6)
    if variant == "non_subscription":
        admitted = admit_initiative_review(auth, model, budget, claim, now=when)
        assert not admitted.admitted and admitted.reason == "person_daily_limit_reached"
        assert budget.read_usage_counts(principal_id=old.principal_id, now=NOW) == (4, 6)
    else:
        with pytest.raises(InitiativeMandateDenied):
            admit_initiative_review(auth, model, budget, claim, now=when)
        _assert_old_ledger_unchanged(review_setup)


def test_subscription_read_keeps_legacy_budget_source_pin(review_setup):
    from eva.adapters.postgres.operational_timeline import OperationalTimelineError
    auth, old, control, budget, store, reviews, before, hashes = review_setup
    original = budget._source_budget_sha256
    budget._source_budget_sha256 = "f" * 64
    with pytest.raises(OperationalTimelineError, match="malformed"):
        budget.read_usage_counts(principal_id=old.principal_id, now=NOW)
    budget._source_budget_sha256 = original
    _assert_old_ledger_unchanged(review_setup)
