"""Bounded field selection preserves source scope and pagination."""
from copy import deepcopy
import asyncio
from types import SimpleNamespace
import pytest
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter as Adapter, HomeAssistantHouseholdTool
from eva.application.conversation import ConversationConflict
from tests.connectors.test_home_assistant_domain_selection import inventory, arguments


def test_selected_fields_large_page_preserves_snapshot_rows_and_raw_inventory():
    data = inventory();before=deepcopy(data)
    full=Adapter._household_page(data,section="entities",domain="camera")
    selected=Adapter._household_page(data,section="entities",domain="camera",fields=["entity_id","name"],limit=200)
    expected=[{k:r[k] for k in ["entity_id","name"]} for r in data["entities"] if r["domain"]=="camera"]
    assert selected["items"]==expected
    assert selected["total"]==56 and selected["has_more"] is False and selected["next_offset"] is None
    assert selected["snapshot_sha256"]==full["snapshot_sha256"]
    assert selected["selected_fields"]==["entity_id","name"]
    assert data==before
    assert len(full["items"])==50


def test_projected_pages_remain_disjoint_with_exact_source_cursor():
    data=inventory();first=Adapter._household_page(data,section="entities",domain="camera",fields=["name"],limit=40)
    last=Adapter._household_page(data,section="entities",domain="camera",fields=["name"],limit=40,
        offset=first["next_offset"],snapshot_sha256=first["snapshot_sha256"])
    assert len(first["items"])+len(last["items"])==56
    assert not last["has_more"] and first["next_offset"]==40
    assert not {r["name"] for r in first["items"]}&{r["name"] for r in last["items"]}


def test_missing_fields_are_explicit_and_no_private_fields_can_be_recovered():
    selected=Adapter._household_page(inventory(),section="entities",domain="camera",fields=["name","credentials"],limit=200)
    assert selected["absent_fields"]==["credentials"]
    assert all(set(r)=={"name"} for r in selected["items"])


@pytest.mark.parametrize("fields,limit", [(None,0),([],201),([],True),(["name"]*2,50),(["bad/key"],50),([1],50),({},50)])
def test_bad_projection_rejected_before_source_read(fields,limit):
    with pytest.raises(ConversationConflict):
        HomeAssistantHouseholdTool._validate(arguments(fields=fields,limit=limit))


def test_details_cannot_silently_be_projected():
    args=arguments(fields=["name"],limit=50);args.update(section="automation_configurations",automation_ids=["automation.one"])
    with pytest.raises(ConversationConflict):HomeAssistantHouseholdTool._validate(args)


def test_tool_binds_projection_into_dispatch_and_proof():
    class Reader:
        async def household_page(self,**kw):return Adapter._household_page(inventory(),**kw)
    tool=HomeAssistantHouseholdTool(Reader());args=arguments(domain="camera",fields=["name"],limit=200)
    command=SimpleNamespace(tool_name="read_home_assistant_inventory",arguments=args,expected_proof=args)
    dispatch=asyncio.run(tool.execute(command));proof=asyncio.run(tool.observe(command,dispatch))
    assert dispatch.details["selection"]==args
    assert proof.observed==args and proof.evidence["observation"]["selected_fields"]==["name"]


def test_projection_cannot_recover_sensitive_values_removed_by_household_boundary():
    data=Adapter._project_household([], [{"id":"device","identifiers":[["secret","private"]]}],
        [{"entity_id":"device_tracker.person","device_id":"device"}],
        [{"entity_id":"device_tracker.person","state":"private-address",
          "attributes":{"latitude":12,"longitude":34,"credentials":"secret"}}])
    page=Adapter._household_page(data,section="entities",fields=["entity_id","state","attributes","identifiers","credentials"],limit=200)
    assert page["items"]==[{"entity_id":"device_tracker.person"}]
    assert page["absent_fields"]==["attributes","credentials","identifiers","state"]
    assert "private-address" not in str(page) and "secret" not in str(page)


