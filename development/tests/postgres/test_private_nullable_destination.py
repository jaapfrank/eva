"""The shared schema's empty speaker field must not break private text work."""
from types import SimpleNamespace
import pytest
from eva.adapters.postgres import delegated_work_actions as module
from eva.application.conversation import ConversationActionCall, ConversationTurn, ConversationConflict, ConversationActionInputRejected


@pytest.mark.parametrize("action,payload", [
    ("start_digital_work", {"task":"Investigate the available records."}),
    ("start_background_research", {"research":{"contract":"PublicResearchRequest/v1","schema_version":1,
        "question":"What may cause a software restart?","title":"Software restarts","options":["Software restart"],
        "research_mode":"topic","public_review":"public_unlinked"}}),
])
def test_empty_shared_destination_admits_once_without_speaker_authority(monkeypatch,action,payload):
    events={};calls=[]
    class Timeline:
        def __init__(self,*args):pass
        def events(self,key):return tuple(events.get(key,()))
        def create_thread(self,*,thread_id,data,event_type,**kwargs):
            events[thread_id]=[SimpleNamespace(event_type=event_type,payload={"data":data})]
        def transition(self,*,thread_id,data,event_type,**kwargs):
            events[thread_id].append(SimpleNamespace(event_type=event_type,payload={"data":data}))
    class Service:
        digital_profile=object();research_profile=object()
        def available_for(self,turn):return turn.principal_id=="owner"
        def validate_authority(self,turn):assert self.available_for(turn)
        def execute(self,operation,turn,**kwargs):
            calls.append((operation,kwargs));return {"created":True,"work":{"work_id":"work:one","state":"admitted","stop_requested":False}}
    monkeypatch.setattr(module,"PostgresOperationalTimeline",Timeline)
    actions=module.PostgresDelegatedWorkActions(object(),Service())
    turn=ConversationTurn.private(tenant_id="tenant:test",installation_id="install:test",principal_id="owner",
        conversation_id="private",message_id="one",text="Can this be investigated?",occurred_at="2026-10-19T12:00:00Z")
    call=ConversationActionCall("call-one",action,{**payload,"acceptance_message":"I will investigate.","destination_satellite_id":None})
    first=actions.execute(turn,call);again=actions.execute(turn,call)
    assert first==again and first.output["acceptance_message"]=="I will investigate."
    assert len(calls)==1 and "destination_satellite_id" not in calls[0][1]
    assert actions.pending(turn)==first
    with pytest.raises((ConversationConflict, ConversationActionInputRejected)):
        actions.execute(turn,ConversationActionCall("call-two",action,{**payload,"destination_satellite_id":"assist_satellite.other"}))
    with pytest.raises((ConversationConflict, ConversationActionInputRejected)):
        actions.execute(turn,ConversationActionCall("call-three",action,{**payload,"unknown_argument":None}))
    assert len(calls)==1
