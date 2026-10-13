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
REPAIR_RECEIPT = (
    EVIDENCE / "agentbench-dbbench-eva-answerblind-repair-result-2026-08-30.json"
)
REVIEWED_RECEIPT = (
    EVIDENCE / "agentbench-dbbench-eva-reviewed-fixed-cases-16-23-2026-08-30.json"
)
DIAGNOSTIC_RECEIPT = (
    EVIDENCE
    / "agentbench-dbbench-eva-review-failure-subtype-diagnostic-result-2026-08-30.json"
)
BOUNDED_RECEIPT = (
    EVIDENCE / "agentbench-dbbench-eva-bounded-review-cases-24-31-2026-08-30.json"
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


def test_answerblind_repair_receipt_keeps_all_four_zero_scores() -> None:
    receipt = json.loads(REPAIR_RECEIPT.read_text(encoding="utf-8"))
    assert receipt["selectionCommit"] == "9149609"
    assert receipt["interpretation"]["promptOnlyRepairAccepted"] is False
    assert receipt["interpretation"]["originalFixedScoresRemainAuthoritative"] is True

    total_turns = 0
    total_model_failures = 0
    total_model_receipts = 0
    for recorded in receipt["cases"]:
        case_index = recorded["caseIndex"]
        path = EVIDENCE / (
            f"agentbench-dbbench-eva-answerblind-repair-case-{case_index}-2026-08-30.raw.json"
        )
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(raw_bytes).hexdigest() == recorded["rawSha256"]
        assert recorded["originalScore"] == 0
        assert recorded["repairScore"] == 0
        assert raw["result"]["metric"]["score"] == 0
        assert all(item["toolsExposed"] == 0 for item in raw["modelReceipts"])
        total_turns += raw["modelTurnsUsed"]
        total_model_failures += len(raw["modelFailures"])
        total_model_receipts += len(raw["modelReceipts"])

    aggregate = receipt["aggregate"]
    assert aggregate["repairCorrect"] == 0
    assert aggregate["repairIncorrect"] == 4
    assert aggregate["improvedScores"] == 0
    assert aggregate["modelCallsReservedBeforeIo"] == total_turns
    assert aggregate["adapterValidationFailuresBeforeDatabaseIo"] == total_model_failures
    assert aggregate["providerFailuresBeforeDatabaseIo"] == 0
    assert aggregate["successfulToolFreeModelReplies"] == total_model_receipts


def test_reviewed_fixed_set_keeps_correct_scores_and_boundary_failures() -> None:
    receipt = json.loads(REVIEWED_RECEIPT.read_text(encoding="utf-8"))
    assert receipt["selection"]["caseIndices"] == list(range(16, 24))
    assert receipt["selection"]["selectedBeforeAnyCaseResultWasKnown"] is True
    assert receipt["failureAnalysis"]["candidateAccepted"] is False

    scores: list[int] = []
    boundary_failures = 0
    total_calls = 0
    total_receipts = 0
    total_failures = 0
    total_sql = 0
    for recorded in receipt["cases"]:
        case_index = recorded["caseIndex"]
        path = EVIDENCE / (
            f"agentbench-dbbench-eva-reviewed-case-{case_index}-2026-08-30.raw.json"
        )
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(raw_bytes).hexdigest() == recorded["rawSha256"]
        assert all(item["toolsExposed"] == 0 for item in raw["modelReceipts"])
        tool_names = _tool_names(raw)
        assert tool_names.count("execute_sql") == recorded["sqlCalls"]
        if recorded["outcome"] == "scored":
            assert raw["result"]["metric"]["score"] == recorded["score"] == 1
            scores.append(recorded["score"])
            total_calls += raw["modelTurnsUsed"]
        else:
            assert recorded["outcome"] == "model_boundary_failure"
            assert recorded["score"] is None
            assert raw["contract"] == "EvaAgentBenchDbBenchDevelopmentFailure/v1"
            boundary_failures += 1
            total_calls += len(raw["modelReceipts"])
        total_receipts += len(raw["modelReceipts"])
        total_failures += len(raw["modelFailures"])
        total_sql += recorded["sqlCalls"]

    aggregate = receipt["aggregate"]
    assert aggregate["correctScoredCases"] == len(scores) == 6
    assert aggregate["incorrectScoredCases"] == 0
    assert aggregate["modelBoundaryFailures"] == boundary_failures == 2
    assert aggregate["modelCallsReservedBeforeIo"] == total_calls
    assert aggregate["successfulToolFreeModelReplies"] == total_receipts
    assert aggregate["adapterValidationFailuresBeforeDatabaseIo"] == total_failures
    assert aggregate["sqlCalls"] == total_sql


def test_case17_diagnostic_does_not_rewrite_the_original_fixed_failure() -> None:
    receipt = json.loads(DIAGNOSTIC_RECEIPT.read_text(encoding="utf-8"))
    path = (
        EVIDENCE
        / "agentbench-dbbench-eva-review-failure-subtype-diagnostic-case-17-2026-08-30.raw.json"
    )
    raw_bytes = path.read_bytes()
    raw = json.loads(raw_bytes)

    assert hashlib.sha256(raw_bytes).hexdigest() == receipt["result"]["rawSha256"]
    assert raw["result"]["metric"]["score"] == receipt["result"]["score"] == 1
    assert receipt["interpretation"]["originalFixedSetFailureChanged"] is False
    assert receipt["interpretation"]["originalCase17FailureStillAuthoritative"] is True
    assert receipt["interpretation"]["failureSubtypeReproduced"] is False
    assert receipt["interpretation"]["furtherRepeatedProbingOfCase17Allowed"] is False


def test_bounded_reviewer_is_rejected_with_all_failures_retained() -> None:
    receipt = json.loads(BOUNDED_RECEIPT.read_text(encoding="utf-8"))
    assert receipt["selection"]["caseIndices"] == list(range(24, 32))
    assert receipt["selection"]["selectedBeforeAnyCaseResultWasKnown"] is True
    assert receipt["decision"]["candidateAccepted"] is False
    assert receipt["decision"]["reviewerMustBeRemovedFromActiveBenchmarkPath"] is True

    scores: list[int] = []
    boundary_failures = 0
    provider_failures = 0
    adapter_failures = 0
    total_receipts = 0
    total_reviews = 0
    total_review_fallbacks = 0
    total_sql = 0
    for recorded in receipt["cases"]:
        case_index = recorded["caseIndex"]
        path = EVIDENCE / (
            f"agentbench-dbbench-eva-bounded-review-case-{case_index}-2026-08-30.raw.json"
        )
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(raw_bytes).hexdigest() == recorded["rawSha256"]
        assert raw["commitReviewsUsed"] == recorded["commitReviews"]
        assert all(item["toolsExposed"] == 0 for item in raw["modelReceipts"])
        tool_names = _tool_names(raw)
        assert tool_names.count("execute_sql") == recorded["sqlCalls"]
        if recorded["outcome"] == "scored":
            score = raw["result"]["metric"]["score"]
            assert score == recorded["score"]
            scores.append(score)
        else:
            assert recorded["outcome"] == "model_boundary_failure"
            assert recorded["score"] is None
            boundary_failures += 1
        for failure in raw["modelFailures"]:
            if failure["failure"] == "tool-free AgentBench request failed":
                provider_failures += 1
            else:
                adapter_failures += 1
        total_receipts += len(raw["modelReceipts"])
        total_reviews += raw["commitReviewsUsed"]
        total_review_fallbacks += len(raw["commitReviewFailures"])
        total_sql += recorded["sqlCalls"]

    aggregate = receipt["aggregate"]
    assert scores.count(1) == aggregate["correctScoredCases"] == 2
    assert scores.count(0) == aggregate["incorrectScoredCases"] == 4
    assert boundary_failures == aggregate["modelBoundaryFailures"] == 2
    assert total_receipts == aggregate["successfulToolFreeModelReplies"]
    assert provider_failures == aggregate["providerFailuresBeforeDatabaseIo"]
    assert adapter_failures == aggregate["adapterValidationFailuresBeforeDatabaseIo"]
    assert total_reviews == aggregate["commitReviews"]
    assert total_review_fallbacks == aggregate["invalidReviewFallbacks"]
    assert total_sql == aggregate["sqlCalls"]
    assert (
        total_receipts + provider_failures
        == aggregate["modelCallsReservedBeforeIo"]
    )
