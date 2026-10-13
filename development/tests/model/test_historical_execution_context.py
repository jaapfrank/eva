"""Historical facts stay readable without filling every new work request."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.model.test_authored_artifact_context import _version, _latest, _ids, _raw
from tests.model.test_catalogue_model_view import _working_note, _checkpoint_ha_read, _context_archive_route


def _discovery(name="discover_connector_tools"):
    args = {"connector_id": "neutral-fixture"} if name == "discover_connector_tools" else {"action": "status"}
    result = {"tools": [{"name": "example", "description": "Original schema. " * 800}]}
    proof = {"expected": {}, "observed": {}, "evidence": {
        "observation": result, "observation_sha256": canonical_sha256(result)}}
    return ConversationActionReceipt(ConversationActionCall("discovery", name, args),
        {"contract": "EvaConversationActionReceipt/v1", "durable": True,
         "status": "verified", "proof": proof, "result": result})


@pytest.mark.parametrize("name", ("discover_connector_tools", "manage_connected_services"))
def test_old_discovery_is_exactly_scoped_readable_and_never_current_authority(name):
    from tests.connectors.test_connector_results import ConnectorResults, TURN
    old = _discovery(name)
    checkpoint = _working_note("saved", 1, "Use the relevant available source.")
    latest = _latest()
    history = (old, checkpoint, latest)
    raw = _raw(history)
    assert old in PydanticConversationModel._receipt_context_selection(history)[0]
    selected, overview = PydanticConversationModel._receipt_context_selection(history, archive_discovery_reads=True)
    assert selected == (checkpoint, latest)
    index = json.loads(overview.split("\n", 1)[1])
    row = index["historical_discovery_reads"][0]
    assert row["call"]["arguments"] == old.call.arguments and row["receipt"] == old.output
    assert row["source_receipt_sha256"] == canonical_sha256(old.output)
    assert row["task_completion_verified"] is False
    _, _, timeline, _, stopped, authorize = _checkpoint_ha_read()
    route = _context_archive_route(timeline, authorize)
    note = PydanticConversationModel._archive_read_overview(TURN, overview, route.archive_model_context)
    manifest = json.loads(note.split("\n", 1)[1])
    assert "Original schema." not in note and len(note) < len(overview) / 3
    capture = ConnectorResults(timeline, TURN, authorize)
    page = capture.read(manifest["reader"]["tool_id"], {
        "pointer": "/historical_discovery_reads/0/receipt/result/tools/0/description",
        "offset": 0, "limit": 200, "query": None})
    assert page["text"] == old.output["result"]["tools"][0]["description"][:200]
    assert _raw(history) == raw
    stopped[0] = True
    from eva.application.conversation import ConversationConflict
    with pytest.raises(ConversationConflict, match="stopped"):
        capture.read(manifest["reader"]["tool_id"], {"pointer": "", "offset": 0, "limit": 1, "query": None})


@pytest.mark.parametrize("change", ("pause", "resume", "stop", "unknown", "failed", "effect",
    "bad_observation", "bad_hash", "bad_proof", "not_durable", "root_error", "result_error",
    "extra_argument", "root_success_false", "result_success_false", "no_checkpoint", "latest", "after_checkpoint"))
def test_discovery_archive_does_not_hide_mutations_failures_or_unbound_reads(change):
    old = _discovery("manage_connected_services")
    output, args = deepcopy(old.output), dict(old.call.arguments)
    if change in {"pause", "resume", "stop"}: args["action"] = change
    elif change in {"unknown", "failed"}: output["status"] = change
    elif change == "effect": output["external_state_verified"] = True
    elif change == "bad_observation": output["proof"]["evidence"]["observation"] = {}
    elif change == "bad_hash": output["proof"]["evidence"]["observation_sha256"] = "0" * 64
    elif change == "bad_proof": output["proof"]["observed"] = {"different": True}
    elif change == "not_durable": output["durable"] = False
    elif change == "root_error": output["error"] = "Retained real error."
    elif change == "result_error": output["result"]["error"] = "Retained real error."
    elif change == "root_success_false": output["success"] = False
    elif change == "result_success_false":
        output["result"]["success"] = False
        output["proof"]["evidence"]["observation_sha256"] = canonical_sha256(output["result"])
    elif change == "extra_argument": args["unexpected"] = True
    old = replace(old, call=replace(old.call, arguments=args), output=output)
    checkpoint = _working_note("saved", 1, "Keep the existing scope.")
    history = ((old, _latest()) if change == "no_checkpoint" else
               (checkpoint, old) if change == "latest" else
               (checkpoint, old, _latest()) if change == "after_checkpoint" else
               (old, checkpoint, _latest()))
    frozen = _raw(history)
    selected, _ = PydanticConversationModel._receipt_context_selection(history, archive_discovery_reads=True)
    assert old in selected and selected[-1] is history[-1] and _raw(history) == frozen


def test_restoration_uses_current_phase_instead_of_filling_space_with_unrelated_old_sources():
    old = _version("older-research", path="/proof-state/old.md")
    new = _version("current-research", path="/proof-state/current.md")
    old_page = old.page("old-page")
    new_page = new.page("new-page")
    checkpoint = _working_note("saved", 1, "Earlier findings are saved; continue the current observation.")
    history = (old.read, old_page, checkpoint, new.read, new_page, _latest())
    selected, _ = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=True)
    model = object.__new__(PydanticConversationModel)
    model._resource_budget = SimpleNamespace(max_input_tokens=65536)
    frozen = _raw(history)
    restored = model._retain_working_reads(history, selected[:-1], selected[-1], prompt="", instructions="", tools=())
    assert "new-page" in _ids(restored) and "old-page" not in _ids(restored)
    assert _raw(history) == frozen


@pytest.mark.parametrize("proof_matches", (True, False))
def test_connector_document_alias_keeps_selector_and_exact_original_json_readback(proof_matches):
    from tests.connectors.test_connector_results import _retained_document, ConnectorResults, TURN
    port, timeline, thread, stopped, authorize = _retained_document()
    rows = timeline.rows.pop(thread)
    admitted = rows[0].payload["data"]
    content = "Observed document λ. " * 1200
    original = json.dumps({"path": "/proof-state/neutral.md", "content": content})
    admitted.update(action_name="use_connector_tool", step_index=1, action_arguments={
        "tool_id": "neutral-document-write", "arguments_json": original})
    rows[-1].payload["data"]["proof"] = {"expected": {"content": content},
        "observed": {"content": content if proof_matches else "Different real observation."}}
    port._action_names = ("use_connector_tool",)
    thread = port._thread_id(TURN, "use_connector_tool", 1)
    timeline.rows[thread] = rows
    receipt = port._receipt(TURN, rows, 1)
    frozen = _raw((receipt,))
    assert PydanticConversationModel._receipt_arguments(receipt, ancestor=False)["arguments_json"] == original
    projected = PydanticConversationModel._receipt_arguments(receipt, ancestor=True)
    if not proof_matches:
        assert projected == receipt.call.arguments and _raw((receipt,)) == frozen
        return
    assert projected["tool_id"] == receipt.call.arguments["tool_id"]
    nested = json.loads(projected["arguments_json"])
    assert nested["path"] == "/proof-state/neutral.md"
    assert nested["content"]["source_encoding"] == "json_string_v1"
    reference = nested["content"]
    capture = ConnectorResults(timeline, TURN, authorize)
    page = capture.read(reference["reader_tool_id"], {"pointer": reference["pointer"], "offset": 0, "limit": 8192, "query": None})
    assert page["text"] == original[:8192]
    assert reference["original_content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert len(canonical_json_bytes(projected)) < len(canonical_json_bytes(receipt.call.arguments)) / 4
    assert _raw((receipt,)) == frozen
