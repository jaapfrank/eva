"""Rank fixed development evidence; never approve semantic benefit or installation."""
from __future__ import annotations

import math
import re

from eva.contracts.canonical_json import canonical_sha256


def validate_development_criteria(criteria):
    if (not isinstance(criteria, dict)
            or set(criteria) != {"id", "objective", "metrics", "hard_checks", "case_count_metric"}
            or not isinstance(criteria["id"], str) or not 1 <= len(criteria["id"]) <= 128
            or not isinstance(criteria["objective"], str) or not 1 <= len(criteria["objective"]) <= 2000
            or not isinstance(criteria["case_count_metric"], str)
            or not criteria["case_count_metric"]
            or not isinstance(criteria["hard_checks"], dict) or not criteria["hard_checks"]
            or not isinstance(criteria["metrics"], list) or not 1 <= len(criteria["metrics"]) <= 16):
        raise ValueError("invalid fixed development criteria")
    names = set()
    resource_seen = False
    quality_seen = False
    for metric in criteria["metrics"]:
        if (not isinstance(metric, dict) or set(metric) != {"name", "direction", "kind"}
                or not isinstance(metric["name"], str) or not metric["name"]
                or metric["name"] in names or metric["direction"] not in {"maximize", "minimize"}
                or metric["kind"] not in {"quality", "resource"}):
            raise ValueError("invalid development metric")
        names.add(metric["name"])
        if metric["kind"] == "resource":
            resource_seen = True
        else:
            if resource_seen:
                raise ValueError("quality must precede resource metrics")
            quality_seen = True
    if not quality_seen:
        raise ValueError("development criteria require result quality")
    for name, expected in criteria["hard_checks"].items():
        if not isinstance(name, str) or not name or not _number(expected):
            raise ValueError("invalid hard development check")
    return criteria


def _number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def compare_development_evaluations(entries):
    """Choose only a measured development winner under identical frozen rules."""
    if not entries:
        raise ValueError("development comparison requires recorded evaluations")
    first = entries[0]["report"]
    assessment = first.get("development_assessment", {})
    criteria = validate_development_criteria(assessment.get("criteria"))
    criterion_hash = canonical_sha256(criteria)
    reference = (first["relative_path"], first["baseline_sha256"], first["image_id"],
                 first["command_sha256"], assessment.get("assessment_sha256"), criterion_hash)
    if not isinstance(reference[4], str) or not re.fullmatch(r"[0-9a-f]{64}", reference[4]):
        raise ValueError("development assessment binding missing")
    seen_steps, seen_candidates = set(), set()
    baseline_metrics = assessment["baseline"]["metrics"]

    def rank(metrics):
        if not isinstance(metrics, dict):
            raise ValueError("development metrics missing")
        required = [m["name"] for m in criteria["metrics"]]
        required += list(criteria["hard_checks"]) + [criteria["case_count_metric"]]
        if any(not _number(metrics.get(name)) for name in required):
            raise ValueError("development metric is missing or non-finite")
        if any(metrics[name] != expected for name, expected in criteria["hard_checks"].items()):
            raise ValueError("hard development condition failed")
        count = metrics[criteria["case_count_metric"]]
        if type(count) is not int or count <= 0 or count != baseline_metrics[criteria["case_count_metric"]]:
            raise ValueError("development case count differs")
        return tuple(metrics[m["name"]] * (1 if m["direction"] == "maximize" else -1)
                     for m in criteria["metrics"])

    baseline_rank = rank(baseline_metrics)
    ranked = []
    for entry in entries:
        report = entry["report"]
        observed = report.get("development_assessment", {})
        binding = (report.get("relative_path"), report.get("baseline_sha256"), report.get("image_id"),
                   report.get("command_sha256"), observed.get("assessment_sha256"),
                   canonical_sha256(observed.get("criteria")))
        if (binding != reference or observed.get("contract") != "EvaComponentDevelopmentAssessment/v1"
                or report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
                or report.get("installed") is not False
                or report.get("semantic_improvement_verified") is not False
                or observed.get("baseline", {}).get("metrics") != baseline_metrics):
            raise ValueError("development evaluation basis differs")
        step, draft_step = entry["evaluation_step"], entry["draft_step"]
        if (type(step) is not int or type(draft_step) is not int
                or draft_step < 0 or step <= draft_step or step in seen_steps):
            raise ValueError("duplicate or invalid development step")
        seen_steps.add(step)
        candidate_hash = report["candidate_sha256"]
        row = {"evaluation_step": step, "draft_step": draft_step,
               "candidate_sha256": candidate_hash,
               "metrics": observed.get("candidate", {}).get("metrics"),
               "elapsed_ms": report.get("candidate", {}).get("elapsed_ms"),
               "error": None}
        try:
            for leg in ("baseline", "candidate"):
                result = report[leg]
                if (result.get("exit_code") != 0 or result.get("timed_out") is not False
                        or result.get("stdout_truncated") is not False
                        or result.get("stderr_truncated") is not False
                        or observed.get(leg, {}).get("complete") is not True):
                    raise ValueError("incomplete or failed development execution")
            key = rank(row["metrics"])
            if candidate_hash in seen_candidates:
                row["error"] = "duplicate candidate; not additional evidence"
            else:
                seen_candidates.add(candidate_hash)
                row["rank"] = list(key)
        except (ValueError, KeyError, TypeError) as error:
            row["error"] = str(error)
        ranked.append(row)
    valid = [row for row in ranked if row["error"] is None]
    best = max(valid, key=lambda row: tuple(row["rank"]), default=None)
    winner = best if best is not None and tuple(best["rank"]) > baseline_rank else None
    return {"contract": "EvaComponentDevelopmentSelection/v1", "state": (
            "candidate_selected" if winner else "baseline_retained"),
        "path": reference[0], "baseline_sha256": reference[1],
        "assessment_sha256": reference[4], "criteria_sha256": criterion_hash,
        "criteria": criteria, "baseline_metrics": baseline_metrics,
        "candidates": ranked, "selected_draft_step": winner["draft_step"] if winner else None,
        "selected_evaluation_step": winner["evaluation_step"] if winner else None,
        "selected_candidate_sha256": winner["candidate_sha256"] if winner else None,
        "installed": False, "semantic_improvement_verified": False,
        "guidance": "Ontwikkelkeuze, geen onafhankelijke eindbeoordeling of installatie. "
                    "Bij gelijkspel of verlies blijft de bestaande versie staan."}
