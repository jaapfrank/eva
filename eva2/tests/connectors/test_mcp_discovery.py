from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayArgumentBinding, McpGatewayConfig, McpGatewayProofBinding,
    McpGatewayToolAdapter, McpGatewayToolSpec, mcp_gateway_catalog_binding,
)
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationToolDescriptor, ConversationTurn
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_discovery import McpDiscoveryToolAdapter, McpExecutionBinding


TURN = ConversationTurn.private("tenant", "installation", "owner", "chat", "message", "Gebruik de dienst.", "2026-09-09T12:00:00Z")
SCHEMA = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False}
WRITE_SCHEMA = {**SCHEMA, "properties": {**SCHEMA["properties"], "value": {"type": "string"}}, "required": ["key", "value"]}


def _row(name, schema):
    return {"name": name, "input_schema": schema, "annotations": {"readOnlyHint": True},
            "enabled": True, "reachable": True, "deprecated": False,
            "integration_type": "MCP", "request_type": "STREAMABLEHTTP", "visibility": "private"}


ROWS = [_row("service.lookup", SCHEMA), _row("service.change", WRITE_SCHEMA)]


def _binding(mode):
    effect = mode == "effect"
    execute = ROWS[1] if effect else ROWS[0]
    observe = ROWS[0]
    schema = WRITE_SCHEMA if effect else SCHEMA
    spec = McpGatewayToolSpec(
        descriptor=ConversationToolDescriptor(f"registered_{mode}", "An unknown configured service tool.", schema),
        execute_tool_name=execute["name"], observe_tool_name=observe["name"],
        execute_catalog_name=execute["name"], observe_catalog_name=observe["name"],
        execute_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(execute, contract="EvaMcpGatewayProvider/v3")),
        observe_catalog_sha256=canonical_sha256(mcp_gateway_catalog_binding(observe, contract="EvaMcpGatewayProvider/v3")),
        proof_bindings=(McpGatewayProofBinding("value", "value"),) if effect else (),
        contract="EvaMcpGatewayProvider/v3", operation_kind=mode,
        execute_argument_bindings=tuple(McpGatewayArgumentBinding(n, n) for n in schema["properties"]),
        observe_argument_bindings=(McpGatewayArgumentBinding("key", "key"),),
    )
    return McpExecutionBinding(spec, McpGatewayConfig("https://gateway.invalid", "private-token"), "connector-1", f"tool-{mode}")


class Registry:
    def __init__(self):
        self.by_tool = {binding.tool_id: binding for binding in (_binding("read"), _binding("effect"))}
        self.by_hash = {b.spec.configuration_sha256: b for b in self.by_tool.values()}
        self.resolved = []
        self.revoked = False

    def resolve_for_turn(self, turn, tool_id):
        assert turn.principal_id == "owner"
        if self.revoked:
            raise ConversationConflict("connector grant revoked")
        return self.by_tool[tool_id]

    def resolve_configuration(self, sha, *, phase):
        binding = self.by_hash[sha]
        if self.revoked and (phase == "execute" or binding.spec.operation_kind == "read"):
            raise ConversationConflict("connector grant revoked")
        self.resolved.append((sha, phase))
        return binding


def _request(prepared):
    return ToolRequest(
        "thread", "operation", "checkpoint", prepared.tool_name, prepared.arguments,
        prepared.expected_proof, observation_tool_name=prepared.observation_tool_name,
        observation_arguments=prepared.observation_arguments,
        tool_configuration_sha256=prepared.tool_configuration_sha256,
    )


