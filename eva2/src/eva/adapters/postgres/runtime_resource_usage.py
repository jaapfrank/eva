"""Durable admission for installation-scoped background resource budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    OperationalTimelineError,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.contracts.runtime_budget import RuntimeResourceBudget


@dataclass(frozen=True, slots=True)
class BackgroundReflectionAdmission:
    """One durable daily allow/deny decision without exposing a principal ID."""

    admitted: bool
    day: str
    person_used: int
    installation_used: int
    reason: str


class PostgresDailyReflectionBudget:
    """Serialize daily reflection usage on Eva's existing append-only timeline."""

    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        installation_id: str,
        resource_budget: RuntimeResourceBudget,
    ) -> None:
        if (
            resource_budget.tenant_id != tenant_id
            or resource_budget.installation_id != installation_id
        ):
            raise ValueError("reflection budget crossed its runtime scope")
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        self._person_limit = resource_budget.background_reflections_per_person_day
        self._installation_limit = (
            resource_budget.background_reflections_per_installation_day
        )
        self._source_budget_sha256 = resource_budget.source_budget_sha256

    @staticmethod
    def _day(now: datetime) -> str:
        if now.tzinfo is None or now.utcoffset() is None or now.microsecond:
            raise ValueError("reflection budget time must be whole-second UTC")
        return now.astimezone(UTC).date().isoformat()

    @staticmethod
    def _principal_sha256(principal_id: str) -> str:
        if not principal_id.strip() or len(principal_id) > 240:
            raise ValueError("reflection budget principal is invalid")
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _usage(
        event_data: object,
        *,
        expected_day: str,
        expected_person_limit: int,
        expected_installation_limit: int,
        expected_source_budget_sha256: str,
    ) -> tuple[int, dict[str, int]]:
        if not isinstance(event_data, dict):
            raise OperationalTimelineError("reflection budget event is malformed")
        if set(event_data) != {
            "admitted",
            "contract",
            "day",
            "installation_limit",
            "installation_used",
            "people",
            "person_limit",
            "principal_sha256",
            "reason",
            "source_budget_sha256",
        }:
            raise OperationalTimelineError("reflection budget event is malformed")
        people = event_data.get("people")
        if (
            event_data.get("contract") != "EvaDailyReflectionBudget/v1"
            or event_data.get("day") != expected_day
            or event_data.get("person_limit") != expected_person_limit
            or event_data.get("installation_limit")
            != expected_installation_limit
            or event_data.get("source_budget_sha256")
            != expected_source_budget_sha256
            or not isinstance(event_data.get("installation_used"), int)
            or not isinstance(people, dict)
            or any(
                not isinstance(key, str)
                or len(key) != 64
                or not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for key, value in people.items()
            )
        ):
            raise OperationalTimelineError("reflection budget event is malformed")
        installation_used = event_data["installation_used"]
        if (
            isinstance(installation_used, bool)
            or installation_used < 0
            or installation_used != sum(people.values())
        ):
            raise OperationalTimelineError("reflection budget totals differ")
        return installation_used, dict(people)

    def admit_background_reflection(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
    ) -> BackgroundReflectionAdmission:
        """Consume at most one slot; revision conflicts are retried, never widened."""

        if tenant_id != self._tenant_id or installation_id != self._installation_id:
            raise ValueError("reflection admission crossed its runtime scope")
        day = self._day(now)
        principal_sha256 = self._principal_sha256(principal_id)
        thread_id = f"runtime-reflections:{day}"
        for _attempt in range(32):
            try:
                state = self._timeline.load(thread_id)
            except OperationalTimelineError as error:
                if str(error) == (
                    "checkpoint projection differs from append-only timeline"
                ):
                    continue
                if str(error) != "thread does not exist":
                    raise
                installation_used = 0
                people: dict[str, int] = {}
                state = None
            else:
                events = self._timeline.events(thread_id)
                if not events:
                    raise OperationalTimelineError(
                        "reflection budget timeline is empty"
                    )
                data = events[-1].payload.get("data")
                installation_used, people = self._usage(
                    data,
                    expected_day=day,
                    expected_person_limit=self._person_limit,
                    expected_installation_limit=self._installation_limit,
                    expected_source_budget_sha256=self._source_budget_sha256,
                )

            person_used = people.get(principal_sha256, 0)
            if person_used >= self._person_limit:
                admitted = False
                reason = "person_daily_limit_reached"
            elif installation_used >= self._installation_limit:
                admitted = False
                reason = "installation_daily_limit_reached"
            else:
                admitted = True
                reason = "reserved"
                person_used += 1
                installation_used += 1
                people[principal_sha256] = person_used

            data = {
                "admitted": admitted,
                "contract": "EvaDailyReflectionBudget/v1",
                "day": day,
                "installation_limit": self._installation_limit,
                "installation_used": installation_used,
                "people": people,
                "person_limit": self._person_limit,
                "principal_sha256": principal_sha256,
                "reason": reason,
                "source_budget_sha256": self._source_budget_sha256,
            }
            try:
                if state is None:
                    self._timeline.create_thread(
                        thread_id=thread_id,
                        checkpoint_id=f"{thread_id}:1",
                        occurred_at=now.astimezone(UTC),
                        data=data,
                        event_type=(
                            "RUNTIME_REFLECTION_RESERVED"
                            if admitted
                            else "RUNTIME_REFLECTION_BLOCKED"
                        ),
                        phase="reflection_budget_active",
                    )
                else:
                    self._timeline.transition(
                        thread_id=thread_id,
                        expected_revision=state.revision,
                        checkpoint_id=f"{thread_id}:{state.revision + 1}",
                        phase="reflection_budget_active",
                        status="active",
                        event_type=(
                            "RUNTIME_REFLECTION_RESERVED"
                            if admitted
                            else "RUNTIME_REFLECTION_BLOCKED"
                        ),
                        occurred_at=now.astimezone(UTC),
                        data=data,
                    )
            except CheckpointConflict:
                continue
            return BackgroundReflectionAdmission(
                admitted=admitted,
                day=day,
                person_used=person_used,
                installation_used=installation_used,
                reason=reason,
            )
        raise OperationalTimelineError("reflection budget admission remained busy")


__all__ = ["BackgroundReflectionAdmission", "PostgresDailyReflectionBudget"]
