"""The installed feedback store must suppress later attention after reloading."""
from datetime import timedelta

import pytest

from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.application.free_will import PersonalInitiativeProposal
from eva.application.resident_attention import NextAttention
from tests.postgres.test_next_attention import finish
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native, NOW


def test_feedback_replay_and_reloaded_attention_do_not_restart_stopped_work(review_setup):
    auth, old, control, budget, store, factory, *_ = review_setup
    origin = finish(store, choice=NextAttention(NOW + timedelta(minutes=5), 'Review pending work'),
        initiative=PersonalInitiativeProposal('Prepare a household follow-up',
            'A retained open intention', 'Check the existing permitted sources'))
    intent = store._timeline.events(origin.thread_id)[-1].payload['data']['initiative']['intent_id']
    feedback = 'Dit is al geregeld; geen herinnering of vervolgonderzoek meer.'
    at = NOW + timedelta(seconds=1)
    result = control.feedback(intent, feedback, now=at, request_key='owner-correction-1')
    assert result['execution_stopped'] is True

    # New adapter/store objects read PostgreSQL, not cached Python state.
    reloaded = PostgresOwnerInitiatives(control._engine, tenant_id=control._tenant_id,
        installation_id=control._installation_id, owner=control._owner,
        binding=control._binding, authority_scope=control._authority)
    assert reloaded.feedback(intent, feedback, now=at + timedelta(seconds=1),
                             request_key='owner-correction-1') == result
    events = store._timeline.events('intent:' + intent)
    recorded = [e for e in events if e.event_type == 'PERSONAL_INITIATIVE_FEEDBACK']
    assert len(recorded) == 1
    assert recorded[0].payload['data']['feedback'] == feedback
    with pytest.raises(ValueError, match='changed during replay'):
        reloaded.feedback(intent, 'Toch iets anders', now=at + timedelta(seconds=2),
                          request_key='owner-correction-1')
    current = next(x for x in factory().recent_initiatives() if x['intent_id'] == intent)
    assert current['owner_feedback'] == feedback
    assert current['phase'] == 'personal_stopped'
    assert factory().schedule_context_review(now=NOW + timedelta(minutes=5)) == 'idle'
    assert factory().claim_next(now=NOW + timedelta(minutes=5)) is None
    assert store._timeline.events('intent:' + intent) == events
