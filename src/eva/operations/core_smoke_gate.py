"""Fase 4 Unified Core Smoke Gate & Regression Verifier for Eva2.

Executes a single deterministic health, integrity, and regression check across:
1. Frozen design source bindings (verify_source_bindings)
2. Canonical workboard & historical completion evidence (check_current_workboard)
3. Dependency update posture (check_dependency_update_posture)
4. Core <-> Worker delegated runtime profile compatibility (check_delegated_runtime_profiles)
5. Live WSL Docker container & systemd service health
6. PostgreSQL schema head, composite B-tree indexes & checkpoint/event ledger integrity
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from eva.adapters.postgres.core_indexes import CORE_COMPOSITE_INDEXES

REPO_ROOT = Path(__file__).resolve().parents[3]


REQUIRED_HEALTHY_CONTAINERS: tuple[str, ...] = (
    "eva2-managed-b-slot3-core",
    "eva2-managed-b-slot3-delegated-work-worker-1",
    "eva2-managed-b-slot3-hindsight-1",
    "eva2-managed-b-slot3-postgres-1",
    "eva2-managed-b-slot3-hindsight-postgres-1",
    "eva2-managed-b-slot3-hindsight-access-lease-1",
)

REQUIRED_RUNNING_CONTAINERS: tuple[str, ...] = (
    *REQUIRED_HEALTHY_CONTAINERS,
    "eva2-managed-b-slot3-model-access-lease-1",
)


def _run_json_tool(script_relpath: str, *extra_args: str, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(repo_root / script_relpath), *extra_args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{script_relpath} failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return json.loads(proc.stdout.strip())


def _run_text_tool(script_relpath: str, *extra_args: str, repo_root: Path = REPO_ROOT) -> str:
    proc = subprocess.run(
        [sys.executable, str(repo_root / script_relpath), *extra_args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{script_relpath} failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def check_live_containers() -> dict[str, Any]:
    proc = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=eva2-managed-b-slot3",
            "--format",
            "{{.Names}}\t{{.Status}}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    statuses: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" in line:
            name, status = line.split("\t", 1)
            statuses[name.strip()] = status.strip()
    missing = [name for name in REQUIRED_RUNNING_CONTAINERS if name not in statuses]
    unhealthy = [
        name
        for name in REQUIRED_HEALTHY_CONTAINERS
        if "(healthy)" not in statuses.get(name, "")
    ]
    if missing or unhealthy:
        raise RuntimeError(
            f"container health gate failed: missing={missing}, unhealthy={unhealthy}"
        )
    return {"running": statuses, "allHealthy": True}


def check_database_and_indexes(core_container: str = "eva2-managed-b-slot3-core") -> dict[str, Any]:
    expected_names = [idx[0] for idx in CORE_COMPOSITE_INDEXES]
    probe = r"""
import json, sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn

s = RuntimeSettings.from_environment()
e = sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn))
with e.connect() as c:
    head = c.scalar(sa.text("SELECT version_num FROM public.eva_alembic_version"))
    idx_rows = c.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'eva_core'"
    )).scalars().all()
    checkpoints = c.scalar(sa.text("SELECT count(*) FROM eva_core.operational_checkpoints"))
    events = c.scalar(sa.text("SELECT count(*) FROM eva_core.operational_events"))
    work_runs = c.scalar(sa.text("SELECT count(*) FROM eva_core.delegated_work_runs"))
    broken_revs = c.scalar(sa.text('''
        SELECT count(*) FROM eva_core.operational_checkpoints cp
        WHERE NOT EXISTS (
            SELECT 1 FROM eva_core.operational_events ev
            WHERE ev.tenant_id = cp.tenant_id
              AND ev.installation_id = cp.installation_id
              AND ev.thread_id = cp.thread_id
              AND ev.sequence = cp.revision
        )
    '''))
    broken_origins = c.scalar(sa.text('''
        SELECT count(*) FROM eva_core.operational_checkpoints cp
        WHERE NOT EXISTS (
            SELECT 1 FROM eva_core.operational_events ev
            WHERE ev.tenant_id = cp.tenant_id
              AND ev.installation_id = cp.installation_id
              AND ev.thread_id = cp.thread_id
              AND ev.sequence = 1
        )
    '''))
print(json.dumps({
    "alembicHead": head,
    "indexes": idx_rows,
    "checkpoints": int(checkpoints),
    "events": int(events),
    "workRuns": int(work_runs),
    "brokenCheckpointRevisions": int(broken_revs),
    "brokenThreadOrigins": int(broken_origins),
}))
"""
    proc = subprocess.run(
        ["docker", "exec", core_container, "python", "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(proc.stdout.strip())
    present = set(data.pop("indexes", []))
    missing_indexes = [name for name in expected_names if name not in present]
    if missing_indexes:
        raise RuntimeError(f"missing core composite indexes: {missing_indexes}")
    if data["brokenCheckpointRevisions"] != 0 or data["brokenThreadOrigins"] != 0:
        raise RuntimeError(f"timeline ledger integrity violation: {data}")
    data["coreCompositeIndexes"] = expected_names
    return data


def run_smoke_gate(*, check_live: bool = True, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    bindings = _run_json_tool("src/tools/verify_source_bindings.py", repo_root=repo_root)
    workboard = _run_json_tool("src/tools/check_current_workboard.py", repo_root=repo_root)
    posture = _run_text_tool("src/tools/check_dependency_update_posture.py", repo_root=repo_root)

    result: dict[str, Any] = {
        "contract": "EvaCoreSmokeGate/v1",
        "status": "passed",
        "sourceBindings": {
            "status": bindings["status"],
            "verifiedCount": bindings["actualCount"],
            "fingerprint": bindings["bindingsFingerprint"],
        },
        "workboard": workboard,
        "dependencyUpdatePosture": posture,
    }
    if check_live:
        profiles = _run_json_tool("src/tools/check_delegated_runtime_profiles.py", repo_root=repo_root)
        if not profiles.get("compatible"):
            raise RuntimeError(f"delegated runtime profiles incompatible: {profiles}")
        result["runtimeProfiles"] = profiles
        result["containers"] = check_live_containers()
        result["database"] = check_database_and_indexes()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="Skip live Docker container and PostgreSQL checks.",
    )
    args = parser.parse_args(argv)
    report = run_smoke_gate(check_live=not args.offline_only)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
