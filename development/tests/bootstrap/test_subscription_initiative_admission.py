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


@pytest.mark.parametrize("worker_name,source", [
    ("decision_worker", "home-assistant"),
    ("context_decision_worker", "initiative-review"),
])
def test_background_runtime_reads_canonical_owner_with_guarded_live_aliases(
        monkeypatch, worker_name, source):
    """Execute the factory assignment without starting its DB, threads or providers."""
    import ast
    from dataclasses import replace
    from pathlib import Path
    from uuid import UUID

    from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver
    from eva.application.memory import MemoryAudience
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

    owner = UUID("33333333-3333-4333-8333-333333333333")
    binding = UUID("44444444-4444-4444-8444-444444444444")
    alias = "telegram:12345"
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(
        12345, str(owner), ("12345",), str(binding)),))
    live = [True]
    resolver = PostgresPrivateOwnerResolver(object(), tenant_id="tenant",
        installation_id="home", owner=owner, binding=binding,
        authority_scope=object(), bindings_loader=lambda: bindings)
    monkeypatch.setattr(resolver, "_current_binding", lambda: live[0])
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    event = SensoryEvent(source, source + ":fixture", "private_opportunity_review"
        if source == "initiative-review" else "state_changed", now, {})
    claim = SensoryDecisionClaim(event, "sensory:" + source, source + ":fixture", 1, 0)
    queries, decisions = [], []
    context = object()

    class Store:
        def claim_next(self, **kwargs): return claim
        def mark_model_started(self, actual, **kwargs): return actual
        def context_review_claim_is_current(self, actual, **kwargs): return True
        def complete(self, actual, decision, **kwargs): decisions.append(decision)
        def fail(self, actual, error, **kwargs): raise error

    class Memory:
        def recall_for_presence(self, query):
            queries.append(query)
            assert resolver.aliases(query) == (str(owner), alias)
            return context

    class Model:
        def autonomous_admission_blocker(self): return None
        def evaluate_sensory_event(self, actual, identity, memory, attention=None, **kwargs):
            assert memory is context
            return FreeWillDecision("silence", "Fixture", 0, "fixture")

    store = Store()
    authority = SimpleNamespace(policy=lambda: None)
    namespace = dict(
        SensoryDecisionWorker=SensoryDecisionWorker,
        settings=SimpleNamespace(background_tenant_id="tenant", background_installation_id="home",
            background_principal_id=str(owner), telegram_principal_bindings_file="fixture",
            dashboard_principal_binding_id=str(binding)),
        sensory_store=store, context_review_store=store, model=Model(), memory=Memory(),
        identity=IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
        provider_bundle=None, reflection_budget=object(),
        runtime=SimpleNamespace(_initiative_authority=lambda: authority),
        admit_initiative_review=lambda *args, **kwargs: SimpleNamespace(admitted=True),
        load_principal_bindings=lambda _: bindings, UUID=UUID, datetime=datetime, UTC=UTC,
    )
    # Compile the actual factory expression, not a second hand-written composition.
    runtime_path = Path(__file__).resolve().parents[3] / "src/eva/bootstrap/runtime.py"
    tree = ast.parse(runtime_path.read_text())
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == worker_name for target in node.targets)]
    assert len(matches) == 1
    exec(compile(ast.Module(body=matches, type_ignores=[]), str(runtime_path), "exec"), namespace)
    worker = namespace[worker_name]
    assert worker.run_once(now=now) == "silence"
    assert len(queries) == len(decisions) == 1
    query = queries[0]
    assert query.principal_id == str(owner) and query.subject_ids == (str(owner),)
    assert query.conversation_id == "sensory:" + source
    assert query.maximum_facts == 8
    # Alias access still needs its exact private conversation and live authority.
    alias_query = replace(query, principal_id=alias,
        audience=MemoryAudience.private_person(principal_id=alias), subject_ids=(alias,))
    assert resolver.aliases(alias_query) == ()
    assert resolver.aliases(replace(alias_query, conversation_id=alias)) == (str(owner), alias)
    assert resolver.aliases(replace(query, tenant_id="another-tenant")) == ()
    assert resolver.aliases(replace(query, installation_id="another-home")) == ()
    live[0] = False
    assert resolver.aliases(query) == ()
    assert resolver.aliases(replace(alias_query, conversation_id=alias)) == ()
