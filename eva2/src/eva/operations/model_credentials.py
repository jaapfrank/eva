"""Publish a refresh-token-free model access lease for Eva's hot path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time


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


def _read_regular(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 65_536:
        raise ModelCredentialLeaseError("model credential source is not a small file")
    raw = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise ModelCredentialLeaseError("model credential source changed while reading")
    return raw


def build_access_lease(source: Path) -> bytes:
    """Strip refresh authority and return one canonical access-only lease."""

    try:
        raw = _read_regular(source)
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelCredentialLeaseError(
            "model credential source is unavailable"
        ) from error
    tokens = value.get("tokens") if isinstance(value, dict) else None
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


def _publish_bytes(lease: bytes, target: Path, *, mode: int = 0o600) -> bool:
    try:
        current = _read_regular(target)
    except (OSError, ModelCredentialLeaseError):
        current = None
    if current == lease:
        return False
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=".model-lease-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(lease)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def publish_access_lease(source: Path, target: Path) -> bool:
    """Atomically publish changed lease bytes; return whether bytes changed."""

    return _publish_bytes(build_access_lease(source), target, mode=0o640)


def publish_codex_consumer_auth(source: Path, target: Path) -> bool:
    """Publish an auto-rotating Codex file with no refresh authority."""

    return _publish_bytes(build_codex_consumer_auth(source), target, mode=0o600)


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
        mode=0o640,
    )


def install_auth_owner(source: Path, target: Path) -> bool:
    """Validate staged login bytes, then atomically replace the live owner."""

    if source.resolve() == target.resolve():
        raise ModelCredentialLeaseError("staged and live model auth paths must differ")
    raw = _read_regular(source)
    build_access_lease(source)
    return _publish_bytes(raw, target)


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
