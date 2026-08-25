"""Project one sealed connector result into durable action-chain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeAlias
from uuid import UUID, uuid5

from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorManifest, InlinePayload, ReadOk
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

    state, response_sha256, read_ok = _classify_result(
        attempt=attempt,
        initialize=initialize,
        invoke=invoke,
        manifest=manifest,
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
    manifest: ConnectorManifest,
    result: ProviderReadResult,
    observed_at: datetime,
    recorded_at: datetime,
) -> tuple[TerminalReadState, str | None, ReadOk | None]:
    result_sha256 = canonical_sha256(result)
    if isinstance(result, ConnectorInvokeSuccess):
        result.validate_against_request(invoke, manifest)
        if result.result.request_sha256 != attempt.request_sha256:
            raise ValueError("provider success binds another read request")
        completed_at = _parse_timestamp(result.result.completed_at)
        if completed_at < observed_at or completed_at > recorded_at:
            raise ValueError("provider completion is outside the host observation window")
        outcome = result.result.outcome
        if isinstance(outcome, ReadOk):
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
        request = initialize if result.request_contract.endswith("InitializeRequest/v1") else invoke
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
        or _parse_timestamp(result.observed_at) != recorded_at
    ):
        raise ValueError("provider protocol failure binds another exchange")
    if result.failure_class == "ambiguous_transport":
        return "ambiguous", None, None
    return "rejected", result_sha256, None


def _build_observation(
    *,
    attempt: ReadAttempt,
    outcome: ReadOk,
    evidence_id: UUID,
    observed_at: datetime,
) -> ReadObservation:
    retrieved_at = _parse_timestamp(outcome.retrieved_at)
    if retrieved_at < observed_at:
        raise ValueError("provider retrieval precedes the host observation")
    normalized_value = (
        outcome.payload.normalized_payload
        if isinstance(outcome.payload, InlinePayload)
        else outcome.payload.object_handle.model_dump(mode="json")
    )
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
            "retrieved_at": outcome.retrieved_at,
            "observed_at": _format_timestamp(observed_at),
            "normalized_value": normalized_value,
            "response_sha256": outcome.response_sha256,
            "trust": "untrusted_external",
            "evidence_ref": str(evidence_id),
        }
    )


def _result_subject_id(result: ProviderReadResult) -> str:
    return result.request_id if isinstance(result, ConnectorProtocolFailure) else result.id


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_utc_second(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole-second precision")


__all__ = [
    "ProjectedActionChainReadResult",
    "ProviderReadResult",
    "project_action_chain_read_result",
]
