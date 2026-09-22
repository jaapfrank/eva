"""Materialize verified installation bytes into one private inactive directory.

Owners are separately supplied operator policy, never archive data. This does
not reconcile source native identities, import databases, prepare relocation,
start providers or release the restore hold. A component has exactly one owner.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from tools.installation_recovery_package import _validate as validate_package_descriptor
from tools.read_eva_portable_archive import _root_descriptor, _scan_regular_files, _read_regular

COMPONENTS = {"core", "memory", "gateway", "documents", "config"}
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _owners(owners):
    if not isinstance(owners, dict) or set(owners) != COMPONENTS:
        raise ValueError("materialization requires one trusted owner per component")
    for owner in owners.values():
        if (not isinstance(owner, dict) or set(owner) != {"uid", "gid"}
                or any(type(value) is not int or not 0 <= value < 4294967295 for value in owner.values())):
            raise ValueError("materialization owner is invalid")
        if os.geteuid() != 0 and owner != {"uid": os.geteuid(), "gid": os.getegid()}:
            raise ValueError("materialization owner requires privileged provisioning")


def _private_operator_directory(fd):
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("materialization directory must be private and operator-owned")


def _rename_absent(parent_fd, source, destination):
    # The existing portable publication helper uses os.replace. Here an empty
    # directory appearing after validation must also be preserved, so require
    # the kernel's no-replace primitive; unsupported platforms fail closed.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise RuntimeError("inactive publication requires renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError("materialization destination already exists")
        raise OSError(code, "inactive publication failed")


def _verify_tree(fd, manifest_raw, entries, owners):
    if _scan_regular_files(fd) != set(entries) | {"archive.json"}:
        raise ValueError("materialized file inventory differs")
    if _read_regular(fd, "archive.json") != manifest_raw:
        raise ValueError("materialized manifest differs")
    for name, entry in entries.items():
        raw = _read_regular(fd, name)
        if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("materialized bytes differ")
    def walk(directory, prefix=""):
        for name in os.listdir(directory):
            relative = prefix + name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            component = relative.split("/")[0]
            owner = owners.get(component, {"uid": os.geteuid(), "gid": os.getegid()})
            mode = 0o700 if stat.S_ISDIR(info.st_mode) else (
                0o600 if relative == "archive.json" else int(entries[relative]["mode"], 8))
            if (info.st_uid != owner["uid"] or info.st_gid != owner["gid"]
                    or stat.S_IMODE(info.st_mode) != mode):
                raise ValueError("materialized owner or mode differs")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=directory)
                try:
                    walk(child, relative + "/")
                    os.fsync(child)
                finally:
                    os.close(child)
    walk(fd)
    os.fsync(fd)


def materialize_installation_package(package_root: Path, destination: Path, *, owners):
    """Publish an exact inactive copy at a previously absent absolute path."""
    _owners(owners)
    if (not isinstance(destination, Path) or not destination.is_absolute()
            or destination.name in {"", ".", ".."}):
        raise ValueError("materialization destination must be an absolute new path")
    with _root_descriptor(package_root) as source_fd, _root_descriptor(destination.parent) as parent_fd:
        _private_operator_directory(source_fd)
        _private_operator_directory(parent_fd)
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("materialization destination already exists")
        package = validate_package_descriptor(source_fd, _read_regular(source_fd, "archive.json", maximum=16777216))
        entries = package["manifest"]["files"]
        staging_name = ".installation-inactive-" + uuid4().hex
        os.mkdir(staging_name, mode=0o700, dir_fd=parent_fd)
        staging_fd = os.open(staging_name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        staging_path = Path(f"/proc/self/fd/{parent_fd}/{staging_name}")
        published = False
        try:
            directories = set()
            for name in entries:
                parts = name.split("/")[:-1]
                directories.update("/".join(parts[:index]) for index in range(1, len(parts)+1))
            for name in sorted(directories, key=lambda item: (item.count("/"), item)):
                os.mkdir(name, mode=0o700, dir_fd=staging_fd)
            for name in [*sorted(entries), "archive.json"]:
                raw = package["manifest_raw"] if name == "archive.json" else _read_regular(source_fd, name)
                if name != "archive.json":
                    entry = entries[name]
                    if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                        raise ValueError("installation source changed during materialization")
                owner = owners[name.split("/")[0]] if name != "archive.json" else {
                    "uid": os.geteuid(), "gid": os.getegid()}
                mode = int(entries[name]["mode"], 8) if name != "archive.json" else 0o600
                out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=staging_fd)
                with os.fdopen(out, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fchown(stream.fileno(), owner["uid"], owner["gid"])
                    os.fchmod(stream.fileno(), mode)
                    os.fsync(stream.fileno())
            for name in sorted(directories, key=lambda item: (-item.count("/"), item)):
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=staging_fd)
                try:
                    owner = owners[name.split("/")[0]]
                    os.fchown(child, owner["uid"], owner["gid"])
                    os.fchmod(child, 0o700)
                finally:
                    os.close(child)
            current = validate_package_descriptor(source_fd, _read_regular(source_fd, "archive.json", maximum=16777216))
            if current["manifest_raw"] != package["manifest_raw"]:
                raise ValueError("installation source manifest changed during materialization")
            _verify_tree(staging_fd, package["manifest_raw"], entries, owners)
            _rename_absent(parent_fd, staging_name, destination.name)
            published = True
            os.fsync(parent_fd)
        finally:
            os.close(staging_fd)
            if not published:
                # Only our unique unpublished directory, addressed through the
                # held parent, is removed. Existing destinations are untouched.
                shutil.rmtree(staging_path)
        return {"status": "materialized_inactive", "destination": str(destination),
                "archive_sha256": package["archive_sha256"], "file_count": len(entries)+1}
