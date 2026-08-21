"""Content-addressed, networkless 4D model fixture interpreter.

The sealed artifact defines canonical program data, not a subprocess protocol.
This module therefore provides the smallest honest runtime boundary: it verifies
the sealed bytes once and interprets the closed decision table in process.  It
does not validate model proposals into host authority and it does not own
durable one-use grant consumption.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast

from eva.contracts.canonical_json import (
    canonical_json_bytes,
    decode_json_bytes,
    require_canonical_bytes,
)
from eva.contracts.execution import CredentialGrant
from eva.contracts.planning import ModelAttempt, ModelPurpose, PlannerRequest


MODEL_ARTIFACT_SHA256 = (
    "3ac74e540ea49f43731113efd874af35acc0a31cd5340495a475c34d73081b3c"
)
MODEL_MANIFEST_SHA256 = (
    "6652e778e6b5668c63dc22457b50e490d7b6fe81829f33e8df4b9f4274f469d7"
)
MODEL_PROGRAM_SHA256 = (
    "2874df00da8d78e23184c085b745f10e5449d2f8c605d21a647bed097de6aa62"
)
MODEL_PROVIDER_PROFILE_ID = "fixture:model-provider:noor-4d:001"
MODEL_WORKER_ID = "worker:model-fixture-adapter:001"
MODEL_SECRET_DESCRIPTOR_ID = "secret-descriptor:model-fixture:001"

_MANIFEST_BYTE_LENGTH = 753
_PROGRAM_BYTE_LENGTH = 31_469
_CANONICAL_PROFILE = "EvaCanonicalJSON/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_LOAD_TOKEN = object()

_ARTIFACT_KEYS = frozenset(
    {
        "authorityBindings",
        "canonicalByteRule",
        "connectorAuthorityCrosswalk",
        "contract",
        "correctionBeforeFirstEffect",
        "decisionDate",
        "exactCallDeltaOracles",
        "fixtureAdapterProfile",
        "happyPathVectors",
        "modelAdapterManifest",
        "modelDispatchVectors",
        "modelFixtureProgram",
        "negativeMutationOracles",
        "nonClaims",
        "packBindings",
        "planToConnectorCalls",
        "preCodeBoundary",
        "presenceAndContextSelection",
        "provider429Recovery",
        "providerFailureAndAmbiguity",
        "schemaSnapshotBinding",
        "staticValidation",
        "status",
    }
)
_BYTE_METADATA_KEYS = frozenset(
    {
        "base64",
        "bom",
        "byteLength",
        "encoding",
        "profile",
        "sha256",
        "trailingLf",
    }
)
_PROGRAM_KEYS = frozenset(
    {
        "blobCatalog",
        "canonicalProfile",
        "contract",
        "decisionTable",
        "defaultAction",
        "environmentReads",
        "guards",
        "interpreterRules",
        "programId",
        "randomness",
        "schemaVersion",
        "selectorFields",
        "zeroNetwork",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "acceptedInputContract",
        "adapterId",
        "adapterVersion",
        "canonicalProfile",
        "contract",
        "internalRetries",
        "interpreterProfile",
        "issuedAt",
        "memory",
        "modelProgramByteLength",
        "modelProgramContract",
        "modelProgramSha256",
        "networkAllowed",
        "outputContracts",
        "providerProfileId",
        "schemaVersion",
        "sessions",
        "tools",
        "workerId",
    }
)

FixtureResultKind: TypeAlias = Literal[
    "return-contract-bytes", "return-http-result", "drop-after-dispatch"
]
FixtureProviderState: TypeAlias = Literal[
    "responded", "rate_limited", "provider_rejected", "ambiguous"
]
FixtureRejectReason: TypeAlias = Literal[
    "invalid_selector",
    "blocked_by_guard",
    "rejected_by_guard",
    "invalid_planner_request",
    "invalid_credential_grant",
    "unknown_selector",
]
FixtureSelector: TypeAlias = tuple[str, ModelPurpose, str, int, str]


class FixtureModelError(ValueError):
    """Base error for the closed model fixture boundary."""


class FixtureProgramError(FixtureModelError):
    """The content-addressed artifact, manifest, program, or blob is invalid."""


class FixtureDispatchRejected(FixtureModelError):
    """A selector or authority binding failed before provider dispatch."""

    logical_provider_call_delta = 0
    actual_network_call_delta = 0
    hidden_retry_delta = 0

    def __init__(self, reason: FixtureRejectReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FixtureInvocationResult:
    """Literal result of one dispatched fixture-program row."""

    kind: FixtureResultKind
    provider_state: FixtureProviderState
    provider_reference: str | None
    response_bytes: bytes | None
    response_sha256: str | None
    row_ordinal: int
    logical_provider_call_delta: int
    actual_network_call_delta: int
    hidden_retry_delta: int


@dataclass(frozen=True, slots=True)
class _Blob:
    blob_id: str
    data: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _ProgramRow:
    selector: FixtureSelector
    row_ordinal: int
    kind: FixtureResultKind
    blob: _Blob | None
    logical_provider_call_delta: int
    actual_network_call_delta: int
    hidden_retry_delta: int


@dataclass(frozen=True, slots=True)
class _RequestBinding:
    data: bytes
    request: PlannerRequest


@dataclass(frozen=True, slots=True)
class _DispatchBinding:
    grant_bytes: bytes
    provider_state: FixtureProviderState
    provider_reference: str | None
    response_sha256: str | None


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise FixtureProgramError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _array(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise FixtureProgramError(f"{label} must be an array")
    return cast(list[Any], value)


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise FixtureProgramError(f"{label} does not have the sealed field set")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _decode_byte_metadata(
    value: Any,
    *,
    label: str,
    attached_payload: Any | None = None,
    expected_sha256: str | None = None,
    expected_byte_length: int | None = None,
    extra_keys: frozenset[str] = frozenset(),
) -> bytes:
    metadata = _mapping(value, label)
    _exact_keys(metadata, _BYTE_METADATA_KEYS | extra_keys, label)
    if (
        metadata["encoding"] != "base64"
        or metadata["profile"] != _CANONICAL_PROFILE
        or metadata["bom"] is not False
        or metadata["trailingLf"] is not False
        or type(metadata["byteLength"]) is not int
        or not isinstance(metadata["sha256"], str)
        or _SHA256_RE.fullmatch(metadata["sha256"]) is None
        or not isinstance(metadata["base64"], str)
    ):
        raise FixtureProgramError(f"{label} byte metadata is not sealed")
    try:
        raw = base64.b64decode(metadata["base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise FixtureProgramError(f"{label} is not strict base64") from exc
    actual_sha256 = _sha256(raw)
    if len(raw) != metadata["byteLength"] or actual_sha256 != metadata["sha256"]:
        raise FixtureProgramError(f"{label} length or SHA-256 differs")
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise FixtureProgramError(f"{label} SHA-256 is not the sealed address")
    if expected_byte_length is not None and len(raw) != expected_byte_length:
        raise FixtureProgramError(f"{label} byte length is not sealed")
    if raw.startswith(b"\xef\xbb\xbf") or raw.endswith(b"\n"):
        raise FixtureProgramError(f"{label} BOM or trailing-LF metadata differs")
    try:
        decoded = require_canonical_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise FixtureProgramError(f"{label} is not canonical JSON") from exc
    if attached_payload is not None and decoded != attached_payload:
        raise FixtureProgramError(f"{label} does not encode its attached payload")
    return raw


def _validate_manifest(artifact: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    wrapper = _mapping(artifact["modelAdapterManifest"], "model manifest wrapper")
    _exact_keys(
        wrapper, frozenset({"canonicalBytes", "payload"}), "model manifest wrapper"
    )
    payload = _mapping(wrapper["payload"], "model manifest")
    _exact_keys(payload, _MANIFEST_KEYS, "model manifest")
    raw = _decode_byte_metadata(
        wrapper["canonicalBytes"],
        label="model manifest bytes",
        attached_payload=payload,
        expected_sha256=MODEL_MANIFEST_SHA256,
        expected_byte_length=_MANIFEST_BYTE_LENGTH,
    )
    if (
        payload["contract"] != "ModelAdapterManifest/v1"
        or payload["schemaVersion"] != 1
        or payload["canonicalProfile"] != _CANONICAL_PROFILE
        or payload["acceptedInputContract"] != "PlannerRequest/v1"
        or payload["adapterId"] != "fixture:model-adapter:noor-4d:001"
        or payload["adapterVersion"] != "1.1.0"
        or payload["interpreterProfile"]
        != "closed ModelFixtureProgram/v1 decision table"
        or payload["modelProgramContract"] != "ModelFixtureProgram/v1"
        or payload["modelProgramSha256"] != MODEL_PROGRAM_SHA256
        or payload["modelProgramByteLength"] != _PROGRAM_BYTE_LENGTH
        or payload["outputContracts"]
        != ["ModelDecision/v1", "DocumentDraft/v1", "ReplyCandidate/v1"]
        or payload["providerProfileId"] != MODEL_PROVIDER_PROFILE_ID
        or payload["workerId"] != MODEL_WORKER_ID
        or payload["internalRetries"] != 0
        or payload["networkAllowed"] is not False
        or payload["memory"] is not False
        or payload["sessions"] is not False
        or payload["tools"] is not False
    ):
        raise FixtureProgramError("model manifest semantics are not sealed")
    return raw, payload


def _validate_fixture_profile(artifact: dict[str, Any]) -> None:
    profile = _mapping(artifact["fixtureAdapterProfile"], "fixture adapter profile")
    expected = {
        "credentialGrantExecutableSha256Meaning",
        "executableHashStoredOrReserved",
        "futureAdapterRule",
        "hiddenRetries",
        "memory",
        "modelAdapterManifestAddress",
        "modelAdapterManifestSha256",
        "networkAllowed",
        "networkAllowlist",
        "outputRetries",
        "profileId",
        "programAddress",
        "programIsDataNotExecutable",
        "programSha256",
        "providerSdkRetries",
        "sessions",
        "tools",
        "transport",
        "validationRetries",
    }
    _exact_keys(profile, frozenset(expected), "fixture adapter profile")
    if (
        profile["profileId"] != MODEL_PROVIDER_PROFILE_ID
        or profile["modelAdapterManifestSha256"] != MODEL_MANIFEST_SHA256
        or profile["modelAdapterManifestAddress"] != f"sha256:{MODEL_MANIFEST_SHA256}"
        or profile["programSha256"] != MODEL_PROGRAM_SHA256
        or profile["programAddress"] != f"sha256:{MODEL_PROGRAM_SHA256}"
        or profile["programIsDataNotExecutable"] is not True
        or profile["transport"] != "generic canonical-byte fixture interpreter"
        or profile["networkAllowed"] is not False
        or profile["networkAllowlist"] != []
        or profile["tools"] is not False
        or profile["sessions"] is not False
        or profile["memory"] is not False
        or any(
            profile[field] != 0
            for field in (
                "hiddenRetries",
                "outputRetries",
                "providerSdkRetries",
                "validationRetries",
            )
        )
    ):
        raise FixtureProgramError("fixture adapter profile is not sealed")


def _load_blobs(program: dict[str, Any]) -> dict[str, _Blob]:
    raw_catalog = _array(program["blobCatalog"], "model blob catalog")
    if len(raw_catalog) != 16:
        raise FixtureProgramError("model blob catalog must contain exactly 16 blobs")
    blobs: dict[str, _Blob] = {}
    for index, value in enumerate(raw_catalog, start=1):
        metadata = _mapping(value, f"model blob {index}")
        blob_id = metadata.get("blobId")
        if not isinstance(blob_id, str) or not blob_id or blob_id in blobs:
            raise FixtureProgramError("model blob IDs must be distinct strings")
        raw = _decode_byte_metadata(
            metadata,
            label=f"model blob {blob_id}",
            extra_keys=frozenset({"blobId"}),
        )
        blobs[blob_id] = _Blob(
            blob_id=blob_id,
            data=raw,
            sha256=cast(str, metadata["sha256"]),
        )
    return blobs


def _selector(row: dict[str, Any], label: str) -> FixtureSelector:
    fixture_case_id = row.get("fixtureCaseId")
    selector = _mapping(row.get("selector"), f"{label} selector")
    _exact_keys(
        selector,
        frozenset({"attemptOrdinal", "dispatchAt", "plannerRequestSha256", "purpose"}),
        f"{label} selector",
    )
    purpose = selector["purpose"]
    request_sha256 = selector["plannerRequestSha256"]
    attempt_ordinal = selector["attemptOrdinal"]
    dispatch_at = selector["dispatchAt"]
    if (
        not isinstance(fixture_case_id, str)
        or not fixture_case_id
        or purpose not in {"plan", "document_draft", "reply"}
        or not isinstance(request_sha256, str)
        or _SHA256_RE.fullmatch(request_sha256) is None
        or type(attempt_ordinal) is not int
        or attempt_ordinal < 1
        or not _canonical_timestamp(dispatch_at)
    ):
        raise FixtureProgramError(f"{label} selector is malformed")
    return (
        fixture_case_id,
        cast(ModelPurpose, purpose),
        request_sha256,
        attempt_ordinal,
        cast(str, dispatch_at),
    )


def _load_rows(
    program: dict[str, Any], blobs: dict[str, _Blob]
) -> tuple[tuple[_ProgramRow, ...], dict[FixtureSelector, _ProgramRow]]:
    raw_rows = _array(program["decisionTable"], "model decision table")
    if len(raw_rows) != 20:
        raise FixtureProgramError("model decision table must contain exactly 20 rows")
    rows: list[_ProgramRow] = []
    by_selector: dict[FixtureSelector, _ProgramRow] = {}
    referenced_blobs: set[str] = set()
    for expected_ordinal, value in enumerate(raw_rows, start=1):
        raw_row = _mapping(value, f"model row {expected_ordinal}")
        _exact_keys(
            raw_row,
            frozenset({"action", "fixtureCaseId", "rowOrdinal", "selector"}),
            f"model row {expected_ordinal}",
        )
        if raw_row["rowOrdinal"] != expected_ordinal:
            raise FixtureProgramError("model row ordinals are not contiguous")
        selector = _selector(raw_row, f"model row {expected_ordinal}")
        if selector in by_selector:
            raise FixtureProgramError("model decision selectors are not unique")
        action = _mapping(raw_row["action"], f"model row {expected_ordinal} action")
        kind = action.get("kind")
        common_keys = {
            "actualNetworkCallDelta",
            "hiddenRetryDelta",
            "kind",
            "logicalProviderCallDelta",
        }
        has_blob = kind in {"return-contract-bytes", "return-http-result"}
        expected_keys = common_keys | ({"blobId", "blobSha256"} if has_blob else set())
        _exact_keys(
            action, frozenset(expected_keys), f"model row {expected_ordinal} action"
        )
        if (
            kind
            not in {
                "return-contract-bytes",
                "return-http-result",
                "drop-after-dispatch",
            }
            or action["logicalProviderCallDelta"] != 1
            or action["actualNetworkCallDelta"] != 0
            or action["hiddenRetryDelta"] != 0
        ):
            raise FixtureProgramError("model row action or call deltas are not sealed")
        blob: _Blob | None = None
        if has_blob:
            blob_id = action["blobId"]
            if not isinstance(blob_id, str) or blob_id not in blobs:
                raise FixtureProgramError("model row references an unknown blob")
            blob = blobs[blob_id]
            if action["blobSha256"] != blob.sha256:
                raise FixtureProgramError("model row blob hash differs")
            referenced_blobs.add(blob_id)
        row = _ProgramRow(
            selector=selector,
            row_ordinal=expected_ordinal,
            kind=cast(FixtureResultKind, kind),
            blob=blob,
            logical_provider_call_delta=1,
            actual_network_call_delta=0,
            hidden_retry_delta=0,
        )
        rows.append(row)
        by_selector[selector] = row
    if referenced_blobs != set(blobs):
        raise FixtureProgramError("model blob catalog contains an unreferenced blob")
    return tuple(rows), by_selector


def _validate_program(
    artifact: dict[str, Any], manifest: dict[str, Any]
) -> tuple[bytes, tuple[_ProgramRow, ...], dict[FixtureSelector, _ProgramRow]]:
    wrapper = _mapping(artifact["modelFixtureProgram"], "model program wrapper")
    _exact_keys(
        wrapper, frozenset({"canonicalBytes", "program"}), "model program wrapper"
    )
    program = _mapping(wrapper["program"], "model fixture program")
    _exact_keys(program, _PROGRAM_KEYS, "model fixture program")
    raw = _decode_byte_metadata(
        wrapper["canonicalBytes"],
        label="model fixture program bytes",
        attached_payload=program,
        expected_sha256=MODEL_PROGRAM_SHA256,
        expected_byte_length=_PROGRAM_BYTE_LENGTH,
    )
    if (
        program["contract"] != "ModelFixtureProgram/v1"
        or program["schemaVersion"] != 1
        or program["canonicalProfile"] != _CANONICAL_PROFILE
        or program["programId"] != "fixture:model-program:noor-4d:001"
        or program["selectorFields"]
        != [
            "fixtureCaseId",
            "purpose",
            "plannerRequestSha256",
            "attemptOrdinal",
            "dispatchAt",
        ]
        or program["environmentReads"] != []
        or program["randomness"] != "none"
        or program["zeroNetwork"] is not True
        or program["defaultAction"]
        != {
            "actualNetworkCallDelta": 0,
            "hiddenRetryDelta": 0,
            "kind": "reject-unknown-selector-before-dispatch",
            "logicalProviderCallDelta": 0,
        }
        or program["guards"]
        != [
            {
                "action": "block-before-dispatch",
                "actualNetworkCallDelta": 0,
                "attemptOrdinal": 2,
                "condition": "dispatchAt earlier than 2026-08-20T12:00:05Z",
                "fixtureCaseId": "provider-rate-limit",
                "guardId": "429-breaker",
                "logicalProviderCallDelta": 0,
                "purpose": "plan",
            },
            {
                "action": "reject-before-dispatch",
                "actualNetworkCallDelta": 0,
                "attemptOrdinalMinimum": 3,
                "guardId": "third-plan-attempt",
                "logicalProviderCallDelta": 0,
                "purpose": "plan",
            },
        ]
        or program["interpreterRules"]
        != [
            "verify program hash before parse",
            "verify blob base64 length and hash",
            "return exact bytes without repair or recanonicalization",
            "never use network, tools, sessions, memory, connectors or internal retry",
        ]
        or manifest["modelProgramSha256"] != _sha256(raw)
        or manifest["modelProgramByteLength"] != len(raw)
    ):
        raise FixtureProgramError("model fixture program semantics are not sealed")
    blobs = _load_blobs(program)
    rows, by_selector = _load_rows(program, blobs)
    return raw, rows, by_selector


def _request_vector_list(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    happy = _array(artifact["happyPathVectors"], "happy-path model vectors")
    correction = _mapping(
        artifact["correctionBeforeFirstEffect"], "correction model vectors"
    )
    generation1 = _mapping(correction["generation1"], "generation-one correction")
    generation2 = _mapping(correction["generation2"], "generation-two correction")
    raw_vectors = [
        generation1["modelVector"],
        *_array(generation2["modelVectors"], "generation-two model vectors"),
    ]
    vectors = [
        _mapping(value, "model request vector") for value in [*happy, *raw_vectors]
    ]
    if len(vectors) != 8:
        raise FixtureProgramError("sealed request catalog must contain eight vectors")
    return vectors


def _load_requests(artifact: dict[str, Any]) -> dict[str, _RequestBinding]:
    requests: dict[str, _RequestBinding] = {}
    for vector in _request_vector_list(artifact):
        payload = _mapping(vector.get("request"), "planner request payload")
        raw = _decode_byte_metadata(
            vector.get("requestCanonicalBytes"),
            label="planner request bytes",
            attached_payload=payload,
        )
        try:
            request = PlannerRequest.model_validate(payload)
        except ValueError as exc:
            raise FixtureProgramError("sealed PlannerRequest/v1 is invalid") from exc
        request_sha256 = _sha256(raw)
        if request_sha256 in requests and requests[request_sha256].data != raw:
            raise FixtureProgramError("planner request hash collision")
        requests[request_sha256] = _RequestBinding(data=raw, request=request)
    if len(requests) != 8:
        raise FixtureProgramError(
            "sealed request catalog must contain eight unique requests"
        )
    return requests


def _provider_state_for_row(row: _ProgramRow) -> FixtureProviderState:
    if row.kind == "drop-after-dispatch":
        return "ambiguous"
    if row.kind == "return-contract-bytes":
        return "responded"
    if row.blob is None:
        raise FixtureProgramError("HTTP fixture row has no provider result")
    provider_result = require_canonical_bytes(row.blob.data)
    status = provider_result.get("status")
    if status == 429:
        return "rate_limited"
    if status == 422:
        return "provider_rejected"
    raise FixtureProgramError("HTTP fixture row has an unsealed provider status")


def _load_dispatches(
    artifact: dict[str, Any],
    rows: tuple[_ProgramRow, ...],
    by_selector: dict[FixtureSelector, _ProgramRow],
    requests: dict[str, _RequestBinding],
) -> dict[FixtureSelector, _DispatchBinding]:
    raw_dispatches = _array(artifact["modelDispatchVectors"], "model dispatch vectors")
    if len(raw_dispatches) != 20:
        raise FixtureProgramError("model dispatch catalog must contain exactly 20 rows")
    bindings: dict[FixtureSelector, _DispatchBinding] = {}
    grant_ids: set[str] = set()
    nonces: set[str] = set()
    dispatch_keys = frozenset(
        {
            "attemptOrdinal",
            "credentialGrant",
            "dispatchAt",
            "dispatchVectorId",
            "eva_core.model_attempts",
            "fixtureCaseId",
            "modelAttempt",
            "oneUseCredentialGrant",
            "purpose",
            "snapshotIsolationKey",
        }
    )
    for index, value in enumerate(raw_dispatches, start=1):
        dispatch = _mapping(value, f"model dispatch {index}")
        _exact_keys(dispatch, dispatch_keys, f"model dispatch {index}")
        grant_wrapper = _mapping(
            dispatch["credentialGrant"], "credential grant wrapper"
        )
        _exact_keys(
            grant_wrapper,
            frozenset({"canonicalBytes", "payload"}),
            "credential grant wrapper",
        )
        grant_payload = _mapping(grant_wrapper["payload"], "credential grant payload")
        grant_bytes = _decode_byte_metadata(
            grant_wrapper["canonicalBytes"],
            label="credential grant bytes",
            attached_payload=grant_payload,
        )
        attempt_wrapper = _mapping(dispatch["modelAttempt"], "model attempt wrapper")
        _exact_keys(
            attempt_wrapper,
            frozenset({"canonicalBytes", "payload", "payload_sha256"}),
            "model attempt wrapper",
        )
        attempt_payload = _mapping(attempt_wrapper["payload"], "model attempt payload")
        attempt_bytes = _decode_byte_metadata(
            attempt_wrapper["canonicalBytes"],
            label="model attempt bytes",
            attached_payload=attempt_payload,
        )
        if attempt_wrapper["payload_sha256"] != _sha256(attempt_bytes):
            raise FixtureProgramError("model attempt payload hash differs")
        try:
            grant = CredentialGrant.model_validate(grant_payload)
            attempt = ModelAttempt.model_validate(attempt_payload)
        except ValueError as exc:
            raise FixtureProgramError("sealed dispatch contract is invalid") from exc
        purpose = dispatch["purpose"]
        request_sha256 = attempt.request_sha256
        selector: FixtureSelector = (
            cast(str, dispatch["fixtureCaseId"]),
            cast(ModelPurpose, purpose),
            request_sha256,
            cast(int, dispatch["attemptOrdinal"]),
            cast(str, dispatch["dispatchAt"]),
        )
        if selector not in by_selector or by_selector[selector] != rows[index - 1]:
            raise FixtureProgramError(
                "dispatch catalog does not match decision-table order"
            )
        row = by_selector[selector]
        request_binding = requests.get(request_sha256)
        expected_state = _provider_state_for_row(row)
        expected_response_sha256 = row.blob.sha256 if row.blob is not None else None
        table_row = _mapping(
            dispatch["eva_core.model_attempts"], "model-attempt table row"
        )
        provider_reference = table_row.get("provider_reference")
        if provider_reference is not None and not isinstance(provider_reference, str):
            raise FixtureProgramError("model provider reference is malformed")
        if (
            dispatch["oneUseCredentialGrant"] is not True
            or request_binding is None
            or request_binding.request.model_input.purpose != purpose
            or request_binding.request.deadline != grant.deadline
            or grant.connector_binding_id is not None
            or grant.model_provider_profile_id != MODEL_PROVIDER_PROFILE_ID
            or grant.worker_id != MODEL_WORKER_ID
            or grant.executable_sha256 != MODEL_PROGRAM_SHA256
            or grant.provider_scopes != [f"model.invoke:{purpose}"]
            or grant.secret_descriptor_id != MODEL_SECRET_DESCRIPTOR_ID
            or grant.issued_at != dispatch["dispatchAt"]
            or attempt.purpose != purpose
            or attempt.ordinal != dispatch["attemptOrdinal"]
            or attempt.credential_grant_id != grant.credential_grant_id
            or attempt.provider_profile_id != MODEL_PROVIDER_PROFILE_ID
            or attempt.state != expected_state
            or attempt.response_sha256 != expected_response_sha256
            or table_row.get("payload") != attempt_payload
            or table_row.get("payload_sha256") != _sha256(attempt_bytes)
            or table_row.get("credential_grant_payload") != grant_payload
            or table_row.get("credential_grant_sha256") != _sha256(grant_bytes)
            or table_row.get("credential_grant_id") != grant.credential_grant_id
            or table_row.get("credential_grant_nonce") != grant.nonce
            or table_row.get("credential_grant_deadline") != grant.deadline
            or table_row.get("model_adapter_manifest_sha256") != MODEL_MANIFEST_SHA256
            or table_row.get("model_program_sha256") != MODEL_PROGRAM_SHA256
            or table_row.get("request_sha256") != request_sha256
            or table_row.get("response_sha256") != expected_response_sha256
            or table_row.get("state") != expected_state
            or (expected_state == "ambiguous" and provider_reference is not None)
        ):
            raise FixtureProgramError(
                "dispatch request, grant, attempt, or deadline binding differs"
            )
        if grant.credential_grant_id in grant_ids or grant.nonce in nonces:
            raise FixtureProgramError("one-use grant IDs and nonces must be distinct")
        grant_ids.add(grant.credential_grant_id)
        nonces.add(grant.nonce)
        bindings[selector] = _DispatchBinding(
            grant_bytes=grant_bytes,
            provider_state=expected_state,
            provider_reference=provider_reference,
            response_sha256=expected_response_sha256,
        )
    if len(bindings) != 20 or len(grant_ids) != 20 or len(nonces) != 20:
        raise FixtureProgramError(
            "dispatch bindings are not exactly twenty unique rows"
        )
    return bindings


class SealedFixtureProgram:
    """Verified immutable view of the sealed 4D model fixture program."""

    __slots__ = (
        "_artifact_sha256",
        "_dispatches",
        "_manifest_bytes",
        "_program_bytes",
        "_requests",
        "_rows",
        "_rows_by_selector",
    )

    def __init__(
        self,
        load_token: object,
        *,
        artifact_sha256: str,
        manifest_bytes: bytes,
        program_bytes: bytes,
        rows: tuple[_ProgramRow, ...],
        rows_by_selector: dict[FixtureSelector, _ProgramRow],
        requests: dict[str, _RequestBinding],
        dispatches: dict[FixtureSelector, _DispatchBinding],
    ) -> None:
        if load_token is not _LOAD_TOKEN:
            raise FixtureProgramError(
                "SealedFixtureProgram must be loaded from exact artifact bytes"
            )
        self._artifact_sha256 = artifact_sha256
        self._manifest_bytes = manifest_bytes
        self._program_bytes = program_bytes
        self._rows = rows
        self._rows_by_selector = MappingProxyType(dict(rows_by_selector))
        self._requests = MappingProxyType(dict(requests))
        self._dispatches = MappingProxyType(dict(dispatches))

    @classmethod
    def from_artifact_bytes(cls, artifact_bytes: bytes) -> "SealedFixtureProgram":
        if type(artifact_bytes) is not bytes:
            raise FixtureProgramError("sealed model artifact input must be bytes")
        artifact_sha256 = _sha256(artifact_bytes)
        if artifact_sha256 != MODEL_ARTIFACT_SHA256:
            raise FixtureProgramError("sealed model artifact SHA-256 differs")
        try:
            artifact = decode_json_bytes(artifact_bytes)
        except (TypeError, ValueError) as exc:
            raise FixtureProgramError(
                "sealed model artifact is not canonical JSON"
            ) from exc
        if canonical_json_bytes(artifact) + b"\n" != artifact_bytes:
            raise FixtureProgramError(
                "sealed model artifact is not canonical JSON plus its repository LF"
            )
        _exact_keys(artifact, _ARTIFACT_KEYS, "sealed model artifact")
        if (
            artifact["contract"] != "Eva24DModelVectors/v1"
            or artifact["status"]
            != "sealed_pre_code_fixture_program_not_runtime_implemented"
        ):
            raise FixtureProgramError(
                "sealed model artifact contract or status differs"
            )
        _validate_fixture_profile(artifact)
        manifest_bytes, manifest = _validate_manifest(artifact)
        program_bytes, rows, rows_by_selector = _validate_program(artifact, manifest)
        requests = _load_requests(artifact)
        dispatches = _load_dispatches(artifact, rows, rows_by_selector, requests)
        return cls(
            _LOAD_TOKEN,
            artifact_sha256=artifact_sha256,
            manifest_bytes=manifest_bytes,
            program_bytes=program_bytes,
            rows=rows,
            rows_by_selector=rows_by_selector,
            requests=requests,
            dispatches=dispatches,
        )

    @property
    def artifact_sha256(self) -> str:
        return self._artifact_sha256

    @property
    def manifest_sha256(self) -> str:
        return _sha256(self._manifest_bytes)

    @property
    def program_sha256(self) -> str:
        return _sha256(self._program_bytes)

    @property
    def manifest_bytes(self) -> bytes:
        return self._manifest_bytes

    @property
    def program_bytes(self) -> bytes:
        return self._program_bytes

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def blob_count(self) -> int:
        return len({row.blob.blob_id for row in self._rows if row.blob is not None})

    @property
    def selectors(self) -> tuple[FixtureSelector, ...]:
        return tuple(row.selector for row in self._rows)


class FixtureModelAdapter:
    """Framework-neutral interpreter for one verified sealed fixture program."""

    __slots__ = ("_program",)

    def __init__(self, program: SealedFixtureProgram) -> None:
        if not isinstance(program, SealedFixtureProgram):
            raise TypeError("program must be a verified SealedFixtureProgram")
        self._program = program

    @classmethod
    def from_artifact_bytes(cls, artifact_bytes: bytes) -> "FixtureModelAdapter":
        return cls(SealedFixtureProgram.from_artifact_bytes(artifact_bytes))

    @property
    def program(self) -> SealedFixtureProgram:
        return self._program

    def invoke(
        self,
        *,
        planner_request_bytes: bytes,
        credential_grant_bytes: bytes,
        fixture_case_id: str,
        purpose: ModelPurpose,
        attempt_ordinal: int,
        dispatch_at: str,
    ) -> FixtureInvocationResult:
        self._validate_selector_fields(
            fixture_case_id=fixture_case_id,
            purpose=purpose,
            attempt_ordinal=attempt_ordinal,
            dispatch_at=dispatch_at,
        )
        self._apply_guards(
            fixture_case_id=fixture_case_id,
            purpose=purpose,
            attempt_ordinal=attempt_ordinal,
            dispatch_at=dispatch_at,
        )
        request, request_sha256 = self._parse_request(
            planner_request_bytes, purpose=purpose
        )
        self._parse_grant(
            credential_grant_bytes,
            purpose=purpose,
            dispatch_at=dispatch_at,
            request_deadline=request.deadline,
        )
        selector: FixtureSelector = (
            fixture_case_id,
            purpose,
            request_sha256,
            attempt_ordinal,
            dispatch_at,
        )
        row = self._program._rows_by_selector.get(selector)
        binding = self._program._dispatches.get(selector)
        if row is None or binding is None:
            raise FixtureDispatchRejected(
                "unknown_selector", "fixture selector is absent from the sealed program"
            )
        if credential_grant_bytes != binding.grant_bytes:
            raise FixtureDispatchRejected(
                "invalid_credential_grant",
                "credential grant bytes differ from the sealed one-use dispatch grant",
            )
        response_bytes = row.blob.data if row.blob is not None else None
        return FixtureInvocationResult(
            kind=row.kind,
            provider_state=binding.provider_state,
            provider_reference=binding.provider_reference,
            response_bytes=response_bytes,
            response_sha256=binding.response_sha256,
            row_ordinal=row.row_ordinal,
            logical_provider_call_delta=row.logical_provider_call_delta,
            actual_network_call_delta=row.actual_network_call_delta,
            hidden_retry_delta=row.hidden_retry_delta,
        )

    @staticmethod
    def _validate_selector_fields(
        *,
        fixture_case_id: str,
        purpose: ModelPurpose,
        attempt_ordinal: int,
        dispatch_at: str,
    ) -> None:
        if (
            not isinstance(fixture_case_id, str)
            or not fixture_case_id
            or purpose not in {"plan", "document_draft", "reply"}
            or type(attempt_ordinal) is not int
            or attempt_ordinal < 1
            or not _canonical_timestamp(dispatch_at)
        ):
            raise FixtureDispatchRejected(
                "invalid_selector", "fixture selector fields are malformed"
            )

    @staticmethod
    def _apply_guards(
        *,
        fixture_case_id: str,
        purpose: ModelPurpose,
        attempt_ordinal: int,
        dispatch_at: str,
    ) -> None:
        if (
            fixture_case_id == "provider-rate-limit"
            and purpose == "plan"
            and attempt_ordinal == 2
            and dispatch_at < "2026-08-20T12:00:05Z"
        ):
            raise FixtureDispatchRejected(
                "blocked_by_guard", "429 breaker is open before 2026-08-20T12:00:05Z"
            )
        if purpose == "plan" and attempt_ordinal >= 3:
            raise FixtureDispatchRejected(
                "rejected_by_guard", "the sealed vertical rejects a third plan attempt"
            )

    def _parse_request(
        self, data: bytes, *, purpose: ModelPurpose
    ) -> tuple[PlannerRequest, str]:
        try:
            payload = require_canonical_bytes(data)
            request = PlannerRequest.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise FixtureDispatchRejected(
                "invalid_planner_request",
                "planner request is not canonical PlannerRequest/v1 bytes",
            ) from exc
        request_sha256 = _sha256(data)
        sealed = self._program._requests.get(request_sha256)
        if (
            sealed is None
            or sealed.data != data
            or request.model_input.purpose != purpose
        ):
            raise FixtureDispatchRejected(
                "invalid_planner_request",
                "planner request bytes or purpose differ from the sealed request catalog",
            )
        return request, request_sha256

    @staticmethod
    def _parse_grant(
        data: bytes,
        *,
        purpose: ModelPurpose,
        dispatch_at: str,
        request_deadline: str,
    ) -> CredentialGrant:
        try:
            payload = require_canonical_bytes(data)
            grant = CredentialGrant.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise FixtureDispatchRejected(
                "invalid_credential_grant",
                "credential grant is not canonical CredentialGrant/v1 bytes",
            ) from exc
        if (
            grant.connector_binding_id is not None
            or grant.model_provider_profile_id != MODEL_PROVIDER_PROFILE_ID
            or grant.worker_id != MODEL_WORKER_ID
            or grant.executable_sha256 != MODEL_PROGRAM_SHA256
            or grant.provider_scopes != [f"model.invoke:{purpose}"]
            or grant.secret_descriptor_id != MODEL_SECRET_DESCRIPTOR_ID
            or grant.issued_at != dispatch_at
            or dispatch_at > grant.deadline
            or request_deadline != grant.deadline
        ):
            raise FixtureDispatchRejected(
                "invalid_credential_grant",
                "credential grant program, profile, purpose, issue time, or deadline differs",
            )
        return grant


__all__ = [
    "FixtureDispatchRejected",
    "FixtureInvocationResult",
    "FixtureModelAdapter",
    "FixtureModelError",
    "FixtureProgramError",
    "MODEL_ARTIFACT_SHA256",
    "MODEL_MANIFEST_SHA256",
    "MODEL_PROGRAM_SHA256",
    "SealedFixtureProgram",
]
