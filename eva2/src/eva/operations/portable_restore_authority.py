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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from pathlib import Path

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

    authority = _canonical_object(
        restore_authority_raw, label="portable restore authority"
    )
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
    if (
        authority.get("contract") != "EvaPortableRestoreAuthority/v1"
        or authority.get("schema_version") != 1
        or _uuid(
            authority.get("source_installation_id"),
            label="authority installation ID",
        )
        != installation_id
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
        erasure_sha256 = _digest(erasure_sha256, label="erasure operation digest")

    rows = authority.get("authorized_archives")
    if not isinstance(rows, list) or len(rows) > 1_024:
        raise PortableRestoreAuthorityError("authorized archive set is invalid")
    identities: list[str] = []
    matched = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "archive_id",
            "archive_json_sha256",
            "created_at",
        }:
            raise PortableRestoreAuthorityError("authorized archive row is invalid")
        row_id = _uuid(row.get("archive_id"), label="authorized archive ID")
        row_sha256 = _digest(
            row.get("archive_json_sha256"), label="authorized archive digest"
        )
        row_created_at = _timestamp(
            row.get("created_at"), label="authorized archive creation time"
        )
        if generation > 0 and row_created_at <= issued_at:
            raise PortableRestoreAuthorityError(
                "authorized archive predates the latest erasure generation"
            )
        identities.append(row_id)
        matched += int(
            (row_id, row_sha256, row_created_at)
            == (archive_id, archive_sha256, created_at)
        )
    if identities != sorted(set(identities)):
        raise PortableRestoreAuthorityError(
            "authorized archives must be unique and sorted"
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
    "PortableRestoreAuthorization",
    "PortableRestoreAuthorityError",
    "authorize_portable_restore",
    "read_private_restore_authority",
]
