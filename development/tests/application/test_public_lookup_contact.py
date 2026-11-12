"""Public lookup stays on contact; substantial work retains its existing route."""
import pytest
from eva.application.conversation import ConversationActionCall, LivingConversation
from tests.application.test_contact_model import IDENTITY, Model, Actions, turn
from tests.application.test_conversation import _Memory


@pytest.mark.parametrize("question", ["Is er file op de A1?", "Wanneer gras voeden?", "Hoe warm morgen?"])
def test_public_result_keeps_contact_identity_and_memory(question):
    class LookupActions(Actions):
        action_names = ("search_public_info", "start_background_research", "research_current_options")

    def choose(t, context):
        assert "research_current_options" not in context["available_actions"]
        if context["completed_action"] is None:
            return ConversationActionCall("lookup", "search_public_info", {})
        assert "search_public_info" not in context["available_actions"]
        return "Volgens de bron is dit de stand."
    contact = Model(choose)
    work = Model(lambda *_: pytest.fail("public lookup must not switch to work model"))
    memory = _Memory()
    reply = LivingConversation(memory, work, IDENTITY, LookupActions(), contact_model=contact).respond(turn(text=question))
    assert reply.text and len(contact.calls) == 2
    assert contact.calls[0][0][1:3] == contact.calls[1][0][1:3]
    assert not work.calls


def test_no_lookup_capability_does_not_add_lookup_instructions():
    contact = Model(lambda *_: "Hallo.")
    LivingConversation(_Memory(), contact, IDENTITY, Actions()).respond(turn())
    assert not any("search_public_info" in s for s in contact.calls[0][1]["self_context"])


def test_lookup_is_available_again_on_a_new_turn_but_not_repeated_within_it():
    class LookupActions(Actions):
        action_names=("search_public_info","start_background_research")
    def choose(t,context):
        if context["completed_action"] is None:
            assert "search_public_info" in context["available_actions"]
            return ConversationActionCall(t.message_id,"search_public_info",{})
        assert "search_public_info" not in context["available_actions"]
        return "Sourced answer."
    contact=Model(choose)
    conversation=LivingConversation(_Memory(),contact,IDENTITY,LookupActions(),contact_model=contact)
    conversation.respond(turn(message="first",text="Current public figure?"))
    conversation.respond(turn(message="followup",text="And for the following period?"))
    assert len(contact.calls)==4


@pytest.mark.parametrize('replay', [False, True])
def test_complete_voice_lookup_skips_rewriting_but_still_checks_reply_authority(replay):
    from eva.application.conversation import ConversationActionReceipt, ConversationConflict
    from eva.application.voice_context import VoiceContext, voice_context
    from eva.bootstrap.public_lookup import PublicLookupActions
    from types import SimpleNamespace
    import hashlib
    allowed = [True]
    text = 'De bron vermeldt het antwoord. https://example.org/source'
    receipt = ConversationActionReceipt(ConversationActionCall('lookup', 'search_public_info', {
        'question': 'A public question', 'public_review': 'public_unlinked', 'answer_directly': True,
    }), {'status': 'verified', 'result': {'content': text,
        'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'sources': [{'url': 'https://example.org/source', 'title': 'Source'}],
        'verification_scope': 'research_artifact_only'}})
    provider = PublicLookupActions(None, None, SimpleNamespace(
        available_action_names=lambda _: ('start_background_research',) if allowed[0] else ()))
    class LookupActions(Actions):
        action_names = ('search_public_info',)
        executions = 0
        guards = 0
        def pending(self, turn): return receipt if replay else None
        def execute(self, turn, call):
            self.executions += 1
            assert call == receipt.call
            return receipt
        def direct_voice_reply(self, turn, receipts): return provider.direct_voice_reply(turn, receipts)
        def reply_guard(self, turn, receipts):
            guard = provider.reply_guard(turn, receipts)
            if guard is None: return None
            def checked(text):
                self.guards += 1
                return guard(text)
            return checked
    actions = LookupActions()
    model = Model(lambda *_: receipt.call)
    context = VoiceContext((), (), lambda _: None, lambda _: None)
    with voice_context(context):
        reply = LivingConversation(_Memory(), model, IDENTITY, actions, contact_model=model).respond(turn())
    assert reply.text == text
    assert len(model.calls) == (0 if replay else 1)
    assert actions.executions == (0 if replay else 1)
    assert actions.guards == 1
    allowed[0] = False
    with voice_context(context), pytest.raises(ConversationConflict):
        LivingConversation(_Memory(), model, IDENTITY, actions, contact_model=model).respond(turn())


@pytest.mark.parametrize('action', ['read_home_assistant_inventory', 'read_home_assistant_state', 'read_home_assistant_history', 'home_assistant_request'])
def test_local_information_results_stay_on_contact_model(action):
    from eva.providers.home_assistant_policy import contact_reply_allowed as _contact_reply_allowed
    class LocalActions(Actions):
        action_names = (action, 'search_public_info')
        contact_reply_allowed = staticmethod(_contact_reply_allowed)
    def choose(t, context):
        if context['completed_action'] is None:
            return ConversationActionCall('local', action, {})
        return 'Dit staat in de lokale bron.'
    contact = Model(choose)
    work = Model(lambda *_: pytest.fail('ordinary local result switched to work model'))
    reply = LivingConversation(_Memory(), work, IDENTITY, LocalActions(), contact_model=contact).respond(turn())
    assert reply.text and len(contact.calls) == 2 and not work.calls


def test_local_source_guidance_requires_current_capability():
    contact = Model(lambda *_: 'Hallo.')
    LivingConversation(_Memory(), contact, IDENTITY, Actions()).respond(turn())
    assert not any('authorized Home Assistant data' in s for s in contact.calls[0][1]['self_context'])
