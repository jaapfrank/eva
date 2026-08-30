from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE = (
    Path(__file__).resolve().parents[3]
    / "benchmark"
    / "agentbench-fc-v1"
    / "agentbench-dbbench-validation-feedback.compose.yml"
)


def _services() -> dict[str, dict[str, object]]:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert set(document) == {"services", "networks"}
    assert set(document["services"]) == {"controller", "model", "mysql", "worker"}
    return document["services"]


def test_all_images_are_digest_pinned_and_never_pulled() -> None:
    for service in _services().values():
        image = service["image"]
        assert isinstance(image, str)
        assert "@sha256:" in image
        assert service["pull_policy"] == "never"


def test_only_model_has_egress_and_only_controller_has_a_host_port() -> None:
    services = _services()
    assert services["controller"]["networks"] == ["task"]
    assert services["worker"]["networks"] == ["task"]
    assert services["mysql"]["networks"] == ["task"]
    assert services["model"]["networks"] == ["task", "model-egress"]
    assert "ports" in services["controller"]
    assert services["controller"]["ports"][0].startswith("127.0.0.1:")
    assert all("ports" not in services[name] for name in ("worker", "mysql", "model"))


def test_worker_has_no_socket_mount_and_uses_only_the_tool_free_model() -> None:
    worker = _services()["worker"]
    assert "volumes" not in worker
    assert worker["environment"] == {
        "EVA_AGENTBENCH_TOOL_FREE_ENDPOINT": "http://model:8090/v1/generate"
    }
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["security_opt"] == ["no-new-privileges:true"]


def test_only_model_receives_the_access_only_lease() -> None:
    services = _services()
    model_volumes = services["model"]["volumes"]
    assert len(model_volumes) == 1
    assert model_volumes[0]["target"] == "/run/eva/model-lease"
    assert model_volumes[0]["read_only"] is True
    for name in ("controller", "worker", "mysql"):
        assert "/run/eva/model-lease" not in str(services[name])


def test_mysql_is_ephemeral_and_memory_bounded() -> None:
    mysql = _services()["mysql"]
    assert mysql["mem_limit"] == "1g"
    assert any(item.startswith("/var/lib/mysql:") for item in mysql["tmpfs"])
    assert mysql["environment"] == {
        "MYSQL_ROOT_PASSWORD_FILE": "/run/secrets/mysql-root-password"
    }
