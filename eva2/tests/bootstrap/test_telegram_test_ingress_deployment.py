from __future__ import annotations

import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
import yaml

from eva.operations import (
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
    assert service["networks"] == ["telegram-test"]
    assert service["labels"] == {"io.eva2.test-route": "telegram-native-test-v1"}
    assert len(service["volumes"]) == 4
    for mount in service["volumes"]:
        assert mount["read_only"] is True
        assert mount["bind"] == {"create_host_path": False}
        assert mount["source"].startswith("${EVA_TEST_")
    assert document["networks"] == {
        "telegram-test": {
            "name": "${EVA_TEST_NETWORK_NAME:?set a labelled external Telegram test network}",
            "external": True,
        }
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

    def docker_json(command: str, arguments: list[str]) -> object:
        assert command == "docker"
        if arguments[:2] == ["network", "inspect"]:
            return [
                {
                    "Name": plan.value("EVA_TEST_NETWORK_NAME"),
                    "Id": "test-network-id",
                    "Driver": "bridge",
                    "Scope": "local",
                    "Labels": {"io.eva2.test-route": "telegram-native-test-v1"},
                    "Containers": {"test-core": {"EndpointID": "test-endpoint-id"}},
                }
            ]
        assert arguments == ["container", "inspect", "test-core"]
        return [
            {
                "Config": {
                    "Labels": {"io.eva2.test-route": "telegram-native-test-v1"}
                },
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
        ]

    monkeypatch.setattr(telegram_test_preflight, "_docker_json", docker_json)
    preflight_telegram_test(plan)


def test_preflight_rejects_managed_or_unlabelled_network_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_telegram_test_plan(DEPLOY / "telegram-test.example")
    monkeypatch.setattr(
        telegram_test_preflight,
        "_preflight_secret_file",
        lambda path, name: (1, hash(name)),
    )

    def docker_json(command: str, arguments: list[str]) -> object:
        if arguments[:2] == ["network", "inspect"]:
            return [
                {
                    "Name": plan.value("EVA_TEST_NETWORK_NAME"),
                    "Id": "test-network-id",
                    "Driver": "bridge",
                    "Scope": "local",
                    "Labels": {"io.eva2.test-route": "telegram-native-test-v1"},
                    "Containers": {"managed-core": {"EndpointID": "test-endpoint-id"}},
                }
            ]
        return [{"Config": {"Labels": {"io.eva2.managed-by": "b"}}}]

    monkeypatch.setattr(telegram_test_preflight, "_docker_json", docker_json)
    with pytest.raises(RuntimeError, match="not a labelled test container"):
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
    monkeypatch.setattr(telegram_test_preflight, "_require_test_secret_root", lambda: root)
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
        "preflight_telegram_test",
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
    monkeypatch.setattr(telegram_test_launch, "preflight_telegram_test", lambda plan: None)
    commands: list[list[str]] = []
    sealed_snapshots: list[str] = []

    def run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        sealed_snapshots.append(Path(command[5]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(telegram_test_launch.subprocess, "run", run)
    telegram_test_launch.launch_telegram_test(plan)
    assert commands[0][:5] == [
        "docker", "compose", "--project-name", "eva2-telegram-native-test", "--env-file"
    ]
    assert Path(commands[0][5]).name == "sealed.env"
    assert commands[0][6:] == [
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
    assert sealed_snapshots == ["".join(f"{key}={value}\n" for key, value in plan.values)]


def test_in_container_wrapper_rejects_non_test_telegram_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVA_LIVING_CORE_URL", "http://core:8080")
    monkeypatch.setenv("EVA_TELEGRAM_API_BASE_URL", "https://api.telegram.org")
    with pytest.raises(RuntimeError, match="EVA_LIVING_CORE_URL differs"):
        telegram_test_ingress.main()


def test_preflight_reads_the_real_docker_alias_shape() -> None:
    """A disposable bridge proves aliases live in container, not network, inspect."""

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
        telegram_test_preflight._require_test_core_endpoint("docker", network[0])
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
