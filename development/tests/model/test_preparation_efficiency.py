"""Exercise ordinary retained-result preparation without a provider request."""
from contextlib import nullcontext
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_json_bytes
from tests.connectors.test_connector_results import _retained_document, TURN
from tests.model.test_catalogue_model_view import expand_receipt_history


def preparation_case(kind):
    if kind == "short":
        receipts = (ConversationActionReceipt(
            ConversationActionCall("short-read", "existing_tool", {}),
            {"status": "verified", "result": {"state": "ready", "value": 21}}),)
    else:
        port, timeline, thread, _, authorize = _retained_document()
        text = ("a" * 2048 if kind == "wrapped-source" else
                ('λ🙂 "source" C:\\path\n' * 128)) * 32
        admission = timeline.rows[thread][0].payload["data"]
        admission["action_arguments"]["content"] = text
        proof = timeline.rows[thread][-1].payload["data"]["proof"]
        proof["expected"]["content"] = proof["observed"]["content"] = text
        document = port._receipt(TURN, timeline.events(thread), 2)
        reader = document.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
        route = PostgresExternalConversationTools(None,
            SimpleNamespace(action_names=("use_connector_tool",)),
            SimpleNamespace(supports_stored_results=True))
        route._execution_lock = lambda _: nullcontext()
        route._timeline = lambda _: timeline
        route._ensure_running = authorize

        def read(call_id, pointer, step):
            call = ConversationActionCall(call_id, "use_connector_tool", {
                "tool_id": reader, "arguments_json": json.dumps({
                    "pointer": pointer, "offset": 0,
                    "limit": 512 if pointer.endswith("/content") else 1024,
                    "query": None})})
            return route.execute(TURN, call, step_index=step)

        receipts = (read("source-literal", "/call/arguments/content", 3),
                    read("source-wrapper", "/call/arguments", 4))
    # Uncertain newest work must remain literal, including stop and origin data.
    newest = ConversationActionReceipt(
        ConversationActionCall("current-request", "existing_tool", {}),
        {"status": "needs_help", "effect_outcome": "unknown",
         "origin": "synthetic-person-A", "stop_requested": True})
    return (*receipts, newest)


def phase_input(model_class, receipts):
    return model_class._phase_admission_body(
        prompt="Read the saved result; do not start new work.",
        instructions="Same Eva, person boundary and source authority.",
        tools=({"name": "existing_tool", "description": "Unchanged permitted tool",
                "type": "function", "parameters": {"type": "object",
                "properties": {}, "additionalProperties": False}},),
        prior_actions=receipts[:-1], completed_action=receipts[-1])


@pytest.mark.parametrize("kind", ["wrapped-source", "saved-artifact", "short"])
def test_prepared_input_keeps_exact_sources_status_origin_stop_and_no_mutation(kind):
    receipts = preparation_case(kind)
    original = canonical_json_bytes([
        [r.call.arguments, r.output, r.model_output] for r in receipts])
    body = phase_input(PydanticConversationModel, receipts)
    views = tuple(json.loads(p["output"]) for p in body["input"]
                  if p["type"] == "function_call_output")
    expanded = expand_receipt_history(receipts, views)
    assert canonical_json_bytes(list(expanded)) == canonical_json_bytes([r.output for r in receipts])
    assert views[-1] == receipts[-1].output
    assert body["tools"][1]["parameters"] == {
        "type": "object", "properties": {}, "additionalProperties": False}
    assert hashlib.sha256(original).digest() == hashlib.sha256(canonical_json_bytes([
        [r.call.arguments, r.output, r.model_output] for r in receipts])).digest()
    # No earlier person's literal anchors can carry into another request.
    assert PydanticConversationModel._receipt_model_views((receipts[-1],)) == (receipts[-1].output,)
