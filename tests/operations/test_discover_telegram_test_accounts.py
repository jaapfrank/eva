from __future__ import annotations

import json

import pytest

from tools.discover_telegram_test_accounts import extract_private_accounts


def _response(*updates: object) -> bytes:
    return json.dumps({"ok": True, "result": list(updates)}).encode("utf-8")


def test_extracts_only_deduplicated_private_self_chat_senders() -> None:
    raw = _response(
        {
            "message": {
                "chat": {"id": 44, "type": "private"},
                "from": {"id": 44, "first_name": "A"},
                "text": "/start",
            }
        },
        {
            "message": {
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 22, "first_name": "B"},
                "text": "/start",
            }
        },
        {
            "message": {
                "chat": {"id": 44, "type": "private"},
                "from": {"id": 44},
                "text": "again",
            }
        },
    )

    assert [account.user_id for account in extract_private_accounts(raw)] == [22, 44]


@pytest.mark.parametrize(
    "update",
    [
        {"message": {"chat": {"id": -1001, "type": "group"}, "from": {"id": 22}}},
        {"message": {"chat": {"id": 22, "type": "private"}, "from": {"id": 44}}},
        {"message": {"chat": {"id": 0, "type": "private"}, "from": {"id": 0}}},
        {"edited_message": {"chat": {"id": 22, "type": "private"}}},
    ],
)
def test_ignores_non_private_or_ambiguous_updates(update: object) -> None:
    with pytest.raises(RuntimeError, match="no private"):
        extract_private_accounts(_response(update))


@pytest.mark.parametrize(
    "raw",
    [b"{}", b'{"ok":false,"result":[]}', b'{"ok":true,"result":{}}', b"not-json"],
)
def test_rejects_an_invalid_bot_api_shape(raw: bytes) -> None:
    with pytest.raises(RuntimeError, match="response"):
        extract_private_accounts(raw)
