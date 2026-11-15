"""Strictly read-only CLI classification of durable memory timelines and native payloads."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
import sys

import sqlalchemy as sa

from eva.adapters.postgres.memory_chronology_cleanup import classify_memory_remnants
from eva.adapters.postgres.operational_timeline import TimelineScope, external_timeline_scope
from eva.adapters.postgres.work_source_retention import WorkSourceRetention


class _ScopedRetentionStore:
    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self._engine = engine
        self.scope = scope

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql("SET LOCAL ROLE eva_runtime")
        connection.execute(
            sa.text("SELECT set_config('eva.tenant_id', :value, true)"),
            {"value": str(self.scope.tenant_id)},
        )
        connection.execute(
            sa.text("SELECT set_config('eva.installation_id', :value, true)"),
            {"value": str(self.scope.installation_id)},
        )


def _parse_utc(raw: str) -> datetime:
    parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("EVA_DATABASE_URL", ""))
    parser.add_argument("--dsn-file", default=os.environ.get("EVA_DATABASE_DSN_FILE", ""))
    parser.add_argument("--hindsight-dsn", default=os.environ.get("EVA_HINDSIGHT_DATABASE_URL", ""))
    parser.add_argument("--hindsight-dsn-file", default=os.environ.get("EVA_HINDSIGHT_DATABASE_DSN_FILE", ""))
    parser.add_argument("--hindsight-schema", default="public")
    parser.add_argument("--tenant-id", default=os.environ.get("EVA_RUNTIME_TENANT_ID", ""))
    parser.add_argument("--installation-id", default=os.environ.get("EVA_RUNTIME_INSTALLATION_ID", ""))
    parser.add_argument("--authority-tenant-uuid", default=os.environ.get("EVA_DASHBOARD_TENANT_ID", ""))
    parser.add_argument("--authority-installation-uuid", default=os.environ.get("EVA_DASHBOARD_INSTALLATION_ID", ""))
    parser.add_argument("--cutoff-utc", default=None)
    parser.add_argument("--now-utc", default=None)
    args = parser.parse_args(argv)

    try:
        dsn = args.dsn.strip()
        if not dsn and args.dsn_file:
            from pathlib import Path
            dsn = Path(args.dsn_file).read_text(encoding="utf-8").strip()
        hindsight_dsn = args.hindsight_dsn.strip()
        if not hindsight_dsn and args.hindsight_dsn_file:
            from pathlib import Path
            hindsight_dsn = Path(args.hindsight_dsn_file).read_text(encoding="utf-8").strip()
        now = _parse_utc(args.now_utc) if args.now_utc else datetime.now(tz=UTC).replace(microsecond=0)
        cutoff = _parse_utc(args.cutoff_utc) if args.cutoff_utc else None
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Invalid classify_memory_remnants configuration: {type(exc).__name__}") from None

    if not dsn or not args.tenant_id or not args.installation_id:
        raise SystemExit("DSN, tenant-id, and installation-id are required")

    engine = sa.create_engine(dsn, pool_pre_ping=False)
    hindsight_engine = sa.create_engine(hindsight_dsn, pool_pre_ping=False) if hindsight_dsn else None
    try:
        if args.authority_tenant_uuid and args.authority_installation_uuid:
            from uuid import UUID
            retention_scope = TimelineScope(
                tenant_id=UUID(args.authority_tenant_uuid),
                installation_id=UUID(args.authority_installation_uuid),
            )
        else:
            retention_scope = external_timeline_scope(args.tenant_id, args.installation_id, "memory-audit")
        retention = WorkSourceRetention(_ScopedRetentionStore(engine, retention_scope))
        report = classify_memory_remnants(
            engine,
            tenant_id=args.tenant_id,
            installation_id=args.installation_id,
            now=now,
            cutoff_utc=cutoff,
            work_source_retention=retention,
            hindsight_engine=hindsight_engine,
            hindsight_schema=args.hindsight_schema,
        )
        json.dump(report.to_payload(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    finally:
        if hindsight_engine is not None:
            hindsight_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
