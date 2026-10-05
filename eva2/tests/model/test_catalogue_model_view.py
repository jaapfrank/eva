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
