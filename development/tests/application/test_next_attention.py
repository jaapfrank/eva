"""Model choice and strict next-attention contract; no provider calls."""
from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
import pytest
from eva.application.resident_attention import NextAttention
from eva.application.free_will import FreeWillDecision
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from tests.model.test_conversation_model import _memory

NOW = datetime(2026,10,15,12,tzinfo=UTC)

@pytest.mark.parametrize("value", [None, "tomorrow", "2026-10-15T12:05:00+02:00", "2026-1-5T12:05:00Z"])
def test_attention_time_requires_explicit_canonical_utc(value):
    with pytest.raises((ValueError,TypeError)):
        NextAttention.from_payload({"at":value,"reason":"Review a pending intention"})

@pytest.mark.parametrize("value", ["", "x"*401, None])
def test_attention_reason_is_bounded(value):
    with pytest.raises(ValueError):NextAttention(NOW,value)


def test_attention_is_distinct_from_an_action_and_survives_decision_replace():
    choice=NextAttention(NOW+timedelta(minutes=5),"Check the pending preparation")
    assert NextAttention.from_payload(choice.to_payload())==choice
    decision=FreeWillDecision("silence","No message now",0,"synthetic",next_attention=choice)
    assert replace(decision,reason="Changed reasoning").next_attention==choice
    with pytest.raises(ValueError):FreeWillDecision("silence","No message",0,"synthetic",next_attention={})


def test_existing_sensory_tool_returns_model_chosen_attention(monkeypatch,tmp_path):
    model=PydanticConversationModel(auth_file=tmp_path/"unused.json");bodies=[]
    def request(body,*a,**kw):
        bodies.append(body)
        return (("",ConversationActionCall("choice","submit_sensory_decision",{
            "disposition":"silence","reason":"Preparation may become relevant later", "expression":None,
            "urgency":0,"initiative":None,"next_attention":{"at":"2026-10-15T12:05:00Z",
                "reason":"Reconsider the pending preparation"}})),ConversationModelUsage.measured(
                    model_attempts=1,tool_calls=1,input_tokens=120,output_tokens=30))
    monkeypatch.setattr(model,"_request_using",request)
    choice=model.evaluate_sensory_event(SensoryEvent("initiative-review","fixture","private_opportunity_review",NOW,{}),
        IdentityProfile("Eva","Housemate",("Honest",),80),_memory())
    assert choice.next_attention.at==NOW+timedelta(minutes=5)
    schema=bodies[0]["tools"][0]["parameters"]
    assert "next_attention" in schema["required"]
    assert schema["properties"]["next_attention"]["additionalProperties"] is False
    assert "reviewed_at" in json.loads(bodies[0]["input"][0]["content"])


def test_attention_replacement_during_recall_cancels_before_provider():
    from types import SimpleNamespace
    from eva.application.free_will import SensoryDecisionClaim,SensoryDecisionWorker
    event=SensoryEvent("initiative-review","late-cancel","private_opportunity_review",NOW,
        {"attention_origin":{"synthetic":"original"}})
    claim=SensoryDecisionClaim(event,"sensory:late-cancel","late-cancel",1,0)
    recalled=[False];checks=[];requests=[]
    class Store:
        def claim_next(self,**kw):return claim
        def cancel_superseded_attention(self,actual,**kw):
            checks.append(recalled[0]);return recalled[0]
        def mark_model_started(self,actual,**kw):return actual
        def context_review_claim_is_current(self,*a,**kw):return True
        def fail(self,claim,error,**kw):raise error
    def recall(q):recalled[0]=True;return _memory()
    model=SimpleNamespace(evaluate_sensory_event=lambda *a,**kw:requests.append(kw))
    worker=SensoryDecisionWorker(Store(),model,SimpleNamespace(recall_for_presence=recall),
        IdentityProfile("Eva","Housemate",("Honest",),80),tenant_id="tenant",installation_id="home",
        principal_id="owner",context_review_authorizer=lambda:None,
        context_review_admission=lambda *a,**kw:SimpleNamespace(admitted=True))
    assert worker.run_once(now=NOW)=="cancelled"
    assert checks==[False,True] and requests==[]
