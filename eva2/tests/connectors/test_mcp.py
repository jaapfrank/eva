from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal

import pytest

from eva.adapters.connectors.mcp import McpReadOnlyError, McpReadOnlyStreamableHttpAdapter
from eva.contracts.capability import McpReadOnlyToolImport
from eva.contracts.canonical_json import canonical_sha256


@dataclass
class _ServerState:
    response_transport: Literal["json", "sse"] = "json"
    schema_drift: bool = False
    malformed_sse: bool = False
    delay_call_seconds: float = 0
    drip_call_seconds: float = 0
    tool_error: bool = False
    invalid_output: bool = False
    calls: list[tuple[dict[str, Any], dict[str, str]]] = field(default_factory=list)


@contextmanager
def _mcp_server(state: _ServerState) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            message = json.loads(self.rfile.read(size))
            state.calls.append((message, dict(self.headers.items())))
            method = message["method"]
            if method == "initialize":
                self._rpc(
                    message["id"],
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "private-test", "version": "1"},
                    },
                    session=True,
                )
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "tools/list":
                input_schema = _input_schema()
                if state.schema_drift:
                    input_schema = {**input_schema, "required": ["other"]}
                self._rpc(
                    message["id"],
                    {
                        "tools": [
                            {
                                "name": "public.read",
                                "inputSchema": input_schema,
                                "outputSchema": _output_schema(),
                            }
                        ]
                    },
                )
                return
            if method == "tools/call":
                if state.delay_call_seconds:
                    time.sleep(state.delay_call_seconds)
                structured_content: dict[str, str] = {"value": "weather: clear"}
                if state.invalid_output:
                    structured_content = {"other": "not admitted"}
                self._rpc(
                    message["id"],
                    {
                        "content": [{"type": "text", "text": "untrusted text is ignored"}],
                        "structuredContent": structured_content,
                        "isError": state.tool_error,
                    },
                )
                return
            raise AssertionError(f"unexpected method {method!r}")

        def _rpc(self, request_id: int, result: dict[str, Any], *, session: bool = False) -> None:
            message = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}).encode()
            self.send_response(200)
            if session:
                self.send_header("Mcp-Session-Id", "test-session")
            if state.response_transport == "sse":
                self.send_header("Content-Type", "text/event-stream")
                body = b"data: " + message + b"\n\n"
            elif state.malformed_sse:
                self.send_header("Content-Type", "text/event-stream")
                body = b"event: message\n\n"
            else:
                self.send_header("Content-Type", "application/json")
                body = message
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if state.drip_call_seconds and request_id == 3:
                    for byte in body:
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                        time.sleep(state.drip_call_seconds)
                else:
                    self.wfile.write(body)
            except BrokenPipeError:
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _input_schema() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "type": "object",
    }


def _output_schema() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "type": "object",
    }


def _import(endpoint: str, *, timeout_milliseconds: int = 1_000) -> McpReadOnlyToolImport:
    input_schema = _input_schema()
    output_schema = _output_schema()
    return McpReadOnlyToolImport.model_validate(
        {
            "contract": "McpReadOnlyToolImport/v1",
            "schema_version": 1,
            "server_id": "mcp:private-test",
            "transport": "streamable_http",
            "server_image_sha256": "a" * 64,
            "sandbox_profile_sha256": "b" * 64,
            "endpoint": endpoint,
            "tool_name": "public.read",
            "effect_class": "read",
            "resource_id": "resource:weather:fixed",
            "timeout_milliseconds": timeout_milliseconds,
            "maximum_request_bytes": 2_048,
            "maximum_response_bytes": 8_192,
            "input_schema": input_schema,
            "input_schema_sha256": canonical_sha256(input_schema),
            "output_schema": output_schema,
            "output_schema_sha256": canonical_sha256(output_schema),
            "provider_scopes": [],
        }
    )


