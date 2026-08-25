from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import subprocess
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

import eva.operations.action_chain_read_authorizer as read_authorizer_module
import eva.operations.action_chain_read_dispatcher as read_dispatcher_module
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
)
from eva.adapters.postgres.action_chain_ledger import PostgresActionChainLedger
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from eva.adapters.postgres.action_chain_reads import (
    ActionChainReadConflict,
    PostgresActionChainReadPreparer,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import (
    ConnectorInvokeSuccess,
    classify_connector_protocol_failure,
)
from eva.operations.action_chain_read_authorizer import (
    PostgresActionChainReadAuthorizer,
)
from eva.operations.action_chain_read_dispatcher import (
    ClaimedActionChainReadCall,
    PostgresActionChainReadDispatcher,
)
from eva.operations.action_chain_read_results import (
    project_action_chain_read_result,
)

from test_action_chain_ledger import (
    NOW,
    _decision_and_plan,
    _read_authority,
    _scope_and_episode,
    _seed_model_attempt,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_read_authorizer import _provider_registry


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _prepared_read(engine: Engine):
    scope, storage_episode_id = _scope_and_episode()
    decision, plan, catalog = _decision_and_plan()
    model_attempt_id = _seed_model_attempt(engine, scope, decision)
    chain = PostgresActionChainLedger(engine, scope).admit(
        storage_episode_id=storage_episode_id,
        model_attempt_id=model_attempt_id,
        decision=decision,
        plan=plan,
        catalog=catalog,
        retention_policy_id="retention:noor-document:30d",
        retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        occurred_at=NOW,
    ).chain
    claim = PostgresActionChainLeases(engine, scope).claim_next(
        worker_id="worker:mc03b2a:prepare",
        now=NOW + timedelta(seconds=1),
        lease_seconds=10,
    )
    assert claim is not None
    prepared = PostgresActionChainReadPreparer(engine, scope).prepare_read(
        claim,
        live_catalog=catalog,
        authority=_read_authority(scope),
        now=NOW + timedelta(seconds=2),
    )
    registry = _provider_registry(engine, scope, prepared.intent)
    return scope, chain, prepared, catalog, registry


def _read_success(claimed: ClaimedActionChainReadCall) -> ConnectorInvokeSuccess:
    invoke = claimed.invoke
    manifest = claimed.connector_manifest
    assert invoke is not None
    assert manifest is not None
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
                    "kind": "read_ok",
                    "provider_resource_id": "ha:entity:light.test_target",
                    "retrieved_at": "2026-08-20T12:00:04Z",
                    "response_sha256": canonical_sha256({"state": "on"}),
                    "payload": {
                        "representation": "inline",
                        "output_schema_sha256": (
                            manifest.operations[0].output_schema_sha256
                        ),
                        "normalized_payload": {"state": "on"},
                    },
                },
                "completed_at": "2026-08-20T12:00:04Z",
            },
        }
    )


