"""Durable bounded goals that wake, re-read evidence and close truthfully."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
from typing import Literal, Protocol

from eva.contracts.canonical_json import canonical_sha256


class DurableGoalError(RuntimeError):
    pass


def _utc_whole(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole seconds")


@dataclass(frozen=True, slots=True)
class GoalWakeCondition:
    provider: str
    resource: str
    field: str
    expected_value: str
    deadline: datetime

    def __post_init__(self) -> None:
        for value, label, maximum in (
            (self.provider, "provider", 80),
            (self.resource, "resource", 240),
            (self.field, "field", 120),
            (self.expected_value, "expected value", 240),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"goal wake {label} is invalid")
        _utc_whole(self.deadline, "goal wake deadline")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/@+\-=]{0,239}", self.resource) is None:
            raise ValueError("goal wake resource must be one exact provider identifier")

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaGoalWakeCondition/v1",
            "deadline": self.deadline.isoformat().replace("+00:00", "Z"),
            "expected_value": self.expected_value,
            "field": self.field,
            "provider": self.provider,
            "resource": self.resource,
        }

    @classmethod
    def from_payload(cls, raw: object) -> GoalWakeCondition:
        if not isinstance(raw, dict) or raw.get("contract") != (
            "EvaGoalWakeCondition/v1"
        ):
            raise DurableGoalError("goal wake condition is malformed")
        try:
            value = cls(
                provider=str(raw["provider"]),
                resource=str(raw["resource"]),
                field=str(raw["field"]),
                expected_value=str(raw["expected_value"]),
                deadline=datetime.fromisoformat(
                    str(raw["deadline"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DurableGoalError("goal wake condition is malformed") from error
        if value.to_payload() != raw:
            raise DurableGoalError("goal wake condition shape differs")
        return value


@dataclass(frozen=True, slots=True)
class DurableGoalSpec:
    goal_id: str
    owner_id: str
    description: str
    primary_instruction: str
    pivot_instruction: str | None
    suitability_field: str | None
    unsuitable_value: str | None
    delivery_recipient: str
    wake: GoalWakeCondition
    created_at: datetime
    followup_instruction: str | None = None

    def __post_init__(self) -> None:
        for value, label, maximum in (
            (self.goal_id, "identity", 240),
            (self.owner_id, "owner", 240),
            (self.description, "description", 1200),
            (self.primary_instruction, "primary instruction", 2000),
            (self.delivery_recipient, "delivery recipient", 160),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"goal {label} is invalid")
        if self.followup_instruction is not None and (
            not isinstance(self.followup_instruction, str)
            or not self.followup_instruction.strip()
            or len(self.followup_instruction) > 2000
        ):
            raise ValueError("goal followup instruction is invalid")
        pivot_fields = (
            self.pivot_instruction,
            self.suitability_field,
            self.unsuitable_value,
        )
        if any(value is not None for value in pivot_fields) and not all(
            isinstance(value, str) and value.strip() for value in pivot_fields
        ):
            raise ValueError("goal pivot definition is incomplete")
        if self.pivot_instruction is not None and len(self.pivot_instruction) > 2000:
            raise ValueError("goal pivot instruction is invalid")
        _utc_whole(self.created_at, "goal creation time")
        if self.wake.deadline <= self.created_at:
            raise ValueError("goal wake deadline must follow creation")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            **({"followup_instruction": self.followup_instruction}
               if self.followup_instruction is not None else {}),
            "contract": ("EvaDurableGoalSpec/v2" if self.followup_instruction is not None
                         else "EvaDurableGoalSpec/v1"),
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "delivery_recipient": self.delivery_recipient,
            "description": self.description,
            "goal_id": self.goal_id,
            "owner_id": self.owner_id,
            "pivot_instruction": self.pivot_instruction,
            "primary_instruction": self.primary_instruction,
            "suitability_field": self.suitability_field,
            "unsuitable_value": self.unsuitable_value,
            "wake": self.wake.to_payload(),
        }

    @classmethod
    def from_payload(cls, raw: object) -> DurableGoalSpec:
        if not isinstance(raw, dict) or raw.get("contract") not in {
            "EvaDurableGoalSpec/v1", "EvaDurableGoalSpec/v2"
        }:
            raise DurableGoalError("durable goal is malformed")
        try:
            value = cls(
                followup_instruction=(raw.get("followup_instruction")
                    if raw["contract"] == "EvaDurableGoalSpec/v2" else None),
                goal_id=str(raw["goal_id"]),
                owner_id=str(raw["owner_id"]),
                description=str(raw["description"]),
                primary_instruction=str(raw["primary_instruction"]),
                pivot_instruction=(
                    None
                    if raw["pivot_instruction"] is None
                    else str(raw["pivot_instruction"])
                ),
                suitability_field=(
                    None
                    if raw["suitability_field"] is None
                    else str(raw["suitability_field"])
                ),
                unsuitable_value=(
                    None
                    if raw["unsuitable_value"] is None
                    else str(raw["unsuitable_value"])
                ),
                delivery_recipient=str(raw["delivery_recipient"]),
                wake=GoalWakeCondition.from_payload(raw["wake"]),
                created_at=datetime.fromisoformat(
                    str(raw["created_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DurableGoalError("durable goal is malformed") from error
        if value.to_payload() != raw:
            raise DurableGoalError("durable goal shape differs")
        return value


@dataclass(frozen=True, slots=True)
class GoalEvidence:
    provider: str
    resource: str
    values: dict[str, str]
    observed_at: datetime
    provider_revision: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.resource.strip():
            raise ValueError("goal evidence scope is invalid")
        if not self.values or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or len(value) > 1000
            for key, value in self.values.items()
        ):
            raise ValueError("goal evidence values are invalid")
        if not self.provider_revision.strip() or len(self.provider_revision) > 240:
            raise ValueError("goal evidence revision is invalid")
        _utc_whole(self.observed_at, "goal evidence time")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaGoalEvidence/v1",
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "provider": self.provider,
            "provider_revision": self.provider_revision,
            "resource": self.resource,
            "values": dict(sorted(self.values.items())),
        }

    @classmethod
    def from_payload(cls, raw: object) -> GoalEvidence:
        if not isinstance(raw, dict) or raw.get("contract") != "EvaGoalEvidence/v1":
            raise DurableGoalError("goal evidence is malformed")
        try:
            values = raw["values"]
            if not isinstance(values, dict):
                raise TypeError
            value = cls(
                provider=str(raw["provider"]),
                resource=str(raw["resource"]),
                values={str(key): str(item) for key, item in values.items()},
                observed_at=datetime.fromisoformat(
                    str(raw["observed_at"]).replace("Z", "+00:00")
                ),
                provider_revision=str(raw["provider_revision"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DurableGoalError("goal evidence is malformed") from error
        if value.to_payload() != raw:
            raise DurableGoalError("goal evidence shape differs")
        return value


@dataclass(frozen=True, slots=True)
class GoalArtifact:
    artifact_id: str
    content: str
    media_type: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.artifact_id.strip() or len(self.artifact_id) > 240:
            raise ValueError("goal artifact identity is invalid")
        if not self.content.strip() or len(self.content.encode()) > 65_536:
            raise ValueError("goal artifact content is invalid")
        if self.media_type not in {"text/plain", "text/markdown", "application/json"}:
            raise ValueError("goal artifact media type is unsupported")
        _utc_whole(self.created_at, "goal artifact time")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()

    def to_payload(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "contract": "EvaGoalArtifact/v1",
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "media_type": self.media_type,
        }

    @classmethod
    def from_payload(cls, raw: object) -> GoalArtifact:
        if not isinstance(raw, dict) or raw.get("contract") != "EvaGoalArtifact/v1":
            raise DurableGoalError("goal artifact is malformed")
        try:
            value = cls(
                artifact_id=str(raw["artifact_id"]),
                content=str(raw["content"]),
                media_type=str(raw["media_type"]),
                created_at=datetime.fromisoformat(
                    str(raw["created_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DurableGoalError("goal artifact is malformed") from error
        if value.to_payload() != raw:
            raise DurableGoalError("goal artifact shape or hash differs")
        return value


GoalStatus = Literal[
    "waiting",
    "cancelled",
    "completed",
    "delivery_failed",
    "needs_help",
]


@dataclass(frozen=True, slots=True)
class DurableGoalState:
    spec: DurableGoalSpec
    status: GoalStatus
    evidence: GoalEvidence | None = None
    selected_instruction: str | None = None
    artifact: GoalArtifact | None = None
    delivery_status: Literal["accepted", "rejected"] | None = None
    delivery_reason: str | None = None
    cancellation_reason: str | None = None
    first_artifact: GoalArtifact | None = None
    effect_dispatch_reservations: int = 0

    @property
    def completed_steps(self) -> int:
        if self.spec.followup_instruction is None:
            return int(self.artifact is not None)
        return int(self.first_artifact is not None) + int(self.artifact is not None)


class GoalEvidencePort(Protocol):
    async def read(self, condition: GoalWakeCondition) -> GoalEvidence: ...


class GoalWorkPort(Protocol):
    async def readback(
        self, *, goal: DurableGoalSpec, idempotency_key: str
    ) -> GoalArtifact | None: ...

    async def execute(
        self,
        *,
        goal: DurableGoalSpec,
        instruction: str,
        evidence: GoalEvidence,
        idempotency_key: str,
        previous_artifact: GoalArtifact | None = None,
    ) -> GoalArtifact: ...


class GoalDeliveryPort(Protocol):
    async def readback(
        self, *, recipient: str, idempotency_key: str
    ) -> tuple[Literal["accepted", "rejected"], str | None] | None: ...

    async def deliver(
        self,
        *,
        recipient: str,
        content: str,
        content_sha256: str,
        idempotency_key: str,
    ) -> tuple[Literal["accepted", "rejected"], str | None]: ...


class DurableGoalStorePort(Protocol):
    def admit(self, goal: DurableGoalSpec) -> str: ...
    def state(self, goal_id: str, owner_id: str) -> DurableGoalState | None: ...
    def schedule_wake(self, goal: DurableGoalSpec) -> str: ...
    def record_wake(self, goal: DurableGoalSpec, evidence: GoalEvidence) -> str: ...
    def record_pivot(self, goal: DurableGoalSpec, instruction: str) -> str: ...
    def prepare_effect(self, goal: DurableGoalSpec, instruction: str) -> str: ...
    def record_effect(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str: ...
    def prepare_followup(self, goal: DurableGoalSpec) -> str: ...
    def record_followup(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str: ...
    def reserve_effect_dispatch(self, goal: DurableGoalSpec, *, followup: bool) -> str: ...
    def prepare_delivery(self, goal: DurableGoalSpec, artifact: GoalArtifact) -> str: ...
    def record_delivery(
        self,
        goal: DurableGoalSpec,
        artifact: GoalArtifact,
        status: Literal["accepted", "rejected"],
        reason: str | None,
    ) -> str: ...
    def cancel(self, goal_id: str, owner_id: str, reason: str, at: datetime) -> str: ...
    def mark_needs_help(
        self, goal: DurableGoalSpec, reason: str, at: datetime
    ) -> str: ...


class DurableGoalWorker:
    def __init__(
        self,
        store: DurableGoalStorePort,
        evidence: GoalEvidencePort,
        work: GoalWorkPort,
        delivery: GoalDeliveryPort,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._work = work
        self._delivery = delivery

    async def on_wake(self, goal: DurableGoalSpec) -> DurableGoalState:
        self._store.admit(goal)
        state = self._required_state(goal)
        if state.status != "waiting":
            return state
        self._store.schedule_wake(goal)
        evidence = state.evidence
        if evidence is None or (
            evidence.values.get(goal.wake.field) != goal.wake.expected_value
        ):
            evidence = await self._evidence.read(goal.wake)
            if (
                evidence.provider != goal.wake.provider
                or evidence.resource != goal.wake.resource
            ):
                raise DurableGoalError("goal evidence crossed its wake scope")
            if state.evidence is None or (
                state.evidence.provider_revision != evidence.provider_revision
                or state.evidence.values != evidence.values
            ):
                self._store.record_wake(goal, evidence)
        if evidence.values.get(goal.wake.field) != goal.wake.expected_value:
            if evidence.observed_at >= goal.wake.deadline:
                self._store.mark_needs_help(
                    goal,
                    f"{goal.wake.provider}:{goal.wake.resource} did not reach "
                    f"{goal.wake.field}={goal.wake.expected_value}",
                    evidence.observed_at,
                )
            return self._required_state(goal)
        instruction = goal.primary_instruction
        if (
            goal.pivot_instruction is not None
            and evidence.values.get(str(goal.suitability_field))
            == goal.unsuitable_value
        ):
            instruction = goal.pivot_instruction
            self._store.record_pivot(goal, instruction)
        state = self._required_state(goal)
        if state.status != "waiting":
            return state
        first_artifact = (state.first_artifact if goal.followup_instruction is not None
                          else state.artifact)
        if first_artifact is None:
            self._store.prepare_effect(goal, instruction)
            effect_key = self.effect_idempotency_key(goal)
            artifact = await self._work.readback(
                goal=goal, idempotency_key=effect_key
            )
            if self._required_state(goal).status != "waiting":
                return self._required_state(goal)
            if artifact is None:
                if goal.followup_instruction is not None:
                    self._store.reserve_effect_dispatch(goal, followup=False)
                artifact = await self._work.execute(
                    goal=goal,
                    instruction=instruction,
                    evidence=evidence,
                    idempotency_key=effect_key,
                )
            if self._required_state(goal).status != "waiting":
                return self._required_state(goal)
            self._store.record_effect(goal, artifact)
        else:
            artifact = first_artifact
        if goal.followup_instruction is not None:
            state = self._required_state(goal)
            if state.status != "waiting":
                return state
            if state.artifact is None:
                self._store.prepare_followup(goal)
                key = self.followup_idempotency_key(goal, artifact)
                result = await self._work.readback(goal=goal, idempotency_key=key)
                if self._required_state(goal).status != "waiting":
                    return self._required_state(goal)
                if result is None:
                    self._store.reserve_effect_dispatch(goal, followup=True)
                    result = await self._work.execute(
                        goal=goal, instruction=goal.followup_instruction,
                        evidence=evidence, idempotency_key=key,
                        previous_artifact=artifact,
                    )
                if self._required_state(goal).status != "waiting":
                    return self._required_state(goal)
                self._store.record_followup(goal, result)
                artifact = result
            else:
                artifact = state.artifact
        if self._required_state(goal).status != "waiting":
            return self._required_state(goal)
        self._store.prepare_delivery(goal, artifact)
        key = self.delivery_idempotency_key(goal, artifact)
        delivered = await self._delivery.readback(
            recipient=goal.delivery_recipient, idempotency_key=key
        )
        if self._required_state(goal).status != "waiting":
            return self._required_state(goal)
        if delivered is None:
            delivered = await self._delivery.deliver(
                recipient=goal.delivery_recipient,
                content=artifact.content,
                content_sha256=artifact.content_sha256,
                idempotency_key=key,
            )
        if self._required_state(goal).status != "waiting":
            return self._required_state(goal)
        self._store.record_delivery(goal, artifact, delivered[0], delivered[1])
        return self._required_state(goal)

    def _required_state(self, goal: DurableGoalSpec) -> DurableGoalState:
        state = self._store.state(goal.goal_id, goal.owner_id)
        if state is None or state.spec != goal:
            raise DurableGoalError("durable goal state differs")
        return state

    @staticmethod
    def effect_idempotency_key(goal: DurableGoalSpec) -> str:
        return "goal-effect:" + canonical_sha256(
            {"goal_sha256": goal.sha256, "goal_id": goal.goal_id}
        )

    @staticmethod
    def followup_idempotency_key(goal: DurableGoalSpec, artifact: GoalArtifact) -> str:
        return "goal-followup:" + canonical_sha256({
            "goal_sha256": goal.sha256,
            "previous_artifact_sha256": canonical_sha256(artifact.to_payload()),
        })

    @staticmethod
    def delivery_idempotency_key(
        goal: DurableGoalSpec, artifact: GoalArtifact
    ) -> str:
        return "goal-delivery:" + canonical_sha256(
            {
                "artifact_sha256": artifact.content_sha256,
                "goal_sha256": goal.sha256,
                "recipient": goal.delivery_recipient,
            }
        )


__all__ = [
    "DurableGoalError",
    "DurableGoalSpec",
    "DurableGoalState",
    "DurableGoalWorker",
    "GoalArtifact",
    "GoalDeliveryPort",
    "GoalEvidence",
    "GoalEvidencePort",
    "GoalWakeCondition",
    "GoalWorkPort",
]
