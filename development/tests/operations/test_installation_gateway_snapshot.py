"""WAL preservation and failed publication on synthetic gateway databases."""
from contextlib import closing
import sqlite3
import pytest
from tools.installation_gateway_snapshot import snapshot_gateway_database


@pytest.fixture
def database(tmp_path):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "private"
    target.mkdir(mode=0o700)
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE tools(id TEXT PRIMARY KEY, binding TEXT)")
        writer.execute("INSERT INTO tools VALUES('retained-tool', 'retained-binding')")
        writer.commit()
        assert (tmp_path / "source.sqlite-wal").stat().st_size > 0
        yield source, target / "contextforge.sqlite", writer


def test_preserves_committed_wal_data_and_checks_before_and_after(database):
    source, destination, writer = database
    checks = []
    result = snapshot_gateway_database(source, destination, verify_quiescence=lambda:checks.append(True))
    assert checks == [True, True]
    assert result["status"] == "snapshot_collected"
    assert destination.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(destination)) as restored:
        assert restored.execute("SELECT * FROM tools").fetchall() == [("retained-tool", "retained-binding")]
        assert restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert writer.execute("SELECT count(*) FROM tools").fetchone() == (1,)
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("fail_at", [1, 2])
def test_runtime_guard_failure_never_publishes(database, fail_at):
    source, destination, _ = database
    calls = []
    def check():
        calls.append(True)
        if len(calls) == fail_at:
            raise ValueError("writer active")
    with pytest.raises(ValueError, match="writer active"):
        snapshot_gateway_database(source, destination, verify_quiescence=check)
    assert list(destination.parent.iterdir()) == []


def test_target_created_during_final_check_is_not_overwritten(database):
    source, destination, _ = database
    calls = []
    def check():
        calls.append(True)
        if len(calls) == 2:
            destination.write_bytes(b"other operator result")
    with pytest.raises(FileExistsError):
        snapshot_gateway_database(source, destination, verify_quiescence=check)
    assert destination.read_bytes() == b"other operator result"
    assert list(destination.parent.iterdir()) == [destination]


def test_source_changed_during_backup_is_rejected(database, monkeypatch):
    source, destination, writer = database
    import tools.installation_gateway_snapshot as module
    original = module._logical_digest
    calls = []
    def digest(connection):
        value = original(connection)
        calls.append(True)
        if len(calls) == 1:
            writer.execute("INSERT INTO tools VALUES('late', 'changed')")
            writer.commit()
        return value
    monkeypatch.setattr(module, "_logical_digest", digest)
    with pytest.raises(ValueError, match="changed during snapshot"):
        snapshot_gateway_database(source, destination, verify_quiescence=lambda:None)
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_alias_source_refused(database, kind):
    source, destination, _ = database
    alias = source.parent / "alias.sqlite"
    if kind == "symlink":
        alias.symlink_to(source)
    else:
        import os
        os.link(source, alias)
    with pytest.raises(ValueError, match="single-link"):
        snapshot_gateway_database(alias, destination, verify_quiescence=lambda:None)
    assert list(destination.parent.iterdir()) == []
