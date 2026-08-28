from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "living-core" / "managed-b.compose.yml"
MANAGEMENT_LABELS = {
    "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
    "io.eva2.configuration-sha256": (
        "${EVA_B_CONFIGURATION_SHA256:?set the sealed B configuration sha256}"
    ),
}


def _manifest() -> dict[str, object]:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _assert_no_docker_secrets(value: object) -> None:
    if isinstance(value, dict):
        assert "secrets" not in value
        for nested in value.values():
            _assert_no_docker_secrets(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_docker_secrets(nested)


def _private_binds(service: dict[str, object]) -> list[dict[str, object]]:
    return [
        volume
        for volume in service.get("volumes", [])
        if isinstance(volume, dict) and volume.get("type") == "bind"
    ]


def test_managed_b_has_exactly_one_telegram_first_service_graph() -> None:
    document = _manifest()
    services = document["services"]

    assert set(services) == {
        "postgres",
        "schema-installer",
        "model-access-lease",
        "hindsight-access-lease",
        "hindsight",
        "core",
        "durable-goal-worker",
        "telegram-ingress",
    }
    assert "profiles" not in services["durable-goal-worker"]
    assert "profiles" not in services["telegram-ingress"]
    assert services["schema-installer"]["profiles"] == ["schema-install"]
    _assert_no_docker_secrets(document)


def test_managed_b_binds_private_inputs_and_pins_all_service_labels() -> None:
    services = _manifest()["services"]

    for service in services.values():
        assert service["labels"] == MANAGEMENT_LABELS
        for volume in _private_binds(service):
            assert volume.get("read_only") is True or volume["target"] in {
                "/run/eva/model-lease",
                "/run/eva/hindsight-access",
            }
            assert volume["bind"] == {"create_host_path": False}

    assert services["core"]["container_name"] == "eva2-living-core-b"
    assert services["core"]["user"] == "65532:65532"
    assert services["postgres"]["environment"]["POSTGRES_INITDB_ARGS"] == (
        "--auth-host=scram-sha-256"
    )
    assert {
        "EVA_DURABLE_GOAL_PROVIDER": (
            "${EVA_DURABLE_GOAL_PROVIDER:?set the exact evidence provider}"
        ),
        "EVA_DURABLE_GOAL_RECIPIENTS": (
            "${EVA_DURABLE_GOAL_RECIPIENTS:?set the durable-goal recipients}"
        ),
        "EVA_FANOUT_ALLOW_INSECURE_HTTP": (
            "${EVA_FANOUT_ALLOW_INSECURE_HTTP:?set the sealed fanout transport policy}"
        ),
        "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON": (
            "${EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON:?set the sealed fanout endpoint map}"
        ),
        "EVA_RUNTIME_PRINCIPAL_ID": (
            "${EVA_RUNTIME_PRINCIPAL_ID:?set the principal identity}"
        ),
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": (
            "${EVA_DASHBOARD_PRINCIPAL_BINDING_ID:?set the dashboard owner binding}"
        ),
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE": "/run/secrets/dashboard_access_token",
        "EVA_IDENTITY_PACK_FILE": "/run/eva/identity/current.json",
        "EVA_IDENTITY_PACK_TRUST_FILE": "/run/eva/identity/trust.json",
    }.items() <= services["core"]["environment"].items()

    core_binds = {volume["target"]: volume for volume in _private_binds(services["core"])}
    assert core_binds["/run/eva/identity/current.json"]["source"] == (
        "${EVA_IDENTITY_PACK_FILE_HOST:?set a verified current IdentityPack file}"
    )
    assert core_binds["/run/eva/identity/trust.json"]["source"] == (
        "${EVA_IDENTITY_PACK_TRUST_FILE_HOST:?set the IdentityPack public trust binding}"
    )


def test_managed_b_dependency_order_and_sealed_volumes() -> None:
    document = _manifest()
    services = document["services"]

    assert services["schema-installer"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"}
    }
    assert services["hindsight"]["depends_on"] == {
        "hindsight-access-lease": {"condition": "service_started"}
    }
    assert services["core"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "hindsight": {"condition": "service_healthy"},
        "model-access-lease": {"condition": "service_started"},
    }
    assert services["durable-goal-worker"]["depends_on"] == {
        "core": {"condition": "service_healthy"}
    }
    assert services["telegram-ingress"]["depends_on"] == {
        "core": {"condition": "service_healthy"}
    }
    assert document["volumes"] == {
        "postgres-data": {
            "name": "${EVA_STACK_POSTGRES_VOLUME:?set the sealed PostgreSQL volume name}",
            "labels": {"eva2.component": "operational-truth"},
        },
        "hindsight-data": {
            "name": "${EVA_STACK_HINDSIGHT_VOLUME:?set the sealed Hindsight volume name}",
            "labels": {"eva2.component": "semantic-memory"},
        },
        "work-data": {
            "name": "${EVA_STACK_WORK_VOLUME:?set the sealed workspace volume name}",
            "labels": {"eva2.component": "bounded-workspace"},
        },
    }