def _install_result_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_effects.reject_mc03b2b_observation()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'forced_mc03b2b_observation_failure'
                    USING ERRCODE = '55000';
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03b2b_observation
            BEFORE INSERT ON eva_effects.read_observations
            FOR EACH ROW EXECUTE FUNCTION eva_effects.reject_mc03b2b_observation()
            """
        )


def _remove_result_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc03b2b_observation "
            "ON eva_effects.read_observations"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION eva_effects.reject_mc03b2b_observation()"
        )


def test_provider_call_claim_is_single_durable_and_has_no_process_io(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, chain, prepared, catalog, registry = _prepared_read(action_chain_engine)
    process_calls: list[tuple[object, ...]] = []

    def forbidden_process(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        process_calls.append(args)
        raise AssertionError("MC03b2a must not start a provider process")

    monkeypatch.setattr(subprocess, "run", forbidden_process)

    def claim(worker_offset: int):
        dispatcher = PostgresActionChainReadDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        )
        return dispatcher.claim_provider_call(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=3 + worker_offset),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(claim, (0, 1)))

    assert sorted(item.disposition for item in results) == [
        "already_started",
        "claimed",
    ]
    selected = next(item for item in results if item.disposition == "claimed")
    assert selected.process_spec is not None
    assert selected.connector_manifest is not None
    assert selected.initialize is not None
    assert selected.invoke is not None
    assert selected.invoke.params.request_id == selected.invoke.id
    assert selected.attempt.request_sha256 == canonical_sha256(
        selected.invoke.params
    )
    assert process_calls == []

    replay_engine = sa.create_engine(action_chain_engine.url)
    try:
        replay = PostgresActionChainReadDispatcher(
            replay_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=5),
        )
    finally:
        replay_engine.dispose()
    assert replay.disposition == "already_started"
    assert replay.process_spec is None
    assert replay.connector_manifest is None
    assert replay.initialize is None
    assert replay.invoke is None
    assert process_calls == []
    assert not hasattr(
        read_authorizer_module,
        "build_action_chain_read_exchange",
    )
    assert all(
        "exchange" not in name.lower()
        for name in read_authorizer_module.__all__
    )
    assert read_dispatcher_module.__all__ == [
        "ClaimedActionChainReadCall",
        "PostgresActionChainReadDispatcher",
    ]

    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(attempts)) == 1
        assert connection.scalar(
            sa.select(sa.func.count()).select_from(observations)
        ) == 0
        call_events = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.event_type
                == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED"
            )
        ).all()
    assert len(call_events) == 1
    assert call_events[0].payload["data"]["provider_call_started_at"] in {
        "2026-08-20T12:00:03Z",
        "2026-08-20T12:00:04Z",
    }
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)


def test_deadline_is_rechecked_after_authorization_without_call_start(
    action_chain_engine: Engine,
) -> None:
    scope, _, prepared, catalog, registry = _prepared_read(action_chain_engine)
    PostgresActionChainReadAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).authorize_read(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    deadline = datetime.strptime(
        prepared.intent.deadline, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)

    with pytest.raises(ActionChainReadConflict, match="deadline has passed"):
        PostgresActionChainReadDispatcher(
            action_chain_engine,
            scope,
            provider_registry=registry,
        ).claim_provider_call(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=deadline,
        )

    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        call_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED"
            )
        )
    assert call_count == 0


def test_provider_result_projection_binds_success_failure_and_evidence(
    action_chain_engine: Engine,
) -> None:
    scope, _, prepared, catalog, registry = _prepared_read(action_chain_engine)
    claimed = PostgresActionChainReadDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    assert claimed.disposition == "claimed"
    assert claimed.initialize is not None
    assert claimed.invoke is not None
    assert claimed.connector_manifest is not None
    response_sha256 = canonical_sha256({"state": "on"})
    success = _read_success(claimed)
    projected = project_action_chain_read_result(
        tenant_id=scope.tenant_id,
        attempt=claimed.attempt,
        initialize=claimed.initialize,
        invoke=claimed.invoke,
        manifest=claimed.connector_manifest,
        result=success,
        observed_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=4),
        retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
    assert projected.attempt.state == "observed"
    assert projected.attempt.response_sha256 == canonical_sha256(success)
    assert projected.observation is not None
    assert projected.observation.normalized_value == {"state": "on"}
    assert projected.observation.response_sha256 == response_sha256
    assert projected.observation.evidence_ref == projected.evidence.evidence_id
    assert projected.evidence.subject_contract == "ConnectorInvokeSuccess/v1"
    assert projected.evidence.subject_payload_sha256 == canonical_sha256(success)

    rate_limited = success.model_dump(mode="json")
    rate_limited["result"]["outcome"] = {
        "kind": "rate_limited",
        "retry_after_ms": 1000,
        "provider_code": "test_429",
        "evidence_sha256": "1" * 64,
    }
    rate_projection = project_action_chain_read_result(
        tenant_id=scope.tenant_id,
        attempt=claimed.attempt,
        initialize=claimed.initialize,
        invoke=claimed.invoke,
        manifest=claimed.connector_manifest,
        result=ConnectorInvokeSuccess.model_validate(rate_limited),
        observed_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=4),
        retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
    assert rate_projection.attempt.state == "rate_limited"
    assert rate_projection.observation is None

    forbidden_ambiguous = success.model_dump(mode="json")
    forbidden_ambiguous["result"]["outcome"] = {
        "kind": "ambiguous_transport",
        "phase": "during_send",
        "safe_message": "read response became uncertain",
        "evidence_sha256": "4" * 64,
    }
    with pytest.raises(ValueError, match="read-only connector result"):
        project_action_chain_read_result(
            tenant_id=scope.tenant_id,
            attempt=claimed.attempt,
            initialize=claimed.initialize,
            invoke=claimed.invoke,
            manifest=claimed.connector_manifest,
            result=ConnectorInvokeSuccess.model_validate(forbidden_ambiguous),
            observed_at=NOW + timedelta(seconds=3),
            recorded_at=NOW + timedelta(seconds=4),
            retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        )

    protocol_failure = classify_connector_protocol_failure(
        request_id=claimed.invoke.id,
        request_sha256=canonical_sha256(claimed.invoke),
        phase="invoke_external_io_possible",
        violation="timeout",
        evidence_sha256="2" * 64,
        observed_at="2026-08-20T12:00:04Z",
    )
    ambiguous = project_action_chain_read_result(
        tenant_id=scope.tenant_id,
        attempt=claimed.attempt,
        initialize=claimed.initialize,
        invoke=claimed.invoke,
        manifest=claimed.connector_manifest,
        result=protocol_failure,
        observed_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=4),
        retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
    assert ambiguous.attempt.state == "ambiguous"
    assert ambiguous.attempt.response_sha256 is None
    assert ambiguous.observation is None

    wrong_invoke_payload = claimed.invoke.model_dump(mode="json")
    wrong_invoke_payload["params"]["request_payload"] = {"entity_id": "light.other"}
    wrong_invoke_payload["params"]["request_payload_sha256"] = canonical_sha256(
        wrong_invoke_payload["params"]["request_payload"]
    )
    wrong_invoke = type(claimed.invoke).model_validate(wrong_invoke_payload)
    self_consistent_wrong_failure = classify_connector_protocol_failure(
        request_id=wrong_invoke.id,
        request_sha256=canonical_sha256(wrong_invoke),
        phase="invoke_external_io_possible",
        violation="timeout",
        evidence_sha256="3" * 64,
        observed_at="2026-08-20T12:00:04Z",
    )
    with pytest.raises(ValueError, match="another read attempt"):
        project_action_chain_read_result(
            tenant_id=scope.tenant_id,
            attempt=claimed.attempt,
            initialize=claimed.initialize,
            invoke=wrong_invoke,
            manifest=claimed.connector_manifest,
            result=self_consistent_wrong_failure,
            observed_at=NOW + timedelta(seconds=3),
            recorded_at=NOW + timedelta(seconds=4),
            retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        )


def test_provider_result_is_atomic_replayable_and_reload_valid(
    action_chain_engine: Engine,
) -> None:
    scope, chain, prepared, catalog, registry = _prepared_read(action_chain_engine)
    claimed = PostgresActionChainReadDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    success = _read_success(claimed)
    store = PostgresActionChainReadResultStore(action_chain_engine, scope)
    recorded = store.record(
        claimed,
        result=success,
        observed_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=4),
    )
    assert recorded.disposition == "recorded"
    assert recorded.attempt.state == "observed"
    assert recorded.observation is not None
    assert recorded.observation.normalized_value == {"state": "on"}

    replay = store.record(
        claimed,
        result=success,
        observed_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=4),
    )
    assert replay.disposition == "already_recorded"
    assert replay == recorded.__class__(
        "already_recorded",
        recorded.attempt,
        recorded.evidence,
        recorded.observation,
    )

    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    evidence = metadata.tables["eva_evidence.evidence_envelopes"]
    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(attempts)) == 1
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(observations))
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(evidence)
                .where(evidence.c.evidence_id == UUID(recorded.evidence.evidence_id))
            )
            == 1
        )

    restart_engine = sa.create_engine(action_chain_engine.url)
    try:
        reloaded = PostgresActionChainLedger(restart_engine, scope).load(chain.plan_id)
    finally:
        restart_engine.dispose()
    assert reloaded.plan_id == chain.plan_id
    assert reloaded.steps[0].state == "running"


def test_provider_result_rolls_back_all_rows_when_observation_insert_fails(
    action_chain_engine: Engine,
) -> None:
    scope, chain, prepared, catalog, registry = _prepared_read(action_chain_engine)
    claimed = PostgresActionChainReadDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.read_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=3),
    )
    evidence_id = UUID(claimed.attempt.read_attempt_id)
    provider_evidence_id = UUID(
        project_action_chain_read_result(
            tenant_id=scope.tenant_id,
            attempt=claimed.attempt,
            initialize=claimed.initialize,
            invoke=claimed.invoke,
            manifest=claimed.connector_manifest,
            result=_read_success(claimed),
            observed_at=NOW + timedelta(seconds=3),
            recorded_at=NOW + timedelta(seconds=4),
            retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        ).evidence.evidence_id
    )
    assert evidence_id != provider_evidence_id
    _install_result_failure(action_chain_engine)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03b2b"):
            PostgresActionChainReadResultStore(action_chain_engine, scope).record(
                claimed,
                result=_read_success(claimed),
                observed_at=NOW + timedelta(seconds=3),
                recorded_at=NOW + timedelta(seconds=4),
            )
    finally:
        _remove_result_failure(action_chain_engine)

    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    evidence = metadata.tables["eva_evidence.evidence_envelopes"]
    with action_chain_engine.connect() as connection:
        attempt = connection.execute(
            sa.select(attempts).where(
                attempts.c.read_attempt_id == UUID(claimed.attempt.read_attempt_id)
            )
        ).one()
        assert attempt.state == "dispatch_authorized"
        assert int(attempt.revision) == 1
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(observations)
            .where(observations.c.read_attempt_id == attempt.read_attempt_id)
        ) == 0
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence)
            .where(evidence.c.evidence_id == provider_evidence_id)
        ) == 0
    PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
