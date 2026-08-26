from __future__ import annotations

import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
import yaml

from eva.operations import (
    telegram_test_core,
    telegram_test_ingress,
    telegram_test_launch,
    telegram_test_preflight,
)
from eva.operations.telegram_test_preflight import (
    load_telegram_test_plan,
    preflight_telegram_test,
)


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "living-core"


def _test_database_network(plan: object, *, ingress_running: bool = False) -> dict[str, object]:
    assert hasattr(plan, "value")
    containers: dict[str, object] = {
        "test-postgres": {"EndpointID": "postgres-endpoint-id"}
    }
    if ingress_running:
        containers["test-ingress"] = {"EndpointID": "ingress-database-endpoint-id"}
    return {
        "Name": plan.value("EVA_TEST_DATABASE_NETWORK_NAME"),
        "Id": "test-database-network-id",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": True,
        "Labels": {
            "io.eva2.test-database": "telegram-native-test-db-v1",
            "io.eva2.test-run": "telegram-native-test-v1",
        },
        "Containers": containers,
    }


def _test_postgres(plan: object) -> dict[str, object]:
    assert hasattr(plan, "value")
    return {
        "Id": "test-postgres",
        "Config": {
            "Image": plan.value("EVA_TEST_POSTGRES_IMAGE"),
            "Labels": {
                "io.eva2.test-database": "telegram-native-test-db-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
            "Env": [
                "POSTGRES_DB=eva2test",
                "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
                "POSTGRES_PASSWORD_FILE=/run/eva2-test/postgres-password",
                "POSTGRES_USER=postgres",
            ],
        },
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Networks": {
                plan.value("EVA_TEST_DATABASE_NETWORK_NAME"): {
                    "NetworkID": "test-database-network-id",
                    "EndpointID": "postgres-endpoint-id",
                }
            }
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": plan.value("EVA_TEST_POSTGRES_PASSWORD_FILE_HOST"),
                "Destination": "/run/eva2-test/postgres-password",
                "RW": False,
            },
            {
                "Type": "volume",
                "Name": plan.value("EVA_TEST_POSTGRES_VOLUME_NAME"),
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            },
        ],
    }


def _test_core(plan: object, *, container_id: str = "test-core") -> dict[str, object]:
    assert hasattr(plan, "value")
    return {
        "Id": container_id,
        "Config": {
            "Image": plan.value("EVA_TEST_LIVING_CORE_IMAGE"),
            "Cmd": ["python", "-m", "eva.operations.telegram_test_core"],
            "User": "65532:65532",
            "Labels": {
                "io.eva2.test-route": "telegram-native-test-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
        },
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Networks": {
                plan.value("EVA_TEST_NETWORK_NAME"): {
                    "Aliases": ["eva-test-core"],
                    "EndpointID": "test-endpoint-id",
                    "NetworkID": "test-network-id",
                }
            }
        },
    }


def _test_volume(plan: object) -> dict[str, object]:
    assert hasattr(plan, "value")
    return {
        "Name": plan.value("EVA_TEST_POSTGRES_VOLUME_NAME"),
        "Driver": "local",
        "Scope": "local",
        "Labels": {
            "io.eva2.test-database": "telegram-native-test-db-v1",
            "io.eva2.test-run": "telegram-native-test-v1",
        },
    }


def _test_transport_network(
    plan: object, *, container_id: str = "test-core", ingress_running: bool = False
) -> dict[str, object]:
    assert hasattr(plan, "value")
    containers: dict[str, object] = {
        container_id: {"EndpointID": "test-endpoint-id"}
    }
    if ingress_running:
        containers["test-ingress"] = {"EndpointID": "ingress-test-endpoint-id"}
    return {
        "Name": plan.value("EVA_TEST_NETWORK_NAME"),
        "Id": "test-network-id",
        "Driver": "bridge",
        "Scope": "local",
        "Labels": {
            "io.eva2.test-route": "telegram-native-test-v1",
            "io.eva2.test-run": "telegram-native-test-v1",
        },
        "Containers": containers,
    }


