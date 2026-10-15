"""Opportunity decisions must not be dominated by completed source reviews."""
from datetime import UTC,datetime
from types import SimpleNamespace
import pytest
from eva.application.free_will import SensoryDecisionClaim,SensoryDecisionWorker,FreeWillDecision,_private_review_recall_context
from eva.application.sensory import SensoryEvent
from eva.application.conversation import IdentityProfile
from eva.contracts.canonical_json import canonical_sha256
NOW=datetime(2026,10,15,12,tzinfo=UTC)
BINDING={"work_id":"work:eva-initiative:"+"a"*64,"spec_sha256":"b"*64,"outcome_sha256":"c"*64}

@pytest.mark.parametrize("kind",["ordinary","completion","attention","attention_without_source","attention_missing"])
def test_only_exact_source_occasion_gets_original_feedback(kind):
    payload={}
    if kind=="completion":payload["native_completion_source"]=BINDING
    if kind.startswith("attention"):payload["attention_origin"]={"reason":"A self-chosen pending question"}
    event=SensoryEvent("initiative-review","ordinary","private_opportunity_review",NOW,payload)
    claim=SensoryDecisionClaim(event,"sensory:ordinary","ordinary",1,0)
    reads=[];models=[];queries=[];complete=[]
    class Store:
        def claim_next(self,**kw):return claim
        def mark_model_started(self,actual,**kw):return actual
        def context_review_claim_is_current(self,*a,**kw):return True
        def attention_source_binding(self,actual):return BINDING if kind in {"attention","attention_missing"} else None
        def recent_initiatives(self):return ({"phase":"initiative_blocked","title":"Prior failed work"},)
        def complete(self,actual,decision,**kw):complete.append(decision)
        def fail(self,claim,error,**kw):raise error
    def recall(query):queries.append(query);return object()
    def sources(**kw):
        reads.append(kw)
        if kind=="attention_missing":return ()
        return (dict(BINDING,verified_reads=[{"source":"original"}]),dict(BINDING,work_id="another-work"))
    def decide(event,*a,**kw):
        models.append(kw);return FreeWillDecision("silence","Nothing useful now",0,"synthetic")
    w=SensoryDecisionWorker(Store(),SimpleNamespace(evaluate_sensory_event=decide),
        SimpleNamespace(recall_for_presence=recall),IdentityProfile("Eva","Housemate",("Honest",),80),
        tenant_id="tenant",installation_id="home",principal_id="owner",
        context_review_authorizer=lambda:None,
        context_review_admission=lambda *a,**kw:SimpleNamespace(admitted=True),source_outcome_reader=sources)
    assert w.run_once(now=NOW)=="silence"
    assert len(models)==len(queries)==len(complete)==1
    assert models[0]["initiative_history"][0]["phase"]=="initiative_blocked"
    assert queries[0].principal_id=="owner" and queries[0].maximum_facts==8
    if kind in {"completion","attention","attention_missing"}:
        assert reads==[{"now":NOW,"work_id":BINDING["work_id"]}]
        assert len(models[0]["initiative_source_outcomes"])==1
        if kind=="attention_missing":assert models[0]["initiative_source_outcomes"][0]["reason"]=="original_native_outcome_no_longer_available"
        assert complete[0].source_context_sha256==canonical_sha256(list(models[0]["initiative_source_outcomes"]))
    else:
        assert reads==[] and "initiative_source_outcomes" not in models[0]
        assert complete[0].source_context_sha256 is None


def test_attention_reason_guides_search_and_closed_work_does_not():
    payload={"attention_origin":{"reason":"Prepare a pending plan"},"life_context":{
        "own_work":[{"title":"Closed synthetic old work","status":"completed"}],
        "observations":[{"entity_id":"binary_sensor.routine_camera","state":"on"}]}}
    event=SensoryEvent("initiative-review","attention","private_opportunity_review",NOW,payload)
    assert _private_review_recall_context(event).endswith("Prepare a pending plan")
    del payload["attention_origin"]
    assert _private_review_recall_context(event)==""
    payload["life_context"]["own_work"].append({"title":"A live intention","status":"active","phase":"active"})
    hint=_private_review_recall_context(event)
    assert "A live intention" in hint and "Closed" not in hint and "routine_camera" not in hint
    assert len(payload["life_context"]["own_work"])==2
