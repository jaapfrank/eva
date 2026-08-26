from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.prepare_telegram_test_config as telegram_config
from tools.prepare_telegram_test_config import (
    TEST_IMAGE,
    build_test_binding,
    build_test_environment,
)


def test_binding_trusts_only_the_owner_private_chat() -> None:
    assert build_test_binding(owner_user_id=123, owner_principal_id="test:owner") == {
        "contract": "EvaTelegramPrincipalBindings/v1",
        "bindings": [
            {
                "allowed_chat_ids": ["123"],
                "principal_id": "test:owner",
                "telegram_user_id": 123,
            }
        ],
    }


@pytest.mark.parametrize("user_id", (0, -1))
def test_binding_rejects_an_invalid_owner_id(user_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_test_binding(owner_user_id=user_id, owner_principal_id="test:owner")


def test_environment_has_closed_nonsecret_test_routes() -> None:
    environment = build_test_environment(owner_user_id=123, image=TEST_IMAGE)

    assert environment["EVA_TEST_LIVING_CORE_IMAGE"] == TEST_IMAGE
    assert environment["EVA_TEST_LIVING_CORE_URL"] == "http://eva-test-core:8080"
    assert environment["EVA_TEST_TELEGRAM_API_BASE_URL"] == "https://api.telegram.org"
    assert all(value.startswith("eva2-telegram-test-") for key, value in environment.items() if key in {
        "EVA_TEST_NETWORK_NAME",
        "EVA_TEST_DATABASE_NETWORK_NAME",
        "EVA_TEST_POSTGRES_VOLUME_NAME",
    })


def test_environment_refuses_a_different_image() -> None:
    with pytest.raises(ValueError, match="checked local image"):
        build_test_environment(owner_user_id=123, image="example@sha256:" + "0" * 64)


def test_preparation_writes_closed_secret_files_without_returning_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_root = tmp_path / "eva2-test"
    monkeypatch.setattr(telegram_config, "TEST_ROOT", test_root)
    monkeypatch.setattr(telegram_config, "RUNTIME_SECRET_ROOT", test_root / "secrets")
    monkeypatch.setattr(
        telegram_config, "POSTGRES_SECRET_ROOT", test_root / "postgres-secrets"
    )
    monkeypatch.setattr(telegram_config.os, "geteuid", lambda: 0)
    monkeypatch.setattr(telegram_config.os, "chown", lambda *_: None)
    monkeypatch.setattr(telegram_config.os, "fchown", lambda *_: None)
    fchmod_calls: list[int] = []

    def remember_fchmod(_descriptor: int, mode: int) -> None:
        fchmod_calls.append(mode)

    monkeypatch.setattr(telegram_config.os, "fchmod", remember_fchmod)
    passwords = iter(("database-password", "pairing-secret"))
    monkeypatch.setattr(telegram_config.secrets, "token_urlsafe", lambda _: next(passwords))

    receipt = telegram_config.prepare_test_config(
        owner_user_id=123,
        owner_principal_id="test:owner",
        token="123456:abcdefghijklmnopqrst",
    )

    assert receipt["status"] == "prepared"
    assert "123456:" not in json.dumps(receipt)
    assert "database-password" not in json.dumps(receipt)
    environment = (test_root / "telegram-test.env").read_text()
    assert "EVA_TEST_LIVING_CORE_IMAGE=" + TEST_IMAGE in environment
    assert "database-password" not in environment
    binding = json.loads((test_root / "secrets" / "telegram-test-bindings.json").read_text())
    assert binding["bindings"][0]["telegram_user_id"] == 123
    assert "database-password" in (test_root / "secrets" / "database-dsn").read_text()
    assert fchmod_calls == [0o600] * 6


def test_preparation_refuses_to_overwrite_an_existing_test_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_root = tmp_path / "eva2-test"
    test_root.mkdir()
    monkeypatch.setattr(telegram_config, "TEST_ROOT", test_root)
    monkeypatch.setattr(telegram_config.os, "geteuid", lambda: 0)

    with pytest.raises(RuntimeError, match="refuse overwrite"):
        telegram_config.prepare_test_config(
            owner_user_id=123,
            owner_principal_id="test:owner",
            token="123456:abcdefghijklmnopqrst",
        )
