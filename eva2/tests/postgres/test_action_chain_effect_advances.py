from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

import eva.operations.action_chain_effect_executor as effect_executor_module
import test_action_chain_read_dispatcher as read_dispatcher_fixtures

from tests.support.postgres_action_chain_effect_advances import (
    ActionChainEffectAdvanceConflict,
    PostgresActionChainEffectAdvancer,
)
from eva.adapters.postgres.action_chain_effect_readback_validation import (
    effect_readback_thread_id,
)
from eva.adapters.postgres.action_chain_effect_stop_requests import (
    PostgresActionChainEffectStopRequester,
)
from tests.support.postgres_action_chain_effect_readback_results import (
    PostgresActionChainEffectReadbackResultStore,
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    PostgresActionChainEffectReadbackPreparer,
)
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.operations.action_chain_effect_executor import (
    PostgresActionChainEffectExecutor,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)

from test_action_chain_effect_readback_executor import _success
from test_action_chain_effect_readback_results import (
    _observed,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_effect_authorizer import (
    _prepared_effect,
    _provider_registry as _effect_provider_registry,
)
from test_action_chain_effect_dispatcher import _effect_result
from test_action_chain_effect_readbacks import (
    READBACK_WORKER_PROGRAM,
    _seed_readback_authority,
)
from test_action_chain_ledger import NOW, _decision_and_plan, _fact_values
from test_action_chain_read_authorizer import (
    _provider_registry as _read_provider_registry,
)


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


def _completion_counts(
    engine: Engine,
    *,
    scope,
    read_intent_id: UUID,
) -> dict[str, int]:
    attempts = metadata.tables["eva_effects.effect_attempts"]
    receipts = metadata.tables["eva_effects.effect_receipts"]
    read_attempts = metadata.tables["eva_effects.read_attempts"]
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        return {
            "effect_attempts": int(
                connection.scalar(sa.select(sa.func.count()).select_from(attempts))
            ),
            "effect_receipts": int(
                connection.scalar(sa.select(sa.func.count()).select_from(receipts))
            ),
            "effect_call_starts": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(operational_events)
                    .where(
                        operational_events.c.event_type
                        == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
                    )
                )
            ),
            "readback_attempts": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(read_attempts)
                    .where(read_attempts.c.read_intent_id == read_intent_id)
                )
            ),
            "physical_readbacks": int(
                connection.scalar(sa.select(sa.func.count()).select_from(readbacks))
            ),
            "readback_call_starts": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(operational_events)
                    .where(
                        operational_events.c.thread_id
                        == effect_readback_thread_id(read_intent_id),
                        operational_events.c.event_type
                        == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                    )
                )
            ),
            "state_verified": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(operational_events)
                    .where(operational_events.c.event_type == "STATE_VERIFIED")
                )
            ),
            "dependency_releases": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(operational_events)
                    .where(
                        operational_events.c.event_type
                        == "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED"
                    )
                )
            ),
        }


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


