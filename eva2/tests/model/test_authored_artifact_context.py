"""Authored work pages remain usable without changing retained evidence.

Neutral fixtures follow the observed SAVE18 -> content-only Read125 -> scoped
local page chain. They use the existing retained-body/reader adapters in memory;
there is no provider or external service behind them.
"""
from contextlib import nullcontext
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.background_execution import (
    BackgroundExecutionScope, background_execution, background_execution_namespace,
)
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_json_bytes
from tests.connectors.test_connector_results import TURN, _retained_document, _retained_mcp
from tests.model.test_catalogue_model_view import (
    _working_note, expand_deferred_read_index, expand_receipt_history,
)


PATH = "/proof-state/work-notes.md"


def _version(label, *, turn=TURN, proof_matches=True):
    # Distinct Unicode lines make observed page ranges independently identifiable.
    content = "".join(f"{label} {i:04d} observed material λ🙂\n" for i in range(500))
    port, timeline, _, _, _ = _retained_document()
    _, read_timeline, template_thread, _, _ = _retained_mcp()
    def authorize(current):
        assert current == turn
    scope = BackgroundExecutionScope(turn, "authored-artifact-fixture", authorize_dispatch=authorize)
    with background_execution(scope):
        port._action_names = ("save_workspace_document", "use_connector_tool")
        port._ensure_running = authorize
        write_thread = port._thread_id(turn, "save_workspace_document", 18)
        admission = {"action_name": "save_workspace_document", "action_call_id": label + "-write",
            "action_arguments": {"path": PATH, "content": content, "component_step": None},
            "step_index": 18, "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id, "source_message_id": turn.message_id,
            "execution_namespace": background_execution_namespace(turn),
            "receipt_output": {"operation_kind": "document_write"}}
        # The retained real write has no path field in either proof object.
        timeline.rows[write_thread] = [
            SimpleNamespace(event_type="TOOL_REQUEST_ADMITTED", payload={"data": admission}),
            SimpleNamespace(event_type="STATE_VERIFIED", payload={"data": {"proof": {
                "expected": {"content": content},
                "observed": {"content": content if proof_matches else "different bytes"}}}}),
        ]
        written = port._receipt(turn, timeline.events(write_thread), 18)
        read_thread = port._thread_id(turn, "use_connector_tool", 125)
        rows = read_timeline.rows[template_thread]
        read_admission = rows[0].payload["data"]
        read_admission.update(action_call_id=label + "-read", step_index=125,
            conversation_id=turn.conversation_id, principal_id=turn.principal_id,
            source_message_id=turn.message_id, execution_namespace=background_execution_namespace(turn),
            action_arguments={"tool_id": "workspace-read-text", "arguments_json": json.dumps({"path": PATH})})
        # Exactly the observed reader normalization: one final LF is absent.
        rows[-1].payload["data"]["proof"]["evidence"]["observation"] = {"content": content[:-1]}
        timeline.rows[read_thread] = rows
        read = port._receipt(turn, timeline.events(read_thread), 125)
        reader = read.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
        route = PostgresExternalConversationTools(None,
            SimpleNamespace(action_names=("use_connector_tool",)),
            SimpleNamespace(supports_stored_results=True))
        route._execution_lock = lambda _: nullcontext()
        route._timeline = lambda _: timeline
        route._ensure_running = authorize
    step = [126]
    def page(call_id, offset=0, limit=900):
        step[0] += 1
        with background_execution(scope):
            return route.execute(turn, ConversationActionCall(call_id, "use_connector_tool", {
                "tool_id": reader, "arguments_json": json.dumps({"pointer": "/output/result/content",
                    "offset": offset, "limit": limit, "query": None})}), step_index=step[0])
    return SimpleNamespace(write=written, read=read, page=page, route=route)


def _latest():
    return ConversationActionReceipt(ConversationActionCall("latest", "finish_observation", {}),
        {"status": "verified", "result": "Distinct latest result remains exact."})


def _raw(receipts):
    return canonical_json_bytes([[asdict(r.call), r.output, r.model_output] for r in receipts])


def _ids(receipts):
    return {r.call.call_id for r in receipts}


