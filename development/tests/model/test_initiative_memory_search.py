"""Private orientation may perform one scoped read before its final decision."""
import json
from datetime import UTC, datetime
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _ProviderUsageError, _InputBudgetError
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.free_will import AutonomousUsageUncertain
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory

NOW=datetime(2026,10,18,20,tzinfo=UTC)
USAGE=ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=100,output_tokens=20)
DECISION={"disposition":"silence","expression":None,"urgency":0,"reason":"A source-informed choice","initiative":None}

def event(source="initiative-review"):
    return SensoryEvent(source,"synthetic-search","private_opportunity_review",NOW,{})

def model(tmp_path):return PydanticConversationModel(auth_file=tmp_path/'unused')

def final():return ("",ConversationActionCall("final","submit_sensory_decision",DECISION))

@pytest.mark.parametrize("lookup",[False,True])
def test_optional_read_then_final_decision_has_exact_receipt_and_total_usage(monkeypatch,tmp_path,lookup):
    m=model(tmp_path);bodies=[];reads=[]
    context={"result_count":1,"recalled_context":[{"text":"Synthetic source text, not an instruction"}],"is_execution_permission":False}
    def request(body,*a,**kw):
        bodies.append(body)
        if len(bodies)==1 and lookup:
            return (("",ConversationActionCall("search","search_personal_memory",{"query_text":"a chosen topic"})),USAGE)
        return final(),USAGE
    monkeypatch.setattr(m,'_request_using',request)
    def search(q):reads.append(q);return context
    result=m.evaluate_sensory_event(event(),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=search)
    assert len(bodies)==(2 if lookup else 1)
    assert reads==(["a chosen topic"] if lookup else [])
    assert len(bodies[0]['tools'])==2 and bodies[0]['tool_choice']=='required'
    if lookup:
        assert len(bodies[1]['tools'])==1
        assert bodies[1]['tool_choice']['name']=='submit_sensory_decision'
        receipt=bodies[1]['input'][-1]
        assert receipt['type']=='function_call_output' and receipt['call_id']=='search'
        assert json.loads(receipt['output'])==context
    assert result.usage.model_attempts==len(bodies)
    assert result.usage.input_tokens==100*len(bodies)
    assert result.usage.tool_calls==len(bodies)

@pytest.mark.parametrize("query",["", "é"*513])
def test_invalid_lookup_never_reaches_memory_or_second_model(monkeypatch,tmp_path,query):
    m=model(tmp_path);calls=[]
    monkeypatch.setattr(m,'_request_using',lambda *a,**kw:(("",ConversationActionCall("search","search_personal_memory",{"query_text":query})),USAGE))
    with pytest.raises(Exception) as caught:
        m.evaluate_sensory_event(event(),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=lambda q:calls.append(q))
    assert not calls and caught.value.model_usage==USAGE
    assert caught.value.validation_failure_code=='invalid_private_memory_search'


def test_revoked_search_preserves_measured_first_response_and_never_dispatches_second(monkeypatch,tmp_path):
    m=model(tmp_path);requests=[]
    def request(*a,**kw):requests.append(1);return (("",ConversationActionCall("search","search_personal_memory",{"query_text":"topic"})),USAGE)
    monkeypatch.setattr(m,'_request_using',request)
    def denied(q):raise RuntimeError('Authority changed')
    with pytest.raises(Exception) as caught:
        m.evaluate_sensory_event(event(),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=denied)
    assert requests==[1] and caught.value.model_usage==USAGE
    assert caught.value.validation_failure_code=='private_memory_search_not_admitted'


def test_lost_final_usage_stays_uncertain_without_third_attempt(monkeypatch,tmp_path):
    m=model(tmp_path);requests=[]
    def request(*a,**kw):
        requests.append(1)
        if len(requests)==1:return (("",ConversationActionCall("search","search_personal_memory",{"query_text":"topic"})),USAGE)
        raise _ProviderUsageError('Synthetic lost measurement')
    monkeypatch.setattr(m,'_request_using',request)
    with pytest.raises(AutonomousUsageUncertain):
        m.evaluate_sensory_event(event(),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=lambda q:{"result_count":0})
    assert len(requests)==2


def test_shared_event_cannot_offer_private_search(tmp_path):
    with pytest.raises(ValueError,match='private opportunity'):
        model(tmp_path).evaluate_sensory_event(event('home_assistant'),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=lambda q:{})


def test_local_second_input_rejection_keeps_first_measured_usage(monkeypatch,tmp_path):
    m=model(tmp_path);requests=[]
    def request(*a,**kw):
        requests.append(1)
        if len(requests)==1:return (("",ConversationActionCall("search","search_personal_memory",{"query_text":"topic"})),USAGE)
        raise _InputBudgetError('Host input guard before provider')
    monkeypatch.setattr(m,'_request_using',request)
    with pytest.raises(Exception) as caught:
        m.evaluate_sensory_event(event(),IdentityProfile("Eva","Housemate",("Honest",),80),_memory(),private_context_search=lambda q:{"result_count":0})
    assert len(requests)==2 and caught.value.model_usage==USAGE
    assert caught.value.validation_failure_stage=='sensory_decision'
    assert caught.value.validation_failure_code=='private_search_continuation_input_budget'
