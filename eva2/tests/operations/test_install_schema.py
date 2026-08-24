from __future__ import annotations

from pathlib import Path

import pytest

from eva.operations.install_schema import _secret


def test_secret_file_is_read_without_putting_value_in_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "database-dsn"
    secret.write_text("postgresql+psycopg://eva:test@postgres/eva\n")
    monkeypatch.delenv("EVA_DATABASE_DSN", raising=False)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))
    assert (
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")
        == "postgresql+psycopg://eva:test@postgres/eva"
    )


def test_secret_source_is_exclusive_and_regular(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "database-dsn"
    secret.write_text("postgresql://example")
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://other")
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))
    with pytest.raises(RuntimeError, match="only one"):
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")

    monkeypatch.delenv("EVA_DATABASE_DSN")
    link = tmp_path / "database-dsn-link"
    link.symlink_to(secret)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(link))
    with pytest.raises(RuntimeError, match="non-symlink"):
        _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE")
