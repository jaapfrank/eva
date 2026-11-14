"""Read-only PostgreSQL aggregate telemetry probe for Fase 5A/5B household observation.

Strictly reads aggregate event and work-run counts scoped to `(tenant_id, installation_id)`
with a 15-second TTL cache. Never reads or returns conversation text, media bytes, or secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any
from uuid import UUID

import sqlalchemy as sa


class PostgresHouseholdObservationProbe:
    """Bounded read-only operational ledger counter for 24h and 7d household observation."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        tenant_id: str | UUID,
        installation_id: str | UUID,
        ttl_seconds: float = 15.0,
    ) -> None:
        self._engine = engine
        self._tenant_id = str(UUID(str(tenant_id)))
        self._installation_id = str(UUID(str(installation_id)))
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._lock = Lock()
        self._expires_at = 0.0
        self._cached: dict[str, Any] | None = None

    def __call__(self) -> dict[str, Any]:
        now_mono = monotonic()
        with self._lock:
            if self._cached is not None and now_mono < self._expires_at:
                return dict(self._cached)
            measured = self._query_counts()
            self._cached = measured
            self._expires_at = monotonic() + self._ttl_seconds
            return dict(measured)

    def _query_counts(self) -> dict[str, Any]:
        params = {
            "tenant_id": self._tenant_id,
            "installation_id": self._installation_id,
        }
        sql = sa.text(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE event_type = 'INGRESS_COMMITTED'
                      AND occurred_at >= (NOW() - INTERVAL '24 hours')
                ) AS ingress_24h,
                COUNT(*) FILTER (
                    WHERE event_type = 'INGRESS_COMMITTED'
                      AND occurred_at >= (NOW() - INTERVAL '7 days')
                ) AS ingress_7d,
                COUNT(DISTINCT DATE_TRUNC('day', occurred_at AT TIME ZONE 'UTC')) FILTER (
                    WHERE event_type = 'INGRESS_COMMITTED'
                      AND occurred_at >= (NOW() - INTERVAL '7 days')
                ) AS active_ingress_days_7d,
                COUNT(*) FILTER (
                    WHERE event_type = 'STATE_VERIFIED'
                      AND occurred_at >= (NOW() - INTERVAL '7 days')
                ) AS verified_tool_effects_7d
            FROM eva_core.operational_events
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND installation_id = CAST(:installation_id AS uuid)
              AND occurred_at >= (NOW() - INTERVAL '7 days')
            """
        )
        scope_sql = sa.text(
            "SELECT set_config('eva.tenant_id', :tenant_id, true), "
            "set_config('eva.installation_id', :installation_id, true)"
        )
        work_sql = sa.text(
            """
            SELECT
                COUNT(*) FILTER (WHERE state IN ('succeeded', 'verified')) AS completed_7d,
                COUNT(*) FILTER (WHERE state IN ('failed', 'ambiguous', 'needs_help')) AS failed_or_blocked_7d
            FROM eva_core.delegated_work_runs
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND installation_id = CAST(:installation_id AS uuid)
              AND created_at >= (NOW() - INTERVAL '7 days')
            """
        )
        with self._engine.begin() as conn:
            conn.execute(scope_sql, params)
            ev_row = conn.execute(sql, params).mappings().one()
            wk_row = conn.execute(work_sql, params).mappings().one()
        return {
            "state": "ready",
            "measured_at": datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ingress_turns_24h": int(ev_row["ingress_24h"] or 0),
            "ingress_turns_7d": int(ev_row["ingress_7d"] or 0),
            "active_ingress_days_7d": int(ev_row["active_ingress_days_7d"] or 0),
            "verified_tool_effects_7d": int(ev_row["verified_tool_effects_7d"] or 0),
            "delegated_work_completed_7d": int(wk_row["completed_7d"] or 0),
            "delegated_work_failed_or_blocked_7d": int(wk_row["failed_or_blocked_7d"] or 0),
        }


__all__ = ["PostgresHouseholdObservationProbe"]
