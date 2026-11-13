"""Fase 2 — Home Proprioception: Active LAN Discovery, Pre-LLM AES-256-GCM Vault, Spatial Presence Field & Digital Organogenesis.

Zero-bloat, capability-safe substrate allowing Eva to:
1. Actively discover devices on private home LANs (mDNS, SSDP, HTTP/TLS/RTSP/ONVIF/Cast/Hue/Shelly/MQTT/Tuya)
   without requiring Home Assistant.
2. Store device passwords/tokens in an AES-256-GCM vault (`0o600`) completely outside LLM prompts, logs, and journals.
3. Sense room-level motion & occupancy (`SpatialPresenceField`) from RF/LQI/RTT variance and sensor deltas at 0 LLM tokens,
   feeding Eva's C/SIMD Liquid Time-Constant (`dx/dt`) core.
4. Bind approved devices through deterministic native or AST-sandboxed capability-pinned micro-drivers ("Digital Organs").
"""

from __future__ import annotations

import ast
import asyncio
import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator, Literal
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DeviceConsentStatus = Literal["pending_consent", "approved", "ignored"]
AuthRequirementKind = Literal["none", "pairing_button", "password_or_token", "rtsp_credentials"]

DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
ROOM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")
_RFC1918_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)

# Safe stdlib modules allowed inside synthesized custom Python micro-organs (no I/O, no network, no OS).
SAFE_ORGAN_IMPORTS = frozenset({"json", "math", "re", "base64", "hashlib", "hmac", "datetime"})
FORBIDDEN_AST_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "memoryview",
        "type",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "help",
        "credits",
        "license",
    }
)
FORBIDDEN_AST_ATTRS = frozenset(
    {
        "format",
        "format_map",
        "gi_frame",
        "gi_code",
        "gi_yieldfrom",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
        "tb_frame",
        "tb_next",
        "f_globals",
        "f_locals",
        "f_back",
        "f_builtins",
        "f_code",
        "codecs",
        "sys",
        "os",
        "builtins",
        "subclasses",
        "mro",
    }
)

