"""Private expression lifecycle in an isolated PostgreSQL16 fixture; mocked delivery."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import json

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, ProactiveExpressionWorker, SensoryDecisionWorker
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_household_observation_initiative import _binding, _shared


def _setup(fixture, enabled=True):
    auth, old, interests, owner, source, path, policy = fixture
    owner.stop(old.intent_id, now=NOW)
    if enabled is not None:
        policy["private_contact"] = {"enabled": enabled, "channel": "telegram",
            "owner_principal_id": old.principal_id}
        path.write_text(json.dumps(policy))
    auth.policy()
    store = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id,
        installation_id=old.installation_id, initiative_principal_id=old.principal_id,
        initiative_authorizer=auth.policy, context_reviews_only=True)
    assert store.schedule_context_review(now=NOW) == "scheduled"
    return auth, old, owner, path, policy, store


def _prepare(store):
    claim = store.claim_next(now=NOW)
    store.complete(claim, FreeWillDecision("respond", "A resident clarification is appropriate",
        1, "fixture", expression="May I ask a private question?"), now=NOW)
    return claim


def _delivery_worker(store, sent, guard=None):
    return ProactiveExpressionWorker(store,
        SimpleNamespace(deliver=lambda claim: (sent.append(claim) or "telegram:42:1")),
        channel="test", before_send=(guard if guard is not None else
            lambda claim: store.authorize_private_expression(claim, now=NOW)))


@pytest.mark.parametrize("enabled", [None, False, True])
def test_private_contact_default_denied_and_exact_opt_in(costless_native, enabled):
    auth, old, owner, path, policy, store = _setup(costless_native, enabled)
    assert store.private_contact_allowed() is (enabled is True)
    claim = _prepare(store)
    state = store._timeline.load(claim.thread_id)
    assert state.phase == ("expression_pending" if enabled is True else "will_decided")
    data = store._timeline.events(claim.thread_id)[-1].payload["data"]
    assert data["disposition"] == ("respond" if enabled is True else "silence")
    if enabled is True:
        binding = data["private_contact"]
        assert binding["owner_principal_id"] == old.principal_id
        assert binding["mandate_sha256"] == auth.policy()[1]
        assert binding["source_sha256"] == store._private_expression_source_sha256(claim.event)
    else:
        assert data["expression"] is None and "private_contact" not in data
        assert store.claim_next_expression(now=NOW) is None


def test_private_delivery_once_then_next_natural_review(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    original = _prepare(store)
    sent = []
    worker = _delivery_worker(store, sent)
    assert worker.run_once(now=NOW) == "delivered"
    assert len(sent) == 1 and sent[0].source_event_id == original.event.source_event_id
    assert worker.run_once(now=NOW + timedelta(hours=2)) == "idle"
    assert store._timeline.load(original.thread_id).phase == "expression_delivered"
    assert store.schedule_context_review(now=NOW + timedelta(hours=1)) == "scheduled"
    fresh = store.claim_next(now=NOW + timedelta(hours=1))
    assert fresh is not None and fresh.thread_id != original.thread_id


@pytest.mark.parametrize("mode", ["pending_age", "scheduler_age", "disabled", "revision_changed"])
def test_unattempted_private_expression_expires_without_replay(costless_native, mode):
    auth, old, owner, path, policy, store = _setup(costless_native)
    claim = _prepare(store)
    when = NOW
    if mode in {"pending_age", "scheduler_age"}:
        when += timedelta(minutes=16)
    else:
        before = auth.policy()[1]
        if mode == "disabled":
            policy["private_contact"]["enabled"] = False
        else:
            policy["authorization_source"] += " revised"
        path.write_text(json.dumps(policy))
        candidate = canonical_sha256({k: v for k, v in policy.items() if k != "enabled"})
        auth.admit_revision(before, candidate)
    if mode == "scheduler_age":
        assert store.schedule_context_review(now=when) == "idle"
    else:
        assert store.claim_next_expression(now=when) is None
    assert store._timeline.load(claim.thread_id).phase == "expression_expired"
    assert store.claim_next_expression(now=when + timedelta(days=1)) is None
    assert store.schedule_context_review(now=when + timedelta(hours=1)) == "scheduled"


@pytest.mark.parametrize("mode", ["mandate_revoked", "owner_revoked", "claim_expired"])
def test_live_guard_blocks_transport_and_records_known_unsent(costless_native, mode):
    auth, old, owner, path, policy, store = _setup(costless_native)
    original = _prepare(store)
    sent = []
    def guard(claim):
        if mode == "mandate_revoked":
            auth.revoke()
        elif mode == "owner_revoked":
            bindings = metadata.tables["eva_core.principal_bindings"]
            with owner._engine.begin() as conn:
                conn.execute(sa.update(bindings).where(
                    bindings.c.tenant_id == owner._authority.tenant_id,
                    bindings.c.principal_binding_id == owner._binding).values(status="revoked"))
        store.authorize_private_expression(claim,
            now=NOW + timedelta(minutes=6) if mode == "claim_expired" else NOW)
    worker = _delivery_worker(store, sent, guard)
    assert worker.run_once(now=NOW) == "failed" and sent == []
    assert store._timeline.load(original.thread_id).phase == "expression_expired"
    outcome = store._timeline.events(original.thread_id)[-1].payload["data"]
    assert outcome["provider_dispatched"] is False
    assert worker.run_once(now=NOW + timedelta(days=1)) == "idle"
    if mode == "claim_expired":
        assert store.schedule_context_review(now=NOW + timedelta(hours=2)) == "scheduled"
    else:
        assert store.schedule_context_review(now=NOW + timedelta(hours=2)) == "mandate_unavailable"


@pytest.mark.parametrize("change", ["text", "revision", "source", "operation", "urgency"])
def test_guard_rejects_changed_claim_but_preserves_original(costless_native, change):
    auth, old, owner, path, policy, store = _setup(costless_native)
    _prepare(store)
    claim = store.claim_next_expression(now=NOW)
    alterations = {"text": {"text": "Other text"}, "revision": {"revision": claim.revision - 1},
        "source": {"source_event_id": "borrowed"}, "operation": {"operation_id": "borrowed"},
        "urgency": {"urgency": 2}}
    with pytest.raises(CheckpointConflict):
        store.authorize_private_expression(replace(claim, **alterations[change]), now=NOW)
    store.authorize_private_expression(claim, now=NOW)


def test_private_contact_never_accepts_household_or_other_owner_source(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    _prepare(store)
    claim = store.claim_next_expression(now=NOW)
    household = _shared(owner._engine, old.tenant_id, _binding(old.tenant_id),
        auth.policy, old.principal_id)
    assert household.private_contact_allowed() is False
    with pytest.raises(ValueError):
        household.authorize_private_expression(claim, now=NOW)
    other = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id,
        installation_id=old.installation_id,
        initiative_principal_id="55555555-5555-5555-8555-555555555555",
        initiative_authorizer=auth.policy, context_reviews_only=True)
    assert other.private_contact_allowed() is False
    with pytest.raises(ValueError):
        other.authorize_private_expression(claim, now=NOW)
    assert other.claim_next_expression(now=NOW) is None


def test_changed_decision_source_cannot_prepare_private_expression(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    claim = store.claim_next(now=NOW)
    changed = replace(claim, event=replace(claim.event, payload={"review_window": -1}))
    with pytest.raises(ValueError):
        store.complete(changed, FreeWillDecision("respond", "Question", 1, "fixture",
            expression="Private question"), now=NOW)


def test_transport_ambiguity_blocks_replay_and_scheduler(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    original = _prepare(store)
    sent = []
    def deliver(claim):
        sent.append(claim)
        raise TimeoutError("synthetic ambiguous acceptance")
    worker = ProactiveExpressionWorker(store, SimpleNamespace(deliver=deliver), channel="test",
        before_send=lambda claim: store.authorize_private_expression(claim, now=NOW))
    assert worker.run_once(now=NOW) == "failed"
    assert store._timeline.load(original.thread_id).phase == "expression_ambiguous"
    assert worker.run_once(now=NOW + timedelta(days=1)) == "idle"
    assert store.schedule_context_review(now=NOW + timedelta(days=1)) == "idle"
    assert len(sent) == 1


@pytest.mark.parametrize("enabled", [None, False, True])
def test_decision_worker_exposes_true_only_for_current_opt_in(costless_native, enabled):
    auth, old, owner, path, policy, store = _setup(costless_native, enabled)
    kwargs_seen = []
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            kwargs_seen.append(kwargs)
            return FreeWillDecision("silence", "Private consideration", 0, "fixture")
    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=lambda q: object()),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
        installation_id=old.installation_id, principal_id=old.principal_id,
        context_review_authorizer=auth.policy,
        context_review_admission=lambda *a, **kw: SimpleNamespace(admitted=True))
    assert worker.run_once(now=NOW) == "silence"
    if enabled is True:
        assert kwargs_seen[0]["private_contact_allowed"] is True
    else:
        assert "private_contact_allowed" not in kwargs_seen[0]


def test_lost_claim_is_unknown_and_never_retried_or_age_expired(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    original = _prepare(store)
    claim = store.claim_next_expression(now=NOW)
    assert claim is not None
    assert store.claim_next_expression(now=NOW + timedelta(minutes=6)) is None
    assert store._timeline.load(original.thread_id).phase == "expression_ambiguous"
    assert store.schedule_context_review(now=NOW + timedelta(days=1)) == "idle"
    with pytest.raises(CheckpointConflict):
        store.fail_before_expression_send(claim, ValueError("cannot undo a lost process"),
            now=NOW + timedelta(minutes=6))


def test_permission_removed_during_decision_cannot_prepare_contact(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    claim = store.claim_next(now=NOW)
    before = auth.policy()[1]
    policy["private_contact"]["enabled"] = False
    path.write_text(json.dumps(policy))
    auth.admit_revision(before, canonical_sha256({k: v for k, v in policy.items() if k != "enabled"}))
    store.complete(claim, FreeWillDecision("respond", "Question was considered", 1, "fixture",
        expression="Private question"), now=NOW)
    assert store._timeline.load(claim.thread_id).phase == "will_decided"
    assert store.claim_next_expression(now=NOW) is None


@pytest.mark.parametrize("phase", ["pending", "claimed", "delivered", "ambiguous"])
def test_contact_history_is_private_bounded_and_never_implies_answer(costless_native, phase):
    auth, old, owner, path, policy, store = _setup(costless_native)
    original = _prepare(store)
    if phase != "pending":
        claim = store.claim_next_expression(now=NOW)
        if phase == "delivered":
            store.authorize_private_expression(claim, now=NOW)
            store.acknowledge_expression(claim, channel="telegram", provider_message_id="telegram:42:1", now=NOW)
        elif phase == "ambiguous":
            store.fail_expression(claim, TimeoutError("unknown send"), now=NOW)
    records = store.recent_private_contacts(now=NOW)
    if phase == "ambiguous":
        assert records == ()
    else:
        assert len(records) == 1
        assert records[0]["kind"] == "private_contact"
        assert records[0]["expression"] == "May I ask a private question?"
        assert records[0]["question"] == records[0]["expression"]
        assert records[0]["status"] == ("delivery_confirmed_no_answer_implied" if phase == "delivered"
            else "prepared_not_confirmed_delivered")
    assert store.recent_private_contacts(now=NOW + timedelta(days=8)) == ()
    with pytest.raises(ValueError):
        store.recent_private_contacts(now=NOW, limit=4)


def test_delivered_contact_reaches_later_orientation_without_assuming_answer(costless_native):
    auth, old, owner, path, policy, store = _setup(costless_native)
    _prepare(store)
    delivery = ProactiveExpressionWorker(store,
        SimpleNamespace(deliver=lambda claim: "telegram:42:1"), channel="telegram",
        before_send=lambda claim: store.authorize_private_expression(claim, now=NOW))
    assert delivery.run_once(now=NOW) == "delivered"
    later = NOW + timedelta(hours=1)
    assert store.schedule_context_review(now=later) == "scheduled"
    observed = []
    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            observed.extend(item for item in kwargs["initiative_history"] if item.get("kind") == "private_contact")
            return FreeWillDecision("silence", "No repeated question", 0, "fixture")
    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=lambda q: object()),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id=old.tenant_id,
        installation_id=old.installation_id, principal_id=old.principal_id,
        context_review_authorizer=auth.policy,
        context_review_admission=lambda *a, **kw: SimpleNamespace(admitted=True))
    assert worker.run_once(now=later) == "silence"
    assert len(observed) == 1
    assert observed[0]["status"] == "delivery_confirmed_no_answer_implied"
    assert observed[0]["expression"] == "May I ask a private question?"
