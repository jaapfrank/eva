"""One owner-bound memory search inside an existing private decision, without providers."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionClaim, SensoryDecisionWorker
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

NOW = datetime(2026, 10, 18, 21, tzinfo=UTC)


class Payload:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        assert mode == "json"
        return deepcopy(self.value)


def memory(query, facts=(), padding=""):
    return SimpleNamespace(confirmed_facts=tuple(Payload(fact) for fact in facts),
        recall=Payload({"recall_id": query.recall_id, "tenant_id": query.tenant_id,
            "principal_id": query.principal_id, "requested_valid_at": query.requested_valid_at,
            "scope_sha256": canonical_sha256(query.scope), "padding": padding}),
        style_mirror=None)


def harness(choose, *, facts=(), padding="", source="initiative-review", capable=True,
            on_lookup=None):
    event = SensoryEvent(source, "synthetic:private-search", "private_opportunity_review"
        if source == "initiative-review" else "household_observation_review"
        if source == "household-initiative" else "state_changed", NOW,
        {"household_binding": "shared-binding"} if source == "household-initiative" else {})
    claim = SensoryDecisionClaim(event, "sensory:synthetic:private-search", "synthetic:private-search", 3, 0)
    state = {"authorized": True, "current": True, "foreground": False,
             "cancelled": False, "principal": "owner"}
    queries, completed, failures, entered, resumed = [], [], [], [], []

    class Store:
        def claim_next(self, **kwargs): return claim
        def mark_model_started(self, actual, **kwargs): return actual
        def context_review_claim_is_current(self, actual, **kwargs):
            assert actual == claim
            return state["current"]
        def cancel_superseded_attention(self, actual, **kwargs): return state["cancelled"]
        def complete(self, actual, decision, **kwargs): completed.append(decision)
        def fail(self, actual, error, **kwargs): failures.append(error)

    def recall(query):
        queries.append(query)
        if len(queries) > 1 and on_lookup is not None:
            on_lookup(state)
        return memory(query, facts if len(queries) > 1 else (), padding if len(queries) > 1 else "")

    def authorize():
        if not state["authorized"]:
            raise RuntimeError("synthetic owner revoked")

    def evaluate(event, identity, initial, attention=None, **kwargs):
        entered.append(kwargs)
        value = choose(kwargs, state)
        resumed.append(True)
        return value

    model = SimpleNamespace(evaluate_sensory_event=evaluate)
    if capable:
        model.supports_private_context_search = True
    worker = SensoryDecisionWorker(Store(), model, SimpleNamespace(recall_for_presence=recall),
        IdentityProfile("Eva", "Fixture", ("Honest",), 80), tenant_id="tenant",
        installation_id="home", principal_id="owner",
        memory_principal_resolver=lambda: state["principal"], clock=lambda: NOW,
        context_review_authorizer=authorize,
        context_review_admission=lambda *args, **kwargs: SimpleNamespace(admitted=True),
        dispatch_ready=lambda: not state["foreground"],
        household_context=(lambda *args: (SimpleNamespace(confirmed_facts=("shared",)), "shared-binding"))
            if source == "household-initiative" else None)
    return worker, state, queries, completed, failures, entered, resumed


def decision(**kwargs):
    return FreeWillDecision("silence", "A considered choice", 0, "synthetic", **kwargs)


def test_one_actual_search_preserves_query_scope_and_seals_host_result_against_model_mutation():
    facts = ({"fact_id": "synthetic:interest", "text": "A retained scoped interest"},)
    captured = []
    def choose(kwargs, state):
        result = kwargs["private_context_search"]("  a self-chosen question  ")
        captured.append(deepcopy(result))
        result["recalled_context"][0]["text"] = "Model fabricated text"
        return decision(memory_search_context={"fabricated": True})
    worker, state, queries, completed, failures, entered, resumed = harness(choose, facts=facts)
    assert worker.run_once(now=NOW) == "silence" and failures == []
    assert len(queries) == 2
    original, searched = queries
    assert searched == replace(original, recall_id=original.recall_id + ":memorysearch",
        query_text="Semantische zoekconcepten:   a self-chosen question  ")
    assert searched.maximum_facts == 8 and searched.principal_id == "owner"
    assert searched.audience == original.audience and searched.requested_valid_at == original.requested_valid_at
    context = completed[0].memory_search_context
    assert context == captured[0] and context["recalled_context"] == list(facts)
    assert context["result_count"] == 1 and context["omitted_count"] == 0
    assert context["recall"]["recall_id"] == searched.recall_id
    assert context["is_execution_permission"] is False and context["truncated"] is False
    assert len(canonical_json_bytes(context)) <= 8192


def test_duplicate_search_cannot_consume_a_second_lookup():
    def choose(kwargs, state):
        search = kwargs["private_context_search"]
        search("first question")
        with pytest.raises(RuntimeError, match="already consumed"): search("another question")
        return decision()
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    assert worker.run_once(now=NOW) == "silence" and len(queries) == 2
    assert completed[0].memory_search_context["result_count"] == 0
    assert completed[0].memory_search_context["recalled_context"] == []
    assert "world_empty" not in completed[0].memory_search_context


@pytest.mark.parametrize("query", [None, "", "  ", "é" * 513])
def test_invalid_query_does_not_lookup_or_enable_another_attempt(query):
    def choose(kwargs, state):
        search = kwargs["private_context_search"]
        with pytest.raises(ValueError, match="query is invalid"): search(query)
        with pytest.raises(RuntimeError, match="already consumed"): search("replacement")
        return decision(memory_search_context={"fabricated": True})
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    assert worker.run_once(now=NOW) == "silence" and len(queries) == 1
    assert completed[0].memory_search_context is None


def test_query_limit_is_utf8_bytes_and_exact_boundary_is_allowed():
    def choose(kwargs, state):
        kwargs["private_context_search"]("é" * 512)
        return decision()
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    assert worker.run_once(now=NOW) == "silence" and len(queries) == 2


@pytest.mark.parametrize("boundary", ["authorized", "current", "foreground", "cancelled", "principal"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_changed_owner_claim_attention_or_foreground_blocks_search_continuation(boundary, when):
    def invalidate(state):
        state[boundary] = "other-owner" if boundary == "principal" else (
            True if boundary in {"foreground", "cancelled"} else False)
    def choose(kwargs, state):
        if when == "before": invalidate(state)
        kwargs["private_context_search"]("a precise question")
        pytest.fail("a failed current-authority check must not return a search result")
    worker, state, queries, completed, failures, entered, resumed = harness(choose,
        on_lookup=invalidate if when == "after" else None)
    assert worker.run_once(now=NOW) == "failed"
    assert len(queries) == (1 if when == "before" else 2)
    assert completed == [] and len(failures) == 1 and resumed == []


@pytest.mark.parametrize("source,capable", [("household-initiative", True),
    ("home-assistant", True), ("initiative-review", False)])
def test_shared_passive_or_old_model_never_receives_callback(source, capable):
    def choose(kwargs, state):
        assert "private_context_search" not in kwargs
        return decision(memory_search_context={"fabricated": True})
    worker, state, queries, completed, failures, entered, resumed = harness(choose,
        source=source, capable=capable)
    assert worker.run_once(now=NOW) == "silence" and failures == []
    assert len(queries) == (0 if source == "household-initiative" else 1)
    assert completed[0].memory_search_context is None


@pytest.mark.parametrize("padding", ["", "m" * 10000])
def test_context_trim_and_metadata_hash_keep_exact_provenance_within8192(padding):
    facts = tuple({"fact_id": f"synthetic:{i}", "text": "é" * 1500} for i in range(8))
    def choose(kwargs, state):
        result = kwargs["private_context_search"]("a question")
        assert len(canonical_json_bytes(result)) <= 8192
        return decision()
    worker, state, queries, completed, failures, entered, resumed = harness(choose, facts=facts, padding=padding)
    assert worker.run_once(now=NOW) == "silence" and failures == []
    context = completed[0].memory_search_context
    assert context["result_count"] == len(facts)
    assert context["omitted_count"] == len(facts) - len(context["recalled_context"])
    assert context["truncated"] is True
    assert context["recalled_context"] == list(facts[:len(context["recalled_context"])])
    if padding:
        original = memory(queries[1], facts, padding).recall.model_dump(mode="json")
        assert context["recall"] == {"sha256": canonical_sha256(original), "metadata_omitted": True}


@pytest.mark.parametrize("context", [{"invalid": object()}, {"oversized": "x" * 8192}])
def test_decision_rejects_non_json_or_oversized_search_context(context):
    with pytest.raises(ValueError): decision(memory_search_context=context)


@pytest.mark.parametrize("measurement", ["measured", "partial", "unmeasured"])
def test_postmodel_guard_keeps_known_aggregate_usage_but_never_invents_missing_usage(measurement):
    from eva.application.conversation import ConversationModelUsage
    usage = (ConversationModelUsage.unmeasured() if measurement == "unmeasured" else
        getattr(ConversationModelUsage, measurement)(model_attempts=2, tool_calls=2,
            input_tokens=200, output_tokens=30))
    def choose(kwargs, state):
        kwargs["private_context_search"]("self-chosen topic")
        state["current"] = False  # The second response completed before this host fence changed.
        return decision(usage=usage)
    worker, state, queries, completed, failures, entered, resumed = harness(choose)
    assert worker.run_once(now=NOW) == "failed" and len(queries) == 2
    assert completed == [] and resumed == [True] and len(failures) == 1
    error = failures[0]
    if measurement == "measured":
        assert error.model_usage is usage
        assert error.validation_failure_stage == "sensory_decision"
        assert error.validation_failure_code == "private_context_changed_before_commit"
    else:
        assert not hasattr(error, "model_usage")
        assert not hasattr(error, "validation_failure_stage")
