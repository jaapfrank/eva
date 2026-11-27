"""Host-side orchestration for Eva's 1-click character, module, and appliance wizard."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

import alembic.command
import alembic.config
import sqlalchemy as sa

from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.application.appliance_setup import (
    CHARACTER_PRESETS,
    HARDWARE_TIERS,
    MODULE_CATALOG,
    HardwareTier,
    ProactivityLevel,
    build_budget_for_tier,
    build_character_soul_markdown,
    get_character_preset,
    recommend_hardware_tier,
    render_character_preview,
)
from eva.application.living_onboarding import (
    detect_living_intent,
    parse_awakening_utterance,
)
from eva.application.qr_svg import render_qr_svg
from eva.application.installer import InstallerProviderProbeError
from eva.application.soul_markdown import (
    SoulMarkdownError,
    identity_to_soul_markdown,
    soul_markdown_to_draft,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.runtime_budget import RuntimeResourceBudget, RuntimeResourceBudgetError
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    bootstrap_identity_authority,
)
from eva.operations.appliance_mcp_gateway import (
    UniversalIntegrationHub,
    detect_local_unifi_console,
    ensure_gateway_runtime_files,
)
from eva.operations.appliance_realtime_voice import (
    REALTIME_MODEL_CATALOG,
    RealtimeVoiceHub,
    VALID_REALTIME_MODELS,
    pick_dutch_filler_phrase,
    run_gemini_live_bridge,
    synthesize_neural_dutch_mp3,
)
from eva.operations.initialize_dashboard_authority import initialize_dashboard_authority
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)
from eva.operations.installer_safety import owner_only_lock, require_owner_only_directory
from eva.operations.model_credentials import build_access_lease
from eva.operations.model_provider_settings import (
    MODEL_PROVIDER_PRESETS,
    ModelProviderSettings,
)


POSTGRES_IMAGE = (
    "docker.io/library/postgres@sha256:"
    "472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
HINDSIGHT_IMAGES_BY_ARCH = {
    "amd64": (
        "ghcr.io/vectorize-io/hindsight@sha256:"
        "aeb0831e2ec55e659d5e69baa064c1278113e523513b8f60a1f09c5cabe59b9b"
    ),
    "arm64": (
        "ghcr.io/vectorize-io/hindsight@sha256:"
        "4b903ed2cbf029d6d305030b0005002d61d6a3b1d9b85867fb459351dcaa671c"
    ),
}

_MAX_PAIRING_ATTEMPTS = 5
_PAIRING_WINDOW_SECONDS = 60.0
_SESSION_TTL_SECONDS = 12 * 3600
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_URL_RE = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b")
_ALL_OPTIONAL_PROVIDER_IDS = ("telegram", "home_assistant", "agentmail")


class ApplianceSetupError(RuntimeError):
    """Domain/operational error presented safely to the wizard UI."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o700)
    require_owner_only_directory(
        path,
        error=ApplianceSetupError,
        unavailable_message="privé werkmap is niet beschikbaar",
        invalid_message="privé werkmap moet alleen door de eigenaar leesbaar zijn",
    )
    return path


def _read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        raw = read_private_artifact(path, label=path.name)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RuntimeError) as exc:
        raise ApplianceSetupError(f"opgeslagen bestand {path.name} is ongeldig") from exc
    if not isinstance(value, dict):
        raise ApplianceSetupError(f"opgeslagen bestand {path.name} is geen object")
    return value


def _write_json_private(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write_private(path, canonical_json_bytes(dict(payload)))


def _write_text_private(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write_private(path, content.encode("utf-8"))


def _format_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(3)
    ]
    return "EVA-" + "-".join(groups)


def _hash_pairing_secret(secret: str, salt_hex: str) -> str:
    return hashlib.sha256(f"{salt_hex}:{secret}".encode("utf-8")).hexdigest()


def is_local_network_client(remote_host: str | None) -> bool:
    """Permit only loopback, RFC1918 private LAN, link-local, or ULA IPv6 clients."""
    if not remote_host:
        return True
    candidate = remote_host.strip()
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return candidate in {"localhost", "testclient"}
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
    )


def _detect_lan_ipv4s() -> list[str]:
    """Return non-loopback private IPv4 addresses sorted with home LAN ranges first."""
    found: list[str] = []

    def _add(raw_ip: str) -> None:
        cand = raw_ip.strip()
        if cand == "10.255.255.254":
            return
        try:
            addr = ipaddress.ip_address(cand)
        except ValueError:
            return
        if not isinstance(addr, ipaddress.IPv4Address):
            return
        if addr.is_loopback or addr.is_link_local or not addr.is_private:
            return
        if cand not in found:
            found.append(cand)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 80))
            _add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    ip_bin = shutil.which("ip")
    if ip_bin:
        try:
            res = subprocess.run(
                [ip_bin, "-4", "-o", "addr", "show", "scope", "global"],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.5,
            )
            for line in res.stdout.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 4
                    and parts[1] not in {"lo", "docker0"}
                    and not parts[1].startswith("br-")
                ):
                    _add(parts[3].split("/", 1)[0])
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4] and isinstance(info[4][0], str):
                _add(info[4][0])
    except OSError:
        pass

    # In WSL2 NAT mode, eth0 only sees an internal 172.16-31.x.x Hyper-V subnet;
    # include the Windows host's real home Wi-Fi/Ethernet LAN IPv4 (192.168.x.x / 10.x.x.x)
    # so phone QR codes point to the reachable laptop IP on Wi-Fi.
    if not any(ip.startswith(("192.168.", "10.")) for ip in found):
        proc_ver = Path("/proc/version")
        ipconfig_exe = Path("/mnt/c/Windows/System32/ipconfig.exe")
        if proc_ver.is_file() and ipconfig_exe.is_file():
            try:
                if "microsoft" in proc_ver.read_text(encoding="utf-8", errors="ignore").lower():
                    ic_res = subprocess.run(
                        [str(ipconfig_exe)],
                        capture_output=True,
                        timeout=1.2,
                        check=False,
                    )
                    ic_txt = ic_res.stdout.decode("cp850", errors="ignore")
                    for m_ip in re.findall(
                        r"\b(?:192\.168|10\.\d{1,3})\.\d{1,3}\.\d{1,3}\b", ic_txt
                    ):
                        if not m_ip.endswith((".0", ".1", ".255")):
                            _add(m_ip)
            except (OSError, subprocess.SubprocessError):
                pass

    def _priority(ip_str: str) -> int:
        if ip_str.startswith("192.168."):
            return 0
        if ip_str.startswith("10."):
            return 1
        return 2

    return sorted(found, key=_priority)


def _detect_host_hardware() -> dict[str, Any]:
    cpu_count = os.cpu_count() or 2
    ram_gib = 8.0
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    ram_gib = round(kb / (1024 * 1024), 1)
                    break
        except (OSError, ValueError, IndexError):
            pass
    disk_free_gib = 50.0
    try:
        usage = shutil.disk_usage("/")
        disk_free_gib = round(usage.free / (1024**3), 1)
    except OSError:
        pass
    import platform

    raw_machine = platform.machine().lower()
    arch = "arm64" if raw_machine in {"aarch64", "arm64"} else "amd64"
    recommended = recommend_hardware_tier(ram_gib, cpu_count)
    return {
        "architecture": arch,
        "cpu_count": cpu_count,
        "ram_gib": ram_gib,
        "disk_free_gib": disk_free_gib,
        "recommended_tier": recommended,
        "hostname": socket.gethostname(),
        "lan_ips": _detect_lan_ipv4s(),
    }


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: Mapping[str, str]) -> None:
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    _write_text_private(path, "\n".join(lines) + "\n")


@dataclass
class _CodexLoginProcess:
    process: subprocess.Popen[str]
    started_at: float
    verification_url: str | None = None
    user_code: str | None = None
    output_lines: list[str] | None = None
    done: bool = False
    error: str | None = None


@dataclass
class _TelegramMagicLinkSession:
    token: str
    person_name: str
    role: str
    bot_username: str
    deep_link: str
    qr_svg: str
    expires_at: float
    status: str = "waiting"
    linked_contact: dict[str, Any] | None = None
    error: str | None = None
    stop_flag: bool = False


