from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    result_id: str = "light.test"
    failed: list[dict[str, str]] = field(default_factory=list)
    omit_data: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    rest_services_calls: int = 0


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
            method = body["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {"tools": [{"name": "HassTurnOff", "inputSchema": state.schema}]}
            elif method == "tools/call":
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
