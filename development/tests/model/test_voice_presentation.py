"""The live response format remains active after source reads, without leaking to text."""
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.application.voice_context import VoiceContext, voice_context
from tests.model.test_pydantic_conversation_model import _adapter, _identity, _memory
from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase


def test_voice_format_is_current_instruction_after_historical_context(tmp_path):
    model = _adapter(tmp_path, ScriptedPydanticConversation((ScriptedPhase.text("unused"),)))
    context = VoiceContext((), (), lambda _: None, lambda _: None)
    receipt = ConversationActionReceipt(
        ConversationActionCall("read", "read_recent_activity", {"offset": 0}),
        {"status": "verified", "activity": []},
    )
    with voice_context(context):
        instructions = model._instructions(
            _identity(), _memory(), (), completed_action=receipt,
            self_context=context.instructions(),
        )
    assert instructions.endswith(context.spoken_instructions())
    own_state = instructions.split("Jouw duurzame eigen toestand:\n", 1)[1].split("Recente gesprekstijdlijn:", 1)[0]
    assert context.spoken_instructions() not in own_state
    assert "Lopende opdrachten in deze sessie" in own_state
    assert instructions.count("Actuele mondelinge antwoordvorm:") == 1


def test_text_conversation_does_not_inherit_spoken_format(tmp_path):
    model = _adapter(tmp_path, ScriptedPydanticConversation((ScriptedPhase.text("unused"),)))
    assert "Actuele mondelinge antwoordvorm:" not in model._instructions(_identity(), _memory(), ())
