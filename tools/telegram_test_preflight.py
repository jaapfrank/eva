"""Host-side fail-closed checks for the separate real-Telegram test ingress."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, NoReturn
import urllib.parse
from uuid import UUID


TEST_SECRET_ROOT = Path("/run/eva2-test/secrets")
TEST_POSTGRES_SECRET_ROOT = Path("/run/eva2-test/postgres-secrets")
RUNTIME_SECRET_OWNER_UID = 65532
POSTGRES_SECRET_OWNER_UID = 999
TEST_NETWORK_LABEL = "io.eva2.test-route"
TEST_NETWORK_VALUE = "telegram-native-test-v1"
TEST_DATABASE_NETWORK_LABEL = "io.eva2.test-database"
TEST_DATABASE_NETWORK_VALUE = "telegram-native-test-db-v1"
TEST_RUN_LABEL = "io.eva2.test-run"
TEST_RUN_VALUE = "telegram-native-test-v1"
TEST_MANAGEMENT_LABEL = "io.eva2.managed-by"
TEST_SERVICE_LABEL = "io.eva2.test-route"
TEST_CORE_SERVICE = "eva-test-core"
TEST_CORE_COMMAND = ["python", "-m", "tools.telegram_test_core"]
TEST_INGRESS_COMMAND = ["python", "-m", "tools.telegram_test_ingress"]
TEST_NETWORK_PREFIX = "eva2-telegram-test-"
TEST_CORE_URL = "http://eva-test-core:8080"
TEST_API_URLS = frozenset({"https://api.telegram.org"})
REQUIRED_CONFIGURATION = frozenset(
    {
        "EVA_TEST_DATABASE_DSN_FILE_HOST",
        "EVA_TEST_DATABASE_NETWORK_NAME",
        "EVA_TEST_DOCKER_BINARY",
        "EVA_TEST_LIVING_CORE_IMAGE",
        "EVA_TEST_LIVING_CORE_URL",
        "EVA_TEST_NETWORK_NAME",
        "EVA_TEST_POSTGRES_IMAGE",
        "EVA_TEST_POSTGRES_PASSWORD_FILE_HOST",
        "EVA_TEST_POSTGRES_VOLUME_NAME",
        "EVA_TEST_RUNTIME_INSTALLATION_ID",
        "EVA_TEST_RUNTIME_TENANT_ID",
        "EVA_TEST_TELEGRAM_API_BASE_URL",
        "EVA_TEST_TELEGRAM_BOT_TOKEN_FILE_HOST",
        "EVA_TEST_TELEGRAM_PAIRING_SECRET_FILE_HOST",
        "EVA_TEST_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
    }
)
SECRET_SETTINGS = (
    "EVA_TEST_DATABASE_DSN_FILE_HOST",
    "EVA_TEST_TELEGRAM_BOT_TOKEN_FILE_HOST",
    "EVA_TEST_TELEGRAM_PAIRING_SECRET_FILE_HOST",
    "EVA_TEST_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
)
POSTGRES_SECRET_SETTING = "EVA_TEST_POSTGRES_PASSWORD_FILE_HOST"


@dataclass(frozen=True, slots=True)
class TelegramTestPlan:
    values: tuple[tuple[str, str], ...]

    def value(self, name: str) -> str:
        return dict(self.values)[name]


def load_telegram_test_plan(environment_file: Path) -> TelegramTestPlan:
    values = _parse_environment(environment_file.read_bytes())
    if set(values) != REQUIRED_CONFIGURATION:
        raise RuntimeError("Telegram test configuration key set differs")
    for name in (*SECRET_SETTINGS, POSTGRES_SECRET_SETTING):
        _require_test_secret_path(values[name], name)
    for name in (
        "EVA_TEST_RUNTIME_TENANT_ID",
        "EVA_TEST_RUNTIME_INSTALLATION_ID",
    ):
        try:
            UUID(values[name])
        except ValueError as error:
            raise RuntimeError(f"{name} must be a UUID") from error
    if values["EVA_TEST_RUNTIME_TENANT_ID"] == values["EVA_TEST_RUNTIME_INSTALLATION_ID"]:
        raise RuntimeError("Telegram test tenant and installation must differ")
    if values["EVA_TEST_LIVING_CORE_URL"] != TEST_CORE_URL:
        raise RuntimeError("Telegram test core URL differs")
    if values["EVA_TEST_TELEGRAM_API_BASE_URL"] not in TEST_API_URLS:
        raise RuntimeError("Telegram test API URL differs")
    for name in (
        "EVA_TEST_NETWORK_NAME",
        "EVA_TEST_DATABASE_NETWORK_NAME",
        "EVA_TEST_POSTGRES_VOLUME_NAME",
    ):
        if not values[name].startswith(TEST_NETWORK_PREFIX) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{2,120}", values[name]
        ):
            raise RuntimeError(f"{name} is invalid")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,240}@sha256:[0-9a-f]{64}",
        values["EVA_TEST_LIVING_CORE_IMAGE"],
    ):
        raise RuntimeError("Telegram test image must use an immutable sha256 digest")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,240}@sha256:[0-9a-f]{64}",
        values["EVA_TEST_POSTGRES_IMAGE"],
    ):
        raise RuntimeError(
            "Telegram test PostgreSQL image must use an immutable sha256 digest"
        )
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", values["EVA_TEST_DOCKER_BINARY"]):
        raise RuntimeError("Telegram test Docker binary is invalid")
    return TelegramTestPlan(tuple(sorted(values.items())))


def preflight_telegram_test(
    plan: TelegramTestPlan, *, ingress_running: bool = False
) -> None:
    """Verify the live support stack before or after its test ingress starts.

    This checks resource identity and isolation.  It parses the test DSN in
    memory solely to bind its endpoint; it never logs, returns, or stores its
    password.  It does not prove valid PostgreSQL credentials or a real Eva
    conversation.
    """

    preflight_telegram_test_files(plan)
    _require_test_database_dsn(plan)
    command = plan.value("EVA_TEST_DOCKER_BINARY")
    database_network = _inspect_network(
        command,
        plan.value("EVA_TEST_DATABASE_NETWORK_NAME"),
        "Telegram test database network",
    )
    test_network = _inspect_network(
        command,
        plan.value("EVA_TEST_NETWORK_NAME"),
        "Telegram test network",
    )
    postgres_id = _require_test_database_network(
        command, plan, database_network, ingress_running=ingress_running
    )
    core_id = _require_test_core_endpoint(
        command, plan, test_network, ingress_running=ingress_running
    )
    if ingress_running:
        _require_test_ingress_endpoints(
            command, plan, test_network, database_network, core_id, postgres_id
        )


def preflight_telegram_test_files(plan: TelegramTestPlan) -> None:
    """Check the test-only inputs before the support stack may be started."""

    secret_inodes: set[tuple[int, int]] = set()
    for name in (*SECRET_SETTINGS, POSTGRES_SECRET_SETTING):
        inode = _preflight_secret_file(Path(plan.value(name)), name)
        if inode in secret_inodes:
            raise RuntimeError("Telegram test secret files must be distinct")
        secret_inodes.add(inode)


def _inspect_network(command: str, name: str, description: str) -> dict[str, Any]:
    try:
        decoded = _docker_json(command, ["network", "inspect", name])
    except RuntimeError as error:
        raise RuntimeError(f"{description} is unavailable") from error
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise RuntimeError(f"{description} is invalid")
    return decoded[0]


def _require_test_database_network(
    command: str,
    plan: TelegramTestPlan,
    network: dict[str, Any],
    *,
    ingress_running: bool,
) -> str:
    labels = network.get("Labels")
    containers = network.get("Containers")
    expected_count = 2 if ingress_running else 1
    if (
        network.get("Name") != plan.value("EVA_TEST_DATABASE_NETWORK_NAME")
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or network.get("Internal") is not True
        or not isinstance(labels, dict)
        or labels.get(TEST_DATABASE_NETWORK_LABEL) != TEST_DATABASE_NETWORK_VALUE
        or labels.get(TEST_RUN_LABEL) != TEST_RUN_VALUE
        or TEST_MANAGEMENT_LABEL in labels
        or not isinstance(containers, dict)
        or len(containers) != expected_count
    ):
        raise RuntimeError("Telegram test database network is not isolated")
    records = _inspect_network_containers(command, containers, "Telegram test database")
    postgres = [
        (container_id, inspected)
        for container_id, inspected in records.items()
        if _has_label(inspected, TEST_DATABASE_NETWORK_LABEL, TEST_DATABASE_NETWORK_VALUE)
    ]
    if len(postgres) != 1:
        raise RuntimeError("Telegram test database must contain one PostgreSQL container")
    container_id, inspected = postgres[0]
    configuration = _configuration(inspected, "Telegram test PostgreSQL")
    state = inspected.get("State")
    if (
        configuration.get("Image") != plan.value("EVA_TEST_POSTGRES_IMAGE")
        or not _has_label(inspected, TEST_RUN_LABEL, TEST_RUN_VALUE)
        or TEST_MANAGEMENT_LABEL in _labels(inspected, "Telegram test PostgreSQL")
        or not isinstance(state, dict)
        or state.get("Status") != "running"
        or not isinstance(state.get("Health"), dict)
        or state["Health"].get("Status") != "healthy"
    ):
        raise RuntimeError("Telegram test PostgreSQL is not a healthy labelled test container")
    _require_postgres_environment(configuration)
    _require_one_network_binding(inspected, network, "Telegram test PostgreSQL")
    _require_postgres_mounts(inspected, plan)
    _require_test_database_volume(command, plan)
    return container_id


def _docker_json(command: str, arguments: list[str]) -> object:
    try:
        completed = subprocess.run(
            [command, *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise RuntimeError("Telegram test Docker inspection failed") from error


def _require_test_core_endpoint(
    command: str,
    plan: TelegramTestPlan,
    network: dict[str, Any],
    *,
    ingress_running: bool,
) -> str:
    containers = network.get("Containers")
    labels = network.get("Labels")
    expected_count = 2 if ingress_running else 1
    if (
        network.get("Name") != plan.value("EVA_TEST_NETWORK_NAME")
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or not isinstance(labels, dict)
        or labels.get(TEST_NETWORK_LABEL) != TEST_NETWORK_VALUE
        or labels.get(TEST_RUN_LABEL) != TEST_RUN_VALUE
        or TEST_MANAGEMENT_LABEL in labels
        or not isinstance(containers, dict)
        or len(containers) != expected_count
    ):
        raise RuntimeError("Telegram test network is not isolated")
    records = _inspect_network_containers(command, containers, "Telegram test")
    cores = [
        (container_id, inspected)
        for container_id, inspected in records.items()
        if _has_label(inspected, TEST_SERVICE_LABEL, TEST_NETWORK_VALUE)
        and _configuration(inspected, "Telegram test core").get("Cmd") == TEST_CORE_COMMAND
    ]
    if len(cores) != 1:
        raise RuntimeError("Telegram test network must contain one fixed test core")
    container_id, inspected = cores[0]
    configuration = _configuration(inspected, "Telegram test core")
    state = inspected.get("State")
    if (
        configuration.get("Image") != plan.value("EVA_TEST_LIVING_CORE_IMAGE")
        or not _has_label(inspected, TEST_RUN_LABEL, TEST_RUN_VALUE)
        or TEST_MANAGEMENT_LABEL in _labels(inspected, "Telegram test core")
        or configuration.get("User") != "65532:65532"
        or not isinstance(state, dict)
        or state.get("Status") != "running"
        or not isinstance(state.get("Health"), dict)
        or state["Health"].get("Status") != "healthy"
    ):
        raise RuntimeError("Telegram test core is not a healthy labelled test container")
    _require_one_network_binding(
        inspected, network, "Telegram test core", required_alias=TEST_CORE_SERVICE
    )
    return container_id


def _inspect_network_containers(
    command: str, containers: dict[str, Any], description: str
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for container_id, endpoint in containers.items():
        if not isinstance(container_id, str) or not isinstance(endpoint, dict):
            raise RuntimeError(f"{description} network endpoint is invalid")
        inspected = _container_inspect(command, container_id, description)
        records[container_id] = inspected
    return records


def _container_inspect(command: str, container_id: str, description: str) -> dict[str, Any]:
    inspected = _docker_json(command, ["container", "inspect", container_id])
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise RuntimeError(f"{description} container is invalid")
    return inspected[0]


def _configuration(inspected: dict[str, Any], description: str) -> dict[str, Any]:
    configuration = inspected.get("Config")
    if not isinstance(configuration, dict):
        raise RuntimeError(f"{description} configuration is invalid")
    return configuration


def _labels(inspected: dict[str, Any], description: str) -> dict[str, str]:
    labels = _configuration(inspected, description).get("Labels")
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        raise RuntimeError(f"{description} labels are invalid")
    return labels


def _has_label(inspected: dict[str, Any], name: str, value: str) -> bool:
    try:
        return _labels(inspected, "Telegram test container").get(name) == value
    except RuntimeError:
        return False


def _require_one_network_binding(
    inspected: dict[str, Any],
    network: dict[str, Any],
    description: str,
    *,
    required_alias: str | None = None,
) -> None:
    name = network.get("Name")
    network_id = network.get("Id")
    containers = network.get("Containers")
    if (
        not isinstance(name, str)
        or not isinstance(network_id, str)
        or not isinstance(containers, dict)
        or not isinstance(inspected.get("Id"), str)
    ):
        raise RuntimeError(f"{description} network identity is invalid")
    endpoint = containers.get(inspected["Id"])
    endpoint_id = endpoint.get("EndpointID") if isinstance(endpoint, dict) else None
    settings = inspected.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    binding = networks.get(name) if isinstance(networks, dict) else None
    if (
        not isinstance(binding, dict)
        or set(networks) != {name}
        or binding.get("NetworkID") != network_id
        or binding.get("EndpointID") != endpoint_id
    ):
        raise RuntimeError(f"{description} network binding differs")
    if required_alias is not None:
        aliases = binding.get("Aliases")
        if not isinstance(aliases, list) or required_alias not in aliases:
            raise RuntimeError(f"{description} network alias differs")


def _require_postgres_environment(configuration: dict[str, Any]) -> None:
    environment = configuration.get("Env")
    if not isinstance(environment, list) or not all(isinstance(value, str) for value in environment):
        raise RuntimeError("Telegram test PostgreSQL environment is invalid")
    required = {
        "POSTGRES_DB=eva2test",
        "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
        "POSTGRES_PASSWORD_FILE=/run/eva2-test/postgres-password",
        "POSTGRES_USER=postgres",
    }
    if not required.issubset(environment) or any(
        value.startswith("POSTGRES_PASSWORD=") for value in environment
    ):
        raise RuntimeError("Telegram test PostgreSQL environment differs")


def _require_postgres_mounts(inspected: dict[str, Any], plan: TelegramTestPlan) -> None:
    mounts = inspected.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != 2 or not all(
        isinstance(mount, dict) for mount in mounts
    ):
        raise RuntimeError("Telegram test PostgreSQL mounts differ")
    expected_password = {
        "Type": "bind",
        "Source": plan.value("EVA_TEST_POSTGRES_PASSWORD_FILE_HOST"),
        "Destination": "/run/eva2-test/postgres-password",
        "RW": False,
    }
    expected_data = {
        "Type": "volume",
        "Name": plan.value("EVA_TEST_POSTGRES_VOLUME_NAME"),
        "Destination": "/var/lib/postgresql/data",
        "RW": True,
    }
    if not any(all(mount.get(key) == value for key, value in expected_password.items()) for mount in mounts):
        raise RuntimeError("Telegram test PostgreSQL password mount differs")
    if not any(all(mount.get(key) == value for key, value in expected_data.items()) for mount in mounts):
        raise RuntimeError("Telegram test PostgreSQL data volume differs")


def _require_test_database_volume(command: str, plan: TelegramTestPlan) -> None:
    inspected = _docker_json(
        command, ["volume", "inspect", plan.value("EVA_TEST_POSTGRES_VOLUME_NAME")]
    )
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise RuntimeError("Telegram test PostgreSQL volume is invalid")
    volume = inspected[0]
    labels = volume.get("Labels")
    if (
        volume.get("Name") != plan.value("EVA_TEST_POSTGRES_VOLUME_NAME")
        or volume.get("Driver") != "local"
        or volume.get("Scope") != "local"
        or volume.get("Options") not in (None, {})
        or not isinstance(labels, dict)
        or labels.get(TEST_DATABASE_NETWORK_LABEL) != TEST_DATABASE_NETWORK_VALUE
        or labels.get(TEST_RUN_LABEL) != TEST_RUN_VALUE
        or TEST_MANAGEMENT_LABEL in labels
    ):
        raise RuntimeError("Telegram test PostgreSQL volume is not isolated")


def _require_test_database_dsn(plan: TelegramTestPlan) -> None:
    """Allow exactly the fresh local test database and no failover route."""

    path = Path(plan.value("EVA_TEST_DATABASE_DSN_FILE_HOST"))
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > 4096:
            raise ValueError("size")
        value = raw.decode("utf-8")
        if value != value.strip() + ("\n" if value.endswith("\n") else ""):
            raise ValueError("format")
        dsn = value.strip()
        if "://" not in dsn:
            raise ValueError("scheme")
        url = urllib.parse.urlsplit(dsn)
        port = url.port
    except (OSError, UnicodeDecodeError, ValueError):
        raise RuntimeError("Telegram test database DSN is invalid") from None
    if (
        url.scheme != "postgresql+psycopg"
        or url.username != "postgres"
        or not isinstance(url.password, str)
        or not url.password
        or url.hostname != "postgres-test"
        or port != 5432
        or url.path != "/eva2test"
        or url.query
        or url.fragment
    ):
        raise RuntimeError("Telegram test database DSN is not the isolated test route")


def _require_test_ingress_endpoints(
    command: str,
    plan: TelegramTestPlan,
    test_network: dict[str, Any],
    database_network: dict[str, Any],
    core_id: str,
    postgres_id: str,
) -> None:
    test_containers = test_network.get("Containers")
    database_containers = database_network.get("Containers")
    if not isinstance(test_containers, dict) or not isinstance(database_containers, dict):
        raise RuntimeError("Telegram test ingress network endpoints are invalid")
    test_ids = set(test_containers)
    database_ids = set(database_containers)
    ingress_ids = test_ids - {core_id}
    if (
        len(ingress_ids) != 1
        or ingress_ids != database_ids - {postgres_id}
        or test_ids != {core_id, *ingress_ids}
        or database_ids != {postgres_id, *ingress_ids}
    ):
        raise RuntimeError("Telegram test ingress network membership differs")
    ingress_id = next(iter(ingress_ids))
    inspected = _container_inspect(command, ingress_id, "Telegram test ingress")
    configuration = _configuration(inspected, "Telegram test ingress")
    host = inspected.get("HostConfig")
    state = inspected.get("State")
    if (
        configuration.get("Image") != plan.value("EVA_TEST_LIVING_CORE_IMAGE")
        or configuration.get("Cmd") != TEST_INGRESS_COMMAND
        or configuration.get("User") != "65532:65532"
        or not _has_label(inspected, TEST_SERVICE_LABEL, TEST_NETWORK_VALUE)
        or not _has_label(inspected, TEST_RUN_LABEL, TEST_RUN_VALUE)
        or TEST_MANAGEMENT_LABEL in _labels(inspected, "Telegram test ingress")
        or not isinstance(host, dict)
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or "no-new-privileges:true" not in host.get("SecurityOpt", [])
        or not isinstance(state, dict)
        or state.get("Status") != "running"
    ):
        raise RuntimeError("Telegram test ingress is not a constrained test container")
    _require_ingress_network_bindings(inspected, test_network, database_network)
    _require_ingress_environment(configuration, plan)
    _require_ingress_mounts(inspected, plan)


def _require_ingress_network_bindings(
    inspected: dict[str, Any], test_network: dict[str, Any], database_network: dict[str, Any]
) -> None:
    settings = inspected.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    if not isinstance(networks, dict):
        raise RuntimeError("Telegram test ingress network bindings differ")
    names = {test_network.get("Name"), database_network.get("Name")}
    if not all(isinstance(name, str) for name in names) or set(networks) != names:
        raise RuntimeError("Telegram test ingress network bindings differ")
    for network in (test_network, database_network):
        name = network["Name"]
        endpoint = network["Containers"].get(inspected.get("Id"))
        binding = networks.get(name)
        if (
            not isinstance(endpoint, dict)
            or not isinstance(binding, dict)
            or binding.get("NetworkID") != network.get("Id")
            or binding.get("EndpointID") != endpoint.get("EndpointID")
        ):
            raise RuntimeError("Telegram test ingress network binding differs")


def _require_ingress_environment(configuration: dict[str, Any], plan: TelegramTestPlan) -> None:
    environment = configuration.get("Env")
    if not isinstance(environment, list) or not all(isinstance(value, str) for value in environment):
        raise RuntimeError("Telegram test ingress environment is invalid")
    required = {
        f"EVA_LIVING_CORE_URL={plan.value('EVA_TEST_LIVING_CORE_URL')}",
        f"EVA_RUNTIME_INSTALLATION_ID={plan.value('EVA_TEST_RUNTIME_INSTALLATION_ID')}",
        f"EVA_RUNTIME_TENANT_ID={plan.value('EVA_TEST_RUNTIME_TENANT_ID')}",
        f"EVA_TELEGRAM_API_BASE_URL={plan.value('EVA_TEST_TELEGRAM_API_BASE_URL')}",
        "EVA_DATABASE_DSN_FILE=/run/secrets/database_dsn",
        "EVA_TELEGRAM_BOT_TOKEN_FILE=/run/secrets/telegram_bot_token",
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE=/run/secrets/telegram_principal_bindings",
        "EVA_TELEGRAM_PAIRING_SECRET_FILE=/run/secrets/telegram_pairing_secret",
    }
    if not required.issubset(environment):
        raise RuntimeError("Telegram test ingress environment differs")


def _require_ingress_mounts(inspected: dict[str, Any], plan: TelegramTestPlan) -> None:
    mounts = inspected.get("Mounts")
    expected = {
        "/run/secrets/database_dsn": plan.value("EVA_TEST_DATABASE_DSN_FILE_HOST"),
        "/run/secrets/telegram_bot_token": plan.value(
            "EVA_TEST_TELEGRAM_BOT_TOKEN_FILE_HOST"
        ),
        "/run/secrets/telegram_principal_bindings": plan.value(
            "EVA_TEST_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST"
        ),
        "/run/secrets/telegram_pairing_secret": plan.value(
            "EVA_TEST_TELEGRAM_PAIRING_SECRET_FILE_HOST"
        ),
    }
    if not isinstance(mounts, list) or len(mounts) != len(expected):
        raise RuntimeError("Telegram test ingress mounts differ")
    actual = {
        mount.get("Destination"): mount.get("Source")
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("Type") == "bind"
        and mount.get("RW") is False
    }
    if actual != expected:
        raise RuntimeError("Telegram test ingress mounts differ")


def _parse_environment(snapshot: bytes) -> dict[str, str]:
    try:
        decoded = snapshot.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("Telegram test configuration must be UTF-8") from error
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(decoded.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw_line:
            raise RuntimeError(
                f"Telegram test configuration line {line_number} is not KEY=value"
            )
        key, value = raw_line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise RuntimeError("Telegram test configuration key is invalid")
        if "\x00" in value:
            raise RuntimeError("Telegram test configuration contains a NUL byte")
        values[key] = value.strip()
    return values


def _require_test_secret_path(value: str, name: str) -> None:
    root = (
        TEST_POSTGRES_SECRET_ROOT
        if name == POSTGRES_SECRET_SETTING
        else TEST_SECRET_ROOT
    )
    _require_secret_path(value, name, root)


def _require_secret_path(value: str, name: str, root: Path) -> None:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{name} must name a canonical private test file")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"{name} must stay below {root}") from error
    if path == root:
        raise RuntimeError(f"{name} must name a private file")


def _preflight_secret_file(path: Path, name: str) -> tuple[int, int]:
    _require_test_secret_path(str(path), name)
    root = (
        TEST_POSTGRES_SECRET_ROOT
        if name == POSTGRES_SECRET_SETTING
        else TEST_SECRET_ROOT
    )
    owner_uid = (
        POSTGRES_SECRET_OWNER_UID
        if name == POSTGRES_SECRET_SETTING
        else RUNTIME_SECRET_OWNER_UID
    )
    try:
        _require_secret_root(root, owner_uid)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        _reject_symlinked_parents(path, root)
        metadata = os.lstat(path)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{name} is inaccessible") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or mode not in {0o400, 0o600}
        or metadata.st_nlink != 1
    ):
        raise RuntimeError(f"{name} must be a private runtime-owned file")
    return (metadata.st_dev, metadata.st_ino)


def _require_test_secret_root() -> Path:
    """Reject link, ownership, and writable-directory escapes before file lookup."""

    return _require_secret_root(TEST_SECRET_ROOT, RUNTIME_SECRET_OWNER_UID)


def _require_secret_root(root: Path, owner_uid: int) -> Path:
    """Check one dedicated owner-only secret directory and its ancestors."""

    if not root.is_absolute():
        raise RuntimeError("Telegram test secret root must be absolute")
    if os.path.islink(root):
        raise RuntimeError("Telegram test secret root may not contain a symlink")
    current = Path("/")
    for part in root.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise RuntimeError("Telegram test secret root is inaccessible") from error
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("Telegram test secret root may not contain a symlink")
        if mode & 0o022:
            raise RuntimeError("Telegram test secret root may not be group or world writable")
        if current == root:
            if metadata.st_uid not in {0, owner_uid} or mode & 0o077:
                raise RuntimeError("Telegram test secret root is not private")
        elif metadata.st_uid != 0:
            raise RuntimeError("Telegram test secret root ancestor is not root-owned")
    return root


def _reject_symlinked_parents(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if os.path.islink(current):
            raise RuntimeError("Telegram test secret parent may not be a symlink")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        plan = load_telegram_test_plan(arguments.env_file)
        preflight_telegram_test(plan)
    except RuntimeError as error:
        _die(str(error))
    print("Telegram test preflight OK")
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-preflight: {message}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TelegramTestPlan", "load_telegram_test_plan", "preflight_telegram_test"]
