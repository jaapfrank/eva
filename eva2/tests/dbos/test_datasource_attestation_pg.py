from __future__ import annotations

import pytest
import sqlalchemy as sa
import dbos._dbos as dbos_runtime_module
from dbos._utils import GlobalParams

from conftest import Postgres4CGate
from eva.adapters.dbos.datasource import (
    DBOSDatasourceAttestationError,
    open_runtime_datasource,
)
from tests.support.episode_transition_4c import (
    CANONICAL_WORKFLOW_NAME,
    configure_episode_transition_runtime,
)


@pytest.mark.parametrize(
    ("mutate", "restore"),
    [
        (
            ("ALTER TABLE dbos.datasource_outputs SET UNLOGGED",),
            ("ALTER TABLE dbos.datasource_outputs SET LOGGED",),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs ENABLE ROW LEVEL SECURITY",
                "ALTER TABLE dbos.datasource_outputs FORCE ROW LEVEL SECURITY",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs NO FORCE ROW LEVEL SECURITY",
                "ALTER TABLE dbos.datasource_outputs DISABLE ROW LEVEL SECURITY",
            ),
        ),
        (
            (
                "CREATE FUNCTION dbos.eva_4c_unexpected_trigger() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                "CREATE TRIGGER eva_4c_unexpected BEFORE INSERT ON "
                "dbos.datasource_outputs FOR EACH ROW EXECUTE FUNCTION "
                "dbos.eva_4c_unexpected_trigger()",
            ),
            (
                "DROP TRIGGER eva_4c_unexpected ON dbos.datasource_outputs",
                "DROP FUNCTION dbos.eva_4c_unexpected_trigger()",
            ),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs DROP CONSTRAINT "
                "datasource_outputs_pkey",
                "ALTER TABLE dbos.datasource_outputs ADD CONSTRAINT "
                "datasource_outputs_pkey PRIMARY KEY (workflow_id, step_id) "
                "DEFERRABLE INITIALLY DEFERRED",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs DROP CONSTRAINT "
                "datasource_outputs_pkey",
                "ALTER TABLE dbos.datasource_outputs ADD CONSTRAINT "
                "datasource_outputs_pkey PRIMARY KEY (workflow_id, step_id) "
                "NOT DEFERRABLE",
            ),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN created_at "
                "SET DEFAULT 0",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN created_at "
                "SET DEFAULT ((EXTRACT(EPOCH FROM now())*1000)::bigint)",
            ),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs ADD CONSTRAINT "
                "eva_4c_unexpected_check CHECK (step_id >= 0)",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs DROP CONSTRAINT "
                "eva_4c_unexpected_check",
            ),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN step_id "
                "ADD GENERATED ALWAYS AS IDENTITY",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN step_id "
                "DROP IDENTITY",
            ),
        ),
        (
            (
                "CREATE INDEX eva_4c_unexpected_index ON "
                "dbos.datasource_outputs(output)",
            ),
            ("DROP INDEX dbos.eva_4c_unexpected_index",),
        ),
        (
            (
                "CREATE RULE eva_4c_unexpected_rule AS ON UPDATE TO "
                "dbos.datasource_outputs DO ALSO NOTHING",
            ),
            ("DROP RULE eva_4c_unexpected_rule ON " "dbos.datasource_outputs",),
        ),
        (
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN workflow_id "
                'TYPE text COLLATE "C"',
            ),
            (
                "ALTER TABLE dbos.datasource_outputs ALTER COLUMN workflow_id "
                'TYPE text COLLATE pg_catalog."default"',
            ),
        ),
        (
            (
                "CREATE TABLE dbos.eva_4c_unexpected_child () "
                "INHERITS (dbos.datasource_outputs)",
            ),
            ("DROP TABLE dbos.eva_4c_unexpected_child",),
        ),
        (
            (
                "CREATE TABLE dbos.eva_4c_unexpected_parent "
                "(workflow_id text, step_id integer, output text, error text, "
                "serialization text, created_at bigint)",
                "ALTER TABLE dbos.datasource_outputs INHERIT "
                "dbos.eva_4c_unexpected_parent",
            ),
            (
                "ALTER TABLE dbos.datasource_outputs NO INHERIT "
                "dbos.eva_4c_unexpected_parent",
                "DROP TABLE dbos.eva_4c_unexpected_parent",
            ),
        ),
    ],
)
def test_runtime_catalog_attestation_rejects_semantic_mutation(
    postgres_4c_gate: Postgres4CGate,
    mutate: tuple[str, ...],
    restore: tuple[str, ...],
) -> None:
    try:
        with postgres_4c_gate.admin_engine.begin() as connection:
            for statement in mutate:
                connection.execute(sa.text(statement))

        with pytest.raises(DBOSDatasourceAttestationError):
            open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    finally:
        with postgres_4c_gate.admin_engine.begin() as connection:
            for statement in restore:
                connection.execute(sa.text(statement))

    datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    datasource.close()


def test_product_runtime_rejects_non_postgres_system_database_before_registration(
    postgres_4c_gate: Postgres4CGate,
) -> None:
    datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)

    with pytest.raises(
        DBOSDatasourceAttestationError,
        match="system state must use PostgreSQL",
    ):
        configure_episode_transition_runtime(
            datasource,
            system_database_url="sqlite:///:memory:",
            application_version="4c-gate-v1",
            executor_id="4c-invalid-system-db",
        )

    assert dbos_runtime_module._dbos_global_instance is None
    assert dbos_runtime_module._dbos_global_registry is None
    with pytest.raises(RuntimeError, match="datasource is closed"):
        datasource.transaction(name="closed")


def test_product_runtime_cannot_rebind_an_existing_dbos_registry(
    postgres_4c_gate: Postgres4CGate,
) -> None:
    first_datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    runtime = configure_episode_transition_runtime(
        first_datasource,
        system_database_url=postgres_4c_gate.runtime_dsn,
        application_version="4c-gate-v1",
        executor_id="4c-owned-runtime",
    )
    assert dbos_runtime_module._dbos_global_registry is not None
    registered = dbos_runtime_module._dbos_global_registry.workflow_info_map[
        CANONICAL_WORKFLOW_NAME
    ]
    second_datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    try:
        with pytest.raises(RuntimeError, match="pristine process DBOS"):
            configure_episode_transition_runtime(
                second_datasource,
                system_database_url=postgres_4c_gate.runtime_dsn,
                application_version="4c-gate-v1",
                executor_id="4c-rebind-attempt",
            )
        assert (
            dbos_runtime_module._dbos_global_registry.workflow_info_map[
                CANONICAL_WORKFLOW_NAME
            ]
            is registered
        )
        with pytest.raises(RuntimeError, match="datasource is closed"):
            second_datasource.transaction(name="closed")
    finally:
        runtime.close()
    assert dbos_runtime_module._dbos_global_instance is None
    assert dbos_runtime_module._dbos_global_registry is None


def test_product_runtime_rejects_dbos_cloud_mode_and_cleans_registry(
    postgres_4c_gate: Postgres4CGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    monkeypatch.setattr(GlobalParams, "dbos_cloud", True)

    with pytest.raises(RuntimeError, match="differs from sealed 4C config"):
        configure_episode_transition_runtime(
            datasource,
            system_database_url=postgres_4c_gate.runtime_dsn,
            application_version="4c-gate-v1",
            executor_id="4c-cloud-rejected",
        )

    assert dbos_runtime_module._dbos_global_instance is None
    assert dbos_runtime_module._dbos_global_registry is None
