"""External restore authority that keeps erased content out of old archives.

Portable archives are immutable and may live outside Eva's host.  Erasing live
content therefore cannot rewrite every old copy.  Instead, every restore must
be admitted by a small private authority file.  A completed erasure advances
its generation and clears the archive allowlist; only a newly created and
explicitly admitted archive can then be restored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import fcntl
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

from eva.application.portable_archive import canonical_json_bytes


_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_BYTES = 1_048_576


class PortableRestoreAuthorityError(ValueError):
    """The archive or private restore authority is malformed or mismatched."""


def read_private_restore_authority(
    path: Path, *, effective_uid: int | None = None
) -> bytes:
    """Read one stable owner-only authority file without following links."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise PortableRestoreAuthorityError(
            "portable restore authority path must be absolute"
        )
    owner_uid = os.geteuid() if effective_uid is None else effective_uid

    def validate(metadata: os.stat_result) -> None:
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != owner_uid
            or not mode & stat.S_IRUSR
            or mode & (stat.S_IRWXG | stat.S_IRWXO)
            or metadata.st_size > _MAX_BYTES
        ):
            raise PortableRestoreAuthorityError(
                "portable restore authority file is unavailable"
            )

    try:
        named = os.lstat(path)
        validate(named)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except (OSError, PortableRestoreAuthorityError) as error:
        if isinstance(error, PortableRestoreAuthorityError):
            raise
        raise PortableRestoreAuthorityError(
            "portable restore authority file is unavailable"
        ) from error
    try:
        opened = os.fstat(descriptor)
        validate(opened)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise PortableRestoreAuthorityError(
                "portable restore authority changed during preflight"
            )
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_mode,
            opened.st_size,
            opened.st_ctime_ns,
        )
        chunks: list[bytes] = []
        remaining = _MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_BYTES:
            raise PortableRestoreAuthorityError(
                "portable restore authority file is unavailable"
            )
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_mode,
            after.st_size,
            after.st_ctime_ns,
        ):
            raise PortableRestoreAuthorityError(
                "portable restore authority changed while it was read"
            )
        _canonical_object(raw, label="portable restore authority")
        return raw
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class PortableRestoreAuthorization:
    archive_id: str
    archive_json_sha256: str
    authority_sha256: str
    erasure_generation: int
    latest_erasure_operation_sha256: str | None
    source_installation_id: str


@dataclass(frozen=True, slots=True)
class PortableRestoreAuthorityAdvanceReceipt:
    authority_sha256: str
    erasure_generation: int
    operation_sha256: str
    replayed: bool
    source_installation_id: str


