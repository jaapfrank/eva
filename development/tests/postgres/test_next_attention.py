"""Persisted next attention: restart, one-shot claims, supersession and audience."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from dataclasses import replace
import pytest
from eva.application.resident_attention import NextAttention
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker, PersonalInitiativeProposal
from eva.application.conversation import IdentityProfile
from eva.application.household_initiative import HouseholdInitiativeContext
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native,NOW
from tests.postgres.test_household_observation_initiative import _shared,_binding,_signal,_empty_shared_memory
from tests.postgres.test_initiative_source_feedback import SOURCE


def finish(store,at=NOW,choice=None,initiative=None):
    claim=store.claim_next(now=at)
    assert claim is not None
    claim=store.mark_model_started(claim,now=at)
    store.complete(claim,FreeWillDecision("silence","A pending intention deserves later attention",0,
        "synthetic",next_attention=choice,initiative=initiative),now=at)
    return claim


def worker(store,auth,model,*,ready=lambda:True,memory=None,binding=None):
    return SensoryDecisionWorker(store,model,memory or SimpleNamespace(recall_for_presence=lambda q:object()),
        IdentityProfile("Eva","Housemate",("Honest",),80),tenant_id=auth.owner._tenant_id,
        installation_id=auth.owner._installation_id,principal_id=str(auth.owner._owner),
        context_review_authorizer=auth.policy,
        context_review_admission=lambda *a,**k:SimpleNamespace(admitted=True),dispatch_ready=ready,
        household_context=None if binding is None else HouseholdInitiativeContext(binding,auth.policy))


def test_persisted_attention_once_after_restart_yields_to_foreground(review_setup):
    auth,old,control,budget,store,factory,*_=review_setup
    origin=finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"Reconsider a pending intention"))
    due=NOW+timedelta(minutes=5)
    assert factory().schedule_context_review(now=due-timedelta(seconds=1))=="idle"
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:factory().schedule_context_review(now=due),range(2)))
    assert sorted(values)==["idle","scheduled"]
    calls=[];ready=[False]
    class Model:
        def evaluate_sensory_event(self,event,*a,**kw):
            calls.append(event)
            return FreeWillDecision("silence","The pending intention is no longer relevant",0,"synthetic")
    assert worker(factory(),auth,Model(),ready=lambda:ready[0]).run_once(now=due)=="deferred"
    assert calls==[]
    ready[0]=True
    assert worker(factory(),auth,Model(),ready=lambda:ready[0]).run_once(now=due+timedelta(seconds=1))=="silence"
    assert len(calls)==1
    ref=calls[0].payload["attention_origin"]
    assert calls[0].occurred_at==due and ref["source_occurred_at"]==NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert ref["source_thread_id"]==origin.thread_id
    event=store._timeline.events(origin.thread_id)[-1]
    assert ref["source_decision_sha256"]==event.payload_sha256
    assert ref["source_decision_sequence"]==event.sequence
    assert factory().schedule_context_review(now=due+timedelta(seconds=2))=="idle"
    assert worker(factory(),auth,Model()).run_once(now=due+timedelta(seconds=2))=="idle"
    assert len(calls)==1


def test_future_choice_overrides_hourly_poll_and_unrelated_null_keeps_choice(review_setup):
    auth,old,control,budget,store,factory,*_=review_setup
    at=NOW+timedelta(hours=2)
    original=finish(store,choice=NextAttention(at,"Review closer to a future intention"))
    assert factory().schedule_context_review(now=NOW+timedelta(hours=1))=="idle"
    # A new source result can interrupt the future plan; an unrelated NULL is not cancellation.
    assert store.schedule_context_review(now=NOW+timedelta(seconds=1),source_outcomes=SOURCE)=="scheduled"
    finish(store,NOW+timedelta(seconds=1))
    assert factory().schedule_context_review(now=at)=="scheduled"
    claim=store.claim_next(now=at)
    assert claim.event.payload["attention_origin"]["source_thread_id"]==original.thread_id


def test_new_nonnull_choice_supersedes_earlier_queued_attention_before_model(review_setup):
    auth,old,control,budget,store,factory,*_=review_setup
    finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"First intention"))
    due=NOW+timedelta(minutes=5)
    assert store.schedule_context_review(now=due)=="scheduled"
    older=store.claim_next(now=due)
    # Two already prepared operations can finish in a different order. Only
    # the latest committed nonnull choice may reach the provider afterwards.
    source=store._decision_prefix.removeprefix("sensory:")+"999999999"
    store.retain(SensoryEvent("initiative-review",source,"private_opportunity_review",due+timedelta(seconds=1),{}))
    finish(store,due+timedelta(seconds=1),NextAttention(due+timedelta(minutes=10),"Newer intention"))
    assert store.cancel_superseded_attention(older,now=due+timedelta(seconds=2))
    assert store._timeline.events(older.thread_id)[-1].event_type=="WILL_ATTENTION_CANCELLED"
    assert factory().schedule_context_review(now=due+timedelta(minutes=9))=="idle"
    assert factory().schedule_context_review(now=due+timedelta(minutes=10))=="scheduled"


def test_stopped_source_intent_and_current_mandate_are_respected(review_setup):
    auth,old,control,budget,store,factory,*_=review_setup
    origin=finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"Review own pending work"),
        initiative=PersonalInitiativeProposal("Synthetic pending work","A synthetic need","Use existing permitted work"))
    data=store._timeline.events(origin.thread_id)[-1].payload["data"]
    control.stop(data["initiative"]["intent_id"],now=NOW+timedelta(seconds=1))
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=5))=="idle"
    auth.revoke()
    assert factory().schedule_context_review(now=NOW+timedelta(hours=1))=="mandate_unavailable"


def test_early_choice_keeps_requested_time_and_existing_coalescing_floor(review_setup):
    *_,store,factory,before,hashes=review_setup
    claim=finish(store,choice=NextAttention(NOW+timedelta(seconds=10),"A concrete near-term intention"))
    data=store._timeline.events(claim.thread_id)[-1].payload["data"]["next_attention"]
    assert data["requested_at"]==(NOW+timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data["at"]==(NOW+timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert factory().schedule_context_review(now=NOW+timedelta(seconds=299))=="idle"
    assert factory().schedule_context_review(now=NOW+timedelta(seconds=300))=="scheduled"


def test_past_attention_does_not_erase_completed_decision_or_create_retry(review_setup):
    *_,store,factory,before,hashes=review_setup
    claim=finish(store,choice=NextAttention(NOW-timedelta(seconds=1),"Invalid past moment"))
    data=store._timeline.events(claim.thread_id)[-1].payload["data"]
    assert data["attention_rejection"]=="not_future_at_commit" and "next_attention" not in data
    assert store._timeline.load(claim.thread_id).phase=="will_decided"
    assert factory().schedule_context_review(now=NOW+timedelta(seconds=1))=="idle"


def test_unknown_due_decision_is_never_replayed(review_setup):
    *_,store,factory,before,hashes=review_setup
    finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"Review a pending intention"))
    due=NOW+timedelta(minutes=5);assert store.schedule_context_review(now=due)=="scheduled"
    claim=store.claim_next(now=due);claim=store.mark_model_started(claim,now=due)
    store.fail(claim,RuntimeError("Unknown after provider dispatch"),now=due)
    assert factory().schedule_context_review(now=due+timedelta(hours=1))=="idle"
    assert store._timeline.load(claim.thread_id).phase=="will_usage_uncertain"


def test_household_attention_precedes_noise_keeps_audience_and_works_without_private_memory(costless_native):
    auth,old,interests,owner,originals,path,policy=costless_native
    owner.stop(old.intent_id,now=NOW);auth.policy();binding=_binding(old.tenant_id)
    factory=lambda:_shared(owner._engine,old.tenant_id,binding,auth.policy,old.principal_id)
    store=factory();assert store.schedule_context_review(now=NOW)=="scheduled"
    first=finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"Reconsider this shared situation"))
    due=NOW+timedelta(minutes=5)
    for index in range(3):originals.retain(_signal("new-noise-"+str(index),at=due))
    assert factory().next_household_observation(now=due) is None
    assert factory().schedule_household_observation(_signal("new-noise-0",at=due),now=due)=="attention_pending"
    assert factory().schedule_context_review(now=due)=="scheduled"
    calls=[]
    class Model:
        def evaluate_sensory_event(self,event,*a,**kw):
            calls.append(event)
            assert event.event_type=="household_attention_review" and binding.matches_event(event)
            assert "life_context" not in event.payload and "private-text" not in str(event.payload)
            assert kw.get("initiative_history",())==()
            return FreeWillDecision("silence","The shared situation needs no message",0,"synthetic")
    w=worker(factory(),auth,Model(),memory=SimpleNamespace(recall_for_presence=_empty_shared_memory),binding=binding)
    assert w.run_once(now=due)=="silence" and len(calls)==1
    ref=calls[0].payload["attention_origin"]
    assert ref["source_thread_id"]==first.thread_id
    assert not _binding(old.tenant_id,"c"*32).matches_event(calls[0])
    assert factory().schedule_context_review(now=due+timedelta(seconds=1))=="idle"


def test_same_second_choices_follow_commit_order_not_source_id_order(review_setup):
    *_,store,factory,before,hashes=review_setup
    finish(store,choice=NextAttention(NOW+timedelta(minutes=5),"First choice"))
    assert store.schedule_context_review(now=NOW,source_outcomes=SOURCE)=="scheduled"
    finish(store,choice=NextAttention(NOW+timedelta(minutes=8),"Second choice"))
    source=store._decision_prefix.removeprefix("sensory:")+"1"
    store.retain(SensoryEvent("initiative-review",source,"private_opportunity_review",NOW,{}))
    latest=finish(store,choice=NextAttention(NOW+timedelta(minutes=10),"Third choice"))
    data=store._timeline.events(latest.thread_id)[-1].payload["data"]["next_attention"]
    assert data["selection_sequence"]==3
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=8))=="idle"
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=10))=="scheduled"
    wake=store.claim_next(now=NOW+timedelta(minutes=10))
    assert wake.event.payload["attention_origin"]["source_thread_id"]==latest.thread_id
