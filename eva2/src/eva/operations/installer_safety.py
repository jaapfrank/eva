"""Shared fail-closed filesystem and timestamp checks for installer workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import re
import stat


ErrorFactory = Callable[[str], Exception]
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def require_utc_timestamp(
    value: object,
    *,
    error: ErrorFactory,
    invalid_message: str,
    non_utc_message: str,
) -> str:
    """Return one strict second-resolution UTC timestamp or fail closed."""

    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise error(invalid_message)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise error(invalid_message) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise error(non_utc_message)
    return value


def require_owner_only_directory(
    path: Path,
    *,
    error: ErrorFactory,
    unavailable_message: str,
    invalid_message: str,
) -> None:
    """Require an existing, non-linked directory owned privately by this user."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise error(unavailable_message) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise error(invalid_message)


@contextmanager
def owner_only_lock(
    path: Path,
    *,
    error: ErrorFactory,
    unavailable_message: str,
) -> Iterator[None]:
    """Hold one owner-only, non-linked regular-file lock for a workflow."""

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
        ):
            raise OSError("installer lock is not a private regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise error(unavailable_message) from exc
    assert descriptor is not None
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


__all__ = [
    "owner_only_lock",
    "require_owner_only_directory",
    "require_utc_timestamp",
]
