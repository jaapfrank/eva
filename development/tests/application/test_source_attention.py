"""Provider coverage and model source choice; no network calls."""
from datetime import UTC,datetime,timedelta
from dataclasses import replace
import json
import pytest
from eva.application.sensory import SensoryEvent,SensoryAttentionSource
from eva.application.resident_attention import NextAttention
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.model.pydantic_conversation import PydanticConversationModel,_FREE_WILL_TOOL,_private_opportunity_tool
from eva.application.conversation import ConversationActionCall,ConversationModelUsage,IdentityProfile
from tests.model.test_conversation_model import _memory

NOW=datetime(2026,10,20,12,tzinfo=UTC)


def test_source_choice_roundtrips_without_changing_old_time_only_choices():
    old=NextAttention(NOW+timedelta(hours=1),"Later")
    assert old.to_payload()=={"at":"2026-10-20T13:00:00Z","reason":"Later"}
    selected=replace(old,source_event_id="synthetic:actual-offered-source")
    assert NextAttention.from_payload(selected.to_payload())==selected
    for bad in ["",42,"a"*241]:
        with pytest.raises(ValueError):replace(old,source_event_id=bad)


def test_event_coverage_is_provider_owned_limited_and_changes_with_current_config():
    def stream(token="synthetic",domains=("sensor",)):
        return HomeAssistantEventStream(base_url="http://synthetic.invalid",token=token,admitted_domains=domains)
    event=SensoryEvent("home_assistant","synthetic:event","state_changed",NOW,{"entity_id":"sensor.alpha"})
    first=stream().attention_source(event)
    assert first.selector_key=="entity_id" and first.selector_value=="sensor.alpha"
    assert "state_value_changes_only" in first.coverage
    assert first.configuration_sha256!=stream("rotated").attention_source(event).configuration_sha256
    for invalid in [replace(event,source="other"),replace(event,event_type="arbitrary_event"),
                    replace(event,payload={"entity_id":"camera.alpha"})]:
        with pytest.raises(ValueError):stream().attention_source(invalid)


def test_source_choice_is_only_offered_in_existing_private_decision_tool(monkeypatch,tmp_path):
    assert "source_event_id" not in _FREE_WILL_TOOL["parameters"]["properties"]["next_attention"]["properties"]
    assert "source_event_id" in _private_opportunity_tool()["parameters"]["properties"]["next_attention"]["properties"]
    model=PydanticConversationModel(auth_file=tmp_path/"unused");bodies=[]
    def request(body,*a,**kw):
        bodies.append(body)
        return (("",ConversationActionCall("decision","submit_sensory_decision",{
            "disposition":"silence","expression":None,"reason":"A source-linked question", "urgency":0,"initiative":None,
            "next_attention":{"at":"2026-10-20T13:00:00Z","reason":"The question needs this source", "source_event_id":"synthetic:offered"}})),
            ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=120,output_tokens=35))
    monkeypatch.setattr(model,"_request_using",request)
    result=model.evaluate_sensory_event(SensoryEvent("initiative-review","synthetic:review","private_opportunity_review",NOW,{}),
        IdentityProfile("Eva","Housemate",("Honest",),80),_memory())
    assert result.next_attention.source_event_id=="synthetic:offered" and len(bodies)==1
    assert "geen verplichte huiscasus" in bodies[0]["instructions"]
