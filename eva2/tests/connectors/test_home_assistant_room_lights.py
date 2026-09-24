"""Room-light resolution and frozen multi-target receipts without physical effects."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from eva.adapters.connectors import home_assistant as module
from eva.adapters.connectors.home_assistant import HomeAssistantConfig, HomeAssistantToolAdapter
from eva.application.conversation import ConversationActionCall, ConversationClarificationRequired
from eva.application.orchestration import ToolRequest


def fixture(monkeypatch, *, grouped=False):
    states = [{"entity_id": "light." + name, "state": "off", "attributes": {"friendly_name": label}}
              for name, label in [("a", "Ceiling"), ("b", "Wall"), ("ring", "LED ring")]]
    if grouped:
        states.append({"entity_id": "light.woonkamer", "state": "off", "attributes": {"friendly_name": "Woonkamer"}})
    inventory = {"areas": [{"area_id": "terrace", "name": "Overkapping"}], "entities": [
        {"entity_id": row["entity_id"], "domain": "light", "area_id": "terrace", "disabled": False,
         "entity_category": "config" if row["entity_id"] == "light.ring" else None}
        for row in states]}
    async def read_inventory():return inventory
    adapter = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "synthetic"), household_inventory=read_inventory)
    monkeypatch.setattr(adapter, "_light_catalog_sync", lambda: states)
    return adapter, states, inventory


def prepare(adapter, target, *, allowed=None):
    allowed = frozenset((name, "turn_on") for name in ("light.a", "light.b", "light.ring", "light.woonkamer")) if allowed is None else allowed
    planner = adapter.conversation_effect_planner(allowlist=allowed,
        principal_sha256_allowlist=frozenset({hashlib.sha256(b"owner").hexdigest()}))
    return asyncio.run(planner.prepare(SimpleNamespace(principal_id="owner"),
        ConversationActionCall("call", "set_home_assistant_light", {"target": target, "state": "on"})))


def test_natural_plural_uses_existing_named_group_without_changing_single_contract(monkeypatch):
    adapter, _, _ = fixture(monkeypatch, grouped=True)
    result = prepare(adapter, "lampen in de woonkamer")
    assert result.tool_name == "home_assistant.light.turn_on"
    assert result.arguments["entity_id"] == "light.woonkamer"


def test_room_uses_registry_members_and_preserves_explicit_diagnostic_control(monkeypatch):
    adapter, _, _ = fixture(monkeypatch)
    result = prepare(adapter, "lampen in de overkapping")
    assert result.arguments["entity_ids"] == ["light.a", "light.b"]
    assert result.expected_proof == {"entity_ids": ["light.a", "light.b"], "state": "on"}
    assert prepare(adapter, "LED ring").arguments["entity_id"] == "light.ring"
    with pytest.raises(ConversationClarificationRequired):
        prepare(adapter, "overkapping", allowed=frozenset({("light.a", "turn_on")}))


@pytest.mark.parametrize("area", [True, False])
def test_unavailable_room_or_named_member_never_becomes_partial_success(monkeypatch, area):
    adapter, states, inventory = fixture(monkeypatch)
    states[1]["state"] = "unavailable"
    if not area:
        inventory["areas"] = []
        for row in states[:2]:row["attributes"]["friendly_name"] = "Overkapping " + row["entity_id"]
    with pytest.raises(ConversationClarificationRequired):prepare(adapter, "overkapping")


@pytest.mark.parametrize("partial", [False, True])
def test_group_dispatch_and_readback_freeze_ids_without_repeating_failed_effects(monkeypatch, partial):
    adapter, _, _ = fixture(monkeypatch)
    prepared = prepare(adapter, "overkapping")
    command = ToolRequest("thread", "operation", "checkpoint", prepared.tool_name,
                          prepared.arguments, prepared.expected_proof)
    sent = []
    def send(**kwargs):
        identifier = kwargs["entity_id"]; sent.append(identifier)
        if partial and identifier == "light.b":raise module.HomeAssistantMcpError("synthetic uncertain response")
        return {}
    monkeypatch.setattr(module, "call_assist_effect", send)
    async def state(*, entity_id):return {"entity_id": entity_id, "state": "on"}
    monkeypatch.setattr(adapter, "state", state)
    dispatch = asyncio.run(adapter.execute(command))
    result = asyncio.run(adapter.observe(command, dispatch))
    assert result.disposition == ("conflict" if partial else "verified")
    assert asyncio.run(adapter.observe(command, None)).disposition == "conflict"
    assert sorted(sent) == ["light.a", "light.b"]
