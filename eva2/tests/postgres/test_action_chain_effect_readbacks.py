from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from typing import Iterator
from uuid import UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
    EffectReadbackAuthority,
    PostgresActionChainEffectReadbackPreparer,
)
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import CapabilityManifest
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)

from test_action_chain_effect_authorizer import _prepared_effect, _provider_registry
from test_action_chain_effect_dispatcher import _effect_result
from test_action_chain_effects import (
    _effect_authority,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW, POLICY_SHA256, RETENTION_UNTIL, _fact_values


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _accepted(engine: Engine):
    scope, chain, catalog, _authority, prepared = _prepared_effect(engine)
    registry = _provider_registry(engine, scope, prepared.intent)
    claimed = PostgresActionChainEffectDispatcher(
        engine, scope, provider_registry=registry
    ).claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    result = _effect_result(
        claimed,
        {
            "kind": "effect_response",
            "provider_resource_id": "ha:entity:input_boolean.eva_test",
            "provider_receipt_sha256": canonical_sha256({"accepted": True}),
            "provider_time": "2026-08-20T12:00:10Z",
        },
    )
    stored = PostgresActionChainEffectResultStore(engine, scope).record(
        claimed,
        result=result,
        observed_at=NOW + timedelta(seconds=9),
        recorded_at=NOW + timedelta(seconds=11),
    )
    assert stored.receipt is not None
    return scope, chain, prepared, stored


def _seed_readback_authority(
    engine: Engine, scope
) -> EffectReadbackAuthority:
    effect_authority = _effect_authority(scope)
    activation_id = uuid5(scope.installation_id, "mc03c3:readback:activation")
    manifest_id = uuid5(scope.installation_id, "mc03c3:readback:manifest")
    manifest = CapabilityManifest(
        schema_version=1,
        contract="CapabilityManifest/v1",
        capability_id="capability:ha-test-target-readback:test",
        connector_id="home-assistant",
        operation="effect.readback",
        input_schema_sha256="7" * 64,
        output_schema_sha256="8" * 64,
        effect_class="verification_read",
        required_provider_scopes=["home_read"],
        proof_floor="current provider state matches intended effect",
        maximum_freshness_ms=0,
        executable_sha256=hashlib.sha256(
            b"# fixture-action-chain-readback-worker-v1"
        ).hexdigest(),
        sandbox_profile_sha256="6" * 64,
    )
    policy = _fact_values("static/policy-pack")[1]
    manifests = metadata.tables["eva_core.capability_manifests"]
    activations = metadata.tables["eva_core.capability_activations"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.insert(manifests).values(
                tenant_id=scope.tenant_id,
                installation_id=scope.installation_id,
                capability_manifest_id=manifest_id,
                capability_id=manifest.capability_id,
                connector_id=manifest.connector_id,
                operation=manifest.operation,
                input_schema_sha256=bytes.fromhex(manifest.input_schema_sha256),
                output_schema_sha256=bytes.fromhex(manifest.output_schema_sha256),
                effect_class=manifest.effect_class,
                required_provider_scopes=manifest.required_provider_scopes,
                proof_floor=manifest.proof_floor,
                maximum_freshness_ms=0,
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(
                    manifest.sandbox_profile_sha256
                ),
                release_signature_sha256=bytes.fromhex("5" * 64),
                state="active",
                payload=manifest.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(manifest)),
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                created_at=NOW,
            )
        )
        connection.execute(
            sa.insert(activations).values(
                tenant_id=scope.tenant_id,
                installation_id=scope.installation_id,
                activation_id=activation_id,
                capability_manifest_id=manifest_id,
                connector_binding_id=effect_authority.connector_binding_id,
                capability_id=manifest.capability_id,
                operation=manifest.operation,
                scope=effect_authority.resource_scope.model_dump(mode="json"),
                scope_sha256=bytes.fromhex(
                    effect_authority.resource_scope.scope_sha256
                ),
                policy_id=policy["policy_id"],
                policy_pack_version=policy["policy_version"],
                policy_pack_sha256=bytes.fromhex(POLICY_SHA256),
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(
                    manifest.sandbox_profile_sha256
                ),
                state="active",
                revision=1,
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                activated_at=NOW,
                updated_at=NOW,
            )
        )
    return EffectReadbackAuthority(
        capability_activation_id=activation_id,
        connector_binding_id=effect_authority.connector_binding_id,
        resource_scope=effect_authority.resource_scope,
    )


def _counts(engine: Engine, scope) -> tuple[int, int, int]:
    reads = metadata.tables["eva_effects.read_intents"]
    readbacks = metadata.tables["eva_effects.effect_readbacks"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        intents = connection.scalar(
            sa.select(sa.func.count())
            .select_from(reads)
            .where(reads.c.purpose == "effect_verify")
        )
        physical = connection.scalar(
            sa.select(sa.func.count()).select_from(readbacks)
        )
        events = connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_READBACK_INTENT_PREPARED"
            )
        )
    return int(intents), int(physical), int(events)


