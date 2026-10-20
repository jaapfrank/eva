"""Provider-owned read targets never infer a source from user prose or writes."""
import pytest
from eva.providers.receipt_policies import retained_source_attention_selectors as select

ARGS={"transport":"rest","method":"GET","path":"/api/states/sensor.example"}
OBS={"success":True,"http_status":200,"truncated":False,"response_json":"1.5"}


def test_exact_verified_rest_read_target_offers_only_that_entity():
    assert select("home_assistant_request",ARGS,OBS)==({"source":"home_assistant","event_type":"state_changed","selector_key":"entity_id","selector_value":"sensor.example"},)


@pytest.mark.parametrize("patch", [{"method":"POST"},{"transport":"websocket"},{"path":"/api/services/light/turn_on"},{"path":"/api/config"},{"path":"/api/states/sensor.example?query=other"}])
def test_non_state_or_non_rest_get_never_offers_a_target(patch):
    assert select("home_assistant_request",{**ARGS,**patch},OBS)==()


@pytest.mark.parametrize("patch", [{"success":False},{"http_status":401},{"selection_error":"no such pointer"}])
def test_failed_read_never_offers_a_target(patch):
    assert select("home_assistant_request",ARGS,{**OBS,**patch})==()


def test_selected_inventory_page_bounded_and_no_query_text_inference():
    obs={"section":"entities","items":[{"entity_id":"sensor."+str(i)} for i in range(8)]+[{"name":"sensor.fake"}]}
    rows=select("read_home_assistant_inventory",{"section":"entities","query":"sensor.fake"},obs)
    assert len(rows)==4 and [r["selector_value"] for r in rows]==["sensor."+str(i) for i in range(4)]
    assert select("read_home_assistant_inventory",{"section":"areas"},obs)==()


def test_bulk_states_only_actual_untruncated_page_and_unknown_optional_provider_empty():
    rows=select("home_assistant_request",{**ARGS,"path":"/api/states"},{**OBS,"response_json":'[{"entity_id":"light.a"},{"entity_id":"sensor.b"}]'})
    assert [r["selector_value"] for r in rows]==["light.a","sensor.b"]
    assert select("home_assistant_request",{**ARGS,"path":"/api/states"},{**OBS,"truncated":True})==()
    assert select("unknown",ARGS,OBS)==()
