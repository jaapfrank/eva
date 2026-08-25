from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
    attention_items,
    plan_steps,
)
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from eva.adapters.postgres.action_chain_read_advances import (
    PostgresActionChainReadAdvancer,
)
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
)
from eva.adapters.postgres.action_chain_reads import (
    ActionChainReadConflict,
    PostgresActionChainReadPreparer,
)
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import (
    ConnectorInvokeSuccess,
    classify_connector_protocol_failure,
)
from eva.operations.action_chain_read_dispatcher import (
    PostgresActionChainReadDispatcher,
)

from test_action_chain_ledger import (
    NOW,
    _decision_and_plan,
    _read_authority,
    _scope_and_episode,
    _seed_model_attempt,
)
from test_action_chain_read_authorizer import _provider_registry
from test_action_chain_read_dispatcher import (
    _prepared_read,
    _read_success,
    action_chain_engine as _shared_action_chain_engine,
)


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _observed_read(engine: Engine):
    scope, chain, prepared, catalog, registry = _prepared_read(engine)
    claimed = PostgresActionChainReadDispatcher(
        engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    stored = PostgresActionChainReadResultStore(engine, scope).record(
        claimed,
        result=_read_success(claimed),
        observed_at=NOW + timedelta(seconds=4),
        recorded_at=NOW + timedelta(seconds=5),
    )
    assert stored.attempt.state == "observed"
    assert stored.observation is not None
    return scope, chain, prepared, catalog


def _terminal_read(engine: Engine, state: str):
    scope, chain, prepared, catalog, registry = _prepared_read(engine)
    claimed = PostgresActionChainReadDispatcher(
        engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    if state == "rate_limited":
        payload = _read_success(claimed).model_dump(mode="json")
        payload["result"]["outcome"] = {
            "kind": "rate_limited",
            "retry_after_ms": 1000,
            "provider_code": "fixture_429",
            "evidence_sha256": "1" * 64,
        }
        result = ConnectorInvokeSuccess.model_validate(payload)
    else:
        assert claimed.invoke is not None
        result = classify_connector_protocol_failure(
            request_id=claimed.invoke.id,
            request_sha256=canonical_sha256(claimed.invoke),
            phase=(
                "invoke_external_io_possible"
                if state == "ambiguous"
                else "invoke_before_external_io"
            ),
            violation="timeout",
            evidence_sha256="2" * 64,
            observed_at="2026-08-20T12:00:04Z",
        )
    stored = PostgresActionChainReadResultStore(engine, scope).record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=4),
        recorded_at=NOW + timedelta(seconds=5),
    )
    assert stored.attempt.state == state
    assert stored.observation is None
    return scope, chain, prepared, catalog


def _scoped_rows(engine: Engine, scope, plan_id: UUID):
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        steps = connection.execute(
            sa.select(plan_steps)
            .where(plan_steps.c.plan_id == plan_id)
            .order_by(plan_steps.c.logical_step_key)
        ).all()
        attention = connection.execute(
            sa.select(attention_items).where(
                attention_items.c.episode_id == steps[0].episode_id,
                attention_items.c.kind == "continuation",
            )
        ).one()
        advances = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.event_type
                == "ACTION_CHAIN_READ_DEPENDENCIES_RELEASED"
            )
        ).all()
    return steps, attention, advances


