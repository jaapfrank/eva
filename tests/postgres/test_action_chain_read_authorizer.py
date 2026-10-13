from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import sys
from threading import Event
from typing import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.action_chain_leases import PostgresActionChainLeases
from eva.operations.action_chain_provider_registry import (
    SealedReadProviderRegistry,
)
from eva.operations.action_chain_read_authorizer import (
    ActionChainReadConflict,
    PostgresActionChainReadAuthorizer,
)
from eva.operations.release_verifier import (
    ReleaseVerification,
    signature_message,
    unsigned_manifest,
    verify_release_manifest,
)
from eva.adapters.postgres.action_chain_reads import PostgresActionChainReadPreparer
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import action_chain_step_operation_id
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.execution import CapabilityManifest, CredentialGrant, ReadIntent

from test_action_chain_ledger import (
    NOW,
    _decision_and_plan,
    _read_authority,
    _scope_and_episode,
    _seed_model_attempt,
    action_chain_engine as _shared_action_chain_engine,
)


WORKER_PROGRAM = b"# fixture-action-chain-read-worker-v1"
RELEASE_VERIFIED_AT = "2026-08-25T12:00:00Z"


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _verified_registry_release(
    *,
    release_id: str,
    registry_sha256: str,
) -> tuple[bytes, ReleaseVerification]:
    provenance_bytes = canonical_json_bytes(
        {
            "schema_version": 1,
            "contract": "EvaRuntimeArtifactProvenance/v1",
            "release_id": release_id,
            "runtime_artifacts": {
                "read_provider_registry_sha256": registry_sha256,
            },
        }
    )
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    root = {
        "contract": "Eva2TrustRoot/v1",
        "rootVersion": 1,
        "validFrom": "2026-08-01T00:00:00Z",
        "threshold": 1,
        "keys": [
            {
                "keyId": "release-key:mc03b:001",
                "algorithm": "Ed25519",
                "publicKeyBase64": base64.b64encode(public).decode("ascii"),
                "roles": ["release_signer"],
                "validFrom": "2026-08-01T00:00:00Z",
                "validUntil": "2027-08-01T00:00:00Z",
            }
        ],
        "previousRootSignatures": [],
    }
    manifest = {
        "contract": "Eva2ReleaseManifest/v1",
        "releaseId": release_id,
        "releaseVersion": "0.1.0-mc03b",
        "minimumSupportedRelease": "0.1.0-mc03b",
        "sourceCommit": "a" * 40,
        "platformLockSha256": "1" * 64,
        "imageDigests": {"living_core": f"sha256:{'2' * 64}"},
        "sbomBindings": ["3" * 64],
        "licenseInventorySha256": "4" * 64,
        "provenanceSha256": hashlib.sha256(provenance_bytes).hexdigest(),
        "schemaSetSha256": "5" * 64,
        "migrationRevision": "4e_0001",
        "compatibleSchemaRevisions": ["4e_0001"],
        "revocationSequence": 1,
        "signatures": [],
    }
    subject_sha256 = hashlib.sha256(
        canonical_json_bytes(unsigned_manifest(manifest))
    ).hexdigest()
    envelope = {
        "contract": "Eva2SignatureEnvelope/v1",
        "scope": "release",
        "subjectId": release_id,
        "subjectSha256": subject_sha256,
        "algorithm": "Ed25519",
        "domainSeparationPrefix": r"Eva2ReleaseSignature/v1\u0000",
        "keyId": "release-key:mc03b:001",
        "trustRootVersion": 1,
        "signedAt": RELEASE_VERIFIED_AT,
        "expiresAt": "2026-09-25T12:00:00Z",
        "provenanceSha256": manifest["provenanceSha256"],
        "signatureBase64": "",
    }
    envelope["signatureBase64"] = base64.b64encode(
        private.sign(signature_message(envelope))
    ).decode("ascii")
    manifest["signatures"] = [envelope]
    verification = verify_release_manifest(
        canonical_json_bytes(manifest),
        canonical_json_bytes(root),
        verified_at=RELEASE_VERIFIED_AT,
    )
    return provenance_bytes, verification


