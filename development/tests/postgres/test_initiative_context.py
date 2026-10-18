"""Real isolated PostgreSQL context selection; no external or model calls."""
from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.initiative_context import read_private_initiative_context
from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.durable_goals import DurableGoalSpec, GoalWakeCondition
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


def _store(engine, label):
    return PostgresSensoryEventStore(engine, tenant_id=label, installation_id="home")


def _observe(store, label, entity="sensor.temperature", at=NOW, state="20"):
    source_id = "home-assistant:" + hashlib.sha256(label.encode()).hexdigest()
    event = SensoryEvent("home_assistant", source_id, "state_changed", at,
        {"entity_id": entity, "friendly_name": "Synthetic room", "new_state": state,
         "device_class": "temperature", "unit_of_measurement": "C",
         "attributes": {"access_token": "synthetic-secret"}, "camera_bytes": "private-frame"})
    store.retain(event)
    return "sensory:" + source_id


def _read(engine, store, owner="owner", now=NOW):
    with engine.connect() as connection:
        store._timeline._scope(connection)
        result = read_private_initiative_context(connection, store._timeline._scope_value, owner, now)
        connection.rollback()
    return result


def _goal(engine, label, goal_id, owner="owner", title="An existing owner commitment"):
    goal = DurableGoalSpec(goal_id, owner, title, "Existing user instruction", None, None, None,
        "configured-private-owner", GoalWakeCondition("synthetic", "resource", "ready", "yes",
            NOW + timedelta(days=1)), NOW - timedelta(hours=1))
    store = PostgresDurableGoalStore(engine, tenant_id=label, installation_id="home")
    store.admit(goal)
    store.schedule_wake(goal)
    return store, goal


def test_context_uses_latest_retained_entity_but_never_implies_live_state(postgres_engine):
    store = _store(postgres_engine, "context-retained")
    _observe(store, "previous", at=NOW - timedelta(minutes=30), state="18")
    _observe(store, "latest", at=NOW - timedelta(minutes=10), state="21")
    _observe(store, "old", entity="sensor.old", at=NOW - timedelta(hours=25))
    _observe(store, "future", entity="sensor.future", at=NOW + timedelta(minutes=1))
    _observe(_store(postgres_engine, "context-other-tenant"), "other", entity="sensor.other")
    result = _read(postgres_engine, store)
    assert len(result["observations"]) == 1
    observation = result["observations"][0]
    assert observation["state"] == "21"
    assert observation["observed_at"] == (NOW - timedelta(minutes=10)).isoformat()
    assert observation["historical"] is True and observation["is_current_proof"] is False
    assert result["timezone"] == "Europe/Amsterdam"
    assert result["local_time"] == "2026-10-14T12:00:00+02:00"
    assert result["observed_at_utc"] == NOW.isoformat()
    assert "synthetic-secret" not in json.dumps(result) and "private-frame" not in json.dumps(result)
    assert "live_calendar_events" in result["coverage"]["missing"]


def test_only_exact_owner_commitment_state_is_snapshotted_without_resume(postgres_engine):
    label = "context-commitments"
    store = _store(postgres_engine, label)
    _observe(store, label)
    goals, owner = _goal(postgres_engine, label, "goal-owner")
    _goal(postgres_engine, label, "goal-other", owner="other", title="Other person's private text")
    goals.cancel(owner.goal_id, owner.owner_id, "Stopped by owner", NOW)
    before = goals._timeline.events(goals._thread_id(owner.goal_id, owner.owner_id))
    result = _read(postgres_engine, store)
    assert len(result["own_work"]) == 1
    work = result["own_work"][0]
    assert work["goal_id"] == "goal-owner"
    assert work["phase"] == "goal_cancelled" and work["status"] == "completed"
    assert work["is_execution_permission"] is False
    assert "Other person's private text" not in json.dumps(result)
    assert "Existing user instruction" not in json.dumps(result)
    assert "ordinary_delegated_work_requires_separate_owner_binding" in result["coverage"]["missing"]
    assert goals._timeline.events(goals._thread_id(owner.goal_id, owner.owner_id)) == before


