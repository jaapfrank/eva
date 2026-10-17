"""Slow authorized memory must not trigger a separate filler-model request."""
from dataclasses import replace
from time import sleep

import pytest

from eva.application.conversation import (
    ConversationModelResult, ConversationModelUsage, ConversationTurn,
    IdentityProfile, LivingConversation,
)
from eva.application.voice_context import VoiceContext, voice_context
from tests.application.test_conversation import _Memory, NOW


def test_slow_recall_keeps_one_grounded_contact_decision():
    calls, events = [], []
    usage = ConversationModelUsage.measured(tool_calls=0, input_tokens=10, output_tokens=2)

    class Memory(_Memory):
        def recall_for_presence(self, query):
            # Exceeds the former 750ms preparation threshold.
            sleep(.8)
            calls.append("memory")
            return replace(super().recall_for_presence(query), style_mirror="authorized style")

    class Model:
        def generate_voice_preparation(self, *args, **kwargs):
            pytest.fail("A slow read must not introduce a separate speech decision")
        def generate(self, turn, identity, memory, recent, **kwargs):
            assert calls == ["memory"]
            assert memory.style_mirror == "authorized style"
            calls.append("answer")
            return ConversationModelResult("Fijn dat je er bent.", "test", usage=usage)

    model = Model()
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Hoe is het?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)):
        result = LivingConversation(Memory(), model, IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80),
                                    contact_model=model).respond(turn)
    assert calls == ["memory", "answer"]
    assert events == []
    assert result.model_usage.model_attempts == 1


def test_failed_memory_never_generates_uninformed_speech():
    events = []
    class Memory:
        def recall_for_presence(self, query):
            raise RuntimeError("source authority rejected")
    class Model:
        def generate(self, *args, **kwargs):
            pytest.fail("Memory failure must precede any response")
        generate_voice_preparation = generate
    model = Model()
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Hoe is het?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)), pytest.raises(RuntimeError, match="source authority"):
        LivingConversation(Memory(), model, IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80),
                           contact_model=model).respond(turn)
    assert events == []
