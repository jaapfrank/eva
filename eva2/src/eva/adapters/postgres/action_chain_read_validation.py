"""Corruption-sensitive reload validation for durable action-chain reads."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp

from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_read_result_validation import (
    validate_action_chain_read_terminal,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
)
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    ReadAttempt,
    ReadIntent,
)
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ModelDecision, ValidatedPlan


capability_activations = metadata.tables["eva_core.capability_activations"]
capability_manifests = metadata.tables["eva_core.capability_manifests"]
connector_bindings = metadata.tables["eva_core.connector_bindings"]
policy_pack_versions = metadata.tables["eva_core.policy_pack_versions"]
read_attempts = metadata.tables["eva_effects.read_attempts"]
read_intents = metadata.tables["eva_effects.read_intents"]
read_observations = metadata.tables["eva_effects.read_observations"]


def validate_action_chain_read(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    decision: ModelDecision,
    authority_snapshot: dict[str, Any],
    plan_row: sa.Row,
    step: Any,
    stored: sa.Row,
    current_event_data: dict[str, Any],
    corrupt: type[RuntimeError],
) -> ReadAttempt | None:
    """Validate the prepared intent and its optional authorization fence."""

    intent_row = connection.execute(
        sa.select(read_intents).where(
            read_intents.c.tenant_id == scope.tenant_id,
            read_intents.c.installation_id == scope.installation_id,
            read_intents.c.read_intent_id == stored.read_intent_id,
        )
    ).one_or_none()
    if intent_row is None:
        raise corrupt("prepared read intent is absent")
    try:
        intent = ReadIntent.model_validate(intent_row.payload)
    except ValueError as error:
        raise corrupt("prepared read intent is invalid") from error
    action = next(
        (item for item in decision.actions if item.action_id == step.step_id), None
    )
    descriptor = next(
        (
            item
            for item in authority_snapshot.get("descriptors", [])
            if isinstance(item, dict) and item.get("operation") == step.operation
        ),
        None,
    )
    if action is None or descriptor is None:
        raise corrupt("prepared read authority is absent")
    expected_intent_id = uuid5(stored.plan_step_id, "read-intent:v1")
    expected_query_sha256 = canonical_sha256(action.arguments)
    expected_scope_payload = {
        "resource_type": intent.resource_scope.resource_type,
        "resource_id": intent.resource_scope.resource_id,
    }
    claim_sha256, prepared_at = _prepared_claim(
        connection,
        scope,
        plan=plan,
        plan_id=plan_id,
        plan_step_id=stored.plan_step_id,
        logical_step_key=step.step_id,
        corrupt=corrupt,
    )
    if (
        stored.read_intent_id != expected_intent_id
        or intent.read_intent_id != str(expected_intent_id)
        or intent.episode_id != plan.episode_id
        or intent.generation != plan.generation
        or intent.purpose != "business_read"
        or intent.effect_intent_id is not None
        or intent.operation != step.operation
        or intent.resource_scope.resource_id != action.resource
        or intent.resource_scope.scope_sha256
        != canonical_sha256(expected_scope_payload)
        or intent.query_sha256 != expected_query_sha256
        or intent.maximum_attempts != 2
        or intent.deadline != plan.deadline
        or canonical_sha256(intent) != bytes(intent_row.payload_sha256).hex()
        or intent_row.episode_id != plan_row.episode_id
        or int(intent_row.generation) != plan.generation
        or intent_row.logical_read_key != f"action-chain:{stored.plan_step_id}:read"
        or intent_row.plan_step_id != stored.plan_step_id
        or intent_row.purpose != intent.purpose
        or intent_row.effect_intent_id is not None
        or intent_row.circuit_breaker_id is not None
        or intent_row.operation != intent.operation
        or intent_row.resource_scope != intent.resource_scope.model_dump(mode="json")
        or bytes(intent_row.resource_scope_sha256).hex()
        != intent.resource_scope.scope_sha256
        or bytes(intent_row.query_sha256).hex() != expected_query_sha256
        or int(intent_row.maximum_freshness_ms) != intent.maximum_freshness_ms
        or int(intent_row.maximum_attempts) != intent.maximum_attempts
        or intent_row.capability_id != intent.capability_id
        or intent_row.deadline != _parse_timestamp(intent.deadline)
        or intent_row.retention_policy_id != plan_row.retention_policy_id
        or intent_row.retention_until != plan_row.retention_until
        or intent_row.created_at != prepared_at
    ):
        raise corrupt("prepared read intent binding differs")

    activation = connection.execute(
        sa.select(capability_activations).where(
            capability_activations.c.tenant_id == scope.tenant_id,
            capability_activations.c.installation_id == scope.installation_id,
            capability_activations.c.activation_id
            == intent_row.capability_activation_id,
        )
    ).one_or_none()
    manifest = (
        None
        if activation is None
        else connection.execute(
            sa.select(capability_manifests).where(
                capability_manifests.c.tenant_id == scope.tenant_id,
                capability_manifests.c.installation_id == scope.installation_id,
                capability_manifests.c.capability_manifest_id
                == activation.capability_manifest_id,
            )
        ).one_or_none()
    )
    binding = (
        None
        if activation is None
        else connection.execute(
            sa.select(connector_bindings).where(
                connector_bindings.c.tenant_id == scope.tenant_id,
                connector_bindings.c.installation_id == scope.installation_id,
                connector_bindings.c.binding_id == activation.connector_binding_id,
            )
        ).one_or_none()
    )
    policy = (
        None
        if activation is None
        else connection.execute(
            sa.select(policy_pack_versions).where(
                policy_pack_versions.c.tenant_id == scope.tenant_id,
                policy_pack_versions.c.installation_id == scope.installation_id,
                policy_pack_versions.c.policy_id == activation.policy_id,
                policy_pack_versions.c.policy_version
                == activation.policy_pack_version,
            )
        ).one_or_none()
    )
    try:
        manifest_payload = CapabilityManifest.model_validate(
            None if manifest is None else manifest.payload
        )
        binding_payload = ConnectorBinding.model_validate(
            None if binding is None else binding.payload
        )
    except ValueError as error:
        raise corrupt("prepared read capability payload is invalid") from error
    if (
        activation is None
        or manifest is None
        or binding is None
        or policy is None
        or activation.state != "active"
        or binding.status != "verified"
        or manifest.state != "active"
        or activation.capability_id != manifest.capability_id
        or activation.operation != manifest.operation
        or activation.operation != step.operation
        or intent.capability_id != activation.capability_id
        or activation.connector_binding_id != binding.binding_id
        or step.operation not in binding.confirmed_operations
        or intent.resource_scope.resource_type not in binding.confirmed_scopes
        or not set(manifest_payload.required_provider_scopes).issubset(
            binding.confirmed_scopes
        )
        or bytes(activation.policy_pack_sha256).hex() != plan.policy_sha256
        or bytes(policy.canonical_sha256).hex() != plan.policy_sha256
        or bytes(activation.executable_sha256) != bytes(manifest.executable_sha256)
        or bytes(activation.sandbox_profile_sha256)
        != bytes(manifest.sandbox_profile_sha256)
        or manifest_payload.operation != step.operation
        or manifest_payload.effect_class != "read"
        or manifest_payload.input_schema_sha256 != step.input_schema_sha256
        or manifest_payload.output_schema_sha256 != step.output_schema_sha256
        or manifest_payload.proof_floor != step.proof_floor
        or manifest_payload.maximum_freshness_ms != intent.maximum_freshness_ms
        or canonical_sha256(manifest_payload) != bytes(manifest.payload_sha256).hex()
        or binding_payload.status != "verified"
        or canonical_sha256(binding_payload) != bytes(binding.payload_sha256).hex()
    ):
        raise corrupt("prepared read capability binding differs")

    prepared_event_data = {
        "capability_activation_id": str(intent_row.capability_activation_id),
        "capability_manifest_id": str(activation.capability_manifest_id),
        "catalog_sha256": authority_snapshot["catalog_sha256"],
        "claim_token_sha256": claim_sha256,
        "connector_binding_id": str(activation.connector_binding_id),
        "logical_step_key": step.step_id,
        "plan_id": str(plan_id),
        "plan_step_id": str(stored.plan_step_id),
        "query_sha256": expected_query_sha256,
        "read_intent_id": str(expected_intent_id),
        "read_intent_payload_sha256": canonical_sha256(intent),
    }
    attempt_rows = connection.execute(
        sa.select(read_attempts)
        .where(
            read_attempts.c.tenant_id == scope.tenant_id,
            read_attempts.c.installation_id == scope.installation_id,
            read_attempts.c.read_intent_id == expected_intent_id,
        )
        .order_by(read_attempts.c.ordinal)
    ).all()
    if not attempt_rows:
        if current_event_data != prepared_event_data:
            raise corrupt("prepared read timeline binding differs")
        return None
    if len(attempt_rows) != 1:
        raise corrupt("authorized read attempt cardinality differs")
    return validate_authorized_action_chain_read(
        connection,
        scope,
        plan=plan,
        intent=intent,
        intent_row=intent_row,
        action_arguments=action.arguments,
        manifest=manifest_payload,
        binding=binding_payload,
        capability_manifest_id=activation.capability_manifest_id,
        attempt_row=attempt_rows[0],
        current_event_data=current_event_data,
        operation_id=action_chain_step_operation_id(plan, step.step_id),
        retention_policy_id=plan_row.retention_policy_id,
        retention_until=plan_row.retention_until,
        allow_terminal=True,
        corrupt=corrupt,
    )


def validate_authorized_action_chain_read(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    intent: ReadIntent,
    intent_row: sa.Row,
    action_arguments: dict[str, Any],
    manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_manifest_id: UUID,
    attempt_row: sa.Row,
    current_event_data: dict[str, Any],
    operation_id: str,
    retention_policy_id: str,
    retention_until: datetime,
    allow_terminal: bool,
    corrupt: type[RuntimeError],
) -> ReadAttempt:
    try:
        attempt = ReadAttempt.model_validate(attempt_row.payload)
        capability_grant = CapabilityGrant.model_validate(
            attempt_row.capability_grant_payload
        )
        credential_grant = CredentialGrant.model_validate(
            attempt_row.credential_grant_payload
        )
    except ValueError as error:
        raise corrupt("authorized read payload is invalid") from error
    expected_attempt_id = uuid5(UUID(intent.read_intent_id), "read-attempt:1")
    expected_capability_grant_id = uuid5(expected_attempt_id, "capability-grant:v1")
    expected_credential_grant_id = uuid5(expected_attempt_id, "credential-grant:v1")
    authorized_attempt = attempt.model_copy(
        update={"state": "dispatch_authorized", "response_sha256": None}
    )
    process_spec_sha256 = _worker_process_sha256(
        credential_grant.worker_id, corrupt=corrupt
    )
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": str(uuid5(expected_attempt_id, "connector-invoke:v1")),
            "correlation_id": str(uuid5(expected_attempt_id, "correlation:v1")),
            "episode_id": plan.episode_id,
            "generation": plan.generation,
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
    initialize = ConnectorInitializeRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInitializeRequest/v1",
            "jsonrpc": "2.0",
            "id": str(uuid5(expected_attempt_id, "connector-initialize:v1")),
            "method": "connector.initialize",
            "params": {
                "protocol_version": "EvaConnectorProtocol/v1",
                "request_id": str(
                    uuid5(expected_attempt_id, "connector-initialize:v1")
                ),
                "correlation_id": envelope.correlation_id,
                "connector_id": binding.provider_id,
                "connector_manifest_sha256": attempt.connector_manifest_sha256,
                "worker_id": credential_grant.worker_id,
                "worker_executable_sha256": manifest.executable_sha256,
                "connector_binding": binding.model_dump(mode="json"),
                "capability_manifest": manifest.model_dump(mode="json"),
                "capability_grant": capability_grant.model_dump(mode="json"),
                "credential_grant": credential_grant.model_dump(mode="json"),
                "deadline": intent.deadline,
            },
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
    if (
        attempt.read_attempt_id != str(expected_attempt_id)
        or attempt.read_intent_id != intent.read_intent_id
        or attempt.ordinal != 1
        or attempt.state
        not in {"dispatch_authorized", "observed", "ambiguous", "rejected", "rate_limited"}
        or attempt.capability_grant_id != str(expected_capability_grant_id)
        or attempt.credential_grant_id != str(expected_credential_grant_id)
        or attempt.request_sha256 != canonical_sha256(envelope)
        or attempt.deadline != intent.deadline
        or attempt.created_at != _format_timestamp(attempt_row.created_at)
        or capability_grant.grant_id != str(expected_capability_grant_id)
        or capability_grant.tenant_id != str(scope.tenant_id)
        or capability_grant.episode_id != plan.episode_id
        or capability_grant.generation != plan.generation
        or capability_grant.capability_id != intent.capability_id
        or capability_grant.connector_binding_id != binding.binding_id
        or capability_grant.operation != intent.operation
        or capability_grant.resource_scope != intent.resource_scope
        or capability_grant.authorized_payload_sha256 != intent.query_sha256
        or capability_grant.policy_sha256 != plan.policy_sha256
        or capability_grant.nonce
        != str(uuid5(expected_attempt_id, "capability-nonce:v1"))
        or capability_grant.issued_at != attempt.created_at
        or capability_grant.deadline != intent.deadline
        or credential_grant.credential_grant_id
        != str(expected_credential_grant_id)
        or credential_grant.connector_binding_id != binding.binding_id
        or credential_grant.model_provider_profile_id is not None
        or credential_grant.executable_sha256 != manifest.executable_sha256
        or credential_grant.provider_scopes != manifest.required_provider_scopes
        or credential_grant.secret_descriptor_id != binding.credential_reference
        or credential_grant.nonce
        != str(uuid5(expected_attempt_id, "credential-nonce:v1"))
        or credential_grant.issued_at != attempt.created_at
        or credential_grant.deadline != intent.deadline
        or attempt_row.read_attempt_id != expected_attempt_id
        or attempt_row.read_intent_id != intent_row.read_intent_id
        or int(attempt_row.ordinal) != 1
        or attempt_row.state != attempt.state
        or attempt_row.capability_grant_id != expected_capability_grant_id
        or attempt_row.credential_grant_id != expected_credential_grant_id
        or bytes(attempt_row.capability_grant_sha256).hex()
        != canonical_sha256(capability_grant)
        or bytes(attempt_row.credential_grant_sha256).hex()
        != canonical_sha256(credential_grant)
        or attempt_row.capability_grant_nonce != capability_grant.nonce
        or attempt_row.credential_grant_nonce != credential_grant.nonce
        or attempt_row.capability_grant_deadline != _parse_timestamp(intent.deadline)
        or attempt_row.credential_grant_deadline != _parse_timestamp(intent.deadline)
        or bytes(attempt_row.connector_manifest_sha256).hex()
        != attempt.connector_manifest_sha256
        or bytes(attempt_row.request_sha256).hex() != attempt.request_sha256
        or (
            None
            if attempt_row.response_sha256 is None
            else bytes(attempt_row.response_sha256).hex()
        )
        != attempt.response_sha256
        or attempt_row.deadline != _parse_timestamp(intent.deadline)
        or bytes(attempt_row.payload_sha256).hex() != canonical_sha256(attempt)
        or int(attempt_row.revision)
        != (1 if attempt.state == "dispatch_authorized" else 2)
        or attempt_row.retention_policy_id != retention_policy_id
        or attempt_row.retention_until != retention_until
        or (
            attempt.state == "dispatch_authorized"
            and attempt_row.created_at != attempt_row.updated_at
        )
        or attempt_row.updated_at < attempt_row.created_at
    ):
        raise corrupt("authorized read binding differs")
    observation_count = connection.scalar(
        sa.select(sa.func.count())
        .select_from(read_observations)
        .where(
            read_observations.c.tenant_id == scope.tenant_id,
            read_observations.c.installation_id == scope.installation_id,
            read_observations.c.read_attempt_id == expected_attempt_id,
        )
    )
    if attempt.state == "dispatch_authorized" and observation_count:
        raise corrupt("authorized undispatched read has an observation")
    profile_revision = current_event_data.get("provider_profile_revision")
    registry_sha256 = current_event_data.get("provider_registry_sha256")
    release_manifest_sha256 = current_event_data.get("release_manifest_sha256")
    release_provenance_sha256 = current_event_data.get(
        "release_provenance_sha256"
    )
    release_verification_receipt_sha256 = current_event_data.get(
        "release_verification_receipt_sha256"
    )
    if (
        not isinstance(profile_revision, int)
        or profile_revision < 1
        or not _is_sha256(registry_sha256)
        or not _is_sha256(release_manifest_sha256)
        or not _is_sha256(release_provenance_sha256)
        or not _is_sha256(release_verification_receipt_sha256)
    ):
        raise corrupt("authorized read provider authority is invalid")
    profile_sha256 = canonical_sha256(
        {
            "contract": "ReadProviderProfile/v1",
            "capability_manifest_id": str(capability_manifest_id),
            "revision": profile_revision,
            "connector_manifest_sha256": attempt.connector_manifest_sha256,
            "process_spec_sha256": process_spec_sha256,
        }
    )
    expected_event_data = {
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "connector_manifest_sha256": attempt.connector_manifest_sha256,
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "process_spec_sha256": process_spec_sha256,
        "provider_profile_revision": profile_revision,
        "provider_profile_sha256": profile_sha256,
        "provider_registry_sha256": registry_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "release_provenance_sha256": release_provenance_sha256,
        "release_verification_receipt_sha256": (
            release_verification_receipt_sha256
        ),
        "read_attempt_id": attempt.read_attempt_id,
        "read_attempt_payload_sha256": canonical_sha256(authorized_attempt),
        "read_intent_id": attempt.read_intent_id,
        "request_sha256": attempt.request_sha256,
    }
    latest = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()
    checkpoint = connection.execute(
        sa.select(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == operation_id,
        )
    ).one()
    if latest.event_type == "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED":
        if attempt.state != "dispatch_authorized":
            raise corrupt("terminal read still has an authorization event")
        expected_phase = "action_chain_read_dispatch_authorized"
        expected_updated_at = attempt_row.created_at
    elif latest.event_type == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED":
        if attempt.state != "dispatch_authorized":
            raise corrupt("terminal read still has a call-start event")
        expected_phase = "action_chain_read_provider_call_started"
        expected_updated_at = checkpoint.updated_at
        started_at = current_event_data.get("provider_call_started_at")
        if (
            started_at != _format_timestamp(checkpoint.updated_at)
            or checkpoint.updated_at < attempt_row.created_at
            or checkpoint.updated_at >= attempt_row.deadline
        ):
            raise corrupt("authorized read provider-call time differs")
        expected_event_data["provider_call_started_at"] = started_at
    elif latest.event_type in {
        "ACTION_CHAIN_READ_OBSERVED",
        "ACTION_CHAIN_READ_AMBIGUOUS",
        "ACTION_CHAIN_READ_REJECTED",
        "ACTION_CHAIN_READ_RATE_LIMITED",
    }:
        if not allow_terminal:
            raise corrupt("effect verification read cannot be terminal here")
        if attempt.state == "dispatch_authorized":
            raise corrupt("authorized read has a terminal result event")
        started_at = current_event_data.get("provider_call_started_at")
        try:
            started = _parse_timestamp(str(started_at))
        except ValueError as error:
            raise corrupt("terminal read call-start time is invalid") from error
        if (
            started < attempt_row.created_at
            or started >= attempt_row.deadline
            or started > checkpoint.updated_at
        ):
            raise corrupt("terminal read call-start time differs")
        expected_event_data["provider_call_started_at"] = started_at
        validate_action_chain_read_terminal(
            connection,
            scope,
            authorized_attempt=authorized_attempt,
            initialize=initialize,
            invoke=invoke,
            attempt_row=attempt_row,
            current_event_data=current_event_data,
            expected_event_data=expected_event_data,
            latest=latest,
            checkpoint=checkpoint,
            retention_policy_id=retention_policy_id,
            retention_until=retention_until,
            corrupt=corrupt,
        )
        return attempt
    else:
        raise corrupt("authorized read timeline event differs")
    if (
        current_event_data != expected_event_data
        or checkpoint.phase != expected_phase
        or checkpoint.status != "waiting_for_proof"
        or checkpoint.updated_at != expected_updated_at
    ):
        raise corrupt("authorized read timeline binding differs")
    return attempt


def _prepared_claim(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    plan_id: UUID,
    plan_step_id: UUID,
    logical_step_key: str,
    corrupt: type[RuntimeError],
) -> tuple[str, datetime]:
    root_thread_id = action_chain_thread_id(plan)
    child_thread_id = action_chain_step_operation_id(plan, logical_step_key)
    child_prepared = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == child_thread_id,
            operational_events.c.event_type == "ACTION_CHAIN_READ_INTENT_PREPARED",
        )
    ).one_or_none()
    if child_prepared is None:
        raise corrupt("prepared read child event is absent")
    prepared = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.event_type == "ACTION_CHAIN_READ_INTENT_PREPARED",
            operational_events.c.occurred_at == child_prepared.occurred_at,
        )
    ).one_or_none()
    if prepared is None:
        raise corrupt("prepared read root event is absent")
    claimed = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.sequence == int(prepared.sequence) - 1,
        )
    ).one_or_none()
    rows = (prepared, claimed)
    if (
        claimed is None
        or prepared.event_type != "ACTION_CHAIN_READ_INTENT_PREPARED"
        or claimed.event_type != "ACTION_CHAIN_CONTINUATION_CLAIMED"
        or int(prepared.sequence) != int(claimed.sequence) + 1
        or prepared.occurred_at != child_prepared.occurred_at
    ):
        raise corrupt("prepared read lease timeline differs")
    for event in rows:
        if (
            event.operation_id != root_thread_id
            or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
            or set(event.payload) != {"checkpoint", "data"}
        ):
            raise corrupt("prepared read lease event differs")
    claimed_data = claimed.payload.get("data")
    if not isinstance(claimed_data, dict):
        raise corrupt("prepared read claim data is absent")
    attention_payload = claimed_data.get("attention")
    if not isinstance(attention_payload, dict):
        raise corrupt("prepared read claim attention is absent")
    try:
        attention = AttentionWake.model_validate(attention_payload)
    except ValueError as error:
        raise corrupt("prepared read claim attention is invalid") from error
    expected_attention_id = uuid5(plan_id, "continuation:1")
    if (
        set(claimed_data)
        != {
            "attention",
            "attention_payload_sha256",
            "attention_revision",
            "plan_id",
        }
        or attention.attention_id != str(expected_attention_id)
        or attention.state != "claimed"
        or attention.lease_owner is None
        or attention.lease_expires_at is None
        or claimed_data.get("attention_payload_sha256")
        != canonical_sha256(attention)
        or claimed_data.get("attention_revision") != claimed.sequence
        or claimed_data.get("plan_id") != str(plan_id)
    ):
        raise corrupt("prepared read claim binding differs")
    return (
        canonical_sha256(
            {
                "attention_id": str(expected_attention_id),
                "attention_revision": claimed_data["attention_revision"],
                "lease_expires_at": attention.lease_expires_at,
                "logical_step_key": logical_step_key,
                "plan_id": str(plan_id),
                "plan_step_id": str(plan_step_id),
                "worker_id": attention.lease_owner,
            }
        ),
        prepared.occurred_at,
    )


def _worker_process_sha256(worker_id: str, *, corrupt: type[RuntimeError]) -> str:
    prefix = "connector-worker:"
    value = worker_id[len(prefix) :] if worker_id.startswith(prefix) else ""
    if not _is_sha256(value):
        raise corrupt("authorized read worker pin is invalid")
    return value


def _read_invoke_method(intent: ReadIntent) -> str:
    return {
        "effect.reconcile": "effect.reconcile",
        "effect.readback": "effect.readback",
        "connector.probe": "connector.probe",
    }.get(intent.operation, "resource.read")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )






__all__ = [
    "validate_action_chain_read",
    "validate_authorized_action_chain_read",
]
