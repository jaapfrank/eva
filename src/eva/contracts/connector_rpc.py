"""Closed JSON-RPC 2.0 and Content-Length profile for connector workers."""

from __future__ import annotations

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
)
from .canonical_json import (
    canonical_json_bytes,
    canonical_sha256,
    require_canonical_bytes,
)
from .connector import ConnectorEnvelope, ConnectorManifest, ConnectorResult
from .execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
)

CONNECTOR_PROTOCOL_VERSION = "EvaConnectorProtocol/v1"
MAX_CONNECTOR_FRAME_BYTES = 1_048_576
MAX_CONNECTOR_EXCHANGE_BYTES = 2_101_248
_CONTENT_LENGTH_HEADER = re.compile(rb"Content-Length: ([1-9][0-9]{0,6})")

ConnectorInvokeMethod: TypeAlias = Literal[
    "resource.read",
    "effect.execute",
    "effect.reconcile",
    "effect.readback",
    "connector.probe",
]


class ConnectorInitializeParams(ContractModel):
    protocol_version: Literal["EvaConnectorProtocol/v1"]
    request_id: Identifier
    correlation_id: Identifier
    connector_id: Identifier
    connector_manifest_sha256: Sha256
    worker_id: Identifier
    worker_executable_sha256: Sha256
    connector_binding: ConnectorBinding
    capability_manifest: CapabilityManifest
    capability_grant: CapabilityGrant
    credential_grant: CredentialGrant
    deadline: Timestamp

    @model_validator(mode="after")
    def _grants_are_usable_for_this_worker(self) -> "ConnectorInitializeParams":
        capability = self.capability_grant
        capability_manifest = self.capability_manifest
        binding = self.connector_binding
        credential = self.credential_grant
        if binding.status != "verified":
            raise ValueError("connector binding is not verified")
        if binding.binding_id != capability.connector_binding_id:
            raise ValueError("connector binding differs from capability grant")
        if binding.tenant_id != capability.tenant_id:
            raise ValueError("connector binding tenant differs from capability grant")
        if capability_manifest.capability_id != capability.capability_id:
            raise ValueError("capability manifest differs from capability grant")
        if capability_manifest.connector_id != self.connector_id:
            raise ValueError("capability manifest binds another connector")
        if capability_manifest.operation != capability.operation:
            raise ValueError("capability manifest operation differs from grant")
        if capability.operation not in binding.confirmed_operations:
            raise ValueError("connector binding does not confirm the granted operation")
        if credential.connector_binding_id != capability.connector_binding_id:
            raise ValueError("credential and capability grants bind another connector")
        if credential.worker_id != self.worker_id:
            raise ValueError("credential grant binds another worker")
        if credential.executable_sha256 != self.worker_executable_sha256:
            raise ValueError("credential grant binds another worker executable")
        if capability_manifest.executable_sha256 != self.worker_executable_sha256:
            raise ValueError("capability manifest binds another worker executable")
        required_scopes = capability_manifest.required_provider_scopes
        credential_scopes = credential.provider_scopes
        confirmed_scopes = binding.confirmed_scopes
        for scopes, label in (
            (required_scopes, "capability manifest"),
            (credential_scopes, "credential grant"),
            (confirmed_scopes, "connector binding"),
        ):
            if len(scopes) != len(set(scopes)):
                raise ValueError(f"{label} provider scopes must be unique")
        if set(credential_scopes) != set(required_scopes):
            raise ValueError(
                "credential provider scopes must exactly cover the selected operation"
            )
        if not set(required_scopes).issubset(confirmed_scopes):
            raise ValueError(
                "selected operation scopes exceed the verified connector binding"
            )
        if self.deadline > capability.deadline or self.deadline > credential.deadline:
            raise ValueError("initialize deadline exceeds a bound grant deadline")
        return self


