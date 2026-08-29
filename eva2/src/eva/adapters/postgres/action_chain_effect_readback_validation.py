"""Corruption-sensitive validation for prepared effect verification reads."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp

from datetime import datetime
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_read_validation import (
    validate_authorized_action_chain_read,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import (
    CapabilityManifest,
    ConnectorBinding,
    EffectAttempt,
    EffectIntent,
    EffectReadback,
    EffectReceipt,
    ReadAttempt,
    ReadIntent,
    ReadObservation,
    ResourceScope,
)
from eva.contracts.planning import ValidatedPlan
from eva.application.action_chain_effect_readback_results import (
    project_action_chain_effect_readback,
)


capability_activations = metadata.tables["eva_core.capability_activations"]
capability_manifests = metadata.tables["eva_core.capability_manifests"]
connector_bindings = metadata.tables["eva_core.connector_bindings"]
policy_pack_versions = metadata.tables["eva_core.policy_pack_versions"]
read_intents = metadata.tables["eva_effects.read_intents"]
read_attempts = metadata.tables["eva_effects.read_attempts"]
read_observations = metadata.tables["eva_effects.read_observations"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_receipts = metadata.tables["eva_effects.effect_receipts"]
effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]


def validate_action_chain_effect_readback_preparation(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    effect_row: sa.Row,
    plan_id: UUID,
    plan_step_id: UUID,
    plan: ValidatedPlan,
    policy_sha256: str,
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    rows = verification_rows(connection, scope, effect_row.effect_intent_id)
    if not rows:
        return
    if len(rows) != 1:
        raise corrupt("effect verification intent cardinality differs")
    row = rows[0]
    try:
        effect, attempt, receipt = accepted_effect(connection, scope, effect_row)
        intent = ReadIntent.model_validate(row.payload)
        activation, manifest, binding = readback_authority(
            connection,
            scope,
            activation_id=row.capability_activation_id,
            binding_id=UUID(effect.connector_binding_id),
            resource_scope=effect.resource_scope,
            effect=effect,
            policy_sha256=policy_sha256,
        )
    except (TypeError, ValueError) as error:
        raise corrupt("effect verification authority is invalid") from error
    expected_id = uuid5(effect_row.effect_intent_id, "effect-readback-intent:v1")
    request_payload = {
        "idempotencyKey": effect.idempotency_key,
        "providerResourceId": receipt.provider_resource_id,
        "requestSha256": effect.frozen_payload_sha256,
    }
    expected = ReadIntent(
        schema_version=1,
        contract="ReadIntent/v1",
        read_intent_id=str(expected_id),
        episode_id=effect.episode_id,
        generation=effect.generation,
        purpose="effect_verify",
        effect_intent_id=effect.effect_intent_id,
        operation="effect.readback",
        resource_scope=effect.resource_scope.model_dump(mode="json"),
        query_sha256=canonical_sha256(request_payload),
        maximum_freshness_ms=0,
        maximum_attempts=2,
        capability_id=manifest.capability_id,
        deadline=effect.deadline,
    )
    if (
        intent != expected
        or row.read_intent_id != expected_id
        or row.episode_id != effect_row.episode_id
        or int(row.generation) != effect.generation
        or row.logical_read_key
        != f"action-chain:{effect_row.effect_intent_id}:readback"
        or row.plan_step_id != plan_step_id
        or row.purpose != "effect_verify"
        or row.effect_intent_id != effect_row.effect_intent_id
        or row.circuit_breaker_id is not None
        or row.operation != "effect.readback"
        or row.resource_scope != effect.resource_scope.model_dump(mode="json")
        or bytes(row.resource_scope_sha256).hex()
        != effect.resource_scope.scope_sha256
        or bytes(row.query_sha256).hex() != expected.query_sha256
        or int(row.maximum_freshness_ms) != 0
        or int(row.maximum_attempts) != 2
        or row.capability_id != manifest.capability_id
        or row.capability_activation_id != activation.activation_id
        or row.deadline != _parse_timestamp(effect.deadline)
        or bytes(row.payload_sha256).hex() != canonical_sha256(expected)
        or row.retention_policy_id != effect_row.retention_policy_id
        or row.retention_until != effect_row.retention_until
        or (
            effect_row.state == "provider_accepted"
            and row.created_at < effect_row.updated_at
        )
        or row.created_at >= row.deadline
    ):
        raise corrupt("effect verification intent binding differs")
    _validate_thread(
        connection,
        scope,
        intent=intent,
        intent_row=row,
        request_payload=request_payload,
        effect=effect,
        attempt=attempt,
        receipt=receipt,
        activation=activation,
        manifest=manifest,
        binding=binding,
        plan_id=plan_id,
        plan_step_id=plan_step_id,
        plan=plan,
        policy_sha256=policy_sha256,
        retention_policy_id=retention_policy_id,
        retention_until=retention_until,
        created_at=row.created_at,
        corrupt=corrupt,
    )


def accepted_effect(
    connection: sa.Connection,
    scope: TimelineScope,
    effect_row: sa.Row,
    *,
    lock: bool = False,
) -> tuple[EffectIntent, EffectAttempt, EffectReceipt]:
    stored_effect = EffectIntent.model_validate(effect_row.payload)
    effect = (
        EffectIntent.model_validate(
            {
                **stored_effect.model_dump(mode="json"),
                "state": "provider_accepted",
            }
        )
        if stored_effect.state == "verified"
        else stored_effect
    )
    attempt_statement = sa.select(effect_attempts).where(
        effect_attempts.c.tenant_id == scope.tenant_id,
        effect_attempts.c.installation_id == scope.installation_id,
        effect_attempts.c.effect_intent_id == effect_row.effect_intent_id,
    )
    receipt_statement = sa.select(effect_receipts).where(
        effect_receipts.c.tenant_id == scope.tenant_id,
        effect_receipts.c.installation_id == scope.installation_id,
        effect_receipts.c.effect_intent_id == effect_row.effect_intent_id,
    )
    if lock:
        attempt_statement = attempt_statement.with_for_update(read=True)
    attempts = connection.execute(attempt_statement).all()
    receipts = connection.execute(receipt_statement).all()
    if len(attempts) != 1 or len(receipts) != 1:
        raise ValueError("accepted effect proof is incomplete")
    attempt = EffectAttempt.model_validate(attempts[0].payload)
    receipt = EffectReceipt.model_validate(receipts[0].payload)
    if (
        effect.state != "provider_accepted"
        or effect_row.state not in {"provider_accepted", "verified"}
        or attempt.state != "provider_accepted"
        or receipt.decision != "accepted"
        or receipt.provider_resource_id is None
        or receipt.effect_attempt_id != attempt.effect_attempt_id
        or receipt.request_payload_sha256 != effect.frozen_payload_sha256
    ):
        raise ValueError("effect is not accepted for readback")
    return effect, attempt, receipt


def readback_authority(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    activation_id: UUID,
    binding_id: UUID,
    resource_scope: ResourceScope,
    effect: EffectIntent,
    policy_sha256: str,
    lock: bool = False,
) -> tuple[sa.Row, CapabilityManifest, ConnectorBinding]:
    activation = _row(
        connection,
        capability_activations,
        scope,
        "activation_id",
        activation_id,
        lock=lock,
    )
    manifest_row = _row(
        connection,
        capability_manifests,
        scope,
        "capability_manifest_id",
        None if activation is None else activation.capability_manifest_id,
        # Release manifests are immutable and intentionally not runtime-lockable.
        lock=False,
    )
    binding_row = _row(
        connection, connector_bindings, scope, "binding_id", binding_id, lock=lock
    )
    policy_statement = (
        None
        if activation is None
        else sa.select(policy_pack_versions).where(
            policy_pack_versions.c.tenant_id == scope.tenant_id,
            policy_pack_versions.c.installation_id == scope.installation_id,
            policy_pack_versions.c.policy_id == activation.policy_id,
            policy_pack_versions.c.policy_version
            == activation.policy_pack_version,
        )
    )
    if policy_statement is not None and lock:
        policy_statement = policy_statement.with_for_update(read=True)
    policy = (
        None
        if policy_statement is None
        else connection.execute(policy_statement).one_or_none()
    )
    if (
        activation is None
        or manifest_row is None
        or binding_row is None
        or policy is None
    ):
        raise ValueError("effect readback authority is incomplete")
    manifest = CapabilityManifest.model_validate(manifest_row.payload)
    binding = ConnectorBinding.model_validate(binding_row.payload)
    if (
        activation.state != "active"
        or manifest_row.state != "active"
        or binding_row.status != "verified"
        or binding.status != "verified"
        or policy.state != "current"
        or activation.capability_manifest_id
        != manifest_row.capability_manifest_id
        or activation.connector_binding_id != binding_row.binding_id
        or binding.binding_id != effect.connector_binding_id
        or binding.binding_id != str(binding_id)
        or binding.binding_id != str(binding_row.binding_id)
        or binding.installation_id != str(binding_row.installation_id)
        or binding.tenant_id != str(binding_row.tenant_id)
        or binding.owner_principal_id != str(binding_row.owner_principal_id)
        or binding.provider_id != binding_row.provider_id
        or binding.provider_account_id != binding_row.provider_account_id
        or binding.provider_resource_id != binding_row.provider_resource_id
        or binding.confirmed_operations != list(binding_row.confirmed_operations)
        or binding.confirmed_scopes != list(binding_row.confirmed_scopes)
        or binding.credential_reference != binding_row.credential_reference
        or manifest_row.capability_id != manifest.capability_id
        or manifest_row.connector_id != manifest.connector_id
        or manifest_row.operation != manifest.operation
        or bytes(manifest_row.input_schema_sha256).hex()
        != manifest.input_schema_sha256
        or bytes(manifest_row.output_schema_sha256).hex()
        != manifest.output_schema_sha256
        or manifest_row.effect_class != manifest.effect_class
        or list(manifest_row.required_provider_scopes)
        != manifest.required_provider_scopes
        or manifest_row.proof_floor != manifest.proof_floor
        or manifest_row.maximum_freshness_ms != manifest.maximum_freshness_ms
        or manifest.connector_id != binding.provider_id
        or manifest.operation != "effect.readback"
        or manifest.effect_class != "verification_read"
        or manifest.maximum_freshness_ms != 0
        or activation.capability_id != manifest.capability_id
        or activation.operation != manifest.operation
        or activation.connector_binding_id != binding_id
        or activation.scope != resource_scope.model_dump(mode="json")
        or bytes(activation.scope_sha256).hex() != resource_scope.scope_sha256
        or resource_scope != effect.resource_scope
        or "effect.readback" not in binding.confirmed_operations
        or not set(manifest.required_provider_scopes).issubset(
            binding.confirmed_scopes
        )
        or bytes(activation.policy_pack_sha256).hex() != policy_sha256
        or bytes(policy.canonical_sha256).hex() != policy_sha256
        or bytes(activation.executable_sha256) != bytes(manifest_row.executable_sha256)
        or bytes(activation.sandbox_profile_sha256)
        != bytes(manifest_row.sandbox_profile_sha256)
        or canonical_sha256(manifest) != bytes(manifest_row.payload_sha256).hex()
        or canonical_sha256(binding) != bytes(binding_row.payload_sha256).hex()
        or canonical_sha256(policy.payload) != bytes(policy.payload_sha256).hex()
        or bytes(policy.canonical_sha256) != bytes(policy.payload_sha256)
    ):
        raise ValueError("effect readback authority differs")
    return activation, manifest, binding


def _validate_thread(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    intent: ReadIntent,
    intent_row: sa.Row,
    request_payload: dict[str, object],
    effect: EffectIntent,
    attempt: EffectAttempt,
    receipt: EffectReceipt,
    activation: sa.Row,
    manifest: CapabilityManifest,
    binding: ConnectorBinding,
    plan_id: UUID,
    plan_step_id: UUID,
    plan: ValidatedPlan,
    policy_sha256: str,
    retention_policy_id: str,
    retention_until: datetime,
    created_at: datetime,
    corrupt: type[RuntimeError],
) -> None:
    thread_id = effect_readback_thread_id(UUID(intent.read_intent_id))
    checkpoint = connection.execute(
        sa.select(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == thread_id,
        )
    ).one_or_none()
    events = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
        )
        .order_by(operational_events.c.sequence)
    ).all()
    if checkpoint is None or not events:
        raise corrupt("effect verification timeline is absent")
    event = events[0]
    expected_data = {
        "capability_activation_id": str(activation.activation_id),
        "capability_manifest_id": str(activation.capability_manifest_id),
        "connector_binding_id": binding.binding_id,
        "effect_attempt_id": attempt.effect_attempt_id,
        "effect_intent_id": intent.effect_intent_id,
        "effect_receipt_id": receipt.receipt_id,
        "effect_receipt_payload_sha256": canonical_sha256(receipt),
        "plan_id": str(plan_id),
        "plan_step_id": str(plan_step_id),
        "policy_sha256": policy_sha256,
        "read_intent": intent.model_dump(mode="json"),
        "read_intent_payload_sha256": canonical_sha256(intent),
        "request_payload": request_payload,
        "request_payload_sha256": canonical_sha256(request_payload),
    }
    expected_checkpoint = {
        "checkpoint_id": f"checkpoint:{thread_id}:prepared",
        "deadline": intent.deadline,
        "last_error_fingerprint": None,
        "operation_id": thread_id,
        "phase": "action_chain_effect_readback_intent_prepared",
        "retry_count": 0,
        "revision": 1,
        "status": "waiting_for_proof",
        "updated_at": _format_timestamp(created_at),
    }
    if (
        event.sequence != 1
        or event.event_type != "ACTION_CHAIN_EFFECT_READBACK_INTENT_PREPARED"
        or event.operation_id != thread_id
        or event.occurred_at != created_at
        or event.payload != {"checkpoint": expected_checkpoint, "data": expected_data}
        or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
    ):
        raise corrupt("effect verification timeline differs")
    attempts = connection.execute(
        sa.select(read_attempts).where(
            read_attempts.c.tenant_id == scope.tenant_id,
            read_attempts.c.installation_id == scope.installation_id,
            read_attempts.c.read_intent_id == UUID(intent.read_intent_id),
        )
    ).all()
    if not attempts:
        if (
            len(events) != 1
            or checkpoint.revision != 1
            or checkpoint.phase
            != "action_chain_effect_readback_intent_prepared"
            or checkpoint.status != "waiting_for_proof"
            or checkpoint.operation_id != thread_id
            or checkpoint.deadline != _parse_timestamp(intent.deadline)
            or checkpoint.updated_at != created_at
        ):
            raise corrupt("effect verification prepared state differs")
        return
    if len(attempts) != 1 or len(events) not in {2, 3, 4}:
        raise corrupt("effect verification dispatch cardinality differs")
    for sequence, item in enumerate(events, start=1):
        if (
            int(item.sequence) != sequence
            or item.operation_id != thread_id
            or canonical_sha256(item.payload) != bytes(item.payload_sha256).hex()
        ):
            raise corrupt("effect verification dispatch history differs")
    authorized = events[1]
    if (
        authorized.event_type != "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED"
        or authorized.occurred_at != attempts[0].created_at
        or not isinstance(authorized.payload.get("data"), dict)
    ):
        raise corrupt("effect verification authorization event differs")
    latest = events[-1]
    current_data = latest.payload.get("data")
    if not isinstance(current_data, dict):
        raise corrupt("effect verification current event data differs")
    if len(events) >= 3:
        call_started = events[2]
        call_data = dict(call_started.payload.get("data", {}))
        started_at = call_data.pop("provider_call_started_at", None)
        if (
            call_started.event_type != "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED"
            or call_data != authorized.payload["data"]
            or started_at != _format_timestamp(call_started.occurred_at)
        ):
            raise corrupt("effect verification call-start event differs")
        if len(events) == 3 and latest is not call_started:
            raise corrupt("effect verification call-start ordering differs")
        if len(events) == 4 and latest.event_type not in {
            "ACTION_CHAIN_READ_OBSERVED",
            "ACTION_CHAIN_READ_AMBIGUOUS",
            "ACTION_CHAIN_READ_REJECTED",
            "ACTION_CHAIN_READ_RATE_LIMITED",
        }:
            raise corrupt("effect verification result event differs")
    elif latest.event_type != "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED":
        raise corrupt("effect verification dispatch event differs")
    if (
        int(checkpoint.revision) != len(events)
        or checkpoint.operation_id != thread_id
        or checkpoint.deadline != _parse_timestamp(intent.deadline)
    ):
        raise corrupt("effect verification dispatch checkpoint differs")
    validate_authorized_action_chain_read(
        connection,
        scope,
        plan=plan,
        intent=intent,
        intent_row=intent_row,
        action_arguments=request_payload,
        manifest=manifest,
        binding=binding,
        capability_manifest_id=activation.capability_manifest_id,
        attempt_row=attempts[0],
        current_event_data=current_data,
        operation_id=thread_id,
        retention_policy_id=retention_policy_id,
        retention_until=retention_until,
        allow_terminal=len(events) == 4,
        corrupt=corrupt,
    )
    _validate_physical_readback(
        connection,
        scope,
        effect=effect,
        receipt=receipt,
        intent=intent,
        attempt_rows=attempts,
        current_event_data=current_data,
        retention_policy_id=retention_policy_id,
        retention_until=retention_until,
        corrupt=corrupt,
    )


def _validate_physical_readback(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    effect: EffectIntent,
    receipt: EffectReceipt,
    intent: ReadIntent,
    attempt_rows: list[sa.Row],
    current_event_data: dict[str, object],
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    rows = connection.execute(
        sa.select(effect_readbacks).where(
            effect_readbacks.c.tenant_id == scope.tenant_id,
            effect_readbacks.c.installation_id == scope.installation_id,
            effect_readbacks.c.effect_intent_id == UUID(effect.effect_intent_id),
        )
    ).all()
    if not rows:
        return
    if len(rows) != 1 or len(attempt_rows) != 1:
        raise corrupt("physical effect readback cardinality differs")
    try:
        attempt = ReadAttempt.model_validate(attempt_rows[0].payload)
        observation_row = connection.execute(
            sa.select(read_observations).where(
                read_observations.c.tenant_id == scope.tenant_id,
                read_observations.c.installation_id == scope.installation_id,
                read_observations.c.read_attempt_id
                == UUID(attempt.read_attempt_id),
            )
        ).one()
        observation = ReadObservation.model_validate(observation_row.payload)
        evidence_row = connection.execute(
            sa.select(evidence_envelopes).where(
                evidence_envelopes.c.tenant_id == scope.tenant_id,
                evidence_envelopes.c.installation_id == scope.installation_id,
                evidence_envelopes.c.evidence_id
                == UUID(observation.evidence_ref),
            )
        ).one()
        evidence = EvidenceEnvelopeV2.model_validate(evidence_row.payload)
        provider_result = ConnectorInvokeSuccess.model_validate(
            current_event_data.get("provider_result")
        )
        projected = project_action_chain_effect_readback(
            effect=effect,
            receipt=receipt,
            read_intent=intent,
            read_attempt=attempt,
            observation=observation,
            evidence=evidence,
            provider_result=provider_result,
        )
        stored = EffectReadback.model_validate(rows[0].payload)
    except (TypeError, ValueError, sa.exc.MultipleResultsFound, sa.exc.NoResultFound) as error:
        raise corrupt("physical effect readback proof is invalid") from error
    readback = projected.readback
    row = rows[0]
    if (
        stored != readback
        or row.readback_id != UUID(readback.readback_id)
        or row.effect_intent_id != UUID(readback.effect_intent_id)
        or row.read_intent_id != UUID(readback.read_intent_id)
        or row.read_attempt_id != UUID(readback.read_attempt_id)
        or row.provider_resource_id != readback.provider_resource_id
        or row.idempotency_key != readback.idempotency_key
        or bytes(row.request_sha256).hex() != readback.request_sha256
        or row.observed_at != _parse_timestamp(readback.observed_at)
        or bytes(row.state_sha256).hex() != readback.state_sha256
        or row.match_status != readback.match_status
        or row.evidence_id != UUID(readback.evidence_ref)
        or bytes(row.payload_sha256).hex() != canonical_sha256(readback)
        or row.retention_policy_id != retention_policy_id
        or row.retention_until != retention_until
    ):
        raise corrupt("physical effect readback binding differs")


def verification_rows(
    connection: sa.Connection, scope: TimelineScope, effect_intent_id: UUID
) -> list[sa.Row]:
    return list(
        connection.execute(
            sa.select(read_intents).where(
                read_intents.c.tenant_id == scope.tenant_id,
                read_intents.c.installation_id == scope.installation_id,
                read_intents.c.effect_intent_id == effect_intent_id,
                read_intents.c.purpose == "effect_verify",
            )
        ).all()
    )


def _row(
    connection: sa.Connection,
    table: sa.Table,
    scope: TimelineScope,
    key: str,
    value: UUID | None,
    *,
    lock: bool = False,
) -> sa.Row | None:
    if value is None:
        return None
    statement = sa.select(table).where(
        table.c.tenant_id == scope.tenant_id,
        table.c.installation_id == scope.installation_id,
        table.c[key] == value,
    )
    if lock:
        statement = statement.with_for_update(read=True)
    return connection.execute(statement).one_or_none()


def effect_readback_thread_id(read_intent_id: UUID) -> str:
    return f"action-chain-effect-readback:{read_intent_id}"






__all__ = [
    "accepted_effect",
    "effect_readback_thread_id",
    "readback_authority",
    "validate_action_chain_effect_readback_preparation",
    "verification_rows",
]
