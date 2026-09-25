"""Stored connector pages preserve bytes without provider replay or new tools."""
import asyncio
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.connector_results import ConnectorResults, result_capture, _encoded
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.connectors.mcp_gateway import McpGatewayToolAdapter
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.application.orchestration import ToolRequest
from tests.connectors.test_mcp_discovery import TURN, SCHEMA, _binding


class Timeline:
    def __init__(self): self.rows = {}
    def events(self, key): return tuple(self.rows.get(key, ()))
    def load(self, key): return SimpleNamespace(revision=len(self.rows[key]))
    def create_thread(self, **kw):
        assert kw["thread_id"] not in self.rows
        self.rows[kw["thread_id"]] = []
        return self.transition(**kw, expected_revision=0)
    def transition(self, **kw):
        rows = self.rows[kw["thread_id"]]
        assert len(rows) == kw["expected_revision"]
        assert len(_encoded(kw["data"])) <= 65536
        rows.append(SimpleNamespace(event_type=kw["event_type"], payload={"data":kw["data"]}))
        return SimpleNamespace(revision=len(rows))


def setup(output):
    binding = _binding("read")
    spec = replace(binding.spec, contract="EvaMcpGatewayProvider/v4", operation_kind="invoke")
    adapter = McpGatewayToolAdapter(binding.config, (spec,))
    adapter._verified_input_schemas[(spec.execute_tool_name, "execute")] = SCHEMA
    calls = []
    adapter._call_tool = lambda *args: calls.append(args) or output
    command = ToolRequest("thread", "operation", "checkpoint", spec.execute_tool_name,
        {"key":"value"}, {}, observation_tool_name=spec.observe_tool_name,
        observation_arguments={"key":"value"}, tool_configuration_sha256=spec.configuration_sha256)
    timeline = Timeline()
    capture = ConnectorResults(timeline, TURN, lambda _: None)
    return adapter, command, calls, capture


def test_large_result_pages_and_crash_readback_never_repeat_invoke():
    output = {"text": "intro\n" + "a"*80000 + "SECTION marker\n" + "b"*10000}
    adapter, command, calls, capture = setup(output)
    with result_capture(capture):
        dispatch = asyncio.run(adapter.execute(command))
        manifest = dispatch.details["invocation_response"]
        assert manifest["stored_complete"] and manifest["has_more"]
        assert manifest["byte_length"] == len(_encoded(output))
        assert manifest["sha256"] == hashlib.sha256(_encoded(output)).hexdigest()
        assert len(_encoded(manifest)) <= 65536
        observation = asyncio.run(adapter.observe(command, None))
        assert observation.evidence["observation"] == manifest
        assert asyncio.run(adapter.execute(command)) == dispatch
    reader = manifest["reader"]["tool_id"]
    first = capture.read(reader, {"pointer":"/text", "offset":0, "limit":100, "query":None})
    section = capture.read(reader, {"pointer":"/text", "offset":0, "limit":40, "query":"SECTION marker"})
    assert first["text"] == output["text"][:100]
    assert section["text"].startswith("SECTION marker")
    assert len(calls) == 1
    # Existing action API is intercepted before the external planner or provider.
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._timeline = lambda turn: capture.timeline
    route._ensure_running = lambda turn: None
    receipt = route.execute(TURN, ConversationActionCall("page", "use_connector_tool", {
        "tool_id":reader, "arguments_json":json.dumps({"pointer":"/text","offset":80006,"limit":14,"query":None})}), step_index=2)
    assert receipt.output["result"]["text"] == "SECTION marker"
    assert len(calls) == 1


def test_actor_hash_and_chunk_corruption_are_rejected():
    adapter, command, _, capture = setup({"text":"x"*70000})
    with result_capture(capture):
        manifest = asyncio.run(adapter.execute(command)).details["invocation_response"]
    reader = manifest["reader"]["tool_id"]
    args = {"pointer":"/text","offset":0,"limit":10,"query":None}
    other = ConnectorResults(capture.timeline, replace(TURN, principal_id="another", audience=replace(TURN.audience, principal_id="another", audience_id="person:another")), lambda _: None)
    with pytest.raises(ConversationConflict): other.read(reader,args)
    forged = reader[:-1]+("a" if reader[-1]!="a" else "b")
    with pytest.raises(ConversationConflict): capture.read(forged,args)
    rows = next(iter(capture.timeline.rows.values()))
    rows[1].payload["data"]["bytes"] = "eA=="
    with pytest.raises(ConversationConflict): capture.read(reader,args)


def test_uncertain_invoke_is_not_automatically_repeated():
    adapter, command, calls, capture = setup({"text":"small"})
    def crash(*args):
        calls.append(args)
        raise RuntimeError("connection lost after dispatch")
    adapter._call_tool = crash
    with result_capture(capture):
        with pytest.raises(RuntimeError): asyncio.run(adapter.execute(command))
        with pytest.raises(ConversationConflict, match="uncertain"):
            asyncio.run(adapter.execute(command))
        with pytest.raises(ConversationConflict, match="uncertain"):
            asyncio.run(adapter.observe(command, None))
    assert len(calls) == 1


def test_small_output_shape_and_no_capture_limit_are_unchanged():
    adapter, command, _, capture = setup({"text":"small"})
    with result_capture(capture):
        assert asyncio.run(adapter.execute(command)).details["invocation_response"] == {"text":"small"}
    adapter, command, _, _ = setup({"text":"x"*70000})
    with pytest.raises(RuntimeError, match="65536"):
        asyncio.run(adapter.execute(command))


def test_invalid_provider_output_cannot_be_sealed_or_recovered_as_verified():
    adapter, command, calls, capture = setup({"text":"wrong schema"})
    adapter._verified_output_schemas[command.tool_name] = {
        "type":"object", "properties":{"number":{"type":"integer"}},
        "required":["number"], "additionalProperties":False}
    with result_capture(capture):
        with pytest.raises(ConversationConflict): asyncio.run(adapter.execute(command))
        with pytest.raises(ConversationConflict, match="uncertain"):
            asyncio.run(adapter.observe(command,None))
    assert len(calls) == 1
    assert not any(e.event_type=="CONNECTOR_CAPTURE_SEALED" for events in capture.timeline.rows.values() for e in events)
