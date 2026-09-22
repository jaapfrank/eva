from dataclasses import replace

import pytest

from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig, HindsightMemoryScopeError
from eva.application.memory_audience import MemoryAudience
from tests.projection.test_hindsight import hindsight_server, _query, PRINCIPAL, NOW

ALIAS = "telegram:101"


def _fact(principal):
    return {"id": "private-topic", "text": "The owner's preserved private topic.",
            "type": "world", "scores": {"final": 0.9}, "mentioned_at": NOW,
            "metadata": {"principal_id": principal, "audience_kind": "private_person",
                         "audience_id": f"person:{principal}", "conversation_id": principal,
                         "source_ref": f"{principal}:message:old", "trust": "direct_user"}}


@pytest.mark.parametrize("actor,source", [(PRINCIPAL, ALIAS), (ALIAS, PRINCIPAL)])
def test_private_alias_recall_preserves_original_source_and_one_request(hindsight_server, actor, source):
    url, state = hindsight_server
    state.group_recall_response = {"results": [_fact(source)]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS))
    query = replace(_query(), principal_id=actor, subject_ids=(actor,),
        audience=MemoryAudience.private_person(principal_id=actor), conversation_id=actor,
        query_text="Topic")
    result = engine.recall_for_presence_fast(query)
    assert result.confirmed_facts[0].subject == source
    assert result.confirmed_facts[0].source_ref == f"{source}:message:old"
    requests = [body for method, path, body in state.requests if path.endswith("/memories/recall")]
    assert len(requests) == 1
    assert set(requests[0]["tags"]) == {engine._person_tag(PRINCIPAL), engine._person_tag(ALIAS)}
    assert requests[0]["tags_match"] == "any_strict"
    assert engine.recall_for_presence_fast(query) == result
    assert len([p for _, p, _ in state.requests if p.endswith("/memories/recall")]) == 1


def test_private_alias_revocation_does_not_serve_populated_fast_cache(hindsight_server):
    url, state = hindsight_server
    current = [True]
    state.group_recall_response = {"results": [_fact(ALIAS)]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS) if current[0] else ())
    query = replace(_query(), query_text="Topic")
    assert engine.recall_for_presence_fast(query).confirmed_facts
    current[0] = False
    state.group_recall_response = {"results": []}
    assert not engine.recall_for_presence_fast(query).confirmed_facts
    request = [body for _, path, body in state.requests if path.endswith("/memories/recall")][-1]
    assert request["tags"] == [engine._person_tag(PRINCIPAL)]


def test_private_alias_revocation_during_recall_discards_result(hindsight_server, monkeypatch):
    url, state = hindsight_server
    current = [True]
    state.group_recall_response = {"results": [_fact(ALIAS)]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS) if current[0] else ())
    original = engine._recall_response
    def revoke(*args):
        result = original(*args)
        current[0] = False
        return result
    monkeypatch.setattr(engine, "_recall_response", revoke)
    assert not engine.recall_for_presence_fast(replace(_query(), query_text="Topic")).confirmed_facts


@pytest.mark.parametrize("changes", [{"principal_id": "person:other"},
    {"audience_kind": "household"}, {"audience_id": "person:other"},
    {"conversation_id": "telegram:-456"}])
def test_private_alias_rejects_unrelated_or_shared_source(hindsight_server, changes):
    url, state = hindsight_server
    fact = _fact(ALIAS)
    fact["metadata"].update(changes)
    state.group_recall_response = {"results": [fact]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS))
    with pytest.raises(HindsightMemoryScopeError):
        engine.recall_for_presence(replace(_query(), query_text="Topic"))


def test_private_alias_correction_invalidates_other_channel_cache(hindsight_server):
    from tests.projection.test_hindsight import _conversation_document
    url, state = hindsight_server
    state.group_recall_response = {"results": [_fact(ALIAS)]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS))
    query = replace(_query(), query_text="Topic")
    assert engine.recall_for_presence_fast(query).confirmed_facts
    document = replace(_conversation_document(), principal_id=ALIAS,
        audience=MemoryAudience.private_person(principal_id=ALIAS), conversation_id=ALIAS)
    engine.retain_conversation(document)
    state.group_recall_response = {"results": []}
    assert not engine.recall_for_presence_fast(query).confirmed_facts


def test_private_alias_resolver_failure_cannot_reuse_linked_cache(hindsight_server):
    url, state = hindsight_server
    state.group_recall_response = {"results": [_fact(ALIAS)]}
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url),
        private_alias_resolver=lambda query: (PRINCIPAL, ALIAS))
    query = replace(_query(), query_text="Topic")
    assert engine.recall_for_presence_fast(query).confirmed_facts
    def unavailable(query):
        raise OSError("trusted bindings unavailable")
    engine._private_alias_resolver = unavailable
    state.group_recall_response = {"results": []}
    assert not engine.recall_for_presence_fast(query).confirmed_facts