def _test_ingress(plan: object) -> dict[str, object]:
    assert hasattr(plan, "value")
    return {
        "Id": "test-ingress",
        "Config": {
            "Image": plan.value("EVA_TEST_LIVING_CORE_IMAGE"),
            "Cmd": ["python", "-m", "eva.operations.telegram_test_ingress"],
            "User": "65532:65532",
            "Labels": {
                "io.eva2.test-route": "telegram-native-test-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
            "Env": [
                f"EVA_LIVING_CORE_URL={plan.value('EVA_TEST_LIVING_CORE_URL')}",
                f"EVA_RUNTIME_INSTALLATION_ID={plan.value('EVA_TEST_RUNTIME_INSTALLATION_ID')}",
                f"EVA_RUNTIME_TENANT_ID={plan.value('EVA_TEST_RUNTIME_TENANT_ID')}",
                f"EVA_TELEGRAM_API_BASE_URL={plan.value('EVA_TEST_TELEGRAM_API_BASE_URL')}",
                "EVA_DATABASE_DSN_FILE=/run/secrets/database_dsn",
                "EVA_TELEGRAM_BOT_TOKEN_FILE=/run/secrets/telegram_bot_token",
                "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE=/run/secrets/telegram_principal_bindings",
                "EVA_TELEGRAM_PAIRING_SECRET_FILE=/run/secrets/telegram_pairing_secret",
            ],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {
                plan.value("EVA_TEST_NETWORK_NAME"): {
                    "NetworkID": "test-network-id",
                    "EndpointID": "ingress-test-endpoint-id",
                },
                plan.value("EVA_TEST_DATABASE_NETWORK_NAME"): {
                    "NetworkID": "test-database-network-id",
                    "EndpointID": "ingress-database-endpoint-id",
                },
            }
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": plan.value("EVA_TEST_DATABASE_DSN_FILE_HOST"),
                "Destination": "/run/secrets/database_dsn",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": plan.value("EVA_TEST_TELEGRAM_BOT_TOKEN_FILE_HOST"),
                "Destination": "/run/secrets/telegram_bot_token",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": plan.value("EVA_TEST_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST"),
                "Destination": "/run/secrets/telegram_principal_bindings",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": plan.value("EVA_TEST_TELEGRAM_PAIRING_SECRET_FILE_HOST"),
                "Destination": "/run/secrets/telegram_pairing_secret",
                "RW": False,
            },
        ],
    }


class _DsnPlan:
    def __init__(self, path: Path) -> None:
        self._path = path

    def value(self, name: str) -> str:
        assert name == "EVA_TEST_DATABASE_DSN_FILE_HOST"
        return str(self._path)


