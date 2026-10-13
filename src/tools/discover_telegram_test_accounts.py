#!/usr/bin/env python3
"""Print private Telegram test-account IDs from a fresh, disposable testbot.

First send ``/start`` to that bot from every intended test account. Then run
this command on any machine with Python and paste the *testbot* token only at
the hidden prompt. It reads Telegram's pending updates once and prints only
positive numeric IDs. It never starts Eva, writes a secret, creates a group or
sends a Telegram message.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import re
from typing import Iterable
from urllib import request as urllib_request


TOKEN_PATTERN = re.compile(r"[0-9]{6,20}:[A-Za-z0-9_-]{20,}\Z")
MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class TelegramTestAccount:
    """One explicit private test account; names and message text stay omitted."""

    user_id: int

    def __post_init__(self) -> None:
        if self.user_id < 1:
            raise ValueError("Telegram test account ID must be positive")


def extract_private_accounts(raw: bytes) -> tuple[TelegramTestAccount, ...]:
    """Return de-duplicated private senders from a bounded Bot API response."""

    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("testbot update response exceeds its byte limit")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("testbot update response is invalid JSON") from error
    if (
        not isinstance(decoded, dict)
        or decoded.get("ok") is not True
        or not isinstance(decoded.get("result"), list)
    ):
        raise RuntimeError("testbot update response differs")
    found: set[int] = set()
    for update in decoded["result"]:
        if not isinstance(update, dict):
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            continue
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        if (
            chat.get("type") != "private"
            or not isinstance(chat_id, int)
            or not isinstance(sender_id, int)
            or sender_id < 1
            or chat_id != sender_id
        ):
            continue
        found.add(sender_id)
    if not found:
        raise RuntimeError("no private /start sender found for the disposable testbot")
    return tuple(TelegramTestAccount(user_id) for user_id in sorted(found))


def discover_private_accounts(*, token: str) -> tuple[TelegramTestAccount, ...]:
    """Fetch updates with a direct fixed HTTPS request and redact failures."""

    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("test BotFather token format is invalid")
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
        raise RuntimeError("could not read disposable testbot updates") from error
    return extract_private_accounts(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minimum", type=int, default=2, help="minimum number of accounts required"
    )
    return parser


def _render_accounts(accounts: Iterable[TelegramTestAccount]) -> str:
    return "\n".join(f"  {account.user_id}" for account in accounts)


def main() -> None:
    args = _parser().parse_args()
    if args.minimum < 1 or args.minimum > 8:
        raise SystemExit("--minimum must be between 1 and 8")
    token = getpass.getpass("BotFather testbot token (hidden): ").strip()
    accounts = discover_private_accounts(token=token)
    if len(accounts) < args.minimum:
        raise SystemExit(
            f"found {len(accounts)} private test account(s), expected at least {args.minimum}"
        )
    print(f"Private test-account IDs ({len(accounts)}):")
    print(_render_accounts(accounts))
    print("No Eva service, secret file, group, or Telegram message was created.")


if __name__ == "__main__":
    main()
