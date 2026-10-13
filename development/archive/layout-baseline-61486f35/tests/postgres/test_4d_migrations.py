from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import runpy

from alembic import command
from alembic.config import Config
import pytest


ROOT = Path(__file__).resolve().parents[2]
REVISION_ROOT = ROOT / "migrations/versions"


def _revision_assignments(path: Path) -> dict[str, str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }


def test_4d_has_the_exact_sealed_linear_revision_chain() -> None:
    actual = {
        values["revision"]: values["down_revision"]
        for path in sorted(REVISION_ROOT.glob("4d_*.py"))
        if (values := _revision_assignments(path))
    }
    assert actual == {
        "4d_0003_core_continuity": "4b_0002_episode_atomicity",
        "4d_0004_effect_authority": "4d_0003_core_continuity",
        "4d_0005_evidence_objects": "4d_0004_effect_authority",
        "4d_0006_memory": "4d_0005_evidence_objects",
        "4d_0007_projection_telemetry": "4d_0006_memory",
    }


def test_frozen_4b_schema_and_migrations_are_byte_identical() -> None:
    expected = {
        "src/eva/adapters/postgres/schema.py": "23c16da5fb5b72d1b7b3ec543e838b23bb8cd84a079207a848d1b82d1e0d80ef",
        "migrations/versions/4b_0000_core_namespace.py": "59fc8da9f1e152b69d953746187e413fe0602dff7df7de0b01160e3d3867cb6c",
        "migrations/versions/4b_0001_tenant_scope.py": "4a65a74790aec595c8643a991e64b10a7e0533f14ff1afe277fef57c6000e3a4",
        "migrations/versions/4b_0002_episode_atomicity.py": "8e725d5696dad6104a25e1580bd28cc6c018427938c3d8dd42748afd4e4dd2e7",
    }
    assert {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in expected
    } == expected


def test_migrations_contain_no_backfill_default_seed_or_runtime_ddl() -> None:
    forbidden = ("server_default", "DEFAULT ", "UPDATE eva_", "INSERT INTO eva_")
    for path in sorted(REVISION_ROOT.glob("4d_*.py")):
        source = path.read_text(encoding="utf-8")
        assert all(marker not in source for marker in forbidden), path


@pytest.mark.parametrize(
    ("operation", "revision"),
    (
        ("upgrade", "head"),
        ("upgrade", "heads"),
        ("upgrade", "4d_0003_core_continuity"),
        ("upgrade", "4d_0003"),
        ("upgrade", "+4"),
        ("upgrade", "4b_0002_episode_atomicity:head"),
        ("upgrade", "4b_0002_episode_atomicity:4b_0002_episode_atomicity+1"),
        ("downgrade", "head:4b_0002_episode_atomicity"),
        ("downgrade", "4d_0004_effect_authority:4d_0004_effect_authority-1"),
    ),
)
def test_offline_route_containing_4d_fails_before_sql_output(
    operation: str, revision: str
) -> None:
    output = io.StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)

    try:
        getattr(command, operation)(config, revision, sql=True)
    except RuntimeError as error:
        assert str(error) == "eva4d_offline_upgrade_unsupported"
    else:  # pragma: no cover - fail-closed behavior is the assertion target
        raise AssertionError("offline 4D upgrade was unexpectedly admitted")

    assert output.getvalue() == ""


def test_offline_route_entirely_within_4b_remains_supported() -> None:
    output = io.StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("sqlalchemy.url", "postgresql://")

    command.upgrade(
        config,
        "4b_0000_core_namespace:4b_0002_episode_atomicity",
        sql=True,
    )

    assert output.getvalue()
    assert "4d_" not in output.getvalue()


def test_0003_explicitly_alters_and_restores_the_five_preserved_collations() -> None:
    source = (REVISION_ROOT / "4d_0003_core_continuity.py").read_text(encoding="utf-8")
    assert source.count('COLLATE "C"') == 1
    assert source.count('COLLATE "default"') == 1
    assert "PRESERVED_4B_COLLATION_ALTERS" in source


def test_0003_revokes_column_acls_before_dropping_the_added_columns() -> None:
    source = (REVISION_ROOT / "4d_0003_core_continuity.py").read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]
    assert downgrade.index("application_role_revoke_statements") < downgrade.index(
        "op.drop_column"
    )


class _BootstrapMappings:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def one(self) -> dict[str, object]:
        return self._row


class _BootstrapResult:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row or {}

    def mappings(self) -> _BootstrapMappings:
        return _BootstrapMappings(self._row)


class _BootstrapConnection:
    def __init__(self, authority_sql: str, owner_sql: str) -> None:
        self.authority_sql = authority_sql
        self.owner_sql = owner_sql
        self.sql: list[str] = []

    def exec_driver_sql(self, statement: str) -> _BootstrapResult:
        self.sql.append(statement)
        if statement == self.authority_sql:
            return _BootstrapResult(
                {
                    "session_user_name": "postgres",
                    "current_user_name": "postgres",
                    "unrestricted_rls_read": True,
                    "may_set_migrator": True,
                }
            )
        if statement == self.owner_sql:
            return _BootstrapResult(
                {
                    "session_user_name": "postgres",
                    "current_user_name": "eva_migrator",
                }
            )
        return _BootstrapResult()


class _BootstrapOp:
    def __init__(self, connection: _BootstrapConnection) -> None:
        self.connection = connection

    def get_bind(self) -> _BootstrapConnection:
        return self.connection


def test_0003_bootstraps_exactly_five_namespaces_before_table_ddl() -> None:
    namespace = runpy.run_path(str(REVISION_ROOT / "4d_0003_core_continuity.py"))
    statements = namespace["INITIAL_ADMISSION_STATEMENTS"]
    schemas = namespace["NAMESPACE_BOOTSTRAP_SCHEMAS"]
    freeze = json.loads(
        (ROOT / "design/vertical/noor-4d-schema-export-freeze-v1.json").read_text(
            encoding="utf-8"
        )
    )
    sealed = freeze["migrationFreeze"]["initialAdmissionPrecondition"][
        "postAdmissionNamespaceBootstrapException"
    ]
    assert list(schemas) == sealed["schemaOrder"]

    connection = _BootstrapConnection(statements[3].sql, statements[8].sql)
    bootstrap = namespace["_bootstrap_namespaces"]
    bootstrap.__globals__["op"] = _BootstrapOp(connection)
    bootstrap()

    expected = [statements[1].sql, statements[3].sql]
    for schema in schemas:
        expected.extend(
            (
                f"CREATE SCHEMA {schema} AUTHORIZATION eva_migrator;",
                f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC;",
            )
        )
    expected.extend((statements[7].sql, statements[8].sql))
    assert connection.sql == expected
    assert all(
        "CREATE SCHEMA" not in path.read_text(encoding="utf-8")
        for path in REVISION_ROOT.glob("4d_000[4-7]_*.py")
    )


def test_namespace_downgrade_is_owned_by_the_revision_that_created_them() -> None:
    core_source = (REVISION_ROOT / "4d_0003_core_continuity.py").read_text(
        encoding="utf-8"
    )
    assert 'op.execute(f"DROP SCHEMA {schema}")' in core_source
    assert all(
        "DROP SCHEMA" not in path.read_text(encoding="utf-8")
        for path in REVISION_ROOT.glob("4d_000[4-7]_*.py")
    )
