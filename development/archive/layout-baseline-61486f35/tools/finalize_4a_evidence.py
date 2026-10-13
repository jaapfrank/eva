#!/usr/bin/env python3
"""Finalize tranche 4A only when all sealed design gates remain reproducible."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from run_gate import (
    CLAIM_BOUNDARY,
    DEFAULT_COMMANDS,
    EXECUTION_BOUNDARY,
    RESULT_CONTRACT,
    SUPPORTED_GATES,
    CheckSpec,
    build_input_snapshot,
    evidence_directory,
    execution_environment,
    source_receipt_passed,
    write_content_addressed,
)
from verify_source_bindings import (
    VERIFICATION_CONTRACT,
    canonical_json_bytes,
    load_json_exact,
    sha256_file,
    verify as verify_source_bindings,
)


INDEX_CONTRACT = "Eva24AAcceptanceIndex/v1"
_RESULT_NAME = re.compile(
    r"^gate-result-(contract-01|platform-design-01|supply-design-01)-sha256-([0-9a-f]{64})\.json$"
)


class FinalizationError(RuntimeError):
    """A fail-closed reason that prevents a 4A acceptance index."""


def _source_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract": receipt.get("contract"),
        "status": receipt.get("status"),
        "expectedCount": receipt.get("expectedCount"),
        "actualCount": receipt.get("actualCount"),
        "manifestPath": receipt.get("manifestPath"),
        "manifestSha256": receipt.get("manifestSha256"),
        "bindingsFingerprint": receipt.get("bindingsFingerprint"),
        "errors": receipt.get("errors", []),
    }


def _validate_result_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FinalizationError(f"gate evidence is not a regular file: {path.name}")
    match = _RESULT_NAME.fullmatch(path.name)
    if match is None:
        raise FinalizationError(f"invalid gate evidence filename: {path.name}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != match.group(2):
        raise FinalizationError(f"gate evidence filename digest mismatch: {path.name}")
    try:
        value = load_json_exact(path)
    except (OSError, ValueError) as exc:
        raise FinalizationError(
            f"invalid gate evidence JSON: {path.name}: {exc}"
        ) from exc
    if raw != canonical_json_bytes(value):
        raise FinalizationError(f"gate evidence is not canonical JSON: {path.name}")
    expected_fields = {
        "checks",
        "claimBoundary",
        "contract",
        "executionBoundary",
        "executionEnvironment",
        "gate",
        "inputSnapshot",
        "integrity",
        "sourceBinding",
        "status",
        "tranche",
    }
    if set(value) != expected_fields:
        raise FinalizationError(f"gate evidence fields are not exact: {path.name}")
    if value.get("contract") != RESULT_CONTRACT or value.get("tranche") != "4A":
        raise FinalizationError(f"unexpected gate evidence contract: {path.name}")
    gate = value.get("gate")
    expected_gate = match.group(1).upper()
    if gate not in SUPPORTED_GATES or gate != expected_gate:
        raise FinalizationError(f"gate identity does not match filename: {path.name}")
    if value.get("status") not in {"passed", "failed", "not_proven"}:
        raise FinalizationError(f"invalid gate status: {path.name}")
    if value.get("claimBoundary") != CLAIM_BOUNDARY:
        raise FinalizationError(f"claim boundary mismatch: {path.name}")
    if value.get("executionBoundary") != EXECUTION_BOUNDARY:
        raise FinalizationError(f"execution boundary mismatch: {path.name}")
    return value


def _validate_passed_result(
    value: Mapping[str, Any],
    *,
    current_source: Mapping[str, Any],
    current_snapshot: Mapping[str, Any],
    current_environment: Mapping[str, Any],
    commands: Mapping[str, Sequence[CheckSpec]],
    filename: str,
) -> None:
    gate = str(value["gate"])
    if value.get("status") != "passed":
        raise FinalizationError(f"gate is not passed: {gate}")
    if value.get("sourceBinding") != _source_summary(current_source):
        raise FinalizationError(f"gate source bindings are stale: {filename}")
    if value.get("inputSnapshot") != current_snapshot:
        raise FinalizationError(f"gate input snapshot is stale: {filename}")
    if value.get("executionEnvironment") != current_environment:
        raise FinalizationError(f"gate execution environment is stale: {filename}")
    if value.get("integrity") != {
        "executionEnvironmentStableDuringRun": True,
        "inputsStableDuringRun": True,
        "sourceBindingsStableDuringRun": True,
    }:
        raise FinalizationError(
            f"gate integrity postconditions did not pass: {filename}"
        )

    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list):
        raise FinalizationError(f"gate checks are not an array: {filename}")
    expected_specs = list(commands[gate])
    if len(raw_checks) != len(expected_specs):
        raise FinalizationError(f"gate check count mismatch: {filename}")
    for check, spec in zip(raw_checks, expected_specs, strict=True):
        if not isinstance(check, dict):
            raise FinalizationError(f"gate check is not an object: {filename}")
        if check.get("id") != spec.identifier:
            raise FinalizationError(f"gate check order or id mismatch: {filename}")
        expected_command = [
            "<python>" if item == "{python}" else item for item in spec.argv
        ]
        if check.get("command") != expected_command:
            raise FinalizationError(f"gate command mismatch: {filename}")
        if check.get("status") != "passed" or check.get("returnCode") != 0:
            raise FinalizationError(f"gate check did not pass: {filename}")
        stdout = check.get("stdout")
        stderr = check.get("stderr")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise FinalizationError(f"gate output is not text: {filename}")
        if (
            check.get("stdoutSha256")
            != hashlib.sha256(stdout.encode("utf-8")).hexdigest()
        ):
            raise FinalizationError(f"stdout receipt mismatch: {filename}")
        if (
            check.get("stderrSha256")
            != hashlib.sha256(stderr.encode("utf-8")).hexdigest()
        ):
            raise FinalizationError(f"stderr receipt mismatch: {filename}")


SourceVerifier = Callable[[Path], dict[str, Any]]
EnvironmentVerifier = Callable[[Path], dict[str, Any]]


def finalize(
    root: Path,
    *,
    commands: Mapping[str, Sequence[CheckSpec]] | None = None,
    source_verifier: SourceVerifier = verify_source_bindings,
    environment_verifier: EnvironmentVerifier = execution_environment,
    evidence_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create one content-addressed index or raise without accepting 4A."""

    selected_commands = DEFAULT_COMMANDS if commands is None else commands
    if set(selected_commands) != set(SUPPORTED_GATES):
        raise FinalizationError(
            "command map must define exactly the three supported gates"
        )
    root = root.resolve(strict=True)
    try:
        output_dir = evidence_directory(root, evidence_dir, create=False)
    except RuntimeError as exc:
        raise FinalizationError(str(exc)) from exc

    try:
        source_before = source_verifier(root)
    except Exception as exc:
        raise FinalizationError(
            f"source verifier failed: {type(exc).__name__}"
        ) from exc
    if source_before.get("contract") != VERIFICATION_CONTRACT:
        raise FinalizationError("source verifier returned an unexpected contract")
    if not source_receipt_passed(source_before):
        raise FinalizationError("sealed source bindings do not currently verify")
    try:
        snapshot_before = build_input_snapshot(root)
        environment_before = environment_verifier(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise FinalizationError(f"cannot snapshot current 4A inputs: {exc}") from exc

    candidate_paths = sorted(
        (path for path in output_dir.iterdir() if path.name.startswith("gate-result-")),
        key=lambda path: path.name,
    )
    if not candidate_paths:
        raise FinalizationError("no gate evidence exists")
    current_passed: dict[str, list[tuple[Path, dict[str, Any]]]] = {
        gate: [] for gate in SUPPORTED_GATES
    }
    for path in candidate_paths:
        value = _validate_result_file(path)
        if (
            value.get("status") == "passed"
            and value.get("sourceBinding") == _source_summary(source_before)
            and value.get("inputSnapshot") == snapshot_before
            and value.get("executionEnvironment") == environment_before
        ):
            current_passed[str(value["gate"])].append((path, value))

    selected: list[tuple[Path, dict[str, Any]]] = []
    for gate in SUPPORTED_GATES:
        candidates = current_passed[gate]
        if len(candidates) != 1:
            raise FinalizationError(
                f"expected exactly one current passed result for {gate}, found {len(candidates)}"
            )
        path, value = candidates[0]
        _validate_passed_result(
            value,
            current_source=source_before,
            current_snapshot=snapshot_before,
            current_environment=environment_before,
            commands=selected_commands,
            filename=path.name,
        )
        selected.append((path, value))

    try:
        source_after = source_verifier(root)
        snapshot_after = build_input_snapshot(root)
        environment_after = environment_verifier(root)
    except Exception as exc:
        raise FinalizationError(
            f"final integrity recheck failed: {type(exc).__name__}"
        ) from exc
    if _source_summary(source_before) != _source_summary(source_after):
        raise FinalizationError("source bindings changed while finalizing")
    if not source_receipt_passed(source_after):
        raise FinalizationError("source bindings failed after gate validation")
    if snapshot_before != snapshot_after:
        raise FinalizationError("4A inputs changed while finalizing")
    if environment_before != environment_after:
        raise FinalizationError(
            "checker interpreter or declared locks changed while finalizing"
        )

    gate_results = [
        {
            "gate": value["gate"],
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "status": "passed",
        }
        for path, value in selected
    ]
    index: dict[str, Any] = {
        "contract": INDEX_CONTRACT,
        "tranche": "4A",
        "status": "passed",
        "acceptance": "design_evidence_only",
        "claimBoundary": CLAIM_BOUNDARY,
        "executionBoundary": EXECUTION_BOUNDARY,
        "executionEnvironment": environment_after,
        "sourceBinding": _source_summary(source_after),
        "inputSnapshot": snapshot_after,
        "gates": gate_results,
    }
    path, _digest = write_content_addressed(output_dir, "4a-index", index)
    return path, index


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_default_root())
    args = parser.parse_args(argv)
    try:
        path, index = finalize(args.root)
        root = args.root.resolve()
        receipt = {
            "contract": "Eva24AFinalization/v1",
            "evidencePath": path.resolve().relative_to(root).as_posix(),
            "evidenceSha256": sha256_file(path),
            "status": index["status"],
        }
        exit_code = 0
    except (FinalizationError, OSError, RuntimeError, ValueError) as exc:
        receipt = {
            "contract": "Eva24AFinalization/v1",
            "evidencePath": None,
            "evidenceSha256": None,
            "status": "not_proven",
            "error": str(exc),
        }
        exit_code = 1
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