ROOM_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("woonkamer", ("woonkamer", "living", "tv", "stalamp", "pui", "media_kast")),
    ("keuken", ("keuken", "kitchen", "eettafel")),
    ("kantoor", ("kantoor", "office", "bureau", "werkplek")),
    ("slaapkamer_lola", ("lola", "slaapkamer_lola")),
    ("slaapkamer", ("slaapkamer", "bedroom", "mb")),
    ("badkamer", ("badkamer", "douche", "wc")),
    ("gang", ("gang", "hal", "voordeur", "entree")),
    ("tuin_buiten", ("tuin", "buiten", "schutting", "overkapping", "schuur", "palmboom")),
)


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _slugify(value: str, *, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (cleaned or "apparaat")[:max_len]


def _sanitize_lan_label(raw: Any, *, fallback: str = "Apparaat", max_len: int = 56) -> str:
    """Strip control characters, quotes, and prompt-injection delimiters from untrusted LAN broadcast labels."""
    txt = str(raw or "")
    cleaned = re.sub(r"[^A-Za-z0-9 _.():/-]+", " ", txt)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    return (cleaned or fallback)[:max_len]


def _local_interface_ips() -> set[str]:
    ips: set[str] = {"127.0.0.1", "0.0.0.0"}
    for probe_target in ("10.0.20.1", "192.168.1.1", "192.168.178.1", "10.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe_target, 80))
                ips.add(sock.getsockname()[0])
        except OSError:
            continue
    return ips


def infer_room_from_label(label: str) -> str:
    lowered = (label or "").lower()
    for room_id, needles in ROOM_KEYWORDS:
        if any(needle in lowered for needle in needles):
            return room_id
    return "algemeen"


def is_safe_lan_ipv4(host: str, *, allow_docker_bridge: bool = False) -> bool:
    """Strict SSRF guard: admit only unicast RFC1918 IPv4 LAN hosts (never loopback, link-local, metadata, Docker, or public)."""
    if not isinstance(host, str):
        return False
    try:
        addr = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
        or not any(addr in net for net in _RFC1918_NETWORKS)
    ):
        return False
    octets = addr.packed
    # Exclude Docker default bridge ranges 172.17.0.0/16 .. 172.31.255.255 unless explicitly requested
    if not allow_docker_bridge and octets[0] == 172 and 17 <= octets[1] <= 31:
        return False
    # Exclude WSL/CGNAT synthetic 10.253..255 internal bridges
    if octets[0] == 10 and octets[1] in (253, 254, 255):
        return False
    if octets[3] in (0, 255):
        return False
    return True


def parse_safe_lan_subnet(cidr: str) -> ipaddress.IPv4Network:
    net = ipaddress.ip_network(cidr.strip(), strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        raise ValueError("Alleen IPv4 LAN-subnetten worden ondersteund")
    if net.prefixlen < 24:
        raise ValueError("Subnet mag maximaal een /24 zijn (max 254 hosts per scan)")
    sample_hosts = list(net.hosts())
    if not sample_hosts or not all(is_safe_lan_ipv4(str(ip), allow_docker_bridge=False) for ip in sample_hosts[:2]):
        raise ValueError(f"Subnet {net} is geen veilig lokaal thuisnetwerk (RFC1918)")
    return net


def detect_default_lan_subnets() -> list[str]:
    """Discover physical home /24 LAN subnets from explicit config, HA anchor URL, and local interfaces."""
    subnets: list[str] = []

    def _add_ip(ip_str: str) -> None:
        if is_safe_lan_ipv4(ip_str, allow_docker_bridge=False):
            net = str(ipaddress.ip_network(f"{ip_str}/24", strict=False))
            if net not in subnets:
                subnets.append(net)

    raw_env = os.environ.get("EVA_HOME_LAN_SUBNETS", "").strip()
    if raw_env:
        for item in raw_env.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                net = parse_safe_lan_subnet(item)
                if str(net) not in subnets:
                    subnets.append(str(net))
            except ValueError:
                _add_ip(item)

    ha_url = os.environ.get("EVA_HOME_ASSISTANT_URL", "").strip()
    if ha_url:
        parsed = urlparse(ha_url)
        if parsed.hostname:
            _add_ip(parsed.hostname)

    # Check routing table / interfaces via UDP connect trick to common LAN gateways without sending packets
    for probe_target in ("10.0.20.1", "192.168.1.1", "192.168.178.1", "10.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe_target, 80))
                local_ip = sock.getsockname()[0]
                _add_ip(local_ip)
        except OSError:
            continue

    return subnets[:3]


def _ensure_private_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        st = os.lstat(directory)
        if not stat.S_ISLNK(st.st_mode) and (stat.S_IMODE(st.st_mode) & 0o077):
            os.chmod(directory, 0o700)
    except OSError:
        pass


def _atomic_write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_dir(path.parent)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(tmp_path), flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, path)


class PreLlmCredentialVault:
    """AES-256-GCM encrypted device secret vault (`0o600`). Secrets NEVER enter LLM context or API responses."""

    def __init__(self, directory: Path | str | None = None) -> None:
        base = Path(
            directory
            or os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
        )
        self.directory = base
        self.key_file = base / ".credential-vault.key"
        self.vault_file = base / "credential-vault.enc.json"
        self.lock_file = base / ".credential-vault.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_private_dir(self.directory)
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _load_or_create_master_key(self) -> bytes:
        _ensure_private_dir(self.directory)
        if self.key_file.exists() or self.key_file.is_symlink():
            st = os.lstat(self.key_file)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise RuntimeError("Vault master key must be a regular non-symlink file")
            if stat.S_IMODE(st.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
                os.chmod(self.key_file, 0o600)
            raw = self.key_file.read_bytes()
            if len(raw) != 32:
                raise RuntimeError("Vault master key has invalid length")
            return raw

        key = secrets.token_bytes(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.key_file), flags, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        return key

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.vault_file.exists():
            return {"contract": "EvaPreLlmVault/v1", "entries": {}}
        st = os.lstat(self.vault_file)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise RuntimeError("Vault store must be a regular non-symlink file")
        try:
            data = json.loads(self.vault_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            quarantine = self.directory / f"credential-vault.corrupt.{int(time.time())}.json"
            try:
                os.replace(self.vault_file, quarantine)
            except OSError:
                pass
            return {"contract": "EvaPreLlmVault/v1", "entries": {}}
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return {"contract": "EvaPreLlmVault/v1", "entries": {}}
        return data

    def store_secret(
        self,
        *,
        device_id: str,
        secret_value: str,
        credential_kind: str = "password_or_token",
        username: str = "",
        require_tls: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        dev_id = device_id.strip().lower()
        if not DEVICE_ID_PATTERN.fullmatch(dev_id):
            raise ValueError("Ongeldig apparaat-ID voor kluisopslag")
        if not isinstance(secret_value, str):
            raise ValueError("Geheim of wachtwoord mag niet leeg zijn")
        cleaned_secret = secret_value.strip("\r\n")
        if not cleaned_secret or not cleaned_secret.strip():
            raise ValueError("Geheim of wachtwoord mag niet leeg zijn")
        if any(ch in cleaned_secret for ch in ("\r", "\n", "\x00")):
            raise ValueError("Geheim mag geen regeleindes of nul-bytes bevatten")
        if len(cleaned_secret) > 4096:
            raise ValueError("Geheim is te lang (max 4096 tekens)")
        clean_kind = _slugify(credential_kind, max_len=32)
        clean_user = username.strip()[:128] if isinstance(username, str) else ""
        if any(ch in clean_user for ch in ("\r", "\n", "\x00")):
            raise ValueError("Gebruikersnaam mag geen regeleindes bevatten")
        ts = _iso(_utc_now(now))

        with self._locked():
            key = self._load_or_create_master_key()
            nonce = secrets.token_bytes(12)
            aad = f"EvaPreLlmVault/v1:{dev_id}:{clean_kind}".encode("utf-8")
            payload_bytes = json.dumps(
                {"secret": cleaned_secret, "username": clean_user},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            ciphertext = AESGCM(key).encrypt(nonce, payload_bytes, aad)
            store = self._read_unlocked()
            store["entries"][dev_id] = {
                "credential_kind": clean_kind,
                "has_username": bool(clean_user),
                "require_tls": bool(require_tls),
                "nonce_b64": base64.b64encode(nonce).decode("ascii"),
                "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
                "updated_at": ts,
            }
            _atomic_write_private_json(self.vault_file, store)

        return {
            "device_id": dev_id,
            "has_credential": True,
            "credential_kind": clean_kind,
            "has_username": bool(clean_user),
            "require_tls": bool(require_tls),
            "updated_at": ts,
        }

    def delete_secret(self, device_id: str) -> bool:
        dev_id = device_id.strip().lower()
        with self._locked():
            store = self._read_unlocked()
            removed = store["entries"].pop(dev_id, None) is not None
            if removed:
                _atomic_write_private_json(self.vault_file, store)
            return removed

    def list_metadata(self) -> dict[str, dict[str, Any]]:
        if not self.vault_file.exists():
            return {}
        with self._locked():
            store = self._read_unlocked()
            return {
                dev_id: {
                    "device_id": dev_id,
                    "has_credential": True,
                    "credential_kind": str(entry.get("credential_kind") or "password_or_token"),
                    "has_username": bool(entry.get("has_username", False)),
                    "require_tls": bool(entry.get("require_tls", False)),
                    "updated_at": str(entry.get("updated_at") or ""),
                }
                for dev_id, entry in store["entries"].items()
                if isinstance(entry, dict)
            }

    def use_secret_for_approved_device(
        self,
        *,
        device_id: str,
        is_device_approved: bool,
        callback: Callable[[str, str], Any],
    ) -> Any:
        """Execute a bounded local driver callback with decrypted `(username, secret)` ONLY if approved."""
        if not is_device_approved:
            raise PermissionError("Kluistoegang geweigerd: apparaat is niet goedgekeurd door het gezinshoofd")
        dev_id = device_id.strip().lower()
        with self._locked():
            store = self._read_unlocked()
            entry = store["entries"].get(dev_id)
            if not isinstance(entry, dict):
                raise KeyError(f"Geen opgeslagen geheim gevonden voor '{dev_id}'")
            key = self._load_or_create_master_key()
            kind = str(entry.get("credential_kind") or "password_or_token")
            nonce = base64.b64decode(entry["nonce_b64"])
            ciphertext = base64.b64decode(entry["ciphertext_b64"])
            aad = f"EvaPreLlmVault/v1:{dev_id}:{kind}".encode("utf-8")
            decrypted = json.loads(AESGCM(key).decrypt(nonce, ciphertext, aad).decode("utf-8"))
        return callback(str(decrypted.get("username") or ""), str(decrypted.get("secret") or ""))


def validate_custom_organ_ast(source_code: str) -> ast.Module:
    """Strict AST verifier for synthesized Python micro-organs: pure logic + capability-pinned `channel` only."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("Orgaan-code mag niet leeg zijn")
    if len(source_code) > 24_576:
        raise ValueError("Orgaan-code overschrijdt de maximale grootte (24 KB)")
    tree = ast.parse(source_code, filename="<digital_organ>")
    has_probe = False
    disallowed_nodes = (
        ast.GeneratorExp,
        ast.Yield,
        ast.YieldFrom,
        ast.AsyncFunctionDef,
        ast.Await,
        ast.AsyncFor,
        ast.AsyncWith,
        ast.ClassDef,
        ast.Global,
        ast.Nonlocal,
    )
    for node in ast.walk(tree):
        if isinstance(node, disallowed_nodes):
            raise ValueError(f"Niet-toegestane Python-constructie in digitaal orgaan: {type(node).__name__}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "." in alias.name or alias.name not in SAFE_ORGAN_IMPORTS:
                    raise ValueError(f"Niet-toegestane import in digitaal orgaan: '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module or "." in node.module or node.module not in SAFE_ORGAN_IMPORTS:
                raise ValueError(f"Niet-toegestane from-import in digitaal orgaan: '{node.module}'")
            for alias in node.names:
                if alias.name == "*" or alias.name.startswith("_") or alias.name in FORBIDDEN_AST_ATTRS:
                    raise ValueError(f"Niet-toegestane symbool-import in digitaal orgaan: '{alias.name}'")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_AST_NAMES or node.id.startswith("__"):
                raise ValueError(f"Verboden identifier in digitaal orgaan: '{node.id}'")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in FORBIDDEN_AST_ATTRS:
                raise ValueError(f"Verboden attribuut in digitaal orgaan: '{node.attr}'")
        elif isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                raise ValueError("Decorators zijn niet toegestaan in digitale organen")
            if node.name == "organ_probe":
                has_probe = True
    if not has_probe:
        raise ValueError("Digitaal orgaan moet een functie `organ_probe(channel)` definiëren")
    return tree


class PinnedDeviceChannel:
    """Object-capability HTTP/HTTPS channel strictly locked to ONE approved private LAN IP + port + TLS mode."""

    def __init__(
        self,
        *,
        ip: str,
        default_port: int,
        use_tls: bool = False,
        auth_header: str | None = None,
        hue_app_key: str | None = None,
        timeout: float = 2.0,
    ) -> None:
        if not is_safe_lan_ipv4(ip, allow_docker_bridge=False):
            raise ValueError(f"Blocked non-LAN or Docker-internal target IP: {ip}")
        port_int = int(default_port)
        if isinstance(default_port, bool) or not 1 <= port_int <= 65535:
            raise ValueError("Ongeldige netwerkpoort voor apparaatkanaal")
        self._ip = ip
        self._default_port = port_int
        self._use_tls = bool(use_tls)
        self._timeout = min(max(float(timeout), 0.2), 5.0)
        # Keep secret headers inside a private closure so `self` never exposes secret attributes
        _bound_auth = str(auth_header) if auth_header else None
        _bound_hue = str(hue_app_key) if hue_app_key else None

        def _apply_secret_headers(target_headers: dict[str, str]) -> None:
            if _bound_auth:
                target_headers["Authorization"] = _bound_auth
            if _bound_hue:
                target_headers["hue-application-key"] = _bound_hue

        self._apply_secret_headers = _apply_secret_headers

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        clean_method = str(method or "").upper().strip()
        if clean_method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError("Alleen GET/POST/PUT/DELETE toegestaan op apparaatkanaal")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or "%2e" in path.lower()
            or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in path)
        ):
            raise ValueError("Ongeldig pad op apparaatkanaal")

        headers: dict[str, str] = {"Accept": "application/json", "Connection": "close"}
        self._apply_secret_headers(headers)
        payload_bytes: bytes | None = None
        if body is not None:
            payload_bytes = json.dumps(body, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self._use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self._ip, self._default_port, timeout=self._timeout, context=ctx
            )
        else:
            conn = http.client.HTTPConnection(self._ip, self._default_port, timeout=self._timeout)
        try:
            conn.request(clean_method, path, body=payload_bytes, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(262_144)
            status = resp.status
        except (ValueError, UnicodeError, http.client.HTTPException, OSError, ssl.SSLError) as exc:
            # Never propagate raw http.client exception messages (which could reflect header values)
            raise RuntimeError(
                f"Verbinding met apparaat op {self._ip}:{self._default_port} mislukt ({type(exc).__name__})"
            ) from None
        finally:
            conn.close()
        if status >= 400:
            raise RuntimeError(f"Apparaat op {self._ip}:{self._default_port} antwoordde met HTTP {status}")
        if not raw:
            return {"status": status}
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Apparaat gaf geen geldig JSON-antwoord terug") from exc


class LanProprioceptionScanner:
    """Bounded async multi-protocol home LAN scanner (SSDP, mDNS, HTTP/TLS, Cast, Hue, RTSP, ONVIF, Shelly, Tuya, HA, MQTT)."""

    PORTS_TO_PROBE: tuple[int, ...] = (80, 443, 554, 1400, 1883, 6668, 8000, 8008, 8009, 8080, 8123)

    def __init__(self, *, connect_timeout: float = 0.20, http_timeout: float = 0.55, concurrency: int = 160) -> None:
        self.connect_timeout = connect_timeout
        self.http_timeout = http_timeout
        self.concurrency = concurrency

    async def scan_subnets(
        self,
        subnets: list[str] | None = None,
        *,
        max_subnets: int = 2,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, Any]]:
        explicit_scope = subnets is not None
        target_subnets = subnets if subnets is not None else detect_default_lan_subnets()
        networks: list[ipaddress.IPv4Network] = []
        single_ips: list[str] = []
        local_self_ips = _local_interface_ips()

        for raw in target_subnets[: max(1, min(max_subnets, 3))]:
            raw = raw.strip()
            if not raw:
                continue
            if "/" in raw:
                networks.append(parse_safe_lan_subnet(raw))
            elif is_safe_lan_ipv4(raw, allow_docker_bridge=False):
                single_ips.append(raw)
            else:
                raise ValueError(f"Ongeldig of onveilig LAN-doel: {raw}")

        candidate_ips: list[str] = [ip for ip in dict.fromkeys(single_ips) if ip not in local_self_ips]
        for net in networks:
            for host_ip in net.hosts():
                ip_s = str(host_ip)
                if (
                    ip_s not in local_self_ips
                    and is_safe_lan_ipv4(ip_s, allow_docker_bridge=False)
                    and ip_s not in candidate_ips
                ):
                    candidate_ips.append(ip_s)

        multicast_hints = await asyncio.to_thread(self._collect_ssdp_hints)
        for hinted_ip in multicast_hints:
            if hinted_ip in local_self_ips or not is_safe_lan_ipv4(hinted_ip, allow_docker_bridge=False):
                continue
            if explicit_scope:
                addr_obj = ipaddress.IPv4Address(hinted_ip)
                if hinted_ip not in single_ips and not any(addr_obj in n for n in networks):
                    continue
            if hinted_ip not in candidate_ips:
                candidate_ips.append(hinted_ip)

        sem = asyncio.Semaphore(self.concurrency)
        discovered: list[dict[str, Any]] = []

        async def _scan_host(ip: str) -> list[dict[str, Any]]:
            if cancel_event is not None and cancel_event.is_set():
                return []
            open_ports: list[tuple[int, float]] = []
            for port in self.PORTS_TO_PROBE:
                if cancel_event is not None and cancel_event.is_set():
                    return []
                async with sem:
                    rtt_ms = await self._probe_tcp(ip, port)
                if rtt_ms is not None:
                    open_ports.append((port, rtt_ms))
            if not open_ports and ip not in multicast_hints:
                return []
            return await asyncio.to_thread(
                self._fingerprint_host,
                ip,
                dict(open_ports),
                multicast_hints.get(ip, {}),
            )

        results = await asyncio.gather(*(_scan_host(ip) for ip in candidate_ips[:512]))
        for host_items in results:
            discovered.extend(host_items)
        return discovered

    async def _probe_tcp(self, ip: str, port: int) -> float | None:
        t0 = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=self.connect_timeout)
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.1)
            except Exception:
                pass
            return round(rtt_ms, 2)
        except Exception:
            return None

    def _collect_ssdp_hints(self) -> dict[str, dict[str, str]]:
        hints: dict[str, dict[str, str]] = {}
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: ssdp:all\r\n\r\n"
        ).encode("ascii")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
                sock.settimeout(0.32)
                sock.sendto(msg, ("239.255.255.250", 1900))
                deadline = time.monotonic() + 0.35
                while time.monotonic() < deadline:
                    try:
                        data, (sender_ip, _) = sock.recvfrom(4096)
                    except OSError:
                        break
                    text = data.decode("utf-8", errors="ignore")
                    headers: dict[str, str] = {}
                    for line in text.splitlines()[1:]:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            headers[k.strip().lower()] = v.strip()
                    hints[sender_ip] = headers
        except OSError:
            pass
        return hints

    def _http_get(self, ip: str, port: int, path: str, *, tls: bool = False) -> tuple[int, dict[str, str], str] | None:
        try:
            if tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                    ip, port, timeout=self.http_timeout, context=ctx
                )
            else:
                conn = http.client.HTTPConnection(ip, port, timeout=self.http_timeout)
            try:
                conn.request("GET", path, headers={"User-Agent": "Eva2-LAN-Discovery/1.0", "Connection": "close"})
                resp = conn.getresponse()
                body = resp.read(16_384).decode("utf-8", errors="ignore")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                return resp.status, hdrs, body
            finally:
                conn.close()
        except Exception:
            return None

    def _probe_rtsp(self, ip: str, port: int = 554) -> tuple[bool, bool, str]:
        try:
            with socket.create_connection((ip, port), timeout=self.http_timeout) as sock:
                sock.sendall(f"OPTIONS rtsp://{ip}:{port}/ RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode("ascii"))
                reply = sock.recv(2048).decode("utf-8", errors="ignore")
            if reply.startswith("RTSP/1.0"):
                needs_auth = "401" in reply.splitlines()[0] or "www-authenticate" in reply.lower()
                server_line = ""
                for line in reply.splitlines():
                    if line.lower().startswith("server:"):
                        server_line = line.split(":", 1)[1].strip()
                        break
                return True, needs_auth, server_line
        except OSError:
            pass
        return False, True, ""

    def _fingerprint_host(
        self,
        ip: str,
        open_ports: dict[int, float],
        ssdp: dict[str, str],
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        min_rtt = min(open_ports.values()) if open_ports else 1.5
        ip_slug = ip.replace(".", "_")

        # 1. Home Assistant (:8123)
        if 8123 in open_ports:
            ha_manifest = self._http_get(ip, 8123, "/manifest.json")
            if ha_manifest and ha_manifest[0] == 200 and "Home Assistant" in ha_manifest[2]:
                found.append(
                    {
                        "device_id": f"ha_{ip_slug}",
                        "kind": "home_assistant",
                        "display_name": f"Home Assistant ({ip})",
                        "manufacturer": "Home Assistant",
                        "ip": ip,
                        "port": 8123,
                        "protocols": ["http", "websocket"],
                        "auth_requirement": "password_or_token",
                        "rtt_ms": open_ports[8123],
                        "capabilities": ["lights", "sensors", "todo_lists", "automations", "areas"],
                        "owner_prompt_nl": (
                            f"Ik heb Home Assistant gevonden op {ip}:8123. Zal ik hiermee koppelen "
                            "(met een Long-Lived Access Token in mijn kluis als die nog niet gekoppeld is)?"
                        ),
                    }
                )

        # 2. Philips Hue Bridge (:80 / :443 /api/config)
        if 80 in open_ports or 443 in open_ports:
            hue_resp = self._http_get(ip, 443 if 443 in open_ports else 80, "/api/config", tls=(443 in open_ports))
            if not hue_resp and 80 in open_ports:
                hue_resp = self._http_get(ip, 80, "/api/config", tls=False)
            if hue_resp and hue_resp[0] == 200 and "bridgeid" in hue_resp[2]:
                try:
                    cfg = json.loads(hue_resp[2])
                except json.JSONDecodeError:
                    cfg = {}
                bridge_id = _slugify(str(cfg.get("bridgeid") or ip_slug), max_len=24)
                name = _sanitize_lan_label(cfg.get("name"), fallback="Philips Hue Bridge", max_len=48)
                model_lbl = _sanitize_lan_label(cfg.get("modelid"), fallback="Hue Bridge", max_len=32)
                found.append(
                    {
                        "device_id": f"hue_{bridge_id}",
                        "kind": "philips_hue_bridge",
                        "display_name": f"{name} ({ip})",
                        "manufacturer": "Signify / Philips Hue",
                        "model": model_lbl,
                        "ip": ip,
                        "port": 443 if 443 in open_ports else 80,
                        "protocols": ["https" if 443 in open_ports else "http", "hue_clip_v2", "zigbee_lqi"],
                        "auth_requirement": "pairing_button",
                        "rtt_ms": min_rtt,
                        "capabilities": ["lights", "zigbee_motion_field", "scenes"],
                        "owner_prompt_nl": (
                            f"Ik heb een Philips Hue Bridge ({name}) op {ip} gevonden. "
                            "Druk één keer op de ronde knop bovenop de Hue Bridge en klik op 'Koppel' — "
                            "dan lees ik ook direct het Zigbee-bewegingsveld uit!"
                        ),
                    }
                )

            # 3. Shelly Smart Relay / Sensor (:80 /shelly)
            elif 80 in open_ports:
                shelly_resp = self._http_get(ip, 80, "/shelly")
                if shelly_resp and shelly_resp[0] == 200 and ('"mac"' in shelly_resp[2] or '"app"' in shelly_resp[2]):
                    try:
                        scfg = json.loads(shelly_resp[2])
                    except json.JSONDecodeError:
                        scfg = {}
                    mac_slug = _slugify(str(scfg.get("mac") or ip_slug), max_len=20)
                    auth_on = bool(scfg.get("auth") or scfg.get("auth_en"))
                    model = _sanitize_lan_label(scfg.get("app") or scfg.get("type"), fallback="Shelly", max_len=40)
                    found.append(
                        {
                            "device_id": f"shelly_{mac_slug}",
                            "kind": "shelly_iot",
                            "display_name": f"Shelly {model} ({ip})",
                            "manufacturer": "Shelly",
                            "model": model,
                            "ip": ip,
                            "port": 80,
                            "protocols": ["http", "shelly_rpc"],
                            "auth_requirement": "password_or_token" if auth_on else "none",
                            "rtt_ms": open_ports[80],
                            "capabilities": ["switch", "power_meter", "sensor"],
                            "owner_prompt_nl": (
                                f"Ik zie een Shelly-apparaat ({model}) op {ip}. Mag ik daarmee verbinden?"
                            ),
                        }
                    )

        # 4. Chromecast / Google TV / Nest Speaker (:8008 / :8009)
        if 8008 in open_ports or 8009 in open_ports:
            cast_name = f"Chromecast / Google TV ({ip})"
            cast_model = "Google Cast"
            if 8008 in open_ports:
                eureka = self._http_get(ip, 8008, "/setup/eureka_info")
                if eureka and eureka[0] == 200:
                    try:
                        edata = json.loads(eureka[2])
                        if edata.get("name"):
                            cast_lbl = _sanitize_lan_label(edata["name"], fallback="Chromecast", max_len=42)
                            cast_name = f"{cast_lbl} ({ip})"
                    except json.JSONDecodeError:
                        pass
            found.append(
                {
                    "device_id": f"cast_{ip_slug}",
                    "kind": "chromecast_google_tv",
                    "display_name": cast_name,
                    "manufacturer": "Google Cast",
                    "model": cast_model,
                    "ip": ip,
                    "port": 8009 if 8009 in open_ports else 8008,
                    "protocols": ["cast_v2", "http"],
                    "auth_requirement": "none",
                    "rtt_ms": open_ports.get(8009) or open_ports.get(8008) or min_rtt,
                    "capabilities": ["holostage_cast", "spoken_announcement", "visual_cards"],
                    "owner_prompt_nl": (
                        f"Ik zie {cast_name} op je netwerk. Mag ik die gebruiken om overzichten en mezelf "
                        "op het scherm te toveren wanneer je daarom vraagt?"
                    ),
                }
            )

        # 5. RTSP / ONVIF / Tuya / Lidl Camera (:554, :6668, :8000, :8080)
        if 554 in open_ports or 6668 in open_ports:
            is_rtsp, needs_auth, rtsp_srv = (
                self._probe_rtsp(ip, 554) if 554 in open_ports else (False, True, "")
            )
            is_tuya = 6668 in open_ports
            if is_rtsp or is_tuya:
                vendor = (
                    "Lidl / Tuya Smart"
                    if is_tuya
                    else _sanitize_lan_label(rtsp_srv, fallback="IP-Camera RTSP/ONVIF", max_len=32)
                )
                label = f"{vendor} ({ip})"
                found.append(
                    {
                        "device_id": f"cam_{ip_slug}",
                        "kind": "ip_camera_rtsp_onvif",
                        "display_name": label,
                        "manufacturer": vendor,
                        "ip": ip,
                        "port": 554 if is_rtsp else 6668,
                        "protocols": (["rtsp"] if is_rtsp else []) + (["tuya_lan"] if is_tuya else []),
                        "auth_requirement": "rtsp_credentials" if needs_auth else "none",
                        "rtt_ms": open_ports.get(554) or open_ports.get(6668) or min_rtt,
                        "capabilities": ["camera_snapshot", "motion_watch"],
                        "owner_prompt_nl": (
                            f"Ik heb een camera/apparaat ({label}) op het netwerk gevonden. "
                            + (
                                "Mag ik verbinden? Stuur het camerawachtwoord veilig met `/kluis cam_"
                                f"{ip_slug} <gebruiker>:<wachtwoord>` (buiten het taalmodel om)."
                                if needs_auth
                                else "Mag ik hiermee verbinden?"
                            )
                        ),
                    }
                )

        # 6. MQTT Broker (:1883 — Zigbee2MQTT / Mosquitto)
        if 1883 in open_ports:
            found.append(
                {
                    "device_id": f"mqtt_{ip_slug}",
                    "kind": "mqtt_broker",
                    "display_name": f"MQTT / Zigbee Broker ({ip}:1883)",
                    "manufacturer": "MQTT",
                    "ip": ip,
                    "port": 1883,
                    "protocols": ["mqtt"],
                    "auth_requirement": "password_or_token",
                    "rtt_ms": open_ports[1883],
                    "capabilities": ["zigbee_lqi_stream", "iot_events"],
                    "owner_prompt_nl": (
                        f"Ik zie een lokale MQTT-broker op {ip}:1883 (bijv. voor Zigbee/sensoren). "
                        "Mag ik daarmee koppelen?"
                    ),
                }
            )

        # 7. Sonos Speaker (:1400)
        if 1400 in open_ports:
            sonos_resp = self._http_get(ip, 1400, "/xml/device_description.xml")
            if sonos_resp and sonos_resp[0] == 200 and "sonos" in sonos_resp[2].lower():
                found.append(
                    {
                        "device_id": f"sonos_{ip_slug}",
                        "kind": "sonos_speaker",
                        "display_name": f"Sonos Speaker ({ip})",
                        "manufacturer": "Sonos",
                        "ip": ip,
                        "port": 1400,
                        "protocols": ["upnp", "http"],
                        "auth_requirement": "none",
                        "rtt_ms": open_ports[1400],
                        "capabilities": ["audio_announce", "media_control"],
                        "owner_prompt_nl": f"Ik zie een Sonos-speaker op {ip}. Mag ik daarmee koppelen?",
                    }
                )

        # 8. UniFi Gateway / Console / UniFi Connect Screen (:443 when not already Hue/HA)
        if not found and 443 in open_ports:
            unifi_resp = self._http_get(ip, 443, "/", tls=True)
            if unifi_resp:
                hdr_blob = json.dumps(unifi_resp[1]).lower() + unifi_resp[2][:1024].lower()
                if "unifi" in hdr_blob or "ubnt" in hdr_blob or ip.endswith(".1"):
                    found.append(
                        {
                            "device_id": f"unifi_{ip_slug}",
                            "kind": "unifi_network_console",
                            "display_name": f"UniFi Netwerk / Gateway ({ip})",
                            "manufacturer": "Ubiquiti UniFi",
                            "ip": ip,
                            "port": 443,
                            "protocols": ["https"],
                            "auth_requirement": "password_or_token",
                            "rtt_ms": open_ports[443],
                            "capabilities": ["wifi_presence_clients", "protect_cameras", "connect_display"],
                            "owner_prompt_nl": (
                                f"Ik zie je UniFi Gateway/Console op {ip}. Als ik leesrechten krijg via `/kluis`, "
                                "kan ik Wi-Fi-aanwezigheid en UniFi-schermen/camera's direct ondersteunen."
                            ),
                        }
                    )

        # 9. Generic SSDP smart device not caught above
        if not found and ssdp:
            srv = _sanitize_lan_label(
                ssdp.get("server") or ssdp.get("st"),
                fallback="Slim netwerkapparaat",
                max_len=40,
            )
            found.append(
                {
                    "device_id": f"lan_{ip_slug}",
                    "kind": "generic_lan_device",
                    "display_name": f"{srv} ({ip})",
                    "manufacturer": srv[:32],
                    "ip": ip,
                    "port": next(iter(open_ports.keys()), 80),
                    "protocols": ["ssdp"],
                    "auth_requirement": "none",
                    "rtt_ms": min_rtt,
                    "capabilities": ["custom_organ"],
                    "owner_prompt_nl": f"Ik zie een netwerkapparaat '{srv}' op {ip}. Zal ik het onderzoeken en koppelen?",
                }
            )

        return found


_ISOLATED_ORGAN_RUNNER_SCRIPT = """
import base64
import datetime
import hashlib
import hmac
import json
import math
import re
import resource
import sys
import types

try:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
except Exception:
    pass

_stdin = sys.stdin
_stdout = sys.stdout
_init_line = _stdin.readline(65536)
_payload = json.loads(_init_line)
_source = _payload["source_code"]

_SAFE_MODULES = {
    "json": types.SimpleNamespace(loads=json.loads, dumps=json.dumps),
    "math": types.SimpleNamespace(
        sqrt=math.sqrt, fabs=math.fabs, floor=math.floor, ceil=math.ceil,
        log=math.log, exp=math.exp, sin=math.sin, cos=math.cos, isfinite=math.isfinite,
    ),
    "re": types.SimpleNamespace(search=re.search, match=re.match, sub=re.sub, findall=re.findall),
    "base64": types.SimpleNamespace(b64encode=base64.b64encode, b64decode=base64.b64decode),
    "hashlib": types.SimpleNamespace(sha256=hashlib.sha256),
    "hmac": types.SimpleNamespace(compare_digest=hmac.compare_digest, new=hmac.new),
    "datetime": types.SimpleNamespace(datetime=datetime.datetime, timedelta=datetime.timedelta, timezone=datetime.timezone),
}

def _safe_import(name, globals_=None, locals_=None, fromlist=(), level=0):
    if level != 0 or name not in _SAFE_MODULES:
        raise ImportError(f"Blocked import: {name}")
    return _SAFE_MODULES[name]

class _RpcChannel:
    def __init__(self):
        self._calls = 0

    def request_json(self, method, path, body=None):
        self._calls += 1
        if self._calls > 4:
            raise RuntimeError("Maximaal 4 kanaalverzoeken per orgaan-puls toegestaan")
        _stdout.write(json.dumps({"rpc": "request_json", "method": str(method), "path": str(path), "body": body}) + "\\n")
        _stdout.flush()
        resp_line = _stdin.readline(262144)
        if not resp_line:
            raise RuntimeError("Kanaalverbinding gesloten")
        msg = json.loads(resp_line)
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        return msg.get("data")

_safe_builtins = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "Exception": Exception, "float": float,
    "int": int, "isinstance": isinstance, "len": len, "list": list,
    "max": max, "min": min, "range": range, "round": round,
    "RuntimeError": RuntimeError, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "ValueError": ValueError,
    "__import__": _safe_import,
}
_ns = {"__builtins__": _safe_builtins}
exec(compile(_source, "<digital_organ>", "exec"), _ns, _ns)
_fn = _ns.get("organ_probe")
if not callable(_fn):
    raise RuntimeError("`organ_probe` is niet aanroepbaar")
_res = _fn(_RpcChannel())
if not isinstance(_res, dict):
    raise ValueError("`organ_probe(channel)` moet een dict teruggeven")
_stdout.write(json.dumps({"rpc": "result", "data": _res}, allow_nan=False) + "\\n")
_stdout.flush()
"""


def _finite_float(val: Any, default: float = 0.0, *, lo: float = -1e6, hi: float = 1e6) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return min(hi, max(lo, f))


class HomeProprioceptionManager:
    """Unified Fase 2 manager: LAN discovery state, Pre-LLM Vault, Spatial Presence Field & Digital Organs."""

    _instance: HomeProprioceptionManager | None = None

    def __init__(self, directory: Path | str | None = None) -> None:
        base = Path(
            directory
            or os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
        )
        self.directory = base
        self.state_file = base / "home-proprioception-state.json"
        self.lock_file = base / ".home-proprioception.lock"
        self.vault = PreLlmCredentialVault(base)
        self.scanner = LanProprioceptionScanner()

    @classmethod
    def get(cls, directory: Path | str | None = None) -> HomeProprioceptionManager:
        if directory is not None:
            return cls(directory)
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_private_dir(self.directory)
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _default_state(self, now: datetime) -> dict[str, Any]:
        return {
            "contract": "EvaHomeProprioceptionState/v1",
            "revision": 1,
            "updated_at": _iso(now),
            "last_scan_at": None,
            "scanned_subnets": [],
            "devices": {},
            "rooms": {},
            "organs": {},
        }

    def _read_unlocked(self, now: datetime) -> dict[str, Any]:
        if not self.state_file.exists():
            return self._default_state(now)
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_state(now)
        if not isinstance(raw, dict) or raw.get("contract") != "EvaHomeProprioceptionState/v1":
            return self._default_state(now)
        for key in ("devices", "rooms", "organs"):
            if not isinstance(raw.get(key), dict):
                raw[key] = {}
        # Sanitize any legacy non-finite floats in rooms
        for r in raw["rooms"].values():
            if isinstance(r, dict):
                r["motion_energy"] = _finite_float(r.get("motion_energy", 0.0), 0.0, lo=0.0, hi=1.0)
                r["occupancy_probability"] = _finite_float(r.get("occupancy_probability", 0.0), 0.0, lo=0.0, hi=1.0)
                r["signal_variance"] = _finite_float(r.get("signal_variance", 0.0), 0.0, lo=0.0, hi=1_000_000.0)
        return raw

    def _write_unlocked(self, state: dict[str, Any], now: datetime) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        state["updated_at"] = _iso(now)
        _atomic_write_private_json(self.state_file, state)

    @staticmethod
    def _match_device_in_map(devices: dict[str, Any], needle_raw: str) -> dict[str, Any] | None:
        needle = (needle_raw or "").strip().lower()
        if not needle:
            return None
        exact = devices.get(needle)
        if isinstance(exact, dict):
            return exact
        matches = [
            d
            for k, d in devices.items()
            if isinstance(d, dict)
            and (k.endswith(needle) or str(d.get("ip") or "").lower() == needle or needle in k)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    # ------------------------------------------------------------------
    # 1. LAN Discovery & Consent Lifecycle
    # ------------------------------------------------------------------
    def run_lan_scan(
        self,
        *,
        subnets: list[str] | None = None,
        max_subnets: int = 2,
        discovered_override: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc_now(now)
        used_subnets = (subnets if subnets is not None else detect_default_lan_subnets())[: max(1, min(max_subnets, 3))]
        if discovered_override is not None:
            found = discovered_override
        else:
            cancel_ev = threading.Event()
            try:
                asyncio.get_running_loop()
                in_loop = True
            except RuntimeError:
                in_loop = False
            if not in_loop:
                found = asyncio.run(
                    self.scanner.scan_subnets(used_subnets, max_subnets=max_subnets, cancel_event=cancel_ev)
                )
            else:
                box: list[Any] = []
                err_box: list[BaseException] = []

                def _worker() -> None:
                    try:
                        box.append(
                            asyncio.run(
                                self.scanner.scan_subnets(
                                    used_subnets, max_subnets=max_subnets, cancel_event=cancel_ev
                                )
                            )
                        )
                    except BaseException as exc:  # noqa: BLE001
                        err_box.append(exc)

                t = threading.Thread(target=_worker, daemon=True)
                t.start()
                t.join(timeout=16.0)
                if t.is_alive():
                    cancel_ev.set()
                    raise RuntimeError("Netwerkscan duurde te lang; specificeer één subnet (bijv. 10.0.20.0/24).")
                if err_box:
                    raise err_box[0]
                found = box[0] if box else []

        with self._locked():
            state = self._read_unlocked(current)
            devices = state["devices"]
            now_str = _iso(current)

            for item in found:
                ip_val = str(item.get("ip") or "").strip()
                if not is_safe_lan_ipv4(ip_val, allow_docker_bridge=False):
                    continue
                port_val = int(item.get("port") or 80)
                if not 1 <= port_val <= 65535:
                    continue
                dev_id = _slugify(str(item.get("device_id") or ip_val), max_len=60)
                if not DEVICE_ID_PATTERN.fullmatch(dev_id):
                    continue

                existing = devices.get(dev_id)
                room_id = (
                    existing.get("room_id")
                    if isinstance(existing, dict) and existing.get("room_id")
                    else infer_room_from_label(str(item.get("display_name") or ""))
                )
                protocols = [str(p) for p in (item.get("protocols") or [])[:8]]
                item_has_tls = "https" in protocols or port_val in (443, 8443)

                consent_status: DeviceConsentStatus = (
                    existing["consent_status"]
                    if isinstance(existing, dict)
                    and existing.get("consent_status") in ("pending_consent", "approved", "ignored")
                    else "pending_consent"
                )
                approved_ip = existing.get("approved_ip") if isinstance(existing, dict) else None
                approved_port = existing.get("approved_port") if isinstance(existing, dict) else None
                approved_tls = bool(existing.get("approved_tls", False)) if isinstance(existing, dict) else False

                # C-02 Anti-Spoofing & Anti-Downgrade: if an approved device ID suddenly responds from a different
                # IP, different port, or drops TLS -> plaintext HTTP, DO NOT carry over approval or overwrite the
                # pinned endpoint silently; demote to pending_consent and deactivate the organ until owner re-approves.
                if consent_status == "approved" and isinstance(existing, dict):
                    pinned_ip = approved_ip or existing.get("ip")
                    pinned_port = approved_port or existing.get("port")
                    if (
                        ip_val != pinned_ip
                        or port_val != pinned_port
                        or (approved_tls and not item_has_tls)
                    ):
                        consent_status = "pending_consent"
                        approved_ip = None
                        approved_port = None
                        approved_tls = False
                        state["organs"].pop(dev_id, None)

                prev_rtt = _finite_float(
                    existing.get("rtt_ms", item.get("rtt_ms", 1.0)) if isinstance(existing, dict) else item.get("rtt_ms", 1.0),
                    1.0,
                    lo=0.0,
                    hi=30_000.0,
                )
                new_rtt = _finite_float(item.get("rtt_ms", 1.0), 1.0, lo=0.0, hi=30_000.0)

                devices[dev_id] = {
                    "device_id": dev_id,
                    "kind": _slugify(str(item.get("kind") or "generic_lan_device"), max_len=36),
                    "display_name": _sanitize_lan_label(item.get("display_name"), fallback=dev_id, max_len=64),
                    "manufacturer": _sanitize_lan_label(item.get("manufacturer"), fallback="Onbekend", max_len=40),
                    "model": _sanitize_lan_label(item.get("model"), fallback="", max_len=40) if item.get("model") else "",
                    "ip": ip_val,
                    "port": port_val,
                    "protocols": protocols,
                    "capabilities": [str(c) for c in (item.get("capabilities") or [])[:12]],
                    "auth_requirement": str(item.get("auth_requirement") or "none"),
                    "room_id": room_id,
                    "consent_status": consent_status,
                    "approved_ip": approved_ip,
                    "approved_port": approved_port,
                    "approved_tls": approved_tls,
                    "rtt_ms": round(new_rtt, 2),
                    "rtt_jitter_ms": round(abs(new_rtt - prev_rtt), 3),
                    "owner_prompt_nl": str(item.get("owner_prompt_nl") or "")[:320],
                    "first_seen_at": existing.get("first_seen_at", now_str) if isinstance(existing, dict) else now_str,
                    "last_seen_at": now_str,
                }
                if consent_status == "approved":
                    self._ingest_signal_unlocked(
                        state,
                        room_id=room_id,
                        source_id=dev_id,
                        metric_kind="wifi_rtt_ms",
                        value=new_rtt,
                        now=current,
                    )

            state["last_scan_at"] = now_str
            state["scanned_subnets"] = list(used_subnets)
            self._write_unlocked(state, current)

        self._sync_pulse_from_presence(now=current)
        return self.status(now=current)

    def set_device_consent(
        self,
        *,
        device_id: str,
        consent_status: DeviceConsentStatus,
        room_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc_now(now)
        if consent_status not in ("pending_consent", "approved", "ignored"):
            raise ValueError("Ongeldige toestemmingsstatus")
        clean_room = _slugify(room_id, max_len=36) if room_id else None

        with self._locked():
            state = self._read_unlocked(current)
            target = self._match_device_in_map(state["devices"], device_id)
            if target is None:
                raise ValueError(f"Apparaat '{device_id}' niet gevonden. Gebruik eerst /netwerk om te scannen.")
            dev_id = target["device_id"]
            target["consent_status"] = consent_status
            if clean_room:
                target["room_id"] = clean_room
            if consent_status == "approved":
                target["approved_ip"] = target["ip"]
                target["approved_port"] = int(target["port"])
                target["approved_tls"] = (
                    "https" in (target.get("protocols") or []) or int(target["port"]) in (443, 8443)
                )
                self._ensure_builtin_organ_unlocked(state, target, now=current)
            else:
                target["approved_ip"] = None
                target["approved_port"] = None
                target["approved_tls"] = False
                if consent_status == "ignored":
                    state["organs"].pop(dev_id, None)
            self._write_unlocked(state, current)

        st = self.status(now=current)
        st["matched_device_id"] = dev_id
        return st

    def store_device_secret(
        self,
        *,
        device_ref: str,
        raw_secret: str,
        username: str = "",
        credential_kind: str = "password_or_token",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve device by ID/IP/suffix, split `user:password` ONLY for RTSP credentials, and bind TLS requirement."""
        current = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(current)
            matched = self._match_device_in_map(state["devices"], device_ref)

        if matched is not None:
            resolved_id = str(matched["device_id"])
            is_rtsp = matched.get("auth_requirement") == "rtsp_credentials" or matched.get("kind") == "ip_camera_rtsp_onvif"
            require_tls = "https" in (matched.get("protocols") or []) or int(matched.get("port") or 80) in (443, 8443)
            eff_kind = "rtsp_credentials" if is_rtsp and credential_kind == "password_or_token" else credential_kind
        else:
            cand = (device_ref or "").strip().lower()
            if not DEVICE_ID_PATTERN.fullmatch(cand):
                raise ValueError(
                    f"Onbekend apparaat '{device_ref}'. Scan eerst met /netwerk of geef een geldig apparaat-ID op."
                )
            resolved_id = cand
            is_rtsp = resolved_id.startswith("cam_")
            require_tls = False
            eff_kind = credential_kind

        user_out = username
        secret_out = raw_secret
        if (
            not user_out
            and is_rtsp
            and ":" in raw_secret
            and not raw_secret.startswith(("rtsp://", "rtsps://", "http://", "https://"))
        ):
            user_out, secret_out = raw_secret.split(":", 1)

        return self.vault.store_secret(
            device_id=resolved_id,
            secret_value=secret_out,
            username=user_out,
            credential_kind=eff_kind,
            require_tls=require_tls,
            now=current,
        )

    def assign_device_room(self, *, device_id: str, room_id: str, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now(now)
        clean_room = _slugify(room_id, max_len=36)
        if not ROOM_ID_PATTERN.fullmatch(clean_room):
            raise ValueError("Ongeldige kamernaam")
        with self._locked():
            state = self._read_unlocked(current)
            target = self._match_device_in_map(state["devices"], device_id)
            if target is None:
                raise ValueError(f"Apparaat '{device_id}' niet gevonden")
            target["room_id"] = clean_room
            self._write_unlocked(state, current)
        return self.status(now=current)

    # ------------------------------------------------------------------
    # 2. Spatial Presence Field (0-token RF / LQI / RTT Room Motion)
    # ------------------------------------------------------------------
    def ingest_signal_sample(
        self,
        *,
        room_id: str,
        source_id: str,
        metric_kind: Literal["zigbee_lqi", "wifi_rtt_ms", "motion_binary", "light_state"],
        value: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("Signaalwaarde moet een eindig getal zijn")
        f_val = float(value)
        if not -500.0 <= f_val <= 100_000.0:
            raise ValueError("Signaalwaarde valt buiten het toegestane sensorbereik (-500..100000)")
        current = _utc_now(now)
        clean_room = _slugify(room_id, max_len=36)
        clean_source = _slugify(source_id, max_len=48)
        with self._locked():
            state = self._read_unlocked(current)
            room_snap = self._ingest_signal_unlocked(
                state,
                room_id=clean_room,
                source_id=clean_source,
                metric_kind=metric_kind,
                value=f_val,
                now=current,
            )
            self._write_unlocked(state, current)
        self._sync_pulse_from_presence(now=current)
        return room_snap

    def _decay_room_unlocked(self, room: dict[str, Any], now: datetime) -> dict[str, Any]:
        updated_str = str(room.get("updated_at") or _iso(now))
        try:
            dt_sec = max(0.0, (now - _parse_iso(updated_str)).total_seconds())
        except ValueError:
            dt_sec = 0.0
        # Exponential decay: motion fades with tau=90s, occupancy probability with tau=240s
        motion_decay = math.exp(-min(dt_sec, 86_400.0) / 90.0)
        occ_decay = math.exp(-min(dt_sec, 86_400.0) / 240.0)
        motion = round(_finite_float(room.get("motion_energy", 0.0), 0.0, lo=0.0, hi=1.0) * motion_decay, 4)
        occ = round(_finite_float(room.get("occupancy_probability", 0.0), 0.0, lo=0.0, hi=1.0) * occ_decay, 4)
        return {
            **room,
            "motion_energy": 0.0 if motion < 0.01 else min(1.0, motion),
            "occupancy_probability": 0.0 if occ < 0.01 else min(1.0, occ),
        }

    def _ingest_signal_unlocked(
        self,
        state: dict[str, Any],
        *,
        room_id: str,
        source_id: str,
        metric_kind: str,
        value: float,
        now: datetime,
    ) -> dict[str, Any]:
        f_val = _finite_float(value, 0.0, lo=-500.0, hi=100_000.0)
        rooms = state["rooms"]
        raw_room = rooms.get(
            room_id,
            {
                "room_id": room_id,
                "occupancy_probability": 0.0,
                "motion_energy": 0.0,
                "signal_variance": 0.0,
                "sources": {},
                "last_motion_at": None,
                "updated_at": _iso(now),
            },
        )
        decayed = self._decay_room_unlocked(raw_room, now)
        sources: dict[str, Any] = dict(decayed.get("sources") or {})
        src_key = f"{metric_kind}:{source_id}"
        prev = sources.get(src_key)

        impulse = 0.0
        variance = _finite_float(decayed.get("signal_variance", 0.0), 0.0, lo=0.0, hi=1_000_000.0) * 0.85

        if metric_kind == "motion_binary":
            if f_val >= 0.5:
                impulse = 0.92
        elif metric_kind == "light_state":
            if prev is not None and abs(f_val - _finite_float(prev.get("last_value", f_val), f_val)) >= 0.5:
                impulse = 0.78
        elif metric_kind == "zigbee_lqi":
            # Hue / Zigbee LQI (0..255) or RSSI (-100..-20 dBm): human body crossing Fresnel zone causes LQI ripple >= 4.5
            if prev is not None:
                mean = _finite_float(prev.get("ema", f_val), f_val)
                delta = min(1_000.0, abs(f_val - mean))
                ema = mean + 0.25 * (f_val - mean)
                prev_var = _finite_float(prev.get("var", 0.0), 0.0, lo=0.0, hi=1_000_000.0)
                variance = min(1_000_000.0, round(0.75 * prev_var + 0.25 * (delta * delta), 4))
                if delta >= 4.5:
                    impulse = min(0.88, 0.25 + (delta - 4.5) * 0.055)
            else:
                ema = f_val
            sources[src_key] = {"last_value": f_val, "ema": round(ema, 3), "var": variance, "seen_at": _iso(now)}
        elif metric_kind == "wifi_rtt_ms":
            if prev is not None:
                mean = _finite_float(prev.get("ema", f_val), f_val)
                delta = min(1_000.0, abs(f_val - mean))
                ema = mean + 0.3 * (f_val - mean)
                prev_var = _finite_float(prev.get("var", 0.0), 0.0, lo=0.0, hi=1_000_000.0)
                variance = min(1_000_000.0, round(0.75 * prev_var + 0.25 * (delta * delta), 4))
                if delta >= 3.5:
                    impulse = min(0.65, 0.18 + (delta - 3.5) * 0.04)
            else:
                ema = f_val
            sources[src_key] = {"last_value": f_val, "ema": round(ema, 3), "var": variance, "seen_at": _iso(now)}

        if metric_kind in ("motion_binary", "light_state"):
            sources[src_key] = {"last_value": f_val, "ema": f_val, "var": round(variance, 4), "seen_at": _iso(now)}

        # Trim source history per room to max 24 sensors
        if len(sources) > 24:
            sorted_keys = sorted(sources.keys(), key=lambda k: sources[k].get("seen_at", ""), reverse=True)[:24]
            sources = {k: sources[k] for k in sorted_keys}

        motion = min(1.0, round(max(float(decayed["motion_energy"]), impulse), 4))
        occ = min(
            1.0,
            round(
                max(float(decayed["occupancy_probability"]), min(0.98, impulse * 1.05)),
                4,
            ),
        )
        now_str = _iso(now)
        room_record = {
            "room_id": room_id,
            "occupancy_probability": occ,
            "motion_energy": motion,
            "signal_variance": round(min(1_000_000.0, variance), 4),
            "sources": sources,
            "active_modalities": sorted({k.split(":", 1)[0] for k in sources}),
            "last_motion_at": now_str if impulse >= 0.20 else decayed.get("last_motion_at"),
            "updated_at": now_str,
        }
        rooms[room_id] = room_record
        if len(rooms) > 32:
            keep_rooms = sorted(rooms.keys(), key=lambda rk: str(rooms[rk].get("updated_at") or ""), reverse=True)[:32]
            state["rooms"] = {rk: rooms[rk] for rk in keep_rooms}
        return room_record

    def _sync_pulse_from_presence(self, *, now: datetime) -> None:
        """Feed zero-token household motion energy directly into Eva's C/SIMD Liquid ODE (`dx/dt`)."""
        try:
            st = self.status(now=now)
            rooms = st.get("rooms") or []
            peak_motion = max((float(r.get("motion_energy", 0.0)) for r in rooms), default=0.0)
            pending_count = sum(1 for d in st.get("devices", []) if d.get("consent_status") == "pending_consent")
            from eva.application.living_entity_pulse import LivingEntityPulseEngine

            engine = LivingEntityPulseEngine.get()
            engine.evolve(
                now=now,
                sensory_input={
                    "arousal": min(1.0, 0.30 + 0.55 * peak_motion),
                    "epistemic_curiosity": min(1.0, 0.35 + (0.25 if pending_count > 0 else 0.0) + 0.20 * peak_motion),
                    "anticipatory_drive": min(1.0, 0.25 + 0.50 * peak_motion),
                    "calm_stability": max(0.20, 0.85 - 0.35 * peak_motion),
                },
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 3. Digital Organogenesis (Built-in & Sandboxed Custom Micro-Drivers)
    # ------------------------------------------------------------------
    def _ensure_builtin_organ_unlocked(
        self,
        state: dict[str, Any],
        device: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        dev_id = device["device_id"]
        existing = state["organs"].get(dev_id)
        if isinstance(existing, dict) and existing.get("organ_type") == "custom_sandbox":
            return existing
        organ = {
            "organ_id": f"organ_{dev_id}",
            "device_id": dev_id,
            "organ_type": "native_builtin",
            "driver_kind": device["kind"],
            "status": "ready",
            "code_sha256": hashlib.sha256(f"native:{device['kind']}:v1".encode("utf-8")).hexdigest(),
            "last_checked_at": _iso(now),
            "last_result_summary": f"Native micro-driver gereed voor {device['display_name']}",
        }
        state["organs"][dev_id] = organ
        return organ

    def pair_philips_hue_bridge(self, device_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """One-click Hue Bridge link-button pairing: obtains client key and stores it directly in Pre-LLM Vault."""
        current = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(current)
            dev = self._match_device_in_map(state["devices"], device_id)
            if not isinstance(dev, dict) or dev.get("kind") != "philips_hue_bridge":
                raise ValueError("Geen Philips Hue Bridge gevonden met dit apparaat-ID")
            if dev.get("consent_status") == "ignored":
                raise PermissionError("Deze Hue Bridge staat op genegeerd; keur eerst goed via /koppel of dashboard.")
            dev_id = str(dev["device_id"])
            ip = str(dev["ip"])
            port = int(dev.get("port") or 443)
            use_tls = "https" in (dev.get("protocols") or []) or port in (443, 8443)

        chan = PinnedDeviceChannel(ip=ip, default_port=port, use_tls=use_tls, timeout=3.0)
        try:
            resp = chan.request_json("POST", "/api", body={"devicetype": "eva2#gezinshoofd"})
        except (OSError, http.client.HTTPException, RuntimeError) as exc:
            raise RuntimeError(f"Hue Bridge op {ip}:{port} niet bereikbaar tijdens koppelen ({type(exc).__name__})") from exc

        if isinstance(resp, list) and resp:
            first = resp[0]
            if isinstance(first, dict) and isinstance(first.get("error"), dict):
                desc = _sanitize_lan_label(str(first["error"].get("description") or "link button not pressed"), max_len=80)
                raise ValueError(
                    f"Hue Bridge wacht op een druk op de fysieke knop bovenop de bridge ({desc}). "
                    "Druk één keer op de knop en probeer het binnen 30 seconden opnieuw."
                )
            if isinstance(first, dict) and isinstance(first.get("success"), dict):
                app_key = str(first["success"].get("username") or "").strip()
                if app_key:
                    with self._locked():
                        state = self._read_unlocked(current)
                        cur_dev = state["devices"].get(dev_id)
                        if isinstance(cur_dev, dict):
                            cur_dev["consent_status"] = "approved"
                            cur_dev["approved_ip"] = ip
                            cur_dev["approved_port"] = port
                            cur_dev["approved_tls"] = use_tls
                            self._ensure_builtin_organ_unlocked(state, cur_dev, now=current)
                            self._write_unlocked(state, current)
                    self.vault.store_secret(
                        device_id=dev_id,
                        secret_value=app_key,
                        credential_kind="hue_application_key",
                        require_tls=use_tls,
                        now=current,
                    )
                    return {"paired": True, "device_id": dev_id, "message": "Hue Bridge succesvol gekoppeld in kluis!"}
        raise ValueError("Onverwacht antwoord van Hue Bridge tijdens koppelen")

    def register_custom_organ(
        self,
        *,
        device_id: str,
        source_code: str,
        run_live_probe: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Validate, sandbox-compile, integrity-pin, and register a custom Python micro-organ for an approved device."""
        current = _utc_now(now)
        dev_id = device_id.strip().lower()
        validate_custom_organ_ast(source_code)
        code_sha256 = hashlib.sha256(source_code.encode("utf-8")).hexdigest()

        with self._locked():
            state = self._read_unlocked(current)
            dev = state["devices"].get(dev_id)
            if not isinstance(dev, dict):
                raise ValueError(f"Apparaat '{dev_id}' niet gevonden")
            if dev.get("consent_status") != "approved":
                raise PermissionError("Kan geen digitaal orgaan activeren zonder toestemming van het gezinshoofd")
            dev_snapshot = dict(dev)

        summary = "AST-veiligheidspoort geslaagd en SHA-256 vastgelegd"
        if run_live_probe:
            probe_out = self._execute_custom_organ_source(dev_snapshot, source_code)
            summary = _sanitize_lan_label(str(probe_out.get("summary") or "Live probe geslaagd"), max_len=160)

        with self._locked():
            state = self._read_unlocked(current)
            dev_recheck = state["devices"].get(dev_id)
            if not isinstance(dev_recheck, dict) or dev_recheck.get("consent_status") != "approved":
                raise PermissionError("Toestemming voor apparaat is ingetrokken tijdens orgaanregistratie")
            organ = {
                "organ_id": f"organ_{dev_id}",
                "device_id": dev_id,
                "organ_type": "custom_sandbox",
                "driver_kind": dev_recheck["kind"],
                "status": "ready",
                "source_code": source_code,
                "code_sha256": code_sha256,
                "last_checked_at": _iso(current),
                "last_result_summary": summary,
            }
            state["organs"][dev_id] = organ
            self._write_unlocked(state, current)
        return organ

    def _build_pinned_channel_for_device(self, dev: dict[str, Any]) -> PinnedDeviceChannel:
        dev_id = str(dev["device_id"])
        ip = str(dev["ip"])
        port = int(dev.get("port") or 80)
        use_tls = "https" in (dev.get("protocols") or []) or port in (443, 8443)
        approved_ip = dev.get("approved_ip")
        if approved_ip and ip != approved_ip:
            raise PermissionError("Apparaat-IP wijkt af van het goedgekeurde IP-adres; herkoppel vereist.")
        approved_tls = bool(dev.get("approved_tls", False))
        if approved_tls and not use_tls:
            raise PermissionError("TLS-verplichting voor dit apparaat geschonden; herkoppel vereist.")

        auth_header: str | None = None
        hue_key: str | None = None

        vault_meta = self.vault.list_metadata().get(dev_id)
        if vault_meta:
            if vault_meta.get("require_tls") and not use_tls:
                raise PermissionError("TLS-verplichting voor dit kluisgeheim geschonden; platte HTTP geweigerd.")

            def _extract(user: str, secret: str) -> tuple[str | None, str | None]:
                kind = vault_meta.get("credential_kind", "")
                if kind == "hue_application_key":
                    return None, secret
                if user:
                    b64 = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
                    return f"Basic {b64}", None
                if ":" in secret and dev.get("kind") == "ip_camera_rtsp_onvif":
                    b64 = base64.b64encode(secret.encode("utf-8")).decode("ascii")
                    return f"Basic {b64}", None
                return f"Bearer {secret}", None

            auth_header, hue_key = self.vault.use_secret_for_approved_device(
                device_id=dev_id,
                is_device_approved=(dev.get("consent_status") == "approved"),
                callback=_extract,
            )

        return PinnedDeviceChannel(
            ip=ip,
            default_port=port,
            use_tls=use_tls,
            auth_header=auth_header,
            hue_app_key=hue_key,
        )

    @staticmethod
    def _read_pipe_line_with_deadline(fd: int, buf: bytearray, deadline: float, max_bytes: int = 262_144) -> bytes:
        while True:
            nl_idx = buf.find(b"\n")
            if nl_idx != -1:
                line = bytes(buf[: nl_idx + 1])
                del buf[: nl_idx + 1]
                return line
            if len(buf) >= max_bytes:
                raise ValueError("Uitvoer van digitaal orgaan overschrijdt de maximale berichtgrootte")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Digitaal orgaan overschreed de maximale uitvoertijd (2.5s)")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError("Digitaal orgaan overschreed de maximale uitvoertijd (2.5s)")
            chunk = os.read(fd, min(16_384, max_bytes - len(buf) + 1))
            if not chunk:
                line = bytes(buf)
                buf.clear()
                return line
            buf.extend(chunk)

    def _execute_custom_organ_source(self, dev: dict[str, Any], source_code: str) -> dict[str, Any]:
        validate_custom_organ_ast(source_code)
        channel = self._build_pinned_channel_for_device(dev)

        proc = subprocess.Popen(  # noqa: S603 - isolated python3 -I subprocess with empty env & rlimits
            [sys.executable, "-I", "-c", _ISOLATED_ORGAN_RUNNER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={},
            cwd="/tmp",
        )
        deadline = time.monotonic() + 2.5
        out_buf = bytearray()
        rpc_calls = 0
        try:
            assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
            init_bytes = (json.dumps({"source_code": source_code}, allow_nan=False) + "\n").encode("utf-8")
            proc.stdin.write(init_bytes)
            proc.stdin.flush()

            stdout_fd = proc.stdout.fileno()
            while True:
                raw_line = self._read_pipe_line_with_deadline(stdout_fd, out_buf, deadline)
                if not raw_line:
                    err_bytes = proc.stderr.read(4096) or b""
                    try:
                        rc = proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        rc = None
                    if rc is not None and rc < 0:
                        raise TimeoutError("Digitaal orgaan overschreed de maximale CPU-/uitvoertijd in isolatieproces")
                    err_tail = err_bytes.decode("utf-8", errors="replace").strip().splitlines()
                    reason = _sanitize_lan_label(err_tail[-1] if err_tail else "onbekende fout", max_len=180)
                    raise RuntimeError(f"Digitaal orgaan gestopt in isolatieproces: {reason}")
                msg = json.loads(raw_line.decode("utf-8", errors="strict"))
                if not isinstance(msg, dict):
                    raise ValueError("Ongeldig RPC-antwoord uit digitaal orgaan")
                rpc_kind = msg.get("rpc")
                if rpc_kind == "request_json":
                    rpc_calls += 1
                    if rpc_calls > 4:
                        raise RuntimeError("Maximaal 4 kanaalverzoeken per orgaan-puls toegestaan")
                    try:
                        res_data = channel.request_json(
                            str(msg.get("method") or "GET"),
                            str(msg.get("path") or "/"),
                            body=msg.get("body"),
                        )
                        reply = json.dumps({"data": res_data}, allow_nan=False) + "\n"
                    except Exception as exc:  # noqa: BLE001
                        reply = json.dumps({"error": _sanitize_lan_label(str(exc), max_len=180)}) + "\n"
                    proc.stdin.write(reply.encode("utf-8"))
                    proc.stdin.flush()
                elif rpc_kind == "result":
                    out = msg.get("data")
                    if not isinstance(out, dict):
                        raise ValueError("`organ_probe(channel)` moet een dict teruggeven")
                    return out
                else:
                    raise ValueError("Onbekend RPC-commando uit digitaal orgaan")
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def poll_organ(self, device_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Run a fast health & telemetry pulse on one approved device organ and feed Spatial Presence Field."""
        current = _utc_now(now)
        dev_id = device_id.strip().lower()
        with self._locked():
            state = self._read_unlocked(current)
            dev = state["devices"].get(dev_id)
            if not isinstance(dev, dict):
                raise ValueError(f"Apparaat '{dev_id}' niet gevonden")
            if dev.get("consent_status") != "approved":
                raise PermissionError(f"Apparaat '{dev_id}' is nog niet goedgekeurd door het gezinshoofd")
            organ = state["organs"].get(dev_id)
            if not isinstance(organ, dict):
                organ = self._ensure_builtin_organ_unlocked(state, dev, now=current)
                self._write_unlocked(state, current)
            dev_snapshot = dict(dev)
            organ_snapshot = dict(organ)

        ip_str = str(dev_snapshot.get("ip") or "")
        if not is_safe_lan_ipv4(ip_str, allow_docker_bridge=False):
            raise ValueError(f"Onveilig apparaat-IP '{ip_str}' geweigerd tijdens orgaan-puls")

        t0 = time.perf_counter()
        room_id = str(dev_snapshot.get("room_id") or "algemeen")
        signals_ingested = 0

        if organ_snapshot.get("organ_type") == "custom_sandbox":
            src = str(organ_snapshot.get("source_code") or "")
            if hashlib.sha256(src.encode("utf-8")).hexdigest() != organ_snapshot.get("code_sha256"):
                raise RuntimeError("SHA-256 integriteitscontrole van digitaal orgaan gefaald")
            result = self._execute_custom_organ_source(dev_snapshot, src)
            summary = _sanitize_lan_label(str(result.get("summary") or "Custom orgaan OK"), max_len=160)
            lqi_samples = result.get("zigbee_lqi_samples") or []
            for sample in lqi_samples[:32]:
                if isinstance(sample, dict) and isinstance(sample.get("lqi"), (int, float)) and not isinstance(sample.get("lqi"), bool):
                    self.ingest_signal_sample(
                        room_id=str(sample.get("room_id") or room_id),
                        source_id=str(sample.get("id") or dev_id),
                        metric_kind="zigbee_lqi",
                        value=float(sample["lqi"]),
                        now=current,
                    )
                    signals_ingested += 1
        else:
            channel = self._build_pinned_channel_for_device(dev_snapshot)
            kind = dev_snapshot.get("kind")
            try:
                if kind == "philips_hue_bridge":
                    try:
                        zigbee = channel.request_json("GET", "/clip/v2/resource/zigbee_connectivity")
                        nodes = zigbee.get("data") or []
                        for item in nodes[:64]:
                            if not isinstance(item, dict):
                                continue
                            sig = item.get("signal_strength")
                            if sig is None:
                                sig = item.get("lqi")
                            if sig is None:
                                sig = item.get("rssi")
                            if isinstance(sig, (int, float)) and not isinstance(sig, bool):
                                self.ingest_signal_sample(
                                    room_id=room_id,
                                    source_id=str(item.get("id") or dev_id)[:36],
                                    metric_kind="zigbee_lqi",
                                    value=float(sig),
                                    now=current,
                                )
                                signals_ingested += 1
                        summary = f"Hue Bridge v2 actief ({len(nodes)} Zigbee-knooppunten)"
                    except Exception:
                        cfg = channel.request_json("GET", "/api/config")
                        hue_name = _sanitize_lan_label(str(cfg.get("name") or "Hue"), max_len=40)
                        summary = f"Hue Bridge '{hue_name}' bereikbaar (gebruik knop-koppeling voor Zigbee-data)"
                elif kind == "shelly_iot":
                    info = channel.request_json("GET", "/shelly")
                    sh_name = _sanitize_lan_label(str(info.get("type") or info.get("app") or "OK"), max_len=40)
                    summary = f"Shelly ({sh_name}) actief"
                elif kind == "home_assistant":
                    manifest = channel.request_json("GET", "/manifest.json")
                    ha_name = _sanitize_lan_label(str(manifest.get("name") or "actief"), max_len=40)
                    summary = f"Home Assistant ({ha_name}) bereikbaar"
                else:
                    port_num = int(dev_snapshot.get("port") or 80)
                    with socket.create_connection((ip_str, port_num), timeout=1.2):
                        pass
                    summary = f"{_sanitize_lan_label(str(dev_snapshot.get('display_name') or dev_id), max_len=60)} bereikbaar"
            except (OSError, TimeoutError, http.client.HTTPException) as exc:
                raise RuntimeError(
                    f"Apparaat '{dev_id}' op {ip_str} reageert momenteel niet ({type(exc).__name__})"
                ) from exc

        summary = _sanitize_lan_label(summary, max_len=160)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        self.ingest_signal_sample(
            room_id=room_id,
            source_id=dev_id,
            metric_kind="wifi_rtt_ms",
            value=elapsed_ms,
            now=current,
        )
        signals_ingested += 1

        with self._locked():
            state = self._read_unlocked(current)
            cur_dev = state["devices"].get(dev_id)
            if isinstance(cur_dev, dict) and cur_dev.get("consent_status") == "approved":
                if dev_id not in state["organs"]:
                    self._ensure_builtin_organ_unlocked(state, cur_dev, now=current)
                state["organs"][dev_id]["status"] = "ready"
                state["organs"][dev_id]["last_checked_at"] = _iso(current)
                state["organs"][dev_id]["last_latency_ms"] = elapsed_ms
                state["organs"][dev_id]["last_result_summary"] = summary
                self._write_unlocked(state, current)

        return {
            "device_id": dev_id,
            "status": "ready",
            "latency_ms": elapsed_ms,
            "signals_ingested": signals_ingested,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # 4. Safe Read-Only Snapshots & Household Turn Awareness (0 Secrets!)
    # ------------------------------------------------------------------
    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now(now)
        if not self.state_file.exists():
            vault_meta = self.vault.list_metadata()
            return {
                "contract": "EvaHomeProprioceptionStatus/v1",
                "revision": 1,
                "last_scan_at": None,
                "scanned_subnets": detect_default_lan_subnets(),
                "devices": [],
                "rooms": [],
                "organs": [],
                "vault_entries_count": len(vault_meta),
            }

        with self._locked():
            state = self._read_unlocked(current)
        vault_meta = self.vault.list_metadata()

        devices_out: list[dict[str, Any]] = []
        for dev_id, dev in sorted(state["devices"].items()):
            if not isinstance(dev, dict):
                continue
            v_info = vault_meta.get(dev_id)
            organ_info = state["organs"].get(dev_id)
            devices_out.append(
                {
                    **dev,
                    "has_credential": bool(v_info),
                    "credential_kind": v_info["credential_kind"] if v_info else None,
                    "organ_status": organ_info.get("status") if isinstance(organ_info, dict) else None,
                    "organ_type": organ_info.get("organ_type") if isinstance(organ_info, dict) else None,
                    "organ_summary": organ_info.get("last_result_summary") if isinstance(organ_info, dict) else None,
                }
            )

        rooms_out: list[dict[str, Any]] = []
        for room_id, raw_room in sorted(state["rooms"].items()):
            if not isinstance(raw_room, dict):
                continue
            decayed = self._decay_room_unlocked(raw_room, current)
            rooms_out.append(
                {
                    "room_id": room_id,
                    "occupancy_probability": decayed["occupancy_probability"],
                    "motion_energy": decayed["motion_energy"],
                    "signal_variance": round(float(decayed.get("signal_variance", 0.0)), 4),
                    "active_modalities": list(decayed.get("active_modalities") or []),
                    "sensor_count": len(decayed.get("sources") or {}),
                    "last_motion_at": decayed.get("last_motion_at"),
                }
            )

        organs_out: list[dict[str, Any]] = []
        for dev_id, org in sorted(state["organs"].items()):
            if not isinstance(org, dict):
                continue
            organs_out.append(
                {
                    "organ_id": org.get("organ_id", f"organ_{dev_id}"),
                    "device_id": dev_id,
                    "organ_type": org.get("organ_type", "native_builtin"),
                    "driver_kind": org.get("driver_kind", ""),
                    "status": org.get("status", "ready"),
                    "code_sha256": org.get("code_sha256", ""),
                    "last_checked_at": org.get("last_checked_at"),
                    "last_latency_ms": org.get("last_latency_ms"),
                    "last_result_summary": org.get("last_result_summary", ""),
                }
            )

        return {
            "contract": "EvaHomeProprioceptionStatus/v1",
            "revision": int(state.get("revision", 1)),
            "last_scan_at": state.get("last_scan_at"),
            "scanned_subnets": state.get("scanned_subnets") or detect_default_lan_subnets(),
            "devices": devices_out,
            "rooms": rooms_out,
            "organs": organs_out,
            "vault_entries_count": len(vault_meta),
        }

    def build_household_awareness_note(self, *, now: datetime | None = None) -> str:
        """Compact, zero-secret proprioception summary for household turns ONLY (never guests).

        Security (H-04): unapproved (`pending_consent`) LAN banners/names are NEVER interpolated
        into `self_context`, eliminating unauthenticated LAN SSDP/HTTP prompt-injection vectors.
        """
        st = self.status(now=now)
        devices = st["devices"]
        approved = [
            _sanitize_lan_label(str(d.get("display_name") or d.get("device_id") or ""), max_len=48)
            for d in devices
            if d.get("consent_status") == "approved"
        ]
        pending = [d for d in devices if d.get("consent_status") == "pending_consent"]
        active_rooms = [
            f"{r['room_id']} ({int(round(r['occupancy_probability'] * 100))}%)"
            for r in st["rooms"]
            if float(r.get("occupancy_probability", 0.0)) >= 0.15
        ]
        parts: list[str] = []
        if approved:
            parts.append(f"Gekoppelde huisapparaten ({len(approved)}): {', '.join(approved[:6])}.")
        if pending:
            pending_tokens = [
                f"{_slugify(str(d.get('kind') or 'device'), max_len=24)}@{d.get('ip', '?')}"
                for d in pending[:4]
            ]
            parts.append(
                f"Nieuw op het thuisnetwerk ontdekt ({len(pending)}, wacht op toestemming van gezinshoofd via /koppel of dashboard): "
                f"{', '.join(pending_tokens)}."
            )
        if active_rooms:
            parts.append(f"Ruimtelijk aanwezigheidsveld: {', '.join(active_rooms[:5])}.")
        return " ".join(parts)


__all__ = [
    "HomeProprioceptionManager",
    "LanProprioceptionScanner",
    "PinnedDeviceChannel",
    "PreLlmCredentialVault",
    "detect_default_lan_subnets",
    "infer_room_from_label",
    "is_safe_lan_ipv4",
    "parse_safe_lan_subnet",
    "validate_custom_organ_ast",
]
