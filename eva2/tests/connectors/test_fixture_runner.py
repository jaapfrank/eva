from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from connector_sdk.fixture_runner import (
    FixtureSelectionError,
    load_fixture_case,
    run_fixture_exchange,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-connector-provider-fixtures-v1.json"
)
ARTIFACT_SHA256 = "7655b181889d95f1140aea114be4934724931814898b4cee134151f57c2009c6"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _frame(value: object) -> bytes:
    body = _canonical_bytes(value)
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _artifact() -> dict[str, object]:
    raw = ARTIFACT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA256
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _worker_key(conformance: dict[str, object]) -> str:
    initialize = conformance["initializeRequest"]
    assert isinstance(initialize, dict)
    params = initialize["params"]
    assert isinstance(params, dict)
    connector_id = params["connector_id"]
    assert isinstance(connector_id, str) and connector_id.endswith("_fixture")
    return connector_id.removesuffix("_fixture")


def _state_sha256(artifact: dict[str, object], case_id: str) -> str:
    cases = artifact["operationCallCases"]
    assert isinstance(cases, dict)
    operation_case = cases[case_id]
    assert isinstance(operation_case, dict)
    state_name = operation_case["providerStateBefore"]
    assert isinstance(state_name, str)
    provider = artifact["providerStateMachine"]
    assert isinstance(provider, dict)
    states = provider["states"]
    assert isinstance(states, dict)
    state = states[state_name]
    assert isinstance(state, dict)
    value = state["canonicalSha256"]
    assert isinstance(value, str)
    return value


def _input_exchange(conformance: dict[str, object]) -> bytes:
    return _frame(conformance["initializeRequest"]) + _frame(
        conformance["invokeRequest"]
    )


def _success_exchange(conformance: dict[str, object]) -> bytes:
    return _frame(conformance["initializeSuccess"]) + _frame(
        conformance["invokeSuccess"]
    )


def _worker_command(
    *,
    worker_key: str,
    case_id: str | None,
    error_case_id: str | None,
    current_state_sha256: str,
) -> list[str]:
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
    return command


@pytest.mark.parametrize("case_id", tuple(_artifact()["operationCallCases"]))
def test_all_58_sealed_cases_are_exact_interpreter_transitions(case_id: str) -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    cases = rpc["conformanceCases"]
    assert isinstance(cases, dict)
    conformance = cases[case_id]
    assert isinstance(conformance, dict)
    current_state_sha256 = _state_sha256(artifact, case_id)

    execution = run_fixture_exchange(
        artifact_path=ARTIFACT_PATH,
        artifact_sha256=ARTIFACT_SHA256,
        worker_key=_worker_key(conformance),
        case_id=case_id,
        error_case_id=None,
        current_state_sha256=current_state_sha256,
        stdin=_input_exchange(conformance),
    )

    operation_cases = artifact["operationCallCases"]
    assert isinstance(operation_cases, dict)
    operation_case = operation_cases[case_id]
    assert isinstance(operation_case, dict)
    assert execution.stdout == _success_exchange(conformance)
    assert execution.initialize_external_call_delta == 0
    assert execution.invoke_external_call_delta == 1
    assert execution.provider_state_before == operation_case["providerStateBefore"]
    assert execution.provider_state_after == operation_case["providerStateAfter"]
    assert execution.provider_mutation_delta == operation_case["providerMutationDelta"]


