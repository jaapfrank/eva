from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from eva.application.durable_goals import (
    DurableGoalSpec,
    DurableGoalState,
    DurableGoalWorker,
    GoalArtifact,
    GoalEvidence,
    GoalWakeCondition,
)


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


def goal(*, pivot: bool = False) -> DurableGoalSpec:
    return DurableGoalSpec(
        goal_id="goal-1",
        owner_id="jaap",
        description="Maak na vrijgave één resultaat",
        primary_instruction="Maak het oorspronkelijke resultaat",
        pivot_instruction="Maak het veilige alternatief" if pivot else None,
        suitability_field="route" if pivot else None,
        unsuitable_value="blocked" if pivot else None,
        delivery_recipient="sink-a",
        wake=GoalWakeCondition(
            provider="goal-test",
            resource="release-1",
            field="ready",
            expected_value="yes",
            deadline=NOW + timedelta(hours=1),
        ),
        created_at=NOW,
    )


class Store:
    def __init__(self) -> None:
        self.value: DurableGoalState | None = None
        self.events: list[str] = []

    def admit(self, spec: DurableGoalSpec) -> str:
        if self.value is None:
            self.value = DurableGoalState(spec, "waiting")
            self.events.append("admit")
        return "admitted"

    def state(self, goal_id: str, owner_id: str) -> DurableGoalState | None:
        return self.value

    def schedule_wake(self, spec: DurableGoalSpec) -> str:
        if "schedule" not in self.events:
            self.events.append("schedule")
        return "scheduled"

    def record_wake(self, spec: DurableGoalSpec, evidence: GoalEvidence) -> str:
        assert self.value is not None
        self.value = DurableGoalState(spec, "waiting", evidence=evidence)
        self.events.append("evidence")
        return "recorded"

    def record_pivot(self, spec: DurableGoalSpec, instruction: str) -> str:
        assert self.value is not None
        self.value = DurableGoalState(
            spec, "waiting", evidence=self.value.evidence, selected_instruction=instruction
        )
        self.events.append("pivot")
        return "pivoted"

    def prepare_effect(self, spec: DurableGoalSpec, instruction: str) -> str:
        self.events.append("effect_prepared")
        return "prepared"

    def record_effect(self, spec: DurableGoalSpec, artifact: GoalArtifact) -> str:
        assert self.value is not None
        self.value = DurableGoalState(
            spec,
            "waiting",
            evidence=self.value.evidence,
            selected_instruction=self.value.selected_instruction,
            artifact=artifact,
        )
        self.events.append("effect")
        return "recorded"

    def prepare_delivery(self, spec: DurableGoalSpec, artifact: GoalArtifact) -> str:
        self.events.append("delivery_prepared")
        return "prepared"

    def record_delivery(
        self,
        spec: DurableGoalSpec,
        artifact: GoalArtifact,
        status: str,
        reason: str | None,
    ) -> str:
        assert self.value is not None
        self.value = DurableGoalState(
            spec,
            "completed" if status == "accepted" else "delivery_failed",
            evidence=self.value.evidence,
            selected_instruction=self.value.selected_instruction,
            artifact=artifact,
            delivery_status=status,  # type: ignore[arg-type]
            delivery_reason=reason,
        )
        self.events.append("delivered")
        return "recorded"

    def cancel(self, goal_id: str, owner_id: str, reason: str, at: datetime) -> str:
        assert self.value is not None
        self.value = DurableGoalState(
            self.value.spec, "cancelled", cancellation_reason=reason
        )
        return "cancelled"

    def mark_needs_help(
        self, spec: DurableGoalSpec, reason: str, at: datetime
    ) -> str:
        assert self.value is not None
        self.value = DurableGoalState(spec, "needs_help", evidence=self.value.evidence)
        self.events.append("needs_help")
        return "needs_help"


class Evidence:
    def __init__(self, values: dict[str, str], at: datetime = NOW) -> None:
        self.value = GoalEvidence("goal-test", "release-1", values, at, "1")
        self.reads = 0

    async def read(self, condition: GoalWakeCondition) -> GoalEvidence:
        self.reads += 1
        return self.value