def _canonical_object(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_BYTES:
        raise PortableRestoreAuthorityError(f"{label} size is invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PortableRestoreAuthorityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise PortableRestoreAuthorityError(f"{label} is not canonical JSON")
    return value


def _uuid(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise PortableRestoreAuthorityError(f"{label} is invalid")
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise PortableRestoreAuthorityError(f"{label} is invalid") from error
    if value != canonical:
        raise PortableRestoreAuthorityError(f"{label} is invalid")
    return value


def _timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise PortableRestoreAuthorityError(f"{label} is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise PortableRestoreAuthorityError(f"{label} is invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PortableRestoreAuthorityError(f"{label} is invalid")
    return value


def _digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PortableRestoreAuthorityError(f"{label} is invalid")
    return value


def _validated_authority(
    raw: bytes, *, expected_installation_id: str | None = None
) -> dict[str, Any]:
    authority = _canonical_object(raw, label="portable restore authority")
    if set(authority) != {
        "authorized_archives",
        "contract",
        "erasure_generation",
        "issued_at",
        "latest_erasure_operation_sha256",
        "schema_version",
        "source_installation_id",
    }:
        raise PortableRestoreAuthorityError("portable restore authority shape differs")
    installation_id = _uuid(
        authority.get("source_installation_id"),
        label="authority installation ID",
    )
    if (
        authority.get("contract") != "EvaPortableRestoreAuthority/v1"
        or authority.get("schema_version") != 1
        or (
            expected_installation_id is not None
            and installation_id != expected_installation_id
        )
    ):
        raise PortableRestoreAuthorityError("portable restore authority scope differs")
    issued_at = _timestamp(authority.get("issued_at"), label="authority issue time")
    generation = authority.get("erasure_generation")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        raise PortableRestoreAuthorityError("erasure generation is invalid")
    erasure_sha256 = authority.get("latest_erasure_operation_sha256")
    if generation == 0:
        if erasure_sha256 is not None:
            raise PortableRestoreAuthorityError("empty erasure generation has a head")
    else:
        _digest(erasure_sha256, label="erasure operation digest")

    rows = authority.get("authorized_archives")
    if not isinstance(rows, list) or len(rows) > 1_024:
        raise PortableRestoreAuthorityError("authorized archive set is invalid")
    identities: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "archive_id",
            "archive_json_sha256",
            "created_at",
        }:
            raise PortableRestoreAuthorityError("authorized archive row is invalid")
        row_id = _uuid(row.get("archive_id"), label="authorized archive ID")
        _digest(row.get("archive_json_sha256"), label="authorized archive digest")
        row_created_at = _timestamp(
            row.get("created_at"), label="authorized archive creation time"
        )
        if generation > 0 and row_created_at <= issued_at:
            raise PortableRestoreAuthorityError(
                "authorized archive predates the latest erasure generation"
            )
        identities.append(row_id)
    if identities != sorted(set(identities)):
        raise PortableRestoreAuthorityError(
            "authorized archives must be unique and sorted"
        )
    return authority


def advance_restore_authority_for_erasure(
    raw: bytes,
    *,
    source_installation_id: str,
    operation_sha256: str,
    issued_at: datetime,
) -> tuple[bytes, PortableRestoreAuthorityAdvanceReceipt]:
    """Clear old archive admission before one explicit erasure can complete."""

    installation_id = _uuid(
        source_installation_id, label="source installation ID"
    )
    operation = _digest(operation_sha256, label="erasure operation digest")
    if issued_at.tzinfo is None or issued_at.utcoffset() != UTC.utcoffset(issued_at):
        raise PortableRestoreAuthorityError("erasure issue time is invalid")
    issued_at_text = issued_at.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    authority = _validated_authority(
        raw, expected_installation_id=installation_id
    )
    generation = authority["erasure_generation"]
    if authority["latest_erasure_operation_sha256"] == operation:
        return raw, PortableRestoreAuthorityAdvanceReceipt(
            authority_sha256=hashlib.sha256(raw).hexdigest(),
            erasure_generation=generation,
            operation_sha256=operation,
            replayed=True,
            source_installation_id=installation_id,
        )
    if issued_at_text < authority["issued_at"]:
        raise PortableRestoreAuthorityError("erasure issue time moves backwards")
    updated = canonical_json_bytes(
        {
            "authorized_archives": [],
            "contract": "EvaPortableRestoreAuthority/v1",
            "erasure_generation": generation + 1,
            "issued_at": issued_at_text,
            "latest_erasure_operation_sha256": operation,
            "schema_version": 1,
            "source_installation_id": installation_id,
        }
    )
    return updated, PortableRestoreAuthorityAdvanceReceipt(
        authority_sha256=hashlib.sha256(updated).hexdigest(),
        erasure_generation=generation + 1,
        operation_sha256=operation,
        replayed=False,
        source_installation_id=installation_id,
    )


class PrivateRestoreAuthorityStore:
    """Atomically advance one external, owner-only authority file.

    Writers lock the containing directory rather than the replaceable file, so
    concurrent erasures cannot both advance from the same generation.
    """

    def __init__(
        self,
        path: Path,
        *,
        source_installation_id: str,
        effective_uid: int | None = None,
    ) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or not path.name:
            raise PortableRestoreAuthorityError(
                "portable restore authority path must be absolute"
            )
        self._path = path
        self._installation_id = _uuid(
            source_installation_id, label="source installation ID"
        )
        self._owner_uid = os.geteuid() if effective_uid is None else effective_uid
        directory_fd = self._open_private_directory()
        os.close(directory_fd)

    def _open_private_directory(self) -> int:
        parent = self._path.parent
        try:
            named = os.lstat(parent)
            descriptor = os.open(
                parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as error:
            raise PortableRestoreAuthorityError(
                "portable restore authority directory is unavailable"
            ) from error
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != self._owner_uid
            or mode & (stat.S_IRWXG | stat.S_IRWXO)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            os.close(descriptor)
            raise PortableRestoreAuthorityError(
                "portable restore authority directory is unavailable"
            )
        return descriptor

    def _read_locked(self, directory_fd: int) -> bytes:
        try:
            descriptor = os.open(
                self._path.name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return canonical_json_bytes(
                {
                    "authorized_archives": [],
                    "contract": "EvaPortableRestoreAuthority/v1",
                    "erasure_generation": 0,
                    "issued_at": "1970-01-01T00:00:00Z",
                    "latest_erasure_operation_sha256": None,
                    "schema_version": 1,
                    "source_installation_id": self._installation_id,
                }
            )
        except OSError as error:
            raise PortableRestoreAuthorityError(
                "portable restore authority file is unavailable"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != self._owner_uid
                or not mode & stat.S_IRUSR
                or mode & (stat.S_IRWXG | stat.S_IRWXO)
                or metadata.st_size > _MAX_BYTES
            ):
                raise PortableRestoreAuthorityError(
                    "portable restore authority file is unavailable"
                )
            raw = bytearray()
            while len(raw) <= _MAX_BYTES:
                chunk = os.read(descriptor, min(65_536, _MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > _MAX_BYTES:
                raise PortableRestoreAuthorityError(
                    "portable restore authority file is unavailable"
                )
            return bytes(raw)
        finally:
            os.close(descriptor)

    def _replace_locked(self, directory_fd: int, raw: bytes) -> None:
        temporary_name = f".{self._path.name}.{uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short authority write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(
                temporary_name,
                self._path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except OSError as error:
            raise PortableRestoreAuthorityError(
                "portable restore authority update failed"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def advance_erasure_generation(
        self, *, operation_sha256: str, erased_at: datetime
    ) -> PortableRestoreAuthorityAdvanceReceipt:
        directory_fd = self._open_private_directory()
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            current = self._read_locked(directory_fd)
            updated, receipt = advance_restore_authority_for_erasure(
                current,
                source_installation_id=self._installation_id,
                operation_sha256=operation_sha256,
                issued_at=erased_at,
            )
            if not receipt.replayed:
                self._replace_locked(directory_fd, updated)
            persisted = self._read_locked(directory_fd)
            if hashlib.sha256(persisted).hexdigest() != receipt.authority_sha256:
                raise PortableRestoreAuthorityError(
                    "portable restore authority readback differs"
                )
            return receipt
        finally:
            os.close(directory_fd)


def authorize_portable_restore(
    *, archive_manifest_raw: bytes, restore_authority_raw: bytes
) -> PortableRestoreAuthorization:
    """Admit only an exact archive present in the latest private generation."""

    archive = _canonical_object(archive_manifest_raw, label="archive manifest")
    if (
        archive.get("contract") != "EvaPortableArchive/v1"
        or archive.get("schema_version") != 1
    ):
        raise PortableRestoreAuthorityError("archive manifest contract is invalid")
    archive_id = _uuid(archive.get("archive_id"), label="archive ID")
    installation_id = _uuid(
        archive.get("source_installation_id"), label="source installation ID"
    )
    created_at = _timestamp(archive.get("created_at"), label="archive creation time")
    archive_sha256 = hashlib.sha256(archive_manifest_raw).hexdigest()

    authority = _validated_authority(
        restore_authority_raw, expected_installation_id=installation_id
    )
    generation = authority["erasure_generation"]
    erasure_sha256 = authority["latest_erasure_operation_sha256"]
    rows = authority["authorized_archives"]
    matched = 0
    for row in rows:
        matched += int(
            (row["archive_id"], row["archive_json_sha256"], row["created_at"])
            == (archive_id, archive_sha256, created_at)
        )
    if matched != 1:
        raise PortableRestoreAuthorityError(
            "archive is not authorized after the latest erasure generation"
        )
    return PortableRestoreAuthorization(
        archive_id=archive_id,
        archive_json_sha256=archive_sha256,
        authority_sha256=hashlib.sha256(restore_authority_raw).hexdigest(),
        erasure_generation=generation,
        latest_erasure_operation_sha256=erasure_sha256,
        source_installation_id=installation_id,
    )


__all__ = [
    "PrivateRestoreAuthorityStore",
    "PortableRestoreAuthorization",
    "PortableRestoreAuthorityAdvanceReceipt",
    "PortableRestoreAuthorityError",
    "advance_restore_authority_for_erasure",
    "authorize_portable_restore",
    "read_private_restore_authority",
]
