from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import command

if TYPE_CHECKING:
    from conftest import PostgresGate


def test_fresh_create_upgrade_and_pretraffic_downgrade_are_proven(
    postgres_gate: PostgresGate,
) -> None:
    assert postgres_gate.preserved_scope_anchor is True
    assert postgres_gate.migrated_as_eva_migrator is True

    with postgres_gate.engine.connect() as connection:
        tables = set(
            connection.scalars(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'eva_core' ORDER BY table_name"
                )
            )
        )
        revision = connection.scalar(
            sa.text("SELECT version_num FROM public.eva_alembic_version")
        )

    assert tables == {"episode_events", "episodes", "installations", "tenants"}
    assert revision == "4b_0002_episode_atomicity"


def test_catalog_has_only_the_sealed_4b_objects_and_owners(
    postgres_gate: PostgresGate,
) -> None:
    with postgres_gate.engine.connect() as connection:
        eva_schemas = set(
            connection.scalars(
                sa.text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE left(nspname, 4) = 'eva_' ORDER BY nspname"
                )
            )
        )
        relations = (
            connection.execute(
                sa.text(
                    "SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner) AS owner "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE (n.nspname = 'eva_core' OR "
                    "(n.nspname = 'public' AND c.relname = 'eva_alembic_version')) "
                    "AND c.relkind IN ('r', 'p') "
                    "ORDER BY n.nspname, c.relname"
                )
            )
            .mappings()
            .all()
        )

    assert eva_schemas == {"eva_core"}
    assert {(row["nspname"], row["relname"], row["owner"]) for row in relations} == {
        ("eva_core", "episode_events", "eva_migrator"),
        ("eva_core", "episodes", "eva_migrator"),
        ("eva_core", "installations", "eva_migrator"),
        ("eva_core", "tenants", "eva_migrator"),
        ("public", "eva_alembic_version", "eva_migrator"),
    }


def test_alembic_ledger_has_no_nonowner_acl(postgres_gate: PostgresGate) -> None:
    with postgres_gate.engine.connect() as connection:
        owner, has_nonowner_acl = connection.execute(
            sa.text(
                "SELECT pg_get_userbyid(c.relowner), EXISTS ("
                "SELECT 1 FROM aclexplode(COALESCE(c.relacl, "
                "acldefault('r', c.relowner))) AS acl "
                "WHERE acl.grantee <> c.relowner"
                ") FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' "
                "AND c.relname = 'eva_alembic_version'"
            )
        ).one()

    assert owner == "eva_migrator"
    assert has_nonowner_acl is False


def test_alembic_metadata_is_at_head_without_drift(
    postgres_gate: PostgresGate,
) -> None:
    command.check(postgres_gate.alembic_config)


def test_runtime_roles_have_no_ddl_or_rls_bypass(postgres_gate: PostgresGate) -> None:
    with postgres_gate.engine.connect() as connection:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, "
                    "rolcanlogin, rolbypassrls, "
                    "has_schema_privilege(rolname, 'eva_core', 'CREATE') AS can_create "
                    "FROM pg_roles WHERE rolname IN ('eva_runtime', 'eva_dbos') "
                    "ORDER BY rolname"
                )
            )
            .mappings()
            .all()
        )

    assert [row["rolname"] for row in rows] == ["eva_dbos", "eva_runtime"]
    assert all(
        not row[flag]
        for row in rows
        for flag in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolcanlogin",
            "rolbypassrls",
            "can_create",
        )
    )


def test_event_rows_are_immutable_to_runtime_roles(postgres_gate: PostgresGate) -> None:
    with postgres_gate.engine.connect() as connection:
        privileges = (
            connection.execute(
                sa.text(
                    "SELECT role_name, "
                    "has_table_privilege(role_name, 'eva_core.episode_events', 'INSERT') AS can_insert, "
                    "has_table_privilege(role_name, 'eva_core.episode_events', 'UPDATE') AS can_update, "
                    "has_table_privilege(role_name, 'eva_core.episode_events', 'DELETE') AS can_delete "
                    "FROM unnest(ARRAY['eva_runtime', 'eva_dbos']) AS role_name "
                    "ORDER BY role_name"
                )
            )
            .mappings()
            .all()
        )

    assert all(row["can_insert"] for row in privileges)
    assert all(not row["can_update"] and not row["can_delete"] for row in privileges)
