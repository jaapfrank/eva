from __future__ import annotations

from pathlib import Path

import pytest

from eva.operations.install_schema import _HEAD_REVISION, _KNOWN_FORWARD_REVISIONS, _secret


def test_delegated_work_migration_is_the_explicit_schema_head() -> None:
    assert _HEAD_REVISION == "4e_0002_delegated_work"
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