class ConnectorInitializeResult(ContractModel):
    protocol_version: Literal["EvaConnectorProtocol/v1"]
    request_id: Identifier
    correlation_id: Identifier
    connector_id: Identifier
    connector_manifest_sha256: Sha256
    worker_id: Identifier
    worker_executable_sha256: Sha256
    connector_binding_sha256: Sha256
    capability_manifest_sha256: Sha256
    capability_grant_sha256: Sha256
    credential_grant_sha256: Sha256
    initialized_at: Timestamp
    external_io_performed: Literal[False]
    secret_bytes_received: Literal[False]


class ConnectorInitializeRequest(Version1Contract):
    contract: Literal["ConnectorInitializeRequest/v1"]
    jsonrpc: Literal["2.0"]
    id: Identifier
    method: Literal["connector.initialize"]
    params: ConnectorInitializeParams

    @model_validator(mode="after")
    def _id_is_bound(self) -> "ConnectorInitializeRequest":
        if self.id != self.params.request_id:
            raise ValueError("JSON-RPC ID must equal initialize request_id")
        return self

    def validate_against_manifest(self, manifest: ConnectorManifest) -> None:
        """Bind the handshake to the preverified manifest and both grants."""

        params = self.params
        if params.protocol_version != manifest.protocol_version:
            raise ValueError("initialize protocol version differs from the manifest")
        if params.connector_id != manifest.connector_id:
            raise ValueError("initialize connector differs from the manifest")
        if params.connector_manifest_sha256 != canonical_sha256(manifest):
            raise ValueError(
                "initialize manifest hash differs from the selected manifest"
            )
        if params.worker_executable_sha256 != manifest.executable_sha256:
            raise ValueError("initialize worker hash differs from the manifest")
        if manifest.maximum_protocol_frame_bytes != MAX_CONNECTOR_FRAME_BYTES:
            raise ValueError("manifest frame budget differs from the sealed profile")
        if manifest.maximum_protocol_frames_per_invocation != 2:
            raise ValueError("manifest frame count differs from the sealed profile")
        if manifest.maximum_stdout_bytes != MAX_CONNECTOR_EXCHANGE_BYTES:
            raise ValueError("manifest stdout budget differs from the sealed profile")
        capability_manifest = params.capability_manifest
        if capability_manifest.connector_id != manifest.connector_id:
            raise ValueError("capability manifest connector differs from manifest")
        if capability_manifest.executable_sha256 != manifest.executable_sha256:
            raise ValueError("capability manifest executable differs from manifest")
        if (
            capability_manifest.sandbox_profile_sha256
            != manifest.sandbox_profile_sha256
        ):
            raise ValueError(
                "capability sandbox profile differs from connector manifest"
            )
        operations = {item.operation: item for item in manifest.operations}
        operation = operations.get(capability_manifest.operation)
        if operation is None:
            raise ValueError("capability operation is absent from connector manifest")
        if operation.input_schema_sha256 != capability_manifest.input_schema_sha256:
            raise ValueError("capability input schema differs from connector manifest")
        if operation.output_schema_sha256 != capability_manifest.output_schema_sha256:
            raise ValueError("capability output schema differs from connector manifest")
        write_effect_classes = {
            "effect",
            "confirmation_delivery_effect",
            "outcome_delivery_effect",
        }
        if operation.writes_provider_state != (
            capability_manifest.effect_class in write_effect_classes
        ):
            raise ValueError("capability effect class differs from connector manifest")
        reserved_effect_classes = {
            "effect.reconcile": "recovery_read",
            "effect.readback": "verification_read",
            "connector.probe": "breaker_probe_read",
        }
        expected_effect_class = reserved_effect_classes.get(
            capability_manifest.operation
        )
        if (
            expected_effect_class is not None
            and capability_manifest.effect_class != expected_effect_class
        ):
            raise ValueError("reserved operation has the wrong capability effect class")


