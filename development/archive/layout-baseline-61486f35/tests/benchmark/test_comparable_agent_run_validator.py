from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "benchmark" / "validate_comparable_agent_run.py"
SPEC = importlib.util.spec_from_file_location("comparable_run_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

SHA = "a" * 64
DIGEST = f"registry.example/eva@sha256:{SHA}"


def _manifest(*, status: str = "executed") -> dict[str, object]:
    result: dict[str, object] = {
        "humanInterventionCount": 0,
        "officialScore": 0.5,
        "status": status,
        "totalCostEuroMicros": 123,
        "totalTokenCount": 456,
        "wallTimeSeconds": 78,
    }
    if status == "planned":
        for key in (
            "humanInterventionCount",
            "officialScore",
            "totalCostEuroMicros",
            "totalTokenCount",
            "wallTimeSeconds",
        ):
            result[key] = None
    return {
        "adapter": {"sha256": SHA, "version": "v1"},
        "agent": {"commitSha256": SHA, "id": "eva2", "imageDigest": DIGEST},
        "artifacts": [
            {"kind": "answers", "path": "artifacts/answers.jsonl", "sha256": SHA},
            {"kind": "runner_log", "path": "artifacts/runner.log", "sha256": SHA},
            {"kind": "tool_trace", "path": "artifacts/tools.jsonl", "sha256": SHA},
        ],
        "benchmark": "gaia",
        "contract": "ComparableAgentRun/v1",
        "environment": {
            "browserOrVmImageDigest": DIGEST,
            "hardwareClass": "amd64-8cpu-32gb",
            "osImageDigest": DIGEST,
        },
        "execution": {
            "contextTokenLimit": 12000,
            "costEuroMicrosLimit": 500000,
            "mode": "controlled",
            "networkPolicy": "benchmark_required",
            "retryLimit": 1,
            "seed": 7,
            "stepLimit": 30,
            "timeLimitSeconds": 900,
        },
        "model": {
            "id": "model-name",
            "provider": "provider-name",
            "reasoning": "medium",
            "revision": "provider-revision",
            "temperature": 0,
        },
        "result": result,
        "runId": "gaia-controlled-eva2-001",
        "upstream": {
            "assetsRevision": "assets-1",
            "datasetRevision": "dataset-1",
            "release": "release-1",
            "repository": "https://example.invalid/upstream",
            "scorerSha256": SHA,
            "sourceCommitSha256": SHA,
        },
    }


def test_valid_executed_and_planned_run_have_a_small_public_summary() -> None:
    assert validator.validate(_manifest()) == {
        "agent": "eva2",
        "benchmark": "gaia",
        "contract": "ComparableAgentRun/v1",
        "mode": "controlled",
        "runId": "gaia-controlled-eva2-001",
        "status": "executed",
    }
    assert validator.validate(_manifest(status="planned"))["status"] == "planned"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda item: item["upstream"].pop("scorerSha256"), "upstream key set"),
        (lambda item: item["agent"].update({"imageDigest": "latest"}), "immutable"),
        (lambda item: item["artifacts"].pop(), "required replay"),
        (lambda item: item["artifacts"][0].update({"path": "../answers.jsonl"}), "artifact path"),
        (lambda item: item["result"].update({"officialScore": 0.7}), "planned run"),
    ],
)
def test_invalid_provenance_or_result_fails_closed(mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    manifest = _manifest(status="planned")
    if message != "planned run":
        manifest = _manifest()
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        validator.validate(manifest)


def test_cli_accepts_valid_file_and_rejects_missing_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(manifest)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["runId"] == "gaia-controlled-eva2-001"
