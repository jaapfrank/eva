"""Read-only preflight for a future explicitly managed Living Core B."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import NoReturn
from uuid import UUID

from eva.bootstrap.secrets import (
    MAX_RUNTIME_SECRET_BYTES,
    read_private_runtime_file_bytes,
)


MANAGEMENT_LABEL = "io.eva2.managed-by"
MANAGEMENT_VALUE = "eva2-explicit-b-manifest-v1"
SNAPSHOT_LABEL = "io.eva2.configuration-sha256"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
MANAGED_SERVICE_NAMES = frozenset(
    {
        "postgres",
        "schema-installer",
        "model-access-lease",
        "hindsight-access-lease",
        "hindsight",
        "core",
        "durable-goal-worker",
        "telegram-ingress",
    }
)
STEADY_MANAGED_SERVICE_NAMES = MANAGED_SERVICE_NAMES - {"schema-installer"}
ENVIRONMENT_NAME = "explicit B deployment environment"
MANAGED_WORK_VOLUME = "eva2-living-work-b"
MANAGED_POSTGRES_VOLUME = "eva2-postgres-b-live"
MODEL_AUTHORITY_UID = 1000
MODEL_AUTHORITY_GID = 1000

REQUIRED_CONFIGURATION = (
    "EVA_B_DOCKER_BINARY",
    "EVA_B_DOCKER_CONFIG_DIR",
    "EVA_B_DOCKER_CONTEXT",
    "EVA_B_DOCKER_DAEMON",
    "EVA_B_COMPOSE_PROJECT",
    "EVA_B_MANAGED_MANIFEST_SHA256",
    "EVA_STACK_CORE_CONTAINER",
    "EVA_STACK_BIND_ADDRESS",
    "EVA_LIVING_CORE_IMAGE",
    "EVA_DATABASE_DSN_FILE_HOST",
    "EVA_POSTGRES_PASSWORD_FILE_HOST",
    "EVA_HOME_ASSISTANT_URL",
    "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST",
    "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST",
    "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
    "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST",
    "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
    "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST",
    "EVA_RUNTIME_TENANT_ID",
    "EVA_RUNTIME_INSTALLATION_ID",
    "EVA_RUNTIME_PRINCIPAL_ID",
    "EVA_DURABLE_GOAL_PROVIDER",
    "EVA_DURABLE_GOAL_PROVIDER_URL",
    "EVA_DURABLE_GOAL_RECIPIENTS",
    "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON",
    "EVA_FANOUT_ALLOW_INSECURE_HTTP",
    "EVA_FILE_BATCH_ROOT",
    "EVA_STACK_WORK_VOLUME",
    "EVA_STACK_POSTGRES_VOLUME",
    "EVA_STACK_HINDSIGHT_VOLUME",
    "EVA_HINDSIGHT_BANK_NAMESPACE",
    "EVA_CODEX_AUTH_DIR",
    "EVA_MODEL_ACCESS_LEASE_DIR",
    "EVA_HINDSIGHT_ACCESS_DIR",
)

# This value is derived from the exact private environment snapshot.  Keeping it
# in that snapshot would make its own hash self-referential and unverifiable.
DERIVED_CONFIGURATION = ("EVA_B_CONFIGURATION_SHA256",)

FORBIDDEN_INLINE_SECRETS = (
    "EVA_DATABASE_DSN",
    "EVA_HOME_ASSISTANT_TOKEN",
    "EVA_DASHBOARD_ACCESS_TOKEN",
    "EVA_TELEGRAM_BOT_TOKEN",
    "EVA_STRIPE_SECRET_KEY",
    "EVA_STRIPE_WEBHOOK_SECRET",
)

HOST_SECRET_SETTING_NAMES = (
    "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST",
    "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST",
    "EVA_DATABASE_DSN_FILE_HOST",
    "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST",
    "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
    "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST",
    "EVA_POSTGRES_PASSWORD_FILE_HOST",
)
HOST_DIRECTORY_SETTING_NAMES = (
    "EVA_CODEX_AUTH_DIR",
    "EVA_MODEL_ACCESS_LEASE_DIR",
    "EVA_HINDSIGHT_ACCESS_DIR",
)


@dataclass(frozen=True, slots=True)
class CoreBPlan:
    configuration: tuple[tuple[str, str], ...]
    snapshot_sha256: str

    def value(self, name: str) -> str:
        for key, value in self.configuration:
            if key == name:
                return value
        raise RuntimeError(f"{name} is missing from the explicit B configuration")


def _parse_environment(snapshot: bytes) -> dict[str, str]:
    try:
        decoded = snapshot.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{ENVIRONMENT_NAME} must contain UTF-8 text") from error
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(decoded.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw_line:
            raise RuntimeError(
                f"{ENVIRONMENT_NAME} line {line_number} is not KEY=value"
            )
        raw_key, raw_value = raw_line.split("=", 1)
        key = raw_key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise RuntimeError(
                f"{ENVIRONMENT_NAME} line {line_number} has an invalid key"
            )
        if key in values:
            raise RuntimeError(f"{ENVIRONMENT_NAME} repeats {key}")
        if "\x00" in raw_value:
            raise RuntimeError(f"{ENVIRONMENT_NAME} contains a NUL byte")
        values[key] = raw_value.strip()
    return values


def _require_absolute(value: str, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path")
    return path


def _preflight_host_secret(
    path_value: str,
    *,
    setting_name: str,
    owner_uid: int,
    required_mode: int | None = None,
) -> None:
    path = _require_absolute(path_value, setting_name)
    try:
        named = os.lstat(path)
    except OSError as error:
        raise RuntimeError(f"{setting_name} is inaccessible") from error
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise RuntimeError(f"{setting_name} must be a regular non-symlink file")
    permissions = stat.S_IMODE(named.st_mode)
    if named.st_uid != owner_uid:
        raise RuntimeError(f"{setting_name} must be owned by UID {owner_uid}")
    if not permissions & stat.S_IRUSR:
        raise RuntimeError(f"{setting_name} owner must have read access")
    if permissions & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(f"{setting_name} must have no group/other permissions")
    if required_mode is not None and permissions != required_mode:
        raise RuntimeError(f"{setting_name} permissions differ from its contract")
    if not 0 < named.st_size <= MAX_RUNTIME_SECRET_BYTES:
        raise RuntimeError(f"{setting_name} size must be within 1..65536")

    if not hasattr(os, "O_PATH"):
        raise RuntimeError("safe metadata-only secret preflight is unavailable")
    flags = os.O_PATH | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"{setting_name} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_mode,
            opened.st_size,
            opened.st_ctime_ns,
        ) != (
            named.st_dev,
            named.st_ino,
            named.st_uid,
            named.st_mode,
            named.st_size,
            named.st_ctime_ns,
        ):
            raise RuntimeError(f"{setting_name} changed during preflight")
    finally:
        os.close(descriptor)


def _preflight_host_directory(
    path_value: str,
    *,
    setting_name: str,
    owner_uid: int,
    group_gid: int,
    required_mode: int,
) -> None:
    path = _require_absolute(path_value, setting_name)
    try:
        named = os.lstat(path)
    except OSError as error:
        raise RuntimeError(f"{setting_name} is inaccessible") from error
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise RuntimeError(f"{setting_name} must be a regular non-symlink directory")
    if named.st_uid != owner_uid or named.st_gid != group_gid:
        raise RuntimeError(f"{setting_name} ownership differs from its contract")
    if stat.S_IMODE(named.st_mode) != required_mode:
        raise RuntimeError(f"{setting_name} permissions differ from its contract")
    if not hasattr(os, "O_PATH"):
        raise RuntimeError("safe metadata-only directory preflight is unavailable")
    flags = os.O_PATH | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"{setting_name} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_gid,
            opened.st_mode,
            opened.st_ctime_ns,
        ) != (
            named.st_dev,
            named.st_ino,
            named.st_uid,
            named.st_gid,
            named.st_mode,
            named.st_ctime_ns,
        ):
            raise RuntimeError(f"{setting_name} changed during preflight")
    finally:
        os.close(descriptor)


def _validate_configuration(values: dict[str, str]) -> None:
    for derived in DERIVED_CONFIGURATION:
        if derived in values:
            raise RuntimeError(
                f"{derived} is derived and forbidden in the explicit B environment"
            )
    for forbidden in FORBIDDEN_INLINE_SECRETS:
        if forbidden in values:
            raise RuntimeError(
                f"{forbidden} is forbidden in the explicit B environment"
            )
    for required in REQUIRED_CONFIGURATION:
        if not values.get(required):
            raise RuntimeError(f"{required} is required for capability preservation")

    if values["EVA_B_COMPOSE_PROJECT"] != "eva2-b-live":
        raise RuntimeError("EVA_B_COMPOSE_PROJECT must be exactly eva2-b-live")
    if not re.fullmatch(r"[0-9a-f]{64}", values["EVA_B_MANAGED_MANIFEST_SHA256"]):
        raise RuntimeError("EVA_B_MANAGED_MANIFEST_SHA256 must be a lowercase sha256")
    if values["EVA_STACK_CORE_CONTAINER"] != "eva2-living-core-b":
        raise RuntimeError(
            "EVA_STACK_CORE_CONTAINER must be exactly eva2-living-core-b"
        )
    if values["EVA_STACK_BIND_ADDRESS"] != "127.0.0.1":
        raise RuntimeError("EVA_STACK_BIND_ADDRESS must be exactly 127.0.0.1")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", values["EVA_LIVING_CORE_IMAGE"]):
        raise RuntimeError("EVA_LIVING_CORE_IMAGE must use an exact sha256 digest")
    if values["EVA_FILE_BATCH_ROOT"] != "/var/lib/eva/work":
        raise RuntimeError("EVA_FILE_BATCH_ROOT must preserve /var/lib/eva/work")
    if values["EVA_STACK_WORK_VOLUME"] != MANAGED_WORK_VOLUME:
        raise RuntimeError(f"EVA_STACK_WORK_VOLUME must be exactly {MANAGED_WORK_VOLUME}")
    if values["EVA_STACK_POSTGRES_VOLUME"] != MANAGED_POSTGRES_VOLUME:
        raise RuntimeError(
            "EVA_STACK_POSTGRES_VOLUME must be exactly "
            f"{MANAGED_POSTGRES_VOLUME}"
        )
    if not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}",
        values["EVA_STACK_HINDSIGHT_VOLUME"],
    ):
        raise RuntimeError("EVA_STACK_HINDSIGHT_VOLUME is invalid")
    docker_binary = _require_absolute(
        values["EVA_B_DOCKER_BINARY"], "EVA_B_DOCKER_BINARY"
    )
    if docker_binary != Path("/usr/bin/docker"):
        raise RuntimeError("EVA_B_DOCKER_BINARY must be exactly /usr/bin/docker")
    try:
        docker_metadata = os.lstat(docker_binary)
    except OSError as error:
        raise RuntimeError("/usr/bin/docker is inaccessible") from error
    docker_permissions = stat.S_IMODE(docker_metadata.st_mode)
    if stat.S_ISLNK(docker_metadata.st_mode) or not stat.S_ISREG(
        docker_metadata.st_mode
    ):
        raise RuntimeError("/usr/bin/docker must be a regular non-symlink file")
    if docker_metadata.st_uid != 0:
        raise RuntimeError("/usr/bin/docker must be root-owned")
    if not docker_permissions & stat.S_IXUSR:
        raise RuntimeError("/usr/bin/docker must be owner-executable")
    if docker_permissions & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("/usr/bin/docker must not be group/other-writable")
    docker_config = _require_absolute(
        values["EVA_B_DOCKER_CONFIG_DIR"], "EVA_B_DOCKER_CONFIG_DIR"
    )
    try:
        config_metadata = os.lstat(docker_config)
    except OSError as error:
        raise RuntimeError("EVA_B_DOCKER_CONFIG_DIR is inaccessible") from error
    if stat.S_ISLNK(config_metadata.st_mode) or not stat.S_ISDIR(
        config_metadata.st_mode
    ):
        raise RuntimeError(
            "EVA_B_DOCKER_CONFIG_DIR must be a regular non-symlink directory"
        )
    config_permissions = stat.S_IMODE(config_metadata.st_mode)
    if config_metadata.st_uid != os.geteuid():
        raise RuntimeError("EVA_B_DOCKER_CONFIG_DIR must be operator-owned")
    if not config_permissions & stat.S_IRUSR or not config_permissions & stat.S_IXUSR:
        raise RuntimeError("EVA_B_DOCKER_CONFIG_DIR must be owner-readable/searchable")
    if config_permissions & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError("EVA_B_DOCKER_CONFIG_DIR must be private")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", values["EVA_B_DOCKER_CONTEXT"]):
        raise RuntimeError("EVA_B_DOCKER_CONTEXT is invalid")
    if not values["EVA_B_DOCKER_DAEMON"].startswith(("unix://", "ssh://")):
        raise RuntimeError("EVA_B_DOCKER_DAEMON must pin a unix:// or ssh:// endpoint")

    try:
        fanout = json.loads(values["EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON"])
    except json.JSONDecodeError as error:
        raise RuntimeError("EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON is invalid") from error
    if not isinstance(fanout, dict) or not fanout or len(fanout) > 8:
        raise RuntimeError("Eva fan-out capability must preserve 1..8 recipients")
    if values["EVA_FANOUT_ALLOW_INSECURE_HTTP"] not in {"true", "false"}:
        raise RuntimeError("EVA_FANOUT_ALLOW_INSECURE_HTTP must be true or false")
    if not any(
        recipient.strip() for recipient in values["EVA_DURABLE_GOAL_RECIPIENTS"].split(",")
    ):
        raise RuntimeError("Eva durable-goal recipients must be preserved")
    try:
        UUID(values["EVA_DASHBOARD_PRINCIPAL_BINDING_ID"])
    except ValueError as error:
        raise RuntimeError("EVA_DASHBOARD_PRINCIPAL_BINDING_ID is invalid") from error

    recall_budget = values.get("EVA_HINDSIGHT_RECALL_BUDGET", "low").casefold()
    if recall_budget not in {"low", "mid", "high"}:
        raise RuntimeError("EVA_HINDSIGHT_RECALL_BUDGET is invalid")
    try:
        recall_max_tokens = int(
            values.get("EVA_HINDSIGHT_RECALL_MAX_TOKENS", "2048")
        )
        recall_node_budget = int(
            values.get("EVA_HINDSIGHT_RECALL_NODE_BUDGET", "40")
        )
    except ValueError as error:
        raise RuntimeError("Eva Hindsight recall configuration is invalid") from error
    if not 2_000 <= recall_max_tokens <= 4_000:
        raise RuntimeError(
            "EVA_HINDSIGHT_RECALL_MAX_TOKENS must be within 2000..4000"
        )
    if not 20 <= recall_node_budget <= 100:
        raise RuntimeError(
            "EVA_HINDSIGHT_RECALL_NODE_BUDGET must be within 20..100"
        )


def load_core_b_plan(
    environment_file: str,
    *,
    runtime_uid: int = 65532,
    operator_uid: int | None = None,
    postgres_password_owner_uid: int = 0,
) -> CoreBPlan:
    """Create one immutable in-memory plan from one hardened file snapshot."""

    snapshot = read_private_runtime_file_bytes(
        environment_file,
        setting_name=ENVIRONMENT_NAME,
        effective_uid=operator_uid,
    )
    if not snapshot.strip():
        raise RuntimeError(f"{ENVIRONMENT_NAME} is empty")
    values = _parse_environment(snapshot)
    _validate_configuration(values)
    _preflight_host_secret(
        values["EVA_HOME_ASSISTANT_TOKEN_FILE_HOST"],
        setting_name="EVA_HOME_ASSISTANT_TOKEN_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST"],
        setting_name="EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_DATABASE_DSN_FILE_HOST"],
        setting_name="EVA_DATABASE_DSN_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_TELEGRAM_BOT_TOKEN_FILE_HOST"],
        setting_name="EVA_TELEGRAM_BOT_TOKEN_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST"],
        setting_name="EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST"],
        setting_name="EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST",
        owner_uid=runtime_uid,
    )
    _preflight_host_secret(
        values["EVA_POSTGRES_PASSWORD_FILE_HOST"],
        setting_name="EVA_POSTGRES_PASSWORD_FILE_HOST",
        owner_uid=postgres_password_owner_uid,
        required_mode=0o600,
    )
    _preflight_host_directory(
        values["EVA_CODEX_AUTH_DIR"],
        setting_name="EVA_CODEX_AUTH_DIR",
        owner_uid=MODEL_AUTHORITY_UID,
        group_gid=MODEL_AUTHORITY_GID,
        required_mode=0o700,
    )
    _preflight_host_directory(
        values["EVA_MODEL_ACCESS_LEASE_DIR"],
        setting_name="EVA_MODEL_ACCESS_LEASE_DIR",
        owner_uid=MODEL_AUTHORITY_UID,
        group_gid=runtime_uid,
        required_mode=0o2750,
    )
    _preflight_host_directory(
        values["EVA_HINDSIGHT_ACCESS_DIR"],
        setting_name="EVA_HINDSIGHT_ACCESS_DIR",
        owner_uid=MODEL_AUTHORITY_UID,
        group_gid=MODEL_AUTHORITY_GID,
        required_mode=0o700,
    )
    return CoreBPlan(
        tuple(sorted(values.items())),
        hashlib.sha256(snapshot).hexdigest(),
    )


def host_metadata_preflight_receipt(plan: CoreBPlan) -> dict[str, object]:
    """Return a secret-free receipt after only host metadata was checked.

    ``load_core_b_plan`` has already checked the exact private configuration
    snapshot, every required secret file's metadata and every required authority
    directory.  This receipt deliberately does *not* inspect Docker, B, a
    container, a volume or a secret's contents.  It exists so an operator can
    close the host-file prerequisite before a managed B graph exists.
    """

    return {
        "apply_supported": False,
        "checked": {
            "docker_observed": False,
            "host_directories": len(HOST_DIRECTORY_SETTING_NAMES),
            "host_secret_file_metadata": len(HOST_SECRET_SETTING_NAMES),
            "secret_contents_read": False,
        },
        "configuration_sha256": plan.snapshot_sha256,
        "contract": "EvaManagedBHostMetadataPreflightReceipt/v1",
        "next_step": "read-only sealed Compose render and independent manifest/adoption review",
        "status": "passed_host_metadata_only",
    }


def verify_docker_endpoint(
    plan: CoreBPlan,
    observed_context: str,
    observed_daemon: str,
) -> None:
    expected_context = plan.value("EVA_B_DOCKER_CONTEXT")
    if observed_context != expected_context:
        raise RuntimeError("Docker returned a different context than the explicit B plan")
    expected = plan.value("EVA_B_DOCKER_DAEMON")
    if observed_daemon != expected:
        raise RuntimeError(
            "Docker context resolves to a different daemon than the explicit B plan"
        )


def verify_current_management(plan: CoreBPlan, labels: object) -> None:
    if not isinstance(labels, dict):
        raise RuntimeError("current B container has no verifiable management labels")
    if labels.get(MANAGEMENT_LABEL) != MANAGEMENT_VALUE:
        raise RuntimeError(
            "current B container is unmanaged; controlled adoption requires a separate review"
        )
    if labels.get(SNAPSHOT_LABEL) != plan.snapshot_sha256:
        raise RuntimeError(
            "current B container was not created from this exact configuration snapshot"
        )


def verify_managed_service_labels(
    plan: CoreBPlan,
    labels_by_service: object,
) -> None:
    """Require every future managed-B service to prove the same sealed origin."""

    _verify_service_labels(
        plan,
        labels_by_service,
        expected_services=MANAGED_SERVICE_NAMES,
        graph_name="exact eight-service graph",
    )


def verify_steady_managed_service_labels(
    plan: CoreBPlan,
    labels_by_service: object,
) -> None:
    """Require the seven continuously running services after schema setup."""

    _verify_service_labels(
        plan,
        labels_by_service,
        expected_services=STEADY_MANAGED_SERVICE_NAMES,
        graph_name="exact seven-service steady graph",
    )


def _verify_service_labels(
    plan: CoreBPlan,
    labels_by_service: object,
    *,
    expected_services: frozenset[str],
    graph_name: str,
) -> None:
    if not isinstance(labels_by_service, dict) or set(labels_by_service) != expected_services:
        raise RuntimeError(f"managed B does not contain its {graph_name}")
    for service_name, labels in labels_by_service.items():
        if not isinstance(labels, dict):
            raise RuntimeError(f"managed B service {service_name} has no verifiable labels")
        verify_current_management(plan, labels)


def _run_docker(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("read-only Docker B preflight failed") from error
    return completed.stdout.strip()


def _docker_context_output(plan: CoreBPlan, arguments: list[str]) -> str:
    command = [
        plan.value("EVA_B_DOCKER_BINARY"),
        "--config",
        plan.value("EVA_B_DOCKER_CONFIG_DIR"),
        "--context",
        plan.value("EVA_B_DOCKER_CONTEXT"),
        *arguments,
    ]
    return _run_docker(command)


def _docker_host_output(
    plan: CoreBPlan,
    verified_daemon: str,
    arguments: list[str],
) -> str:
    """Read container state only through the already verified daemon endpoint."""

    if verified_daemon != plan.value("EVA_B_DOCKER_DAEMON"):
        raise RuntimeError("container read attempted with an unverified Docker daemon")
    command = [
        plan.value("EVA_B_DOCKER_BINARY"),
        "--config",
        plan.value("EVA_B_DOCKER_CONFIG_DIR"),
        "--host",
        verified_daemon,
        *arguments,
    ]
    return _run_docker(command)


def observe_current_b(plan: CoreBPlan) -> None:
    observed_daemon = _verified_docker_daemon(plan)
    labels_raw = _docker_host_output(
        plan,
        observed_daemon,
        [
            "inspect",
            "--type",
            "container",
            "--format",
            "{{json .Config.Labels}}",
            plan.value("EVA_STACK_CORE_CONTAINER"),
        ],
    )
    try:
        labels = json.loads(labels_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("current B container returned invalid labels") from error
    verify_current_management(plan, labels)


def _verified_docker_daemon(plan: CoreBPlan) -> str:
    context_raw = _docker_context_output(
        plan,
        [
            "context",
            "inspect",
            plan.value("EVA_B_DOCKER_CONTEXT"),
        ],
    )
    try:
        contexts = json.loads(context_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Docker context returned invalid endpoint metadata") from error
    if not isinstance(contexts, list) or len(contexts) != 1:
        raise RuntimeError("Docker context read did not return exactly one context")
    context = contexts[0]
    if not isinstance(context, dict):
        raise RuntimeError("Docker context returned no pinned daemon endpoint")
    observed_context = context.get("Name")
    endpoints = context.get("Endpoints")
    observed_daemon = (
        endpoints.get("docker", {}).get("Host")
        if isinstance(endpoints, dict)
        and isinstance(endpoints.get("docker"), dict)
        else None
    )
    if not isinstance(observed_context, str) or not isinstance(observed_daemon, str):
        raise RuntimeError("Docker context returned no pinned daemon endpoint")
    verify_docker_endpoint(plan, observed_context, observed_daemon)
    return observed_daemon


def observe_managed_b_services(plan: CoreBPlan) -> None:
    """Read all future B containers and prove the complete sealed service graph."""

    observed_daemon = _verified_docker_daemon(plan)
    container_ids = _docker_host_output(
        plan,
        observed_daemon,
        [
            "ps",
            "--all",
            "--filter",
            f"label={MANAGEMENT_LABEL}={MANAGEMENT_VALUE}",
            "--filter",
            f"label={SNAPSHOT_LABEL}={plan.snapshot_sha256}",
            "--format",
            "{{.ID}}",
        ],
    )
    identifiers = tuple(value for value in container_ids.splitlines() if value)
    if len(identifiers) != len(STEADY_MANAGED_SERVICE_NAMES):
        raise RuntimeError("managed B does not expose exactly seven labeled containers")
    labels_raw = _docker_host_output(
        plan,
        observed_daemon,
        [
            "inspect",
            "--type",
            "container",
            "--format",
            "{{json .Config.Labels}}",
            *identifiers,
        ],
    )
    labels_by_service: dict[str, object] = {}
    for line in labels_raw.splitlines():
        try:
            labels = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("managed B container returned invalid labels") from error
        if not isinstance(labels, dict):
            raise RuntimeError("managed B container has no verifiable labels")
        if labels.get(COMPOSE_PROJECT_LABEL) != plan.value("EVA_B_COMPOSE_PROJECT"):
            raise RuntimeError("managed B container belongs to a different Compose project")
        service = labels.get(COMPOSE_SERVICE_LABEL)
        if not isinstance(service, str):
            raise RuntimeError("managed B container has no Compose service label")
        if service in labels_by_service:
            raise RuntimeError("managed B has duplicate Compose service labels")
        labels_by_service[service] = labels
    verify_steady_managed_service_labels(plan, labels_by_service)


def _refuse(message: str) -> NoReturn:
    raise SystemExit(f"core-B plan refused: {message}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only preflight; this command cannot recreate containers."
    )
    parser.add_argument(
        "--host-only",
        action="store_true",
        help="check only the private configuration and host-file metadata; never read Docker or B",
    )
    parser.add_argument("environment_file")
    arguments = parser.parse_args(argv)
    try:
        plan = load_core_b_plan(arguments.environment_file)
        if arguments.host_only:
            receipt = host_metadata_preflight_receipt(plan)
        else:
            observe_current_b(plan)
            receipt = {
                "apply_supported": False,
                "configuration_sha256": plan.snapshot_sha256,
                "current_b_managed": True,
                "next_step": "independent manifest/adoption review",
                "status": "passed_current_managed_b_observation",
            }
    except RuntimeError as error:
        _refuse(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
