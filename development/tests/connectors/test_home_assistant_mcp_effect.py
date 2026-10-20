from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any, Iterator

import pytest

from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantToolAdapter,
)
from eva.application.orchestration import ToolExecutionRejected, ToolRequest


SCHEMA = {
    "type": "object",
    "properties": {
        "area": {"type": "string"},
        "device_class": {
            "items": {
                "enum": [
                    "identify", "restart", "update", "awning", "blind", "curtain",
                    "damper", "door", "garage", "gate", "shade", "shutter", "window",
                    "outlet", "switch", "tv", "speaker", "receiver", "projector",
                    "water", "gas",
                ],
                "type": "string",
            },
            "type": "array",
        },
        "domain": {"items": {"type": "string"}, "type": "array"},
        "floor": {"type": "string"},
        "name": {"type": "string"},
    },
}


@dataclass
class _State:
    schema: dict[str, Any] = field(default_factory=lambda: dict(SCHEMA))
    tool_name: str = "HassTurnOff"
    duplicate_name: str | None = None
    result_id: str = "light.test"
    failed: list[dict[str, str]] = field(default_factory=list)
    omit_data: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    rest_services_calls: int = 0
    reject_effect: bool = False
    effect_disconnect: bool = False
    session_headers: list[str | None] = field(default_factory=list)


