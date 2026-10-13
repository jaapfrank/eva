from datetime import timedelta

import pytest

from eva.adapters.connectors.telegram import TelegramProactiveDelivery
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker
from tests.postgres.test_delegated_delivery_store import NOW, setup, admit, succeed
from tests.postgres.test_delegated_work_store import delegated_engine


@pytest.mark.parametrize('uncertain', [False, True])
def test_saved_result_to_bound_transport_and_restart_without_resend(delegated_engine, monkeypatch, uncertain):
    work, spec, destination, storage, bindings, authority = setup(
        delegated_engine, 'return-chain-' + str(uncertain),
    )
    admit(work, spec, destination)
    saved = succeed(work, spec)
    calls = []
    transport = TelegramProactiveDelivery(bot_token='123456:fixture', recipient_resolver=lambda:'42')
    def send(method, body, **kwargs):
        calls.append(body)
        assert storage.status(spec.work_id, **authority) == 'delivery_claimed'
        if uncertain:
            raise TimeoutError('fixture: acceptance unknown')
        return {'message_id': 7, 'chat': {'id': 42, 'type': 'private'}, 'text': body['text'],
                'reply_to_message': {'message_id': body['reply_parameters']['message_id']}}
    monkeypatch.setattr(transport._api, '_call', send)
    clock = lambda: NOW + timedelta(seconds=4)
    worker = DelegatedWorkDeliveryWorker(storage, transport, clock=clock)
    assert worker.run_once() == ('ambiguous' if uncertain else 'acknowledged')
    assert calls[0]['chat_id'] == '42'
    assert saved.output in calls[0]['text']
    restarted = PostgresDelegatedWorkDeliveryStore(
        work, bindings_loader=lambda:bindings[0], clock=clock,
    )
    assert DelegatedWorkDeliveryWorker(restarted, transport, clock=clock).run_once() == 'idle'
    assert len(calls) == 1
    assert restarted.status(spec.work_id, **authority) == (
        'delivery_ambiguous' if uncertain else 'delivery_acknowledged'
    )
    assert work.load(spec.work_id, **authority).result == saved
