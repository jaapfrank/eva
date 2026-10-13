from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.durable_goals import (
    DurableGoalError,
    DurableGoalSpec,
    GoalArtifact,
    GoalEvidence,
    GoalWakeCondition,
)


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


class Timeline:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    def create_thread(self, **values):  # type: ignore[no-untyped-def]
        self.rows.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )

    def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return tuple(self.rows)

    def load(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(revision=len(self.rows))

    def transition(self, **values):  # type: ignore[no-untyped-def]
        assert values["expected_revision"] == len(self.rows)
        self.rows.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )

    def list_checkpoints(self, **_values):  # type: ignore[no-untyped-def]
        return (SimpleNamespace(thread_id="goal:jaap:goal-rejected"),)


def spec(goal_id: str = "goal-1") -> DurableGoalSpec:
    return DurableGoalSpec(
        goal_id=goal_id,
        owner_id="jaap",
        description="Wacht op vrijgave en maak één resultaat",
        primary_instruction="Maak primair",
        pivot_instruction="Maak alternatief",
        suitability_field="route",
        unsuitable_value="blocked",
        delivery_recipient="sink-a",
        wake=GoalWakeCondition(
            "goal-test",
            "release-1",
            "ready",
            "yes",
            NOW + timedelta(hours=1),
        ),
        created_at=NOW,
    )


def store_with_timeline() -> tuple[PostgresDurableGoalStore, Timeline]:
    timeline = Timeline()
    store = object.__new__(PostgresDurableGoalStore)
    store._timeline = timeline  # type: ignore[attr-defined]
    return store, timeline


def test_store_persists_wake_pivot_effect_and_delivery_separately() -> None:
    store, timeline = store_with_timeline()
    goal = spec()
    evidence = GoalEvidence(
        "goal-test",
        "release-1",
        {"ready": "yes", "route": "blocked"},
        NOW + timedelta(minutes=1),
        "2",
    )
    artifact = GoalArtifact(
        "artifact-1", "Een inhoudelijk resultaat", "text/plain", NOW + timedelta(minutes=2)
    )

    assert store.admit(goal) == "admitted"
    assert store.schedule_wake(goal) == "scheduled"
    assert store.schedule_wake(goal) == "already_scheduled"
    assert store.record_wake(goal, evidence) == "recorded"
    assert store.record_wake(goal, evidence) == "already_recorded"
    assert store.record_pivot(goal, "Maak alternatief") == "pivoted"
    assert store.prepare_effect(goal, "Maak alternatief") == "prepared"
    assert store.record_effect(goal, artifact) == "recorded"
    assert store.prepare_delivery(goal, artifact) == "prepared"
    assert store.record_delivery(goal, artifact, "accepted", None) == "recorded"

    state = store.state(goal.goal_id, goal.owner_id)
    assert state is not None
    assert state.status == "completed"
    assert state.selected_instruction == "Maak alternatief"
    assert state.artifact == artifact
    assert [row.event_type for row in timeline.rows] == [
        "GOAL_ADMITTED",
        "GOAL_WAKE_SCHEDULED",
        "GOAL_EVIDENCE_READ",
        "GOAL_PLAN_PIVOTED",
        "GOAL_EFFECT_PREPARED",
        "GOAL_EFFECT_COMPLETED",
        "GOAL_DELIVERY_PREPARED",
        "GOAL_COMPLETED",
    ]


def test_cancelled_goal_cannot_later_execute() -> None:
    store, timeline = store_with_timeline()
    goal = spec("goal-cancel")
    store.admit(goal)
    store.schedule_wake(goal)

    assert store.cancel(goal.goal_id, goal.owner_id, "Niet meer nodig", NOW) == (
        "cancelled"
    )
    assert store.cancel(goal.goal_id, goal.owner_id, "Niet meer nodig", NOW) == (
        "already_cancelled"
    )
    with pytest.raises(DurableGoalError, match="terminal"):
        store.record_wake(
            goal,
            GoalEvidence(
                "goal-test", "release-1", {"ready": "yes"}, NOW, "3"
            ),
        )
    assert [row.event_type for row in timeline.rows] == [
        "GOAL_ADMITTED",
        "GOAL_WAKE_SCHEDULED",
        "GOAL_CANCELLED",
    ]


def test_rejected_delivery_is_terminally_distinct_from_cancelled() -> None:
    store, _ = store_with_timeline()
    goal = spec("goal-rejected")
    evidence = GoalEvidence(
        "goal-test", "release-1", {"ready": "yes"}, NOW, "1"
    )
    artifact = GoalArtifact("artifact-2", "Resultaat", "text/plain", NOW)
    store.admit(goal)
    store.schedule_wake(goal)
    store.record_wake(goal, evidence)
    store.prepare_effect(goal, goal.primary_instruction)
    store.record_effect(goal, artifact)
    store.prepare_delivery(goal, artifact)
    store.record_delivery(goal, artifact, "rejected", "recipient_rejected")

    state = store.state(goal.goal_id, goal.owner_id)
    assert state is not None
    assert state.status == "delivery_failed"
    assert state.delivery_status == "rejected"
    assert state.cancellation_reason is None

    assert store.waiting(observed_before=NOW + timedelta(minutes=1)) == ()
