"""Purpose-bound recall keeps full private context without unrelated anchor boosts."""
from dataclasses import replace

import pytest

from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
from tests.projection.test_hindsight import _query, PRINCIPAL, NOW

QUERY = "Persoonlijke plannen, dagelijkse behoeften, interesses en voorkeuren voor een nuttige eigen bijdrage."


def engine_with_query_relative_scores(monkeypatch, full_query):
    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://synthetic.invalid"))
    monkeypatch.setattr(engine, "ensure_bank", lambda *_: "existing-person-bank")
    monkeypatch.setattr(engine, "_recall_style_mirror", lambda *_: None)
    calls = []

    def request(method, path, body=None, **kwargs):
        assert method == "POST" and path.endswith("/memories/recall")
        calls.append(body)
        complete = body["query"] == full_query
        identifier = "full-context" if complete else "unrelated-anchor"
        return 200, {"results": [{
            "id": identifier, "scores": {"final": 0.31 if complete else 0.99},
            "text": "Relevant complete context" if complete else "Unrelated isolated-word context",
            "type": "world", "mentioned_at": NOW, "document_id": identifier,
            "metadata": {"principal_id": PRINCIPAL, "privacy_class": "internal",
                         "source_ref": "synthetic:" + identifier,
                         "evidence_ref": "synthetic-evidence:" + identifier,
                         "trust": "direct_user"}}]}

    monkeypatch.setattr(engine, "_request", request)
    return engine, calls


def test_private_orientation_does_not_promote_high_score_from_unrelated_implicit_word(monkeypatch):
    engine, calls = engine_with_query_relative_scores(monkeypatch, QUERY)
    query = replace(_query(), query_purpose="private_opportunity_review", query_text=QUERY,
                    maximum_facts=1)
    memory = engine.recall_for_presence(query)
    assert [call["query"] for call in calls] == [QUERY]
    fact, = memory.confirmed_facts
    assert fact.fact_id == "hindsight:full-context"
    assert fact.source_ref == "synthetic:full-context" and fact.observed_at == NOW
    assert fact.trust == "direct_user"
    assert memory.recall.query_purpose == query.query_purpose
    assert memory.recall.requested_valid_at == NOW
    assert memory.recall.principal_id == query.principal_id
    assert query.query_text == QUERY and query.maximum_facts == 1
    call, = calls
    assert call["query_timestamp"] == NOW and call["budget"] == engine._config.recall_budget
    assert call["max_tokens"] == engine._config.recall_max_tokens
    assert call["tags"] == [engine._person_tag(PRINCIPAL)]
    assert call["tags_match"] == "all_strict"


@pytest.mark.parametrize("purpose", ["reply", "passive_event_decision"])
def test_other_query_purposes_keep_existing_implicit_anchor_retrieval(monkeypatch, purpose):
    engine, calls = engine_with_query_relative_scores(monkeypatch, QUERY)
    query = replace(_query(), query_purpose=purpose, query_text=QUERY, maximum_facts=1)
    memory = engine.recall_for_presence(query)
    expected = HindsightMemoryEngine._recall_query_variants(QUERY)
    assert len(expected) > 1 and {call["query"] for call in calls} == set(expected)
    assert memory.confirmed_facts[0].fact_id == "hindsight:unrelated-anchor"


@pytest.mark.parametrize("marker", ["Semantische zoekconcepten:", "Gebonden retrieval-ankers:"])
def test_explicit_private_search_concepts_still_expand_as_requested(monkeypatch, marker):
    text = "Vraag met oorspronkelijke context. " + marker + " concept_alpha; concept_beta"
    engine, calls = engine_with_query_relative_scores(monkeypatch, text)
    query = replace(_query(), query_purpose="private_opportunity_review", query_text=text,
                    maximum_facts=1)
    engine.recall_for_presence(query)
    assert {call["query"] for call in calls} == {text, "concept_alpha", "concept_beta"}
