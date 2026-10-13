from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.util import CommandError
from eva.operations.install_schema import (
    _HEAD_REVISION,
    _KNOWN_FORWARD_REVISIONS,
    _LOGICAL_RESTORE_ROLES,
    _LOGICAL_RESTORE_ROLE_SQL,
    LogicalRestoreRoleReceipt,
    _require_no_schema_drift,
    _secret,
    logical_restore_preparation_sql,
    main,
)


ROLE_SQL = importlib.import_module(
    "migrations.versions.4b_0000_core_namespace"
).ROLE_SQL


def test_delegated_work_migration_is_the_explicit_schema_head() -> None:
    assert _HEAD_REVISION == "4e_0009_restore_hold"
    assert _HEAD_REVISION in _KNOWN_FORWARD_REVISIONS


def test_logical_restore_role_bootstrap_matches_the_initial_migration() -> None:
    assert _LOGICAL_RESTORE_ROLE_SQL == ROLE_SQL
    assert _LOGICAL_RESTORE_ROLES == (
        "eva_backup",
        "eva_dbos",
        "eva_migrator",
        "eva_runtime",
    )
    preparation = logical_restore_preparation_sql()
    assert preparation.endswith(ROLE_SQL)
    assert "pg_catalog.pg_tables" in preparation
    assert "pg_catalog.pg_namespace" in preparation
    assert "completely empty target database" in preparation


def test_secret_file_is_read_without_putting_value_in_environment(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql+psycopg://eva:test@postgres/eva\n")
    secret.chmod(0o600)
    monkeypatch.delenv("EVA_DATABASE_DSN", raising=False)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))
    assert (
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")
        == "postgresql+psycopg://eva:test@postgres/eva"
    )


def test_secret_source_is_file_only_and_regular(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql://example")
    secret.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://other")
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))
    with pytest.raises(RuntimeError, match="EVA_DATABASE_DSN is forbidden"):
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")

    monkeypatch.delenv("EVA_DATABASE_DSN")
    link = private_tmp_path / "database-dsn-link"
    link.symlink_to(secret)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(link))
    with pytest.raises(RuntimeError, match="non-symlink"):
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")


def test_schema_drift_check_keeps_stdout_for_one_json_cli_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def successful_check(config: Config) -> None:
        config.print_stdout("No new upgrade operations detected.")

    monkeypatch.setattr("eva.operations.install_schema.command.check", successful_check)

    _require_no_schema_drift(Config())

    assert capsys.readouterr().out == ""


def test_cli_reports_alembic_drift_as_a_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql://example", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.delenv("EVA_DATABASE_DSN", raising=False)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))

    def drift(*_arguments: object, **_keywords: object) -> object:
        raise CommandError("new upgrade operations detected")

    monkeypatch.setattr("eva.operations.install_schema.install_schema", drift)

    assert main([]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "new upgrade operations detected",
        "status": "failed",
    }


def test_cli_prepares_only_the_fixed_roles_for_a_logical_restore(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql://example", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.delenv("EVA_DATABASE_DSN", raising=False)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))
    monkeypatch.setattr(
        "eva.operations.install_schema.prepare_logical_restore_roles",
        lambda _dsn: LogicalRestoreRoleReceipt(
            roles=_LOGICAL_RESTORE_ROLES,
            target_was_empty=True,
        ),
    )
    monkeypatch.setattr(
        "eva.operations.install_schema.install_schema",
        lambda *_args, **_kwargs: pytest.fail("normal schema installation was called"),
    )

    assert main(["--prepare-logical-restore-roles"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "contract": "EvaLogicalRestoreRoleReceipt/v1",
        "roles": list(_LOGICAL_RESTORE_ROLES),
        "status": "passed",
        "target_was_empty": True,
    }
