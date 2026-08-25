from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
from typing import Iterator
from uuid import UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effects import (
    ActionChainEffectConflict,
    EffectStepAuthority,
    PostgresActionChainEffectPreparer,
)
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
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import CapabilityManifest, ConnectorBinding, ResourceScope

from test_action_chain_ledger import (
    NOW,
    POLICY_SHA256,
    RETENTION_UNTIL,
    _fact_values,
)
from test_action_chain_read_advances import (
    _observed_read,
    action_chain_engine as _shared_action_chain_engine,
)


effect_intents = metadata.tables["eva_effects.effect_intents"]


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _effect_authority(scope) -> EffectStepAuthority:
    resource_scope = ResourceScope(
        resource_type="home-assistant-entity",
        resource_id="input_boolean.eva_test",
        scope_sha256=canonical_sha256(
            {
                "resource_type": "home-assistant-entity",
                "resource_id": "input_boolean.eva_test",
            }
        ),
    )
    return EffectStepAuthority(
        capability_activation_id=uuid5(scope.installation_id, "mc03c0:activation"),
        connector_binding_id=uuid5(scope.installation_id, "mc03c0:binding"),
        resource_scope=resource_scope,
    )


def _seed_effect_authority(engine: Engine, scope) -> EffectStepAuthority:
    authority = _effect_authority(scope)
    principal = _fact_values("static/principal-owner")[1]
    evidence = _fact_values("evidence/evidence:authenticated-http:001")[1]
    policy = _fact_values("static/policy-pack")[1]
    binding = ConnectorBinding(
        schema_version=1,
        contract="ConnectorBinding/v1",
        binding_id=str(authority.connector_binding_id),
        installation_id=str(scope.installation_id),
        tenant_id=str(scope.tenant_id),
        owner_principal_id=str(principal["principal_id"]),
        provider_id="home-assistant",
        provider_account_id="fixture-home-assistant-effect",
        provider_resource_id="fixture-home",
        confirmed_operations=["ha.test_target.set", "effect.readback"],
        confirmed_scopes=[
            authority.resource_scope.resource_type,
            "home_effect",
            "home_read",
        ],
        credential_reference="secret-descriptor:mc03c0:home-assistant",
        verification_evidence_ref=str(evidence["evidence_id"]),
        verified_at="2026-08-20T12:00:00Z",
        status="verified",
    )
    manifest_id = uuid5(scope.installation_id, "mc03c0:manifest")
    manifest = CapabilityManifest(
        schema_version=1,
        contract="CapabilityManifest/v1",
        capability_id="capability:ha-test-target-set:test",
        connector_id=binding.provider_id,
        operation="ha.test_target.set",
        input_schema_sha256="e" * 64,
        output_schema_sha256="f" * 64,
        effect_class="effect",
        required_provider_scopes=["home_effect"],
        proof_floor="provider receipt plus independent readback",
        maximum_freshness_ms=None,
        executable_sha256=hashlib.sha256(
            b"# fixture-action-chain-effect-worker-v1"
        ).hexdigest(),
        sandbox_profile_sha256="6" * 64,
    )
    bindings = metadata.tables["eva_core.connector_bindings"]
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
            sa.insert(bindings).values(
                tenant_id=scope.tenant_id,
                installation_id=scope.installation_id,
                binding_id=authority.connector_binding_id,
                owner_principal_id=principal["principal_id"],
                provider_id=binding.provider_id,
                provider_account_id=binding.provider_account_id,
                provider_resource_id=binding.provider_resource_id,
                confirmed_operations=binding.confirmed_operations,
                confirmed_scopes=binding.confirmed_scopes,
                credential_reference=binding.credential_reference,
                verification_evidence_id=evidence["evidence_id"],
                verified_at=NOW,
                status="verified",
                payload=binding.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(binding)),
                revision=1,
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                created_at=NOW,
                updated_at=NOW,
            )
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
                maximum_freshness_ms=None,
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
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
                activation_id=authority.capability_activation_id,
                capability_manifest_id=manifest_id,
                connector_binding_id=authority.connector_binding_id,
                capability_id=manifest.capability_id,
                operation=manifest.operation,
                scope=authority.resource_scope.model_dump(mode="json"),
                scope_sha256=bytes.fromhex(authority.resource_scope.scope_sha256),
                policy_id=policy["policy_id"],
                policy_pack_version=policy["policy_version"],
                policy_pack_sha256=bytes.fromhex(POLICY_SHA256),
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
                state="active",
                revision=1,
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                activated_at=NOW,
                updated_at=NOW,
            )
        )
    return authority


def _claimed_effect(engine: Engine):
    scope, chain, prepared_read, catalog = _observed_read(engine)
    PostgresActionChainReadAdvancer(engine, scope).advance(
        UUID(prepared_read.intent.read_intent_id),
        now=NOW + timedelta(seconds=6),
    )
    authority = _seed_effect_authority(engine, scope)
    claim = PostgresActionChainLeases(engine, scope).claim_next(
        worker_id="worker:mc03c0:effect",
        now=NOW + timedelta(seconds=7),
        lease_seconds=20,
    )
    assert claim is not None
    assert claim.logical_step_key == "step:test-effect"
    return scope, chain, catalog, authority, claim


