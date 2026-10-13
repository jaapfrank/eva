"""Domain selection narrows discovery before paging without changing old requests."""
import asyncio
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter, HomeAssistantHouseholdTool
from eva.application.conversation import ConversationConflict


def inventory():
    states = [{"entity_id": f"camera.c{i:03}", "state": "idle", "attributes": {}}
              for i in range(55)]
    states += [{"entity_id": f"automation.a{i:03}", "state": "on", "attributes": {}}
               for i in range(60)]
    registry = [{"entity_id": row["entity_id"], "area_id": "outside"}
                for row in states]
    registry += [{"entity_id": "camera.indoor", "area_id": "inside"}]
    return HomeAssistantAdminAdapter._project_household(
        [{"area_id": "outside"}, {"area_id": "inside"}], [], registry, states)


def arguments(**changes):
    return dict(section="entities", area_id="", offset=0,
                snapshot_sha256="", automation_ids=[], **changes)


def test_domain_and_room_filters_precede_paging_and_unknown_domain_is_empty():
    data = inventory()
    first = HomeAssistantAdminAdapter._household_page(data, section="entities", domain="camera", area_id="outside")
    assert first["total"] == 55 and first["next_offset"] == 50
    assert all(row["entity_id"].startswith("camera.") for row in first["items"])
    second = HomeAssistantAdminAdapter._household_page(data, section="entities", domain="camera",
        area_id="outside", offset=50, snapshot_sha256=first["snapshot_sha256"])
    assert len(second["items"]) == 5 and second["has_more"] is False
    assert not ({r["entity_id"] for r in first["items"]} & {r["entity_id"] for r in second["items"]})
    empty = HomeAssistantAdminAdapter._household_page(data, section="entities", domain="climate")
    assert empty["total"] == 0 and empty["items"] == []
    legacy = HomeAssistantAdminAdapter._household_page(data, section="entities")
    explicit = HomeAssistantAdminAdapter._household_page(data, section="entities", domain="")
    assert legacy == explicit and "domain" not in legacy
    assert legacy["total"] == 116


@pytest.mark.parametrize("domain", [None, 1, "camera.front", "Camera", "camera/../", "x"*65])
def test_invalid_domain_rejected_before_external_read(domain):
    with pytest.raises(ConversationConflict):
        HomeAssistantHouseholdTool._validate(arguments(domain=domain))


@pytest.mark.parametrize("section", ["overview", "areas", "devices", "automations"])
def test_domain_cannot_silently_filter_unrelated_sections(section):
    args = arguments(domain="camera"); args["section"] = section
    with pytest.raises(ConversationConflict): HomeAssistantHouseholdTool._validate(args)
    with pytest.raises(ValueError):
        HomeAssistantAdminAdapter._household_page(inventory(), section=section, domain="camera")


@pytest.mark.parametrize("domain", [None, "", "camera"])
def test_existing_durable_route_preserves_selection_and_legacy_replay(domain):
    class Reader:
        async def household_page(self, **selection):
            self.selection = selection
            return HomeAssistantAdminAdapter._household_page(inventory(), **selection)
    reader = Reader(); tool = HomeAssistantHouseholdTool(reader)
    args = arguments() if domain is None else arguments(domain=domain)
    call = SimpleNamespace(name="read_home_assistant_inventory", arguments=args)
    prepared = asyncio.run(tool.prepare(None, call))
    command = SimpleNamespace(tool_name=prepared.tool_name, arguments=prepared.arguments,
                              expected_proof=prepared.expected_proof)
    dispatch = asyncio.run(tool.execute(command))
    observed = asyncio.run(tool.observe(command, dispatch))
    assert observed.disposition == "verified"
    assert dispatch.details["selection"] == args
    assert reader.selection.get("domain", "") == (domain or "")
    assert ("domain" in reader.selection) is bool(domain)
    if domain:
        assert dispatch.details["observation"]["total"] == 56
