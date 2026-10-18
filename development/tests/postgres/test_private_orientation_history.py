"""Private measured decisions become bounded historical context, never authority."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.initiative_context import read_private_initiative_context
from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import ConversationModelUsage
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
from eva.application.resident_attention import NextAttention
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_initiative_memory_search import store

USAGE = ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=20, output_tokens=10)


@pytest.fixture
def private(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    return store(auth, old, "private-orientation-continuity-v3"), auth, old, owner


def complete(source, at=NOW, *, reason="An earlier self-chosen consideration", usage=USAGE,
             initiative=None, attention=None):
    assert source.schedule_context_review(now=at) == "scheduled"
    claim = source.mark_model_started(source.claim_next(now=at), now=at)
    source.complete(claim, FreeWillDecision("silence", reason, 0, "synthetic-measured-model",
        usage=usage, initiative=initiative, next_attention=attention), now=at)
    return claim


def read(source, at=NOW, *, principal=None, ordinary_work=None):
    # Same current-owner gate as the production capture caller; the pure reader
    # does not create an independent authority mechanism.
    source._initiative_authorizer()
    with source._timeline._engine.begin() as connection:
        source._timeline._scope(connection)
        return read_private_initiative_context(connection, source._timeline._scope_value,
            principal or source._initiative_principal_id, at, ordinary_work=ordinary_work)


def rewrite_event(source, thread, event_type, change, *, rehash=True):
    e = operational_events.c
    with source._timeline._engine.begin() as connection:
        row = connection.execute(sa.select(e.payload).where(
            e.tenant_id == source._timeline._scope_value.tenant_id,
            e.installation_id == source._timeline._scope_value.installation_id,
            e.thread_id == thread, e.event_type == event_type)).one()
        payload = deepcopy(row.payload)
        change(payload)
        values = {"payload": payload}
        if rehash:
            values["payload_sha256"] = bytes.fromhex(canonical_sha256(payload))
        connection.exec_driver_sql("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
        try:
            connection.execute(sa.update(operational_events).where(
                e.tenant_id == source._timeline._scope_value.tenant_id,
                e.installation_id == source._timeline._scope_value.installation_id,
                e.thread_id == thread, e.event_type == event_type).values(**values))
        finally:
            connection.exec_driver_sql("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")


def test_measured_silence_reaches_next_snapshot_with_exact_provenance_no_recursive_context(private):
    source, _, _, _ = private
    reason = "A self-chosen consideration " * 15
    claim = complete(source, reason=reason)
    history_before = source._timeline.events(claim.thread_id)
    at = NOW + timedelta(hours=1)
    assert source.schedule_context_review(now=at) == "scheduled"
    following = source.claim_next(now=at)
    values = following.event.payload["life_context"]["own_orientation_history"]
    assert len(values) == 1
    item = values[0]
    assert item["reason"] == reason[:300]
    assert item["reason_sha256"] == hashlib.sha256(reason.encode()).hexdigest()
    assert item["reason_truncated"] is True
    assert item["disposition"] == "silence" and item["initiative_status"] is None
    assert item["next_attention"] is None
    assert item["source_sha256"] == history_before[0].payload_sha256
    assert item["decision_sha256"] == history_before[-1].payload_sha256
    assert item["decision_sequence"] == history_before[-1].sequence
    assert item["source_event_id"] == claim.event.source_event_id
    assert item["decided_at"] == NOW.isoformat()
    assert item["classification"] == "historical_model_decision_not_owner_preference_or_execution_permission"
    assert "life_context" not in item and "memory_search_context" not in item and "expression" not in item
    assert "feedback" not in json.dumps(item)
    assert source._timeline.events(claim.thread_id) == history_before


def test_three_recent_decisions_newest_first_and_48h_and_future_excluded(private):
    source, _, _, _ = private
    for i in range(4):
        complete(source, NOW + timedelta(hours=i), reason=f"Historical consideration {i}")
    snapshot = read(source, NOW + timedelta(hours=3))
    assert [x["reason"] for x in snapshot["own_orientation_history"]] == [
        "Historical consideration 3", "Historical consideration 2", "Historical consideration 1"]
    assert snapshot["coverage"]["own_orientation_history_truncated"] is True
    assert read(source, NOW + timedelta(hours=52))["own_orientation_history"] == []
    assert read(source, NOW - timedelta(seconds=1))["own_orientation_history"] == []
    assert read(source, principal="another-current-owner")["own_orientation_history"] == []


def test_other_tenant_and_passive_household_decisions_are_not_private_history(private):
    source, _, old, _ = private
    complete(source)
    other = PostgresSensoryEventStore(source._timeline._engine, tenant_id="another-tenant",
        installation_id=old.installation_id, initiative_principal_id=old.principal_id,
        initiative_authorizer=lambda: ({}, "0" * 64), context_reviews_only=True)
    assert read(other)["own_orientation_history"] == []
    # A passive sensory decision in the same tenant is not a private orientation.
    passive = PostgresSensoryEventStore(source._timeline._engine, tenant_id=old.tenant_id,
        installation_id=old.installation_id)
    passive.retain(SensoryEvent("home_assistant", "home-assistant:synthetic-passive",
        "state_changed", NOW, {"entity_id": "light.synthetic", "new_state": "on"}))
    claim = passive.mark_model_started(passive.claim_next(now=NOW), now=NOW)
    passive.complete(claim, FreeWillDecision("silence", "Passive household consideration", 0,
        "synthetic-measured-model", usage=USAGE), now=NOW)
    assert [x["reason"] for x in read(source)["own_orientation_history"]] == ["An earlier self-chosen consideration"]


@pytest.mark.parametrize("usage", [None, ConversationModelUsage.unmeasured(),
    ConversationModelUsage.partial(model_attempts=1, tool_calls=0, input_tokens=0, output_tokens=0)])
def test_unmeasured_or_deterministic_budget_outcomes_are_not_model_history(private, usage):
    source, _, _, _ = private
    complete(source, usage=usage)
    assert read(source)["own_orientation_history"] == []


def test_zero_attempt_marker_is_not_a_completed_model_decision(private):
    source, _, _, _ = private
    claim = complete(source)
    rewrite_event(source, claim.thread_id, "WILL_DECIDED",
        lambda p: p["data"]["usage"].update(model_attempts=0))
    assert read(source)["own_orientation_history"] == []


def test_unknown_or_invalid_or_abandoned_does_not_become_valid_history(private):
    source, _, _, _ = private
    assert source.schedule_context_review(now=NOW) == "scheduled"
    claim = source.mark_model_started(source.claim_next(now=NOW), now=NOW)
    source.fail(claim, RuntimeError("Unknown provider response"), now=NOW)
    before = source._timeline.events(claim.thread_id)
    assert read(source)["own_orientation_history"] == []
    assert source._timeline.events(claim.thread_id) == before
    # A projection alone cannot turn an unknown or abandoned/invalid outcome into
    # a completed model decision: a latest WILL_DECIDED event is still required.
    for phase in ["will_abandoned", "will_decision_rejected", "will_decided"]:
        with source._timeline._engine.begin() as connection:
            connection.execute(sa.update(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == source._timeline._scope_value.tenant_id,
                operational_checkpoints.c.installation_id == source._timeline._scope_value.installation_id,
                operational_checkpoints.c.thread_id == claim.thread_id).values(phase=phase, status="completed"))
        assert read(source)["own_orientation_history"] == []


@pytest.mark.parametrize("target,change,rehash,message", [
    ("SENSORY_EVENT_OBSERVED", lambda p: p["data"].update(source="household-initiative"), False, "source hash differs"),
    ("WILL_DECIDED", lambda p: p["data"].update(reason="Changed reason"), False, "source hash differs"),
    ("SENSORY_EVENT_OBSERVED", lambda p: p["data"].update(event_type="household_opportunity_review"), True, "source binding differs"),
    ("WILL_DECIDED", lambda p: p["data"].update(source_event_id="initiative-review:another:1"), True, "source binding differs"),
    ("WILL_DECIDED", lambda p: p["checkpoint"].update(revision=True), True, "checkpoint differs"),
])
def test_hash_binding_and_typed_checkpoint_corruption_stop_capture(private, target, change, rehash, message):
    source, _, _, _ = private
    claim = complete(source)
    rewrite_event(source, claim.thread_id, target, change, rehash=rehash)
    with pytest.raises(RuntimeError, match=message):
        read(source)


def test_changed_current_checkpoint_is_not_authoritative_history(private):
    source, _, _, _ = private
    claim = complete(source)
    with source._timeline._engine.begin() as connection:
        connection.execute(sa.update(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == source._timeline._scope_value.tenant_id,
            operational_checkpoints.c.installation_id == source._timeline._scope_value.installation_id,
            operational_checkpoints.c.thread_id == claim.thread_id).values(operation_id="changed-binding"))
    with pytest.raises(RuntimeError, match="checkpoint differs"):
        read(source)


def test_attention_and_adoption_status_remain_historical_without_resuming_stopped_work(private):
    source, _, _, owner = private
    claim = complete(source, initiative=PersonalInitiativeProposal("A distinct synthetic intention",
        "A self-chosen hypothesis", "Check a synthetic retained source"),
        attention=NextAttention(NOW + timedelta(hours=2), "Revisit the original question"))
    stored = source._timeline.events(claim.thread_id)[-1].payload["data"]
    owner.stop(stored["initiative"]["intent_id"], now=NOW)
    before = source._timeline.events(claim.thread_id)
    item = read(source)["own_orientation_history"][0]
    assert item["initiative_status"] == "adopted"  # Original admission, not a current work status.
    assert item["next_attention"] == stored["next_attention"]
    assert source._timeline.events(claim.thread_id) == before
    assert owner.page(now=NOW)["cards"][0]["status"] == "stopped"


def test_oldest_history_is_dropped_before_real_commitments_under_total_budget(private, monkeypatch):
    from tests.postgres import test_initiative_context as donor
    monkeypatch.setattr(donor, "NOW", NOW)
    _goal = donor._goal
    source, _, old, _ = private
    for i in range(3):
        complete(source, NOW + timedelta(hours=i), reason=str(i) + "é" * 550)
    for i in range(4):
        _goal(source._timeline._engine, old.tenant_id, f"retained-obligation-{i}",
            owner=old.principal_id, title="A real retained commitment " * 12)
    at = NOW + timedelta(hours=2)
    # Separate current-owner work metadata can fill the same existing envelope.
    ordinary = {"available": True, "source": "current_private_owner_work_store", "as_of": at.isoformat(),
        "jobs": [{"work_id": f"work:{i}", "spec_sha256": "a"*64, "source_ingress_sha256": str(i)*64,
                  "source_conversation_kind": "telegram", "title": "Current private work " * 55} for i in range(3)]}
    result = read(source, at, ordinary_work=ordinary)
    assert len(canonical_json_bytes(result)) <= 8192
    assert result["coverage"]["own_orientation_history_truncated"] is True
    assert result["coverage"]["byte_budget_truncated"] is True
    assert len(result["own_work"]) == 4 and result["coverage"]["commitments_truncated"] is False
    assert len(result["own_orientation_history"]) < 3


def test_current_owner_revocation_blocks_the_existing_capture_gate(private):
    source, auth, _, _ = private
    complete(source)
    table = __import__("eva.adapters.postgres.current_catalog", fromlist=["metadata"]).metadata.tables["eva_core.principal_bindings"]
    with source._timeline._engine.begin() as connection:
        changed = connection.execute(sa.update(table).where(table.c.tenant_id == auth.owner._authority.tenant_id,
            table.c.principal_binding_id == auth.owner._binding).values(status="revoked"))
        assert changed.rowcount == 1
    with pytest.raises(RuntimeError, match="binding is not current"):
        read(source)


def test_old_household_observations_yield_space_before_private_decision_history(private):
    from tests.postgres.test_initiative_context import _observe
    source, _, old, _ = private
    complete(source, reason="A real earlier consideration " * 15)
    passive = PostgresSensoryEventStore(source._timeline._engine,
        tenant_id=old.tenant_id, installation_id=old.installation_id)
    for i in range(8):
        _observe(passive, f"orientation-space-{i}", entity=f"sensor.synthetic_{i}",
            at=NOW - timedelta(seconds=i))
    ordinary = {"available": True, "source": "current_private_owner_work_store", "as_of": NOW.isoformat(),
        "jobs": [{"work_id": f"work:{i}", "spec_sha256": "a"*64, "source_ingress_sha256": str(i)*64,
                  "source_conversation_kind": "telegram", "title": "Current private work " * 55} for i in range(3)]}
    result = read(source, ordinary_work=ordinary)
    assert len(canonical_json_bytes(result)) <= 8192
    assert result["coverage"]["observations_truncated"] is True
    assert result["coverage"]["byte_budget_truncated"] is True
    assert len(result["own_orientation_history"]) == 1
    assert result["coverage"]["own_orientation_history_truncated"] is False
