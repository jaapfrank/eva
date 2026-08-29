#!/usr/bin/env python3
"""Prove daily Hindsight reflection admission on a migrated disposable database."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.adapters.postgres.runtime_resource_usage import (
    PostgresDailyReflectionBudget,
)
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.runtime_budget import RuntimeResourceBudget


ROOT = Path(__file__).resolve().parents[1]
TENANT = "reflection-budget-proof-tenant"
INSTALLATION = "reflection-budget-proof-installation"
NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_COMMIT = re.compile(r"[0-9a-f]{40}")
SOURCE_PATHS = (
    "src/eva/adapters/postgres/runtime_resource_usage.py",
    "src/eva/adapters/projection/hindsight.py",
    "src/eva/application/memory_background.py",
    "src/eva/bootstrap/runtime.py",
    "tests/application/test_memory_background.py",
    "tests/postgres/test_runtime_resource_usage.py",
    "tests/projection/test_hindsight.py",
    "tools/prove_daily_reflection_budget.py",
)


def _budget() -> RuntimeResourceBudget:
    payload = {
        "background_reflections_per_installation_day": 6,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v1",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_records_per_person": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _governor(engine: sa.Engine) -> PostgresDailyReflectionBudget:
    return PostgresDailyReflectionBudget(
        engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        resource_budget=_budget(),
    )


def prove(
    database_dsn: str, *, source_commit: str, focused_tests_passed: int
) -> dict[str, object]:
    if _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source commit must be an exact lowercase Git commit")
    if focused_tests_passed < 1:
        raise ValueError("focused test count is invalid")
    engine = sa.create_engine(database_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            version = str(connection.scalar(sa.text("SHOW server_version")))
        governor = _governor(engine)
        with ThreadPoolExecutor(max_workers=8) as pool:
            first_person = tuple(
                pool.map(
                    lambda _index: governor.admit_background_reflection(
                        tenant_id=TENANT,
                        installation_id=INSTALLATION,
                        principal_id="person:proof-a",
                        now=NOW,
                    ),
                    range(8),
                )
            )
        restarted = _governor(engine)
        second_person = tuple(
            restarted.admit_background_reflection(
                tenant_id=TENANT,
                installation_id=INSTALLATION,
                principal_id="person:proof-b",
                now=NOW + timedelta(seconds=index + 1),
            )
            for index in range(3)
        )
        next_day = restarted.admit_background_reflection(
            tenant_id=TENANT,
            installation_id=INSTALLATION,
            principal_id="person:proof-a",
            now=NOW + timedelta(days=1),
        )
        timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{TENANT}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{TENANT}:{INSTALLATION}"
                ),
            ),
        )
        day_events = timeline.events("runtime-reflections:2026-08-29")
        reserved = sum(
            event.event_type == "RUNTIME_REFLECTION_RESERVED"
            for event in day_events
        )
        blocked = sum(
            event.event_type == "RUNTIME_REFLECTION_BLOCKED"
            for event in day_events
        )
        if (
            sum(item.admitted for item in first_person) != 4
            or [item.admitted for item in second_person] != [True, True, False]
            or reserved != 6
            or blocked != 5
            or not next_day.admitted
            or next_day.installation_used != 1
        ):
            raise RuntimeError("daily reflection budget proof differs")
        return {
            "contract": "EvaDailyReflectionBudgetProof/v1",
            "day_boundary": {
                "next_day_admitted": next_day.admitted,
                "next_day_installation_used": next_day.installation_used,
            },
            "hindsight": {
                "admission_before_provider": True,
                "unmanaged_refresh_cron": False,
            },
            "limits": {
                "installation_per_day": 6,
                "person_per_day": 4,
            },
            "non_claims": [
                "geen productiepromotie of live Hindsight-wijziging",
                "geen bewijs van geheugenbytes, bewaartermijn of geldafrekening",
            ],
            "postgres": {
                "blocked_events": blocked,
                "reserved_events": reserved,
                "restart_reloaded_same_day": True,
                "server_version": version,
            },
            "source_commit": source_commit,
            "source_files": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(
                        (ROOT / relative).read_bytes()
                    ).hexdigest(),
                }
                for relative in SOURCE_PATHS
            ],
            "tests": {"focused_passed": focused_tests_passed},
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-dsn-file", type=Path, required=True)
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    arguments = parser.parse_args()
    dsn = read_runtime_secret_file(
        str(arguments.database_dsn_file.resolve()),
        setting_name="reflection budget proof database DSN",
    )
    print(
        json.dumps(
            prove(
                dsn,
                source_commit=arguments.source_commit,
                focused_tests_passed=arguments.focused_tests_passed,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
