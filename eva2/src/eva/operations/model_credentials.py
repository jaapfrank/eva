"""Publish a refresh-token-free model access lease for Eva's hot path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time

from eva.bootstrap.secrets import (
    read_private_runtime_file_bytes,
    read_shared_runtime_file_bytes,
)


MODEL_LEASE_OWNER_UID = 1000
EVA_CORE_LEASE_READER_GID = 65532
EVA_CORE_LEASE_DIRECTORY_MODE = 0o2750
EVA_CORE_LEASE_FILE_MODE = 0o640
CODEX_LEASE_READER_GID = 1000
CODEX_LEASE_DIRECTORY_MODE = 0o700
CODEX_LEASE_FILE_MODE = 0o600


class ModelCredentialLeaseError(RuntimeError):
    """The credential authority could not produce a safe access lease."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _read_authority_source(path: Path) -> bytes:
    try:
        return read_private_runtime_file_bytes(
            str(path),
            setting_name="model credential source",
            effective_uid=MODEL_LEASE_OWNER_UID,
        )
    except RuntimeError as error:
        raise ModelCredentialLeaseError("model credential source is unavailable") from error


def _open_lease_directory(
    directory: Path, *, reader_gid: int, required_mode: int
) -> int:
    try:
        named = os.lstat(directory)
    except OSError as error:
        raise ModelCredentialLeaseError("model lease directory is unavailable") from error
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or named.st_uid != MODEL_LEASE_OWNER_UID
        or named.st_gid != reader_gid
        or stat.S_IMODE(named.st_mode) != required_mode
    ):
        raise ModelCredentialLeaseError("model lease directory differs from its contract")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as error:
        raise ModelCredentialLeaseError("model lease directory is unavailable") from error
    opened = os.fstat(descriptor)
    if (
        (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid, opened.st_mode)
        != (named.st_dev, named.st_ino, named.st_uid, named.st_gid, named.st_mode)
    ):
        os.close(descriptor)
        raise ModelCredentialLeaseError("model lease directory changed during preflight")
    return descriptor


def build_access_lease(source: Path) -> bytes:
    """Strip refresh authority and return one canonical access-only lease."""

    try:
        raw = _read_authority_source(source)
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelCredentialLeaseError(
            "model credential source is unavailable"
        ) from error
    # Eva's original Node runtime keeps its OAuth authority flat, whereas the
    # Codex consumer format nests it under ``tokens``.  Both are owner-only
    # sources; the published lease always strips refresh authority.
    tokens = value.get("tokens") if isinstance(value, dict) else None
    if not isinstance(tokens, dict) and isinstance(value, dict):
        tokens = value
    if not isinstance(tokens, dict):
        raise ModelCredentialLeaseError("model credential source has no token set")
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not access_token:
        raise ModelCredentialLeaseError("model access token is missing")
    if not isinstance(account_id, str) or not account_id:
        raise ModelCredentialLeaseError("model account ID is missing")
    return _canonical(
        {
            "access_token": access_token,
            "account_id": account_id,
            "contract": "EvaModelAccessLease/v1",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )


def build_codex_consumer_auth(source: Path) -> bytes:
    """Build the minimal Codex auth shape needed by an access-only consumer."""

    lease = json.loads(build_access_lease(source))
    return _canonical(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": lease["access_token"],
                "account_id": lease["account_id"],
            },
        }
    )


def _publish_bytes(
    lease: bytes,
    target: Path,
    *,
    reader_gid: int,
    directory_mode: int,
    file_mode: int,
) -> bool:
    directory = _open_lease_directory(
        target.parent, reader_gid=reader_gid, required_mode=directory_mode
    )
    temporary_name = f".model-lease-{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    try:
        try:
            current = read_shared_runtime_file_bytes(
                str(target),
                setting_name="model access lease",
                owner_uid=MODEL_LEASE_OWNER_UID,
                reader_gid=reader_gid,
                required_mode=file_mode,
            )
        except RuntimeError:
            current = None
        if current == lease:
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary_name, flags, file_mode, dir_fd=directory)
        os.fchmod(descriptor, file_mode)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != MODEL_LEASE_OWNER_UID
            or metadata.st_gid != reader_gid
            or stat.S_IMODE(metadata.st_mode) != file_mode
        ):
            raise ModelCredentialLeaseError("model lease temp file differs from its contract")
        offset = 0
        while offset < len(lease):
            offset += os.write(descriptor, lease[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory)
    return True


def publish_access_lease(source: Path, target: Path) -> bool:
    """Atomically publish changed lease bytes; return whether bytes changed."""

    return _publish_bytes(
        build_access_lease(source),
        target,
        reader_gid=EVA_CORE_LEASE_READER_GID,
        directory_mode=EVA_CORE_LEASE_DIRECTORY_MODE,
        file_mode=EVA_CORE_LEASE_FILE_MODE,
    )


def publish_codex_consumer_auth(source: Path, target: Path) -> bool:
    """Publish an auto-rotating Codex file with no refresh authority."""

    return _publish_bytes(
        build_codex_consumer_auth(source),
        target,
        reader_gid=CODEX_LEASE_READER_GID,
        directory_mode=CODEX_LEASE_DIRECTORY_MODE,
        file_mode=CODEX_LEASE_FILE_MODE,
    )


def publish_unavailable_lease(target: Path) -> bool:
    """Replace any stale token with a non-secret fail-closed marker."""

    return _publish_bytes(
        _canonical(
            {
                "contract": "EvaModelAccessLeaseUnavailable/v1",
                "state": "source_unavailable",
            }
        ),
        target,
        reader_gid=EVA_CORE_LEASE_READER_GID,
        directory_mode=EVA_CORE_LEASE_DIRECTORY_MODE,
        file_mode=EVA_CORE_LEASE_FILE_MODE,
    )


def install_auth_owner(source: Path, target: Path) -> bool:
    """Validate staged login bytes, then atomically replace the live owner."""

    if source.resolve() == target.resolve():
        raise ModelCredentialLeaseError("staged and live model auth paths must differ")
    raw = _read_authority_source(source)
    build_access_lease(source)
    return _publish_bytes(
        raw,
        target,
        reader_gid=CODEX_LEASE_READER_GID,
        directory_mode=CODEX_LEASE_DIRECTORY_MODE,
        file_mode=CODEX_LEASE_FILE_MODE,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument(
        "--format",
        choices=("eva-lease", "codex-consumer"),
        default="eva-lease",
    )
    parser.add_argument("--install-owner", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if not 0.25 <= arguments.interval_seconds <= 60:
        print("model lease interval must be 0.25..60 seconds", file=sys.stderr)
        return 2
    if arguments.install_owner:
        try:
            install_auth_owner(arguments.source, arguments.target)
        except (OSError, ModelCredentialLeaseError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    while True:
        try:
            if arguments.format == "codex-consumer":
                publish_codex_consumer_auth(arguments.source, arguments.target)
            else:
                publish_access_lease(arguments.source, arguments.target)
        except ModelCredentialLeaseError as error:
            publish_unavailable_lease(arguments.target)
            if arguments.once:
                print(str(error), file=sys.stderr)
                return 2
        if arguments.once:
            return 0
        time.sleep(arguments.interval_seconds)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ModelCredentialLeaseError",
    "build_access_lease",
    "build_codex_consumer_auth",
    "install_auth_owner",
    "publish_access_lease",
    "publish_codex_consumer_auth",
    "publish_unavailable_lease",
]
