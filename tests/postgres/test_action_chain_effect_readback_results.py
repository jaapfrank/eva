from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Iterator
from uuid import UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from tests.support.postgres_action_chain_effect_readback_results import (
    ActionChainEffectReadbackResultConflict,
    PostgresActionChainEffectReadbackResultStore,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)
from eva.operations.action_chain_effect_readback_authorizer import (
    PostgresActionChainEffectReadbackAuthorizer,
)

from test_action_chain_effect_readback_executor import _success
from test_action_chain_effect_readbacks import (
    _prepared_authorization,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _observed(
    engine: Engine,
    *,
    match_status: str = "matches",
    state: object = None,
):
    scope, chain, effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(engine)
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    claim = PostgresActionChainEffectReadbackDispatcher(
        engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        read_intent_id,
        now=NOW + timedelta(seconds=14),
    )
    assert claim.invoke is not None
    assert claim.connector_manifest is not None
    result = _success(claim.invoke, claim.connector_manifest)
    if state is None and match_status == "matches":
        state = {"state": "on"}
    raw_result = result.model_dump(mode="json")
    raw_outcome = raw_result["result"]["outcome"]
    raw_outcome["match_status"] = match_status
    raw_outcome["payload"] = (
        None
        if state is None
        else {
            "representation": "inline",
            "output_schema_sha256": (
                claim.connector_manifest.operations[0].output_schema_sha256
            ),
            "normalized_payload": state,
        }
    )
    result = ConnectorInvokeSuccess.model_validate(raw_result)
    stored = PostgresActionChainReadResultStore(engine, scope).record(
        claim,
        result=result,
        observed_at=NOW + timedelta(seconds=14),
        recorded_at=NOW + timedelta(seconds=16),
    )
    assert stored.attempt.state == "observed"
    return scope, chain, effect, prepared, stored


def _readback_count(engine: Engine) -> int:
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    with engine.connect() as connection:
        return int(
            connection.scalar(sa.select(sa.func.count()).select_from(readbacks))
        )


def test_physical_effect_readback_is_canonical_replayable_and_does_not_advance(
    action_chain_engine: Engine,
) -> None:
    scope, chain, effect, prepared, observed = _observed(action_chain_engine)
    read_intent_id = UUID(prepared.intent.read_intent_id)
    event_count_before = _readback_event_count(
        action_chain_engine, scope, read_intent_id
    )

    recorded = PostgresActionChainEffectReadbackResultStore(
        action_chain_engine, scope
    ).record(read_intent_id)
    assert recorded.disposition == "recorded"
    assert recorded.readback.readback_id == str(
        uuid5(UUID(observed.attempt.read_attempt_id), "effect-readback:v1")
    )
    assert recorded.readback.effect_intent_id == effect.intent.effect_intent_id
    assert recorded.readback.read_intent_id == prepared.intent.read_intent_id
    assert recorded.readback.read_attempt_id == observed.attempt.read_attempt_id
    assert recorded.readback.provider_resource_id == (
        "ha:entity:input_boolean.eva_test"
    )
    assert recorded.readback.idempotency_key == effect.intent.idempotency_key
    assert recorded.readback.request_sha256 == effect.intent.frozen_payload_sha256
    assert recorded.readback.match_status == "matches"
    assert recorded.readback.state_sha256 == canonical_sha256({"state": "on"})
    assert recorded.readback.evidence_ref == observed.evidence.evidence_id
    assert _readback_count(action_chain_engine) == 1
    assert _readback_event_count(action_chain_engine, scope, read_intent_id) == (
        event_count_before
    )

    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    )
    assert loaded.steps[1].state == "running"
    effects = metadata.tables["eva_effects.effect_intents"]
    with action_chain_engine.connect() as connection:
        assert connection.scalar(
            sa.select(effects.c.state).where(
                effects.c.effect_intent_id
                == UUID(effect.intent.effect_intent_id)
            )
        ) == "provider_accepted"

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectReadbackResultStore(
            restarted, scope
        ).record(read_intent_id)
    finally:
        restarted.dispose()
    assert replay.disposition == "already_recorded"
    assert replay.readback == recorded.readback
    assert _readback_count(action_chain_engine) == 1


def test_physical_effect_readback_is_single_under_concurrency(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, prepared, _observed_result = _observed(
        action_chain_engine
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)

    def record(_: int):
        return PostgresActionChainEffectReadbackResultStore(
            action_chain_engine, scope
        ).record(read_intent_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(record, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_recorded",
        "recorded",
    ]
    assert results[0].readback == results[1].readback
    assert _readback_count(action_chain_engine) == 1


@pytest.mark.parametrize(
    ("match_status", "state"),
    (
        ("not_observed", None),
        ("conflict", {"state": "unexpected"}),
        ("inconclusive", {"state": "unknown"}),
    ),
)
def test_physical_effect_readback_preserves_nonmatching_provider_outcome(
    action_chain_engine: Engine,
    match_status: str,
    state: object,
) -> None:
    scope, chain, _effect, prepared, _observed_result = _observed(
        action_chain_engine,
        match_status=match_status,
        state=state,
    )
    stored = PostgresActionChainEffectReadbackResultStore(
        action_chain_engine, scope
    ).record(UUID(prepared.intent.read_intent_id))
    assert stored.readback.match_status == match_status
    assert stored.readback.state_sha256 == canonical_sha256(state)
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"


def test_physical_effect_readback_insert_rolls_back_cleanly(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _effect, prepared, _observed_result = _observed(
        action_chain_engine
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_effects.reject_mc03c3d_readback()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'forced_mc03c3d_readback_failure'
                    USING ERRCODE = '55000';
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c3d_readback
            BEFORE INSERT ON eva_effects.effect_readbacks
            FOR EACH ROW EXECUTE FUNCTION eva_effects.reject_mc03c3d_readback()
            """
        )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c3d"):
            PostgresActionChainEffectReadbackResultStore(
                action_chain_engine, scope
            ).record(UUID(prepared.intent.read_intent_id))
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c3d_readback "
                "ON eva_effects.effect_readbacks"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_effects.reject_mc03c3d_readback()"
            )
    assert _readback_count(action_chain_engine) == 0
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"


def test_reload_rejects_corrupt_physical_effect_readback(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _effect, prepared, _observed_result = _observed(
        action_chain_engine
    )
    stored = PostgresActionChainEffectReadbackResultStore(
        action_chain_engine, scope
    ).record(UUID(prepared.intent.read_intent_id))
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    with action_chain_engine.begin() as connection:
        connection.execute(
            sa.update(readbacks)
            .where(
                readbacks.c.readback_id == UUID(stored.readback.readback_id)
            )
            .values(state_sha256=bytes.fromhex("0" * 64))
        )
    with pytest.raises(ActionChainCorrupt, match="physical effect readback binding"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


def test_physical_effect_readback_requires_observed_provider_state(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    PostgresActionChainEffectReadbackAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).authorize_readback(
        UUID(prepared.intent.read_intent_id),
        now=NOW + timedelta(seconds=13),
    )
    with pytest.raises(
        ActionChainEffectReadbackResultConflict,
        match="no observed provider state",
    ):
        PostgresActionChainEffectReadbackResultStore(
            action_chain_engine, scope
        ).record(UUID(prepared.intent.read_intent_id))
    assert _readback_count(action_chain_engine) == 0


def _readback_event_count(engine: Engine, scope, read_intent_id: UUID) -> int:
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
                    == f"action-chain-effect-readback:{read_intent_id}"
                )
            )
        )