def test_owner_stop_after_call_retains_readback_but_never_releases_followup(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late stop keeps the facts but prevents a new autonomous action."""

    _use_followup_plan(monkeypatch)
    scope, chain, catalog, _authority, prepared_effect = _prepared_effect(
        action_chain_engine
    )
    effect_intent_id = UUID(prepared_effect.intent.effect_intent_id)
    claimed = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=_effect_provider_registry(
            action_chain_engine, scope, prepared_effect.intent
        ),
    ).claim_provider_call(
        effect_intent_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    assert claimed.disposition == "claimed"
    principal = _fact_values("static/principal-owner")[1]
    binding = _fact_values("static/principal-binding")[1]
    stopped = PostgresActionChainEffectStopRequester(
        action_chain_engine, scope
    ).request_stop(
        effect_intent_id,
        owner_principal_id=principal["principal_id"],
        principal_binding_id=binding["principal_binding_id"],
        now=NOW + timedelta(seconds=10),
    )
    assert stopped.disposition == "stop_requested"
    accepted = PostgresActionChainEffectResultStore(
        action_chain_engine, scope
    ).record(
        claimed,
        result=_effect_result(
            claimed,
            {
                "kind": "effect_response",
                "provider_resource_id": "ha:entity:input_boolean.eva_test",
                "provider_receipt_sha256": canonical_sha256({"accepted": True}),
                "provider_time": "2026-08-20T12:00:10Z",
            },
        ),
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert accepted.attempt.state == "provider_accepted"
    authority = _seed_readback_authority(action_chain_engine, scope)
    prepared_readback = PostgresActionChainEffectReadbackPreparer(
        action_chain_engine, scope
    ).prepare(
        effect_intent_id,
        authority=authority,
        now=NOW + timedelta(seconds=12),
    )
    read_intent_id = UUID(prepared_readback.intent.read_intent_id)
    readback_claim = PostgresActionChainEffectReadbackDispatcher(
        action_chain_engine,
        scope,
        provider_registry=_read_provider_registry(
            action_chain_engine,
            scope,
            prepared_readback.intent,
            capability_activation_id=authority.capability_activation_id,
            worker_program=READBACK_WORKER_PROGRAM,
        ),
    ).claim_provider_call(read_intent_id, now=NOW + timedelta(seconds=14))
    assert readback_claim.disposition == "claimed"
    assert readback_claim.invoke is not None
    assert readback_claim.connector_manifest is not None
    observed = PostgresActionChainReadResultStore(action_chain_engine, scope).record(
        readback_claim,
        result=_success(readback_claim.invoke, readback_claim.connector_manifest),
        observed_at=NOW + timedelta(seconds=14),
        recorded_at=NOW + timedelta(seconds=16),
    )
    assert observed.attempt.state == "observed"
    readback = PostgresActionChainEffectReadbackResultStore(
        action_chain_engine, scope
    ).record(read_intent_id)
    assert readback.disposition == "recorded"

    with pytest.raises(
        ActionChainEffectAdvanceConflict,
        match="owner stopped the chain after provider call start",
    ):
        PostgresActionChainEffectAdvancer(action_chain_engine, scope).advance(
            effect_intent_id,
            now=NOW + timedelta(seconds=17),
        )

    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert [step.state for step in loaded.steps] == ["succeeded", "running", "pending"]
    assert (
        PostgresActionChainLeases(action_chain_engine, scope).claim_next(
            worker_id="worker:must-not-run-after-owner-stop",
            now=NOW + timedelta(seconds=18),
            lease_seconds=10,
        )
        is None
    )
    assert _completion_counts(
        action_chain_engine,
        scope=scope,
        read_intent_id=read_intent_id,
    )["dependency_releases"] == 0


def test_committed_effect_recovers_across_every_readback_process_boundary(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_followup_plan(monkeypatch)
    scope, chain, catalog, _authority, prepared_effect = _prepared_effect(
        action_chain_engine
    )
    effect_intent_id = UUID(prepared_effect.intent.effect_intent_id)
    effect_registry = _effect_provider_registry(
        action_chain_engine,
        scope,
        prepared_effect.intent,
    )
    process_calls: list[dict[str, object]] = []

    def accepted_run(**kwargs: object) -> ConnectorInvokeSuccess:
        process_calls.append(kwargs)
        return _effect_result(
            SimpleNamespace(
                invoke=kwargs["invoke"],
                connector_manifest=kwargs["manifest"],
            ),
            {
                "kind": "effect_response",
                "provider_resource_id": "ha:entity:input_boolean.eva_test",
                "provider_receipt_sha256": canonical_sha256({"accepted": True}),
                "provider_time": "2026-08-20T12:00:10Z",
            },
        )

    monkeypatch.setattr(
        effect_executor_module,
        "run_connector_process",
        accepted_run,
    )
    executed = PostgresActionChainEffectExecutor(
        action_chain_engine,
        scope,
        provider_registry=effect_registry,
        clock=lambda: NOW + timedelta(seconds=11),
    ).execute(
        effect_intent_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
        timeout_seconds=2,
    )
    assert executed.disposition == "executed"
    assert executed.stored_result is not None
    assert executed.stored_result.intent.state == "provider_accepted"
    assert len(process_calls) == 1

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        effect_replay = PostgresActionChainEffectExecutor(
            restarted,
            scope,
            provider_registry=effect_registry,
            clock=lambda: NOW + timedelta(seconds=12),
        ).execute(
            effect_intent_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=12),
            timeout_seconds=2,
        )
    finally:
        restarted.dispose()
    assert effect_replay.disposition == "already_started"
    assert effect_replay.stored_result is not None
    assert effect_replay.stored_result.disposition == "already_recorded"
    assert len(process_calls) == 1

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        authority = _seed_readback_authority(restarted, scope)
        prepared = PostgresActionChainEffectReadbackPreparer(
            restarted, scope
        ).prepare(
            effect_intent_id,
            authority=authority,
            now=NOW + timedelta(seconds=12),
        )
    finally:
        restarted.dispose()
    read_intent_id = UUID(prepared.intent.read_intent_id)

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        registry = _read_provider_registry(
            restarted,
            scope,
            prepared.intent,
            capability_activation_id=authority.capability_activation_id,
            worker_program=READBACK_WORKER_PROGRAM,
        )
        claim = PostgresActionChainEffectReadbackDispatcher(
            restarted,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            read_intent_id,
            now=NOW + timedelta(seconds=14),
        )
        assert claim.disposition == "claimed"
        assert claim.invoke is not None
        assert claim.connector_manifest is not None
    finally:
        restarted.dispose()

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        stored = PostgresActionChainReadResultStore(restarted, scope).record(
            claim,
            result=_success(claim.invoke, claim.connector_manifest),
            observed_at=NOW + timedelta(seconds=14),
            recorded_at=NOW + timedelta(seconds=16),
        )
        assert stored.attempt.state == "observed"
    finally:
        restarted.dispose()

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        readback = PostgresActionChainEffectReadbackResultStore(
            restarted, scope
        ).record(read_intent_id)
        assert readback.disposition == "recorded"
    finally:
        restarted.dispose()

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        advanced = PostgresActionChainEffectAdvancer(restarted, scope).advance(
            effect_intent_id,
            now=NOW + timedelta(seconds=17),
        )
        assert advanced.disposition == "advanced"
        assert advanced.released_step_ids == (chain.steps[2].plan_step_id,)
        loaded = PostgresActionChainLedger(restarted, scope).load(chain.plan_id)
        assert [step.state for step in loaded.steps] == [
            "succeeded",
            "succeeded",
            "ready",
        ]
    finally:
        restarted.dispose()

    expected_counts = {
        "effect_attempts": 1,
        "effect_receipts": 1,
        "effect_call_starts": 1,
        "readback_attempts": 1,
        "physical_readbacks": 1,
        "readback_call_starts": 1,
        "state_verified": 1,
        "dependency_releases": 1,
    }
    assert _completion_counts(
        action_chain_engine,
        scope=scope,
        read_intent_id=read_intent_id,
    ) == expected_counts

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        readback_replay = PostgresActionChainEffectReadbackResultStore(
            restarted, scope
        ).record(read_intent_id)
        advance_replay = PostgresActionChainEffectAdvancer(
            restarted, scope
        ).advance(
            effect_intent_id,
            now=NOW + timedelta(seconds=18),
        )
    finally:
        restarted.dispose()
    assert readback_replay.disposition == "already_recorded"
    assert advance_replay.disposition == "already_advanced"
    assert _completion_counts(
        action_chain_engine,
        scope=scope,
        read_intent_id=read_intent_id,
    ) == expected_counts


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
