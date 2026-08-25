"""Reload validation for one durable action-chain effect-provider result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.action_chain_effect_readback_validation import (
    validate_action_chain_effect_readback_preparation,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope, ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    EffectIntent,
    EffectReceipt,
)
from eva.contracts.planning import ValidatedPlan
from eva.operations.action_chain_effect_results import (
    ProjectedActionChainEffectResult,
    ProviderEffectResult,
    project_action_chain_effect_result,
)


effect_receipts = metadata.tables["eva_effects.effect_receipts"]
effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]

_TERMINAL_TIMELINE = {
    "provider_accepted": (
        "ACTION_CHAIN_EFFECT_PROVIDER_ACCEPTED",
        "action_chain_effect_provider_accepted",
        "waiting_for_proof",
    ),
    "definitive_reject": (
        "ACTION_CHAIN_EFFECT_REJECTED",
        "action_chain_effect_rejected",
        "failed",
    ),
    "ambiguous_transport": (
        "ACTION_CHAIN_EFFECT_AMBIGUOUS",
        "action_chain_effect_ambiguous",
        "needs_help",
    ),
    "rate_limited": (
        "ACTION_CHAIN_EFFECT_RATE_LIMITED",
        "action_chain_effect_rate_limited",
        "active",
    ),
    "policy_denied": (
        "ACTION_CHAIN_EFFECT_BLOCKED",
        "action_chain_effect_blocked",
        "failed",
    ),
    "internal_failure": (
        "ACTION_CHAIN_EFFECT_BLOCKED",
        "action_chain_effect_blocked",
        "failed",
    ),
}
_RESULT_KEYS = {
    "connector_manifest",
    "effect_attempt_terminal_payload_sha256",
    "effect_intent_terminal_payload_sha256",
    "effect_receipt_id",
    "effect_receipt_payload_sha256",
    "effect_terminal_state",
    "provider_evidence_id",
    "provider_evidence_payload_sha256",
    "provider_result",
    "provider_result_observed_at",
    "provider_result_recorded_at",
    "provider_result_sha256",
}


@dataclass(frozen=True, slots=True)
class ReconstructedActionChainEffectState:
    """Authorized pre-call state recovered from an authorized or terminal row."""

    intent: EffectIntent
    attempt: EffectAttempt
    terminal: bool


def validate_action_chain_effect_history(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    operation_id: UUID,
    plan_id: UUID,
    plan_step_id: UUID,
    state: ReconstructedActionChainEffectState,
    plan: ValidatedPlan,
    action_arguments: dict[str, object],
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
    intent_row: sa.Row,
    attempt_row: sa.Row,
    expected_authorization_data: dict[str, object],
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    """Validate authorization, call claim and any terminal provider result."""

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
    if state.terminal:
        validate_action_chain_effect_terminal(
            connection,
            scope,
            plan=plan,
            action_arguments=action_arguments,
            authorized_intent=state.intent,
            authorized_attempt=state.attempt,
            capability_manifest=capability_manifest,
            binding=binding,
            capability_grant=capability_grant,
            credential_grant=credential_grant,
            intent_row=intent_row,
            attempt_row=attempt_row,
            current_event_data=latest.payload["data"],
            expected_authorization_data=expected_authorization_data,
            latest=latest,
            checkpoint=checkpoint,
            retention_policy_id=retention_policy_id,
            retention_until=retention_until,
            corrupt=corrupt,
        )
        validate_action_chain_effect_readback_preparation(
            connection,
            scope,
            effect_row=intent_row,
            plan_id=plan_id,
            plan_step_id=plan_step_id,
            plan=plan,
            policy_sha256=plan.policy_sha256,
            retention_policy_id=retention_policy_id,
            retention_until=retention_until,
            corrupt=corrupt,
        )
        return
    current_event_data = latest.payload.get("data")
    if not isinstance(current_event_data, dict):
        raise corrupt("authorized effect timeline data is invalid")
    if latest.event_type == "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED":
        valid_timeline = (
            current_event_data == expected_authorization_data
            and checkpoint.phase == "action_chain_effect_dispatch_authorized"
            and checkpoint.status == "waiting_for_proof"
            and checkpoint.updated_at == attempt_row.created_at
        )
    elif latest.event_type == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED":
        call_data = dict(current_event_data)
        started_at = call_data.pop("provider_call_started_at", None)
        previous = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == operation_id,
                operational_events.c.sequence == int(latest.sequence) - 1,
            )
        ).one_or_none()
        started = _try_timestamp(started_at)
        valid_timeline = (
            call_data == expected_authorization_data
            and started is not None
            and started == latest.occurred_at
            and attempt_row.created_at
            <= started
            < _parse_timestamp(state.intent.deadline)
            and checkpoint.phase == "action_chain_effect_provider_call_started"
            and checkpoint.status == "waiting_for_proof"
            and checkpoint.updated_at == started
            and previous is not None
            and previous.event_type == "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED"
            and previous.occurred_at == attempt_row.created_at
            and previous.payload.get("data") == expected_authorization_data
        )
    else:
        valid_timeline = False
    if not valid_timeline:
        raise corrupt("authorized effect timeline binding differs")


def validate_action_chain_effect_terminal(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    action_arguments: dict[str, object],
    authorized_intent: EffectIntent,
    authorized_attempt: EffectAttempt,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
    intent_row: sa.Row,
    attempt_row: sa.Row,
    current_event_data: dict[str, object],
    expected_authorization_data: dict[str, object],
    latest: sa.Row,
    checkpoint: sa.Row,
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    """Reproject stored provider bytes and require exact rows and history."""

    try:
        manifest = ConnectorManifest.model_validate(
            current_event_data.get("connector_manifest")
        )
        initialize, invoke = _build_exchange(
            scope=scope,
            plan=plan,
            action_arguments=action_arguments,
            intent=authorized_intent,
            attempt=authorized_attempt,
            connector_manifest=manifest,
            capability_manifest=capability_manifest,
            binding=binding,
            capability_grant=capability_grant,
            credential_grant=credential_grant,
        )
        result = _provider_result(current_event_data.get("provider_result"))
        observed_at = _parse_timestamp(
            current_event_data.get("provider_result_observed_at")
        )
        recorded_at = _parse_timestamp(
            current_event_data.get("provider_result_recorded_at")
        )
        projected = project_action_chain_effect_result(
            tenant_id=scope.tenant_id,
            intent=authorized_intent,
            attempt=authorized_attempt,
            initialize=initialize,
            invoke=invoke,
            manifest=manifest,
            result=result,
            observed_at=observed_at,
            recorded_at=recorded_at,
            retention_until=retention_until,
        )
        stored_intent = EffectIntent.model_validate(intent_row.payload)
        stored_attempt = EffectAttempt.model_validate(attempt_row.payload)
    except (TypeError, ValueError) as error:
        raise corrupt("terminal effect provider projection is invalid") from error

    expected_event, expected_phase, expected_status = _timeline(
        projected.attempt.state, corrupt
    )
    authorization_data = {
        key: value
        for key, value in current_event_data.items()
        if key not in _RESULT_KEYS and key != "provider_call_started_at"
    }
    previous = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == latest.thread_id,
            operational_events.c.sequence == int(latest.sequence) - 1,
        )
    ).one_or_none()
    previous_data = (
        None
        if previous is None or not isinstance(previous.payload.get("data"), dict)
        else previous.payload["data"]
    )
    started_at = (
        None
        if previous_data is None
        else _try_timestamp(previous_data.get("provider_call_started_at"))
    )
    if (
        latest.event_type != expected_event
        or latest.occurred_at != recorded_at
        or checkpoint.phase != expected_phase
        or checkpoint.status != expected_status
        or checkpoint.updated_at != recorded_at
        or authorization_data != expected_authorization_data
        or previous is None
        or previous.event_type != "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
        or previous_data is None
        or started_at is None
        or not started_at <= observed_at <= recorded_at
        or {
            key: value
            for key, value in previous_data.items()
            if key != "provider_call_started_at"
        }
        != expected_authorization_data
        or canonical_sha256(result)
        != current_event_data.get("provider_result_sha256")
        or current_event_data.get("effect_terminal_state")
        != projected.attempt.state
        or current_event_data.get("effect_intent_terminal_payload_sha256")
        != canonical_sha256(projected.intent)
        or current_event_data.get("effect_attempt_terminal_payload_sha256")
        != canonical_sha256(projected.attempt)
    ):
        raise corrupt("terminal effect timeline binding differs")

    if (
        stored_intent != projected.intent
        or stored_attempt != projected.attempt
        or intent_row.state != projected.intent.state
        or attempt_row.state != projected.attempt.state
        or bytes(intent_row.payload_sha256).hex() != canonical_sha256(projected.intent)
        or bytes(attempt_row.payload_sha256).hex()
        != canonical_sha256(projected.attempt)
        or int(intent_row.revision) != 3
        or int(attempt_row.revision) != 2
        or intent_row.updated_at != recorded_at
        or attempt_row.updated_at != recorded_at
        or attempt_row.error_class != projected.attempt.error_class
        or _optional_hash(attempt_row.dispatch_permit_sha256)
        != projected.attempt.dispatch_permit_sha256
        or _optional_hash(attempt_row.response_sha256)
        != projected.attempt.response_sha256
        or str(attempt_row.transport_evidence_id)
        != projected.evidence.evidence_id
    ):
        raise corrupt("terminal effect rows differ from provider result")
    _validate_evidence_and_receipt(
        connection,
        scope,
        projected=projected,
        current_event_data=current_event_data,
        retention_policy_id=retention_policy_id,
        retention_until=retention_until,
        recorded_at=recorded_at,
        corrupt=corrupt,
    )


def _validate_evidence_and_receipt(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    projected: ProjectedActionChainEffectResult,
    current_event_data: dict[str, object],
    retention_policy_id: str,
    retention_until: datetime,
    recorded_at: datetime,
    corrupt: type[RuntimeError],
) -> None:
    evidence = projected.evidence
    evidence_row = connection.execute(
        sa.select(evidence_envelopes).where(
            evidence_envelopes.c.tenant_id == scope.tenant_id,
            evidence_envelopes.c.installation_id == scope.installation_id,
            evidence_envelopes.c.evidence_id == UUID(evidence.evidence_id),
        )
    ).one_or_none()
    receipt_rows = connection.execute(
        sa.select(effect_receipts).where(
            effect_receipts.c.tenant_id == scope.tenant_id,
            effect_receipts.c.installation_id == scope.installation_id,
            effect_receipts.c.effect_attempt_id
            == UUID(projected.attempt.effect_attempt_id),
        )
    ).all()
    readback_count = connection.scalar(
        sa.select(sa.func.count())
        .select_from(effect_readbacks)
        .where(
            effect_readbacks.c.tenant_id == scope.tenant_id,
            effect_readbacks.c.installation_id == scope.installation_id,
            effect_readbacks.c.effect_intent_id
            == UUID(projected.intent.effect_intent_id),
        )
    )
    if (
        evidence_row is None
        or EvidenceEnvelopeV2.model_validate(evidence_row.payload) != evidence
        or bytes(evidence_row.payload_sha256).hex() != canonical_sha256(evidence)
        or evidence_row.retention_policy_id != retention_policy_id
        or evidence_row.retention_until != retention_until
        or current_event_data.get("provider_evidence_id") != evidence.evidence_id
        or current_event_data.get("provider_evidence_payload_sha256")
        != canonical_sha256(evidence)
    ):
        raise corrupt("terminal effect evidence differs")
    receipt = projected.receipt
    if receipt is None:
        if receipt_rows or readback_count != 0 or any(
            current_event_data.get(key) is not None
            for key in ("effect_receipt_id", "effect_receipt_payload_sha256")
        ):
            raise corrupt("terminal effect unexpectedly has a receipt")
        return
    if len(receipt_rows) != 1:
        raise corrupt("terminal effect receipt cardinality differs")
    if readback_count not in {0, 1}:
        raise corrupt("terminal effect readback cardinality differs")
    row = receipt_rows[0]
    if (
        EffectReceipt.model_validate(row.payload) != receipt
        or bytes(row.payload_sha256).hex() != canonical_sha256(receipt)
        or row.retention_policy_id != retention_policy_id
        or row.retention_until != retention_until
        or row.created_at != recorded_at
        or current_event_data.get("effect_receipt_id") != receipt.receipt_id
        or current_event_data.get("effect_receipt_payload_sha256")
        != canonical_sha256(receipt)
    ):
        raise corrupt("terminal effect receipt differs")


def _provider_result(value: object) -> ProviderEffectResult:
    if not isinstance(value, dict):
        raise ValueError("provider result is not an object")
    contract = value.get("contract")
    model = {
        "ConnectorInvokeSuccess/v1": ConnectorInvokeSuccess,
        "ConnectorErrorResponse/v1": ConnectorErrorResponse,
        "ConnectorProtocolFailure/v1": ConnectorProtocolFailure,
    }.get(contract)
    if model is None:
        raise ValueError("provider result contract is unsupported")
    return model.model_validate(value)


def _build_exchange(
    *,
    scope: TimelineScope,
    plan: ValidatedPlan,
    action_arguments: dict[str, object],
    intent: EffectIntent,
    attempt: EffectAttempt,
    connector_manifest: ConnectorManifest,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
) -> tuple[ConnectorInitializeRequest, ConnectorInvokeRequest]:
    initialize_id = str(
        uuid5(UUID(attempt.effect_attempt_id), "connector-initialize:v1")
    )
    correlation_id = str(uuid5(UUID(attempt.effect_attempt_id), "correlation:v1"))
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
                "worker_id": credential_grant.worker_id,
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
            "request_id": intent.idempotency_key,
            "correlation_id": correlation_id,
            "episode_id": plan.episode_id,
            "generation": plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": intent.frozen_payload_sha256,
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
            "id": intent.idempotency_key,
            "method": "effect.execute",
            "params": envelope.model_dump(mode="json"),
        }
    )
    initialize.validate_against_manifest(connector_manifest)
    invoke.validate_against_initialize(initialize, connector_manifest)
    return initialize, invoke


def _timeline(
    state: str, corrupt: type[RuntimeError]
) -> tuple[str, str, str]:
    try:
        return _TERMINAL_TIMELINE[state]
    except KeyError as error:
        raise corrupt("terminal effect state is unsupported") from error


def _optional_hash(value: object) -> str | None:
    return None if value is None else bytes(value).hex()


def _try_timestamp(value: object) -> datetime | None:
    try:
        return _parse_timestamp(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp is not text")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ReconstructedActionChainEffectState",
    "validate_action_chain_effect_history",
    "validate_action_chain_effect_terminal",
]
