"""Corruption-sensitive reload validation for prepared action-chain effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import CapabilityManifest, ConnectorBinding, EffectIntent
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
        or intent.state != "prepared"
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
        or intent_row.state != "prepared"
        or intent_row.supersedes_effect_intent_id is not None
        or intent_row.consent_grant_id is not None
        or bytes(intent_row.policy_pack_sha256).hex() != plan.policy_sha256
        or intent_row.deadline != _parse_timestamp(intent.deadline)
        or int(intent_row.revision) != 1
        or intent_row.retention_policy_id != plan_row.retention_policy_id
        or intent_row.retention_until != plan_row.retention_until
        or intent_row.created_at != prepared_at
        or intent_row.updated_at != prepared_at
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
        "effect_intent_payload_sha256": canonical_sha256(intent),
        "frozen_payload_sha256": expected_payload_sha256,
        "idempotency_key": expected_idempotency_key,
        "logical_step_key": step.step_id,
        "plan_id": str(plan_id),
        "plan_step_id": str(stored.plan_step_id),
    }
    if current_event_data != expected_event_data:
        raise corrupt("prepared effect timeline binding differs")
    for table in (effect_attempts, effect_receipts, effect_readbacks):
        if connection.scalar(
            sa.select(sa.func.count())
            .select_from(table)
            .where(
                table.c.tenant_id == scope.tenant_id,
                table.c.installation_id == scope.installation_id,
                table.c.effect_intent_id == expected_intent_id,
            )
        ):
            raise corrupt("prepared effect already has external execution evidence")


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


__all__ = ["is_prepared_action_chain_effect", "validate_action_chain_effect"]
