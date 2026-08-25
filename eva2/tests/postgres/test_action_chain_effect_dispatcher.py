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
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import (
    ConnectorInvokeSuccess,
    classify_connector_protocol_failure,
)
from eva.contracts.execution import EffectIntent
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)
from eva.operations.action_chain_effect_results import (
    project_action_chain_effect_result,
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


def _effect_result(claimed, outcome: dict[str, object]) -> ConnectorInvokeSuccess:
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
                "outcome": outcome,
                "completed_at": "2026-08-20T12:00:10Z",
            },
        }
    )


def test_effect_result_projection_separates_ack_reject_and_ambiguity(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    claimed = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    assert claimed.initialize is not None
    assert claimed.invoke is not None
    assert claimed.connector_manifest is not None
    intent = EffectIntent.model_validate(
        {**prepared.intent.model_dump(mode="json"), "state": "dispatch_authorized"}
    )
    common = {
        "tenant_id": scope.tenant_id,
        "intent": intent,
        "attempt": claimed.attempt,
        "initialize": claimed.initialize,
        "invoke": claimed.invoke,
        "manifest": claimed.connector_manifest,
        "observed_at": NOW + timedelta(seconds=9),
        "recorded_at": NOW + timedelta(seconds=11),
        "retention_until": datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    }

    accepted = project_action_chain_effect_result(
        **common,
        result=_effect_result(
            claimed,
            {
                "kind": "effect_response",
                "provider_resource_id": "ha:entity:light.test_target",
                "provider_receipt_sha256": canonical_sha256({"accepted": True}),
                "provider_time": "2026-08-20T12:00:10Z",
            },
        ),
    )
    assert accepted.attempt.state == "provider_accepted"
    assert accepted.intent.state == "provider_accepted"
    assert accepted.receipt is not None
    assert accepted.receipt.decision == "accepted"
    assert accepted.receipt.provider_resource_id == "ha:entity:light.test_target"

    rejected = project_action_chain_effect_result(
        **common,
        result=_effect_result(
            claimed,
            {
                "kind": "definitive_reject",
                "provider_code": "target_unavailable",
                "safe_message": "Target unavailable",
                "evidence_sha256": canonical_sha256({"rejected": True}),
            },
        ),
    )
    assert rejected.attempt.state == "definitive_reject"
    assert rejected.intent.state == "provider_rejected"
    assert rejected.receipt is not None
    assert rejected.receipt.decision == "rejected"
    assert rejected.receipt.provider_resource_id is None

    ambiguous_result = classify_connector_protocol_failure(
        request_id=claimed.invoke.id,
        request_sha256=canonical_sha256(claimed.invoke),
        phase="invoke_external_io_possible",
        violation="timeout",
        evidence_sha256=canonical_sha256({"timeout": True}),
        observed_at="2026-08-20T12:00:09Z",
    )
    ambiguous = project_action_chain_effect_result(
        **common,
        result=ambiguous_result,
    )
    assert ambiguous.attempt.state == "ambiguous_transport"
    assert ambiguous.attempt.response_sha256 is None
    assert ambiguous.intent.state == "ambiguous"
    assert ambiguous.receipt is None


def test_effect_result_store_commits_provider_ack_without_readback(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    claimed = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    result = _effect_result(
        claimed,
        {
            "kind": "effect_response",
            "provider_resource_id": "ha:entity:light.test_target",
            "provider_receipt_sha256": canonical_sha256({"accepted": True}),
            "provider_time": "2026-08-20T12:00:10Z",
        },
    )
    store = PostgresActionChainEffectResultStore(action_chain_engine, scope)
    stored = store.record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert stored.disposition == "recorded"
    assert stored.attempt.state == "provider_accepted"
    assert stored.intent.state == "provider_accepted"
    assert stored.receipt is not None
    assert stored.receipt.decision == "accepted"
    exact_replay = store.record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert exact_replay.disposition == "already_recorded"
    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        reloaded = PostgresActionChainEffectResultStore(
            restarted, scope
        ).load_terminal(UUID(prepared.intent.effect_intent_id))
    finally:
        restarted.dispose()
    assert reloaded is not None
    assert reloaded.disposition == "already_recorded"
    assert reloaded.attempt == stored.attempt
    assert reloaded.intent == stored.intent
    assert reloaded.receipt == stored.receipt

    intents = sa.table(
        "effect_intents",
        sa.column("state"),
        schema="eva_effects",
    )
    attempts = sa.table(
        "effect_attempts",
        sa.column("state"),
        sa.column("transport_evidence_id"),
        schema="eva_effects",
    )
    evidence = sa.table(
        "evidence_envelopes",
        sa.column("aggregate_type"),
        schema="eva_evidence",
    )
    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(intents.c.state)) == "provider_accepted"
        attempt_row = connection.execute(sa.select(attempts)).one()
        assert attempt_row.state == "provider_accepted"
        assert attempt_row.transport_evidence_id is not None
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(evidence)
            .where(
                evidence.c.aggregate_type == "action_chain_effect_attempt"
            )
        ) == 1
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["receipts"] == 1
    assert counts["readbacks"] == 0
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"


def test_effect_result_store_rolls_back_all_provider_evidence(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    claimed = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    result = _effect_result(
        claimed,
        {
            "kind": "effect_response",
            "provider_resource_id": "ha:entity:light.test_target",
            "provider_receipt_sha256": canonical_sha256({"accepted": True}),
            "provider_time": "2026-08-20T12:00:10Z",
        },
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_effects.reject_mc03c2_receipt()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'forced_mc03c2_receipt_failure'
                    USING ERRCODE = '55000';
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c2_receipt
            BEFORE INSERT ON eva_effects.effect_receipts
            FOR EACH ROW EXECUTE FUNCTION eva_effects.reject_mc03c2_receipt()
            """
        )
    store = PostgresActionChainEffectResultStore(action_chain_engine, scope)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c2"):
            store.record(
                claimed,
                result=result,
                observed_at=NOW + timedelta(seconds=9),
                recorded_at=NOW + timedelta(seconds=11),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c2_receipt "
                "ON eva_effects.effect_receipts"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_effects.reject_mc03c2_receipt()"
            )
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["receipts"] == counts["readbacks"] == 0
    retry = store.record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert retry.disposition == "recorded"


def test_effect_result_reload_rejects_corrupt_provider_evidence(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    claimed = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=registry,
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    result = _effect_result(
        claimed,
        {
            "kind": "effect_response",
            "provider_resource_id": "ha:entity:light.test_target",
            "provider_receipt_sha256": canonical_sha256({"accepted": True}),
            "provider_time": "2026-08-20T12:00:10Z",
        },
    )
    stored = PostgresActionChainEffectResultStore(
        action_chain_engine, scope
    ).record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    evidence = sa.table(
        "evidence_envelopes",
        sa.column("evidence_id"),
        sa.column("payload_sha256"),
        schema="eva_evidence",
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        updated = connection.execute(
            sa.update(evidence)
            .where(evidence.c.evidence_id == UUID(stored.evidence.evidence_id))
            .values(payload_sha256=bytes.fromhex("0" * 64))
        )
        assert updated.rowcount == 1
    with pytest.raises(ActionChainCorrupt, match="evidence differs"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
