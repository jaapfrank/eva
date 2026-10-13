from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "living-core"


def test_telegram_ingress_binds_each_hardened_file_secret_read_only() -> None:
    document = yaml.safe_load(
        (DEPLOY / "telegram-ingress.compose.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["telegram-ingress"]

    assert "secrets" not in service
    mounts = service["volumes"]
    assert len(mounts) == 4
    assert {mount["target"] for mount in mounts} == {
        "/run/secrets/database_dsn",
        "/run/secrets/telegram_bot_token",
        "/run/secrets/telegram_principal_bindings",
        "/run/secrets/telegram_pairing_secret",
    }
    for mount in mounts:
        assert mount["type"] == "bind"
        assert mount["read_only"] is True
        assert mount["bind"] == {"create_host_path": False}
        assert mount["source"].startswith("${EVA_")


def test_proactive_overlay_reuses_core_without_a_second_poller() -> None:
    document = yaml.safe_load((DEPLOY / "telegram-proactive.compose.yml").read_text(encoding="utf-8"))
    assert set(document) == {"services"}
    assert set(document["services"]) == {"core"}
    core = document["services"]["core"]
    assert "command" not in core and "entrypoint" not in core and "secrets" not in core
    assert core["environment"] == {
        "EVA_TELEGRAM_BOT_TOKEN_FILE": "/run/secrets/telegram_bot_token",
        "EVA_TELEGRAM_PROACTIVE_CHAT_ID": "${EVA_TELEGRAM_PROACTIVE_CHAT_ID:-}",
    }
    mount, = core["volumes"]
    assert mount["type"] == "bind" and mount["read_only"] is True
    assert mount["target"] == "/run/secrets/telegram_bot_token"
    assert mount["bind"] == {"create_host_path": False}
    assert mount["source"].startswith("${EVA_TELEGRAM_BOT_TOKEN_FILE_HOST:?")
