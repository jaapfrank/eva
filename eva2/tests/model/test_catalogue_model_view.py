"""Lossless model input compression; no provider requests or external tools."""
from copy import deepcopy
import hashlib
import json

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_json_bytes


SHARED = "model_view_shared_tool_guidance"
GUIDANCE = "Verify only the requested result; keep authority and uncertainty explicit. " * 8


def receipt(guidance=GUIDANCE, count=24):
    result = {
        "connector_id": "own-connector",
        "tools": [
            {
                "tool_id": f"tool:{index}",
                "name": f"tool_{index}",
                "description": f"Capability {index}",
                "completion_guidance": guidance,
                "operation_kind": "read" if index % 2 else "invoke",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "enum": [f"choice-{index}", "日本語"]}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }
            for index in range(count)
        ],
    }
    return {
        "contract": "EvaConversationActionReceipt/v1",
        "durable": True,
        "status": "verified",
        "result": result,
        "proof": {
            "observed": {"connector_id": "own-connector"},
            "evidence": {"source": "registered_mcp_catalog", "observation": deepcopy(result)},
        },
    }


def expand_catalogue(view):
    """Independently resolve JSON pointers, then remove the representation table."""
    root = view
    result = deepcopy(view["result"])
    for tool in result["tools"]:
        guidance = tool.get("completion_guidance")
        if isinstance(guidance, dict) and set(guidance) == {"model_view_reference"}:
            value = root
            for part in guidance["model_view_reference"][1:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
            tool["completion_guidance"] = deepcopy(value)
    result.pop(SHARED, None)
    return result


def test_actual_phase_input_retains_every_tool_schema_and_expands_to_original():
    original = receipt()
    frozen = canonical_json_bytes(original)
    action = ConversationActionReceipt(
        ConversationActionCall("catalogue-call", "discover_connector_tools", {"connector_id": "own-connector"}),
        original,
    )
    body = PydanticConversationModel._phase_admission_body(
        prompt="Use the discovered capability", instructions="Existing authority", completed_action=action,
    )
    view = json.loads(body["input"][-1]["output"])
    assert canonical_json_bytes(original) == frozen
    assert canonical_json_bytes(expand_catalogue(view)) == canonical_json_bytes(original["result"])
    assert view["result"][SHARED] == [GUIDANCE]
    assert [t["input_schema"] for t in view["result"]["tools"]] == [
        t["input_schema"] for t in original["result"]["tools"]
    ]
    reference = view["proof"]["evidence"]["observation"]
    assert reference["model_view_encoding"] == "shared_tool_guidance_v1"
    assert reference["observation_sha256"] == hashlib.sha256(canonical_json_bytes(original["result"])).hexdigest()
    assert reference["durable_proof_sha256"] == hashlib.sha256(canonical_json_bytes(original["proof"])).hexdigest()
    assert reference["model_view_sha256"] == hashlib.sha256(canonical_json_bytes(view["result"])).hexdigest()
    assert len(canonical_json_bytes(view)) < len(frozen)
    assert PydanticConversationModel._receipt_model_view(view) == view


def test_distinct_guidance_groups_unicode_and_tool_order_remain_lossless():
    original = receipt(guidance="Neem rustig waar. 🧠\n" * 40, count=6)
    second = "Different guidance: do not infer success. " * 20
    original["result"]["tools"][2]["completion_guidance"] = second
    original["result"]["tools"][4]["completion_guidance"] = second
    original["proof"]["evidence"]["observation"] = deepcopy(original["result"])
    view = PydanticConversationModel._receipt_model_view(original)
    assert canonical_json_bytes(expand_catalogue(view)) == canonical_json_bytes(original["result"])
    assert len(view["result"][SHARED]) == 2


@pytest.mark.parametrize("change", ["foreign_source", "rejected", "wrong_contract", "non_durable", "different_observation", "reserved_field"])
def test_unknown_failed_or_conflicting_catalogues_are_not_compressed(change):
    original = receipt()
    if change == "foreign_source":
        original["proof"]["evidence"]["source"] = "untrusted_tool_result"
    elif change == "rejected":
        original["status"] = "rejected"
        original["tool_error"] = {"untrusted": True, "effect_outcome": "unknown", "messages": ["failed"]}
    elif change == "wrong_contract":
        original["contract"] = "SomeTool/v1"
    elif change == "non_durable":
        original["durable"] = False
    elif change == "different_observation":
        original["proof"]["evidence"]["observation"]["tools"] = []
    else:
        original["result"][SHARED] = ["provider-owned field"]
        original["proof"]["evidence"]["observation"] = deepcopy(original["result"])
    view = PydanticConversationModel._receipt_model_view(original)
    assert view["result"] == original["result"]
    if change == "rejected":
        assert view == original
        assert view["tool_error"]["effect_outcome"] == "unknown"


@pytest.mark.parametrize("guidance,count", [("x", 2), (GUIDANCE, 1), ("", 24)])
def test_small_or_unique_guidance_never_adds_a_representation_cost(guidance, count):
    original = receipt(guidance=guidance, count=count)
    view = PydanticConversationModel._receipt_model_view(original)
    assert view["result"] == original["result"]
    assert SHARED not in view["result"]


def expand_receipt_history(receipts, views):
    """Resolve earlier-call pointers independently, including local proof pointers."""
    by_call = {receipt.call.call_id: view for receipt, view in zip(receipts, views, strict=True)}
    def resolve(value, root):
        if isinstance(value, dict) and "model_view_reference" in value:
            target = by_call[value["model_view_call_id"]] if "model_view_call_id" in value else root
            for part in value["model_view_reference"][1:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
            mode = value.get("model_view_fragment_encoding")
            if mode in {"literal_string_slice_v1", "json_escape_string_slice_v1", "json_unescape_string_slice_v1"}:
                assert isinstance(target, str)  # A literal anchor, never another reference.
                start, count = value["start"], value["characters"]
                assert type(start) is int and type(count) is int and 0 <= start < start + count <= len(target)
                fragment = target[start:start + count]
                if mode == "json_escape_string_slice_v1":
                    fragment = json.dumps(fragment, ensure_ascii=False)[1:-1]
                elif mode == "json_unescape_string_slice_v1":
                    fragment = json.loads('"' + fragment + '"')
                assert hashlib.sha256(fragment.encode("utf-8")).hexdigest() == value["fragment_sha256"]
                return fragment
            target = resolve(target, root)
            if value.get("model_view_fragment_encoding") == "json_escape_content_v1":
                assert hashlib.sha256(canonical_json_bytes(target)).hexdigest() == value["value_sha256"]
                return json.dumps(target, ensure_ascii=False)[1:-1]
            return target
        if isinstance(value, dict) and value.get("model_view_encoding") == "json_string_fragments_v1":
            text = "".join(resolve(part, root) for part in value["parts"])
            assert hashlib.sha256(text.encode("utf-8")).hexdigest() == value["string_sha256"]
            return text
        if isinstance(value, dict):
            return {key: resolve(item, root) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, root) for item in value]
        return value
    return tuple(resolve(view, view) for view in views)


def action(call_id, output):
    return ConversationActionReceipt(ConversationActionCall(call_id, "existing_tool", {}), output)


def test_retained_package_and_exact_readback_remain_lossless_in_actual_phase_input():
    source = "A large real component with Unicode. 🧠\\n" * 2000
    evaluation = {"cases": [{"answer": "Original measured observation. " * 500}], "hard": 0}
    earlier = {"status": "verified", "scope": "unexecuted_proposal",
               "operation_result": {"source/~name": source, "evaluation": evaluation}}
    prepared = {"status": "verified", "scope": "installation_candidate_pending_review",
                "operation_result": {"source/~name": source, "evaluation": deepcopy(evaluation),
                                     "installed": False}}
    content = json.dumps(prepared["operation_result"], ensure_ascii=False)
    saved = {"contract": "EvaConversationActionReceipt/v1", "durable": True, "status": "verified",
             "proof": {"expected": {"content": content}, "observed": {"content": content},
                       "evidence": {"source": "independent_readback", "observation_sha256": "unchanged"}}}
    receipts = (action("draft", earlier), action("prepare", prepared), action("save", saved))
    frozen = canonical_json_bytes([item.output for item in receipts])
    body = PydanticConversationModel._phase_admission_body(
        prompt="Finish existing saved work", instructions="Same authority",
        prior_actions=receipts[:-1], completed_action=receipts[-1],
    )
    views = tuple(json.loads(item["output"]) for item in body["input"] if item["type"] == "function_call_output")
    expanded = expand_receipt_history(receipts, views)
    assert canonical_json_bytes(list(expanded)) == frozen
    assert canonical_json_bytes([item.output for item in receipts]) == frozen
    assert len(canonical_json_bytes(list(views))) < len(frozen) * 0.70
    reference = views[1]["operation_result"]["source/~name"]
    assert reference["model_view_call_id"] == "draft"
    assert reference["model_view_reference"] == "/operation_result/source~1~0name"
    assert reference["value_sha256"] == hashlib.sha256(canonical_json_bytes(source)).hexdigest()
    assert reference["source_receipt_sha256"] == hashlib.sha256(canonical_json_bytes(earlier)).hexdigest()
    assert views[1]["scope"] == prepared["scope"]
    assert views[1]["operation_result"]["installed"] is False
    local_reference = views[-1]["proof"]["observed"]
    assert local_reference["model_view_reference"] == "/proof/expected"
    assert local_reference["durable_proof_sha256"] == hashlib.sha256(canonical_json_bytes(saved["proof"])).hexdigest()


@pytest.mark.parametrize("state", ["failed", "ambiguous", "unconfirmed"])
def test_failed_history_cannot_supply_or_consume_verified_value_references(state):
    content = "Evidence that must stay explicit. " * 200
    failed = {"status": state, "result": {"content": content}, "uncertainty": "retained"}
    verified = {"status": "verified", "result": {"content": content}}
    receipts = (action("failed", failed), action("verified", verified), action("later_failed", failed))
    views = PydanticConversationModel._receipt_model_views(receipts)
    assert views == tuple(item.output for item in receipts)


@pytest.mark.parametrize("change", ["mismatch", "non_durable", "wrong_contract", "failed", "missing"])
def test_effect_readback_with_uncertainty_or_distinct_bytes_is_not_collapsed(change):
    content = "Exact saved document. " * 200
    output = {"contract": "EvaConversationActionReceipt/v1", "durable": True, "status": "verified",
              "proof": {"expected": {"content": content}, "observed": {"content": content}}}
    if change == "mismatch":
        output["proof"]["observed"]["content"] += " changed"
    elif change == "non_durable":
        output["durable"] = False
    elif change == "wrong_contract":
        output["contract"] = "Other/v1"
    elif change == "failed":
        output["status"] = "failed"
    else:
        output["proof"].pop("observed")
    assert PydanticConversationModel._receipt_model_view(output) == output


def test_no_cross_request_forward_or_reference_chain_targets_and_duplicate_ids_fail_closed():
    content = "A large retained value. " * 200
    output = {"status": "verified", "result": {"content": content}}
    first = action("first", output)
    second = action("second", deepcopy(output))
    third = action("third", deepcopy(output))
    views = PydanticConversationModel._receipt_model_views((first, second, third))
    assert views[0] == output
    assert views[1]["result"]["model_view_call_id"] == "first"
    assert views[2]["result"]["model_view_call_id"] == "first"
    assert PydanticConversationModel._receipt_model_views((third,))[0] == output
    assert PydanticConversationModel._receipt_model_views((first, first)) == (output, output)
    assert canonical_json_bytes(list(expand_receipt_history((first, second, third), views))) == canonical_json_bytes([output] * 3)


def test_local_proof_anchors_and_catalogue_encoding_hash_remain_literal_across_receipts():
    catalogue = receipt()
    source = action("catalogue_first", catalogue)
    second = action("catalogue_second", deepcopy(catalogue))
    views = PydanticConversationModel._receipt_model_views((source, second))
    for view in views:
        reference = view["proof"]["evidence"]["observation"]
        assert "model_view_reference" not in view["result"]
        assert reference["model_view_sha256"] == hashlib.sha256(canonical_json_bytes(view["result"])).hexdigest()
        assert expand_catalogue(view) == catalogue["result"]
    content = "Repeated saved document content. " * 500
    output = {"contract": "EvaConversationActionReceipt/v1", "durable": True, "status": "verified",
              "proof": {"expected": {"content": content}, "observed": {"content": content}}}
    saved = (action("save_first", output), action("save_second", deepcopy(output)))
    views = PydanticConversationModel._receipt_model_views(saved)
    assert "model_view_reference" not in views[1]["proof"]["expected"]
    assert views[1]["proof"]["observed"]["model_view_reference"] == "/proof/expected"
    assert canonical_json_bytes(list(expand_receipt_history(saved, views))) == canonical_json_bytes([output] * 2)


@pytest.mark.parametrize("value", [[], {}, 5, None, False, "not-a-pointer"])
def test_unknown_provider_reference_fields_stay_literal_and_cannot_break_history(value):
    output = {"status": "verified", "result": {"model_view_reference": value,
              "model_view_call_id": value, "content": "Provider data." * 100}}
    assert PydanticConversationModel._receipt_model_views((action("read", output),))[0] == output


def test_saved_json_text_preserves_quotes_slashes_unicode_and_original_bytes():
    code = 'print("Een pad C:\\temp, 🧠 en 日本語")\n' * 800
    observation = 'Exact observation with escaped \\ slash and "quotes".\n' * 400
    first = action("source", {"status": "verified", "operation_result": {"code": code, "stdout": observation}})
    document = json.dumps({"code": code, "stdout": observation, "code_again": code}, ensure_ascii=False)
    saved = {"contract": "EvaConversationActionReceipt/v1", "status": "verified", "durable": True,
             "proof": {"expected": {"content": document}, "observed": {"content": document},
                       "evidence": {"source": "independent_readback"}}}
    receipts = (first, action("save", saved))
    frozen = canonical_json_bytes([r.output for r in receipts])
    views = PydanticConversationModel._receipt_model_views(receipts)
    content_view = views[1]["proof"]["expected"]["content"]
    assert content_view["model_view_encoding"] == "json_string_fragments_v1"
    assert all(part["model_view_call_id"] == "source" for part in content_view["parts"] if isinstance(part, dict))
    assert len(canonical_json_bytes(list(views))) < len(frozen) * 0.50
    expanded = expand_receipt_history(receipts, views)
    assert canonical_json_bytes(list(expanded)) == frozen
    assert expanded[1]["proof"]["observed"]["content"].encode() == document.encode()
    reference = views[1]["proof"]["observed"]
    assert reference["model_view_sha256"] == hashlib.sha256(canonical_json_bytes(views[1]["proof"]["expected"])).hexdigest()
    assert reference["durable_proof_sha256"] == hashlib.sha256(canonical_json_bytes(saved["proof"])).hexdigest()


@pytest.mark.parametrize("with_expected", [False, True])
def test_provider_supplied_effect_reference_does_not_authorize_encoding(with_expected):
    literal = "Provider content. " * 500
    proof = {"observed": {"model_view_reference": "/proof/expected"}}
    if with_expected:
        proof["expected"] = {"content": literal}
    output = {"contract": "EvaConversationActionReceipt/v1", "status": "verified", "durable": True, "proof": proof}
    source = action("previous", {"status": "verified", "result": {"content": literal}})
    assert PydanticConversationModel._receipt_model_views((source, action("provider", output)))[1] == output


def household_receipt():
    inventory = {"items": [{"entity_id": "sensor.room_temperature", "state": "21.5",
                           "attributes": {"friendly_name": "Room temperature", "notes": "🧠 " * 400}}],
                 "truncated": False, "missing": ["outdoor_temperature"]}
    return {"contract": "EvaConversationActionReceipt/v1", "durable": True, "status": "verified",
            "provider": "home_assistant", "inventory": inventory,
            "proof": {"evidence": {"source": "home_assistant_inventory",
                                    "observation": deepcopy(inventory)}}}


def test_household_inventory_proof_references_the_complete_observation_losslessly():
    original = household_receipt()
    frozen = canonical_json_bytes(original)
    receipts = (action("house-read", original), action("later-house-read", deepcopy(original)))
    body = PydanticConversationModel._phase_admission_body(
        prompt="Complete the existing household task", instructions="Unchanged authority",
        prior_actions=receipts[:-1], completed_action=receipts[-1])
    views = tuple(json.loads(item["output"]) for item in body["input"]
                  if item["type"] == "function_call_output")
    assert canonical_json_bytes(list(expand_receipt_history(receipts, views))) == canonical_json_bytes([original, original])
    assert canonical_json_bytes(original) == frozen
    assert len(canonical_json_bytes(list(views))) < len(canonical_json_bytes([original, original]))
    for view in views:
        assert view["inventory"] == original["inventory"]
        reference = view["proof"]["evidence"]["observation"]
        assert reference["model_view_reference"] == "/inventory"
        assert reference["observation_sha256"] == hashlib.sha256(canonical_json_bytes(original["inventory"])).hexdigest()
        assert reference["durable_proof_sha256"] == hashlib.sha256(canonical_json_bytes(original["proof"])).hexdigest()


@pytest.mark.parametrize("change", ["different_observation", "failed", "non_durable", "small"])
def test_household_inventory_uncertainty_or_larger_reference_stays_literal(change):
    original = household_receipt()
    if change == "different_observation":
        original["proof"]["evidence"]["observation"]["items"][0]["state"] = "22.0"
    elif change == "failed":
        original["status"] = "failed"
    elif change == "non_durable":
        original["durable"] = False
    else:
        original["inventory"] = {}
        original["proof"]["evidence"]["observation"] = {}
    assert PydanticConversationModel._receipt_model_view(original) == original


def test_validated_local_literal_slices_keep_unicode_json_facts_and_newest_on_sdk_wire(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from tests.connectors.test_connector_results import _retained_document, TURN, _encoded
    from tests.model.test_pydantic_conversation_model import _phase_transport_adapter, _generate, RECEIPT

    port, timeline, thread, _, authorize = _retained_document()
    text = "".join(f'Line {i:04d} λ🙂 "quote {i}" C:\\path\n' for i in range(800))
    admission = timeline.rows[thread][0].payload["data"]
    admission["action_arguments"]["content"] = text
    proof = timeline.rows[thread][-1].payload["data"]["proof"]
    proof["expected"]["content"] = proof["observed"]["content"] = text
    document = port._receipt(TURN, timeline.events(thread), 2)
    reader = document.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._execution_lock = lambda _: nullcontext()
    route._timeline = lambda _: timeline
    route._ensure_running = authorize

    def page(call_id, pointer, offset, limit, step):
        call = ConversationActionCall(call_id, "use_connector_tool", {"tool_id": reader,
            "arguments_json": json.dumps({"pointer": pointer, "offset": offset, "limit": limit, "query": None})})
        return route.execute(TURN, call, step_index=step)

    plain = page("literal-anchor", "/call/arguments/content", 0, 6000, 3)
    shorter = page("shorter-slice", "/call/arguments/content", 500, 4500, 4)
    longer = page("longer-slice", "/call/arguments/content", 0, 8192, 5)
    escaped = page("escaped-slice", "/call/arguments", 0, 8192, 6)
    receipts = (plain, shorter, longer, escaped, RECEIPT)
    raw = canonical_json_bytes([[item.call.arguments, item.output] for item in receipts])
    views = PydanticConversationModel._receipt_model_views(receipts)
    expanded = expand_receipt_history(receipts, views)
    assert canonical_json_bytes(list(expanded[:-1])) == canonical_json_bytes([r.output for r in receipts[:-1]])
    assert views[-1] == PydanticConversationModel._receipt_model_views(receipts, _literal_slices=False)[-1]
    encodings = set()
    def check_references(value, position, indexed):
        if isinstance(value, dict):
            if value.get("model_view_fragment_encoding") in {
                    "literal_string_slice_v1", "json_escape_string_slice_v1", "json_unescape_string_slice_v1"}:
                assert indexed[value["model_view_call_id"]] < position
                encodings.add(value["model_view_fragment_encoding"])
            for item in value.values():
                check_references(item, position, indexed)
        elif isinstance(value, list):
            for item in value:
                check_references(item, position, indexed)
    indexed = {r.call.call_id: i for i, r in enumerate(receipts)}
    for i, view in enumerate(views):
        check_references(view, i, indexed)
    assert {"literal_string_slice_v1", "json_escape_string_slice_v1"} <= encodings
    # A selected escaped JSON page can also supply the exact shorter plaintext.
    reverse = (escaped, longer, RECEIPT)
    reverse_views = PydanticConversationModel._receipt_model_views(reverse)
    assert canonical_json_bytes(list(expand_receipt_history(reverse, reverse_views)[:-1])) == canonical_json_bytes([r.output for r in reverse[:-1]])
    for i, view in enumerate(reverse_views):
        check_references(view, i, {r.call.call_id: j for j, r in enumerate(reverse)})
    assert "json_unescape_string_slice_v1" in encodings
    model, _, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch, [("final_answer", ("Afgerond.",))])
    assert _generate(model, prior_actions=receipts[:-1], completed_action=RECEIPT).text == "Afgerond."
    wire = {part["call_id"]: json.loads(part["output"]) for part in requests[0]["input"]
            if part.get("type") == "function_call_output"}
    selected, overview = PydanticConversationModel._receipt_context_selection(receipts)
    assert tuple(wire[r.call.call_id] for r in selected) == PydanticConversationModel._receipt_model_views(selected)
    assert set(wire) == {r.call.call_id for r in selected}
    assert canonical_json_bytes([[item.call.arguments, item.output] for item in receipts]) == raw
    assert len(canonical_json_bytes(list(views))) < len(canonical_json_bytes(list(
        PydanticConversationModel._receipt_model_views(receipts, _literal_slices=False))))
    # Unverified output and unvalidated pages cannot supply a slice anchor.
    failed = ConversationActionReceipt(plain.call, {**plain.output, "status": "failed"})
    unknown = ConversationActionReceipt(plain.call, {**plain.output, "status": "ambiguous"})
    for untrusted in (failed, unknown, ConversationActionReceipt(plain.call, plain.output)):
        assert PydanticConversationModel._receipt_model_views((untrusted, shorter, RECEIPT))[1] == PydanticConversationModel._receipt_model_views((untrusted, shorter, RECEIPT), _literal_slices=False)[1]


def _coverage_pages():
    from contextlib import nullcontext
    from types import SimpleNamespace
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from tests.connectors.test_connector_results import _retained_document, TURN
    port, timeline, thread, _, authorize = _retained_document()
    text = "".join(f'{i:05d} λ🙂漢字\n' for i in range(1600))
    admission = timeline.rows[thread][0].payload["data"]
    admission["action_arguments"]["content"] = text
    proof = timeline.rows[thread][-1].payload["data"]["proof"]
    proof["expected"]["content"] = proof["observed"]["content"] = text
    document = port._receipt(TURN, timeline.events(thread), 2)
    reader = document.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._execution_lock = lambda _: nullcontext()
    route._timeline = lambda _: timeline
    route._ensure_running = authorize
    step = [3]
    def page(call_id, pointer="/call/arguments/content", offset=0, limit=6500, tool_id=reader):
        call = ConversationActionCall(call_id, "use_connector_tool", {"tool_id": tool_id,
            "arguments_json": json.dumps({"pointer": pointer, "offset": offset, "limit": limit, "query": None})})
        step[0] += 1
        return route.execute(TURN, call, step_index=step[0])
    return page, port, timeline, thread, reader


def test_covered_local_reads_select_exact_earlier_facts_and_keep_latest_on_sdk_wire(tmp_path, monkeypatch):
    from tests.model.test_pydantic_conversation_model import _phase_transport_adapter, _generate, RECEIPT
    page, _, _, _, _ = _coverage_pages()
    anchor = page("coverage-anchor")
    covered = page("coverage-shorter", offset=1000, limit=4500)
    extension = page("coverage-extension", offset=6000, limit=5000)
    combined = page("coverage-union", offset=1000, limit=8192)
    pointer = page("different-pointer", pointer="/call/arguments", limit=8192)
    progress = ConversationActionReceipt(ConversationActionCall("checkpoint", "save_work_progress",
        {"steps": [{"id": "remaining", "state": "in_progress", "title": "Read verified sources"}]}),
        {"status": "verified", "steps": [{"id": "remaining", "state": "in_progress"}]})
    receipts = (anchor, covered, extension, combined, pointer, progress, RECEIPT)
    original = canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in receipts])
    selected, overview = PydanticConversationModel._receipt_context_selection(receipts)
    assert selected == (anchor, extension, pointer, progress, RECEIPT)
    summaries = json.loads(overview.split("\n", 1)[1])
    assert [r["call"]["call_id"] for r in summaries] == [covered.call.call_id, combined.call.call_id]
    assert summaries[0]["call"]["arguments"] == covered.call.arguments
    assert {r["earlier_call_id"] for r in summaries[1]["coverage"]} == {anchor.call.call_id, extension.call.call_id}
    # All newly introduced characters remain literal or refer to earlier visible literals.
    views = PydanticConversationModel._receipt_model_views(selected)
    expanded = expand_receipt_history(selected, views)
    assert canonical_json_bytes(list(expanded[:-1])) == canonical_json_bytes([r.output for r in selected[:-1]])
    assert views[-1] == PydanticConversationModel._receipt_model_views(receipts)[-1]
    model, _, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch, [("final_answer", ("Afgerond.",))])
    assert _generate(model, prior_actions=receipts[:-1], completed_action=RECEIPT).text == "Afgerond."
    wire = {part["call_id"]: json.loads(part["output"]) for part in requests[0]["input"]
            if part.get("type") == "function_call_output"}
    assert set(wire) == {r.call.call_id for r in selected}
    assert tuple(wire[r.call.call_id] for r in selected) == views
    assert overview in requests[0]["instructions"]
    assert wire[progress.call.call_id] == progress.output
    assert canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in receipts]) == original


