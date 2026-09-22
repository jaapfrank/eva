"""Collect a closed gateway database through SQLite's WAL-aware backup API.

The installation controller supplies fresh process/fencing checks. This helper
never stops a service, imports data or authorizes a restore.
"""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile


def _regular_identity(path):
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("gateway snapshot path has a symbolic parent")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("gateway database must be a single-link regular file")
    return info.st_dev, info.st_ino


def _logical_digest(connection):
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        raw = statement.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def snapshot_gateway_database(source: Path, destination: Path, *, verify_quiescence):
    """Publish a private complete snapshot only after repeated live checks.

    verify_quiescence is trusted operator composition, never a manifest boolean.
    The source must remain fenced for the whole collection operation. This
    point-in-time helper is not a mutex against other privileged operators.
    """
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("gateway snapshot paths must be absolute")
    parent = destination.parent
    info = parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or any(p.is_symlink() for p in (parent, *parent.parents))):
        raise ValueError("gateway snapshot destination must have a private operator directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("gateway snapshot destination already exists")
    verify_quiescence()
    original = _regular_identity(source)
    fd, name = tempfile.mkstemp(prefix=".gateway-", suffix=".sqlite", dir=parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as live:
            before = _logical_digest(live)
            with closing(sqlite3.connect(temporary)) as target:
                live.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("gateway snapshot integrity differs")
                copied = _logical_digest(target)
            if copied != before or _logical_digest(live) != before:
                raise ValueError("gateway database changed during snapshot")
        if _regular_identity(source) != original:
            raise ValueError("gateway source was replaced during snapshot")
        verify_quiescence()
        with temporary.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
            os.fsync(data.fileno())
        # Exclusive publication: an existing file or symlink is never replaced.
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"status":"snapshot_collected", "sha256":digest,
                "logical_sha256":copied, "bytes":destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(temporary)+suffix).unlink(missing_ok=True)
