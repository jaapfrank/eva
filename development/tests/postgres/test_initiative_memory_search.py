"""Recorded private search and one ordinary occasion after a capability update."""
from datetime import timedelta
import pytest
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import FreeWillDecision
from tests.postgres.test_owner_initiatives import costless_native, NOW


def store(auth, old, profile=None):
    return PostgresSensoryEventStore(auth.owner._engine,tenant_id=old.tenant_id,
        installation_id=old.installation_id,initiative_principal_id=old.principal_id,
        initiative_authorizer=auth.policy,context_reviews_only=True,private_review_profile=profile)


def test_installed_capability_change_is_new_occasion_not_repeated_hourly_sample(costless_native):
    auth, old, _, owner, _, _, _=costless_native
    owner.stop(old.intent_id,now=NOW)
    first=store(auth,old);before=NOW+timedelta(minutes=59)
    assert first.schedule_context_review(now=before)=='scheduled'
    claim=first.mark_model_started(first.claim_next(now=before),now=before)
    first.complete(claim,FreeWillDecision('silence','A considered old decision',0,'synthetic'),now=before)
    original=first._timeline.events(claim.thread_id)
    changed=store(auth,old,'private-memory-search-v1');after=NOW+timedelta(hours=1)
    assert changed.schedule_context_review(now=after)=='scheduled'
    follow=changed.mark_model_started(changed.claim_next(now=after),now=after)
    assert follow.event.payload['review_profile']=='private-memory-search-v1'
    assert follow.thread_id!=claim.thread_id and first._timeline.events(claim.thread_id)==original
    context={'result_count':0,'recalled_context':[],'is_execution_permission':False,'truncated':False}
    changed.complete(follow,FreeWillDecision('silence','No forced work',0,'synthetic',memory_search_context=context),now=after)
    data=changed._timeline.events(follow.thread_id)[-1].payload['data']
    assert data['memory_search_context']==context
    assert changed.schedule_context_review(now=after+timedelta(minutes=1))=='idle'


def test_new_profile_cannot_bypass_a_lost_model_response(costless_native):
    auth, old, _, owner, _, _, _=costless_native
    owner.stop(old.intent_id,now=NOW);first=store(auth,old)
    assert first.schedule_context_review(now=NOW)=='scheduled'
    claim=first.mark_model_started(first.claim_next(now=NOW),now=NOW)
    first.fail(claim,RuntimeError('Lost response'),now=NOW)
    previous=first._timeline.events(claim.thread_id)
    assert store(auth,old,'private-memory-search-v1').schedule_context_review(now=NOW+timedelta(hours=1))=='idle'
    assert first._timeline.events(claim.thread_id)==previous
