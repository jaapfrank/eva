"""Exact official Home Assistant Assist-MCP effect transport."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_PROTOCOL = "2025-06-18"
_EFFECT_SCHEMA_SHA256 = "21499e66f4cf9309c45fcac873959cb1a136781415006767388010feb5776fc1"


class HomeAssistantMcpError(RuntimeError):
    """The admitted Assist effect failed closed."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def call_assist_effect(
    *, base_url: str, token: str, timeout: int, domain: str, service: str, entity_id: str
) -> dict[str, JsonValue]:
    """Attest and call one built-in on/off intent; return no provider payload."""

    if domain not in {"light", "switch"} or service not in {"turn_on", "turn_off"}:
        raise HomeAssistantMcpError("Home Assistant MCP effect is not admitted")
    tool_name = "HassTurnOn" if service == "turn_on" else "HassTurnOff"
    endpoint = f"{base_url.rstrip('/')}/api/mcp/assist"
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + timeout
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    def post(payload: dict[str, JsonValue], *, status: int = 200) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HomeAssistantMcpError("Home Assistant MCP lifecycle timed out")
        body = canonical_json_bytes(payload)
        try:
            response = opener.open(
                request.Request(endpoint, data=body, headers=headers, method="POST"),
                timeout=remaining,
            )
            with response:
                raw = response.read(1_048_577)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                observed_status = response.status
        except (error.HTTPError, error.URLError, OSError) as exc:
            raise HomeAssistantMcpError("Home Assistant MCP endpoint failed") from exc
        if observed_status != status or len(raw) > 1_048_576:
            raise HomeAssistantMcpError("Home Assistant MCP response boundary failed")
        if status == 202:
            if raw:
                raise HomeAssistantMcpError("Home Assistant MCP notification returned data")
            return {}
        decoded = _response(raw, content_type)
        if decoded.get("jsonrpc") != "2.0" or decoded.get("id") != payload.get("id"):
            raise HomeAssistantMcpError("Home Assistant MCP response is not request-bound")
        result = decoded.get("result")
        if "error" in decoded or not isinstance(result, dict):
            raise HomeAssistantMcpError("Home Assistant MCP request was rejected")
        return result

    initialized = post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                   "clientInfo": {"name": "eva2-home-assistant", "version": "1"}},
    })
    capabilities = initialized.get("capabilities")
    if initialized.get("protocolVersion") != _PROTOCOL or not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
        raise HomeAssistantMcpError("Home Assistant MCP initialization differs")
    headers["MCP-Protocol-Version"] = _PROTOCOL
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, status=202)
    tools = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).get("tools")
    matches = [item for item in tools if isinstance(item, dict) and item.get("name") == tool_name] if isinstance(tools, list) else []
    if len(matches) != 1 or canonical_sha256(matches[0].get("inputSchema")) != _EFFECT_SCHEMA_SHA256:
        raise HomeAssistantMcpError("Home Assistant MCP effect schema drifted")
    called = post({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": tool_name, "arguments": {"name": entity_id}},
    })
    content = called.get("content")
    if called.get("isError") is True or not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get("type") != "text":
        raise HomeAssistantMcpError("Home Assistant MCP effect result differs")
    try:
        result = json.loads(content[0]["text"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HomeAssistantMcpError("Home Assistant MCP effect result is invalid") from exc
    data = result.get("data") if isinstance(result, dict) else None
    success = data.get("success") if isinstance(data, dict) else None
    if result.get("response_type") != "action_done" or data.get("failed") != [] or not isinstance(success, list) or len(success) != 1 or not isinstance(success[0], dict) or success[0].get("id") != entity_id:
        raise HomeAssistantMcpError("Home Assistant MCP effect was not exact")
    return {"entity_id": entity_id, "response_sha256": canonical_sha256(result), "tool_name": tool_name}


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
