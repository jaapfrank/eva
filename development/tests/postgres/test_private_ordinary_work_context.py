"""Current ordinary-work metadata joins an immutable private review, never execution."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from eva.adapters.postgres.initiative_context import read_private_initiative_context
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_household_observation_initiative import _binding
from tests.postgres.test_initiative_context import NOW, _observe, _goal


def _snapshot(now=NOW):
    return {"available": True, "source": "current_private_owner_work_store", "as_of": now.isoformat(),
        "coverage": "Bounded retained private digital sources; tasks without a private source are not covered.",
        "jobs": [{"work_id": "work:ordinary-" + str(i), "title": "An existing private project",
            "state": state, "spec_sha256": str(i + 1) * 64,
            "source_ingress_sha256": str(i + 4) * 64, "source_conversation_kind": "telegram",
            "result_available": state == "succeeded",
            "earlier_attempts": i, "earlier_uncertain_attempts": min(i, 1)}
            for i, state in enumerate(("succeeded", "failed", "waiting_for_approval"))],
        "truncated": False, "is_execution_permission": False}


def _store(engine, label, callback=None, authorizer=None):
    return PostgresSensoryEventStore(engine, tenant_id=label, installation_id="home",
        initiative_principal_id="owner", initiative_authorizer=authorizer or (lambda: ({}, "a" * 64)),
        context_reviews_only=True, private_work_context_reader=callback)


def _capture(store, now=NOW):
    assert store.schedule_context_review(now=now) == "scheduled"
    state = store._timeline.list_checkpoints(thread_prefix=store._decision_prefix)[0]
    event = store._timeline.events(state.thread_id)[0]
    return state, event, event.payload["data"]["payload"]["life_context"]


def _direct(store, value):
    with store._timeline._engine.connect() as connection:
        store._timeline._scope(connection)
        result = read_private_initiative_context(connection, store._timeline._scope_value,
            "owner", NOW, ordinary_work=value)
        connection.rollback()
    return result


def test_three_current_records_are_captured_once_and_source_stays_immutable(postgres_engine):
    supplied = _snapshot()
    calls = []
    def read(*, now):
        calls.append(now)
        return supplied
    store = _store(postgres_engine, "ordinary-immutable", read)
    state, event, context = _capture(store)
    assert calls == [NOW]
    assert context["ordinary_work"]["jobs"] == supplied["jobs"]
    assert context["ordinary_work"]["jobs"][2]["earlier_attempts"] == 2
    assert context["ordinary_work"]["jobs"][2]["earlier_uncertain_attempts"] == 1
    assert context["ordinary_work"]["available"] is True
    assert context["ordinary_work"]["is_execution_permission"] is False
    assert context["ordinary_work"]["coverage"] == supplied["coverage"]
    assert context["coverage"]["ordinary_work_available"] is True
    assert not any("ordinary_delegated_work" in x for x in context["coverage"]["missing"])
    original_hash = event.payload_sha256
    supplied["jobs"][0]["state"] = "invented_later_state"
    assert store.schedule_context_review(now=NOW) == "idle"
    assert calls == [NOW]
    original = store._timeline.events(state.thread_id)[0]
    assert original.payload_sha256 == original_hash
    assert original.payload["data"]["payload"]["life_context"]["ordinary_work"]["jobs"][0]["state"] == "succeeded"


@pytest.mark.parametrize("failure", ["raises", "none", "list", "unavailable", "source", "spec_hash", "ingress_hash", "source_kind"])
def test_unavailable_or_malformed_callback_is_unknown_not_empty_work(postgres_engine, failure):
    value = _snapshot()
    def read(*, now):
        if failure == "raises":
            raise RuntimeError("private diagnostic detail must never enter the review")
        if failure == "none": return None
        if failure == "list": return []
        if failure == "unavailable": return {"available": False, "jobs": [], "reason": "private detail"}
        if failure == "source": value["source"] = "unbound"
        if failure == "spec_hash": value["jobs"][0]["spec_sha256"] = "not-a-proof"
        if failure == "ingress_hash": value["jobs"][0]["source_ingress_sha256"] = "not-a-proof"
        if failure == "source_kind": value["jobs"][0]["source_conversation_kind"] = "household"
        return value
    store = _store(postgres_engine, "ordinary-unavailable-" + failure, read)
    _, _, context = _capture(store)
    assert context["ordinary_work"] == {"available": False,
        "reason": "private_work_context_unavailable", "is_execution_permission": False}
    assert context["coverage"]["ordinary_work_available"] is False
    assert "ordinary_delegated_work_current_status_unknown" in context["coverage"]["missing"]
    assert "jobs" not in context["ordinary_work"]
    assert "private detail" not in json.dumps(context)
    assert "private diagnostic detail" not in json.dumps(context)


@pytest.mark.parametrize("mode", ["not_callable", "ordinary_sensory", "household"])
def test_context_reader_cannot_be_attached_to_unbound_or_household_store(postgres_engine, mode):
    kwargs = {"tenant_id": "ordinary-constructor", "installation_id": "home",
        "initiative_principal_id": "owner", "initiative_authorizer": lambda: ({}, "a" * 64),
        "context_reviews_only": mode != "ordinary_sensory",
        "private_work_context_reader": False if mode == "not_callable" else lambda **kw: _snapshot()}
    if mode == "household": kwargs["household_binding"] = _binding("ordinary-constructor")
    with pytest.raises(ValueError, match="private work context"):
        PostgresSensoryEventStore(postgres_engine, **kwargs)


def test_combined_snapshot_preserves_work_proof_within_original_byte_budget(postgres_engine):
    store = _store(postgres_engine, "ordinary-bounded")
    for i in range(20):
        _observe(store, "ordinary-bound-" + str(i), entity="sensor.bound_" + str(i),
            at=NOW-timedelta(seconds=i))
    for i in range(6):
        _goal(postgres_engine, "ordinary-bounded", "ordinary-goal-" + str(i),
            title="An already authorized durable commitment " * 20)
    supplied = _snapshot()
    for job in supplied["jobs"]: job["bounded_metadata"] = "x" * 1000
    context = _direct(store, supplied)
    assert len(canonical_json_bytes(context)) <= 8192
    assert context["ordinary_work"]["available"] is True
    assert len(context["ordinary_work"]["jobs"]) == 3
    assert context["ordinary_work"]["jobs"][0]["spec_sha256"] == "1" * 64
    assert context["coverage"]["byte_budget_truncated"] is True


def test_revoked_authorizer_stops_before_reading_any_work(postgres_engine):
    calls = []
    def denied(): raise ValueError("revoked")
    store = _store(postgres_engine, "ordinary-revoked", lambda **kw: calls.append(kw), denied)
    assert store.schedule_context_review(now=NOW) == "mandate_unavailable"
    assert calls == []


@pytest.mark.parametrize("delta", [-1, 1])
def test_stale_or_future_work_context_is_not_current_coverage(postgres_engine, delta):
    store = _store(postgres_engine, "ordinary-asof-" + str(delta))
    context = _direct(store, _snapshot(NOW + timedelta(seconds=delta)))
    assert context["ordinary_work"]["available"] is False


def test_successfully_read_empty_work_is_distinct_from_unknown(postgres_engine):
    store = _store(postgres_engine, "ordinary-known-empty")
    value = _snapshot(); value["jobs"] = []
    context = _direct(store, value)
    assert context["ordinary_work"]["available"] is True
    assert context["ordinary_work"]["jobs"] == []
    assert context["coverage"]["ordinary_work_available"] is True


def test_extra_envelope_fields_are_not_copied_and_coverage_is_bounded(postgres_engine):
    store = _store(postgres_engine, "ordinary-coverage")
    value = _snapshot(); value["opaque_private_data"] = "must-not-be-copied"; value["coverage"] = "scope " * 100
    context = _direct(store, value)
    assert len(context["ordinary_work"]["coverage"]) == 160
    assert "must-not-be-copied" not in json.dumps(context)


def test_none_preserves_previous_unknown_coverage_shape(postgres_engine):
    store = _store(postgres_engine, "ordinary-legacy-none")
    context = _direct(store, None)
    assert "ordinary_work" not in context
    assert "ordinary_work_available" not in context["coverage"]
    assert "ordinary_delegated_work_requires_separate_owner_binding" in context["coverage"]["missing"]


def test_more_than_three_jobs_are_bounded_without_granting_execution(postgres_engine):
    store = _store(postgres_engine, "ordinary-job-limit")
    value = _snapshot(); value["jobs"].append(dict(value["jobs"][0], work_id="work:extra"))
    value["is_execution_permission"] = True
    context = _direct(store, value)
    assert len(context["ordinary_work"]["jobs"]) == 3
    assert context["ordinary_work"]["truncated"] is True
    assert context["ordinary_work"]["is_execution_permission"] is False
