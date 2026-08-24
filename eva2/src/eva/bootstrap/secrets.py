"""Fail-closed loading for runtime secrets supplied by environment or file."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import stat


MAX_RUNTIME_SECRET_BYTES = 64 * 1024


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_ctime_ns,
    )


def _validate_secret_metadata(
    metadata: os.stat_result,
    *,
    setting_name: str,
    owner_uid: int,
) -> None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            f"{setting_name} must name an accessible regular non-symlink file"
        )
    if metadata.st_uid != owner_uid:
        raise RuntimeError(
            f"{setting_name} must be owned by runtime effective UID {owner_uid}"
        )
    permissions = stat.S_IMODE(metadata.st_mode)
    if not permissions & stat.S_IRUSR:
        raise RuntimeError(f"{setting_name} must grant its owner read permission")
    if permissions & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(
            f"{setting_name} must not grant group or other permissions"
        )
    if metadata.st_size > MAX_RUNTIME_SECRET_BYTES:
        raise RuntimeError(f"{setting_name} is too large")


def read_private_runtime_file_bytes(
    raw_path: str,
    *,
    setting_name: str,
    effective_uid: int | None = None,
) -> bytes:
    """Snapshot one bounded private file through a verified nonblocking fd."""

    path = Path(raw_path)
    if not path.is_absolute():
        raise RuntimeError(f"{setting_name} must be an absolute path")

    try:
        named_metadata = os.lstat(path)
    except OSError as error:
        raise RuntimeError(
            f"{setting_name} must name an accessible regular non-symlink file"
        ) from error
    owner_uid = os.geteuid() if effective_uid is None else effective_uid
    _validate_secret_metadata(
        named_metadata,
        setting_name=setting_name,
        owner_uid=owner_uid,
    )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(
            f"{setting_name} must name an accessible regular non-symlink file"
        ) from error

    try:
        metadata = os.fstat(descriptor)
        _validate_secret_metadata(
            metadata,
            setting_name=setting_name,
            owner_uid=owner_uid,
        )
        if (metadata.st_dev, metadata.st_ino) != (
            named_metadata.st_dev,
            named_metadata.st_ino,
        ):
            raise RuntimeError(f"{setting_name} changed during permission preflight")
        before_read = _metadata_identity(metadata)
        chunks: list[bytes] = []
        remaining = MAX_RUNTIME_SECRET_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        secret_bytes = b"".join(chunks)
        if len(secret_bytes) > MAX_RUNTIME_SECRET_BYTES:
            raise RuntimeError(f"{setting_name} is too large")
        after_read = os.fstat(descriptor)
        if _metadata_identity(after_read) != before_read:
            raise RuntimeError(f"{setting_name} changed while it was read")
    finally:
        os.close(descriptor)

    return secret_bytes


def read_runtime_secret_file(
    raw_path: str,
    *,
    setting_name: str,
    effective_uid: int | None = None,
) -> str:
    """Decode one runtime-owned UTF-8 secret after hardened byte snapshotting."""

    secret_bytes = read_private_runtime_file_bytes(
        raw_path,
        setting_name=setting_name,
        effective_uid=effective_uid,
    )

    try:
        secret = secret_bytes.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{setting_name} must contain UTF-8 text") from error
    if not secret:
        raise RuntimeError(f"{setting_name} is empty")
    return secret


def _validate_shared_runtime_metadata(
    metadata: os.stat_result,
    *,
    setting_name: str,
    owner_uid: int,
    reader_gid: int,
    required_mode: int,
) -> None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{setting_name} must name a regular non-symlink file")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"{setting_name} must have exactly one hard link")
    if metadata.st_uid != owner_uid or metadata.st_gid != reader_gid:
        raise RuntimeError(f"{setting_name} ownership differs from its shared contract")
    if stat.S_IMODE(metadata.st_mode) != required_mode:
        raise RuntimeError(f"{setting_name} permissions differ from its shared contract")
    if metadata.st_size > MAX_RUNTIME_SECRET_BYTES:
        raise RuntimeError(f"{setting_name} is too large")


def _shared_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_ctime_ns,
    )


def read_shared_runtime_file_bytes(
    raw_path: str,
    *,
    setting_name: str,
    owner_uid: int,
    reader_gid: int,
    required_mode: int,
) -> bytes:
    """Snapshot one fixed-owner, fixed-group lease through a verified fd."""

    path = Path(raw_path)
    if not path.is_absolute():
        raise RuntimeError(f"{setting_name} must be an absolute path")
    try:
        named_metadata = os.lstat(path)
    except OSError as error:
        raise RuntimeError(
            f"{setting_name} must name an accessible regular non-symlink file"
        ) from error
    _validate_shared_runtime_metadata(
        named_metadata,
        setting_name=setting_name,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        required_mode=required_mode,
    )
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(
            f"{setting_name} must name an accessible regular non-symlink file"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        _validate_shared_runtime_metadata(
            metadata,
            setting_name=setting_name,
            owner_uid=owner_uid,
            reader_gid=reader_gid,
            required_mode=required_mode,
        )
        if (metadata.st_dev, metadata.st_ino) != (
            named_metadata.st_dev,
            named_metadata.st_ino,
        ):
            raise RuntimeError(f"{setting_name} changed during permission preflight")
        before_read = _shared_metadata_identity(metadata)
        chunks: list[bytes] = []
        remaining = MAX_RUNTIME_SECRET_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_RUNTIME_SECRET_BYTES:
            raise RuntimeError(f"{setting_name} is too large")
        if _shared_metadata_identity(os.fstat(descriptor)) != before_read:
            raise RuntimeError(f"{setting_name} changed while it was read")
        return content
    finally:
        os.close(descriptor)


def environment_secret(
    value_name: str,
    file_name: str,
    *,
    environment: Mapping[str, str] | None = None,
    effective_uid: int | None = None,
) -> str:
    """Load exactly one environment or hardened file-secret source."""

    source = os.environ if environment is None else environment
    value = source.get(value_name, "").strip()
    raw_path = source.get(file_name, "").strip()
    if value and raw_path:
        raise RuntimeError(f"set only one of {value_name} and {file_name}")
    if value:
        return value
    if not raw_path:
        return ""
    return read_runtime_secret_file(
        raw_path,
        setting_name=file_name,
        effective_uid=effective_uid,
    )
