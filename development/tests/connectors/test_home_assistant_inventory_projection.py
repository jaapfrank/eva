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
