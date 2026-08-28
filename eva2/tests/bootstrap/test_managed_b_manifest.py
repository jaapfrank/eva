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
        "hindsight-postgres",
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

    assert services["core"]["container_name"] == (
        "${EVA_STACK_CORE_CONTAINER:?set the sealed core container name}"
    )
    assert services["core"]["user"] == "65532:65532"
    assert services["postgres"]["environment"]["POSTGRES_INITDB_ARGS"] == (
        "--auth-host=scram-sha-256"
    )
    assert services["hindsight-postgres"]["environment"][
        "POSTGRES_INITDB_ARGS"
    ] == "--auth-host=scram-sha-256"
    assert services["postgres"]["pull_policy"] == "never"
    assert services["hindsight-postgres"]["pull_policy"] == "never"
    assert services["hindsight"]["pull_policy"] == "never"
    assert services["hindsight"]["user"] == "1000:1000"
    assert services["hindsight"]["read_only"] is True
    assert services["hindsight"]["cap_drop"] == ["ALL"]
    assert services["hindsight"]["environment"][
        "HINDSIGHT_API_DATABASE_URL_FILE"
    ] == "/run/secrets/hindsight_database_dsn"
    assert services["hindsight"]["environment"][
        "HINDSIGHT_API_EMBEDDINGS_PROVIDER"
    ] == "local"
    assert (
        services["hindsight"]["environment"][
            "HINDSIGHT_API_SKIP_LLM_VERIFICATION"
        ]
        == "true"
    )
    assert {
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "TZ": "UTC",
        "TRANSFORMERS_OFFLINE": "1",
    }.items() <= services["hindsight"]["environment"].items()
    for service_name in (
        "schema-installer",
        "model-access-lease",
        "hindsight-access-lease",
        "core",
        "durable-goal-worker",
        "telegram-ingress",
    ):
        assert services[service_name]["entrypoint"] == ["/usr/local/bin/python"]
    assert services["schema-installer"]["command"] == [
        "-m",
        "eva.operations.install_schema",
    ]
    assert services["core"]["command"] == [
        "-m",
        "uvicorn",
        "eva.bootstrap.runtime:create_runtime_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--no-access-log",
    ]
    assert {
        "EVA_DURABLE_GOAL_PROVIDER": (
            "${EVA_DURABLE_GOAL_ADMISSION_PROVIDER:-}"
        ),
        "EVA_DURABLE_GOAL_RECIPIENTS": (
            "${EVA_DURABLE_GOAL_ADMISSION_RECIPIENTS:-}"
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
        "EVA_DASHBOARD_TENANT_ID": (
            "${EVA_DASHBOARD_TENANT_ID:?set the dashboard tenant scope}"
        ),
        "EVA_DASHBOARD_INSTALLATION_ID": (
            "${EVA_DASHBOARD_INSTALLATION_ID:?set the dashboard installation scope}"
        ),
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": (
            "${EVA_DASHBOARD_PRINCIPAL_BINDING_ID:?set the dashboard owner binding}"
        ),
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE": "/run/secrets/dashboard_access_token",
        "EVA_IDENTITY_PACK_FILE": "/run/eva/identity/current.json",
        "EVA_IDENTITY_PACK_TRUST_FILE": "/run/eva/identity/trust.json",
    }.items() <= services["core"]["environment"].items()
    assert services["durable-goal-worker"]["environment"][
        "EVA_DURABLE_GOAL_PROVIDER"
    ] == "${EVA_DURABLE_GOAL_PROVIDER:?set the exact evidence provider}"

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
        "hindsight-access-lease": {"condition": "service_started"},
        "hindsight-postgres": {"condition": "service_healthy"},
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
        "hindsight-postgres-data": {
            "name": (
                "${EVA_STACK_HINDSIGHT_POSTGRES_VOLUME:?set the sealed "
                "Hindsight PostgreSQL volume name}"
            ),
            "labels": {"eva2.component": "semantic-memory"},
        },
        "work-data": {
            "name": "${EVA_STACK_WORK_VOLUME:?set the sealed workspace volume name}",
            "labels": {"eva2.component": "bounded-workspace"},
        },
    }


def test_managed_b_keeps_hindsight_database_credentials_out_of_environment() -> None:
    services = _manifest()["services"]
    hindsight = services["hindsight"]
    hindsight_database = services["hindsight-postgres"]

    assert "HINDSIGHT_API_DATABASE_URL" not in hindsight["environment"]
    hindsight_binds = {
        volume["target"]: volume for volume in _private_binds(hindsight)
    }
    assert hindsight_binds["/run/secrets/hindsight_database_dsn"] == {
        "type": "bind",
        "source": (
            "${EVA_HINDSIGHT_DATABASE_DSN_FILE_HOST:?set a "
            "Hindsight-runtime-owned PostgreSQL DSN file}"
        ),
        "target": "/run/secrets/hindsight_database_dsn",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    assert hindsight_database["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/eva2/hindsight-postgres-password"
    )
    assert "hindsight-data" not in _manifest()["volumes"]