def test_coverage_keeps_gaps_versions_unknown_and_current_result_and_rejects_false_source():
    from dataclasses import replace
    from tests.connectors.test_connector_results import TURN
    from tests.model.test_pydantic_conversation_model import RECEIPT
    from eva.adapters.model.contracts import ConversationModelError
    page, port, timeline, thread, reader = _coverage_pages()
    anchor = page("retained-anchor")
    gap = page("uncovered-gap", offset=10000, limit=3000)
    current = page("current-duplicate", offset=1000, limit=4500)
    missing = page("missing-selection", pointer="/missing")
    # Same content and pointer, but another retained action version has another scoped reader.
    timeline.rows[thread][0].payload["data"]["action_call_id"] = "other-source-version"
    document = port._receipt(TURN, timeline.events(thread), 2)
    other_reader = document.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    assert other_reader != reader
    version = page("other-version", tool_id=other_reader)
    untrusted = tuple(ConversationActionReceipt(ConversationActionCall("untrusted-" + status, anchor.call.name,
        anchor.call.arguments), {**anchor.output, "status": status}) for status in ("failed", "ambiguous"))
    receipts = (anchor, gap, version, missing, *untrusted, current)
    assert PydanticConversationModel._receipt_context_selection(receipts) == (receipts, "")
    # A failed or unvalidated page never becomes a coverage anchor.
    unvalidated = ConversationActionReceipt(anchor.call, anchor.output)
    assert PydanticConversationModel._receipt_context_selection((unvalidated, current, RECEIPT)) == ((unvalidated, current, RECEIPT), "")
    # The current receipt may have a same-value reference to an earlier receipt: keep that anchor.
    repeated = page("repeat-current-version", tool_id=other_reader)
    anchored = (version, repeated, replace(repeated, call=replace(repeated.call, call_id="latest-reference"), model_output=None))
    assert PydanticConversationModel._receipt_context_selection(anchored) == (anchored, "")
    injected = deepcopy(current.model_output)
    injected["ancestor_model_projection"]["source"]["receipt_sha256"] = "0" * 64
    with pytest.raises(ConversationModelError, match="differs"):
        PydanticConversationModel._receipt_context_selection((anchor, replace(current, model_output=injected), RECEIPT))


