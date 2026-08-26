"""Fail closed before a separate real-Telegram test ingress is started."""

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
from uuid import UUID


TEST_SECRET_ROOT = Path("/run/eva2-test/secrets")
TEST_NETWORK_LABEL = "io.eva2.test-route"
TEST_NETWORK_VALUE = "telegram-native-test-v1"
TEST_MANAGEMENT_LABEL = "io.eva2.managed-by"
TEST_SERVICE_LABEL = "io.eva2.test-route"
TEST_CORE_SERVICE = "eva-test-core"
TEST_NETWORK_PREFIX = "eva2-telegram-test-"
TEST_CORE_URL = "http://eva-test-core:8080"
TEST_API_URLS = frozenset({"https://api.telegram.org"})
REQUIRED_CONFIGURATION = frozenset(
    {
        "EVA_TEST_DATABASE_DSN_FILE_HOST",
        "EVA_TEST_DOCKER_BINARY",
        "EVA_TEST_LIVING_CORE_IMAGE",
        "EVA_TEST_LIVING_CORE_URL",
        "EVA_TEST_NETWORK_NAME",
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


@dataclass(frozen=True, slots=True)
class TelegramTestPlan:
    values: tuple[tuple[str, str], ...]

    def value(self, name: str) -> str:
        return dict(self.values)[name]


def load_telegram_test_plan(environment_file: Path) -> TelegramTestPlan:
    values = _parse_environment(environment_file.read_bytes())
    if set(values) != REQUIRED_CONFIGURATION:
        raise RuntimeError("Telegram test configuration key set differs")
    for name in SECRET_SETTINGS:
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
    if not values["EVA_TEST_NETWORK_NAME"].startswith(TEST_NETWORK_PREFIX):
        raise RuntimeError("Telegram test network name differs")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,120}", values["EVA_TEST_NETWORK_NAME"]):
        raise RuntimeError("Telegram test network name is invalid")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,240}@sha256:[0-9a-f]{64}",
        values["EVA_TEST_LIVING_CORE_IMAGE"],
    ):
        raise RuntimeError("Telegram test image must use an immutable sha256 digest")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", values["EVA_TEST_DOCKER_BINARY"]):
        raise RuntimeError("Telegram test Docker binary is invalid")
    return TelegramTestPlan(tuple(sorted(values.items())))


def preflight_telegram_test(plan: TelegramTestPlan) -> None:
    secret_inodes: set[tuple[int, int]] = set()
    for name in SECRET_SETTINGS:
        inode = _preflight_secret_file(Path(plan.value(name)), name)
        if inode in secret_inodes:
            raise RuntimeError("Telegram test secret files must be distinct")
        secret_inodes.add(inode)
    command = plan.value("EVA_TEST_DOCKER_BINARY")
    try:
        decoded = _docker_json(
            command, ["network", "inspect", plan.value("EVA_TEST_NETWORK_NAME")]
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise RuntimeError("Telegram test network is unavailable") from error
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise RuntimeError("Telegram test network is invalid")
    network = decoded[0]
    labels = network.get("Labels")
    if (
        network.get("Name") != plan.value("EVA_TEST_NETWORK_NAME")
        or not isinstance(labels, dict)
        or labels.get(TEST_NETWORK_LABEL) != TEST_NETWORK_VALUE
        or TEST_MANAGEMENT_LABEL in labels
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
    ):
        raise RuntimeError("Telegram test network is not isolated")
    _require_test_core_endpoint(command, network)


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


def _require_test_core_endpoint(command: str, network: dict[str, Any]) -> None:
    containers = network.get("Containers")
    if not isinstance(containers, dict) or len(containers) != 1:
        raise RuntimeError("Telegram test network must contain only its test core")
    container_id, endpoint = next(iter(containers.items()))
    if not isinstance(container_id, str) or not isinstance(endpoint, dict):
        raise RuntimeError("Telegram test network endpoint is invalid")
    network_id = network.get("Id")
    endpoint_id = endpoint.get("EndpointID")
    if not isinstance(network_id, str) or not isinstance(endpoint_id, str):
        raise RuntimeError("Telegram test network identity is invalid")
    inspected = _docker_json(command, ["container", "inspect", container_id])
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise RuntimeError("Telegram test core container is invalid")
    configuration = inspected[0].get("Config")
    if not isinstance(configuration, dict):
        raise RuntimeError("Telegram test core configuration is invalid")
    labels = configuration.get("Labels")
    if (
        not isinstance(labels, dict)
        or labels.get(TEST_SERVICE_LABEL) != TEST_NETWORK_VALUE
        or TEST_MANAGEMENT_LABEL in labels
    ):
        raise RuntimeError("Telegram test core is not a labelled test container")
    settings = inspected[0].get("NetworkSettings")
    if not isinstance(settings, dict):
        raise RuntimeError("Telegram test core network settings are invalid")
    networks = settings.get("Networks")
    test_network = (
        networks.get(network["Name"])
        if isinstance(networks, dict) and isinstance(network.get("Name"), str)
        else None
    )
    if not isinstance(test_network, dict):
        raise RuntimeError("Telegram test core is absent from the test network")
    aliases = test_network.get("Aliases")
    if (
        not isinstance(aliases, list)
        or TEST_CORE_SERVICE not in aliases
        or test_network.get("NetworkID") != network_id
        or test_network.get("EndpointID") != endpoint_id
    ):
        raise RuntimeError("Telegram test core network binding differs")


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
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{name} must name a canonical private test file")
    try:
        path.relative_to(TEST_SECRET_ROOT)
    except ValueError as error:
        raise RuntimeError(f"{name} must stay below {TEST_SECRET_ROOT}") from error
    if path == TEST_SECRET_ROOT:
        raise RuntimeError(f"{name} must name a private file")


def _preflight_secret_file(path: Path, name: str) -> tuple[int, int]:
    _require_test_secret_path(str(path), name)
    try:
        root = _require_test_secret_root()
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        _reject_symlinked_parents(path)
        metadata = os.lstat(path)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{name} is inaccessible") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 65532
        or mode not in {0o400, 0o600}
        or metadata.st_nlink != 1
    ):
        raise RuntimeError(f"{name} must be a private runtime-owned file")
    return (metadata.st_dev, metadata.st_ino)


def _require_test_secret_root() -> Path:
    """Reject link, ownership, and writable-directory escapes before file lookup."""

    if not TEST_SECRET_ROOT.is_absolute():
        raise RuntimeError("Telegram test secret root must be absolute")
    if os.path.islink(TEST_SECRET_ROOT):
        raise RuntimeError("Telegram test secret root may not contain a symlink")
    current = Path("/")
    for part in TEST_SECRET_ROOT.parts[1:]:
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
        if current == TEST_SECRET_ROOT:
            if metadata.st_uid not in {0, 65532} or mode & 0o077:
                raise RuntimeError("Telegram test secret root is not private")
        elif metadata.st_uid != 0:
            raise RuntimeError("Telegram test secret root ancestor is not root-owned")
    return TEST_SECRET_ROOT


def _reject_symlinked_parents(path: Path) -> None:
    relative = path.relative_to(TEST_SECRET_ROOT)
    current = TEST_SECRET_ROOT
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
