"""Silent success avoids a second model without weakening failed/legacy turns."""
from dataclasses import replace
import pytest
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, LivingConversation, silent_completion_scope
from tests.application.test_contact_model import Model, Actions, IDENTITY, turn
from tests.application.test_conversation import _Memory

class QuietActions(Actions):
    action_names = ("inspect_item",)
    def __init__(self, status="verified"):
        super().__init__();self.status=status;self.effects=0;self.guards=0
    def execute(self, t, call):
        self.effects+=1
        return ConversationActionReceipt(call, {"status":self.status})
    def silent_completion_allowed(self, t, receipts):
        self.guards+=1
        return receipts[0].call.arguments.get("finish_without_reply") is True
    def contact_reply_allowed(self, t, receipt):return True


def runtime(actions):
    model=Model(lambda t,k: ConversationActionCall("call","inspect_item",{"finish_without_reply":True})
                if k["completed_action"] is None else "Dit lukte niet.")
    work=Model(lambda *a:pytest.fail("unexpected work model"))
    return LivingConversation(_Memory(),work,IDENTITY,actions,contact_model=model),model


def test_verified_action_has_one_model_and_no_spoken_reply_on_supported_adapter():
    actions=QuietActions();core,model=runtime(actions)
    with silent_completion_scope():reply=core.respond(turn())
    assert reply.response_mode=="silent" and reply.text and actions.effects==1
    assert len(model.calls)==1 and reply.model_usage.model_attempts==1


@pytest.mark.parametrize("status",["failed","rejected","timed_out","needs_help"])
def test_failed_action_never_becomes_silent_even_if_provider_claims_eligibility(status):
    actions=QuietActions(status);core,model=runtime(actions)
    with silent_completion_scope():reply=core.respond(turn())
    assert reply.response_mode=="reply" and len(model.calls)==2 and actions.guards==0


def test_silent_support_is_reset_before_a_legacy_request():
    actions=QuietActions();core,model=runtime(actions)
    with silent_completion_scope():assert core.respond(turn()).response_mode=="silent"
    assert core.respond(turn("legacy")).response_mode=="reply"
    assert len(model.calls)==3 and actions.effects==2


def test_extra_question_choice_keeps_the_following_model_answer():
    actions=QuietActions();core,model=runtime(actions)
    original=model.choose
    def choose(t,k):
        result=original(t,k)
        return replace(result, arguments={"finish_without_reply":False}) if isinstance(result,ConversationActionCall) else "Het licht is uit; met mij gaat het goed."
    model.choose=choose
    with silent_completion_scope():reply=core.respond(turn(text="Doe dat en hoe gaat het?"))
    assert reply.response_mode=="reply" and "gaat het goed" in reply.text and len(model.calls)==2


def test_stop_after_proof_prevents_silent_completion():
    from eva.application.conversation import ConversationConflict
    actions=QuietActions();core,model=runtime(actions)
    def checkpoint(t,usage):
        if actions.effects:
            raise ConversationConflict("stopped after effect")
    with silent_completion_scope(),pytest.raises(ConversationConflict,match="stopped after effect"):
        core.respond(turn(),before_step=checkpoint)
    assert actions.effects==1 and len(model.calls)==1