def test_context_is_bounded_and_marks_missing_or_truncated_coverage(postgres_engine):
    label = "context-bounded"
    store = _store(postgres_engine, label)
    for i in range(20):
        _observe(store, f"bounded-{i}", entity=f"sensor.item_{i}", at=NOW - timedelta(seconds=i))
    for i in range(6):
        _goal(postgres_engine, label, f"goal-{i}", title="Synthetic description " * 30)
    result = _read(postgres_engine, store)
    assert len(result["observations"]) <= 8 and len(result["own_work"]) <= 4
    assert len(canonical_json_bytes(result)) <= 8192
    assert result["coverage"]["observations_truncated"] is True
    assert result["coverage"]["commitments_truncated"] is True
    assert result["coverage"]["observations_are_current_proof"] is False
    assert _read(postgres_engine, store, owner="unbound-other")["own_work"] == []


def test_invalid_retained_observation_hash_stops_context_before_a_model_call(postgres_engine):
    store = _store(postgres_engine, "context-bad-hash")
    thread = _observe(store, "bad-hash")
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
        try:
            connection.execute(sa.update(operational_events).where(
                operational_events.c.thread_id == thread).values(payload_sha256=b"\0" * 32))
        finally:
            connection.exec_driver_sql("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")
    with pytest.raises(RuntimeError, match="source hash differs"):
        _read(postgres_engine, store)


def test_changed_commitment_projection_is_not_accepted_as_current_state(postgres_engine):
    label = "context-bad-projection"
    store = _store(postgres_engine, label)
    _observe(store, label)
    goals, goal = _goal(postgres_engine, label, "goal-projection")
    with postgres_engine.begin() as connection:
        connection.execute(sa.update(operational_checkpoints).where(
            operational_checkpoints.c.thread_id == goals._thread_id(goal.goal_id, goal.owner_id)
        ).values(phase="invented_phase"))
    with pytest.raises(RuntimeError, match="projection differs"):
        _read(postgres_engine, store)


def test_unknown_clock_is_rejected_and_empty_sources_remain_explicit(postgres_engine):
    store = _store(postgres_engine, "context-empty")
    _observe(store, "expired-only", at=NOW - timedelta(days=2))
    result = _read(postgres_engine, store)
    assert result["observations"] == result["own_work"] == []
    assert "unretained_household_observations" in result["coverage"]["missing"]
    with pytest.raises(ValueError, match="timezone-aware"):
        _read(postgres_engine, store, now=NOW.replace(tzinfo=None))


def test_old_closed_goals_do_not_displace_live_commitments_or_erase_history(postgres_engine):
    label = "context-old-closed"
    store = _store(postgres_engine, label)
    goals, closed = _goal(postgres_engine, label, "old-closed")
    goals.cancel(closed.goal_id, closed.owner_id, "Stopped by owner", NOW)
    _, active = _goal(postgres_engine, label, "still-active")
    before = goals._timeline.events(goals._thread_id(closed.goal_id, closed.owner_id))
    result = _read(postgres_engine, store, now=NOW + timedelta(days=2))
    assert [x["goal_id"] for x in result["own_work"]] == [active.goal_id]
    assert "older closed history retained" in result["coverage"]["commitment_selection"]
    assert goals._timeline.events(goals._thread_id(closed.goal_id, closed.owner_id)) == before


def test_old_needs_help_is_still_unfulfilled_not_silently_closed(postgres_engine):
    label = "context-old-needs-help"
    store = _store(postgres_engine, label)
    goals, goal = _goal(postgres_engine, label, "unfulfilled")
    goals.mark_needs_help(goal, "A real unresolved blocker", NOW)
    result = _read(postgres_engine, store, now=NOW + timedelta(days=2))
    assert result["own_work"][0]["goal_id"] == goal.goal_id
    assert result["own_work"][0]["status"] == "needs_help"
    assert result["own_work"][0]["is_execution_permission"] is False