def test_telegram_test_ingress_requires_test_namespaced_inputs() -> None:
    document = yaml.safe_load(
        (DEPLOY / "telegram-test-ingress.compose.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["telegram-ingress-test"]

    assert service["profiles"] == ["telegram-native-test"]
    assert set(service) == {
        "cap_drop",
        "command",
        "environment",
        "image",
        "labels",
        "networks",
        "profiles",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "user",
        "volumes",
    }
    assert service["restart"] == "no"
    assert service["read_only"] is True
    assert service["user"] == "65532:65532"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["command"] == ["python", "-m", "eva.operations.telegram_test_ingress"]
    assert service["image"].startswith("${EVA_TEST_")
    assert service["environment"]["EVA_LIVING_CORE_URL"].startswith("${EVA_TEST_")
    assert service["environment"]["EVA_RUNTIME_INSTALLATION_ID"].startswith(
        "${EVA_TEST_"
    )
    assert service["environment"]["EVA_RUNTIME_TENANT_ID"].startswith("${EVA_TEST_")
    assert service["environment"]["EVA_TELEGRAM_API_BASE_URL"].startswith(
        "${EVA_TEST_"
    )
    assert "env_file" not in service
    assert "secrets" not in service
    assert "configs" not in service
    assert "network_mode" not in service
    assert "privileged" not in service
    assert service["networks"] == ["telegram-test", "telegram-test-db"]
    assert service["labels"] == {
        "io.eva2.test-route": "telegram-native-test-v1",
        "io.eva2.test-run": "telegram-native-test-v1",
    }
    assert len(service["volumes"]) == 4
    for mount in service["volumes"]:
        assert mount["read_only"] is True
        assert mount["bind"] == {"create_host_path": False}
        assert mount["source"].startswith("${EVA_TEST_")
    assert document["networks"] == {
        "telegram-test": {
            "name": "${EVA_TEST_NETWORK_NAME:?set a labelled external Telegram test network}",
            "external": True,
        },
        "telegram-test-db": {
            "name": "${EVA_TEST_DATABASE_NETWORK_NAME:?set the labelled external Telegram test database network}",
            "external": True,
        },
    }


def test_telegram_test_environment_rejects_managed_paths_and_escape(tmp_path: Path) -> None:
    source = (DEPLOY / "telegram-test.example").read_text(encoding="utf-8")
    environment_file = tmp_path / "telegram-test.env"
    environment_file.write_text(
        source.replace(
            "/run/eva2-test/secrets/database-dsn", "/run/eva2/secrets/database-dsn"
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="EVA_TEST_DATABASE_DSN_FILE_HOST must stay"):
        load_telegram_test_plan(environment_file)
    environment_file.write_text(
        source.replace(
            "/run/eva2-test/secrets/database-dsn",
            "/run/eva2-test/secrets/../../eva2/secrets/database-dsn",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="canonical private test file"):
        load_telegram_test_plan(environment_file)


def test_telegram_test_environment_template_is_closed() -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    assert plan.value("EVA_TEST_LIVING_CORE_URL") == "http://eva-test-core:8080"
    assert plan.value("EVA_TEST_NETWORK_NAME").startswith("eva2-telegram-test-")
    assert "@sha256:" in plan.value("EVA_TEST_LIVING_CORE_IMAGE")
    assert "@sha256:" in plan.value("EVA_TEST_POSTGRES_IMAGE")


def test_test_database_dsn_allows_only_its_single_postgres_service(tmp_path: Path) -> None:
    dsn = tmp_path / "database-dsn"
    plan = _DsnPlan(dsn)
    dsn.write_text(
        "postgresql+psycopg://postgres:private@postgres-test:5432/eva2test\n",
        encoding="utf-8",
    )
    telegram_test_preflight._require_test_database_dsn(plan)  # type: ignore[arg-type]
    for invalid in (
        "postgresql+psycopg://postgres:private@other:5432/eva2test",
        "postgresql+psycopg://postgres:private@postgres-test:5432/other",
        "postgresql+psycopg://postgres:private@postgres-test/eva2test",
        "postgresql+psycopg://postgres:private@postgres-test:5432/eva2test?hostaddr=127.0.0.1",
        "postgresql+psycopg://postgres:private@postgres-test,other:5432/eva2test",
    ):
        dsn.write_text(invalid, encoding="utf-8")
        with pytest.raises(RuntimeError, match="not the isolated test route"):
            telegram_test_preflight._require_test_database_dsn(plan)  # type: ignore[arg-type]
    dsn.write_text("not-a-dsn-secretpassword", encoding="utf-8")
    with pytest.raises(RuntimeError, match="database DSN is invalid"):
        telegram_test_preflight._require_test_database_dsn(plan)  # type: ignore[arg-type]


def test_test_database_volume_rejects_local_bind_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    volume = _test_volume(plan)
    volume["Options"] = {"type": "none", "o": "bind", "device": "/srv/eva-b"}
    monkeypatch.setattr(
        telegram_test_preflight, "_docker_json", lambda command, arguments: [volume]
    )
    with pytest.raises(RuntimeError, match="volume is not isolated"):
        telegram_test_preflight._require_test_database_volume("docker", plan)


def test_telegram_test_support_keeps_database_and_transport_separate() -> None:
    document = yaml.safe_load(
        (DEPLOY / "telegram-test-support.compose.yml").read_text(encoding="utf-8")
    )
    assert set(document["services"]) == {
        "postgres-test",
        "schema-installer-test",
        "telegram-test-core",
    }
    postgres = document["services"]["postgres-test"]
    schema = document["services"]["schema-installer-test"]
    core = document["services"]["telegram-test-core"]
    assert set(postgres) == {
        "environment",
        "healthcheck",
        "image",
        "labels",
        "networks",
        "profiles",
        "pull_policy",
        "restart",
        "security_opt",
        "volumes",
    }
    assert set(schema) == {
        "cap_drop",
        "command",
        "depends_on",
        "environment",
        "image",
        "labels",
        "networks",
        "profiles",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "user",
        "volumes",
    }
    assert set(core) == {
        "cap_drop",
        "command",
        "environment",
        "healthcheck",
        "image",
        "labels",
        "networks",
        "profiles",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "user",
    }
    assert "ports" not in postgres and "privileged" not in postgres
    assert "ports" not in schema and "privileged" not in schema
    assert "ports" not in core and "privileged" not in core
    assert postgres["networks"] == ["telegram-test-db"]
    assert schema["networks"] == ["telegram-test-db"]
    assert core["networks"] == {"telegram-test": {"aliases": ["eva-test-core"]}}
    assert core["command"] == ["python", "-m", "eva.operations.telegram_test_core"]
    assert postgres["labels"] == {
        "io.eva2.test-database": "telegram-native-test-db-v1",
        "io.eva2.test-run": "telegram-native-test-v1",
    }
    assert schema["labels"] == {
        "io.eva2.test-database": "telegram-native-test-db-v1",
        "io.eva2.test-run": "telegram-native-test-v1",
    }
    assert core["labels"] == {
        "io.eva2.test-route": "telegram-native-test-v1",
        "io.eva2.test-run": "telegram-native-test-v1",
    }
    assert document["networks"] == {
        "telegram-test": {
            "name": "${EVA_TEST_NETWORK_NAME:?set the dedicated Telegram test network}",
            "driver": "bridge",
            "labels": {
                "io.eva2.test-route": "telegram-native-test-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
        },
        "telegram-test-db": {
            "name": "${EVA_TEST_DATABASE_NETWORK_NAME:?set the dedicated Telegram test database network}",
            "driver": "bridge",
            "internal": True,
            "labels": {
                "io.eva2.test-database": "telegram-native-test-db-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
        },
    }
    assert document["volumes"] == {
        "postgres-test-data": {
            "name": "${EVA_TEST_POSTGRES_VOLUME_NAME:?set a dedicated Telegram test PostgreSQL volume}",
            "labels": {
                "io.eva2.test-database": "telegram-native-test-db-v1",
                "io.eva2.test-run": "telegram-native-test-v1",
            },
        }
    }


def test_telegram_test_core_returns_only_a_fixed_transport_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVA_TEST_RUNTIME_TENANT_ID", "tenant-test")
    monkeypatch.setenv("EVA_TEST_RUNTIME_INSTALLATION_ID", "installation-test")
    response = telegram_test_core._response(
        {
            "audience": {
                "audience_id": "person:test-owner",
                "kind": "private_person",
                "principal_id": "test-owner",
            },
            "conversation_id": "telegram:123",
            "installation_id": "installation-test",
            "message_id": "telegram:123:5",
            "occurred_at": "2026-08-26T10:00:00Z",
            "principal_id": "test-owner",
            "tenant_id": "tenant-test",
            "text": "Hallo",
        }
    )
    assert response["provider"] == "telegram-transport-test-core"
    assert response["text"] == (
        "Telegram-test ontvangen. Dit controleert de verbinding, niet Eva's gesprek."
    )


def test_telegram_test_environment_rejects_mutable_image(tmp_path: Path) -> None:
    source = (DEPLOY / "telegram-test.example").read_text(encoding="utf-8")
    environment_file = tmp_path / "telegram-test"
    environment_file.write_text(
        source.replace(
            "docker.io/example/eva2@sha256:"
            "0000000000000000000000000000000000000000000000000000000000000000",
            "eva2:latest",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="immutable sha256 digest"):
        load_telegram_test_plan(environment_file)


def test_preflight_requires_one_labelled_test_core(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_preflight,
        "_preflight_secret_file",
        lambda path, name: (1, hash(name)),
    )
    monkeypatch.setattr(
        telegram_test_preflight, "_require_test_database_dsn", lambda plan: None
    )

    def docker_json(command: str, arguments: list[str]) -> object:
        assert command == "docker"
        if arguments[:2] == ["network", "inspect"]:
            if arguments[2] == plan.value("EVA_TEST_DATABASE_NETWORK_NAME"):
                return [_test_database_network(plan)]
            return [_test_transport_network(plan)]
        if arguments == ["container", "inspect", "test-postgres"]:
            return [_test_postgres(plan)]
        if arguments == ["volume", "inspect", plan.value("EVA_TEST_POSTGRES_VOLUME_NAME")]:
            return [_test_volume(plan)]
        assert arguments == ["container", "inspect", "test-core"]
        return [_test_core(plan)]

    monkeypatch.setattr(telegram_test_preflight, "_docker_json", docker_json)
    preflight_telegram_test(plan)


def test_running_ingress_is_bound_to_only_the_two_test_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_preflight,
        "_preflight_secret_file",
        lambda path, name: (1, hash(name)),
    )
    monkeypatch.setattr(
        telegram_test_preflight, "_require_test_database_dsn", lambda plan: None
    )

    def docker_json(command: str, arguments: list[str]) -> object:
        assert command == "docker"
        if arguments[:2] == ["network", "inspect"]:
            if arguments[2] == plan.value("EVA_TEST_DATABASE_NETWORK_NAME"):
                return [_test_database_network(plan, ingress_running=True)]
            return [_test_transport_network(plan, ingress_running=True)]
        if arguments == ["container", "inspect", "test-postgres"]:
            return [_test_postgres(plan)]
        if arguments == ["container", "inspect", "test-core"]:
            return [_test_core(plan)]
        if arguments == ["container", "inspect", "test-ingress"]:
            return [_test_ingress(plan)]
        if arguments == ["volume", "inspect", plan.value("EVA_TEST_POSTGRES_VOLUME_NAME")]:
            return [_test_volume(plan)]
        raise AssertionError(arguments)

    monkeypatch.setattr(telegram_test_preflight, "_docker_json", docker_json)
    preflight_telegram_test(plan, ingress_running=True)


def test_running_ingress_rejects_an_extra_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    ingress = _test_ingress(plan)
    assert isinstance(ingress["NetworkSettings"], dict)
    networks = ingress["NetworkSettings"]["Networks"]
    assert isinstance(networks, dict)
    networks["untrusted"] = {"NetworkID": "other", "EndpointID": "other"}
    test_network = _test_transport_network(plan, ingress_running=True)
    database_network = _test_database_network(plan, ingress_running=True)
    monkeypatch.setattr(
        telegram_test_preflight,
        "_container_inspect",
        lambda command, container_id, description: ingress,
    )
    with pytest.raises(RuntimeError, match="network bindings differ"):
        telegram_test_preflight._require_test_ingress_endpoints(
            "docker", plan, test_network, database_network, "test-core", "test-postgres"
        )


def test_preflight_rejects_managed_or_unlabelled_network_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_preflight,
        "_preflight_secret_file",
        lambda path, name: (1, hash(name)),
    )
    monkeypatch.setattr(
        telegram_test_preflight, "_require_test_database_dsn", lambda plan: None
    )

    def docker_json(command: str, arguments: list[str]) -> object:
        if arguments[:2] == ["network", "inspect"]:
            if arguments[2] == plan.value("EVA_TEST_DATABASE_NETWORK_NAME"):
                return [_test_database_network(plan)]
            return [_test_transport_network(plan, container_id="managed-core")]
        if arguments == ["container", "inspect", "test-postgres"]:
            return [_test_postgres(plan)]
        if arguments == ["volume", "inspect", plan.value("EVA_TEST_POSTGRES_VOLUME_NAME")]:
            return [_test_volume(plan)]
        return [{"Config": {"Labels": {"io.eva2.managed-by": "b"}}}]

    monkeypatch.setattr(telegram_test_preflight, "_docker_json", docker_json)
    with pytest.raises(RuntimeError, match="one fixed test core"):
        preflight_telegram_test(plan)


def test_secret_preflight_rejects_symlink_parent_and_hardlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "secrets"
    real = root / "real"
    root.mkdir()
    real.mkdir()
    secret = real / "token"
    secret.write_text("test", encoding="utf-8")
    secret.chmod(0o600)
    alias = root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(telegram_test_preflight, "TEST_SECRET_ROOT", root)
    monkeypatch.setattr(
        telegram_test_preflight, "_require_secret_root", lambda root, owner_uid: root
    )
    with pytest.raises(RuntimeError, match="parent may not be a symlink"):
        telegram_test_preflight._preflight_secret_file(alias / "token", "token")

    hardlink = root / "hardlink"
    hardlink.hardlink_to(secret)
    original_lstat = telegram_test_preflight.os.lstat

    def lstat_with_runtime_owner(path: str | Path) -> os.stat_result:
        result = original_lstat(path)
        if Path(path) != hardlink:
            return result
        values = list(result)
        values[4] = 65532
        return os.stat_result(values)

    monkeypatch.setattr(telegram_test_preflight.os, "lstat", lstat_with_runtime_owner)
    with pytest.raises(RuntimeError, match="private runtime-owned file"):
        telegram_test_preflight._preflight_secret_file(hardlink, "token")


def test_secret_preflight_rejects_a_symlinked_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "secrets"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setattr(telegram_test_preflight, "TEST_SECRET_ROOT", symlink_root)
    with pytest.raises(RuntimeError, match="root may not contain a symlink"):
        telegram_test_preflight._require_test_secret_root()


def test_launcher_stops_before_compose_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_launch,
        "preflight_telegram_test_files",
        lambda plan: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    called = False

    def forbidden(*arguments: object, **keywords: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("Compose must not run")

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", forbidden)
    with pytest.raises(RuntimeError, match="blocked"):
        telegram_test_launch.launch_telegram_test(plan)
    assert not called


def test_launcher_starts_only_the_test_ingress_after_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )
    preflight_calls: list[bool] = []
    monkeypatch.setattr(
        telegram_test_launch,
        "preflight_telegram_test",
        lambda plan, *, ingress_running=False: preflight_calls.append(ingress_running),
    )
    commands: list[list[str]] = []
    sealed_snapshots: list[str] = []

    def run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        sealed_snapshots.append(Path(command[5]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", run)
    telegram_test_launch.launch_telegram_test(plan)
    assert preflight_calls == [False, False, True]
    assert len(commands) == 3
    assert commands[0][:5] == [
        "docker", "compose", "--project-name", "eva2-telegram-native-test", "--env-file"
    ]
    assert Path(commands[0][5]).name == "sealed.env"
    assert commands[0][6:] == [
        "-f",
        str((DEPLOY / "telegram-test-support.compose.yml").resolve()),
        "--profile",
        "telegram-native-test",
        "up",
        "--detach",
        "--wait",
        "--pull",
        "never",
        "postgres-test",
        "telegram-test-core",
    ]
    assert commands[1][6:] == [
        "-f",
        str((DEPLOY / "telegram-test-support.compose.yml").resolve()),
        "--profile",
        "telegram-native-test",
        "run",
        "--rm",
        "--no-deps",
        "schema-installer-test",
    ]
    assert commands[2][6:] == [
        "-f",
        str((DEPLOY / "telegram-test-ingress.compose.yml").resolve()),
        "--profile",
        "telegram-native-test",
        "up",
        "--detach",
        "--no-deps",
        "--pull",
        "never",
        "telegram-ingress-test",
    ]
    assert sealed_snapshots == [
        "".join(f"{key}={value}\n" for key, value in plan.values)
    ] * 3


def test_launcher_does_not_migrate_before_support_preflight_and_keeps_failure_inspectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )
    monkeypatch.setattr(
        telegram_test_launch,
        "preflight_telegram_test",
        lambda plan, *, ingress_running=False: (_ for _ in ()).throw(
            RuntimeError("support identity differs")
        ),
    )
    commands: list[list[str]] = []

    def run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="support identity differs.*may still be active"):
        telegram_test_launch.launch_telegram_test(plan)
    assert len(commands) == 1
    assert commands[0][-2:] == ["postgres-test", "telegram-test-core"]


def test_launcher_keeps_support_inspectable_for_a_malformed_dsn_before_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    malformed = tmp_path / "database-dsn"
    malformed.write_text("not-a-dsn-secretpassword", encoding="utf-8")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )
    monkeypatch.setattr(
        telegram_test_launch,
        "preflight_telegram_test",
        lambda plan, **kwargs: telegram_test_preflight._require_test_database_dsn(
            _DsnPlan(malformed)  # type: ignore[arg-type]
        ),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        telegram_test_launch.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )
    with pytest.raises(RuntimeError, match="database DSN is invalid.*may still be active"):
        telegram_test_launch.launch_telegram_test(plan)
    assert len(commands) == 1
    assert commands[0][-2:] == ["postgres-test", "telegram-test-core"]


def test_launcher_keeps_support_inspectable_when_schema_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test", lambda plan, **kwargs: None
    )
    commands: list[list[str]] = []

    def run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-1] == "schema-installer-test":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="stack launch failed.*may still be active"):
        telegram_test_launch.launch_telegram_test(plan)
    assert len(commands) == 2
    assert commands[-1][-1] == "schema-installer-test"


def test_launcher_stops_its_poller_when_final_postflight_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )

    def preflight(plan: object, *, ingress_running: bool = False) -> None:
        if ingress_running:
            raise RuntimeError("ingress network binding differs")

    monkeypatch.setattr(telegram_test_launch, "preflight_telegram_test", preflight)
    commands: list[list[str]] = []

    def run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="ingress was stopped after the failed postflight"):
        telegram_test_launch.launch_telegram_test(plan)
    assert len(commands) == 4
    assert commands[-1][-2:] == ["stop", "telegram-ingress-test"]


