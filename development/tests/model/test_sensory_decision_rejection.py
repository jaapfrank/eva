"""A paid but invalid sensory response keeps its measured usage and stays rejected."""
from datetime import UTC, datetime
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.model.contracts import ConversationModelError
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory

BASE = {"disposition":"silence", "reason":"Considered", "expression":None, "urgency":0,
        "initiative":None, "next_attention":None}
EVENT = SensoryEvent("initiative-review", "synthetic:decision-rejection", "private_opportunity_review", datetime(2026,10,18,tzinfo=UTC), {})
IDENTITY = IdentityProfile("Eva", "Housemate", ("Honest",), 80)

@pytest.mark.parametrize("change,code", [
    ({"reason":"r"*601}, "invalid_reason"),
    ({"disposition":{}}, "invalid_values"),
    ({"disposition":"respond", "expression":"x"*601, "urgency":1}, "invalid_expression"),
    ({"disposition":"respond", "expression":"Hello", "urgency":0}, "invalid_response_urgency"),
    ({"expression":"Unexpected"}, "invalid_silence_expression"),
    ({"next_attention":{"at":"2026-10-19T12:00:00+00:00", "reason":"Later"}}, "invalid_attention_time"),
    ({"initiative":{"title":"x"*161, "motivation":"Useful", "next_exploration":"Check"}}, "invalid_initiative"),
])
def test_invalid_completed_response_preserves_usage_without_accepting_it(tmp_path, monkeypatch, change, code):
    model=PydanticConversationModel(auth_file=tmp_path/"unused")
    usage=ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=311,output_tokens=71)
    calls=[]
    def request(body,*args,**kwargs):
        calls.append(body)
        return (("",ConversationActionCall("choice","submit_sensory_decision",BASE|change)),usage)
    monkeypatch.setattr(model,"_request_using",request)
    with pytest.raises(ConversationModelError) as failure:
        model.evaluate_sensory_event(EVENT,IDENTITY,_memory())
    error=failure.value
    assert error.model_usage is usage
    assert error.validation_failure_stage=="sensory_decision"
    assert error.validation_failure_code==code
    assert len(calls)==1
    assert change.get("reason", "not-private") not in str(error)


def test_offered_bounds_match_accepted_decision_and_valid_response_is_unchanged(tmp_path,monkeypatch):
    model=PydanticConversationModel(auth_file=tmp_path/"unused")
    usage=ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=90,output_tokens=30)
    def request(body,*args,**kwargs):
        properties=body['tools'][0]['parameters']['properties']
        assert properties['reason']['maxLength']==600
        assert properties['expression']['maxLength']==600
        return (("",ConversationActionCall("choice","submit_sensory_decision",BASE|{"reason":"r"*600})),usage)
    monkeypatch.setattr(model,"_request_using",request)
    decision=model.evaluate_sensory_event(EVENT,IDENTITY,_memory())
    assert decision.reason=="r"*600 and decision.usage is usage and decision.initiative is None


def test_unfinished_provider_error_never_gains_known_usage_or_rejection_marker(tmp_path,monkeypatch):
    model=PydanticConversationModel(auth_file=tmp_path/"unused")
    error=ConversationModelError("conversation provider is unavailable")
    def request(*args,**kwargs):raise error
    monkeypatch.setattr(model,"_request_using",request)
    with pytest.raises(ConversationModelError) as failure:model.evaluate_sensory_event(EVENT,IDENTITY,_memory())
    assert failure.value is error and error.model_usage is None
    assert not hasattr(error,"validation_failure_stage")


@pytest.mark.parametrize("arguments", [[], [{"bad":"shape"}]])
def test_malformed_argument_container_retains_completed_usage(tmp_path,monkeypatch,arguments):
    model=PydanticConversationModel(auth_file=tmp_path/"unused")
    usage=ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=90,output_tokens=30)
    monkeypatch.setattr(model,"_request_using",lambda *args,**kwargs: (("",ConversationActionCall("choice","submit_sensory_decision",arguments)),usage))
    with pytest.raises(ConversationModelError) as failure:model.evaluate_sensory_event(EVENT,IDENTITY,_memory())
    assert failure.value.model_usage is usage
    assert failure.value.validation_failure_stage=="sensory_decision"
    assert failure.value.validation_failure_code=="invalid_fields"
