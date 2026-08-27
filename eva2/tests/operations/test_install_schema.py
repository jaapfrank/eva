from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic.util import CommandError
from eva.operations.install_schema import (
    _HEAD_REVISION,
    _KNOWN_FORWARD_REVISIONS,
    _require_no_schema_drift,
    _secret,
    main,
)


def test_delegated_work_migration_is_the_explicit_schema_head() -> None:
    assert _HEAD_REVISION == "4e_0004_delegated_work_stop"
    assert _HEAD_REVISION in _KNOWN_FORWARD_REVISIONS


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
    def successful_check(_config: object) -> None:
        print("No new upgrade operations detected.")

    monkeypatch.setattr("eva.operations.install_schema.command.check", successful_check)

    _require_no_schema_drift(object())  # type: ignore[arg-type]

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
