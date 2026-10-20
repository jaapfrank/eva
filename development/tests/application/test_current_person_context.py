"""Current person context reaches contact and initiative without another model/store."""
from dataclasses import replace
from datetime import UTC, datetime
import json
import pytest

from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile, LivingConversation
from eva.application.human_understanding import (build_human_understanding_document,
    parse_human_understanding_record, current_person_context)
from eva.application.memory import RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes
from tests.application.test_contact_model import Model, turn, IDENTITY

NOW = "2026-10-20T12:00:00Z"


def query():
    return RecallCurrentFacts(recall_id="synthetic:current-context", tenant_id="tenant", installation_id="installation",
        principal_id="jaap", audience=MemoryAudience.private_person(principal_id="jaap"),
        conversation_id="conversation", project_id=None, query_purpose="private_conversation",
        requested_valid_at=NOW, scope={"kind":"private"}, subject_ids=("jaap",), query_text="Hello", maximum_facts=8)


def source(name, stamp, *, previous=None, outcome="open", text="Tentative preference", kind="person_hypothesis"):
    t = replace(turn(name, "Synthetic actual-source counterinformation"), occurred_at=stamp)
    doc = build_human_understanding_document(t, {"kind":kind,"interpretation":text,
        "uncertainty_reason":"Limited evidence, do not turn this into a trait.",
        "review_at":"2026-10-20T11:00:00Z" if kind=="expectation" and outcome=="open" else None,
        "outcome":outcome,"previous_source_ref":previous["source_ref"] if previous else None,
        "previous_evidence_ref":previous["evidence_ref"] if previous else None})
    head = {"source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,
            "record":parse_human_understanding_record(doc.content,evidence_ref=doc.evidence_ref)}
    return {"principal_id":doc.principal_id,"source_ref":doc.source_ref,"evidence_ref":doc.evidence_ref,
        "project_id":None,"occurred_at":stamp,"text":t.text,"truncated":False,
        "learning_context":{"contract":"EvaHumanUnderstandingContext/v1",
            "records":[head]+(previous["learning_context"]["records"] if previous else [])}}


def engine(current):
    memory = HindsightMemoryEngine(HindsightProjectionConfig("http://127.0.0.1:1"),
        private_source_resolver=lambda q, refs: tuple(current))
    # Isolate native GET latency; the actual source resolver still runs each turn.
    memory.read_style_context = lambda q: None
    return memory


def test_contact_sees_fresh_refutation_before_dream_and_forget_removes_it():
    old = source("before", "2026-10-20T10:00:00Z",text="Obsolete guess")
    new = source("correction", "2026-10-20T10:01:00Z",previous=old,outcome="refuted",text="Revised uncertain interpretation")
    current=[old]; memory=engine(current)
    memory.recall_for_presence = lambda q: pytest.fail("contact must not run semantic history recall")
    contact=Model(lambda t,k:"Hello")
    conversation=LivingConversation(memory,contact,IDENTITY,contact_model=contact)
    conversation.respond(turn("one"))
    current[:]=[new]
    conversation.respond(turn("two"))
    initial, revised=(current_person_context(c[0][2]) for c in contact.calls)
    assert initial["entries"][0]["text"]=="Obsolete guess"
    assert revised["entries"][0]["text"]=="Revised uncertain interpretation"
    assert revised["entries"][0]["outcome"]=="refuted"
    assert revised["entries"][0]["supersedes"]["evidence_ref"]==old["evidence_ref"]
    assert revised["history_deferred"] is True
    instructions=object.__new__(PydanticConversationModel)._instructions(IDENTITY,contact.calls[-1][0][2],())
    assert instructions.count("Revised uncertain interpretation")==1 and "Obsolete guess" not in instructions
    assert "search_personal_memory" in instructions and "geen volledig profiel" in instructions
    current.clear()  # Same outcome as source resolver blocking a pending/erased head.
    conversation.respond(turn("three"))
    assert current_person_context(contact.calls[-1][0][2])["entries"]==[]
    assert len(contact.calls)==3  # One ordinary decision per turn; no portrait model.


def test_terminal_expectation_does_not_become_due_open_work():
    old=source("expectation", "2026-10-20T10:00:00Z",kind="expectation")
    new=source("outcome", "2026-10-20T10:01:00Z",previous=old,kind="expectation",outcome="confirmed")
    assert current_person_context(engine([old]).read_contact_context(query()))["entries"][0]["review_due"] is True
    result=current_person_context(engine([new]).read_contact_context(query()))
    item=result["entries"][0]
    assert item["review_due"] is False and item["epistemic_status"]=="model_inference_unconfirmed"
    assert result["is_execution_permission"] is False


