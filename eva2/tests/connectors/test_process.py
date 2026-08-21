from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from eva.adapters.connectors.process import (
    ConnectorFixtureSelection,
    ConnectorProcessSpec,
    run_connector_process,
)
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-connector-provider-fixtures-v1.json"
)
ARTIFACT_SHA256 = "a17451b03978a7018f7fe8b7143981ce7d9a1c596abc7ada261cc77c211534e5"


def _artifact() -> dict[str, object]:
    raw = ARTIFACT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA256
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _frame(value: object) -> bytes:
    body = canonical_json_bytes(value)
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _selection(
    case_id: str,
) -> tuple[
    dict[str, object],
    str,
    ConnectorManifest,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    bytes,
    str,
]:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    conformance_cases = rpc["conformanceCases"]
    assert isinstance(conformance_cases, dict)
    conformance = conformance_cases[case_id]
    assert isinstance(conformance, dict)
    initialize = ConnectorInitializeRequest.model_validate(
        conformance["initializeRequest"]
    )
    invoke = ConnectorInvokeRequest.model_validate(conformance["invokeRequest"])
    connector_id = initialize.params.connector_id
    worker_key = connector_id.removesuffix("_fixture")
    manifests = artifact["connectorManifests"]
    programs = artifact["fixtureWorkerPrograms"]
    assert isinstance(manifests, dict) and isinstance(programs, dict)
    manifest_entry = manifests[worker_key]
    program_entry = programs[worker_key]
    assert isinstance(manifest_entry, dict) and isinstance(program_entry, dict)
    manifest = ConnectorManifest.model_validate(manifest_entry["payload"])
    program_bytes = canonical_json_bytes(program_entry["payload"])
    operation_cases = artifact["operationCallCases"]
    provider = artifact["providerStateMachine"]
    assert isinstance(operation_cases, dict) and isinstance(provider, dict)
    operation_case = operation_cases[case_id]
    states = provider["states"]
    assert isinstance(operation_case, dict) and isinstance(states, dict)
    state = states[operation_case["providerStateBefore"]]
    assert isinstance(state, dict)
    current_state_sha256 = state["canonicalSha256"]
    assert isinstance(current_state_sha256, str)
    return (
        conformance,
        worker_key,
        manifest,
        initialize,
        invoke,
        program_bytes,
        current_state_sha256,
    )


def _fixture_command(
    *,
    worker_key: str,
    current_state_sha256: str,
    case_id: str | None = None,
    error_case_id: str | None = None,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-I",
        str(EVA2_ROOT / f"connectors/{worker_key}/worker.py"),
        "--artifact",
        str(ARTIFACT_PATH),
        "--artifact-sha256",
        ARTIFACT_SHA256,
        "--current-state-sha256",
        current_state_sha256,
    ]
    if case_id is not None:
        command.extend(("--case-id", case_id))
    if error_case_id is not None:
        command.extend(("--error-case-id", error_case_id))
    return tuple(command)


@pytest.mark.parametrize(
    "case_id",
    (
        "mail.search.happy",
        "erp.lookup.unique",
        "calendar.create.rate-limited",
        "calendar.create.ambiguous-after-commit",
        "document.create.happy",
        "confirmation.deliver.happy",
        "outcome.deliver.reoffer-after-ack-loss.evaSuccess",
    ),
)
def test_host_runs_and_revalidates_real_worker_exchange(case_id: str) -> None:
    (
        conformance,
        worker_key,
        manifest,
        initialize,
        invoke,
        program_bytes,
        current_state_sha256,
    ) = _selection(case_id)
    spec = ConnectorProcessSpec(
        command=_fixture_command(
            worker_key=worker_key,
            case_id=case_id,
            current_state_sha256=current_state_sha256,
        ),
        program_bytes=program_bytes,
        fixture_selection=ConnectorFixtureSelection(
            artifact_path=ARTIFACT_PATH,
            artifact_sha256=ARTIFACT_SHA256,
            worker_key=worker_key,
            case_id=case_id,
            current_state_sha256=current_state_sha256,
        ),
    )

    result = run_connector_process(
        spec=spec,
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )

    assert isinstance(result, ConnectorInvokeSuccess)
    assert result.model_dump(mode="json") == conformance["invokeSuccess"]
    assert result.result.outcome.kind in {
        "read_ok",
        "effect_response",
        "rate_limited",
        "ambiguous_transport",
    }


@pytest.mark.parametrize(
    "error_case_id",
    (
        "initialize.invalid-params",
        "invoke.invalid-params",
        "invoke.worker-failure-before-io",
        "invoke.worker-failure-after-possible-io",
    ),
)
def test_host_rejects_error_frame_without_its_unsealed_request_preimage(
    error_case_id: str,
) -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    error_cases = rpc["errorCases"]
    assert isinstance(error_cases, dict)
    error_case = error_cases[error_case_id]
    assert isinstance(error_case, dict)
    request_case_id = error_case["requestCaseId"]
    assert isinstance(request_case_id, str)
    (
        _,
        worker_key,
        manifest,
        initialize,
        invoke,
        program_bytes,
        current_state_sha256,
    ) = _selection(request_case_id)
    phase = (
        "invoke_external_io_possible"
        if error_case["externalIoMayHaveStarted"]
        else "invoke_before_external_io"
    )
    spec = ConnectorProcessSpec(
        command=_fixture_command(
            worker_key=worker_key,
            error_case_id=error_case_id,
            current_state_sha256=current_state_sha256,
        ),
        program_bytes=program_bytes,
        invoke_failure_phase=phase,
    )

    result = run_connector_process(
        spec=spec,
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )

    assert isinstance(result, ConnectorProtocolFailure)
    assert result.violation == "invalid_message"
    assert result.automatic_resend_permitted is False


