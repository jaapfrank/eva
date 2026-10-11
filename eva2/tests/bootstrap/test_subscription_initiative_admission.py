"""Scoped subscription admission; model-free and no credential reads."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionClaim, SensoryDecisionWorker
from eva.application.sensory import SensoryEvent
from eva.bootstrap.idle_capacity import idle_capacity_available
from eva.bootstrap.native_initiative_work import subscription_initiative_available
from tests.model.test_conversation_model import _runtime_budget


@pytest.mark.parametrize("endpoint,scope,budget_present,expected", [
    ("https://chatgpt.com/backend-api/codex/responses", "current", True, True),
    ("https://api.openai.com/v1/responses", "current", True, False),
    ("https://chatgpt.com.example.invalid/backend-api/codex/responses", "current", True, False),
    ("https://chatgpt.com/backend-api/codex/responses?billing=api", "current", True, False),
    ("https://chatgpt.com/backend-api/codex/responses", "other_tenant", True, False),
    ("https://chatgpt.com/backend-api/codex/responses", "other_installation", True, False),
    ("https://chatgpt.com/backend-api/codex/responses", "current", False, False),
])
def test_subscription_identity_is_exact_scoped_route_without_auth_or_dispatch(tmp_path, endpoint, scope, budget_present, expected):
    budget = _runtime_budget()
    model = PydanticConversationModel(auth_file=tmp_path / "does-not-exist.json", endpoint=endpoint,
        resource_budget=budget if budget_present else None)
    assert model.existing_subscription_autonomy(
        tenant_id="other" if scope == "other_tenant" else budget.tenant_id,
        installation_id="other" if scope == "other_installation" else budget.installation_id) is expected
    assert not (tmp_path / "does-not-exist.json").exists()


@pytest.mark.parametrize("subscription,revoked", [(True, False), (False, False), (True, True)])
def test_capacity_requires_live_owner_authority_and_subscription(subscription, revoked):
    checked = []
    def policy():
        checked.append(True)
        if revoked:
            raise RuntimeError("durable revocation")
        return {}, "a" * 64
    authority = SimpleNamespace(owner=SimpleNamespace(_tenant_id="tenant", _installation_id="home"), policy=policy)
    def known_route(**scope):
        assert scope == {"tenant_id": "tenant", "installation_id": "home"}
        return subscription
    if revoked:
        with pytest.raises(RuntimeError, match="revocation"):
            subscription_initiative_available(authority, SimpleNamespace(existing_subscription_autonomy=known_route))
    else:
        assert subscription_initiative_available(authority, SimpleNamespace(existing_subscription_autonomy=known_route)) is subscription
    assert len(checked) == int(subscription)


@pytest.mark.parametrize("initiative,provider_block,active,compute,expected", [
    (True, None, False, True, True),
    (False, None, False, True, False),
    (True, "unverified_model_cost", False, True, False),
    (True, None, True, True, False),
    (True, None, False, False, False),
    ("error", None, False, True, False),
])
def test_exhausted_memory_quota_does_not_block_authorized_idle_initiative_but_work_and_compute_do(
        monkeypatch, initiative, provider_block, active, compute, expected):
    monkeypatch.setattr("eva.bootstrap.idle_capacity.spare_compute_available", lambda: compute)
    def capacity():
        if initiative == "error":
            raise RuntimeError("owner expired")
        return initiative
    assert idle_capacity_available(
        model=SimpleNamespace(autonomous_admission_blocker=lambda: provider_block),
        reflection_budget=SimpleNamespace(has_installation_capacity=lambda **_: False),
        delegated_work_store=SimpleNamespace(has_pending_or_running_work=lambda: active),
        initiative_capacity=capacity) is expected


def test_private_subscription_callback_cannot_admit_household_review():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    event = SensoryEvent("household-initiative", "household:source", "private_opportunity_review", now, {})
    claim = SensoryDecisionClaim(event, "sensory:household:source", "household:source", 1, 0)
    decisions, reservations = [], []
    class Store:
        def claim_next(self, **kwargs): return claim
        def mark_model_started(self, actual, **kwargs): return actual
        def complete(self, actual, decision, **kwargs): decisions.append(decision)
        def fail(self, actual, error, **kwargs): raise error
    def reserve(**kwargs):
        reservations.append(kwargs)
        return SimpleNamespace(admitted=False)
    def native_callback(*args, **kwargs):
        raise AssertionError("private route used for household")
    worker = SensoryDecisionWorker(Store(), object(), object(),
        IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
        tenant_id="tenant", installation_id="home", principal_id="owner",
        context_review_authorizer=lambda: None, context_review_admission=native_callback,
        context_review_budget=SimpleNamespace(admit_background_reflection=reserve),
        household_context=lambda *_: (_ for _ in ()).throw(AssertionError("no recall")))
    assert worker.run_once(now=now) == "waiting_for_budget"
    assert len(reservations) == len(decisions) == 1
    assert decisions[0].provider == "eva:background-budget"
