"""Collect explicitly fenced installation sources; never stop or start services.

The trusted caller supplies all writers and maintains their fence throughout.
Before/after inventories detect observed changes, not adversarial ABA changes or
unknown writers. Failed private output is retained without a completion receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from tools.eva2_quiescent_cutover import SystemBackend
from tools.installation_gateway_snapshot import snapshot_gateway_database


class SourceSnapshotError(RuntimeError):
    """Partial output must be inspected, never automatically retried or removed."""


def _label(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)


def _path(path):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("absolute source paths required")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("symbolic source paths refused")


def _stamp(info):
    return tuple(getattr(info, key) for key in
                 ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _file(path, target=None):
    _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    output = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("only single-link regular source files allowed")
        if target is not None:
            output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest, size = hashlib.sha256(), 0
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
            size += len(block)
            if output is not None:
                view = memoryview(block)
                while view:
                    written = os.write(output, view)
                    view = view[written:]
        if _stamp(before) != _stamp(os.fstat(fd)) or _stamp(before) != _stamp(path.lstat()):
            raise ValueError("source file changed during collection")
        if output is not None:
            os.fchmod(output, 0o600)
            os.fsync(output)
        return {"kind": "file", "size": size, "sha256": digest.hexdigest()}, _stamp(before)
    finally:
        os.close(fd)
        if output is not None:
            os.close(output)


def _inventory(root, target=None):
    entries, stamps = {}, {}

    def walk(path, relative, output):
        _path(path)
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            entries[relative], stamps[relative] = _file(path, output)
        elif stat.S_ISDIR(info.st_mode):
            entries[relative], stamps[relative] = {"kind": "directory"}, _stamp(info)
            if output is not None:
                output.mkdir(mode=0o700)
                output.chmod(0o700)
            for child in sorted(path.iterdir()):
                walk(child, child.name if relative == "." else relative + "/" + child.name,
                     None if output is None else output / child.name)
            if _stamp(info) != _stamp(path.lstat()):
                raise ValueError("source directory changed during collection")
        else:
            raise ValueError("nonregular source entry refused")

    walk(root, ".", target)
    return entries, stamps


def collect_installation_source_snapshot(*, destination: Path, databases: dict,
        gateway_database: Path, sources: dict, verify_quiescence,
        dump_writer=None, gateway_snapshot=None):
    """Collect named native sources plus custom PostgreSQL and SQLite snapshots.

    Database selectors are trusted Docker container/database pairs, not DSNs.
    The receipt contains labels and content inventory, never those selectors or
    source paths. Callers must not select credential files as ordinary sources.
    """
    if not callable(verify_quiescence) or not databases or not isinstance(databases, dict) or not isinstance(sources, dict):
        raise ValueError("explicit database inventory and live guard required")
    for label, pair in databases.items():
        if (not _label(label) or not isinstance(pair, dict) or set(pair) != {"container", "database"}
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) for value in pair.values())):
            raise ValueError("invalid database source selector")
    _path(destination)
    _path(gateway_database)
    info = destination.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("private operator destination parent required")
    for label, source in sources.items():
        if not _label(label):
            raise ValueError("invalid source label")
        _path(source)
        if source == destination or source in destination.parents or destination in source.parents:
            raise ValueError("source and destination overlap")
    if gateway_database == destination or destination in gateway_database.parents:
        raise ValueError("gateway and destination overlap")
    verify_quiescence()
    destination.mkdir(mode=0o700, exist_ok=False)
    destination.chmod(0o700)
    try:
        baseline = {label: _inventory(path) for label, path in sources.items()}
        verify_quiescence()
        for name in ("databases", "gateway", "sources"):
            (destination / name).mkdir(mode=0o700)
        writer = dump_writer or SystemBackend._write_dump
        for label, pair in databases.items():
            verify_quiescence()
            writer(pair["container"], pair["database"], destination / "databases" / (label + ".dump"), portable=True)
            verify_quiescence()
        (gateway_snapshot or snapshot_gateway_database)(gateway_database,
            destination / "gateway/contextforge.sqlite", verify_quiescence=verify_quiescence)
        verify_quiescence()
        for label, source in sources.items():
            if _inventory(source, destination / "sources" / label) != baseline[label]:
                raise ValueError("source changed before copy completed")
            verify_quiescence()
        if any(_inventory(path) != baseline[label] for label, path in sources.items()):
            raise ValueError("source changed after collection")
        entries, _ = _inventory(destination)
        for relative, entry in entries.items():
            path = destination if relative == "." else destination / relative
            path.chmod(0o700 if entry["kind"] == "directory" else 0o600)
        verify_quiescence()
        receipt = {"contract": "EvaInstallationSourceSnapshot/v1", "status": "collected",
                   "database_labels": sorted(databases), "source_labels": sorted(sources), "entries": entries}
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        pending = destination / ".source-snapshot.pending"
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        for relative, entry in sorted(entries.items(), reverse=True):
            if entry["kind"] == "directory":
                directory = destination if relative == "." else destination / relative
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        verify_quiescence()
        os.link(pending, destination / "source-snapshot.json", follow_symlinks=False)
        pending.unlink()
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return receipt
    except Exception as error:
        raise SourceSnapshotError("source snapshot incomplete; preserve private partial output for inspection") from error
