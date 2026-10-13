#!/usr/bin/env python3
"""Fail closed on incomplete or non-comparable public agent-run manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, NoReturn


HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "comparable-agent-run-v1.schema.json"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}@sha256:[0-9a-f]{64}\Z")
SAFE_PATH = re.compile(r"(?!/)(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")


def _object(value: object, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} key set differs")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be nonempty text")
    return value


def _sha(value: object, name: str) -> str:
    value = _text(value, name)
    if not SHA256.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if not DIGEST.fullmatch(value):
        raise ValueError(f"{name} must be immutable image digest")
    return value


def _positive(value: object, name: str, *, zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < (0 if zero else 1):
        raise ValueError(f"{name} must be a valid integer limit")
    return value


def validate(manifest: object) -> dict[str, object]:
    root = _object(
        manifest,
        "run",
        {
            "adapter", "agent", "artifacts", "benchmark", "contract",
            "environment", "execution", "model", "result", "runId", "upstream",
        },
    )
    if root["contract"] != "ComparableAgentRun/v1":
        raise ValueError("run contract differs")
    run_id = _text(root["runId"], "runId")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,119}", run_id):
        raise ValueError("runId differs")
    benchmark = root["benchmark"]
    if benchmark not in {"agentbench_fc", "gaia", "osworld_2"}:
        raise ValueError("benchmark differs")
    upstream = _object(
        root["upstream"], "upstream",
        {"assetsRevision", "datasetRevision", "release", "repository", "scorerSha256", "sourceCommitSha256"},
    )
    if not _text(upstream["repository"], "upstream.repository").startswith("https://"):
        raise ValueError("upstream.repository must be HTTPS")
    for key in ("assetsRevision", "datasetRevision", "release"):
        _text(upstream[key], f"upstream.{key}")
    for key in ("scorerSha256", "sourceCommitSha256"):
        _sha(upstream[key], f"upstream.{key}")
    agent = _object(root["agent"], "agent", {"commitSha256", "id", "imageDigest"})
    agent_id = _text(agent["id"], "agent.id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", agent_id):
        raise ValueError("agent.id differs")
    _sha(agent["commitSha256"], "agent.commitSha256")
    _digest(agent["imageDigest"], "agent.imageDigest")
    adapter = _object(root["adapter"], "adapter", {"sha256", "version"})
    _sha(adapter["sha256"], "adapter.sha256")
    _text(adapter["version"], "adapter.version")
    model = _object(root["model"], "model", {"id", "provider", "reasoning", "revision", "temperature"})
    _text(model["id"], "model.id")
    _text(model["provider"], "model.provider")
    if model["reasoning"] is not None and not isinstance(model["reasoning"], str):
        raise ValueError("model.reasoning differs")
    if model["revision"] is not None and not isinstance(model["revision"], str):
        raise ValueError("model.revision differs")
    if model["temperature"] is not None and (
        not isinstance(model["temperature"], (int, float))
        or isinstance(model["temperature"], bool)
        or not 0 <= model["temperature"] <= 2
    ):
        raise ValueError("model.temperature differs")
    execution = _object(
        root["execution"], "execution",
        {"contextTokenLimit", "costEuroMicrosLimit", "mode", "networkPolicy", "retryLimit", "seed", "stepLimit", "timeLimitSeconds"},
    )
    if execution["mode"] not in {"native", "controlled"}:
        raise ValueError("execution.mode differs")
    if execution["networkPolicy"] not in {"benchmark_required", "none", "recorded_allowlist"}:
        raise ValueError("execution.networkPolicy differs")
    for key in ("contextTokenLimit", "stepLimit", "timeLimitSeconds"):
        _positive(execution[key], f"execution.{key}")
    for key in ("costEuroMicrosLimit", "retryLimit", "seed"):
        _positive(execution[key], f"execution.{key}", zero=True)
    if execution["retryLimit"] > 20:
        raise ValueError("execution.retryLimit exceeds 20")
    environment = _object(root["environment"], "environment", {"browserOrVmImageDigest", "hardwareClass", "osImageDigest"})
    _digest(environment["browserOrVmImageDigest"], "environment.browserOrVmImageDigest")
    _digest(environment["osImageDigest"], "environment.osImageDigest")
    _text(environment["hardwareClass"], "environment.hardwareClass")
    artifacts = root["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("artifacts must be nonempty")
    kinds: set[str] = set()
    paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        item = _object(artifact, f"artifacts[{index}]", {"kind", "path", "sha256"})
        if item["kind"] not in {"answers", "runner_log", "screenshot_or_video_index", "tool_trace"}:
            raise ValueError("artifact kind differs")
        path = _text(item["path"], "artifact.path")
        if not SAFE_PATH.fullmatch(path):
            raise ValueError("artifact path differs")
        _sha(item["sha256"], "artifact.sha256")
        if item["kind"] in kinds or path in paths:
            raise ValueError("artifact kind or path is duplicated")
        kinds.add(item["kind"])
        paths.add(path)
    if {"answers", "runner_log", "tool_trace"} - kinds:
        raise ValueError("required replay artifacts are missing")
    result = _object(
        root["result"], "result",
        {"humanInterventionCount", "officialScore", "status", "totalCostEuroMicros", "totalTokenCount", "wallTimeSeconds"},
    )
    if result["status"] not in {"planned", "executed"}:
        raise ValueError("result.status differs")
    score = result["officialScore"]
    metrics = ("humanInterventionCount", "totalCostEuroMicros", "totalTokenCount", "wallTimeSeconds")
    if result["status"] == "planned":
        if score is not None or any(result[key] is not None for key in metrics):
            raise ValueError("planned run must not claim a result")
    else:
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            raise ValueError("executed run officialScore differs")
        for key in metrics:
            _positive(result[key], f"result.{key}", zero=True)
    return {
        "agent": agent_id,
        "benchmark": benchmark,
        "contract": "ComparableAgentRun/v1",
        "mode": execution["mode"],
        "runId": run_id,
        "status": result["status"],
    }


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("run manifest is unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    try:
        if not SCHEMA.is_file():
            raise ValueError("ComparableAgentRun schema is missing")
        summary = validate(_load(arguments.manifest))
    except ValueError as error:
        _die(str(error))
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"comparable-agent-run: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
