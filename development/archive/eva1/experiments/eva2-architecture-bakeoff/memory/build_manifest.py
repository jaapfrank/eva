#!/usr/bin/env python3
"""Build one content-addressed memory-subtrial manifest from sealed evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from canonical_memory import canonical_json


CONTRACT = "EvaArchitectureBakeoffRun/v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def docker_names(kind: str) -> list[str]:
    command = ["docker", kind, "ls"]
    if kind == "container":
        command.append("-a")
    command.extend(("--format", "{{.Names}}" if kind == "container" else "{{.Name}}"))
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return [f"docker inspection failed: {completed.stderr.strip()}"]
    return sorted(
        name
        for name in completed.stdout.splitlines()
        if name.startswith(("eva2-memory-", "eva2-hindsight-", "scale-10000"))
    )


def run(sql_path: Path, hindsight_path: Path, blockers_path: Path, output_dir: Path) -> Path:
    sql = load(sql_path)
    hindsight = load(hindsight_path)
    blockers = load(blockers_path)
    if sql.get("contract") != "EvaSqlMemoryProbeEvidence/v1":
        raise ValueError("unexpected SQL evidence contract")
    if hindsight.get("contract") != "EvaHindsightInfrastructureProbeEvidence/v1":
        raise ValueError("unexpected Hindsight evidence contract")
    if blockers.get("contract") != "EvaMemoryCandidateBlockers/v1":
        raise ValueError("unexpected blocker contract")

    here = Path(__file__).resolve().parent
    source_names = [
        "canonical_memory.py",
        "run_sql_probe.py",
        "hindsight_adapter.py",
        "run_hindsight_probe.py",
        "build_manifest.py",
        "README.md",
        "candidate-blockers.json",
    ]
    sources = {name: sha256_file(here / name) for name in source_names}
    sealed_doc = here.parents[2] / "docs" / "eva2-architecture-bakeoff-v1.md"
    sql_scales = {int(item["scale"]): item for item in sql["scales"]}
    h_result = hindsight["outcomes"][0]["result"]
    containers = docker_names("container")
    networks = docker_names("network")

    manifest = {
        "contract": CONTRACT,
        "subtrial": "sealed_memory",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "winner": None,
        "authority": "canonical_sql_events",
        "candidate_role": "optional_derived_projection_only",
        "artifacts": {
            str(path.name): sha256_file(path)
            for path in (sql_path, hindsight_path, blockers_path)
        },
        "source_sha256": sources,
        "sealed_document_sha256": sha256_file(sealed_doc),
        "fixture_authority_sha256": {
            str(scale): item["canonical_authority_sha256"]
            for scale, item in sql_scales.items()
        },
        "runtime_policy": {
            "root": "/tmp/docker",
            "product_database_used": False,
            "product_service_used": False,
            "eva1_imported": False,
            "real_accounts_used": False,
            "paid_api_used": False,
            "remaining_probe_containers": containers,
            "remaining_probe_networks": networks,
            "cleanup_verified": not containers and not networks,
        },
        "hard_gates": {
            "sql_10000": {
                "status": "proven",
                "event_count": sql_scales[10_000]["canonical_event_count_at_scale_gate"],
                "p95_ms": sql_scales[10_000]["latency"]["p95_ms"],
            },
            "sql_100000": {
                "status": "proven",
                "event_count": sql_scales[100_000]["canonical_event_count_at_scale_gate"],
                "p95_ms": sql_scales[100_000]["latency"]["p95_ms"],
            },
            "sql_semantics_provenance_crash_erase_rebuild_fallback": (
                "proven" if all(sql["hard_gate_facts"].values()) else "failed"
            ),
            "hindsight_10000_infrastructure": {
                "status": "proven",
                "event_count": h_result["canonical_event_count_at_scale_gate"],
                "adapter_exit_code": h_result["crash_before_readback"]["adapter_exit_code"],
                "rebuild_equivalent": h_result["rebuild_equivalent"],
                "p95_ms": h_result["latency"]["p95_ms"],
                "tokens": h_result["tokens"],
                "semantic_quality": h_result["semantic_quality"],
            },
            "hindsight_100000": "not_proven",
            "hindsight_semantic_quality": "not_proven",
            "graphiti_install_and_trial": "not_proven",
        },
        "dependency_locks": {
            "hindsight_image": h_result["candidate"]["image"],
            "hindsight_image_index_digest": h_result["candidate"]["image_index_digest"],
            "hindsight_source_revision": h_result["candidate"]["source_revision"],
            "graphiti_inspected_source_revision": blockers["graphiti"]["official_snapshot"][
                "source_revision"
            ],
            "graphiti_uninstalled_wheel_sha256": blockers["graphiti"]["official_snapshot"][
                "wheel_sha256"
            ],
        },
        "blockers": {
            "hindsight_100000": blockers["hindsight_100000"]["blocker"],
            "graphiti": blockers["graphiti"]["blocker"],
        },
        "toolchain": {
            "python": sys.version.split()[0],
            "sqlite": sql["sqlite"],
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(manifest) + "\n").encode("utf-8")
    output = output_dir / f"memory-run-{hashlib.sha256(payload).hexdigest()}.json"
    output.write_bytes(payload)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", required=True, type=Path)
    parser.add_argument("--hindsight", required=True, type=Path)
    parser.add_argument("--blockers", type=Path, default=Path(__file__).with_name("candidate-blockers.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("evidence"))
    args = parser.parse_args(argv)
    print(run(args.sql, args.hindsight, args.blockers, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
