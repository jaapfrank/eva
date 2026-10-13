"""Language-neutral connector manifest, request envelope, and result wire types."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    JsonValue,
    MimeType,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    validate_json_value,
)
from .canonical_json import canonical_sha256
from .execution import Operation, ResourceScope


class ConnectorOperation(ContractModel):
    operation: Operation
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256
    external_calls_per_invocation: Literal[0, 1]
    writes_provider_state: bool


class ConnectorManifest(Version1Contract):
    contract: Literal["ConnectorManifest/v1"]
    connector_id: Identifier
    connector_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$",
    )
    protocol_version: Literal["EvaConnectorProtocol/v1"]
    executable_sha256: Sha256
    sandbox_profile_sha256: Sha256
    operations: list[ConnectorOperation] = Field(min_length=1, max_length=64)
    maximum_protocol_frame_bytes: int = Field(ge=1, le=1_048_576)
    maximum_protocol_frames_per_invocation: int = Field(ge=1, le=2)
    maximum_stdout_bytes: int = Field(ge=1, le=2_101_248)
    maximum_stderr_bytes: int = Field(ge=0, le=65_536)
    issued_at: Timestamp

    @model_validator(mode="after")
    def _unique_operations(self) -> "ConnectorManifest":
        operations = [item.operation for item in self.operations]
        if len(operations) != len(set(operations)):
            raise ValueError("connector operations must be unique")
        minimum_stdout = (
            self.maximum_protocol_frame_bytes
            * self.maximum_protocol_frames_per_invocation
        )
        if self.maximum_stdout_bytes < minimum_stdout:
            raise ValueError(
                "stdout budget must carry every maximum-sized protocol frame"
            )
        return self


class ObjectHandle(ContractModel):
    object_id: Identifier
    capability_scope_sha256: Sha256
    byte_length: int = Field(ge=0, le=1_073_741_824)
    mime_type: MimeType
    content_sha256: Sha256


class InlinePayload(ContractModel):
    representation: Literal["inline"]
    output_schema_sha256: Sha256
    normalized_payload: JsonValue

    @model_validator(mode="after")
    def _validate_payload(self) -> "InlinePayload":
        validate_json_value(self.normalized_payload)
        return self


class ObjectPayload(ContractModel):
    representation: Literal["object"]
    output_schema_sha256: Sha256
    object_handle: ObjectHandle


TypedPayload = Annotated[
    InlinePayload | ObjectPayload, Field(discriminator="representation")
]


class ConnectorEnvelope(Version1Contract):
    contract: Literal["ConnectorEnvelope/v1"]
    installation_id: Identifier
    tenant_id: Identifier
    request_id: Identifier
    correlation_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    connector_id: Identifier
    connector_binding_id: Identifier
    capability_id: Identifier
    operation: Operation
    resource_scope: ResourceScope
    deadline: Timestamp
    request_payload_sha256: Sha256
    connector_manifest_sha256: Sha256
    capability_grant_id: Identifier
    credential_grant_id: Identifier
    request_payload: JsonValue

    @model_validator(mode="after")
    def _validate_request_payload(self) -> "ConnectorEnvelope":
        validate_json_value(self.request_payload)
        if canonical_sha256(self.request_payload) != self.request_payload_sha256:
            raise ValueError(
                "request_payload_sha256 does not bind canonical request_payload"
            )
        return self


class ReadOk(ContractModel):
    kind: Literal["read_ok"]
    provider_resource_id: ShortText
    retrieved_at: Timestamp
    response_sha256: Sha256
    payload: TypedPayload


class EffectResponse(ContractModel):
    kind: Literal["effect_response"]
    provider_resource_id: ShortText
    provider_receipt_sha256: Sha256
    provider_time: Timestamp


class ReconcileResult(ContractModel):
    kind: Literal["reconcile_result"]
    idempotency_key: Identifier
    resource_scope: ResourceScope
    request_payload_sha256: Sha256
    result: Literal["found_same", "not_observed", "conflict", "inconclusive"]
    provider_resource_id: ShortText | None
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def _resource_presence_matches_result(self) -> "ReconcileResult":
        if self.result == "found_same" and self.provider_resource_id is None:
            raise ValueError("found_same requires the matched provider resource")
        if self.result == "not_observed" and self.provider_resource_id is not None:
            raise ValueError("not_observed cannot claim a provider resource")
        return self


class ReadbackResult(ContractModel):
    kind: Literal["readback"]
    provider_resource_id: ShortText
    idempotency_key: Identifier
    request_sha256: Sha256
    match_status: Literal["matches", "not_observed", "conflict", "inconclusive"]
    payload: TypedPayload | None
    observed_at: Timestamp

    @model_validator(mode="after")
    def _payload_presence_matches_result(self) -> "ReadbackResult":
        if self.match_status == "matches" and self.payload is None:
            raise ValueError("matches requires the current typed provider state")
        if self.match_status == "not_observed" and self.payload is not None:
            raise ValueError("not_observed cannot carry provider state")
        return self


class ProbeResult(ContractModel):
    kind: Literal["probe_result"]
    result: Literal["ready", "rate_limited", "unavailable"]
    observed_at: Timestamp
    no_provider_mutation_evidence_sha256: Sha256
    retry_after_ms: int | None = Field(ge=1_000, le=300_000)

    @model_validator(mode="after")
    def _retry_after_matches_result(self) -> "ProbeResult":
        if (self.result == "rate_limited") != (self.retry_after_ms is not None):
            raise ValueError("Retry-After is required only for a rate-limited probe")
        return self


class RateLimited(ContractModel):
    kind: Literal["rate_limited"]
    retry_after_ms: int = Field(ge=1_000, le=300_000)
    provider_code: ShortText
    evidence_sha256: Sha256


class DefinitiveReject(ContractModel):
    kind: Literal["definitive_reject"]
    provider_code: ShortText
    safe_message: ShortText
    evidence_sha256: Sha256


class AmbiguousTransport(ContractModel):
    kind: Literal["ambiguous_transport"]
    phase: Literal["before_send", "during_send", "after_possible_commit"]
    safe_message: ShortText
    evidence_sha256: Sha256


class TransientReadFailure(ContractModel):
    kind: Literal["transient_read_failure"]
    provider_code: ShortText
    safe_message: ShortText
    evidence_sha256: Sha256


class PolicyDenied(ContractModel):
    kind: Literal["policy_denied"]
    policy_code: Identifier
    safe_message: ShortText


class InternalFailure(ContractModel):
    kind: Literal["internal_failure"]
    failure_code: Identifier
    safe_message: ShortText


ConnectorOutcome = Annotated[
    ReadOk
    | EffectResponse
    | ReconcileResult
    | ReadbackResult
    | ProbeResult
    | RateLimited
    | DefinitiveReject
    | AmbiguousTransport
    | TransientReadFailure
    | PolicyDenied
    | InternalFailure,
    Field(discriminator="kind"),
]


class ConnectorResult(Version1Contract):
    contract: Literal["ConnectorResult/v1"]
    connector_id: Identifier
    request_id: Identifier
    correlation_id: Identifier
    operation: Operation
    request_sha256: Sha256
    connector_manifest_sha256: Sha256
    outcome: ConnectorOutcome
    completed_at: Timestamp

    @model_validator(mode="after")
    def _operation_matches_typed_outcome(self) -> "ConnectorResult":
        kind = self.outcome.kind
        operation_by_kind = {
            "reconcile_result": "effect.reconcile",
            "readback": "effect.readback",
            "probe_result": "connector.probe",
        }
        if kind in operation_by_kind and self.operation != operation_by_kind[kind]:
            raise ValueError(f"{kind} requires operation {operation_by_kind[kind]}")
        kind_by_operation = {
            operation: outcome_kind
            for outcome_kind, operation in operation_by_kind.items()
        }
        expected_kind = kind_by_operation.get(self.operation)
        if expected_kind is not None and kind != expected_kind:
            raise ValueError(f"{self.operation} requires outcome kind {expected_kind}")
        return self
