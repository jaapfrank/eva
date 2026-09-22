"""Independent context reads overlap without releasing partial authority/context."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier, Event

import pytest

from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope, check_cancellation
from eva.application.conversation import LivingConversation, ConversationModelResult
from tests.application.test_contact_model import IDENTITY, turn
from tests.application.test_conversation import _Memory


def test_recall_overlaps_context_only_after_pending_recovery_and_keeps_contextvars():
    recovered, entered, release = Event(), Event(), Event()
    scope = ContextVar("preparation_test_scope", default=None)
    token = scope.set("private-owner")
    queries = []
    class Memory(_Memory):
        supports_parallel_context_recall = True
        def recall_for_presence(self, query):
            assert recovered.is_set()
            assert scope.get() == "private-owner"
            queries.append(query)
            entered.set()
            assert release.wait(2)
            return super().recall_for_presence(query)
    class Actions:
        action_names = ()
        def pending(self, t):
            recovered.set()
        def active_context(self, t):
            assert entered.wait(2)
            release.set()
            return ("Full context retained.",)
    class Model:
        def generate(self, t, identity, memory, recent, **kwargs):
            assert release.is_set() and len(queries) == 1
            assert memory.confirmed_facts[0].subject == t.principal_id
            assert kwargs["self_context"] == ("Full context retained.",)
            return ConversationModelResult("Ready.", "fixture")
    try:
        LivingConversation(Memory(), Model(), IDENTITY, Actions()).respond(turn())
        assert queries[0].audience == turn().audience
        assert queries[0].scope == {"kind": "person", "principal_id": "jaap"}
    finally:
        scope.reset(token)


@pytest.mark.parametrize("failure", ["memory", "context", "cancel"])
def test_parallel_preparation_failure_never_reaches_model(failure):
    entered, release = Event(), Event()
    signal = CancellationSignal()
    class Memory(_Memory):
        supports_parallel_context_recall = True
        def recall_for_presence(self, query):
            entered.set()
            assert release.wait(2)
            check_cancellation()  # request cancellation must reach the copied context
            if failure == "memory":
                raise ValueError("memory authority unavailable")
            return super().recall_for_presence(query)
    class Actions:
        action_names = ()
        def pending(self, t):
            return None
        def active_context(self, t):
            assert entered.wait(2)
            if failure == "cancel":
                signal.cancel()
            release.set()
            if failure == "context":
                raise ValueError("context authority unavailable")
            return ()
    class Model:
        def generate(self, *args, **kwargs):
            pytest.fail("no model request before complete, admitted preparation")
    with cancellation_scope(signal), pytest.raises((ValueError, WorkInterrupted)):
        LivingConversation(Memory(), Model(), IDENTITY, Actions()).respond(turn())


def test_concurrent_people_never_share_recalled_context():
    rendezvous = Barrier(2)
    class Memory(_Memory):
        supports_parallel_context_recall = True
        def recall_for_presence(self, query):
            rendezvous.wait(timeout=2)
            return super().recall_for_presence(query)
    class Model:
        def generate(self, t, identity, memory, recent, **kwargs):
            assert all(fact.subject == t.principal_id for fact in memory.confirmed_facts)
            return ConversationModelResult(t.principal_id, "fixture")
    conversation = LivingConversation(Memory(), Model(), IDENTITY)
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(conversation.respond, turn("owner", principal="jaap"))
        other = executor.submit(conversation.respond, turn("other", principal="daughter"))
        assert owner.result(timeout=3).text == "jaap"
        assert other.result(timeout=3).text == "daughter"