def test_two_hundred_row_boundary_and_absent_field_scope():
    data=inventory()
    data["entities"]=[{"entity_id":f"light.l{i}","name":str(i),"domain":"light"} for i in range(205)]
    data["entities"][-1]["state"]="on"
    first=Adapter._household_page(data,section="entities",fields=["name","state"],limit=200)
    assert len(first["items"])==200 and first["next_offset"]==200 and first["has_more"]
    assert first["absent_fields"]==[]  # Across filtered source, not just this page.
    last=Adapter._household_page(data,section="entities",fields=["name","state"],limit=200,
        offset=200,snapshot_sha256=first["snapshot_sha256"])
    assert len(last["items"])==5 and not last["has_more"]
    assert last["items"][-1]=={"name":"204","state":"on"}


def test_explicit_default_shape_preserves_legacy_result_exactly():
    data=inventory()
    assert Adapter._household_page(data,section="entities")==Adapter._household_page(
        data,section="entities",fields=[],limit=50)


def searchable_inventory():
    return Adapter._project_household(
        [{"area_id": "room_a", "name": "Studio", "aliases": ["Workshop"]},
         {"area_id": "room_b", "name": "Hall"}],
        [{"id": "device_a", "area_id": "room_a"}],
        [{"entity_id": "light.reading", "device_id": "device_a", "name": "Reading lamp", "aliases": ["Book nook"]},
         {"entity_id": "light.desk", "area_id": "room_a", "name": "Desk lamp"},
         {"entity_id": "light.hall", "area_id": "room_b", "name": "Hall lamp"},
         {"entity_id": "sensor.private", "area_id": "room_a"}],
        [{"entity_id": "light.reading", "state": "on", "attributes": {"secret": "Never searchable"}},
         {"entity_id": "sensor.private", "state": "Confidential state", "attributes": {}}])


def test_query_matches_only_names_identifiers_areas_and_actual_aliases():
    data = searchable_inventory()
    for query, ids in [("READING", ["light.reading"]), ("Book nook", ["light.reading"]),
                       ("light.desk", ["light.desk"]),
                       ("studio", ["light.desk", "light.reading"]),
                       ("workshop", ["light.desk", "light.reading"]),
                       ("Never searchable", []), ("Confidential state", []), ("not present", [])]:
        result = Adapter._household_page(data, section="entities", domain="light", query=query,
                                        fields=["entity_id", "aliases"], limit=200)
        assert result["items"] == [{"entity_id": value} for value in ids]
        assert result["total"] == len(ids) and result["has_more"] is False
        assert result["next_offset"] is None and result["query"] == query
        assert "aliases" in result["absent_fields"]
        assert "_search_aliases" not in result


def test_query_paging_projection_and_legacy_shape_share_one_snapshot():
    data = searchable_inventory(); before = deepcopy(data)
    legacy = Adapter._household_page(data, section="entities")
    for empty in ("", "   "):
        assert Adapter._household_page(data, section="entities", query=empty) == legacy
    first = Adapter._household_page(data, section="entities", domain="light", query="Studio", limit=1, fields=["entity_id"])
    second = Adapter._household_page(data, section="entities", domain="light", query="Studio", limit=1,
                                    fields=["entity_id"], offset=first["next_offset"], snapshot_sha256=first["snapshot_sha256"])
    assert first["items"] == [{"entity_id": "light.desk"}]
    assert second["items"] == [{"entity_id": "light.reading"}]
    assert first["total"] == second["total"] == 2
    assert first["has_more"] is True and second["has_more"] is False
    assert first["snapshot_sha256"] == second["snapshot_sha256"] == legacy["snapshot_sha256"]
    assert data == before
    changed = deepcopy(data); changed["_search_aliases"]["entities"]["light.reading"] = ["New alias"]
    assert Adapter._snapshot_digest(changed) != first["snapshot_sha256"]


@pytest.mark.parametrize("query", [None, 1, [], "x" * 201])
def test_query_invalid_input_rejected_before_read(query):
    with pytest.raises(ConversationConflict):
        HomeAssistantHouseholdTool._validate(arguments(query=query))


