"""Reload validation for a terminal action-chain provider-read projection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.execution import ReadAttempt, ReadObservation
from eva.operations.action_chain_read_results import (
    ProjectedActionChainReadResult,
    ProviderReadResult,
    project_action_chain_read_result,
)


read_observations = metadata.tables["eva_effects.read_observations"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]

_TERMINAL_TIMELINE = {
    "observed": ("ACTION_CHAIN_READ_OBSERVED", "action_chain_read_observed", "active"),
    "ambiguous": (
        "ACTION_CHAIN_READ_AMBIGUOUS",
        "action_chain_read_ambiguous",
        "needs_help",
    ),
    "rejected": ("ACTION_CHAIN_READ_REJECTED", "action_chain_read_rejected", "failed"),
    "rate_limited": (
        "ACTION_CHAIN_READ_RATE_LIMITED",
        "action_chain_read_rate_limited",
        "active",
    ),
}


def validate_action_chain_read_terminal(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    authorized_attempt: ReadAttempt,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    attempt_row: sa.Row,
    current_event_data: dict[str, object],
    expected_event_data: dict[str, object],
    latest: sa.Row,
    checkpoint: sa.Row,
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    """Reproject stored provider bytes and require exact rows and timeline bytes."""

    try:
        manifest = ConnectorManifest.model_validate(
            current_event_data.get("connector_manifest")
        )
        result = _provider_result(current_event_data.get("provider_result"))
        observed_at = _parse_timestamp(
            str(current_event_data.get("provider_call_started_at"))
        )
        recorded_at = _parse_timestamp(
            str(current_event_data.get("provider_result_recorded_at"))
        )
        projected = project_action_chain_read_result(
            tenant_id=scope.tenant_id,
            attempt=authorized_attempt,
            initialize=initialize,
            invoke=invoke,
            manifest=manifest,
            result=result,
            observed_at=observed_at,
            recorded_at=recorded_at,
            retention_until=retention_until,
        )
    except (TypeError, ValueError) as error:
        raise corrupt("terminal read provider projection is invalid") from error

    event_type, phase, status = _TERMINAL_TIMELINE[projected.attempt.state]
    observation = projected.observation
    expected_event_data.update(
        {
            "connector_manifest": manifest.model_dump(mode="json"),
            "provider_evidence_id": projected.evidence.evidence_id,
            "provider_evidence_payload_sha256": canonical_sha256(
                projected.evidence
            ),
            "provider_result": result.model_dump(mode="json"),
            "provider_result_recorded_at": _format_timestamp(recorded_at),
            "provider_result_sha256": projected.provider_result_sha256,
            "read_attempt_terminal_payload_sha256": canonical_sha256(
                projected.attempt
            ),
            "read_observation_id": (
                None if observation is None else observation.observation_id
            ),
            "read_observation_payload_sha256": (
                None if observation is None else canonical_sha256(observation)
            ),
            "read_terminal_state": projected.attempt.state,
        }
    )
    response = projected.attempt.response_sha256
    if (
        ReadAttempt.model_validate(attempt_row.payload) != projected.attempt
        or attempt_row.state != projected.attempt.state
        or (
            None
            if attempt_row.response_sha256 is None
            else bytes(attempt_row.response_sha256).hex()
        )
        != response
        or bytes(attempt_row.payload_sha256).hex()
        != canonical_sha256(projected.attempt)
        or int(attempt_row.revision) != 2
        or attempt_row.updated_at != recorded_at
        or current_event_data != expected_event_data
        or latest.event_type != event_type
        or checkpoint.phase != phase
        or checkpoint.status != status
        or checkpoint.updated_at != recorded_at
    ):
        raise corrupt("terminal read attempt or timeline binding differs")
    _validate_evidence(
        connection,
        scope,
        projected=projected,
        retention_policy_id=retention_policy_id,
        retention_until=retention_until,
        corrupt=corrupt,
    )


def _validate_evidence(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    projected: ProjectedActionChainReadResult,
    retention_policy_id: str,
    retention_until: datetime,
    corrupt: type[RuntimeError],
) -> None:
    evidence = projected.evidence
    row = connection.execute(
        sa.select(evidence_envelopes).where(
            evidence_envelopes.c.tenant_id == scope.tenant_id,
            evidence_envelopes.c.installation_id == scope.installation_id,
            evidence_envelopes.c.evidence_id == UUID(evidence.evidence_id),
        )
    ).one_or_none()
    if (
        row is None
        or row.payload != evidence.model_dump(mode="json")
        or bytes(row.payload_sha256).hex() != canonical_sha256(evidence)
        or row.aggregate_type != evidence.aggregate_type
        or row.aggregate_id != evidence.aggregate_id
        or int(row.sequence) != evidence.sequence
        or row.idempotency_key != evidence.idempotency_key
        or row.producer != evidence.producer
        or row.subject_contract != evidence.subject_contract
        or row.subject_id != evidence.subject_id
        or bytes(row.subject_payload_sha256).hex()
        != evidence.subject_payload_sha256
        or row.object_ref_id is not None
        or row.object_sha256 is not None
        or row.predecessor_sha256 is not None
        or list(row.provenance_refs) != []
        or row.captured_at != _parse_timestamp(evidence.captured_at)
        or row.privacy_class != evidence.privacy_class
        or row.retention_policy_id != retention_policy_id
        or row.retention_until != retention_until
    ):
        raise corrupt("terminal read evidence binding differs")
    observation = projected.observation
    rows = connection.execute(
        sa.select(read_observations).where(
            read_observations.c.tenant_id == scope.tenant_id,
            read_observations.c.installation_id == scope.installation_id,
            read_observations.c.read_attempt_id
            == UUID(projected.attempt.read_attempt_id),
        )
    ).all()
    if observation is None:
        if rows:
            raise corrupt("failed terminal read has an observation")
        return
    if len(rows) != 1:
        raise corrupt("observed terminal read lacks one observation")
    row = rows[0]
    if (
        ReadObservation.model_validate(row.payload) != observation
        or bytes(row.payload_sha256).hex() != canonical_sha256(observation)
        or row.observation_id != UUID(observation.observation_id)
        or row.read_intent_id != UUID(observation.read_intent_id)
        or row.provider_resource_id != observation.provider_resource_id
        or row.retrieved_at != _parse_timestamp(observation.retrieved_at)
        or row.observed_at != _parse_timestamp(observation.observed_at)
        or row.normalized_value != observation.normalized_value
        or bytes(row.normalized_value_sha256).hex()
        != canonical_sha256(observation.normalized_value)
        or bytes(row.response_sha256).hex() != observation.response_sha256
        or row.trust != observation.trust
        or row.evidence_id != UUID(observation.evidence_ref)
        or row.retention_policy_id != retention_policy_id
        or row.retention_until != retention_until
    ):
        raise corrupt("terminal read observation binding differs")


def _provider_result(value: object) -> ProviderReadResult:
    if not isinstance(value, dict):
        raise ValueError("provider result is absent")
    contract = value.get("contract")
    result_type = {
        "ConnectorInvokeSuccess/v1": ConnectorInvokeSuccess,
        "ConnectorErrorResponse/v1": ConnectorErrorResponse,
        "ConnectorProtocolFailure/v1": ConnectorProtocolFailure,
    }.get(contract)
    if result_type is None:
        raise ValueError("provider result contract is not allowed")
    return result_type.model_validate(value)


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["validate_action_chain_read_terminal"]
