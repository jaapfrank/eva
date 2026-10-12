"""Stored connector pages preserve bytes without provider replay or new tools."""
import asyncio
from contextlib import nullcontext
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
    route._execution_lock = lambda key: nullcontext()
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


def _retained_ha():
    """A previously verified response, with no provider available for replay."""
    timeline = Timeline()
    stopped = [False]
    def authorize(turn):
        assert turn == TURN
        if stopped[0]:
            raise ConversationConflict("owner stopped")
    port = PostgresExternalConversationTools(None,
        SimpleNamespace(action_names=("home_assistant_request",)), SimpleNamespace())
    port._timeline = lambda _: timeline
    port._ensure_running = authorize
    thread = port._thread_id(TURN, "home_assistant_request", 1)
    observation = {"response_json": json.dumps(["value-" + str(i) for i in range(4000)]),
                   "truncated": False, "external_state_verified": False}
    admission = {"action_name": "home_assistant_request", "action_call_id": "retained-read",
        "action_arguments": {"method": "GET"}, "step_index": 1,
        "conversation_id": TURN.conversation_id, "principal_id": TURN.principal_id,
        "source_message_id": TURN.message_id, "receipt_output": {"external_state_verified": False},
        "observation_output_key": "result"}
    timeline.rows[thread] = [
        SimpleNamespace(event_type="TOOL_REQUEST_ADMITTED", payload={"data": admission}),
        SimpleNamespace(event_type="STATE_VERIFIED", payload={"data": {"proof": {
            "expected": {}, "observed": {}, "evidence": {
                "source": "home_assistant_api_response", "observation": observation}}}})]
    return port, timeline, thread, stopped, authorize


