"""Conversation preparation overlaps two complete reads without losing turn scope."""
from contextvars import ContextVar
from threading import Event, get_ident

import pytest

from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope, check_cancellation
from eva.application.conversation import ConversationTurn, IdentityProfile, LivingConversation
from tests.application.test_conversation import _Memory, _Model, NOW


SCOPE = ContextVar("test_preparation_scope", default=None)


def scenario(failure=None):
    context_started, memory_started, context_finished = Event(), Event(), Event()
    caller = get_ident()
    signal = CancellationSignal()
    calls = []
    class Memory(_Memory):
        def recall_for_presence_fast(self, query):
            assert get_ident() == caller
            assert context_started.wait(2), "context did not start alongside recall"
            memory_started.set()
            if failure == "memory":
                raise ValueError("memory failure")
            return super().recall_for_presence(query)
    class Actions:
        action_names = ()
        def pending(self, turn):
            assert get_ident() == caller
            calls.append("pending")
            return None
        def available_action_names(self, turn):
            assert calls == ["pending"] and get_ident() == caller
            calls.append("available")
            return ()
        def active_context(self, turn):
            try:
                assert calls == ["pending", "available"]
                assert get_ident() != caller and SCOPE.get() == "bound-turn"
                context_started.set()
                assert memory_started.wait(2), "memory was serialized behind context"
                if failure == "context":
                    raise ValueError("context failure")
                if failure == "cancel":
                    signal.cancel()
                    check_cancellation()  # The copied context carries the same signal.
                return ("Preserved complete active context",)
            finally:
                context_finished.set()
    class Model(_Model):
        def generate(self, *args, self_context=(), **kwargs):
            assert context_finished.is_set() and memory_started.is_set()
            assert "Preserved complete active context" in self_context
            assert args[2].confirmed_facts
            calls.append("model")
            return super().generate(*args, self_context=self_context, **kwargs)
    memory, model = Memory(), Model()
    conversation = LivingConversation(memory, model,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80), Actions())
    turn = ConversationTurn.private("tenant", "installation", "jaap", "conversation", "message", "Hoi", NOW)
    token = SCOPE.set("bound-turn")
    try:
        with cancellation_scope(signal):
            if failure:
                with pytest.raises(WorkInterrupted if failure == "cancel" else ValueError):
                    conversation.respond(turn)
            else:
                assert conversation.respond(turn).text
    finally:
        SCOPE.reset(token)
    assert context_finished.is_set(), "context worker escaped its owning response"
    assert ("model" in calls) is (failure is None)
    return memory, model


def test_complete_context_and_memory_overlap_before_model():
    memory, model = scenario()
    assert memory.query is not None and model.memory is not None


@pytest.mark.parametrize("failure", ["memory", "context", "cancel"])
def test_preparation_failure_joins_worker_and_never_answers(failure):
    scenario(failure)
