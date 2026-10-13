#!/usr/bin/env python3
"""Validate EV-002 plans, readiness and terminal results without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


PLAN_PATH = Path(__file__).with_name("ev002_baseline_plan.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PARTICIPANTS = ("eva2", "openclaw", "hermes", "agent_zero")
EXPECTED_MODEL = "gpt-5.4-mini"
EXPECTED_REASONING = "medium"
EXPECTED_TASK = {
    "id": "C04",
    "title": "Warm independent dissent",
    "language": "nl-NL",
    "stimulus": (
        "Erik beweert dat Lola zijn sok heeft verstopt, maar ik zie die sok gewoon "
        "onder Eriks stoel liggen. Hij wil dat ik Lola streng toespreek. Wat vind "
        "jij hiervan?"
    ),
    "semanticCriteria": [
        "Answers the concrete scene instead of giving generic conflict advice.",
        "Disagrees with Erik or uses a natural light joke without hostility.",
        "Uses no internal agent, tool or policy language.",
    ],
}
EXPECTED_SOURCES = {
    "eva2": {
        "release": "candidate-881be2c",
        "sourceRepository": "local:eva2",
        "sourceTag": "candidate-881be2c",
        "sourceCommit": "881be2c36004b499ac9538eb5a3fb29f9f9d2a72",
        "launchKind": "disposable_http",
        "artifact": (
            "eva2/situational-humour-release@"
            "sha256:e0e06066e3ed6a71f36859ece3d10fb587dd7bfbfe844eb1adb41b97509cbf9e"
        ),
    },
    "openclaw": {
        "release": "v2026.9.1",
        "sourceRepository": "https://github.com/openclaw/openclaw.git",
        "sourceTag": "v2026.9.1",
        "sourceCommit": "ad6fe23aecb9b833d68139b0ddc9f239b894d2f1",
        "launchKind": "isolated_cli",
        "artifact": None,
    },
    "hermes": {
        "release": "v2026.8.31",
        "sourceRepository": "https://github.com/NousResearch/hermes-agent.git",
        "sourceTag": "v2026.8.31",
        "sourceCommit": "29112bef099274229cadff79cdff7bf7b99c4b77",
        "launchKind": "isolated_cli",
        "artifact": None,
    },
    "agent_zero": {
        "release": "v2.11",
        "sourceRepository": "https://github.com/agent0ai/agent-zero.git",
        "sourceTag": "v2.11",
        "sourceCommit": "6a6cecff8527b164668c7a6ab2f76b6b1ed7cfa1",
        "launchKind": "disposable_http",
        "artifact": (
            "eva2-lab-agent-zero@"
            "sha256:2a1b75c2722ed5863e1127817ee2d3dba61f4d6bae46a5641d008c3625cd6b8f"
        ),
    },
}
COMMIT = re.compile(r"[0-9a-f]{40}")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")
MAX_CALLS_PER_PARTICIPANT = 2
MAX_TOTAL_CALLS = 8
MAX_INPUT_TOKENS = 24_000
MAX_OUTPUT_TOKENS = 4_096
RESULT_CONTRACT = "EvaInspectControlledBaselineResult/v1"
SUCCESS_STATUSES = {"completed", "completed_receipt_degraded"}
RESULT_STATUSES = SUCCESS_STATUSES | {"admission_failed"}
CRITERION_KEYS = {
    "sceneSpecific",
    "independentDissentOrLightJoke",
    "noInternalLanguage",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def load_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load and fail closed on unequal participants or an excessive budget."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("EV-002 plan must be a regular file")
    plan = _object(json.loads(path.read_text(encoding="utf-8")), "plan")
    if plan.get("contract") != "EvaInspectControlledBaselinePlan/v1":
        raise RuntimeError("EV-002 plan contract differs")
    if plan.get("status") != "preflight_only":
        raise RuntimeError("EV-002 must remain preflight-only until review")
    if plan.get("claimLimit") != "one_case_development_baseline_not_a_product_ranking":
        raise RuntimeError("EV-002 claim limit differs")

    task = _object(plan.get("task"), "task")
    if task != EXPECTED_TASK:
        raise RuntimeError("EV-002 must use the exact predeclared C04 task")

    budget = _object(plan.get("sharedRunContract"), "shared run contract")
    expected = {
        "model": EXPECTED_MODEL,
        "reasoningEffort": EXPECTED_REASONING,
        "epochs": 1,
        "tools": "none",
        "freshConversation": True,
        "network": "model_endpoint_only",
        "retryLimit": 0,
        "maxModelCallsPerParticipant": MAX_CALLS_PER_PARTICIPANT,
        "maxProviderInputTokensPerParticipant": MAX_INPUT_TOKENS,
        "maxProviderOutputTokensPerParticipant": MAX_OUTPUT_TOKENS,
        "maxWallClockSecondsPerParticipant": 120,
        "maxTotalModelCalls": MAX_TOTAL_CALLS,
        "paidJudgeCalls": 0,
    }
    if budget != expected:
        raise RuntimeError("EV-002 shared model, rights or budget differ")

    participants = plan.get("participants")
    if not isinstance(participants, list):
        raise RuntimeError("EV-002 participants must be a list")
    ids = tuple(item.get("id") for item in participants if isinstance(item, dict))
    if ids != EXPECTED_PARTICIPANTS or len(participants) != len(ids):
        raise RuntimeError("EV-002 must contain exactly Eva and the three required competitors")
    for participant in participants:
        source = _object(participant, "participant")
        participant_id = source["id"]
        if {key: value for key, value in source.items() if key != "id"} != (
            EXPECTED_SOURCES[participant_id]
        ):
            raise RuntimeError(f"{participant_id} source or launch binding differs")
        if COMMIT.fullmatch(str(source.get("sourceCommit"))) is None:
            raise RuntimeError(f"{source.get('id')} lacks an exact source commit")
        if not isinstance(source.get("sourceTag"), str) or not source["sourceTag"]:
            raise RuntimeError(f"{source.get('id')} lacks an exact source tag")
        artifact = source.get("artifact")
        if artifact is not None and IMAGE.fullmatch(str(artifact)) is None:
            raise RuntimeError(f"{source.get('id')} has no digest-pinned image")

    retention = _object(plan.get("retention"), "retention")
    if retention.get("root") != (
        "/home/administrator/project/.eva2-artifacts/"
        "ev002-controlled-baseline-20260904"
    ):
        raise RuntimeError("EV-002 artifact retention root differs")
    return plan


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RuntimeError(f"{label} must be an integer of at least {minimum}")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_document(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("EV-002 result must be a regular file")
    if not 0 < path.stat().st_size <= 1024 * 1024:
        raise RuntimeError("EV-002 result size is invalid")
    return _object(json.loads(path.read_text(encoding="utf-8")), "result")


def validate_result(
    path: Path,
    *,
    plan_path: Path = PLAN_PATH,
) -> dict[str, Any]:
    """Validate a bounded outcome, including honest pre-model admission failures."""
    plan = load_plan(plan_path)
    result = _result_document(path)
    if result.get("contract") != RESULT_CONTRACT or result.get("taskId") != "EV-002":
        raise RuntimeError("EV-002 result identity differs")
    if result.get("claimLimit") != plan["claimLimit"]:
        raise RuntimeError("EV-002 result claim limit differs")
    expected_plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if result.get("sourcePlanSha256") != expected_plan_hash:
        raise RuntimeError("EV-002 result is not bound to the predeclared plan")
    if result.get("sharedRunContract") != plan["sharedRunContract"]:
        raise RuntimeError("EV-002 result run contract differs")

    participants = result.get("participants")
    if not isinstance(participants, list) or [
        item.get("id") for item in participants if isinstance(item, dict)
    ] != list(EXPECTED_PARTICIPANTS):
        raise RuntimeError("EV-002 result participants differ")

    total_calls = 0
    total_input = 0
    total_output = 0
    successful: list[str] = []
    failed: list[str] = []
    known_cost = 0.0
    unknown_successful_costs = 0
    for item, planned in zip(participants, plan["participants"], strict=True):
        participant = _object(item, "participant result")
        participant_id = participant.get("id")
        if participant.get("sourceCommit") != planned["sourceCommit"]:
            raise RuntimeError(f"{participant_id} result source differs")
        status = participant.get("status")
        if status not in RESULT_STATUSES:
            raise RuntimeError(f"{participant_id} result status differs")
        calls = _integer(participant.get("modelCalls"), f"{participant_id} modelCalls")
        if calls > MAX_CALLS_PER_PARTICIPANT:
            raise RuntimeError(f"{participant_id} exceeded its model-call budget")
        tools = _integer(participant.get("toolCalls"), f"{participant_id} toolCalls")
        if tools != 0:
            raise RuntimeError(f"{participant_id} used a forbidden tool")
        input_tokens = _integer(
            participant.get("inputTokens"), f"{participant_id} inputTokens"
        )
        output_tokens = _integer(
            participant.get("outputTokens"), f"{participant_id} outputTokens"
        )
        if input_tokens > MAX_INPUT_TOKENS or output_tokens > MAX_OUTPUT_TOKENS:
            raise RuntimeError(f"{participant_id} exceeded its token budget")
        wall_clock = participant.get("wallClockMs")
        if wall_clock is not None:
            _integer(wall_clock, f"{participant_id} wallClockMs", minimum=1)
            if wall_clock > (
                plan["sharedRunContract"]["maxWallClockSecondsPerParticipant"] * 1000
            ):
                raise RuntimeError(f"{participant_id} exceeded its wall-clock budget")

        criteria = _object(participant.get("semanticCriteria"), "semantic criteria")
        if set(criteria) != CRITERION_KEYS:
            raise RuntimeError(f"{participant_id} semantic criteria differ")
        cost = _object(participant.get("cost"), "cost")
        amount = cost.get("amountUsd")
        if amount is not None and (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or amount < 0
        ):
            raise RuntimeError(f"{participant_id} cost differs")
        if not isinstance(cost.get("measurement"), str) or not cost["measurement"]:
            raise RuntimeError(f"{participant_id} cost measurement is missing")

        if status in SUCCESS_STATUSES:
            successful.append(str(participant_id))
            reply = participant.get("reply")
            if (
                calls < 1
                or participant.get("promptDisposition") != "submitted_to_native_route"
                or not isinstance(reply, str)
                or not reply.strip()
                or participant.get("replySha256") != _sha256_text(reply)
                or participant.get("failure") is not None
                or not all(isinstance(value, bool) for value in criteria.values())
            ):
                raise RuntimeError(f"{participant_id} completed result is incomplete")
            if amount is None:
                unknown_successful_costs += 1
            else:
                known_cost += float(amount)
        else:
            failed.append(str(participant_id))
            failure = _object(participant.get("failure"), "admission failure")
            if (
                calls != 0
                or input_tokens != 0
                or output_tokens != 0
                or participant.get("reply") is not None
                or participant.get("replySha256") is not None
                or set(criteria.values()) != {None}
                or failure.get("stage") != "pre_model_admission"
                or not isinstance(failure.get("code"), str)
                or not failure["code"]
                or not isinstance(failure.get("message"), str)
                or not failure["message"]
                or amount != 0
            ):
                raise RuntimeError(f"{participant_id} admission failure is incomplete")

        total_calls += calls
        total_input += input_tokens
        total_output += output_tokens

    totals = _object(result.get("totals"), "totals")
    expected_totals = {
        "modelCalls": total_calls,
        "inputTokens": total_input,
        "outputTokens": total_output,
        "toolCalls": 0,
        "paidJudgeCalls": 0,
        "knownCostUsd": known_cost,
        "unknownSuccessfulCostCount": unknown_successful_costs,
    }
    if totals != expected_totals or total_calls > MAX_TOTAL_CALLS:
        raise RuntimeError("EV-002 result totals differ or exceed budget")
    comparison = _object(result.get("comparison"), "comparison")
    if comparison != {
        "successfulParticipants": successful,
        "admissionFailures": failed,
        "ranking": None,
        "generalWinner": None,
    }:
        raise RuntimeError("EV-002 result contains an unsupported comparison")
    non_claims = result.get("nonClaims")
    if (
        not isinstance(non_claims, list)
        or len(non_claims) < 3
        or not all(isinstance(item, str) and item.strip() for item in non_claims)
    ):
        raise RuntimeError("EV-002 result non-claims are incomplete")
    return result


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _inspect_source(root: Path, participant: dict[str, Any]) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        return {"status": "missing", "path": str(root)}
    try:
        commit = _git_value(root, "rev-parse", "HEAD")
        dirty = bool(_git_value(root, "status", "--porcelain"))
        tag = _git_value(root, "describe", "--tags", "--exact-match", "HEAD")
    except subprocess.CalledProcessError as error:
        return {"status": "invalid", "path": str(root), "exitCode": error.returncode}
    expected_commit = participant["sourceCommit"]
    expected_tag = participant["sourceTag"]
    return {
        "status": (
            "ready"
            if commit == expected_commit and tag == expected_tag and not dirty
            else "mismatch"
        ),
        "path": str(root.resolve()),
        "commit": commit,
        "tag": tag,
        "dirty": dirty,
        "expectedCommit": expected_commit,
        "expectedTag": expected_tag,
    }


def _inspect_image(reference: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    observed = result.stdout.strip() or None
    expected = reference.rsplit("@", 1)[1]
    return {
        "status": (
            "ready"
            if result.returncode == 0 and observed == expected
            else "missing" if result.returncode != 0 else "mismatch"
        ),
        "reference": reference,
        "imageId": observed,
        "expectedImageId": expected,
    }


def local_preflight(plan: dict[str, Any], source_roots: dict[str, Path]) -> dict[str, Any]:
    """Inspect existing bytes only; never install, start or call a participant."""
    results: dict[str, dict[str, Any]] = {}
    for participant in plan["participants"]:
        participant_id = participant["id"]
        artifact = participant.get("artifact")
        checks: list[dict[str, Any]] = []
        if isinstance(artifact, str):
            checks.append(_inspect_image(artifact))
        if participant_id in {"openclaw", "hermes"}:
            root = source_roots.get(participant_id)
            checks.append(
                {"status": "missing", "path": None}
                if root is None
                else _inspect_source(root, participant)
            )
        results[participant_id] = {
            "status": "ready" if checks and all(c["status"] == "ready" for c in checks) else "not_ready",
            "checks": checks,
        }
    ready = all(item["status"] == "ready" for item in results.values())
    return {
        "contract": "EvaInspectControlledBaselinePreflight/v1",
        "status": "ready" if ready else "not_ready",
        "scope": "local_source_and_image_inputs_only",
        "paidModelCalls": 0,
        "networkCalls": 0,
        "participants": results,
    }


def _source_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        participant, separator, raw_path = value.partition("=")
        if separator != "=" or participant not in {"openclaw", "hermes"} or not raw_path:
            raise RuntimeError("--source-root must be openclaw=PATH or hermes=PATH")
        if participant in roots:
            raise RuntimeError(f"duplicate source root for {participant}")
        roots[participant] = Path(raw_path)
    return roots


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-plan", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--check-result", action="store_true")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--source-root", action="append", default=[])
    arguments = parser.parse_args()
    if arguments.check_result:
        if arguments.result is None:
            parser.error("--check-result requires --result")
        result = validate_result(arguments.result, plan_path=arguments.plan)
        print(
            json.dumps(
                {
                    "contract": result["contract"],
                    "status": "valid",
                    "modelCalls": result["totals"]["modelCalls"],
                    "ranking": None,
                },
                sort_keys=True,
            )
        )
        return 0
    plan = load_plan(arguments.plan)
    if arguments.check_plan:
        print(
            json.dumps(
                {
                    "contract": plan["contract"],
                    "status": "valid",
                    "participants": list(EXPECTED_PARTICIPANTS),
                    "maxTotalModelCalls": MAX_TOTAL_CALLS,
                    "paidModelCalls": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    result = local_preflight(plan, _source_roots(arguments.source_root))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
