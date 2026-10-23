"""Preserve only the existing bridge's active ArcFace profiles, never its old DB.

The original reader remains the schema/encoding/revision authority. This adapter
writes a new standalone faces-only database, not an agent database or a model.
"""
from pathlib import Path
import hashlib
import os
import sqlite3
from uuid import uuid4

from eva.adapters.connectors.local_face_identity import read_enrolled_profiles

_SELECTION = ("SELECT id,person,vec,quality,backend,active FROM faces "
              "WHERE backend='arcface' AND coalesce(active,1)!=0 ORDER BY id LIMIT 4097")
_COLUMNS = frozenset({"id", "person", "vec", "quality", "backend", "active"})


class FaceProfileSnapshotError(RuntimeError):
    """The operator-private partial file is retained; no sensitive details escape."""


def _source_reads_only(action, table, column, database, trigger):
    if action == sqlite3.SQLITE_READ:
        return (sqlite3.SQLITE_OK if table == "faces" and column in _COLUMNS
                else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if column == "coalesce" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and table in {"BEGIN", "ROLLBACK"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def snapshot_enrolled_faces(source: Path, destination: Path, *, backend_revision: str) -> dict:
    """Copy the current reader-selected rows to a new inactive private SQLite file.

    No image, enrollment, health check, inference, token or other source table is
    read. SQLite mode=ro includes the current WAL; immutable=1 is intentionally
    not used. A concurrent profile revision change rejects publication.
    """
    source, destination = Path(source), Path(destination)
    parent = destination.parent
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
            or source.resolve() != source or not destination.is_absolute()
            or parent.resolve() != parent or not parent.is_dir()
            or parent.stat().st_uid != os.geteuid()
            or parent.stat().st_mode & 0o777 != 0o700):
        raise ValueError("explicit source and private operator destination required")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("face snapshot destination already exists")
    if (not isinstance(backend_revision, str) or not backend_revision.strip()
            or len(backend_revision) > 200):
        raise ValueError("existing backend revision required")
    pending = parent / ("." + destination.name + ".pending-" + uuid4().hex)
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=2) as origin:
            origin.execute("PRAGMA query_only=ON")
            origin.set_authorizer(_source_reads_only)
            origin.execute("BEGIN")
            rows = origin.execute(_SELECTION).fetchall()
            if len(rows) > 4096:
                raise ValueError("profile reader bound exceeded")
            with sqlite3.connect(pending) as target:
                target.execute("PRAGMA journal_mode=DELETE")
                # No affinity changes to id/quality/vec: preserve original SQLite
                # values and the reader's exact id/person/quality/blob digest.
                target.execute("CREATE TABLE faces (id PRIMARY KEY,person,vec,quality,backend,active)")
                target.executemany("INSERT INTO faces VALUES (?,?,?,?,?,?)", rows)
            profiles, revision = read_enrolled_profiles(pending)
            current, current_revision = read_enrolled_profiles(source)
            if revision != current_revision or len(profiles) != len(current):
                raise ValueError("enrolled profiles changed during snapshot")
            origin.rollback()
        # No source schema or bytes are copied wholesale. Only the six selected
        # columns ever enter this fresh SQLite file.
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        size = pending.stat().st_size
        os.link(pending, destination, follow_symlinks=False)  # exclusive publication
        pending.unlink()
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"contract": "EvaEnrolledFaceSnapshot/v1", "status": "preserved_inactive",
                "database_sha256": digest, "database_bytes": size,
                "profile_revision": revision, "profile_count": len(profiles),
                "backend": "arcface", "backend_revision": backend_revision,
                "reader_revision_matches_current_source": True,
                "package_role": "document_journal", "mode": "0600",
                "source_database_written": False, "other_source_tables_read": False,
                "inference_performed": False, "runtime_activated": False}
    except FileExistsError:
        raise FileExistsError("face snapshot destination appeared during capture") from None
    except Exception:
        raise FaceProfileSnapshotError("face-only snapshot incomplete; preserve private partial output") from None