@pytest.mark.parametrize("section", ["overview", "areas", "devices", "automations", "automation_details"])
def test_query_rejects_unsupported_sections(section):
    args = arguments(query="Studio"); args["section"] = section
    with pytest.raises(ConversationConflict):
        HomeAssistantHouseholdTool._validate(args)


def test_query_durable_selection_and_snapshot_reuse_fetch_once():
    class Reader(Adapter):
        def __init__(self):
            from collections import OrderedDict
            from threading import Lock
            self._household_snapshots = OrderedDict()
            self._household_snapshot_lock = Lock()
            self.reads = 0
        async def household_inventory(self):
            self.reads += 1
            return searchable_inventory()
    async def run():
        reader = Reader(); tool = HomeAssistantHouseholdTool(reader)
        args = arguments(query="Workshop", domain="light", fields=["entity_id"], limit=1)
        for offset in (0, 1):
            args["offset"] = offset
            call = SimpleNamespace(name="read_home_assistant_inventory", arguments=dict(args))
            prep = await tool.prepare(None, call)
            command = SimpleNamespace(tool_name=prep.tool_name, arguments=prep.arguments, expected_proof=prep.expected_proof)
            dispatch = await tool.execute(command)
            assert (await tool.observe(command, dispatch)).disposition == "verified"
            assert dispatch.details["selection"]["query"] == "Workshop"
            args["snapshot_sha256"] = dispatch.details["observation"]["snapshot_sha256"]
        assert reader.reads == 1
    asyncio.run(run())


@pytest.mark.parametrize("domain", ["light", "switch"])
def test_device_group_membership_preserves_exact_source_order_without_target_expansion(domain):
    members = ["light.reading", "switch.desk"]
    data = Adapter._project_household([], [], [], [{"entity_id": domain + ".group", "state": "off",
        "attributes": {"entity_id": members, "secret": "not public"}}])
    result = Adapter._household_page(data, section="entities", domain=domain,
                                    fields=["entity_id", "attributes"])
    assert result["items"] == [{"entity_id": domain + ".group", "attributes": {"entity_id": members}}]
    assert result["total"] == 1  # No invented child rows or automatic targeting.
    changed = deepcopy(data); changed["entities"][0]["attributes"]["entity_id"] = list(reversed(members))
    assert Adapter._snapshot_digest(changed) != result["snapshot_sha256"]
    assert Adapter._household_page(data, section="entities", fields=["name"])["items"] == [{"name": domain + ".group"}]


@pytest.mark.parametrize("members", [None, "light.reading", {"entity_id": "light.reading"},
    ["person.someone"], ["light.reading", "device_tracker.someone"], ["light.good", 1],
    ["light.bad name"], ["sensor.private"], ["light." + "a" * 250], ["light.reading"] * 51])
def test_device_group_membership_rejects_entire_unsafe_or_unbounded_source_list(members):
    data = Adapter._project_household([], [], [], [{"entity_id": "light.group", "state": "off",
        "attributes": {"entity_id": members}}])
    assert data["entities"][0]["attributes"] == {}


@pytest.mark.parametrize("domain", ["person", "device_tracker", "sensor", "climate"])
def test_device_group_membership_never_projects_from_private_or_other_parent_domains(domain):
    data = Adapter._project_household([], [], [], [{"entity_id": domain + ".group", "state": "private",
        "attributes": {"entity_id": ["light.reading"], "device_class": "temperature"}}])
    assert "entity_id" not in data["entities"][0].get("attributes", {})


def test_device_group_membership_accepts_complete_bounded_and_empty_lists():
    for members in ([], [f"light.member_{i}" for i in range(50)]):
        data = Adapter._project_household([], [], [], [{"entity_id": "light.group", "state": "off",
            "attributes": {"entity_id": members}}])
        assert data["entities"][0]["attributes"]["entity_id"] == members
