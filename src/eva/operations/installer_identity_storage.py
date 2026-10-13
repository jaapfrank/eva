"""Owner-only artifact storage for the local installer authority."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
import stat
import tempfile

from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError

_MAX_ARTIFACT_BYTES = 65_536


def read_private_artifact(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IdentityPackLifecycleError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_ARTIFACT_BYTES
    ):
        raise IdentityPackLifecycleError(f"{label} is not a private regular file")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            raw = handle.read(_MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise IdentityPackLifecycleError(f"{label} is unavailable") from exc
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise IdentityPackLifecycleError(f"{label} is too large")
    return raw


def write_private_once(path: Path, raw: bytes) -> bool:
    """Write exact bytes once; an identical retained artifact is idempotent."""

    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        retained = read_private_artifact(path, label="installer identity artifact")
        if not hmac.compare_digest(retained, raw):
            raise IdentityPackLifecycleError("installer identity artifact conflicts")
        return True
    except OSError as exc:
        raise IdentityPackLifecycleError(
            "installer identity artifact write failed"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise IdentityPackLifecycleError(
            "installer identity artifact write failed"
        ) from exc
    return False


def atomic_write_private(path: Path, raw: bytes) -> None:
    descriptor: int | None = None
    temporary: str | None = None
    if path.exists() or path.is_symlink():
        read_private_artifact(path, label="installer identity workflow")
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".installer-identity-", dir=path.parent
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = None
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise IdentityPackLifecycleError(
            "installer identity workflow write failed"
        ) from exc


__all__ = ["atomic_write_private", "read_private_artifact", "write_private_once"]
