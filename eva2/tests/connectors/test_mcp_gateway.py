from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayConfig,
    McpGatewayProofBinding,
    McpGatewayToolAdapter,
    McpGatewayToolSpec,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationTurn,
)
from eva.application.memory_audience import MemoryAudience
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256


SCHEMA = {
    "additionalProperties": False,
    "properties": {"message": {"maxLength": 80, "minLength": 1, "type": "string"}},
    "required": ["message"],
    "type": "object",
}


def _catalog_row(name: str = "proof-echo") -> dict[str, object]:
    return {
        "annotations": {"readOnlyHint": True},
        "deprecated": False,
        "enabled": True,
        "input_schema": SCHEMA,
        "integration_type": "MCP",
        "name": name,
        "reachable": True,
        "request_type": "STREAMABLEHTTP",
        "visibility": "public",
    }


def _catalog_hash(row: dict[str, object]) -> str:
    return canonical_sha256(row)  # selected test row already has only bound fields


def _spec(*, catalog_hash: str | None = None) -> McpGatewayToolSpec:
    digest = catalog_hash or _catalog_hash(_catalog_row())
    return McpGatewayToolSpec(
        descriptor=ConversationToolDescriptor(
            name="echo_proof",
            description="Echo one short message through the configured generic tool.",
            input_schema=SCHEMA,
        ),
        execute_tool_name="proof-echo",
        observe_tool_name="proof-echo",
        execute_catalog_name="proof-echo",
        observe_catalog_name="proof-echo",
        execute_catalog_sha256=digest,
        observe_catalog_sha256=digest,
        proof_bindings=(McpGatewayProofBinding("message", "text"),),
    )


def _turn() -> ConversationTurn:
    return ConversationTurn(
        tenant_id="tenant-test",
        installation_id="installation-test",
        principal_id="principal-test",
        conversation_id="conversation-test",
        message_id="message-test",
        text="echo dit",
        occurred_at="2026-08-30T19:30:00Z",
        audience=MemoryAudience(
            kind="private_person",
            audience_id="person:principal-test",
            principal_id="principal-test",
        ),
    )


class _GatewayHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []
    catalog: list[dict[str, object]] = [_catalog_row()]
    reject = False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/tools":
            self.send_error(404)
            return
        self._json(self.catalog)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rpc":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.calls.append(body)
        if self.reject:
            self._json(
                {
                    "id": body["id"],
                    "jsonrpc": "2.0",
                    "result": {"content": [], "isError": True},
                }
            )
            return
        self._json(
            {
                "id": body["id"],
                "jsonrpc": "2.0",
                "result": {
                    "content": [
                        {"text": body["params"]["arguments"]["message"], "type": "text"}
                    ],
                    "isError": False,
                },
            }
        )

    def log_message(self, *_args: object) -> None:
        return

    def _json(self, value: object) -> None:
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def _gateway() -> tuple[str, type[_GatewayHandler]]:
    _GatewayHandler.calls = []
    _GatewayHandler.catalog = [_catalog_row()]
    _GatewayHandler.reject = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _GatewayHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _adapter(
    base_url: str, *, catalog_hash: str | None = None
) -> McpGatewayToolAdapter:
    return McpGatewayToolAdapter(
        McpGatewayConfig(base_url, "private-token", allow_plain_http=True),
        (_spec(catalog_hash=catalog_hash),),
    )


def test_gateway_config_requires_explicit_plain_http_and_hides_token() -> None:
    with pytest.raises(ValueError, match="URL"):
        McpGatewayConfig("http://contextforge:4444", "secret")
    config = McpGatewayConfig(
        "http://contextforge:4444", "secret", allow_plain_http=True
    )
    assert "secret" not in repr(config)


def test_catalog_is_hash_bound_and_must_be_reachable_mcp() -> None:
    with _gateway() as (base_url, handler):
        adapter = _adapter(base_url)
        verified = asyncio.run(adapter.verify_catalog())
        assert verified == {"proof-echo": _catalog_hash(_catalog_row())}

        handler.catalog[0]["reachable"] = False
        with pytest.raises(RuntimeError, match="safely executable"):
            asyncio.run(adapter.verify_catalog())

        handler.catalog = [_catalog_row()]
        with pytest.raises(RuntimeError, match="sealed catalog"):
            asyncio.run(_adapter(base_url, catalog_hash="0" * 64).verify_catalog())

        handler.catalog[0]["visibility"] = "private"
        with pytest.raises(RuntimeError, match="sealed catalog"):
            asyncio.run(_adapter(base_url).verify_catalog())


def test_catalog_accepts_contextforge_wire_aliases_but_hashes_one_shape() -> None:
    row = _catalog_row()
    row["integrationType"] = row.pop("integration_type")
    row["requestType"] = row.pop("request_type")
    row["inputSchema"] = row.pop("input_schema")
    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        assert asyncio.run(_adapter(base_url).verify_catalog()) == {
            "proof-echo": _catalog_hash(_catalog_row())
        }


def test_planner_validates_closed_arguments_and_binds_expected_proof() -> None:
    planner = McpGatewayToolAdapter(
        McpGatewayConfig("https://gateway.example", "token"), (_spec(),)
    ).planner()
    preparation = asyncio.run(
        planner.prepare(
            _turn(), ConversationActionCall("call-1", "echo_proof", {"message": "hoi"})
        )
    )
    assert preparation.tool_name == "proof-echo"
    assert preparation.expected_proof == {"text": "hoi"}
    assert preparation.receipt_output == {
        "catalog_sha256": _spec().execute_catalog_sha256,
        "tool_configuration_sha256": _spec().configuration_sha256,
        "transport": "mcp_gateway",
    }

    with pytest.raises(ConversationConflict, match="fields"):
        asyncio.run(
            planner.prepare(
                _turn(),
                ConversationActionCall(
                    "call-2", "echo_proof", {"message": "hoi", "extra": True}
                ),
            )
        )


def test_execute_and_independent_observe_use_gateway_rpc_twice() -> None:
    with _gateway() as (base_url, handler):
        adapter = _adapter(base_url)
        request = ToolRequest(
            thread_id="thread-1",
            operation_id="operation-1",
            checkpoint_prefix="checkpoint-1",
            tool_name="proof-echo",
            arguments={"message": "koperen maan"},
            expected_proof={"text": "koperen maan"},
        )
        dispatch = asyncio.run(adapter.execute(request))
        observation = asyncio.run(adapter.observe(request, dispatch))

        assert dispatch.accepted is True
        assert observation.observed == {"text": "koperen maan"}
        assert observation.evidence["source"] == "mcp_gateway_readback"
        assert len(handler.calls) == 2
        assert all(call["method"] == "tools/call" for call in handler.calls)
        assert all(call["params"]["name"] == "proof-echo" for call in handler.calls)


def test_gateway_tool_error_is_a_definitive_rejection() -> None:
    with _gateway() as (base_url, handler):
        handler.reject = True
        request = ToolRequest(
            thread_id="thread-1",
            operation_id="operation-1",
            checkpoint_prefix="checkpoint-1",
            tool_name="proof-echo",
            arguments={"message": "weigeren"},
            expected_proof={"text": "weigeren"},
        )
        with pytest.raises(Exception, match="rejected") as raised:
            asyncio.run(_adapter(base_url).execute(request))
        assert raised.type.__name__ == "ToolExecutionRejected"
