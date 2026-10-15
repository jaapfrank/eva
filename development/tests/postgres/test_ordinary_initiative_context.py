"""Source-related attention retains its exact completion; ordinary hours do not."""
from datetime import timedelta
from eva.application.resident_attention import NextAttention
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_next_attention import finish
from tests.postgres.test_owner_initiatives import NOW,costless_native
from tests.postgres.test_initiative_source_feedback import SOURCE


def test_due_attention_keeps_exact_original_completion_after_restart(review_setup):
    *_,store,factory,before,hashes=review_setup
    finish(store)
    at=NOW+timedelta(seconds=1)
    assert store.schedule_context_review(now=at,source_outcomes=SOURCE)=="scheduled"
    origin=finish(store,at,NextAttention(at+timedelta(minutes=5),"Reconsider this original finding"))
    due=at+timedelta(minutes=5)
    assert factory().schedule_context_review(now=due)=="scheduled"
    claim=store.claim_next(now=due)
    assert factory().attention_source_binding(claim)==origin.event.payload["native_completion_source"]
    assert claim.event.payload["attention_origin"]["origin_thread_id"]==origin.thread_id


def test_non_source_attention_does_not_invent_a_recent_source(review_setup):
    *_,store,factory,before,hashes=review_setup
    finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"Review a different intention"))
    due=NOW+timedelta(minutes=5)
    assert store.schedule_context_review(now=due,source_outcomes=SOURCE)=="scheduled"
    claim=store.claim_next(now=due)
    assert store.attention_source_binding(claim) is None
