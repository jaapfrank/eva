"""Source-bound attention reuses the real PG sensory lane, no provider/model calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import pytest
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.sensory import SensoryEvent
from eva.application.resident_attention import NextAttention
from tests.postgres.test_owner_initiatives import costless_native,NOW
from tests.postgres.test_next_attention import finish,worker
from eva.application.free_will import FreeWillDecision


def signal(name,at,entity="sensor.alpha",old="0",new="1"):
    return SensoryEvent("home_assistant","home-assistant:"+hashlib.sha256(name.encode()).hexdigest(),
        "state_changed",at,{"entity_id":entity,"old_state":old,"new_state":new})


@pytest.fixture
def sources(costless_native):
    auth,old,interest,owner,originals,path,policy=costless_native
    owner.stop(old.intent_id,now=NOW);auth.policy()
    stream=[HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("sensor",))]
    first=signal("selected-original",NOW-timedelta(seconds=2))
    other=signal("not-selected-original",NOW-timedelta(seconds=1),"sensor.beta")
    originals.retain(first);originals.retain(other)
    def factory():
        return PostgresSensoryEventStore(owner._engine,tenant_id=old.tenant_id,
            installation_id=old.installation_id,initiative_principal_id=old.principal_id,
            initiative_authorizer=auth.policy,context_reviews_only=True,
            attention_source_resolver=lambda event:stream[0].attention_source(event))
    store=factory();assert store.schedule_context_review(now=NOW)=="scheduled"
    return auth,old,owner,originals,stream,first,factory,store


def choose(sources,source_id=None):
    *_,first,factory,store=sources
    return finish(store,choice=NextAttention(NOW+timedelta(hours=1),
        "A source-dependent information gap deserves another look",source_id or first.source_event_id))


def test_chosen_source_change_uses_one_attention_identity_after_restart_and_duplicate_callbacks(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    origin=choose(sources)
    for index in range(4):originals.retain(signal("unrelated-"+str(index),NOW+timedelta(minutes=1),"sensor.beta"))
    assert factory().schedule_context_review(now=NOW+timedelta(minutes=5))=="idle"
    changed=signal("selected-change",NOW+timedelta(minutes=2),new="2")
    originals.retain(changed);originals.retain(changed)
    assert factory().schedule_context_review(now=NOW+timedelta(seconds=299))=="idle"
    at=NOW+timedelta(minutes=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda _:factory().schedule_context_review(now=at),range(2)))
    assert sorted(outcomes)==["idle","scheduled"]
    calls=[]
    class Model:
        def evaluate_sensory_event(self,event,*a,**kw):
            calls.append(event)
            return FreeWillDecision("silence","The selected change needs no action",0,"synthetic")
    assert worker(factory(),auth,Model()).run_once(now=at)=="silence"
    payload=calls[0].payload
    assert payload["attention_source_change"]["source_event_id"]==changed.source_event_id
    assert payload["attention_source_change"]["is_current_proof"] is False
    assert payload["attention_source"]["source_event_id"]==first.source_event_id
    assert payload["attention_origin"]["source_thread_id"]==origin.thread_id
    assert factory().schedule_context_review(now=at+timedelta(seconds=1))=="idle"
    assert len(calls)==1  # Not one model call per callback or unrelated source.


def test_timer_fallback_retains_exact_source_without_fabricating_a_change(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    choose(sources)
    at=NOW+timedelta(hours=1)
    assert factory().schedule_context_review(now=at)=="scheduled"
    claim=factory().claim_next(now=at)
    assert claim.event.payload["attention_source"]["source_event_id"]==first.source_event_id
    assert claim.event.payload["attention_source"]["available"] is True
    assert "attention_source_change" not in claim.event.payload
    assert "other_changes_require_scheduled_native_read" in claim.event.payload["attention_source"]["binding"]["coverage"]


def test_source_not_offered_to_model_cannot_be_attached(sources):
    *_,store=sources
    claim=store.mark_model_started(store.claim_next(now=NOW),now=NOW)
    with pytest.raises(ValueError,match="not offered"):
        store.complete(claim,FreeWillDecision("silence","Unknown source",0,"synthetic",
            next_attention=NextAttention(NOW+timedelta(hours=1),"Unknown source", "home-assistant:"+"f"*64)),now=NOW)
    assert not any("next_attention" in e.payload.get("data",{}) for e in store._timeline.events(claim.thread_id))


def test_config_change_before_commit_cannot_reuse_old_source_metadata(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    stream[0]=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="rotated-synthetic",admitted_domains=("sensor",))
    with pytest.raises(ValueError,match="coverage changed"):
        choose(sources)


def test_changed_configuration_before_dispatch_cancels_without_model(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    choose(sources)
    originals.retain(signal("selected-late-change",NOW+timedelta(minutes=2)))
    at=NOW+timedelta(minutes=5);assert factory().schedule_context_review(now=at)=="scheduled"
    stream[0]=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("light",))
    class Model:
        def evaluate_sensory_event(self,*a,**kw):pytest.fail("revoked source must not dispatch")
    assert worker(factory(),auth,Model()).run_once(now=at)=="cancelled"


def test_removed_source_at_planned_time_is_explicitly_unavailable_not_new_permission(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    choose(sources)
    stream[0]=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("light",))
    at=NOW+timedelta(hours=1);assert factory().schedule_context_review(now=at)=="scheduled"
    claim=factory().claim_next(now=at)
    assert claim.event.payload["attention_source"]["available"] is False
    assert "reconnect_before_native_read" in claim.event.payload["attention_source"]["reason"]
    assert "attention_source_change" not in claim.event.payload


def test_unavailable_offered_fallback_cannot_be_chosen_even_after_config_returns(sources):
    auth,old,owner,originals,stream,first,factory,store=sources
    choose(sources)
    stream[0]=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("light",))
    at=NOW+timedelta(days=2)  # Original is no longer among the 24-hour observations.
    assert factory().schedule_context_review(now=at)=="scheduled"
    claim=factory().claim_next(now=at);claim=factory().mark_model_started(claim,now=at)
    assert claim.event.payload["life_context"]["observations"]==[]
    assert claim.event.payload["attention_source"]["available"] is False
    stream[0]=HomeAssistantEventStream(base_url="http://synthetic.invalid",token="synthetic-only",admitted_domains=("sensor",))
    with pytest.raises(ValueError,match="coverage changed"):
        factory().complete(claim,FreeWillDecision("silence","Unavailable offered source",0,"synthetic",
            next_attention=NextAttention(at+timedelta(hours=1),"Revisit",first.source_event_id)),now=at)


def test_offered_source_hash_must_match_exact_retained_revision(sources):
    from copy import deepcopy
    from eva.adapters.postgres.initiative_attention import bind_attention_source
    auth,old,owner,originals,stream,first,factory,store=sources
    claim=store.claim_next(now=NOW)
    payload=deepcopy(claim.event.payload)
    selected=next(o for o in payload["life_context"]["observations"] if o["source_event_id"]==first.source_event_id)
    selected["source_sha256"]="0"*64
    with owner._engine.begin() as conn:
        store._timeline._scope(conn)
        with pytest.raises(ValueError,match="revision differs"):
            bind_attention_source(conn,store,NextAttention(NOW+timedelta(hours=1),"Exact source",first.source_event_id),
                replace(claim.event,payload=payload),now=NOW)


def test_full_existing_context_retains_usable_coverage_without_exceeding_byte_budget(sources):
    from copy import deepcopy
    from eva.contracts.canonical_json import canonical_json_bytes
    from eva.adapters.postgres.initiative_attention import annotate_attention_sources
    auth,old,owner,originals,stream,first,factory,store=sources
    claim=store.claim_next(now=NOW)
    context=deepcopy(claim.event.payload["life_context"])
    for observation in context["observations"]:observation.pop("source_attention",None)
    context["synthetic_existing_context"]=""
    context["synthetic_existing_context"]="x"*(8000-len(canonical_json_bytes(context)))
    with owner._engine.begin() as conn:
        store._timeline._scope(conn);annotate_attention_sources(conn,store,context)
    assert len(canonical_json_bytes(context))<=8192
    assert any(o.get("source_attention",{}).get("available") is True for o in context["observations"])
    assert context["coverage"]["observations_truncated"] is True
