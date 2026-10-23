"""Current day sources reach private initiative without becoming new authority."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import json

import pytest

from eva.bootstrap.private_continuity import (private_initiative_day_context,
    private_initiative_day_context_current)
from eva.application.conversation import ConversationModelUsage, ConversationActionCall, IdentityProfile
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.application.test_initiative_memory_search import harness, decision, NOW


def fixture(text="Recent exchange"):
    docs = [SimpleNamespace(document_id=f"private:exchange:{i}", principal_id="owner",
        source_ref=f"message:{i}", evidence_ref=f"original:{i}", occurred_at="2026-10-18T20:30:00Z",
        content=text, retention_mode="dream", trust="direct_user", project_id=None) for i in range(3)]
    class Reader:
        def recent_private_exchanges(self, *, now, limit):
            assert now == NOW and limit == 3
            return tuple((doc, "day_context") for doc in docs)
        def document_reference(self, principal, source, evidence, *, now, recent_private_exchange=False):
            assert recent_private_exchange is True
            doc = next((d for d in docs if (d.principal_id, d.source_ref, d.evidence_ref)
                == (principal, source, evidence)), None)
            return None if doc is None else ("exact-source", doc, "day_context")
    reader = Reader()
    runtime = SimpleNamespace(_owner_memory_sources=lambda: reader)
    return runtime, reader, docs


def test_utf8_day_view_is_bounded_and_keeps_original_source_hashes():
    runtime, reader, docs = fixture("🙂" * 2000)
    view = private_initiative_day_context(runtime, now=NOW)
    assert len(canonical_json_bytes(view)) <= 4096 and len(view["sources"]) == 3
    for source in view["sources"]:
        assert len(source["text"].encode()) <= 512 and source["truncated"] is True
        assert source["content_sha256"] == sha256(docs[0].content.encode()).hexdigest()
    assert view["is_execution_permission"] is False and "not_new_request" in view["classification"]
    assert private_initiative_day_context_current(runtime, view, now=NOW)


@pytest.mark.parametrize("change", ["erased", "changed", "compacted", "not_selected"])
def test_source_change_blocks_reuse_but_zero_durable_facts_does_not(change):
    runtime, reader, docs = fixture()
    view = private_initiative_day_context(runtime, now=NOW)
    if change == "erased": docs.clear()
    elif change == "changed": docs[0].content = "Changed source"
    elif change == "compacted": del docs[0].content
    else:
        original = reader.document_reference
        reader.document_reference = lambda *a, **k: (*original(*a, **k)[:2], "not_selected")
    assert private_initiative_day_context_current(runtime, view, now=NOW) is (change == "not_selected")


def test_reader_failure_is_visible_unavailable_not_empty_context():
    runtime = SimpleNamespace(_owner_memory_sources=lambda: (_ for _ in ()).throw(RuntimeError()))
    view = private_initiative_day_context(runtime, now=NOW)
    assert view["available"] is False and "sources" not in view
    assert view["is_execution_permission"] is False


def test_new_arrivals_do_not_invalidate_the_exact_still_retained_selection():
    runtime, reader, docs = fixture()
    view = private_initiative_day_context(runtime, now=NOW)
    docs.append(SimpleNamespace(principal_id="owner",source_ref="new",evidence_ref="new"))
    assert private_initiative_day_context_current(runtime, view, now=NOW)


def test_worker_passes_sealed_context_and_records_only_hash_receipt():
    runtime, reader, docs = fixture()
    view = private_initiative_day_context(runtime, now=NOW)
    seen = []
    def choose(kwargs, state):
        seen.append(deepcopy(kwargs["private_day_context"]))
        kwargs["private_day_context"]["sources"][0]["text"] = "fabricated by model"
        return decision()
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    worker._private_day_context_reader = lambda **k: deepcopy(view)
    worker._private_day_context_revalidator = lambda context, **k: private_initiative_day_context_current(runtime, context, now=NOW)
    assert worker.run_once(now=NOW) == "silence" and failures == []
    receipt = completed[0].memory_search_context["private_day_context"]
    assert seen == [view] and receipt["context_sha256"] == canonical_sha256(view)
    assert receipt["source_count"] == 3 and "sources" not in receipt and "text" not in receipt
    assert len(queries) == 1


def test_worker_blocks_adoption_after_erasure_with_measured_usage_preserved():
    runtime, reader, docs = fixture()
    view = private_initiative_day_context(runtime, now=NOW)
    usage = ConversationModelUsage.measured(model_attempts=1, tool_calls=1,input_tokens=50,output_tokens=20)
    def choose(kwargs, state):
        docs.clear()
        return decision(usage=usage)
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    worker._private_day_context_reader = lambda **k: deepcopy(view)
    worker._private_day_context_revalidator = lambda context, **k: private_initiative_day_context_current(runtime, context, now=NOW)
    assert worker.run_once(now=NOW) == "failed" and completed == []
    assert failures[0].model_usage == usage
    assert failures[0].validation_failure_code == "private_day_context_changed_before_commit"


def test_household_worker_never_receives_private_day_context():
    def choose(kwargs, state):
        assert "private_day_context" not in kwargs
        return decision()
    worker, *rest = harness(choose, source="household-initiative")
    worker._private_day_context_reader = lambda **k: pytest.fail("No private source read for household")
    assert worker.run_once(now=NOW) == "silence"


def test_actual_model_body_contains_private_day_context_and_rejects_other_audience(monkeypatch,tmp_path):
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.sensory import SensoryEvent
    from tests.model.test_conversation_model import _memory
    runtime, reader, docs = fixture()
    view = private_initiative_day_context(runtime, now=NOW)
    bodies = []
    model=PydanticConversationModel(auth_file=tmp_path/"not-used")
    def request(body,*args,**kwargs):
        bodies.append(body)
        return (("",ConversationActionCall("decision","submit_sensory_decision",{
            "disposition":"silence","expression":None,"reason":"A considered choice", "urgency":0})),
            ConversationModelUsage.measured(model_attempts=1,tool_calls=1,input_tokens=50,output_tokens=20))
    monkeypatch.setattr(model,"_request_using",request)
    identity=IdentityProfile("Eva","Housemate",("Honesty",))
    event=SensoryEvent("initiative-review","synthetic:day","private_opportunity_review",NOW,{})
    model.evaluate_sensory_event(event,identity,_memory(),private_day_context=view)
    assert json.loads(bodies[0]["input"][0]["content"])["private_day_context"] == view
    with pytest.raises(ValueError, match="bounded private review"):
        model.evaluate_sensory_event(SensoryEvent("home_assistant","synthetic:shared","state_changed",NOW,{}),identity,_memory(),private_day_context=view)
    assert len(bodies) == 1


@pytest.mark.parametrize("source,private",[("initiative-review",True),("home-assistant",False)])
def test_person_oriented_default_recall_is_private_only(source,private):
    worker,state,queries,completed,failures,entered,resumed=harness(lambda kwargs,state:decision(),source=source)
    assert worker.run_once(now=NOW) == "silence" and failures == []
    query=queries[0]
    if private:
        assert query.query_text.startswith("Wie zijn de mensen in dit gezin")
    else:
        assert query.query_text.startswith("Persoonlijke plannen, dagelijkse behoeften")
        assert "Context bij deze waarneming: home-assistant state_changed" in query.query_text
    assert (query.query_purpose == "private_opportunity_review") is private
