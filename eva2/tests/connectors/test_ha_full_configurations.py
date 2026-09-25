"""Full authorized configuration batches preserve HA content without writes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter, HomeAssistantHouseholdTool
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.contracts.canonical_json import canonical_sha256


def reader(configs):
    value = HomeAssistantAdminAdapter(base_url="http://ha.invalid", token="fixture")
    async def inventory():
        return {"captured_at": "2026-09-24T00:00:00Z", "coverage": {}, "areas": [],
                "automations": [{"automation_id": key, "area_id": None} for key in configs]}
    async def get(identifier):
        return configs[identifier]
    value.household_inventory = inventory
    value._automation_reader = SimpleNamespace(_get=get)
    return value


def test_full_batch_retains_conditions_data_unknown_fields_and_decimals():
    configs = {"one": {"alias": "One", "conditions": [{"condition": "template", "value_template": "{{ states('sensor.x') | float > 21.5 }}"}],
                       "actions": [{"action": "climate.set_temperature", "data": {"temperature": 21.5}}], "custom": {"future_field": "keep"}},
               "two": {"alias": "Two", "use_blueprint": {"path": "local/example.yaml", "input": {"level": 0.5}}}}
    result = asyncio.run(reader(configs).automation_details_many(list(configs), full=True))
    assert result["configurations_read"] == 2
    assert {row["automation_id"]: json.loads(row["configuration_json"]) for row in result["automations"]} == configs
    assert all(row["coverage"]["complete"] for row in result["automations"])
    assert canonical_sha256(result)


def test_output_limit_is_explicit_instead_of_claiming_full_content():
    result = asyncio.run(reader({"large": {"description": "x" * 40001}}).automation_details_many(["large"], full=True))
    row = result["automations"][0]
    assert row["configuration_read"] and not row["coverage"]["complete"]
    assert "configuration_json" not in row
    assert row["reason"] == "configuration_output_limit"


@pytest.mark.parametrize("kind,principal", [("household", "other"), ("private_person", "other"), ("unknown", "owner")])
def test_full_configuration_view_requires_matching_authorized_audience(kind, principal):
    tool = HomeAssistantHouseholdTool(reader({}))
    turn = SimpleNamespace(principal_id="owner", audience=SimpleNamespace(kind=kind, principal_id=principal))
    call = ConversationActionCall("read", "read_home_assistant_inventory", {
        "section": "automation_configurations", "area_id": "", "offset": 0, "snapshot_sha256": "", "automation_ids": ["one"]})
    with pytest.raises(ConversationConflict):
        asyncio.run(tool.prepare(turn, call))


@pytest.mark.parametrize("kind", ["private_person", "household"])
def test_authorized_full_view_uses_existing_dispatch_and_observation(kind):
    tool = HomeAssistantHouseholdTool(reader({"one": {"alias": "One", "custom": 0.5}}))
    turn = SimpleNamespace(principal_id="owner", audience=SimpleNamespace(kind=kind, principal_id="owner"))
    call = ConversationActionCall("read", "read_home_assistant_inventory", {
        "section": "automation_configurations", "area_id": "", "offset": 0, "snapshot_sha256": "", "automation_ids": ["one"]})
    preparation = asyncio.run(tool.prepare(turn, call))
    command = SimpleNamespace(tool_name=preparation.tool_name, arguments=preparation.arguments, expected_proof=preparation.expected_proof)
    dispatch = asyncio.run(tool.execute(command))
    proof = asyncio.run(tool.observe(command, dispatch))
    assert proof.disposition == "verified"
    assert json.loads(proof.evidence["observation"]["automations"][0]["configuration_json"])["custom"] == 0.5
