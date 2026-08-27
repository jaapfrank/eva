from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import tempfile

import pytest

from eva.application.legacy_memory_trial import LegacyMemoryTrialError
from tools.run_legacy_memory_trial import (
    _active_records,
    _copy_readonly_sqlite_snapshot,
    _read_private_text,
    _trial_url,
    run_trial,
)


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


def test_snapshot_has_one_consistent_read_only_source_state(tmp_path: Path) -> None:
    source = tmp_path / "live eva with spaces.db"
    temporary = tempfile.TemporaryDirectory(prefix="eva2-legacy-snapshot-test-", dir="/var/tmp")
    snapshot = Path(temporary.name) / "snapshot.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "CREATE TABLE memories (id INTEGER, ts TEXT, person TEXT, content TEXT, category TEXT, archived INTEGER)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (1, '2026-08-01T12:00:00Z', 'owner', 'first', 'preference', 0)"
        )
        connection.commit()
        _copy_readonly_sqlite_snapshot(source=source, destination=snapshot)
        connection.execute(
            "INSERT INTO memories VALUES (2, '2026-08-01T12:01:00Z', 'owner', 'later', 'preference', 0)"
        )
        connection.commit()
    finally:
        connection.close()

    try:
        records = _active_records(source=snapshot, person_label="owner", limit=25)

        assert [record.legacy_memory_id for record in records] == [1]
        assert snapshot.stat().st_mode & 0o777 == 0o600
    finally:
        temporary.cleanup()


@pytest.mark.parametrize(
    "dsn",
    (
        "sqlite:///tmp/legacy.db",
        "postgresql+psycopg://localhost/eva2_live",
        "postgresql+psycopg://localhost/eva2_4b_disposable_trial",
        "postgresql+psycopg://example.invalid/eva2_legacy_memory_trial_owner",
    ),
)
def test_trial_url_refuses_non_trial_databases(dsn: str) -> None:
    with pytest.raises(
        LegacyMemoryTrialError, match="eva2_legacy_memory_trial|local PostgreSQL host"
    ):
        _trial_url(dsn)


def test_trial_url_accepts_only_named_disposable_database() -> None:
    assert _trial_url(
        "postgresql+psycopg://localhost/eva2_legacy_memory_trial_owner"
    ).endswith("eva2_legacy_memory_trial_owner")


def test_plan_only_hashes_one_private_snapshot_without_opening_postgres(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "CREATE TABLE memories (id INTEGER, ts TEXT, person TEXT, content TEXT, category TEXT, archived INTEGER)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (1, '2026-08-01T12:00:00Z', 'owner', 'kept', 'preference', 0)"
        )
        connection.commit()
    finally:
        connection.close()
    label = _private(tmp_path / "label", "owner")
    confirmation = _private(
        tmp_path / "confirmation", "CONFIRM_LEGACY_MEMORY_TRIAL_V1"
    )

    receipt = run_trial(
        argparse.Namespace(
            source_sqlite=source,
            person_label_file=label,
            confirmation_file=confirmation,
            trial_dsn=None,
            tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
            installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
            target_principal_id="8d0ce6a5-da38-51a8-81a6-f2a99d1652d3",
            provenance_evidence_id="52752881-f9e4-58e7-a398-8c4a982d6794",
            limit=25,
            retention_days=7,
            plan_only=True,
        )
    )

    assert receipt["mode"] == "plan_only"
    assert receipt["selected_count"] == 1
    assert receipt["imported_count"] == 0
    assert receipt["ordinary_recall_count"] is None
    assert len(str(receipt["commands_sha256"])) == 64


def test_write_mode_requires_a_disposable_database_dsn(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "CREATE TABLE memories (id INTEGER, ts TEXT, person TEXT, content TEXT, category TEXT, archived INTEGER)"
        )
        connection.execute(
            "INSERT INTO memories VALUES (1, '2026-08-01T12:00:00Z', 'owner', 'kept', 'preference', 0)"
        )
        connection.commit()
    finally:
        connection.close()
    label = _private(tmp_path / "label", "owner")
    confirmation = _private(
        tmp_path / "confirmation", "CONFIRM_LEGACY_MEMORY_TRIAL_V1"
    )

    with pytest.raises(LegacyMemoryTrialError, match="DSN is required"):
        run_trial(
            argparse.Namespace(
                source_sqlite=source,
                person_label_file=label,
                confirmation_file=confirmation,
                trial_dsn=None,
                tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
                installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
                target_principal_id="8d0ce6a5-da38-51a8-81a6-f2a99d1652d3",
                provenance_evidence_id="52752881-f9e4-58e7-a398-8c4a982d6794",
                limit=25,
                retention_days=7,
                plan_only=False,
            )
        )
