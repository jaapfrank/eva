"""Exact official Home Assistant Assist-MCP effect transport."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import threading
import time
from typing import Any
from urllib import error, request

from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_PROTOCOL = "2025-06-18"
# Only the order of the device-class enum is non-semantic. All schema fields
# and the complete allowed value set remain pinned.
_EFFECT_SCHEMA_SHA256 = "712dde1bf07fd0158e1171dd9ca0991bc952c8247bd83a4c798570b4de92f9ea"


class HomeAssistantMcpError(RuntimeError):
    """The admitted Assist effect failed closed."""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HomeAssistantMcpError("Home Assistant MCP lifecycle timed out")
    return remaining


@contextmanager
def _locked(lock, deadline):
    if not lock.acquire(timeout=_remaining(deadline)):
        raise HomeAssistantMcpError("Home Assistant catalog lock timed out")
    try:
        _remaining(deadline)
        yield
    finally:
        lock.release()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class _Catalog:
    payload: bytes
    expires_at: float
    headers: dict[str, str]


class HomeAssistantAssistClient:
    """Provider-owned, bounded Assist discovery cache; effects are never retried.

    Endpoint, credentials, granted scope and the observed HA version are immutable
    bindings. Their owner must replace the client when any binding changes. HA's
    Assist endpoint has no catalog-change notifications, so expiry and explicit
    refresh are the discovery boundaries, rather than instantaneous invalidation.
    """

    def __init__(self, *, base_url: str, token: str, scope_binding: str = "",
                 server_version: str = "", catalog_ttl_seconds: float = 60) -> None:
        if not 0 < catalog_ttl_seconds <= 300:
            raise ValueError("Home Assistant MCP catalog lifetime must be within 300 seconds")
        self._endpoint = f"{base_url.rstrip('/')}/api/mcp/assist"
        self._token = token
        self._ttl = catalog_ttl_seconds
        self._binding = canonical_sha256({
            "endpoint": self.endpoint, "credential_sha256": canonical_sha256(token),
            "scope": scope_binding, "server_version": server_version,
        })
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self._catalog: _Catalog | None = None
        self._lock = threading.Lock()
        self._request_id = 0

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def matches(self, *, base_url: str, token: str) -> bool:
        return self.endpoint == f"{base_url.rstrip('/')}/api/mcp/assist" and self._token == token

    def invalidate(self) -> None:
        with self._lock:
            self._catalog = None

    def _next_id(self) -> int:
        # Discovery already owns this lock; use only from its caller or under it.
        self._request_id += 1
        return self._request_id

    def _post(self, payload: dict[str, JsonValue], *, headers: dict[str, str],
              deadline: float, status: int = 200) -> tuple[dict[str, Any], str | None]:
        if payload.get("method") == "tools/call":
            from eva.adapters.postgres.connector_results import check_capture_authority
            check_capture_authority()
        remaining = _remaining(deadline)
        try:
            response = self._opener.open(
                request.Request(self.endpoint, data=canonical_json_bytes(payload), headers=headers, method="POST"),
                timeout=remaining,
            )
            with response:
                raw = response.read(1_048_577)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                observed_status = response.status
                session_id = response.headers.get("Mcp-Session-Id")
        except (error.HTTPError, error.URLError, OSError) as exc:
            raise HomeAssistantMcpError("Home Assistant MCP endpoint failed") from exc
        if observed_status != status or len(raw) > 1_048_576:
            raise HomeAssistantMcpError("Home Assistant MCP response boundary failed")
        if status == 202:
            if raw:
                raise HomeAssistantMcpError("Home Assistant MCP notification returned data")
            return {}, session_id
        decoded = _response(raw, content_type)
        if decoded.get("jsonrpc") != "2.0" or decoded.get("id") != payload.get("id"):
            raise HomeAssistantMcpError("Home Assistant MCP response is not request-bound")
        result = decoded.get("result")
        if "error" in decoded or not isinstance(result, dict):
            raise HomeAssistantMcpError("Home Assistant MCP request was rejected")
        return result, session_id

    def _catalog_locked(self, *, deadline: float, force_refresh: bool) -> _Catalog:
        if not force_refresh and self._catalog is not None and time.monotonic() < self._catalog.expires_at:
            return self._catalog
        self._catalog = None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
        }
        initialized, session_id = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "eva2-home-assistant", "version": "1"}},
        }, headers=headers, deadline=deadline)
        capabilities = initialized.get("capabilities")
        if (initialized.get("protocolVersion") != _PROTOCOL or not isinstance(capabilities, dict)
                or not isinstance(capabilities.get("tools"), dict)):
            raise HomeAssistantMcpError("Home Assistant MCP initialization differs")
        headers["MCP-Protocol-Version"] = _PROTOCOL
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   headers=headers, deadline=deadline, status=202)
        listed, _ = self._post({"jsonrpc": "2.0", "id": self._next_id(),
                                "method": "tools/list", "params": {}}, headers=headers, deadline=deadline)
        tools = listed.get("tools")
        if not isinstance(tools, list) or listed.get("nextCursor"):
            raise HomeAssistantMcpError("Home Assistant MCP catalog is incomplete")
        payload = {
            "protocol_version": _PROTOCOL, "server_info": initialized.get("serverInfo", {}),
            "tools": tools, "catalog_sha256": canonical_sha256(tools), "binding_sha256": self._binding,
        }
        self._catalog = _Catalog(canonical_json_bytes(payload), time.monotonic() + self._ttl, headers)
        return self._catalog

    def catalog(self, timeout: float, *, force_refresh: bool = False) -> dict[str, Any]:
        """Discover without invoking tools; returned data cannot mutate the cache."""
        deadline = time.monotonic() + timeout
        with _locked(self._lock, deadline):
            try:
                selected = self._catalog_locked(deadline=deadline, force_refresh=force_refresh)
                return json.loads(selected.payload)
            except HomeAssistantMcpError:
                self._catalog = None
                raise

    def effect(self, *, timeout: float, tool_name: str, entity_id: str,
               expected_catalog_sha256: str | None = None) -> tuple[dict[str, Any], str]:
        deadline = time.monotonic() + timeout
        with _locked(self._lock, deadline):
            try:
                selected = self._catalog_locked(deadline=deadline, force_refresh=False)
                catalog = json.loads(selected.payload)
                if expected_catalog_sha256 is not None and catalog["catalog_sha256"] != expected_catalog_sha256:
                    raise HomeAssistantMcpError("Home Assistant MCP catalog binding changed")
                admitted_names = {tool_name, f"intent__{tool_name}"}
                matches = [item for item in catalog["tools"] if isinstance(item, dict)
                           and isinstance(item.get("name"), str) and item["name"] in admitted_names]
                if len(matches) != 1 or _effect_schema_digest(matches[0].get("inputSchema")) != _EFFECT_SCHEMA_SHA256:
                    raise HomeAssistantMcpError("Home Assistant MCP effect schema drifted")
                chosen_name = matches[0]["name"]
                request_id = self._next_id()
                headers = dict(selected.headers)
            except HomeAssistantMcpError:
                self._catalog = None
                raise
        try:
            called, _ = self._post({
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": chosen_name, "arguments": {"name": entity_id}},
            }, headers=headers, deadline=deadline)
        except HomeAssistantMcpError:
            self.invalidate()
            raise
        return called, chosen_name


def call_assist_effect(
    *, base_url: str, token: str, timeout: int, domain: str, service: str, entity_id: str,
    client: HomeAssistantAssistClient | None = None, expected_catalog_sha256: str | None = None,
) -> dict[str, JsonValue]:
    """Attest and call one built-in on/off intent; return no provider payload."""
    if domain not in {"light", "switch"} or service not in {"turn_on", "turn_off"}:
        raise HomeAssistantMcpError("Home Assistant MCP effect is not admitted")
    selected = client or HomeAssistantAssistClient(base_url=base_url, token=token)
    if not selected.matches(base_url=base_url, token=token):
        raise HomeAssistantMcpError("Home Assistant MCP client binding differs")
    called, tool_name = selected.effect(
        timeout=timeout, tool_name="HassTurnOn" if service == "turn_on" else "HassTurnOff",
        entity_id=entity_id, expected_catalog_sha256=expected_catalog_sha256,
    )
    try:
        content = called.get("content")
        if called.get("isError") is True or not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get("type") != "text":
            raise HomeAssistantMcpError("Home Assistant MCP effect result differs")
        try:
            result = json.loads(content[0]["text"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HomeAssistantMcpError("Home Assistant MCP effect result is invalid") from exc
        data = result.get("data") if isinstance(result, dict) else None
        success = data.get("success") if isinstance(data, dict) else None
        if not isinstance(result, dict) or result.get("response_type") != "action_done" or not isinstance(data, dict) or data.get("failed") != [] or not isinstance(success, list) or len(success) != 1 or not isinstance(success[0], dict) or success[0].get("id") != entity_id:
            raise HomeAssistantMcpError("Home Assistant MCP effect was not exact")
        return {"entity_id": entity_id, "response_sha256": canonical_sha256(result), "tool_name": tool_name}
    except HomeAssistantMcpError:
        selected.invalidate()
        raise


def _effect_schema_digest(schema: Any) -> str | None:
    """Normalize only a validated enum permutation, preserving all other data."""
    if not isinstance(schema, dict):
        return None
    try:
        values = schema["properties"]["device_class"]["items"]["enum"]
    except (KeyError, TypeError):
        return None
    if (not isinstance(values, list) or not values
            or not all(isinstance(value, str) for value in values)
            or len(set(values)) != len(values)):
        return None
    normalized = json.loads(canonical_json_bytes(schema))
    normalized["properties"]["device_class"]["items"]["enum"] = sorted(values)
    return canonical_sha256(normalized)


def _response(raw: bytes, content_type: str) -> dict[str, Any]:
    if content_type == "text/event-stream":
        events = [part for part in raw.replace(b"\r\n", b"\n").split(b"\n\n") if part]
        data = [line[6:] for line in events[0].split(b"\n") if line.startswith(b"data: ")] if len(events) == 1 else []
        raw = data[0] if len(data) == 1 else b""
    elif content_type != "application/json":
        raw = b""
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HomeAssistantMcpError("Home Assistant MCP response is invalid") from exc
    if not isinstance(value, dict):
        raise HomeAssistantMcpError("Home Assistant MCP response is not an object")
    return value
