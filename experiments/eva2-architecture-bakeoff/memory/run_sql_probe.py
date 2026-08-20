#!/usr/bin/env python3
"""Run the sealed SQL memory probe in isolated disposable child processes."""

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


CONTRACT = "EvaSqlMemoryProbeEvidence/v1"
DEFAULT_SCALES = (10_000, 100_000)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(output_dir: Path, scales: Sequence[int]) -> Path:
    allowed = {10_000, 100_000}
    if set(scales) - allowed or not scales:
        raise ValueError("only sealed scales 10000 and 100000 are allowed")
    docker_root = Path("/tmp/docker")
    docker_root.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="eva2-memory-sql-", dir=docker_root))
    scale_results: list[dict[str, Any]] = []
    try:
        for scale in scales:
            scale_dir = runtime / f"scale-{scale}"
            scale_dir.mkdir()
            database = scale_dir / "canonical.sqlite3"
            result_path = scale_dir / "result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("canonical_memory.py")),
                    "run-scale",
                    "--db",
                    str(database),
                    "--event-count",
                    str(scale),
                    "--result",
                    str(result_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"scale {scale} failed rc={completed.returncode}\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                )
            scale_results.append(json.loads(result_path.read_text(encoding="utf-8")))

        source_files = [Path(__file__).resolve(), Path(__file__).with_name("canonical_memory.py").resolve()]
        evidence = {
            "contract": CONTRACT,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "winner": None,
            "classification": "sealed_sql_baseline",
            "python": sys.version.split()[0],
            "sqlite": __import__("sqlite3").sqlite_version,
            "source_sha256": {path.name: _sha256_file(path) for path in source_files},
            "runtime_policy": {
                "root": "/tmp/docker",
                "unique_runtime_name": runtime.name,
                "product_database_used": False,
                "eva1_imported": False,
                "runtime_removed_after_run": True,
            },
            "scales": scale_results,
            "hard_gate_facts": {
                "source_correction_removal_rebuild": all(
                    item["projection_rebuild_equivalent"]
                    and item["erase"]["sentinel_absent_from_compacted_bytes"]
                    for item in scale_results
                ),
                "zero_tenant_leaks": all(
                    item["semantic_gates"]["tenant_leaks"] == 0 for item in scale_results
                ),
                "zero_untrusted_promotions": all(
                    item["semantic_gates"]["untrusted_promotions"] == 0 for item in scale_results
                ),
                "exact_source_provenance": all(
                    item["semantic_gates"]["provenance_failures"] == 0 for item in scale_results
                ),
                "accepted_write_survives_crash": all(
                    item["crash_before_readback"]["child_exit_code"] == 86
                    and item["crash_before_readback"]["readback_event_ids"]
                    == [item["crash_before_readback"]["accepted_event_id"]]
                    for item in scale_results
                ),
                "sql_fts_fallback": all(
                    item["fallback"]["backend"] == "sql_fts" for item in scale_results
                ),
            },
        }
    finally:
        shutil.rmtree(runtime)

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(evidence).encode("utf-8") + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    output = output_dir / f"sql-memory-probe-{digest}.json"
    output.write_bytes(payload)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).with_name("evidence"),
    )
    parser.add_argument("--scales", type=int, nargs="+", default=list(DEFAULT_SCALES))
    args = parser.parse_args(argv)
    output = run(args.output_dir, args.scales)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
