"""Permit scope-bound backup of current authoritative task records."""
from alembic import op

revision = "4e_0008_portable_current"
down_revision = "4e_0007_delegated_private"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("GRANT SELECT ON eva_core.operational_events, "
               "eva_core.delegated_work_runs TO eva_backup")


def downgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("REVOKE SELECT ON eva_core.operational_events, "
               "eva_core.delegated_work_runs FROM eva_backup")
