"""Shared persistence mechanics for read and effect provider dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_ledger import (
    StoredActionChain,
    capability_activations,
    capability_manifests,
    connector_bindings,
    plan_steps,
    policy_pack_versions,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectIntent,
    ReadIntent,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)


@dataclass(frozen=True, slots=True)
class DispatchAuthorityRows:
    intent: Any
    step: Any
    attempt: Any
    activation: Any
    manifest: Any
    binding: Any


def build_dispatch_authority_material(
    *,
    kind: Literal["read", "effect"],
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    intent: ReadIntent | EffectIntent,
    profile: Any,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    created_at: datetime,
    conflict_type: type[Exception],
) -> tuple[UUID, CapabilityGrant, CredentialGrant, str, ConnectorEnvelope]:
    """Build the provider-neutral grants and exact connector envelope once."""

    if datetime.strptime(
        profile.connector_manifest.issued_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC) > created_at:
        raise conflict_type("connector manifest was issued in the future")
    if kind == "read" and isinstance(intent, ReadIntent):
        attempt_id = uuid5(UUID(intent.read_intent_id), "read-attempt:1")
        payload_sha256 = intent.query_sha256
        request_id = str(uuid5(attempt_id, "connector-invoke:v1"))
    elif kind == "effect" and isinstance(intent, EffectIntent):
        attempt_id = uuid5(UUID(intent.effect_intent_id), "effect-attempt:1")
        payload_sha256 = intent.frozen_payload_sha256
        request_id = intent.idempotency_key
    else:
        raise TypeError("dispatch kind and intent differ")
    issued_at = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    capability_grant = CapabilityGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CapabilityGrant/v1",
            "grant_id": str(uuid5(attempt_id, "capability-grant:v1")),
            "tenant_id": str(scope.tenant_id),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "capability_id": intent.capability_id,
            "connector_binding_id": binding.binding_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "authorized_payload_sha256": payload_sha256,
            "policy_sha256": chain.plan.policy_sha256,
            "nonce": str(uuid5(attempt_id, "capability-nonce:v1")),
            "issued_at": issued_at,
            "deadline": intent.deadline,
        }
    )
    credential_grant = CredentialGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CredentialGrant/v1",
            "credential_grant_id": str(uuid5(attempt_id, "credential-grant:v1")),
            "connector_binding_id": binding.binding_id,
            "model_provider_profile_id": None,
            "worker_id": profile.worker_id,
            "executable_sha256": capability_manifest.executable_sha256,
            "provider_scopes": capability_manifest.required_provider_scopes,
            "secret_descriptor_id": binding.credential_reference,
            "nonce": str(uuid5(attempt_id, "credential-nonce:v1")),
            "issued_at": issued_at,
            "deadline": intent.deadline,
        }
    )
    connector_manifest_sha256 = canonical_sha256(profile.connector_manifest)
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": request_id,
            "correlation_id": str(uuid5(attempt_id, "correlation:v1")),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": payload_sha256,
            "connector_manifest_sha256": connector_manifest_sha256,
            "capability_grant_id": capability_grant.grant_id,
            "credential_grant_id": credential_grant.credential_grant_id,
            "request_payload": action_arguments,
        }
    )
    return (
        attempt_id,
        capability_grant,
        credential_grant,
        connector_manifest_sha256,
        envelope,
    )


def load_dispatch_authority_rows(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    intent_table: sa.Table,
    attempt_table: sa.Table,
    intent_id_name: str,
    intent_id: UUID,
    lock_intent: bool,
) -> DispatchAuthorityRows:
    """Load the same sealed authority graph for either a read or an effect."""

    intent_id_column = intent_table.c[intent_id_name]
    intent = _load_scoped_row(
        connection,
        scope,
        intent_table,
        intent_id_column == intent_id,
        lock="write" if lock_intent else None,
    )
    step = _load_scoped_row(
        connection,
        scope,
        plan_steps,
        plan_steps.c.plan_step_id == intent.plan_step_id,
        plan_steps.c[intent_id_name] == intent_id,
        lock="read",
    )
    attempt = _load_scoped_row(
        connection,
        scope,
        attempt_table,
        attempt_table.c[intent_id_name] == intent_id,
        attempt_table.c.ordinal == 1,
        lock="write",
    )
    activation = _load_scoped_row(
        connection,
        scope,
        capability_activations,
        capability_activations.c.activation_id == intent.capability_activation_id,
        lock="read",
    )
    manifest = _load_scoped_row(
        connection,
        scope,
        capability_manifests,
        capability_manifests.c.capability_manifest_id
        == activation.capability_manifest_id,
    )
    binding = _load_scoped_row(
        connection,
        scope,
        connector_bindings,
        connector_bindings.c.binding_id == activation.connector_binding_id,
        lock="read",
    )
    _load_scoped_row(
        connection,
        scope,
        policy_pack_versions,
        policy_pack_versions.c.policy_id == activation.policy_id,
        policy_pack_versions.c.policy_version == activation.policy_pack_version,
        lock="read",
    )
    return DispatchAuthorityRows(intent, step, attempt, activation, manifest, binding)


def _load_scoped_row(
    connection: sa.Connection,
    scope: TimelineScope,
    table: sa.Table,
    *criteria: Any,
    lock: Literal["read", "write"] | None = None,
) -> Any:
    statement = sa.select(table).where(
        table.c.tenant_id == scope.tenant_id,
        table.c.installation_id == scope.installation_id,
        *criteria,
    )
    if lock is not None:
        statement = statement.with_for_update(read=lock == "read")
    return connection.execute(statement).one()


def load_latest_provider_event(
    connection: sa.Connection, scope: TimelineScope, operation_id: str
) -> Any:
    return connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()


def commit_provider_call_started(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    kind: Literal["read", "effect"],
    operation_id: str,
    checkpoint_data: dict[str, object],
    now: datetime,
    deadline: datetime,
) -> None:
    checkpoint = _load_scoped_row(
        connection,
        scope,
        operational_checkpoints,
        operational_checkpoints.c.thread_id == operation_id,
        lock="write",
    )
    data = dict(checkpoint_data)
    data["provider_call_started_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    transition_timeline_thread(
        connection,
        scope,
        thread_id=operation_id,
        expected_revision=int(checkpoint.revision),
        checkpoint_id=f"checkpoint:{operation_id}:{kind}-provider-call-started",
        phase=f"action_chain_{kind}_provider_call_started",
        status="waiting_for_proof",
        event_type=f"ACTION_CHAIN_{kind.upper()}_PROVIDER_CALL_STARTED",
        occurred_at=now,
        data=data,
        operation_id=operation_id,
        deadline=deadline,
        retry_count=int(checkpoint.retry_count),
        last_error_fingerprint=(
            None
            if checkpoint.last_error_fingerprint is None
            else bytes(checkpoint.last_error_fingerprint).hex()
        ),
    )
