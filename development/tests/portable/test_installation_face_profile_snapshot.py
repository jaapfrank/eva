"""The snapshot must preserve the real reader contract and exclude other data."""
from pathlib import Path
import hashlib
import sqlite3
import struct
import pytest

from eva.adapters.connectors.local_face_identity import read_enrolled_profiles
from tools import installation_face_profile_snapshot as adapter

REVISION = "sha256:" + "1" * 64 + ":cpu"


def database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE faces(id INTEGER PRIMARY KEY,person TEXT,vec BLOB,quality REAL,backend TEXT,active INTEGER)")
        connection.execute("CREATE TABLE conversations(secret TEXT)")
        connection.execute("INSERT INTO conversations VALUES ('MUST_NOT_TRANSFER')")
        vector = struct.pack("<512f", *([1.0] + [0.0] * 511))
        connection.executemany("INSERT INTO faces VALUES (?,?,?,?,?,?)", [
            (1, "Known-A", vector, .8, "arcface", 1),
            (2, "Inactive", vector, .9, "arcface", 0),
            (3, "Other-backend", vector, .6, "other", 1),
            (4, "Known-B", vector, None, "arcface", None)])
    return path


@pytest.fixture
def sources(tmp_path):
    source = database(tmp_path / "source.sqlite")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    return source, private / "profiles.sqlite"


def test_existing_reader_revision_and_only_confirmed_face_rows_survive(sources):
    source, target = sources
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
    assert read_enrolled_profiles(source) == read_enrolled_profiles(target)
    assert result["profile_count"] == 2 and result["mode"] == "0600"
    assert target.stat().st_mode & 0o777 == 0o600
    assert before == hashlib.sha256(source.read_bytes()).hexdigest()
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("faces",)]
        assert connection.execute("SELECT id FROM faces ORDER BY id").fetchall() == [(1,), (4,)]
    assert b"MUST_NOT_TRANSFER" not in target.read_bytes()
    assert b"Inactive" not in target.read_bytes() and b"Other-backend" not in target.read_bytes()
    assert result["profile_revision"] == read_enrolled_profiles(source)[1]
    assert not any(key in str(result) for key in ["Known-A", "Known-B", "MUST_NOT_TRANSFER"])


def test_current_wal_rows_are_included_without_checkpoint(sources):
    source, target = sources
    with sqlite3.connect(source) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE faces SET quality=.75 WHERE id=1")
        writer.commit()
        assert source.with_name(source.name + "-wal").is_file()
        result = adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
        assert result["profile_revision"] == read_enrolled_profiles(source)[1]


def test_existing_destination_is_untouched(sources):
    source, target = sources
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
    assert target.read_bytes() == b"existing"


@pytest.mark.parametrize("kind", ["source_link", "target_link", "public_parent"])
def test_unsafe_paths_rejected(sources, kind):
    source, target = sources
    if kind == "source_link":
        linked = source.with_name("source-link"); linked.symlink_to(source); source = linked
    elif kind == "target_link":
        target.symlink_to(source)
    else:
        target.parent.chmod(0o755)
    with pytest.raises((ValueError, FileExistsError)):
        adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)


def test_invalid_encoding_is_private_failure_and_not_published(sources):
    source, target = sources
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE faces SET vec=? WHERE id=1", (b"bad",))
    with pytest.raises(adapter.FaceProfileSnapshotError) as error:
        adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
    assert "Known" not in str(error.value) and not target.exists()
    assert list(target.parent.glob(".profiles.sqlite.pending-*"))


def test_drift_is_not_published(sources, monkeypatch):
    source, target = sources
    reader = adapter.read_enrolled_profiles
    def changed(path):
        profiles, revision = reader(path)
        return (profiles, "0" * 64) if Path(path) == source else (profiles, revision)
    monkeypatch.setattr(adapter, "read_enrolled_profiles", changed)
    with pytest.raises(adapter.FaceProfileSnapshotError):
        adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
    assert not target.exists()


def test_source_authorizer_rejects_unrelated_table_and_all_writes(sources):
    source, _ = sources
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        connection.set_authorizer(adapter._source_reads_only)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("SELECT secret FROM conversations").fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE faces SET quality=0")


def test_reader_bound_does_not_publish_partial_set(sources):
    source, target = sources
    with sqlite3.connect(source) as connection:
        row = connection.execute("SELECT person,vec,quality,backend,active FROM faces WHERE id=1").fetchone()
        connection.executemany("INSERT INTO faces VALUES (?,?,?,?,?,?)", [(i, *row) for i in range(10,4106)])
    with pytest.raises(adapter.FaceProfileSnapshotError):
        adapter.snapshot_enrolled_faces(source, target, backend_revision=REVISION)
    assert not target.exists()
