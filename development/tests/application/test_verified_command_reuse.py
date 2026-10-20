"""A module-owned interpretation may skip the model, never pending work or STOP."""
import pytest
from eva.application.conversation import (ConversationActionCall, ConversationActionReceipt,
    ConversationConflict, ConversationModelUsage, LivingConversation, silent_completion_scope)
from tests.application.test_contact_model import Model, IDENTITY, turn
from tests.application.test_silent_conversation_completion import QuietActions
from tests.application.test_conversation import _Memory

class Reusing(QuietActions):
    def __init__(self, status="verified"):
        super().__init__(status); self.reuses=0
    def reuse_verified_action(self,t):
        self.reuses+=1;self.effects+=1
        return ConversationActionReceipt(ConversationActionCall("fresh", "inspect_item",
            {"finish_without_reply":True}), {"status":self.status})


def core(actions, memory=None):
    model=Model(lambda t,k:"Dat lukte niet." if k["completed_action"] else "Hoi!")
    return LivingConversation(memory or _Memory(),Model(lambda *a:pytest.fail("work model")),
        IDENTITY,actions,contact_model=model),model


def test_verified_reuse_skips_memory_context_and_all_model_calls():
    actions=Reusing()
    actions.active_context=lambda t:pytest.fail("no conversation context needed")
    class NoMemory(_Memory):
        def initial_context(self,*a,**k):pytest.fail("memory retrieval")
    runtime,model=core(actions,NoMemory())
    with silent_completion_scope():reply=runtime.respond(turn())
    assert reply.response_mode=="silent" and reply.provider=="eva:verified-reuse"
    assert not model.calls and actions.reuses==actions.effects==1
    assert reply.model_usage==ConversationModelUsage.measured(model_attempts=0,tool_calls=0,input_tokens=0,output_tokens=0)


def test_cache_miss_and_legacy_adapter_keep_normal_conversation():
    actions=Reusing();actions.reuse_verified_action=lambda t:None
    runtime,model=core(actions)
    with silent_completion_scope():assert runtime.respond(turn()).text=="Hoi!"
    actions.reuse_verified_action=lambda t:pytest.fail("legacy adapter cannot reuse")
    assert runtime.respond(turn("legacy")).text=="Hoi!" and len(model.calls)==2


@pytest.mark.parametrize("status",["failed","timed_out","needs_help"])
def test_failed_reused_execution_gets_a_reply_without_repeating_effect(status):
    actions=Reusing(status);runtime,model=core(actions)
    with silent_completion_scope():reply=runtime.respond(turn())
    assert reply.response_mode=="reply" and reply.text=="Dat lukte niet."
    assert actions.effects==1 and len(model.calls)==1


def test_pending_receipt_precedes_cache():
    actions=Reusing();actions.pending=lambda t:ConversationActionReceipt(
        ConversationActionCall("old","inspect_item",{}),{"status":"verified"})
    actions.reuse_verified_action=lambda t:pytest.fail("pending must win")
    runtime,model=core(actions)
    with silent_completion_scope():runtime.respond(turn())
    assert len(model.calls)==1 and actions.effects==0


def test_stop_after_cached_dispatch_is_preserved():
    actions=Reusing();runtime,model=core(actions)
    def checkpoint(t,usage):
        if actions.effects:raise ConversationConflict("stopped")
    with silent_completion_scope(),pytest.raises(ConversationConflict,match="stopped"):
        runtime.respond(turn(),before_step=checkpoint)
    assert actions.effects==1 and not model.calls


def test_known_zero_usage_roundtrips_without_turning_unknown_into_zero():
    zero=ConversationModelUsage.measured(model_attempts=0,tool_calls=0,input_tokens=0,output_tokens=0)
    assert ConversationModelUsage.from_wire(zero.to_wire())==zero
    assert ConversationModelUsage.aggregate((zero,zero))==zero
    assert ConversationModelUsage.aggregate((zero,ConversationModelUsage.unmeasured())).measurement=="unmeasured"
    for values in [("partial",0,0,0,0),("measured",0,1,0,0),("measured",-1,0,0,0)]:
        with pytest.raises(ValueError):ConversationModelUsage(*values)
