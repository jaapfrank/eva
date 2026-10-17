"""STOP after the ordinary model's accounting forbids late speech."""
import pytest
from eva.application.cancellation import CancellationSignal, cancellation_scope, WorkInterrupted
from eva.application.conversation import (
    ConversationActionCall, ConversationModelResult, ConversationModelUsage,
    ConversationTurn, IdentityProfile, LivingConversation,
)
from eva.application.voice_context import VoiceContext, voice_context, SPEAK_AND_CONTINUE
from tests.application.test_conversation import _Memory, NOW


def test_stop_after_model_accounting_prevents_intermediate_speech():
    signal = CancellationSignal()
    published = []
    usage = ConversationModelUsage.measured(tool_calls=1, input_tokens=10, output_tokens=2)
    class Model:
        def generate(self, *args, **kwargs):
            return ConversationModelResult(None, "test", ConversationActionCall(
                "thought", SPEAK_AND_CONTINUE, {"text": "Even denken."}), usage=usage)
    class Journal:
        def __init__(self): self.usages = []
        def begin_model_request(self, turn): return str(len(self.usages))
        def complete_model_request(self, turn, key, usage, **kwargs):
            self.usages.append(usage)
            signal.cancel()
        def model_usage(self, turn):
            return ConversationModelUsage.aggregate(tuple(self.usages))
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Wat deed je vandaag?", NOW)
    with cancellation_scope(signal), voice_context(VoiceContext((), (), lambda _: None, published.append)), pytest.raises(WorkInterrupted):
        LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80)).respond(turn, model_journal=Journal())
    assert published == []
