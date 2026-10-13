#!/usr/bin/env python3
"""Run one sealed DBBench development selection exactly once per case."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from run_agentbench_dbbench_development import (
    DbBenchDevelopmentError,
    run_one_case,
)

SELECTION_CONTRACT = "EvaAgentBenchDbBenchValidationFeedbackSelection/v1"


class BatchError(RuntimeError):
    """A sealed batch precondition or execution boundary failed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_selection(path: Path) -> tuple[dict[str, Any], list[int]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BatchError("selection is unreadable") from error
    if not isinstance(document, dict) or document.get("contract") != SELECTION_CONTRACT:
        raise BatchError("selection contract is invalid")
    if document.get("selectedBeforeAnyCase32Through39ResultWasKnown") is not True:
        raise BatchError("selection was not sealed before results")
    selection = document.get("selection")
    indices = selection.get("caseIndices") if isinstance(selection, dict) else None
    if indices != list(range(32, 40)):
        raise BatchError("selection is not the sealed case range 32 through 39")
    candidate = document.get("candidate")
    if not isinstance(candidate, dict):
        raise BatchError("candidate binding is missing")
    root = Path(__file__).resolve().parent
    expected = {
        "clientSha256": _sha256(root / "eva_agentbench_client.py"),
        "runnerSha256": _sha256(root / "run_agentbench_dbbench_development.py"),
    }
    if any(candidate.get(key) != value for key, value in expected.items()):
        raise BatchError("candidate source differs from the sealed selection")
    return document, indices


def _write_private(path: Path, document: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def run_batch(*, controller: str, selection_path: Path, output_dir: Path) -> dict[str, Any]:
    selection, indices = _load_selection(selection_path)
    try:
        output_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise BatchError("output directory already exists") from error

    cases: list[dict[str, Any]] = []
    for index in indices:
        case_path = output_dir / f"case-{index}.json"
        try:
            artifact = run_one_case(
                controller=controller,
                case_index=index,
                output=case_path,
            )
        except DbBenchDevelopmentError:
            if not case_path.is_file():
                raise BatchError(f"case {index} failed without a sealed artifact") from None
            cases.append(
                {"artifactSha256": _sha256(case_path), "caseIndex": index, "outcome": "failed"}
            )
            continue
        result = artifact.get("result")
        cases.append(
            {
                "artifactSha256": _sha256(case_path),
                "caseIndex": index,
                "finish": result.get("finish") if isinstance(result, dict) else None,
                "modelTurnsUsed": artifact.get("modelTurnsUsed"),
                "outcome": "completed",
                "status": result.get("status") if isinstance(result, dict) else None,
            }
        )

    summary = {
        "contract": "EvaAgentBenchDbBenchPreselectedBatch/v1",
        "selectionSha256": _sha256(selection_path),
        "selectionRule": selection["selection"]["selectionRule"],
        "cases": cases,
        "completedCases": sum(case["outcome"] == "completed" for case in cases),
        "failedCases": sum(case["outcome"] == "failed" for case in cases),
        "hiddenAnswerRead": False,
        "retriedCases": 0,
        "nonClaims": [
            "This is not an official AgentBench or leaderboard score.",
            "The batch summary does not contain prompts, answers or hidden labels.",
        ],
    }
    _write_private(output_dir / "batch-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run_batch(
            controller=args.controller,
            selection_path=args.selection,
            output_dir=args.output_dir,
        )
    except BatchError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "completedCases": summary["completedCases"],
                "failedCases": summary["failedCases"],
                "status": "complete",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
