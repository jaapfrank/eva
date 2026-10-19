"""Fast contact retains the existing scoped style mirror without semantic recall."""
from dataclasses import replace

import pytest

from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig, HindsightMemoryScopeError
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
from tests.projection.test_hindsight import hindsight_server, _query, TENANT, INSTALLATION, PRINCIPAL


def test_contact_reads_the_same_grounded_style_without_semantic_recall(hindsight_server):
    url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url))
    engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    expected = engine.recall_for_presence(_query()).style_mirror
    state.requests.clear()
    assert engine.read_style_context(_query()) == expected
    assert expected
    assert len(state.requests) == 1
    assert state.requests[0][0] == "GET"
    assert "/mental-models/" in state.requests[0][1]


def test_contact_has_no_style_when_no_grounded_mirror_exists(hindsight_server):
    url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url))
    assert engine.read_style_context(_query()) is None
    assert not any(path.endswith(("/recall", "/reflect", "/refresh")) for _, path, _ in state.requests)


def test_shared_contact_never_reads_a_private_style_mirror(hindsight_server):
    url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url))
    audience = MemoryAudience.household(principal_id=PRINCIPAL, household_id="home",
        consent_ref=HouseholdConsentReference(PRINCIPAL, "home", "household-consent:test"))
    query = replace(_query(), audience=audience, scope={"kind": "household", "household_id": "home"},
                    conversation_id="household:home:conversation:test")
    assert engine.read_style_context(query) is None
    assert state.requests == []


def test_changed_private_binding_discards_style(hindsight_server):
    url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url))
    engine.maintain_person_scope(TENANT, INSTALLATION, PRINCIPAL)
    calls = iter([(PRINCIPAL, "alias"), (PRINCIPAL,)])
    engine._private_recall_principals = lambda query: next(calls)
    assert engine.read_style_context(_query()) is None


def test_style_read_requires_the_admitted_subject(hindsight_server):
    url, state = hindsight_server
    engine = HindsightMemoryEngine(HindsightProjectionConfig(url))
    with pytest.raises(HindsightMemoryScopeError):
        engine.read_style_context(replace(_query(), subject_ids=("someone-else",)))
    assert state.requests == []