def test_launcher_stops_its_poller_for_a_malformed_final_dsn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    malformed = tmp_path / "database-dsn"
    malformed.write_text("not-a-dsn-secretpassword", encoding="utf-8")
    monkeypatch.setattr(
        telegram_test_launch, "preflight_telegram_test_files", lambda plan: None
    )

    def preflight(plan: object, *, ingress_running: bool = False) -> None:
        if ingress_running:
            telegram_test_preflight._require_test_database_dsn(
                _DsnPlan(malformed)  # type: ignore[arg-type]
            )

    monkeypatch.setattr(telegram_test_launch, "preflight_telegram_test", preflight)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        telegram_test_launch.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )
    with pytest.raises(RuntimeError, match="database DSN is invalid.*ingress was stopped"):
        telegram_test_launch.launch_telegram_test(plan)
    assert len(commands) == 4
    assert commands[-1][-2:] == ["stop", "telegram-ingress-test"]


def test_in_container_wrapper_rejects_non_test_telegram_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVA_LIVING_CORE_URL", "http://core:8080")
    monkeypatch.setenv("EVA_TELEGRAM_API_BASE_URL", "https://api.telegram.org")
    with pytest.raises(RuntimeError, match="EVA_LIVING_CORE_URL differs"):
        telegram_test_ingress.main()


