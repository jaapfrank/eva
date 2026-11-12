"""Idempotent composite B-tree index management for the three live Eva Core tables."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine

CORE_COMPOSITE_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_delegated_work_runs__worker_claim",
        "CREATE INDEX IF NOT EXISTS ix_delegated_work_runs__worker_claim "
        "ON eva_core.delegated_work_runs (tenant_id, installation_id, state, lease_expires_at, created_at)",
    ),
    (
        "ix_operational_events__type_seq_time",
        "CREATE INDEX IF NOT EXISTS ix_operational_events__type_seq_time "
        "ON eva_core.operational_events (tenant_id, installation_id, event_type, sequence, occurred_at)",
    ),
    (
        "ix_operational_events__thread_type_seq",
        "CREATE INDEX IF NOT EXISTS ix_operational_events__thread_type_seq "
        "ON eva_core.operational_events (tenant_id, installation_id, thread_id, event_type, sequence)",
    ),
    (
        "ix_operational_events__occurred_at",
        "CREATE INDEX IF NOT EXISTS ix_operational_events__occurred_at "
        "ON eva_core.operational_events (tenant_id, installation_id, occurred_at DESC, sequence DESC)",
    ),
)


def ensure_core_composite_indexes(engine: Engine, *, analyze: bool = False) -> tuple[str, ...]:
    """Ensure all composite indexes required to prevent sequential scans exist."""
    ensured: list[str] = []
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for index_name, ddl in CORE_COMPOSITE_INDEXES:
            conn.execute(sa.text(ddl))
            ensured.append(index_name)
        if analyze:
            for table in (
                "eva_core.delegated_work_runs",
                "eva_core.operational_checkpoints",
                "eva_core.operational_events",
            ):
                conn.execute(sa.text(f"ANALYZE {table}"))
    return tuple(ensured)


__all__ = [
    "CORE_COMPOSITE_INDEXES",
    "ensure_core_composite_indexes",
]
