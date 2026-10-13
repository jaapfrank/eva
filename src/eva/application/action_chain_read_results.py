"""Purely project one sealed connector result into durable action-chain contracts."""

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
    InlinePayload,
    ReadbackResult,
    ReadOk,
)
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import ReadAttempt, ReadObservation


ProviderReadResult: TypeAlias = (
    ConnectorInvokeSuccess | ConnectorErrorResponse | ConnectorProtocolFailure
)
TerminalReadState: TypeAlias = Literal[
    "observed", "ambiguous", "rejected", "rate_limited"
]


@dataclass(frozen=True, slots=True)
class ProjectedActionChainReadResult:
    """Closed contracts ready for one atomic PostgreSQL result transaction."""

    attempt: ReadAttempt
    evidence: EvidenceEnvelopeV2
    observation: ReadObservation | None
    provider_result: ProviderReadResult
    provider_result_sha256: str


def project_action_chain_read_result(
    *,
    tenant_id: UUID,
    attempt: ReadAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    manifest: ConnectorManifest,
    result: ProviderReadResult,
    observed_at: datetime,
    recorded_at: datetime,
    retention_until: datetime,
) -> ProjectedActionChainReadResult:
    """Revalidate and bind a provider result without writing or retrying anything."""

    for value, label in (
        (observed_at, "observed_at"),
        (recorded_at, "recorded_at"),
        (retention_until, "retention_until"),
    ):
        _require_utc_second(value, label)
    if attempt.state != "dispatch_authorized" or attempt.response_sha256 is not None:
        raise ValueError("provider result requires a dispatch-authorized read attempt")
    if observed_at < _parse_timestamp(attempt.created_at):
        raise ValueError("provider observation precedes read authorization")
    if recorded_at < observed_at:
        raise ValueError("provider result recording precedes observation")
    if retention_until < recorded_at:
        raise ValueError("provider result retention ends before recording")
    _validate_exchange_authority(
        attempt=attempt,
        initialize=initialize,
        invoke=invoke,
        tenant_id=tenant_id,
    )
    if canonical_sha256(manifest) != attempt.connector_manifest_sha256:
        raise ValueError("selected connector manifest differs from read attempt")
    invoke.validate_against_initialize(initialize, manifest)
    operation = next(
        (
            item
            for item in manifest.operations
            if item.operation == invoke.params.operation
        ),
        None,
    )
    if operation is None:
        raise ValueError("read operation is absent from connector manifest")
    if isinstance(result, ConnectorInvokeSuccess):
        result.validate_against_request(invoke, manifest)

    state, response_sha256, read_ok = _classify_result(
        attempt=attempt,
        initialize=initialize,
        invoke=invoke,
        output_schema_sha256=operation.output_schema_sha256,
        result=result,
        observed_at=observed_at,
        recorded_at=recorded_at,
    )
    provider_result_sha256 = canonical_sha256(result)
    evidence_id = uuid5(UUID(attempt.read_attempt_id), "provider-evidence:v1")
    evidence = EvidenceEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "contract": "EvidenceEnvelope/v2",
            "evidence_id": str(evidence_id),
            "tenant_id": str(tenant_id),
            "aggregate_type": "action_chain_read_attempt",
            "aggregate_id": attempt.read_attempt_id,
            "sequence": 1,
            "idempotency_key": "provider-result:v1",
            "producer": "eva.action_chain.read.executor",
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
    terminal_attempt = attempt.model_copy(
        update={"state": state, "response_sha256": response_sha256}
    )
    observation = (
        None
        if read_ok is None
        else _build_observation(
            attempt=terminal_attempt,
            outcome=read_ok,
            evidence_id=evidence_id,
            observed_at=observed_at,
        )
    )
    return ProjectedActionChainReadResult(
        attempt=terminal_attempt,
        evidence=evidence,
        observation=observation,
        provider_result=result,
        provider_result_sha256=provider_result_sha256,
    )


