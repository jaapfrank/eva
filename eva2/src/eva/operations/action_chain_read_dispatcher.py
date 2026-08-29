"""Claim one authorized action-chain read for exactly one provider call."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.connectors.process import ConnectorProcessSpec
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
    capability_activations,
    capability_manifests,
    connector_bindings,
    plan_steps,
    policy_pack_versions,
    read_intents,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope, ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
)
from eva.contracts.execution import (
    CapabilityManifest,
    CapabilityGrant,
    ConnectorBinding,
    CredentialGrant,
    ReadAttempt,
    ReadIntent,
)
from eva.operations.action_chain_provider_registry import (
    ReadProviderProfile,
    SealedReadProviderRegistry,
)
from eva.operations.action_chain_read_authorizer import (
    PostgresActionChainReadAuthorizer,
    build_action_chain_read_authority,
    require_existing_action_chain_read_attempt,
    validate_action_chain_read_profile,
)
from eva.adapters.postgres.action_chain_reads import ActionChainReadConflict


read_attempts = metadata.tables["eva_effects.read_attempts"]


@dataclass(frozen=True, slots=True)
class ClaimedActionChainReadCall:
    """A first claim carries exact RPC bytes; replay deliberately carries none."""

    disposition: Literal["claimed", "already_started"]
    attempt: ReadAttempt
    process_spec: ConnectorProcessSpec | None
    connector_manifest: ConnectorManifest | None
    initialize: ConnectorInitializeRequest | None
    invoke: ConnectorInvokeRequest | None


class PostgresActionChainReadDispatcher(PostgresScopedRole):
    """Commit a one-way call-start marker before any provider process can run."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedReadProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain read dispatch requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._provider_registry = provider_registry
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)
        self._authorizer = PostgresActionChainReadAuthorizer(
            engine,
            scope,
            provider_registry=provider_registry,
            role=role,
        )

    def claim_provider_call(
        self,
        read_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
    ) -> ClaimedActionChainReadCall:
        """Return an exchange only after its irreversible call-start event commits."""

        _require_utc_second(now)
        self._authorizer.authorize_read(
            read_intent_id,
            live_catalog=live_catalog,
            now=now,
        )
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                )
            ).one()
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.read_intent_id == read_intent_id,
                )
                .with_for_update(read=True)
            ).one()
            attempt_row = connection.execute(
                sa.select(read_attempts)
                .where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id
                    == self._scope_value.installation_id,
                    read_attempts.c.read_intent_id == read_intent_id,
                    read_attempts.c.ordinal == 1,
                )
                .with_for_update()
            ).one()
            activation = connection.execute(
                sa.select(capability_activations)
                .where(
                    capability_activations.c.tenant_id == self._scope_value.tenant_id,
                    capability_activations.c.installation_id
                    == self._scope_value.installation_id,
                    capability_activations.c.activation_id
                    == intent_row.capability_activation_id,
                )
                .with_for_update(read=True)
            ).one()
            manifest_row = connection.execute(
                sa.select(capability_manifests).where(
                    capability_manifests.c.tenant_id == self._scope_value.tenant_id,
                    capability_manifests.c.installation_id
                    == self._scope_value.installation_id,
                    capability_manifests.c.capability_manifest_id
                    == activation.capability_manifest_id,
                )
            ).one()
            binding_row = connection.execute(
                sa.select(connector_bindings)
                .where(
                    connector_bindings.c.tenant_id == self._scope_value.tenant_id,
                    connector_bindings.c.installation_id
                    == self._scope_value.installation_id,
                    connector_bindings.c.binding_id == activation.connector_binding_id,
                )
                .with_for_update(read=True)
            ).one()
            connection.execute(
                sa.select(policy_pack_versions)
                .where(
                    policy_pack_versions.c.tenant_id == self._scope_value.tenant_id,
                    policy_pack_versions.c.installation_id
                    == self._scope_value.installation_id,
                    policy_pack_versions.c.policy_id == activation.policy_id,
                    policy_pack_versions.c.policy_version
                    == activation.policy_pack_version,
                )
                .with_for_update(read=True)
            ).one()

            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            try:
                validate_action_chain_requests(chain.decision, live_catalog)
                intent = ReadIntent.model_validate(intent_row.payload)
                capability_manifest = CapabilityManifest.model_validate(
                    manifest_row.payload
                )
                binding = ConnectorBinding.model_validate(binding_row.payload)
            except (ValueError, ActionChainValidationError) as error:
                raise ActionChainReadConflict(str(error)) from error
            if live_catalog.authority_snapshot() != chain.authority_snapshot:
                raise ActionChainReadConflict(
                    "live action catalog differs from admission"
                )
            stored_step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == step_row.plan_step_id
                    and item.read_intent_id == read_intent_id
                ),
                None,
            )
            action = next(
                (
                    item
                    for item in chain.decision.actions
                    if stored_step is not None
                    and item.action_id == stored_step.logical_step_key
                ),
                None,
            )
            if stored_step is None or action is None or action.kind != "request_read":
                raise ActionChainReadConflict("authorized read differs from its plan")
            try:
                profile = self._provider_registry.resolve(
                    activation.capability_manifest_id
                )
            except LookupError as error:
                raise ActionChainReadConflict(str(error)) from error
            validate_action_chain_read_profile(
                profile,
                capability_manifest=capability_manifest,
                binding=binding,
                intent=intent,
            )
            attempt, capability_grant, credential_grant = (
                build_action_chain_read_authority(
                    scope=self._scope_value,
                    chain=chain,
                    action_arguments=action.arguments,
                    intent=intent,
                    profile=profile,
                    capability_manifest=capability_manifest,
                    binding=binding,
                    created_at=attempt_row.created_at,
                )
            )
            require_existing_action_chain_read_attempt(
                attempt_row,
                attempt=attempt,
                capability_grant=capability_grant,
                credential_grant=credential_grant,
            )
            operation_id = action_chain_step_operation_id(
                chain.plan, stored_step.logical_step_key
            )
            latest = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == operation_id,
                )
                .order_by(operational_events.c.sequence.desc())
                .limit(1)
            ).one()
            latest_data = _event_data(latest.payload)
            require_action_chain_read_registry_event(
                latest_data,
                profile,
                self._provider_registry,
            )
            if latest.event_type == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED":
                return ClaimedActionChainReadCall(
                    "already_started", attempt, None, None, None, None
                )
            if latest.event_type != "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED":
                raise ActionChainReadConflict("read provider-call state differs")
            deadline = _parse_timestamp(intent.deadline)
            if now < attempt_row.created_at or now >= deadline:
                raise ActionChainReadConflict("read provider-call deadline has passed")
            initialize, invoke = build_claimed_action_chain_read_exchange(
                scope=self._scope_value,
                chain=chain,
                action_arguments=action.arguments,
                attempt=attempt,
                intent=intent,
                profile=profile,
                capability_manifest=capability_manifest,
                binding=binding,
                capability_grant=capability_grant,
                credential_grant=credential_grant,
            )
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == operation_id,
                )
                .with_for_update()
            ).one()
            event_data = dict(latest_data)
            event_data["provider_call_started_at"] = _format_timestamp(now)
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=operation_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{operation_id}:read-provider-call-started",
                phase="action_chain_read_provider_call_started",
                status="waiting_for_proof",
                event_type="ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                occurred_at=now,
                data=event_data,
                operation_id=operation_id,
                deadline=deadline,
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                ),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return ClaimedActionChainReadCall(
                "claimed",
                attempt,
                profile.process_spec,
                profile.connector_manifest,
                initialize,
                invoke,
            )

