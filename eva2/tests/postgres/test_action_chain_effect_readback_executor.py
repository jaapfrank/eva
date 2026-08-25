from __future__ import annotations

from datetime import timedelta
from typing import Iterator
from uuid import UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

import eva.operations.action_chain_effect_readback_executor as executor_module
from eva.adapters.postgres.action_chain_ledger import PostgresActionChainLedger
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)
from eva.operations.action_chain_effect_readback_executor import (
    PostgresActionChainEffectReadbackExecutor,
)

from test_action_chain_effect_readbacks import (
    _prepared_authorization,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _success(invoke, manifest) -> ConnectorInvokeSuccess:
    return ConnectorInvokeSuccess.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInvokeSuccess/v1",
            "jsonrpc": "2.0",
            "id": invoke.id,
            "result": {
                "schema_version": 1,
                "contract": "ConnectorResult/v1",
                "connector_id": invoke.params.connector_id,
                "request_id": invoke.id,
                "correlation_id": invoke.params.correlation_id,
                "operation": invoke.params.operation,
                "request_sha256": canonical_sha256(invoke.params),
                "connector_manifest_sha256": canonical_sha256(manifest),
                "outcome": {
                    "kind": "readback",
                    "provider_resource_id": "ha:entity:input_boolean.eva_test",
                    "idempotency_key": invoke.params.request_payload[
                        "idempotencyKey"
                    ],
                    "request_sha256": invoke.params.request_payload["requestSha256"],
                    "match_status": "matches",
                    "payload": {
                        "representation": "inline",
                        "output_schema_sha256": (
                            manifest.operations[0].output_schema_sha256
                        ),
                        "normalized_payload": {"state": "on"},
                    },
                    "observed_at": "2026-08-20T12:00:14Z",
                },
                "completed_at": "2026-08-20T12:00:15Z",
            },
        }
    )


def _counts(engine: Engine, read_intent_id: UUID) -> dict[str, int]:
    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    evidence = metadata.tables["eva_evidence.evidence_envelopes"]
    with engine.connect() as connection:
        return {
            "attempts": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(attempts)
                    .where(attempts.c.read_intent_id == read_intent_id)
                )
            ),
            "observations": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(observations)
                    .where(observations.c.read_intent_id == read_intent_id)
                )
            ),
            "readbacks": int(
                connection.scalar(sa.select(sa.func.count()).select_from(readbacks))
            ),
            "evidence": int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(evidence)
                    .where(
                        evidence.c.aggregate_type == "action_chain_read_attempt",
                        evidence.c.aggregate_id
                        == str(uuid5(read_intent_id, "read-attempt:1")),
                    )
                )
            ),
        }