def test_preflight_reads_the_real_docker_network_binding_shape() -> None:
    """A disposable bridge proves endpoint identity must be checked twice."""

    if subprocess.run(
        ["docker", "info"], capture_output=True, check=False
    ).returncode:
        pytest.skip("Docker daemon unavailable")
    if subprocess.run(
        ["docker", "image", "inspect", "alpine:3.20"],
        capture_output=True,
        check=False,
    ).returncode:
        pytest.skip("local disposable Alpine image unavailable")
    suffix = uuid4().hex
    network_name = f"eva2-telegram-test-alias-{suffix}"
    container_name = f"eva2-telegram-test-core-{suffix}"
    try:
        subprocess.run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                "io.eva2.test-route=telegram-native-test-v1",
                network_name,
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
                "--network",
                network_name,
                "--network-alias",
                "eva-test-core",
                "--label",
                "io.eva2.test-route=telegram-native-test-v1",
                "alpine:3.20",
                "sleep",
                "30",
            ],
            check=True,
            capture_output=True,
        )
        network = telegram_test_preflight._docker_json(
            "docker", ["network", "inspect", network_name]
        )
        assert isinstance(network, list) and isinstance(network[0], dict)
        container = telegram_test_preflight._docker_json(
            "docker", ["container", "inspect", container_name]
        )
        assert isinstance(container, list) and isinstance(container[0], dict)
        telegram_test_preflight._require_one_network_binding(
            container[0], network[0], "disposable test", required_alias="eva-test-core"
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["docker", "network", "rm", network_name],
            capture_output=True,
            check=False,
        )
