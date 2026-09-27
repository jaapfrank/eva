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


@pytest.mark.parametrize("limit", [8192, 8193])
def test_background_reader_uses_real_adapter_validation_without_provider_replay(limit):
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
    output = {"text": "stored document " + "x" * 90000}
    adapter, command, provider_calls, initial = setup(output)
    checked, dispatched = [], []
    with background_execution(BackgroundExecutionScope(TURN, "work:original", lambda turn: dispatched.append(turn))):
        capture = ConnectorResults(initial.timeline, TURN, lambda turn: checked.append(turn))
        with result_capture(capture):
            manifest = asyncio.run(adapter.execute(command)).details["invocation_response"]
        route = PostgresExternalConversationTools(None,
            SimpleNamespace(action_names=("use_connector_tool",)),
            SimpleNamespace(supports_stored_results=True), authorize_turn=lambda turn: checked.append(turn))
        route._timeline = lambda turn: capture.timeline
        route._ensure_running = lambda turn: checked.append(turn)
        actions = BackgroundConnectorActions((route,), ("use_connector_tool",), lambda turn: checked.append(turn), 6)
        call = ConversationActionCall("read-saved", "use_connector_tool", {
            "tool_id": manifest["reader"]["tool_id"],
            "arguments_json": json.dumps({"pointer": "/text", "offset": 0, "limit": limit, "query": None})})
        if limit == 8192:
            result = actions.execute_step(TURN, call, step_index=4)
            assert result.output["result"]["text"] == output["text"][:8192]
        else:
            receipt = actions.execute_step(TURN, call, step_index=4)
            assert receipt.output["status"] == "selection_invalid"
            assert receipt.output["arguments_schema"]["oneOf"][0]["properties"]["limit"]["maximum"] == 8192
            assert actions.can_continue_after(TURN, receipt)
        assert actions.calls == 1
        assert checked and len(dispatched) == 1
        assert len(provider_calls) == 1


def test_batch_reads_share_one_capture_and_record_complete_bounded_receipt():
    output = {"text": "A" * 5000 + "SECTION TWO" + "B" * 85000}
    adapter, command, calls, capture = setup(output)
    with result_capture(capture):
        manifest = asyncio.run(adapter.execute(command)).details["invocation_response"]
    reader = manifest["reader"]["tool_id"]
    selections = [{"pointer": "/text", "offset": 0, "limit": 4096, "query": query}
                  for query in [None, "SECTION TWO"]]
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._timeline = lambda turn: capture.timeline
    route._ensure_running = lambda turn: None
    call = ConversationActionCall("batch", "use_connector_tool", {
        "tool_id": reader, "arguments_json": json.dumps({"selections": selections})})
    receipt = route.execute(TURN, call)
    result = receipt.output["result"]
    assert result["contract"] == "EvaStoredConnectorPages/v1"
    assert result["selected_characters"] == 8192
    assert result["pages"][1]["text"].startswith("SECTION TWO")
    assert all(page["sha256"] == manifest["sha256"] for page in result["pages"])
    assert len(_encoded(result)) <= 65536
    assert route.execute(TURN, call) == receipt
    records = [e for rows in capture.timeline.rows.values() for e in rows if e.event_type == "CONNECTOR_SELECTION_READ"]
    assert len(records) == 1 and records[0].payload["data"]["output"] == result
    assert records[0].payload["data"]["arguments"] == {"selections": selections}
    assert len(calls) == 1
    single = capture.read(reader, selections[0])
    assert single == result["pages"][0]
    assert "pages" not in single


@pytest.mark.parametrize("case", ["empty", "too_many", "aggregate", "type", "late_pointer", "late_bounds"])
def test_invalid_batch_never_records_partial_success(case):
    from eva.adapters.postgres.connector_results import ConnectorSelectionError
    adapter, command, calls, capture = setup({"text": "x" * 90000})
    with result_capture(capture):
        reader = asyncio.run(adapter.execute(command)).details["invocation_response"]["reader"]["tool_id"]
    first = {"pointer": "/text", "offset": 0, "limit": 4096, "query": None}
    cases = {"empty": [], "too_many": [dict(first, limit=1)] * 9,
             "aggregate": [first, dict(first, limit=4097)], "type": {},
             "late_pointer": [first, dict(first, pointer="/absent")],
             "late_bounds": [first, dict(first, offset=-1)]}
    with pytest.raises(ConnectorSelectionError):
        capture.read_recorded(reader, {"selections": cases[case]}, "invalid-batch")
    assert not any(e.event_type == "CONNECTOR_SELECTION_READ" for rows in capture.timeline.rows.values() for e in rows)
    assert len(calls) == 1


@pytest.mark.parametrize("count", [1, 8])
def test_batch_count_boundaries_and_missing_search_are_explicit(count):
    adapter, command, calls, capture = setup({"text": "x" * 90000})
    with result_capture(capture):
        reader = asyncio.run(adapter.execute(command)).details["invocation_response"]["reader"]["tool_id"]
    args = {"selections": [{"pointer": "/text", "offset": 0, "limit": 8192 // count, "query": "absent"}] * count}
    result = capture.read_recorded(reader, args, "missing")
    assert len(result["pages"]) == count and result["selected_characters"] == 0
    assert all(not page["found"] and not page["has_more"] for page in result["pages"])
    assert len(calls) == 1
