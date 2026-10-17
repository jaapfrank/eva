"""Bind an approved script before admitting it to the speech transport."""
import asyncio
import json
import pytest
from eva.voice_service.codex_session import CodexVoiceSession, VoiceTransportError


@pytest.mark.asyncio
async def test_literal_instruction_precedes_unchanged_speech_payload():
    sent = []
    session = CodexVoiceSession('unused', lambda: None, None)
    session.thread = 'test-thread'
    async def request(method, params):
        sent.append((method, params))
    session.request = request
    text = 'Ze zei: "even denken".\nIk heb niets aangezet.'
    await session.say(text)
    assert [method for method, _ in sent] == ['thread/realtime/appendText', 'thread/realtime/appendSpeech']
    instruction = sent[0][1]
    assert instruction['role'] == 'developer' and instruction['threadId'] == session.thread
    assert json.loads(instruction['text'].split('Script: ', 1)[1]) == text
    assert sent[1][1] == {'threadId': session.thread, 'text': text}


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [VoiceTransportError('rejected'), asyncio.CancelledError()])
async def test_failed_or_cancelled_binding_does_not_send_unbound_speech(error):
    sent = []
    session = CodexVoiceSession('unused', lambda: None, None)
    session.thread = 'test-thread'
    async def request(method, params):
        sent.append(method)
        raise error
    session.request = request
    with pytest.raises(type(error)):
        await session.say('Even denken.')
    assert sent == ['thread/realtime/appendText']