class ConnectorInitializeSuccess(Version1Contract):
    contract: Literal["ConnectorInitializeSuccess/v1"]
    jsonrpc: Literal["2.0"]
    id: Identifier
    result: ConnectorInitializeResult

    @model_validator(mode="after")
    def _id_is_bound(self) -> "ConnectorInitializeSuccess":
        if self.id != self.result.request_id:
            raise ValueError("JSON-RPC ID must equal initialize result request_id")
        return self

    def validate_against_request(self, request: ConnectorInitializeRequest) -> None:
        """Reject a response replayed from another handshake or authority set."""

        result = self.result
        params = request.params
        if self.id != request.id:
            raise ValueError("initialize response ID differs from its request")
        for result_value, request_value, label in (
            (result.request_id, params.request_id, "request"),
            (result.correlation_id, params.correlation_id, "correlation"),
            (result.protocol_version, params.protocol_version, "protocol"),
            (result.connector_id, params.connector_id, "connector"),
            (
                result.connector_manifest_sha256,
                params.connector_manifest_sha256,
                "manifest",
            ),
            (result.worker_id, params.worker_id, "worker"),
            (
                result.worker_executable_sha256,
                params.worker_executable_sha256,
                "worker executable",
            ),
        ):
            if result_value != request_value:
                raise ValueError(f"initialize response {label} binding differs")
        if result.capability_grant_sha256 != canonical_sha256(params.capability_grant):
            raise ValueError("initialize response capability grant hash differs")
        if result.capability_manifest_sha256 != canonical_sha256(
            params.capability_manifest
        ):
            raise ValueError("initialize response capability manifest hash differs")
        if result.connector_binding_sha256 != canonical_sha256(
            params.connector_binding
        ):
            raise ValueError("initialize response connector binding hash differs")
        if result.credential_grant_sha256 != canonical_sha256(params.credential_grant):
            raise ValueError("initialize response credential grant hash differs")
        if result.initialized_at > params.deadline:
            raise ValueError("initialize response was produced after its deadline")


class ConnectorInvokeRequest(Version1Contract):
    contract: Literal["ConnectorInvokeRequest/v1"]
    jsonrpc: Literal["2.0"]
    id: Identifier
    method: ConnectorInvokeMethod
    params: ConnectorEnvelope

    @model_validator(mode="after")
    def _id_and_reserved_method_are_bound(self) -> "ConnectorInvokeRequest":
        if self.id != self.params.request_id:
            raise ValueError("JSON-RPC ID must equal ConnectorEnvelope request_id")
        reserved_methods = {
            "effect.reconcile": "effect.reconcile",
            "effect.readback": "effect.readback",
            "connector.probe": "connector.probe",
        }
        expected = reserved_methods.get(self.params.operation)
        if expected is not None and self.method != expected:
            raise ValueError(
                "reserved connector operation requires its exact RPC method"
            )
        if (
            self.method in reserved_methods.values()
            and self.params.operation != self.method
        ):
            raise ValueError(
                "reserved RPC method requires its exact connector operation"
            )
        return self

    def validate_against_initialize(
        self,
        initialize: ConnectorInitializeRequest,
        manifest: ConnectorManifest,
    ) -> None:
        """Bind invocation, manifest and complete authority from the handshake."""

        initialize.validate_against_manifest(manifest)
        if self.id == initialize.id:
            raise ValueError("initialize and invoke require phase-unique JSON-RPC IDs")
        envelope = self.params
        init = initialize.params
        capability = init.capability_grant
        binding = init.connector_binding
        credential = init.credential_grant
        if envelope.correlation_id != init.correlation_id:
            raise ValueError("invoke correlation differs from initialize")
        if envelope.connector_id != init.connector_id:
            raise ValueError("invoke connector differs from initialize")
        if envelope.connector_manifest_sha256 != init.connector_manifest_sha256:
            raise ValueError("invoke manifest hash differs from initialize")
        if envelope.installation_id != binding.installation_id:
            raise ValueError("invoke installation differs from connector binding")
        if envelope.tenant_id != binding.tenant_id:
            raise ValueError("invoke tenant differs from connector binding")
        if envelope.tenant_id != capability.tenant_id:
            raise ValueError("invoke tenant differs from capability grant")
        if envelope.episode_id != capability.episode_id:
            raise ValueError("invoke episode differs from capability grant")
        if envelope.generation != capability.generation:
            raise ValueError("invoke generation differs from capability grant")
        if envelope.capability_id != capability.capability_id:
            raise ValueError("invoke capability differs from capability grant")
        if envelope.connector_binding_id != capability.connector_binding_id:
            raise ValueError("invoke binding differs from capability grant")
        if envelope.capability_grant_id != capability.grant_id:
            raise ValueError("invoke capability grant reference differs")
        if envelope.credential_grant_id != credential.credential_grant_id:
            raise ValueError("invoke credential grant reference differs")
        if envelope.operation != capability.operation:
            raise ValueError("invoke operation differs from capability grant")
        if envelope.resource_scope != capability.resource_scope:
            raise ValueError("invoke resource differs from capability grant")
        if envelope.request_payload_sha256 != capability.authorized_payload_sha256:
            raise ValueError("invoke payload differs from capability grant")
        if envelope.deadline > init.deadline:
            raise ValueError("invoke deadline exceeds initialize deadline")

        operations = {item.operation: item for item in manifest.operations}
        operation = operations.get(envelope.operation)
        if operation is None:
            raise ValueError("invoke operation is absent from the selected manifest")
        if envelope.operation in {
            "effect.reconcile",
            "effect.readback",
            "connector.probe",
        }:
            if operation.writes_provider_state:
                raise ValueError("recovery and probe methods must be read-only")
            return
        expected_method = (
            "effect.execute" if operation.writes_provider_state else "resource.read"
        )
        if self.method != expected_method:
            raise ValueError("RPC method differs from manifest operation effect class")


