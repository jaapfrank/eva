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
    return SimpleNamespace(write=written, read=read, page=page)


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