def test_authored_content_only_proof_pages_survive_checkpoint_pressure_and_wire():
    version = _version("original")
    first = version.page("first-page")
    tail = version.page("tail-page", offset=8000)
    failed = ConversationActionReceipt(ConversationActionCall("failed", "read_source", {}),
        {"status": "failed", "error": "A real source failure."})
    unknown = ConversationActionReceipt(ConversationActionCall("unknown", "read_source", {}),
        {"status": "ambiguous", "result": None})
    checkpoint = _working_note("saved-notes", 2, "Continue using the observed work-document pages.")
    history = (version.write, version.read, first, tail, checkpoint, failed, unknown, _latest())
    frozen = _raw(history)
    assert set(version.write.output["proof"]["expected"]) == {"content"}
    assert version.read.output["result"]["content"] + "\n" == version.write.call.arguments["content"]
    baseline = PydanticConversationModel._phase_admission_body(prompt="", instructions="",
        prior_actions=history[:-1], completed_action=history[-1])["input"][-1]
    for pressure in (False, True):
        selected, _ = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=pressure)
        assert {"first-page", "tail-page", "saved-notes", "failed", "unknown", "latest"} <= _ids(selected)
        wire = PydanticConversationModel._phase_admission_body(prompt="", instructions="",
            prior_actions=selected[:-1], completed_action=selected[-1])
        assert wire["input"][-1] == baseline
        rendered = tuple(json.loads(row["output"]) for row in wire["input"] if row["type"] == "function_call_output")
        expanded = dict(zip((r.call.call_id for r in selected), expand_receipt_history(selected, rendered), strict=True))
        for page in (first, tail):
            assert expanded[page.call.call_id]["result"]["text"] == page.output["result"]["text"]
        assert next(r for r in selected if r.call.call_id == "failed") == failed
        assert next(r for r in selected if r.call.call_id == "unknown") == unknown
    assert _raw(history) == frozen


@pytest.mark.parametrize("reason", ("unwritten", "wrong_proof", "other_ingress"))
def test_unproven_or_other_ingress_pages_keep_existing_authorized_deferral(reason):
    original = _version("original", proof_matches=reason != "wrong_proof")
    source = (_version("original", turn=replace(TURN, message_id="other-message"))
              if reason == "other_ingress" else original)
    page = source.page("unproven-page")
    writer = () if reason == "unwritten" else (original.write,)
    history = writer + (source.read, page, _working_note("checkpoint", 2, "No special authorship proof."), _latest())
    frozen = _raw(history)
    selected, overview = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=True)
    assert "unproven-page" not in _ids(selected)
    index = expand_deferred_read_index(json.loads(overview.split("\n", 1)[1]))
    assert "unproven-page" in {row["call_id"] for row in index["reads"]}
    assert selected[-1] == history[-1]
    assert _raw(history) == frozen


def test_latest_actually_read_version_and_distinct_ranges_replace_old_pages_once():
    old, new = _version("old"), _version("new")
    old_page = old.page("old-page")
    checkpoint = _working_note("checkpoint", 2, "Use the currently observed work document.")
    # A newer readroot alone must not hide the last version whose pages were read.
    unread_history = (old.write, old.read, old_page, new.write, new.read, checkpoint, _latest())
    selected, _ = PydanticConversationModel._receipt_context_selection(unread_history, defer_local_reads=True)
    assert "old-page" in _ids(selected)
    first = new.page("new-first")
    tail = new.page("new-tail", offset=8000)
    duplicate = new.page("new-first-repeat")
    history = (old.write, old.read, old_page, new.write, new.read, first, tail, duplicate, checkpoint, _latest())
    frozen = _raw(history)
    selected, overview = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=True)
    assert {"new-tail", "new-first-repeat", "checkpoint", "latest"} <= _ids(selected)
    assert not {"old-page", "new-first"} & _ids(selected)
    index = expand_deferred_read_index(json.loads(overview.split("\n", 1)[1]))
    assert {"old-page", "new-first"} <= {row["call_id"] for row in index["reads"]}
    assert selected[-1] == history[-1]
    assert _raw(history) == frozen


