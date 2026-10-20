"""Index existing scoped event-type/time reads without changing their authority."""
from alembic import op
import sqlalchemy as sa

revision = "4e_0011_timeline_type_time"
down_revision = "4e_0010_memory_compaction"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    # Fresh historical creation uses the current metadata index list. Verify
    # that an existing name is exactly the usable index we would create.
    op.execute("CREATE INDEX IF NOT EXISTS ix_operational_events__type_time "
               "ON eva_core.operational_events (tenant_id, installation_id, event_type, occurred_at)")
    valid = op.get_bind().scalar(sa.text("""
        SELECT i.indisvalid AND i.indisready AND NOT i.indisunique
          AND i.indpred IS NULL AND i.indexprs IS NULL
          AND i.indnkeyatts = 4 AND i.indnatts = 4 AND am.amname = 'btree'
          AND ARRAY(SELECT pg_catalog.pg_get_indexdef(i.indexrelid, n, false)
              FROM generate_series(1, i.indnkeyatts) AS n ORDER BY n)
              = ARRAY['tenant_id','installation_id','event_type','occurred_at']
        FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = idx.relnamespace
        JOIN pg_catalog.pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_catalog.pg_am am ON am.oid = idx.relam
        WHERE ns.nspname = 'eva_core' AND idx.relname = 'ix_operational_events__type_time'
          AND tbl.relname = 'operational_events'
    """))
    if valid is not True:
        raise RuntimeError("timeline type/time index differs from its declared shape")


def downgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_index("ix_operational_events__type_time", schema="eva_core")