def _classify_result(
    *,
    attempt: ReadAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    output_schema_sha256: str,
    result: ProviderReadResult,
    observed_at: datetime,
    recorded_at: datetime,
) -> tuple[TerminalReadState, str | None, ReadOk | ReadbackResult | None]:
    result_sha256 = canonical_sha256(result)
    if isinstance(result, ConnectorInvokeSuccess):
        provider_result = result.result
        envelope = invoke.params
        if (
            result.id != invoke.id
            or provider_result.request_id != envelope.request_id
            or provider_result.correlation_id != envelope.correlation_id
            or provider_result.connector_id != envelope.connector_id
            or provider_result.operation != envelope.operation
            or provider_result.request_sha256 != attempt.request_sha256
            or provider_result.request_sha256 != canonical_sha256(envelope)
            or provider_result.connector_manifest_sha256
            != attempt.connector_manifest_sha256
            or envelope.connector_manifest_sha256 != attempt.connector_manifest_sha256
            or invoke.method != _required_read_method(envelope.operation)
        ):
            raise ValueError("provider success binds another read request")
        completed_at = _parse_timestamp(provider_result.completed_at)
        if completed_at > _parse_timestamp(envelope.deadline):
            raise ValueError("provider completion exceeds the read deadline")
        if completed_at < observed_at or completed_at > recorded_at:
            raise ValueError(
                "provider completion is outside the host observation window"
            )
        outcome = provider_result.outcome
        if isinstance(outcome, ReadOk):
            if outcome.payload.output_schema_sha256 != output_schema_sha256:
                raise ValueError("provider result uses another output schema")
            retrieved_at = _parse_timestamp(outcome.retrieved_at)
            if retrieved_at < observed_at or retrieved_at > completed_at:
                raise ValueError(
                    "provider retrieval is outside the host/completion window"
                )
            return "observed", result_sha256, outcome
        if isinstance(outcome, ReadbackResult):
            request_payload = envelope.request_payload
            payload = outcome.payload
            if (
                invoke.method != "effect.readback"
                or outcome.idempotency_key != request_payload.get("idempotencyKey")
                or outcome.request_sha256 != request_payload.get("requestSha256")
                or outcome.provider_resource_id
                != request_payload.get("providerResourceId")
            ):
                raise ValueError("provider readback binds another effect request")
            readback_at = _parse_timestamp(outcome.observed_at)
            if readback_at < observed_at or readback_at > completed_at:
                raise ValueError(
                    "provider readback is outside the host/completion window"
                )
            if (
                payload is not None
                and payload.output_schema_sha256 != output_schema_sha256
            ):
                raise ValueError("provider readback uses another output schema")
            return "observed", result_sha256, outcome
        if outcome.kind == "rate_limited":
            return "rate_limited", result_sha256, None
        if outcome.kind == "ambiguous_transport":
            return "ambiguous", None, None
        if outcome.kind in {
            "definitive_reject",
            "transient_read_failure",
            "policy_denied",
            "internal_failure",
        }:
            return "rejected", result_sha256, None
        raise ValueError("provider result is not a business-read outcome")

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
            return "ambiguous", None, None
        return "rejected", result_sha256, None

    request = initialize if result.phase == "initialize" else invoke
    if (
        result.request_id != request.id
        or result.request_sha256 != canonical_sha256(request)
        or _parse_timestamp(result.observed_at) != observed_at
    ):
        raise ValueError("provider protocol failure binds another exchange")
    if result.failure_class == "ambiguous_transport":
        return "ambiguous", None, None
    return "rejected", result_sha256, None


def _validate_exchange_authority(
    *,
    attempt: ReadAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    tenant_id: UUID,
) -> None:
    envelope = invoke.params
    params = initialize.params
    if (
        invoke.method != _required_read_method(envelope.operation)
        or envelope.tenant_id != str(tenant_id)
        or canonical_sha256(envelope) != attempt.request_sha256
        or envelope.connector_manifest_sha256 != attempt.connector_manifest_sha256
        or envelope.capability_grant_id != attempt.capability_grant_id
        or envelope.credential_grant_id != attempt.credential_grant_id
        or envelope.deadline != attempt.deadline
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
        raise ValueError("provider exchange binds another read attempt")


def _build_observation(
    *,
    attempt: ReadAttempt,
    outcome: ReadOk | ReadbackResult,
    evidence_id: UUID,
    observed_at: datetime,
) -> ReadObservation:
    retrieved_at = _parse_timestamp(
        outcome.retrieved_at if isinstance(outcome, ReadOk) else outcome.observed_at
    )
    if retrieved_at < observed_at:
        raise ValueError("provider retrieval precedes the host observation")
    if isinstance(outcome, ReadOk):
        normalized_value = _normalized_payload(outcome.payload)
        response_sha256 = outcome.response_sha256
    else:
        normalized_value = {
            "idempotencyKey": outcome.idempotency_key,
            "matchStatus": outcome.match_status,
            "payload": (
                None
                if outcome.payload is None
                else _normalized_payload(outcome.payload)
            ),
            "requestSha256": outcome.request_sha256,
        }
        response_sha256 = canonical_sha256(outcome)
    return ReadObservation.model_validate(
        {
            "schema_version": 1,
            "contract": "ReadObservation/v1",
            "observation_id": str(
                uuid5(UUID(attempt.read_attempt_id), "observation:v1")
            ),
            "read_intent_id": attempt.read_intent_id,
            "read_attempt_id": attempt.read_attempt_id,
            "provider_resource_id": outcome.provider_resource_id,
            "retrieved_at": _format_timestamp(retrieved_at),
            "observed_at": _format_timestamp(observed_at),
            "normalized_value": normalized_value,
            "response_sha256": response_sha256,
            "trust": "untrusted_external",
            "evidence_ref": str(evidence_id),
        }
    )


def _normalized_payload(payload: object) -> object:
    if isinstance(payload, InlinePayload):
        return payload.normalized_payload
    object_handle = getattr(payload, "object_handle", None)
    if object_handle is None:
        raise ValueError("provider read payload is not typed")
    return object_handle.model_dump(mode="json")


def _required_read_method(operation: str) -> str:
    if operation == "effect.readback":
        return "effect.readback"
    return "resource.read"


def _result_subject_id(result: ProviderReadResult) -> str:
    return (
        result.request_id if isinstance(result, ConnectorProtocolFailure) else result.id
    )


__all__ = [
    "ProjectedActionChainReadResult",
    "ProviderReadResult",
    "project_action_chain_read_result",
]