def test_retained_ha_projection_resume_and_local_reader_preserve_raw_proof():
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _InputBudgetError
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    port, timeline, thread, _, authorize = _retained_ha()
    old = _encoded([e.payload for e in timeline.rows[thread]])
    original = port._receipt(TURN, timeline.events(thread), 1, project=False)
    projected = port._receipt(TURN, timeline.events(thread), 1)
    assert projected.output == original.output
    assert projected.model_output["model_projection"]["source_receipt_sha256"] == canonical_sha256(original.output)
    assert projected.model_output["model_projection"]["source_proof_sha256"] == canonical_sha256(original.output["proof"])
    assert _encoded([e.payload for e in timeline.rows[thread]]) == old
    rows_before = _encoded({k: [e.payload for e in v] for k, v in timeline.rows.items()})
    # Fresh adapter reconstruction is the same route used by pending() on resume.
    assert port._receipt(TURN, timeline.events(thread), 1).model_output == projected.model_output
    assert _encoded({k: [e.payload for e in v] for k, v in timeline.rows.items()}) == rows_before
    model = object.__new__(PydanticConversationModel)
    model._resource_budget = SimpleNamespace(max_input_tokens=4000)
    model._endpoint = "https://chatgpt.com/backend-api/codex/responses"
    body = lambda receipt: model._phase_admission_body(prompt="", instructions="",
                                                      completed_action=receipt)
    with pytest.raises(_InputBudgetError):
        model._admit_request(body(original), autonomous=False)
    model._admit_request(body(projected), autonomous=False)
    assert len(canonical_json_bytes(list(model._receipt_model_views((projected,))))) < 8192
    # Use the actual existing local-reader action, never an external provider.
    route = PostgresExternalConversationTools(None,
        SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._execution_lock = lambda _: nullcontext()
    route._timeline = lambda _: timeline
    route._ensure_running = authorize
    reader = projected.model_output["result"]["reader"]["tool_id"]
    args = {"pointer": "/response_json", "offset": 0, "limit": 40, "query": None}
    call = ConversationActionCall("local-page", "use_connector_tool",
        {"tool_id": reader, "arguments_json": json.dumps(args)})
    receipt = route.execute(TURN, call, step_index=2)
    assert receipt.output["result"]["text"] == original.output["result"]["response_json"][:40]
    assert receipt.output["external_state_verified"] is False
    assert route.execute(TURN, call, step_index=2) == receipt
    assert _encoded([e.payload for e in timeline.rows[thread]]) == old


def test_retained_ha_projection_rejects_changed_scope_source_and_injected_view():
    from eva.adapters.model.contracts import ConversationModelError
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    port, timeline, thread, stopped, authorize = _retained_ha()
    projected = port._receipt(TURN, timeline.events(thread), 1)
    reader = projected.model_output["result"]["reader"]["tool_id"]
    args = {"pointer": "/response_json", "offset": 0, "limit": 40, "query": None}
    capture = ConnectorResults(timeline, TURN, authorize)
    other = ConnectorResults(timeline, replace(TURN, principal_id="other",
        audience=replace(TURN.audience, principal_id="other", audience_id="person:other")), lambda _: None)
    with pytest.raises(ConversationConflict):
        other.read(reader, args)
    changed_ingress = ConnectorResults(timeline, replace(TURN, text="different ingress"), lambda _: None)
    with pytest.raises(ConversationConflict, match="source is malformed"):
        changed_ingress.read(reader, args)
    stopped[0] = True
    with pytest.raises(ConversationConflict, match="stopped"):
        capture.read(reader, args)
    stopped[0] = False
    admission = timeline.rows[thread][0].payload["data"]
    admission["execution_namespace"] = "another-task"
    with pytest.raises(ConversationConflict, match="namespace"):
        capture.read(reader, args)
    del admission["execution_namespace"]
    observation = timeline.rows[thread][-1].payload["data"]["proof"]["evidence"]["observation"]
    before = observation["response_json"]
    observation["response_json"] = "changed"
    with pytest.raises(ConversationConflict, match="proof differs"):
        capture.read(reader, args)
    observation["response_json"] = before
    view = {**projected.model_output, "status": "failed"}
    with pytest.raises(ConversationModelError, match="differs"):
        PydanticConversationModel._receipt_model_views((replace(projected, model_output=view),))
    del timeline.rows[thread]
    with pytest.raises(ConversationConflict, match="absent"):
        capture.read(reader, args)


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
        route._execution_lock = lambda key: nullcontext()
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
    route._execution_lock = lambda key: nullcontext()
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


def _retained_document():
    port, timeline, _, stopped, authorize = _retained_ha()
    port._action_names = ("save_workspace_document",)
    thread = port._thread_id(TURN, "save_workspace_document", 2)
    arguments = {"path": "/proof-state/note.md", "content": "Retained document text. " * 2000}
    admission = {"action_name": "save_workspace_document", "action_call_id": "saved-document",
        "action_arguments": arguments, "step_index": 2,
        "conversation_id": TURN.conversation_id, "principal_id": TURN.principal_id,
        "source_message_id": TURN.message_id, "receipt_output": {"operation_kind": "document_write"}}
    expected = {"path": arguments["path"], "content": arguments["content"]}
    timeline.rows[thread] = [
        SimpleNamespace(event_type="TOOL_REQUEST_ADMITTED", payload={"data": admission}),
        SimpleNamespace(event_type="STATE_VERIFIED", payload={"data": {"proof": {
            "expected": expected, "observed": dict(expected)}}})]
    return port, timeline, thread, stopped, authorize


def test_ancestor_action_body_bounds_old_document_arguments_and_keeps_latest_exact():
    from eva.application.conversation import ConversationActionReceipt
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _input_tokenizer
    from eva.contracts.canonical_json import canonical_json_bytes
    port, timeline, thread, _, authorize = _retained_document()
    original = port._receipt(TURN, timeline.events(thread), 2, project=False)
    old_source = _encoded([event.payload for event in timeline.events(thread)])
    projected = port._receipt(TURN, timeline.events(thread), 2)
    assert projected.call == original.call and projected.output == original.output
    newer = ConversationActionReceipt(ConversationActionCall("newest", "read_home_assistant_inventory", {}),
        {"status": "verified", "inventory": {"complete": True, "value": "latest exact"}})
    view = PydanticConversationModel._phase_admission_body(prompt="", instructions="", prior_actions=(projected,), completed_action=newer)
    full = PydanticConversationModel._phase_admission_body(prompt="", instructions="", prior_actions=(original,), completed_action=newer)
    assert view["input"][-1] == full["input"][-1]
    old_arguments = json.loads(view["input"][1]["arguments"])
    assert old_arguments["model_view_encoding"] == "retained_action_arguments_v1"
    assert json.loads(view["input"][2]["output"])["status"] == "verified"
    assert PydanticConversationModel._receipt_arguments(projected) == original.call.arguments
    assert PydanticConversationModel._receipt_presentation(projected) == original.output
    reader = projected.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    capture = ConnectorResults(timeline, TURN, authorize)
    page = capture.read(reader, {"pointer": "/call/arguments/content", "offset": 0, "limit": 40, "query": None})
    assert page["text"] == original.call.arguments["content"][:40]
    proof = capture.read(reader, {"pointer": "/output/proof/observed/content", "offset": 0, "limit": 40, "query": None})
    assert proof["text"] == page["text"]
    assert _encoded([event.payload for event in timeline.events(thread)]) == old_source
    rows_before = _encoded({key: [e.payload for e in rows] for key, rows in timeline.rows.items()})
    assert port._receipt(TURN, timeline.events(thread), 2).model_output == projected.model_output
    assert _encoded({key: [e.payload for e in rows] for key, rows in timeline.rows.items()}) == rows_before
    tokenizer = _input_tokenizer()
    assert len(tokenizer.encode_ordinary(canonical_json_bytes(view).decode())) < len(
        tokenizer.encode_ordinary(canonical_json_bytes(full).decode())) // 4
    # A failure is never projected merely because its body is large.
    failed = replace(original, output={**original.output, "status": "failed"})
    assert port._body_presentation(TURN, failed, 2) is failed


def test_ancestor_action_body_rechecks_actor_execution_source_hash_and_stop():
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.adapters.model.contracts import ConversationModelError
    port, timeline, thread, stopped, authorize = _retained_document()
    projected = port._receipt(TURN, timeline.events(thread), 2)
    reader = projected.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    arguments = {"pointer": "/output", "offset": 0, "limit": 20, "query": None}
    capture = ConnectorResults(timeline, TURN, authorize)
    other_turn = replace(TURN, principal_id="other", audience=replace(TURN.audience,
        principal_id="other", audience_id="person:other"))
    with pytest.raises(ConversationConflict):
        ConnectorResults(timeline, other_turn, lambda _: None).read(reader, arguments)
    with pytest.raises(ConversationConflict, match="source is malformed"):
        ConnectorResults(timeline, replace(TURN, text="changed"), lambda _: None).read(reader, arguments)
    timeline.rows[thread][0].payload["data"]["execution_namespace"] = "wrong"
    with pytest.raises(ConversationConflict, match="namespace"):
        capture.read(reader, arguments)
    del timeline.rows[thread][0].payload["data"]["execution_namespace"]
    timeline.rows[thread][-1].payload["data"]["proof"]["observed"]["content"] = "changed"
    with pytest.raises(ConversationConflict, match="proof differs"):
        capture.read(reader, arguments)
    timeline.rows[thread][-1].payload["data"]["proof"]["observed"]["content"] = projected.call.arguments["content"]
    stopped[0] = True
    with pytest.raises(ConversationConflict, match="stopped"):
        capture.read(reader, arguments)
    stopped[0] = False
    injected = json.loads(_encoded(projected.model_output))
    injected["ancestor_model_projection"]["source"]["receipt_sha256"] = "0" * 64
    with pytest.raises(ConversationModelError, match="differs"):
        PydanticConversationModel._receipt_model_views((replace(projected, model_output=injected),))
    del timeline.rows[thread]
    with pytest.raises(ConversationConflict, match="absent"):
        capture.read(reader, arguments)


def test_ancestor_local_selection_stays_readable_without_external_dispatch():
    port, timeline, thread, _, authorize = _retained_ha()
    original = port._receipt(TURN, timeline.events(thread), 1, project=False)
    projected = port._receipt(TURN, timeline.events(thread), 1)
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._execution_lock = lambda _: nullcontext()
    route._timeline = lambda _: timeline
    route._ensure_running = authorize
    reader = projected.model_output["result"]["reader"]["tool_id"]
    call = ConversationActionCall("large-local-page", "use_connector_tool", {"tool_id": reader,
        "arguments_json": json.dumps({"pointer": "/response_json", "offset": 0, "limit": 8192, "query": None})})
    selected = route.execute(TURN, call, step_index=2)
    assert selected.output["result"]["text"] == original.output["result"]["response_json"][:8192]
    assert "ancestor_model_projection" in selected.model_output
    body_reader = selected.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    page = ConnectorResults(timeline, TURN, authorize).read(body_reader,
        {"pointer": "/output/result/text", "offset": 0, "limit": 30, "query": None})
    assert page["text"] == selected.output["result"]["text"][:30]
    assert route.execute(TURN, call, step_index=2) == selected


def test_ancestor_presentation_reaches_sdk_wire_without_rewriting_newest_receipt(tmp_path, monkeypatch):
    from eva.application.conversation import ConversationActionReceipt
    from eva.adapters.model.pydantic_conversation import _input_tokenizer
    from eva.contracts.canonical_json import canonical_json_bytes
    from tests.model.test_pydantic_conversation_model import _phase_transport_adapter, _generate, RECEIPT
    port, timeline, thread, _, _ = _retained_document()
    original = port._receipt(TURN, timeline.events(thread), 2, project=False)
    projected = port._receipt(TURN, timeline.events(thread), 2)
    before = _encoded([original.call.arguments, original.output, RECEIPT.output])
    payloads = []
    for older in (original, projected):
        model, _, requests, _ = _phase_transport_adapter(
            tmp_path, monkeypatch, [("final_answer", ("Afgerond.",))])
        assert _generate(model, prior_actions=(older,), completed_action=RECEIPT).text == "Afgerond."
        assert len(requests) == 1
        payloads.append(requests[0])
    full, bounded = payloads
    assert bounded["input"][-2:] == full["input"][-2:]
    call = next(part for part in bounded["input"] if part.get("call_id") == original.call.call_id and part["type"] == "function_call")
    assert call["name"] == original.call.name
    assert json.loads(call["arguments"])["model_view_encoding"] == "retained_action_arguments_v1"
    assert _encoded([original.call.arguments, original.output, RECEIPT.output]) == before
    tokenizer = _input_tokenizer()
    full_bytes, bounded_bytes = (canonical_json_bytes(payload) for payload in payloads)
    assert len(bounded_bytes) < len(full_bytes) // 4
    assert len(tokenizer.encode_ordinary(bounded_bytes.decode())) < len(tokenizer.encode_ordinary(full_bytes.decode())) // 4
