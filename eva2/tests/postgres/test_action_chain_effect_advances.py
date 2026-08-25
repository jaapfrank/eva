from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

import test_action_chain_read_dispatcher as read_dispatcher_fixtures

from eva.adapters.postgres.action_chain_effect_advances import (
    ActionChainEffectAdvanceConflict,
    PostgresActionChainEffectAdvancer,
)
from eva.adapters.postgres.action_chain_effect_readback_results import (
    PostgresActionChainEffectReadbackResultStore,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256

from test_action_chain_effect_readback_results import (
    _observed,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW, _decision_and_plan


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _ready(
    engine: Engine,
    *,
    match_status: str = "matches",
    state: object = None,
):
    scope, chain, effect, prepared, _observed_result = _observed(
        engine,
        match_status=match_status,
        state=state,
    )
    stored = PostgresActionChainEffectReadbackResultStore(engine, scope).record(
        UUID(prepared.intent.read_intent_id)
    )
    return scope, chain, effect, stored


def _decision_and_plan_with_followup():
    decision, plan, catalog = _decision_and_plan()
    decision_payload = decision.model_dump(mode="json")
    decision_payload["actions"].append(
        {
            "action_id": "step:confirm-after-effect",
            "kind": "request_read",
            "operation": "ha.state.read",
            "resource": "overkapping",
            "arguments": {"target": "overkapping"},
            "depends_on": ["step:test-effect"],
            "uncertainty_basis_points": 100,
        }
    )
    decision = decision.__class__.model_validate(decision_payload)
    plan_payload = plan.model_dump(mode="json")
    plan_payload.update(
        {
            "model_decision_sha256": canonical_sha256(decision),
            "maximum_connector_calls": 3,
        }
    )
    plan_payload["steps"].append(
        {
            "step_id": "step:confirm-after-effect",
            "kind": "read",
            "operation": "ha.state.read",
            "resource": "overkapping",
            "input_schema_sha256": "a" * 64,
            "output_schema_sha256": "b" * 64,
            "depends_on": ["step:test-effect"],
            "capability_manifest_id": catalog.plan_capability_manifest_id(
                "ha.state.read"
            ),
            "proof_floor": "fresh provider observation",
        }
    )
    plan = plan.__class__.model_validate(plan_payload)
    return decision, plan, catalog


def _use_followup_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        read_dispatcher_fixtures,
        "_decision_and_plan",
        _decision_and_plan_with_followup,
    )


def test_matching_physical_readback_verifies_effect_and_replays_after_restart(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_followup_plan(monkeypatch)
    scope, chain, effect, readback = _ready(action_chain_engine)
    effect_intent_id = UUID(effect.intent.effect_intent_id)
    advanced = PostgresActionChainEffectAdvancer(
        action_chain_engine, scope
    ).advance(effect_intent_id, now=NOW + timedelta(seconds=17))

    assert advanced.disposition == "advanced"
    assert advanced.released_step_ids == (chain.steps[2].plan_step_id,)
    assert advanced.outcome_ref == f"effect-readback:{readback.readback.readback_id}"
    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert loaded.steps[1].state == "succeeded"
    assert loaded.steps[2].state == "ready"
    effects = metadata.tables["eva_effects.effect_intents"]
    with action_chain_engine.connect() as connection:
        row = connection.execute(
            sa.select(effects).where(effects.c.effect_intent_id == effect_intent_id)
        ).one()
        assert row.state == "verified"
        assert row.revision == 4
        assert row.payload["state"] == "verified"
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(operational_events.c.event_type == "STATE_VERIFIED")
        ) == 1

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectAdvancer(restarted, scope).advance(
            effect_intent_id, now=NOW + timedelta(seconds=18)
        )
    finally:
        restarted.dispose()
    assert replay.disposition == "already_advanced"
    assert replay == advanced.__class__(
        "already_advanced",
        advanced.effect_step_id,
        advanced.released_step_ids,
        advanced.outcome_ref,
    )
    next_claim = PostgresActionChainLeases(
        action_chain_engine, scope
    ).claim_next(
        worker_id="worker:mc03c3e:followup",
        now=NOW + timedelta(seconds=19),
        lease_seconds=10,
    )
    assert next_claim is not None
    assert next_claim.plan_step_id == chain.steps[2].plan_step_id
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


@pytest.mark.parametrize(
    ("match_status", "state"),
    (
        ("not_observed", None),
        ("conflict", {"state": "on"}),
        ("inconclusive", {"state": "on"}),
        ("matches", {"state": "off"}),
    ),
)
def test_nonmatching_or_independently_wrong_state_never_advances(
    action_chain_engine: Engine,
    match_status: str,
    state: object,
) -> None:
    scope, chain, effect, _readback = _ready(
        action_chain_engine,
        match_status=match_status,
        state=state,
    )
    with pytest.raises(ActionChainEffectAdvanceConflict, match="proof is invalid"):
        PostgresActionChainEffectAdvancer(action_chain_engine, scope).advance(
            UUID(effect.intent.effect_intent_id),
            now=NOW + timedelta(seconds=17),
        )
    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert loaded.steps[1].state == "running"


def test_effect_advance_is_single_under_concurrency(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, effect, _readback = _ready(action_chain_engine)
    effect_intent_id = UUID(effect.intent.effect_intent_id)

    def advance(_: int):
        return PostgresActionChainEffectAdvancer(
            action_chain_engine, scope
        ).advance(effect_intent_id, now=NOW + timedelta(seconds=17))

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(advance, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "advanced",
        "already_advanced",
    ]
    assert results[0].outcome_ref == results[1].outcome_ref


def test_effect_advance_rolls_back_rows_and_events_together(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_followup_plan(monkeypatch)
    scope, chain, effect, _readback = _ready(action_chain_engine)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c3e_verified_event()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type = 'STATE_VERIFIED' THEN
                    RAISE EXCEPTION 'forced_mc03c3e_verified_event_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c3e_verified_event
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c3e_verified_event()
            """
        )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c3e"):
            PostgresActionChainEffectAdvancer(action_chain_engine, scope).advance(
                UUID(effect.intent.effect_intent_id),
                now=NOW + timedelta(seconds=17),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c3e_verified_event "
                "ON eva_core.operational_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_mc03c3e_verified_event()"
            )
    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert loaded.steps[1].state == "running"
    assert loaded.steps[2].state == "pending"


def test_reload_rejects_corrupt_verified_effect_outcome(
    action_chain_engine: Engine,
) -> None:
    scope, chain, effect, _readback = _ready(action_chain_engine)
    PostgresActionChainEffectAdvancer(action_chain_engine, scope).advance(
        UUID(effect.intent.effect_intent_id),
        now=NOW + timedelta(seconds=17),
    )
    steps = metadata.tables["eva_core.plan_steps"]
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(steps)
            .where(steps.c.effect_intent_id == UUID(effect.intent.effect_intent_id))
            .values(outcome_ref="effect-readback:00000000-0000-0000-0000-000000000000")
        )
    with pytest.raises(ActionChainCorrupt, match="proof timeline differs"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
