"""Create the minimal Eva namespace and fail-closed database roles."""

from __future__ import annotations

from alembic import op


revision = "4b_0000_core_namespace"
down_revision = None
branch_labels = None
depends_on = None


ROLE_SQL = """
DO $roles$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'eva_migrator', 'eva_runtime', 'eva_dbos', 'eva_backup'
    ]
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOINHERIT NOREPLICATION NOBYPASSRLS',
                role_name
            );
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = role_name
              AND (
                  rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
                  OR rolinherit OR rolreplication OR rolbypassrls
              )
        ) THEN
            RAISE EXCEPTION 'unsafe pre-existing Eva database role: %', role_name;
        END IF;
    END LOOP;
END
$roles$;
"""


def upgrade() -> None:
    op.execute(ROLE_SQL)
    op.execute("CREATE SCHEMA eva_core AUTHORIZATION eva_migrator")
    op.execute("REVOKE ALL ON SCHEMA eva_core FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA eva_core TO eva_runtime, eva_dbos, eva_backup")
    # Alembic materializes its ledger before it invokes the first revision.
    # Transfer that bootstrap object to the durable group owner immediately so
    # a rotated management login does not become migration authority.
    op.execute("ALTER TABLE public.eva_alembic_version OWNER TO eva_migrator")
    op.execute(
        "REVOKE ALL ON TABLE public.eva_alembic_version "
        "FROM PUBLIC, eva_runtime, eva_dbos, eva_backup"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA eva_core")
    # Roles are cluster-global installation prerequisites and are intentionally
    # not removed by a database-local, pre-traffic downgrade.