def test_actual_input_misfit_defers_only_old_scoped_reads_and_rechecks_sdk_envelope(tmp_path, monkeypatch):
    from tests.model.test_pydantic_conversation_model import _phase_transport_adapter, _generate, _runtime_budget, RECEIPT
    from eva.adapters.model.pydantic_conversation import _input_tokenizer, _INPUT_FRAMING_RESERVE_TOKENS
    page, _, _, _, _ = _coverage_pages()
    first = page("older-local-first", limit=8192)
    second = page("older-local-next", offset=8192, limit=6500)
    progress = ConversationActionReceipt(ConversationActionCall("active-plan", "save_work_progress",
        {"steps": [{"id": "verify", "state": "in_progress", "title": "Check retained evidence"}]}),
        {"status": "verified", "steps": [{"id": "verify", "state": "in_progress"}]})
    failed = ConversationActionReceipt(ConversationActionCall("failed-read", first.call.name, first.call.arguments),
        {**first.output, "status": "failed"})
    receipts = (first, second, progress, failed, RECEIPT)
    original = canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in receipts])
    assert PydanticConversationModel._receipt_context_selection(receipts) == (receipts, "")
    model, _, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch, [("final_answer", ("Afgerond.",))])
    model._resource_budget = _runtime_budget(max_input_tokens=65536)
    admissions = []
    original_admit = model._admit_request
    def record(body, **kwargs):
        admissions.append(body)
        return original_admit(body, **kwargs)
    model._admit_request = record
    assert _generate(model, prior_actions=receipts[:-1], completed_action=RECEIPT).text == "Afgerond."
    high_wire = requests[-1]
    count = lambda body: len(_input_tokenizer().encode_ordinary(canonical_json_bytes(body).decode())) + _INPUT_FRAMING_RESERVE_TOKENS
    limit = count(admissions[0]) - 1
    budget = _runtime_budget(max_input_tokens=limit)
    model._resource_budget = budget
    admissions.clear()
    assert _generate(model, prior_actions=receipts[:-1], completed_action=RECEIPT).text == "Afgerond."
    assert count(admissions[0]) > limit and count(admissions[1]) <= limit
    low_wire = requests[-1]
    assert "wegens invoerruimte" not in high_wire["instructions"]
    assert "wegens invoerruimte" in low_wire["instructions"]
    assert "Hun tekst is nu niet in deze modelcontext" in low_wire["instructions"]
    outputs = {p["call_id"]: p for p in low_wire["input"] if p.get("type") == "function_call_output"}
    assert set(outputs) == {progress.call.call_id, failed.call.call_id, RECEIPT.call.call_id}
    assert json.loads(outputs[progress.call.call_id]["output"]) == progress.output
    assert json.loads(outputs[failed.call.call_id]["output"]) == failed.output
    assert high_wire["input"][-2:] == low_wire["input"][-2:]
    selected, note = PydanticConversationModel._receipt_context_selection(receipts, defer_local_reads=True)
    index = json.loads(note.split("\n", 1)[1])
    assert index["contract"] == "EvaDeferredLocalReadsModelIndex/v1"
    assert index["action"] == "use_connector_tool"
    assert selected == (progress, failed, RECEIPT)
    assert len(index["sources"]) == 1
    for summary, old in zip(index["reads"], (first, second), strict=True):
        source = index["sources"][summary["source_index"]]
        assert summary["call_id"] == old.call.call_id
        assert source["reader_tool_id"] == old.call.arguments["tool_id"]
        assert summary["selections"] == [json.loads(old.call.arguments["arguments_json"])]
        assert source["source_version"] == old.output["result"]["sha256"]
        assert summary["ranges"] == [{k: old.output["result"][k] for k in ("pointer", "offset", "next_offset", "selection_length")}]
    assert model._resource_budget is budget and budget.max_input_tokens == limit
    assert canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in receipts]) == original
    assert len(requests) == 2  # Two local MockTransport responses; no provider/external action.