class ConnectorInvokeSuccess(Version1Contract):
    contract: Literal["ConnectorInvokeSuccess/v1"]
    jsonrpc: Literal["2.0"]
    id: Identifier
    result: ConnectorResult

    @model_validator(mode="after")
    def _id_is_bound(self) -> "ConnectorInvokeSuccess":
        if self.id != self.result.request_id:
            raise ValueError("JSON-RPC ID must equal ConnectorResult request_id")
        return self

    def validate_against_request(
        self, request: ConnectorInvokeRequest, manifest: ConnectorManifest
    ) -> None:
        """Bind the successful result to the exact canonical envelope."""

        result = self.result
        envelope = request.params
        if envelope.connector_manifest_sha256 != canonical_sha256(manifest):
            raise ValueError("invoke request does not bind the selected manifest")
        if envelope.connector_id != manifest.connector_id:
            raise ValueError("invoke request connector differs from selected manifest")
        operations = {item.operation: item for item in manifest.operations}
        operation = operations.get(envelope.operation)
        if operation is None:
            raise ValueError("invoke operation is absent from the selected manifest")
        if envelope.operation not in {
            "effect.reconcile",
            "effect.readback",
            "connector.probe",
        }:
            expected_method = (
                "effect.execute" if operation.writes_provider_state else "resource.read"
            )
            if request.method != expected_method:
                raise ValueError(
                    "invoke method differs from selected manifest effect class"
                )
        if self.id != request.id:
            raise ValueError("invoke response ID differs from its request")
        for result_value, request_value, label in (
            (result.request_id, envelope.request_id, "request"),
            (result.correlation_id, envelope.correlation_id, "correlation"),
            (result.connector_id, envelope.connector_id, "connector"),
            (result.operation, envelope.operation, "operation"),
            (
                result.connector_manifest_sha256,
                envelope.connector_manifest_sha256,
                "manifest",
            ),
        ):
            if result_value != request_value:
                raise ValueError(f"invoke response {label} binding differs")
        if result.request_sha256 != canonical_sha256(envelope):
            raise ValueError("ConnectorResult request_sha256 differs from its envelope")
        if result.completed_at > envelope.deadline:
            raise ValueError("ConnectorResult completed after the invocation deadline")

        outcome = result.outcome
        failure_kinds = {
            "rate_limited",
            "definitive_reject",
            "ambiguous_transport",
            "transient_read_failure",
            "policy_denied",
            "internal_failure",
        }
        expected_kind = {
            "effect.reconcile": "reconcile_result",
            "effect.readback": "readback",
            "connector.probe": "probe_result",
        }.get(envelope.operation)
        if expected_kind is None:
            expected_kind = (
                "effect_response" if operation.writes_provider_state else "read_ok"
            )
        if outcome.kind not in failure_kinds and outcome.kind != expected_kind:
            raise ValueError(
                "ConnectorResult outcome kind differs from manifest effect class"
            )
        if (
            outcome.kind == "ambiguous_transport"
            and not operation.writes_provider_state
        ):
            raise ValueError("read-only connector result cannot be ambiguous transport")
        if outcome.kind == "transient_read_failure" and operation.writes_provider_state:
            raise ValueError(
                "write connector result cannot claim transient read failure"
            )
        if outcome.kind == "read_ok":
            if outcome.payload.output_schema_sha256 != operation.output_schema_sha256:
                raise ValueError(
                    "ConnectorResult payload schema differs from connector manifest"
                )
        if outcome.kind == "readback" and outcome.payload is not None:
            if outcome.payload.output_schema_sha256 != operation.output_schema_sha256:
                raise ValueError(
                    "ConnectorResult readback schema differs from connector manifest"
                )