def test_context_budget_omits_whole_entries_without_silent_truncation_or_duplicate_heads():
    sources=[source(str(i),f"2026-10-20T10:0{i}:00Z",text="é"*1500) for i in range(8)]
    memory=engine(sources).read_contact_context(query())
    result=current_person_context(memory)
    assert len(canonical_json_bytes(result))<=8192
    assert 0<len(result["entries"])<8 and result["omitted_by_limit"]>0
    assert all(e["text"]=="é"*1500 for e in result["entries"])
    duplicated=replace(memory,confirmed_facts=memory.confirmed_facts*2)
    assert current_person_context(duplicated)==result


def test_current_projection_excludes_unbound_legacy_facts_and_requires_all_pattern_sources():
    from eva.contracts.planning import ConfirmedFact
    base=HindsightMemoryEngine._empty_presence(query())
    facts=[ConfirmedFact(fact_id="legacy",subject="jaap",predicate="memory.world",value={"text":"Legacy guess"},
        source_ref="old",evidence_ref="legacy",trust="host_policy",observed_at=NOW)]
    refs=[{"principal_id":"jaap","source_ref":"one","evidence_ref":"proof"}]
    sources=[{**refs[0],"text":None,"raw_state":"compacted","content_sha256":"ab"*32,"occurred_at":"2026-10-19T10:00:00Z"}]
    facts.append(facts[0].model_copy(update={"fact_id":"pattern","predicate":"memory.observation",
        "value":{"text":"A contextual native pattern, not a trait.","semantic_trust":"model_inference_unconfirmed",
            "observation_source_refs":refs,"observation_sources":sources}}))
    memory=replace(base,confirmed_facts=tuple(facts))
    result=current_person_context(memory)
    assert [e["fact_id"] for e in result["entries"]]==["pattern"]
    assert result["entries"][0]["source_refs"][0]["source_text_sha256"]=="ab"*32
    facts[-1]=facts[-1].model_copy(update={"value":{**facts[-1].value,"observation_sources":[]}})
    assert current_person_context(replace(base,confirmed_facts=tuple(facts)))["entries"]==[]


def test_initiative_gets_one_current_head_with_uncertainty_without_copying_full_history(monkeypatch,tmp_path):
    old=source("before", "2026-10-20T10:00:00Z",text="Obsolete guess")
    new=source("correction", "2026-10-20T10:01:00Z",previous=old,outcome="refuted",text="Revised view")
    host=engine([new])
    memory=host._with_private_sources(query(),replace(host.read_contact_context(query()),
        style_mirror="Obsolete absolute style preference"))
    model=PydanticConversationModel(auth_file=tmp_path/"unused")
    bodies=[]
    def request(body,*args,**kwargs):
        bodies.append(body)
        return (("",ConversationActionCall("decision","submit_sensory_decision",{
            "disposition":"silence","expression":None,"urgency":0,"reason":"Context considered","initiative":None})),
            ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=80,output_tokens=10))
    monkeypatch.setattr(model,"_request_using",request)
    event=SensoryEvent("initiative-review","synthetic:projection","private_opportunity_review",datetime(2026,10,20,12,tzinfo=UTC),{})
    model.evaluate_sensory_event(event,IDENTITY,memory)
    payload=json.loads(bodies[0]["input"][0]["content"])
    assert len(payload["current_person_context"]["entries"])==1 and payload["recalled_context"]==[]
    assert bodies[0]["input"][0]["content"].count("Revised view")==1
    assert "Obsolete guess" not in bodies[0]["input"][0]["content"]
    assert "Obsolete absolute style preference" not in bodies[0]["input"][0]["content"]
    assert "private_learning_context" not in payload
    assert "geen nieuw mandaat" in bodies[0]["instructions"] and len(bodies)==1


def test_old_style_cannot_return_after_pending_or_erased_current_source():
    current=[source("before","2026-10-20T10:00:00Z",text="Current uncertainty")]
    memory=engine(current)
    memory.read_style_context=lambda query:"Obsolete absolute style preference"
    first=memory.read_contact_context(query())
    assert first.style_mirror is None
    current.clear()
    assert memory.read_contact_context(query()).style_mirror is None
    # Full recall/cache enrichment must not restore the old mirror either.
    cached=replace(first,style_mirror="Obsolete absolute style preference")
    result=memory._with_private_sources(query(),cached)
    assert result.style_mirror is None and result.confirmed_facts==()


def test_distinct_fact_ids_of_same_current_head_do_not_duplicate_model_context():
    memory=engine([source("one","2026-10-20T10:00:00Z",text="One current interpretation")]).read_contact_context(query())
    duplicate=memory.confirmed_facts[0].model_copy(update={"fact_id":"second-semantic-representation"})
    memory=replace(memory,confirmed_facts=(*memory.confirmed_facts,duplicate))
    text=object.__new__(PydanticConversationModel)._instructions(IDENTITY,memory,())
    assert text.count("One current interpretation")==1
