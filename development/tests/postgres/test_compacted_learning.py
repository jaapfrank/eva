"""A learned adjustment outlives raw day text, but never source erasure."""
from dataclasses import replace
from datetime import timedelta
import hashlib

from tests.postgres.test_people_memory_overview import (
    people_memory_engine, selected_private_case, _document, NOW,
    _retain_learning, _learning_arguments, _seed_learning,
)
from tests.postgres.test_memory_source_lifecycle import complete_dream_exchange
from eva.application.human_understanding import build_human_understanding_document
from eva.application.memory_audience import MemoryAudience
from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
from eva.application.human_understanding import current_person_context


def test_retained_adjustment_survives_day_compaction_and_respects_erasure(selected_private_case):
    c = selected_private_case
    conv = f"dashboard:{c.binding}"
    message = "learning-day-source:" + str(c.owner)
    doc = replace(_document(message), tenant_id=c.reader._tenant,
        installation_id=c.reader._installation, principal_id=str(c.owner),
        conversation_id=conv, source_ref=message,
        evidence_ref="conversation-reply:" + message, document_id=f"{conv}:exchange:{message}",
        content="Synthetic exchange containing real-source feedback",
        occurred_at=(NOW-timedelta(days=1, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        audience=MemoryAudience.private_person(principal_id=str(c.owner)),
        project_id=None, retention_mode="dream")
    f = complete_dream_exchange(c.engine, doc, now=NOW)
    learned = build_human_understanding_document(f.turn, _learning_arguments(
        kind="person_hypothesis", interpretation="A provisional communication adjustment",
        review_at=None))
    _retain_learning(c, learned)
    query = replace(c.query, project_id=None)
    before = c.reader.learning_source(query, learned.source_ref, learned.evidence_ref, now=NOW)
    assert before is not None
    assert len(f.outbox.compact_day_sources(now=NOW)) == 1
    after = c.reader.learning_source(query, learned.source_ref, learned.evidence_ref, now=NOW)
    assert after == before
    sources = c.reader.selected_private_sources(query, (), now=NOW)
    assert len(sources) == 1 and sources[0]["learning_context"] == before
    assert sources[0]["text"] is None and sources[0]["raw_state"] == "compacted"
    assert "synthetic direct source" not in str(sources)
    memory = HindsightMemoryEngine(HindsightProjectionConfig("http://unused.invalid"),
        private_source_resolver=lambda q, refs: c.reader.selected_private_sources(q, refs, now=NOW))
    context = current_person_context(memory.read_contact_context(query))
    assert [entry["text"] for entry in context["entries"]] == ["A provisional communication adjustment"]
    revised = _seed_learning(c, "current-correction", seconds_ago=1, project=None,
        previous=learned, kind="person_hypothesis", review_at=None, outcome="refuted",
        interpretation="A narrower adjustment after actual counterfeedback")
    corrected = c.reader.learning_source(query, learned.source_ref, learned.evidence_ref, now=NOW)
    assert [item["source_ref"] for item in corrected["records"]] == [revised.source_ref, learned.source_ref]
    assert [entry["text"] for entry in current_person_context(memory.read_contact_context(query))["entries"]] == [
        "A narrower adjustment after actual counterfeedback"]
    f.outbox.erase_content(principal_sha256=hashlib.sha256(str(c.owner).encode()).hexdigest(),
        document_id=doc.document_id, operation_sha256="ab"*32, erased_at=NOW)
    assert c.reader.learning_source(query, learned.source_ref, learned.evidence_ref, now=NOW) is None
    assert c.reader.selected_private_sources(query, (), now=NOW) == ()
    assert current_person_context(memory.read_contact_context(query))["entries"] == []