def _provider_registry(
    engine: Engine,
    scope: TimelineScope,
    intent: ReadIntent,
    *,
    capability_activation_id: UUID | None = None,
    command_suffix: str = "v1",
    worker_program: bytes = WORKER_PROGRAM,
    program_argument: bytes | None = None,
    program_binding_index: int | None = None,
    signed_registry_sha256: str | None = None,
) -> SealedReadProviderRegistry:
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
                == (
                    _read_authority(scope).capability_activation_id
                    if capability_activation_id is None
                    else capability_activation_id
                )
            )
        ).one()
        capability_manifest = CapabilityManifest.model_validate(
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
            "connector_id": capability_manifest.connector_id,
            "connector_version": "1.0.0",
            "protocol_version": "EvaConnectorProtocol/v1",
            "executable_sha256": capability_manifest.executable_sha256,
            "sandbox_profile_sha256": capability_manifest.sandbox_profile_sha256,
            "operations": [
                {
                    "operation": intent.operation,
                    "input_schema_sha256": capability_manifest.input_schema_sha256,
                    "output_schema_sha256": capability_manifest.output_schema_sha256,
                    "external_calls_per_invocation": 1,
                    "writes_provider_state": False,
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
    command.extend(
        ("-c", (worker_program if program_argument is None else program_argument).decode("utf-8"))
    )
    release_id = f"fixture:mc03b:{command_suffix}"
    actual_program_index = command.index("-c") + 1
    artifact_bytes = canonical_json_bytes(
        {
            "schema_version": 1,
            "contract": "ReadProviderRegistryRelease/v1",
            "release_id": release_id,
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
                        "program_base64": base64.b64encode(worker_program).decode(
                            "ascii"
                        ),
                        "program_binding": {
                            "kind": "python_inline_code",
                            "argument_index": (
                                actual_program_index
                                if program_binding_index is None
                                else program_binding_index
                            ),
                        },
                        "invoke_failure_phase": "invoke_external_io_possible",
                        "fixture_selection": None,
                    },
                }
            ],
        }
    )
    provenance_bytes, release_verification = _verified_registry_release(
        release_id=release_id,
        registry_sha256=(
            hashlib.sha256(artifact_bytes).hexdigest()
            if signed_registry_sha256 is None
            else signed_registry_sha256
        ),
    )
    return SealedReadProviderRegistry.from_release_artifact(
        artifact_bytes,
        provenance_bytes=provenance_bytes,
        release_verification=release_verification,
    )