ConnectorRpcErrorKind: TypeAlias = Literal[
    "method_not_found",
    "invalid_params",
    "internal_error",
    "worker_failure",
]

ConnectorProtocolViolation: TypeAlias = Literal[
    "framing_error",
    "unexpected_eof",
    "frame_too_large",
    "extra_frame",
    "invalid_json",
    "invalid_message",
    "timeout",
    "manifest_mismatch",
    "executable_mismatch",
    "response_id_mismatch",
]
ConnectorProtocolPhase: TypeAlias = Literal[
    "initialize",
    "invoke_before_external_io",
    "invoke_external_io_possible",
]


class ConnectorRpcError(ContractModel):
    code: Literal[-32601, -32602, -32603, -32000]
    kind: ConnectorRpcErrorKind
    message: ShortText
    failure_code: Identifier
    external_io_possible: bool
    failure_class: Literal["internal_failure", "ambiguous_transport"]

    @model_validator(mode="after")
    def _code_and_failure_class_are_closed(self) -> "ConnectorRpcError":
        expected_code = {
            "method_not_found": -32601,
            "invalid_params": -32602,
            "internal_error": -32603,
            "worker_failure": -32000,
        }[self.kind]
        if self.code != expected_code:
            raise ValueError("JSON-RPC error code differs from its typed kind")
        expected_message = {
            "method_not_found": "Method not found",
            "invalid_params": "Invalid params",
            "internal_error": "Internal error",
            "worker_failure": "Worker failure",
        }[self.kind]
        if self.message != expected_message:
            raise ValueError("JSON-RPC error message differs from its typed kind")
        if self.kind in {"method_not_found", "invalid_params"} and (
            self.external_io_possible
        ):
            raise ValueError("pre-dispatch JSON-RPC errors cannot claim external I/O")
        expected_class = (
            "ambiguous_transport" if self.external_io_possible else "internal_failure"
        )
        if self.failure_class != expected_class:
            raise ValueError(
                "connector failure class understates external-I/O ambiguity"
            )
        return self