def test_pressure_deferral_keeps_latest_literal_anchor_unknown_and_invalid_projections():
    from dataclasses import replace
    from tests.model.test_pydantic_conversation_model import RECEIPT
    from eva.adapters.model.contracts import ConversationModelError
    page, _, _, _, _ = _coverage_pages()
    anchor = page("latest-needed-anchor")
    other = page("other-source-range", offset=9000, limit=6500)
    latest = page("latest-same-content")
    unknown = ConversationActionReceipt(ConversationActionCall("unknown-result", other.call.name, other.call.arguments),
        {**other.output, "status": "ambiguous"})
    unvalidated = ConversationActionReceipt(ConversationActionCall("no-host-projection", other.call.name, other.call.arguments), other.output)
    history = (anchor, other, unknown, unvalidated, latest)
    baseline = PydanticConversationModel._receipt_model_views(history, _literal_slices=False)[-1]
    selected, note = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=True)
    assert selected == (anchor, unknown, unvalidated, latest)
    assert PydanticConversationModel._receipt_model_views(selected, _literal_slices=False)[-1] == baseline
    assert other.call.call_id in note
    injected = deepcopy(other.model_output)
    injected["ancestor_model_projection"]["source"]["receipt_sha256"] = "0" * 64
    with pytest.raises(ConversationModelError, match="differs"):
        PydanticConversationModel._receipt_context_selection((replace(other, model_output=injected), RECEIPT), defer_local_reads=True)


