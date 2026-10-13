from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from pathlib import Path
import sys
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effects import (
    ActionChainEffectConflict,
    PostgresActionChainEffectPreparer,
)
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.execution import CapabilityManifest, CredentialGrant, EffectIntent
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_provider_registry import (
    SealedActionProviderRegistry,
)

from test_action_chain_effects import (
    _claimed_effect,
    _effect_authority,
    _effect_counts,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW
from test_action_chain_read_authorizer import _verified_registry_release


WORKER_PROGRAM = b"# fixture-action-chain-effect-worker-v1"


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _provider_registry(
    engine: Engine,
    scope,
    intent: EffectIntent,
    *,
    command_suffix: str = "v1",
    writes_provider_state: bool = True,
) -> SealedActionProviderRegistry:
    activations = metadata.tables["eva_core.capability_activations"]
    manifests = metadata.tables["eva_core.capability_manifests"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        activation = connection.execute(
            sa.select(activations).where(
                activations.c.activation_id
                == _effect_authority(scope).capability_activation_id
            )
        ).one()
        manifest = CapabilityManifest.model_validate(
            connection.scalar(
                sa.select(manifests.c.payload).where(
                    manifests.c.capability_manifest_id
                    == activation.capability_manifest_id
                )
            )
        )
    connector_manifest = ConnectorManifest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorManifest/v1",
            "connector_id": manifest.connector_id,
            "connector_version": "1.0.0",
            "protocol_version": "EvaConnectorProtocol/v1",
            "executable_sha256": manifest.executable_sha256,
            "sandbox_profile_sha256": manifest.sandbox_profile_sha256,
            "operations": [
                {
                    "operation": intent.operation,
                    "input_schema_sha256": manifest.input_schema_sha256,
                    "output_schema_sha256": manifest.output_schema_sha256,
                    "external_calls_per_invocation": 1,
                    "writes_provider_state": writes_provider_state,
                }
            ],
            "maximum_protocol_frame_bytes": 1_048_576,
            "maximum_protocol_frames_per_invocation": 2,
            "maximum_stdout_bytes": 2_101_248,
            "maximum_stderr_bytes": 65_536,
            "issued_at": "2026-08-20T12:00:00Z",
        }
    )
    executable = Path(sys.executable).resolve()
    command = [str(executable), "-I"]
    if command_suffix != "v1":
        command.append("-B")
    command.extend(("-c", WORKER_PROGRAM.decode("utf-8")))
    artifact = canonical_json_bytes(
        {
            "schema_version": 1,
            "contract": "ReadProviderRegistryRelease/v1",
            "release_id": f"fixture:mc03c1:{command_suffix}",
            "profiles": [
                {
                    "capability_manifest_id": str(
                        activation.capability_manifest_id
                    ),
                    "revision": 1,
                    "connector_manifest": connector_manifest.model_dump(mode="json"),
                    "process_spec": {
                        "command": command,
                        "command_targets": [
                            {
                                "argument_index": 0,
                                "target_path": str(executable),
                                "target_sha256": hashlib.sha256(
                                    executable.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                        "program_base64": base64.b64encode(WORKER_PROGRAM).decode(
                            "ascii"
                        ),
                        "program_binding": {
                            "kind": "python_inline_code",
                            "argument_index": command.index("-c") + 1,
                        },
                        "invoke_failure_phase": "invoke_external_io_possible",
                        "fixture_selection": None,
                    },
                }
            ],
        }
    )
    provenance, verification = _verified_registry_release(
        release_id=f"fixture:mc03c1:{command_suffix}",
        registry_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    return SealedActionProviderRegistry.from_release_artifact(
        artifact,
        provenance_bytes=provenance,
        release_verification=verification,
    )


def _prepared_effect(engine: Engine):
    scope, chain, catalog, authority, claim = _claimed_effect(engine)
    prepared = PostgresActionChainEffectPreparer(engine, scope).prepare_effect(
        claim,
        live_catalog=catalog,
        authority=authority,
        now=NOW + timedelta(seconds=8),
    )
    return scope, chain, catalog, authority, prepared


def _install_dispatch_event_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03c1_dispatch_event()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type = 'ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED' THEN
                    RAISE EXCEPTION 'forced_mc03c1_dispatch_event_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03c1_dispatch_event
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03c1_dispatch_event()
            """
        )


def _remove_dispatch_event_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc03c1_dispatch_event "
            "ON eva_core.operational_events"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION eva_core.reject_mc03c1_dispatch_event()"
        )


def test_effect_dispatch_authority_is_durable_concurrent_and_uncalled(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    authorizer = PostgresActionChainEffectAuthorizer(
        action_chain_engine, scope, provider_registry=registry
    )
    _install_dispatch_event_failure(action_chain_engine)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03c1"):
            authorizer.authorize_effect(
                UUID(prepared.intent.effect_intent_id),
                live_catalog=catalog,
                now=NOW + timedelta(seconds=9),
            )
    finally:
        _remove_dispatch_event_failure(action_chain_engine)
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0

    def authorize(_: int):
        return authorizer.authorize_effect(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=9),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(authorize, (0, 1)))
    assert sorted(item.disposition for item in results) == [
        "already_authorized",
        "authorized",
    ]
    selected = next(item for item in results if item.disposition == "authorized")
    assert selected.attempt.state == "dispatch_authorized"
    assert not hasattr(selected, "invoke")
    assert not hasattr(selected, "process_spec")
    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert counts["attempts"] == 1
    assert counts["receipts"] == counts["readbacks"] == 0
    assert counts["tool_prepared"] == 0

    attempts = metadata.tables["eva_effects.effect_attempts"]
    with action_chain_engine.connect() as connection:
        row = connection.execute(sa.select(attempts)).one()
        assert row.response_sha256 is None
        assert row.transport_evidence_id is None
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED"
            )
        ) == 1
        event = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED"
            )
        ).one()
        assert event.payload["data"]["provider_registry_sha256"] == (
            registry.registry_sha256
        )
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"

    changed_registry = _provider_registry(
        action_chain_engine, scope, prepared.intent, command_suffix="v2"
    )
    with pytest.raises(ActionChainEffectConflict, match="attempt differs"):
        PostgresActionChainEffectAuthorizer(
            action_chain_engine, scope, provider_registry=changed_registry
        ).authorize_effect(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=10),
        )

    action_chain_engine.dispose()
    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectAuthorizer(
            restarted, scope, provider_registry=registry
        ).authorize_effect(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
        )
        assert replay.disposition == "already_authorized"
        assert replay.attempt == selected.attempt
        with restarted.connect() as connection:
            assert connection.scalar(
                sa.select(sa.func.count()).select_from(attempts)
            ) == 1
    finally:
        restarted.dispose()


def test_effect_dispatch_revalidates_profile_and_live_authority(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, authority, prepared = _prepared_effect(
        action_chain_engine
    )
    read_only_registry = _provider_registry(
        action_chain_engine,
        scope,
        prepared.intent,
        writes_provider_state=False,
    )
    effect_registry = _provider_registry(
        action_chain_engine, scope, prepared.intent, command_suffix="effect-v1"
    )
    with pytest.raises(ActionChainEffectConflict, match="profile differs"):
        PostgresActionChainEffectAuthorizer(
            action_chain_engine, scope, provider_registry=read_only_registry
        ).authorize_effect(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=9),
        )
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
            .values(state="revoked", revision=2)
        )
    with pytest.raises(ActionChainCorrupt, match="capability binding"):
        PostgresActionChainEffectAuthorizer(
            action_chain_engine,
            scope,
            provider_registry=effect_registry,
        ).authorize_effect(
            UUID(prepared.intent.effect_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=9),
        )
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0


def test_reload_rejects_tampered_authorized_effect(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(
        action_chain_engine
    )
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    PostgresActionChainEffectAuthorizer(
        action_chain_engine, scope, provider_registry=registry
    ).authorize_effect(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    attempts = metadata.tables["eva_effects.effect_attempts"]
    with action_chain_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        row = connection.execute(sa.select(attempts)).one()
        credential = CredentialGrant.model_validate(row.credential_grant_payload)
        tampered = credential.model_copy(update={"worker_id": "connector-worker:bad"})
        connection.execute(
            sa.update(attempts).values(
                credential_grant_payload=tampered.model_dump(mode="json"),
                credential_grant_sha256=bytes.fromhex(canonical_sha256(tampered)),
            )
        )
        with pytest.raises(ActionChainCorrupt, match="worker pin"):
            PostgresActionChainLedger(action_chain_engine, scope).load_in_transaction(
                connection, chain.plan_id
            )
        transaction.rollback()
