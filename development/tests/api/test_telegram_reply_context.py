"""Real Telegram API boundary preserves the user's words and reply reference."""
import pytest
from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from tests.api.test_telegram_canary_api import _Runtime, _payload


@pytest.mark.parametrize('streaming', [False, True])
def test_private_reply_context_reaches_the_turn_without_becoming_user_text(streaming):
    runtime = _Runtime()
    payload = _payload(message_id=20)
    payload['text'] = 'Dit is je laatste bericht!'
    payload['reply_context'] = {'source_ref':'telegram:7:9','text':'PHP disclosure timeline',
                                'occurred_at':'2026-08-27T10:00:00Z'}
    response = TestClient(create_app(runtime)).post('/internal/v1/telegram/canary/respond',
        json=payload, headers={'accept':'application/x-ndjson'} if streaming else {})
    assert response.status_code == 200
    assert runtime.turns[-1].text == payload['text']
    assert runtime.turns[-1].reply_context.text == 'PHP disclosure timeline'
    assert 'PHP disclosure timeline' in runtime.turns[-1].grounded_text


@pytest.mark.parametrize('source', ['telegram:8:9', 'telegram:7:20'])
def test_inconsistent_private_reply_reference_is_rejected(source):
    payload = _payload(message_id=20)
    payload['reply_context'] = {'source_ref':source,'text':'Quoted data',
                                'occurred_at':'2026-08-27T10:00:00Z'}
    assert TestClient(create_app(_Runtime())).post('/internal/v1/telegram/canary/respond',
                                                   json=payload).status_code == 422