def _python_stdout_command(stdout: bytes) -> tuple[str, ...]:
    return (
        sys.executable,
        "-I",
        "-c",
        f"import sys;sys.stdout.buffer.write(bytes.fromhex('{stdout.hex()}'))",
    )


def _run_fake_stdout(
    stdout: bytes, *, invoke_failure_phase: str
) -> ConnectorProtocolFailure:
    (
        _,
        _,
        manifest,
        initialize,
        invoke,
        program_bytes,
        _,
    ) = _selection("calendar.create.ambiguous-after-commit")
    result = run_connector_process(
        spec=ConnectorProcessSpec(
            command=_python_stdout_command(stdout),
            program_bytes=program_bytes,
            invoke_failure_phase=invoke_failure_phase,
        ),
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )
    assert isinstance(result, ConnectorProtocolFailure)
    return result


def test_four_fatal_protocol_paths_return_typed_nonresendable_results() -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    cases = rpc["conformanceCases"]
    failures = rpc["protocolFailureCases"]
    assert isinstance(cases, dict) and isinstance(failures, list)
    conformance = cases["calendar.create.ambiguous-after-commit"]
    assert isinstance(conformance, dict)
    initialize_frame = _frame(conformance["initializeSuccess"])
    success_exchange = initialize_frame + _frame(conformance["invokeSuccess"])

    mismatch = copy.deepcopy(conformance["invokeSuccess"])
    assert isinstance(mismatch, dict)
    mismatch["id"] = "fixture:rpc:wrong-response:invoke"
    result = mismatch["result"]
    assert isinstance(result, dict)
    result["request_id"] = "fixture:rpc:wrong-response:invoke"

    actual = [
        _run_fake_stdout(
            b"not-a-frame", invoke_failure_phase="invoke_before_external_io"
        ),
        _run_fake_stdout(
            initialize_frame + b"Content-Length: 12\r\n\r\n{}",
            invoke_failure_phase="invoke_before_external_io",
        ),
        _run_fake_stdout(
            success_exchange + initialize_frame,
            invoke_failure_phase="invoke_external_io_possible",
        ),
        _run_fake_stdout(
            initialize_frame + _frame(mismatch),
            invoke_failure_phase="invoke_external_io_possible",
        ),
    ]

    for got, expected_entry in zip(actual, failures, strict=True):
        assert isinstance(expected_entry, dict)
        expected = expected_entry["payload"]
        assert isinstance(expected, dict)
        assert got.phase == expected["phase"]
        assert got.violation == expected["violation"]
        assert got.failure_class == expected["failure_class"]
        assert got.automatic_resend_permitted is False


def test_program_hash_mismatch_fails_before_process_start() -> None:
    (
        _,
        _,
        manifest,
        initialize,
        invoke,
        program_bytes,
        _,
    ) = _selection("calendar.create.happy")
    spec = ConnectorProcessSpec(
        command=("/definitely/not/a/worker",),
        program_bytes=program_bytes + b" ",
    )

    result = run_connector_process(
        spec=spec,
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )

    assert isinstance(result, ConnectorProtocolFailure)
    assert result.phase == "initialize"
    assert result.violation == "executable_mismatch"
    assert result.failure_class == "internal_failure"
    assert result.automatic_resend_permitted is False


def test_host_rejects_wrong_fixture_state_before_process_start() -> None:
    (
        _,
        worker_key,
        manifest,
        initialize,
        invoke,
        program_bytes,
        _,
    ) = _selection("calendar.create.happy")
    spec = ConnectorProcessSpec(
        command=("/definitely/not/a/worker",),
        program_bytes=program_bytes,
        fixture_selection=ConnectorFixtureSelection(
            artifact_path=ARTIFACT_PATH,
            artifact_sha256=ARTIFACT_SHA256,
            worker_key=worker_key,
            case_id="calendar.create.happy",
            current_state_sha256="0" * 64,
        ),
    )

    result = run_connector_process(
        spec=spec,
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )

    assert isinstance(result, ConnectorProtocolFailure)
    assert result.phase == "initialize"
    assert result.violation == "manifest_mismatch"
    assert result.automatic_resend_permitted is False


def test_host_does_not_trust_worker_case_selection() -> None:
    (
        _,
        worker_key,
        manifest,
        initialize,
        invoke,
        program_bytes,
        current_state_sha256,
    ) = _selection("calendar.create.happy")
    spec = ConnectorProcessSpec(
        command=_fixture_command(
            worker_key=worker_key,
            case_id="calendar.create.rate-limited",
            current_state_sha256=current_state_sha256,
        ),
        program_bytes=program_bytes,
        fixture_selection=ConnectorFixtureSelection(
            artifact_path=ARTIFACT_PATH,
            artifact_sha256=ARTIFACT_SHA256,
            worker_key=worker_key,
            case_id="calendar.create.happy",
            current_state_sha256=current_state_sha256,
        ),
    )

    result = run_connector_process(
        spec=spec,
        manifest=manifest,
        initialize=initialize,
        invoke=invoke,
        observed_at="2026-08-20T12:00:00Z",
        timeout_seconds=10,
    )

    assert isinstance(result, ConnectorProtocolFailure)
    assert result.automatic_resend_permitted is False
