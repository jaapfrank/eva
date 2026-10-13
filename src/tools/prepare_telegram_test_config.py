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
from dataclasses import dataclass
from typing import Iterable, NoReturn
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
GROUP_CHAT_ID_PATTERN = re.compile(r"-[0-9]{1,30}\Z")
MAX_TRUSTED_TEST_ACCOUNTS = 8


@dataclass(frozen=True, slots=True)
class TelegramTestPrincipal:
    """One explicitly trusted test person, never an implicit group grant."""

    telegram_user_id: int
    principal_id: str

    def __post_init__(self) -> None:
        if self.telegram_user_id < 1:
            raise ValueError("trusted Telegram user ID must be positive")
        if not PRINCIPAL_PATTERN.fullmatch(self.principal_id):
            raise ValueError("trusted principal ID is invalid")


def build_test_binding(
    *,
    owner_user_id: int,
    owner_principal_id: str,
    members: Iterable[TelegramTestPrincipal] = (),
    allowed_group_chat_ids: Iterable[tuple[int, str]] = (),
) -> dict[str, object]:
    """Return closed private and explicitly granted group test bindings.

    ``/link`` adds another Telegram account to one existing person. A second
    household member must receive its own principal here; otherwise a group
    test would quietly test one person twice.
    """

    entries = (
        TelegramTestPrincipal(owner_user_id, owner_principal_id),
        *tuple(members),
    )
    if not 1 <= len(entries) <= MAX_TRUSTED_TEST_ACCOUNTS:
        raise ValueError("test configuration requires 1-8 trusted accounts")
    user_ids = [entry.telegram_user_id for entry in entries]
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("trusted Telegram user IDs must be unique")
    principal_ids = [entry.principal_id for entry in entries]
    if len(set(principal_ids)) != len(principal_ids):
        raise ValueError(
            "trusted principals must be unique; use /link for another account of one person"
        )
    groups_by_user: dict[int, set[str]] = {
        entry.telegram_user_id: set() for entry in entries
    }
    for user_id, group_chat_id in allowed_group_chat_ids:
        if user_id not in groups_by_user:
            raise ValueError("a group grant requires a trusted Telegram account")
        if not GROUP_CHAT_ID_PATTERN.fullmatch(group_chat_id):
            raise ValueError("Telegram test group ID is invalid")
        groups_by_user[user_id].add(group_chat_id)
    return {
        "contract": "EvaTelegramPrincipalBindings/v1",
        "bindings": [
            {
                "allowed_chat_ids": [
                    str(entry.telegram_user_id),
                    *sorted(groups_by_user[entry.telegram_user_id]),
                ],
                "principal_id": entry.principal_id,
                "telegram_user_id": entry.telegram_user_id,
            }
            for entry in entries
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


def prepare_test_config(
    *,
    owner_user_id: int,
    owner_principal_id: str,
    token: str,
    members: Iterable[TelegramTestPrincipal] = (),
    allowed_group_chat_ids: Iterable[tuple[int, str]] = (),
) -> dict[str, object]:
    """Write the test-only root exactly once and return a secret-free receipt."""

    if os.geteuid() != 0:
        raise RuntimeError("Telegram test configuration must run as root")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("test BotFather token format is invalid")
    binding = build_test_binding(
        owner_user_id=owner_user_id,
        owner_principal_id=owner_principal_id,
        members=members,
        allowed_group_chat_ids=allowed_group_chat_ids,
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
            (
                json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode(),
            owner=RUNTIME_OWNER,
        )
        _write_private(
            POSTGRES_SECRET_ROOT / "postgres-password",
            f"{database_password}\n".encode(),
            owner=POSTGRES_OWNER,
        )
        _write_private(
            TEST_ROOT / "telegram-test.env",
            "".join(
                f"{key}={value}\n" for key, value in sorted(environment.items())
            ).encode(),
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
        "trusted_account_count": len(binding["bindings"]),
        "next_command": (
            "PYTHONPATH=src:. python3 -m tools.telegram_test_launch "
            f"--env-file {TEST_ROOT / 'telegram-test.env'}"
        ),
        "nonclaims": [
            "No Telegram API call, container, database, or poller was started.",
            "The test bot token and generated passwords are never printed.",
            "Only named test people are statically trusted; /link adds another account to an existing person, not another person.",
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
    parser.add_argument(
        "--member",
        action="append",
        default=[],
        metavar="TELEGRAM_USER_ID=PRINCIPAL_ID",
        help="separate test person; repeat for up to seven additional people",
    )
    parser.add_argument(
        "--allow-group-for",
        action="append",
        default=[],
        metavar="TELEGRAM_USER_ID=GROUP_CHAT_ID",
        help="explicitly allow one known negative Telegram group ID for a named test person",
    )
    return parser


def _parse_member(value: str) -> TelegramTestPrincipal:
    user_id_raw, separator, principal_id = value.partition("=")
    if not separator or not user_id_raw.isdecimal() or not principal_id:
        raise ValueError("--member must be TELEGRAM_USER_ID=PRINCIPAL_ID")
    return TelegramTestPrincipal(int(user_id_raw), principal_id)


def _parse_group_grant(value: str) -> tuple[int, str]:
    user_id_raw, separator, group_chat_id = value.partition("=")
    if not separator or not user_id_raw.isdecimal() or not group_chat_id:
        raise ValueError("--allow-group-for must be TELEGRAM_USER_ID=GROUP_CHAT_ID")
    return int(user_id_raw), group_chat_id


def main() -> int:
    arguments = _parser().parse_args()
    try:
        token = getpass.getpass("BotFather testbot token (not shown): ").strip()
        receipt = prepare_test_config(
            owner_user_id=arguments.owner_telegram_user_id,
            owner_principal_id=arguments.owner_principal_id,
            token=token,
            members=tuple(_parse_member(value) for value in arguments.member),
            allowed_group_chat_ids=tuple(
                _parse_group_grant(value) for value in arguments.allow_group_for
            ),
        )
    except (RuntimeError, ValueError) as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-config: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
