from __future__ import annotations

import os
import uuid

import pytest

from eva.adapters.postgres.portable_export import PostgresPortableSnapshot


POSTGRES_DSN = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()


@pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="EVA2_TEST_POSTGRES_DSN is required for the real portable RLS gate",
)
def test_real_postgres_export_role_enforces_tenant_and_installation_isolation() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine(POSTGRES_DSN)
    schema = f"portable_p1_{uuid.uuid4().hex}"
    tenant_id = "66a4aad5-0550-5211-8b36-398eabe5b7be"
    installation_id = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
    other_tenant = "09ddd995-d43e-5a1c-a48c-7d99f3ee93c6"
    other_installation = "fc9d24b8-5e85-5f8a-991a-e5f92c8e5ae2"
    selected_row = "f6f7995a-cb95-56aa-92c0-8ec549e3f291"
    try:
        with engine.begin() as connection:
            bypass = connection.exec_driver_sql(
                "SELECT rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname = 'eva_backup'"
            ).scalar_one()
            assert bypass is False
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            connection.exec_driver_sql(
                f"CREATE TABLE {schema}.rows ("
                "tenant_id uuid NOT NULL, installation_id uuid NOT NULL, "
                "row_id uuid NOT NULL, PRIMARY KEY (tenant_id, installation_id, row_id))"
            )
            connection.exec_driver_sql(
                f"ALTER TABLE {schema}.rows ENABLE ROW LEVEL SECURITY"
            )
            connection.exec_driver_sql(
                f"ALTER TABLE {schema}.rows FORCE ROW LEVEL SECURITY"
            )
            connection.exec_driver_sql(
                f"CREATE POLICY exact_portable_scope ON {schema}.rows USING ("
                "tenant_id = pg_catalog.current_setting('eva.tenant_id')::uuid "
                "AND installation_id = "
                "pg_catalog.current_setting('eva.installation_id')::uuid)"
            )
            connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO eva_backup")
            connection.exec_driver_sql(f"GRANT SELECT ON {schema}.rows TO eva_backup")
            connection.exec_driver_sql(
                f"INSERT INTO {schema}.rows VALUES "
                f"('{tenant_id}','{installation_id}','{selected_row}'),"
                f"('{tenant_id}','{other_installation}',"
                "'324c0a02-ff4a-5aa8-a859-1c1518ad2e4e'),"
                f"('{other_tenant}','{installation_id}',"
                "'e5439779-74c6-5b9d-99da-5ecae582a2de')"
            )

        with engine.connect() as connection:
            with PostgresPortableSnapshot(
                connection,
                tenant_id=tenant_id,
                installation_id=installation_id,
                table_specs=(
                    {
                        "export_ordinal": 1,
                        "table": f"{schema}.rows",
                        "database_column_order": (
                            "tenant_id",
                            "installation_id",
                            "row_id",
                        ),
                        "primary_key": ("tenant_id", "installation_id", "row_id"),
                        "scope": "installation",
                    },
                ),
            ) as snapshot:
                rows = snapshot.read_all()

        assert rows[f"{schema}.rows"] == [
            {
                "tenant_id": uuid.UUID(tenant_id),
                "installation_id": uuid.UUID(installation_id),
                "row_id": uuid.UUID(selected_row),
            }
        ]
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        engine.dispose()
