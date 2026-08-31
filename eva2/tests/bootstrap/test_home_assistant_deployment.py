from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "living-core"


def test_home_assistant_override_uses_only_the_file_secret_route() -> None:
    document = yaml.safe_load(
        (DEPLOY / "home-assistant.compose.yml").read_text(encoding="utf-8")
    )
    core = document["services"]["core"]
    environment = core["environment"]

    assert environment["EVA_HOME_ASSISTANT_TOKEN_FILE"] == (
        "/run/secrets/home_assistant_token"
    )
    assert "EVA_HOME_ASSISTANT_TOKEN" not in environment
    assert core["secrets"] == ["home_assistant_token"]
    assert document["secrets"]["home_assistant_token"]["file"].startswith(
        "${EVA_HOME_ASSISTANT_TOKEN_FILE_HOST:"
    )


def test_core_b_preflight_has_no_apply_or_recreate_path() -> None:
    script = (DEPLOY / "preflight-core-b.sh").read_text(encoding="utf-8")

    assert "exec env -i" in script
    assert "HOME=" not in script
    assert "--apply" not in script
    assert "compose up" not in script
    assert "force-recreate" not in script
    assert "docker inspect" not in script
    assert "docker cp" not in script


def test_secret_boundary_inventory_keeps_unhardened_processes_as_blockers() -> None:
    inventory = (DEPLOY / "SECRET_BOUNDARY_INVENTORY.md").read_text(encoding="utf-8")

    for loose_reader in (
        "operations/install_schema.py",
        "operations/model_credentials.py",
        "adapters/model/pydantic_conversation.py",
    ):
        assert loose_reader in inventory
    for hardened_reader in (
        "operations/durable_goal_worker.py",
        "bootstrap/telegram_ingress.py",
    ):
        assert hardened_reader not in inventory.split(
            "## Niet door deze vertical gedekt en dus deploymentblocker", 1
        )[1]
    assert "deploymentblocker" in inventory
