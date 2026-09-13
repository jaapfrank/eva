"""Append-only PostgreSQL repository for bounded durable goals."""

from __future__ import annotations

from datetime import datetime
import hashlib

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    external_timeline_scope,
    PostgresOperationalTimeline,
    TimelineEvent,
)
from eva.application.durable_goals import (
    DurableGoalError,
    DurableGoalSpec,
    DurableGoalState,
    DurableGoalWorker,
    GoalArtifact,
    GoalEvidence,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_TERMINAL_EVENTS = {
    "GOAL_CANCELLED",
    "GOAL_COMPLETED",
    "GOAL_DELIVERY_FAILED",
    "GOAL_NEEDS_HELP",
}


class PostgresDurableGoalStore:
    def __init__(
        self, engine: sa.Engine, *, tenant_id: str, installation_id: str
    ) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            external_timeline_scope(
                tenant_id, installation_id, "durable goal store scope is required"
            ),
        )

    @staticmethod
    def _thread_id(goal_id: str, owner_id: str) -> str:
        identity = {"goal_id": goal_id, "owner_id": owner_id}
        return "goal:" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()

    def admit(self, goal: DurableGoalSpec) -> str:
        thread_id = self._thread_id(goal.goal_id, goal.owner_id)
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"checkpoint:{thread_id}:admitted",
                occurred_at=goal.created_at,
                data=goal.to_payload(),
                event_type="GOAL_ADMITTED",
                phase="goal_admitted",
                status="active",
                operation_id=goal.goal_id,
            )
        except CheckpointConflict:
            durable = self.state(goal.goal_id, goal.owner_id)
            if durable is None or durable.spec != goal:
                raise DurableGoalError("durable goal identity collision") from None
            return "already_admitted"
        return "admitted"

    def state(self, goal_id: str, owner_id: str) -> DurableGoalState | None:
        events = self._timeline.events(self._thread_id(goal_id, owner_id))
        if not events:
            return None
        if events[0].event_type != "GOAL_ADMITTED":
            raise DurableGoalError("durable goal admission is missing")
        spec = DurableGoalSpec.from_payload(self._data(events[0]))
        evidence_event = self._last(events, "GOAL_EVIDENCE_READ")
        pivot_event = self._last(events, "GOAL_PLAN_PIVOTED")
        first_event = self._last(events, "GOAL_EFFECT_COMPLETED")
        artifact_event = (first_event if spec.followup_instruction is None else
                          self._last(events, "GOAL_FOLLOWUP_COMPLETED"))
        delivery_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type in {"GOAL_COMPLETED", "GOAL_DELIVERY_FAILED"}
            ),
            None,
        )
        cancelled = self._last(events, "GOAL_CANCELLED")
        needs_help = self._last(events, "GOAL_NEEDS_HELP")
        status: str = "waiting"
        cancellation_reason: str | None = None
        delivery_status = None
        delivery_reason = None
        if cancelled is not None:
            status = "cancelled"
            reason = self._data(cancelled).get("reason")
            cancellation_reason = reason if isinstance(reason, str) else None
        elif needs_help is not None:
            status = "needs_help"
        elif delivery_event is not None:
            data = self._data(delivery_event)
            raw_status = data.get("status")
            raw_reason = data.get("reason")
            if raw_status not in {"accepted", "rejected"}:
                raise DurableGoalError("durable goal delivery result is malformed")
            delivery_status = raw_status
            delivery_reason = raw_reason if isinstance(raw_reason, str) else None
            status = (
                "completed" if delivery_event.event_type == "GOAL_COMPLETED"
                else "delivery_failed"
            )
        selected_instruction = spec.primary_instruction
        if pivot_event is not None:
            raw = self._data(pivot_event).get("selected_instruction")
            if not isinstance(raw, str) or raw != spec.pivot_instruction:
                raise DurableGoalError("durable goal pivot is malformed")
            selected_instruction = raw
        return DurableGoalState(
            spec=spec,
            status=status,  # type: ignore[arg-type]
            evidence=(
                None
                if evidence_event is None
                else GoalEvidence.from_payload(self._data(evidence_event).get("evidence"))
            ),
            selected_instruction=selected_instruction,
            artifact=(
                None
                if artifact_event is None
                else GoalArtifact.from_payload(self._data(artifact_event).get("artifact"))
            ),
            delivery_status=delivery_status,
            delivery_reason=delivery_reason,
            cancellation_reason=cancellation_reason,
            first_artifact=(None if first_event is None else
                GoalArtifact.from_payload(self._data(first_event).get("artifact"))),
            effect_dispatch_reservations=sum(
                event.event_type == "GOAL_EFFECT_DISPATCH_RESERVED" for event in events
            ),
        )

    def document_plan_binding(self, goal: DurableGoalSpec) -> tuple[str, str] | None:
        event = self._last(self._events(goal), "GOAL_DOCUMENT_PLAN_BOUND")
        if event is None:
            return None
        data = self._data(event)
        if set(data) != {"goal_sha256", "plan_sha256", "authority_configuration_sha256"}:
            raise DurableGoalError("document plan binding is malformed")
        if data["goal_sha256"] != goal.sha256:
            raise DurableGoalError("document plan goal binding differs")
        values = (data["plan_sha256"], data["authority_configuration_sha256"])
        for value in values:
            if (not isinstance(value, str) or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)):
                raise DurableGoalError("document plan digest is invalid")
        return values

    def bind_document_plan(
        self, goal: DurableGoalSpec, plan_sha256: str, authority_configuration_sha256: str
    ) -> str:
        for value in (plan_sha256, authority_configuration_sha256):
            if (not isinstance(value, str) or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)):
                raise DurableGoalError("document plan digest is invalid")
        if goal.followup_instruction is None:
            raise DurableGoalError("document plan requires a two-step goal")
        existing = self.document_plan_binding(goal)
        if existing is not None:
            if existing != (plan_sha256, authority_configuration_sha256):
                raise DurableGoalError("document plan binding differs")
            return "already_bound"
        events = self._events(goal)
        self._require_waiting(events)
        if self._last(events, "GOAL_EFFECT_PREPARED") is not None:
            raise DurableGoalError("document plan must precede effect preparation")
        self._append(
            goal, event_type="GOAL_DOCUMENT_PLAN_BOUND", phase="goal_document_bound",
            status="active", occurred_at=goal.created_at, suffix="document-plan-bound",
            data={"goal_sha256": goal.sha256, "plan_sha256": plan_sha256,
                  "authority_configuration_sha256": authority_configuration_sha256},
        )
        return "bound"

    def waiting(
        self, *, observed_before: datetime, limit: int = 32
    ) -> tuple[DurableGoalSpec, ...]:
        checkpoints = self._timeline.list_checkpoints(
            thread_prefix="goal:",
            statuses=("active", "failed"),
            updated_before=observed_before,
            limit=limit,
        )
        goals: list[DurableGoalSpec] = []
        for checkpoint in reversed(checkpoints):
            events = self._timeline.events(checkpoint.thread_id)
            if not events or events[0].event_type != "GOAL_ADMITTED":
                raise DurableGoalError("waiting goal admission is malformed")
            goal = DurableGoalSpec.from_payload(self._data(events[0]))
            state = self.state(goal.goal_id, goal.owner_id)
            if state is None:
                raise DurableGoalError("waiting goal projection differs")
            # Operational checkpoints use ``failed`` both for retryable worker
            # failures and terminal delivery failures.  The semantic event
            # projection is authoritative here: terminal goals must disappear
            # from the worker queue instead of crashing the whole scheduler.
            if state.status != "waiting":
                continue
            goals.append(goal)
        return tuple(goals)

    def record_worker_failure(
        self, goal: DurableGoalSpec, error_text: str, at: datetime
    ) -> str:
        if not error_text.strip():
            raise ValueError("durable goal worker error is empty")
        events = self._events(goal)
        self._require_waiting(events)
        thread_id = self._thread_id(goal.goal_id, goal.owner_id)
        checkpoint = self._timeline.load(thread_id)
        result = self._timeline.record_recovery_failure(
            thread_id=thread_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{thread_id}:recovery-{checkpoint.revision + 1}",
            operation_id=goal.goal_id,
            error_text=error_text[:1000],
            occurred_at=at,
        )
        if result.status == "needs_help":
            self.mark_needs_help(
                goal,
                "Dezelfde uitvoeringsfout blokkeerde dit doel drie keer; "
                "menselijke hulp is nodig.",
                at,
            )
            return "needs_help"
        return "retryable_failure"

    def schedule_wake(self, goal: DurableGoalSpec) -> str:
        events = self._events(goal)
        existing = self._last(events, "GOAL_WAKE_SCHEDULED")
        data = {
            "goal_sha256": goal.sha256,
            "wake": goal.wake.to_payload(),
        }
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable wake condition differs")
            return "already_scheduled"
        self._require_waiting(events)
        self._append(
            goal,
            event_type="GOAL_WAKE_SCHEDULED",
            phase="goal_waiting",
            status="active",
            occurred_at=goal.created_at,
            suffix="wake-scheduled",
            data=data,
        )
        return "scheduled"

    def record_wake(self, goal: DurableGoalSpec, evidence: GoalEvidence) -> str:
        events = self._events(goal)
        if self._last(events, "GOAL_WAKE_SCHEDULED") is None:
            raise DurableGoalError("durable wake was not scheduled")
        self._require_waiting(events)
        data = {
            "condition_matched": (
                evidence.values.get(goal.wake.field) == goal.wake.expected_value
            ),
            "evidence": evidence.to_payload(),
            "evidence_sha256": evidence.sha256,
            "goal_sha256": goal.sha256,
        }
        existing = [
            event
            for event in events
            if event.event_type == "GOAL_EVIDENCE_READ"
            and self._data(event).get("evidence_sha256") == evidence.sha256
        ]
        if existing:
            if len(existing) != 1 or self._data(existing[0]) != data:
                raise DurableGoalError("durable wake evidence differs")
            return "already_recorded"
        self._append(
            goal,
            event_type="GOAL_EVIDENCE_READ",
            phase="goal_evidence_read",
            status="active",
            occurred_at=evidence.observed_at,
            suffix=f"evidence-{evidence.sha256[:16]}",
            data=data,
        )
        return "recorded"

    def record_pivot(self, goal: DurableGoalSpec, instruction: str) -> str:
        if instruction != goal.pivot_instruction:
            raise DurableGoalError("durable goal pivot is outside plan")
        events = self._events(goal)
        existing = self._last(events, "GOAL_PLAN_PIVOTED")
        data = {
            "goal_sha256": goal.sha256,
            "selected_instruction": instruction,
        }
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal pivot differs")
            return "already_pivoted"
        self._require_waiting(events)
        evidence = self._required_evidence(events)
        self._append(
            goal,
            event_type="GOAL_PLAN_PIVOTED",
            phase="goal_pivoted",
            status="active",
            occurred_at=evidence.observed_at,
            suffix="pivoted",
            data=data,
        )
        return "pivoted"

    def prepare_effect(self, goal: DurableGoalSpec, instruction: str) -> str:
        events = self._events(goal)
        if self._last(events, "GOAL_EFFECT_COMPLETED") is not None:
            return "already_completed"
        evidence = self._required_matching_evidence(goal, events)
        selected = self._selected_instruction(goal, events)
        if instruction != selected:
            raise DurableGoalError("durable goal selected instruction differs")
        data = {
            "evidence_sha256": evidence.sha256,
            "goal_sha256": goal.sha256,
            "idempotency_key": "goal-effect:"
            + canonical_sha256(
                {"goal_id": goal.goal_id, "goal_sha256": goal.sha256}
            ),
            "selected_instruction": selected,
        }
        existing = self._last(events, "GOAL_EFFECT_PREPARED")
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal effect preparation differs")
            return "already_prepared"
        self._require_waiting(events)
        self._append(
            goal,
            event_type="GOAL_EFFECT_PREPARED",
            phase="goal_effect_prepared",
            status="active",
            occurred_at=evidence.observed_at,
            suffix="effect-prepared",
            data=data,
        )
        return "prepared"

    def record_effect(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str:
        events = self._events(goal)
        existing = self._last(events, "GOAL_EFFECT_COMPLETED")
        data = {
            "artifact": artifact.to_payload(),
            "artifact_sha256": artifact.content_sha256,
            "goal_sha256": goal.sha256,
        }
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal artifact differs")
            return "already_recorded"
        if self._last(events, "GOAL_EFFECT_PREPARED") is None:
            raise DurableGoalError("durable goal effect was not prepared")
        self._require_waiting(events)
        self._append(
            goal,
            event_type="GOAL_EFFECT_COMPLETED",
            phase="goal_effect_completed",
            status="active",
            occurred_at=artifact.created_at,
            suffix="effect-completed",
            data=data,
        )
        return "recorded"

    def prepare_followup(self, goal: DurableGoalSpec) -> str:
        events = self._events(goal)
        self._require_waiting(events)
        if goal.followup_instruction is None:
            raise DurableGoalError("durable goal has no followup")
        previous = self._required_artifact(events)
        data = {
            "goal_sha256": goal.sha256,
            "previous_artifact_sha256": canonical_sha256(previous.to_payload()),
            "selected_instruction": goal.followup_instruction,
            "idempotency_key": DurableGoalWorker.followup_idempotency_key(goal, previous),
        }
        existing = self._last(events, "GOAL_FOLLOWUP_PREPARED")
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal followup preparation differs")
            return "already_prepared"
        self._append(
            goal, event_type="GOAL_FOLLOWUP_PREPARED", phase="goal_followup_prepared",
            status="active", occurred_at=previous.created_at,
            suffix="followup-prepared", data=data,
        )
        return "prepared"

    def reserve_effect_dispatch(self, goal: DurableGoalSpec, *, followup: bool) -> str:
        events = self._events(goal)
        self._require_waiting(events)
        if goal.followup_instruction is None:
            raise DurableGoalError("dispatch accounting requires a two-step goal")
        prepared_type = "GOAL_FOLLOWUP_PREPARED" if followup else "GOAL_EFFECT_PREPARED"
        completed_type = "GOAL_FOLLOWUP_COMPLETED" if followup else "GOAL_EFFECT_COMPLETED"
        prepared = self._last(events, prepared_type)
        if prepared is None or self._last(events, completed_type) is not None:
            raise DurableGoalError("durable goal dispatch is outside prepared step")
        number = 1 + sum(e.event_type == "GOAL_EFFECT_DISPATCH_RESERVED" for e in events)
        self._append(
            goal, event_type="GOAL_EFFECT_DISPATCH_RESERVED", phase="goal_effect_dispatch",
            status="active", occurred_at=datetime.now(tz=goal.created_at.tzinfo).replace(microsecond=0),
            suffix=f"dispatch-{number}", data={
                "goal_sha256": goal.sha256, "step": 2 if followup else 1,
                "idempotency_key": self._data(prepared)["idempotency_key"],
            },
        )
        return "reserved"

    def record_followup(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str:
        events = self._events(goal)
        data = {"artifact": artifact.to_payload(), "artifact_sha256": artifact.content_sha256,
                "goal_sha256": goal.sha256}
        existing = self._last(events, "GOAL_FOLLOWUP_COMPLETED")
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal followup artifact differs")
            return "already_recorded"
        self._require_waiting(events)
        if self._last(events, "GOAL_FOLLOWUP_PREPARED") is None:
            raise DurableGoalError("durable goal followup was not prepared")
        self._append(
            goal, event_type="GOAL_FOLLOWUP_COMPLETED", phase="goal_followup_completed",
            status="active", occurred_at=artifact.created_at,
            suffix="followup-completed", data=data,
        )
        return "recorded"

    def prepare_delivery(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str:
        events = self._events(goal)
        if self._last(events, "GOAL_COMPLETED") is not None:
            return "already_completed"
        durable = self._required_artifact(events, followup=goal.followup_instruction is not None)
        if durable != artifact:
            raise DurableGoalError("durable goal delivery artifact differs")
        data = {
            "artifact_sha256": artifact.content_sha256,
            "goal_sha256": goal.sha256,
            "idempotency_key": "goal-delivery:"
            + canonical_sha256(
                {
                    "artifact_sha256": artifact.content_sha256,
                    "goal_sha256": goal.sha256,
                    "recipient": goal.delivery_recipient,
                }
            ),
            "recipient": goal.delivery_recipient,
        }
        existing = self._last(events, "GOAL_DELIVERY_PREPARED")
        if existing is not None:
            if self._data(existing) != data:
                raise DurableGoalError("durable goal delivery preparation differs")
            return "already_prepared"
        self._require_waiting(events)
        self._append(
            goal,
            event_type="GOAL_DELIVERY_PREPARED",
            phase="goal_delivery_prepared",
            status="active",
            occurred_at=artifact.created_at,
            suffix="delivery-prepared",
            data=data,
        )
        return "prepared"

    def record_delivery(
        self,
        goal: DurableGoalSpec,
        artifact: GoalArtifact,
        status: str,
        reason: str | None,
    ) -> str:
        if status not in {"accepted", "rejected"}:
            raise DurableGoalError("durable goal delivery status is invalid")
        if (status == "accepted" and reason is not None) or (
            status == "rejected" and (not reason or len(reason) > 240)
        ):
            raise DurableGoalError("durable goal delivery reason is invalid")
        events = self._events(goal)
        durable = self._required_artifact(events, followup=goal.followup_instruction is not None)
        if durable != artifact or self._last(events, "GOAL_DELIVERY_PREPARED") is None:
            raise DurableGoalError("durable goal delivery was not prepared")
        terminal = next(
            (event for event in events if event.event_type in _TERMINAL_EVENTS), None
        )
        data = {
            "artifact_sha256": artifact.content_sha256,
            "goal_sha256": goal.sha256,
            "reason": reason,
            "recipient": goal.delivery_recipient,
            "status": status,
        }
        expected_type = "GOAL_COMPLETED" if status == "accepted" else (
            "GOAL_DELIVERY_FAILED"
        )
        if terminal is not None:
            if terminal.event_type != expected_type or self._data(terminal) != data:
                raise DurableGoalError("durable goal terminal state differs")
            return "already_recorded"
        self._append(
            goal,
            event_type=expected_type,
            phase="goal_completed" if status == "accepted" else "goal_delivery_failed",
            status="completed" if status == "accepted" else "failed",
            occurred_at=datetime.now(tz=artifact.created_at.tzinfo).replace(microsecond=0),
            suffix="completed" if status == "accepted" else "delivery-failed",
            data=data,
        )
        return "recorded"

    def cancel(
        self, goal_id: str, owner_id: str, reason: str, at: datetime
    ) -> str:
        if not reason.strip() or len(reason) > 240:
            raise ValueError("durable goal cancellation reason is invalid")
        state = self.state(goal_id, owner_id)
        if state is None:
            raise DurableGoalError("durable goal does not exist")
        events = self._events(state.spec)
        terminal = next(
            (event for event in events if event.event_type in _TERMINAL_EVENTS), None
        )
        data = {"goal_sha256": state.spec.sha256, "reason": reason}
        if terminal is not None:
            if terminal.event_type == "GOAL_CANCELLED" and self._data(terminal) == data:
                return "already_cancelled"
            raise DurableGoalError("terminal durable goal cannot be cancelled")
        if state.spec.followup_instruction is None and (
            self._last(events, "GOAL_EFFECT_COMPLETED") is not None
        ):
            raise DurableGoalError("completed goal effect cannot be cancelled")
        self._append(
            state.spec,
            event_type="GOAL_CANCELLED",
            phase="goal_cancelled",
            status="completed",
            occurred_at=at,
            suffix="cancelled",
            data=data,
        )
        return "cancelled"

    def mark_needs_help(
        self, goal: DurableGoalSpec, reason: str, at: datetime
    ) -> str:
        if not reason.strip() or len(reason) > 600:
            raise ValueError("durable goal blocker reason is invalid")
        events = self._events(goal)
        terminal = next(
            (event for event in events if event.event_type in _TERMINAL_EVENTS), None
        )
        data = {
            "blocker_owner": f"{goal.wake.provider}:{goal.wake.resource}",
            "goal_sha256": goal.sha256,
            "reason": reason,
            "wake": goal.wake.to_payload(),
        }
        if terminal is not None:
            if terminal.event_type == "GOAL_NEEDS_HELP" and self._data(terminal) == data:
                return "already_needs_help"
            raise DurableGoalError("durable goal already reached a terminal state")
        self._append(
            goal,
            event_type="GOAL_NEEDS_HELP",
            phase="goal_needs_help",
            status="needs_help",
            occurred_at=at,
            suffix="needs-help",
            data=data,
        )
        return "needs_help"

    def _events(self, goal: DurableGoalSpec) -> tuple[TimelineEvent, ...]:
        events = self._timeline.events(self._thread_id(goal.goal_id, goal.owner_id))
        if not events or DurableGoalSpec.from_payload(self._data(events[0])) != goal:
            raise DurableGoalError("durable goal does not match admission")
        return events

    def _append(
        self,
        goal: DurableGoalSpec,
        *,
        event_type: str,
        phase: str,
        status: str,
        occurred_at: datetime,
        suffix: str,
        data: dict[str, object],
    ) -> None:
        thread_id = self._thread_id(goal.goal_id, goal.owner_id)
        state = self._timeline.load(thread_id)
        events = self._events(goal)
        self._require_waiting(events)
        if state.revision != len(events):
            raise DurableGoalError("durable goal update raced another worker")
        try:
            self._timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"checkpoint:{thread_id}:{suffix}",
                occurred_at=occurred_at,
                data=data,
                event_type=event_type,
                phase=phase,
                status=status,
                operation_id=goal.goal_id,
                deadline=goal.wake.deadline,
            )
        except CheckpointConflict as error:
            raise DurableGoalError("durable goal update raced another worker") from error

    @staticmethod
    def _data(event: TimelineEvent) -> dict[str, object]:
        data = event.payload.get("data")
        if not isinstance(data, dict):
            raise DurableGoalError("durable goal event data is malformed")
        return data

    @staticmethod
    def _last(
        events: tuple[TimelineEvent, ...], event_type: str
    ) -> TimelineEvent | None:
        return next(
            (event for event in reversed(events) if event.event_type == event_type),
            None,
        )

    @staticmethod
    def _require_waiting(events: tuple[TimelineEvent, ...]) -> None:
        if any(event.event_type in _TERMINAL_EVENTS for event in events):
            raise DurableGoalError("terminal durable goal cannot advance")

    def _required_evidence(
        self, events: tuple[TimelineEvent, ...]
    ) -> GoalEvidence:
        event = self._last(events, "GOAL_EVIDENCE_READ")
        if event is None:
            raise DurableGoalError("durable goal evidence is missing")
        return GoalEvidence.from_payload(self._data(event).get("evidence"))

    def _required_matching_evidence(
        self, goal: DurableGoalSpec, events: tuple[TimelineEvent, ...]
    ) -> GoalEvidence:
        evidence = self._required_evidence(events)
        if evidence.values.get(goal.wake.field) != goal.wake.expected_value:
            raise DurableGoalError("durable goal wake condition is not satisfied")
        return evidence

    def _selected_instruction(
        self, goal: DurableGoalSpec, events: tuple[TimelineEvent, ...]
    ) -> str:
        pivot = self._last(events, "GOAL_PLAN_PIVOTED")
        if pivot is None:
            return goal.primary_instruction
        value = self._data(pivot).get("selected_instruction")
        if value != goal.pivot_instruction:
            raise DurableGoalError("durable goal pivot differs")
        return str(value)

    def _required_artifact(
        self, events: tuple[TimelineEvent, ...], *, followup: bool = False
    ) -> GoalArtifact:
        event = self._last(events, "GOAL_FOLLOWUP_COMPLETED" if followup
                           else "GOAL_EFFECT_COMPLETED")
        if event is None:
            raise DurableGoalError("durable goal artifact is missing")
        return GoalArtifact.from_payload(self._data(event).get("artifact"))


__all__ = ["PostgresDurableGoalStore"]