def _effect_counts(engine: Engine, scope, plan_id: UUID) -> dict[str, int]:
    tables = {
        "intents": effect_intents,
        "attempts": metadata.tables["eva_effects.effect_attempts"],
        "receipts": metadata.tables["eva_effects.effect_receipts"],
        "readbacks": metadata.tables["eva_effects.effect_readbacks"],
    }
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        counts = {
            name: int(connection.scalar(sa.select(sa.func.count()).select_from(table)))
            for name, table in tables.items()
        }
        counts["tool_prepared"] = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(operational_events.c.event_type == "TOOL_PREPARED")
            )
        )
        counts["prepared_events"] = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.event_type
                    == "ACTION_CHAIN_EFFECT_INTENT_PREPARED"
                )
            )
        )
        step = connection.execute(
            sa.select(plan_steps).where(
                plan_steps.c.plan_id == plan_id,
                plan_steps.c.logical_step_key == "step:test-effect",
            )
        ).one()
        attention = connection.execute(
            sa.select(attention_items).where(
                attention_items.c.episode_id == step.episode_id,
                attention_items.c.kind == "continuation",
            )
        ).one()
    counts["step_revision"] = int(step.revision)
    counts["step_running"] = int(step.state == "running")
    counts["attention_waiting"] = int(attention.state == "waiting")
    return counts


def test_effect_preparation_is_durable_replayable_and_external_io_free(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)
    preparer = PostgresActionChainEffectPreparer(action_chain_engine, scope)
    prepared = preparer.prepare_effect(
        claim,
        live_catalog=catalog,
        authority=authority,
        now=NOW + timedelta(seconds=8),
    )
    assert prepared.disposition == "prepared"
    assert prepared.intent.state == "prepared"
    assert prepared.intent.frozen_payload_sha256 == canonical_sha256(
        {"state": "on", "target": "input_boolean.eva_test"}
    )
    assert _effect_counts(action_chain_engine, scope, chain.plan_id) == {
        "intents": 1,
        "attempts": 0,
        "receipts": 0,
        "readbacks": 0,
        "tool_prepared": 0,
        "prepared_events": 2,
        "step_revision": 3,
        "step_running": 1,
        "attention_waiting": 1,
    }
    replay = preparer.prepare_effect(
        claim,
        live_catalog=catalog,
        authority=authority,
        now=NOW + timedelta(seconds=9),
    )
    assert replay.disposition == "already_prepared"
    assert replay.intent == prepared.intent

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        loaded = PostgresActionChainLedger(restarted, scope).load(chain.plan_id)
    finally:
        restarted.dispose()
    assert [step.state for step in loaded.steps] == ["succeeded", "running"]
    assert loaded.steps[1].read_intent_id is None


def test_concurrent_effect_preparation_inserts_once(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)

    def prepare(_: int):
        return PostgresActionChainEffectPreparer(
            action_chain_engine, scope
        ).prepare_effect(
            claim,
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=8),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(prepare, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_prepared",
        "prepared",
    ]
    assert results[0].intent == results[1].intent
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["intents"] == 1
    assert counts["prepared_events"] == 2


def test_effect_preparation_rejects_wrong_or_expired_lease(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)
    preparer = PostgresActionChainEffectPreparer(action_chain_engine, scope)
    with pytest.raises(ActionChainEffectConflict, match="stale or expired"):
        preparer.prepare_effect(
            claim,
            live_catalog=catalog,
            authority=authority,
            now=claim.lease_expires_at,
        )
    prepared = preparer.prepare_effect(
        claim,
        live_catalog=catalog,
        authority=authority,
        now=NOW + timedelta(seconds=8),
    )
    with pytest.raises(ActionChainEffectConflict, match="another lease"):
        preparer.prepare_effect(
            replace(claim, worker_id="worker:mc03c0:wrong"),
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=9),
        )
    assert prepared.intent.state == "prepared"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["intents"] == 1


def test_effect_preparation_revalidates_live_authority(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)
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
            .where(activations.c.activation_id == authority.capability_activation_id)
            .values(state="revoked", revision=2, updated_at=NOW + timedelta(seconds=8))
        )
    with pytest.raises(ActionChainEffectConflict, match="authority differs"):
        PostgresActionChainEffectPreparer(action_chain_engine, scope).prepare_effect(
            claim,
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=8),
        )
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["intents"] == 0
    assert counts["step_running"] == 0


def test_effect_preparation_rolls_back_when_step_cas_fails(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c0_running_step()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.state = 'running' AND NEW.effect_intent_id IS NOT NULL THEN
                    RAISE EXCEPTION 'forced_mc03c0_step_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c0_running_step
            BEFORE UPDATE ON eva_core.plan_steps
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c0_running_step()
            """
        )
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c0"):
            PostgresActionChainEffectPreparer(
                action_chain_engine, scope
            ).prepare_effect(
                claim,
                live_catalog=catalog,
                authority=authority,
                now=NOW + timedelta(seconds=8),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_mc03c0_running_step ON eva_core.plan_steps"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_mc03c0_running_step()"
            )
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["intents"] == 0
    assert counts["prepared_events"] == 0
    assert counts["step_running"] == 0


def test_reload_rejects_corrupted_prepared_effect(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, claim = _claimed_effect(action_chain_engine)
    prepared = PostgresActionChainEffectPreparer(
        action_chain_engine, scope
    ).prepare_effect(
        claim,
        live_catalog=catalog,
        authority=authority,
        now=NOW + timedelta(seconds=8),
    )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(effect_intents)
            .where(
                effect_intents.c.effect_intent_id
                == UUID(prepared.intent.effect_intent_id)
            )
            .values(frozen_payload_sha256=bytes.fromhex("9" * 64))
        )
    with pytest.raises(ActionChainCorrupt, match="effect intent binding"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