@pytest.mark.parametrize("transport", ["json", "sse"])
def test_calls_one_fixed_tool_over_real_streamable_http(
    transport: Literal["json", "sse"]
) -> None:
    state = _ServerState(response_transport=transport)
    with _mcp_server(state) as endpoint:
        result = McpReadOnlyStreamableHttpAdapter(_import(endpoint)).read_fixed(
            resource_id="resource:weather:fixed", arguments={"query": "today"}
        )

    assert result.structured_content == {"value": "weather: clear"}
    assert [call[0]["method"] for call in state.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    for message, headers in state.calls:
        serialized = json.dumps(message)
        assert "Authorization" not in headers
        assert "persona" not in serialized.lower()
        assert "memory" not in serialized.lower()
        assert "timeline" not in serialized.lower()
    assert state.calls[1][1]["Mcp-Session-Id"] == "test-session"
    assert state.calls[2][1]["Mcp-Protocol-Version"] == "2025-06-18"
    assert state.calls[3][0]["params"] == {
        "name": "public.read",
        "arguments": {"query": "today"},
    }


def test_rejects_schema_drift_before_tool_call() -> None:
    state = _ServerState(schema_drift=True)
    with _mcp_server(state) as endpoint, pytest.raises(McpReadOnlyError, match="schema drifted"):
        McpReadOnlyStreamableHttpAdapter(_import(endpoint)).read_fixed(
            resource_id="resource:weather:fixed", arguments={"query": "today"}
        )
    assert [call[0]["method"] for call in state.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_rejects_unadmitted_resource_and_arguments_without_network_call() -> None:
    import_ = _import("http://127.0.0.1:65530/mcp")
    adapter = McpReadOnlyStreamableHttpAdapter(import_)
    with pytest.raises(McpReadOnlyError, match="not admitted"):
        adapter.read_fixed(resource_id="resource:other", arguments={"query": "today"})
    with pytest.raises(McpReadOnlyError, match="outside the admitted"):
        adapter.read_fixed(
            resource_id="resource:weather:fixed", arguments={"other": "value"}
        )


def test_total_lifecycle_deadline_is_enforced_against_real_server() -> None:
    state = _ServerState(delay_call_seconds=1.1)
    with _mcp_server(state) as endpoint, pytest.raises(McpReadOnlyError, match="failed safely"):
        McpReadOnlyStreamableHttpAdapter(
            _import(endpoint, timeout_milliseconds=1_000)
        ).read_fixed(resource_id="resource:weather:fixed", arguments={"query": "today"})


def test_rejects_malformed_sse() -> None:
    state = _ServerState(malformed_sse=True)
    with _mcp_server(state) as endpoint, pytest.raises(McpReadOnlyError, match="SSE"):
        McpReadOnlyStreamableHttpAdapter(_import(endpoint)).read_fixed(
            resource_id="resource:weather:fixed", arguments={"query": "today"}
        )


def test_total_deadline_cannot_be_bypassed_by_a_drip_body() -> None:
    state = _ServerState(drip_call_seconds=0.02)
    with _mcp_server(state) as endpoint, pytest.raises(McpReadOnlyError, match="time budget"):
        McpReadOnlyStreamableHttpAdapter(
            _import(endpoint, timeout_milliseconds=1_000)
        ).read_fixed(resource_id="resource:weather:fixed", arguments={"query": "today"})


def test_rejects_unhashable_required_schema_without_typeerror() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [{}],
    }
    input_schema = _input_schema()
    with pytest.raises(McpReadOnlyError, match="invalid required"):
        McpReadOnlyStreamableHttpAdapter(
            McpReadOnlyToolImport.model_validate(
                {
                    **_import("http://127.0.0.1:1/mcp").model_dump(mode="json"),
                    "input_schema": schema,
                    "input_schema_sha256": canonical_sha256(schema),
                }
            )
        )
    assert input_schema["required"] == ["query"]


@pytest.mark.parametrize(
    ("state", "match"),
    [
        (_ServerState(tool_error=True), "execution error"),
        (_ServerState(invalid_output=True), "outside the admitted"),
    ],
)
def test_rejects_untrusted_tool_errors_and_output_drift(
    state: _ServerState, match: str
) -> None:
    with _mcp_server(state) as endpoint, pytest.raises(McpReadOnlyError, match=match):
        McpReadOnlyStreamableHttpAdapter(_import(endpoint)).read_fixed(
            resource_id="resource:weather:fixed", arguments={"query": "today"}
        )
