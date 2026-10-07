"""Candidate continuation retains the installed reference and release provenance."""
import hashlib
from copy import deepcopy

import pytest

from eva.application.component_improvements import draft_component, prepare_component_release
from eva.contracts.canonical_json import canonical_sha256


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def arguments(source, old, new):
    return {"path": "example.py", "source_sha256": sha(source),
            "old_text": old, "new_text": new, "problem": "public functional fixture",
            "expected_gain": "measured later", "verification_plan": "fixed independent assessment"}


def report(draft):
    outcome = {"exit_code": 0, "timed_out": False, "stdout_truncated": False,
               "stderr_truncated": False}
    return {"contract": "EvaIsolatedComponentEvaluation/v1", "relative_path": draft["path"],
            "baseline_sha256": draft["source_sha256"], "candidate_sha256": draft["candidate_sha256"],
            "installed": False, "semantic_improvement_verified": False,
            "baseline": dict(outcome), "candidate": dict(outcome)}


def test_three_generations_and_release_keep_installed_reference(tmp_path):
    source = "VALUE = 1\nSTYLE = 'plain'\n"
    target = tmp_path / "example.py"
    target.write_text(source)
    first = draft_component(tmp_path, arguments(source, "VALUE = 1", "VALUE = 2"))
    second = draft_component(tmp_path, arguments(first["candidate_content"], "'plain'", "'warm'"), parent=first)
    third = draft_component(tmp_path, arguments(second["candidate_content"], "VALUE = 2", "VALUE = 3"), parent=second)
    assert target.read_text() == source
    assert [d["source_sha256"] for d in (first, second, third)] == [sha(source)] * 3
    assert third["candidate_lineage"]["ancestor_candidate_sha256s"] == [
        first["candidate_sha256"], second["candidate_sha256"]]
    assert "-VALUE = 1" in third["patch"] and "+VALUE = 3" in third["patch"]
    assert "-STYLE = 'plain'" in third["patch"] and "+STYLE = 'warm'" in third["patch"]
    package = prepare_component_release(third, report(third))
    assert package["baseline_sha256"] == sha(source)
    assert package["candidate_lineage"] == third["candidate_lineage"]
    assert package["state"] == "awaiting_measured_gain_review"
    assert package["installed"] is False
    assert package["candidate_package_sha256"] == canonical_sha256({
        key: value for key, value in package.items() if key != "candidate_package_sha256"})
    # A report evaluated against the parent instead of the installed reference is rejected.
    altered = report(third)
    altered["baseline_sha256"] = second["candidate_sha256"]
    with pytest.raises(ValueError, match="bindings differ"):
        prepare_component_release(third, altered)


@pytest.mark.parametrize("change", ["bytes", "path", "baseline", "ancestry"])
def test_rejects_tampered_parent(tmp_path, change):
    source = "VALUE = 1\n"
    (tmp_path / "example.py").write_text(source)
    first = draft_component(tmp_path, arguments(source, "VALUE = 1", "VALUE = 2"))
    parent = deepcopy(first)
    if change == "bytes":
        parent["candidate_content"] = "VALUE = 90\n"
    elif change == "path":
        parent["path"] = "another.py"
    elif change == "baseline":
        parent["source_sha256"] = "0" * 64
    else:
        parent["candidate_lineage"] = {"ancestor_candidate_sha256s": ["not-a-hash"]}
    with pytest.raises(ValueError, match="parent candidate source"):
        draft_component(tmp_path, arguments(first["candidate_content"], "VALUE = 2", "VALUE = 3"), parent=parent)


def test_rejects_stale_selection_changed_installation_and_full_revert(tmp_path):
    source = "VALUE = 1\n"
    target = tmp_path / "example.py"
    target.write_text(source)
    first = draft_component(tmp_path, arguments(source, "VALUE = 1", "VALUE = 2"))
    with pytest.raises(ValueError, match="changed since it was selected"):
        draft_component(tmp_path, arguments(source, "VALUE = 2", "VALUE = 3"), parent=first)
    with pytest.raises(ValueError, match="does not change"):
        draft_component(tmp_path, arguments(first["candidate_content"], "VALUE = 2", "VALUE = 1"), parent=first)
    target.write_text("VALUE = 10\n")
    with pytest.raises(ValueError, match="parent candidate source"):
        draft_component(tmp_path, arguments(first["candidate_content"], "VALUE = 2", "VALUE = 3"), parent=first)


def test_actual_model_serialization_allows_first_draft_without_parent():
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.tools import ToolDefinition
    from eva.adapters.model.pydantic_conversation import _descriptor_tool
    from eva.adapters.postgres.component_improvement_actions import DRAFT, _SCHEMAS, component_descriptors

    descriptor = next(item for item in component_descriptors() if item.name == DRAFT)
    tool = _descriptor_tool(descriptor)
    model = OpenAIResponsesModel("gpt-6-sol",
        provider=OpenAIProvider(api_key="offline-schema-inspection-no-request"))
    params = ModelRequestParameters(function_tools=[ToolDefinition(
        name=tool["name"], description=tool["description"],
        parameters_json_schema=tool["parameters"], strict=tool["strict"])])
    _, prepared = model.prepare_request(None, params)
    wire = model._map_tool_definition(prepared.function_tools[0], visibility="eager")
    schema = wire["parameters"]
    assert wire["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["parent_draft_step"]["type"] == ["integer", "null"]
    assert "parent_draft_step" not in _SCHEMAS[DRAFT]["required"]
