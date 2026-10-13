"""Development selection is quality-first and separate from final approval."""
from copy import deepcopy

import pytest

from eva.application.component_development import compare_development_evaluations, validate_development_criteria

CRITERIA = {"id": "public-development-fixture", "objective": "Relevant task results",
    "metrics": [{"name": "hits", "direction": "maximize", "kind": "quality"},
                {"name": "bytes", "direction": "minimize", "kind": "resource"}],
    "hard_checks": {"leaks": 0, "stopFailures": 0}, "case_count_metric": "total"}


def entry(step, *, hits=2, bytes=100, **changes):
    metrics = {"hits": hits, "bytes": bytes, "leaks": 0, "stopFailures": 0, "total": 4, **changes}
    leg = {"exit_code": 0, "timed_out": False, "stdout_truncated": False,
           "stderr_truncated": False, "elapsed_ms": 7}
    return {"evaluation_step": step, "draft_step": step - 1, "report": {
        "contract": "EvaIsolatedComponentEvaluation/v1", "relative_path": "application/example.py",
        "baseline_sha256": "a" * 64, "candidate_sha256": str(step) * 64,
        "image_id": "sha256:" + "b" * 64, "command_sha256": "c" * 64,
        "installed": False, "semantic_improvement_verified": False,
        "baseline": dict(leg), "candidate": dict(leg),
        "development_assessment": {"contract": "EvaComponentDevelopmentAssessment/v1",
            "assessment_sha256": "d" * 64, "criteria": deepcopy(CRITERIA),
            "baseline": {"complete": True, "metrics": {"hits": 2, "bytes": 100,
                "leaks": 0, "stopFailures": 0, "total": 4}},
            "candidate": {"complete": True, "metrics": metrics}}}}


def test_quality_wins_even_if_more_expensive_and_keeps_exact_provenance():
    result = compare_development_evaluations([entry(2, hits=1, bytes=10),
                                              entry(4, hits=3, bytes=500)])
    assert result["state"] == "candidate_selected"
    assert result["selected_draft_step"] == 3
    assert result["selected_evaluation_step"] == 4
    assert result["selected_candidate_sha256"] == "4" * 64
    assert result["installed"] is result["semantic_improvement_verified"] is False
    assert result["candidates"][1]["elapsed_ms"] == 7


def test_resource_tiebreak_requires_equal_quality_and_loss_keeps_baseline():
    assert compare_development_evaluations([entry(2, bytes=99)])["state"] == "candidate_selected"
    for candidate in [entry(2), entry(2, hits=1, bytes=1)]:
        result = compare_development_evaluations([candidate])
        assert result["state"] == "baseline_retained"
        assert result["selected_draft_step"] is None


@pytest.mark.parametrize("changes", [
    {"leaks": 1}, {"stopFailures": 1}, {"leaks": True}, {"hits": float("nan")},
    {"hits": float("inf")}, {"hits": None}, {"total": 3}, {"total": 4.0},
])
def test_failed_unknown_or_incomparable_metrics_never_win(changes):
    candidate = entry(2, hits=4, **changes) if "hits" not in changes else entry(2, **changes)
    result = compare_development_evaluations([candidate])
    assert result["state"] == "baseline_retained"
    assert result["candidates"][0]["error"]


@pytest.mark.parametrize("field,value", [
    ("exit_code", 1), ("timed_out", True), ("stdout_truncated", True),
    ("stderr_truncated", True),
])
def test_failed_execution_remains_visible(field, value):
    candidate = entry(2, hits=4)
    candidate["report"]["candidate"][field] = value
    result = compare_development_evaluations([candidate])
    assert result["state"] == "baseline_retained"
    assert "failed" in result["candidates"][0]["error"]


@pytest.mark.parametrize("mutation", ["baseline", "image", "rules", "assessment", "baseline_metrics"])
def test_different_reference_or_rules_are_rejected(mutation):
    second = entry(4, hits=4)
    report = second["report"]
    if mutation == "baseline": report["baseline_sha256"] = "e" * 64
    if mutation == "image": report["image_id"] = "other"
    if mutation == "rules": report["development_assessment"]["criteria"]["metrics"].reverse()
    if mutation == "assessment": report["development_assessment"]["assessment_sha256"] = "e" * 64
    if mutation == "baseline_metrics": report["development_assessment"]["baseline"]["metrics"]["hits"] = 1
    with pytest.raises(ValueError):
        compare_development_evaluations([entry(2), second])


def test_duplicates_are_not_extra_evidence_and_rules_cannot_prioritize_cost():
    second = entry(4)
    second["report"]["candidate_sha256"] = "2" * 64
    result = compare_development_evaluations([entry(2), second])
    assert "duplicate" in result["candidates"][1]["error"]
    rules = deepcopy(CRITERIA)
    rules["metrics"].reverse()
    with pytest.raises(ValueError, match="quality"):
        validate_development_criteria(rules)
