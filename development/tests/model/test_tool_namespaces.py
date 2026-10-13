"""Grouping must change presentation only, including retained-call replay."""
import json
import httpx2
from eva.adapters.model.tool_namespaces import deferred_namespaces, _GROUPS
from eva.adapters.model.openai_codex_provider import _codex_compatible_request


def tool(name):
    return {"type": "function", "name": name, "description": "Original details for " + name,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True}


def test_grouping_retains_exact_definitions_and_authorized_subset():
    original = [tool("get_delegated_work"), tool("stop_delegated_work"), tool("new_extension"), {"type": "tool_search"}]
    snapshot = json.dumps(original)
    grouped, mapping = deferred_namespaces(original)
    functions = [f for group in grouped if group["type"] == "namespace" for f in group["tools"]]
    assert functions == [{**f, "defer_loading": True} for f in original if f["type"] == "function"]
    assert mapping == {"get_delegated_work": "eva_work_control", "stop_delegated_work": "eva_work_control", "new_extension": "new_extension"}
    assert json.dumps(original) == snapshot
    assert grouped[-1] == {"type": "tool_search"}
    one, mapping = deferred_namespaces([original[0]])
    assert len(one[0]["tools"]) == 1
    assert mapping["get_delegated_work"] == "eva_work_control"
    assert all(len(names) <= 8 for _, _, names in _GROUPS)
    names = [name for _, _, group in _GROUPS for name in group]
    assert len(names) == len(set(names))


def test_unknown_extension_with_group_name_does_not_merge_with_reserved_group():
    groups, mapping = deferred_namespaces([tool("get_delegated_work"), tool("eva_work_control")])
    assert mapping == {"get_delegated_work": "get_delegated_work", "eva_work_control": "eva_work_control"}
    assert all(len(group["tools"]) == 1 for group in groups)


def test_old_individual_namespace_receipt_projects_to_current_group_without_new_action():
    call = {"type": "function_call", "name": "get_delegated_work", "namespace": "get_delegated_work",
            "call_id": "already-completed", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": call["call_id"], "output": "Saved result"}
    body = {"model": "gpt-6-sol", "store": False, "stream": True, "instructions": "Answer from the result.",
            "input": [{"role": "user", "content": "Read my task result."}, call, output],
            "tools": [tool("get_delegated_work"), tool("stop_delegated_work")]}
    request = httpx2.Request("POST", "https://example.invalid/responses", json=body)
    projected = json.loads(_codex_compatible_request(request, namespace_functions=True).content)
    assert projected["input"][1] == {**call, "namespace": "eva_work_control"}
    assert projected["input"][2] == output
    assert len(projected["tools"]) == 2  # Search plus one group.
