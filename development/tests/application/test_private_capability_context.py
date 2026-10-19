"""Pure declared capability projection; no provider or execution dispatch."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from eva.application.private_capability_context import build_private_capability_context
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

NOW = datetime(2026, 10, 19, 8, tzinfo=UTC)
SHA = "a" * 64
SCHEMA = {"type": "object", "properties": {"path": {"type": "string", "const": "/read"}},
          "required": ["path"], "additionalProperties": False}


def capability(name="read_service", schema=None):
    return {"action_name": name, "tool_name": name, "execute_schema": deepcopy(SCHEMA if schema is None else schema),
        "configuration_sha256": "b" * 64, "new_cost_eur_cents": 0, "new_obligation": False,
        "data_scope": "owner_private_local", "communication_scope": "none",
        "cost_evidence": "Existing local read, no new subscription", "observe_schema": {},
        "observation_tool_name": name}


def policy(*caps, digital=()):
    return {"enabled": True, "capabilities": list(caps),
        "digital_work": {"capability_ids": list(digital), "worker_profile_release_sha256": "c" * 64}}


def build(p, descriptors=None, **kwargs):
    return build_private_capability_context(p, SHA, observed_at=NOW,
        descriptors={} if descriptors is None else descriptors, **kwargs)


def check_hash(result):
    assert result["configuration_sha256"] == canonical_sha256({k: v for k, v in result.items()
        if k not in {"configuration_sha256", "observed_at"}})
    assert len(canonical_json_bytes(result)) <= 8192


def test_native_schema_overrides_broader_model_and_projection_does_not_mutate_inputs():
    p = policy(capability(), digital=("read_service",))
    broad = {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": True}
    descriptors = {"read_service": {"name": "read_service", "description": "Read current local service",
        "parameters": broad, "credentials": "MUST_NOT_EXPORT"}}
    original = deepcopy((p, descriptors))
    result = build(p, descriptors)
    row, = result["capabilities"]
    assert row["parameters"] == SCHEMA and row["parameters"] != broad
    assert row["routes"] == ["native", "digital_work"]
    assert row["parameters"]["required"] == ["path"]
    assert row["data_scope"] == "owner_private_local" and row["new_cost_eur_cents"] == 0
    assert row["communication_scope"] == "none" and row["new_obligation"] is False
    assert result["is_execution_permission"] is False and "not_live_health" in result["classification"]
    assert "MUST_NOT_EXPORT" not in canonical_json_bytes(result).decode()
    row["parameters"]["required"].append("mutation")
    assert (p, descriptors) == original


def test_only_admitted_names_and_allowlisted_metadata_are_exported():
    p = policy(capability(), digital=("write_document",))
    p.update(secret="NEVER_EXPORT", private_contact={"token": "NEVER_EXPORT"}, authorization_source="NEVER_EXPORT")
    result = build(p, {"unadmitted": {"description": "NEVER_EXPORT"},
        "write_document": {"description": "Save bounded local output", "parameters": SCHEMA}})
    assert [r["name"] for r in result["capabilities"]] == ["read_service", "write_document"]
    assert "NEVER_EXPORT" not in canonical_json_bytes(result).decode()
    assert result["capabilities"][1]["cost_scope"].endswith("unknown_not_free")
    check_hash(result)


def test_disabled_mandate_exposes_no_native_or_digital_names():
    p = policy(capability(), digital=("write_document",))
    p["enabled"] = False
    assert build(p)["capabilities"] == []


def test_missing_descriptor_is_explicit_and_does_not_invent_parameters():
    result = build(policy(digital=("host_action",)))
    row, = result["capabilities"]
    assert row["description"] == "" and row["definition_sha256"] == canonical_sha256({"description": "", "parameters": None})
    assert row["parameters_included"] is False and "parameters" not in row
    assert result["omitted_schema_count"] == 1
    assert result["omitted_description_count"] == 1


def test_budget_keeps_all_names_and_omits_full_schema_atomically():
    huge = deepcopy(SCHEMA)
    huge["properties"]["path"]["enum"] = ["x" * 9000]
    names = tuple("action_" + str(i) for i in range(23))
    descriptions = {name: {"description": "Meaningful action "+name, "parameters": huge} for name in names}
    result = build(policy(digital=names), descriptions)
    assert {row["name"] for row in result["capabilities"]} == set(names)
    assert result["omitted_schema_count"] == 23
    for row in result["capabilities"]:
        assert row["parameters_included"] is False and "parameters" not in row
        assert row["definition_sha256"] == canonical_sha256({"description": descriptions[row["name"]]["description"], "parameters": huge})
    check_hash(result)


def test_configuration_hash_excludes_time_but_binds_mandate_schema_and_description():
    p = policy(capability())
    descriptors = {"read_service": {"description": "é" * 1000, "parameters": {}}}
    first = build(p, descriptors)
    second = build_private_capability_context(p, SHA, observed_at=NOW + timedelta(days=1), descriptors=descriptors)
    assert first["observed_at"] != second["observed_at"]
    assert first["configuration_sha256"] == second["configuration_sha256"]
    assert first["omitted_description_count"] == 1
    changed = build_private_capability_context(p, "d" * 64, observed_at=NOW, descriptors=descriptors)
    assert changed["configuration_sha256"] != first["configuration_sha256"]
    p["capabilities"][0]["execute_schema"]["properties"]["path"]["const"] = "/different"
    assert build(p, descriptors)["configuration_sha256"] != first["configuration_sha256"]
    descriptors["read_service"]["description"] += "last byte"
    assert build(policy(capability()), descriptors)["configuration_sha256"] != first["configuration_sha256"]
    check_hash(first)


@pytest.mark.parametrize("bad_time", [NOW.replace(tzinfo=None), NOW.replace(microsecond=1)])
def test_invalid_host_time_rejected(bad_time):
    with pytest.raises(ValueError):
        build_private_capability_context(policy(), SHA, observed_at=bad_time, descriptors={})


def test_model_metadata_export_is_deep_copy_of_existing_tools_only(tmp_path):
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _ACTION_TOOLS
    model = PydanticConversationModel(auth_file=tmp_path / "not-used")
    known = next(iter(_ACTION_TOOLS))
    model._conversation_tools["external"] = {"name": "external", "description": "Existing external descriptor", "parameters": SCHEMA}
    original = deepcopy((_ACTION_TOOLS[known], model._conversation_tools["external"]))
    result = model.private_action_descriptors((known, "external", "not_admitted_here"))
    assert set(result) == {known, "external"}
    result[known]["description"] = "Changed copy"
    result["external"]["parameters"]["required"].append("changed")
    assert (_ACTION_TOOLS[known], model._conversation_tools["external"]) == original


def test_explicitly_disabled_digital_scope_never_exports_its_names():
    p = policy(capability(), digital=("write_document",))
    p["digital_work"]["enabled"] = False
    assert [row["name"] for row in build(p)["capabilities"]] == ["read_service"]


def test_full_twenty_three_action_catalog_keeps_meaning_before_schemas():
    names = ["action_" + str(i) for i in range(23)]
    huge = deepcopy(SCHEMA)
    huge["properties"]["path"]["enum"] = ["x" * 9000]
    descriptors = {name: {"description": "Meaningful existing action description " * 40,
        "parameters": huge} for name in names}
    p = policy(*(capability(name, huge) for name in names[:10]), digital=names[10:])
    result = build(p, descriptors)
    assert len(result["capabilities"]) == 23
    assert all(row["description"].startswith("Meaningful existing action") for row in result["capabilities"])
    assert all(not row["parameters_included"] and "parameters" not in row for row in result["capabilities"])
    assert result["omitted_schema_count"] == result["omitted_description_count"] == 23
    check_hash(result)
