from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.operations.core_b_preflight import (
    load_core_b_plan as _load_core_b_plan,
    observe_current_b,
    verify_current_management,
    verify_docker_endpoint,
)


def load_core_b_plan(*args: object, **kwargs: object):
    """Run the root-owned production contract against private test files."""

    kwargs.setdefault("postgres_password_owner_uid", os.geteuid())
    return _load_core_b_plan(*args, **kwargs)


def _configuration(private_tmp_path: Path) -> dict[str, str]:
    docker_config = private_tmp_path / "docker-config"
    docker_config.mkdir(mode=0o700, exist_ok=True)
    docker_config.chmod(0o700)
    home_assistant_token = _private_secret(
        private_tmp_path / "home-assistant-token", "not-a-real-token\n"
    )
    database_dsn = _private_secret(
        private_tmp_path / "database-dsn", "postgresql://not-a-real-dsn\n"
    )
    telegram_token = _private_secret(
        private_tmp_path / "telegram-token", "not-a-real-telegram-token\n"
    )
    telegram_bindings = _private_secret(
        private_tmp_path / "telegram-bindings", "{}\n"
    )
    postgres_password = _private_secret(
        private_tmp_path / "postgres-password", "not-a-real-postgres-password\n"
    )
    codex_authority = private_tmp_path / "codex-auth"
    codex_authority.mkdir(mode=0o700, exist_ok=True)
    codex_authority.chmod(0o700)
    model_lease = private_tmp_path / "model-access-lease"
    model_lease.mkdir(mode=0o2750, exist_ok=True)
    model_lease.chmod(0o2750)
    hindsight_lease = private_tmp_path / "hindsight-access"
    hindsight_lease.mkdir(mode=0o700, exist_ok=True)
    hindsight_lease.chmod(0o700)
    return {
        "EVA_B_DOCKER_BINARY": "/usr/bin/docker",
        "EVA_B_DOCKER_CONFIG_DIR": str(docker_config),
        "EVA_B_DOCKER_CONTEXT": "eva2-b-local",
        "EVA_B_DOCKER_DAEMON": "unix:///var/run/docker.sock",
        "EVA_B_COMPOSE_PROJECT": "eva2-b-live",
        "EVA_STACK_CORE_CONTAINER": "eva2-living-core-b",
        "EVA_STACK_BIND_ADDRESS": "127.0.0.1",
        "EVA_LIVING_CORE_IMAGE": "eva2-living-core@sha256:" + ("a" * 64),
        "EVA_DATABASE_DSN_FILE_HOST": str(database_dsn),
        "EVA_POSTGRES_PASSWORD_FILE_HOST": str(postgres_password),
        "EVA_HOME_ASSISTANT_URL": "http://home-assistant:8123",
        "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST": str(home_assistant_token),
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(telegram_token),
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": str(telegram_bindings),
        "EVA_RUNTIME_TENANT_ID": "tenant:test",
        "EVA_RUNTIME_INSTALLATION_ID": "installation:test",
        "EVA_RUNTIME_PRINCIPAL_ID": "principal:test",
        "EVA_DURABLE_GOAL_PROVIDER": "provider:test",
        "EVA_DURABLE_GOAL_PROVIDER_URL": "https://provider.example/v1",
        "EVA_DURABLE_GOAL_RECIPIENTS": "owner-channel",
        "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON": (
            '{"owner":"https://delivery.example/eva"}'
        ),
        "EVA_FANOUT_ALLOW_INSECURE_HTTP": "false",
        "EVA_FILE_BATCH_ROOT": "/var/lib/eva/work",
        "EVA_STACK_WORK_VOLUME": "eva2-living-work-b",
        "EVA_STACK_POSTGRES_VOLUME": "eva2-postgres-b-live",
        "EVA_STACK_HINDSIGHT_VOLUME": "eva2-hindsight-memory",
        "EVA_CODEX_AUTH_DIR": str(codex_authority),
        "EVA_MODEL_ACCESS_LEASE_DIR": str(model_lease),
        "EVA_HINDSIGHT_ACCESS_DIR": str(hindsight_lease),
    }


