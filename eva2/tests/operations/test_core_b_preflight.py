from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.operations import core_b_preflight
from eva.operations.core_b_preflight import (
    REQUIRED_CONFIGURATION,
    host_metadata_preflight_receipt,
    load_core_b_plan as _load_core_b_plan,
    observe_current_b,
    observe_managed_b_services,
    verify_current_management,
    verify_docker_endpoint,
    verify_managed_service_labels,
    verify_steady_managed_service_labels,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ENVIRONMENT_TEMPLATE = ROOT / "deploy" / "living-core" / "runtime.env.example"
MANAGED_B_MANIFEST = ROOT / "deploy" / "living-core" / "managed-b.compose.yml"


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
    dashboard_access_token = _private_secret(
        private_tmp_path / "dashboard-access-token", "t" * 32
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
    telegram_pairing_secret = _private_secret(
        private_tmp_path / "telegram-pairing-secret", "p" * 32
    )
    postgres_password = _private_secret(
        private_tmp_path / "postgres-password", "not-a-real-postgres-password\n"
    )
    identity_pack = _private_secret(
        private_tmp_path / "identity-pack.json",
        (ROOT / "src/eva/application/default_identity_pack.json").read_text(),
    )
    identity_trust = _private_secret(
        private_tmp_path / "identity-trust.json",
        (ROOT / "src/eva/application/default_identity_pack_trust.json").read_text(),
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
        "EVA_B_MANAGED_MANIFEST_SHA256": hashlib.sha256(
            MANAGED_B_MANIFEST.read_bytes()
        ).hexdigest(),
        "EVA_STACK_CORE_CONTAINER": "eva2-living-core-b",
        "EVA_STACK_BIND_ADDRESS": "127.0.0.1",
        "EVA_LIVING_CORE_IMAGE": "eva2-living-core@sha256:" + ("a" * 64),
        "EVA_DATABASE_DSN_FILE_HOST": str(database_dsn),
        "EVA_POSTGRES_PASSWORD_FILE_HOST": str(postgres_password),
        "EVA_HOME_ASSISTANT_URL": "http://home-assistant:8123",
        "EVA_HOME_ASSISTANT_EFFECTS_ENABLED": "false",
        "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST": "",
        "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST": str(home_assistant_token),
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST": str(dashboard_access_token),
        "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": "44444444-4444-4444-8444-444444444444",
        "EVA_IDENTITY_PACK_FILE_HOST": str(identity_pack),
        "EVA_IDENTITY_PACK_TRUST_FILE_HOST": str(identity_trust),
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": str(telegram_token),
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": str(telegram_bindings),
        "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST": str(telegram_pairing_secret),
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
        "EVA_HINDSIGHT_BANK_NAMESPACE": "eva2",
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


def test_runtime_environment_template_names_every_required_preflight_value() -> None:
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for raw_line in RUNTIME_ENVIRONMENT_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#") and "=" in line
    }

    assert set(REQUIRED_CONFIGURATION) <= set(values)
    assert {"EVA_DATABASE_DSN", "EVA_HOME_ASSISTANT_TOKEN", "EVA_TELEGRAM_BOT_TOKEN"}.isdisjoint(
        values
    )
    assert values["EVA_B_MANAGED_MANIFEST_SHA256"] == hashlib.sha256(
        MANAGED_B_MANIFEST.read_bytes()
    ).hexdigest()


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


def test_plan_refuses_its_derived_configuration_hash_in_the_snapshot(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values["EVA_B_CONFIGURATION_SHA256"] = "a" * 64
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match="derived and forbidden"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_plan_refuses_the_compose_required_hindsight_bank_namespace(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values.pop("EVA_HINDSIGHT_BANK_NAMESPACE")

    with pytest.raises(RuntimeError, match="EVA_HINDSIGHT_BANK_NAMESPACE is required"):
        load_core_b_plan(
            _environment_file(private_tmp_path, values).as_posix(),
            runtime_uid=os.geteuid(),
        )


def test_managed_service_labels_require_the_entire_sealed_graph(
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    labels = {
        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
        "io.eva2.configuration-sha256": plan.snapshot_sha256,
    }
    services = {
        name: dict(labels)
        for name in (
            "postgres",
            "schema-installer",
            "model-access-lease",
            "hindsight-access-lease",
            "hindsight",
            "core",
            "durable-goal-worker",
            "telegram-ingress",
        )
    }

    verify_managed_service_labels(plan, services)

    services.pop("telegram-ingress")
    with pytest.raises(RuntimeError, match="exact eight-service graph"):
        verify_managed_service_labels(plan, services)


def test_managed_service_labels_reject_a_service_from_another_snapshot(
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    labels = {
        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
        "io.eva2.configuration-sha256": plan.snapshot_sha256,
    }
    services = {
        name: dict(labels)
        for name in (
            "postgres",
            "schema-installer",
            "model-access-lease",
            "hindsight-access-lease",
            "hindsight",
            "core",
            "durable-goal-worker",
            "telegram-ingress",
        )
    }
    services["hindsight"]["io.eva2.configuration-sha256"] = "a" * 64

    with pytest.raises(RuntimeError, match="exact configuration snapshot"):
        verify_managed_service_labels(plan, services)


def test_steady_managed_service_labels_exclude_profiled_schema_installer(
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    labels = {
        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
        "io.eva2.configuration-sha256": plan.snapshot_sha256,
    }
    services = {
        name: dict(labels)
        for name in (
            "postgres",
            "model-access-lease",
            "hindsight-access-lease",
            "hindsight",
            "core",
            "durable-goal-worker",
            "telegram-ingress",
        )
    }

    verify_steady_managed_service_labels(plan, services)

    services["schema-installer"] = dict(labels)
    with pytest.raises(RuntimeError, match="exact seven-service steady graph"):
        verify_steady_managed_service_labels(plan, services)


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


def test_plan_validates_optional_hindsight_recall_limits_before_render(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values.update(
        {
            "EVA_HINDSIGHT_RECALL_BUDGET": "mid",
            "EVA_HINDSIGHT_RECALL_MAX_TOKENS": "3000",
            "EVA_HINDSIGHT_RECALL_NODE_BUDGET": "25",
        }
    )
    environment = _environment_file(private_tmp_path, values)

    plan = load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())

    assert plan.value("EVA_HINDSIGHT_RECALL_BUDGET") == "mid"
    assert plan.value("EVA_HINDSIGHT_RECALL_MAX_TOKENS") == "3000"
    assert plan.value("EVA_HINDSIGHT_RECALL_NODE_BUDGET") == "25"

    values["EVA_HINDSIGHT_RECALL_MAX_TOKENS"] = "4001"
    environment = _environment_file(private_tmp_path, values)
    with pytest.raises(RuntimeError, match="RECALL_MAX_TOKENS"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())

    values["EVA_HINDSIGHT_RECALL_MAX_TOKENS"] = "3000"
    values["EVA_HINDSIGHT_RECALL_NODE_BUDGET"] = "invalid"
    environment = _environment_file(private_tmp_path, values)
    with pytest.raises(RuntimeError, match="Hindsight recall configuration"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


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


def test_plan_requires_a_bounded_home_assistant_effect_allowlist(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values["EVA_HOME_ASSISTANT_EFFECTS_ENABLED"] = "true"
    environment = _environment_file(private_tmp_path, values)
    with pytest.raises(RuntimeError, match="EFFECT_ALLOWLIST is required"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())

    values["EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST"] = (
        "light.dimmable_light_1:turn_off"
    )
    environment = _environment_file(private_tmp_path, values)
    assert load_core_b_plan(
        environment.as_posix(), runtime_uid=os.geteuid()
    ).value("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST") == (
        "light.dimmable_light_1:turn_off"
    )

    values["EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST"] = "light.dimmable_light_1:toggle"
    environment = _environment_file(private_tmp_path, values)
    with pytest.raises(RuntimeError, match="EFFECT_ALLOWLIST is invalid"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


@pytest.mark.parametrize(
    "setting_name",
    (
        "EVA_DATABASE_DSN_FILE_HOST",
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST",
        "EVA_IDENTITY_PACK_FILE_HOST",
        "EVA_IDENTITY_PACK_TRUST_FILE_HOST",
        "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST",
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
        "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST",
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


def test_plan_rejects_an_invalid_dashboard_owner_binding(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    values["EVA_DASHBOARD_PRINCIPAL_BINDING_ID"] = "not-a-uuid"
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match="DASHBOARD_PRINCIPAL_BINDING_ID is invalid"):
        load_core_b_plan(environment.as_posix(), runtime_uid=os.geteuid())


def test_plan_rejects_identity_bytes_outside_the_bound_trust(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    identity_path = Path(values["EVA_IDENTITY_PACK_FILE_HOST"])
    identity_path.write_text('{"contract":"IdentityPack/v1"}\n', encoding="utf-8")
    identity_path.chmod(0o600)
    environment = _environment_file(private_tmp_path, values)

    with pytest.raises(RuntimeError, match="IdentityPack binding is invalid"):
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
        "EVA_IDENTITY_PACK_FILE_HOST",
        "EVA_IDENTITY_PACK_TRUST_FILE_HOST",
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


def test_host_only_receipt_has_no_secret_or_docker_observation(
    private_tmp_path: Path,
) -> None:
    values = _configuration(private_tmp_path)
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, values).as_posix(),
        runtime_uid=os.geteuid(),
    )

    receipt = host_metadata_preflight_receipt(plan)

    assert receipt == {
        "apply_supported": False,
        "checked": {
            "authority_directories": 3,
            "docker_binary_metadata": True,
            "docker_config_directory_metadata": True,
            "docker_observed": False,
            "host_secret_file_metadata": 9,
            "secret_contents_read": False,
        },
        "configuration_sha256": plan.snapshot_sha256,
        "contract": "EvaManagedBHostMetadataPreflightReceipt/v1",
        "next_step": "read-only sealed Compose render and independent manifest/adoption review",
        "status": "passed_host_metadata_only",
    }
    rendered = json.dumps(receipt, sort_keys=True)
    assert str(private_tmp_path) not in rendered
    assert "not-a-real-token" not in rendered
    assert "not-a-real-dsn" not in rendered
    assert "not-a-real-postgres-password" not in rendered


def test_host_only_cli_never_observes_docker_or_b(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    monkeypatch.setattr(
        core_b_preflight,
        "load_core_b_plan",
        lambda _environment_file: plan,
    )
    monkeypatch.setattr(
        core_b_preflight,
        "observe_current_b",
        lambda _plan: pytest.fail("host-only mode must not observe Docker or B"),
    )

    assert core_b_preflight.main(["--host-only", "/private/eva2-b-runtime.env"]) == 0

    assert json.loads(capsys.readouterr().out) == host_metadata_preflight_receipt(plan)


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


def test_managed_service_observation_reads_seven_steady_labeled_containers(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    commands: list[list[str]] = []
    names = (
        "postgres",
        "model-access-lease",
        "hindsight-access-lease",
        "hindsight",
        "core",
        "durable-goal-worker",
        "telegram-ingress",
    )

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if len(commands) == 1:
            return SimpleNamespace(
                stdout=(
                    '[{"Name":"eva2-b-local","Endpoints":{"docker":'
                    '{"Host":"unix:///var/run/docker.sock"}}}]'
                )
            )
        if len(commands) == 2:
            return SimpleNamespace(stdout="\n".join(f"container-{index}" for index in range(7)))
        return SimpleNamespace(
            stdout="\n".join(
                json.dumps(
                    {
                        "com.docker.compose.project": "eva2-b-live",
                        "com.docker.compose.service": name,
                        "io.eva2.configuration-sha256": plan.snapshot_sha256,
                        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
                    }
                )
                for name in names
            )
        )

    monkeypatch.setattr("eva.operations.core_b_preflight.subprocess.run", fake_run)

    observe_managed_b_services(plan)

    assert commands[1][-8:] == [
        "ps",
        "--all",
        "--filter",
        "label=io.eva2.managed-by=eva2-explicit-b-manifest-v1",
        "--filter",
        f"label=io.eva2.configuration-sha256={plan.snapshot_sha256}",
        "--format",
        "{{.ID}}",
    ]
    assert commands[2][-12:] == [
        "inspect",
        "--type",
        "container",
        "--format",
        "{{json .Config.Labels}}",
        "container-0",
        "container-1",
        "container-2",
        "container-3",
        "container-4",
        "container-5",
        "container-6",
    ]


def test_managed_service_observation_rejects_another_compose_project(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    plan = load_core_b_plan(
        _environment_file(private_tmp_path, _configuration(private_tmp_path)).as_posix(),
        runtime_uid=os.geteuid(),
    )
    calls = 0

    def fake_run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                stdout=(
                    '[{"Name":"eva2-b-local","Endpoints":{"docker":'
                    '{"Host":"unix:///var/run/docker.sock"}}}]'
                )
            )
        if calls == 2:
            return SimpleNamespace(stdout="\n".join(f"container-{index}" for index in range(7)))
        return SimpleNamespace(
            stdout="\n".join(
                json.dumps(
                    {
                        "com.docker.compose.project": "wrong-project",
                        "com.docker.compose.service": f"service-{index}",
                        "io.eva2.configuration-sha256": plan.snapshot_sha256,
                        "io.eva2.managed-by": "eva2-explicit-b-manifest-v1",
                    }
                )
                for index in range(7)
            )
        )

    monkeypatch.setattr("eva.operations.core_b_preflight.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="different Compose project"):
        observe_managed_b_services(plan)
