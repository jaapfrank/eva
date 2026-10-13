#!/usr/bin/env python3
"""Fail-closed verifier for Eva's LAB-36 core-live evidence matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise RuntimeError(f"invalid JSON evidence file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON evidence is not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(root: Path, index_path: Path) -> dict[str, object]:
    root = root.resolve()
    benchmark_path = root / "development/benchmark/eva-agent-benchmark-v1.json"
    benchmark = _json(benchmark_path)
    index = _json((root / index_path).resolve())
    if benchmark.get("contract") != "LivedAgentBenchmark/v1":
        raise RuntimeError("LAB-36 benchmark contract differs")
    expected = [
        item["case_id"]
        for item in benchmark.get("cases", [])
        if isinstance(item, dict) and item.get("track") == "core_live"
    ]
    if len(expected) != 15 or len(set(expected)) != len(expected):
        raise RuntimeError("LAB-36 core-live catalog differs")
    if (
        index.get("contract") != "EvaLab36CoreLiveEvidenceIndex/v1"
        or index.get("benchmark_sha256") != _sha256(benchmark_path)
    ):
        raise RuntimeError("LAB-36 core-live index authority differs")
    cases = index.get("cases")
    if not isinstance(cases, list) or [item.get("case_id") for item in cases] != expected:
        raise RuntimeError("LAB-36 core-live index coverage differs")
    evidence_count = 0
    for item in cases:
        if not isinstance(item, dict) or set(item) != {
            "case_id",
            "critical_failures",
            "evidence",
            "semantic_gates",
            "status",
        }:
            raise RuntimeError("LAB-36 core-live case shape differs")
        if (
            item["status"] != "passed_live_b"
            or item["semantic_gates"] != "passed"
            or item["critical_failures"] != []
            or not isinstance(item["evidence"], list)
            or not item["evidence"]
        ):
            raise RuntimeError(f"LAB-36 core-live case is not green: {item['case_id']}")
        for binding in item["evidence"]:
            if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
                raise RuntimeError("LAB-36 evidence binding shape differs")
            relative = Path(str(binding["path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("LAB-36 evidence path escapes the repository")
            path = (root / relative).resolve()
            evidence_root = (root / "development/evidence/living-core").resolve()
            if path.parent != evidence_root or _sha256(path) != binding["sha256"]:
                raise RuntimeError(f"LAB-36 evidence hash differs: {relative}")
            evidence_count += 1
    return {
        "benchmark_id": benchmark.get("benchmark_id"),
        "case_count": len(cases),
        "contract": "EvaLab36CoreLiveEvidenceCheck/v1",
        "critical_failures": 0,
        "evidence_bindings": evidence_count,
        "semantic_gates": "passed",
        "status": "passed",
        "track": "core_live",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = check(arguments.root, arguments.index)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}))
        raise SystemExit(1) from error
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
