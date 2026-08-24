from __future__ import annotations

from pathlib import Path

import pytest

from eva.operations.durable_goal_worker import _database_dsn


def test_durable_goal_worker_requires_one_private_file(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql+psycopg://eva:test@postgres/eva\n", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.delenv("EVA_DATABASE_DSN", raising=False)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))

    assert _database_dsn() == "postgresql+psycopg://eva:test@postgres/eva"


def test_durable_goal_worker_rejects_inline_or_permissive_dsn(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    secret = private_tmp_path / "database-dsn"
    secret.write_text("postgresql://example", encoding="utf-8")
    secret.chmod(0o640)
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://inline")
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(secret))

    with pytest.raises(RuntimeError, match="group or other permissions"):
        _database_dsn()


def test_durable_goal_worker_does_not_fall_back_to_inline_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVA_DATABASE_DSN", "postgresql://inline")
    monkeypatch.delenv("EVA_DATABASE_DSN_FILE", raising=False)

    with pytest.raises(RuntimeError, match="EVA_DATABASE_DSN_FILE is required"):
        _database_dsn()
