"""Stdlib-only interpreter for the sealed tranche-4D fixture worker programs.

The case, current provider-state digest, fixture clock and fault schedule are
harness inputs outside the connector RPC wire.  A child handles exactly one
initialize/invoke exchange and never persists provider state across processes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_FRAME_BYTES = 1_048_576
MAX_EXCHANGE_BYTES = 2_101_248
_FRAME_HEADER = re.compile(rb"Content-Length: ([1-9][0-9]{0,6})")
_WORKER_KEYS = frozenset({"mail", "erp", "calendar", "document", "channel"})


class FixtureRunnerError(ValueError):
    """Base error for a fixture artifact or exchange that cannot be interpreted."""


class FixtureSelectionError(FixtureRunnerError):
    """The out-of-band worker, case, state or exact stdin selection is invalid."""


@dataclass(frozen=True, slots=True)
class FixtureCase:
    worker_key: str
    case_id: str
    program_sha256: str
    program_bytes: bytes
    manifest_sha256: str
    provider_state_before: str
    provider_state_after: str
    provider_state_before_sha256: str
    provider_state_after_sha256: str
    provider_mutation_delta: int
    initialize_external_call_delta: int
    invoke_external_call_delta: int
    input_exchange: bytes
    success_exchange: bytes


@dataclass(frozen=True, slots=True)
class FixtureExecution:
    stdout: bytes
    program_sha256: str
    manifest_sha256: str
    provider_state_before: str
    provider_state_after: str
    provider_state_before_sha256: str
    provider_state_after_sha256: str
    provider_mutation_delta: int
    initialize_external_call_delta: int
    invoke_external_call_delta: int


def _reject_float(value: str) -> None:
    raise FixtureRunnerError(f"floating-point JSON number is forbidden: {value}")


def _reject_constant(value: str) -> None:
    raise FixtureRunnerError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureRunnerError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureRunnerError(f"{label} must be an object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FixtureRunnerError(f"{label} must be an integer")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise FixtureRunnerError(f"{label} must be a string")
    return value


def _verified_content_address(
    entry: object, label: str
) -> tuple[dict[str, Any], bytes]:
    wrapper = _mapping(entry, label)
    payload = _mapping(wrapper.get("payload"), f"{label}.payload")
    encoded = _canonical_bytes(payload)
    if len(encoded) != _integer(
        wrapper.get("canonicalByteLength"), f"{label}.canonicalByteLength"
    ):
        raise FixtureRunnerError(f"{label} canonical byte length differs")
    if _sha256(encoded) != _string(
        wrapper.get("canonicalSha256"), f"{label}.canonicalSha256"
    ):
        raise FixtureRunnerError(f"{label} canonical SHA-256 differs")
    return payload, encoded


def _frame(payload: object) -> bytes:
    body = _canonical_bytes(payload)
    if len(body) > MAX_FRAME_BYTES:
        raise FixtureRunnerError("fixture RPC body exceeds the sealed frame budget")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _verify_frame_metadata(
    payload: object, metadata_value: object, label: str
) -> bytes:
    metadata = _mapping(metadata_value, label)
    body = _canonical_bytes(payload)
    frame = _frame(payload)
    expected_header = f"Content-Length: {len(body)}\r\n\r\n"
    if metadata.get("headerAscii") != expected_header:
        raise FixtureRunnerError(f"{label} header differs from canonical body")
    if metadata.get("canonicalBodyByteLength") != len(body):
        raise FixtureRunnerError(f"{label} body length differs")
    if metadata.get("canonicalBodySha256") != _sha256(body):
        raise FixtureRunnerError(f"{label} body SHA-256 differs")
    if metadata.get("completeFrameByteLength") != len(frame):
        raise FixtureRunnerError(f"{label} complete length differs")
    return frame


def _load_artifact(path: Path, expected_sha256: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if _sha256(raw) != expected_sha256:
        raise FixtureSelectionError("fixture artifact SHA-256 differs")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureRunnerError(
            "fixture artifact is not valid canonical JSON"
        ) from exc
    artifact = _mapping(value, "fixture artifact")
    if artifact.get("contract") != "Eva24DConnectorProviderFixtures/v1":
        raise FixtureRunnerError("fixture artifact contract differs")
    if artifact.get("status") != "sealed_pre_runtime_fixture_not_implemented":
        raise FixtureRunnerError(
            "fixture artifact is not the sealed pre-runtime fixture"
        )
    return artifact


def _state_entry(
    artifact: dict[str, Any], state_name: str
) -> tuple[str, dict[str, Any]]:
    provider = _mapping(artifact.get("providerStateMachine"), "providerStateMachine")
    states = _mapping(provider.get("states"), "providerStateMachine.states")
    _, encoded = _verified_content_address(
        states.get(state_name), f"providerStateMachine.states.{state_name}"
    )
    wrapper = _mapping(states[state_name], f"providerStateMachine.states.{state_name}")
    return _sha256(encoded), wrapper


def load_fixture_case(
    artifact_path: Path,
    artifact_sha256: str,
    worker_key: str,
    case_id: str,
) -> FixtureCase:
    """Resolve and verify one exact program/case/provider-state transition."""

    if worker_key not in _WORKER_KEYS:
        raise FixtureSelectionError("unknown fixture worker key")
    artifact = _load_artifact(Path(artifact_path), artifact_sha256)
    programs = _mapping(artifact.get("fixtureWorkerPrograms"), "fixtureWorkerPrograms")
    manifests = _mapping(artifact.get("connectorManifests"), "connectorManifests")
    operation_cases = _mapping(artifact.get("operationCallCases"), "operationCallCases")
    rpc = _mapping(
        artifact.get("languageNeutralRpcConformance"),
        "languageNeutralRpcConformance",
    )
    conformance_cases = _mapping(rpc.get("conformanceCases"), "conformanceCases")
    if case_id not in operation_cases or case_id not in conformance_cases:
        raise FixtureSelectionError("unknown sealed operation case")

    program, program_bytes = _verified_content_address(
        programs.get(worker_key), f"fixtureWorkerPrograms.{worker_key}"
    )
    manifest, manifest_bytes = _verified_content_address(
        manifests.get(worker_key), f"connectorManifests.{worker_key}"
    )
    program_sha256 = _sha256(program_bytes)
    manifest_sha256 = _sha256(manifest_bytes)
    if program.get("contract") != "FixtureWorkerProgram/v1":
        raise FixtureRunnerError("selected worker program contract differs")
    if program.get("interpreter_contract") != "Eva4DGenericFixtureRunner/v1":
        raise FixtureRunnerError("selected worker interpreter contract differs")
    if manifest.get("executable_sha256") != program_sha256:
        raise FixtureRunnerError("manifest executable does not bind program bytes")
    expected_connector_id = f"{worker_key}_fixture"
    if program.get("connector_id") != expected_connector_id:
        raise FixtureSelectionError("worker key differs from selected program")
    if manifest.get("connector_id") != expected_connector_id:
        raise FixtureSelectionError("worker key differs from selected manifest")

    operation_case = _mapping(operation_cases[case_id], f"operationCallCases.{case_id}")
    conformance = _mapping(conformance_cases[case_id], f"conformanceCases.{case_id}")
    if operation_case.get("caseId") != case_id:
        raise FixtureRunnerError("operation case ID differs")
    if conformance.get("operationCaseId") != case_id:
        raise FixtureRunnerError("RPC conformance case ID differs")
    initialize = _mapping(conformance.get("initializeRequest"), "initializeRequest")
    initialize_params = _mapping(initialize.get("params"), "initializeRequest.params")
    invoke = _mapping(conformance.get("invokeRequest"), "invokeRequest")
    envelope = _mapping(invoke.get("params"), "invokeRequest.params")
    initialize_success = _mapping(
        conformance.get("initializeSuccess"), "initializeSuccess"
    )
    invoke_success = _mapping(conformance.get("invokeSuccess"), "invokeSuccess")
    result = _mapping(invoke_success.get("result"), "invokeSuccess.result")
    outcome = _mapping(result.get("outcome"), "invokeSuccess.result.outcome")

    if initialize_params.get("connector_id") != expected_connector_id:
        raise FixtureSelectionError("case initialize request selects another worker")
    if initialize_params.get("worker_id") != program.get("worker_id"):
        raise FixtureRunnerError("case initialize worker ID differs from program")
    if initialize_params.get("worker_executable_sha256") != program_sha256:
        raise FixtureRunnerError("case initialize executable differs from program")
    if initialize_params.get("connector_manifest_sha256") != manifest_sha256:
        raise FixtureRunnerError("case initialize manifest SHA-256 differs")
    if envelope.get("connector_id") != expected_connector_id:
        raise FixtureSelectionError("case invoke request selects another worker")
    if envelope.get("connector_manifest_sha256") != manifest_sha256:
        raise FixtureRunnerError("case invoke manifest SHA-256 differs")
    if envelope.get("request_payload") != _mapping(
        operation_case.get("requestPayload"), "operationCase.requestPayload"
    ).get("payload"):
        raise FixtureRunnerError("case invoke payload differs from operation case")
    request_payload_bytes = _canonical_bytes(envelope.get("request_payload"))
    request_payload_sha256 = _sha256(request_payload_bytes)
    if envelope.get("request_payload_sha256") != request_payload_sha256:
        raise FixtureRunnerError("case invoke payload SHA-256 differs")
    if (
        _mapping(
            operation_case.get("requestPayload"), "operationCase.requestPayload"
        ).get("canonicalSha256")
        != request_payload_sha256
    ):
        raise FixtureRunnerError("operation case payload content address differs")
    if outcome.get("kind") != operation_case.get("outcomeKind"):
        raise FixtureRunnerError("case result outcome differs from operation case")

    operations = program.get("operations")
    if not isinstance(operations, list):
        raise FixtureRunnerError("selected program operations must be an array")
    operation = next(
        (
            _mapping(item, "program operation")
            for item in operations
            if isinstance(item, dict)
            and item.get("operation") == envelope.get("operation")
        ),
        None,
    )
    if operation is None:
        raise FixtureSelectionError("case operation is absent from selected program")
    if operation.get("rpcMethod") != invoke.get("method"):
        raise FixtureRunnerError("case RPC method differs from selected program")
    binding = _mapping(initialize_params.get("connector_binding"), "connector_binding")
    accepted_bindings = operation.get("acceptedBindingIds")
    if (
        not isinstance(accepted_bindings, list)
        or binding.get("binding_id") not in accepted_bindings
    ):
        raise FixtureSelectionError("case binding is not accepted by selected program")
    capability = _mapping(
        initialize_params.get("capability_manifest"), "capability_manifest"
    )
    schema_id = operation.get("inputSchemaId")
    operation_schemas = _mapping(artifact.get("operationSchemas"), "operationSchemas")
    schema_wrapper = _mapping(
        operation_schemas.get(schema_id), f"operationSchemas.{schema_id}"
    )
    _, schema_bytes = _verified_content_address(
        schema_wrapper, f"operationSchemas.{schema_id}"
    )
    input_schema_sha256 = _sha256(schema_bytes)
    if operation.get("inputSchemaSha256") != input_schema_sha256:
        raise FixtureRunnerError("program input schema content address differs")
    if capability.get("input_schema_sha256") != input_schema_sha256:
        raise FixtureRunnerError(
            "capability input schema differs from selected program"
        )
    output_schema_id = operation.get("outputSchemaId")
    output_wrapper = _mapping(
        operation_schemas.get(output_schema_id), f"operationSchemas.{output_schema_id}"
    )
    _, output_schema_bytes = _verified_content_address(
        output_wrapper, f"operationSchemas.{output_schema_id}"
    )
    output_schema_sha256 = _sha256(output_schema_bytes)
    if operation.get("outputSchemaSha256") != output_schema_sha256:
        raise FixtureRunnerError("program output schema content address differs")
    if capability.get("output_schema_sha256") != output_schema_sha256:
        raise FixtureRunnerError(
            "capability output schema differs from selected program"
        )

    before_name = _string(
        operation_case.get("providerStateBefore"), "providerStateBefore"
    )
    after_name = _string(operation_case.get("providerStateAfter"), "providerStateAfter")
    before_sha256, _ = _state_entry(artifact, before_name)
    after_sha256, _ = _state_entry(artifact, after_name)
    mutation_delta = _integer(
        operation_case.get("providerMutationDelta"), "providerMutationDelta"
    )
    initialize_delta = _integer(
        conformance.get("initializeExternalCallDelta"),
        "initializeExternalCallDelta",
    )
    invoke_delta = _integer(
        conformance.get("invokeExternalCallDelta"), "invokeExternalCallDelta"
    )
    if initialize_delta != 0:
        raise FixtureRunnerError("initialize performed an external call")
    if invoke_delta != operation_case.get("externalCallDelta") or invoke_delta != 1:
        raise FixtureRunnerError("invoke external-call delta differs from sealed case")

    input_exchange = _verify_frame_metadata(
        initialize,
        conformance.get("initializeRequestFrame"),
        "initializeRequestFrame",
    ) + _verify_frame_metadata(
        invoke, conformance.get("invokeRequestFrame"), "invokeRequestFrame"
    )
    success_exchange = _verify_frame_metadata(
        initialize_success,
        conformance.get("initializeSuccessFrame"),
        "initializeSuccessFrame",
    ) + _verify_frame_metadata(
        invoke_success,
        conformance.get("invokeSuccessFrame"),
        "invokeSuccessFrame",
    )
    if len(success_exchange) > MAX_EXCHANGE_BYTES:
        raise FixtureRunnerError("sealed stdout exchange exceeds its byte budget")

    return FixtureCase(
        worker_key=worker_key,
        case_id=case_id,
        program_sha256=program_sha256,
        program_bytes=program_bytes,
        manifest_sha256=manifest_sha256,
        provider_state_before=before_name,
        provider_state_after=after_name,
        provider_state_before_sha256=before_sha256,
        provider_state_after_sha256=after_sha256,
        provider_mutation_delta=mutation_delta,
        initialize_external_call_delta=initialize_delta,
        invoke_external_call_delta=invoke_delta,
        input_exchange=input_exchange,
        success_exchange=success_exchange,
    )


def run_fixture_exchange(
    *,
    artifact_path: Path,
    artifact_sha256: str,
    worker_key: str,
    case_id: str | None,
    error_case_id: str | None,
    current_state_sha256: str,
    stdin: bytes,
) -> FixtureExecution:
    """Interpret one exact selected fixture case without any network access."""

    if (case_id is None) == (error_case_id is None):
        raise FixtureSelectionError("select exactly one operation or RPC error case")
    artifact = _load_artifact(Path(artifact_path), artifact_sha256)
    selected_case_id = case_id
    error_case: dict[str, Any] | None = None
    if error_case_id is not None:
        rpc = _mapping(
            artifact.get("languageNeutralRpcConformance"),
            "languageNeutralRpcConformance",
        )
        error_cases = _mapping(rpc.get("errorCases"), "errorCases")
        if error_case_id not in error_cases:
            raise FixtureSelectionError("unknown sealed RPC error case")
        error_case = _mapping(error_cases[error_case_id], f"errorCases.{error_case_id}")
        selected_case_id = _string(error_case.get("requestCaseId"), "requestCaseId")
    assert selected_case_id is not None
    selected = load_fixture_case(
        Path(artifact_path), artifact_sha256, worker_key, selected_case_id
    )
    if current_state_sha256 != selected.provider_state_before_sha256:
        raise FixtureSelectionError("current provider state differs from selected case")
    if not isinstance(stdin, bytes) or stdin != selected.input_exchange:
        raise FixtureSelectionError(
            "stdin exchange differs from selected sealed frames"
        )

    stdout = selected.success_exchange
    provider_state_after = selected.provider_state_after
    provider_state_after_sha256 = selected.provider_state_after_sha256
    provider_mutation_delta = selected.provider_mutation_delta
    invoke_delta = selected.invoke_external_call_delta
    if error_case is not None:
        response = _mapping(error_case.get("response"), "error response")
        response_frame = _verify_frame_metadata(
            response, error_case.get("responseFrame"), "error response frame"
        )
        if error_case.get("phase") == "initialize":
            stdout = response_frame
            provider_state_after = selected.provider_state_before
            provider_state_after_sha256 = selected.provider_state_before_sha256
            provider_mutation_delta = 0
            invoke_delta = 0
        elif error_case.get("phase") == "invoke":
            rpc = _mapping(
                artifact.get("languageNeutralRpcConformance"),
                "languageNeutralRpcConformance",
            )
            conformance = _mapping(
                _mapping(rpc.get("conformanceCases"), "conformanceCases").get(
                    selected_case_id
                ),
                f"conformanceCases.{selected_case_id}",
            )
            initialize_success_frame = _verify_frame_metadata(
                conformance.get("initializeSuccess"),
                conformance.get("initializeSuccessFrame"),
                "initializeSuccessFrame",
            )
            stdout = initialize_success_frame + response_frame
            if not error_case.get("externalIoMayHaveStarted"):
                provider_state_after = selected.provider_state_before
                provider_state_after_sha256 = selected.provider_state_before_sha256
                provider_mutation_delta = 0
                invoke_delta = 0
        else:
            raise FixtureRunnerError("RPC error phase differs")

    return FixtureExecution(
        stdout=stdout,
        program_sha256=selected.program_sha256,
        manifest_sha256=selected.manifest_sha256,
        provider_state_before=selected.provider_state_before,
        provider_state_after=provider_state_after,
        provider_state_before_sha256=selected.provider_state_before_sha256,
        provider_state_after_sha256=provider_state_after_sha256,
        provider_mutation_delta=provider_mutation_delta,
        initialize_external_call_delta=0,
        invoke_external_call_delta=invoke_delta,
    )


def worker_main(worker_key: str, argv: list[str] | None = None) -> int:
    """Run one isolated fixture worker process."""

    parser = argparse.ArgumentParser(prog=f"eva2-{worker_key}-fixture-worker")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--current-state-sha256", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case-id")
    selection.add_argument("--error-case-id")
    args = parser.parse_args(argv)
    try:
        execution = run_fixture_exchange(
            artifact_path=args.artifact,
            artifact_sha256=args.artifact_sha256,
            worker_key=worker_key,
            case_id=args.case_id,
            error_case_id=args.error_case_id,
            current_state_sha256=args.current_state_sha256,
            stdin=sys.stdin.buffer.read(MAX_EXCHANGE_BYTES + 1),
        )
    except (OSError, FixtureRunnerError) as exc:
        sys.stderr.write(f"fixture worker rejected exchange: {exc}\n")
        return 2
    sys.stdout.buffer.write(execution.stdout)
    sys.stdout.buffer.flush()
    return 0


__all__ = [
    "FixtureCase",
    "FixtureExecution",
    "FixtureRunnerError",
    "FixtureSelectionError",
    "load_fixture_case",
    "run_fixture_exchange",
    "worker_main",
]
