"""An expired local lease waits without poisoning the durable initiative lane."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionClaim, SensoryDecisionWorker
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.mark.parametrize('initial', ['expired', 'unavailable', 'invalid', 'rejected', 'raises'])
def test_access_recovery_uses_same_worker_without_claim_or_model_during_outage(initial):
    state = [initial]
    claimed = []
    def status():
        if state[0] == 'raises':
            raise OSError('private path must not escape')
        return SimpleNamespace(ready=state[0] == 'ready')
    worker = SensoryDecisionWorker(SimpleNamespace(claim_next=lambda **kw: claimed.append(kw)),
        SimpleNamespace(credential_status=status), object(), IdentityProfile('Eva', 'Fixture', ('Honest',), 80),
        tenant_id='tenant', installation_id='home', principal_id='owner', context_review_authorizer=lambda: None)
    assert worker.run_once(now=NOW) == 'waiting_for_model_access'
    assert claimed == []
    state[0] = 'ready'
    assert worker.run_once(now=NOW) == 'idle'
    assert len(claimed) == 1


def test_access_expiring_during_recall_defers_then_resumes_without_unknown_usage():
    ready = [True]
    deferred, completed, calls = [], [], []
    event = SensoryEvent('initiative-review', 'fixture', 'private_opportunity_review', NOW, {})
    claim = SensoryDecisionClaim(event, 'sensory:fixture', 'fixture', 1, 0)
    store = SimpleNamespace(claim_next=lambda **kw: claim, mark_model_started=lambda c, **kw: c,
        context_review_claim_is_current=lambda c, **kw: True,
        defer_before_dispatch=lambda c, **kw: deferred.append(kw),
        complete=lambda c, d, **kw: completed.append(d))
    def recall(query):
        ready[0] = False
        return _memory()
    def evaluate(*args, **kwargs):
        calls.append(True)
        return FreeWillDecision('silence', 'No useful message', 0, 'fixture')
    memory = SimpleNamespace(recall_for_presence=recall)
    model = SimpleNamespace(credential_status=lambda: SimpleNamespace(ready=ready[0]),
        evaluate_sensory_event=evaluate)
    worker = SensoryDecisionWorker(store, model, memory, IdentityProfile('Eva', 'Fixture', ('Honest',), 80),
        tenant_id='tenant', installation_id='home', principal_id='owner',
        context_review_authorizer=lambda: None,
        context_review_admission=lambda *a, **kw: SimpleNamespace(admitted=True))
    assert worker.run_once(now=NOW) == 'waiting_for_model_access'
    assert deferred == [{'reason': 'model_access_unavailable', 'now': NOW}]
    assert not calls and not completed
    ready[0] = True
    memory.recall_for_presence = lambda q: _memory()
    assert worker.run_once(now=NOW) == 'silence'
    assert len(calls) == len(completed) == 1