def test_effect_readback_executor_records_observation_once_without_verifying_effect(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, chain, effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    process_calls: list[dict[str, object]] = []

    def successful_run(**kwargs: object) -> ConnectorInvokeSuccess:
        process_calls.append(kwargs)
        return _success(kwargs["invoke"], kwargs["manifest"])

    monkeypatch.setattr(executor_module, "run_connector_process", successful_run)
    read_intent_id = UUID(prepared.intent.read_intent_id)
    executor = PostgresActionChainEffectReadbackExecutor(
        action_chain_engine,
        scope,
        provider_registry=registry,
        clock=lambda: NOW + timedelta(seconds=16),
    )
    executed = executor.execute(
        read_intent_id,
        now=NOW + timedelta(seconds=14),
        timeout_seconds=2,
    )
    assert executed.disposition == "executed"
    assert executed.stored_result is not None
    assert executed.stored_result.attempt.state == "observed"
    assert executed.stored_result.observation is not None
    assert executed.stored_result.observation.normalized_value == {
        "idempotencyKey": effect.intent.idempotency_key,
        "matchStatus": "matches",
        "payload": {"state": "on"},
        "requestSha256": effect.intent.frozen_payload_sha256,
    }
    assert len(process_calls) == 1
    assert _counts(action_chain_engine, read_intent_id) == {
        "attempts": 1,
        "observations": 1,
        "readbacks": 0,
        "evidence": 1,
    }
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"

    replay = executor.execute(
        read_intent_id,
        now=NOW + timedelta(seconds=17),
        timeout_seconds=2,
    )
    assert replay.disposition == "already_started"
    assert replay.stored_result is not None
    assert replay.stored_result.disposition == "already_recorded"
    assert replay.stored_result.observation == executed.stored_result.observation
    assert len(process_calls) == 1


def test_effect_readback_executor_preserves_real_protocol_failure_once(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    real_run = executor_module.run_connector_process
    process_calls: list[dict[str, object]] = []

    def counted_run(**kwargs: object):
        process_calls.append(kwargs)
        return real_run(**kwargs)

    monkeypatch.setattr(executor_module, "run_connector_process", counted_run)
    executor = PostgresActionChainEffectReadbackExecutor(
        action_chain_engine,
        scope,
        provider_registry=registry,
        clock=lambda: NOW + timedelta(seconds=16),
    )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    executed = executor.execute(
        read_intent_id,
        now=NOW + timedelta(seconds=14),
        timeout_seconds=2,
    )
    assert executed.disposition == "executed"
    assert executed.stored_result is not None
    assert executed.stored_result.attempt.state == "rejected"
    assert executed.stored_result.observation is None
    assert executed.stored_result.evidence.subject_contract == (
        "ConnectorProtocolFailure/v1"
    )
    assert len(process_calls) == 1

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectReadbackExecutor(
            restarted,
            scope,
            provider_registry=registry,
            clock=lambda: NOW + timedelta(seconds=18),
        ).execute(
            read_intent_id,
            now=NOW + timedelta(seconds=17),
            timeout_seconds=2,
        )
        reloaded = PostgresActionChainLedger(restarted, scope).load(chain.plan_id)
    finally:
        restarted.dispose()
    assert replay.disposition == "already_started"
    assert replay.stored_result is not None
    assert replay.stored_result.attempt == executed.stored_result.attempt
    assert len(process_calls) == 1
    assert reloaded.steps[1].state == "running"


def test_effect_readback_executor_never_repeats_unresolved_call_start(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, _chain, _effect, _stored, _authority, prepared, registry = (
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

    def forbidden_run(**_: object):
        raise AssertionError("an unresolved readback call-start must not be repeated")

    monkeypatch.setattr(executor_module, "run_connector_process", forbidden_run)
    replay = PostgresActionChainEffectReadbackExecutor(
        action_chain_engine,
        scope,
        provider_registry=registry,
        clock=lambda: NOW + timedelta(seconds=16),
    ).execute(
        read_intent_id,
        now=NOW + timedelta(seconds=15),
        timeout_seconds=2,
    )
    assert replay.disposition == "already_started"
    assert replay.stored_result is None
    assert _counts(action_chain_engine, read_intent_id)["observations"] == 0


def test_effect_readback_result_rolls_back_as_one_transaction(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, chain, _effect, _stored, _authority, prepared, registry = (
        _prepared_authorization(action_chain_engine)
    )
    monkeypatch.setattr(
        executor_module,
        "run_connector_process",
        lambda **kwargs: _success(kwargs["invoke"], kwargs["manifest"]),
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_effects.reject_mc03c3c_observation()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'forced_mc03c3c_observation_failure'
                    USING ERRCODE = '55000';
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c3c_observation
            BEFORE INSERT ON eva_effects.read_observations
            FOR EACH ROW EXECUTE FUNCTION eva_effects.reject_mc03c3c_observation()
            """
        )
    read_intent_id = UUID(prepared.intent.read_intent_id)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c3c"):
            PostgresActionChainEffectReadbackExecutor(
                action_chain_engine,
                scope,
                provider_registry=registry,
                clock=lambda: NOW + timedelta(seconds=16),
            ).execute(
                read_intent_id,
                now=NOW + timedelta(seconds=14),
                timeout_seconds=2,
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c3c_observation "
                "ON eva_effects.read_observations"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_effects.reject_mc03c3c_observation()"
            )
    assert _counts(action_chain_engine, read_intent_id) == {
        "attempts": 1,
        "observations": 0,
        "readbacks": 0,
        "evidence": 0,
    }
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        terminal_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.thread_id
                == f"action-chain-effect-readback:{read_intent_id}",
                operational_events.c.event_type == "ACTION_CHAIN_READ_OBSERVED",
            )
        )
    assert terminal_count == 0
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"
