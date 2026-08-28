#!/usr/bin/env python3
"""Seal access-only model and identity inputs for the real Telegram test.

Run this once as root after the disposable Telegram support configuration has
been prepared.  The command copies only validated, bounded snapshots into the
test-only runtime directory and refuses to overwrite an earlier preparation.
It never prints token values or source paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import NoReturn
from uuid import uuid4

from eva.application.identity_verifier import (
    parse_identity_pack_trust_binding,
    verify_identity_pack_bytes,
)
from eva.contracts.canonical_json import canonical_json_bytes


CONVERSATION_ROOT = Path("/run/eva2-test/conversation-secrets")
OVERLAY_ENV = Path("/run/eva2-test/telegram-conversation-test.env")
RUNTIME_OWNER = 65532
HINDSIGHT_OWNER = 1000
MAX_INPUT_BYTES = 64 * 1024
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,240}@sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MODEL_KEYS = {"access_token", "account_id", "contract", "source_sha256"}
_HINDSIGHT_KEYS = {"auth_mode", "tokens"}
_TOKEN_KEYS = {"access_token", "account_id"}


def _snapshot_private_file(path: Path, *, label: str) -> bytes:
    if not path.is_absolute():
        raise RuntimeError(f"{label} must use an absolute path")
    try:
        named = os.lstat(path)
    except OSError as error:
        raise RuntimeError(f"{label} is unavailable") from error
    permissions = stat.S_IMODE(named.st_mode)
    if (
        not stat.S_ISREG(named.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or named.st_nlink != 1
        or permissions & (stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO)
        or not permissions & stat.S_IRUSR
        or named.st_size > MAX_INPUT_BYTES
    ):
        raise RuntimeError(f"{label} is not a bounded private regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or opened.st_uid != named.st_uid
            or opened.st_gid != named.st_gid
            or opened.st_mode != named.st_mode
            or opened.st_nlink != 1
            or opened.st_size != named.st_size
            or opened.st_ctime_ns != named.st_ctime_ns
        ):
            raise RuntimeError(f"{label} changed during validation")
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_INPUT_BYTES:
            raise RuntimeError(f"{label} is too large")
        after = os.fstat(descriptor)
        if (
            after.st_size != opened.st_size
            or after.st_ctime_ns != opened.st_ctime_ns
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RuntimeError(f"{label} changed while it was read")
        return raw
    finally:
        os.close(descriptor)


def _json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain one JSON object")
    return value


def _model_access(raw: bytes) -> bytes:
    value = _json_object(raw, label="conversation model access file")
    if (
        set(value) != _MODEL_KEYS
        or value.get("contract") != "EvaModelAccessLease/v1"
        or not isinstance(value.get("access_token"), str)
        or not value["access_token"]
        or len(value["access_token"]) > 16_384
        or not isinstance(value.get("account_id"), str)
        or not value["account_id"]
        or len(value["account_id"]) > 512
        or not isinstance(value.get("source_sha256"), str)
        or _SHA256.fullmatch(value["source_sha256"]) is None
    ):
        raise RuntimeError("conversation model access file is not access-only")
    return canonical_json_bytes(value)


def _hindsight_access(raw: bytes) -> bytes:
    value = _json_object(raw, label="Hindsight access file")
    tokens = value.get("tokens")
    if (
        set(value) != _HINDSIGHT_KEYS
        or value.get("auth_mode") != "chatgpt"
        or not isinstance(tokens, dict)
        or set(tokens) != _TOKEN_KEYS
        or not isinstance(tokens.get("access_token"), str)
        or not tokens["access_token"]
        or len(tokens["access_token"]) > 16_384
        or not isinstance(tokens.get("account_id"), str)
        or not tokens["account_id"]
        or len(tokens["account_id"]) > 512
    ):
        raise RuntimeError("Hindsight access file is not access-only")
    return canonical_json_bytes(value)


def _write_private(path: Path, raw: bytes, *, owner: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("could not write sealed Telegram conversation input")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchown(descriptor, owner, owner)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _mkdir_private(path: Path, *, owner: int) -> None:
    path.mkdir(mode=0o700)
    os.chown(path, owner, owner)
    os.chmod(path, 0o700)


def prepare_conversation_inputs(
    *,
    model_access_file: Path,
    hindsight_access_file: Path,
    identity_file: Path,
    identity_trust_file: Path,
    core_image: str,
    hindsight_image: str,
    destination: Path = CONVERSATION_ROOT,
    overlay_env: Path = OVERLAY_ENV,
    runtime_owner: int = RUNTIME_OWNER,
    hindsight_owner: int = HINDSIGHT_OWNER,
    overlay_owner: int = 0,
) -> dict[str, object]:
    """Create one closed snapshot and return only hashes and counts."""

    if destination.exists() or overlay_env.exists():
        raise RuntimeError("Telegram conversation test inputs already exist")
    if _IMAGE.fullmatch(core_image) is None or _IMAGE.fullmatch(hindsight_image) is None:
        raise RuntimeError("conversation test images must use exact sha256 digests")

    model_raw = _model_access(
        _snapshot_private_file(model_access_file, label="conversation model access file")
    )
    hindsight_raw = _hindsight_access(
        _snapshot_private_file(hindsight_access_file, label="Hindsight access file")
    )
    identity_raw = _snapshot_private_file(identity_file, label="IdentityPack file")
    trust_raw = _snapshot_private_file(
        identity_trust_file, label="IdentityPack trust file"
    )
    try:
        trust = parse_identity_pack_trust_binding(trust_raw)
        identity = verify_identity_pack_bytes(identity_raw, trust=trust)
    except ValueError as error:
        raise RuntimeError("signed IdentityPack binding is invalid") from error

    if not destination.parent.is_dir():
        raise RuntimeError("Telegram test root is unavailable")
    try:
        _mkdir_private(destination, owner=runtime_owner)
        hindsight_directory = destination / "hindsight-access"
        _mkdir_private(hindsight_directory, owner=hindsight_owner)
        _write_private(destination / "model-access.json", model_raw, owner=runtime_owner)
        _write_private(destination / "identity.json", identity_raw, owner=runtime_owner)
        _write_private(destination / "identity-trust.json", trust_raw, owner=runtime_owner)
        _write_private(
            hindsight_directory / "auth.json", hindsight_raw, owner=hindsight_owner
        )
        volume = f"eva2-telegram-test-conversation-hindsight-{uuid4().hex[:16]}"
        environment = {
            "EVA_TEST_CONVERSATION_CORE_IMAGE": core_image,
            "EVA_TEST_CONVERSATION_HINDSIGHT_VOLUME_NAME": volume,
            "EVA_TEST_CONVERSATION_MODEL_ACCESS_FILE_HOST": str(
                destination / "model-access.json"
            ),
            "EVA_TEST_HINDSIGHT_ACCESS_DIR_HOST": str(hindsight_directory),
            "EVA_TEST_HINDSIGHT_IMAGE": hindsight_image,
            "EVA_TEST_IDENTITY_PACK_FILE_HOST": str(destination / "identity.json"),
            "EVA_TEST_IDENTITY_TRUST_FILE_HOST": str(
                destination / "identity-trust.json"
            ),
        }
        payload = "".join(
            f"{key}={value}\n" for key, value in sorted(environment.items())
        ).encode()
        _write_private(overlay_env, payload, owner=overlay_owner)
    except BaseException:
        # A partial private root is deliberately retained for explicit operator
        # inspection. Guessing cleanup after a failed secret copy is unsafe.
        raise

    return {
        "contract": "EvaTelegramConversationTestPreparationReceipt/v1",
        "identity_id": identity.identity_id,
        "input_hashes": {
            "model_access": hashlib.sha256(model_raw).hexdigest(),
            "hindsight_access": hashlib.sha256(hindsight_raw).hexdigest(),
            "identity": hashlib.sha256(identity_raw).hexdigest(),
            "identity_trust": hashlib.sha256(trust_raw).hexdigest(),
        },
        "refresh_token_present": False,
        "sealed_file_count": 5,
        "status": "prepared",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-access-file", required=True, type=Path)
    parser.add_argument("--hindsight-access-file", required=True, type=Path)
    parser.add_argument("--identity-file", required=True, type=Path)
    parser.add_argument("--identity-trust-file", required=True, type=Path)
    parser.add_argument("--core-image", required=True)
    parser.add_argument("--hindsight-image", required=True)
    return parser


def main() -> int:
    if os.geteuid() != 0:
        _die("must run as root")
    arguments = _parser().parse_args()
    try:
        receipt = prepare_conversation_inputs(
            model_access_file=arguments.model_access_file,
            hindsight_access_file=arguments.hindsight_access_file,
            identity_file=arguments.identity_file,
            identity_trust_file=arguments.identity_trust_file,
            core_image=arguments.core_image,
            hindsight_image=arguments.hindsight_image,
        )
    except (OSError, RuntimeError) as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-conversation-test-prepare: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
