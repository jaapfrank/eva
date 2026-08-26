#!/usr/bin/env python3
"""Create one private, disposable configuration for the real Telegram test.

Run this once as root on the Docker host.  It asks for a *test* BotFather token
without echoing it, creates all five secret files with their container owners,
and writes a sealed test env file.  It refuses to overwrite a previous test.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
from typing import NoReturn
from urllib.parse import quote
from uuid import uuid4


TEST_ROOT = Path("/run/eva2-test")
RUNTIME_SECRET_ROOT = TEST_ROOT / "secrets"
POSTGRES_SECRET_ROOT = TEST_ROOT / "postgres-secrets"
RUNTIME_OWNER = 65532
POSTGRES_OWNER = 999
TEST_IMAGE = (
    "eva2-telegram-test@sha256:"
    "12cd4341765796f92c1279ba79017d9b164fed5510ef9d11baceb87e0dc4c621"
)
POSTGRES_IMAGE = (
    "docker.io/library/postgres@sha256:"
    "472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
TOKEN_PATTERN = re.compile(r"[0-9]{6,20}:[A-Za-z0-9_-]{20,}\Z")
PRINCIPAL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")


def build_test_binding(*, owner_user_id: int, owner_principal_id: str) -> dict[str, object]:
    """Return the sole initial binding; every other person must pair in chat."""

    if owner_user_id < 1:
        raise ValueError("owner Telegram user ID must be positive")
    if not PRINCIPAL_PATTERN.fullmatch(owner_principal_id):
        raise ValueError("owner principal ID is invalid")
    return {
        "contract": "EvaTelegramPrincipalBindings/v1",
        "bindings": [
            {
                "allowed_chat_ids": [str(owner_user_id)],
                "principal_id": owner_principal_id,
                "telegram_user_id": owner_user_id,
            }
        ],
    }


def build_test_environment(*, owner_user_id: int, image: str) -> dict[str, str]:
    """Make one closed, random-named test graph without embedding secrets."""

    if image != TEST_IMAGE:
        raise ValueError("Telegram test image differs from the checked local image")
    if owner_user_id < 1:
        raise ValueError("owner Telegram user ID must be positive")
    suffix = uuid4().hex[:16]
    tenant_id = str(uuid4())
    installation_id = str(uuid4())
    return {
        "EVA_TEST_DATABASE_DSN_FILE_HOST": str(RUNTIME_SECRET_ROOT / "database-dsn"),
        "EVA_TEST_DATABASE_NETWORK_NAME": f"eva2-telegram-test-db-{suffix}",
        "EVA_TEST_DOCKER_BINARY": "docker",
        "EVA_TEST_LIVING_CORE_IMAGE": image,
        "EVA_TEST_LIVING_CORE_URL": "http://eva-test-core:8080",
        "EVA_TEST_NETWORK_NAME": f"eva2-telegram-test-{suffix}",
        "EVA_TEST_POSTGRES_IMAGE": POSTGRES_IMAGE,
        "EVA_TEST_POSTGRES_PASSWORD_FILE_HOST": str(
            POSTGRES_SECRET_ROOT / "postgres-password"
        ),
        "EVA_TEST_POSTGRES_VOLUME_NAME": f"eva2-telegram-test-pg-{suffix}",
        "EVA_TEST_RUNTIME_INSTALLATION_ID": installation_id,
        "EVA_TEST_RUNTIME_TENANT_ID": tenant_id,
        "EVA_TEST_TELEGRAM_API_BASE_URL": "https://api.telegram.org",
        "EVA_TEST_TELEGRAM_BOT_TOKEN_FILE_HOST": str(
            RUNTIME_SECRET_ROOT / "telegram-test-bot-token"
        ),
        "EVA_TEST_TELEGRAM_PAIRING_SECRET_FILE_HOST": str(
            RUNTIME_SECRET_ROOT / "telegram-test-pairing-secret"
        ),
        "EVA_TEST_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": str(
            RUNTIME_SECRET_ROOT / "telegram-test-bindings.json"
        ),
    }


def prepare_test_config(*, owner_user_id: int, owner_principal_id: str, token: str) -> dict[str, object]:
    """Write the test-only root exactly once and return a secret-free receipt."""

    if os.geteuid() != 0:
        raise RuntimeError("Telegram test configuration must run as root")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("test BotFather token format is invalid")
    binding = build_test_binding(
        owner_user_id=owner_user_id, owner_principal_id=owner_principal_id
    )
    environment = build_test_environment(owner_user_id=owner_user_id, image=TEST_IMAGE)
    if TEST_ROOT.exists():
        raise RuntimeError("Telegram test root already exists; refuse overwrite")
    database_password = secrets.token_urlsafe(32)
    pairing_secret = secrets.token_urlsafe(32)
    try:
        _mkdir(TEST_ROOT, owner=0)
        _mkdir(RUNTIME_SECRET_ROOT, owner=RUNTIME_OWNER)
        _mkdir(POSTGRES_SECRET_ROOT, owner=POSTGRES_OWNER)
        _write_private(
            RUNTIME_SECRET_ROOT / "database-dsn",
            (
                "postgresql+psycopg://postgres:"
                f"{quote(database_password, safe='')}@postgres-test:5432/eva2test\n"
            ).encode(),
            owner=RUNTIME_OWNER,
        )
        _write_private(
            RUNTIME_SECRET_ROOT / "telegram-test-bot-token",
            f"{token}\n".encode(),
            owner=RUNTIME_OWNER,
        )
        _write_private(
            RUNTIME_SECRET_ROOT / "telegram-test-pairing-secret",
            f"{pairing_secret}\n".encode(),
            owner=RUNTIME_OWNER,
        )
        _write_private(
            RUNTIME_SECRET_ROOT / "telegram-test-bindings.json",
            (json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            owner=RUNTIME_OWNER,
        )
        _write_private(
            POSTGRES_SECRET_ROOT / "postgres-password",
            f"{database_password}\n".encode(),
            owner=POSTGRES_OWNER,
        )
        _write_private(
            TEST_ROOT / "telegram-test.env",
            "".join(f"{key}={value}\n" for key, value in sorted(environment.items())).encode(),
            owner=0,
        )
    except BaseException:
        # Never attempt automatic cleanup: a partly written secret root should
        # be inspected and explicitly removed by the operator, not guessed at.
        raise
    return {
        "contract": "EvaTelegramTestConfigReceipt/v1",
        "configuration_file": str(TEST_ROOT / "telegram-test.env"),
        "owner_telegram_user_id": owner_user_id,
        "status": "prepared",
        "next_command": (
            "PYTHONPATH=src:. python3 -m eva.operations.telegram_test_launch "
            f"--env-file {TEST_ROOT / 'telegram-test.env'}"
        ),
        "nonclaims": [
            "No Telegram API call, container, database, or poller was started.",
            "The test bot token and generated passwords are never printed.",
            "Only the owner is statically trusted; a second account must pair through Telegram.",
        ],
    }


def _mkdir(path: Path, *, owner: int) -> None:
    path.mkdir(mode=0o700)
    os.chown(path, owner, owner)
    os.chmod(path, 0o700)


def _write_private(path: Path, data: bytes, *, owner: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError(f"could not write private test file {path}")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchown(descriptor, owner, owner)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-telegram-user-id", required=True, type=int)
    parser.add_argument("--owner-principal-id", default="test:owner")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        token = getpass.getpass("BotFather testbot token (not shown): ").strip()
        receipt = prepare_test_config(
            owner_user_id=arguments.owner_telegram_user_id,
            owner_principal_id=arguments.owner_principal_id,
            token=token,
        )
    except (RuntimeError, ValueError) as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-config: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