@pytest.mark.parametrize("case_id", tuple(_artifact()["operationCallCases"]))
def test_all_58_sealed_cases_run_as_real_short_lived_workers(case_id: str) -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    cases = rpc["conformanceCases"]
    assert isinstance(cases, dict)
    conformance = cases[case_id]
    assert isinstance(conformance, dict)
    worker_key = _worker_key(conformance)

    completed = subprocess.run(
        _worker_command(
            worker_key=worker_key,
            case_id=case_id,
            error_case_id=None,
            current_state_sha256=_state_sha256(artifact, case_id),
        ),
        input=_input_exchange(conformance),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stderr == b""
    assert completed.stdout == _success_exchange(conformance)


@pytest.mark.parametrize(
    "error_case_id",
    (
        "initialize.invalid-params",
        "invoke.invalid-params",
        "invoke.worker-failure-before-io",
        "invoke.worker-failure-after-possible-io",
    ),
)
def test_four_sealed_rpc_error_frames_are_emitted_exactly(error_case_id: str) -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    conformance_cases = rpc["conformanceCases"]
    error_cases = rpc["errorCases"]
    assert isinstance(conformance_cases, dict) and isinstance(error_cases, dict)
    error_case = error_cases[error_case_id]
    assert isinstance(error_case, dict)
    request_case_id = error_case["requestCaseId"]
    assert isinstance(request_case_id, str)
    conformance = conformance_cases[request_case_id]
    assert isinstance(conformance, dict)
    expected = _frame(error_case["response"])
    if error_case["phase"] == "invoke":
        expected = _frame(conformance["initializeSuccess"]) + expected

    completed = subprocess.run(
        _worker_command(
            worker_key=_worker_key(conformance),
            case_id=None,
            error_case_id=error_case_id,
            current_state_sha256=_state_sha256(artifact, request_case_id),
        ),
        input=_input_exchange(conformance),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stderr == b""
    assert completed.stdout == expected


def test_wrong_current_state_for_a_sealed_case_fails_before_output() -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    cases = rpc["conformanceCases"]
    assert isinstance(cases, dict)
    conformance = cases["calendar.create.happy"]
    assert isinstance(conformance, dict)
    wrong_state = _state_sha256(artifact, "document.create.happy")

    with pytest.raises(FixtureSelectionError, match="current provider state"):
        run_fixture_exchange(
            artifact_path=ARTIFACT_PATH,
            artifact_sha256=ARTIFACT_SHA256,
            worker_key="calendar",
            case_id="calendar.create.happy",
            error_case_id=None,
            current_state_sha256=wrong_state,
            stdin=_input_exchange(conformance),
        )


def test_trailing_or_altered_stdin_fails_before_state_or_output() -> None:
    artifact = _artifact()
    rpc = artifact["languageNeutralRpcConformance"]
    assert isinstance(rpc, dict)
    cases = rpc["conformanceCases"]
    assert isinstance(cases, dict)
    conformance = cases["calendar.create.happy"]
    assert isinstance(conformance, dict)
    valid_stdin = _input_exchange(conformance)

    for invalid_stdin in (valid_stdin + b"\n", valid_stdin[:-1] + b"0"):
        with pytest.raises(FixtureSelectionError, match="stdin exchange"):
            run_fixture_exchange(
                artifact_path=ARTIFACT_PATH,
                artifact_sha256=ARTIFACT_SHA256,
                worker_key="calendar",
                case_id="calendar.create.happy",
                error_case_id=None,
                current_state_sha256=_state_sha256(artifact, "calendar.create.happy"),
                stdin=invalid_stdin,
            )


def test_c07_c10_and_c13_are_separate_sealed_state_transitions() -> None:
    c07_ambiguous = load_fixture_case(
        ARTIFACT_PATH,
        ARTIFACT_SHA256,
        "calendar",
        "calendar.create.ambiguous-before-commit",
    )
    c07_reconcile = load_fixture_case(
        ARTIFACT_PATH, ARTIFACT_SHA256, "calendar", "calendar.reconcile.not-observed"
    )
    c07_ordinal_two = load_fixture_case(
        ARTIFACT_PATH,
        ARTIFACT_SHA256,
        "calendar",
        "calendar.create.ordinal2-after-not-observed",
    )
    assert (
        c07_ambiguous.provider_state_before_sha256
        == c07_ambiguous.provider_state_after_sha256
    )
    assert (
        c07_ambiguous.provider_state_after_sha256
        == c07_reconcile.provider_state_before_sha256
    )
    assert (
        c07_reconcile.provider_state_after_sha256
        == c07_ordinal_two.provider_state_before_sha256
    )
    assert c07_ordinal_two.provider_mutation_delta == 1

    c10_first = load_fixture_case(
        ARTIFACT_PATH, ARTIFACT_SHA256, "calendar", "calendar.readback.matches"
    )
    c10_second = load_fixture_case(
        ARTIFACT_PATH,
        ARTIFACT_SHA256,
        "calendar",
        "calendar.readback.matches.c10-repeat",
    )
    assert (
        c10_first.provider_state_before_sha256
        == c10_second.provider_state_before_sha256
    )
    assert (
        c10_first.provider_state_after_sha256 == c10_second.provider_state_after_sha256
    )
    assert c10_first.provider_mutation_delta == c10_second.provider_mutation_delta == 0

    c13_first = load_fixture_case(
        ARTIFACT_PATH, ARTIFACT_SHA256, "channel", "outcome.deliver.evaSuccess"
    )
    c13_reoffer = load_fixture_case(
        ARTIFACT_PATH,
        ARTIFACT_SHA256,
        "channel",
        "outcome.deliver.reoffer-after-ack-loss.evaSuccess",
    )
    assert (
        c13_first.provider_state_after_sha256
        == c13_reoffer.provider_state_before_sha256
    )
    assert (
        c13_reoffer.provider_state_before_sha256
        == c13_reoffer.provider_state_after_sha256
    )
    assert c13_reoffer.provider_mutation_delta == 0
