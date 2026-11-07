"""One ordinary private occasion can reuse a clean silence before model admission."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker
from eva.application.human_understanding import (
    build_human_understanding_document, current_person_context, parse_human_understanding_record,
)
from eva.application.memory import PresenceMemory
from eva.application.private_capability_context import build_private_capability_context
from eva.application.resident_attention import NextAttention
from eva.contracts.memory import MemoryRecall
from eva.contracts.planning import ConfirmedFact
from tests.application.test_contact_model import turn
from tests.postgres.test_owner_initiatives import NOW, costless_native


@pytest.mark.parametrize("change", [
    None, "new_source", "new_source_during_comparison", "expectation_due", "scheduled_attention", "stop",
])
def test_private_review_reuse_is_bounded_and_source_bound(costless_native, change):
    authority, original, _, control, _, _, _ = costless_native
    control.stop(original.intent_id, now=NOW)
    owner = dict(tenant_id=original.tenant_id, installation_id=original.installation_id)
    calls, admissions, reviewed_due = [], [], []
    sources = []
    due = NOW + timedelta(hours=1)
    day_reads = 0
    facts = ()
    if change == "expectation_due":
        statement = replace(turn("current-expectation", "Synthetic private source"),
            occurred_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        doc = build_human_understanding_document(statement, {
            "kind": "expectation", "interpretation": "Check a synthetic expectation",
            "uncertainty_reason": "Test-only evidence",
            "review_at": (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "outcome": "open", "previous_source_ref": None, "previous_evidence_ref": None,
        })
        record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
        facts = (ConfirmedFact(fact_id="expectation", subject=statement.principal_id,
            predicate="memory.person", trust="direct_user", observed_at=statement.occurred_at,
            source_ref=doc.source_ref,
            evidence_ref=doc.evidence_ref, value={"learning_context": {
                "contract": "EvaHumanUnderstandingContext/v1",
                "records": [{"source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
                    "record": record}],
            }}),)

    def store():
        return PostgresSensoryEventStore(
            control._engine, **owner, initiative_principal_id=original.principal_id,
            initiative_authorizer=authority.policy, context_reviews_only=True,
            private_review_profile="fixture-review-v1",
            private_capability_context_reader=lambda policy, digest, at:
                build_private_capability_context(policy, digest, observed_at=at, descriptors={}),
            private_work_context_reader=lambda now: {
                "available": True, "source": "current_private_owner_work_store",
                "as_of": now.isoformat(), "jobs": [],
            },
        )

    def recall(query):
        return PresenceMemory(MemoryRecall(
            schema_version=1, contract="MemoryRecall/v1", recall_id=query.recall_id,
            query_purpose=query.query_purpose, tenant_id=query.tenant_id,
            principal_id=query.principal_id, conversation_id=query.conversation_id,
            project_id=None, requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256="a" * 64, selected=[], excluded_reasons={},
            projection_version="fixture-v1", scope_sha256="b" * 64,
            created_at=query.requested_valid_at,
        ), facts)

    def day_context(now):
        nonlocal day_reads
        if now == due:
            day_reads += 1
            if change == "new_source_during_comparison" and day_reads == 2:
                sources.append({"source_ref": "new-in-comparison", "content_sha256": "d" * 64})
        return {"available": True, "as_of": now.isoformat(),
            "sources": list(sources), "truncated": False}

    def admit(claim, now):
        admissions.append(claim.thread_id)
        return SimpleNamespace(admitted=True)

    class Model:
        def evaluate_sensory_event(self, event, _identity, memory, *_args, **kwargs):
            calls.append(event)
            if change == "new_source_during_comparison" and len(calls) == 2:
                assert kwargs["private_day_context"]["sources"] == sources
            if change == "expectation_due":
                reviewed_due.append(current_person_context(memory)["entries"][0]["review_due"])
            attention = (NextAttention(NOW + timedelta(hours=1), "Revisit this source")
                if change == "scheduled_attention" and len(calls) == 1 else None)
            return FreeWillDecision("silence", "No change requires action", 0, "fixture",
                usage=ConversationModelUsage.measured(tool_calls=1, input_tokens=100, output_tokens=20),
                next_attention=attention)

    def worker(current_store):
        return SensoryDecisionWorker(current_store, Model(),
            SimpleNamespace(recall_for_presence=recall),
            IdentityProfile("Eva", "Housemate", ("Honest",)), **owner,
            principal_id=original.principal_id, context_review_authorizer=authority.policy,
            context_review_admission=admit, private_day_context_reader=day_context,
            private_day_context_revalidator=lambda context, now:
                all(source in sources for source in context["sources"]))

    first = store()
    assert first.schedule_context_review(now=NOW) == "scheduled"
    assert worker(first).run_once(now=NOW) == "silence"
    predecessor = first._timeline.events("sensory:" + calls[0].source_event_id)[-1]

    if change == "new_source":
        sources.append({"source_ref": "synthetic", "content_sha256": "c" * 64})
    if change == "stop":
        authority.revoke()
    second = store()
    if change == "stop":
        assert second.schedule_context_review(now=due) == "mandate_unavailable"
        assert len(calls) == len(admissions) == 1
        return
    assert second.schedule_context_review(now=due) == "scheduled"
    assert worker(second).run_once(now=due) == ("unchanged" if change is None else "silence")
    assert worker(store()).run_once(now=due) == "idle"
    if change is not None:
        assert len(calls) == len(admissions) == 2
        assert ("attention_origin" in calls[1].payload) == (change == "scheduled_attention")
        if change == "expectation_due":
            assert reviewed_due == [False, True]
        return

    assert len(calls) == len(admissions) == 1
    state = second._timeline.list_checkpoints(thread_prefix=second._decision_prefix, limit=1)[0]
    skipped = second._timeline.events(state.thread_id)[-1].payload["data"]
    assert skipped["memory_search_context"]["reused_decision_sha256"] == predecessor.payload_sha256
    assert skipped["usage"] == dict(measurement="measured", model_attempts=0,
        tool_calls=0, input_tokens=0, output_tokens=0)
    later = due + timedelta(hours=1)
    assert store().schedule_context_review(now=later) == "scheduled"
    assert worker(store()).run_once(now=later) == "silence"
    assert len(calls) == len(admissions) == 2
