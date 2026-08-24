from __future__ import annotations

from pathlib import Path

import yaml


STACK = Path("eva2/deploy/living-core/stack.compose.yml")


def _volume(service: dict[str, object], target: str) -> str:
    volumes = service.get("volumes")
    assert isinstance(volumes, list)
    matches = [
        item for item in volumes if isinstance(item, str) and f":{target}:" in item
    ]
    assert len(matches) == 1
    return matches[0]


def _source(binding: str) -> str:
    return binding.rsplit(":", 2)[0]


def test_stack_has_one_external_refresh_owner_and_access_only_consumers() -> None:
    stack = yaml.safe_load(STACK.read_bytes())
    services = stack["services"]
    hindsight = services["hindsight"]
    publisher = services["model-access-lease"]
    hindsight_publisher = services["hindsight-access-lease"]
    core = services["core"]

    hindsight_consumer = _volume(hindsight, "/home/hindsight/.codex")
    publisher_source = _volume(publisher, "/run/eva/codex-owner")
    publisher_target = _volume(publisher, "/run/eva/model-lease")
    hindsight_publisher_source = _volume(
        hindsight_publisher, "/run/eva/codex-owner"
    )
    hindsight_publisher_target = _volume(
        hindsight_publisher, "/run/eva/hindsight-access"
    )
    core_lease = _volume(core, "/run/eva/model-lease")

    assert hindsight_consumer.endswith(":/home/hindsight/.codex:ro")
    assert publisher_source.endswith(":/run/eva/codex-owner:ro")
    assert hindsight_publisher_source.endswith(":/run/eva/codex-owner:ro")
    assert publisher_target.endswith(":/run/eva/model-lease:rw")
    assert hindsight_publisher_target.endswith(":/run/eva/hindsight-access:rw")
    assert core_lease.endswith(":/run/eva/model-lease:ro")
    assert _source(publisher_source) == _source(hindsight_publisher_source)
    assert _source(publisher_target) == _source(core_lease)
    assert _source(hindsight_publisher_target) == _source(hindsight_consumer)
    assert _source(publisher_source) not in {
        _source(core_lease),
        _source(hindsight_consumer),
    }
    assert _source(core_lease) != _source(hindsight_consumer)
    assert core["environment"]["EVA_CONVERSATION_AUTH_FILE"] == (
        "/run/eva/model-lease/auth.json"
    )
    assert publisher["healthcheck"] == {"disable": True}
    assert hindsight_publisher["healthcheck"] == {"disable": True}
    assert "codex-consumer" in hindsight_publisher["command"]
    assert hindsight["depends_on"]["hindsight-access-lease"]["condition"] == (
        "service_started"
    )