class ConnectorErrorResponse(Version1Contract):
    contract: Literal["ConnectorErrorResponse/v1"]
    jsonrpc: Literal["2.0"]
    id: Identifier
    request_contract: Literal[
        "ConnectorInitializeRequest/v1", "ConnectorInvokeRequest/v1"
    ]
    request_method: Literal[
        "connector.initialize",
        "resource.read",
        "effect.execute",
        "effect.reconcile",
        "effect.readback",
        "connector.probe",
    ]
    request_sha256: Sha256
    error: ConnectorRpcError

    @model_validator(mode="after")
    def _request_phase_is_closed(self) -> "ConnectorErrorResponse":
        initialize = self.request_contract == "ConnectorInitializeRequest/v1"
        if initialize != (self.request_method == "connector.initialize"):
            raise ValueError("connector error request contract and method phase differ")
        if initialize and self.error.external_io_possible:
            raise ValueError(
                "connector.initialize error cannot claim external provider I/O"
            )
        return self

    def validate_against_request(
        self,
        request: ConnectorInitializeRequest | ConnectorInvokeRequest,
        *,
        external_io_may_have_started: bool,
    ) -> None:
        """Bind an error ID and the supervisor's durable ambiguity classification."""

        if self.id != request.id:
            raise ValueError("connector error response ID differs from its request")
        if self.request_contract != request.contract:
            raise ValueError("connector error response binds another request contract")
        if self.request_method != request.method:
            raise ValueError("connector error response binds another request method")
        if self.request_sha256 != canonical_sha256(request):
            raise ValueError(
                "connector error response binds other canonical request bytes"
            )
        if (
            isinstance(request, ConnectorInitializeRequest)
            and self.error.external_io_possible
        ):
            raise ValueError("connector.initialize cannot claim external provider I/O")
        if self.error.external_io_possible != external_io_may_have_started:
            raise ValueError(
                "connector error response conflicts with supervisor I/O phase"
            )


class ConnectorProtocolFailure(Version1Contract):
    """Durable host classification for a fatal supervisor protocol observation."""

    contract: Literal["ConnectorProtocolFailure/v1"]
    request_id: Identifier
    request_sha256: Sha256
    phase: ConnectorProtocolPhase
    violation: ConnectorProtocolViolation
    failure_class: Literal["internal_failure", "ambiguous_transport"]
    automatic_resend_permitted: Literal[False]
    evidence_sha256: Sha256
    observed_at: Timestamp

    @model_validator(mode="after")
    def _phase_closes_failure_class(self) -> "ConnectorProtocolFailure":
        expected = (
            "ambiguous_transport"
            if self.phase == "invoke_external_io_possible"
            else "internal_failure"
        )
        if self.failure_class != expected:
            raise ValueError(
                "protocol failure class understates or overstates I/O phase"
            )
        return self


def classify_connector_protocol_failure(
    *,
    request_id: Identifier,
    request_sha256: Sha256,
    phase: ConnectorProtocolPhase,
    violation: ConnectorProtocolViolation,
    evidence_sha256: Sha256,
    observed_at: Timestamp,
) -> ConnectorProtocolFailure:
    """Create the only durable classification for a supervisor-fatal failure."""

    failure_class: Literal["internal_failure", "ambiguous_transport"] = (
        "ambiguous_transport"
        if phase == "invoke_external_io_possible"
        else "internal_failure"
    )
    return ConnectorProtocolFailure.model_validate(
        {
            "contract": "ConnectorProtocolFailure/v1",
            "schema_version": 1,
            "request_id": request_id,
            "request_sha256": request_sha256,
            "phase": phase,
            "violation": violation,
            "failure_class": failure_class,
            "automatic_resend_permitted": False,
            "evidence_sha256": evidence_sha256,
            "observed_at": observed_at,
        }
    )


ConnectorRpcMessage: TypeAlias = Annotated[
    ConnectorInitializeRequest
    | ConnectorInitializeSuccess
    | ConnectorInvokeRequest
    | ConnectorInvokeSuccess
    | ConnectorErrorResponse,
    Field(discriminator="contract"),
]

