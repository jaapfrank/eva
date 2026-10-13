"""Add the append-only Living Core timeline and rebuildable checkpoint cursor."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints,
    operational_events,
)


revision = "4e_0001_operational_timeline"
down_revision = "4d_0007_projection_telemetry"
branch_labels = None
depends_on = None


def _create_table(table: sa.Table) -> None:
    op.execute(sa.schema.CreateTable(table))
    for index in sorted(table.indexes, key=lambda item: item.name or ""):
        op.execute(sa.schema.CreateIndex(index))


def _scope_table(table: sa.Table, *, mutable: bool) -> None:
    full_name = f"{table.schema}.{table.name}"
    predicate = (
        "tenant_id::text = current_setting('eva.tenant_id', true) "
        "AND installation_id::text = current_setting('eva.installation_id', true)"
    )
    op.execute(f"ALTER TABLE {full_name} OWNER TO eva_migrator")
    op.execute(f"REVOKE ALL ON TABLE {full_name} FROM PUBLIC, eva_runtime, eva_dbos")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {full_name} TO eva_runtime, eva_dbos")
    if mutable:
        columns = (
            "checkpoint_id, operation_id, phase, status, retry_count, "
            "last_error_fingerprint, deadline, revision, updated_at"
        )
        op.execute(
            f"GRANT UPDATE ({columns}) ON TABLE {full_name} TO eva_runtime, eva_dbos"
        )
    op.execute(f"ALTER TABLE {full_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {full_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table.name}_scope ON {full_name} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    _create_table(operational_events)
    _create_table(operational_checkpoints)
    op.execute(
        """
        CREATE FUNCTION eva_core.reject_operational_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
            RAISE EXCEPTION 'eva_operational_events_are_append_only'
                USING ERRCODE = '55000';
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER operational_events_append_only
        BEFORE UPDATE OR DELETE ON eva_core.operational_events
        FOR EACH ROW EXECUTE FUNCTION eva_core.reject_operational_event_mutation()
        """
    )
    _scope_table(operational_events, mutable=False)
    _scope_table(operational_checkpoints, mutable=True)


def downgrade() -> None:
    op.drop_table(operational_checkpoints.name, schema=operational_checkpoints.schema)
    op.drop_table(operational_events.name, schema=operational_events.schema)
    op.execute("DROP FUNCTION eva_core.reject_operational_event_mutation()")