@contextmanager
def _server(state: _State) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith("/api/services/"):
                state.rest_services_calls += 1
                self.send_error(500)
                return
            assert self.path == "/api/mcp/assist"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(body)
            state.session_headers.append(self.headers.get("Mcp-Session-Id"))
            method = body["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {"tools": [{"name": state.tool_name, "inputSchema": state.schema}]}
                if state.duplicate_name is not None:
                    result["tools"].append({"name": state.duplicate_name, "inputSchema": state.schema})
            elif method == "tools/call":
                if state.reject_effect:
                    self.send_error(401)
                    return
                if state.effect_disconnect:
                    self.close_connection = True
                    return
                success = [] if state.failed else [{"id": state.result_id}]
                effect = {"response_type": "action_done", "data": {"failed": state.failed, "success": success}}
                if state.omit_data:
                    effect.pop("data")
                result = {"content": [{"type": "text", "text": json.dumps(effect)}]}
            else:
                raise AssertionError(method)
            raw = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if method == "initialize":
                self.send_header("Mcp-Session-Id", "test-session")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _command() -> ToolRequest:
    return ToolRequest(
        "thread", "operation", "checkpoint", "home_assistant.light.turn_off",
        {"domain": "light", "entity_id": "light.test", "service": "turn_off", "service_data": {}},
        {"entity_id": "light.test", "state": "off"},
    )


def test_effect_uses_official_mcp_and_never_direct_rest_service() -> None:
    state = _State()
    with _server(state) as origin:
        result = asyncio.run(HomeAssistantToolAdapter(HomeAssistantConfig(origin, "token")).execute(_command()))

    assert result.accepted is True
    assert result.details["tool_name"] == "HassTurnOff"
    assert [call["method"] for call in state.calls] == [
        "initialize", "notifications/initialized", "tools/list", "tools/call"
    ]
    assert state.calls[-1]["params"]["arguments"] == {"name": "light.test"}
    assert state.rest_services_calls == 0


@pytest.mark.parametrize("failure", ["schema", "target", "provider", "missing_data"])
def test_effect_rejects_drift_wrong_target_and_provider_failure(failure: str) -> None:
    state = _State()
    if failure == "schema":
        state.schema = {"type": "object", "properties": {"other": {"type": "string"}}}
    elif failure == "target":
        state.result_id = "light.other"
    elif failure == "provider":
        state.failed = [{"id": "light.test"}]
    else:
        state.omit_data = True
    with _server(state) as origin:
        adapter = HomeAssistantToolAdapter(HomeAssistantConfig(origin, "token"))
        with pytest.raises(ToolExecutionRejected):
            asyncio.run(adapter.execute(_command()))

    expected_calls = 3 if failure == "schema" else 4
    assert len(state.calls) == expected_calls
    assert state.rest_services_calls == 0


@pytest.mark.parametrize("prefix", ["", "intent__"])
@pytest.mark.parametrize("service,intent", [("turn_off", "HassTurnOff"), ("turn_on", "HassTurnOn")])
def test_official_names_and_enum_permutations_keep_exact_call(prefix, service, intent):
    tool_name = prefix + intent
    state = _State(tool_name=tool_name, schema=deepcopy(SCHEMA))
    state.schema["properties"]["device_class"]["items"]["enum"].reverse()
    before = deepcopy(state.schema)
    command = replace(_command(), tool_name="home_assistant.light." + service,
        arguments={**_command().arguments, "service": service},
        expected_proof={"entity_id": "light.test", "state": "on" if service == "turn_on" else "off"})
    with _server(state) as origin:
        result = asyncio.run(HomeAssistantToolAdapter(HomeAssistantConfig(origin, "token")).execute(command))
    assert result.details["tool_name"] == tool_name
    assert state.calls[-1]["params"] == {"name": tool_name, "arguments": {"name": "light.test"}}
    assert state.schema == before
    assert state.rest_services_calls == 0


@pytest.mark.parametrize("drift", ["added_enum", "removed_enum", "duplicate_enum", "nonstring_enum",
    "name_type", "required", "extra_property", "foreign_namespace", "malformed_name", "duplicate_bare", "both_aliases"])
def test_only_enum_order_and_exact_official_alias_are_compatible(drift):
    state = _State(tool_name="intent__HassTurnOff", schema=deepcopy(SCHEMA))
    values = state.schema["properties"]["device_class"]["items"]["enum"]
    if drift == "added_enum": values.append("unreviewed")
    elif drift == "removed_enum": values.pop()
    elif drift == "duplicate_enum": values.append(values[0])
    elif drift == "nonstring_enum": values.append(None)
    elif drift == "name_type": state.schema["properties"]["name"]["type"] = "array"
    elif drift == "required": state.schema["required"] = ["area"]
    elif drift == "extra_property": state.schema["properties"]["unsafe"] = {"type": "string"}
    elif drift == "foreign_namespace": state.tool_name = "custom__HassTurnOff"
    elif drift == "malformed_name": state.tool_name = ["HassTurnOff"]
    elif drift == "duplicate_bare": state.duplicate_name = state.tool_name
    elif drift == "both_aliases": state.duplicate_name = "HassTurnOff"
    with _server(state) as origin:
        with pytest.raises(ToolExecutionRejected):
            asyncio.run(HomeAssistantToolAdapter(HomeAssistantConfig(origin, "token")).execute(_command()))
    assert [call["method"] for call in state.calls] == ["initialize", "notifications/initialized", "tools/list"]
    assert state.rest_services_calls == 0


def test_provider_client_reuses_catalog_and_preserves_session() -> None:
    from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient, call_assist_effect
    state = _State()
    with _server(state) as origin:
        client = HomeAssistantAssistClient(base_url=origin, token="token", scope_binding="lights", server_version="2026.10")
        catalog = client.catalog(5)
        catalog["tools"].clear()  # The caller cannot mutate trusted discovery.
        for _ in range(2):
            call_assist_effect(base_url=origin, token="token", timeout=5, domain="light", service="turn_off",
                               entity_id="light.test", client=client)
    assert [call["method"] for call in state.calls] == [
        "initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"]
    assert state.session_headers == [None, "test-session", "test-session", "test-session", "test-session"]
    assert len({call["id"] for call in state.calls if "id" in call}) == 4
    assert state.rest_services_calls == 0


@pytest.mark.parametrize("failure", ["revoked", "disconnect"])
def test_uncertain_effect_is_not_repeated_and_evicts_catalog(failure: str) -> None:
    from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient, HomeAssistantMcpError, call_assist_effect
    state = _State()
    with _server(state) as origin:
        client = HomeAssistantAssistClient(base_url=origin, token="token")
        original = client.catalog(5)
        state.reject_effect = failure == "revoked"
        state.effect_disconnect = failure == "disconnect"
        with pytest.raises(HomeAssistantMcpError):
            call_assist_effect(base_url=origin, token="token", timeout=5, domain="light", service="turn_off",
                               entity_id="light.test", client=client)
        assert len([call for call in state.calls if call["method"] == "tools/call"]) == 1
        state.reject_effect = state.effect_disconnect = False
        assert client.catalog(5)["catalog_sha256"] == original["catalog_sha256"]
    assert [call["method"] for call in state.calls].count("initialize") == 2
    assert state.rest_services_calls == 0


def test_refresh_schema_and_catalog_change_fail_before_effect() -> None:
    from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient, HomeAssistantMcpError, call_assist_effect
    state = _State(schema=deepcopy(SCHEMA))
    with _server(state) as origin:
        client = HomeAssistantAssistClient(base_url=origin, token="token")
        original = client.catalog(5)
        state.schema["required"] = ["area"]
        changed = client.catalog(5, force_refresh=True)
        assert changed["catalog_sha256"] != original["catalog_sha256"]
        with pytest.raises(HomeAssistantMcpError, match="catalog binding changed"):
            call_assist_effect(base_url=origin, token="token", timeout=5, domain="light", service="turn_off",
                               entity_id="light.test", client=client, expected_catalog_sha256=original["catalog_sha256"])
        with pytest.raises(HomeAssistantMcpError, match="schema drifted"):
            call_assist_effect(base_url=origin, token="token", timeout=5, domain="light", service="turn_off",
                               entity_id="light.test", client=client)
    assert not [call for call in state.calls if call["method"] == "tools/call"]
    assert state.rest_services_calls == 0


def test_catalog_expiry_refreshes_and_changed_binding_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import eva.adapters.connectors.home_assistant_mcp as mcp
    clock = [100.0]
    monkeypatch.setattr(mcp.time, "monotonic", lambda: clock[0])
    state = _State()
    with _server(state) as origin:
        client = mcp.HomeAssistantAssistClient(base_url=origin, token="token", catalog_ttl_seconds=60)
        client.catalog(5)
        clock[0] = 159.0
        client.catalog(5)
        assert [call["method"] for call in state.calls].count("initialize") == 1
        clock[0] = 160.0
        client.catalog(5)
        assert [call["method"] for call in state.calls].count("initialize") == 2
        with pytest.raises(mcp.HomeAssistantMcpError, match="client binding differs"):
            mcp.call_assist_effect(base_url=origin, token="changed", timeout=5, domain="light", service="turn_off",
                                   entity_id="light.test", client=client)
    assert not [call for call in state.calls if call["method"] == "tools/call"]


def test_version_and_scope_bindings_are_separate_without_exposing_credentials() -> None:
    from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient
    state = _State()
    with _server(state) as origin:
        profiles = [HomeAssistantAssistClient(base_url=origin, token="private-token", scope_binding=scope,
                                              server_version=version).catalog(5)
                    for scope, version in [("lights", "2026.10"), ("all", "2026.10"), ("lights", "2026.11")]]
    assert len({profile["binding_sha256"] for profile in profiles}) == 3
    assert all("private-token" not in json.dumps(profile) for profile in profiles)


@pytest.mark.parametrize("failure", ["stop", "revoked"])
def test_discovery_cannot_outlive_current_stop_or_authority(monkeypatch, failure):
    from types import SimpleNamespace
    from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient
    from eva.adapters.postgres.connector_results import result_capture
    from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
    state = _State()
    signal = CancellationSignal()
    revoked = [False]
    def authorize(turn):
        if revoked[0]:raise PermissionError("revoked")
    with _server(state) as origin:
        client = HomeAssistantAssistClient(base_url=origin, token="token")
        original = client._catalog_locked
        def discover(**kwargs):
            result = original(**kwargs)
            if failure == "stop":signal.cancel()
            else:revoked[0] = True
            return result
        monkeypatch.setattr(client, "_catalog_locked", discover)
        with cancellation_scope(signal), result_capture(SimpleNamespace(
                authorize=authorize, turn=object())):
            with pytest.raises(WorkInterrupted if failure == "stop" else PermissionError):
                client.effect(timeout=5, tool_name="HassTurnOff", entity_id="light.test")
    assert [call["method"] for call in state.calls].count("tools/list") == 1
    assert not [call for call in state.calls if call["method"] == "tools/call"]


def test_catalog_lock_wait_uses_original_deadline(monkeypatch):
    import eva.adapters.connectors.home_assistant_mcp as mcp
    clock = [100.0]
    monkeypatch.setattr(mcp.time, "monotonic", lambda: clock[0])
    class Lock:
        def acquire(self, *, timeout):
            assert timeout == 5
            clock[0] += 6
            return True
        def release(self):pass
    client = mcp.HomeAssistantAssistClient(base_url="http://unused.test", token="test")
    client._lock = Lock()
    monkeypatch.setattr(client, "_post", lambda *a, **kw:pytest.fail("late discovery"))
    with pytest.raises(mcp.HomeAssistantMcpError, match="timed out"):
        client.catalog(timeout=5)