class Work:
    def __init__(self) -> None:
        self.effects = 0
        self.existing: GoalArtifact | None = None
        self.instructions: list[str] = []

    async def readback(
        self, *, goal: DurableGoalSpec, idempotency_key: str
    ) -> GoalArtifact | None:
        return self.existing

    async def execute(
        self,
        *,
        goal: DurableGoalSpec,
        instruction: str,
        evidence: GoalEvidence,
        idempotency_key: str,
    ) -> GoalArtifact:
        self.effects += 1
        self.instructions.append(instruction)
        self.existing = GoalArtifact("artifact-1", instruction, "text/plain", NOW)
        return self.existing


class Delivery:
    def __init__(self, status: str = "accepted") -> None:
        self.status = status
        self.posts = 0
        self.existing: tuple[str, str | None] | None = None

    async def readback(
        self, *, recipient: str, idempotency_key: str
    ) -> tuple[str, str | None] | None:
        return self.existing

    async def deliver(
        self,
        *,
        recipient: str,
        content: str,
        content_sha256: str,
        idempotency_key: str,
    ) -> tuple[str, str | None]:
        self.posts += 1
        self.existing = (
            ("accepted", None)
            if self.status == "accepted"
            else ("rejected", "recipient_rejected")
        )
        return self.existing


def test_goal_wakes_pivots_executes_and_delivers_once() -> None:
    store = Store()
    evidence = Evidence({"ready": "yes", "route": "blocked"})
    work = Work()
    delivery = Delivery()
    worker = DurableGoalWorker(store, evidence, work, delivery)

    first = asyncio.run(worker.on_wake(goal(pivot=True)))
    second = asyncio.run(worker.on_wake(goal(pivot=True)))

    assert first.status == second.status == "completed"
    assert work.instructions == ["Maak het veilige alternatief"]
    assert work.effects == delivery.posts == 1
    assert evidence.reads == 1
    assert "pivot" in store.events


def test_unmet_deadline_records_owned_blocker_without_effect() -> None:
    store = Store()
    evidence = Evidence(
        {"ready": "no"}, at=NOW + timedelta(hours=1)
    )
    work = Work()
    delivery = Delivery()

    state = asyncio.run(
        DurableGoalWorker(store, evidence, work, delivery).on_wake(goal())
    )

    assert state.status == "needs_help"
    assert work.effects == delivery.posts == 0
    assert store.events[-1] == "needs_help"


def test_unchanged_provider_revision_is_polled_without_timeline_spam() -> None:
    store = Store()
    evidence = Evidence({"ready": "no"}, at=NOW + timedelta(minutes=1))
    worker = DurableGoalWorker(store, evidence, Work(), Delivery())

    first = asyncio.run(worker.on_wake(goal()))
    evidence.value = GoalEvidence(
        "goal-test",
        "release-1",
        {"ready": "no"},
        NOW + timedelta(minutes=2),
        "1",
    )
    second = asyncio.run(worker.on_wake(goal()))

    assert first.status == second.status == "waiting"
    assert evidence.reads == 2
    assert store.events.count("evidence") == 1


def test_delivery_rejection_is_not_cancellation_or_success() -> None:
    store = Store()
    state = asyncio.run(
        DurableGoalWorker(
            store,
            Evidence({"ready": "yes"}),
            Work(),
            Delivery("rejected"),
        ).on_wake(goal())
    )

    assert state.status == "delivery_failed"
    assert state.delivery_status == "rejected"
    assert state.cancellation_reason is None


def test_cancelled_goal_creates_no_effect_or_delivery() -> None:
    store = Store()
    spec = goal()
    store.admit(spec)
    store.schedule_wake(spec)
    store.cancel(spec.goal_id, spec.owner_id, "Niet meer nodig", NOW)
    work = Work()
    delivery = Delivery()

    state = asyncio.run(
        DurableGoalWorker(store, Evidence({"ready": "yes"}), work, delivery).on_wake(
            spec
        )
    )

    assert state.status == "cancelled"
    assert work.effects == delivery.posts == 0
