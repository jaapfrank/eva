"""Bind an approved script before admitting it to the speech transport."""
import asyncio
import json
import pytest
from eva.voice_service.codex_session import CodexVoiceSession, VoiceTransportError


@pytest.mark.asyncio
async def test_literal_instruction_precedes_unchanged_speech_payload():
    sent = []
    session = CodexVoiceSession('unused', lambda: None, None, context_sender=sent.append)
    async def request(method, params):
        pytest.fail('V3 must not disguise speakable appendText as a developer instruction')
    session.request = request
    text = 'Ze zei: "even denken".\nIk heb niets aangezet.'
    await session.say(text)
    assert [event['type'] for event in sent] == ['session.context.append'] * 2
    assert [event['channel'] for event in sent] == ['commentary', 'speakable']
    assert json.loads(sent[0]['content'][0]['text'].split('Script: ', 1)[1]) == text
    assert sent[1]['content'] == [{'type': 'input_text', 'text': text}]
    assert all(set(event) == {'type', 'channel', 'content'} for event in sent)


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [VoiceTransportError('rejected'), asyncio.CancelledError()])
async def test_failed_or_cancelled_binding_does_not_send_unbound_speech(error):
    sent = []
    def send(event):
        sent.append(event['channel'])
        raise error
    session = CodexVoiceSession('unused', lambda: None, None, context_sender=send)
    with pytest.raises(type(error)):
        await session.say('Even denken.')
    assert sent == ['commentary']


@pytest.mark.asyncio
async def test_speech_without_explicit_context_transport_fails_closed():
    session = CodexVoiceSession('unused', lambda: None, None)
    with pytest.raises(VoiceTransportError, match='context transport unavailable'):
        await session.say('Even denken.')


@pytest.mark.asyncio
async def test_closed_session_does_not_offer_more_context():
    sent = []
    session = CodexVoiceSession('unused', lambda: None, None, context_sender=sent.append)
    session.closing = True
    with pytest.raises(VoiceTransportError, match='context transport unavailable'):
        await session.say('Even denken.')
    assert not sent
