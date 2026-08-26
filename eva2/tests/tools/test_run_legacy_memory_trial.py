from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from eva.application.legacy_memory_trial import LegacyMemoryTrialError
from tools.run_legacy_memory_trial import _active_records, _read_private_text, _trial_url


def _private(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_private_file_rejects_group_readable_and_symlink(tmp_path: Path) -> None:
    private = _private(tmp_path / "label", "owner", 0o644)
    with pytest.raises(LegacyMemoryTrialError, match="group or others"):
        _read_private_text(private, label="person label file")
    private.chmod(0o600)
    linked = tmp_path / "linked"
    linked.symlink_to(private)
    with pytest.raises(LegacyMemoryTrialError, match="non-symlink"):
        _read_private_text(linked, label="person label file")


def test_active_records_are_read_only_selected_and_hashed(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE memories (id INTEGER, ts TEXT, person TEXT, content TEXT, category TEXT, archived INTEGER)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (1, '2026-08-01T12:00:00Z', 'owner', 'kept', 'preference', 0)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (2, '2026-08-01T12:00:00Z', 'owner', 'archived', 'preference', 1)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (3, '2026-08-01T12:00:00Z', 'other', 'other', 'preference', 0)"
        )
        connection.commit()
    finally:
        connection.close()

    records = _active_records(source=database, person_label="owner", limit=25)

    assert len(records) == 1
    assert records[0].legacy_memory_id == 1
    assert records[0].content == "kept"
    assert records[0].person_label_sha256 != "owner"


@pytest.mark.parametrize(
    "dsn",
    (
        "sqlite:///tmp/legacy.db",
        "postgresql+psycopg://localhost/eva2_live",
        "postgresql+psycopg://localhost/eva2_4b_disposable_trial",
    ),
)
def test_trial_url_refuses_non_trial_databases(dsn: str) -> None:
    with pytest.raises(LegacyMemoryTrialError, match="eva2_legacy_memory_trial"):
        _trial_url(dsn)


def test_trial_url_accepts_only_named_disposable_database() -> None:
    assert _trial_url(
        "postgresql+psycopg://localhost/eva2_legacy_memory_trial_owner"
    ).endswith("eva2_legacy_memory_trial_owner")