def _install_dispatch_event_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03b_dispatch_event()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type = 'ACTION_CHAIN_READ_DISPATCH_AUTHORIZED' THEN
                    RAISE EXCEPTION 'forced_mc03b_dispatch_event_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03b_dispatch_event
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03b_dispatch_event()
            """
        )


def _remove_dispatch_event_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc03b_dispatch_event "
            "ON eva_core.operational_events"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION eva_core.reject_mc03b_dispatch_event()"
        )


def test_read_dispatch_authority_is_host_resolved_durable_and_uncalled(
    action_chain_engine: Engine,
) -> None:
    scope, storage_episode_id = _scope_and_episode()
    decision, plan, catalog = _decision_and_plan()
    model_attempt_id = _seed_model_attempt(action_chain_engine, scope, decision)
    ledger = PostgresActionChainLedger(action_chain_engine, scope)
    admitted = ledger.admit(
        storage_episode_id=storage_episode_id,
        model_attempt_id=model_attempt_id,
        decision=decision,
        plan=plan,
        catalog=catalog,
        retention_policy_id="retention:noor-document:30d",
        retention_until=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        occurred_at=NOW,
    ).chain
    claim = PostgresActionChainLeases(action_chain_engine, scope).claim_next(
        worker_id="worker:mc03b:prepare",
        now=NOW + timedelta(seconds=1),
        lease_seconds=10,
    )
    assert claim is not None
    prepared = PostgresActionChainReadPreparer(
        action_chain_engine, scope
    ).prepare_read(
        claim,
        live_catalog=catalog,
        authority=_read_authority(scope),
        now=NOW + timedelta(seconds=2),
    )
    registry = _provider_registry(
        action_chain_engine, scope, prepared.intent
    )
    with pytest.raises(ValueError, match="release artifact hash differs"):
        _provider_registry(
            action_chain_engine,
            scope,
            prepared.intent,
            command_suffix="untrusted-first-authorization",
            signed_registry_sha256=registry.registry_sha256,
        )
    with pytest.raises(ValueError, match="inline code differs"):
        _provider_registry(
            action_chain_engine,
            scope,
            prepared.intent,
            program_argument=b"# different-command-program",
        )
    with pytest.raises(ValueError, match="must immediately follow -c"):
        _provider_registry(
            action_chain_engine,
            scope,
            prepared.intent,
            program_binding_index=1,
        )
    with pytest.raises(ValueError, match="sealed release artifact"):
        SealedReadProviderRegistry(
            (),
            release_artifact_sha256="0" * 64,
            release_manifest_sha256="1" * 64,
            provenance_sha256="2" * 64,
            release_verification_receipt_sha256="3" * 64,
            _seal=object(),
        )
    authorizer = PostgresActionChainReadAuthorizer(
        action_chain_engine,
        scope,
        provider_registry=registry,
    )

    with pytest.raises(TypeError, match="unexpected keyword argument 'provider'"):
        authorizer.authorize_read(  # type: ignore[call-arg]
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            provider=object(),
            now=NOW + timedelta(seconds=3),
        )
    _install_dispatch_event_failure(action_chain_engine)
    try:
        with pytest.raises(
            sa.exc.DBAPIError, match="forced_mc03b_dispatch_event_failure"
        ):
            authorizer.authorize_read(
                UUID(prepared.intent.read_intent_id),
                live_catalog=catalog,
                now=NOW + timedelta(seconds=3),
            )
    finally:
        _remove_dispatch_event_failure(action_chain_engine)
    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    effects = metadata.tables["eva_effects.effect_intents"]
    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(attempts)) == 0
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.event_type
                    == "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED"
                )
            )
            == 0
        )

    insert_entered = Event()
    release_insert = Event()

    def hold_after_authority_locks(
        _connection: sa.Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO eva_effects.read_attempts"):
            insert_entered.set()
            if not release_insert.wait(timeout=5):
                raise AssertionError("MC03b authorization barrier timed out")

    sa.event.listen(
        action_chain_engine, "before_cursor_execute", hold_after_authority_locks
    )
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        futures = tuple(
            pool.submit(
                authorizer.authorize_read,
                UUID(prepared.intent.read_intent_id),
                live_catalog=catalog,
                now=NOW + timedelta(seconds=3),
            )
            for _ in range(2)
        )
        assert insert_entered.wait(timeout=5)
        activations = metadata.tables["eva_core.capability_activations"]
        with pytest.raises(sa.exc.DBAPIError, match="statement timeout"):
            with action_chain_engine.begin() as authority_update:
                authority_update.execute(
                    sa.text("SET LOCAL statement_timeout = '250ms'")
                )
                authority_update.execute(
                    sa.update(activations)
                    .where(
                        activations.c.activation_id
                        == _read_authority(scope).capability_activation_id
                    )
                    .values(state="suspended", revision=2)
                )
        release_insert.set()
        results = tuple(future.result(timeout=5) for future in futures)
    finally:
        release_insert.set()
        pool.shutdown(wait=True, cancel_futures=True)
        sa.event.remove(
            action_chain_engine, "before_cursor_execute", hold_after_authority_locks
        )
    assert sorted(item.disposition for item in results) == [
        "already_authorized",
        "authorized",
    ]
    selected = next(item for item in results if item.disposition == "authorized")
    assert not hasattr(selected, "initialize")
    assert not hasattr(selected, "invoke")
    assert selected.attempt.state == "dispatch_authorized"
    assert selected.attempt.response_sha256 is None

    loaded = ledger.load(admitted.plan_id)
    assert loaded.steps[0].state == "running"
    assert loaded.steps[1].state == "pending"
    with action_chain_engine.connect() as connection:
        attempt_row = connection.execute(sa.select(attempts)).one()
        assert attempt_row.state == "dispatch_authorized"
        assert attempt_row.response_sha256 is None
        assert connection.scalar(sa.select(sa.func.count()).select_from(attempts)) == 1
        assert connection.scalar(
            sa.select(sa.func.count()).select_from(observations)
        ) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(effects)) == 0
        dispatch_event = connection.execute(
            sa.select(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED"
            )
        ).one()
        event_data = dispatch_event.payload["data"]
        profile = registry.resolve(
            UUID(
                connection.scalar(
                    sa.select(activations.c.capability_manifest_id).where(
                        activations.c.activation_id
                        == _read_authority(scope).capability_activation_id
                    )
                ).hex
            )
        )
        assert event_data["process_spec_sha256"] == profile.process_spec_sha256
        assert event_data["provider_profile_sha256"] == profile.profile_sha256
        assert event_data["provider_registry_sha256"] == registry.registry_sha256
        assert (
            event_data["release_manifest_sha256"]
            == registry.release_manifest_sha256
        )
        assert (
            event_data["release_provenance_sha256"]
            == registry.provenance_sha256
        )
        assert (
            event_data["release_verification_receipt_sha256"]
            == registry.release_verification_receipt_sha256
        )

    other_scope = TimelineScope(
        UUID("d3244894-f179-497f-9588-76e1aeb54530"),
        UUID("745b2b30-2a63-4ae9-a07e-7f7cbd9bad49"),
    )
    with pytest.raises(ActionChainReadConflict, match="absent in scope"):
        PostgresActionChainReadAuthorizer(
            action_chain_engine,
            other_scope,
            provider_registry=registry,
        ).authorize_read(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=4),
        )

    changed_registry = _provider_registry(
        action_chain_engine,
        scope,
        prepared.intent,
        command_suffix="injected-consistent-v2",
    )
    with pytest.raises(ActionChainReadConflict, match="attempt differs"):
        PostgresActionChainReadAuthorizer(
            action_chain_engine,
            scope,
            provider_registry=changed_registry,
        ).authorize_read(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=4),
        )

    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(activations)
            .where(
                activations.c.activation_id
                == _read_authority(scope).capability_activation_id
            )
            .values(state="suspended", revision=2)
        )
    with pytest.raises(ActionChainCorrupt, match="capability binding"):
        ledger.load(admitted.plan_id)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.update(activations)
            .where(
                activations.c.activation_id
                == _read_authority(scope).capability_activation_id
            )
            .values(state="active", revision=3)
        )

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
            ledger.load_in_transaction(connection, admitted.plan_id)
        transaction.rollback()

    operation_id = action_chain_step_operation_id(
        admitted.plan, admitted.plan.steps[0].step_id
    )
    with action_chain_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        checkpoint = connection.execute(
            sa.select(operational_checkpoints)
            .where(operational_checkpoints.c.thread_id == operation_id)
            .with_for_update()
        ).one()
        transition_timeline_thread(
            connection,
            scope,
            thread_id=operation_id,
            expected_revision=int(checkpoint.revision),
            checkpoint_id=f"checkpoint:{operation_id}:dispatch-corrupt-test",
            phase="action_chain_read_dispatch_authorized",
            status="waiting_for_proof",
            event_type="ACTION_CHAIN_READ_DISPATCH_AUTHORIZED",
            occurred_at=NOW + timedelta(seconds=5),
            data={"tampered": True},
            operation_id=operation_id,
            deadline=datetime.strptime(
                admitted.plan.deadline, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC),
            retry_count=int(checkpoint.retry_count),
            last_error_fingerprint=None,
        )
        with pytest.raises(ActionChainCorrupt, match="authorized read"):
            ledger.load_in_transaction(connection, admitted.plan_id)
        transaction.rollback()

    action_chain_engine.dispose()
    restarted_engine = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainReadAuthorizer(
            restarted_engine,
            scope,
            provider_registry=registry,
        ).authorize_read(
            UUID(prepared.intent.read_intent_id),
            live_catalog=catalog,
            now=NOW + timedelta(seconds=6),
        )
        assert replay.disposition == "already_authorized"
        assert replay.attempt == selected.attempt
        assert not hasattr(replay, "initialize")
        assert not hasattr(replay, "invoke")
        with restarted_engine.connect() as connection:
            assert connection.scalar(
                sa.select(sa.func.count()).select_from(attempts)
            ) == 1
            assert connection.scalar(
                sa.select(sa.func.count()).select_from(observations)
            ) == 0
    finally:
        restarted_engine.dispose()