class ApplianceSetupService:
    """Stateful, fail-closed backend for the 1-click character, module, and appliance wizard."""

    def __init__(
        self,
        *,
        workspace: Path,
        runtime_root: Path,
        repo_root: Path,
        codex_auth_file: Path | None = None,
        core_base_url: str = "http://127.0.0.1:44710",
        dashboard_proxy_url: str = "http://127.0.0.1:44711",
        hindsight_url: str = "http://127.0.0.1:44672",
        dry_run_apply: bool = False,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        http_get_json: Callable[[str], tuple[int, dict[str, Any] | None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = _ensure_private_dir(Path(workspace))
        self.runtime_root = _ensure_private_dir(Path(runtime_root))
        self.repo_root = Path(repo_root)
        self.codex_auth_file = codex_auth_file or (Path.home() / ".codex" / "auth.json")
        self.core_base_url = core_base_url.rstrip("/")
        self.dashboard_proxy_url = dashboard_proxy_url.rstrip("/")
        self.hindsight_url = hindsight_url.rstrip("/")
        self.dry_run_apply = dry_run_apply
        self._run = command_runner or (
            lambda cmd, **kw: subprocess.run(
                cmd, capture_output=True, text=True, check=False, **kw
            )
        )
        self._http_get_json = http_get_json or self._default_http_get_json
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._session_wall_expiries: dict[str, int] = {}
        self._device_invites: dict[str, float] = {}
        self._failed_attempts: dict[str, list[float]] = {}
        self._codex_login: _CodexLoginProcess | None = None
        self._telegram_magic: _TelegramMagicLinkSession | None = None
        self._pairing_code_plain: str | None = None
        self._realtime_hub = RealtimeVoiceHub(self.runtime_root)
        self._ha_cam_cache: tuple[float, dict[str, Any]] | None = None
        self._ensure_pairing_record()
        self._load_persisted_sessions()

    # ------------------------------------------------------------------
    # Pairing & LAN access gate
    # ------------------------------------------------------------------

    def _pairing_path(self) -> Path:
        return self.workspace / "pairing.json"

    def _pairing_link_path(self) -> Path:
        return self.workspace / "pairing-link.private.txt"

    def _sessions_path(self) -> Path:
        return self.workspace / "sessions.private.json"

    @staticmethod
    def _session_key_sha256(session_cookie: str) -> str:
        return hashlib.sha256(session_cookie.encode("utf-8")).hexdigest()

    def _load_persisted_sessions(self) -> None:
        raw = _read_json_if_present(self._sessions_path())
        if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
            return
        now_wall = int(time.time())
        now_mono = self._clock()
        for tok_sha, exp_val in raw["sessions"].items():
            if (
                isinstance(tok_sha, str)
                and len(tok_sha) == 64
                and isinstance(exp_val, int)
                and not isinstance(exp_val, bool)
                and exp_val > now_wall
            ):
                rem = exp_val - now_wall
                self._sessions[tok_sha] = now_mono + rem
                self._session_wall_expiries[tok_sha] = exp_val

    def _persist_sessions_unlocked(self) -> None:
        now_wall = int(time.time())
        active = {
            k: int(v)
            for k, v in self._session_wall_expiries.items()
            if isinstance(v, int) and v > now_wall
        }
        # Keep bounded to 32 most recent browser sessions
        if len(active) > 32:
            top_keys = sorted(active, key=lambda k: active[k], reverse=True)[:32]
            active = {k: active[k] for k in top_keys}
        self._session_wall_expiries = active
        _write_json_private(
            self._sessions_path(),
            {"contract": "EvaApplianceSessions/v1", "sessions": active},
        )

    def _ensure_pairing_record(self) -> dict[str, Any]:
        record = _read_json_if_present(self._pairing_path())
        if record is not None:
            return record
        code = _format_pairing_code()
        salt = secrets.token_hex(16)
        record = {
            "contract": "EvaAppliancePairing/v1",
            "salt": salt,
            "code_sha256": _hash_pairing_secret(code, salt),
            "created_at": _utc_now_iso(),
        }
        _write_json_private(self._pairing_path(), record)
        _write_text_private(
            self._pairing_link_path(),
            f"code={code}\nlocal_url=http://127.0.0.1:8765/pair#{code}\n",
        )
        self._pairing_code_plain = code
        return record

    def initial_pairing_code(self) -> str | None:
        """Return the newly generated pairing code when created in this process."""
        return self._pairing_code_plain

    def client_permitted(self, remote_host: str | None) -> bool:
        return is_local_network_client(remote_host)

    def is_paired_session(self, session_cookie: str | None) -> bool:
        if not session_cookie:
            return False
        tok_sha = self._session_key_sha256(session_cookie)
        now = self._clock()
        now_wall = int(time.time())
        with self._lock:
            expired = [key for key, exp in self._sessions.items() if exp <= now]
            for key in expired:
                self._sessions.pop(key, None)
                self._session_wall_expiries.pop(key, None)
            expiry = self._sessions.get(tok_sha)
            if expiry is None or expiry <= now:
                return False
            prev_wall = self._session_wall_expiries.get(tok_sha, 0)
            new_ttl = max(_SESSION_TTL_SECONDS, prev_wall - now_wall)
            self._sessions[tok_sha] = now + new_ttl
            if prev_wall - now_wall < (_SESSION_TTL_SECONDS - 1800):
                self._session_wall_expiries[tok_sha] = now_wall + _SESSION_TTL_SECONDS
                self._persist_sessions_unlocked()
            return True

    def _issue_session_unlocked(self, now: float, *, ttl_seconds: int = _SESSION_TTL_SECONDS) -> str:
        session_id = secrets.token_urlsafe(32)
        tok_sha = self._session_key_sha256(session_id)
        self._sessions[tok_sha] = now + ttl_seconds
        self._session_wall_expiries[tok_sha] = int(time.time()) + ttl_seconds
        self._persist_sessions_unlocked()
        return session_id

    def exchange_loopback_session(self, *, remote_host: str | None) -> str:
        cand = (remote_host or "").strip()
        if cand.startswith("[") and "]" in cand:
            cand = cand[1 : cand.index("]")]
        if "%" in cand:
            cand = cand.split("%", 1)[0]
        try:
            addr = ipaddress.ip_address(cand)
        except ValueError as exc:
            raise ApplianceSetupError("alleen lokaal op deze pc toegestaan", status_code=403) from exc
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if not addr.is_loopback:
            raise ApplianceSetupError("alleen lokaal op deze pc toegestaan", status_code=403)
        with self._lock:
            return self._issue_session_unlocked(self._clock())

    def exchange_pairing_code(self, code: str, *, client_key: str) -> str:
        candidate = str(code).strip()
        if not candidate or len(candidate) > 256:
            raise ApplianceSetupError("koppelcode is ongeldig", status_code=401)
        now = self._clock()
        with self._lock:
            attempts = [
                ts
                for ts in self._failed_attempts.get(client_key, [])
                if now - ts < _PAIRING_WINDOW_SECONDS
            ]
            if len(attempts) >= _MAX_PAIRING_ATTEMPTS:
                self._failed_attempts[client_key] = attempts
                raise ApplianceSetupError(
                    "te veel foute koppelpogingen; wacht een minuut", status_code=429
                )

            record = self._ensure_pairing_record()
            matched = hmac.compare_digest(
                _hash_pairing_secret(candidate, str(record["salt"])),
                str(record["code_sha256"]),
            )
            if not matched:
                expired_inv = [k for k, exp in self._device_invites.items() if exp <= now]
                for k in expired_inv:
                    self._device_invites.pop(k, None)
                for inv_code in list(self._device_invites):
                    if hmac.compare_digest(candidate, inv_code):
                        self._device_invites.pop(inv_code, None)
                        matched = True
                        break
            if not matched:
                token_path = self.runtime_root / "secrets" / "dashboard-token"
                if token_path.is_file() and not token_path.is_symlink():
                    try:
                        existing = token_path.read_text(encoding="utf-8").strip()
                        if len(existing) >= 32 and hmac.compare_digest(candidate, existing):
                            matched = True
                    except OSError:
                        pass
            if not matched:
                attempts.append(now)
                self._failed_attempts[client_key] = attempts
                raise ApplianceSetupError("koppelcode is onjuist", status_code=401)

            self._failed_attempts.pop(client_key, None)
            ttl = (365 * 86400) if candidate.startswith("EVA-DISP-") else _SESSION_TTL_SECONDS
            return self._issue_session_unlocked(now, ttl_seconds=ttl)

    # ------------------------------------------------------------------
    # Persistent wizard state helpers
    # ------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.workspace / "appliance-state.json"

    def _character_path(self) -> Path:
        return self.workspace / "character-settings.json"

    def _identity_authority_dir(self) -> Path:
        return self.workspace / "identity-authority"

    def _identity_workspace_dir(self) -> Path:
        return _ensure_private_dir(self.workspace / "identity-workspace")

    def _default_state(self) -> dict[str, Any]:
        env_vals = _parse_env_file(self.runtime_root / "runtime.env")
        hw = _detect_host_hardware()
        return {
            "contract": "EvaApplianceState/v1",
            "profile": {
                "installation_name": "Eva thuis",
                "owner_name": "Jaap",
                "family_members": [],
                "language": "nl-NL",
                "time_zone": "Europe/Amsterdam",
            },
            "hardware": {
                "tier": hw["recommended_tier"],
                "monthly_spend_cents": 0,
            },
            "ids": {
                "tenant_id": env_vals.get(
                    "EVA_RUNTIME_TENANT_ID", "11111111-1111-1111-8111-111111111111"
                ),
                "installation_id": env_vals.get(
                    "EVA_RUNTIME_INSTALLATION_ID", "22222222-2222-2222-8222-222222222222"
                ),
                "principal_id": env_vals.get(
                    "EVA_RUNTIME_PRINCIPAL_ID", "33333333-3333-3333-8333-333333333333"
                ),
                "binding_id": env_vals.get(
                    "EVA_DASHBOARD_PRINCIPAL_BINDING_ID",
                    "44444444-4444-4444-8444-444444444444",
                ),
            },
            "model": {
                "mode": "codex",
                "conversation_model": env_vals.get("EVA_CONVERSATION_MODEL", "gpt-6-astra"),
                "reasoning_effort": env_vals.get("EVA_CONVERSATION_REASONING_EFFORT", "medium"),
                "selected_api_id": None,
            },
            "modules": {
                "telegram": {
                    "enabled": bool(env_vals.get("EVA_TELEGRAM_BOT_TOKEN_FILE")),
                    "owner_user_id": "",
                    "bot_username": "",
                    "verified": False,
                },
                "smart_home": {
                    "enabled": bool(env_vals.get("EVA_HOME_ASSISTANT_URL")),
                    "url": env_vals.get("EVA_HOME_ASSISTANT_URL", "http://homeassistant.local:8123"),
                    "allow_effects": env_vals.get("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "false") == "true",
                    "verified": False,
                },
                "voice": {
                    "enabled": env_vals.get("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", "false") == "true",
                    "speaker_mode": "browser_and_phone",
                },
                "holostage": {
                    "enabled": env_vals.get("EVA_HOLOSTAGE_LAN_ENABLED", "false") == "true",
                    "device_label": "Woonkamer / Tablet",
                },
                "digital_work": {
                    "enabled": env_vals.get("EVA_DELEGATED_DIGITAL_ENABLED", "false") == "true",
                    "allow_public_web": True,
                },
                "mail": {
                    "enabled": bool(env_vals.get("EVA_AGENTMAIL_INBOX_ID")),
                    "inbox_id": env_vals.get("EVA_AGENTMAIL_INBOX_ID", ""),
                    "verified": False,
                },
            },
            "last_applied_at": None,
            "awakening_completed": False,
            "awakening_turns": 0,
            "guest_mode_active": False,
            "wizard_completed": False,
            "realtime_voice": {
                "model": "gpt-realtime-2.1",
                "voice": "marin",
            },
            "main_display": {
                "enabled": True,
                "label": "Keuken Hoofdscherm (UniFi Connect Display)",
                "room": "keuken",
                "camera_entity_id": "",
                "camera_snapshot_url": "",
                "wake_entity_id": "notify.keuken_scherm",
                "https_port": 8766,
            },
        }

    def _load_state(self) -> dict[str, Any]:
        stored = _read_json_if_present(self._state_path())
        base = self._default_state()
        if stored is None:
            return base
        for section in ("profile", "hardware", "ids", "model", "realtime_voice", "main_display"):
            if isinstance(stored.get(section), dict):
                base[section].update(stored[section])
        if isinstance(stored.get("modules"), dict):
            for mod_id, mod_val in stored["modules"].items():
                if mod_id in base["modules"] and isinstance(mod_val, dict):
                    base["modules"][mod_id].update(mod_val)
        if isinstance(stored.get("last_applied_at"), str):
            base["last_applied_at"] = stored["last_applied_at"]
        if isinstance(stored.get("awakening_completed"), bool):
            base["awakening_completed"] = stored["awakening_completed"]
        if isinstance(stored.get("awakening_turns"), int) and not isinstance(
            stored.get("awakening_turns"), bool
        ):
            base["awakening_turns"] = stored["awakening_turns"]
        if isinstance(stored.get("guest_mode_active"), bool):
            base["guest_mode_active"] = stored["guest_mode_active"]
        if isinstance(stored.get("wizard_completed"), bool):
            base["wizard_completed"] = stored["wizard_completed"]
        return base

    def _save_state(self, state: Mapping[str, Any]) -> None:
        _write_json_private(self._state_path(), state)

    def _identity_lifecycle(self) -> InstallerIdentityLifecycle | None:
        auth_dir = self._identity_authority_dir()
        if not (auth_dir / "current.json").is_file():
            return None
        return InstallerIdentityLifecycle(
            current_path=auth_dir / "current.json",
            trust_path=auth_dir / "trust.json",
            private_key_path=auth_dir / "private-key.bin",
            workspace=self._identity_workspace_dir(),
            clock=_utc_now_iso,
        )

    def _load_character(self) -> dict[str, Any]:
        stored = _read_json_if_present(self._character_path())
        preset = CHARACTER_PRESETS[0]
        lifecycle = self._identity_lifecycle()
        pack = (
            lifecycle.current_identity()
            if lifecycle is not None
            else load_current_identity_pack()
        )
        if stored is not None:
            data = dict(stored)
        else:
            data = {
                "preset_id": preset.preset_id,
                "display_name": pack.display_name,
                "pronouns": list(pack.pronouns),
                "language": pack.language,
                "temperament": list(preset.temperament),
                "humour": preset.humour,
                "voice_qualities": list(preset.voice_qualities),
                "proactivity": preset.proactivity,
                "custom_rules": "",
            }
        data["pack_version"] = pack.pack_version
        data["can_rollback"] = bool(
            lifecycle is not None and lifecycle.rollback_path.is_file()
        )
        return data

    # ------------------------------------------------------------------
    # Live status & probes
    # ------------------------------------------------------------------

    @staticmethod
    def _default_http_get_json(url: str) -> tuple[int, dict[str, Any] | None]:
        parsed = urlsplit(url)
        conn_cls = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(parsed.hostname or "127.0.0.1", port=parsed.port, timeout=2.0)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            conn.request("GET", path, headers={"Accept": "application/json"})
            resp = conn.getresponse()
            raw = resp.read(64 * 1024)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
            return resp.status, body if isinstance(body, dict) else None
        except OSError:
            return 0, None
        finally:
            conn.close()

    def _codex_auth_status(self) -> dict[str, Any]:
        path = self.codex_auth_file
        if not path.is_file() or path.is_symlink():
            return {"logged_in": False, "account_id": None, "detail": "Nog niet ingelogd met ChatGPT/Codex"}
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            tokens = payload.get("tokens") if isinstance(payload, dict) else None
            if not isinstance(tokens, dict) and isinstance(payload, dict):
                tokens = payload
            has_access = bool(
                isinstance(tokens, dict)
                and isinstance(tokens.get("access_token"), str)
                and tokens["access_token"].strip()
                and isinstance(tokens.get("account_id"), str)
                and tokens["account_id"].strip()
            )
            if not has_access:
                return {"logged_in": False, "account_id": None, "detail": "Codex loginbestand is onvolledig"}
            acct = str(tokens["account_id"])
            masked = acct[:4] + "…" + acct[-4:] if len(acct) > 10 else acct
            return {"logged_in": True, "account_id": masked, "detail": "ChatGPT / Codex actief gekoppeld"}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"logged_in": False, "account_id": None, "detail": "Codex loginbestand kon niet gelezen worden"}

    def _runtime_health(self) -> dict[str, Any]:
        hz_status, _ = self._http_get_json(f"{self.core_base_url}/healthz")
        rz_status, rz_body = self._http_get_json(f"{self.core_base_url}/readyz")
        hs_status, hs_body = self._http_get_json(f"{self.hindsight_url}/health")
        dash_status, _ = self._http_get_json(f"{self.dashboard_proxy_url}/internal/v1/dashboard/status")
        installed = (self.runtime_root / "runtime.env").is_file()
        secrets_dir = self.runtime_root / "secrets"
        token_present = (secrets_dir / "dashboard-token").is_file()
        tg_ready = (
            (secrets_dir / "telegram-bot-token").is_file()
            and (secrets_dir / "telegram-principal-bindings.json").is_file()
        )
        return {
            "installed": installed,
            "core_alive": hz_status == 200,
            "core_ready": rz_status == 200 and bool(rz_body and rz_body.get("ready")),
            "database": (rz_body or {}).get("database", "unknown"),
            "memory_projection": (rz_body or {}).get("memory_projection", "unknown"),
            "model_provider": (rz_body or {}).get("model_provider", "unknown"),
            "hindsight_healthy": hs_status == 200 and (hs_body or {}).get("status") == "healthy",
            "dashboard_proxy_ready": bool((hz_status == 200 and token_present) or dash_status == 200),
            "telegram_ready": tg_ready,
            "dashboard_url": "/internal/v1/dashboard",
            "conversation_url": "/internal/v1/dashboard/conversation",
            "holostage_url": "/internal/v1/dashboard/holostage",
        }

    def public_ping(self) -> dict[str, Any]:
        """Return non-sensitive appliance beacon metadata for local LAN discovery."""
        state = self._load_state()
        hw = _detect_host_hardware()
        rt = self._runtime_health()
        return {
            "contract": "EvaAppliancePing/v1",
            "eva_appliance": True,
            "phase": "ready",
            "hostname": hw["hostname"],
            "lan_ips": hw["lan_ips"],
            "installation_name": state["profile"].get("installation_name", "Eva thuis"),
            "installed": rt["installed"],
            "core_ready": rt["core_ready"],
            "telegram_ready": rt["telegram_ready"],
        }

    def status_snapshot(self) -> dict[str, Any]:
        state = self._load_state()
        character = self._load_character()
        hardware = _detect_host_hardware()
        codex_status = self._codex_auth_status()
        runtime = self._runtime_health()
        secrets_dir = self.runtime_root / "secrets"
        provider_settings = ModelProviderSettings(
            self.runtime_root / "work" / "model-providers"
        ).read()
        connections = [
            {
                "id": cid,
                "kind": row.get("kind"),
                "label": row.get("label"),
                "model": row.get("model"),
                "endpoint": row.get("endpoint"),
                "check_state": row.get("check_state"),
                "selected": provider_settings.get("selected_id") == cid,
            }
            for cid, row in (provider_settings.get("connections") or {}).items()
            if isinstance(row, dict) and row.get("enabled")
        ]

        modules_view = []
        for item in MODULE_CATALOG:
            mod_state = dict(state["modules"].get(item.module_id, {}))
            secret_name = {
                "telegram": "telegram-bot-token",
                "smart_home": "home-assistant-token",
                "mail": "eva-agentmail-api-key",
            }.get(item.module_id)
            mod_state["secret_present"] = bool(
                secret_name and (secrets_dir / secret_name).is_file()
            )
            modules_view.append(
                {
                    "module_id": item.module_id,
                    "title": item.title,
                    "summary": item.summary,
                    "ram_hint": item.ram_hint,
                    "has_connection_test": item.has_connection_test,
                    "config": mod_state,
                }
            )

        return {
            "contract": "EvaApplianceSnapshot/v1",
            "runtime": runtime,
            "hardware": {
                **hardware,
                "selected_tier": state["hardware"]["tier"],
                "monthly_spend_eur": round(int(state["hardware"].get("monthly_spend_cents", 0)) / 100, 2),
                "tiers": [
                    {
                        "tier": tier.tier,
                        "label": tier.label,
                        "summary": tier.summary,
                        "postgres_shared_buffers_mb": tier.postgres_shared_buffers_mb,
                        "hindsight_threads": tier.hindsight_threads,
                    }
                    for tier in HARDWARE_TIERS.values()
                ],
            },
            "profile": state["profile"],
            "character": {
                **character,
                "presets": [
                    {
                        "preset_id": p.preset_id,
                        "label": p.label,
                        "tagline": p.tagline,
                        "temperament": list(p.temperament),
                        "humour": p.humour,
                        "voice_qualities": list(p.voice_qualities),
                        "proactivity": p.proactivity,
                    }
                    for p in CHARACTER_PRESETS
                ],
                "preview": render_character_preview(
                    display_name=character["display_name"],
                    owner_name=state["profile"].get("owner_name", "Jaap"),
                    preset_id=character.get("preset_id", "warm_huisgenoot"),
                    proactivity=character.get("proactivity", "balanced"),
                    temperament=character.get("temperament", ()),
                    humour=character.get("humour", ""),
                ),
            },
            "model": {
                **state["model"],
                "codex": codex_status,
                "api_presets": [
                    {
                        "preset_id": p["preset_id"],
                        "kind": p["kind"],
                        "label": p["label"],
                        "default_model": p["default_model"],
                        "default_endpoint": p["default_endpoint"],
                        "requires_api_key": p["requires_api_key"],
                        "hint_nl": p["hint_nl"],
                    }
                    for p in MODEL_PROVIDER_PRESETS
                ],
                "configured_api_connections": connections,
            },
            "modules": modules_view,
            "last_applied_at": state.get("last_applied_at"),
        }

    # ------------------------------------------------------------------
    # Step 1: Owner profile & hardware
    # ------------------------------------------------------------------

    def save_profile_and_hardware(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        state = self._load_state()
        owner_name = str(payload.get("owner_name", "")).strip()
        installation_name = str(payload.get("installation_name", "Eva thuis")).strip()
        language = str(payload.get("language", "nl-NL")).strip() or "nl-NL"
        time_zone = str(payload.get("time_zone", "Europe/Amsterdam")).strip() or "Europe/Amsterdam"
        raw_family = payload.get("family_members", [])
        if isinstance(raw_family, str):
            family = [item.strip() for item in raw_family.split(",") if item.strip()]
        elif isinstance(raw_family, list):
            family = [str(item).strip() for item in raw_family if str(item).strip()]
        else:
            raise ApplianceSetupError("gezinsleden moeten tekst of een lijst zijn")
        if not owner_name or len(owner_name) > 80:
            raise ApplianceSetupError("vul een geldige naam in (1–80 tekens)")
        if not installation_name or len(installation_name) > 80:
            raise ApplianceSetupError("vul een geldige naam voor deze installatie in")
        tier = str(payload.get("tier", state["hardware"]["tier"])).strip()
        if tier not in HARDWARE_TIERS:
            raise ApplianceSetupError("kies een geldig hardwareprofiel (compact, standard of performance)")
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ApplianceSetupError("tijdzone is ongeldig (gebruik bijv. Europe/Amsterdam)") from exc

        state["profile"] = {
            "installation_name": installation_name,
            "owner_name": owner_name,
            "family_members": family[:12],
            "language": language,
            "time_zone": time_zone,
        }
        state["hardware"]["tier"] = tier
        self._save_state(state)
        self._write_budget_file(state)
        self._sync_household_head_settings()
        return {"ok": True, "profile": state["profile"], "hardware": state["hardware"]}

    def _write_budget_file(self, state: Mapping[str, Any]) -> RuntimeResourceBudget:
        priv = _ensure_private_dir(self.runtime_root / "private")
        ids = state["ids"]
        tier: HardwareTier = state["hardware"]["tier"]
        spend = round(int(state["hardware"].get("monthly_spend_cents", 0)) / 100, 2)
        budget = build_budget_for_tier(
            tier,
            tenant_id=ids["tenant_id"],
            installation_id=ids["installation_id"],
            monthly_spend_eur=spend,
        )
        budget_file = priv / "runtime-budget.json"
        atomic_write_private(budget_file, budget.to_bytes())
        return budget

    # ------------------------------------------------------------------
    # Step 2: Character & signed IdentityPack
    # ------------------------------------------------------------------

    def preview_character(self, payload: Mapping[str, Any]) -> dict[str, str]:
        state = self._load_state()
        preset_id = str(payload.get("preset_id", "warm_huisgenoot"))
        proactivity = str(payload.get("proactivity", "balanced"))
        if proactivity not in {"quiet", "balanced", "proactive"}:
            proactivity = "balanced"
        temp = payload.get("temperament") or []
        if isinstance(temp, str):
            temp = [x.strip() for x in temp.split(",") if x.strip()]
        return render_character_preview(
            display_name=str(payload.get("display_name", "Eva")),
            owner_name=str(payload.get("owner_name") or state["profile"].get("owner_name", "Jaap")),
            preset_id=preset_id,
            proactivity=proactivity,  # type: ignore[arg-type]
            temperament=temp,
            humour=str(payload.get("humour", "")),
        )

    def save_character(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        preset_id = str(payload.get("preset_id", "warm_huisgenoot")).strip()
        preset = get_character_preset(preset_id) if preset_id != "custom" else CHARACTER_PRESETS[0]
        display_name = str(payload.get("display_name", "Eva")).strip()
        language = str(payload.get("language", self._load_state()["profile"]["language"])).strip() or "nl-NL"
        humour = str(payload.get("humour", preset.humour)).strip()
        proactivity = str(payload.get("proactivity", preset.proactivity)).strip()
        if proactivity not in {"quiet", "balanced", "proactive"}:
            raise ApplianceSetupError("kies een geldige initiatiefstand")
        custom_rules = str(payload.get("custom_rules", "")).strip()
        if len(custom_rules) > 2400:
            raise ApplianceSetupError("eigen huisregels mogen maximaal 2400 tekens zijn")

        def _as_list(raw: object, fallback: tuple[str, ...]) -> list[str]:
            if isinstance(raw, str):
                items = [x.strip() for x in raw.split(",") if x.strip()]
                return items or list(fallback)
            if isinstance(raw, list):
                items = [str(x).strip() for x in raw if str(x).strip()]
                return items or list(fallback)
            return list(fallback)

        pronouns = _as_list(payload.get("pronouns"), ("zij", "haar"))
        temperament = _as_list(payload.get("temperament"), preset.temperament)
        voice_qualities = _as_list(payload.get("voice_qualities"), preset.voice_qualities)

        soul_override = payload.get("soul_markdown")
        template = load_current_identity_pack()
        try:
            if isinstance(soul_override, str) and soul_override.strip():
                markdown = soul_override.strip() + "\n"
            else:
                markdown = build_character_soul_markdown(
                    display_name=display_name,
                    pronouns=pronouns,
                    language=language,
                    humour=humour,
                    temperament=temperament,
                    voice_qualities=voice_qualities,
                    base_pack=template,
                )
        except SoulMarkdownError as exc:
            raise ApplianceSetupError(str(exc)) from exc

        auth_dir = self._identity_authority_dir()
        lifecycle = self._identity_lifecycle()
        transition_status = "unchanged"

        try:
            if lifecycle is None:
                # Bootstrap private Ed25519 identity authority; guarantee a non-empty diff from fixture.
                try:
                    draft = soul_markdown_to_draft(
                        markdown, draft_id="appliance-bootstrap", base=template
                    )
                except SoulMarkdownError as exc:
                    if "verandert de huidige identiteit niet" in str(exc):
                        fallback_md = build_character_soul_markdown(
                            display_name=display_name,
                            pronouns=pronouns,
                            language=language,
                            humour=preset.humour,
                            temperament=preset.temperament,
                            voice_qualities=preset.voice_qualities,
                        )
                        draft = soul_markdown_to_draft(
                            fallback_md, draft_id="appliance-bootstrap", base=template
                        )
                    else:
                        raise
                bootstrap_identity_authority(
                    output_directory=auth_dir,
                    template=template,
                    draft=draft,
                    identity_id="identity:eva:home",
                    issuer="issuer:eva2:appliance",
                    issued_at=_utc_now_iso(),
                )
                lifecycle = self._identity_lifecycle()
                transition_status = "bootstrapped"
            else:
                current = lifecycle.current_identity()
                try:
                    soul_markdown_to_draft(
                        markdown, draft_id="appliance-check", base=current
                    )
                    base_sha = canonical_sha256(current)
                    staged = lifecycle.stage(
                        markdown=markdown,
                        draft_id=f"appliance-{int(time.time())}",
                        expected_base_sha256=base_sha,
                    )
                    issued = lifecycle.issue(
                        draft_sha256=staged.draft_sha256,
                        expected_base_sha256=base_sha,
                    )
                    transition = lifecycle.activate(
                        candidate_sha256=issued.candidate_sha256,
                        expected_current_sha256=base_sha,
                    )
                    transition_status = transition.status
                except SoulMarkdownError as exc:
                    if "verandert de huidige identiteit niet" not in str(exc):
                        raise
        except (SoulMarkdownError, IdentityPackLifecycleError, ValueError) as exc:
            raise ApplianceSetupError(f"karakter kon niet worden ondertekend: {exc}") from exc

        assert lifecycle is not None
        self._publish_runtime_identity_files()
        saved_char = {
            "contract": "EvaCharacterSettings/v1",
            "preset_id": preset_id,
            "display_name": lifecycle.current_identity().display_name,
            "pronouns": list(lifecycle.current_identity().pronouns),
            "language": lifecycle.current_identity().language,
            "temperament": list(lifecycle.current_identity().temperament),
            "humour": lifecycle.current_identity().humour,
            "voice_qualities": list(lifecycle.current_identity().voice_style.qualities),
            "proactivity": proactivity,
            "custom_rules": custom_rules,
        }
        _write_json_private(self._character_path(), saved_char)
        self._sync_household_head_settings()
        return {
            "ok": True,
            "status": transition_status,
            "character": self._load_character(),
        }

    def rollback_character(self) -> dict[str, Any]:
        lifecycle = self._identity_lifecycle()
        if lifecycle is None or not lifecycle.rollback_path.is_file():
            raise ApplianceSetupError("er is nog geen vorig karakter om terug te zetten")
        current = lifecycle.current_identity()
        try:
            res = lifecycle.rollback(expected_current_sha256=canonical_sha256(current))
        except IdentityPackLifecycleError as exc:
            raise ApplianceSetupError(f"terugzetten mislukt: {exc}") from exc
        self._publish_runtime_identity_files()
        pack = lifecycle.current_identity()
        stored = self._load_character()
        stored.update(
            {
                "display_name": pack.display_name,
                "pronouns": list(pack.pronouns),
                "language": pack.language,
                "temperament": list(pack.temperament),
                "humour": pack.humour,
                "voice_qualities": list(pack.voice_style.qualities),
            }
        )
        stored.pop("pack_version", None)
        stored.pop("can_rollback", None)
        _write_json_private(self._character_path(), stored)
        self._sync_household_head_settings()
        return {"ok": True, "status": res.status, "character": self._load_character()}

    def export_soul_markdown(self) -> str:
        lifecycle = self._identity_lifecycle()
        pack = lifecycle.current_identity() if lifecycle else load_current_identity_pack()
        return identity_to_soul_markdown(pack)

    def _publish_runtime_identity_files(self) -> None:
        auth_dir = self._identity_authority_dir()
        if not (auth_dir / "current.json").is_file():
            return
        runtime_id_dir = _ensure_private_dir(self.runtime_root / "identity")
        atomic_write_private(
            runtime_id_dir / "current.json", (auth_dir / "current.json").read_bytes()
        )
        atomic_write_private(
            runtime_id_dir / "trust.json", (auth_dir / "trust.json").read_bytes()
        )

    # ------------------------------------------------------------------
    # Step 3: AI model (Codex device login & optional API key) + spend cap
    # ------------------------------------------------------------------

    def start_codex_login(self) -> dict[str, Any]:
        codex_status = self._codex_auth_status()
        if codex_status["logged_in"]:
            return {"status": "logged_in", **codex_status}
        with self._lock:
            existing = self._codex_login
            if existing is not None and existing.process.poll() is None:
                return self._codex_login_view(existing)
            codex_bin = shutil.which("codex")
            if not codex_bin:
                raise ApplianceSetupError(
                    "Codex CLI niet gevonden op dit systeem; gebruik een API-sleutel of installeer @openai/codex"
                )
            env = dict(os.environ)
            env["CODEX_HOME"] = str(self.codex_auth_file.parent)
            proc = subprocess.Popen(
                [codex_bin, "login", "--device-auth"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            tracker = _CodexLoginProcess(
                process=proc,
                started_at=self._clock(),
                output_lines=[],
            )
            self._codex_login = tracker
            reader = threading.Thread(
                target=self._read_codex_output, args=(tracker,), daemon=True
            )
            reader.start()

        deadline = self._clock() + 5.0
        while self._clock() < deadline:
            view = self.poll_codex_login()
            if view.get("verification_url") or view.get("user_code") or view.get("logged_in"):
                return view
            time.sleep(0.15)
        return self.poll_codex_login()

    def _read_codex_output(self, tracker: _CodexLoginProcess) -> None:
        assert tracker.process.stdout is not None
        try:
            for raw_line in tracker.process.stdout:
                clean = _ANSI_RE.sub("", raw_line).strip()
                if not clean:
                    continue
                with self._lock:
                    assert tracker.output_lines is not None
                    tracker.output_lines.append(clean)
                    if tracker.verification_url is None:
                        m_url = _URL_RE.search(clean)
                        if m_url:
                            tracker.verification_url = m_url.group(0)
                    if tracker.user_code is None:
                        m_code = _DEVICE_CODE_RE.search(clean)
                        if m_code:
                            tracker.user_code = m_code.group(0)
        finally:
            rc = tracker.process.wait()
            with self._lock:
                tracker.done = True
                if rc != 0 and not self._codex_auth_status()["logged_in"]:
                    tracker.error = f"codex login eindigde met code {rc}"

    def _codex_login_view(self, tracker: _CodexLoginProcess) -> dict[str, Any]:
        codex_status = self._codex_auth_status()
        if codex_status["logged_in"]:
            state = self._load_state()
            state["model"]["mode"] = "codex"
            self._save_state(state)
            return {
                "status": "logged_in",
                "verification_url": tracker.verification_url,
                "user_code": tracker.user_code,
                **codex_status,
            }
        return {
            "status": "error" if tracker.done and tracker.error else "pending",
            "logged_in": False,
            "verification_url": tracker.verification_url,
            "user_code": tracker.user_code,
            "detail": tracker.error or "Open de link, vul de code in en log in bij OpenAI.",
        }

    def poll_codex_login(self) -> dict[str, Any]:
        codex_status = self._codex_auth_status()
        if codex_status["logged_in"]:
            return {"status": "logged_in", **codex_status}
        with self._lock:
            tracker = self._codex_login
            if tracker is None:
                return {"status": "idle", **codex_status}
            return self._codex_login_view(tracker)

    def save_and_test_api_model(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", "openai_compatible")).strip()
        label = str(payload.get("label", "Eigen API-model")).strip()
        model = str(payload.get("model", "")).strip()
        endpoint = str(payload.get("endpoint", "")).strip()
        api_key = str(payload.get("api_key", "")).strip()
        skip_probe = bool(payload.get("skip_probe", False))

        parsed_ep = urlsplit(endpoint.rstrip("/"))
        ep_host = (parsed_ep.hostname or "").lower()
        if ep_host == "host.docker.internal":
            try:
                socket.gethostbyname("host.docker.internal")
            except OSError:
                endpoint = endpoint.replace("host.docker.internal", "127.0.0.1", 1)
                ep_host = "127.0.0.1"
        if not api_key and ep_host in {"127.0.0.1", "::1", "localhost", "host.docker.internal"}:
            api_key = "eva-local-keyless-endpoint"

        settings = ModelProviderSettings(
            _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        )
        try:
            conn_id = settings.configure(
                kind=kind,
                label=label,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
            )
        except ValueError as exc:
            raise ApplianceSetupError(str(exc)) from exc

        if skip_probe:
            ready = True
            detail = "Opgeslagen zonder live test"
        else:
            ready, detail = self._probe_api_model(kind=kind, endpoint=endpoint, api_key=api_key)
        settings.record_check(
            conn_id, ready=ready, usage={"source": "appliance_wizard", "detail": detail}
        )
        if not ready:
            raise ApplianceSetupError(f"verbinding met modelprovider mislukt: {detail}")
        settings.select(conn_id)
        state = self._load_state()
        state["model"]["mode"] = "api"
        state["model"]["selected_api_id"] = conn_id
        self._save_state(state)
        return {"ok": True, "connection_id": conn_id, "detail": detail}

    @staticmethod
    def _probe_api_model(*, kind: str, endpoint: str, api_key: str) -> tuple[bool, str]:
        parsed = urlsplit(endpoint.rstrip("/"))
        conn_cls = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        headers = {"Accept": "application/json", "User-Agent": "Eva2-Appliance/1"}
        base_path = parsed.path.rstrip("/")
        if kind == "anthropic":
            probe_path = f"{base_path}/v1/models"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            probe_path = f"{base_path}/models"
            if api_key and api_key != "eva-local-keyless-endpoint":
                headers["Authorization"] = f"Bearer {api_key}"
        conn = conn_cls(parsed.hostname or "", port=parsed.port, timeout=6.0)
        try:
            conn.request("GET", probe_path, headers=headers)
            resp = conn.getresponse()
            resp.read(4096)
            if resp.status == 200:
                return True, "API-sleutel en endpoint bereikbaar (HTTP 200)"
            if resp.status in {401, 403}:
                return False, f"API-sleutel geweigerd (HTTP {resp.status})"
            return False, f"onverwachte status HTTP {resp.status}"
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            return False, f"verbinding niet bereikbaar ({type(exc).__name__})"
        finally:
            conn.close()

    def save_model_budget(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            spend_eur = float(payload.get("monthly_spend_eur", 0.0))
        except (TypeError, ValueError) as exc:
            raise ApplianceSetupError("maandplafond moet een getal in euro zijn") from exc
        if spend_eur < 0 or spend_eur > 10_000:
            raise ApplianceSetupError("maandplafond moet tussen € 0 en € 10.000 liggen")
        state = self._load_state()
        state["hardware"]["monthly_spend_cents"] = round(spend_eur * 100)
        if "mode" in payload and payload["mode"] in {"codex", "api"}:
            state["model"]["mode"] = payload["mode"]
        if "reasoning_effort" in payload and payload["reasoning_effort"] in {"low", "medium", "high"}:
            state["model"]["reasoning_effort"] = payload["reasoning_effort"]
        self._save_state(state)
        self._write_budget_file(state)
        return {"ok": True, "hardware": state["hardware"], "model": state["model"]}

    # ------------------------------------------------------------------
    # Step 4: Modules & direct connection tests
    # ------------------------------------------------------------------

    def _secrets_dir(self) -> Path:
        return _ensure_private_dir(self.runtime_root / "secrets")

    def save_module(self, module_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        state = self._load_state()
        if module_id not in state["modules"]:
            raise ApplianceSetupError(f"onbekende module: {module_id}", status_code=404)
        mod = state["modules"][module_id]
        enabled = bool(payload.get("enabled", mod.get("enabled", False)))
        mod["enabled"] = enabled
        secrets_dir = self._secrets_dir()

        if module_id == "telegram":
            token = str(payload.get("bot_token", "")).strip()
            if token:
                if len(token) < 16 or any(ord(c) <= 0x20 for c in token):
                    raise ApplianceSetupError("Telegram bot-token heeft een ongeldig formaat")
                _write_text_private(secrets_dir / "telegram-bot-token", token + "\n")
                mod["verified"] = False
            owner_uid = str(payload.get("owner_user_id", mod.get("owner_user_id", ""))).strip()
            if owner_uid:
                if not owner_uid.isdigit():
                    raise ApplianceSetupError("Telegram gebruikers-ID moet uit cijfers bestaan")
                mod["owner_user_id"] = owner_uid
                self._write_telegram_bindings(owner_uid, state["ids"])
        elif module_id == "smart_home":
            url = str(payload.get("url", mod.get("url", ""))).strip().rstrip("/")
            if enabled and url:
                try:
                    HomeAssistantInstallerReadOnlyProbe(base_url=url)
                except ValueError as exc:
                    raise ApplianceSetupError(f"ongeldige Home Assistant-URL: {exc}") from exc
                mod["url"] = url
            mod["allow_effects"] = bool(payload.get("allow_effects", mod.get("allow_effects", False)))
            token = str(payload.get("token", "")).strip()
            if token:
                if len(token) < 16 or any(ord(c) <= 0x20 for c in token):
                    raise ApplianceSetupError("Home Assistant-token is te kort of ongeldig")
                _write_text_private(secrets_dir / "home-assistant-token", token + "\n")
                mod["verified"] = False
        elif module_id == "voice":
            mode = str(payload.get("speaker_mode", mod.get("speaker_mode", "browser_and_phone")))
            if mode not in {"browser_and_phone", "household_speakers"}:
                raise ApplianceSetupError("ongeldige spraakmodus")
            mod["speaker_mode"] = mode
        elif module_id == "holostage":
            mod["device_label"] = str(payload.get("device_label", mod.get("device_label", "Tablet / Scherm"))).strip()[:80]
        elif module_id == "digital_work":
            mod["allow_public_web"] = bool(payload.get("allow_public_web", True))
        elif module_id == "mail":
            inbox = str(payload.get("inbox_id", mod.get("inbox_id", ""))).strip()
            if inbox:
                mod["inbox_id"] = inbox
            api_key = str(payload.get("api_key", "")).strip()
            if api_key:
                if len(api_key) < 16:
                    raise ApplianceSetupError("E-mail API-sleutel is te kort")
                _write_text_private(secrets_dir / "eva-agentmail-api-key", api_key + "\n")
                mod["verified"] = False

        self._save_state(state)
        return {"ok": True, "module_id": module_id, "config": mod}

    def _telegram_contacts_path(self) -> Path:
        return self.workspace / "telegram-contacts.json"

    def _load_telegram_contacts(self, ids: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        stored = _read_json_if_present(self._telegram_contacts_path())
        if stored is not None and isinstance(stored.get("contacts"), list):
            return [dict(c) for c in stored["contacts"] if isinstance(c, dict)]
        # Reconstruct from existing bindings file if present
        bindings_file = self._secrets_dir() / "telegram-principal-bindings.json"
        existing = _read_json_if_present(bindings_file)
        if existing is None or not isinstance(existing.get("bindings"), list):
            return []
        resolved_ids = ids or self._load_state()["ids"]
        contacts: list[dict[str, Any]] = []
        for row in existing["bindings"]:
            if not isinstance(row, dict) or not isinstance(row.get("telegram_user_id"), int):
                continue
            is_owner = row.get("principal_id") == resolved_ids.get("principal_id")
            contacts.append(
                {
                    "telegram_user_id": int(row["telegram_user_id"]),
                    "name": "Eigenaar" if is_owner else str(row.get("principal_id", "Gezinslid")),
                    "role": "owner" if is_owner else "household",
                    "username": "",
                    "principal_id": str(row["principal_id"]),
                    "principal_binding_id": str(
                        row.get("principal_binding_id") or resolved_ids["binding_id"]
                    ),
                    "linked_at": _utc_now_iso(),
                }
            )
        return contacts

    def _save_telegram_contacts_and_bindings(
        self, contacts: list[dict[str, Any]], ids: Mapping[str, str]
    ) -> None:
        _write_json_private(
            self._telegram_contacts_path(),
            {"contract": "EvaTelegramContacts/v1", "contacts": contacts},
        )
        secrets_dir = self._secrets_dir()
        bindings_file = secrets_dir / "telegram-principal-bindings.json"
        if not contacts:
            if bindings_file.exists():
                bindings_file.unlink()
            return
        strict_bindings = [
            {
                "telegram_user_id": int(item["telegram_user_id"]),
                "principal_id": str(item["principal_id"]),
                "principal_binding_id": str(item["principal_binding_id"]),
                "allowed_chat_ids": [str(int(item["telegram_user_id"]))],
            }
            for item in contacts
        ]
        _write_json_private(
            bindings_file,
            {
                "contract": "EvaTelegramPrincipalBindings/v2",
                "bindings": strict_bindings,
            },
        )
        pairing_secret = secrets_dir / "telegram-pairing-secret"
        if not pairing_secret.is_file():
            _write_text_private(pairing_secret, secrets.token_urlsafe(48) + "\n")
        _ = ids

    def _write_telegram_bindings(self, owner_user_id: str, ids: Mapping[str, str]) -> None:
        uid_int = int(owner_user_id)
        state = self._load_state()
        owner_label = state["profile"].get("owner_name", "Eigenaar")
        contacts = [
            c
            for c in self._load_telegram_contacts(ids)
            if int(c["telegram_user_id"]) != uid_int and c.get("role") != "owner"
        ]
        contacts.insert(
            0,
            {
                "telegram_user_id": uid_int,
                "name": owner_label,
                "role": "owner",
                "username": "",
                "principal_id": ids["principal_id"],
                "principal_binding_id": ids["binding_id"],
                "linked_at": _utc_now_iso(),
            },
        )
        self._save_telegram_contacts_and_bindings(contacts, ids)

    def start_telegram_magic_link(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Generate a zero-user-ID 1-scan Telegram QR/deep-link and listen for /start."""
        bot_token_in = str(payload.get("bot_token", "")).strip()
        if bot_token_in:
            self.save_module("telegram", {"enabled": True, "bot_token": bot_token_in})
        state = self._load_state()
        tok_path = self._secrets_dir() / "telegram-bot-token"
        if not tok_path.is_file():
            raise ApplianceSetupError("plak eerst het Telegram BotFather-token")
        bot_token = tok_path.read_text(encoding="utf-8").strip()
        bot_username = self._fetch_telegram_bot_username(bot_token)
        if not bot_username:
            raise ApplianceSetupError(
                "kon de botnaam niet ophalen bij Telegram; controleer het BotFather-token"
            )

        role = str(payload.get("role", "owner")).strip().lower()
        if role not in {"owner", "household"}:
            raise ApplianceSetupError("rol moet 'owner' of 'household' zijn")
        default_name = state["profile"].get("owner_name", "Jaap") if role == "owner" else "Gezinslid"
        person_name = re.sub(r"\s+", " ", str(payload.get("person_name", default_name))).strip()[:60]
        if not person_name:
            person_name = default_name

        state["modules"]["telegram"]["enabled"] = True
        state["modules"]["telegram"]["bot_username"] = bot_username
        state["modules"]["telegram"]["verified"] = True
        self._save_state(state)

        link_token = "eva_" + secrets.token_hex(12)
        deep_link = f"https://t.me/{bot_username}?start={link_token}"
        qr_svg = render_qr_svg(deep_link)

        with self._lock:
            if self._telegram_magic is not None:
                self._telegram_magic.stop_flag = True
            session = _TelegramMagicLinkSession(
                token=link_token,
                person_name=person_name,
                role=role,
                bot_username=bot_username,
                deep_link=deep_link,
                qr_svg=qr_svg,
                expires_at=self._clock() + 600.0,
            )
            self._telegram_magic = session

        if not bool(payload.get("skip_poller", False)):
            poller = threading.Thread(
                target=self._poll_telegram_magic_updates,
                args=(session, bot_token),
                daemon=True,
                name="eva-tg-magic-link",
            )
            poller.start()
        return self.poll_telegram_magic_link()

    def _poll_telegram_magic_updates(
        self, session: _TelegramMagicLinkSession, bot_token: str
    ) -> None:
        paused_service = False
        if not self.dry_run_apply:
            chk = self._run(["systemctl", "is-active", "eva2-local-telegram.service"])
            if chk.returncode == 0 and chk.stdout.strip() == "active":
                self._run(["sudo", "-n", "systemctl", "stop", "eva2-local-telegram.service"])
                paused_service = True
        offset: int | None = None
        expected_texts = {
            f"/start {session.token}",
            f"/start@{session.bot_username} {session.token}",
        }
        try:
            while not session.stop_flag and self._clock() < session.expires_at:
                updates = self._telegram_api_post(
                    bot_token,
                    "getUpdates",
                    {
                        "timeout": 3,
                        "allowed_updates": ["message"],
                        **({"offset": offset} if offset is not None else {}),
                    },
                    timeout=6.0,
                )
                if updates is None:
                    time.sleep(1.5)
                    continue
                for upd in updates.get("result") or []:
                    if not isinstance(upd, dict) or not isinstance(upd.get("update_id"), int):
                        continue
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if not isinstance(msg, dict):
                        continue
                    chat = msg.get("chat") or {}
                    sender = msg.get("from") or {}
                    text = str(msg.get("text") or "").strip()
                    if (
                        chat.get("type") != "private"
                        or not isinstance(sender.get("id"), int)
                        or chat.get("id") != sender.get("id")
                        or text not in expected_texts
                    ):
                        # Default-deny: ignore strangers completely (0 replies, 0 tokens).
                        continue

                    uid = int(sender["id"])
                    tg_username = str(sender.get("username") or "").strip()
                    first_name = str(sender.get("first_name") or session.person_name).strip()
                    linked = self._complete_telegram_magic_link(
                        uid=uid,
                        person_name=session.person_name or first_name,
                        role=session.role,
                        tg_username=tg_username,
                    )
                    # Acknowledge consumed update so telegram_ingress does not replay /start
                    self._telegram_api_post(
                        bot_token, "getUpdates", {"offset": offset, "timeout": 0}, timeout=4.0
                    )
                    eva_name = self._load_character().get("display_name", "Eva")
                    self._telegram_api_post(
                        bot_token,
                        "sendMessage",
                        {
                            "chat_id": uid,
                            "text": (
                                f"Hoi {linked['name']}! Gelukt — ik ben gekoppeld op Telegram. "
                                f"Je kunt hier nu rechtstreeks met mij ({eva_name}) praten."
                            ),
                        },
                        timeout=5.0,
                    )
                    with self._lock:
                        session.linked_contact = linked
                        session.status = "linked"
                        session.stop_flag = True
                    paused_service = True
                    return
            with self._lock:
                if session.status == "waiting" and not session.stop_flag:
                    session.status = "expired"
        finally:
            if (
                not self.dry_run_apply
                and (paused_service or session.status == "linked")
                and (self._secrets_dir() / "telegram-principal-bindings.json").is_file()
                and (self.runtime_root / "runtime.env").is_file()
            ):
                self._sync_telegram_env_if_installed()
                self._run(["sudo", "-n", "systemctl", "enable", "eva2-local-telegram.service"])
                self._run(["sudo", "-n", "systemctl", "restart", "eva2-local-telegram.service"])

    def _complete_telegram_magic_link(
        self, *, uid: int, person_name: str, role: str, tg_username: str
    ) -> dict[str, Any]:
        state = self._load_state()
        ids = state["ids"]
        contacts = [
            c
            for c in self._load_telegram_contacts(ids)
            if int(c["telegram_user_id"]) != uid
            and not (role == "owner" and c.get("role") == "owner")
        ]
        if role == "owner":
            principal_id = ids["principal_id"]
            binding_id = ids["binding_id"]
            state["modules"]["telegram"]["owner_user_id"] = str(uid)
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", person_name.lower()).strip("-")[:24] or "lid"
            principal_id = f"household:{slug}:{uid}"
            binding_id = str(uuid4())

        entry = {
            "telegram_user_id": uid,
            "name": person_name,
            "role": role,
            "username": tg_username,
            "principal_id": principal_id,
            "principal_binding_id": binding_id,
            "linked_at": _utc_now_iso(),
        }
        if role == "owner":
            contacts.insert(0, entry)
        else:
            contacts.append(entry)
        self._save_telegram_contacts_and_bindings(contacts, ids)
        state["modules"]["telegram"]["enabled"] = True
        self._save_state(state)
        return entry

    def _sync_telegram_env_if_installed(self) -> None:
        env_path = self.runtime_root / "runtime.env"
        if not env_path.is_file():
            return
        env_vals = _parse_env_file(env_path)
        secrets_dir = self._secrets_dir()
        contacts = self._load_telegram_contacts()
        if (secrets_dir / "telegram-bot-token").is_file() and contacts:
            env_vals["EVA_TELEGRAM_BOT_TOKEN_FILE"] = str(secrets_dir / "telegram-bot-token")
            env_vals["EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE"] = str(
                secrets_dir / "telegram-principal-bindings.json"
            )
            env_vals["EVA_TELEGRAM_PAIRING_SECRET_FILE"] = str(
                secrets_dir / "telegram-pairing-secret"
            )
            env_vals["EVA_LIVING_CORE_URL"] = self.core_base_url
            owner_c = next((c for c in contacts if c.get("role") == "owner"), None)
            if owner_c:
                env_vals["EVA_TELEGRAM_PROACTIVE_CHAT_ID"] = str(owner_c["telegram_user_id"])
            disabled = [
                p for p in env_vals.get("EVA_EXTERNAL_PROVIDERS_DISABLED", "").split(",")
                if p and p != "telegram"
            ]
            env_vals["EVA_EXTERNAL_PROVIDERS_DISABLED"] = ",".join(disabled)
            _write_env_file(env_path, env_vals)

    @staticmethod
    def _telegram_api_post(
        token: str, method: str, payload: Mapping[str, Any], *, timeout: float = 6.0
    ) -> dict[str, Any] | None:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
        try:
            raw_req = json.dumps(dict(payload)).encode("utf-8")
            conn.request(
                "POST",
                f"/bot{token}/{method}",
                body=raw_req,
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            body = resp.read(128 * 1024)
            if resp.status != 200:
                return None
            decoded = json.loads(body.decode("utf-8"))
            return decoded if isinstance(decoded, dict) and decoded.get("ok") else None
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
            return None
        finally:
            conn.close()

    def poll_telegram_magic_link(self) -> dict[str, Any]:
        contacts = self._load_telegram_contacts()
        tg_active = False
        if not self.dry_run_apply:
            chk = self._run(["systemctl", "is-active", "eva2-local-telegram.service"])
            tg_active = chk.returncode == 0 and chk.stdout.strip() == "active"
        with self._lock:
            sess = self._telegram_magic
            if sess is None:
                return {
                    "status": "idle",
                    "contacts": contacts,
                    "telegram_service_active": tg_active,
                }
            if sess.status == "waiting" and self._clock() >= sess.expires_at:
                sess.status = "expired"
            return {
                "status": sess.status,
                "person_name": sess.person_name,
                "role": sess.role,
                "bot_username": sess.bot_username,
                "deep_link": sess.deep_link,
                "qr_svg": sess.qr_svg,
                "linked_contact": sess.linked_contact,
                "error": sess.error,
                "contacts": contacts,
                "telegram_service_active": tg_active,
            }

    def remove_telegram_contact(self, telegram_user_id: int) -> dict[str, Any]:
        state = self._load_state()
        contacts = [
            c
            for c in self._load_telegram_contacts(state["ids"])
            if int(c["telegram_user_id"]) != int(telegram_user_id)
        ]
        self._save_telegram_contacts_and_bindings(contacts, state["ids"])
        if state["modules"]["telegram"].get("owner_user_id") == str(telegram_user_id):
            state["modules"]["telegram"]["owner_user_id"] = ""
            self._save_state(state)
        return {"ok": True, "contacts": contacts}

    def test_module(self, module_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload:
            self.save_module(module_id, payload)
        state = self._load_state()
        secrets_dir = self._secrets_dir()
        if module_id == "telegram":
            tok_path = secrets_dir / "telegram-bot-token"
            if not tok_path.is_file():
                raise ApplianceSetupError("vul eerst het Telegram bot-token in")
            token = tok_path.read_text(encoding="utf-8").strip()
            probe = TelegramInstallerReadOnlyProbe()
            try:
                obs = probe.read_only_probe(secret=memoryview(token.encode("ascii")), timeout_seconds=6.0)
            except InstallerProviderProbeError as exc:
                raise ApplianceSetupError(f"Telegram-verbinding mislukt ({exc.reason_code})") from exc
            bot_username = self._fetch_telegram_bot_username(token)
            state["modules"]["telegram"]["bot_username"] = bot_username
            state["modules"]["telegram"]["verified"] = True
            self._save_state(state)
            return {
                "ok": True,
                "reason_code": obs.reason_code,
                "detail": f"Verbonden met @{bot_username}" if bot_username else "Telegram-bot geverifieerd",
                "bot_username": bot_username,
            }
        if module_id == "smart_home":
            tok_path = secrets_dir / "home-assistant-token"
            if not tok_path.is_file():
                raise ApplianceSetupError("vul eerst de Home Assistant Long-Lived Access Token in")
            url = state["modules"]["smart_home"]["url"]
            token = tok_path.read_text(encoding="utf-8").strip()
            try:
                probe = HomeAssistantInstallerReadOnlyProbe(base_url=url)
                obs = probe.read_only_probe(secret=memoryview(token.encode("ascii")), timeout_seconds=5.0)
            except (ValueError, InstallerProviderProbeError) as exc:
                reason = getattr(exc, "reason_code", str(exc))
                raise ApplianceSetupError(f"Home Assistant-verbinding mislukt ({reason})") from exc
            state["modules"]["smart_home"]["verified"] = True
            self._save_state(state)
            return {"ok": True, "reason_code": obs.reason_code, "detail": f"Home Assistant bereikbaar op {url}"}
        if module_id == "mail":
            key_path = secrets_dir / "eva-agentmail-api-key"
            inbox_id = state["modules"]["mail"]["inbox_id"]
            if not inbox_id or not key_path.is_file():
                raise ApplianceSetupError("vul zowel de inbox-ID als de API-sleutel in")
            key = key_path.read_text(encoding="utf-8").strip()
            try:
                probe = AgentMailInstallerReadOnlyProbe(inbox_id=inbox_id)
                obs = probe.read_only_probe(secret=memoryview(key.encode("ascii")), timeout_seconds=6.0)
            except (ValueError, InstallerProviderProbeError) as exc:
                reason = getattr(exc, "reason_code", str(exc))
                raise ApplianceSetupError(f"E-mailverbinding mislukt ({reason})") from exc
            state["modules"]["mail"]["verified"] = True
            self._save_state(state)
            return {"ok": True, "reason_code": obs.reason_code, "detail": f"Inbox {inbox_id} bereikbaar"}
        raise ApplianceSetupError("deze module heeft geen losse verbindingstest nodig")

    @staticmethod
    def _fetch_telegram_bot_username(token: str) -> str:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=5.0)
        try:
            conn.request("POST", f"/bot{token}/getMe", body=b"{}", headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            if resp.status != 200:
                return ""
            data = json.loads(resp.read(16_384).decode("utf-8"))
            username = (data.get("result") or {}).get("username")
            return str(username) if isinstance(username, str) else ""
        except (OSError, http.client.HTTPException, ValueError):
            return ""
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Step 5: Apply installation / reconfigure & portable export
    # ------------------------------------------------------------------

    def _compose_yaml(self, state: Mapping[str, Any], arch: str) -> str:
        tier_profile = HARDWARE_TIERS[state["hardware"]["tier"]]
        hindsight_image = HINDSIGHT_IMAGES_BY_ARCH.get(arch, HINDSIGHT_IMAGES_BY_ARCH["amd64"])
        return "\n".join(
            (
                "services:",
                "  postgres:",
                f"    image: {POSTGRES_IMAGE}",
                "    container_name: eva2-local-postgres",
                "    restart: unless-stopped",
                f'    command: ["postgres", "-c", "shared_buffers={tier_profile.postgres_shared_buffers_mb}MB", "-c", "plan_cache_mode=force_custom_plan"]',
                "    ports:",
                '      - "127.0.0.1:5432:5432"',
                "    environment:",
                "      POSTGRES_DB: eva2",
                "      POSTGRES_USER: postgres",
                "      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password",
                "    volumes:",
                f"      - {self.runtime_root}/pg-secret/postgres-password:/run/secrets/postgres_password:ro",
                "      - eva2-local-postgres:/var/lib/postgresql/data",
                "    healthcheck:",
                '      test: ["CMD-SHELL", "pg_isready -U postgres -d eva2"]',
                "      interval: 3s",
                "      timeout: 3s",
                "      retries: 30",
                "      start_period: 10s",
                "",
                "  hindsight:",
                f"    image: {hindsight_image}",
                "    container_name: eva2-local-hindsight",
                "    restart: unless-stopped",
                "    init: true",
                "    ports:",
                '      - "127.0.0.1:44672:8888"',
                "    environment:",
                '      HINDSIGHT_ENABLE_CP: "false"',
                "      HINDSIGHT_API_LLM_PROVIDER: openai-codex",
                "      HINDSIGHT_API_LLM_MODEL: gpt-6-astra",
                "      HINDSIGHT_API_LLM_REASONING_EFFORT: medium",
                "      HINDSIGHT_API_EMBEDDINGS_PROVIDER: local",
                '      HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU: "true"',
                '      HINDSIGHT_API_RERANKER_LOCAL_FORCE_CPU: "true"',
                '      HINDSIGHT_API_RERANKER_LOCAL_FP16: "false"',
                f'      OMP_NUM_THREADS: "{tier_profile.hindsight_threads}"',
                f'      MKL_NUM_THREADS: "{tier_profile.hindsight_threads}"',
                '      HINDSIGHT_API_RERANKER_LOCAL_MAX_CONCURRENT: "2"',
                '      HINDSIGHT_API_RERANKER_MAX_CANDIDATES_LOW: "32"',
                "      HINDSIGHT_API_WORKER_ID: eva2-local-hindsight",
                "    volumes:",
                f"      - {self.runtime_root}/hindsight-access:/home/hindsight/.codex:ro",
                f"      - {self.runtime_root}/hindsight-access/auth.lock:/home/hindsight/.codex/auth.lock:rw",
                "      - eva2-local-hindsight:/home/hindsight/.pg0",
                "    healthcheck:",
                "      test:",
                "        - CMD",
                "        - python",
                "        - -c",
                '        - "import json,urllib.request; assert json.load(urllib.request.urlopen(\'http://127.0.0.1:8888/health\',timeout=2))[\'status\']==\'healthy\'"',
                "      interval: 5s",
                "      timeout: 3s",
                "      retries: 36",
                "      start_period: 30s",
                "",
                "volumes:",
                "  eva2-local-postgres:",
                "    name: eva2-local-postgres",
                "  eva2-local-hindsight:",
                "    name: eva2-local-hindsight",
                "",
            )
        )

    def _build_identity_description(self, state: Mapping[str, Any], character: Mapping[str, Any]) -> str:
        preset = CHARACTER_PRESETS[0]
        for item in CHARACTER_PRESETS:
            if item.preset_id == character.get("preset_id"):
                preset = item
                break
        owner = state["profile"].get("owner_name", "Jaap")
        family = state["profile"].get("family_members") or []
        parts = [preset.description_template]
        if family:
            parts.append(f"Je woont digitaal in huis bij {owner} en {', '.join(family)}.")
        else:
            parts.append(f"Je bent de persoonlijke digitale huisgenoot van {owner}.")
        proactivity = character.get("proactivity", "balanced")
        if proactivity == "quiet":
            parts.append("Blijf rustig op de achtergrond tot je aangesproken wordt.")
        elif proactivity == "proactive":
            parts.append("Neem passend en behulpzaam initiatief wanneer iets opvalt.")
        rules = str(character.get("custom_rules", "")).strip()
        if rules:
            parts.append("Huisafspraken: " + " ".join(rules.splitlines()))
        return " ".join(parts)

    @staticmethod
    def _wait_for_postgres(dsn: str, *, timeout_seconds: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_exc: Exception | None = None
        sa_dsn = sqlalchemy_postgres_dsn(dsn)
        while True:
            engine = sa.create_engine(sa_dsn)
            try:
                with engine.connect() as conn:
                    conn.execute(sa.text("SELECT 1"))
                return
            except Exception as exc:
                last_exc = exc
            finally:
                engine.dispose()
            if time.monotonic() >= deadline:
                raise ApplianceSetupError(
                    f"PostgreSQL reageert nog niet op tijd: {last_exc}", status_code=503
                )
            time.sleep(1.0)

    def apply_installation(self) -> dict[str, Any]:
        state = self._load_state()
        character = self._load_character()
        secrets_dir = self._secrets_dir()
        priv_dir = _ensure_private_dir(self.runtime_root / "private")
        _ensure_private_dir(priv_dir / "restore-authority")
        _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        _ensure_private_dir(self.runtime_root / "hindsight-access")
        lock_file = self.runtime_root / "hindsight-access" / "auth.lock"
        if not lock_file.exists():
            lock_file.touch()
            os.chmod(lock_file, 0o600)

        pw_file = secrets_dir / "postgres-password"
        if not pw_file.is_file():
            pw = secrets.token_hex(32)
            _write_text_private(pw_file, pw + "\n")
        else:
            pw = pw_file.read_text(encoding="utf-8").strip()

        dsn_file = secrets_dir / "database-dsn"
        if not dsn_file.is_file():
            _write_text_private(
                dsn_file, f"postgresql://postgres:{pw}@127.0.0.1:5432/eva2\n"
            )
        dsn = dsn_file.read_text(encoding="utf-8").strip()

        tok_file = secrets_dir / "dashboard-token"
        if not tok_file.is_file():
            _write_text_private(tok_file, secrets.token_urlsafe(36) + "\n")
        dashboard_token = tok_file.read_text(encoding="utf-8").strip()

        self._write_budget_file(state)
        if not (self._identity_authority_dir() / "current.json").is_file():
            self.save_character(character)
        else:
            self._publish_runtime_identity_files()

        # Compose runtime.env safely while retaining existing non-wizard fields.
        env_path = self.runtime_root / "runtime.env"
        env_vals = _parse_env_file(env_path)
        ids = state["ids"]
        env_vals.update(
            {
                "EVA_DATABASE_DSN_FILE": str(dsn_file),
                "EVA_HINDSIGHT_URL": self.hindsight_url,
                "EVA_HINDSIGHT_BANK_NAMESPACE": env_vals.get(
                    "EVA_HINDSIGHT_BANK_NAMESPACE", "eva2_local"
                ),
                "EVA_CONVERSATION_AUTH_FILE": env_vals.get(
                    "EVA_CONVERSATION_AUTH_FILE",
                    str(self.runtime_root / "model-lease" / "auth.json"),
                ),
                "EVA_CONVERSATION_MODEL": state["model"].get("conversation_model", "gpt-6-astra"),
                "EVA_CONVERSATION_REASONING_EFFORT": state["model"].get("reasoning_effort", "medium"),
                "TZ": state["profile"].get("time_zone", "Europe/Amsterdam"),
                "EVA_RUNTIME_TENANT_ID": ids["tenant_id"],
                "EVA_RUNTIME_INSTALLATION_ID": ids["installation_id"],
                "EVA_RUNTIME_PRINCIPAL_ID": ids["principal_id"],
                "EVA_RUNTIME_RESOURCE_BUDGET_FILE": str(priv_dir / "runtime-budget.json"),
                "EVA_DASHBOARD_TENANT_ID": ids["tenant_id"],
                "EVA_DASHBOARD_INSTALLATION_ID": ids["installation_id"],
                "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": ids["binding_id"],
                "EVA_DASHBOARD_ACCESS_TOKEN_FILE": str(tok_file),
                "EVA_PORTABLE_RESTORE_AUTHORITY_FILE": str(
                    priv_dir / "restore-authority" / "current.json"
                ),
                "EVA_MODEL_PROVIDER_DIRECTORY": str(
                    self.runtime_root / "work" / "model-providers"
                ),
                "EVA_LIVING_CORE_URL": self.core_base_url,
                "EVA_FILE_BATCH_ROOT": str(self.runtime_root / "work"),
                "EVA_IDENTITY_NAME": character["display_name"],
                "EVA_IDENTITY_DESCRIPTION": self._build_identity_description(state, character),
                "EVA_IDENTITY_PACK_FILE": str(self.runtime_root / "identity" / "current.json"),
                "EVA_IDENTITY_PACK_TRUST_FILE": str(self.runtime_root / "identity" / "trust.json"),
            }
        )

        disabled_providers: list[str] = []
        mods = state["modules"]

        # Telegram
        tg_mod = mods["telegram"]
        tg_contacts = self._load_telegram_contacts(ids)
        if tg_mod.get("enabled") and (secrets_dir / "telegram-bot-token").is_file():
            env_vals["EVA_TELEGRAM_BOT_TOKEN_FILE"] = str(secrets_dir / "telegram-bot-token")
            if (secrets_dir / "telegram-principal-bindings.json").is_file():
                env_vals["EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE"] = str(
                    secrets_dir / "telegram-principal-bindings.json"
                )
            if (secrets_dir / "telegram-pairing-secret").is_file():
                env_vals["EVA_TELEGRAM_PAIRING_SECRET_FILE"] = str(
                    secrets_dir / "telegram-pairing-secret"
                )
            owner_tg = next((c for c in tg_contacts if c.get("role") == "owner"), None)
            if owner_tg:
                env_vals["EVA_TELEGRAM_PROACTIVE_CHAT_ID"] = str(owner_tg["telegram_user_id"])
        else:
            disabled_providers.append("telegram_contacts")
            for k in (
                "EVA_TELEGRAM_BOT_TOKEN_FILE",
                "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE",
                "EVA_TELEGRAM_PAIRING_SECRET_FILE",
                "EVA_TELEGRAM_PROACTIVE_CHAT_ID",
            ):
                env_vals.pop(k, None)

        # Smart Home (Home Assistant)
        sh_mod = mods["smart_home"]
        sh_tok_file = secrets_dir / "home-assistant-token"
        if sh_mod.get("enabled") and sh_tok_file.is_file():
            env_vals["EVA_HOME_ASSISTANT_URL"] = sh_mod["url"]
            env_vals["EVA_HOME_ASSISTANT_TOKEN_FILE"] = str(sh_tok_file)
            env_vals["EVA_HOME_ASSISTANT_API_ENABLED"] = "true"
            principal_hashes = [
                hashlib.sha256(str(ids["principal_id"]).encode("utf-8")).hexdigest()
            ]
            for tg_c in self._load_telegram_contacts(ids):
                if tg_c.get("principal_id"):
                    principal_hashes.append(
                        hashlib.sha256(str(tg_c["principal_id"]).encode("utf-8")).hexdigest()
                    )
            env_vals["EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST"] = ",".join(
                sorted(set(principal_hashes))
            )
            if sh_mod.get("allow_effects"):
                env_vals["EVA_HOME_ASSISTANT_EFFECTS_ENABLED"] = "true"
                ha_tok = sh_tok_file.read_text(encoding="utf-8").strip()
                disc = (
                    {"allowlist": ["switch.eva_appliance:turn_on", "switch.eva_appliance:turn_off"]}
                    if self.dry_run_apply
                    else self._discover_ha_entities(str(sh_mod["url"]), ha_tok)
                )
                env_vals["EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST"] = ",".join(disc["allowlist"])
            else:
                env_vals["EVA_HOME_ASSISTANT_EFFECTS_ENABLED"] = "false"
                env_vals.pop("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST", None)
        else:
            disabled_providers.append("home_assistant")
            for k in (
                "EVA_HOME_ASSISTANT_URL",
                "EVA_HOME_ASSISTANT_TOKEN_FILE",
                "EVA_HOME_ASSISTANT_API_ENABLED",
                "EVA_HOME_ASSISTANT_EFFECTS_ENABLED",
                "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST",
                "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST",
            ):
                env_vals.pop(k, None)

        # Mail (AgentMail)
        mail_mod = mods["mail"]
        if mail_mod.get("enabled") and mail_mod.get("inbox_id") and (secrets_dir / "eva-agentmail-api-key").is_file():
            env_vals["EVA_AGENTMAIL_INBOX_ID"] = mail_mod["inbox_id"]
            env_vals["EVA_AGENTMAIL_API_KEY_FILE"] = str(secrets_dir / "eva-agentmail-api-key")
        else:
            disabled_providers.append("agentmail")
            env_vals.pop("EVA_AGENTMAIL_INBOX_ID", None)
            env_vals.pop("EVA_AGENTMAIL_API_KEY_FILE", None)

        env_vals["EVA_EXTERNAL_PROVIDERS_DISABLED"] = ",".join(disabled_providers)

        # Provision Universal MCP Gateway, Pre-LLM Vault tools, and Procedure References
        gw_env = ensure_gateway_runtime_files(
            runtime_root=self.runtime_root,
            tenant_id=ids["tenant_id"],
            installation_id=ids["installation_id"],
            owner_principal_id=ids["principal_id"],
        )
        env_vals.update(gw_env)

        # Fast live contact model + deferred CPU recall when running Astra medium/high
        if (
            state["model"].get("mode", "codex") == "codex"
            and env_vals.get("EVA_CONVERSATION_MODEL") == "gpt-6-astra"
            and env_vals.get("EVA_CONVERSATION_REASONING_EFFORT") in {"medium", "high"}
        ):
            env_vals["EVA_CONVERSATION_CONTACT_MODEL"] = "gpt-6-sol"
            env_vals["EVA_CONVERSATION_CONTACT_REASONING_EFFORT"] = "none"
        else:
            env_vals.pop("EVA_CONVERSATION_CONTACT_MODEL", None)
            env_vals.pop("EVA_CONVERSATION_CONTACT_REASONING_EFFORT", None)

        # Activate background Research & Digital Worker when Codex model lease is active
        lease_file = self.runtime_root / "model-lease" / "auth.json"
        delegated_ready = bool(
            not self.dry_run_apply
            and state["model"].get("mode", "codex") == "codex"
            and (lease_file.is_file() or self._codex_auth_status().get("logged_in"))
        )
        if delegated_ready:
            env_vals["EVA_DELEGATED_WORK_ENABLED"] = "true"
            env_vals["EVA_DELEGATED_RESEARCH_ENABLED"] = "true"
            env_vals["EVA_DELEGATED_DIGITAL_ENABLED"] = "true"
            env_vals["EVA_MODEL_ACCESS_LEASE_FILE"] = str(lease_file)
            env_vals["EVA_DELEGATED_WORKER_ID"] = "eva2-local-worker"
        else:
            for dk in (
                "EVA_DELEGATED_WORK_ENABLED",
                "EVA_DELEGATED_RESEARCH_ENABLED",
                "EVA_DELEGATED_DIGITAL_ENABLED",
                "EVA_MODEL_ACCESS_LEASE_FILE",
                "EVA_DELEGATED_WORKER_ID",
            ):
                env_vals.pop(dk, None)

        _write_env_file(env_path, env_vals)

        hw = _detect_host_hardware()
        compose_path = self.runtime_root / "docker-compose.appliance.yml"
        _write_text_private(compose_path, self._compose_yaml(state, hw["architecture"]))

        steps: list[dict[str, str]] = [
            {"step": "config", "status": "ok", "detail": "Profiel, karakter, limieten en runtime.env geschreven"}
        ]

        if self.dry_run_apply:
            runtime_snap = None
            steps.append(
                {"step": "dry_run", "status": "ok", "detail": "Dry-run modus: Docker en systemd overgeslagen"}
            )
        else:
            # Ensure PostgreSQL and Hindsight are running when Docker is available.
            pg_secret = self.runtime_root / "pg-secret" / "postgres-password"
            if pg_secret.is_file():
                compose_main = self.runtime_root / "docker-compose.yml"
                target_compose = compose_main if compose_main.is_file() else compose_path
                up_res = self._run(["docker", "compose", "-f", str(target_compose), "up", "-d"])
                steps.append(
                    {
                        "step": "containers",
                        "status": "ok" if up_res.returncode == 0 else "warning",
                        "detail": "Containers bijgewerkt"
                        if up_res.returncode == 0
                        else f"Docker Compose gaf code {up_res.returncode}",
                    }
                )
            try:
                self._wait_for_postgres(dsn, timeout_seconds=30.0)
                cfg = alembic.config.Config(str(self.repo_root / "deploy" / "alembic.ini"))
                cfg.set_main_option("sqlalchemy.url", sqlalchemy_postgres_dsn(dsn))
                alembic.command.upgrade(cfg, "head")
                steps.append({"step": "database", "status": "ok", "detail": "Database schema actueel (head)"})
            except Exception as exc:
                steps.append({"step": "database", "status": "warning", "detail": f"Migratie kon niet lopen: {exc}"})

            try:
                engine = sa.create_engine(sqlalchemy_postgres_dsn(dsn))
                try:
                    with engine.connect() as conn:
                        existing = conn.execute(
                            sa.text(
                                "SELECT 1 FROM eva_core.principal_bindings "
                                "WHERE tenant_id = CAST(:t AS uuid) "
                                "AND principal_binding_id = CAST(:b AS uuid)"
                            ),
                            {"t": ids["tenant_id"], "b": ids["binding_id"]},
                        ).first()
                finally:
                    engine.dispose()
                if existing is not None:
                    steps.append({"step": "authority", "status": "ok", "detail": "Bestaande eigenaar-autoriteit behouden"})
                else:
                    initialize_dashboard_authority(
                        dsn,
                        tenant_id=ids["tenant_id"],
                        installation_id=ids["installation_id"],
                        principal_id=ids["principal_id"],
                        binding_id=ids["binding_id"],
                        release_sha256=hashlib.sha256(b"eva2-appliance-local").hexdigest(),
                        access_token=dashboard_token,
                        owner_label=state["profile"]["owner_name"],
                    )
                    steps.append({"step": "authority", "status": "ok", "detail": "Eigenaar-autoriteit geïnitialiseerd"})
            except Exception as exc:
                steps.append({"step": "authority", "status": "warning", "detail": f"Eigenaar-autoriteit controle: {exc}"})

            # Start local authenticated MCP Gateway & Universal Capability Host BEFORE Living Core
            self._run(["sudo", "-n", "systemctl", "enable", "eva2-local-gateway.service"])
            self._run(["sudo", "-n", "systemctl", "restart", "eva2-local-gateway.service"])
            gw_deadline = time.monotonic() + 5.0
            while time.monotonic() < gw_deadline:
                gw_st, _ = self._http_get_json("http://127.0.0.1:44712/health")
                if gw_st == 200:
                    break
                time.sleep(0.25)

            self._run(
                [
                    "sudo",
                    "-n",
                    "systemctl",
                    "enable",
                    "eva2-local-core.service",
                    "eva2-local-owner.service",
                ]
            )
            restart_res = self._run(
                [
                    "sudo",
                    "-n",
                    "systemctl",
                    "restart",
                    "eva2-local-core.service",
                    "eva2-local-owner.service",
                ]
            )
            if env_vals.get("EVA_DELEGATED_WORK_ENABLED") == "true":
                self._run(["sudo", "-n", "systemctl", "enable", "eva2-local-worker.service"])
                self._run(["sudo", "-n", "systemctl", "restart", "eva2-local-worker.service"])
            else:
                self._run(["sudo", "-n", "systemctl", "stop", "eva2-local-worker.service"])
            if restart_res.returncode == 0:
                steps.append(
                    {
                        "step": "core_restart",
                        "status": "ok",
                        "detail": "Eva Living Core en dashboard herstart met nieuwe instellingen",
                    }
                )
            else:
                steps.append(
                    {
                        "step": "core_restart",
                        "status": "manual",
                        "detail": "Instellingen opgeslagen; herstart eva2-local-core.service als root om wijzigingen actief te laden",
                    }
                )

            poll_deadline = time.monotonic() + 12.0
            runtime_snap = self._runtime_health()
            while (
                time.monotonic() < poll_deadline
                and not (runtime_snap.get("core_ready") or runtime_snap.get("core_alive"))
            ):
                time.sleep(0.5)
                runtime_snap = self._runtime_health()

            tg_can_run = (
                mods["telegram"].get("enabled")
                and (secrets_dir / "telegram-bot-token").is_file()
                and (secrets_dir / "telegram-principal-bindings.json").is_file()
            )
            if tg_can_run:
                self._run(["sudo", "-n", "systemctl", "enable", "eva2-local-telegram.service"])
                tg_res = self._run(["sudo", "-n", "systemctl", "restart", "eva2-local-telegram.service"])
                steps.append(
                    {
                        "step": "telegram_ingress",
                        "status": "ok" if tg_res.returncode == 0 else "manual",
                        "detail": "Telegram ontvangstservice actief"
                        if tg_res.returncode == 0
                        else "Telegram instellingen klaar; eva2-local-telegram.service niet gestart",
                    }
                )
            else:
                self._run(["sudo", "-n", "systemctl", "stop", "eva2-local-telegram.service"])

        state["last_applied_at"] = _utc_now_iso()
        self._save_state(state)
        self._sync_household_head_settings()
        return {
            "ok": True,
            "applied_at": state["last_applied_at"],
            "steps": steps,
            "runtime": runtime_snap if runtime_snap is not None else self._runtime_health(),
        }

    # ------------------------------------------------------------------
    # Contact Hub: Eva Pocket / HoloStage QR, Live Chat Test & Streaming Proxy
    # ------------------------------------------------------------------

    def _preferred_lan_origin(self, base_origin: str | None) -> tuple[str, list[str]]:
        lan_ips = _detect_lan_ipv4s()
        hostname = socket.gethostname()
        port = 8765
        scheme = "http"
        req_host = ""
        if base_origin:
            parsed = urlsplit(base_origin)
            if parsed.scheme in {"http", "https"}:
                scheme = parsed.scheme
            if parsed.port:
                port = parsed.port
            elif parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 80
            req_host = (parsed.hostname or "").lower()

        port_suffix = "" if (scheme == "http" and port == 80) or (scheme == "https" and port == 443) else f":{port}"
        candidates: list[str] = []
        if req_host and req_host not in {"127.0.0.1", "localhost", "::1"}:
            candidates.append(f"{scheme}://{req_host}{port_suffix}")
        for ip in lan_ips:
            url = f"{scheme}://{ip}{port_suffix}"
            if url not in candidates:
                candidates.append(url)
        mdns_host = hostname if hostname.endswith(".local") else f"{hostname}.local"
        mdns_url = f"{scheme}://{mdns_host}{port_suffix}"
        if mdns_url not in candidates:
            candidates.append(mdns_url)
        if not candidates:
            candidates.append(f"http://127.0.0.1{port_suffix}")
        return candidates[0], candidates

    def _holostage_grants_list(self) -> list[dict[str, Any]]:
        mp_dir = _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            snap = mgr.build_stage_state(viewer_kind="owner")
            return [
                dict(g)
                for g in (snap.get("device_grants") or [])
                if isinstance(g, dict) and g.get("status") == "active"
            ]
        stage_file = mp_dir / "holostage-state.json"
        data = _read_json_if_present(stage_file) or {}
        return [
            {k: v for k, v in g.items() if k != "token_sha256"}
            for g in (data.get("device_grants") or [])
            if isinstance(g, dict) and g.get("status") == "active"
        ]

    def contact_hub_snapshot(self, *, base_origin: str | None = None) -> dict[str, Any]:
        primary_origin, all_origins = self._preferred_lan_origin(base_origin)
        tg_state = self.poll_telegram_magic_link()
        state = self._load_state()
        return {
            "contract": "EvaContactHub/v1",
            "primary_origin": primary_origin,
            "lan_origins": all_origins,
            "owner_name": state["profile"].get("owner_name", "Jaap"),
            "family_members": list(state["profile"].get("family_members") or []),
            "eva_name": self._load_character().get("display_name", "Eva"),
            "device_grants": self._holostage_grants_list(),
            "telegram": {
                "bot_username": state["modules"]["telegram"].get("bot_username", ""),
                "token_saved": (self._secrets_dir() / "telegram-bot-token").is_file(),
                **tg_state,
            },
            "runtime": self._runtime_health(),
        }

    def create_device_pairing(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        primary_origin, all_origins = self._preferred_lan_origin(base_origin)
        mode = str(payload.get("mode", "pocket")).strip().lower()
        label = re.sub(r"\s+", " ", str(payload.get("label", "Mijn Telefoon"))).strip()[:80]
        if not label:
            label = "Mijn Telefoon"

        if mode == "owner_browser":
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            code = "EVA-DEV-" + "".join(secrets.choice(alphabet) for _ in range(10))
            with self._lock:
                self._device_invites[code] = self._clock() + 900.0
            url = f"{primary_origin}/pair#{code}"
            return {
                "ok": True,
                "mode": "owner_browser",
                "label": label,
                "url": url,
                "alternate_urls": [f"{orig}/pair#{code}" for orig in all_origins],
                "qr_svg": render_qr_svg(url),
                "expires_in_minutes": 15,
            }

        if mode not in {"pocket", "unifi_screen", "tv_cast", "cockpit"}:
            raise ApplianceSetupError("onbekend apparaattype voor koppeling")

        allow_private = bool(payload.get("allow_private", mode in {"pocket", "unifi_screen", "cockpit"}))
        allow_voice = bool(payload.get("allow_owner_voice", mode == "pocket"))
        room_id = str(payload.get("room_id", "woonkamer")).strip() or "woonkamer"

        mp_dir = _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            issued = mgr.issue_device_token(
                label=label,
                form_factor=mode,  # type: ignore[arg-type]
                room_id=room_id,
                allow_private_cards_when_alone=allow_private,
                allow_owner_voice=allow_voice,
            )
            pair_path = str(issued["pair_path"])
            grant_info = dict(issued["grant"])
        else:
            raw_token = "stg_" + secrets.token_urlsafe(32)
            token_sha = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            grant_id = "stg_" + token_sha[:12]
            now_iso = _utc_now_iso()
            grant_info = {
                "grant_id": grant_id,
                "label": label,
                "form_factor": mode,
                "room_id": room_id,
                "allow_private_cards_when_alone": allow_private and mode != "tv_cast",
                "allow_owner_voice": allow_voice and mode == "pocket",
                "status": "active",
                "created_at": now_iso,
                "expires_at": now_iso,
            }
            stage_file = mp_dir / "holostage-state.json"
            existing_stage = _read_json_if_present(stage_file) or {"device_grants": []}
            grants = list(existing_stage.get("device_grants") or [])
            grants.insert(0, {**grant_info, "token_sha256": token_sha})
            existing_stage["device_grants"] = grants[:24]
            _write_json_private(stage_file, existing_stage)
            pair_path = f"/internal/v1/dashboard/holostage?mode={mode}#{raw_token}"

        url = f"{primary_origin}{pair_path}"
        return {
            "ok": True,
            "mode": mode,
            "label": label,
            "url": url,
            "alternate_urls": [f"{orig}{pair_path}" for orig in all_origins],
            "qr_svg": render_qr_svg(url),
            "grant": grant_info,
            "device_grants": self._holostage_grants_list(),
        }

    def revoke_device_pairing(self, grant_id: str) -> dict[str, Any]:
        clean_id = str(grant_id).strip()
        if not clean_id:
            raise ApplianceSetupError("grant_id ontbreekt")
        mp_dir = _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            mgr.revoke_device_grant(clean_id)
        else:
            stage_file = mp_dir / "holostage-state.json"
            existing_stage = _read_json_if_present(stage_file) or {"device_grants": []}
            for g in existing_stage.get("device_grants") or []:
                if isinstance(g, dict) and g.get("grant_id") == clean_id:
                    g["status"] = "revoked"
            _write_json_private(stage_file, existing_stage)
        return {"ok": True, "device_grants": self._holostage_grants_list()}

    def test_live_contact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Send an immediate real greeting turn to Eva Living Core and return reply + latency."""
        text = str(payload.get("text", "")).strip()
        if not text:
            eva_name = self._load_character().get("display_name", "Eva")
            text = f"Hoi {eva_name}! Stel jezelf in twee korte zinnen voor en noem voor wie je hier in huis klaarstaat."
        if len(text) > 1000:
            raise ApplianceSetupError("testbericht mag maximaal 1000 tekens bevatten")

        t0 = time.monotonic()
        status_code, _, stream_or_bytes = self.open_dashboard_stream(
            method="POST",
            path="/internal/v1/dashboard/conversation/respond",
            query="",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
                "Origin": self.core_base_url,
            },
            body=json.dumps({"text": text}).encode("utf-8"),
            owner_authenticated=True,
        )
        raw = (
            stream_or_bytes
            if isinstance(stream_or_bytes, bytes)
            else b"".join(stream_or_bytes)
        )
        elapsed_ms = max(1, round((time.monotonic() - t0) * 1000))
        if status_code != 200:
            raise ApplianceSetupError(
                f"Eva kon het bericht nog niet beantwoorden (HTTP {status_code}). "
                "Controleer of stap 3 (AI-Brein) en stap 5 (Opslaan & Activeren) zijn uitgevoerd.",
                status_code=502,
            )

        deltas: list[str] = []
        final_obj: dict[str, Any] | None = None
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("contract") == "EvaTextDelta/v1" and isinstance(event.get("text"), str):
                deltas.append(event["text"])
            elif event.get("contract") == "EvaInternalGroundedReply/v1":
                final_obj = event
            elif event.get("contract") == "EvaTextStreamError/v1":
                code = event.get("code") or "model_unavailable"
                raise ApplianceSetupError(
                    f"Eva is bereikbaar, maar het AI-model gaf een fout ({code}). Controleer stap 3 (AI-Brein).",
                    status_code=502,
                )

        reply_text = (
            str(final_obj.get("text"))
            if final_obj and isinstance(final_obj.get("text"), str)
            else "".join(deltas)
        ).strip()
        if not reply_text:
            raise ApplianceSetupError(
                "Geen tekstantwoord ontvangen van Eva; controleer of het AI-model actief is.",
                status_code=502,
            )
        return {
            "ok": True,
            "prompt": text,
            "reply_text": reply_text,
            "provider": (final_obj or {}).get("provider", "actief"),
            "latency_ms": elapsed_ms,
        }

    def open_dashboard_stream(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
        owner_authenticated: bool = True,
    ) -> tuple[int, dict[str, str], bytes | Iterator[bytes]]:
        if (
            not path
            or "\\" in path
            or ".." in path
            or (path != "/internal/v1/dashboard" and not path.startswith("/internal/v1/dashboard/"))
        ):
            raise ApplianceSetupError("ongeldig dashboardpad", status_code=400)

        parsed = urlsplit(self.core_base_url)
        up_host = parsed.hostname or "127.0.0.1"
        up_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        lower_in = {str(k).lower(): str(v) for k, v in headers.items()}

        up_headers: dict[str, str] = {}
        if owner_authenticated:
            tok_path = self._secrets_dir() / "dashboard-token"
            if not tok_path.is_file():
                raise ApplianceSetupError("Eva dashboard is nog niet geactiveerd", status_code=503)
            try:
                token = tok_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ApplianceSetupError(
                    "dashboardtoegangstoken kon niet gelezen worden", status_code=503
                ) from exc
            if len(token) < 32:
                raise ApplianceSetupError("dashboardtoegangstoken is ongeldig", status_code=503)
            up_headers["Host"] = f"localhost:{up_port}"
            up_headers["Authorization"] = f"Bearer {token}"
            if lower_in.get("origin"):
                up_headers["Origin"] = f"http://localhost:{up_port}"
        else:
            if path != "/internal/v1/dashboard/holostage" and not path.startswith(
                "/internal/v1/dashboard/holostage/"
            ):
                raise ApplianceSetupError("koppeling vereist", status_code=401)
            up_headers["Host"] = lower_in.get("host") or f"localhost:{up_port}"
            up_headers["X-HoloStage-Lan-Proxy"] = "1"
            if lower_in.get("origin"):
                up_headers["Origin"] = lower_in["origin"]

        for key, out_key in (
            ("accept", "Accept"),
            ("content-type", "Content-Type"),
            ("accept-language", "Accept-Language"),
            ("user-agent", "User-Agent"),
            ("x-holostage-client", "X-HoloStage-Client"),
            ("x-holostage-token", "X-HoloStage-Token"),
        ):
            if key in lower_in and lower_in[key]:
                up_headers[out_key] = lower_in[key]

        raw_cookie = lower_in.get("cookie") or ""
        if raw_cookie:
            for part in raw_cookie.split(";"):
                name, sep, val = part.strip().partition("=")
                if sep and name.strip() == "eva_holostage_session" and val.strip():
                    up_headers["Cookie"] = f"eva_holostage_session={val.strip()}"
                    break

        target_path = f"{path}?{query}" if query else path
        conn_cls = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(up_host, port=up_port, timeout=120.0)
        try:
            conn.request(
                method.upper(),
                target_path,
                body=body if body else None,
                headers=up_headers,
            )
            resp = conn.getresponse()
            resp_headers: dict[str, str] = {"Cache-Control": "no-store"}
            content_type = ""
            for hdr_name, hdr_val in resp.getheaders():
                lk = hdr_name.lower()
                if lk == "content-type":
                    content_type = hdr_val.lower()
                    resp_headers["Content-Type"] = hdr_val
                elif lk in {
                    "content-disposition",
                    "etag",
                    "vary",
                    "x-eva-input-receipt",
                    "x-accel-buffering",
                }:
                    resp_headers[hdr_name] = hdr_val
                elif lk == "set-cookie" and hdr_val.lstrip().startswith("eva_holostage_session="):
                    resp_headers["Set-Cookie"] = hdr_val
                elif lk == "location":
                    rewritten = hdr_val
                    for up_prefix in (
                        f"http://localhost:{up_port}/",
                        f"http://127.0.0.1:{up_port}/",
                    ):
                        if rewritten.startswith(up_prefix):
                            rewritten = "/" + rewritten[len(up_prefix) :].lstrip("/")
                            break
                    resp_headers["Location"] = rewritten

            is_stream = "application/x-ndjson" in content_type or "text/event-stream" in content_type
            if is_stream and method.upper() != "HEAD":

                def _iter_chunks() -> Iterator[bytes]:
                    try:
                        while True:
                            line = resp.readline(65_536)
                            if not line:
                                break
                            yield line
                    finally:
                        conn.close()

                return resp.status, resp_headers, _iter_chunks()

            raw_body = resp.read()
            conn.close()
            return resp.status, resp_headers, raw_body
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            conn.close()
            raise ApplianceSetupError(
                f"Eva dashboard op {self.core_base_url} is nog niet bereikbaar",
                status_code=502,
            ) from exc

    def proxy_dashboard_request(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        status, resp_headers, body_or_iter = self.open_dashboard_stream(
            method=method,
            path=path,
            query=query,
            headers=headers,
            body=body,
            owner_authenticated=True,
        )
        raw_bytes = (
            body_or_iter if isinstance(body_or_iter, bytes) else b"".join(body_or_iter)
        )
        return status, resp_headers, raw_bytes

    def export_portable_bundle(self) -> dict[str, Any]:
        """Return a secret-free JSON export of profile, hardware tier, character, and module flags."""
        state = self._load_state()
        character = self._load_character()
        safe_modules = {
            mod_id: {k: v for k, v in cfg.items() if "token" not in k and "key" not in k and "secret" not in k}
            for mod_id, cfg in state["modules"].items()
        }
        return {
            "contract": "EvaApplianceExport/v1",
            "exported_at": _utc_now_iso(),
            "profile": state["profile"],
            "hardware": state["hardware"],
            "character": {
                k: v
                for k, v in character.items()
                if k not in {"pack_version", "can_rollback"}
            },
            "soul_markdown": self.export_soul_markdown(),
            "modules": safe_modules,
        }

    def import_portable_bundle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Import a previously exported secret-free profile, character, and module configuration."""
        if payload.get("contract") != "EvaApplianceExport/v1":
            raise ApplianceSetupError("ongeldig profielbestand (verwacht EvaApplianceExport/v1)")
        prof = payload.get("profile")
        hw = payload.get("hardware")
        if isinstance(prof, Mapping):
            merged_prof = dict(prof)
            if isinstance(hw, Mapping) and "tier" in hw:
                merged_prof["tier"] = hw["tier"]
            self.save_profile_and_hardware(merged_prof)
        char = payload.get("character")
        if isinstance(char, Mapping):
            self.save_character(char)
        mods = payload.get("modules")
        known_mod_ids = {item.module_id for item in MODULE_CATALOG}
        if isinstance(mods, Mapping):
            for mod_id, mod_cfg in mods.items():
                if isinstance(mod_cfg, Mapping) and mod_id in known_mod_ids:
                    self.save_module(str(mod_id), mod_cfg)
        return {"ok": True, "snapshot": self.status_snapshot()}

    # ------------------------------------------------------------------
    # Immersive Awakening Stage, Reversible Preferences & Family Welcome
    # ------------------------------------------------------------------

    def _model_providers_dir(self) -> Path:
        return _ensure_private_dir(self.runtime_root / "work" / "model-providers")

    @staticmethod
    def _base_safety_rules(owner_name: str, family_members: list[str]) -> list[str]:
        fam_clean = [m.strip() for m in family_members if m.strip()]
        fam_suffix = f" en {', '.join(fam_clean)}." if fam_clean else "."
        return [
            f"Het kerngezin bestaat uitsluitend uit {owner_name} (gezinshoofd){fam_suffix}",
            (
                "Andere personen (buren, familie, bezoekers of uitgenodigde gasten) horen niet bij "
                "het kerngezin; deel nooit privégegevens, agenda's of gesprekken van het kerngezin met gasten."
            ),
            "Val het gezin niet lastig met losse 'wie is dit?'-vragen bij vage of voorbijlopende camerabeelden.",
        ]

    @staticmethod
    def _is_core_safety_rule(rule: str) -> bool:
        low = rule.strip().lower()
        return (
            low.startswith("het kerngezin bestaat uitsluitend uit")
            or low.startswith("andere personen (buren, familie, bezoekers")
            or low.startswith("val het gezin niet lastig met losse")
            or low.startswith("eigen omgeving:")
            or low.startswith("home assistant:")
            or low.startswith("koppelingen & kluis:")
            or low.startswith("zelfbouw & onderzoek:")
        )

    def _integration_hub(self) -> UniversalIntegrationHub:
        return UniversalIntegrationHub(self.runtime_root)

    def _probe_local_unifi_cached(self) -> dict[str, Any]:
        now_m = time.monotonic()
        cached = getattr(self, "_unifi_lan_cache", None)
        if isinstance(cached, tuple) and (now_m - cached[0]) < 30.0:
            return dict(cached[1])
        res = (
            {"reachable": False, "ip": "", "port": 443, "url": "https://192.168.1.1"}
            if self.dry_run_apply
            else detect_local_unifi_console()
        )
        self._unifi_lan_cache = (now_m, res)
        return dict(res)

    def _probe_local_ha_reachable(self) -> dict[str, Any]:
        """Quickly check if Home Assistant is reachable on the local network (cached 25s)."""
        now_m = time.monotonic()
        cached = getattr(self, "_ha_lan_cache", None)
        if isinstance(cached, tuple) and (now_m - cached[0]) < 25.0:
            return dict(cached[1])
        result: dict[str, Any] = {"reachable": False, "url": "http://homeassistant.local:8123", "ip": ""}
        if self.dry_run_apply:
            self._ha_lan_cache = (now_m, result)
            return dict(result)
        state = self._load_state()
        configured_url = str(state["modules"]["smart_home"].get("url") or "http://homeassistant.local:8123").strip()
        parsed = urlsplit(configured_url)
        host = parsed.hostname or "homeassistant.local"
        port = parsed.port or 8123
        try:
            ip = socket.gethostbyname(host)
            with socket.create_connection((ip, port), timeout=0.9):
                result = {"reachable": True, "url": configured_url, "ip": ip}
        except OSError:
            pass
        self._ha_lan_cache = (now_m, result)
        return dict(result)

    @staticmethod
    def _discover_ha_entities(url: str, token: str) -> dict[str, Any]:
        """Fetch /api/states from Home Assistant to build valid light/switch allowlist and summary."""
        fallback = ["switch.eva_appliance:turn_on", "switch.eva_appliance:turn_off"]
        parsed = urlsplit(url.rstrip("/"))
        conn_cls = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        base_path = parsed.path.rstrip("/")
        conn = conn_cls(parsed.hostname or "", port=parsed.port, timeout=4.5)
        try:
            conn.request(
                "GET",
                f"{base_path}/api/states",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            resp = conn.getresponse()
            raw = resp.read(4_194_304)
            if resp.status != 200:
                return {"lights": 0, "switches": 0, "sensors": 0, "total": 0, "allowlist": fallback}
            rows = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(rows, list):
                return {"lights": 0, "switches": 0, "sensors": 0, "total": 0, "allowlist": fallback}
            lights = 0
            switches = 0
            sensors = 0
            allow: list[str] = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entity_id") or "").strip()
                dom, dot, obj_id = eid.partition(".")
                if not dot or not obj_id:
                    continue
                if dom == "light":
                    lights += 1
                elif dom == "switch":
                    switches += 1
                elif dom in {"sensor", "binary_sensor"}:
                    sensors += 1
                if (
                    dom in {"light", "switch"}
                    and all(c in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in obj_id)
                    and len(allow) < 400
                ):
                    allow.append(f"{eid}:turn_on")
                    allow.append(f"{eid}:turn_off")
            return {
                "lights": lights,
                "switches": switches,
                "sensors": sensors,
                "total": len(rows),
                "allowlist": allow if allow else fallback,
            }
        except Exception:
            return {"lights": 0, "switches": 0, "sensors": 0, "total": 0, "allowlist": fallback}
        finally:
            conn.close()

    def _self_awareness_rules(self, state: Mapping[str, Any]) -> list[str]:
        hw = _detect_host_hardware()
        hostname = socket.gethostname()
        env_rule = (
            f"Eigen omgeving: Je draait lokaal op thuisnetwerk-host '{hostname}' "
            f"({hw['cpu_count']} CPU-cores, {hw['ram_gib']} GB RAM, lokale Postgres & Hindsight). "
            "Vraag het gezin nooit of je lokaal, op een externe server of in Docker draait."
        )[:270]
        sh = state["modules"]["smart_home"]
        ha_tok_ok = (self._secrets_dir() / "home-assistant-token").is_file()
        if sh.get("enabled") and ha_tok_ok:
            mode_lbl = "lezen en bedienen" if sh.get("allow_effects") else "alleen lezen"
            ha_rule = (
                f"Home Assistant: ACTIEF gekoppeld op {sh.get('url')} ({mode_lbl}). "
                "Gebruik direct je Home Assistant-tools om sensoren te lezen of lampen/schakelaars te bedienen."
            )[:270]
        else:
            probe = self._probe_local_ha_reachable()
            reach = f"al gevonden op {probe['url']}" if probe["reachable"] else "nog niet gekoppeld"
            ha_rule = (
                f"Home Assistant: {reach}, wacht op koppeling. Vraag NOOIT tokens in de chat en zeg NOOIT "
                "dat je niet kunt koppelen: wijs naar het beveiligde 'Koppelingen & Kluis'-venster rechts."
            )[:270]

        hub = self._integration_hub()
        svcs = hub.list_services()
        active_names = [s["title"] for s in svcs if s.get("has_credential")]
        svc_lbl = ", ".join(active_names[:6]) if active_names else "nog geen extra kluis-diensten"
        vault_rule = (
            f"Koppelingen & Kluis: Actief ({svc_lbl}). Voor UniFi Netwerk, UniFi Protect, agenda, e-mail "
            "of elk ander systeem: gebruik `discover_network_and_vault`, `use_vault_service` of open het "
            "beveiligde koppelvak met `request_owner_connection`."
        )[:270]

        caps = hub.list_capabilities()
        cap_lbl = f" ({len(caps)} eigen skills actief)" if caps else ""
        self_build_rule = (
            f"Zelfbouw & Onderzoek{cap_lbl}: Kun je iets nog niet of ontbreekt een integratie? Zeg NOOIT "
            "dat je dat niet kunt: zoek de API op (`search_public_info`) en schrijf, test en bewaar "
            "zelf de Python-vaardigheid via `build_and_run_capability` of `start_digital_work`."
        )[:270]
        return [env_rule, ha_rule, vault_rule, self_build_rule]

    def _sync_household_head_settings(
        self,
        *,
        extra_rules: list[str] | None = None,
        tone_override: str | None = None,
    ) -> dict[str, Any]:
        """Keep HouseholdHeadSettings (runtime live prompt & member roster) in lockstep with IdentityPack & profile."""
        state = self._load_state()
        character = self._load_character()
        mp_dir = self._model_providers_dir()

        owner_name = str(state["profile"].get("owner_name") or "Jaap").strip() or "Jaap"
        eva_name = str(character.get("display_name") or "Eva").strip() or "Eva"
        family = [
            str(m).strip()
            for m in (state["profile"].get("family_members") or [])
            if str(m).strip()
        ]
        temp_str = ", ".join(character.get("temperament") or ["warm", "attent"])
        voice_str = ", ".join(character.get("voice_qualities") or ["natuurlijk", "vlot"])
        humour_str = str(character.get("humour") or "Subtiel en gevat.").strip()
        proactivity_str = str(character.get("proactivity") or "balanced").strip()

        custom_lines = [
            re.sub(r"\s+", " ", line.strip(" -•\t\r"))[:260]
            for line in str(character.get("custom_rules") or "").splitlines()
            if line.strip(" -•\t\r")
        ]

        if os.name != "nt":
            from eva.application.household_head_settings import HouseholdHeadSettings

            hh = HouseholdHeadSettings(mp_dir)
            hh_snap = hh.status()
            existing_members = [dict(m) for m in (hh_snap.get("members") or []) if isinstance(m, dict)]

            # Update owner display_name if customized
            existing_owner = next(
                (m for m in existing_members if m.get("role") == "gezinshoofd"),
                None,
            )
            if existing_owner and existing_owner.get("display_name") != owner_name:
                with hh._locked():
                    raw_hh = hh._read_unlocked(datetime.now(UTC))
                    for m in raw_hh.get("members") or []:
                        if isinstance(m, dict) and m.get("role") == "gezinshoofd":
                            m["display_name"] = owner_name[:64]
                            m["updated_at"] = _utc_now_iso()
                    hh._write_unlocked(raw_hh)

            # Upsert family members without duplicating existing names (e.g. family-lola)
            for fam_name in family[:10]:
                if fam_name.lower() == owner_name.lower():
                    continue
                matched = next(
                    (
                        m
                        for m in hh.status().get("members", [])
                        if isinstance(m, dict)
                        and str(m.get("display_name") or "").strip().lower() == fam_name.lower()
                        and m.get("role") != "gezinshoofd"
                    ),
                    None,
                )
                try:
                    hh.update_member_or_invite(
                        action="upsert_member",
                        target=str(matched["member_id"]) if matched else "",
                        role="gezin",
                        display_name=fam_name[:64],
                        notes=str(matched.get("notes") or "Kerngezin") if matched else "Kerngezin",
                    )
                except ValueError:
                    pass

            refreshed_snap = hh.status()
            active_gezin_names = [
                str(m.get("display_name")).strip()
                for m in refreshed_snap.get("members", [])
                if isinstance(m, dict)
                and m.get("status") == "active"
                and m.get("role") == "gezin"
                and str(m.get("display_name") or "").strip()
            ]
            effective_family = family if family else active_gezin_names
            fam_clause = f" en {', '.join(effective_family)}" if effective_family else ""
            persona_summary = (
                f"{eva_name} ({temp_str}) — levende digitale huisgenoot van {owner_name}{fam_clause}. "
                f"Humor: {humour_str}. Initiatief: {proactivity_str}."
            )[:580]

            existing_tone = str(refreshed_snap.get("eva_profile", {}).get("tone_guidelines") or "").strip()
            base_tone = f"Praat natuurlijk en menselijk Nederlands ({voice_str}). {humour_str}"
            if tone_override:
                base_tone = f"{base_tone} {tone_override}"
            elif existing_tone and tone_override is None and len(existing_tone) <= 560:
                # Keep custom spoken guideline cues if previously added
                pass
            tone_guidelines = re.sub(r"\s+", " ", base_tone).strip()[:580]

            preserved_member_rules = [
                str(r).strip()
                for r in (refreshed_snap.get("eva_profile", {}).get("household_rules") or [])
                if str(r).strip().startswith("Voorkeur van ")
            ]
            merged_rules: list[str] = [
                *self._base_safety_rules(owner_name, effective_family),
                *self._self_awareness_rules(state),
            ]
            for candidate in [*custom_lines, *(extra_rules or []), *preserved_member_rules]:
                clean_r = re.sub(r"\s+", " ", str(candidate or "")).strip()[:275]
                if (
                    clean_r
                    and not self._is_core_safety_rule(clean_r)
                    and clean_r.lower() not in {x.lower() for x in merged_rules}
                ):
                    merged_rules.append(clean_r)
                if len(merged_rules) >= 24:
                    break

            return hh.update_eva_profile(
                display_name=eva_name[:40],
                persona_summary=persona_summary,
                tone_guidelines=tone_guidelines,
                household_rules=merged_rules[:24],
            )

        # Windows (nt) fallback without fcntl
        hh_file = mp_dir / "household-head-state.json"
        now_iso = _utc_now_iso()
        raw = _read_json_if_present(hh_file) or {
            "contract": "EvaHouseholdHeadState/v1",
            "revision": 1,
            "updated_at": now_iso,
            "telegram_bot_username": "",
            "eva_profile": {},
            "previous_eva_profile": None,
            "members": [
                {
                    "member_id": "owner-jaap",
                    "display_name": owner_name,
                    "role": "gezinshoofd",
                    "telegram_user_id": 6034485735,
                    "status": "active",
                    "notes": "Gezinshoofd & eigenaar",
                    "added_at": now_iso,
                    "updated_at": now_iso,
                },
                {
                    "member_id": "family-lola",
                    "display_name": "Lola",
                    "role": "gezin",
                    "telegram_user_id": 7803668978,
                    "status": "active",
                    "notes": "Kerngezin",
                    "added_at": now_iso,
                    "updated_at": now_iso,
                },
            ],
            "invites": [],
        }
        members = [dict(m) for m in (raw.get("members") or []) if isinstance(m, dict)]
        for m in members:
            if m.get("role") == "gezinshoofd":
                m["display_name"] = owner_name[:64]
                m["updated_at"] = now_iso
        for fam_name in family[:10]:
            if fam_name.lower() == owner_name.lower():
                continue
            existing_m = next(
                (
                    m
                    for m in members
                    if str(m.get("display_name") or "").lower() == fam_name.lower()
                ),
                None,
            )
            if existing_m is None:
                members.append(
                    {
                        "member_id": f"gezin-{secrets.token_hex(4)}",
                        "display_name": fam_name[:64],
                        "role": "gezin",
                        "telegram_user_id": None,
                        "status": "active",
                        "notes": "Kerngezin",
                        "added_at": now_iso,
                        "updated_at": now_iso,
                    }
                )
        raw["members"] = members
        eff_fam = family or [
            str(m["display_name"]) for m in members if m.get("role") == "gezin" and m.get("status") == "active"
        ]
        fam_clause = f" en {', '.join(eff_fam)}" if eff_fam else ""
        preserved_member_rules = [
            str(r).strip()
            for r in (raw.get("eva_profile", {}).get("household_rules") or [])
            if str(r).strip().startswith("Voorkeur van ")
        ]
        merged_rules = [
            *self._base_safety_rules(owner_name, eff_fam),
            *self._self_awareness_rules(state),
        ]
        for candidate in [*custom_lines, *(extra_rules or []), *preserved_member_rules]:
            clean_r = re.sub(r"\s+", " ", str(candidate or "")).strip()[:275]
            if (
                clean_r
                and not self._is_core_safety_rule(clean_r)
                and clean_r.lower() not in {x.lower() for x in merged_rules}
            ):
                merged_rules.append(clean_r)
            if len(merged_rules) >= 24:
                break
        tone_base = f"Praat natuurlijk en menselijk Nederlands ({voice_str}). {humour_str}"
        if tone_override:
            tone_base = f"{tone_base} {tone_override}"
        raw["eva_profile"] = {
            "display_name": eva_name[:40],
            "persona_summary": (
                f"{eva_name} ({temp_str}) — levende digitale huisgenoot van {owner_name}{fam_clause}. "
                f"Humor: {humour_str}. Initiatief: {proactivity_str}."
            )[:580],
            "tone_guidelines": re.sub(r"\s+", " ", tone_base).strip()[:580],
            "household_rules": merged_rules[:24],
        }
        raw["revision"] = int(raw.get("revision", 1)) + 1
        raw["updated_at"] = now_iso
        _write_json_private(hh_file, raw)
        return raw

    def _read_household_view(self) -> dict[str, Any]:
        mp_dir = self._model_providers_dir()
        if os.name != "nt":
            from eva.application.household_head_settings import HouseholdHeadSettings

            return HouseholdHeadSettings(mp_dir).status()
        hh_file = mp_dir / "household-head-state.json"
        if not hh_file.is_file():
            return self._sync_household_head_settings()
        return _read_json_if_present(hh_file) or self._sync_household_head_settings()

    def _living_undo_path(self) -> Path:
        return self.workspace / "living-undo.json"

    def _record_living_undo(self, label: str) -> str:
        state = self._load_state()
        char = self._load_character()
        hh_view = self._read_household_view()
        change_id = "chg_" + secrets.token_hex(6)
        entry: dict[str, Any] = {
            "change_id": change_id,
            "created_at": _utc_now_iso(),
            "label": str(label)[:140],
            "undone": False,
            "profile": {
                "installation_name": str(state["profile"].get("installation_name", "Eva thuis")),
                "owner_name": str(state["profile"].get("owner_name", "Jaap")),
                "family_members": [str(x) for x in (state["profile"].get("family_members") or [])],
                "language": str(state["profile"].get("language", "nl-NL")),
                "time_zone": str(state["profile"].get("time_zone", "Europe/Amsterdam")),
            },
            "awakening_completed": bool(state.get("awakening_completed", False)),
            "guest_mode_active": bool(state.get("guest_mode_active", False)),
            "character": {
                "preset_id": str(char.get("preset_id", "warm_huisgenoot")),
                "display_name": str(char.get("display_name", "Eva")),
                "pronouns": [str(x) for x in (char.get("pronouns") or ["zij", "haar"])],
                "language": str(char.get("language", "nl-NL")),
                "temperament": [str(x) for x in (char.get("temperament") or [])],
                "humour": str(char.get("humour", "")),
                "voice_qualities": [str(x) for x in (char.get("voice_qualities") or [])],
                "proactivity": str(char.get("proactivity", "balanced")),
                "custom_rules": str(char.get("custom_rules", "")),
            },
            "household_eva_profile": {
                "display_name": str(hh_view.get("eva_profile", {}).get("display_name", "Eva")),
                "persona_summary": str(hh_view.get("eva_profile", {}).get("persona_summary", "")),
                "tone_guidelines": str(hh_view.get("eva_profile", {}).get("tone_guidelines", "")),
                "household_rules": [
                    str(r) for r in (hh_view.get("eva_profile", {}).get("household_rules") or [])
                ],
            },
        }
        ledger = _read_json_if_present(self._living_undo_path()) or {"entries": []}
        entries = [dict(e) for e in (ledger.get("entries") or []) if isinstance(e, dict)]
        entries.insert(0, entry)
        _write_json_private(self._living_undo_path(), {"entries": entries[:20]})
        return change_id

    def undo_living_change(self, change_id: str) -> dict[str, Any]:
        clean_id = str(change_id or "").strip()
        if not clean_id:
            raise ApplianceSetupError("change_id ontbreekt")
        ledger = _read_json_if_present(self._living_undo_path()) or {"entries": []}
        entries = [dict(e) for e in (ledger.get("entries") or []) if isinstance(e, dict)]
        matched = next((e for e in entries if e.get("change_id") == clean_id), None)
        if matched is None:
            raise ApplianceSetupError("deze wijziging is niet meer beschikbaar om terug te draaien", status_code=404)
        if matched.get("undone"):
            raise ApplianceSetupError("deze wijziging was al ongedaan gemaakt")

        state = self._load_state()
        if isinstance(matched.get("profile"), dict):
            state["profile"].update(matched["profile"])
        state["awakening_completed"] = bool(matched.get("awakening_completed", state["awakening_completed"]))
        prev_guest = bool(matched.get("guest_mode_active", False))
        state["guest_mode_active"] = prev_guest
        self._save_state(state)

        if isinstance(matched.get("character"), dict):
            self.save_character(matched["character"])

        hh_prof = matched.get("household_eva_profile")
        if isinstance(hh_prof, dict) and hh_prof.get("display_name"):
            mp_dir = self._model_providers_dir()
            if os.name != "nt":
                from eva.application.household_head_settings import HouseholdHeadSettings

                HouseholdHeadSettings(mp_dir).update_eva_profile(
                    display_name=str(hh_prof["display_name"]),
                    persona_summary=str(hh_prof.get("persona_summary") or ""),
                    tone_guidelines=str(hh_prof.get("tone_guidelines") or ""),
                    household_rules=[str(r) for r in (hh_prof.get("household_rules") or [])],
                )
            else:
                hh_file = mp_dir / "household-head-state.json"
                raw_hh = _read_json_if_present(hh_file) or {}
                raw_hh["eva_profile"] = dict(hh_prof)
                _write_json_private(hh_file, raw_hh)

        self._apply_guest_mode_side_effects(enabled=prev_guest)
        matched["undone"] = True
        _write_json_private(self._living_undo_path(), {"entries": entries})
        return {
            "ok": True,
            "undone_change_id": clean_id,
            "label": matched.get("label", "Wijziging teruggedraaid"),
            "stage": self.living_stage_snapshot(),
        }

    def remove_household_rule(self, rule_index: int) -> dict[str, Any]:
        hh_view = self._read_household_view()
        all_rules = [str(r) for r in (hh_view.get("eva_profile", {}).get("household_rules") or [])]
        user_rules = [r for r in all_rules if not self._is_core_safety_rule(r)]
        if rule_index < 0 or rule_index >= len(user_rules):
            raise ApplianceSetupError("huisafspraak niet gevonden", status_code=404)
        removed = user_rules[rule_index]
        change_id = self._record_living_undo(f"Verwijderd: {removed[:60]}")
        remaining_user_rules = [r for i, r in enumerate(user_rules) if i != rule_index]

        char = self._load_character()
        char_custom = [r for r in remaining_user_rules if not r.startswith("Voorkeur van ")]
        self.save_character(
            {
                "preset_id": char.get("preset_id", "warm_huisgenoot"),
                "display_name": char.get("display_name", "Eva"),
                "proactivity": char.get("proactivity", "balanced"),
                "temperament": char.get("temperament", []),
                "humour": char.get("humour", ""),
                "voice_qualities": char.get("voice_qualities", []),
                "custom_rules": "\n".join(char_custom),
            }
        )
        # Also remove if it was a "Voorkeur van ..." rule in HouseholdHeadSettings
        mp_dir = self._model_providers_dir()
        core_rules = [r for r in all_rules if self._is_core_safety_rule(r)]
        if os.name != "nt":
            from eva.application.household_head_settings import HouseholdHeadSettings

            HouseholdHeadSettings(mp_dir).update_eva_profile(
                household_rules=[*core_rules, *remaining_user_rules][:24]
            )
        else:
            hh_file = mp_dir / "household-head-state.json"
            raw_hh = _read_json_if_present(hh_file) or {}
            if isinstance(raw_hh.get("eva_profile"), dict):
                raw_hh["eva_profile"]["household_rules"] = [*core_rules, *remaining_user_rules][:24]
                _write_json_private(hh_file, raw_hh)

        return {
            "ok": True,
            "removed": removed,
            "change_id": change_id,
            "stage": self.living_stage_snapshot(),
        }

    def _apply_guest_mode_side_effects(self, *, enabled: bool) -> None:
        mp_dir = self._model_providers_dir()
        eva_name = self._load_character().get("display_name", "Eva")
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            mgr.biometrics.set_manual_privacy_shield(locked=bool(enabled))
            if enabled:
                mgr.trigger_stage_animation(
                    effect="celebration",
                    label_nl="🥂 Gastvrije Modus actief — privégegevens afgeschermd",
                    privacy_level="shared",
                )
                mgr.summon_spotlight_card(
                    title="Welkom in huis! ✨",
                    body_nl=(
                        f"{eva_name} staat in Gastvrije Modus: privékaarten van het gezin zijn netjes "
                        "afgeschermd, maar ik denk graag gezellig mee voor de sfeer, muziek of een goed idee."
                    ),
                    items=[
                        "🛡️ Privéschild actief voor gezinsagenda en privéberichten",
                        "🥂 Vraag gerust iets gezelligs voor aan tafel!",
                    ],
                    theme="celebration",
                    privacy_level="shared",
                    ttl_minutes=180,
                )
            else:
                snap = mgr.build_stage_state(viewer_kind="owner")
                for card in (snap.get("cards", {}).get("spotlights") or []):
                    if isinstance(card, dict) and str(card.get("title") or "").startswith("Welkom in huis"):
                        try:
                            mgr.dismiss_spotlight_card(str(card["card_id"]), allow_private=True)
                        except Exception:
                            pass
                mgr.trigger_stage_animation(
                    effect="aurora_reveal",
                    label_nl="🏡 Privé Gezinsmodus hersteld",
                    privacy_level="private_household",
                )

    def set_guest_mode(self, *, enabled: bool) -> dict[str, Any]:
        change_id = self._record_living_undo(
            "Gastvrije Modus ingeschakeld" if enabled else "Gastvrije Modus uitgeschakeld"
        )
        state = self._load_state()
        state["guest_mode_active"] = bool(enabled)
        self._save_state(state)
        self._apply_guest_mode_side_effects(enabled=bool(enabled))
        return {
            "ok": True,
            "guest_mode_active": bool(enabled),
            "change_id": change_id,
            "stage": self.living_stage_snapshot(),
        }

    def scan_lan_devices(self) -> dict[str, Any]:
        mp_dir = self._model_providers_dir()
        devices: list[dict[str, Any]] = []
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            try:
                mgr.proprioception.run_lan_scan(max_subnets=1)
            except Exception:
                pass
            prop = mgr.proprioception.status()
            devices = [dict(d) for d in (prop.get("devices") or []) if isinstance(d, dict)]
        return {
            "ok": True,
            "devices": devices,
            "stage": self.living_stage_snapshot(),
        }

    def set_lan_device_consent(self, device_id: str, *, approved: bool) -> dict[str, Any]:
        clean_id = str(device_id or "").strip()
        if not clean_id:
            raise ApplianceSetupError("device_id ontbreekt")
        mp_dir = self._model_providers_dir()
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            try:
                mgr.proprioception.set_device_consent(
                    device_id=clean_id,
                    consent_status="approved" if approved else "ignored",
                    room_id="woonkamer",
                )
            except ValueError as exc:
                raise ApplianceSetupError(str(exc), status_code=404) from exc
        return {
            "ok": True,
            "device_id": clean_id,
            "consent_status": "approved" if approved else "ignored",
            "stage": self.living_stage_snapshot(),
        }

    def _enroll_member_pcm_if_valid(self, *, member_id: str, audio_pcm_b64: str) -> dict[str, Any] | None:
        raw_b64 = (audio_pcm_b64 or "").strip()
        if not raw_b64:
            return None
        try:
            pcm_bytes = base64.b64decode(raw_b64, validate=True)
        except Exception:
            return None
        if len(pcm_bytes) < 6400 or len(pcm_bytes) > 320_000 or len(pcm_bytes) % 2 != 0:
            return None
        mp_dir = self._model_providers_dir()
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            try:
                return mgr.biometrics.speaker.enroll_pcm(member_id=member_id, pcm_bytes=pcm_bytes)
            except Exception:
                return None
        vp_file = mp_dir / "biometric-voiceprints.json"
        vp = _read_json_if_present(vp_file) or {"contract": "EvaLocalVoiceprints/v1", "profiles": {}}
        profiles = dict(vp.get("profiles") or {})
        profiles[member_id] = {
            "member_id": member_id,
            "display_name": member_id,
            "updated_at": _utc_now_iso(),
            "samples": 1,
        }
        vp["profiles"] = profiles
        _write_json_private(vp_file, vp)
        return {"member_id": member_id, "samples": 1}

    def living_stage_snapshot(self, *, base_origin: str | None = None) -> dict[str, Any]:
        primary_origin, all_origins = self._preferred_lan_origin(base_origin)
        state = self._load_state()
        character = self._load_character()
        codex_status = self._codex_auth_status()
        mp_dir = self._model_providers_dir()
        provider_data = ModelProviderSettings(mp_dir).read()
        connections = [
            {
                "id": cid,
                "kind": row.get("kind"),
                "label": row.get("label"),
                "model": row.get("model"),
                "endpoint": row.get("endpoint"),
                "check_state": row.get("check_state"),
                "selected": provider_data.get("selected_id") == cid,
            }
            for cid, row in (provider_data.get("connections") or {}).items()
            if isinstance(row, dict) and row.get("enabled")
        ]
        ai_ready = bool(
            codex_status.get("logged_in")
            or any(c.get("check_state") == "ready" for c in connections)
        )
        runtime_health = self._runtime_health()
        hh_view = self._read_household_view()
        all_rules = [
            str(r) for r in (hh_view.get("eva_profile", {}).get("household_rules") or [])
        ]
        user_rules = [r for r in all_rules if not self._is_core_safety_rule(r)]

        shield_active = bool(state.get("guest_mode_active", False))
        shield_reason = "handmatig_gastmodus" if shield_active else ""
        enrolled_ids: set[str] = set()
        devices: list[dict[str, Any]] = []
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(mp_dir)
            sh_bool, sh_why = mgr.biometrics.is_privacy_shield_active()
            shield_active = bool(sh_bool or state.get("guest_mode_active", False))
            shield_reason = str(sh_why or shield_reason)
            sp_sum = mgr.biometrics.speaker.status_summary()
            for p in sp_sum.get("members") or []:
                if isinstance(p, dict) and p.get("enrolled") and p.get("member_id"):
                    enrolled_ids.add(str(p["member_id"]))
            prop = mgr.proprioception.status()
            devices = [dict(d) for d in (prop.get("devices") or []) if isinstance(d, dict)]
        else:
            vp = _read_json_if_present(mp_dir / "biometric-voiceprints.json") or {}
            enrolled_ids = set((vp.get("profiles") or {}).keys())

        members_out: list[dict[str, Any]] = []
        for m in hh_view.get("members") or []:
            if isinstance(m, dict) and m.get("status") == "active":
                mid = str(m.get("member_id") or "")
                members_out.append(
                    {
                        "member_id": mid,
                        "display_name": str(m.get("display_name") or ""),
                        "role": str(m.get("role") or "gezin"),
                        "notes": str(m.get("notes") or ""),
                        "voice_enrolled": mid in enrolled_ids,
                    }
                )

        sphere_themes = {
            "warm_huisgenoot": {"color": "#38bdf8", "accent": "#10b981", "orb": "Warm & Gezellig"},
            "nuchter_direct": {"color": "#22d3ee", "accent": "#818cf8", "orb": "Scherp & Droog"},
            "creatief_nieuwsgierig": {"color": "#c084fc", "accent": "#f472b6", "orb": "Speels & Creatief"},
            "stille_regelaar": {"color": "#34d399", "accent": "#64748b", "orb": "Rustig op Achtergrond"},
        }

        welcomes_raw = _read_json_if_present(self._member_welcomes_path()) or {"records": []}
        now_epoch = int(time.time())
        welcome_summaries = [
            {
                "welcome_id": str(rec.get("welcome_id") or ""),
                "member_name": str(rec.get("member_name") or ""),
                "role": str(rec.get("role") or "gezin"),
                "status": "expired"
                if int(rec.get("expires_epoch", 0)) <= now_epoch and rec.get("status") == "pending"
                else str(rec.get("status") or "pending"),
                "created_at": str(rec.get("created_at") or ""),
                "completed_at": rec.get("completed_at"),
            }
            for rec in (welcomes_raw.get("records") or [])
            if isinstance(rec, dict)
        ]

        hw_info = _detect_host_hardware()
        tg_poll = self.poll_telegram_magic_link()

        return {
            "contract": "EvaLivingStage/v1",
            "primary_origin": primary_origin,
            "lan_origins": all_origins,
            "wizard_completed": bool(state.get("wizard_completed", False)),
            "awakening_completed": bool(state.get("awakening_completed", False)),
            "awakening_turns": int(state.get("awakening_turns", 0)),
            "ai_ready": ai_ready,
            "core_ready": bool(runtime_health.get("core_ready", False)),
            "applied": bool(state.get("last_applied_at")),
            "hardware": {
                "tier": state["hardware"].get("tier", hw_info["recommended_tier"]),
                "monthly_spend_cents": int(state["hardware"].get("monthly_spend_cents", 0)),
                "detected": hw_info,
                "tiers": [
                    {
                        "tier_id": t.tier,
                        "label": t.label,
                        "summary": t.summary,
                    }
                    for t in HARDWARE_TIERS.values()
                ],
            },
            "channels": {
                "telegram": {
                    "token_saved": (self._secrets_dir() / "telegram-bot-token").is_file(),
                    "bot_username": state["modules"]["telegram"].get("bot_username", ""),
                    "contacts": tg_poll.get("contacts", []),
                },
                "smart_home": {
                    "enabled": bool(state["modules"]["smart_home"].get("enabled", False)),
                    "url": state["modules"]["smart_home"].get("url", "http://homeassistant.local:8123"),
                    "allow_effects": bool(state["modules"]["smart_home"].get("allow_effects", False)),
                    "secret_present": (self._secrets_dir() / "home-assistant-token").is_file(),
                    "lan_detected": self._probe_local_ha_reachable(),
                },
            },
            "integrations": {
                "services": self._integration_hub().list_services(),
                "capabilities": self._integration_hub().list_capabilities(),
                "pending_request": self._integration_hub().get_pending_connection(),
                "unifi_detected": self._probe_local_unifi_cached(),
            },
            "realtime_voice": self._realtime_voice_status(state, codex_status),
            "main_display": self._main_display_status(state, base_origin=base_origin),
            "eva": {
                "display_name": character.get("display_name", "Eva"),
                "preset_id": character.get("preset_id", "warm_huisgenoot"),
                "proactivity": character.get("proactivity", "balanced"),
                "temperament": list(character.get("temperament") or []),
                "humour": character.get("humour", ""),
                "voice_qualities": list(character.get("voice_qualities") or []),
                "persona_summary": str(hh_view.get("eva_profile", {}).get("persona_summary") or ""),
                "tone_guidelines": str(hh_view.get("eva_profile", {}).get("tone_guidelines") or ""),
                "presets": [
                    {
                        "preset_id": p.preset_id,
                        "label": p.label,
                        "tagline": p.tagline,
                        "proactivity": p.proactivity,
                        **sphere_themes.get(
                            p.preset_id,
                            {"color": "#38bdf8", "accent": "#10b981", "orb": p.label},
                        ),
                    }
                    for p in CHARACTER_PRESETS
                ],
            },
            "household": {
                "owner_name": state["profile"].get("owner_name", "Jaap"),
                "installation_name": state["profile"].get("installation_name", "Eva thuis"),
                "family_members": list(state["profile"].get("family_members") or []),
                "members": members_out,
                "custom_rules": user_rules,
                "all_rules": all_rules,
                "guest_mode_active": bool(state.get("guest_mode_active", False) or shield_active),
                "privacy_shield_active": shield_active,
                "privacy_shield_reason": shield_reason,
            },
            "discovered_devices": devices[:24],
            "member_welcomes": welcome_summaries[:16],
            "device_grants": self._holostage_grants_list(),
            "model": {
                **state["model"],
                "codex": codex_status,
                "api_presets": [
                    {
                        "preset_id": p["preset_id"],
                        "kind": p["kind"],
                        "label": p["label"],
                        "default_model": p["default_model"],
                        "default_endpoint": p["default_endpoint"],
                        "requires_api_key": p["requires_api_key"],
                        "hint_nl": p["hint_nl"],
                    }
                    for p in MODEL_PROVIDER_PRESETS
                ],
                "configured_api_connections": connections,
            },
        }

    def _try_core_conversation_turn(self, prompt_text: str) -> str | None:
        try:
            res = self.test_live_contact({"text": prompt_text[:980]})
            reply = str(res.get("reply_text") or "").strip()
            return reply or None
        except Exception:
            return None

    def _observe_speaker_pcm_if_valid(self, audio_pcm_b64: str) -> dict[str, Any] | None:
        raw_b64 = (audio_pcm_b64 or "").strip()
        if not raw_b64:
            return None
        try:
            pcm_bytes = base64.b64decode(raw_b64, validate=True)
        except Exception:
            return None
        if len(pcm_bytes) < 6400 or len(pcm_bytes) > 320_000 or len(pcm_bytes) % 2 != 0:
            return None
        if os.name != "nt":
            from eva.application.holostage import HoloStageManager

            mgr = HoloStageManager(self._model_providers_dir())
            try:
                match = mgr.biometrics.observe_voice_pcm(pcm_bytes)
                sim_ppm = int(match.get("similarity_ppm", 0))
                return {
                    **match,
                    "similarity_pct": max(0, min(100, round(sim_ppm / 10_000))),
                }
            except Exception:
                return None
        return {"status": "unconfigured", "similarity_pct": 0}

    def enroll_living_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Explicitly enroll 16 kHz mono s16le PCM voiceprint for an active household member."""
        audio_pcm_b64 = str(payload.get("audio_pcm_b64") or "").strip()
        if not audio_pcm_b64:
            raise ApplianceSetupError("geen spraakopname ontvangen")
        hh_view = self._read_household_view()
        req_member_id = str(payload.get("member_id") or "").strip()
        if not req_member_id:
            owner_m = next(
                (
                    m
                    for m in (hh_view.get("members") or [])
                    if isinstance(m, dict) and m.get("role") == "gezinshoofd"
                ),
                {"member_id": "owner-jaap"},
            )
            req_member_id = str(owner_m.get("member_id") or "owner-jaap")
        res = self._enroll_member_pcm_if_valid(member_id=req_member_id, audio_pcm_b64=audio_pcm_b64)
        if res is None:
            raise ApplianceSetupError(
                "stemopname was te kort of te stil — spreek ongeveer 3 seconden rustig in"
            )
        return {
            "ok": True,
            "enrollment": res,
            "stage": self.living_stage_snapshot(),
        }

    def check_living_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Test live speaker recognition against enrolled household voiceprints without mutating profiles."""
        audio_pcm_b64 = str(payload.get("audio_pcm_b64") or "").strip()
        if not audio_pcm_b64:
            raise ApplianceSetupError("geen spraakopname ontvangen")
        match = self._observe_speaker_pcm_if_valid(audio_pcm_b64)
        if match is None:
            raise ApplianceSetupError("stemopname was te kort voor herkenning (spreek ~2-3 sec)")
        return {
            "ok": True,
            "match": match,
            "stage": self.living_stage_snapshot(),
        }

    def set_onboarding_wizard_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        completed = bool(payload.get("completed", True))
        state = self._load_state()
        state["wizard_completed"] = completed
        if completed:
            state["awakening_completed"] = True
        self._save_state(state)
        self._sync_household_head_settings()
        snap = self.living_stage_snapshot()
        if completed and snap["ai_ready"] and not self.dry_run_apply and not snap["core_ready"]:
            try:
                self.apply_installation()
                snap = self.living_stage_snapshot()
            except Exception:
                pass
        return {
            "ok": True,
            "wizard_completed": completed,
            "stage": snap,
        }

    def connect_smart_home(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate Home Assistant token, discover entities, write runtime.env, and live-restart Living Core."""
        url = str(payload.get("url") or "http://homeassistant.local:8123").strip().rstrip("/")
        token = str(payload.get("token") or "").strip()
        allow_effects = bool(payload.get("allow_effects", True))
        skip_probe = bool(payload.get("skip_probe", False))

        self.save_module(
            "smart_home",
            {
                "enabled": True,
                "url": url,
                "token": token,
                "allow_effects": allow_effects,
            },
        )
        tok_path = self._secrets_dir() / "home-assistant-token"
        if not tok_path.is_file():
            raise ApplianceSetupError("plak eerst je Home Assistant Long-Lived Access Token")
        eff_token = tok_path.read_text(encoding="utf-8").strip()

        if not skip_probe:
            self.test_module("smart_home", {})

        disc = (
            {"lights": 0, "switches": 0, "sensors": 0, "total": 0}
            if (self.dry_run_apply or skip_probe)
            else self._discover_ha_entities(url, eff_token)
        )
        self.apply_installation()

        # Wait briefly for eva2-local-core.service to return ready=True after restart
        if not self.dry_run_apply:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if self._runtime_health().get("core_ready"):
                    break
                time.sleep(0.35)

        detail = (
            f"Home Assistant gekoppeld op {url}! "
            f"Gevonden: {disc['lights']} lampen, {disc['switches']} schakelaars en {disc['sensors']} sensoren."
        )
        return {
            "ok": True,
            "url": url,
            "allow_effects": allow_effects,
            "discovered": {
                "lights": disc["lights"],
                "switches": disc["switches"],
                "sensors": disc["sensors"],
                "total": disc["total"],
            },
            "detail": detail,
            "stage": self.living_stage_snapshot(),
        }

    def connect_universal_service(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Store credentials in AES-256-GCM PreLlmCredentialVault, live-probe, and update Eva's awareness."""
        hub = self._integration_hub()
        service_kind = str(payload.get("service_kind") or "rest_api")
        base_url = str(payload.get("base_url") or "")
        auth_mode = str(payload.get("auth_mode") or "api_key")
        username = str(payload.get("username") or "")
        secret = str(payload.get("secret") or "")
        verify_tls = bool(payload.get("verify_tls", False))
        run_probe = not (self.dry_run_apply or bool(payload.get("skip_probe", False)))
        try:
            res = hub.save_and_verify_service(
                service_id=str(payload.get("service_id") or ""),
                title=str(payload.get("title") or ""),
                service_kind=service_kind,
                base_url=base_url,
                auth_mode=auth_mode,
                username=username,
                secret=secret,
                smtp_url=str(payload.get("smtp_url") or ""),
                header_name=str(payload.get("header_name") or ""),
                verify_tls=verify_tls,
                run_probe=run_probe,
            )
        except (ValueError, PermissionError, RuntimeError) as exc:
            raise ApplianceSetupError(str(exc)) from exc

        detail = str(res["summary_nl"])
        if bool(payload.get("also_connect_unifi_pair", False)) and service_kind in {
            "unifi_network",
            "unifi_protect",
        }:
            sibling_kind = "unifi_protect" if service_kind == "unifi_network" else "unifi_network"
            sibling_title = (
                "UniFi Protect Camera's & Deurbel"
                if sibling_kind == "unifi_protect"
                else "UniFi Netwerk & Wi-Fi"
            )
            try:
                sib_res = hub.save_and_verify_service(
                    service_id=sibling_kind,
                    title=sibling_title,
                    service_kind=sibling_kind,
                    base_url=base_url,
                    auth_mode=auth_mode,
                    username=username,
                    secret=secret,
                    verify_tls=verify_tls,
                    run_probe=run_probe,
                )
                detail = f"{detail} Ook direct geactiveerd: {sib_res['summary_nl']}"
            except Exception:
                pass

        hub.clear_pending_connection()
        self._sync_household_head_settings()
        return {
            "ok": True,
            "service_id": res["service_id"],
            "detail": detail,
            "stage": self.living_stage_snapshot(),
        }

    def remove_universal_service(self, service_id: str) -> dict[str, Any]:
        res = self._integration_hub().remove_service(service_id)
        self._sync_household_head_settings()
        return {**res, "stage": self.living_stage_snapshot()}

    def delete_custom_capability(self, capability_id: str) -> dict[str, Any]:
        res = self._integration_hub().handle_capability_tool(
            {
                "action": "delete",
                "capability_id": capability_id,
                "title": "",
                "description": "",
                "service_id": "",
                "python_code": "",
                "params_json": "{}",
            }
        )
        self._sync_household_head_settings()
        return {**res, "stage": self.living_stage_snapshot()}

    def handle_living_turn(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        """Process a natural spoken or typed living turn, apply immediate reversible preferences, and reply."""
        raw_text = str(payload.get("text") or "").strip()
        clicked_preset = str(payload.get("preset_id") or "").strip()
        audio_pcm_b64 = str(payload.get("audio_pcm_b64") or "").strip()
        complete_awakening = bool(payload.get("complete_awakening", False))
        explicit_enroll = bool(payload.get("enroll_voice", False))

        if len(raw_text) > 900:
            raise ApplianceSetupError("bericht mag maximaal 900 tekens bevatten")
        if not raw_text and not clicked_preset and not complete_awakening:
            raise ApplianceSetupError("zeg of typ iets tegen Eva, of kies een karakterbol")

        # 0. Pre-LLM AES-256-GCM Vault interception (/kluis <id> <geheim> NEVER touches LLM or logs!)
        if raw_text and re.match(r"^/(?:kluis|wachtwoord)(?:\s|$)", raw_text.strip(), flags=re.IGNORECASE):
            parts_kl = raw_text.strip().split(maxsplit=2)
            if len(parts_kl) < 3:
                return {
                    "ok": True,
                    "reply_text": (
                        "Gebruik het beveiligde Koppelingen & Kluis-venster rechts op je scherm, "
                        "of typ `/kluis <dienst-id> <wachtwoord-of-sleutel>`."
                    ),
                    "applied_changes": [],
                    "conjure_panel": "integrations",
                    "voice_enrolled": False,
                    "speaker_match": None,
                    "stage": self.living_stage_snapshot(base_origin=base_origin),
                }
            dev_ref = re.sub(r"[^a-z0-9_-]+", "_", parts_kl[1].strip().lower()).strip("_-")
            secret_val = parts_kl[2]
            hub_kl = self._integration_hub()
            pend_kl = hub_kl.get_pending_connection()
            try:
                if pend_kl and pend_kl.get("service_id") == dev_ref and pend_kl.get("default_url"):
                    user_kl, sec_kl = ("", secret_val)
                    if ":" in secret_val and pend_kl.get("auth_mode") in {"basic_auth", "imap_password"}:
                        user_kl, sec_kl = secret_val.split(":", 1)
                    hub_kl.save_and_verify_service(
                        service_id=dev_ref,
                        title=str(pend_kl.get("title") or dev_ref),
                        service_kind=str(pend_kl.get("service_kind") or "rest_api"),
                        base_url=str(pend_kl.get("default_url") or ""),
                        auth_mode=str(pend_kl.get("auth_mode") or "api_key"),
                        username=user_kl,
                        secret=sec_kl,
                        run_probe=not self.dry_run_apply,
                    )
                else:
                    hub_kl.vault.store_secret(device_id=dev_ref, secret_value=secret_val)
                    hub_kl.clear_pending_connection(dev_ref)
            except Exception as exc:
                raise ApplianceSetupError(f"Kon geheim niet in kluis opslaan: {exc}") from exc
            self._sync_household_head_settings()
            return {
                "ok": True,
                "reply_text": (
                    f"Veilig AES-256 versleuteld opgeslagen in mijn kluis voor '{dev_ref}' — "
                    "volledig buiten het taalmodel en het gesprekslogboek om!"
                ),
                "applied_changes": [
                    {
                        "change_id": "",
                        "kind": "vault_secret",
                        "title": "🔐 Versleuteld opgeslagen in Kluis",
                        "summary": f"Geheime sleutel gekoppeld aan '{dev_ref}'",
                        "undoable": False,
                    }
                ],
                "conjure_panel": None,
                "voice_enrolled": False,
                "speaker_match": None,
                "stage": self.living_stage_snapshot(base_origin=base_origin),
            }

        state = self._load_state()
        char = self._load_character()
        pre_snap = self.living_stage_snapshot(base_origin=base_origin)
        owner_member = next(
            (
                m
                for m in (pre_snap["household"].get("members") or [])
                if isinstance(m, dict) and m.get("role") == "gezinshoofd"
            ),
            {"member_id": "owner-jaap", "voice_enrolled": False},
        )
        owner_id = str(owner_member.get("member_id") or "owner-jaap")
        owner_already_enrolled = bool(owner_member.get("voice_enrolled", False))

        awk_preview = (
            parse_awakening_utterance(
                raw_text,
                current_owner=str(state["profile"].get("owner_name", "Jaap")),
                current_eva_name=str(char.get("display_name", "Eva")),
            )
            if raw_text
            else {"owner_name": None}
        )

        voice_res: dict[str, Any] | None = None
        speaker_match: dict[str, Any] | None = None
        if audio_pcm_b64:
            if explicit_enroll or (not owner_already_enrolled and awk_preview.get("owner_name")):
                voice_res = self._enroll_member_pcm_if_valid(
                    member_id=owner_id,
                    audio_pcm_b64=audio_pcm_b64,
                )
            else:
                speaker_match = self._observe_speaker_pcm_if_valid(audio_pcm_b64)

        applied_changes: list[dict[str, Any]] = []
        conjure_panel: str | None = None
        conjure_preset: str | None = None

        # 1. Instant character sphere tasting (Scene 3 or anytime)
        valid_presets = {p.preset_id: p for p in CHARACTER_PRESETS}
        if clicked_preset and clicked_preset in valid_presets:
            preset_obj = valid_presets[clicked_preset]
            chg_id = self._record_living_undo(f"Karakter: {preset_obj.label}")
            self.save_character(
                {
                    "preset_id": clicked_preset,
                    "display_name": char.get("display_name", "Eva"),
                    "custom_rules": char.get("custom_rules", ""),
                }
            )
            char = self._load_character()
            applied_changes.append(
                {
                    "change_id": chg_id,
                    "kind": "character",
                    "title": f"Karakter gekozen · {preset_obj.label}",
                    "summary": preset_obj.tagline,
                    "undoable": True,
                }
            )
            if os.name != "nt":
                from eva.application.holostage import HoloStageManager

                HoloStageManager(self._model_providers_dir()).trigger_stage_animation(
                    effect="aurora_reveal",
                    label_nl=f"✨ Karakter: {preset_obj.label}",
                    privacy_level="private_household",
                )
            if not raw_text:
                owner_nm = state["profile"].get("owner_name", "Jaap")
                fam_list = state["profile"].get("family_members") or []
                fam_bit = f" en {', '.join(fam_list)}" if fam_list else ""
                eva_nm = char.get("display_name", "Eva")
                samples = {
                    "warm_huisgenoot": (
                        f"Zo klink ik als warme huisgenoot, {owner_nm}! Ik let op de gezelligheid voor jou{fam_bit}, "
                        "denk proactief mee en houd het lekker huiselijk."
                    ),
                    "nuchter_direct": (
                        f"Helder, {owner_nm}. Geen omhaal, droge humor waar het past, en direct tot de kern. "
                        f"Zeg maar wat we vandaag regelen."
                    ),
                    "creatief_nieuwsgierig": (
                        f"Oeh, leuk {owner_nm}! Met dit karakter daag ik jullie af en toe speels uit en kom ik met "
                        f"verrassende ideeën voor in huis{fam_bit}."
                    ),
                    "stille_regelaar": (
                        f"Begrepen, {owner_nm}. Ik regel alles stil en betrouwbaar op de achtergrond en trek alleen "
                        "aan de bel als het echt belangrijk is."
                    ),
                }
                return {
                    "ok": True,
                    "reply_text": samples.get(clicked_preset, f"Ik ben {eva_nm}, klaar voor jullie!"),
                    "applied_changes": applied_changes,
                    "conjure_panel": None,
                    "voice_enrolled": voice_res is not None,
                    "stage": self.living_stage_snapshot(base_origin=base_origin),
                }

        # 2. Natural Dutch onboarding & living intent detection
        if raw_text:
            awk = parse_awakening_utterance(
                raw_text,
                current_owner=str(state["profile"].get("owner_name", "Jaap")),
                current_eva_name=str(char.get("display_name", "Eva")),
            )
            intent = detect_living_intent(raw_text)

            if awk["owner_name"] or awk["family_members"] or awk["eva_name"]:
                chg_id = self._record_living_undo("Kennismaking & gezin bijgewerkt")
                new_owner = awk["owner_name"] or state["profile"].get("owner_name", "Jaap")
                merged_fam = list(state["profile"].get("family_members") or [])
                for fm in awk["family_members"]:
                    if fm.lower() not in {x.lower() for x in merged_fam} and fm.lower() != new_owner.lower():
                        merged_fam.append(fm)
                self.save_profile_and_hardware(
                    {
                        "owner_name": new_owner,
                        "installation_name": state["profile"].get("installation_name", f"Eva bij {new_owner}"),
                        "family_members": merged_fam,
                        "language": state["profile"].get("language", "nl-NL"),
                        "time_zone": state["profile"].get("time_zone", "Europe/Amsterdam"),
                        "tier": state["hardware"]["tier"],
                    }
                )
                if awk["eva_name"] or (awk["preset_hint"] and intent["kind"] != "tone_preference"):
                    self.save_character(
                        {
                            "preset_id": awk["preset_hint"] or char.get("preset_id", "warm_huisgenoot"),
                            "display_name": awk["eva_name"] or char.get("display_name", "Eva"),
                            "custom_rules": char.get("custom_rules", ""),
                        }
                    )
                    char = self._load_character()
                state = self._load_state()
                fam_desc = f" · Gezin: {', '.join(merged_fam)}" if merged_fam else ""
                eva_desc = f" · Naam: {char.get('display_name', 'Eva')}" if awk["eva_name"] else ""
                applied_changes.append(
                    {
                        "change_id": chg_id,
                        "kind": "household_intro",
                        "title": "Ik heb je gehoord · Gezin & Naam",
                        "summary": f"Eigenaar: {new_owner}{fam_desc}{eva_desc}",
                        "undoable": True,
                    }
                )

            if intent["kind"] == "guest_mode_on":
                gm = self.set_guest_mode(enabled=True)
                applied_changes.append(
                    {
                        "change_id": gm["change_id"],
                        "kind": "guest_mode",
                        "title": intent["title"],
                        "summary": intent["summary"],
                        "undoable": True,
                    }
                )
            elif intent["kind"] == "guest_mode_off":
                gm = self.set_guest_mode(enabled=False)
                applied_changes.append(
                    {
                        "change_id": gm["change_id"],
                        "kind": "guest_mode",
                        "title": intent["title"],
                        "summary": intent["summary"],
                        "undoable": True,
                    }
                )
            elif intent["kind"] == "household_rule":
                rule_txt = str(intent["rule_text"])
                chg_id = self._record_living_undo(f"Huisafspraak: {rule_txt[:60]}")
                char = self._load_character()
                existing_custom = [
                    line.strip()
                    for line in str(char.get("custom_rules") or "").splitlines()
                    if line.strip()
                ]
                if rule_txt.lower() not in {x.lower() for x in existing_custom}:
                    existing_custom.append(rule_txt)
                self.save_character(
                    {
                        "preset_id": char.get("preset_id", "warm_huisgenoot"),
                        "display_name": char.get("display_name", "Eva"),
                        "proactivity": char.get("proactivity", "balanced"),
                        "temperament": char.get("temperament", []),
                        "humour": char.get("humour", ""),
                        "voice_qualities": char.get("voice_qualities", []),
                        "custom_rules": "\n".join(existing_custom[:20]),
                    }
                )
                if os.name != "nt":
                    from eva.application.holostage import HoloStageManager

                    HoloStageManager(self._model_providers_dir()).summon_spotlight_card(
                        title="Huisafspraak vastgelegd ✨",
                        body_nl=rule_txt,
                        items=["Direct actief in Eva's gedrag"],
                        theme="aurora",
                        privacy_level="private_household",
                        ttl_minutes=45,
                    )
                applied_changes.append(
                    {
                        "change_id": chg_id,
                        "kind": "household_rule",
                        "title": "Ik heb je gehoord · Huisafspraak",
                        "summary": rule_txt,
                        "undoable": True,
                    }
                )
            elif intent["kind"] == "tone_preference":
                chg_id = self._record_living_undo(f"Spreekstijl: {intent['summary'][:60]}")
                char = self._load_character()
                new_preset = intent.get("preset_hint") or char.get("preset_id", "warm_huisgenoot")
                self.save_character(
                    {
                        "preset_id": new_preset,
                        "display_name": char.get("display_name", "Eva"),
                        "custom_rules": char.get("custom_rules", ""),
                    }
                )
                self._sync_household_head_settings(tone_override=str(intent["tone_guideline"]))
                applied_changes.append(
                    {
                        "change_id": chg_id,
                        "kind": "tone_preference",
                        "title": "Ik heb je gehoord · Spreekstijl",
                        "summary": str(intent["summary"]),
                        "undoable": True,
                    }
                )
            elif intent["kind"] == "conjure_card":
                conjure_panel = str(intent["card"])

            # Detect Smart Home / Universal Integrations (UniFi, Protect, Calendar, Email, etc.) / Wizard
            low_txt = raw_text.lower()
            hub_turn = self._integration_hub()
            existing_sids = {s["service_id"] for s in hub_turn.list_services() if s.get("has_credential")}
            existing_kinds = {s["service_kind"] for s in hub_turn.list_services() if s.get("has_credential")}
            wants_connect = any(
                w in low_txt
                for w in (
                    "koppel",
                    "verbind",
                    "aansluit",
                    "instel",
                    "inlog",
                    "token",
                    "sleutel",
                    "toegang",
                    "activeer",
                    "activeren",
                )
            )
            if conjure_panel is None:
                if any(
                    kw in low_txt
                    for kw in (
                        "long living token",
                        "long-lived token",
                        "slim huis koppelen",
                        "lampen koppelen",
                    )
                ) or (
                    wants_connect and ("home assistant" in low_txt or "homeassistant" in low_txt)
                ):
                    sh_now = state["modules"]["smart_home"]
                    if not (sh_now.get("enabled") and (self._secrets_dir() / "home-assistant-token").is_file()):
                        hub_turn.clear_pending_connection()
                        conjure_panel = "smart_home"
                        conjure_preset = "home_assistant"
                elif (
                    wants_connect
                    and "protect" in low_txt
                    and any(w in low_txt for w in ("netwerk", "network", "wifi", "wi-fi"))
                ):
                    if "unifi_network" not in existing_kinds or "unifi_protect" not in existing_kinds:
                        u_det = self._probe_local_unifi_cached()
                        hub_turn.record_connection_request(
                            {
                                "service_id": "unifi_network",
                                "title": "UniFi Netwerk & UniFi Protect",
                                "service_kind": "unifi_network",
                                "default_url": u_det["url"],
                                "auth_mode": "api_key",
                                "instructions_nl": (
                                    "Plak je UniFi OS Integration API-Key (Control Plane → Integrations) en laat het vinkje "
                                    "aan staan: dan worden zowel UniFi Netwerk als UniFi Protect in één klik versleuteld gekoppeld!"
                                ),
                            }
                        )
                        conjure_panel = "integrations"
                        conjure_preset = "unifi_network"
                elif wants_connect and ("unifi protect" in low_txt or ("protect" in low_txt and "camera" in low_txt)):
                    if "unifi_protect" not in existing_kinds and "unifi_protect" not in existing_sids:
                        u_det = self._probe_local_unifi_cached()
                        hub_turn.record_connection_request(
                            {
                                "service_id": "unifi_protect",
                                "title": "UniFi Protect Camera's & Deurbel",
                                "service_kind": "unifi_protect",
                                "default_url": u_det["url"],
                                "auth_mode": "api_key",
                                "instructions_nl": "Maak in UniFi OS → Instellingen → Control Plane → Integrations een API-Key aan (of kies Lokaal account).",
                            }
                        )
                        conjure_panel = "integrations"
                        conjure_preset = "unifi_protect"
                elif wants_connect and "unifi" in low_txt:
                    if "unifi_network" not in existing_kinds and "unifi_network" not in existing_sids:
                        u_det = self._probe_local_unifi_cached()
                        hub_turn.record_connection_request(
                            {
                                "service_id": "unifi_network",
                                "title": "UniFi Netwerk & Wi-Fi Aanwezigheid",
                                "service_kind": "unifi_network",
                                "default_url": u_det["url"],
                                "auth_mode": "api_key",
                                "instructions_nl": "Plak je UniFi OS Integration API-Key (Control Plane → Integrations) of gebruik een lokaal alleen-lezen UniFi-account.",
                            }
                        )
                        conjure_panel = "integrations"
                        conjure_preset = "unifi_network"
                elif wants_connect and any(
                    kw in low_txt for kw in ("agenda", "caldav", "kalender", "icloud agenda", "google calendar")
                ):
                    if "calendar" not in existing_kinds:
                        hub_turn.record_connection_request(
                            {
                                "service_id": "gezins_agenda",
                                "title": "Agenda (CalDAV / iCloud / Google ICS)",
                                "service_kind": "calendar",
                                "default_url": "https://caldav.icloud.com",
                                "auth_mode": "basic_auth",
                                "instructions_nl": "Vul je CalDAV-adres + app-specifiek wachtwoord in (of kies ICS-link voor een geheime Google/Outlook agenda-URL).",
                            }
                        )
                        conjure_panel = "integrations"
                        conjure_preset = "calendar"
                elif wants_connect and any(kw in low_txt for kw in ("e-mail", "email", "mailbox", "imap", "gmail")):
                    if "email" not in existing_kinds:
                        hub_turn.record_connection_request(
                            {
                                "service_id": "eigen_email",
                                "title": "E-mail (IMAP & SMTP)",
                                "service_kind": "email",
                                "default_url": "imaps://imap.gmail.com:993",
                                "auth_mode": "imap_password",
                                "instructions_nl": "Vul je IMAP-server, e-mailadres en een App-wachtwoord in. Alles blijft AES-256 versleuteld in Eva's lokale kluis.",
                            }
                        )
                        conjure_panel = "integrations"
                        conjure_preset = "email"
                elif any(kw in low_txt for kw in ("telegram koppelen", "botfather")):
                    conjure_panel = "telegram"
                elif any(kw in low_txt for kw in ("open kluis", "koppelingen openen", "nieuwe koppeling")):
                    conjure_panel = "integrations"
                elif any(kw in low_txt for kw in ("open wizard", "start wizard", "kennismaking starten", "onboarding")):
                    conjure_panel = "wizard"

        # 3. Track awakening progress & auto-activate runtime when AI is ready and unprovisioned
        state = self._load_state()
        state["awakening_turns"] = int(state.get("awakening_turns", 0)) + 1
        if complete_awakening or state["awakening_turns"] >= 1:
            state["awakening_completed"] = True
        self._save_state(state)

        snap_now = self.living_stage_snapshot(base_origin=base_origin)
        if (
            snap_now["ai_ready"]
            and not snap_now["core_ready"]
            and not (self.runtime_root / "runtime.env").is_file()
            and not self.dry_run_apply
        ):
            try:
                self.apply_installation()
                snap_now = self.living_stage_snapshot(base_origin=base_origin)
            except Exception:
                pass

        # 4. Generate natural Dutch reply via Living Core (with instant response for pure local preference toggles)
        owner_nm = snap_now["household"]["owner_name"]
        speaker_nm = (
            str(speaker_match["display_name"])
            if isinstance(speaker_match, dict)
            and speaker_match.get("status") == "household_voice_candidate"
            and speaker_match.get("display_name")
            else owner_nm
        )
        unknown_voice = (
            isinstance(speaker_match, dict) and speaker_match.get("status") == "unknown_voice"
        )
        eva_nm = snap_now["eva"]["display_name"]
        fam_members = snap_now["household"]["family_members"]
        reply_text: str | None = None
        pure_local_preference = bool(
            applied_changes
            and conjure_panel is None
            and "?" not in raw_text
            and all(
                c["kind"] in {"guest_mode", "household_rule", "tone_preference", "household_intro"}
                for c in applied_changes
            )
        )

        if raw_text and snap_now["core_ready"] and not pure_local_preference:
            pend_before = self._integration_hub().get_pending_connection()
            prev_req_at = (pend_before or {}).get("requested_at")
            if unknown_voice:
                core_prompt = (
                    "Er spreekt een onbekende bezoeker in de kamer (privéschild actief; deel geen gezinsagenda of privézaken): "
                    f"{raw_text}"
                )
            else:
                core_prompt = raw_text
            reply_text = self._try_core_conversation_turn(core_prompt)
            # If Eva's model invoked `request_owner_connection` during this turn, auto-open the exact tab on the 3D stage!
            pend_after = self._integration_hub().get_pending_connection()
            if pend_after and pend_after.get("requested_at") != prev_req_at:
                if conjure_panel is None:
                    conjure_panel = "integrations"
                conjure_preset = str(pend_after.get("service_kind") or "auto")

        if not reply_text:
            if applied_changes:
                top = applied_changes[-1]
                if top["kind"] == "guest_mode":
                    if snap_now["household"]["guest_mode_active"]:
                        reply_text = (
                            f"Gezellig, {owner_nm}! Ik heb het privéschild direct aangezet zodat gezinszaken netjes "
                            "onder ons blijven, en ik doe met plezier mee als gastvrouw."
                        )
                    else:
                        reply_text = (
                            f"Welkom terug onder ons, {owner_nm}! De visite-modus is uit en alle gezinskaarten zijn weer zichtbaar."
                        )
                elif top["kind"] == "household_rule":
                    reply_text = f"Staat genoteerd, {owner_nm}: \"{top['summary']}\". Mocht je je bedenken, dan draai je het met één tik weer terug."
                elif top["kind"] == "tone_preference":
                    reply_text = f"Helemaal goed, {owner_nm} — ik pas mijn toon meteen voor je aan!"
                elif top["kind"] == "household_intro":
                    fam_str = f" en fijn dat ik ook voor {', '.join(fam_members)} klaar mag staan" if fam_members else ""
                    voice_bit = " Ik heb meteen je stemgeluid lokaal opgeslagen zodat ik je herken." if voice_res else ""
                    reply_text = (
                        f"Aangenaam {owner_nm}{fam_str}! Ik ben {eva_nm}, en ik voel me hier nu al thuis.{voice_bit}"
                    )
                else:
                    reply_text = f"Geregeld, {owner_nm}! {top['summary']}"
            elif conjure_panel == "rules":
                reply_text = f"Hier zijn onze actuele huisafspraken, {owner_nm}. Zeg het gerust als we iets willen bijstellen."
            elif conjure_panel == "devices":
                reply_text = f"Dit heb ik tot nu toe op ons thuisnetwerk gezien, {owner_nm}."
            elif conjure_panel == "family":
                reply_text = f"Hier kun je meteen je eigen telefoon of een persoonlijke welkomstkaart voor het gezin koppelen."
            else:
                fam_str = f" en {', '.join(fam_members)}" if fam_members else ""
                reply_text = (
                    f"Hoi {owner_nm}! Ik ben {eva_nm} en ik sta klaar voor jou{fam_str}. "
                    "Vertel me gerust wat je fijn vindt in huis of tik op een van mijn sferen."
                )

        return {
            "ok": True,
            "reply_text": reply_text,
            "applied_changes": applied_changes,
            "conjure_panel": conjure_panel,
            "conjure_preset": conjure_preset,
            "voice_enrolled": voice_res is not None,
            "speaker_match": speaker_match,
            "stage": self.living_stage_snapshot(base_origin=base_origin),
        }

    # ------------------------------------------------------------------
    # Personal 1-Scan Welcome Portal for Lola, Family & Guests (/welkom/{token})
    # ------------------------------------------------------------------

    def _member_welcomes_path(self) -> Path:
        return self.workspace / "member-welcomes.json"

    def create_member_welcome(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        primary_origin, all_origins = self._preferred_lan_origin(base_origin)
        raw_name = re.sub(r"\s+", " ", str(payload.get("member_name") or "Lola")).strip()[:64]
        if not raw_name:
            raise ApplianceSetupError("vul een naam in voor het welkomstkaartje")
        role = str(payload.get("role") or "gezin").strip().lower()
        if role not in {"gezin", "gast"}:
            raise ApplianceSetupError("rol moet 'gezin' of 'gast' zijn")
        personal_note = re.sub(r"\s+", " ", str(payload.get("personal_note") or "")).strip()[:200]

        if role == "gezin":
            state = self._load_state()
            owner_nm = str(state["profile"].get("owner_name") or "Jaap")
            fam = [str(x) for x in (state["profile"].get("family_members") or [])]
            if raw_name.lower() not in {x.lower() for x in fam} and raw_name.lower() != owner_nm.lower():
                fam.append(raw_name)
                state["profile"]["family_members"] = fam[:12]
                self._save_state(state)
            self._sync_household_head_settings()

        raw_token = "wlk_" + secrets.token_urlsafe(24)
        token_sha256 = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        welcome_id = "wlk_" + token_sha256[:10]
        now_iso = _utc_now_iso()
        expires_epoch = int(time.time()) + (7 * 86400 if role == "gezin" else 2 * 86400)

        store = _read_json_if_present(self._member_welcomes_path()) or {"records": []}
        records = [dict(r) for r in (store.get("records") or []) if isinstance(r, dict)]
        records.insert(
            0,
            {
                "welcome_id": welcome_id,
                "token_sha256": token_sha256,
                "member_name": raw_name,
                "role": role,
                "personal_note": personal_note,
                "status": "pending",
                "created_at": now_iso,
                "expires_epoch": expires_epoch,
                "completed_at": None,
            },
        )
        _write_json_private(self._member_welcomes_path(), {"records": records[:32]})

        url = f"{primary_origin}/welkom/{raw_token}"
        return {
            "ok": True,
            "welcome_id": welcome_id,
            "member_name": raw_name,
            "role": role,
            "url": url,
            "alternate_urls": [f"{orig}/welkom/{raw_token}" for orig in all_origins],
            "qr_svg": render_qr_svg(url),
            "stage": self.living_stage_snapshot(base_origin=base_origin),
        }

    def _resolve_welcome_record(self, token: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cand = (token or "").strip()
        if not re.fullmatch(r"wlk_[A-Za-z0-9_-]{24,48}", cand):
            raise ApplianceSetupError("ongeldige welkomstlink", status_code=404)
        cand_hash = hashlib.sha256(cand.encode("ascii")).hexdigest()
        store = _read_json_if_present(self._member_welcomes_path()) or {"records": []}
        records = [dict(r) for r in (store.get("records") or []) if isinstance(r, dict)]
        matched = next(
            (
                r
                for r in records
                if len(str(r.get("token_sha256") or "")) == 64
                and hmac.compare_digest(cand_hash, str(r["token_sha256"]))
            ),
            None,
        )
        if matched is None:
            raise ApplianceSetupError("deze welkomstlink is onbekend of ingetrokken", status_code=404)
        if int(matched.get("expires_epoch", 0)) <= int(time.time()):
            raise ApplianceSetupError("deze welkomstlink is verlopen; vraag een nieuwe QR-code", status_code=410)
        return matched, records

    def get_member_welcome(self, token: str) -> dict[str, Any]:
        rec, _ = self._resolve_welcome_record(token)
        state = self._load_state()
        char = self._load_character()
        eva_name = str(char.get("display_name") or "Eva")
        owner_name = str(state["profile"].get("owner_name") or "Jaap")
        member_name = str(rec["member_name"])
        role = str(rec["role"])

        if role == "gezin":
            greeting = (
                f"Hoi {member_name}! Wat leuk om je te ontmoeten — ik ben {eva_name}. "
                f"{owner_name} vertelde al dat we hier samen in huis wonen! "
                "Zeg gerust even gedag met je stem of laat weten wat jij fijn vindt (en wanneer ik je juist met rust moet laten)."
            )
            chips = [
                "Houd antwoorden lekker vlot en gezellig",
                "'s Ochtends vroeg liever nog even rust",
                "Help me graag met muziek, school of plannen",
                "Droge humor mag altijd!",
            ]
        else:
            greeting = (
                f"Hoi {member_name}! Welkom bij {owner_name} thuis. Ik ben {eva_name}, de digitale huisgenoot hier in huis. "
                "Vertel gerust waar ik je tijdens je bezoek mee kan helpen!"
            )
            chips = [
                "Gezellig kennis te maken!",
                "Leuke muziektips of weetjes voor aan tafel",
                "Houd het lekker luchtig en kort",
            ]

        return {
            "contract": "EvaMemberWelcome/v1",
            "welcome_id": rec["welcome_id"],
            "eva_name": eva_name,
            "owner_name": owner_name,
            "member_name": member_name,
            "role": role,
            "personal_note": rec.get("personal_note", ""),
            "greeting_text": greeting,
            "wish_chips": chips,
            "status": rec.get("status", "pending"),
        }

    def complete_member_welcome(
        self, token: str, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        rec, records = self._resolve_welcome_record(token)
        member_name = str(rec["member_name"])
        role: str = "gezin" if rec.get("role") == "gezin" else "gast"
        wishes = re.sub(r"\s+", " ", str(payload.get("wishes") or "")).strip()[:220]
        audio_pcm_b64 = str(payload.get("audio_pcm_b64") or "").strip()

        mp_dir = self._model_providers_dir()
        member_id = ""
        if os.name != "nt":
            from eva.application.household_head_settings import HouseholdHeadSettings

            hh = HouseholdHeadSettings(mp_dir)
            existing = next(
                (
                    m
                    for m in hh.status().get("members", [])
                    if isinstance(m, dict)
                    and str(m.get("display_name") or "").strip().lower() == member_name.lower()
                    and m.get("role") != "gezinshoofd"
                ),
                None,
            )
            note_val = wishes or str(rec.get("personal_note") or ("Kerngezin" if role == "gezin" else "Gast"))
            updated_hh = hh.update_member_or_invite(
                action="upsert_member",
                target=str(existing["member_id"]) if existing else "",
                role=role,  # type: ignore[arg-type]
                display_name=member_name,
                notes=note_val[:200],
            )
            matched_after = next(
                (
                    m
                    for m in updated_hh.get("members", [])
                    if isinstance(m, dict)
                    and str(m.get("display_name") or "").strip().lower() == member_name.lower()
                ),
                None,
            )
            if matched_after:
                member_id = str(matched_after["member_id"])
        else:
            self._sync_household_head_settings()
            hh_view = self._read_household_view()
            matched_after = next(
                (
                    m
                    for m in (hh_view.get("members") or [])
                    if isinstance(m, dict)
                    and str(m.get("display_name") or "").strip().lower() == member_name.lower()
                ),
                None,
            )
            member_id = str(matched_after["member_id"]) if matched_after else f"{role}-{member_name.lower()}"

        voice_enrolled = False
        if role == "gezin" and member_id and audio_pcm_b64:
            vp_res = self._enroll_member_pcm_if_valid(member_id=member_id, audio_pcm_b64=audio_pcm_b64)
            voice_enrolled = vp_res is not None

        if role == "gezin" and wishes:
            self._sync_household_head_settings(extra_rules=[f"Voorkeur van {member_name}: {wishes}"])

        pairing = self.create_device_pairing(
            {
                "mode": "pocket",
                "label": f"{member_name} (Eva Pocket)",
                "allow_private": role == "gezin",
                "allow_owner_voice": False,
            },
            base_origin=base_origin,
        )
        pocket_url = str(pairing["url"])
        parsed_pocket = urlsplit(pocket_url)
        pocket_path = parsed_pocket.path
        if parsed_pocket.query:
            pocket_path += f"?{parsed_pocket.query}"
        if parsed_pocket.fragment:
            pocket_path += f"#{parsed_pocket.fragment}"

        rec["status"] = "completed"
        rec["completed_at"] = _utc_now_iso()
        _write_json_private(self._member_welcomes_path(), {"records": records})

        eva_name = self._load_character().get("display_name", "Eva")
        voice_note = " Ik herken je stem voortaan meteen in huis!" if voice_enrolled else ""
        wish_note = f" En ik onthoud goed wat je zei: \"{wishes}\"." if wishes else ""
        reply_text = (
            f"Superleuk {member_name}!{voice_note}{wish_note} "
            f"Je eigen {eva_name} Pocket staat nu klaar — klik hieronder om haar op je telefoon te openen."
        )
        return {
            "ok": True,
            "member_name": member_name,
            "role": role,
            "voice_enrolled": voice_enrolled,
            "reply_text": reply_text,
            "pocket_url": pocket_url,
            "pocket_path": pocket_path,
        }

    def synthesize_living_speech(self, text: str) -> tuple[bytes, str] | None:
        """Synthesize natural spoken Dutch audio via Living Core TTS or OpenAI TTS if configured."""
        clean_text = re.sub(r"\s+", " ", (text or "").strip())[:1400]
        if not clean_text:
            return None

        # 1. Try Living Core neural speech bridge (/internal/v1/dashboard/holostage/speech-audio)
        tok_path = self._secrets_dir() / "dashboard-token"
        if tok_path.is_file():
            try:
                status, hdrs, body = self.proxy_dashboard_request(
                    method="POST",
                    path="/internal/v1/dashboard/holostage/speech-audio",
                    query="",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": self.core_base_url,
                    },
                    body=json.dumps({"text": clean_text}).encode("utf-8"),
                )
                if status == 200 and len(body) > 256:
                    return body, hdrs.get("Content-Type", "audio/mpeg")
            except Exception:
                pass

        # 2. Try direct OpenAI TTS if an active api.openai.com API key is stored
        try:
            mp_settings = ModelProviderSettings(self._model_providers_dir())
            sel_id = mp_settings.read().get("selected_id")
            if sel_id:
                row, api_key = mp_settings.connection(sel_id, selected=True)
            else:
                row, api_key = {}, ""
            if (
                row.get("kind") == "openai_compatible"
                and "api.openai.com" in str(row.get("endpoint") or "")
                and api_key
                and api_key != "eva-local-keyless-endpoint"
            ):
                conn = http.client.HTTPSConnection("api.openai.com", timeout=10.0)
                try:
                    tts_body = json.dumps(
                        {
                            "model": "tts-1",
                            "voice": "nova",
                            "input": clean_text,
                            "response_format": "mp3",
                        }
                    ).encode("utf-8")
                    conn.request(
                        "POST",
                        "/v1/audio/speech",
                        body=tts_body,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    resp = conn.getresponse()
                    audio_bytes = resp.read(4_194_304)
                    if resp.status == 200 and len(audio_bytes) > 256:
                        return audio_bytes, "audio/mpeg"
                finally:
                    conn.close()
        except Exception:
            pass

        # 3. Warm Dutch neural female voice (`nl-NL-FennaNeural` via edge-tts, cached in RAM)
        neural = synthesize_neural_dutch_mp3(clean_text, voice="nl-NL-FennaNeural")
        if neural is not None:
            return neural

        return None

    # ------------------------------------------------------------------
    # Full-Duplex Realtime Live Voice (`gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, `gemini-3.8-live`)
    # ------------------------------------------------------------------

    def _find_realtime_api_key(self, kind: str) -> str | None:
        hub = self._integration_hub()
        vault_meta = hub.vault.list_metadata()
        slot_candidates = (
            ("openai_realtime", "openai_api")
            if kind == "openai"
            else ("gemini_live", "gemini_cloud", "gemini")
        )
        for slot in slot_candidates:
            if slot in vault_meta:
                try:
                    _, secret = hub.vault.use_secret_for_approved_device(
                        device_id=slot,
                        is_device_approved=True,
                        callback=lambda u, s: (u, s),
                    )
                    if secret.strip():
                        return secret.strip()
                except Exception:
                    pass
        try:
            mp = ModelProviderSettings(self._model_providers_dir())
            data = mp.read()
            for cid, row in (data.get("connections") or {}).items():
                if not isinstance(row, dict) or not row.get("enabled"):
                    continue
                if kind == "openai" and row.get("kind") == "openai_compatible" and "api.openai.com" in str(row.get("endpoint") or ""):
                    _, key = mp.connection(cid)
                    if key and key != "eva-local-keyless-endpoint":
                        return key.strip()
                if kind == "gemini" and row.get("kind") == "gemini":
                    _, key = mp.connection(cid)
                    if key:
                        return key.strip()
        except Exception:
            pass
        return None

    def _realtime_voice_status(
        self, state: Mapping[str, Any], codex_status: Mapping[str, Any]
    ) -> dict[str, Any]:
        rt_cfg = state.get("realtime_voice") if isinstance(state.get("realtime_voice"), Mapping) else {}
        sel_model = str(rt_cfg.get("model") or "gpt-realtime-2.1")
        if sel_model not in VALID_REALTIME_MODELS:
            sel_model = "gpt-realtime-2.1"
        spec = VALID_REALTIME_MODELS[sel_model]
        sel_voice = str(rt_cfg.get("voice") or spec["default_voice"])
        if sel_voice not in spec["voices"]:
            sel_voice = str(spec["default_voice"])

        has_oai = bool(self._find_realtime_api_key("openai"))
        has_gem = bool(self._find_realtime_api_key("gemini"))
        codex_ok = bool(
            codex_status.get("logged_in")
            or (self.runtime_root / "model-lease" / "auth.json").is_file()
        )
        ready = (has_oai or codex_ok) if spec["provider"] == "openai_realtime" else has_gem
        return {
            "selected_model": sel_model,
            "selected_voice": sel_voice,
            "transport": spec["transport"],
            "ready": ready,
            "has_openai_key": has_oai,
            "has_gemini_key": has_gem,
            "codex_webrtc_ready": codex_ok,
            "models": list(REALTIME_MODEL_CATALOG),
        }

    def save_realtime_voice_preference(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        model = str(payload.get("model") or "gpt-realtime-2.1").strip()
        if model not in VALID_REALTIME_MODELS:
            raise ApplianceSetupError("onbekend realtime spraakmodel")
        spec = VALID_REALTIME_MODELS[model]
        voice = str(payload.get("voice") or spec["default_voice"]).strip()
        if voice not in spec["voices"]:
            voice = str(spec["default_voice"])

        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            slot_id = "gemini_live" if spec["provider"] == "gemini_live" else "openai_realtime"
            title = "Google Gemini 3.8 Live Sleutel" if slot_id == "gemini_live" else "OpenAI Realtime 2.1 Sleutel"
            base_url = "https://generativelanguage.googleapis.com" if slot_id == "gemini_live" else "https://api.openai.com"
            self._integration_hub().save_and_verify_service(
                service_id=slot_id,
                title=title,
                service_kind=slot_id,
                base_url=base_url,
                auth_mode="api_key",
                secret=api_key,
                run_probe=False,
            )

        state = self._load_state()
        state["realtime_voice"] = {"model": model, "voice": voice}
        self._save_state(state)
        return {
            "ok": True,
            "realtime_voice": self._realtime_voice_status(state, self._codex_auth_status()),
            "stage": self.living_stage_snapshot(),
        }

    def start_realtime_webrtc_session(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        offer_sdp = str(payload.get("offer_sdp") or "")
        state = self._load_state()
        rt_cfg = state.get("realtime_voice") if isinstance(state.get("realtime_voice"), Mapping) else {}
        req_model = str(payload.get("model") or rt_cfg.get("model") or "gpt-realtime-2.1")
        if req_model not in {"gpt-realtime-2.1", "gpt-realtime-2.1-mini"}:
            req_model = "gpt-realtime-2.1"
        req_voice = str(payload.get("voice") or rt_cfg.get("voice") or "marin")
        snap = self.living_stage_snapshot(base_origin=base_origin)
        lease_path = self.runtime_root / "model-lease" / "auth.json"
        if not lease_path.is_file() and self.codex_auth_file.is_file():
            lease_path = self.codex_auth_file
        try:
            return self._realtime_hub.negotiate_openai_or_codex_webrtc(
                offer_sdp=offer_sdp,
                model=req_model,
                voice=req_voice,
                stage_snapshot=snap,
                openai_api_key=self._find_realtime_api_key("openai"),
                codex_lease_path=lease_path,
            )
        except (ValueError, RuntimeError, TimeoutError) as exc:
            raise ApplianceSetupError(str(exc), status_code=502) from exc

    def stop_realtime_voice_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        sid = str(payload.get("session_id") or "").strip() or None
        return self._realtime_hub.stop_session(sid)

    async def bridge_gemini_live_websocket(
        self, websocket: Any, *, model: str, voice: str, base_origin: str | None = None
    ) -> None:
        state = self._load_state()
        rt_cfg = state.get("realtime_voice") if isinstance(state.get("realtime_voice"), Mapping) else {}
        eff_model = model or str(rt_cfg.get("model") or "gemini-3.8-live")
        if not eff_model.startswith("gemini"):
            eff_model = "gemini-3.8-live"
        eff_voice = voice or str(rt_cfg.get("voice") or "Aoede")
        snap = await asyncio.to_thread(self.living_stage_snapshot, base_origin=base_origin)
        gem_key = await asyncio.to_thread(self._find_realtime_api_key, "gemini")
        await run_gemini_live_bridge(
            browser_ws=websocket,
            gemini_api_key=gem_key or "",
            requested_model=eff_model,
            voice=eff_voice,
            stage_snapshot=snap,
            execute_core_turn=lambda txt: self.handle_living_turn({"text": txt}, base_origin=base_origin),
            set_guest_mode_fn=lambda on: self.set_guest_mode(enabled=on),
            execute_vision_look=lambda cam, q: self.handle_vision_look({"camera_hint": cam, "prompt": q}),
        )

    # ------------------------------------------------------------------
    # Main Family Wall Display (`UC-Display-SE-21`), Local LAN HTTPS (:8766) & Camera Vision Bridge
    # ------------------------------------------------------------------

    def ensure_lan_tls_cert(self) -> tuple[Path, Path]:
        """Ensure an X.509 TLS certificate covering localhost + all current LAN IPs exists for wall-display WebRTC."""
        from datetime import timedelta
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        cert_path = self.workspace / "lan-tls-cert.pem"
        key_path = self.workspace / "lan-tls-key.pem"
        meta_path = self.workspace / "lan-tls-meta.json"
        lan_ips = sorted(set(["127.0.0.1"] + _detect_lan_ipv4s()))

        if cert_path.is_file() and key_path.is_file():
            meta = _read_json_if_present(meta_path) or {}
            if meta.get("ips") == lan_ips and int(meta.get("expires_epoch") or 0) > int(time.time()) + 86400 * 7:
                return cert_path, key_path

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Eva Thuisserver"),
                x509.NameAttribute(NameOID.COMMON_NAME, "Eva Local LAN Stage CA"),
            ]
        )
        san_entries: list[x509.GeneralName] = [
            x509.DNSName("localhost"),
            x509.DNSName(socket.gethostname() or "eva-local"),
        ]
        for raw_ip in lan_ips:
            try:
                san_entries.append(x509.IPAddress(ipaddress.ip_address(raw_ip)))
            except ValueError:
                continue

        now_dt = datetime.now(UTC)
        exp_dt = now_dt + timedelta(days=825)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now_dt - timedelta(minutes=5))
            .not_valid_after(exp_dt)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .sign(key, hashes.SHA256())
        )

        key_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        atomic_write_private(key_path, key_bytes)
        atomic_write_private(cert_path, cert_bytes)
        _write_json_private(meta_path, {"ips": lan_ips, "expires_epoch": int(exp_dt.timestamp())})
        return cert_path, key_path

    def get_lan_ca_cert_pem(self) -> str:
        cert_path, _ = self.ensure_lan_tls_cert()
        return cert_path.read_text(encoding="utf-8")

    def _discover_ha_cameras_and_displays(self) -> dict[str, Any]:
        now_m = time.monotonic()
        if self._ha_cam_cache is not None and (now_m - self._ha_cam_cache[0]) < 25.0:
            return self._ha_cam_cache[1]

        out: dict[str, Any] = {"cameras": [], "display_entities": []}
        state = self._load_state()
        ha_url = str(state["modules"]["smart_home"].get("url") or "").strip().rstrip("/")
        tok_path = self._secrets_dir() / "home-assistant-token"
        if not ha_url or not tok_path.is_file():
            self._ha_cam_cache = (now_m, out)
            return out

        try:
            token = tok_path.read_text(encoding="utf-8").strip()
            parsed = urlsplit(ha_url)
            conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 8123), timeout=3.0)
            try:
                conn.request("GET", "/api/states", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
                resp = conn.getresponse()
                raw = resp.read(2 * 1024 * 1024)
                if resp.status == 200:
                    items = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(items, list):
                        for row in items:
                            if not isinstance(row, dict):
                                continue
                            eid = str(row.get("entity_id") or "")
                            st = str(row.get("state") or "")
                            attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
                            fname = str(attrs.get("friendly_name") or eid)
                            if eid.startswith("camera.") and "valetudo" not in eid and "low_resolution" not in eid:
                                out["cameras"].append({"entity_id": eid, "label": fname, "state": st})
                            low_comb = f"{eid} {fname}".lower()
                            if (
                                eid.startswith(("notify.", "switch."))
                                and any(k in low_comb for k in ("unifi_display", "uc_display", "keuken_scherm", "connect_display", "display"))
                            ):
                                out["display_entities"].append({"entity_id": eid, "label": fname, "state": st})
            finally:
                conn.close()
        except Exception:
            pass

        self._ha_cam_cache = (now_m, out)
        return out

    def _main_display_status(self, state: Mapping[str, Any], *, base_origin: str | None = None) -> dict[str, Any]:
        md = dict(state.get("main_display") or {})
        disc = self._discover_ha_cameras_and_displays()
        lan_ips = _detect_lan_ipv4s()
        https_port = int(md.get("https_port") or 8766)
        best_lan_ip = lan_ips[0] if lan_ips else "127.0.0.1"
        https_base = f"https://{best_lan_ip}:{https_port}"
        primary_http, _ = self._preferred_lan_origin(base_origin)

        cam_id = str(md.get("camera_entity_id") or "")
        if not cam_id and disc["cameras"]:
            # Prefer a kitchen/living/doorbell camera automatically when available
            preferred_cam = next(
                (
                    c["entity_id"]
                    for c in disc["cameras"]
                    if any(k in c["label"].lower() for k in ("keuken", "woonkamer", "doorbell", "deurbel"))
                ),
                disc["cameras"][0]["entity_id"],
            )
            cam_id = str(preferred_cam)

        return {
            "enabled": bool(md.get("enabled", True)),
            "label": str(md.get("label") or "Keuken Hoofdscherm (UC-Display-SE-21)"),
            "room": str(md.get("room") or "keuken"),
            "camera_entity_id": cam_id,
            "camera_snapshot_url": str(md.get("camera_snapshot_url") or ""),
            "wake_entity_id": str(md.get("wake_entity_id") or "notify.keuken_scherm"),
            "https_port": https_port,
            "https_origin": https_base,
            "kiosk_preview_url": f"{primary_http}/?kiosk=1",
            "available_cameras": disc["cameras"],
            "detected_displays": disc["display_entities"],
        }

    def save_main_display_config(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]:
        state = self._load_state()
        md = dict(state.get("main_display") or {})
        if "label" in payload:
            md["label"] = re.sub(r"\s+", " ", str(payload.get("label") or "")).strip()[:80] or md.get("label", "Hoofdscherm")
        if "room" in payload:
            md["room"] = re.sub(r"[^a-z0-9_-]+", "_", str(payload.get("room") or "keuken").lower()).strip("_") or "keuken"
        if "camera_entity_id" in payload:
            md["camera_entity_id"] = str(payload.get("camera_entity_id") or "").strip()[:120]
        if "camera_snapshot_url" in payload:
            md["camera_snapshot_url"] = str(payload.get("camera_snapshot_url") or "").strip()[:300]
        if "wake_entity_id" in payload:
            md["wake_entity_id"] = str(payload.get("wake_entity_id") or "").strip()[:120]
        state["main_display"] = md
        self._save_state(state)

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "EVA-DISP-" + "".join(secrets.choice(alphabet) for _ in range(12))
        with self._lock:
            self._device_invites[code] = self._clock() + 86400.0

        status = self._main_display_status(state, base_origin=base_origin)
        kiosk_https_url = f"{status['https_origin']}/pair?kiosk=1#{code}"
        return {
            "ok": True,
            "pairing_code": code,
            "kiosk_https_url": kiosk_https_url,
            "qr_svg": render_qr_svg(kiosk_https_url),
            "main_display": status,
            "stage": self.living_stage_snapshot(base_origin=base_origin),
        }

    def wake_main_display(self) -> dict[str, Any]:
        """Wake the UC-Display-SE-21 screen via Home Assistant Companion notify or ha-unifi-connect switch."""
        state = self._load_state()
        ha_url = str(state["modules"]["smart_home"].get("url") or "").strip().rstrip("/")
        tok_path = self._secrets_dir() / "home-assistant-token"
        if not ha_url or not tok_path.is_file():
            raise ApplianceSetupError("Home Assistant is nog niet gekoppeld om het scherm te wekken.")

        wake_id = str((state.get("main_display") or {}).get("wake_entity_id") or "notify.keuken_scherm").strip()
        targets = [wake_id] if wake_id else []
        if "notify.unifi_display_for_arm64" not in targets:
            targets.append("notify.unifi_display_for_arm64")

        token = tok_path.read_text(encoding="utf-8").strip()
        parsed = urlsplit(ha_url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        triggered: list[str] = []
        for target in targets:
            conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 8123), timeout=4.0)
            try:
                if target.startswith("switch."):
                    svc_path = "/api/services/switch/turn_on"
                    body = json.dumps({"entity_id": target}).encode("utf-8")
                else:
                    svc_name = target.split(".", 1)[1] if "." in target else target
                    svc_path = f"/api/services/notify/{svc_name}"
                    body = json.dumps({"message": "command_screen_on"}).encode("utf-8")
                conn.request(
                    "POST",
                    svc_path,
                    body=body,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                resp.read(16_384)
                if 200 <= resp.status < 300:
                    triggered.append(target)
            except OSError:
                continue
            finally:
                conn.close()

        return {
            "ok": bool(triggered),
            "triggered": triggered,
            "detail": (
                f"Wek-signaal verstuurd naar {', '.join(triggered)}!"
                if triggered
                else "Kon geen wek-signaal afleveren bij Home Assistant."
            ),
        }

    def handle_vision_look(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Analyze either a browser/USB camera JPEG or a live Home Assistant / UniFi Protect camera snapshot."""
        from eva.adapters.model.holostage_vision import HoloStageVisionBridge, HoloStageVisionError

        raw_b64 = str(payload.get("image_jpeg_b64") or "").strip()
        cam_hint = str(payload.get("camera_hint") or "").strip().lower()
        prompt_q = str(payload.get("prompt") or "Beschrijf vlot en warm in het Nederlands wat of wie je ziet.").strip()[:280]

        img_bytes: bytes | None = None
        media_type = "image/jpeg"
        cam_label = "Lokale camera"

        if raw_b64:
            if "," in raw_b64 and raw_b64.startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                img_bytes = base64.b64decode(raw_b64, validate=False)
            except Exception as exc:
                raise ApplianceSetupError("Ongeldig camerabeeld ontvangen") from exc

        if not img_bytes:
            state = self._load_state()
            md_status = self._main_display_status(state)
            cams = md_status["available_cameras"]
            chosen_id = str(payload.get("camera_entity_id") or "").strip()
            if not chosen_id and cam_hint and cams:
                aliases = {
                    "voordeur": ("doorbell", "deurbel", "voordeur"),
                    "deurbel": ("doorbell", "deurbel", "voordeur"),
                    "tuin": ("tuin", "garden", "achter"),
                    "garage": ("garage", "oprit"),
                    "keuken": ("keuken", "kitchen"),
                }
                search_terms: tuple[str, ...] = (cam_hint,)
                for key, vals in aliases.items():
                    if key in cam_hint:
                        search_terms = vals
                        break
                matched = next(
                    (
                        c
                        for c in cams
                        if any(t in f"{c['entity_id']} {c['label']}".lower() for t in search_terms)
                    ),
                    None,
                )
                if matched:
                    chosen_id = str(matched["entity_id"])
                    cam_label = str(matched["label"])

            if not chosen_id:
                chosen_id = str(md_status["camera_entity_id"] or "")
                match_def = next((c for c in cams if c["entity_id"] == chosen_id), None)
                if match_def:
                    cam_label = str(match_def["label"])

            ha_url = str(state["modules"]["smart_home"].get("url") or "").strip().rstrip("/")
            tok_path = self._secrets_dir() / "home-assistant-token"
            if chosen_id and ha_url and tok_path.is_file():
                token = tok_path.read_text(encoding="utf-8").strip()
                parsed = urlsplit(ha_url)
                conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
                conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 8123), timeout=6.0)
                try:
                    conn.request(
                        "GET",
                        f"/api/camera_proxy/{chosen_id}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    resp = conn.getresponse()
                    fetched = resp.read(5 * 1024 * 1024)
                    if resp.status == 200 and len(fetched) > 512:
                        img_bytes = fetched
                        ct = resp.getheader("Content-Type") or "image/jpeg"
                        media_type = ct.split(";", 1)[0].strip() or "image/jpeg"
                        if cam_label == "Lokale camera":
                            cam_label = chosen_id
                finally:
                    conn.close()

        if not img_bytes:
            raise ApplianceSetupError(
                "Geen camerabeeld beschikbaar. Kies bij 🖥️ Hoofdscherm een Home Assistant / UniFi Protect camera of geef cameratoestemming."
            )

        lease_path = self.runtime_root / "model-lease" / "auth.json"
        if not lease_path.is_file() and self.codex_auth_file.is_file():
            lease_path = self.codex_auth_file

        old_auth_env = os.environ.get("EVA_CONVERSATION_AUTH_FILE")
        try:
            if lease_path.is_file():
                os.environ["EVA_CONVERSATION_AUTH_FILE"] = str(lease_path)
            bridge = HoloStageVisionBridge.from_runtime_settings()
            receipt = bridge.analyze_frame(img_bytes, media_type=media_type, focus_hint=prompt_q)
        except HoloStageVisionError as exc:
            raise ApplianceSetupError(str(exc), status_code=502) from exc
        finally:
            if old_auth_env is None:
                os.environ.pop("EVA_CONVERSATION_AUTH_FILE", None)
            else:
                os.environ["EVA_CONVERSATION_AUTH_FILE"] = old_auth_env

        return {
            "ok": True,
            "camera_label": cam_label,
            "summary_nl": receipt.summary_nl,
            "latency_ms": receipt.latency_ms,
            "model": receipt.model,
        }

    def stream_living_turn_ndjson(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> Iterator[bytes]:
        """Stream NDJSON turn with automatic Dutch 'tussenzinnetje' after 1.1s if Core is busy with tools/recall."""
        from eva.application.holostage import SentenceBoundaryChunker
        from queue import Empty, SimpleQueue

        raw_text = str(payload.get("text") or "").strip()
        state = self._load_state()
        owner_nm = str(state["profile"].get("owner_name") or "Jaap")
        q: SimpleQueue[dict[str, Any] | None] = SimpleQueue()

        def _worker() -> None:
            try:
                low = raw_text.lower()
                is_special = (
                    not raw_text
                    or bool(payload.get("preset_id"))
                    or raw_text.startswith("/")
                    or detect_living_intent(raw_text)["kind"] != "none"
                )
                if is_special or self.dry_run_apply:
                    out = self.handle_living_turn(payload, base_origin=base_origin)
                    if out.get("reply_text"):
                        q.put({"type": "speech_clause", "text": out["reply_text"]})
                    q.put({"type": "final", **out})
                    return

                pend_before = self._integration_hub().get_pending_connection()
                prev_req_at = (pend_before or {}).get("requested_at")

                status_code, _, stream_or_bytes = self.open_dashboard_stream(
                    method="POST",
                    path="/internal/v1/dashboard/conversation/respond",
                    query="",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/x-ndjson",
                        "Origin": self.core_base_url,
                    },
                    body=json.dumps({"text": raw_text[:980]}).encode("utf-8"),
                    owner_authenticated=True,
                )
                if status_code != 200 or isinstance(stream_or_bytes, bytes):
                    out = self.handle_living_turn(payload, base_origin=base_origin)
                    if out.get("reply_text"):
                        q.put({"type": "speech_clause", "text": out["reply_text"]})
                    q.put({"type": "final", **out})
                    return

                chunker = SentenceBoundaryChunker()
                deltas: list[str] = []
                final_text = ""
                for raw_line in stream_or_bytes:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("contract") == "EvaTextDelta/v1" and isinstance(ev.get("text"), str):
                        delta = ev["text"]
                        deltas.append(delta)
                        q.put({"type": "delta", "text": delta})
                        for clause in chunker.feed(delta):
                            q.put({"type": "speech_clause", "text": clause})
                    elif ev.get("contract") == "EvaInternalGroundedReply/v1":
                        final_text = str(ev.get("text") or "").strip()

                for rem in chunker.flush():
                    q.put({"type": "speech_clause", "text": rem})

                reply_text = final_text or "".join(deltas).strip()
                conjure_panel: str | None = None
                conjure_preset: str | None = None
                pend_after = self._integration_hub().get_pending_connection()
                if pend_after and pend_after.get("requested_at") != prev_req_at:
                    conjure_panel = "integrations"
                    conjure_preset = str(pend_after.get("service_kind") or "auto")
                elif any(w in low for w in ("koppel", "verbind", "aansluit", "instel", "token", "sleutel")):
                    if "home assistant" in low or "homeassistant" in low:
                        conjure_panel = "smart_home"
                        conjure_preset = "home_assistant"

                q.put(
                    {
                        "type": "final",
                        "ok": True,
                        "reply_text": reply_text,
                        "applied_changes": [],
                        "conjure_panel": conjure_panel,
                        "conjure_preset": conjure_preset,
                        "stage": self.living_stage_snapshot(base_origin=base_origin),
                    }
                )
            except Exception as exc:
                q.put({"type": "error", "detail": str(exc)})
            finally:
                q.put(None)

        threading.Thread(target=_worker, daemon=True, name="eva-living-turn-stream").start()
        filler_sent = False
        first_speech_seen = False
        t0 = time.monotonic()
        while True:
            try:
                item = q.get(timeout=0.35)
            except Empty:
                if not filler_sent and not first_speech_seen and raw_text and (time.monotonic() - t0) >= 1.15:
                    filler_sent = True
                    filler_txt = pick_dutch_filler_phrase(raw_text, owner_nm)
                    yield (json.dumps({"type": "filler", "text": filler_txt}, ensure_ascii=False) + "\n").encode("utf-8")
                continue
            if item is None:
                return
            if item.get("type") in {"speech_clause", "delta", "final"}:
                first_speech_seen = True
            yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")


__all__ = [
    "ApplianceSetupError",
    "ApplianceSetupService",
    "HINDSIGHT_IMAGES_BY_ARCH",
    "POSTGRES_IMAGE",
    "is_local_network_client",
]
