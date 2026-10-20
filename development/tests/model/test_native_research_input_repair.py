"""Safe public-input rejection reaches the existing one-repair protocol."""
import asyncio
from types import SimpleNamespace
import pytest
from eva.adapters.model import research_tools as module
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationActionInputRejected


def test_native_projection_can_be_rephrased_before_any_preparation(monkeypatch):
    monkeypatch.setattr(module, "require_initiative_context", lambda _: object())
    tool = module.NativeResearchTool(object(), lambda value:value)
    prepared=[]
    monkeypatch.setattr(tool, "_preparation", lambda args, review: prepared.append(args) or "prepared")
    invalid={"question":"Waarom werkt mijn huis niet?", "title":"Publieke uitleg", "options":["Home Assistant"],"public_review":"public_unlinked"}
    with pytest.raises(ConversationActionInputRejected) as caught:
        asyncio.run(tool.prepare(object(), ConversationActionCall("first", "research_current_options", invalid)))
    assert prepared == []
    assert caught.value.fields == ("question", "title", "options", "public_review")
    assert "Herschrijf" in caught.value.prompt and invalid["question"] not in caught.value.prompt
    public={**invalid,"question":"Hoe werkt de openbare documentatie van Home Assistant?"}
    assert asyncio.run(tool.prepare(object(), ConversationActionCall("corrected", "research_current_options", public))) == "prepared"
    assert prepared == [public]


def test_native_authority_failure_never_becomes_input_repair(monkeypatch):
    def stopped(_): raise ConversationConflict("authority stopped")
    monkeypatch.setattr(module, "require_initiative_context", stopped)
    tool=module.NativeResearchTool(object(), lambda _:pytest.fail("validation must not run"))
    with pytest.raises(ConversationConflict,match="authority stopped"):
        asyncio.run(tool.prepare(object(), ConversationActionCall("first","research_current_options",{})))


def test_projection_without_public_review_is_not_repairable(monkeypatch):
    monkeypatch.setattr(module,"require_initiative_context",lambda _:object())
    tool=module.NativeResearchTool(object(),lambda x:x)
    with pytest.raises(ConversationConflict,match="uncertain or not reviewed"):
        asyncio.run(tool.prepare(object(),ConversationActionCall("first","research_current_options",{
            "question":"Public question","title":"Title","options":["Documentation"],"public_review":"uncertain"})))


@pytest.mark.parametrize("rejected_again", [False, True])
def test_existing_model_loop_repairs_public_input_once_then_finishes(monkeypatch, rejected_again):
    from eva.application.conversation import LivingConversation, ConversationActionReceipt
    from tests.application.test_contact_model import IDENTITY, Model, Actions, turn
    from tests.application.test_conversation import _Memory
    monkeypatch.setattr(module, "require_initiative_context", lambda _:object())
    tool=module.NativeResearchTool(object(),lambda x:x)
    prepared=[]
    monkeypatch.setattr(tool,"_preparation",lambda args,review:prepared.append(args))
    bad={"question":"Waarom werkt mijn huis niet?","title":"Publieke uitleg","options":["Home Assistant"],"public_review":"public_unlinked"}
    good={**bad,"question":"Hoe werkt de openbare documentatie van Home Assistant?"}
    class ResearchActions(Actions):
        action_names=("research_current_options",)
        def execute(self,t,call):
            asyncio.run(tool.prepare(t,call))
            return ConversationActionReceipt(call,{"status":"verified","result":{"content":"A public source result"}})
    def choose(t,context):
        receipt=context["completed_action"]
        if receipt is None: return ConversationActionCall("first","research_current_options",bad)
        if receipt.output["status"]=="invalid_input":
            assert receipt.output["action_admitted"] is False and prepared==[]
            assert "Herschrijf" in receipt.output["reason"]
            assert bad["question"] not in receipt.output["reason"]
            return ConversationActionCall("second","research_current_options",bad if rejected_again else good)
        return "Publieke bron gecontroleerd."
    model=Model(choose)
    reply=LivingConversation(_Memory(),model,IDENTITY,ResearchActions()).respond(turn())
    assert len(model.calls)==(2 if rejected_again else 3)
    assert prepared==([] if rejected_again else [good])
    assert "niet gestart" in reply.text if rejected_again else reply.text=="Publieke bron gecontroleerd."
