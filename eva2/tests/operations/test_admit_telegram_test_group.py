from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

import tools.admit_telegram_test_group as group_admission


def _private_stat() -> os.stat_result:
    return os.stat_result((stat.S_IFREG | 0o600, 0, 0, 1, 65532, 65532, 0, 0, 0, 0))


def _bindings() -> dict[str, object]:
    return {
        "contract": "EvaTelegramPrincipalBindings/v1",
        "bindings": [
            {
                "allowed_chat_ids": ["123"],
                "principal_id": "test:owner",
                "telegram_user_id": 123,
            },
            {
                "allowed_chat_ids": ["456"],
                "principal_id": "test:member",
                "telegram_user_id": 456,
            },
        ],
    }


def test_discovers_exactly_one_marked_group_without_retaining_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "ok": True,
        "result": [
            {"message": {"text": "other", "chat": {"id": -1001, "type": "group"}}},
            {
                "message": {
                    "text": "EVA2-TEST-GROUP-0123456789ab",
                    "chat": {"id": -1002, "type": "supergroup"},
                }
            },
        ],
    }

    class Reply:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def read(self, size):  # type: ignore[no-untyped-def]
            assert size == group_admission.MAX_RESPONSE_BYTES + 1
            return json.dumps(response).encode()

    class Opener:
        def open(self, request, timeout):  # type: ignore[no-untyped-def]
            assert request.full_url.startswith("https://api.telegram.org/bot123456:")
            assert timeout == 10
            return Reply()

    monkeypatch.setattr(
        group_admission.urllib_request, "build_opener", lambda *_: Opener()
    )

    assert (
        group_admission.discover_group_chat_id(
            token="123456:abcdefghijklmnopqrst", marker="EVA2-TEST-GROUP-0123456789ab"
        )
        == "-1002"
    )


@pytest.mark.parametrize(
    "response",
    [
        {"ok": True, "result": []},
        {
            "ok": True,
            "result": [
                {
                    "message": {
                        "text": "EVA2-TEST-GROUP-0123456789ab",
                        "chat": {"id": -1, "type": "group"},
                    }
                },
                {
                    "message": {
                        "text": "EVA2-TEST-GROUP-0123456789ab",
                        "chat": {"id": -2, "type": "group"},
                    }
                },
            ],
        },
    ],
)
def test_group_discovery_rejects_missing_or_ambiguous_marker(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, object]
) -> None:
    class Reply:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def read(self, _size):  # type: ignore[no-untyped-def]
            return json.dumps(response).encode()

    class Opener:
        def open(self, _request, timeout):  # type: ignore[no-untyped-def]
            assert timeout == 10
            return Reply()

    monkeypatch.setattr(
        group_admission.urllib_request, "build_opener", lambda *_: Opener()
    )
    with pytest.raises(RuntimeError, match="marker"):
        group_admission.discover_group_chat_id(
            token="123456:abcdefghijklmnopqrst", marker="EVA2-TEST-GROUP-0123456789ab"
        )


def test_group_admission_adds_only_the_requested_known_people(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bindings_path = tmp_path / "telegram-test-bindings.json"
    bindings_path.write_text(json.dumps(_bindings()), encoding="utf-8")
    monkeypatch.setattr(
        group_admission,
        "_require_private_regular_file",
        lambda *_args, **_kwargs: _private_stat(),
    )
    written: dict[str, object] = {}

    def replace(path: Path, data: bytes, *, source_stat: os.stat_result) -> None:
        assert path == bindings_path
        assert source_stat.st_uid == 65532
        written.update(json.loads(data))

    monkeypatch.setattr(group_admission, "_replace_private_file", replace)

    receipt = group_admission.admit_group(
        bindings_path=bindings_path,
        group_chat_id="-100999",
        trusted_user_ids=(123, 456),
    )

    assert receipt["status"] == "prepared"
    assert receipt["newly_granted_account_count"] == 2
    assert written["bindings"] == [
        {
            "allowed_chat_ids": ["123", "-100999"],
            "principal_id": "test:owner",
            "telegram_user_id": 123,
        },
        {
            "allowed_chat_ids": ["456", "-100999"],
            "principal_id": "test:member",
            "telegram_user_id": 456,
        },
    ]


def test_group_admission_refuses_a_user_not_in_the_initial_private_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bindings_path = tmp_path / "telegram-test-bindings.json"
    bindings_path.write_text(json.dumps(_bindings()), encoding="utf-8")
    monkeypatch.setattr(
        group_admission,
        "_require_private_regular_file",
        lambda *_args, **_kwargs: _private_stat(),
    )

    with pytest.raises(RuntimeError, match="not trusted"):
        group_admission.admit_group(
            bindings_path=bindings_path,
            group_chat_id="-100999",
            trusted_user_ids=(999,),
        )
