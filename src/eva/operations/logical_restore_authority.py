"""Dependency-free authority bootstrap for an empty logical-restore target."""

from __future__ import annotations


_LOGICAL_RESTORE_ROLES = (
    "eva_backup",
    "eva_dbos",
    "eva_migrator",
    "eva_runtime",
)
_LOGICAL_RESTORE_ROLE_SQL = """
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
_LOGICAL_RESTORE_EMPTY_GUARD_SQL = """
DO $restore_target$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace
        WHERE nspname <> 'public'
          AND nspname <> 'information_schema'
          AND nspname NOT LIKE 'pg_%'
    ) THEN
        RAISE EXCEPTION 'logical restore roles require a completely empty target database';
    END IF;
END
$restore_target$;
"""


def logical_restore_preparation_sql() -> str:
    """Return the exact atomic empty-target guard and fixed-role bootstrap."""

    return _LOGICAL_RESTORE_EMPTY_GUARD_SQL + _LOGICAL_RESTORE_ROLE_SQL


__all__ = ["logical_restore_preparation_sql"]
