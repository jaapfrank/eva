from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.operations.telegram_ingress import load_principal_bindings


def test_binding_file_is_strict_and_person_isolated(private_tmp_path: Path) -> None:
    path = private_tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "contract": "EvaTelegramPrincipalBindings/v1",
                "bindings": [
                    {
                        "allowed_chat_ids": ["101"],
                        "principal_id": "person:jaap",
                        "telegram_user_id": 101,
                    },
                    {
                        "allowed_chat_ids": ["202"],
                        "principal_id": "person:frank",
                        "telegram_user_id": 202,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    bindings = load_principal_bindings(str(path))

    assert bindings.resolve(101, "101") == "person:jaap"
    assert bindings.resolve(202, "202") == "person:frank"
    assert bindings.resolve(101, "202") is None


def test_principal_binding_file_rejects_ambiguous_people(private_tmp_path: Path) -> None:
    path = private_tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "contract": "EvaTelegramPrincipalBindings/v1",
                "bindings": [
                    {
                        "allowed_chat_ids": ["101"],
                        "principal_id": "person:shared",
                        "telegram_user_id": 101,
                    },
                    {
                        "allowed_chat_ids": ["202"],
                        "principal_id": "person:shared",
                        "telegram_user_id": 202,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(ValueError, match="ambiguous"):
        load_principal_bindings(str(path))


def test_principal_binding_file_requires_private_runtime_permissions(
    private_tmp_path: Path,
) -> None:
    path = private_tmp_path / "bindings.json"
    path.write_text(
        '{"contract":"EvaTelegramPrincipalBindings/v1","bindings":[]}',
        encoding="utf-8",
    )
    path.chmod(0o640)

    with pytest.raises(RuntimeError, match="group or other permissions"):
        load_principal_bindings(str(path))
