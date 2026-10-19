"""Expired day text is provenance, not a replacement for selected native facts."""
from dataclasses import replace
import pytest
from tests.projection.test_hindsight import (
    hindsight_server, _query, _conversation_document, NOW,
    HindsightMemoryEngine, HindsightProjectionConfig,
)


def _source(doc):
    return {"principal_id": doc.principal_id, "source_ref": doc.source_ref,
        "evidence_ref": doc.evidence_ref, "project_id": None, "occurred_at": NOW,
        "text": None, "truncated": False, "raw_state": "compacted",
        "compacted_at": "2026-08-24T10:00:00Z", "content_sha256": "a" * 64}


def test_selected_fact_survives_day_expiry_but_not_later_forgetting(hindsight_server):
    base, state = hindsight_server; doc = _conversation_document()
    metadata = {"principal_id": doc.principal_id, "source_ref": doc.source_ref,
        "evidence_ref": doc.evidence_ref, "privacy_class": doc.privacy_class,
        "trust": doc.trust, "audience_kind": "private_person", "audience_id": doc.audience.audience_id}
    state.group_recall_response = {"results": [{"id": "native-compact", "text": "Prefers concise answers.",
        "type": "world", "mentioned_at": NOW, "scores": {"final": 1.0}, "metadata": metadata}]}
    active = [True]
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base),
        private_source_resolver=lambda q, r: (_source(doc),) if active[0] else ())
    first = engine.recall_for_presence_fast(_query())
    assert len(first.confirmed_facts) == 1
    fact = first.confirmed_facts[0]
    assert fact.value["text"] == "Prefers concise answers."
    assert "original_source" not in fact.value
    assert fact.value["source_provenance"] == _source(doc)
    requests = len(state.requests)
    active[0] = False
    second = engine.recall_for_presence_fast(_query())
    assert second.confirmed_facts == ()
    assert len(state.requests) == requests
    assert second.recall.excluded_reasons == {fact.fact_id: "known_source_unavailable"}


@pytest.mark.parametrize("change", [{"text": "FAKE RAW"}, {"content_sha256": "invalid"},
    {"project_id": "foreign"}, {"principal_id": "other"}, {"compacted_at": "yesterday"}])
def test_invalid_compacted_provenance_never_becomes_original_text(hindsight_server, change):
    base, state = hindsight_server; doc = _conversation_document()
    state.group_recall_response = {"results": [{"id": "native-compact", "text": "Native selected fact.",
        "type": "world", "mentioned_at": NOW, "scores": {"final": 1.0},
        "metadata": {"principal_id": doc.principal_id, "source_ref": doc.source_ref,
            "evidence_ref": doc.evidence_ref, "privacy_class": doc.privacy_class,
            "trust": doc.trust, "audience_kind": "private_person", "audience_id": doc.audience.audience_id}}]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(base),
        private_source_resolver=lambda q, r: ({**_source(doc), **change},))
    recalled = engine.recall_for_presence_fast(_query())
    assert all("original_source" not in f.value and "source_provenance" not in f.value
        for f in recalled.confirmed_facts)
    assert "FAKE RAW" not in str(recalled)