def test_effect_readback_preparation_is_durable_read_only_and_replayable(
    action_chain_engine: Engine,
) -> None:
    scope, chain, effect, stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)
    preparer = PostgresActionChainEffectReadbackPreparer(
        action_chain_engine, scope
    )
    prepared = preparer.prepare(
        UUID(effect.intent.effect_intent_id),
        authority=authority,
        now=NOW + timedelta(seconds=12),
    )
    assert prepared.disposition == "prepared"
    assert prepared.intent.purpose == "effect_verify"
    assert prepared.intent.effect_intent_id == effect.intent.effect_intent_id
    assert prepared.intent.operation == "effect.readback"
    assert prepared.intent.maximum_freshness_ms == 0
    assert prepared.intent.query_sha256 == canonical_sha256(
        {
            "idempotencyKey": effect.intent.idempotency_key,
            "providerResourceId": stored.receipt.provider_resource_id,
            "requestSha256": effect.intent.frozen_payload_sha256,
        }
    )
    assert _counts(action_chain_engine, scope) == (1, 0, 1)
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectReadbackPreparer(
            restarted, scope
        ).prepare(
            UUID(effect.intent.effect_intent_id),
            authority=authority,
            now=NOW + timedelta(seconds=13),
        )
    finally:
        restarted.dispose()
    assert replay.disposition == "already_prepared"
    assert replay.intent == prepared.intent


def test_effect_readback_preparation_requires_accepted_effect(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, _authority, effect = _prepared_effect(
        action_chain_engine
    )
    authority = _seed_readback_authority(action_chain_engine, scope)
    with pytest.raises(
        ActionChainEffectReadbackConflict, match="accepted effect proof is incomplete"
    ):
        PostgresActionChainEffectReadbackPreparer(
            action_chain_engine, scope
        ).prepare(
            UUID(effect.intent.effect_intent_id),
            authority=authority,
            now=NOW + timedelta(seconds=10),
        )
    assert _counts(action_chain_engine, scope) == (0, 0, 0)


def test_effect_readback_preparation_rechecks_independent_authority(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, effect, _stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)
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
                activations.c.activation_id == authority.capability_activation_id
            )
            .values(
                state="revoked",
                revision=2,
                updated_at=NOW + timedelta(seconds=12),
            )
        )
    with pytest.raises(ActionChainEffectReadbackConflict, match="authority differs"):
        PostgresActionChainEffectReadbackPreparer(
            action_chain_engine, scope
        ).prepare(
            UUID(effect.intent.effect_intent_id),
            authority=authority,
            now=NOW + timedelta(seconds=12),
        )
    assert _counts(action_chain_engine, scope) == (0, 0, 0)


def test_effect_readback_preparation_rejects_relational_manifest_corruption(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, effect, _stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)
    activations = metadata.tables["eva_core.capability_activations"]
    manifests = metadata.tables["eva_core.capability_manifests"]
    with action_chain_engine.begin() as connection:
        manifest_id = connection.scalar(
            sa.select(activations.c.capability_manifest_id).where(
                activations.c.activation_id == authority.capability_activation_id
            )
        )
        connection.execute(
            sa.update(manifests)
            .where(manifests.c.capability_manifest_id == manifest_id)
            .values(connector_id="corrupt-provider")
        )
    with pytest.raises(ActionChainEffectReadbackConflict, match="authority differs"):
        PostgresActionChainEffectReadbackPreparer(
            action_chain_engine, scope
        ).prepare(
            UUID(effect.intent.effect_intent_id),
            authority=authority,
            now=NOW + timedelta(seconds=12),
        )
    assert _counts(action_chain_engine, scope) == (0, 0, 0)


def test_effect_readback_preparation_is_single_under_concurrency(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, effect, _stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)

    def prepare(_: int):
        return PostgresActionChainEffectReadbackPreparer(
            action_chain_engine, scope
        ).prepare(
            UUID(effect.intent.effect_intent_id),
            authority=authority,
            now=NOW + timedelta(seconds=12),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(prepare, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_prepared",
        "prepared",
    ]
    assert results[0].intent == results[1].intent
    assert _counts(action_chain_engine, scope) == (1, 0, 1)


def test_effect_readback_preparation_rolls_back_with_timeline(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, effect, _stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c3_readback_event()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type =
                    'ACTION_CHAIN_EFFECT_READBACK_INTENT_PREPARED' THEN
                    RAISE EXCEPTION 'forced_mc03c3_readback_event_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c3_readback_event
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c3_readback_event()
            """
        )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c3"):
            PostgresActionChainEffectReadbackPreparer(
                action_chain_engine, scope
            ).prepare(
                UUID(effect.intent.effect_intent_id),
                authority=authority,
                now=NOW + timedelta(seconds=12),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c3_readback_event "
                "ON eva_core.operational_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_mc03c3_readback_event()"
            )
    assert _counts(action_chain_engine, scope) == (0, 0, 0)


def test_reload_rejects_corrupt_effect_readback_intent(
    action_chain_engine: Engine,
) -> None:
    scope, chain, effect, _stored = _accepted(action_chain_engine)
    authority = _seed_readback_authority(action_chain_engine, scope)
    prepared = PostgresActionChainEffectReadbackPreparer(
        action_chain_engine, scope
    ).prepare(
        UUID(effect.intent.effect_intent_id),
        authority=authority,
        now=NOW + timedelta(seconds=12),
    )
    reads = metadata.tables["eva_effects.read_intents"]
    with action_chain_engine.begin() as connection:
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(reads)
            .where(
                reads.c.read_intent_id == UUID(prepared.intent.read_intent_id)
            )
            .values(query_sha256=bytes.fromhex("0" * 64))
        )
    with pytest.raises(ActionChainCorrupt, match="verification intent binding"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
