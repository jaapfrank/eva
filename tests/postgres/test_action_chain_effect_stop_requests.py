from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_effect_stop_requests import (
    ActionChainEffectStopRequestConflict,
    PostgresActionChainEffectStopRequester,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)
from eva.operations.action_chain_effect_executor import (
    PostgresActionChainEffectExecutor,
)

from test_action_chain_effect_authorizer import _prepared_effect, _provider_registry
from test_action_chain_effect_dispatcher import _effect_result
from test_action_chain_effects import action_chain_engine as _shared_action_chain_engine
from test_action_chain_ledger import NOW, _fact_values


effect_attempts = metadata.tables["eva_effects.effect_attempts"]
principal_bindings = metadata.tables["eva_core.principal_bindings"]


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _owner() -> tuple[UUID, UUID]:
    principal = _fact_values("static/principal-owner")[1]
    binding = _fact_values("static/principal-binding")[1]
    return principal["principal_id"], binding["principal_binding_id"]


def _started_effect(engine: Engine):
    scope, chain, catalog, _authority, prepared = _prepared_effect(engine)
    claimed = PostgresActionChainEffectDispatcher(
        engine,
        scope,
        provider_registry=_provider_registry(engine, scope, prepared.intent),
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    assert claimed.disposition == "claimed"
    return scope, chain, catalog, prepared, claimed


def test_stop_request_after_call_start_is_owner_bound_and_idempotent(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    effect_intent_id = UUID(prepared.intent.effect_intent_id)

    requested = requester.request_stop(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )
    assert requested.disposition == "stop_requested"
    assert requested.effect_attempt_id == UUID(claimed.attempt.effect_attempt_id)

    replay = requester.request_stop(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=11),
    )
    assert replay.disposition == "already_requested"
    assert replay.thread_id == requested.thread_id

    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        checkpoint = connection.execute(
            sa.select(operational_checkpoints).where(
                operational_checkpoints.c.thread_id == requested.thread_id
            )
        ).one()
        event = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.thread_id == requested.thread_id
            )
        ).one()
    assert checkpoint.checkpoint_id == (
        f"checkpoint:{requested.thread_id}:{canonical_sha256(event.payload['data'])}"
    )
    assert checkpoint.phase == "action_chain_effect_stop_requested"
    assert checkpoint.status == "waiting_for_proof"
    assert event.event_type == "ACTION_CHAIN_EFFECT_STOP_REQUESTED"
    assert event.sequence == 1
    assert event.payload["data"]["effect_intent_id"] == str(effect_intent_id)
    assert canonical_sha256(event.payload) == bytes(event.payload_sha256).hex()


