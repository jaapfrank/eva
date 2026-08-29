#!/usr/bin/env python3
"""Arm and verify a managed Eva2 reboot without rebooting the caller.

``arm`` records a private pre-reboot state and one synthetic durable turn.
``verify`` must run in a later process (normally a systemd oneshot) and writes
one new, secret-free receipt.  This program deliberately has no reboot or WSL
termination command.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from typing import Any, NoReturn
from urllib import error, request


CONTRACT_ARM = "Eva2ManagedBRebootArm/v1"
CONTRACT_RECEIPT = "Eva2ManagedBPostbootReceipt/v1"
ACTIVE_PORT = 44711
ROLLBACK_PORT = 44710
EVA1_PORT = 3210
ROUTE_DROPIN = Path("/etc/systemd/system/eva.service.d/30-eva2-current-candidate.conf")
DASHBOARD_TOKEN = Path("/var/lib/eva2/managed-b-slot4/secrets/dashboard-token")
POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
CONTAINERS = {
    "core": ("eva2-managed-b-slot4-core", "running", True),
    "hindsight": ("eva2-managed-b-slot4-hindsight-1", "running", True),
    "hindsight_access_lease": (
        "eva2-managed-b-slot4-hindsight-access-lease-1",
        "running",
        False,
    ),
    "hindsight_postgres": (
        "eva2-managed-b-slot4-hindsight-postgres-1",
        "running",
        True,
    ),
    "model_access_lease": (
        "eva2-managed-b-slot4-model-access-lease-1",
        "running",
        False,
    ),
    "postgres": ("eva2-managed-b-slot4-postgres-1", "running", True),
    "rollback_core": ("eva2-managed-b-slot3-core", "exited", False),
    "rollback_hindsight": ("eva2-managed-b-slot3-hindsight-1", "exited", False),
    "rollback_hindsight_access_lease": (
        "eva2-managed-b-slot3-hindsight-access-lease-1",
        "running",
        False,
    ),
    "rollback_hindsight_postgres": (
        "eva2-managed-b-slot3-hindsight-postgres-1",
        "running",
        True,
    ),
    "rollback_model_access_lease": (
        "eva2-managed-b-slot3-model-access-lease-1",
        "running",
        False,
    ),
    "rollback_postgres": (
        "eva2-managed-b-slot3-postgres-1",
        "running",
        True,
    ),
}
EVA1_ROUTES = (
    "/",
    "/api/overview",
    "/api/workstatus",
    "/api/capabilities",
    "/api/health",
)
_WINDOWS_BOOT_COMMAND = (
    str(POWERSHELL),
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime."
    "ToUniversalTime().ToString('o')",
)
_WINDOWS_BOOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
_BOOT_ID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_MARKER_RE = re.compile(r"^[a-z]+ [a-z]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESTART_RANK = {
    "none": 0,
    "wsl_systemd_restart": 1,
    "wsl_kernel_restart": 2,
    "windows_host_reboot": 3,
}
_ADJECTIVES = (
    "amberen",
    "koperen",
    "paarse",
    "robijnen",
    "zilveren",
)
_NOUNS = ("kompas", "maan", "paraplu", "rivier", "wolk")


class RebootProbeError(RuntimeError):
    """A fail-closed probe failure safe to show to an operator."""


@dataclass(frozen=True)
class BootIdentity:
    windows_boot_utc: str
    kernel_boot_id: str
    pid1_start_ticks: int


def _run(command: list[str] | tuple[str, ...], *, timeout: int = 20) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RebootProbeError(f"local check failed: {Path(command[0]).name}") from exc
    return completed.stdout.strip()


def _pid1_start_ticks(raw: str) -> int:
    closing = raw.rfind(")")
    fields = raw[closing + 1 :].split() if closing >= 0 else []
    if len(fields) < 20 or not fields[19].isdigit():
        raise RebootProbeError("PID 1 identity is invalid")
    return int(fields[19])


def _read_boot_identity() -> BootIdentity:
    try:
        kernel_boot_id = (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        )
        pid1_stat = Path("/proc/1/stat").read_text(encoding="ascii")
    except OSError as exc:
        raise RebootProbeError("Linux boot identity is unavailable") from exc
    windows_boot_utc = _run(_WINDOWS_BOOT_COMMAND).replace("\r", "").strip()
    if not _BOOT_ID_RE.fullmatch(kernel_boot_id):
        raise RebootProbeError("Linux kernel boot identity is invalid")
    if not _WINDOWS_BOOT_RE.fullmatch(windows_boot_utc):
        raise RebootProbeError("Windows host boot identity is invalid")
    return BootIdentity(
        windows_boot_utc=windows_boot_utc,
        kernel_boot_id=kernel_boot_id,
        pid1_start_ticks=_pid1_start_ticks(pid1_stat),
    )


def classify_restart(before: BootIdentity, after: BootIdentity) -> str:
    if after.windows_boot_utc != before.windows_boot_utc:
        return "windows_host_reboot"
    if after.kernel_boot_id != before.kernel_boot_id:
        return "wsl_kernel_restart"
    if after.pid1_start_ticks != before.pid1_start_ticks:
        return "wsl_systemd_restart"
    return "none"


def _strict_read(
    path: Path,
    *,
    allowed_uids: set[int],
    max_bytes: int,
    private: bool,
) -> bytes:
    if not path.is_absolute():
        raise RebootProbeError("private input path must be absolute")
    try:
        before = path.lstat()
    except OSError as exc:
        raise RebootProbeError("required private input is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid not in allowed_uids
        or before.st_size < 1
        or before.st_size > max_bytes
        or (private and stat.S_IMODE(before.st_mode) & 0o077)
        or (not private and stat.S_IMODE(before.st_mode) & 0o022)
    ):
        raise RebootProbeError("required input metadata is unsafe")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RebootProbeError("required input could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RebootProbeError("required input changed while opening")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise RebootProbeError("required input is too large")
        return payload
    finally:
        os.close(descriptor)


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute() or path.suffix != ".json":
        raise RebootProbeError("output must be an absolute new .json path")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise RebootProbeError("output directory is unavailable") from exc
    if not stat.S_ISDIR(parent.st_mode) or path.parent.resolve() != path.parent:
        raise RebootProbeError("output directory is unsafe")
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RebootProbeError("output must not already exist") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise RebootProbeError("output write was incomplete")
            view = view[written:]
        os.fsync(descriptor)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            raise RebootProbeError("output metadata differs")
    finally:
        os.close(descriptor)


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    bearer: str | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    http_request = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read(1_048_577)
            status_code = response.status
    except error.HTTPError as exc:
        raw = exc.read(1_048_577)
        status_code = exc.code
    except (OSError, error.URLError) as exc:
        raise RebootProbeError("local HTTP check failed") from exc
    if len(raw) > 1_048_576:
        raise RebootProbeError("local HTTP response is too large")
    try:
        decoded = json.loads(raw) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RebootProbeError("local HTTP response is invalid") from exc
    if not isinstance(decoded, dict):
        raise RebootProbeError("local HTTP response differs")
    return status_code, decoded


def _status_only(url: str, *, timeout: float = 20) -> int:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            response.read(1)
            return response.status
    except error.HTTPError as exc:
        return exc.code
    except (OSError, error.URLError) as exc:
        raise RebootProbeError("local route check failed") from exc


def _dashboard_token() -> str:
    raw = _strict_read(
        DASHBOARD_TOKEN,
        allowed_uids={65532},
        max_bytes=4096,
        private=True,
    )
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RebootProbeError("dashboard token is invalid") from exc
    if not token or any(character.isspace() for character in token):
        raise RebootProbeError("dashboard token is invalid")
    return token


def _route_sha256() -> str:
    raw = _strict_read(
        ROUTE_DROPIN,
        allowed_uids={0},
        max_bytes=4096,
        private=False,
    )
    expected = f"Environment=EVA2_CANARY_URL=http://127.0.0.1:{ACTIVE_PORT}".encode()
    if expected not in raw.splitlines():
        raise RebootProbeError("loaded Eva2 route is not the managed candidate")
    return hashlib.sha256(raw).hexdigest()


def _service_snapshot() -> dict[str, Any]:
    for service in ("docker.service", "eva.service"):
        if _run(["/usr/bin/systemctl", "is-active", service]) != "active":
            raise RebootProbeError(f"{service} is not active")
        if _run(["/usr/bin/systemctl", "is-enabled", service]) != "enabled":
            raise RebootProbeError(f"{service} is not enabled")
    raw_restarts = _run(
        ["/usr/bin/systemctl", "show", "eva.service", "--property=NRestarts", "--value"]
    )
    if not raw_restarts.isdigit() or int(raw_restarts) != 0:
        raise RebootProbeError("Eva1 restarted unexpectedly")
    return {"docker": "active_enabled", "eva1": "active_enabled", "eva1_restarts": 0}


def _container_snapshot() -> dict[str, dict[str, Any]]:
    names = [value[0] for value in CONTAINERS.values()]
    output = _run(
        [
            "/usr/bin/docker",
            "inspect",
            "--format",
            "{{.Name}}|{{.State.Status}}|{{if .State.Health}}"
            "{{.State.Health.Status}}{{else}}none{{end}}|"
            "{{.RestartCount}}|{{.HostConfig.RestartPolicy.Name}}",
            *names,
        ]
    )
    observed: dict[str, dict[str, Any]] = {}
    by_name = {
        name: (key, expected_status, health)
        for key, (name, expected_status, health) in CONTAINERS.items()
    }
    for line in output.splitlines():
        fields = line.split("|")
        if len(fields) != 5 or not fields[3].isdigit():
            raise RebootProbeError("Docker runtime output is invalid")
        name = fields[0].removeprefix("/")
        if name not in by_name or name in observed:
            raise RebootProbeError("Docker runtime set differs")
        key, expected_status, needs_health = by_name[name]
        restart_count = int(fields[3])
        if (
            fields[1] != expected_status
            or fields[4] != "unless-stopped"
            or restart_count > 1
            or (
                expected_status == "running" and needs_health and fields[2] != "healthy"
            )
            or (
                expected_status == "running"
                and not needs_health
                and fields[2] != "none"
            )
        ):
            raise RebootProbeError(f"managed container is not stable: {key}")
        observed[name] = {
            "health": fields[2],
            "restart_count": restart_count,
            "status": fields[1],
        }
    if set(observed) != set(by_name):
        raise RebootProbeError("managed container set is incomplete")
    return {by_name[name][0]: observed[name] for name in sorted(observed)}


def _hindsight_counts() -> dict[str, int]:
    sql = (
        "select (select count(*) from public.memory_units)::text || E'\\t' || "
        "(select count(*) from public.mental_models)::text;"
    )
    output = _run(
        [
            "/usr/bin/docker",
            "exec",
            "-u",
            "postgres",
            CONTAINERS["hindsight_postgres"][0],
            "/usr/bin/psql",
            "-X",
            "-At",
            "-U",
            "postgres",
            "-d",
            "hindsight",
            "-c",
            sql,
        ]
    )
    fields = output.split("\t")
    if len(fields) != 2 or not all(value.isdigit() for value in fields):
        raise RebootProbeError("Hindsight count output is invalid")
    return {"memory_units": int(fields[0]), "mental_models": int(fields[1])}


def _runtime_snapshot(token: str) -> dict[str, Any]:
    services = _service_snapshot()
    route_sha256 = _route_sha256()
    eva1_statuses = {
        route: _status_only(f"http://127.0.0.1:{EVA1_PORT}{route}")
        for route in EVA1_ROUTES
    }
    if set(eva1_statuses.values()) != {200}:
        raise RebootProbeError("Eva1 route is not healthy")
    status_code, active_ready = _http_json(
        "GET", f"http://127.0.0.1:{ACTIVE_PORT}/readyz"
    )
    if status_code != 200 or active_ready.get("ready") is not True:
        raise RebootProbeError("Eva2 active route is not ready")
    unauthorized, _ = _http_json(
        "GET", f"http://127.0.0.1:{ACTIVE_PORT}/internal/v1/dashboard/workboard"
    )
    authorized, workboard = _http_json(
        "GET",
        f"http://127.0.0.1:{ACTIVE_PORT}/internal/v1/dashboard/workboard",
        bearer=token,
    )
    if (
        unauthorized != 401
        or authorized != 200
        or workboard.get("contract") != "EvaWorkBoard/v1"
        or workboard.get("read_only") is not True
    ):
        raise RebootProbeError("owner dashboard boundary differs")
    return {
        "containers": _container_snapshot(),
        "dashboard": {
            "authorized_status": authorized,
            "contract": workboard["contract"],
            "unauthorized_status": unauthorized,
        },
        "eva1_http_statuses": eva1_statuses,
        "hindsight": _hindsight_counts(),
        "ready": {"active": active_ready},
        "rollback": {
            "databases_healthy": True,
            "port": ROLLBACK_PORT,
            "writers_stopped": True,
        },
        "route_dropin_sha256": route_sha256,
        "services": services,
    }


def _canary_payload(
    *, chat_id: str, user_id: int, message_id: int, text: str
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        # The live canary derives person/chat identity server-side but still
        # accepts only the sealed runtime scope used by Eva1's /eva2 client.
        "installation_id": "live-test-installation",
        "message_id": message_id,
        "occurred_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "recent_messages": [],
        "telegram_user_id": user_id,
        "tenant_id": "live-test-tenant",
        "text": text,
    }


def _canary_turn(
    *, chat_id: str, user_id: int, message_id: int, text: str
) -> dict[str, Any]:
    payload = _canary_payload(
        chat_id=chat_id, user_id=user_id, message_id=message_id, text=text
    )
    status_code, response = _http_json(
        "POST",
        f"http://127.0.0.1:{ACTIVE_PORT}/internal/v1/telegram/canary/respond",
        payload=payload,
        timeout=60,
    )
    if (
        status_code != 200
        or response.get("contract") != "EvaInternalGroundedReply/v1"
        or response.get("conversation_id") != f"telegram:{chat_id}"
        or response.get("message_id") != f"telegram:{chat_id}:{message_id}"
        or not isinstance(response.get("text"), str)
    ):
        raise RebootProbeError("synthetic Eva2 conversation response differs")
    return response


def _source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def arm(
    state_path: Path, *, required_restart: str, memory_wait_seconds: int
) -> dict[str, Any]:
    token = _dashboard_token()
    boot = _read_boot_identity()
    before = _runtime_snapshot(token)
    user_id = 8_000_000_000 + secrets.randbelow(900_000_000)
    chat_id = str(user_id)
    marker = f"{secrets.choice(_ADJECTIVES)} {secrets.choice(_NOUNS)}"
    stored = _canary_turn(
        chat_id=chat_id,
        user_id=user_id,
        message_id=1,
        text=(
            f"Onthoud voor de herstart precies deze twee woorden: {marker}. "
            "Bevestig kort dat je ze hebt onthouden."
        ),
    )
    if not stored["text"].strip():
        raise RebootProbeError("Eva2 did not answer the synthetic reboot marker")
    deadline = time.monotonic() + memory_wait_seconds
    after_counts = before["hindsight"]
    while time.monotonic() < deadline:
        after_counts = _hindsight_counts()
        if after_counts["memory_units"] > before["hindsight"]["memory_units"]:
            break
        time.sleep(1)
    if after_counts["memory_units"] <= before["hindsight"]["memory_units"]:
        raise RebootProbeError("synthetic reboot marker did not reach Hindsight")
    state = {
        "armed_at": datetime.now(UTC).isoformat(),
        "boot": asdict(boot),
        "contract": CONTRACT_ARM,
        "conversation": {
            "chat_id": chat_id,
            "marker": marker,
            "stored_message_id": 1,
            "stored_reply_sha256": hashlib.sha256(stored["text"].encode()).hexdigest(),
            "verify_message_id": 2,
            "user_id": user_id,
        },
        "pre_reboot": {
            "eva1_restarts": before["services"]["eva1_restarts"],
            "hindsight": after_counts,
            "route_dropin_sha256": before["route_dropin_sha256"],
        },
        "required_restart": required_restart,
        "source_sha256": _source_sha256(),
    }
    _write_new_json(state_path, state)
    return state


def _load_state(path: Path) -> dict[str, Any]:
    # ``main`` requires root.  Keeping the current uid here as well lets the
    # pure verifier be exercised unprivileged without weakening production:
    # for the real CLI ``os.geteuid()`` is zero.
    raw = _strict_read(
        path, allowed_uids={0, os.geteuid()}, max_bytes=65_536, private=True
    )
    try:
        state = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RebootProbeError("armed reboot state is invalid") from exc
    if not isinstance(state, dict) or state.get("contract") != CONTRACT_ARM:
        raise RebootProbeError("armed reboot state contract differs")
    required = state.get("required_restart")
    conversation = state.get("conversation")
    boot = state.get("boot")
    pre = state.get("pre_reboot")
    if (
        set(state)
        != {
            "armed_at",
            "boot",
            "contract",
            "conversation",
            "pre_reboot",
            "required_restart",
            "source_sha256",
        }
        or required not in _RESTART_RANK
        or required == "none"
        or not isinstance(conversation, dict)
        or not isinstance(boot, dict)
        or not isinstance(pre, dict)
        or set(boot) != {"kernel_boot_id", "pid1_start_ticks", "windows_boot_utc"}
        or not isinstance(boot.get("windows_boot_utc"), str)
        or not _WINDOWS_BOOT_RE.fullmatch(boot["windows_boot_utc"])
        or not isinstance(boot.get("kernel_boot_id"), str)
        or not _BOOT_ID_RE.fullmatch(boot["kernel_boot_id"])
        or not isinstance(boot.get("pid1_start_ticks"), int)
        or isinstance(boot.get("pid1_start_ticks"), bool)
        or boot["pid1_start_ticks"] < 1
        or set(conversation)
        != {
            "chat_id",
            "marker",
            "stored_message_id",
            "stored_reply_sha256",
            "verify_message_id",
            "user_id",
        }
        or not isinstance(conversation.get("chat_id"), str)
        or not isinstance(conversation.get("user_id"), int)
        or isinstance(conversation.get("user_id"), bool)
        or not 1 <= conversation["user_id"] <= 9_007_199_254_740_991
        or conversation.get("chat_id") != str(conversation.get("user_id"))
        or conversation.get("stored_message_id") != 1
        or conversation.get("verify_message_id") != 2
        or not isinstance(conversation.get("marker"), str)
        or not _MARKER_RE.fullmatch(conversation["marker"])
        or not isinstance(conversation.get("stored_reply_sha256"), str)
        or not _SHA256_RE.fullmatch(conversation["stored_reply_sha256"])
        or set(pre) != {"eva1_restarts", "hindsight", "route_dropin_sha256"}
        or pre.get("eva1_restarts") != 0
        or not isinstance(pre.get("hindsight"), dict)
        or set(pre["hindsight"]) != {"memory_units", "mental_models"}
        or not all(
            isinstance(pre["hindsight"].get(key), int)
            and not isinstance(pre["hindsight"].get(key), bool)
            and pre["hindsight"][key] >= 0
            for key in ("memory_units", "mental_models")
        )
        or not isinstance(pre.get("route_dropin_sha256"), str)
        or not _SHA256_RE.fullmatch(pre["route_dropin_sha256"])
        or not isinstance(state.get("source_sha256"), str)
        or not _SHA256_RE.fullmatch(state["source_sha256"])
    ):
        raise RebootProbeError("armed reboot state fields differ")
    return state


def verify(
    state_path: Path, output_path: Path, *, startup_wait_seconds: int = 0
) -> dict[str, Any]:
    state = _load_state(state_path)
    if state["source_sha256"] != _source_sha256():
        raise RebootProbeError("reboot probe source changed after arming")
    before = BootIdentity(**state["boot"])
    after = _read_boot_identity()
    restart_kind = classify_restart(before, after)
    if _RESTART_RANK[restart_kind] < _RESTART_RANK[state["required_restart"]]:
        raise RebootProbeError(
            f"required {state['required_restart']} was not observed; saw {restart_kind}"
        )
    token = _dashboard_token()
    deadline = time.monotonic() + startup_wait_seconds
    while True:
        try:
            runtime = _runtime_snapshot(token)
            break
        except RebootProbeError as exc:
            if time.monotonic() >= deadline:
                raise RebootProbeError(
                    "managed runtime did not settle after reboot"
                ) from exc
            time.sleep(2)
    pre = state["pre_reboot"]
    if runtime["route_dropin_sha256"] != pre["route_dropin_sha256"]:
        raise RebootProbeError("Eva2 route changed across reboot")
    for key in ("memory_units", "mental_models"):
        if runtime["hindsight"][key] < pre["hindsight"][key]:
            raise RebootProbeError("Hindsight data count decreased across reboot")
    conversation = state["conversation"]
    recalled = _canary_turn(
        chat_id=conversation["chat_id"],
        user_id=conversation["user_id"],
        message_id=conversation["verify_message_id"],
        text=(
            "Welke twee woorden vroeg ik je vlak voor de herstart te onthouden? "
            "Antwoord alleen exact met die twee woorden."
        ),
    )
    if recalled["text"].strip().casefold() != conversation["marker"].casefold():
        raise RebootProbeError("durable conversation marker was not recalled exactly")
    receipt = {
        "contract": CONTRACT_RECEIPT,
        "content": {
            "dashboard": runtime["dashboard"],
            "durable_marker_recalled_exactly": True,
            "hindsight": runtime["hindsight"],
        },
        "issued_at": datetime.now(UTC).isoformat(),
        "restart": {
            "kind": restart_kind,
            "kernel_identity_changed": before.kernel_boot_id != after.kernel_boot_id,
            "pid1_identity_changed": before.pid1_start_ticks != after.pid1_start_ticks,
            "windows_boot_identity_changed": (
                before.windows_boot_utc != after.windows_boot_utc
            ),
        },
        "runtime": {
            "containers": runtime["containers"],
            "eva1_http_statuses": runtime["eva1_http_statuses"],
            "ready": {
                label: value.get("ready") for label, value in runtime["ready"].items()
            },
            "rollback": runtime["rollback"],
            "route_port": ACTIVE_PORT,
            "services": runtime["services"],
        },
        "source_sha256": _source_sha256(),
        "status": "passed",
    }
    encoded = json.dumps(receipt, sort_keys=True)
    if (
        token in encoded
        or conversation["chat_id"] in encoded
        or conversation["marker"] in encoded
    ):
        raise RebootProbeError("receipt contains private probe material")
    _write_new_json(output_path, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    arm_parser = subparsers.add_parser("arm")
    arm_parser.add_argument("--state", required=True, type=Path)
    arm_parser.add_argument(
        "--required-restart",
        choices=("windows_host_reboot", "wsl_kernel_restart", "wsl_systemd_restart"),
        default="windows_host_reboot",
    )
    arm_parser.add_argument("--memory-wait-seconds", type=int, default=90)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--state", required=True, type=Path)
    verify_parser.add_argument("--output", required=True, type=Path)
    verify_parser.add_argument("--startup-wait-seconds", type=int, default=180)
    return parser


def _die(message: str) -> NoReturn:
    raise SystemExit(f"managed-b-reboot-probe: {message}")


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if os.geteuid() != 0:
            raise RebootProbeError("probe must run as root")
        if arguments.command == "arm":
            if arguments.memory_wait_seconds < 1 or arguments.memory_wait_seconds > 300:
                raise RebootProbeError("memory wait must be between 1 and 300 seconds")
            arm(
                arguments.state,
                required_restart=arguments.required_restart,
                memory_wait_seconds=arguments.memory_wait_seconds,
            )
        else:
            if (
                arguments.startup_wait_seconds < 0
                or arguments.startup_wait_seconds > 600
            ):
                raise RebootProbeError("startup wait must be between 0 and 600 seconds")
            verify(
                arguments.state,
                arguments.output,
                startup_wait_seconds=arguments.startup_wait_seconds,
            )
    except RebootProbeError as exc:
        _die(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
