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
    assert len(mounts) == 3
    assert {mount["target"] for mount in mounts} == {
        "/run/secrets/database_dsn",
        "/run/secrets/telegram_bot_token",
        "/run/secrets/telegram_principal_bindings",
    }
    for mount in mounts:
        assert mount["type"] == "bind"
        assert mount["read_only"] is True
        assert mount["bind"] == {"create_host_path": False}
        assert mount["source"].startswith("${EVA_")
