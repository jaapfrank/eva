#!/usr/bin/env python3
"""Run isolated Hindsight scale probes and emit content-addressed evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from canonical_memory import canonical_json


CONTRACT = "EvaHindsightInfrastructureProbeEvidence/v1"


def run(output_dir: Path, scales: Sequence[int]) -> Path:
    if not scales or set(scales) - {10_000, 100_000}:
        raise ValueError("only sealed scales 10000 and 100000 are allowed")
    docker_root = Path("/tmp/docker")
    docker_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="eva2-memory-hindsight-", dir=docker_root))
    outcomes: list[dict[str, Any]] = []
    try:
        for scale in scales:
            runtime = root / f"scale-{scale}"
            runtime.mkdir()
            result = runtime / "result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("hindsight_adapter.py")),
                    "run-scale",
                    "--runtime",
                    str(runtime),
                    "--event-count",
                    str(scale),
                    "--result",
                    str(result),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3600,
            )
            if completed.returncode != 0:
                outcomes.append(
                    {
                        "scale": scale,
                        "status": "blocked",
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-10000:],
                        "stderr": completed.stderr[-20000:],
                        "technical_blocker": "real local Hindsight probe process failed",
                    }
                )
                break
            outcomes.append({"scale": scale, "status": "completed", "result": json.loads(result.read_text())})

        completed_scales = {
            int(item["scale"])
            for item in outcomes
            if item["status"] == "completed"
        }
        evidence = {
            "contract": CONTRACT,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "winner": None,
            "classification": "infrastructure_only_no_real_llm",
            "outcomes": outcomes,
            "hard_gate_facts": {
                "semantic_memory_quality": "not_proven",
                "reason": "official account-free provider=none forces chunks mode and performs no temporal/fact extraction",
                "all_requested_scales_completed": len(outcomes) == len(scales)
                and all(item["status"] == "completed" for item in outcomes),
                "sealed_10000_completed": 10_000 in completed_scales,
                "sealed_100000_completed": 100_000 in completed_scales,
                "sealed_scale_gate": "proven" if completed_scales == {10_000, 100_000} else "not_proven",
                "missing_sealed_scales": sorted({10_000, 100_000} - completed_scales),
            },
        }
    finally:
        shutil.rmtree(root)

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(evidence) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    output = output_dir / f"hindsight-memory-probe-{digest}.json"
    output.write_bytes(payload)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("evidence"))
    parser.add_argument("--scales", type=int, nargs="+", default=[10_000])
    args = parser.parse_args(argv)
    print(run(args.output_dir, args.scales))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
