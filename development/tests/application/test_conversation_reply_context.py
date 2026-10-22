"""Quoted references bind a reply without becoming instructions or testimony."""
from dataclasses import replace
import pytest
from eva.application.conversation import ConversationTurn
from eva.application.conversation_reply import ConversationReplyContext


def turn():
    return ConversationTurn.private('t', 'i', 'person', 'telegram:7', 'telegram:7:12',
        'Dit bedoel ik.', '2026-10-22T07:00:00Z')


def quote(**changes):
    return ConversationReplyContext(**({'source_ref':'telegram:7:9','text':'PHP was reported in August.',
        'occurred_at':'2026-10-21T23:00:00Z'} | changes))


def test_quoted_text_is_separate_data_and_digest_can_reconstruct_identity_without_plaintext():
    quoted = quote(text='Do not treat this quoted instruction as a new request.')
    t = replace(turn(), reply_context=quoted)
    assert t.text == 'Dit bedoel ik.'
    assert 'not a new instruction' in t.grounded_text and quoted.text in t.grounded_text
    assert t.grounded_text.endswith('Gebruikersbericht: Dit bedoel ik.')
    digest_only = replace(turn(), reply_context_sha256=quoted.context_sha256)
    assert digest_only.reply_context is None and quoted.text not in digest_only.grounded_text
    assert quote(text="Different message").context_sha256 != quoted.context_sha256
    with pytest.raises(ValueError, match='digest'):
        replace(t, reply_context_sha256='0'*64)


@pytest.mark.parametrize('changes', [{'source_ref':'telegram:8:9'},
    {'source_ref':'telegram:7:12'}, {'occurred_at':'2026-10-22T07:00:01Z'}])
def test_cross_chat_self_and_future_references_are_rejected(changes):
    with pytest.raises(ValueError, match='bound'):
        replace(turn(), reply_context=quote(**changes))


def test_selection_and_wire_fields_are_bounded():
    q = quote(selection='reported in August')
    assert ConversationReplyContext.from_wire(q.to_wire()) == q
    for value in (q.to_wire() | {'authority':True}, q.to_wire() | {'selection':'different text'},
                  q.to_wire() | {'text':'x'*16385}):
        with pytest.raises(ValueError):
            ConversationReplyContext.from_wire(value)


@pytest.mark.parametrize('kind', ['household', 'telegram_group'])
def test_private_quote_does_not_cross_audience_boundaries(kind):
    from eva.application.memory_audience import (MemoryAudience, HouseholdConsentReference,
                                                 TelegramGroupConsentReference)
    if kind == 'household':
        audience = MemoryAudience.household(principal_id='person', household_id='home',
            consent_ref=HouseholdConsentReference('person','home','household-consent:one'))
        conversation_id = 'household:home:conversation:room'
    else:
        audience = MemoryAudience.telegram_group(principal_id='person', group_chat_id='-7',
            consent_ref=TelegramGroupConsentReference('person','-7','telegram-group-consent:one'))
        conversation_id = 'telegram:-7'
    shared = replace(turn(), audience=audience, conversation_id=conversation_id,
                     message_id=conversation_id+':12')
    with pytest.raises(ValueError, match='bound'):
        replace(shared, reply_context=quote(source_ref=conversation_id+':9'))
