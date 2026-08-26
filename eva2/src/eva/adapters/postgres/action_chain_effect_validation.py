"""Corruption-sensitive reload validation for prepared action-chain effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effect_result_validation import (
    validate_action_chain_effect_history,
)
from eva.adapters.postgres.action_chain_effect_state_validation import (
    reconstruct_action_chain_effect_state,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.application.effect_verification import (
    EffectVerificationError,
    EffectVerificationProjection,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    EffectIntent,
)
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ModelDecision, ValidatedPlan


capability_activations = metadata.tables["eva_core.capability_activations"]
capability_manifests = metadata.tables["eva_core.capability_manifests"]
connector_bindings = metadata.tables["eva_core.connector_bindings"]
policy_pack_versions = metadata.tables["eva_core.policy_pack_versions"]
effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_receipts = metadata.tables["eva_effects.effect_receipts"]
effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]


def validate_action_chain_effect(
    connection: sa.Connection,
    scope: TimelineScope,
    plan_id: UUID,
    plan: ValidatedPlan,
    decision: ModelDecision,
    authority_snapshot: dict[str, Any],
    plan_row: sa.Row,
    step: Any,
    stored: sa.Row,
    current_event_data: dict[str, Any],
    corrupt: type[RuntimeError],
) -> None:
    """Require one prepared effect to remain exactly bound and undispatched."""

    intent_row = connection.execute(
        sa.select(effect_intents).where(
            effect_intents.c.tenant_id == scope.tenant_id,
            effect_intents.c.installation_id == scope.installation_id,
            effect_intents.c.effect_intent_id == stored.effect_intent_id,
        )
    ).one_or_none()
    if intent_row is None:
        raise corrupt("prepared effect intent is absent")
    try:
        intent = EffectIntent.model_validate(intent_row.payload)
    except ValueError as error:
        raise corrupt("prepared effect intent is invalid") from error
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
        raise corrupt("prepared effect authority is absent")
    try:
        verification = EffectVerificationProjection.from_authority_payload(
            descriptor.get("effect_verification")
        )
    except EffectVerificationError as error:
        raise corrupt("prepared effect verification authority is invalid") from error

    expected_intent_id = uuid5(stored.plan_step_id, "effect-intent:v1")
    expected_idempotency_key = f"action-chain:{expected_intent_id}:effect:v1"
    expected_payload_sha256 = canonical_sha256(action.arguments)
    expected_scope_base = {
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
    prepared_intent = EffectIntent.model_validate(
        {**intent.model_dump(mode="json"), "state": "prepared"}
    )
    if (
        stored.effect_intent_id != expected_intent_id
        or intent.effect_intent_id != str(expected_intent_id)
        or intent.episode_id != plan.episode_id
        or intent.generation != plan.generation
        or intent.plan_step_id != str(stored.plan_step_id)
        or intent.connector_binding_id != str(intent_row.connector_binding_id)
        or intent.operation != step.operation
        or intent.resource_scope.resource_id != action.resource
        or intent.resource_scope.scope_sha256 != canonical_sha256(expected_scope_base)
        or intent.frozen_payload_sha256 != expected_payload_sha256
        or intent.idempotency_key != expected_idempotency_key
        or intent.proof_floor != step.proof_floor
        or intent.state
        not in {
            "prepared",
            "cancelled",
            "dispatch_authorized",
            "provider_accepted",
            "provider_rejected",
            "ambiguous",
            "waiting_breaker",
            "blocked",
            "verified",
        }
        or intent.supersedes_effect_intent_id is not None
        or intent.deadline != plan.deadline
        or canonical_sha256(intent) != bytes(intent_row.payload_sha256).hex()
        or intent_row.episode_id != plan_row.episode_id
        or int(intent_row.generation) != plan.generation
        or intent_row.logical_effect_key != f"action-chain:{stored.plan_step_id}:effect"
        or intent_row.plan_step_id != stored.plan_step_id
        or intent_row.connector_binding_id != UUID(intent.connector_binding_id)
        or intent_row.capability_id != intent.capability_id
        or intent_row.operation != intent.operation
        or intent_row.resource_scope != intent.resource_scope.model_dump(mode="json")
        or bytes(intent_row.resource_scope_sha256).hex()
        != intent.resource_scope.scope_sha256
        or bytes(intent_row.frozen_payload_sha256).hex() != expected_payload_sha256
        or intent_row.idempotency_key != expected_idempotency_key
        or intent_row.proof_floor != intent.proof_floor
        or intent_row.state != intent.state
        or intent_row.supersedes_effect_intent_id is not None
        or intent_row.consent_grant_id is not None
        or bytes(intent_row.policy_pack_sha256).hex() != plan.policy_sha256
        or intent_row.deadline != _parse_timestamp(intent.deadline)
        or intent_row.retention_policy_id != plan_row.retention_policy_id
        or intent_row.retention_until != plan_row.retention_until
        or intent_row.created_at != prepared_at
        or intent_row.updated_at < prepared_at
    ):
        raise corrupt("prepared effect intent binding differs")

    activation = connection.execute(
        sa.select(capability_activations).where(
            capability_activations.c.tenant_id == scope.tenant_id,
            capability_activations.c.installation_id == scope.installation_id,
            capability_activations.c.activation_id
            == intent_row.capability_activation_id,
        )
    ).one_or_none()
    manifest_row = _authority_row(
        connection,
        capability_manifests,
        scope,
        "capability_manifest_id",
        None if activation is None else activation.capability_manifest_id,
    )
    binding_row = _authority_row(
        connection,
        connector_bindings,
        scope,
        "binding_id",
        None if activation is None else activation.connector_binding_id,
    )
    policy_row = (
        None
        if activation is None
        else connection.execute(
            sa.select(policy_pack_versions).where(
                policy_pack_versions.c.tenant_id == scope.tenant_id,
                policy_pack_versions.c.installation_id == scope.installation_id,
                policy_pack_versions.c.policy_id == activation.policy_id,
                policy_pack_versions.c.policy_version == activation.policy_pack_version,
            )
        ).one_or_none()
    )
    try:
        manifest = CapabilityManifest.model_validate(
            None if manifest_row is None else manifest_row.payload
        )
        binding = ConnectorBinding.model_validate(
            None if binding_row is None else binding_row.payload
        )
    except ValueError as error:
        raise corrupt("prepared effect capability payload is invalid") from error
    if (
        activation is None
        or manifest_row is None
        or binding_row is None
        or policy_row is None
        or activation.state != "active"
        or manifest_row.state != "active"
        or binding_row.status != "verified"
        or policy_row.state != "current"
        or activation.connector_binding_id != binding_row.binding_id
        or intent.connector_binding_id != str(binding_row.binding_id)
        or activation.scope != intent.resource_scope.model_dump(mode="json")
        or bytes(activation.scope_sha256).hex() != intent.resource_scope.scope_sha256
        or activation.capability_id != manifest.capability_id
        or intent.capability_id != activation.capability_id
        or activation.operation != manifest.operation
        or activation.operation != step.operation
        or manifest_row.capability_id != manifest.capability_id
        or manifest_row.connector_id != manifest.connector_id
        or manifest_row.operation != manifest.operation
        or manifest.connector_id != binding.provider_id
        or manifest.effect_class != "effect"
        or manifest.maximum_freshness_ms is not None
        or manifest.input_schema_sha256 != step.input_schema_sha256
        or manifest.output_schema_sha256 != step.output_schema_sha256
        or manifest.proof_floor != step.proof_floor
        or manifest.input_schema_sha256 != descriptor.get("input_schema_sha256")
        or manifest.output_schema_sha256 != descriptor.get("output_schema_sha256")
        or manifest.proof_floor != descriptor.get("proof_floor")
        or step.operation not in binding.confirmed_operations
        or intent.resource_scope.resource_type not in binding.confirmed_scopes
        or not set(manifest.required_provider_scopes).issubset(binding.confirmed_scopes)
        or bytes(activation.policy_pack_sha256).hex() != plan.policy_sha256
        or bytes(policy_row.canonical_sha256).hex() != plan.policy_sha256
        or bytes(activation.executable_sha256) != bytes(manifest_row.executable_sha256)
        or bytes(activation.sandbox_profile_sha256)
        != bytes(manifest_row.sandbox_profile_sha256)
        or canonical_sha256(manifest) != bytes(manifest_row.payload_sha256).hex()
        or canonical_sha256(binding) != bytes(binding_row.payload_sha256).hex()
        or canonical_sha256(policy_row.payload)
        != bytes(policy_row.payload_sha256).hex()
        or bytes(policy_row.canonical_sha256) != bytes(policy_row.payload_sha256)
    ):
        raise corrupt("prepared effect capability binding differs")

    expected_event_data = {
        "capability_activation_id": str(activation.activation_id),
        "capability_manifest_id": str(activation.capability_manifest_id),
        "catalog_sha256": authority_snapshot["catalog_sha256"],
        "claim_token_sha256": claim_sha256,
        "connector_binding_id": str(binding_row.binding_id),
        "effect_intent_id": str(expected_intent_id),
        "effect_intent_payload_sha256": canonical_sha256(prepared_intent),
        "frozen_payload_sha256": expected_payload_sha256,
        "idempotency_key": expected_idempotency_key,
        "logical_step_key": step.step_id,
        "plan_id": str(plan_id),
        "plan_step_id": str(stored.plan_step_id),
    }
    attempts = connection.execute(
        sa.select(effect_attempts)
        .where(
            effect_attempts.c.tenant_id == scope.tenant_id,
            effect_attempts.c.installation_id == scope.installation_id,
            effect_attempts.c.effect_intent_id == expected_intent_id,
        )
        .order_by(effect_attempts.c.ordinal)
    ).all()
    external_counts = tuple(
        connection.scalar(
            sa.select(sa.func.count())
            .select_from(table)
            .where(
                table.c.tenant_id == scope.tenant_id,
                table.c.installation_id == scope.installation_id,
                table.c.effect_intent_id == expected_intent_id,
            )
        )
        for table in (effect_receipts, effect_readbacks)
    )
    if not attempts:
        if intent.state == "cancelled":
            if (
                int(intent_row.revision) != 2
                or intent_row.updated_at < prepared_at
                or current_event_data.get("effect_intent_id")
                != str(expected_intent_id)
                or current_event_data.get("effect_intent_payload_sha256")
                != canonical_sha256(intent)
                or current_event_data.get("logical_step_key") != step.step_id
                or current_event_data.get("plan_id") != str(plan_id)
                or current_event_data.get("plan_step_id")
                != str(stored.plan_step_id)
                or any(external_counts)
            ):
                raise corrupt("cancelled effect timeline binding differs")
            return
        if (
            intent.state != "prepared"
            or int(intent_row.revision) != 1
            or intent_row.updated_at != prepared_at
            or current_event_data != expected_event_data
            or any(external_counts)
        ):
            raise corrupt("prepared effect timeline binding differs")
        return
    if len(attempts) != 1:
        raise corrupt("authorized effect attempt cardinality differs")
    history_event_data = _effect_history_event_data(
        connection,
        scope,
        plan=plan,
        logical_step_key=step.step_id,
        intent=intent,
        current_event_data=current_event_data,
        corrupt=corrupt,
    )
    _validate_authorized_effect(
        connection,
        scope,
        plan=plan,
        plan_row=plan_row,
        step=step,
        intent=intent,
        intent_row=intent_row,
        action_arguments=action.arguments,
        action_resource=action.resource,
        effect_verification=verification,
        manifest=manifest,
        binding=binding,
        capability_manifest_id=activation.capability_manifest_id,
        attempt_row=attempts[0],
        external_counts=external_counts,
        current_event_data=history_event_data,
        corrupt=corrupt,
    )


def _validate_authorized_effect(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    plan_row: sa.Row,
    step: Any,
    intent: EffectIntent,
    intent_row: sa.Row,
    action_arguments: dict[str, Any],
    action_resource: str,
    effect_verification: EffectVerificationProjection,
    manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_manifest_id: UUID,
    attempt_row: sa.Row,
    external_counts: tuple[int, int],
    current_event_data: dict[str, Any],
    corrupt: type[RuntimeError],
) -> None:
    try:
        stored_attempt = EffectAttempt.model_validate(attempt_row.payload)
        capability_grant = CapabilityGrant.model_validate(
            attempt_row.capability_grant_payload
        )
        credential_grant = CredentialGrant.model_validate(
            attempt_row.credential_grant_payload
        )
    except ValueError as error:
        raise corrupt("authorized effect payload is invalid") from error
    expected_attempt_id = uuid5(UUID(intent.effect_intent_id), "effect-attempt:1")
    capability_grant_id = uuid5(expected_attempt_id, "capability-grant:v1")
    credential_grant_id = uuid5(expected_attempt_id, "credential-grant:v1")
    process_spec_sha256 = _worker_process_sha256(
        credential_grant.worker_id, corrupt=corrupt
    )
    try:
        envelope = ConnectorEnvelope.model_validate(
            {
                "schema_version": 1,
                "contract": "ConnectorEnvelope/v1",
                "installation_id": str(scope.installation_id),
                "tenant_id": str(scope.tenant_id),
                "request_id": intent.idempotency_key,
                "correlation_id": str(uuid5(expected_attempt_id, "correlation:v1")),
                "episode_id": plan.episode_id,
                "generation": plan.generation,
                "connector_id": binding.provider_id,
                "connector_binding_id": binding.binding_id,
                "capability_id": intent.capability_id,
                "operation": intent.operation,
                "resource_scope": intent.resource_scope.model_dump(mode="json"),
                "deadline": intent.deadline,
                "request_payload_sha256": intent.frozen_payload_sha256,
                "connector_manifest_sha256": (
                    stored_attempt.connector_manifest_sha256
                ),
                "capability_grant_id": capability_grant.grant_id,
                "credential_grant_id": credential_grant.credential_grant_id,
                "request_payload": action_arguments,
            }
        )
    except ValueError as error:
        raise corrupt("authorized effect envelope is invalid") from error
    profile_revision = current_event_data.get("provider_profile_revision")
    registry_sha256 = current_event_data.get("provider_registry_sha256")
    release_manifest_sha256 = current_event_data.get("release_manifest_sha256")
    provenance_sha256 = current_event_data.get("release_provenance_sha256")
    verification_sha256 = current_event_data.get(
        "release_verification_receipt_sha256"
    )
    if (
        not isinstance(profile_revision, int)
        or profile_revision < 1
        or any(
            not _is_sha256(value)
            for value in (
                registry_sha256,
                release_manifest_sha256,
                provenance_sha256,
                verification_sha256,
            )
        )
    ):
        raise corrupt("authorized effect provider authority is invalid")
    profile_sha256 = canonical_sha256(
        {
            "contract": "ReadProviderProfile/v1",
            "capability_manifest_id": str(capability_manifest_id),
            "revision": profile_revision,
            "connector_manifest_sha256": stored_attempt.connector_manifest_sha256,
            "process_spec_sha256": process_spec_sha256,
        }
    )
    permit = {
        "schema_version": 1,
        "contract": "EffectDispatchPermit/v1",
        "effect_attempt_id": str(expected_attempt_id),
        "effect_intent_id": intent.effect_intent_id,
        "idempotency_key": intent.idempotency_key,
        "request_sha256": canonical_sha256(envelope),
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "connector_manifest_sha256": stored_attempt.connector_manifest_sha256,
        "process_spec_sha256": process_spec_sha256,
        "provider_profile_sha256": profile_sha256,
        "provider_registry_sha256": registry_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "release_provenance_sha256": provenance_sha256,
        "release_verification_receipt_sha256": verification_sha256,
        "issued_at": stored_attempt.created_at,
        "deadline": intent.deadline,
    }
    state = reconstruct_action_chain_effect_state(
        intent=intent,
        intent_row=intent_row,
        stored_attempt=stored_attempt,
        attempt_row=attempt_row,
        dispatch_permit_sha256=canonical_sha256(permit),
        external_counts=external_counts,
        corrupt=corrupt,
    )
    authorized_intent = state.intent
    attempt = state.attempt
    if (
        attempt.effect_attempt_id != str(expected_attempt_id)
        or attempt.effect_intent_id != intent.effect_intent_id
        or attempt.ordinal != 1
        or attempt.state != "dispatch_authorized"
        or attempt.dispatch_permit_sha256 != canonical_sha256(permit)
        or attempt.capability_grant_id != str(capability_grant_id)
        or attempt.credential_grant_id != str(credential_grant_id)
        or attempt.request_sha256 != canonical_sha256(envelope)
        or attempt.response_sha256 is not None
        or attempt.error_class != "none"
        or attempt.deadline != intent.deadline
        or attempt.created_at != _format_timestamp(attempt_row.created_at)
        or capability_grant.grant_id != str(capability_grant_id)
        or capability_grant.tenant_id != str(scope.tenant_id)
        or capability_grant.episode_id != plan.episode_id
        or capability_grant.generation != plan.generation
        or capability_grant.capability_id != intent.capability_id
        or capability_grant.connector_binding_id != binding.binding_id
        or capability_grant.operation != step.operation
        or capability_grant.resource_scope != intent.resource_scope
        or capability_grant.authorized_payload_sha256
        != intent.frozen_payload_sha256
        or capability_grant.policy_sha256 != plan.policy_sha256
        or capability_grant.nonce
        != str(uuid5(expected_attempt_id, "capability-nonce:v1"))
        or capability_grant.issued_at != attempt.created_at
        or capability_grant.deadline != intent.deadline
        or credential_grant.credential_grant_id != str(credential_grant_id)
        or credential_grant.connector_binding_id != binding.binding_id
        or credential_grant.model_provider_profile_id is not None
        or credential_grant.executable_sha256 != manifest.executable_sha256
        or credential_grant.provider_scopes != manifest.required_provider_scopes
        or credential_grant.secret_descriptor_id != binding.credential_reference
        or credential_grant.nonce
        != str(uuid5(expected_attempt_id, "credential-nonce:v1"))
        or credential_grant.issued_at != attempt.created_at
        or credential_grant.deadline != intent.deadline
        or attempt_row.effect_attempt_id != expected_attempt_id
        or attempt_row.effect_intent_id != intent_row.effect_intent_id
        or int(attempt_row.ordinal) != 1
        or attempt_row.capability_grant_id != capability_grant_id
        or attempt_row.credential_grant_id != credential_grant_id
        or bytes(attempt_row.capability_grant_sha256).hex()
        != canonical_sha256(capability_grant)
        or bytes(attempt_row.credential_grant_sha256).hex()
        != canonical_sha256(credential_grant)
        or attempt_row.capability_grant_nonce != capability_grant.nonce
        or attempt_row.credential_grant_nonce != credential_grant.nonce
        or attempt_row.capability_grant_deadline
        != _parse_timestamp(intent.deadline)
        or attempt_row.credential_grant_deadline
        != _parse_timestamp(intent.deadline)
        or attempt_row.capability_grant_payload
        != capability_grant.model_dump(mode="json")
        or attempt_row.credential_grant_payload
        != credential_grant.model_dump(mode="json")
        or bytes(attempt_row.connector_manifest_sha256).hex()
        != attempt.connector_manifest_sha256
        or bytes(attempt_row.request_sha256).hex() != attempt.request_sha256
        or attempt_row.object_store_id is not None
        or attempt_row.object_candidate_id is not None
        or attempt_row.object_candidate_ciphertext_sha256 is not None
        or attempt_row.object_candidate_envelope_byte_length is not None
        or attempt_row.object_candidate_temp_name_sha256 is not None
        or attempt_row.object_candidate_state is not None
        or attempt_row.deadline != _parse_timestamp(intent.deadline)
        or attempt_row.retention_policy_id != plan_row.retention_policy_id
        or attempt_row.retention_until != plan_row.retention_until
    ):
        raise corrupt("authorized effect binding differs")
    expected_event_data = {
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "connector_manifest_sha256": attempt.connector_manifest_sha256,
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "dispatch_permit_sha256": canonical_sha256(permit),
        "effect_attempt_id": attempt.effect_attempt_id,
        "effect_attempt_payload_sha256": canonical_sha256(attempt),
        "effect_intent_id": attempt.effect_intent_id,
        "effect_intent_payload_sha256": canonical_sha256(authorized_intent),
        "idempotency_key": authorized_intent.idempotency_key,
        "process_spec_sha256": process_spec_sha256,
        "provider_profile_revision": profile_revision,
        "provider_profile_sha256": profile_sha256,
        "provider_registry_sha256": registry_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "release_provenance_sha256": provenance_sha256,
        "release_verification_receipt_sha256": verification_sha256,
        "request_sha256": attempt.request_sha256,
    }
    validate_action_chain_effect_history(
        connection,
        scope,
        operation_id=action_chain_step_operation_id(plan, step.step_id),
        plan_id=plan_row.plan_id,
        plan_step_id=intent_row.plan_step_id,
        state=state,
        plan=plan,
        action_arguments=action_arguments,
        action_resource=action_resource,
        effect_verification=effect_verification,
        capability_manifest=manifest,
        binding=binding,
        capability_grant=capability_grant,
        credential_grant=credential_grant,
        intent_row=intent_row,
        attempt_row=attempt_row,
        expected_authorization_data=expected_event_data,
        retention_policy_id=plan_row.retention_policy_id,
        retention_until=plan_row.retention_until,
        corrupt=corrupt,
    )


def _effect_history_event_data(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    logical_step_key: str,
    intent: EffectIntent,
    current_event_data: dict[str, Any],
    corrupt: type[RuntimeError],
) -> dict[str, Any]:
    if intent.state != "verified":
        return current_event_data
    operation_id = action_chain_step_operation_id(plan, logical_step_key)
    rows = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(2)
    ).all()
    if (
        len(rows) != 2
        or rows[0].event_type != "STATE_VERIFIED"
        or rows[1].event_type != "ACTION_CHAIN_EFFECT_PROVIDER_ACCEPTED"
        or not isinstance(rows[1].payload.get("data"), dict)
    ):
        raise corrupt("verified effect provider history differs")
    return dict(rows[1].payload["data"])


def is_prepared_action_chain_effect(
    step: Any, stored: sa.Row, running_revision: int
) -> bool:
    """Recognize the sole effect state this validator currently owns."""

    return (
        step.kind == "effect"
        and stored.state == "running"
        and stored.read_intent_id is None
        and stored.effect_intent_id is not None
        and stored.outcome_ref is None
        and stored.revision == running_revision
        and stored.updated_at >= stored.created_at
    )


def is_cancelled_action_chain_effect(
    step: Any, stored: sa.Row, cancelled_revision: int
) -> bool:
    """Recognize a pre-dispatch cancellation without inventing a receipt."""

    return (
        step.kind == "effect"
        and stored.state == "cancelled"
        and stored.effect_intent_id is not None
        and stored.read_intent_id is None
        and stored.outcome_ref is None
        and stored.revision == cancelled_revision
        and stored.updated_at >= stored.created_at
    )


def is_completed_action_chain_effect(
    step: Any, stored: sa.Row, succeeded_revision: int
) -> bool:
    """Recognize an effect that earned success from physical readback."""

    return (
        step.kind == "effect"
        and stored.state == "succeeded"
        and stored.read_intent_id is None
        and stored.effect_intent_id is not None
        and isinstance(stored.outcome_ref, str)
        and stored.outcome_ref.startswith("effect-readback:")
        and stored.revision == succeeded_revision
        and stored.updated_at >= stored.created_at
    )


def _authority_row(
    connection: sa.Connection,
    table: sa.Table,
    scope: TimelineScope,
    key_name: str,
    key_value: UUID | None,
) -> sa.Row | None:
    if key_value is None:
        return None
    return connection.execute(
        sa.select(table).where(
            table.c.tenant_id == scope.tenant_id,
            table.c.installation_id == scope.installation_id,
            table.c[key_name] == key_value,
        )
    ).one_or_none()


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
    child = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == child_thread_id,
            operational_events.c.event_type == "ACTION_CHAIN_EFFECT_INTENT_PREPARED",
        )
    ).one_or_none()
    if child is None:
        raise corrupt("prepared effect child event is absent")
    prepared = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.event_type == "ACTION_CHAIN_EFFECT_INTENT_PREPARED",
            operational_events.c.occurred_at == child.occurred_at,
        )
    ).one_or_none()
    claimed = (
        None
        if prepared is None
        else connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == root_thread_id,
                operational_events.c.sequence == int(prepared.sequence) - 1,
            )
        ).one_or_none()
    )
    if (
        prepared is None
        or claimed is None
        or claimed.event_type != "ACTION_CHAIN_CONTINUATION_CLAIMED"
        or prepared.occurred_at != child.occurred_at
    ):
        raise corrupt("prepared effect lease timeline differs")
    for event in (prepared, claimed):
        if (
            event.operation_id != root_thread_id
            or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
            or set(event.payload) != {"checkpoint", "data"}
        ):
            raise corrupt("prepared effect lease event differs")
    data = claimed.payload.get("data")
    attention_payload = data.get("attention") if isinstance(data, dict) else None
    try:
        attention = AttentionWake.model_validate(attention_payload)
    except ValueError as error:
        raise corrupt("prepared effect claim attention is invalid") from error
    attention_id = uuid5(plan_id, "continuation:1")
    if (
        set(data)
        != {"attention", "attention_payload_sha256", "attention_revision", "plan_id"}
        or attention.attention_id != str(attention_id)
        or attention.state != "claimed"
        or attention.lease_owner is None
        or attention.lease_expires_at is None
        or data.get("attention_payload_sha256") != canonical_sha256(attention)
        or data.get("attention_revision") != claimed.sequence
        or data.get("plan_id") != str(plan_id)
    ):
        raise corrupt("prepared effect claim binding differs")
    return (
        canonical_sha256(
            {
                "attention_id": str(attention_id),
                "attention_revision": data["attention_revision"],
                "lease_expires_at": attention.lease_expires_at,
                "logical_step_key": logical_step_key,
                "plan_id": str(plan_id),
                "plan_step_id": str(plan_step_id),
                "worker_id": attention.lease_owner,
            }
        ),
        prepared.occurred_at,
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _worker_process_sha256(worker_id: str, *, corrupt: type[RuntimeError]) -> str:
    prefix = "connector-worker:"
    value = worker_id[len(prefix) :] if worker_id.startswith(prefix) else ""
    if not _is_sha256(value):
        raise corrupt("authorized effect worker pin is invalid")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "is_cancelled_action_chain_effect",
    "is_completed_action_chain_effect",
    "is_prepared_action_chain_effect",
    "validate_action_chain_effect",
]
