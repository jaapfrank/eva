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
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.operations.action_chain_effect_readback_authorizer import (
    PostgresActionChainEffectReadbackAuthorizer,
)
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)

from test_action_chain_effect_readbacks import (
    _authorization_counts,
    _prepared_authorization,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _call_start_count(engine: Engine, scope, read_intent_id: UUID) -> int:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.thread_id
                    == f"action-chain-effect-readback:{read_intent_id}",
                    operational_events.c.event_type
                    == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                )
            )
        )


def _physical_readback_count(engine: Engine) -> int:
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    with engine.begin() as connection:
        return int(connection.scalar(sa.select(sa.func.count()).select_from(readbacks)))


def test_effect_readback_call_claim_is_single_replayable_and_unexecuted(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    claimed = PostgresActionChainEffectReadbackDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        read_intent_id,
        now=NOW + timedelta(seconds=14),
    )
    assert claimed.disposition == "claimed"
    assert claimed.process_spec is not None
    assert claimed.connector_manifest is not None
    assert claimed.initialize is not None
    assert claimed.invoke is not None
    assert claimed.invoke.method == "effect.readback"
    assert claimed.invoke.params.operation == "effect.readback"
    assert claimed.invoke.params.request_payload_sha256 == prepared.intent.query_sha256
    assert _authorization_counts(action_chain_engine, scope, read_intent_id) == (1, 1)
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 1
    assert _physical_readback_count(action_chain_engine) == 0
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectReadbackDispatcher(
            restarted,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            read_intent_id,
            now=NOW + timedelta(seconds=15),
        )
    finally:
        restarted.dispose()
    assert replay.disposition == "already_started"
    assert replay.attempt == claimed.attempt
    assert replay.process_spec is None
    assert replay.connector_manifest is None
    assert replay.initialize is None
    assert replay.invoke is None
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 1
    assert _physical_readback_count(action_chain_engine) == 0


def test_effect_readback_call_claim_is_single_under_concurrency(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)

    def claim(_: int):
        return PostgresActionChainEffectReadbackDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            read_intent_id,
            now=NOW + timedelta(seconds=14),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(claim, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_started",
        "claimed",
    ]
    assert results[0].attempt == results[1].attempt
    assert sum(item.invoke is not None for item in results) == 1
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 1
    assert _physical_readback_count(action_chain_engine) == 0


def test_effect_readback_call_claim_rejects_expired_deadline(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    PostgresActionChainEffectReadbackAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).authorize_readback(read_intent_id, now=NOW + timedelta(seconds=13))
    with pytest.raises(ActionChainEffectReadbackConflict, match="deadline"):
        PostgresActionChainEffectReadbackDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            read_intent_id,
            now=NOW + timedelta(minutes=2),
        )
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 0
    assert _physical_readback_count(action_chain_engine) == 0


def test_effect_readback_call_claim_rechecks_revoked_authority(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, _stored, authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    PostgresActionChainEffectReadbackAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).authorize_readback(read_intent_id, now=NOW + timedelta(seconds=13))
    activations = metadata.tables["eva_core.capability_activations"]
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(activations)
            .where(
                activations.c.activation_id
                == authority.capability_activation_id
            )
            .values(
                state="revoked",
                revision=2,
                updated_at=NOW + timedelta(seconds=14),
            )
        )
    with pytest.raises(ActionChainCorrupt, match="verification authority"):
        PostgresActionChainEffectReadbackDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            read_intent_id,
            now=NOW + timedelta(seconds=15),
        )
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 0
    assert _physical_readback_count(action_chain_engine) == 0


def test_effect_readback_call_claim_rolls_back_before_releasing_material(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c3b2_call_start()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.thread_id LIKE 'action-chain-effect-readback:%%'
                   AND NEW.event_type =
                       'ACTION_CHAIN_READ_PROVIDER_CALL_STARTED' THEN
                    RAISE EXCEPTION 'forced_mc03c3b2_call_start_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c3b2_call_start
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c3b2_call_start()
            """
        )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c3b2"):
            PostgresActionChainEffectReadbackDispatcher(
                action_chain_engine,
                scope,
                provider_registry=registry,
            ).claim_provider_call(
                read_intent_id,
                now=NOW + timedelta(seconds=14),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c3b2_call_start "
                "ON eva_core.operational_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_mc03c3b2_call_start()"
            )
    assert _authorization_counts(action_chain_engine, scope, read_intent_id) == (1, 1)
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 0

    claimed = PostgresActionChainEffectReadbackDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        read_intent_id,
        now=NOW + timedelta(seconds=15),
    )
    assert claimed.disposition == "claimed"
    assert claimed.invoke is not None
    assert _call_start_count(action_chain_engine, scope, read_intent_id) == 1
    assert _physical_readback_count(action_chain_engine) == 0
