"""Purely project one effect-provider result without claiming physical success."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias
from uuid import UUID, uuid5

from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import (
    ConnectorManifest,
    DefinitiveReject,
    EffectResponse,
)
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import EffectAttempt, EffectIntent, EffectReceipt


ProviderEffectResult: TypeAlias = (
    ConnectorInvokeSuccess | ConnectorErrorResponse | ConnectorProtocolFailure
)
TerminalEffectAttemptState: TypeAlias = Literal[
    "provider_accepted",
    "definitive_reject",
    "ambiguous_transport",
    "rate_limited",
    "policy_denied",
    "internal_failure",
]
TerminalEffectIntentState: TypeAlias = Literal[
    "provider_accepted",
    "provider_rejected",
    "ambiguous",
    "waiting_breaker",
    "blocked",
]


@dataclass(frozen=True, slots=True)
class ProjectedActionChainEffectResult:
    """Closed provider evidence; readback and effect completion remain absent."""

    intent: EffectIntent
    attempt: EffectAttempt
    evidence: EvidenceEnvelopeV2
    receipt: EffectReceipt | None
    provider_result: ProviderEffectResult
    provider_result_sha256: str


def project_action_chain_effect_result(
    *,
    tenant_id: UUID,
    intent: EffectIntent,
    attempt: EffectAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    manifest: ConnectorManifest,
    result: ProviderEffectResult,
    observed_at: datetime,
    recorded_at: datetime,
    retention_until: datetime,
) -> ProjectedActionChainEffectResult:
    """Revalidate one claimed exchange and derive its durable terminal records."""

    for value, label in (
        (observed_at, "observed_at"),
        (recorded_at, "recorded_at"),
        (retention_until, "retention_until"),
    ):
        _require_utc_second(value, label)
    if intent.state != "dispatch_authorized":
        raise ValueError("effect result requires a dispatch-authorized intent")
    if attempt.state != "dispatch_authorized" or attempt.response_sha256 is not None:
        raise ValueError("effect result requires a dispatch-authorized attempt")
    if observed_at < _parse_timestamp(attempt.created_at):
        raise ValueError("provider observation precedes effect authorization")
    if recorded_at < observed_at:
        raise ValueError("provider result recording precedes observation")
    if retention_until < recorded_at:
        raise ValueError("provider result retention ends before recording")
    _validate_exchange_authority(
        tenant_id=tenant_id,
        intent=intent,
        attempt=attempt,
        initialize=initialize,
        invoke=invoke,
    )
    if canonical_sha256(manifest) != attempt.connector_manifest_sha256:
        raise ValueError("selected connector manifest differs from effect attempt")
    invoke.validate_against_initialize(initialize, manifest)
    operation = next(
        (item for item in manifest.operations if item.operation == intent.operation),
        None,
    )
    if operation is None or not operation.writes_provider_state:
        raise ValueError("effect result requires a state-writing manifest operation")
    if isinstance(result, ConnectorInvokeSuccess):
        result.validate_against_request(invoke, manifest)

    attempt_state, intent_state, response_sha256, receipt_outcome = _classify_result(
        attempt=attempt,
        initialize=initialize,
        invoke=invoke,
        result=result,
        observed_at=observed_at,
        recorded_at=recorded_at,
    )
    provider_result_sha256 = canonical_sha256(result)
    evidence_id = uuid5(UUID(attempt.effect_attempt_id), "provider-evidence:v1")
    evidence = EvidenceEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "contract": "EvidenceEnvelope/v2",
            "evidence_id": str(evidence_id),
            "tenant_id": str(tenant_id),
            "aggregate_type": "action_chain_effect_attempt",
            "aggregate_id": attempt.effect_attempt_id,
            "sequence": 1,
            "idempotency_key": "provider-result:v1",
            "producer": "eva.action_chain.effect.executor",
            "subject_contract": result.contract,
            "subject_id": _result_subject_id(result),
            "subject_payload_sha256": provider_result_sha256,
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [],
            "captured_at": _format_timestamp(recorded_at),
            "privacy_class": "confidential",
            "retention_until": _format_timestamp(retention_until),
        }
    )
    terminal_attempt = EffectAttempt.model_validate(
        {
            **attempt.model_dump(mode="json"),
            "state": attempt_state,
            "dispatch_permit_sha256": (
                None
                if attempt_state in {"policy_denied", "internal_failure"}
                else attempt.dispatch_permit_sha256
            ),
            "response_sha256": response_sha256,
            "error_class": (
                "none" if attempt_state == "provider_accepted" else attempt_state
            ),
        }
    )
    terminal_intent = EffectIntent.model_validate(
        {**intent.model_dump(mode="json"), "state": intent_state}
    )
    receipt = _build_receipt(
        intent=terminal_intent,
        attempt=terminal_attempt,
        outcome=receipt_outcome,
        evidence_id=evidence_id,
        completed_at=_result_completed_at(result, observed_at),
    )
    return ProjectedActionChainEffectResult(
        intent=terminal_intent,
        attempt=terminal_attempt,
        evidence=evidence,
        receipt=receipt,
        provider_result=result,
        provider_result_sha256=provider_result_sha256,
    )


def _classify_result(
    *,
    attempt: EffectAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    result: ProviderEffectResult,
    observed_at: datetime,
    recorded_at: datetime,
) -> tuple[
    TerminalEffectAttemptState,
    TerminalEffectIntentState,
    str | None,
    EffectResponse | DefinitiveReject | None,
]:
    result_sha256 = canonical_sha256(result)
    if isinstance(result, ConnectorInvokeSuccess):
        response = result.result
        completed_at = _parse_timestamp(response.completed_at)
        if completed_at < observed_at or completed_at > recorded_at:
            raise ValueError(
                "provider completion is outside the host observation window"
            )
        if response.request_sha256 != attempt.request_sha256:
            raise ValueError("provider result binds another effect request")
        outcome = response.outcome
        if isinstance(outcome, EffectResponse):
            provider_time = _parse_timestamp(outcome.provider_time)
            if provider_time < observed_at or provider_time > completed_at:
                raise ValueError("provider receipt time is outside the effect window")
            return "provider_accepted", "provider_accepted", result_sha256, outcome
        if isinstance(outcome, DefinitiveReject):
            return "definitive_reject", "provider_rejected", result_sha256, outcome
        if outcome.kind == "rate_limited":
            return "rate_limited", "waiting_breaker", result_sha256, None
        if outcome.kind == "ambiguous_transport":
            return "ambiguous_transport", "ambiguous", None, None
        if outcome.kind == "policy_denied":
            return "policy_denied", "blocked", None, None
        if outcome.kind == "internal_failure":
            return "internal_failure", "blocked", None, None
        raise ValueError("provider result is not a business-effect outcome")

    if isinstance(result, ConnectorErrorResponse):
        request = (
            initialize
            if result.request_contract.endswith("InitializeRequest/v1")
            else invoke
        )
        result.validate_against_request(
            request,
            external_io_may_have_started=result.error.external_io_possible,
        )
        if result.error.failure_class == "ambiguous_transport":
            return "ambiguous_transport", "ambiguous", None, None
        return "internal_failure", "blocked", None, None

    request = initialize if result.phase == "initialize" else invoke
    if (
        result.request_id != request.id
        or result.request_sha256 != canonical_sha256(request)
        or _parse_timestamp(result.observed_at) != observed_at
    ):
        raise ValueError("provider protocol failure binds another effect exchange")
    if result.failure_class == "ambiguous_transport":
        return "ambiguous_transport", "ambiguous", None, None
    return "internal_failure", "blocked", None, None


def _validate_exchange_authority(
    *,
    tenant_id: UUID,
    intent: EffectIntent,
    attempt: EffectAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
) -> None:
    envelope = invoke.params
    params = initialize.params
    if (
        invoke.method != "effect.execute"
        or envelope.tenant_id != str(tenant_id)
        or envelope.request_id != intent.idempotency_key
        or canonical_sha256(envelope) != attempt.request_sha256
        or envelope.connector_manifest_sha256 != attempt.connector_manifest_sha256
        or envelope.capability_grant_id != attempt.capability_grant_id
        or envelope.credential_grant_id != attempt.credential_grant_id
        or envelope.deadline != attempt.deadline
        or envelope.operation != intent.operation
        or envelope.resource_scope != intent.resource_scope
        or envelope.request_payload_sha256 != intent.frozen_payload_sha256
        or envelope.correlation_id != params.correlation_id
        or envelope.connector_id != params.connector_id
        or envelope.connector_manifest_sha256 != params.connector_manifest_sha256
        or envelope.connector_binding_id != params.connector_binding.binding_id
        or envelope.capability_id != params.capability_grant.capability_id
        or envelope.operation != params.capability_grant.operation
        or envelope.resource_scope != params.capability_grant.resource_scope
        or envelope.request_payload_sha256
        != params.capability_grant.authorized_payload_sha256
        or params.capability_grant.grant_id != attempt.capability_grant_id
        or params.credential_grant.credential_grant_id != attempt.credential_grant_id
        or params.deadline != attempt.deadline
    ):
        raise ValueError("provider exchange binds another effect attempt")


def _build_receipt(
    *,
    intent: EffectIntent,
    attempt: EffectAttempt,
    outcome: EffectResponse | DefinitiveReject | None,
    evidence_id: UUID,
    completed_at: datetime,
) -> EffectReceipt | None:
    if outcome is None:
        return None
    accepted = isinstance(outcome, EffectResponse)
    return EffectReceipt.model_validate(
        {
            "schema_version": 1,
            "contract": "EffectReceipt/v1",
            "receipt_id": str(uuid5(UUID(attempt.effect_attempt_id), "receipt:v1")),
            "effect_intent_id": intent.effect_intent_id,
            "effect_attempt_id": attempt.effect_attempt_id,
            "decision": "accepted" if accepted else "rejected",
            "provider_resource_id": (
                outcome.provider_resource_id if accepted else None
            ),
            "provider_receipt_sha256": (
                outcome.provider_receipt_sha256 if accepted else outcome.evidence_sha256
            ),
            "request_payload_sha256": intent.frozen_payload_sha256,
            "provider_time": (
                outcome.provider_time if accepted else _format_timestamp(completed_at)
            ),
            "evidence_ref": str(evidence_id),
        }
    )


def _result_completed_at(
    result: ProviderEffectResult, observed_at: datetime
) -> datetime:
    if isinstance(result, ConnectorInvokeSuccess):
        return _parse_timestamp(result.result.completed_at)
    return observed_at


def _result_subject_id(result: ProviderEffectResult) -> str:
    return (
        result.request_id if isinstance(result, ConnectorProtocolFailure) else result.id
    )


__all__ = [
    "ProjectedActionChainEffectResult",
    "ProviderEffectResult",
    "project_action_chain_effect_result",
]