def test_research_material_survives_saved_note_without_repeating_local_read():
    version = _version("research-source")
    first = version.page("research-first")
    tail = version.page("research-tail", offset=8000)
    duplicate = version.page("research-first-repeat")
    # This source was read, not authored in the current task. Its material must
    # not depend on whether a prose checkpoint copied every observed fact.
    history = (version.read, first, tail, duplicate,
        _working_note("checkpoint", 2, "Continue the synthesis from source references."), _latest())
    frozen = _raw(history)
    selected, overview = PydanticConversationModel._receipt_context_selection(history)
    assert overview and not {"research-first", "research-tail", "research-first-repeat"} & _ids(selected)
    model = object.__new__(PydanticConversationModel)
    model._resource_budget = SimpleNamespace(max_input_tokens=65536)
    restored = model._retain_working_reads(history, selected[:-1], selected[-1],
        prompt="Complete the requested document.", instructions="Use observed data as evidence.", tools=())
    assert {"research-tail", "research-first"} <= _ids(restored)
    assert "research-first-repeat" not in _ids(restored)
    wire = model._phase_admission_body(prompt="Complete the requested document.",
        instructions="Use observed data as evidence.", tools=(), prior_actions=restored,
        completed_action=selected[-1])
    model._admit_request(wire, autonomous=False)
    baseline = model._phase_admission_body(prompt="Complete the requested document.",
        instructions="Use observed data as evidence.", tools=(), prior_actions=selected[:-1],
        completed_action=selected[-1])
    assert wire["input"][-2:] == baseline["input"][-2:]
    assert _raw(history) == frozen


def test_research_material_restoration_respects_request_space_and_has_no_uncapped_mode():
    version = _version("research-source")
    page = version.page("research-page", limit=8000)
    history = (version.read, page, _working_note("checkpoint", 2, "Source remains locally available."), _latest())
    selected, _ = PydanticConversationModel._receipt_context_selection(history)
    model = object.__new__(PydanticConversationModel)
    model._resource_budget = None
    assert model._retain_working_reads(history, selected[:-1], selected[-1],
        prompt="", instructions="", tools=()) == selected[:-1]
    model._resource_budget = SimpleNamespace(max_input_tokens=4000)
    restored = model._retain_working_reads(history, selected[:-1], selected[-1],
        prompt="", instructions="Large required current instruction. " * 140, tools=())
    assert "research-page" not in _ids(restored)
    assert restored == selected[:-1]


def test_research_material_cannot_cross_ingress_or_replace_failure_or_latest_result():
    version = _version("research-source", turn=replace(TURN, message_id="different-source"))
    page = version.page("other-person-page")
    current = _version("current-source")
    failed = ConversationActionReceipt(ConversationActionCall("failed", "read_source", {}),
        {"status": "failed", "error": "An actual read failure."})
    history = (version.read, page, current.read,
        _working_note("checkpoint", 2, "Continue the original current task."), failed, _latest())
    selected, _ = PydanticConversationModel._receipt_context_selection(history)
    model = object.__new__(PydanticConversationModel)
    model._resource_budget = SimpleNamespace(max_input_tokens=65536)
    restored = model._retain_working_reads(history, selected[:-1], selected[-1],
        prompt="", instructions="", tools=())
    assert "other-person-page" not in _ids(restored)
    assert "failed" in _ids(restored)
    assert restored[-1] == failed


def test_actual_provider_envelope_carries_read_material_after_checkpoint(tmp_path, monkeypatch):
    from eva.adapters.model.pydantic_conversation import _descriptor_tool
    from tests.model.test_pydantic_conversation_model import (
        _phase_transport_adapter, _identity, _memory, TOOL)
    model, _, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch,
        [("final_answer", ("Synthetic observed result.",))])
    descriptor = replace(TOOL, name="use_connector_tool")
    model._conversation_tools[descriptor.name] = _descriptor_tool(descriptor)
    model._conversation_tool_descriptors[descriptor.name] = descriptor
    model._resource_budget = SimpleNamespace(max_input_tokens=65536, max_output_tokens=1200)
    version = _version("research-source")
    page = version.page("research-page")
    history = (version.read, page, _working_note("saved", 2, "Use the source for the final synthesis."))
    scoped = model.with_context_archive(version.route.archive_model_context)
    result = scoped.generate(TURN, _identity(), _memory(), (),
        available_actions=("use_connector_tool",), prior_actions=history[:-1],
        completed_action=history[-1])
    assert result.text == "Synthetic observed result." and len(requests) == 1
    assert "Modelcontextselectie" in requests[0]["instructions"]
    outputs = {item["call_id"]: json.loads(item["output"]) for item in requests[0]["input"]
        if item.get("type") == "function_call_output"}
    assert "research-page" in outputs
    assert outputs["research-page"]["result"]["text"] == page.output["result"]["text"]
    assert outputs["saved"] == PydanticConversationModel._receipt_model_views(history)[-1]
    assert model._context_archive is None
