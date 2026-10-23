"""A completed result must survive stopping its still-pending follow-up."""
from datetime import timedelta
import hashlib
from types import SimpleNamespace

import pytest

from eva.application.free_will import PersonalInitiativeProposal
from eva.application.resident_attention import NextAttention
from tests.postgres.test_next_attention import finish, worker
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native, NOW


def completed(setup, phase='initiative_completed'):
    auth, old, owner, budget, source, factory, *_ = setup
    origin = finish(source, choice=NextAttention(NOW+timedelta(minutes=5), 'Review this result later'),
        initiative=PersonalInitiativeProposal('A completed useful result', 'Useful preparation', 'Produce a bounded result'))
    intent = source._timeline.events(origin.thread_id)[-1].payload['data']['initiative']['intent_id']
    thread = 'intent:' + intent
    state = source._timeline.load(thread)
    content = 'Preserved result content'
    source._timeline.transition(thread_id=thread, expected_revision=state.revision,
        checkpoint_id=thread+':fixture-completed', phase=phase, status='completed',
        event_type='PERSONAL_INITIATIVE_COMPLETED' if phase=='initiative_completed' else 'PERSONAL_PROTOTYPE_COMPLETED',
        occurred_at=NOW, operation_id=intent, data={'intent_id':intent, 'filename':'result.md',
            'content':content, 'content_sha256':hashlib.sha256(content.encode()).hexdigest()})
    return intent


@pytest.mark.parametrize('phase', ['initiative_completed', 'release_ready'])
def test_stop_completed_keeps_result_and_suppresses_attention_after_reload(review_setup, phase):
    auth, old, owner, budget, source, factory, *_ = review_setup
    intent = completed(review_setup, phase)
    original = source._timeline.events('intent:'+intent)
    artifact = owner.artifact(intent, now=NOW)
    result = owner.stop(intent, now=NOW+timedelta(seconds=1))
    owner.stop(intent, now=NOW+timedelta(seconds=2))
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=5)) == 'idle'
    assert result['followup_stopped'] is True
    assert source._timeline.load('intent:'+intent).phase == phase
    assert owner.artifact(intent, now=NOW+timedelta(seconds=3)) == artifact
    events = source._timeline.events('intent:'+intent)
    assert events[:len(original)] == original
    assert sum(e.event_type=='PERSONAL_INITIATIVE_FOLLOWUP_STOPPED' for e in events) == 1
    item = next(x for x in factory().recent_initiatives() if x['intent_id']==intent)
    assert item['followup_stopped'] is True
    assert item['recorded_result']['content'] == artifact['content']


def test_completed_without_stop_still_gets_chosen_attention(review_setup):
    *_, source, factory, before, hashes = review_setup
    completed(review_setup)
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=5)) == 'scheduled'


@pytest.mark.parametrize('when', ['queued', 'preparing'])
def test_stop_completed_cancels_due_review_before_model(review_setup, when):
    auth, old, owner, budget, source, factory, *_ = review_setup
    intent = completed(review_setup)
    due = NOW+timedelta(minutes=5)
    assert factory().schedule_context_review(now=due) == 'scheduled'
    def recall(query):
        if when=='preparing':
            owner.stop(intent, now=due)
        return object()
    if when=='queued':
        owner.stop(intent, now=due)
    model = SimpleNamespace(evaluate_sensory_event=lambda *a,**k:pytest.fail('Stopped follow-up reached model'))
    result = worker(factory(),auth,model,memory=SimpleNamespace(recall_for_presence=recall)).run_once(now=due)
    assert result == 'cancelled'
