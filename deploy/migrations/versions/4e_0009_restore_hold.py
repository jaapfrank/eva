"""Keep restored databases inert until separately verified operator activation."""
from alembic import op

revision = "4e_0009_restore_hold"
down_revision = "4e_0008_portable_current"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("""
        CREATE TABLE eva_core.restore_activation (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            restore_id uuid NOT NULL,
            archive_sha256 text NOT NULL CHECK (archive_sha256 ~ '^[a-f0-9]{64}$'),
            tenant_id uuid NOT NULL,
            installation_id uuid NOT NULL,
            runtime_tenant_id text NOT NULL,
            runtime_installation_id text NOT NULL,
            target_database_oid oid NOT NULL,
            held boolean NOT NULL DEFAULT true,
            release_authority_sha256 text,
            CHECK ((held AND release_authority_sha256 IS NULL) OR
                   (NOT held AND release_authority_sha256 IS NOT NULL AND release_authority_sha256 ~ '^[a-f0-9]{64}$'))
        )
    """)
    op.execute("REVOKE ALL ON eva_core.restore_activation FROM PUBLIC")
    op.execute("GRANT SELECT ON eva_core.restore_activation TO eva_runtime, eva_dbos")


def downgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("DROP TABLE eva_core.restore_activation")
