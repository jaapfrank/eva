from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import subprocess
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import PostgresActionChainLedger
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)

from test_action_chain_effect_authorizer import (
    _prepared_effect,
    _provider_registry,
)
from test_action_chain_effects import (
    _effect_counts,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def test_effect_call_claim_is_single_durable_idempotent_and_io_free(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    process_calls: list[tuple[object, ...]] = []

    def forbidden_process(*args: object, **kwargs: object) -> subprocess.Popen:
        process_calls.append(args)
        raise AssertionError("MC03c1b must not start a provider process")

    monkeypatch.setattr(subprocess, "Popen", forbidden_process)

    def claim(worker_offset: int):
        return PostgresActionChainEffectDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=9 + worker_offset),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(claim, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_started",
        "claimed",
    ]
    selected = next(item for item in results if item.disposition == "claimed")
    assert selected.process_spec is not None
    assert selected.connector_manifest is not None
    assert selected.initialize is not None
    assert selected.invoke is not None
    assert selected.invoke.method == "effect.execute"
    assert selected.invoke.id == prepared.intent.idempotency_key
    assert selected.invoke.params.request_id == prepared.intent.idempotency_key
    assert selected.attempt.request_sha256 == canonical_sha256(
        selected.invoke.params
    )
    assert process_calls == []

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectDispatcher(
            restarted,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
        )
        reloaded = PostgresActionChainLedger(restarted, scope).load(chain.plan_id)
    finally:
        restarted.dispose()
    assert replay.disposition == "already_started"
    assert replay.process_spec is None
    assert replay.connector_manifest is None
    assert replay.initialize is None
    assert replay.invoke is None
    assert reloaded.steps[1].state == "running"
    assert process_calls == []

    with action_chain_engine.connect() as connection:
        events = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
            )
        ).all()
    assert len(events) == 1
    assert events[0].payload["data"]["idempotency_key"] == (
        prepared.intent.idempotency_key
    )
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["attempts"] == 1
    assert counts["receipts"] == counts["readbacks"] == 0


def test_effect_call_deadline_is_rechecked_without_start_event(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    PostgresActionChainEffectAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).authorize_effect(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    deadline = datetime.strptime(
        prepared.intent.deadline, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)
    with pytest.raises(ActionChainEffectConflict, match="deadline has passed"):
        PostgresActionChainEffectDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=deadline,
        )
    with action_chain_engine.connect() as connection:
        count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
            )
        )
    assert count == 0


def test_effect_call_marker_rolls_back_before_material_is_returned(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c1b_call_event()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type = 'ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED' THEN
                    RAISE EXCEPTION 'forced_mc03c1b_call_event_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c1b_call_event
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c1b_call_event()
            """
        )
    dispatcher = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c1b"):
            dispatcher.claim_provider_call(
                UUID(prepared.intent.effect_intent_id),
                live_catalog=catalog,
                now=NOW + timedelta(seconds=9),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c1b_call_event "
                "ON eva_core.operational_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_mc03c1b_call_event()"
            )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
            )
        ) == 0
    retry = dispatcher.claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=10),
    )
    assert retry.disposition == "claimed"
    assert retry.invoke is not None
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"
