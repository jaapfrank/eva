from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any, Iterator

import pytest

from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256

from candidate import (
    AssistToolAdmission,
    HomeAssistantOfficialMcpClient,
    HomeAssistantOfficialMcpConfig,
    HomeAssistantOfficialMcpEffectAdapter,
    HomeAssistantOfficialMcpError,
)


TOKEN = "synthetic-home-assistant-token-123456789"
TURN_ON_SCHEMA = {
    "type": "object",
    "properties": {
        "area": {"type": "string"},
        "name": {"type": "string"},
    },
}
CONTEXT_SCHEMA = {"type": "object", "properties": {}}


@dataclass
class _ServerState:
    schemas: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "GetLiveContext": CONTEXT_SCHEMA,
            "HassTurnOn": TURN_ON_SCHEMA,
        }
    )
    calls: list[dict[str, Any]] = field(default_factory=list)
    auth_headers: list[str | None] = field(default_factory=list)
    light_state: str = "off"


@contextmanager
def _server(state: _ServerState) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_POST(self) -> None:  # noqa: N802
            assert self.path == "/api/mcp/assist"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(payload)
            state.auth_headers.append(self.headers.get("Authorization"))
            method = payload["method"]
            if method == "initialize":
                self._json(
                    payload["id"],
                    {
                        "capabilities": {"tools": {}},
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "home-assistant", "version": "2026.8.1"},
                    },
                )
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "tools/list":
                self._json(
                    payload["id"],
                    {
                        "tools": [
                            {
                                "description": f"Synthetic {name}",
                                "inputSchema": schema,
                                "name": name,
                            }
                            for name, schema in state.schemas.items()
                        ]
                    },
                )
                return
            if method == "tools/call":
                name = payload["params"]["name"]
                if name == "HassTurnOn":
                    state.light_state = "on"
                    result = "Done"
                else:
                    result = "Live Context: test light is off"
                self._json(
                    payload["id"],
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"success": True, "result": result},
                                    separators=(",", ":"),
                                ),
                            }
                        ]
                    },
                )
                return
            raise AssertionError(method)

        def _json(self, request_id: int, result: dict[str, Any]) -> None:
            body = json.dumps(
                {"id": request_id, "jsonrpc": "2.0", "result": result},
                separators=(",", ":"),
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _client(origin: str) -> HomeAssistantOfficialMcpClient:
    return HomeAssistantOfficialMcpClient(
        HomeAssistantOfficialMcpConfig(origin, TOKEN)
    )


def _turn_on_admission() -> AssistToolAdmission:
    return AssistToolAdmission(
        "HassTurnOn",
        "effect",
        canonical_sha256(TURN_ON_SCHEMA),
        required_arguments=("name",),
    )


def test_attests_official_catalog_and_calls_one_read_tool() -> None:
    state = _ServerState()
    with _server(state) as origin:
        client = _client(origin)
        catalog = client.catalog()
        context = catalog.exact("GetLiveContext")
        result = client.call(
            admission=AssistToolAdmission(
                "GetLiveContext",
                "read",
                context.input_schema_sha256,
            ),
            arguments={},
            expected_catalog_sha256=catalog.sha256,
        )

    assert result.result == "Live Context: test light is off"
    assert all(value == f"Bearer {TOKEN}" for value in state.auth_headers)
    assert [call["method"] for call in state.calls].count("tools/call") == 1


def test_repairs_missing_required_boundary_in_local_admission() -> None:
    state = _ServerState()
    with _server(state) as origin:
        client = _client(origin)
        catalog = client.catalog()
        before = len(state.calls)
        with pytest.raises(HomeAssistantOfficialMcpError, match="required argument"):
            client.call(
                admission=_turn_on_admission(),
                arguments={"area": "woonkamer"},
                expected_catalog_sha256=catalog.sha256,
            )
    assert all(call["method"] != "tools/call" for call in state.calls[before:])


def test_rejects_catalog_drift_before_effect_call() -> None:
    state = _ServerState()
    with _server(state) as origin:
        client = _client(origin)
        catalog = client.catalog()
        state.schemas["HassTurnOff"] = TURN_ON_SCHEMA
        with pytest.raises(HomeAssistantOfficialMcpError, match="catalog drifted"):
            client.call(
                admission=_turn_on_admission(),
                arguments={"name": "testlamp"},
                expected_catalog_sha256=catalog.sha256,
            )
    assert [call["method"] for call in state.calls].count("tools/call") == 0


@dataclass
class _StateReader:
    state_source: _ServerState

    async def state(self, entity_id: str) -> dict[str, Any]:
        return {"entity_id": entity_id, "state": self.state_source.light_state}


def test_generic_effect_uses_mcp_then_independent_state_readback() -> None:
    state = _ServerState()
    with _server(state) as origin:
        client = _client(origin)
        catalog = client.catalog()
        adapter = HomeAssistantOfficialMcpEffectAdapter(
            client,
            catalog_sha256=catalog.sha256,
            admissions=(_turn_on_admission(),),
            state_reader=_StateReader(state),
        )
        command = ToolRequest(
            "thread:test",
            "operation:test",
            "checkpoint:test",
            "home_assistant.assist.HassTurnOn",
            {
                "mcp_arguments": {"name": "testlamp"},
                "mcp_tool_name": "HassTurnOn",
                "readback_entity_id": "light.testlamp",
            },
            {"entity_id": "light.testlamp", "state": "on"},
        )
        dispatch = asyncio.run(adapter.execute(command))
        proof = asyncio.run(adapter.observe(command, dispatch))

    assert dispatch.accepted is True
    assert proof.disposition == "verified"
    assert proof.observed == {"entity_id": "light.testlamp", "state": "on"}


def test_unadmitted_tool_is_rejected_without_network() -> None:
    client = _client("http://127.0.0.1:65530")
    adapter = HomeAssistantOfficialMcpEffectAdapter(
        client,
        catalog_sha256="a" * 64,
        admissions=(_turn_on_admission(),),
        state_reader=_StateReader(_ServerState()),
    )
    command = ToolRequest(
        "thread:test",
        "operation:test",
        "checkpoint:test",
        "home_assistant.assist.HassUnlock",
        {
            "mcp_arguments": {"name": "voordeur"},
            "mcp_tool_name": "HassUnlock",
            "readback_entity_id": "lock.voordeur",
        },
        {"entity_id": "lock.voordeur", "state": "unlocked"},
    )
    with pytest.raises(Exception, match="not admitted"):
        asyncio.run(adapter.execute(command))