def _private_secret(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)
    return path


def _environment_file(private_tmp_path: Path, values: dict[str, str]) -> Path:
    path = private_tmp_path / "eva2-b-runtime.env"
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_plan_uses_one_immutable_environment_snapshot(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    environment = _environment_file(private_tmp_path, values)
    plan = load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())

    environment.write_text("EVA_FILE_BATCH_ROOT=/lost\n", encoding="utf-8")

    assert plan.value("EVA_FILE_BATCH_ROOT") == "/var/lib/eva/work"
    assert plan.value("EVA_DURABLE_GOAL_PROVIDER") == "provider:test"
    assert "owner" in plan.value("EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON")
    assert plan.value("EVA_MODEL_ACCESS_LEASE_DIR") == values[
        "EVA_MODEL_ACCESS_LEASE_DIR"
    ]


def test_plan_rejects_environment_metadata_change_while_snapshotting(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    environment = _environment_file(private_tmp_path, values)
    real_read = os.read
    changed = False

    def changing_read(descriptor: int, maximum: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, maximum)
        if chunk and not changed:
            changed = True
            environment.chmod(0o400)
        return chunk

    monkeypatch.setattr(os, "read", changing_read)

    with pytest.raises(RuntimeError, match="changed while it was read"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_plan_rejects_environment_owned_by_another_operator(
    private_tmp_path: Path,
) -> None:
    environment = _environment_file(
        private_tmp_path,
        _configuration(private_tmp_path),
    )

    with pytest.raises(RuntimeError, match="runtime effective UID"):
        load_core_b_plan(
            environment.as_posix(),
            runtime_uid=os.geteuid(),
            operator_uid=os.geteuid() + 1,
        )


@pytest.mark.parametrize(
    ("mode", "expected"),
    ((0o100, "owner read permission"), (0o640, "group or other permissions")),
)
def test_plan_rejects_insecure_environment_permissions(
    mode: int,
    expected: str,
    private_tmp_path: Path,
) -> None:
    environment = _environment_file(
        private_tmp_path,
        _configuration(private_tmp_path),
    )
    environment.chmod(mode)

    with pytest.raises(RuntimeError, match=expected):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_plan_rejects_execute_only_home_assistant_host_secret(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    Path(values["EVA_HOME_ASSISTANT_TOKEN_FILE_HOST"]).chmod(0o100)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match="owner must have read access"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


@pytest.mark.parametrize(
    "setting_name",
    (
        "EVA_DATABASE_DSN_FILE_HOST",
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST",
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
    ),
)
def test_plan_rejects_insecure_mounted_runtime_secret(
    setting_name: str,
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    Path(values[setting_name]).chmod(0o640)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match=setting_name):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_production_default_rejects_non_root_postgres_password_file(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match="EVA_POSTGRES_PASSWORD_FILE_HOST.*UID 0"):
        _load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        (0o400, "permissions differ"),
        (0o640, "no group/other permissions"),
    ),
)
def test_plan_rejects_postgres_password_outside_exact_root_contract(
    mode: int,
    expected: str,
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    Path(values["EVA_POSTGRES_PASSWORD_FILE_HOST"]).chmod(mode)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(
        RuntimeError,
        match=f"EVA_POSTGRES_PASSWORD_FILE_HOST.*{expected}",
    ):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


@pytest.mark.parametrize(
    "capability",
    (
        "EVA_DURABLE_GOAL_PROVIDER",
        "EVA_DURABLE_GOAL_RECIPIENTS",
        "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON",
        "EVA_FANOUT_ALLOW_INSECURE_HTTP",
        "EVA_FILE_BATCH_ROOT",
        "EVA_STACK_WORK_VOLUME",
        "EVA_STACK_POSTGRES_VOLUME",
        "EVA_STACK_HINDSIGHT_VOLUME",
        "EVA_POSTGRES_PASSWORD_FILE_HOST",
        "EVA_CODEX_AUTH_DIR",
        "EVA_MODEL_ACCESS_LEASE_DIR",
        "EVA_HINDSIGHT_ACCESS_DIR",
    ),
)
def test_plan_refuses_capability_loss(
    capability: str,
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values.pop(capability)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match=capability):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_plan_rejects_wrong_docker_context_or_daemon(private_tmp_path: Path) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )

    with pytest.raises(RuntimeError, match="different context"):
        verify_docker_endpoint(
            plan,
            "production",
            "unix:///var/run/docker.sock",
        )
    with pytest.raises(RuntimeError, match="different daemon"):
        verify_docker_endpoint(
            plan,
            "eva2-b-local",
            "ssh://production.example",
        )


def test_plan_rejects_unpinned_volume_and_unsafe_contract_directories(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values["EVA_STACK_WORK_VOLUME"] = "another-volume"
    with pytest.raises(RuntimeError, match="must be exactly eva2-living-work-b"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )

    values = _configuration(private_tmp_path)
    values["EVA_STACK_POSTGRES_VOLUME"] = "another-postgres-volume"
    with pytest.raises(RuntimeError, match="must be exactly eva2-postgres-b-live"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )

    values = _configuration(private_tmp_path)
    values["EVA_STACK_HINDSIGHT_VOLUME"] = "/unsafe-volume"
    with pytest.raises(RuntimeError, match="EVA_STACK_HINDSIGHT_VOLUME is invalid"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )

    values = _configuration(private_tmp_path)
    values["EVA_MODEL_ACCESS_LEASE_DIR"] = "/"
    with pytest.raises(RuntimeError, match="ownership differs"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )

    values = _configuration(private_tmp_path)
    path = Path(values["EVA_HINDSIGHT_ACCESS_DIR"])
    path.chmod(0o755)
    with pytest.raises(RuntimeError, match="permissions differ"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )


def test_plan_rejects_unmanaged_current_b(private_tmp_path: Path) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )

    with pytest.raises(RuntimeError, match="current B container is unmanaged"):
        verify_current_management(plan, {})


def test_plan_rejects_true_and_wrapper_instead_of_system_docker(
    private_tmp_path: Path,
) -> None:
    wrapper = private_tmp_path / "docker-wrapper"
    wrapper.write_text("#!/bin/sh\nexec /usr/bin/docker \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o700)
    baseline = _configuration(private_tmp_path)

    for candidate in ("/usr/bin/true", wrapper.as_posix()):
        values = dict(baseline)
        values["EVA_B_DOCKER_BINARY"] = candidate
        environment = _environment_file(private_tmp_path, values)

        with pytest.raises(RuntimeError, match="exactly /usr/bin/docker"):
            load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_container_read_uses_verified_host_after_context_config_swap(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    docker_config_marker = (
        Path(plan.value("EVA_B_DOCKER_CONFIG_DIR")) / "context-authority"
    )
    docker_config_marker.write_text("expected", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if len(commands) == 1:
            docker_config_marker.write_text("swapped", encoding="utf-8")
            return SimpleNamespace(
                stdout=(
                    '[{"Name":"eva2-b-local","Endpoints":{"docker":'
                    '{"Host":"unix:///var/run/docker.sock"}}}]'
                )
            )
        return SimpleNamespace(
            stdout=(
                '{"io.eva2.managed-by":"eva2-explicit-b-manifest-v1",'
                f'"io.eva2.configuration-sha256":"{plan.snapshot_sha256}"}}'
            )
        )

    monkeypatch.setattr("eva.operations.core_b_preflight.subprocess.run", fake_run)

    observe_current_b(plan)

    assert len(commands) == 2
    assert "--context" in commands[0]
    assert "--host" not in commands[0]
    assert commands[1][:5] == [
        "/usr/bin/docker",
        "--config",
        plan.value("EVA_B_DOCKER_CONFIG_DIR"),
        "--host",
        "unix:///var/run/docker.sock",
    ]
    assert "--context" not in commands[1]
    assert commands[1][-6:] == [
        "inspect",
        "--type",
        "container",
        "--format",
        "{{json .Config.Labels}}",
        "eva2-living-core-b",
    ]
