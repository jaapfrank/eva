"""A spoken thinking reaction leaves Eva's normal memory and work route intact."""
import pytest
from eva.adapters.model.contracts import ConversationModelError
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    ConversationTurn, IdentityProfile, LivingConversation, MEMORY_SEARCH_ACTION,
)
from eva.application.voice_context import VoiceContext, voice_context, SPEAK_AND_CONTINUE


def test_thinking_then_memory_retains_spoken_part_and_first_real_step():
    from tests.application.test_conversation import _Memory, NOW
    events, steps = [], []
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def continuation_action_names(self, turn): return ()
        def execute(self, turn, call):
            steps.append(0)
            return ConversationActionReceipt(call, {"status": "verified", "value": "blue"})
        def execute_step(self, *args, **kwargs): pytest.fail("Thinking and recall are not durable work steps")
    class Model:
        count = 0
        def generate(self, *args, completed_action=None, prior_actions=(), **kwargs):
            self.count += 1
            if self.count == 1:
                call = ConversationActionCall("thinking", SPEAK_AND_CONTINUE, {"text": "Even denken, die kleur hadden we al gekozen."})
            elif self.count == 2:
                assert completed_action.call.name == SPEAK_AND_CONTINUE
                call = ConversationActionCall("recall", MEMORY_SEARCH_ACTION, {"query_text": "gekozen kleur"})
            elif self.count == 3:
                assert completed_action.call.name == MEMORY_SEARCH_ACTION
                assert [receipt.call.name for receipt in prior_actions] == [SPEAK_AND_CONTINUE]
                call = ConversationActionCall("read", "inspect_item", {})
            else:
                assert [receipt.call.name for receipt in prior_actions] == [SPEAK_AND_CONTINUE, MEMORY_SEARCH_ACTION]
                return ConversationModelResult("We hadden blauw gekozen.", "test")
            return ConversationModelResult(None, "test", call)
    context = VoiceContext((), (), lambda _: None, events.append)
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Welke kleur hadden we gekozen?", NOW)
    with voice_context(context):
        result = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions()).respond(turn)
    assert result.text == "We hadden blauw gekozen."
    assert steps == [0]
    assert len([event for event in events if event["type"] == "spoken_update"]) == 1


def test_model_can_search_memory_after_its_thinking_reaction(tmp_path):
    from tests.model.test_pydantic_conversation_model import _adapter, _turn, _identity, _memory
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase, ScriptedCall
    scripted = ScriptedPydanticConversation((ScriptedPhase.deferred(
        ScriptedCall("recall", MEMORY_SEARCH_ACTION, {"query_text": "gekozen kleur"})),))
    model = _adapter(tmp_path, scripted)
    thinking = ConversationActionReceipt(
        ConversationActionCall("thinking", SPEAK_AND_CONTINUE, {"text": "Even denken."}),
        {"status": "offered_to_voice", "text": "Even denken.", "heard": False})
    with voice_context(VoiceContext((), (), lambda _: None, lambda _: None)):
        result = model.generate(_turn(), _identity(), _memory(), (), completed_action=thinking)
    assert result.action_call.name == MEMORY_SEARCH_ACTION


def test_memory_then_thinking_keeps_used_search_in_model_history():
    from tests.application.test_conversation import _Memory, NOW
    class Model:
        count = 0
        def generate(self, *args, completed_action=None, prior_actions=(), **kwargs):
            self.count += 1
            if self.count == 1:
                call = ConversationActionCall("recall", MEMORY_SEARCH_ACTION, {"query_text": "gekozen kleur"})
            elif self.count == 2:
                call = ConversationActionCall("thinking", SPEAK_AND_CONTINUE, {"text": "Die keuze zie ik niet meer terug."})
            else:
                assert completed_action.call.name == SPEAK_AND_CONTINUE
                assert [receipt.call.name for receipt in prior_actions] == [MEMORY_SEARCH_ACTION]
                return ConversationModelResult("Welke kleur had je in gedachten?", "test")
            return ConversationModelResult(None, "test", call)
    context = VoiceContext((), (), lambda _: None, lambda _: None)
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Welke kleur hadden we gekozen?", NOW)
    with voice_context(context):
        result = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80)).respond(turn)
    assert result.text == "Welke kleur had je in gedachten?"


def test_model_cannot_repeat_search_after_memory_then_thinking(tmp_path):
    from tests.model.test_pydantic_conversation_model import _adapter, _turn, _identity, _memory
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase, ScriptedCall
    scripted = ScriptedPydanticConversation((ScriptedPhase.deferred(
        ScriptedCall("repeated-search", MEMORY_SEARCH_ACTION, {"query_text": "gekozen kleur"})),))
    model = _adapter(tmp_path, scripted)
    memory_receipt = ConversationActionReceipt(
        ConversationActionCall("recall", MEMORY_SEARCH_ACTION, {"query_text": "gekozen kleur"}),
        {"result_count": 0})
    thinking = ConversationActionReceipt(
        ConversationActionCall("thinking", SPEAK_AND_CONTINUE, {"text": "Even denken."}),
        {"status": "offered_to_voice", "text": "Even denken.", "heard": False})
    with voice_context(VoiceContext((), (), lambda _: None, lambda _: None)):
        with pytest.raises(ConversationModelError):
            model.generate(_turn(), _identity(), _memory(), (), completed_action=thinking,
                           prior_actions=(memory_receipt,))
    assert len(scripted.captures) == 1
    assert MEMORY_SEARCH_ACTION not in scripted.captures[0].offered_tool_names