def test_stop_request_never_changes_or_repeats_the_started_provider_call(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    requester.request_stop(
        UUID(prepared.intent.effect_intent_id),
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )

    stored = PostgresActionChainEffectResultStore(action_chain_engine, scope).record(
        claimed,
        result=_effect_result(
            claimed,
            {
                "kind": "effect_response",
                "provider_resource_id": "ha:entity:light.test_target",
                "provider_receipt_sha256": canonical_sha256({"accepted": True}),
                "provider_time": "2026-08-20T12:00:10Z",
            },
        ),
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert stored.disposition == "recorded"
    assert stored.attempt.state == "provider_accepted"

    owner_principal_id, principal_binding_id = _owner()
    terminal = requester.request_stop(
        UUID(prepared.intent.effect_intent_id),
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=12),
    )
    assert terminal.disposition == "already_requested"


def test_stop_request_and_provider_result_have_one_durable_order(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    start = Barrier(2)

    def request_stop() -> str:
        start.wait()
        return requester.request_stop(
            UUID(prepared.intent.effect_intent_id),
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=10),
        ).disposition

    def record_result() -> str:
        start.wait()
        return PostgresActionChainEffectResultStore(action_chain_engine, scope).record(
            claimed,
            result=_effect_result(
                claimed,
                {
                    "kind": "effect_response",
                    "provider_resource_id": "ha:entity:light.test_target",
                    "provider_receipt_sha256": canonical_sha256({"accepted": True}),
                    "provider_time": "2026-08-20T12:00:10Z",
                },
            ),
            observed_at=NOW + timedelta(seconds=9),
            recorded_at=NOW + timedelta(seconds=11),
        ).disposition

    with ThreadPoolExecutor(max_workers=2) as workers:
        stop_disposition, result_disposition = tuple(
            workers.map(lambda callback: callback(), (request_stop, record_result))
        )
    assert result_disposition == "recorded"
    assert stop_disposition in {"stop_requested", "already_terminal"}


def test_stop_request_never_lets_executor_repeat_a_call_started_before_it(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, _chain, catalog, prepared, _claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    PostgresActionChainEffectStopRequester(action_chain_engine, scope).request_stop(
        UUID(prepared.intent.effect_intent_id),
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )

    def forbidden_provider(**_kwargs: object) -> object:
        raise AssertionError("a stop request must not reopen a call-started effect")

    monkeypatch.setattr(
        "eva.operations.action_chain_effect_executor.run_connector_process",
        forbidden_provider,
    )
    with pytest.raises(ActionChainEffectConflict, match="authorized effect attempt differs"):
        PostgresActionChainEffectExecutor(
            action_chain_engine,
            scope,
            provider_registry=_provider_registry(
                action_chain_engine, scope, prepared.intent
            ),
        ).execute(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
            timeout_seconds=2,
        )


def test_stop_request_rejects_before_call_start(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    with pytest.raises(ActionChainEffectStopRequestConflict, match="not started"):
        requester.request_stop(
            UUID(prepared.intent.effect_intent_id),
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=9),
        )

def test_stop_request_rejects_wrong_owner(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, _claimed = _started_effect(action_chain_engine)
    _owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    with pytest.raises(
        ActionChainEffectStopRequestConflict, match="owner authority is not current"
    ):
        requester.request_stop(
            UUID(prepared.intent.effect_intent_id),
            owner_principal_id=uuid4(),
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=10),
        )


def test_stop_request_replays_after_later_binding_revocation(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, _claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    effect_intent_id = UUID(prepared.intent.effect_intent_id)
    requested = requester.request_stop(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        updated = connection.execute(
            sa.update(principal_bindings)
            .where(
                principal_bindings.c.tenant_id == scope.tenant_id,
                principal_bindings.c.principal_binding_id == principal_binding_id,
                principal_bindings.c.status == "verified",
            )
            .values(status="revoked", revision=principal_bindings.c.revision + 1)
        )
    assert updated.rowcount == 1
    replay = requester.request_stop(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=11),
    )
    assert replay.disposition == "already_requested"
    assert replay.thread_id == requested.thread_id


def test_stop_request_rejects_a_tampered_call_start_or_stored_context(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, prepared, claimed = _started_effect(action_chain_engine)
    owner_principal_id, principal_binding_id = _owner()
    requester = PostgresActionChainEffectStopRequester(action_chain_engine, scope)
    effect_intent_id = UUID(prepared.intent.effect_intent_id)
    requested = requester.request_stop(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )

    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(sa.text("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER"))
        row = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.thread_id == requested.thread_id
            )
        ).one()
        payload = dict(row.payload)
        original_payload = dict(row.payload)
        original_data = dict(original_payload["data"])
        connection.execute(sa.text("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER"))

    corruptions = {
        "binding_verification_evidence_id": str(uuid4()),
        "binding_subject_sha256": "0" * 64,
        "binding_revision": int(original_data["binding_revision"]) + 1,
        "binding_verified_at": "2026-08-20T12:00:11Z",
        "call_start_event_id": str(uuid4()),
        "call_start_payload_sha256": "0" * 64,
        "call_start_sequence": int(original_data["call_start_sequence"]) + 1,
        "dispatch_permit_sha256": "0" * 64,
        "effect_attempt_payload_sha256": "0" * 64,
        "effect_intent_payload_sha256": "0" * 64,
        "owner_principal_id": str(uuid4()),
        "plan_id": str(uuid4()),
        "plan_step_id": str(uuid4()),
        "principal_binding_id": str(uuid4()),
        "request_sha256": "0" * 64,
        "requested_at": "2026-08-20T12:00:11Z",
    }
    def replace_event_payload(payload: dict[str, object]) -> None:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            set_local_scope(
                connection,
                tenant_id=str(scope.tenant_id),
                installation_id=str(scope.installation_id),
            )
            connection.execute(
                sa.text("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
            )
            connection.execute(
                sa.update(operational_events)
                .where(operational_events.c.thread_id == requested.thread_id)
                .values(
                    payload=payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(payload)),
                )
            )
            connection.execute(
                sa.text("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")
            )

    for key, value in corruptions.items():
        payload = dict(original_payload)
        data = dict(original_data)
        data[key] = value
        payload["data"] = data
        replace_event_payload(payload)
        with pytest.raises(ActionChainEffectStopRequestConflict, match="stored stop request"):
            requester.request_stop(
                effect_intent_id,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
                now=NOW + timedelta(seconds=11),
            )
        replace_event_payload(original_payload)

    checkpoint_tampered = dict(original_payload)
    checkpoint_data = dict(checkpoint_tampered["checkpoint"])
    checkpoint_data["phase"] = "tampered"
    checkpoint_tampered["checkpoint"] = checkpoint_data
    replace_event_payload(checkpoint_tampered)
    with pytest.raises(ActionChainEffectStopRequestConflict, match="stored stop request"):
        requester.request_stop(
            effect_intent_id,
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=11),
        )
    replace_event_payload(original_payload)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        attempt = connection.execute(
            sa.select(effect_attempts).where(
                effect_attempts.c.effect_attempt_id == UUID(claimed.attempt.effect_attempt_id)
            )
        ).one()
    assert attempt.state == "dispatch_authorized"
