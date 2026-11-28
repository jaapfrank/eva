"""Local authenticated MCP Gateway, Pre-LLM Vault Service Executor & Self-Building Capability Host.

Provides Eva's foreground and background conversation runtimes with sealed, owner-authorized
universal tools (`EvaMcpGatewayProvider/v4` + ContextForge registration routes) so Eva can:
1. Discover LAN devices & vaulted integrations (`discover_network_and_vault`) without exposing secrets.
2. Conjure a tailored encrypted credential/connection card on the 3D Stage (`request_owner_connection`).
3. Connect to & operate UniFi Network, UniFi Protect, Calendar (CalDAV/ICS), Email (IMAP/SMTP),
   and any REST/JSON service (`use_vault_service`) with secrets injected inside the vault boundary.
4. Write, AST-verify, sandbox-test, persist, and run her own reusable Python capabilities
   (`build_and_run_capability`) whenever a requested capability does not exist yet.
"""

from __future__ import annotations

import ast
import base64
from datetime import UTC, datetime, timedelta
import email
from email.header import decode_header
from email.message import EmailMessage
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import imaplib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import select
import smtplib
import socket
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.adapters.connectors.mcp_registration import canonical_mcp_endpoint
from eva.application.home_proprioception import (
    HomeProprioceptionManager,
    PreLlmCredentialVault,
    is_safe_lan_ipv4,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.installer_identity_storage import atomic_write_private

_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")

_SAFE_SKILL_IMPORTS = frozenset(
    {
        "json",
        "math",
        "re",
        "base64",
        "hashlib",
        "hmac",
        "datetime",
        "urllib.parse",
        "xml.etree.ElementTree",
    }
)
_FORBIDDEN_SKILL_NAMES = frozenset(
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
    }
)
_FORBIDDEN_SKILL_ATTRS = frozenset(
    {
        "format",
        "format_map",
        "gi_frame",
        "gi_code",
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
        "subclasses",
        "mro",
        "os",
        "sys",
        "builtins",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_safe_value(val: Any) -> Any:
    """Recursively convert floats to exact ints or decimal strings so Eva's canonical_sha256 ledger accepts every output."""
    if isinstance(val, bool) or val is None or isinstance(val, (int, str)):
        return val
    if isinstance(val, float):
        if not (val == val and val not in (float("inf"), float("-inf"))):
            return "0"
        if val.is_integer() and -9_007_199_254_740_991 <= int(val) <= 9_007_199_254_740_991:
            return int(val)
        return format(round(val, 6), "g")
    if isinstance(val, dict):
        return {str(k): _canonical_safe_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_canonical_safe_value(x) for x in val]
    return str(val)


def _slugify_id(raw: str, *, fallback: str = "service_custom") -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", (raw or "").strip().lower()).strip("_-")
    if len(cleaned) < 3:
        cleaned = f"{cleaned}_{fallback}" if cleaned else fallback
    return cleaned[:63]


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _read_private_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_dir(path.parent)
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    atomic_write_private(path, encoded)


def detect_local_unifi_console() -> dict[str, Any]:
    """Quickly check common gateway IPs for a UniFi OS / Network / Protect console."""
    candidates: list[str] = []
    for probe_target in ("10.0.20.1", "192.168.1.1", "192.168.0.1", "10.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe_target, 80))
                local_ip = sock.getsockname()[0]
                gw = ".".join(local_ip.split(".")[:3] + ["1"])
                if is_safe_lan_ipv4(gw) and gw not in candidates:
                    candidates.append(gw)
        except OSError:
            continue
    for extra in ("10.0.20.1", "192.168.1.1"):
        if extra not in candidates:
            candidates.append(extra)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for ip in candidates[:4]:
        for port in (443, 8443):
            conn = http.client.HTTPSConnection(ip, port, timeout=0.65, context=ctx)
            try:
                conn.request("GET", "/", headers={"User-Agent": "Eva2-Discovery/1.0", "Connection": "close"})
                resp = conn.getresponse()
                body = resp.read(4096).decode("utf-8", errors="ignore").lower()
                hdrs = json.dumps(dict(resp.getheaders())).lower()
                if "unifi" in body or "unifi" in hdrs or "ubnt" in body or "ubnt" in hdrs:
                    url = f"https://{ip}" if port == 443 else f"https://{ip}:{port}"
                    return {"reachable": True, "ip": ip, "port": port, "url": url}
            except Exception:
                pass
            finally:
                conn.close()
    return {"reachable": False, "ip": "", "port": 443, "url": "https://192.168.1.1"}


def validate_capability_python_ast(source_code: str) -> ast.Module:
    """Validate custom Python capability code before isolated execution."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("Python-code voor vaardigheid mag niet leeg zijn")
    if len(source_code) > 32_768:
        raise ValueError("Python-code voor vaardigheid is te groot (max 32 KB)")
    tree = ast.parse(source_code, filename="<eva_capability>")
    has_entry = False
    disallowed = (
        ast.AsyncFunctionDef,
        ast.Await,
        ast.AsyncFor,
        ast.AsyncWith,
        ast.ClassDef,
        ast.Global,
        ast.Nonlocal,
        ast.Yield,
        ast.YieldFrom,
        ast.GeneratorExp,
    )
    for node in ast.walk(tree):
        if isinstance(node, disallowed):
            raise ValueError(f"Niet-toegestane constructie in vaardigheid: {type(node).__name__}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _SAFE_SKILL_IMPORTS:
                    raise ValueError(f"Import '{alias.name}' is niet toegestaan in sandbox")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level != 0 or mod not in _SAFE_SKILL_IMPORTS:
                raise ValueError(f"From-import '{mod}' is niet toegestaan in sandbox")
            for alias in node.names:
                if alias.name == "*" or alias.name.startswith("_") or alias.name in _FORBIDDEN_SKILL_ATTRS:
                    raise ValueError(f"Symbool '{alias.name}' is niet toegestaan")
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_SKILL_NAMES or node.id.startswith("__"):
                raise ValueError(f"Verboden identifier '{node.id}'")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in _FORBIDDEN_SKILL_ATTRS:
                raise ValueError(f"Verboden attribuut '{node.attr}'")
        elif isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                raise ValueError("Decorators zijn niet toegestaan in sandbox")
            if node.name == "run_capability":
                has_entry = True
    if not has_entry:
        raise ValueError("Definieer `def run_capability(ctx, params):` die een JSON-dict teruggeeft")
    return tree


_ISOLATED_CAPABILITY_RUNNER = """
import base64
import datetime
import hashlib
import hmac
import json
import math
import re
import sys
import types
import urllib.parse
import xml.etree.ElementTree as ET

try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
except Exception:
    pass

_stdin = sys.stdin
_stdout = sys.stdout
_init = json.loads(_stdin.readline(131072))
_source = _init["source_code"]
_params = _init.get("params") or {}

_SAFE_MODULES = {
    "json": types.SimpleNamespace(loads=json.loads, dumps=json.dumps),
    "math": types.SimpleNamespace(
        sqrt=math.sqrt, fabs=math.fabs, floor=math.floor, ceil=math.ceil,
        log=math.log, exp=math.exp, sin=math.sin, cos=math.cos, isfinite=math.isfinite,
    ),
    "re": types.SimpleNamespace(search=re.search, match=re.match, sub=re.sub, findall=re.findall, split=re.split),
    "base64": types.SimpleNamespace(b64encode=base64.b64encode, b64decode=base64.b64decode),
    "hashlib": types.SimpleNamespace(sha256=hashlib.sha256),
    "hmac": types.SimpleNamespace(compare_digest=hmac.compare_digest, new=hmac.new),
    "datetime": types.SimpleNamespace(datetime=datetime.datetime, timedelta=datetime.timedelta, timezone=datetime.timezone),
    "urllib.parse": types.SimpleNamespace(urlencode=urllib.parse.urlencode, quote=urllib.parse.quote, urlsplit=urllib.parse.urlsplit),
    "xml.etree.ElementTree": types.SimpleNamespace(fromstring=ET.fromstring, tostring=ET.tostring, Element=ET.Element),
}

def _safe_import(name, globals_=None, locals_=None, fromlist=(), level=0):
    if level != 0 or name not in _SAFE_MODULES:
        raise ImportError(f"Blocked import: {name}")
    mod = _SAFE_MODULES[name]
    if name == "urllib.parse" and not fromlist:
        return types.SimpleNamespace(parse=mod)
    if name == "xml.etree.ElementTree" and not fromlist:
        return types.SimpleNamespace(etree=types.SimpleNamespace(ElementTree=mod))
    return mod

class _CapabilityContext:
    def __init__(self):
        self._calls = 0

    def _rpc(self, payload):
        self._calls += 1
        if self._calls > 10:
            raise RuntimeError("Maximaal 10 verzoeken per uitvoering toegestaan")
        _stdout.write(json.dumps(payload, allow_nan=False) + "\\n")
        _stdout.flush()
        line = _stdin.readline(524288)
        if not line:
            raise RuntimeError("Hostkanaal gesloten")
        msg = json.loads(line)
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        return msg.get("data")

    def http_json(self, method, path, body=None, headers=None):
        return self._rpc({
            "rpc": "http_json",
            "method": str(method or "GET"),
            "path": str(path or "/"),
            "body": body,
            "headers": headers or {},
        })

    def get_state(self):
        return self._rpc({"rpc": "get_state"}) or {}

    def set_state(self, data):
        if not isinstance(data, dict):
            raise ValueError("State moet een dict zijn")
        return self._rpc({"rpc": "set_state", "data": data})

_safe_builtins = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "Exception": Exception, "float": float,
    "int": int, "isinstance": isinstance, "len": len, "list": list,
    "max": max, "min": min, "range": range, "reversed": reversed, "round": round,
    "RuntimeError": RuntimeError, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "ValueError": ValueError,
    "zip": zip, "__import__": _safe_import,
}
_ns = {"__builtins__": _safe_builtins}
exec(compile(_source, "<eva_capability>", "exec"), _ns, _ns)
_fn = _ns.get("run_capability")
if not callable(_fn):
    raise RuntimeError("`run_capability(ctx, params)` ontbreekt")
_out = _fn(_CapabilityContext(), _params)
if not isinstance(_out, dict):
    raise ValueError("`run_capability` moet een JSON-dict teruggeven")
_stdout.write(json.dumps({"rpc": "result", "data": _out}, allow_nan=False) + "\\n")
_stdout.flush()
"""


# ---------------------------------------------------------------------------
# Built-in Universal Tool Definitions & Sealed Catalog Builder
# ---------------------------------------------------------------------------

BUILTIN_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "discover_network_and_vault",
        "operation_kind": "read",
        "description": (
            "Bekijk alle op het lokale thuisnetwerk ontdekte apparaten (zoals UniFi Gateway/Protect, "
            "Home Assistant, Hue, Sonos, camera's), alle gekoppelde diensten in Eva's versleutelde kluis "
            "(zonder geheime sleutels in beeld) én alle eigen Python-vaardigheden die Eva al heeft gebouwd. "
            "Roep deze tool ALTIJD eerst aan wanneer de gebruiker vraagt om verbinding te maken met een "
            "systeem (zoals UniFi, UniFi Protect, agenda, e-mail of slimme apparaten) of vraagt wat er al gekoppeld is."
        ),
        "completion_guidance": (
            "Vat helder samen wat er al gekoppeld of op het netwerk gevonden is. Vraag nooit wachtwoorden of "
            "API-sleutels in de chat; gebruik `request_owner_connection` om het beveiligde koppelvak te openen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scan_lan": {
                    "type": "boolean",
                    "description": "Zet op true om direct een snelle lokale netwerkscan uit te voeren.",
                }
            },
            "required": ["scan_lan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_owner_connection",
        "operation_kind": "invoke",
        "description": (
            "Open direct een beveiligd, versleuteld koppelvak op Eva's 3D-scherm wanneer de eigenaar "
            "verbinding wil maken met een dienst waarvan het adres, wachtwoord of API-token nog ontbreekt "
            "in de kluis — zoals UniFi Netwerk (`unifi_network`), UniFi Protect (`unifi_protect`), "
            "Agenda/CalDAV/ICS (`calendar`), E-mail IMAP/SMTP (`email`), Home Assistant (`home_assistant`) "
            "of elke willekeurige andere API/dienst (`rest_api`). Zo hoeft de eigenaar nooit een geheim in de chat te typen."
        ),
        "completion_guidance": (
            "Vertel enthousiast en concreet dat je het beveiligde koppelvak nu rechts op het scherm hebt geopend "
            "en wat de eigenaar daar kan invullen om direct te verbinden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 63,
                    "description": "Korte ID met kleine letters, bijv. 'unifi_network', 'unifi_protect', 'agenda', 'email' of 'zonnepanelen'.",
                },
                "title": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 80,
                    "description": "Duidelijke Nederlandse titel voor op de koppelkaart.",
                },
                "service_kind": {
                    "type": "string",
                    "enum": [
                        "unifi_network",
                        "unifi_protect",
                        "calendar",
                        "email",
                        "home_assistant",
                        "rest_api",
                    ],
                },
                "default_url": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Vooringevuld adres (bijv. 'https://10.0.20.1', 'https://caldav.icloud.com', 'imaps://imap.gmail.com:993').",
                },
                "auth_mode": {
                    "type": "string",
                    "enum": ["api_key", "basic_auth", "bearer_token", "imap_password", "ics_url"],
                },
                "instructions_nl": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 400,
                    "description": "Korte Nederlandse uitleg waar de eigenaar de API-sleutel of inloggegevens vindt.",
                },
            },
            "required": [
                "service_id",
                "title",
                "service_kind",
                "default_url",
                "auth_mode",
                "instructions_nl",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "use_vault_service",
        "operation_kind": "invoke",
        "description": (
            "Voer een geverifieerde actie of uitlezing uit op een gekoppelde dienst in Eva's versleutelde kluis "
            "(geheimen worden binnen de kluisgrens toegevoegd en komen nooit in de chat): "
            "- `unifi_network`: operations 'summary', 'clients' (wie is thuis / verbonden apparaten), 'devices', 'request' "
            "- `unifi_protect`: operations 'summary', 'cameras', 'events' (slimme detecties / deurbel / beweging), 'request' "
            "- `calendar`: operations 'today', 'upcoming' (komende afspraken), 'create_event' (summary, start_iso, end_iso, location) "
            "- `email`: operations 'inbox_summary' (ongelezen & recente e-mails), 'search' (query), 'read_message' (uid), 'send_email' (to, subject, text) "
            "- `rest_api`: operation 'request' met `params_json` = '{\"method\":\"GET\",\"path\":\"/...\"}'."
        ),
        "completion_guidance": (
            "Rapporteer uitsluitend de werkelijk ontvangen gegevens in natuurlijk Nederlands. Als status='needs_credentials', "
            "roep dan direct `request_owner_connection` aan zodat de eigenaar veilig kan koppelen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 63,
                },
                "operation": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 64,
                },
                "params_json": {
                    "type": "string",
                    "maxLength": 8192,
                    "description": "JSON-object als tekst met extra parameters (gebruik '{}' als er geen nodig zijn).",
                },
            },
            "required": ["service_id", "operation", "params_json"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_and_run_capability",
        "operation_kind": "invoke",
        "description": (
            "Maak, test, bewaar en gebruik zelf nieuwe herbruikbare Python-vaardigheden wanneer Eva iets gevraagd "
            "wordt dat nog niet standaard ingebouwd is (bijv. een nieuwe apparaat-integratie, API-koppeling, "
            "slimme berekening of eigen workflow). "
            "- `action='list'`: toon al je zelfgebouwde vaardigheden. "
            "- `action='create_or_update'`: schrijf Python-code met `def run_capability(ctx, params):` die een dict teruggeeft. "
            "  `ctx.http_json(method, path, body=None, headers=None)` gebruikt automatisch de versleutelde kluis-authenticatie van `service_id` "
            "  (of publieke HTTPS als `service_id` leeg is), en `ctx.get_state()` / `ctx.set_state(dict)` bewaart status. "
            "  De code wordt AST-gecontroleerd, live getest in een geïsoleerd proces met `params_json`, en blijvend opgeslagen! "
            "- `action='run'`: voer een eerder gemaakte `capability_id` uit met `params_json`."
        ),
        "completion_guidance": (
            "Leg kort en trots in natuurlijk Nederlands uit welke nieuwe mogelijkheid je hebt gemaakt of uitgevoerd "
            "en wat het echte test-/uitvoerresultaat is."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create_or_update", "run", "delete"],
                },
                "capability_id": {
                    "type": "string",
                    "maxLength": 63,
                    "description": "Unieke ID voor de vaardigheid (bijv. 'unifi_wifi_gasten' of '' bij action='list').",
                },
                "title": {
                    "type": "string",
                    "maxLength": 100,
                },
                "description": {
                    "type": "string",
                    "maxLength": 500,
                },
                "service_id": {
                    "type": "string",
                    "maxLength": 63,
                    "description": "Optionele kluis-dienst-ID waarvoor `ctx.http_json` automatisch inlogt (of '' zonder kluis).",
                },
                "python_code": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "Volledige Python-code met `def run_capability(ctx, params):` bij create_or_update, anders ''.",
                },
                "params_json": {
                    "type": "string",
                    "maxLength": 8192,
                    "description": "JSON-object als tekst voor de live test of uitvoering (bijv. '{}').",
                },
            },
            "required": [
                "action",
                "capability_id",
                "title",
                "description",
                "service_id",
                "python_code",
                "params_json",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "beheer_gezins_kennisbank",
        "operation_kind": "invoke",
        "description": (
            "Bewaar, doorzoek of bekijk exacte naslagdocumenten in Eva's Gezins-Kennisbank (gekoppeld aan "
            "Hindsight 1.3.0 Knowledge-Base met tijdloze opslag & hybride BM25+vector zoekfunctie) zonder dat "
            "serienummers, codes, handleidingen, garanties of familierecepten door een taalmodel worden herschreven: "
            "- `action='opslaan'`: bewaar een exact stuk naslagtekst onder `titel` en `categorie`. "
            "- `action='zoeken'`: zoek razendsnel op trefwoord of betekenis in alle opgeslagen gezinsdocumenten. "
            "- `action='overzicht'`: bekijk alle titels en categorieën in de kennisbank. "
            "- `action='verwijder'`: verwijder een item op basis van `titel` of item-ID."
        ),
        "completion_guidance": (
            "Geef gevonden serienummers, codes, stappen of recepten exact en helder terug in natuurlijk Nederlands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["opslaan", "zoeken", "overzicht", "verwijder"],
                },
                "titel": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Duidelijke titel bij opslaan/verwijderen (bijv. 'Warmtepomp serienummer & filter'), of '' bij overzicht.",
                },
                "inhoud_of_zoekterm": {
                    "type": "string",
                    "maxLength": 16000,
                    "description": "Exacte tekst/handleiding/code bij `opslaan`, zoekvraag bij `zoeken`, of '' bij `overzicht`/`verwijder`.",
                },
                "categorie": {
                    "type": "string",
                    "enum": [
                        "handleiding",
                        "code_of_serienummer",
                        "recept",
                        "garantie",
                        "huisinfo",
                        "overig",
                    ],
                },
            },
            "required": ["action", "titel", "inhoud_of_zoekterm", "categorie"],
            "additionalProperties": False,
        },
    },
)


def gateway_tool_rows() -> list[dict[str, Any]]:
    """Return ContextForge-compatible tool rows matching sealed EvaMcpGatewayProvider/v4 bindings."""
    rows: list[dict[str, Any]] = []
    for item in BUILTIN_TOOL_DEFINITIONS:
        rows.append(
            {
                "id": f"eva_builtin_{item['name']}",
                "name": item["name"],
                "description": item["description"],
                "inputSchema": item["input_schema"],
                "integrationType": "MCP",
                "requestType": "SSE",
                "enabled": True,
                "deprecated": False,
                "reachable": True,
                "visibility": "private",
                "annotations": {},
            }
        )
    return rows


def build_appliance_gateway_catalog() -> dict[str, Any]:
    """Build the exact EvaMcpGatewayProvider/v4 catalog file consumed by Living Core & Worker."""
    rows_by_name = {r["name"]: r for r in gateway_tool_rows()}
    tools_spec: list[dict[str, Any]] = []
    for item in BUILTIN_TOOL_DEFINITIONS:
        name = item["name"]
        row = rows_by_name[name]
        binding = mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v4")
        cat_sha = canonical_sha256(binding)
        props = item["input_schema"]["properties"]
        arg_bindings = [{"argument_name": k, "provider_name": k} for k in props]
        tools_spec.append(
            {
                "eva_name": name,
                "description": item["description"],
                "input_schema": item["input_schema"],
                "completion_guidance": item["completion_guidance"],
                "operation_kind": item["operation_kind"],
                "execute_tool_name": name,
                "observe_tool_name": name,
                "execute_catalog_name": name,
                "observe_catalog_name": name,
                "execute_catalog_sha256": cat_sha,
                "observe_catalog_sha256": cat_sha,
                "execute_argument_bindings": arg_bindings,
                "observe_argument_bindings": arg_bindings,
                "proof_bindings": [],
            }
        )
    return {
        "contract": "EvaMcpGatewayProvider/v4",
        "tools": tools_spec,
    }


# ---------------------------------------------------------------------------
# Universal Vault Service & Self-Building Capability Engine
# ---------------------------------------------------------------------------


class UniversalIntegrationHub:
    """Manages vaulted service metadata, protocol execution, and Eva's custom Python capabilities."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)
        self.mp_dir = _ensure_private_dir(self.runtime_root / "work" / "model-providers")
        self.vault = PreLlmCredentialVault(self.mp_dir)
        self.services_file = self.mp_dir / "vault-services.json"
        self.pending_file = self.mp_dir / "pending-connection.json"
        self.capabilities_dir = _ensure_private_dir(self.mp_dir / "custom-capabilities")
        self.capability_state_dir = _ensure_private_dir(self.mp_dir / "capability-state")
        self.knowledge_bank_file = self.mp_dir / "knowledge-bank.json"
        self.gateways_file = self.mp_dir / "mcp-registered-gateways.json"
        self._lock = threading.Lock()

    def list_services(self) -> list[dict[str, Any]]:
        raw = _read_private_json(self.services_file, {"services": {}})
        services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
        vault_meta = self.vault.list_metadata()
        out: list[dict[str, Any]] = []
        for sid, cfg in sorted(services.items()):
            if not isinstance(cfg, dict):
                continue
            v_entry = vault_meta.get(sid)
            out.append(
                {
                    "service_id": sid,
                    "title": str(cfg.get("title") or sid),
                    "service_kind": str(cfg.get("service_kind") or "rest_api"),
                    "base_url": str(cfg.get("base_url") or ""),
                    "auth_mode": str(cfg.get("auth_mode") or "api_key"),
                    "smtp_url": str(cfg.get("smtp_url") or ""),
                    "header_name": str(cfg.get("header_name") or ""),
                    "verify_tls": bool(cfg.get("verify_tls", False)),
                    "has_credential": bool(v_entry) or cfg.get("auth_mode") == "ics_url",
                    "has_username": bool(v_entry and v_entry.get("has_username")),
                    "verified": bool(cfg.get("verified", False)),
                    "last_summary": str(cfg.get("last_summary") or ""),
                    "updated_at": str(cfg.get("updated_at") or ""),
                }
            )
        return out

    def get_pending_connection(self) -> dict[str, Any] | None:
        data = _read_private_json(self.pending_file, {})
        if not data or not data.get("service_id"):
            return None
        exp = data.get("expires_epoch")
        if not isinstance(exp, int) or exp <= int(time.time()):
            self.clear_pending_connection()
            return None
        return data

    def clear_pending_connection(self, service_id: str | None = None) -> None:
        if not self.pending_file.is_file():
            return
        if service_id:
            cur = _read_private_json(self.pending_file, {})
            if cur and cur.get("service_id") != service_id:
                return
        try:
            self.pending_file.unlink()
        except OSError:
            pass

    def record_connection_request(self, args: dict[str, Any]) -> dict[str, Any]:
        sid = _slugify_id(str(args.get("service_id") or "service"))
        kind = str(args.get("service_kind") or "rest_api")
        default_url = str(args.get("default_url") or "").strip()
        if not default_url and kind in {"unifi_network", "unifi_protect"}:
            det = detect_local_unifi_console()
            default_url = det["url"]
        req = {
            "contract": "EvaPendingConnectionRequest/v1",
            "service_id": sid,
            "title": str(args.get("title") or sid)[:80],
            "service_kind": kind,
            "default_url": default_url[:260],
            "auth_mode": str(args.get("auth_mode") or "api_key"),
            "instructions_nl": str(args.get("instructions_nl") or "")[:400],
            "requested_at": _utc_now_iso(),
            "expires_epoch": int(time.time()) + 600,
        }
        _write_private_json(self.pending_file, req)
        extra_note = (
            "Het koppelvak staat nu rechts op het scherm open met het vinkje voor zowel UniFi Netwerk als UniFi Protect standaard AAN (één UniFi OS API-Key koppelt beide direct; roep request_owner_connection niet nogmaals aan)."
            if kind in {"unifi_network", "unifi_protect"}
            else "Het beveiligde koppelvak staat nu rechts op het scherm open."
        )
        return {
            "ok": True,
            "status": "connection_card_opened_on_stage",
            "note_nl": extra_note,
            "request": req,
        }

    def save_and_verify_service(
        self,
        *,
        service_id: str,
        title: str,
        service_kind: str,
        base_url: str,
        auth_mode: str = "api_key",
        username: str = "",
        secret: str = "",
        smtp_url: str = "",
        header_name: str = "",
        verify_tls: bool = False,
        run_probe: bool = True,
    ) -> dict[str, Any]:
        sid = _slugify_id(service_id)
        if not _SERVICE_ID_RE.fullmatch(sid):
            raise ValueError("Ongeldig dienst-ID (gebruik bijv. unifi_network of agenda)")
        if service_kind not in {
            "unifi_network",
            "unifi_protect",
            "calendar",
            "email",
            "rest_api",
            "openai_realtime",
            "gemini_live",
        }:
            raise ValueError("Onbekend diensttype")
        clean_url = base_url.strip()
        if not clean_url:
            raise ValueError("Vul het adres of de server-URL in")

        if secret.strip():
            self.vault.store_secret(
                device_id=sid,
                secret_value=secret.strip(),
                credential_kind=auth_mode,
                username=username.strip(),
                require_tls=clean_url.lower().startswith(("https://", "imaps://")),
            )
        elif auth_mode != "ics_url" and sid not in self.vault.list_metadata():
            raise ValueError("Vul de API-sleutel of het wachtwoord in voor de versleutelde kluis")

        with self._lock:
            raw = _read_private_json(self.services_file, {"services": {}})
            services = dict(raw.get("services") or {})
            prev = services.get(sid) if isinstance(services.get(sid), dict) else {}
            services[sid] = {
                "service_id": sid,
                "title": (title.strip() or prev.get("title") or sid)[:80],
                "service_kind": service_kind,
                "base_url": clean_url[:260],
                "auth_mode": auth_mode,
                "smtp_url": (smtp_url.strip() or prev.get("smtp_url") or "")[:260],
                "header_name": (header_name.strip() or prev.get("header_name") or "")[:64],
                "verify_tls": bool(verify_tls),
                "verified": bool(prev.get("verified", False)),
                "last_summary": str(prev.get("last_summary") or ""),
                "updated_at": _utc_now_iso(),
            }
            _write_private_json(self.services_file, {"services": services})

        summary_nl = f"{title or sid} opgeslagen in versleutelde kluis."
        probe_data: dict[str, Any] = {}
        if run_probe and service_kind not in {"openai_realtime", "gemini_live"}:
            default_op = {
                "unifi_network": "summary",
                "unifi_protect": "summary",
                "calendar": "upcoming",
                "email": "inbox_summary",
                "rest_api": "request",
            }[service_kind]
            probe_params = {"method": "GET", "path": "/"} if service_kind == "rest_api" else {}
            res = self.execute_service(sid, default_op, probe_params)
            if not res.get("ok", False):
                raise ValueError(str(res.get("message_nl") or "Verbindingstest mislukt"))
            summary_nl = str(res.get("summary_nl") or summary_nl)
            probe_data = res
            with self._lock:
                raw2 = _read_private_json(self.services_file, {"services": {}})
                if isinstance(raw2.get("services"), dict) and sid in raw2["services"]:
                    raw2["services"][sid]["verified"] = True
                    raw2["services"][sid]["last_summary"] = summary_nl[:200]
                    _write_private_json(self.services_file, raw2)

        self.clear_pending_connection(sid)
        return {
            "ok": True,
            "service_id": sid,
            "summary_nl": summary_nl,
            "probe": probe_data,
        }

    def remove_service(self, service_id: str) -> dict[str, Any]:
        sid = _slugify_id(service_id)
        self.vault.delete_secret(sid)
        with self._lock:
            raw = _read_private_json(self.services_file, {"services": {}})
            services = dict(raw.get("services") or {})
            services.pop(sid, None)
            _write_private_json(self.services_file, {"services": services})
        self.clear_pending_connection(sid)
        return {"ok": True, "removed": sid}

    def _get_credentials(self, service_id: str) -> tuple[str, str]:
        return self.vault.use_secret_for_approved_device(
            device_id=service_id,
            is_device_approved=True,
            callback=lambda u, s: (u, s),
        )

    def _load_service_cfg(self, service_id: str) -> dict[str, Any] | None:
        raw = _read_private_json(self.services_file, {"services": {}})
        services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
        cfg = services.get(service_id)
        return dict(cfg) if isinstance(cfg, dict) else None

    # --- Protocol Drivers ---

    @staticmethod
    def _ssl_context(verify_tls: bool) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _http_request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body_bytes: bytes | None = None,
        verify_tls: bool = False,
        timeout: float = 8.0,
    ) -> tuple[int, dict[str, str], bytes]:
        target = path if path.startswith(("http://", "https://")) else urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Ongeldig HTTP/HTTPS-doeladres")
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        kw: dict[str, Any] = {"timeout": timeout}
        if parsed.scheme == "https":
            kw["context"] = self._ssl_context(verify_tls)
        conn = conn_cls(parsed.hostname, port=parsed.port, **kw)
        try:
            req_path = parsed.path or "/"
            if parsed.query:
                req_path = f"{req_path}?{parsed.query}"
            req_hdrs = {"User-Agent": "Eva2-Universal-Connector/1.0", "Connection": "close"}
            if headers:
                req_hdrs.update(headers)
            conn.request(method.upper(), req_path, body=body_bytes, headers=req_hdrs)
            resp = conn.getresponse()
            raw = resp.read(524_288)
            out_hdrs = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, out_hdrs, raw
        finally:
            conn.close()

    def _unifi_headers(self, cfg: dict[str, Any], user: str, secret: str) -> dict[str, str]:
        base_url = str(cfg["base_url"]).rstrip("/")
        verify_tls = bool(cfg.get("verify_tls", False))
        auth_mode = str(cfg.get("auth_mode") or "api_key")
        if auth_mode == "api_key" or not user:
            return {"X-API-KEY": secret, "Accept": "application/json"}
        # Local UniFi OS username + password login
        login_body = json.dumps({"username": user, "password": secret, "rememberMe": False}).encode("utf-8")
        for login_path in ("/api/auth/login", "/api/login"):
            status, hdrs, _ = self._http_request(
                base_url,
                "POST",
                login_path,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body_bytes=login_body,
                verify_tls=verify_tls,
            )
            if status in {200, 204}:
                cookie = hdrs.get("set-cookie", "").split(";", 1)[0]
                out = {"Accept": "application/json"}
                if cookie:
                    out["Cookie"] = cookie
                csrf = hdrs.get("x-csrf-token") or hdrs.get("x-updated-csrf-token")
                if csrf:
                    out["X-CSRF-Token"] = csrf
                return out
        raise ValueError("Inloggen op UniFi Console geweigerd (controleer lokale gebruikersnaam/wachtwoord of gebruik een UniFi API-Key)")

    def _exec_unifi_network(self, sid: str, cfg: dict[str, Any], op: str, params: dict[str, Any]) -> dict[str, Any]:
        user, secret = self._get_credentials(sid)
        hdrs = self._unifi_headers(cfg, user, secret)
        base_url = str(cfg["base_url"]).rstrip("/")
        verify_tls = bool(cfg.get("verify_tls", False))

        def _get_json(paths: tuple[str, ...]) -> Any:
            last_status = 0
            for p in paths:
                st, _, raw = self._http_request(base_url, "GET", p, headers=hdrs, verify_tls=verify_tls)
                last_status = st
                if st == 200 and raw:
                    return json.loads(raw.decode("utf-8", errors="replace"))
            raise ValueError(f"UniFi Network gaf HTTP {last_status} op {base_url}")

        if op == "request":
            method = str(params.get("method") or "GET").upper()
            path = str(params.get("path") or "/proxy/network/api/s/default/stat/health")
            body = params.get("body")
            b_bytes = json.dumps(body).encode("utf-8") if body is not None else None
            if b_bytes:
                hdrs = {**hdrs, "Content-Type": "application/json"}
            st, _, raw = self._http_request(base_url, method, path, headers=hdrs, body_bytes=b_bytes, verify_tls=verify_tls)
            parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
            return {"ok": st < 400, "http_status": st, "data": parsed}

        if op == "clients":
            data = _get_json(("/proxy/network/api/s/default/stat/sta", "/api/s/default/stat/sta"))
            rows = data.get("data") if isinstance(data, dict) else []
            clients = [
                {
                    "name": str(c.get("name") or c.get("hostname") or c.get("mac") or "Apparaat")[:48],
                    "ip": str(c.get("ip") or c.get("last_ip") or ""),
                    "essid": str(c.get("essid") or ("bekabeld" if c.get("is_wired") else "")),
                    "is_wired": bool(c.get("is_wired", False)),
                    "signal_dbm": c.get("signal"),
                }
                for c in (rows or [])[:40]
                if isinstance(c, dict)
            ]
            return {
                "ok": True,
                "client_count": len(rows or []),
                "clients": clients,
                "summary_nl": f"UniFi Netwerk: {len(rows or [])} actieve apparaten verbonden.",
            }

        if op == "devices":
            data = _get_json(("/proxy/network/api/s/default/stat/device", "/api/s/default/stat/device"))
            rows = data.get("data") if isinstance(data, dict) else []
            devs = [
                {
                    "name": str(d.get("name") or d.get("model") or "UniFi")[:48],
                    "model": str(d.get("model") or ""),
                    "ip": str(d.get("ip") or ""),
                    "state": "online" if d.get("state") == 1 else "offline",
                    "num_sta": d.get("num_sta", 0),
                }
                for d in (rows or [])[:30]
                if isinstance(d, dict)
            ]
            return {
                "ok": True,
                "device_count": len(devs),
                "devices": devs,
                "summary_nl": f"UniFi Netwerk: {len(devs)} netwerkapparaten (switches/AP's/gateway).",
            }

        # summary (supports both official Integration v1 API and classic stat endpoints)
        try:
            health_res = _get_json(("/proxy/network/api/s/default/stat/health", "/api/s/default/stat/health"))
            subsystems = {
                str(h.get("subsystem")): {
                    "status": h.get("status"),
                    "num_user": h.get("num_user"),
                    "wan_ip": h.get("wan_ip"),
                    "isp_name": h.get("isp_name"),
                }
                for h in (health_res.get("data") or [])
                if isinstance(h, dict) and h.get("subsystem")
            }
            wlan_users = (subsystems.get("wlan") or {}).get("num_user") or 0
            lan_users = (subsystems.get("lan") or {}).get("num_user") or 0
            return {
                "ok": True,
                "subsystems": subsystems,
                "summary_nl": (
                    f"UniFi Netwerk verbonden op {base_url} "
                    f"({wlan_users} wifi-apparaten, {lan_users} bekabelde apparaten actief)."
                ),
            }
        except Exception:
            sites_res = _get_json(("/proxy/network/integration/v1/sites",))
            sites = sites_res.get("data") if isinstance(sites_res, dict) else []
            return {
                "ok": True,
                "sites": (sites or [])[:10],
                "summary_nl": f"UniFi Network Integration API verbonden op {base_url} ({len(sites or [])} site(s) gevonden).",
            }

    def _exec_unifi_protect(self, sid: str, cfg: dict[str, Any], op: str, params: dict[str, Any]) -> dict[str, Any]:
        user, secret = self._get_credentials(sid)
        hdrs = self._unifi_headers(cfg, user, secret)
        base_url = str(cfg["base_url"]).rstrip("/")
        verify_tls = bool(cfg.get("verify_tls", False))

        if op == "request":
            method = str(params.get("method") or "GET").upper()
            path = str(params.get("path") or "/proxy/protect/integration/v1/cameras")
            st, _, raw = self._http_request(base_url, method, path, headers=hdrs, verify_tls=verify_tls)
            parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
            return {"ok": st < 400, "http_status": st, "data": parsed}

        # Try official Protect Integration v1 first, fallback to /proxy/protect/api/bootstrap
        cameras_out: list[dict[str, Any]] = []
        st, _, raw = self._http_request(
            base_url, "GET", "/proxy/protect/integration/v1/cameras", headers=hdrs, verify_tls=verify_tls
        )
        if st == 200 and raw:
            rows = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(rows, dict):
                rows = rows.get("data") or rows.get("cameras") or []
            if isinstance(rows, list):
                for c in rows[:32]:
                    if isinstance(c, dict):
                        cameras_out.append(
                            {
                                "id": str(c.get("id") or ""),
                                "name": str(c.get("name") or "Camera")[:48],
                                "state": str(c.get("state") or ""),
                                "is_mic_enabled": c.get("isMicEnabled"),
                                "smart_detect_types": (c.get("smartDetectSettings") or {}).get("objectTypes", []),
                            }
                        )
        else:
            st2, _, raw2 = self._http_request(
                base_url, "GET", "/proxy/protect/api/bootstrap", headers=hdrs, verify_tls=verify_tls
            )
            if st2 != 200 or not raw2:
                raise ValueError(f"UniFi Protect niet bereikbaar of sleutel ongeldig (HTTP {st}/{st2})")
            boot = json.loads(raw2.decode("utf-8", errors="replace"))
            for c in (boot.get("cameras") or [])[:32]:
                if isinstance(c, dict):
                    cameras_out.append(
                        {
                            "id": str(c.get("id") or ""),
                            "name": str(c.get("name") or "Camera")[:48],
                            "state": str(c.get("state") or ""),
                            "is_motion_detected": bool(c.get("isMotionDetected", False)),
                            "last_motion": c.get("lastMotion"),
                        }
                    )

        if op == "events":
            st_e, _, raw_e = self._http_request(
                base_url,
                "GET",
                "/proxy/protect/api/events?limit=15&orderDirection=DESC",
                headers=hdrs,
                verify_tls=verify_tls,
            )
            events_list: list[dict[str, Any]] = []
            if st_e == 200 and raw_e:
                ev_rows = json.loads(raw_e.decode("utf-8", errors="replace"))
                if isinstance(ev_rows, list):
                    for ev in ev_rows[:15]:
                        if isinstance(ev, dict):
                            events_list.append(
                                {
                                    "type": str(ev.get("type") or ""),
                                    "camera": str(ev.get("camera") or ""),
                                    "smart_detect": ev.get("smartDetectTypes") or [],
                                    "start": ev.get("start"),
                                }
                            )
            return {
                "ok": True,
                "cameras": cameras_out,
                "events": events_list,
                "summary_nl": f"UniFi Protect: {len(events_list)} recente gebeurtenissen en {len(cameras_out)} camera's.",
            }

        online_cnt = sum(1 for c in cameras_out if str(c.get("state")).upper() == "CONNECTED")
        return {
            "ok": True,
            "camera_count": len(cameras_out),
            "online_count": online_cnt,
            "cameras": cameras_out,
            "summary_nl": f"UniFi Protect verbonden op {base_url}: {len(cameras_out)} camera's gevonden ({online_cnt} online).",
        }

    @staticmethod
    def _parse_ics_events(ics_text: str, *, max_days: int = 14) -> list[dict[str, str]]:
        unfolded = re.sub(r"\r?\n[ \t]", "", ics_text or "")
        events: list[dict[str, str]] = []
        now_str = datetime.now(UTC).strftime("%Y%m%d")
        cutoff_str = (datetime.now(UTC) + timedelta(days=max(1, min(max_days, 90)))).strftime("%Y%m%d")
        in_ev = False
        cur: dict[str, str] = {}
        for line in unfolded.splitlines():
            stripped = line.strip()
            if stripped == "BEGIN:VEVENT":
                in_ev = True
                cur = {}
            elif stripped == "END:VEVENT" and in_ev:
                in_ev = False
                dt_start = cur.get("DTSTART", "")
                date_key = re.sub(r"[^0-9]", "", dt_start)[:8]
                if not date_key or (now_str <= date_key <= cutoff_str):
                    events.append(
                        {
                            "summary": cur.get("SUMMARY", "(Zonder titel)")[:140],
                            "start": dt_start[:32],
                            "end": cur.get("DTEND", "")[:32],
                            "location": cur.get("LOCATION", "")[:120],
                        }
                    )
            elif in_ev and ":" in stripped:
                raw_k, val = stripped.split(":", 1)
                prop = raw_k.split(";", 1)[0].upper()
                if prop in {"SUMMARY", "DTSTART", "DTEND", "LOCATION"}:
                    cur[prop] = val.replace("\\n", " ").replace("\\,", ",").strip()
        events.sort(key=lambda e: e["start"])
        return events[:30]

    def _exec_calendar(self, sid: str, cfg: dict[str, Any], op: str, params: dict[str, Any]) -> dict[str, Any]:
        base_url = str(cfg["base_url"]).strip()
        if base_url.startswith("webcal://"):
            base_url = "https://" + base_url[len("webcal://") :]
        auth_mode = str(cfg.get("auth_mode") or "basic_auth")
        verify_tls = bool(cfg.get("verify_tls", True))
        hdrs: dict[str, str] = {}

        if auth_mode != "ics_url" and sid in self.vault.list_metadata():
            user, secret = self._get_credentials(sid)
            if auth_mode == "bearer_token" or not user:
                hdrs["Authorization"] = f"Bearer {secret}"
            else:
                tok = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
                hdrs["Authorization"] = f"Basic {tok}"

        days = 1 if op == "today" else int(params.get("days") or 14)

        if op == "create_event":
            if auth_mode == "ics_url":
                return {
                    "ok": False,
                    "message_nl": "Een alleen-lezen ICS-link kan geen nieuwe afspraken schrijven; koppel via CalDAV om ook afspraken toe te voegen.",
                }
            summary = str(params.get("summary") or "Afspraak").strip()[:140]
            start_iso = re.sub(r"[-:]", "", str(params.get("start_iso") or "")).split(".")[0]
            end_iso = re.sub(r"[-:]", "", str(params.get("end_iso") or "")).split(".")[0]
            location = str(params.get("location") or "").strip()[:120]
            if len(start_iso) < 8:
                raise ValueError("Geef start_iso op (bijv. 2026-12-01T14:00:00Z)")
            if len(end_iso) < 8:
                end_iso = start_iso
            uid = f"eva-{uuid4()}@local"
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            ics_payload = (
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Eva2//NL\r\nBEGIN:VEVENT\r\n"
                f"UID:{uid}\r\nDTSTAMP:{stamp}\r\nDTSTART:{start_iso}\r\nDTEND:{end_iso}\r\n"
                f"SUMMARY:{summary}\r\nLOCATION:{location}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            ).encode("utf-8")
            put_path = base_url.rstrip("/") + f"/{quote(uid)}.ics"
            st, _, _ = self._http_request(
                put_path,
                "PUT",
                put_path,
                headers={**hdrs, "Content-Type": "text/calendar; charset=utf-8"},
                body_bytes=ics_payload,
                verify_tls=verify_tls,
            )
            if st not in {200, 201, 204}:
                raise ValueError(f"CalDAV weigerde nieuwe afspraak (HTTP {st})")
            return {
                "ok": True,
                "uid": uid,
                "summary_nl": f"Afspraak '{summary}' toegevoegd aan je agenda.",
            }

        # Read ICS directly or query CalDAV REPORT
        if auth_mode == "ics_url" or base_url.lower().endswith(".ics") or "basic.ics" in base_url.lower():
            st, _, raw = self._http_request(base_url, "GET", base_url, headers=hdrs, verify_tls=verify_tls)
            if st != 200:
                raise ValueError(f"Agenda-feed gaf HTTP {st}")
            events = self._parse_ics_events(raw.decode("utf-8", errors="replace"), max_days=days)
            return {
                "ok": True,
                "events": events,
                "summary_nl": f"Agenda gekoppeld ({len(events)} afspraken in de komende {days} dagen).",
            }

        t_min = datetime.now(UTC).strftime("%Y%m%dT000000Z")
        t_max = (datetime.now(UTC) + timedelta(days=max(1, min(days, 60)))).strftime("%Y%m%dT235959Z")
        report_xml = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:prop><D:getetag/><C:calendar-data/></D:prop>"
            "<C:filter><C:comp-filter name=\"VCALENDAR\">"
            f'<C:comp-filter name="VEVENT"><C:time-range start="{t_min}" end="{t_max}"/></C:comp-filter>'
            "</C:comp-filter></C:filter></C:calendar-query>"
        ).encode("utf-8")
        st, _, raw = self._http_request(
            base_url,
            "REPORT",
            base_url,
            headers={**hdrs, "Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            body_bytes=report_xml,
            verify_tls=verify_tls,
        )
        if st not in {200, 207}:
            # Fallback to PROPFIND check so root CalDAV URLs also verify cleanly
            st_p, _, _ = self._http_request(
                base_url,
                "PROPFIND",
                base_url,
                headers={**hdrs, "Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                body_bytes=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>',
                verify_tls=verify_tls,
            )
            if st_p not in {200, 207}:
                raise ValueError(f"CalDAV-agenda weigerde verbinding (HTTP {st}/{st_p})")
            return {
                "ok": True,
                "events": [],
                "summary_nl": "CalDAV-server geverifieerd (tip: gebruik de specifieke agenda-collectie-URL voor directe afspraken).",
            }
        xml_txt = raw.decode("utf-8", errors="replace")
        ics_chunks = re.findall(r"BEGIN:VCALENDAR.*?END:VCALENDAR", xml_txt, flags=re.DOTALL)
        events = self._parse_ics_events("\n".join(ics_chunks), max_days=days)
        return {
            "ok": True,
            "events": events,
            "summary_nl": f"CalDAV-agenda verbonden ({len(events)} afspraken in de komende {days} dagen).",
        }

    @staticmethod
    def _decode_mime_words(raw: str) -> str:
        parts: list[str] = []
        for chunk, enc in decode_header(raw or ""):
            if isinstance(chunk, bytes):
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            else:
                parts.append(str(chunk))
        return "".join(parts).strip()

    def _exec_email(self, sid: str, cfg: dict[str, Any], op: str, params: dict[str, Any]) -> dict[str, Any]:
        user, secret = self._get_credentials(sid)
        if not user:
            raise ValueError("E-mailadres / gebruikersnaam ontbreekt voor e-mailkoppeling")
        parsed_imap = urlsplit(
            cfg["base_url"] if "://" in str(cfg["base_url"]) else f"imaps://{cfg['base_url']}"
        )
        imap_host = parsed_imap.hostname or ""
        imap_port = parsed_imap.port or 993

        if op == "send_email":
            to_addr = str(params.get("to") or "").strip()
            subj = str(params.get("subject") or "").strip()[:200]
            body_txt = str(params.get("text") or params.get("body") or "").strip()[:8000]
            if not to_addr or "@" not in to_addr or not subj:
                raise ValueError("Geef 'to' (ontvanger) en 'subject' op om een e-mail te versturen")
            raw_smtp = str(cfg.get("smtp_url") or "").strip()
            if not raw_smtp:
                smtp_host = imap_host.replace("imap.", "smtp.")
                smtp_port = 465
                use_ssl = True
            else:
                ps = urlsplit(raw_smtp if "://" in raw_smtp else f"smtps://{raw_smtp}")
                smtp_host = ps.hostname or imap_host.replace("imap.", "smtp.")
                smtp_port = ps.port or (465 if ps.scheme == "smtps" else 587)
                use_ssl = ps.scheme == "smtps" or smtp_port == 465
            msg = EmailMessage()
            msg["From"] = user
            msg["To"] = to_addr
            msg["Subject"] = subj
            msg.set_content(body_txt)
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10.0) as srv:
                    srv.login(user, secret)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=10.0) as srv:
                    srv.starttls()
                    srv.login(user, secret)
                    srv.send_message(msg)
            return {"ok": True, "summary_nl": f"E-mail '{subj}' verzonden naar {to_addr}."}

        with imaplib.IMAP4_SSL(imap_host, imap_port, timeout=9.0) as client:
            client.login(user, secret)
            client.select("INBOX", readonly=True)

            if op == "read_message":
                uid = str(params.get("uid") or "").strip()
                if not uid.isdigit():
                    raise ValueError("Geef een geldig numeriek bericht-ID op")
                _, msg_data = client.uid("fetch", uid, "(RFC822)")
                raw_bytes = msg_data[0][1] if msg_data and isinstance(msg_data[0], tuple) else b""
                parsed_msg = email.message_from_bytes(raw_bytes)
                plain_body = ""
                if parsed_msg.is_multipart():
                    for part in parsed_msg.walk():
                        if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                            payload = part.get_payload(decode=True) or b""
                            plain_body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            break
                else:
                    payload = parsed_msg.get_payload(decode=True) or b""
                    plain_body = payload.decode(parsed_msg.get_content_charset() or "utf-8", errors="replace")
                return {
                    "ok": True,
                    "uid": uid,
                    "from": self._decode_mime_words(parsed_msg.get("From", ""))[:140],
                    "subject": self._decode_mime_words(parsed_msg.get("Subject", ""))[:180],
                    "date": str(parsed_msg.get("Date") or "")[:64],
                    "body_preview": plain_body.strip()[:3500],
                }

            _, unseen_data = client.uid("search", None, "UNSEEN")
            unseen_uids = (unseen_data[0].decode("ascii", errors="ignore").split() if unseen_data and unseen_data[0] else [])
            if op == "search" and params.get("query"):
                q_clean = re.sub(r'["\\\r\n]', " ", str(params["query"])).strip()[:80]
                _, search_data = client.uid("search", None, f'(OR SUBJECT "{q_clean}" FROM "{q_clean}")')
                target_uids = (search_data[0].decode("ascii", errors="ignore").split() if search_data and search_data[0] else [])[-10:]
            else:
                _, all_data = client.uid("search", None, "ALL")
                target_uids = (all_data[0].decode("ascii", errors="ignore").split() if all_data and all_data[0] else [])[-8:]

            messages: list[dict[str, Any]] = []
            for uid in reversed(target_uids):
                _, hdr_data = client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                raw_hdr = hdr_data[0][1] if hdr_data and isinstance(hdr_data[0], tuple) else b""
                m_obj = email.message_from_bytes(raw_hdr)
                messages.append(
                    {
                        "uid": uid,
                        "unread": uid in unseen_uids,
                        "from": self._decode_mime_words(m_obj.get("From", ""))[:120],
                        "subject": self._decode_mime_words(m_obj.get("Subject", ""))[:160],
                        "date": str(m_obj.get("Date") or "")[:60],
                    }
                )
            return {
                "ok": True,
                "unread_count": len(unseen_uids),
                "recent_messages": messages,
                "summary_nl": f"E-mail ({user}) verbonden: {len(unseen_uids)} ongelezen berichten in Postvak IN.",
            }

    def _exec_rest_api(self, sid: str, cfg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        base_url = str(cfg["base_url"]).rstrip("/")
        auth_mode = str(cfg.get("auth_mode") or "api_key")
        verify_tls = bool(cfg.get("verify_tls", False))
        hdrs: dict[str, str] = {"Accept": "application/json"}
        if sid in self.vault.list_metadata():
            user, secret = self._get_credentials(sid)
            custom_hdr = str(cfg.get("header_name") or "").strip()
            if custom_hdr:
                hdrs[custom_hdr] = secret
            elif auth_mode == "basic_auth" and user:
                b64 = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
                hdrs["Authorization"] = f"Basic {b64}"
            elif auth_mode == "api_key":
                hdrs["X-API-Key"] = secret
            else:
                hdrs["Authorization"] = f"Bearer {secret}"

        method = str(params.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Alleen GET/POST/PUT/PATCH/DELETE toegestaan")
        path = str(params.get("path") or "/")
        if path.startswith(("http://", "https://")) and not path.startswith(base_url):
            raise PermissionError("Verzoekpad moet binnen het goedgekeurde basisadres van deze dienst blijven")
        body = params.get("body")
        body_bytes = json.dumps(body).encode("utf-8") if body is not None else None
        if body_bytes is not None:
            hdrs["Content-Type"] = "application/json"
        st, _, raw = self._http_request(
            base_url, method, path, headers=hdrs, body_bytes=body_bytes, verify_tls=verify_tls
        )
        txt = raw.decode("utf-8", errors="replace")[:32_000]
        try:
            data: Any = json.loads(txt) if txt else {}
        except ValueError:
            data = {"raw_text": txt[:8000]}
        return {
            "ok": st < 400,
            "http_status": st,
            "data": data,
            "summary_nl": f"Verbonden met {cfg.get('title') or sid} (HTTP {st}).",
        }

    def execute_service(self, service_id: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        sid = _slugify_id(service_id)
        cfg = self._load_service_cfg(sid)
        if cfg is None:
            return {
                "ok": False,
                "status": "needs_credentials",
                "service_id": sid,
                "message_nl": (
                    f"Dienst '{sid}' staat nog niet in de versleutelde kluis. "
                    "Roep direct `request_owner_connection` aan om het beveiligde koppelvak te openen."
                ),
            }
        kind = str(cfg.get("service_kind") or "rest_api")
        try:
            if kind == "unifi_network":
                return self._exec_unifi_network(sid, cfg, operation, params)
            if kind == "unifi_protect":
                return self._exec_unifi_protect(sid, cfg, operation, params)
            if kind == "calendar":
                return self._exec_calendar(sid, cfg, operation, params)
            if kind == "email":
                return self._exec_email(sid, cfg, operation, params)
            return self._exec_rest_api(sid, cfg, params)
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "service_id": sid,
                "message_nl": f"Kon actie '{operation}' op '{cfg.get('title') or sid}' niet uitvoeren: {exc}",
            }

    # --- Eva's Self-Programming / Custom Capability Engine ---

    def list_capabilities(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for p in sorted(self.capabilities_dir.glob("*.json")):
            if p.is_symlink():
                continue
            data = _read_private_json(p, {})
            if data.get("capability_id"):
                items.append(
                    {
                        "capability_id": str(data["capability_id"]),
                        "title": str(data.get("title") or data["capability_id"]),
                        "description": str(data.get("description") or ""),
                        "service_id": str(data.get("service_id") or ""),
                        "code_sha256": str(data.get("code_sha256") or ""),
                        "verified": bool(data.get("verified", False)),
                        "last_run_at": data.get("last_run_at"),
                        "updated_at": str(data.get("updated_at") or ""),
                    }
                )
        return items

    def _run_isolated_capability(
        self,
        *,
        capability_id: str,
        service_id: str,
        source_code: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        validate_capability_python_ast(source_code)
        state_path = self.capability_state_dir / f"{capability_id}.json"

        proc = subprocess.Popen(  # noqa: S603 - isolated python -I with empty env & rlimits
            [sys.executable, "-I", "-c", _ISOLATED_CAPABILITY_RUNNER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={},
        )
        deadline = time.monotonic() + 12.0
        buf = bytearray()
        try:
            assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
            init_line = json.dumps(
                {"source_code": source_code, "params": params}, allow_nan=False
            ).encode("utf-8") + b"\n"
            proc.stdin.write(init_line)
            proc.stdin.flush()
            fd = proc.stdout.fileno()

            while True:
                while b"\n" not in buf:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        raise TimeoutError("Vaardigheid duurde langer dan 12 seconden")
                    if os.name != "nt":
                        ready, _, _ = select.select([fd], [], [], rem)
                        if not ready:
                            raise TimeoutError("Vaardigheid duurde langer dan 12 seconden")
                    chunk = os.read(fd, 16_384)
                    if not chunk:
                        err_txt = (proc.stderr.read(4096) or b"").decode("utf-8", errors="replace").strip()
                        last_line = err_txt.splitlines()[-1] if err_txt else "onbekende fout"
                        raise RuntimeError(f"Sandbox-uitvoering gestopt: {last_line[:220]}")
                    buf.extend(chunk)
                    if len(buf) > 524_288:
                        raise ValueError("Uitvoer van vaardigheid is te groot")

                nl = buf.index(b"\n")
                raw_line = bytes(buf[:nl])
                del buf[: nl + 1]
                msg = json.loads(raw_line.decode("utf-8"))
                rpc = msg.get("rpc")
                if rpc == "result":
                    out = msg.get("data")
                    if not isinstance(out, dict):
                        raise ValueError("Resultaat moet een JSON-object zijn")
                    return out
                if rpc == "get_state":
                    st_data = _read_private_json(state_path, {})
                    proc.stdin.write((json.dumps({"data": st_data}) + "\n").encode("utf-8"))
                    proc.stdin.flush()
                elif rpc == "set_state":
                    new_st = msg.get("data")
                    if not isinstance(new_st, dict) or len(json.dumps(new_st)) > 65_536:
                        proc.stdin.write(b'{"error":"Ongeldige of te grote status"}\n')
                    else:
                        _write_private_json(state_path, new_st)
                        proc.stdin.write(b'{"data":{"saved":true}}\n')
                    proc.stdin.flush()
                elif rpc == "http_json":
                    try:
                        if service_id:
                            res = self.execute_service(
                                service_id,
                                "request",
                                {
                                    "method": msg.get("method", "GET"),
                                    "path": msg.get("path", "/"),
                                    "body": msg.get("body"),
                                },
                            )
                            reply = {"data": res} if res.get("ok") else {"error": res.get("message_nl", "Fout")}
                        else:
                            url_s = str(msg.get("path") or "")
                            parsed_u = urlsplit(url_s)
                            host_ip_safe = False
                            try:
                                addr = ipaddress.ip_address(parsed_u.hostname or "")
                                host_ip_safe = addr.is_global
                            except ValueError:
                                host_ip_safe = bool(parsed_u.hostname and "." in parsed_u.hostname and not parsed_u.hostname.endswith(".local"))
                            if parsed_u.scheme != "https" or not host_ip_safe:
                                raise PermissionError("Zonder gekoppelde kluis-dienst is alleen publieke HTTPS toegestaan")
                            b_raw = json.dumps(msg["body"]).encode("utf-8") if msg.get("body") is not None else None
                            st_c, _, r_bytes = self._http_request(
                                url_s,
                                str(msg.get("method") or "GET"),
                                url_s,
                                headers={"Accept": "application/json"},
                                body_bytes=b_raw,
                                verify_tls=True,
                            )
                            decoded = json.loads(r_bytes.decode("utf-8", errors="replace")) if r_bytes else {}
                            reply = {"data": {"http_status": st_c, "body": decoded}}
                    except Exception as rpc_exc:
                        reply = {"error": str(rpc_exc)[:200]}
                    proc.stdin.write((json.dumps(reply, allow_nan=False) + "\n").encode("utf-8"))
                    proc.stdin.flush()
                else:
                    raise ValueError("Onbekende RPC-actie")
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            for s in (proc.stdin, proc.stdout, proc.stderr):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def handle_capability_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "list")
        if action == "list":
            caps = self.list_capabilities()
            return {"ok": True, "count": len(caps), "capabilities": caps}

        cid = _slugify_id(str(args.get("capability_id") or ""), fallback="eigen_vaardigheid")
        if not _CAPABILITY_ID_RE.fullmatch(cid):
            return {"ok": False, "message_nl": "Ongeldige capability_id"}
        cap_file = self.capabilities_dir / f"{cid}.json"

        if action == "delete":
            if cap_file.is_file():
                cap_file.unlink()
            return {"ok": True, "deleted": cid}

        try:
            params = json.loads(str(args.get("params_json") or "{}") or "{}")
            if not isinstance(params, dict):
                params = {}
        except ValueError as exc:
            return {"ok": False, "message_nl": f"params_json is geen geldig JSON-object: {exc}"}

        if action == "create_or_update":
            code = str(args.get("python_code") or "")
            sid = _slugify_id(str(args.get("service_id") or "")) if str(args.get("service_id") or "").strip() else ""
            try:
                test_out = self._run_isolated_capability(
                    capability_id=cid,
                    service_id=sid,
                    source_code=code,
                    params=params,
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "status": "verification_failed",
                    "capability_id": cid,
                    "message_nl": f"Test van nieuwe vaardigheid mislukte: {exc}",
                }
            code_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
            now_iso = _utc_now_iso()
            record = {
                "contract": "EvaCustomCapability/v1",
                "capability_id": cid,
                "title": str(args.get("title") or cid)[:100],
                "description": str(args.get("description") or "")[:500],
                "service_id": sid,
                "python_code": code,
                "code_sha256": code_sha,
                "verified": True,
                "last_run_at": now_iso,
                "updated_at": now_iso,
            }
            _write_private_json(cap_file, record)
            return {
                "ok": True,
                "status": "created_and_verified",
                "capability_id": cid,
                "code_sha256": code_sha,
                "test_output": test_out,
            }

        if action == "run":
            rec = _read_private_json(cap_file, {})
            if not rec.get("python_code"):
                return {
                    "ok": False,
                    "status": "not_found",
                    "message_nl": f"Vaardigheid '{cid}' bestaat nog niet; maak deze eerst met action='create_or_update'.",
                }
            code = str(rec["python_code"])
            if hashlib.sha256(code.encode("utf-8")).hexdigest() != rec.get("code_sha256"):
                return {"ok": False, "message_nl": "SHA-256 integriteitscontrole gefaald"}
            try:
                out = self._run_isolated_capability(
                    capability_id=cid,
                    service_id=str(rec.get("service_id") or ""),
                    source_code=code,
                    params=params,
                )
            except Exception as exc:
                return {"ok": False, "capability_id": cid, "message_nl": f"Uitvoering mislukt: {exc}"}
            rec["last_run_at"] = _utc_now_iso()
            _write_private_json(cap_file, rec)
            return {"ok": True, "capability_id": cid, "output": out}

        return {"ok": False, "message_nl": f"Onbekende actie: {action}"}

    # --- Verbatim Household Knowledge Bank + Hindsight 1.3.0 Knowledge-Base Sync ---

    _HINDSIGHT_KB_BANK = "eva-gezins-kennisbank"

    @staticmethod
    def _hindsight_kb_request(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 2.5,
    ) -> tuple[int, dict[str, Any]]:
        conn = http.client.HTTPConnection("127.0.0.1", 44672, timeout=timeout)
        try:
            headers = {"Accept": "application/json"}
            payload: bytes | None = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(262_144)
            if 200 <= resp.status < 300 and raw:
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
                return resp.status, parsed if isinstance(parsed, dict) else {}
            return resp.status, {}
        except Exception:
            return 0, {}
        finally:
            conn.close()

    def list_knowledge_bank_items(self) -> list[dict[str, Any]]:
        raw = _read_private_json(self.knowledge_bank_file, {"items": {}})
        items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
        out: list[dict[str, Any]] = []
        for item_id, rec in sorted(
            items.items(),
            key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
            reverse=True,
        ):
            if not isinstance(rec, dict):
                continue
            content = str(rec.get("content") or "")
            out.append(
                {
                    "item_id": str(item_id),
                    "title": str(rec.get("title") or item_id),
                    "category": str(rec.get("category") or "overig"),
                    "preview": content[:220],
                    "content": content[:4000],
                    "hindsight_synced": bool(rec.get("hindsight_synced", False)),
                    "updated_at": str(rec.get("updated_at") or ""),
                }
            )
        return out[:64]

    def handle_knowledge_bank_tool(self, args: Mapping[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "zoeken").strip().lower()
        titel = str(args.get("titel") or args.get("title") or "").strip()[:120]
        body_or_q = str(
            args.get("inhoud_of_zoekterm") or args.get("content") or args.get("query") or ""
        ).strip()[:16000]
        cat = str(args.get("categorie") or args.get("category") or "overig").strip().lower()
        if cat not in {
            "handleiding",
            "code_of_serienummer",
            "recept",
            "garantie",
            "huisinfo",
            "overig",
        }:
            cat = "overig"

        if action == "overzicht":
            rows = self.list_knowledge_bank_items()
            return {
                "ok": True,
                "aantal": len(rows),
                "items": [
                    {
                        "item_id": r["item_id"],
                        "titel": r["title"],
                        "categorie": r["category"],
                        "voorbeeld": r["preview"],
                        "hindsight_synced": r["hindsight_synced"],
                        "updated_at": r["updated_at"],
                    }
                    for r in rows
                ],
            }

        if action == "opslaan":
            if not titel or not body_or_q:
                return {
                    "ok": False,
                    "message_nl": "Geef zowel een `titel` als de exacte `inhoud_of_zoekterm` mee om op te slaan.",
                }
            item_id = _slugify_id(titel, fallback="kennis_item")
            now_iso = _utc_now_iso()
            bank_path = f"/v1/default/banks/{self._HINDSIGHT_KB_BANK}"
            hindsight_synced = False
            page_id = ""

            with self._lock:
                raw = _read_private_json(self.knowledge_bank_file, {"items": {}})
                items = dict(raw.get("items") or {})
                prev = items.get(item_id) if isinstance(items.get(item_id), dict) else {}
                page_id = str(prev.get("hindsight_page_id") or "")

            # Sync timeless verbatim reference document + Knowledge-Base page to Hindsight 1.3.0
            st_mem, _ = self._hindsight_kb_request(
                "POST",
                f"{bank_path}/memories",
                {
                    "async": True,
                    "items": [
                        {
                            "content": f"[{titel} — {cat}]\n{body_or_q}",
                            "timestamp": "unset",
                            "context": f"Gezins-Kennisbank ({cat}): {titel}",
                            "document_id": f"kb_{item_id}",
                            "tags": ["gezins_kennisbank", cat],
                        }
                    ],
                },
            )
            if 200 <= st_mem < 300:
                hindsight_synced = True
                if not page_id:
                    st_pg, pg_data = self._hindsight_kb_request(
                        "POST",
                        f"{bank_path}/knowledge-base/pages",
                        {
                            "name": titel,
                            "source_query": f"Exacte gegevens, codes en instructies voor {titel} ({cat})",
                            "tags": ["gezins_kennisbank", cat],
                        },
                    )
                    if 200 <= st_pg < 300 and isinstance(pg_data.get("page_id"), str):
                        page_id = pg_data["page_id"]

            with self._lock:
                raw = _read_private_json(self.knowledge_bank_file, {"items": {}})
                items = dict(raw.get("items") or {})
                items[item_id] = {
                    "item_id": item_id,
                    "title": titel,
                    "category": cat,
                    "content": body_or_q,
                    "hindsight_synced": hindsight_synced,
                    "hindsight_page_id": page_id,
                    "updated_at": now_iso,
                }
                _write_private_json(self.knowledge_bank_file, {"items": items})

            return {
                "ok": True,
                "status": "saved",
                "item_id": item_id,
                "titel": titel,
                "categorie": cat,
                "hindsight_1_3_synced": hindsight_synced,
            }

        if action == "verwijder":
            target_key = _slugify_id(titel or body_or_q, fallback="")
            removed_id = ""
            removed_page_id = ""
            with self._lock:
                raw = _read_private_json(self.knowledge_bank_file, {"items": {}})
                items = dict(raw.get("items") or {})
                for k in list(items.keys()):
                    rec = items.get(k) or {}
                    if k == target_key or k == titel or str(rec.get("title") or "").lower() == titel.lower():
                        removed_id = k
                        removed_page_id = str(rec.get("hindsight_page_id") or "")
                        items.pop(k, None)
                        break
                if removed_id:
                    _write_private_json(self.knowledge_bank_file, {"items": items})

            if not removed_id:
                return {"ok": False, "message_nl": f"Geen kennisbank-item gevonden voor '{titel or body_or_q}'."}

            bank_path = f"/v1/default/banks/{self._HINDSIGHT_KB_BANK}"
            self._hindsight_kb_request("DELETE", f"{bank_path}/documents/kb_{quote(removed_id)}", timeout=2.0)
            if removed_page_id:
                self._hindsight_kb_request(
                    "DELETE",
                    f"{bank_path}/knowledge-base/nodes/{quote(removed_page_id)}",
                    timeout=2.0,
                )
            return {"ok": True, "status": "deleted", "item_id": removed_id}

        if action == "zoeken":
            query = (body_or_q or titel).strip()
            raw = _read_private_json(self.knowledge_bank_file, {"items": {}})
            items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
            if not query:
                return {"ok": True, "resultaten": self.list_knowledge_bank_items()[:8]}

            q_low = query.lower()
            q_tokens = [
                w
                for w in re.split(r"\W+", q_low)
                if len(w) >= 2 and w not in {"de", "het", "een", "van", "voor", "wat", "waar", "hoe", "onze", "ons"}
            ]
            scored: list[tuple[int, dict[str, Any]]] = []
            by_title_low: dict[str, dict[str, Any]] = {}
            for item_id, rec in items.items():
                if not isinstance(rec, dict):
                    continue
                t_str = str(rec.get("title") or item_id)
                c_str = str(rec.get("content") or "")
                cat_str = str(rec.get("category") or "overig")
                by_title_low[t_str.lower()] = rec
                hay = f"{item_id} {t_str} {cat_str} {c_str}".lower()
                score = 0
                if q_low in hay:
                    score += 80
                score += sum(20 for tok in q_tokens if tok in hay)
                if score > 0:
                    scored.append((score, rec))

            # Hybrid BM25 + Local Vector RRF search in Hindsight 1.3.0 Knowledge-Base
            bank_path = f"/v1/default/banks/{self._HINDSIGHT_KB_BANK}"
            st_kb, kb_res = self._hindsight_kb_request(
                "GET",
                f"{bank_path}/knowledge-base/search?q={quote(query)}&limit=5",
                timeout=2.2,
            )
            hindsight_hits: list[dict[str, Any]] = []
            if st_kb == 200 and isinstance(kb_res.get("results"), list):
                seen_ids = {str(r.get("item_id")) for _, r in scored}
                for hit in kb_res["results"][:5]:
                    if not isinstance(hit, dict):
                        continue
                    h_name = str(hit.get("name") or "").strip()
                    matched_local = by_title_low.get(h_name.lower())
                    if matched_local and str(matched_local.get("item_id")) not in seen_ids:
                        scored.append((45, matched_local))
                        seen_ids.add(str(matched_local.get("item_id")))
                    elif h_name:
                        hindsight_hits.append(
                            {
                                "titel": h_name,
                                "fragment": str(hit.get("snippet") or "")[:600],
                            }
                        )

            scored.sort(key=lambda pair: -pair[0])
            results = [
                {
                    "item_id": str(rec.get("item_id") or ""),
                    "titel": str(rec.get("title") or ""),
                    "categorie": str(rec.get("category") or "overig"),
                    "exacte_inhoud": str(rec.get("content") or ""),
                    "updated_at": str(rec.get("updated_at") or ""),
                }
                for _, rec in scored[:6]
            ]
            return {
                "ok": True,
                "zoekvraag": query,
                "aantal_gevonden": len(results) + len(hindsight_hits),
                "resultaten": results,
                "hindsight_knowledge_hits": hindsight_hits,
            }

        return {"ok": False, "message_nl": f"Onbekende kennisbank-actie: {action}"}

    def discover_overview(self, *, scan_lan: bool = False) -> dict[str, Any]:
        lan_devices: list[dict[str, Any]] = []
        if os.name != "nt":
            try:
                hp = HomeProprioceptionManager(self.mp_dir)
                if scan_lan:
                    hp.run_lan_scan(max_subnets=1)
                st = hp.status()
                lan_devices = [
                    {
                        "device_id": d.get("device_id"),
                        "kind": d.get("kind"),
                        "display_name": d.get("display_name"),
                        "ip": d.get("ip"),
                        "port": d.get("port"),
                        "consent_status": d.get("consent_status"),
                        "has_credential": d.get("has_credential"),
                    }
                    for d in (st.get("devices") or [])[:24]
                    if isinstance(d, dict)
                ]
            except Exception:
                pass
        unifi_hint = detect_local_unifi_console()
        if unifi_hint.get("reachable"):
            unifi_hint = {
                **unifi_hint,
                "hosts_both_network_and_protect": True,
                "note_nl": (
                    f"UniFi OS Console bereikbaar op {unifi_hint['url']} ({unifi_hint['ip']}); "
                    "hierop kunnen zowel UniFi Netwerk als UniFi Protect met dezelfde UniFi OS API-Key gekoppeld worden."
                ),
            }
        ha_hint: dict[str, Any] = {
            "reachable": False,
            "url": "http://homeassistant.local:8123",
            "ip": "",
            "token_configured": (self.runtime_root / "secrets" / "home-assistant-token").is_file(),
        }
        try:
            ha_ip = socket.gethostbyname("homeassistant.local")
            with socket.create_connection((ha_ip, 8123), timeout=0.8):
                ha_hint["reachable"] = True
                ha_hint["ip"] = ha_ip
        except OSError:
            pass
        return {
            "ok": True,
            "configured_vault_services": self.list_services(),
            "custom_capabilities": self.list_capabilities(),
            "knowledge_bank_items": [
                {
                    "item_id": r["item_id"],
                    "title": r["title"],
                    "category": r["category"],
                }
                for r in self.list_knowledge_bank_items()[:24]
            ],
            "discovered_lan_devices": lan_devices,
            "home_assistant_detected": ha_hint,
            "unifi_console_detected": unifi_hint,
        }

    # --- ContextForge-compatible Upstream MCP Gateway Registry ---

    def list_registered_gateways(self) -> list[dict[str, Any]]:
        data = _read_private_json(self.gateways_file, {"gateways": []})
        rows = data.get("gateways")
        return [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def register_gateway(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        endpoint = canonical_mcp_endpoint(str(body.get("url") or ""))
        transport = str(body.get("transport") or "STREAMABLEHTTP")
        with self._lock:
            rows = self.list_registered_gateways()
            for existing in rows:
                if existing.get("name") == name:
                    return existing
            gw_id = "gw_" + hashlib.sha256(f"{name}:{endpoint}".encode("utf-8")).hexdigest()[:24]
            row: dict[str, Any] = {
                "id": gw_id,
                "name": name,
                "url": endpoint,
                "transport": transport,
                "visibility": str(body.get("visibility") or "private"),
            }
            if body.get("auth_type") == "bearer" and isinstance(body.get("auth_token"), str):
                self.vault.store_secret(
                    device_id=_slugify_id(gw_id, fallback="mcp_gw"),
                    secret_value=body["auth_token"],
                    credential_kind="bearer_token",
                )
                row["has_upstream_bearer"] = True
            rows.append(row)
            _write_private_json(self.gateways_file, {"gateways": rows[:64]})
            return row

    def discover_upstream_tools(self, gateway_id: str) -> list[dict[str, Any]]:
        rows = self.list_registered_gateways()
        gw = next((r for r in rows if r.get("id") == gateway_id), None)
        if gw is None:
            return []
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if gw.get("has_upstream_bearer"):
            _, tok = self._get_credentials(_slugify_id(gateway_id, fallback="mcp_gw"))
            hdrs["Authorization"] = f"Bearer {tok}"
        rpc_req = json.dumps({"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}}).encode("utf-8")
        st, _, raw = self._http_request(gw["url"], "POST", gw["url"], headers=hdrs, body_bytes=rpc_req, verify_tls=False)
        if st != 200 or not raw:
            return []
        resp = json.loads(raw.decode("utf-8", errors="replace"))
        tools = ((resp.get("result") or {}).get("tools")) or []
        out: list[dict[str, Any]] = []
        for t in tools[:64]:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                out.append(
                    {
                        "id": f"{gateway_id}:{t['name']}",
                        "gatewayId": gateway_id,
                        "name": t["name"],
                        "description": str(t.get("description") or ""),
                        "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
                        "integrationType": "MCP",
                        "requestType": "SSE",
                        "enabled": True,
                        "deprecated": False,
                        "reachable": True,
                        "visibility": "private",
                        "annotations": t.get("annotations") or {},
                    }
                )
        return out


# ---------------------------------------------------------------------------
# Authenticated Local HTTP Server (`127.0.0.1:44712`)
# ---------------------------------------------------------------------------


class ApplianceMcpGatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        hub: UniversalIntegrationHub,
        gateway_token: str,
        registration_token: str,
    ) -> None:
        self.hub = hub
        self.gateway_token = gateway_token
        self.registration_token = registration_token
        super().__init__(address, _GatewayRequestHandler)


class _GatewayRequestHandler(BaseHTTPRequestHandler):
    server: ApplianceMcpGatewayServer
    server_version = "EvaApplianceMcpGateway/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _check_bearer(self, *, allow_registration: bool = False) -> bool:
        hdr = (self.headers.get("Authorization") or "").strip()
        if not hdr.startswith("Bearer "):
            return False
        cand = hdr[7:].strip()
        if hmac.compare_digest(cand, self.server.gateway_token):
            return True
        if allow_registration and hmac.compare_digest(cand, self.server.registration_token):
            return True
        return False

    def _read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 524_288:
            return {}
        raw = self.rfile.read(length)
        val = json.loads(raw.decode("utf-8"))
        return val if isinstance(val, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "healthy"})
            return
        if not self._check_bearer(allow_registration=True):
            self._send_json(401, {"error": "unauthorized"})
            return
        if parsed.path == "/gateways":
            self._send_json(200, {"gateways": self.server.hub.list_registered_gateways()})
            return
        if parsed.path == "/tools":
            q = dict(
                part.split("=", 1)
                for part in (parsed.query.split("&") if parsed.query else [])
                if "=" in part
            )
            gw_id = q.get("gateway_id", "")
            if gw_id:
                rows = self.server.hub.discover_upstream_tools(gw_id)
                self._send_json(200, {"tools": rows, "nextCursor": None})
                return
            rows = gateway_tool_rows()
            self._send_json(200, {"tools": rows, "nextCursor": None})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/gateways":
            if not self._check_bearer(allow_registration=True):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                row = self.server.hub.register_gateway(self._read_body_json())
                self._send_json(200, row)
            except Exception as exc:
                self._send_json(400, {"error": str(exc)[:180]})
            return

        if parsed.path == "/rpc":
            if not self._check_bearer(allow_registration=False):
                self._send_json(401, {"error": "unauthorized"})
                return
            req = self._read_body_json()
            rpc_id = req.get("id")
            method = req.get("method")
            params = req.get("params") if isinstance(req.get("params"), dict) else {}

            if method == "tools/list":
                self._send_json(
                    200,
                    {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": gateway_tool_rows()}},
                )
                return

            if method == "tools/call":
                tool_name = str(params.get("name") or "")
                arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                out = _canonical_safe_value(self._dispatch_tool(tool_name, arguments))
                text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(text.encode("utf-8")) > 60_000:
                    out = {"ok": bool(out.get("ok", True)), "truncated": True, "preview": text[:48_000]}
                    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                self._send_json(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "isError": False,
                            "content": [{"type": "text", "text": text}],
                        },
                    },
                )
                return

            self._send_json(
                200,
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}},
            )
            return

        self._send_json(404, {"error": "not_found"})

    def _dispatch_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        hub = self.server.hub
        if tool_name == "discover_network_and_vault":
            return hub.discover_overview(scan_lan=bool(arguments.get("scan_lan", False)))
        if tool_name == "request_owner_connection":
            return hub.record_connection_request(arguments)
        if tool_name == "use_vault_service":
            try:
                extra = json.loads(str(arguments.get("params_json") or "{}") or "{}")
                if not isinstance(extra, dict):
                    extra = {}
            except ValueError:
                extra = {}
            return hub.execute_service(
                str(arguments.get("service_id") or ""),
                str(arguments.get("operation") or "summary"),
                extra,
            )
        if tool_name == "build_and_run_capability":
            return hub.handle_capability_tool(arguments)
        if tool_name == "beheer_gezins_kennisbank":
            return hub.handle_knowledge_bank_tool(arguments)
        return {"ok": False, "message_nl": f"Onbekende gateway-tool: {tool_name}"}


def ensure_gateway_runtime_files(
    *,
    runtime_root: Path,
    tenant_id: str,
    installation_id: str,
    owner_principal_id: str,
    gateway_url: str = "http://127.0.0.1:44712",
) -> dict[str, str]:
    """Provision idempotent private tokens, owner mandate, and sealed v4 catalog for Eva's MCP stack."""
    secrets_dir = _ensure_private_dir(runtime_root / "secrets")
    priv_dir = _ensure_private_dir(runtime_root / "private")

    gw_tok_file = secrets_dir / "mcp-gateway-token"
    if not gw_tok_file.is_file():
        atomic_write_private(gw_tok_file, (secrets.token_urlsafe(36) + "\n").encode("utf-8"))

    reg_tok_file = secrets_dir / "mcp-registration-token"
    if not reg_tok_file.is_file():
        atomic_write_private(reg_tok_file, (secrets.token_urlsafe(36) + "\n").encode("utf-8"))

    # Preserve existing mandate issued_at so PostgresOperationalTimeline hash stays immutable across restarts!
    mandate_file = priv_dir / "mcp-owner-mandate.json"
    existing_mandate = _read_private_json(mandate_file, {})
    if (
        existing_mandate.get("contract") == "OwnerConnectorMandate/v1"
        and existing_mandate.get("tenant_id") == tenant_id
        and existing_mandate.get("installation_id") == installation_id
        and existing_mandate.get("owner_principal_id") == owner_principal_id
    ):
        mandate_payload = dict(existing_mandate)
        mandate_payload["enabled"] = True
    else:
        mandate_payload = {
            "authorization_source": "appliance_owner_setup",
            "contract": "OwnerConnectorMandate/v1",
            "enabled": True,
            "installation_id": installation_id,
            "issued_at": _utc_now_iso(),
            "local_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
            "mandate_id": f"mandate:{installation_id}:owner",
            "owner_principal_id": owner_principal_id,
            "public_https": True,
            "tenant_id": tenant_id,
        }
    atomic_write_private(mandate_file, canonical_json_bytes(mandate_payload))

    catalog_file = priv_dir / "mcp-gateway-catalog.json"
    catalog_payload = build_appliance_gateway_catalog()
    atomic_write_private(catalog_file, canonical_json_bytes(catalog_payload))

    principal_sha = hashlib.sha256(owner_principal_id.encode("utf-8")).hexdigest()
    return {
        "EVA_MCP_GATEWAY_URL": gateway_url,
        "EVA_MCP_GATEWAY_TOKEN_FILE": str(gw_tok_file),
        "EVA_MCP_REGISTRATION_TOKEN_FILE": str(reg_tok_file),
        "EVA_MCP_GATEWAY_CATALOG_FILE": str(catalog_file),
        "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
        "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST": principal_sha,
        "EVA_MCP_AUTONOMY_MANDATE_FILE": str(mandate_file),
        "EVA_MCP_REGISTRATION_VISIBILITY": "private",
        "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY": "true",
        "EVA_PROCEDURE_REFERENCES_ENABLED": "true",
    }


__all__ = [
    "ApplianceMcpGatewayServer",
    "BUILTIN_TOOL_DEFINITIONS",
    "UniversalIntegrationHub",
    "build_appliance_gateway_catalog",
    "detect_local_unifi_console",
    "ensure_gateway_runtime_files",
    "gateway_tool_rows",
    "validate_capability_python_ast",
]
