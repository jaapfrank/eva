from __future__ import annotations

import json
from pathlib import Path

from eva.adapters.model.conversation import CodexConversationModel


def test_conversation_loads_only_the_expected_shared_lease_contract(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def read_lease(path: str, **kwargs: object) -> bytes:
        observed["path"] = path
        observed.update(kwargs)
        return json.dumps(
            {
                "access_token": "access-token",
                "account_id": "account-id",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "a" * 64,
            }
        ).encode("utf-8")

    monkeypatch.setattr(
        "eva.adapters.model.conversation.read_shared_runtime_file_bytes", read_lease
    )
    model = CodexConversationModel(auth_file=Path("/run/eva/model-lease/auth.json"))

    assert model._load_auth() == ("access-token", "account-id")
    assert observed == {
        "path": "/run/eva/model-lease/auth.json",
        "setting_name": "conversation auth lease",
        "owner_uid": 1000,
        "reader_gid": 65532,
        "required_mode": 0o640,
    }
