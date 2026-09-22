from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker, result_messages

NOW = datetime(2026, 9, 22, tzinfo=UTC)


class Store:
    def __init__(self, output='Resultaat met bron'):
        self.claim = SimpleNamespace(work_id='work:example', owner_principal_id='owner',
            principal_binding_id='binding', operation_id='operation', deadline=NOW+timedelta(seconds=120))
        self.result = SimpleNamespace(operation_id='operation', output=output,
                                      destination=SimpleNamespace(chat_id='42'))
        self.calls = []
    def claim_next(self, **kwargs):
        self.calls.append('claim')
        return self.claim
    def authorize_delivery(self, *args, **kwargs):
        self.calls.append('authorize')
        return self.result
    def acknowledge(self, claim, receipt, **kwargs):
        self.calls.append(('ack', receipt))
        return 'acknowledged'
    def mark_ambiguous(self, claim, code, **kwargs):
        self.calls.append(('ambiguous', code))


@pytest.mark.parametrize('output', ['Kort resultaat', '😀Bron\n'*6000])
def test_complete_result_preserved_in_bounded_messages(output):
    messages = result_messages('work:example', output)
    assert ''.join(message.split('\n\n', 1)[1] for message in messages) == output
    assert all(len(message.encode('utf-16-le')) // 2 <= 4096 for message in messages)


def test_claim_precedes_effect_and_all_parts_precede_ack():
    store = Store('a'*7000)
    def send(text, *, expected_chat_id):
        assert store.calls[0] == 'claim'
        assert expected_chat_id == '42'
        store.calls.append('send')
        return 'telegram:42:7'
    worker = DelegatedWorkDeliveryWorker(store, SimpleNamespace(deliver_text=send), clock=lambda:NOW)
    assert worker.run_once() == 'acknowledged'
    assert store.calls.count('send') == 3
    assert store.calls[-1] == ('ack', 'telegram:42:7')


def test_revocation_between_parts_stops_remaining_result():
    store = Store('a'*7000)
    sent = []
    def send(text, **kwargs):
        sent.append(text)
        store.result = None
        return 'telegram:42:7'
    worker = DelegatedWorkDeliveryWorker(store, SimpleNamespace(deliver_text=send), clock=lambda:NOW)
    assert worker.run_once() == 'ambiguous'
    assert len(sent) == 1
    assert not any(isinstance(item, tuple) and item[0] == 'ack' for item in store.calls)


def test_unknown_acceptance_never_retries_in_worker():
    store = Store()
    sent = []
    def send(*args, **kwargs):
        sent.append(1)
        raise TimeoutError('sensitive transport detail')
    worker = DelegatedWorkDeliveryWorker(store, SimpleNamespace(deliver_text=send), clock=lambda:NOW)
    assert worker.run_once() == 'ambiguous'
    assert sent == [1]
    assert store.calls[-1] == ('ambiguous', 'TimeoutError')


def test_expired_claim_cannot_send_another_part():
    store = Store('a'*7000)
    now = [NOW]
    sent = []
    def send(*args, **kwargs):
        sent.append(1)
        now[0] = store.claim.deadline
        return 'telegram:42:7'
    worker = DelegatedWorkDeliveryWorker(store, SimpleNamespace(deliver_text=send), clock=lambda:now[0])
    assert worker.run_once() == 'ambiguous'
    assert sent == [1]


def test_ack_failure_does_not_repeat_the_provider_effect():
    store = Store()
    sent = []
    def send(*args, **kwargs):
        sent.append(1)
        return 'telegram:42:7'
    def unavailable(*args, **kwargs):
        raise ConnectionError('database receipt uncertain')
    store.acknowledge = unavailable
    worker = DelegatedWorkDeliveryWorker(store, SimpleNamespace(deliver_text=send), clock=lambda:NOW)
    assert worker.run_once() == 'ambiguous'
    assert sent == [1]
    assert store.calls[-1] == ('ambiguous', 'ConnectionError')
