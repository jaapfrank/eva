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
from eva.operations.action_chain_read_authorizer import (
    PostgresActionChainReadAuthorizer,
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