def _install_attention_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03b3_attention()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'forced_mc03b3_attention_failure'
                    USING ERRCODE = '55000';
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03b3_attention
            BEFORE UPDATE ON eva_core.attention_items
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03b3_attention()
            """
        )


def _remove_attention_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc03b3_attention ON eva_core.attention_items"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION eva_core.reject_mc03b3_attention()"
        )


def test_observed_read_atomically_succeeds_releases_and_replays(
    action_chain_engine: Engine,
) -> None:
    scope, chain, prepared, _ = _observed_read(action_chain_engine)
    advancer = PostgresActionChainReadAdvancer(action_chain_engine, scope)
    advanced = advancer.advance(
        UUID(prepared.intent.read_intent_id),
        now=NOW + timedelta(seconds=6),
    )

    assert advanced.disposition == "advanced"
    assert advanced.read_step_id == chain.steps[0].plan_step_id
    assert advanced.released_step_ids == (chain.steps[1].plan_step_id,)
    assert advanced.outcome_ref.startswith("read-observation:")
    steps, attention, events = _scoped_rows(
        action_chain_engine, scope, chain.plan_id
    )
    by_key = {row.logical_step_key: row for row in steps}
    assert by_key["step:state"].state == "succeeded"
    assert int(by_key["step:state"].revision) == 3
    assert by_key["step:state"].outcome_ref == advanced.outcome_ref
    assert by_key["step:test-effect"].state == "ready"
    assert int(by_key["step:test-effect"].revision) == 2
    assert attention.state == "pending"
    assert attention.lease_owner is None
    assert len(events) == 1

    replay = advancer.advance(
        UUID(prepared.intent.read_intent_id),
        now=NOW + timedelta(seconds=7),
    )
    assert replay == advanced.__class__(
        "already_advanced",
        advanced.read_step_id,
        advanced.released_step_ids,
        advanced.outcome_ref,
    )
    replay_steps, replay_attention, replay_events = _scoped_rows(
        action_chain_engine, scope, chain.plan_id
    )
    assert [(row.state, row.revision) for row in replay_steps] == [
        (row.state, row.revision) for row in steps
    ]
    assert replay_attention.revision == attention.revision
    assert len(replay_events) == 1

    restart_engine = sa.create_engine(action_chain_engine.url)
    try:
        reloaded = PostgresActionChainLedger(restart_engine, scope).load(chain.plan_id)
    finally:
        restart_engine.dispose()
    assert [step.state for step in reloaded.steps] == ["succeeded", "ready"]

    next_claim = PostgresActionChainLeases(action_chain_engine, scope).claim_next(
        worker_id="worker:mc03b3:next",
        now=NOW + timedelta(seconds=8),
        lease_seconds=10,
    )
    assert next_claim is not None
    assert next_claim.plan_step_id == chain.steps[1].plan_step_id
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


def test_concurrent_read_advance_has_one_transition(
    action_chain_engine: Engine,
) -> None:
    scope, chain, prepared, _ = _observed_read(action_chain_engine)

    def advance(_: int):
        return PostgresActionChainReadAdvancer(
            action_chain_engine,
            scope,
        ).advance(
            UUID(prepared.intent.read_intent_id),
            now=NOW + timedelta(seconds=6),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(advance, (0, 1)))

    assert sorted(item.disposition for item in results) == [
        "advanced",
        "already_advanced",
    ]
    assert results[0].released_step_ids == results[1].released_step_ids
    _, _, events = _scoped_rows(action_chain_engine, scope, chain.plan_id)
    assert len(events) == 1


def test_released_dependent_read_uses_next_cas_revision(
    action_chain_engine: Engine,
) -> None:
    scope, storage_episode_id = _scope_and_episode()
    decision, plan, catalog = _decision_and_plan()
    decision_payload = decision.model_dump(mode="json")
    decision_payload["actions"][1] = {
        "action_id": "step:second-read",
        "kind": "request_read",
        "operation": "ha.state.read",
        "resource": "overkapping",
        "arguments": {"target": "overkapping"},
        "depends_on": ["step:state"],
        "uncertainty_basis_points": 100,
    }
    decision_payload["decision"] = "request_read"
    decision = type(decision).model_validate(decision_payload)
    plan_payload = plan.model_dump(mode="json")
    plan_payload["model_decision_sha256"] = canonical_sha256(decision)
    plan_payload["steps"][1] = {
        "step_id": "step:second-read",
        "kind": "read",
        "operation": "ha.state.read",
        "resource": "overkapping",
        "input_schema_sha256": plan.steps[0].input_schema_sha256,
        "output_schema_sha256": plan.steps[0].output_schema_sha256,
        "depends_on": ["step:state"],
        "capability_manifest_id": plan.steps[0].capability_manifest_id,
        "proof_floor": plan.steps[0].proof_floor,
    }
    plan = type(plan).model_validate(plan_payload)
    model_attempt_id = _seed_model_attempt(action_chain_engine, scope, decision)
    chain = PostgresActionChainLedger(action_chain_engine, scope).admit(
        storage_episode_id=storage_episode_id,
        model_attempt_id=model_attempt_id,
        decision=decision,
        plan=plan,
        catalog=catalog,
        retention_policy_id="retention:noor-document:30d",
        retention_until=NOW + timedelta(days=30),
        occurred_at=NOW,
    ).chain
    leases = PostgresActionChainLeases(action_chain_engine, scope)
    first_claim = leases.claim_next(
        worker_id="worker:mc03b3:first-read",
        now=NOW + timedelta(seconds=1),
        lease_seconds=10,
    )
    assert first_claim is not None
    first = PostgresActionChainReadPreparer(
        action_chain_engine, scope
    ).prepare_read(
        first_claim,
        live_catalog=catalog,
        authority=_read_authority(scope),
        now=NOW + timedelta(seconds=2),
    )
    registry = _provider_registry(action_chain_engine, scope, first.intent)
    dispatched = PostgresActionChainReadDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(first.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    PostgresActionChainReadResultStore(action_chain_engine, scope).record(
        dispatched,
        result=_read_success(dispatched),
        observed_at=NOW + timedelta(seconds=4),
        recorded_at=NOW + timedelta(seconds=5),
    )
    PostgresActionChainReadAdvancer(action_chain_engine, scope).advance(
        UUID(first.intent.read_intent_id),
        now=NOW + timedelta(seconds=6),
    )
    second_claim = leases.claim_next(
        worker_id="worker:mc03b3:second-read",
        now=NOW + timedelta(seconds=7),
        lease_seconds=10,
    )
    assert second_claim is not None
    assert second_claim.logical_step_key == "step:second-read"
    second = PostgresActionChainReadPreparer(
        action_chain_engine, scope
    ).prepare_read(
        second_claim,
        live_catalog=catalog,
        authority=_read_authority(scope),
        now=NOW + timedelta(seconds=8),
    )
    assert second.disposition == "prepared"

    reloaded = PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    )
    assert [step.state for step in reloaded.steps] == ["succeeded", "running"]
    rows, _, _ = _scoped_rows(action_chain_engine, scope, chain.plan_id)
    second_row = next(
        row for row in rows if row.logical_step_key == "step:second-read"
    )
    assert int(second_row.revision) == 3


@pytest.mark.parametrize("terminal_state", ["rate_limited", "ambiguous", "rejected"])
def test_non_observed_read_cannot_release_dependency(
    action_chain_engine: Engine,
    terminal_state: str,
) -> None:
    scope, chain, prepared, _ = _terminal_read(
        action_chain_engine,
        terminal_state,
    )
    with pytest.raises(
        ActionChainReadConflict,
        match="proof payload is absent or invalid",
    ):
        PostgresActionChainReadAdvancer(action_chain_engine, scope).advance(
            UUID(prepared.intent.read_intent_id),
            now=NOW + timedelta(seconds=6),
        )

    steps, attention, events = _scoped_rows(
        action_chain_engine, scope, chain.plan_id
    )
    by_key = {row.logical_step_key: row for row in steps}
    assert by_key["step:state"].state == "running"
    assert by_key["step:state"].outcome_ref is None
    assert by_key["step:test-effect"].state == "pending"
    assert attention.state == "waiting"
    assert events == []
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


def test_read_advance_rolls_back_steps_when_attention_write_fails(
    action_chain_engine: Engine,
) -> None:
    scope, chain, prepared, _ = _observed_read(action_chain_engine)
    _install_attention_failure(action_chain_engine)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03b3"):
            PostgresActionChainReadAdvancer(action_chain_engine, scope).advance(
                UUID(prepared.intent.read_intent_id),
                now=NOW + timedelta(seconds=6),
            )
    finally:
        _remove_attention_failure(action_chain_engine)

    steps, attention, events = _scoped_rows(
        action_chain_engine, scope, chain.plan_id
    )
    by_key = {row.logical_step_key: row for row in steps}
    assert by_key["step:state"].state == "running"
    assert int(by_key["step:state"].revision) == 2
    assert by_key["step:test-effect"].state == "pending"
    assert int(by_key["step:test-effect"].revision) == 1
    assert attention.state == "waiting"
    assert events == []
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


def test_reload_rejects_unreceipted_dependency_progress(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _, _ = _observed_read(action_chain_engine)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        dependent_id = chain.steps[1].plan_step_id
        result = connection.execute(
            sa.update(plan_steps)
            .where(plan_steps.c.plan_step_id == dependent_id)
            .values(state="ready", revision=2, updated_at=NOW + timedelta(seconds=6))
        )
        assert result.rowcount == 1

    with pytest.raises(
        ActionChainCorrupt,
        match="dependent step progress lacks one read advance",
    ):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
