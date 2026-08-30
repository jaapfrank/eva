from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence" / "living-core"
RECEIPT = EVIDENCE / "agentbench-dbbench-eva-tool-free-fixed-cases-8-15-2026-08-30.json"
SELECTION = (
    EVIDENCE
    / "agentbench-dbbench-eva-tool-free-fixed-cases-8-15-selection-2026-08-30.json"
)


def _raw_path(case_index: int) -> Path:
    return EVIDENCE / f"agentbench-dbbench-eva-tool-free-case-{case_index}-2026-08-30.raw.json"


def _tool_names(raw: dict[str, object]) -> list[str]:
    names: list[str] = []
    for turn in raw["trajectory"]:  # type: ignore[index]
        for call in turn["assistant"].get("tool_calls", []):
            names.append(call["function"]["name"])
    return names


def test_fixed_cases_8_through_15_receipt_matches_private_raw_artifacts() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    expected_indices = list(range(8, 16))

    assert selection["selection"]["caseIndices"] == expected_indices
    assert selection["selectedBeforeAnyCase8Through15ResultWasKnown"] is True
    assert receipt["selection"]["caseIndices"] == expected_indices
    assert receipt["selection"]["selectedBeforeAnyCaseResultWasKnown"] is True

    scores: list[int] = []
    total_turns = 0
    total_sql_calls = 0
    total_model_failures = 0
    total_model_receipts = 0
    for recorded in receipt["cases"]:
        case_index = recorded["caseIndex"]
        raw_path = _raw_path(case_index)
        raw_bytes = raw_path.read_bytes()
        raw = json.loads(raw_bytes)
        assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
        assert hashlib.sha256(raw_bytes).hexdigest() == recorded["rawSha256"]
        assert raw["caseIndex"] == case_index
        assert raw["result"]["finish"] is True
        assert raw["result"]["metric"]["score"] == recorded["score"]
        assert raw["modelTurnsUsed"] == recorded["modelTurns"]
        assert all(item["toolsExposed"] == 0 for item in raw["modelReceipts"])
        assert all(
            item["usage"]["measurement"] == "unmeasured"
            for item in raw["modelReceipts"]
        )
        tool_names = _tool_names(raw)
        assert tool_names.count("execute_sql") == recorded["sqlCalls"]
        assert tool_names.count("commit_final_answer") == 1
        assert set(tool_names) <= {"execute_sql", "commit_final_answer"}

        scores.append(recorded["score"])
        total_turns += raw["modelTurnsUsed"]
        total_sql_calls += recorded["sqlCalls"]
        total_model_failures += len(raw["modelFailures"])
        total_model_receipts += len(raw["modelReceipts"])

    aggregate = receipt["aggregate"]
    assert aggregate["casesSelected"] == len(expected_indices)
    assert aggregate["casesScored"] == len(scores)
    assert aggregate["correctScoredCases"] == scores.count(1)
    assert aggregate["incorrectScoredCases"] == scores.count(0)
    assert aggregate["modelCallsReservedBeforeIo"] == total_turns
    assert aggregate["providerFailuresBeforeDatabaseIo"] == total_model_failures
    assert aggregate["sqlCalls"] == total_sql_calls
    assert aggregate["successfulToolFreeModelReplies"] == total_model_receipts
    assert aggregate["toolsExposedByEvaModelBoundary"] == 0
