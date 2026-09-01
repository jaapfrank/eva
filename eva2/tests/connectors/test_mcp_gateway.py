from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from eva.adapters.connectors import mcp_gateway as mcp_gateway_module
from eva.adapters.connectors.mcp_gateway import (
    McpGatewayArgumentBinding,
    McpGatewayConfig,
    McpGatewayProofBinding,
    McpGatewayToolAdapter,
    McpGatewayToolPlanner,
    McpGatewayToolSpec,
    mcp_gateway_catalog_binding,
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


def _catalog_row(
    name: str = "proof-echo",
    *,
    schema: dict[str, object] = SCHEMA,
    read_only: bool = True,
) -> dict[str, object]:
    return {
        "annotations": {"readOnlyHint": read_only},
        "deprecated": False,
        "enabled": True,
        "input_schema": schema,
        "integration_type": "MCP",
        "name": name,
        "reachable": True,
        "request_type": "STREAMABLEHTTP",
        "visibility": "public",
    }


def _catalog_hash(row: dict[str, object]) -> str:
    return canonical_sha256(
        mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v2")
    )


def _spec(*, catalog_hash: str | None = None) -> McpGatewayToolSpec:
    digest = catalog_hash or _catalog_hash(_catalog_row())
    return McpGatewayToolSpec(
        contract="EvaMcpGatewayProvider/v2",
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
        execute_argument_bindings=(McpGatewayArgumentBinding("message", "message"),),
        observe_argument_bindings=(McpGatewayArgumentBinding("message", "message"),),
        proof_bindings=(McpGatewayProofBinding("message", "text"),),
    )


def _v1_spec(*, description: str | None = None) -> McpGatewayToolSpec:
    base = _spec()
    return McpGatewayToolSpec(
        contract="EvaMcpGatewayProvider/v1",
        descriptor=ConversationToolDescriptor(
            name=base.descriptor.name,
            description=description or base.descriptor.description,
            input_schema=base.descriptor.input_schema,
        ),
        execute_tool_name=base.execute_tool_name,
        observe_tool_name=base.observe_tool_name,
        execute_catalog_name=base.execute_catalog_name,
        observe_catalog_name=base.observe_catalog_name,
        execute_catalog_sha256=base.execute_catalog_sha256,
        observe_catalog_sha256=base.observe_catalog_sha256,
        execute_argument_bindings=base.execute_argument_bindings,
        observe_argument_bindings=base.observe_argument_bindings,
        proof_bindings=base.proof_bindings,
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


def test_legacy_exported_constructors_keep_identity_projection() -> None:
    descriptor = ConversationToolDescriptor(
        name="echo_proof",
        description="Echo one short message through the configured generic tool.",
        input_schema=SCHEMA,
    )
    digest = canonical_sha256(mcp_gateway_catalog_binding(_catalog_row()))
    spec = McpGatewayToolSpec(
        descriptor,
        "proof-echo",
        "proof-echo",
        "proof-echo",
        "proof-echo",
        digest,
        digest,
        (McpGatewayProofBinding("message", "text"),),
    )

    assert spec.contract == "EvaMcpGatewayProvider/v1"
    assert spec.execute_argument_bindings == (
        McpGatewayArgumentBinding("message", "message"),
    )
    assert spec.observe_argument_bindings == spec.execute_argument_bindings

    prepared = asyncio.run(
        McpGatewayToolPlanner((spec,)).prepare(
            _turn(),
            ConversationActionCall(
                call_id="legacy-call",
                name="echo_proof",
                arguments={"message": "hallo"},
            ),
        )
    )
    assert prepared.arguments == {"message": "hallo"}
    assert prepared.observation_arguments == {"message": "hallo"}


@pytest.mark.parametrize(
    "bindings",
    [
        (McpGatewayArgumentBinding("message", "renamed"),),
        (McpGatewayArgumentBinding("message", "message"),),
    ],
)
def test_v1_rejects_unhashed_non_identity_projection(
    bindings: tuple[McpGatewayArgumentBinding, ...],
) -> None:
    descriptor = ConversationToolDescriptor(
        name="two_fields",
        description="Bind two fields without changing historical v1 behavior.",
        input_schema={
            "additionalProperties": False,
            "properties": {
                "message": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["message", "target"],
            "type": "object",
        },
    )
    digest = "a" * 64
    with pytest.raises(ValueError, match="v1 requires identity argument projection"):
        McpGatewayToolSpec(
            descriptor,
            "proof-echo",
            "proof-echo",
            "proof-echo",
            "proof-echo",
            digest,
            digest,
            (McpGatewayProofBinding("message", "text"),),
            execute_argument_bindings=bindings,
            observe_argument_bindings=(
                McpGatewayArgumentBinding("message", "message"),
                McpGatewayArgumentBinding("target", "target"),
            ),
        )


class _GatewayHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []
    catalog: list[dict[str, object]] = [_catalog_row()]
    reject = False
    wrong_id = False

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
                "id": "wrong-id" if self.wrong_id else body["id"],
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
    _GatewayHandler.wrong_id = False
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


@pytest.mark.parametrize("additional_properties", [None, True])
def test_standard_open_provider_schema_is_safe_through_explicit_projection(
    additional_properties: bool | None,
) -> None:
    provider_schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "delay": {"minimum": 0, "type": ["integer", "null"]},
            "message": {"type": "string"},
        },
        "required": ["message"],
        "type": "object",
    }
    if additional_properties is not None:
        provider_schema["additionalProperties"] = additional_properties
    row = _catalog_row(schema=provider_schema)
    spec = _spec(catalog_hash=_catalog_hash(row))

    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        asyncio.run(adapter.verify_catalog())
        prepared = asyncio.run(
            adapter.planner().prepare(
                _turn(),
                ConversationActionCall(
                    "open-schema-call", "echo_proof", {"message": "alleen gebonden"}
                ),
            )
        )
        request = _echo_request(spec, "alleen gebonden")
        dispatch = asyncio.run(adapter.execute(request))
        observation = asyncio.run(adapter.observe(request, dispatch))

    assert prepared.arguments == {"message": "alleen gebonden"}
    assert dispatch.accepted is True
    assert observation.observed == {"text": "alleen gebonden"}
    assert [call["params"]["arguments"] for call in handler.calls] == [
        {"message": "alleen gebonden"},
        {"message": "alleen gebonden"},
    ]


def test_open_provider_schema_still_rejects_unbound_required_field() -> None:
    provider_schema = {
        "properties": {
            "destination": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["destination", "message"],
        "type": "object",
    }
    row = _catalog_row(schema=provider_schema)
    spec = _spec(catalog_hash=_catalog_hash(row))

    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        with pytest.raises(RuntimeError, match="projection is incomplete"):
            asyncio.run(adapter.verify_catalog())


def test_provider_schema_rejects_invalid_additional_properties_keyword() -> None:
    provider_schema = {
        "additionalProperties": "open",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "type": "object",
    }
    row = _catalog_row(schema=provider_schema)
    spec = _spec(catalog_hash=_catalog_hash(row))

    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        with pytest.raises(RuntimeError, match="input schema is unsupported"):
            asyncio.run(adapter.verify_catalog())


@pytest.mark.parametrize("additional_properties", [True, False, {}])
def test_nested_provider_object_accepts_valid_additional_properties_keyword(
    additional_properties: bool | dict[str, object],
) -> None:
    schema = {
        "properties": {
            "nested": {
                "additionalProperties": additional_properties,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "type": "object",
            }
        },
        "required": ["nested"],
        "type": "object",
    }

    mcp_gateway_module._validate_schema_value(
        {"nested": {"value": "begrensd"}},
        schema,
        "provider_arguments",
        provider_schema=True,
    )


@pytest.mark.parametrize("additional_properties", ["open", 1, None])
def test_nested_provider_object_rejects_invalid_additional_properties_keyword(
    additional_properties: object,
) -> None:
    schema = {
        "properties": {
            "nested": {
                "additionalProperties": additional_properties,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "type": "object",
            }
        },
        "required": ["nested"],
        "type": "object",
    }

    with pytest.raises(ConversationConflict, match="fields are invalid"):
        mcp_gateway_module._validate_schema_value(
            {"nested": {"value": "begrensd"}},
            schema,
            "provider_arguments",
            provider_schema=True,
        )


def test_planner_validates_closed_arguments_and_binds_expected_proof() -> None:
    with _gateway() as (base_url, _handler):
        adapter = _adapter(base_url)
        asyncio.run(adapter.verify_catalog())
        planner = adapter.planner()
        preparation = asyncio.run(
            planner.prepare(
                _turn(),
                ConversationActionCall("call-1", "echo_proof", {"message": "hoi"}),
            )
        )
    assert preparation.tool_name == "proof-echo"
    assert preparation.arguments == {"message": "hoi"}
    assert preparation.observation_tool_name == "proof-echo"
    assert preparation.observation_arguments == {"message": "hoi"}
    assert preparation.tool_configuration_sha256 == _spec().configuration_sha256
    assert preparation.expected_proof == {"text": "hoi"}
    assert preparation.receipt_output == {
        "catalog_sha256": _spec().execute_catalog_sha256,
        "tool_configuration_sha256": _spec().configuration_sha256,
        "transport": "mcp_gateway",
    }

    with pytest.raises(ConversationConflict, match="fields"):
        with _gateway() as (base_url, _handler):
            adapter = _adapter(base_url)
            asyncio.run(adapter.verify_catalog())
            asyncio.run(
                adapter.planner().prepare(
                    _turn(),
                    ConversationActionCall(
                        "call-2", "echo_proof", {"message": "hoi", "extra": True}
                    ),
                )
            )


def _echo_request(spec: McpGatewayToolSpec, message: str) -> ToolRequest:
    return ToolRequest(
        thread_id="thread-1",
        operation_id="operation-1",
        checkpoint_prefix="checkpoint-1",
        tool_name="proof-echo",
        arguments={"message": message},
        expected_proof={"text": message},
        observation_tool_name="proof-echo",
        observation_arguments={"message": message},
        tool_configuration_sha256=spec.configuration_sha256,
    )


def test_execute_and_independent_observe_use_gateway_rpc_twice() -> None:
    with _gateway() as (base_url, handler):
        adapter = _adapter(base_url)
        asyncio.run(adapter.verify_catalog())
        request = _echo_request(_spec(), "koperen maan")
        dispatch = asyncio.run(adapter.execute(request))
        observation = asyncio.run(adapter.observe(request, dispatch))

        assert dispatch.accepted is True
        assert observation.observed == {"text": "koperen maan"}
        assert observation.evidence["source"] == "mcp_gateway_readback"
        assert len(handler.calls) == 2
        assert all(call["method"] == "tools/call" for call in handler.calls)
        assert all(call["params"]["name"] == "proof-echo" for call in handler.calls)
        assert handler.calls[0]["id"] != handler.calls[1]["id"]


def test_execute_and_observe_project_different_provider_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "additionalProperties": False,
        "properties": {
            "content": {"minLength": 1, "type": "string"},
            "path": {"minLength": 1, "type": "string"},
        },
        "required": ["content", "path"],
        "type": "object",
    }
    write_schema = schema
    read_schema = {
        "additionalProperties": False,
        "properties": {"path": {"minLength": 1, "type": "string"}},
        "required": ["path"],
        "type": "object",
    }
    write_row = _catalog_row("write_file", schema=write_schema, read_only=False)
    read_row = _catalog_row("read_text_file", schema=read_schema)
    spec = McpGatewayToolSpec(
        contract="EvaMcpGatewayProvider/v2",
        descriptor=ConversationToolDescriptor(
            name="write_document",
            description="Write and independently read one document.",
            input_schema=schema,
        ),
        execute_tool_name="write_file",
        observe_tool_name="read_text_file",
        execute_catalog_name="write_file",
        observe_catalog_name="read_text_file",
        execute_catalog_sha256=_catalog_hash(write_row),
        observe_catalog_sha256=_catalog_hash(read_row),
        execute_argument_bindings=(
            McpGatewayArgumentBinding("path", "path"),
            McpGatewayArgumentBinding("content", "content"),
        ),
        observe_argument_bindings=(McpGatewayArgumentBinding("path", "path"),),
        proof_bindings=(McpGatewayProofBinding("content", "text"),),
    )
    calls: list[tuple[str, dict[str, object], str]] = []

    def call_tool(
        name: str, arguments: dict[str, object], rpc_id: str
    ) -> dict[str, object]:
        calls.append((name, arguments, rpc_id))
        return {"text": "koperen maan"}

    with _gateway() as (base_url, handler):
        handler.catalog = [write_row, read_row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        asyncio.run(adapter.verify_catalog())
        monkeypatch.setattr(adapter, "_call_tool", call_tool)
        request = ToolRequest(
            thread_id="thread-1",
            operation_id="operation-1",
            checkpoint_prefix="checkpoint-1",
            tool_name="write_file",
            arguments={"path": "proof.txt", "content": "koperen maan"},
            expected_proof={"text": "koperen maan"},
            observation_tool_name="read_text_file",
            observation_arguments={"path": "proof.txt"},
            tool_configuration_sha256=spec.configuration_sha256,
        )

        dispatch = asyncio.run(adapter.execute(request))
        observation = asyncio.run(adapter.observe(request, dispatch))

    assert observation.observed == {"text": "koperen maan"}
    assert calls == [
        (
            "write_file",
            {"path": "proof.txt", "content": "koperen maan"},
            calls[0][2],
        ),
        ("read_text_file", {"path": "proof.txt"}, calls[1][2]),
    ]
    assert calls[0][2] != calls[1][2]


def test_empty_durable_observation_arguments_stay_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_schema = {
        "additionalProperties": False,
        "properties": {},
        "required": [],
        "type": "object",
    }
    execute_row = _catalog_row("write_value", read_only=False)
    observe_row = _catalog_row("read_value", schema=empty_schema)
    base = _spec()
    spec = McpGatewayToolSpec(
        contract=base.contract,
        descriptor=base.descriptor,
        execute_tool_name="write_value",
        observe_tool_name="read_value",
        execute_catalog_name="write_value",
        observe_catalog_name="read_value",
        execute_catalog_sha256=_catalog_hash(execute_row),
        observe_catalog_sha256=_catalog_hash(observe_row),
        execute_argument_bindings=base.execute_argument_bindings,
        observe_argument_bindings=(),
        proof_bindings=base.proof_bindings,
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(
        name: str, arguments: dict[str, object], _rpc_id: str
    ) -> dict[str, object]:
        calls.append((name, arguments))
        return {"text": "stil"}

    with _gateway() as (base_url, handler):
        handler.catalog = [execute_row, observe_row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        asyncio.run(adapter.verify_catalog())
        monkeypatch.setattr(adapter, "_call_tool", call_tool)
        request = ToolRequest(
            "thread-1",
            "operation-1",
            "checkpoint-1",
            "write_value",
            {"message": "stil"},
            {"text": "stil"},
            observation_tool_name="read_value",
            observation_arguments={},
            tool_configuration_sha256=spec.configuration_sha256,
        )
        dispatch = asyncio.run(adapter.execute(request))
        asyncio.run(adapter.observe(request, dispatch))

    assert calls == [
        ("write_value", {"message": "stil"}),
        ("read_value", {}),
    ]


def test_legacy_v1_request_requires_its_historical_configuration_hash() -> None:
    original = _v1_spec()
    changed = _v1_spec(description="Changed provider meaning.")
    request = ToolRequest(
        "thread-1",
        "operation-1",
        "checkpoint-1",
        "proof-echo",
        {"message": "legacy"},
        {"text": "legacy"},
        tool_configuration_sha256=original.configuration_sha256,
    )
    with _gateway() as (base_url, _handler):
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (changed,)
        )
        asyncio.run(adapter.verify_catalog())
        with pytest.raises(RuntimeError, match="binding differs"):
            asyncio.run(adapter.execute(request))

        matching = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (original,)
        )
        asyncio.run(matching.verify_catalog())
        dispatch = asyncio.run(matching.execute(request))
        observation = asyncio.run(matching.observe(request, dispatch))
        assert observation.observed == {"text": "legacy"}


def test_argument_projection_rejects_unknown_or_duplicate_bindings() -> None:
    base = _spec()
    with pytest.raises(ValueError, match="projection"):
        McpGatewayToolSpec(
            contract=base.contract,
            descriptor=base.descriptor,
            execute_tool_name=base.execute_tool_name,
            observe_tool_name=base.observe_tool_name,
            execute_catalog_name=base.execute_catalog_name,
            observe_catalog_name=base.observe_catalog_name,
            execute_catalog_sha256=base.execute_catalog_sha256,
            observe_catalog_sha256=base.observe_catalog_sha256,
            execute_argument_bindings=(
                McpGatewayArgumentBinding("missing", "message"),
            ),
            observe_argument_bindings=base.observe_argument_bindings,
            proof_bindings=base.proof_bindings,
        )
    with pytest.raises(ValueError, match="projection"):
        McpGatewayToolSpec(
            contract=base.contract,
            descriptor=base.descriptor,
            execute_tool_name=base.execute_tool_name,
            observe_tool_name=base.observe_tool_name,
            execute_catalog_name=base.execute_catalog_name,
            observe_catalog_name=base.observe_catalog_name,
            execute_catalog_sha256=base.execute_catalog_sha256,
            observe_catalog_sha256=base.observe_catalog_sha256,
            execute_argument_bindings=(
                McpGatewayArgumentBinding("message", "same"),
                McpGatewayArgumentBinding("message", "other"),
            ),
            observe_argument_bindings=base.observe_argument_bindings,
            proof_bindings=base.proof_bindings,
        )


def test_catalog_rejects_projection_that_does_not_cover_provider_schema() -> None:
    provider_schema = {
        "additionalProperties": False,
        "properties": {
            "destination": {"type": "string"},
            "payload": {"type": "string"},
        },
        "required": ["destination", "payload"],
        "type": "object",
    }
    row = _catalog_row("provider-write", schema=provider_schema, read_only=False)
    base = _spec(catalog_hash=_catalog_hash(row))
    spec = McpGatewayToolSpec(
        contract=base.contract,
        descriptor=base.descriptor,
        execute_tool_name=base.execute_tool_name,
        observe_tool_name=base.observe_tool_name,
        execute_catalog_name="provider-write",
        observe_catalog_name="provider-write",
        execute_catalog_sha256=_catalog_hash(row),
        observe_catalog_sha256=_catalog_hash(row),
        execute_argument_bindings=(
            McpGatewayArgumentBinding("message", "misspelled_payload"),
        ),
        observe_argument_bindings=(McpGatewayArgumentBinding("message", "payload"),),
        proof_bindings=base.proof_bindings,
    )
    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        with pytest.raises(RuntimeError, match="projection is incomplete"):
            asyncio.run(adapter.verify_catalog())


def test_required_provider_field_must_come_from_required_eva_input() -> None:
    eva_schema = {
        "additionalProperties": False,
        "properties": {
            "expected": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["expected"],
        "type": "object",
    }
    provider_schema = {
        "additionalProperties": False,
        "properties": {"payload": {"type": "string"}},
        "required": ["payload"],
        "type": "object",
    }
    row = _catalog_row("provider-write", schema=provider_schema, read_only=False)
    spec = McpGatewayToolSpec(
        contract="EvaMcpGatewayProvider/v2",
        descriptor=ConversationToolDescriptor(
            name="optional_message",
            description="Fixture with an optional Eva input.",
            input_schema=eva_schema,
        ),
        execute_tool_name="provider-write",
        observe_tool_name="provider-write",
        execute_catalog_name="provider-write",
        observe_catalog_name="provider-write",
        execute_catalog_sha256=_catalog_hash(row),
        observe_catalog_sha256=_catalog_hash(row),
        execute_argument_bindings=(McpGatewayArgumentBinding("message", "payload"),),
        observe_argument_bindings=(McpGatewayArgumentBinding("message", "payload"),),
        proof_bindings=(McpGatewayProofBinding("expected", "text"),),
    )
    with _gateway() as (base_url, handler):
        handler.catalog = [row]
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(base_url, "token", allow_plain_http=True), (spec,)
        )
        with pytest.raises(RuntimeError, match="projection is incomplete"):
            asyncio.run(adapter.verify_catalog())


def test_gateway_tool_error_is_a_definitive_rejection() -> None:
    with _gateway() as (base_url, handler):
        handler.reject = True
        adapter = _adapter(base_url)
        asyncio.run(adapter.verify_catalog())
        request = _echo_request(_spec(), "weigeren")
        with pytest.raises(Exception, match="rejected") as raised:
            asyncio.run(adapter.execute(request))
        assert raised.type.__name__ == "ToolExecutionRejected"


def test_gateway_rejects_response_for_another_rpc_id() -> None:
    with _gateway() as (base_url, handler):
        handler.wrong_id = True
        adapter = _adapter(base_url)
        asyncio.run(adapter.verify_catalog())
        with pytest.raises(RuntimeError, match="invalid RPC envelope"):
            asyncio.run(adapter.execute(_echo_request(_spec(), "gebonden")))