def test_metatool_reads_writes_and_resumes_exact_registered_configuration(monkeypatch):
    registry = Registry()
    calls, catalog_reads, state = [], [], {"entry": "before"}
    def request_json(self, method, path, payload):
        if method == "GET":
            catalog_reads.append(path)
            return {"tools": ROWS, "nextCursor": None}
        params = payload["params"]
        calls.append(params)
        args = params["arguments"]
        if params["name"] == "service.change":
            state[args["key"]] = args["value"]
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"value": state[args["key"]]}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(registry)
    assert adapter.conversation_tools()[0].name == "use_connector_tool"
    for mode, arguments in (("read", {"key": "entry"}), ("effect", {"key": "entry", "value": "after"})):
        prepared = asyncio.run(adapter.planner().prepare(
            TURN, ConversationActionCall(f"call-{mode}", "use_connector_tool",
                                         {"tool_id": f"tool-{mode}", "arguments_json": json.dumps(arguments)}),
        ))
        assert prepared.receipt_output["connector_id"] == "connector-1"
        assert prepared.receipt_output["tool_id"] == f"tool-{mode}"
        command = _request(prepared)
        dispatch = asyncio.run(adapter.execute(command))
        if mode == "read":
            assert calls == []
            assert prepared.observation_output_key == "result"
        else:
            assert prepared.arguments == {"key": "entry", "value": "after"}
            assert prepared.observation_arguments == {"key": "entry"}
            assert prepared.expected_proof == {"value": "after"}
            assert prepared.observation_output_key is None
            # The current name now points elsewhere. Replay must keep the admitted hash.
            binding = registry.by_tool["tool-effect"]
            new_spec = replace(binding.spec, descriptor=replace(binding.spec.descriptor, description="New generation."))
            registry.by_tool["tool-effect"] = replace(binding, spec=new_spec)
            adapter = McpDiscoveryToolAdapter(registry)  # also exercise an empty post-restart cache
        observed = asyncio.run(adapter.observe(command, dispatch))
        if mode == "read":
            assert observed.evidence["observation"] == {"value": "before"}
            assert observed.observed == {}
        else:
            assert observed.observed == {"value": "after"}
            assert registry.resolved[-1] == (binding.spec.configuration_sha256, "observe")
    assert [c["name"] for c in calls] == ["service.lookup", "service.change", "service.lookup"]
    assert len(catalog_reads) == 3  # first read, first effect, resumed old effect
    count = len(calls)
    registry.by_hash[command.tool_configuration_sha256] = registry.by_tool["tool-effect"]
    with pytest.raises(RuntimeError, match="configuration binding differs"):
        asyncio.run(adapter.observe(command, None))
    registry.revoked = True
    with pytest.raises(ConversationConflict, match="grant revoked"):
        asyncio.run(adapter.execute(command))
    assert len(calls) == count


@pytest.mark.parametrize("raw", ["[]", "{broken}", '{"key":"a","key":"b"}', '{"key":NaN}', "{}" + " " * 65_536])
def test_metatool_rejects_invalid_json_before_registry_or_gateway(raw):
    class UnusedRegistry:
        def resolve_for_turn(self, *_args):
            pytest.fail("invalid JSON reached the registry")
    adapter = McpDiscoveryToolAdapter(UnusedRegistry())
    with pytest.raises(ConversationConflict, match="JSON|arguments"):
        asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "call", "use_connector_tool", {"tool_id": "tool-read", "arguments_json": raw},
        )))


def test_metatool_enforces_the_registered_input_schema(monkeypatch):
    calls = []
    def request_json(self, method, path, payload):
        calls.append(method)
        return {"tools": ROWS, "nextCursor": None}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(Registry())
    with pytest.raises(ConversationConflict, match="fields are invalid"):
        asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "call", "use_connector_tool", {"tool_id": "tool-read", "arguments_json": '{"key":"entry","extra":true}'},
        )))
    assert calls == ["GET"]


def test_revocation_blocks_dispatch_and_new_reads_but_allows_admitted_effect_readback(monkeypatch):
    registry = Registry()
    calls = []
    def request_json(self, method, path, payload):
        if method == "GET":
            return {"tools": ROWS, "nextCursor": None}
        calls.append(payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"value": "already changed"}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    adapter = McpDiscoveryToolAdapter(registry)
    commands = {}
    for mode, arguments in (("read", {"key": "entry"}),
                            ("effect", {"key": "entry", "value": "already changed"})):
        prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            f"call-{mode}", "use_connector_tool",
            {"tool_id": f"tool-{mode}", "arguments_json": json.dumps(arguments)},
        )))
        commands[mode] = _request(prepared)
    dispatch = asyncio.run(adapter.execute(commands["effect"]))
    assert calls == [{"name": "service.change", "arguments": {"key": "entry", "value": "already changed"}}]
    registry.revoked = True
    for command in commands.values():
        with pytest.raises(ConversationConflict, match="revoked"):
            asyncio.run(adapter.execute(command))
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.observe(commands["read"], None))
    observation = asyncio.run(adapter.observe(commands["effect"], dispatch))
    assert observation.observed == {"value": "already changed"}
    assert [call["name"] for call in calls] == ["service.change", "service.lookup"]
    assert registry.resolved[-1] == (commands["effect"].tool_configuration_sha256, "observe")