def require_action_chain_read_registry_event(
    data: object,
    profile: ReadProviderProfile,
    registry: SealedReadProviderRegistry,
) -> None:
    expected = {
        "process_spec_sha256": profile.process_spec_sha256,
        "provider_profile_revision": profile.revision,
        "provider_profile_sha256": profile.profile_sha256,
        "provider_registry_sha256": registry.registry_sha256,
        "release_manifest_sha256": registry.release_manifest_sha256,
        "release_provenance_sha256": registry.provenance_sha256,
        "release_verification_receipt_sha256": (
            registry.release_verification_receipt_sha256
        ),
    }
    if not isinstance(data, dict) or any(
        data.get(key) != value for key, value in expected.items()
    ):
        raise ActionChainReadConflict("read provider release authority differs")


def build_claimed_action_chain_read_exchange(
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    attempt: ReadAttempt,
    intent: ReadIntent,
    profile: ReadProviderProfile,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
) -> tuple[ConnectorInitializeRequest, ConnectorInvokeRequest]:
    initialize_id = str(uuid5(UUID(attempt.read_attempt_id), "connector-initialize:v1"))
    correlation_id = str(uuid5(UUID(attempt.read_attempt_id), "correlation:v1"))
    initialize = ConnectorInitializeRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInitializeRequest/v1",
            "jsonrpc": "2.0",
            "id": initialize_id,
            "method": "connector.initialize",
            "params": {
                "protocol_version": "EvaConnectorProtocol/v1",
                "request_id": initialize_id,
                "correlation_id": correlation_id,
                "connector_id": binding.provider_id,
                "connector_manifest_sha256": attempt.connector_manifest_sha256,
                "worker_id": profile.worker_id,
                "worker_executable_sha256": capability_manifest.executable_sha256,
                "connector_binding": binding.model_dump(mode="json"),
                "capability_manifest": capability_manifest.model_dump(mode="json"),
                "capability_grant": capability_grant.model_dump(mode="json"),
                "credential_grant": credential_grant.model_dump(mode="json"),
                "deadline": intent.deadline,
            },
        }
    )
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": str(
                uuid5(UUID(attempt.read_attempt_id), "connector-invoke:v1")
            ),
            "correlation_id": correlation_id,
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": intent.query_sha256,
            "connector_manifest_sha256": attempt.connector_manifest_sha256,
            "capability_grant_id": capability_grant.grant_id,
            "credential_grant_id": credential_grant.credential_grant_id,
            "request_payload": action_arguments,
        }
    )
    invoke = ConnectorInvokeRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInvokeRequest/v1",
            "jsonrpc": "2.0",
            "id": envelope.request_id,
            "method": _read_invoke_method(intent),
            "params": envelope.model_dump(mode="json"),
        }
    )
    try:
        initialize.validate_against_manifest(profile.connector_manifest)
        invoke.validate_against_initialize(initialize, profile.connector_manifest)
    except ValueError as error:
        raise ActionChainReadConflict(str(error)) from error
    if canonical_sha256(envelope) != attempt.request_sha256:
        raise ActionChainReadConflict("claimed read request differs from authorization")
    return initialize, invoke


def _event_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ActionChainReadConflict("read provider timeline payload differs")
    return payload["data"]


def _read_invoke_method(intent: ReadIntent) -> str:
    return {
        "effect.reconcile": "effect.reconcile",
        "effect.readback": "effect.readback",
        "connector.probe": "connector.probe",
    }.get(intent.operation, "resource.read")


def _require_utc_second(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("now must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError("now must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    _require_utc_second(value)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "ClaimedActionChainReadCall",
    "PostgresActionChainReadDispatcher",
]
