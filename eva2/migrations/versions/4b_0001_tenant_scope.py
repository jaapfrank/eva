"""Create the tenant and installation authority anchors."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "4b_0001_tenant_scope"
down_revision = "4b_0000_core_namespace"
branch_labels = None
depends_on = None


TENANT_PREDICATE = "tenant_id::text = current_setting('eva.tenant_id', true)"
INSTALLATION_PREDICATE = (
    TENANT_PREDICATE
    + " AND installation_id::text = current_setting('eva.installation_id', true)"
)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenants"),
        sa.CheckConstraint(
            "date_trunc('second', created_at) = created_at",
            name="ck_tenants_created_at_seconds",
        ),
        schema="eva_core",
    )
    op.create_table(
        "installations",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "installation_id", name="pk_installations"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["eva_core.tenants.tenant_id"],
            name="fk_installations_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "date_trunc('second', created_at) = created_at",
            name="ck_installations_created_at_seconds",
        ),
        schema="eva_core",
    )
    op.execute("ALTER TABLE eva_core.tenants OWNER TO eva_migrator")
    op.execute("ALTER TABLE eva_core.installations OWNER TO eva_migrator")
    op.execute("REVOKE ALL ON eva_core.tenants, eva_core.installations FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON eva_core.tenants, eva_core.installations "
        "TO eva_runtime, eva_dbos, eva_backup"
    )
    op.execute("ALTER TABLE eva_core.tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.tenants FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.installations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.installations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenants_scope ON eva_core.tenants "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(
        "CREATE POLICY installations_scope ON eva_core.installations "
        f"USING ({INSTALLATION_PREDICATE}) "
        f"WITH CHECK ({INSTALLATION_PREDICATE})"
    )


def downgrade() -> None:
    op.drop_table("installations", schema="eva_core")
    op.drop_table("tenants", schema="eva_core")