def test_pressure_index_groups_small_reads_without_losing_selectors_or_source_versions():
    from tests.model.test_pydantic_conversation_model import RECEIPT
    page, source_port, shared_timeline, _, reader = _coverage_pages()
    first = page("small-first", offset=0, limit=500)
    second = page("small-second", offset=1000, limit=500)
    pointer = page("small-pointer", pointer="/call/arguments", offset=2500, limit=500)
    other_page, port, timeline, thread, _ = _coverage_pages()
    timeline.rows[thread][0].payload["data"]["action_call_id"] = "other-retained-version"
    from tests.connectors.test_connector_results import TURN
    other_document = port._receipt(TURN, timeline.events(thread), 2)
    other_reader = other_document.model_output["ancestor_model_projection"]["manifest"]["reader"]["tool_id"]
    other = other_page("small-other-version", tool_id=other_reader, offset=3000, limit=500)
    from contextlib import nullcontext
    from types import SimpleNamespace
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("use_connector_tool",)),
        SimpleNamespace(supports_stored_results=True))
    route._execution_lock = lambda _: nullcontext()
    route._timeline = lambda _: shared_timeline
    route._ensure_running = source_port._ensure_running
    selections = [{"pointer": "/call/arguments/content", "offset": offset, "limit": 100, "query": None}
                  for offset in (6000, 7000)]
    batch = route.execute(TURN, ConversationActionCall("small-batch", "use_connector_tool",
        {"tool_id": reader, "arguments_json": json.dumps({"selections": selections})}), step_index=30)
    prior = (first, second, pointer, batch, other)
    history = (*prior, RECEIPT)
    raw = canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in history])
    # Ordinary fitting history still exposes each unique page, regardless of size.
    assert PydanticConversationModel._receipt_context_selection(history) == (history, "")
    selected, note = PydanticConversationModel._receipt_context_selection(history, defer_local_reads=True)
    assert selected == (RECEIPT,)
    index = json.loads(note.split("\n", 1)[1])
    assert len(index["sources"]) == 2
    assert [r["source_index"] for r in index["reads"]] == [0, 0, 0, 0, 1]
    assert "Hun tekst is nu niet in deze modelcontext" in note
    assert "bewaarde bevindingen en voortgang" in note
    for read, receipt in zip(index["reads"], prior, strict=True):
        source = index["sources"][read["source_index"]]
        assert read["call_id"] == receipt.call.call_id
        # These are the exact original authorized retrieval arguments, with no
        # provider invocation, generated summary or inferred selector.
        assert source["reader_tool_id"] == receipt.call.arguments["tool_id"]
        original_arguments = json.loads(receipt.call.arguments["arguments_json"])
        assert read["selections"] == original_arguments.get("selections", [original_arguments])
        pages = receipt.output["result"].get("pages", [receipt.output["result"]])
        assert read["ranges"] == [{key: page[key]
                                  for key in ("pointer", "offset", "next_offset", "selection_length")}
                                  for page in pages]
        assert source["source_version"] == receipt.output["result"]["sha256"]
        assert source["source_ingress_sha256"] == receipt.model_output["ancestor_model_projection"]["source"]["source_ingress_sha256"]
    assert reader != other_reader
    assert PydanticConversationModel._receipt_model_views(selected)[-1] == PydanticConversationModel._receipt_model_views(history)[-1]
    assert canonical_json_bytes([[r.call.arguments, r.output, r.model_output] for r in history]) == raw