_RPC_CONTRACT_TYPES: dict[str, type[ContractModel]] = {
    "ConnectorErrorResponse/v1": ConnectorErrorResponse,
    "ConnectorInitializeRequest/v1": ConnectorInitializeRequest,
    "ConnectorInitializeSuccess/v1": ConnectorInitializeSuccess,
    "ConnectorInvokeRequest/v1": ConnectorInvokeRequest,
    "ConnectorInvokeSuccess/v1": ConnectorInvokeSuccess,
}


def encode_connector_rpc_frame(message: ContractModel) -> bytes:
    """Encode one canonical RPC body with the only allowed framing header."""

    if type(message) not in _RPC_CONTRACT_TYPES.values():
        raise TypeError("only a registered connector RPC message can be framed")
    body = canonical_json_bytes(message)
    if len(body) > MAX_CONNECTOR_FRAME_BYTES:
        raise ValueError("connector RPC body exceeds the 1 MiB frame budget")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def decode_connector_rpc_frame_prefix(
    stream: bytes,
) -> tuple[ContractModel, bytes]:
    """Decode exactly the first frame and return untouched subsequent bytes."""

    if not isinstance(stream, bytes):
        raise TypeError("connector protocol input must be bytes")
    marker = stream.find(b"\r\n\r\n")
    if marker < 0:
        raise ValueError("incomplete connector Content-Length header")
    header = stream[:marker]
    match = _CONTENT_LENGTH_HEADER.fullmatch(header)
    if match is None:
        raise ValueError("connector frame must have one exact Content-Length header")
    content_length = int(match.group(1))
    if content_length > MAX_CONNECTOR_FRAME_BYTES:
        raise ValueError("connector RPC body exceeds the 1 MiB frame budget")
    body_start = marker + 4
    body_end = body_start + content_length
    if len(stream) < body_end:
        raise ValueError("incomplete connector RPC body")
    body = stream[body_start:body_end]
    decoded = require_canonical_bytes(body, maximum_bytes=MAX_CONNECTOR_FRAME_BYTES)
    contract_name = decoded.get("contract")
    model = (
        _RPC_CONTRACT_TYPES.get(contract_name)
        if isinstance(contract_name, str)
        else None
    )
    if model is None:
        raise ValueError("connector frame contains an unknown RPC contract")
    return model.model_validate(decoded), stream[body_end:]


def decode_connector_rpc_frame(frame: bytes) -> ContractModel:
    """Decode one frame and reject logs, another frame, or any trailing byte."""

    message, remainder = decode_connector_rpc_frame_prefix(frame)
    if remainder:
        raise ValueError("single connector frame contains trailing bytes")
    return message


def decode_connector_rpc_exchange(
    stream: bytes,
) -> tuple[
    ConnectorInitializeSuccess | ConnectorInvokeSuccess | ConnectorErrorResponse, ...
]:
    """Decode only a complete, closed short-worker stdout response sequence."""

    if len(stream) > MAX_CONNECTOR_EXCHANGE_BYTES:
        raise ValueError("connector exchange exceeds the stdout byte budget")
    first, remainder = decode_connector_rpc_frame_prefix(stream)
    if isinstance(first, ConnectorErrorResponse):
        if first.request_contract != "ConnectorInitializeRequest/v1":
            raise ValueError("first connector response must close initialize")
        if remainder:
            raise ValueError("initialize error must be the only stdout frame")
        return (first,)
    if not isinstance(first, ConnectorInitializeSuccess):
        raise ValueError("first connector response must be initialize success or error")
    if not remainder:
        raise ValueError("initialize success requires one operation response")
    second, remainder = decode_connector_rpc_frame_prefix(remainder)
    if remainder:
        raise ValueError("connector exchange contains more than two frames")
    if isinstance(second, ConnectorErrorResponse):
        if second.request_contract != "ConnectorInvokeRequest/v1":
            raise ValueError("second connector error must bind invoke")
    elif not isinstance(second, ConnectorInvokeSuccess):
        raise ValueError("second connector response must close invoke")
    return first, second
