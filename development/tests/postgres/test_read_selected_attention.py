"""Native read receipts select a real retained source outside the newest events."""
from datetime import timedelta
import pytest
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.initiative_source_outcomes import read_recent_source_outcomes
from eva.application.free_will import FreeWillDecision
from eva.application.conversation import ConversationModelUsage
from eva.application.resident_attention import NextAttention
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_native_initiative_digital_work import native_digital,admitted,started
from tests.postgres.test_initiative_source_outcomes import _read,_finish
from tests.postgres.test_source_attention import signal


@pytest.mark.parametrize("state",["covered","absent","revoked","write"])
def test_exact_native_read_source_is_offered_then_watch_survives(state,native_digital):
    f=native_digital;_,work=admitted(f);call=started(f,work)
    observation={"success":True,"http_status":200,"truncated":False,"response_json":'{"entity_id":"sensor.target","state":"1"}'}
    timeline,thread=_read(f,work,payload=observation,path="/api/states/sensor.target",method="POST" if state=="write" else "GET")
    _finish(f,work,call);outcomes=read_recent_source_outcomes(f.auth,now=NOW)
    if state=="write": assert outcomes[0]["verified_reads"]==[]
    else: assert outcomes[0]["verified_reads"][0]["attention_selectors"][0]["selector_value"]=="sensor.target"
    receipt_hashes=[(e.sequence,e.payload_sha256) for e in timeline.events(thread)]
    target=signal("read-selected-target",NOW-timedelta(minutes=20),"sensor.target")
    if state!="absent": f.sensory.retain(target)
    for i in range(12):f.sensory.retain(signal("noisy-source-"+str(i),NOW-timedelta(seconds=10),"sensor.noise_"+str(i)))
    stream=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("light",) if state=="revoked" else ("sensor",))
    store=PostgresSensoryEventStore(f.control._engine,tenant_id=f.claim.tenant_id,installation_id=f.claim.installation_id,
        initiative_principal_id=f.claim.principal_id,initiative_authorizer=f.auth.policy,context_reviews_only=True,
        attention_source_resolver=stream.attention_source)
    assert store.schedule_context_review(now=NOW,source_outcomes=outcomes)=="scheduled"
    claim=store.claim_next(now=NOW);values=claim.event.payload["life_context"]["observations"]
    selected=[v for v in values if v["source_event_id"]==target.source_event_id]
    if state!="covered":
        assert selected==[]
        assert [(e.sequence,e.payload_sha256) for e in timeline.events(thread)]==receipt_hashes
        return
    assert selected and values[0]==selected[0]
    assert selected[0]["source_attention"]["available"] is True
    assert selected[0]["attention_selection"]["receipt_sha256"]==outcomes[0]["verified_reads"][0]["source"]["receipt_sha256"]
    assert selected[0]["historical"] and selected[0]["is_current_proof"] is False
    claim=store.mark_model_started(claim,now=NOW)
    store.complete(claim,FreeWillDecision("silence","Synthetic fixture selected a read source",0,"synthetic-no-model",
        usage=ConversationModelUsage.measured(tool_calls=0,input_tokens=8,output_tokens=4),
        next_attention=NextAttention(NOW+timedelta(hours=1),"Review only this source",target.source_event_id)),now=NOW)
    changed=signal("read-selected-change",NOW+timedelta(minutes=2),"sensor.target",new="2")
    f.sensory.retain(changed);f.sensory.retain(changed)
    at=NOW+timedelta(minutes=5)
    assert store.schedule_context_review(now=at,source_outcomes=outcomes)=="scheduled"
    following=store.claim_next(now=at)
    assert following.event.payload["attention_source_change"]["source_event_id"]==changed.source_event_id
    assert following.event.payload["life_context"]["own_orientation_history"][0]["next_attention"]["source_watch"]["source_event_id"]==target.source_event_id
    assert [(e.sequence,e.payload_sha256) for e in timeline.events(thread)]==receipt_hashes
    assert store.schedule_context_review(now=at,source_outcomes=outcomes)=="idle"
