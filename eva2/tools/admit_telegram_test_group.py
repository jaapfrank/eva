#!/usr/bin/env python3
"""Admit one marked disposable Telegram group to a prepared test configuration.

Run as root *before* the Telegram test stack is launched.  The command reads
the private testbot token without printing it, asks Telegram once for updates,
finds exactly one group message with the supplied marker, and atomically adds
that exact negative chat ID to the named test people.  It does not start a
container, poller, database migration or conversational core.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import NoReturn
from urllib import request as urllib_request


TEST_ROOT = Path("/run/eva2-test")
SECRETS_ROOT = TEST_ROOT / "secrets"
TOKEN_PATH = SECRETS_ROOT / "telegram-test-bot-token"
BINDINGS_PATH = SECRETS_ROOT / "telegram-test-bindings.json"
RUNTIME_OWNER = 65532
MARKER_PATTERN = re.compile(r"EVA2-TEST-GROUP-[A-Za-z0-9_-]{12,64}\Z")
GROUP_CHAT_ID_PATTERN = re.compile(r"-[0-9]{1,30}\Z")
MAX_RESPONSE_BYTES = 1_048_576


def discover_group_chat_id(*, token: str, marker: str) -> str:
    """Return exactly one marked group ID without retaining message content."""

    if not MARKER_PATTERN.fullmatch(marker):
        raise ValueError(
            "marker must be EVA2-TEST-GROUP- followed by 12-64 safe characters"
        )
    endpoint = f"https://api.telegram.org/bot{token}/getUpdates"
    payload = json.dumps(
        {"allowed_updates": ["message"], "limit": 100, "timeout": 0},
        separators=(",", ":"),
    ).encode("utf-8")
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    request = urllib_request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=10) as response:  # noqa: S310 - fixed Telegram API
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as error:
        raise RuntimeError("could not read the disposable testbot updates") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("testbot update response exceeds its byte limit")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("testbot update response is invalid JSON") from error
    if (
        not isinstance(decoded, dict)
        or decoded.get("ok") is not True
        or not isinstance(decoded.get("result"), list)
    ):
        raise RuntimeError("testbot update response differs")
    matches: set[str] = set()
    for update in decoded["result"]:
        if not isinstance(update, dict):
            continue
        message = update.get("message")
        if not isinstance(message, dict) or message.get("text") != marker:
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("type") not in {
            "group",
            "supergroup",
        }:
            continue
        chat_id = chat.get("id")
        if isinstance(chat_id, int) and GROUP_CHAT_ID_PATTERN.fullmatch(str(chat_id)):
            matches.add(str(chat_id))
    if not matches:
        raise RuntimeError(
            "marked group message was not found; add the bot and send the marker once"
        )
    if len(matches) != 1:
        raise RuntimeError("marker appeared in more than one group; make a new marker")
    return matches.pop()


def admit_group(
    *, bindings_path: Path, group_chat_id: str, trusted_user_ids: tuple[int, ...]
) -> dict[str, object]:
    """Atomically add one group only to already trusted private test accounts."""

    if not GROUP_CHAT_ID_PATTERN.fullmatch(group_chat_id):
        raise ValueError("Telegram test group ID is invalid")
    if not trusted_user_ids or any(user_id < 1 for user_id in trusted_user_ids):
        raise ValueError("at least one positive trusted Telegram user ID is required")
    if len(set(trusted_user_ids)) != len(trusted_user_ids):
        raise ValueError("trusted Telegram user IDs must be unique")
    raw, source_stat = _read_private_file(
        bindings_path, owner=RUNTIME_OWNER, maximum_bytes=65_536
    )
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("test bindings file is unreadable") from error
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"bindings", "contract"}
        or decoded.get("contract") != "EvaTelegramPrincipalBindings/v1"
        or not isinstance(decoded.get("bindings"), list)
    ):
        raise RuntimeError("test bindings contract differs")
    requested = set(trusted_user_ids)
    found: set[int] = set()
    changed = 0
    for binding in decoded["bindings"]:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"allowed_chat_ids", "principal_id", "telegram_user_id"}
            or not isinstance(binding.get("telegram_user_id"), int)
            or not isinstance(binding.get("allowed_chat_ids"), list)
            or not all(isinstance(value, str) for value in binding["allowed_chat_ids"])
        ):
            raise RuntimeError("test binding entry differs")
        user_id = binding["telegram_user_id"]
        if user_id not in requested:
            continue
        found.add(user_id)
        private_chat_id = str(user_id)
        if private_chat_id not in binding["allowed_chat_ids"]:
            raise RuntimeError("test binding lost its own private chat")
        permitted = set(binding["allowed_chat_ids"])
        if group_chat_id not in permitted:
            permitted.add(group_chat_id)
            changed += 1
        # TelegramPrincipalBinding requires the complete set to be in one
        # canonical lexical order.  Keeping the private chat artificially
        # first makes a valid negative group ID fail at ingress startup.  Do
        # this on replay too, so an older prepared file repairs atomically.
        binding["allowed_chat_ids"] = sorted(permitted)
    if found != requested:
        raise RuntimeError("one or more requested test accounts are not trusted")
    rendered = (
        json.dumps(decoded, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    _replace_private_file(bindings_path, rendered, source_stat=source_stat)
    return {
        "contract": "EvaTelegramTestGroupAdmissionReceipt/v1",
        "group_chat_id": group_chat_id,
        "newly_granted_account_count": changed,
        "status": "prepared",
        "trusted_account_count": len(trusted_user_ids),
        "nonclaims": [
            "No Telegram message was sent by this command.",
            "No container, poller, database migration, or conversational core was started.",
            "The marker text and BotFather token were not printed or retained in the receipt.",
        ],
    }


def _read_private_file(
    path: Path, *, owner: int, maximum_bytes: int
) -> tuple[bytes, os.stat_result]:
    """Read the exact verified inode, never a path checked before opening it."""

    if maximum_bytes < 1:
        raise ValueError("private test file byte limit must be positive")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError("required private test file is missing") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RuntimeError("required private test file is not a regular file")
        if (
            details.st_uid != owner
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise RuntimeError("required private test file ownership or mode differs")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum_bytes:
            raise RuntimeError("required private test file exceeds its byte limit")
        return data, details
    finally:
        os.close(descriptor)


def _read_token() -> str:
    raw, _ = _read_private_file(TOKEN_PATH, owner=RUNTIME_OWNER, maximum_bytes=256)
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("testbot token file is unreadable") from error
    if not re.fullmatch(r"[0-9]{6,20}:[A-Za-z0-9_-]{20,}", token):
        raise RuntimeError("testbot token format differs")
    return token


def _replace_private_file(
    path: Path, data: bytes, *, source_stat: os.stat_result
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, source_stat.st_uid, source_stat.st_gid)
        os.chmod(temporary, stat.S_IMODE(source_stat.st_mode))
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--grant-to", required=True, action="append", type=int)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if os.geteuid() != 0:
            raise RuntimeError("Telegram test group admission must run as root")
        group_chat_id = discover_group_chat_id(
            token=_read_token(), marker=arguments.marker
        )
        receipt = admit_group(
            bindings_path=BINDINGS_PATH,
            group_chat_id=group_chat_id,
            trusted_user_ids=tuple(arguments.grant_to),
        )
    except (RuntimeError, ValueError) as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-group: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
