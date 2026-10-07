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
    output = {"status": "verified", "result": {"model_view_reference": value, "content": "Provider data." * 100}}
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
