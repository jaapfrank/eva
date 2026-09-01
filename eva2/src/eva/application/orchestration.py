"""Connector-neutral, proof-first orchestration for one external operation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import time
from typing import Literal, Protocol

from eva.contracts.base import JsonValue, validate_json_value


ToolResultStatus = Literal["verified", "timed_out", "rejected", "failed", "needs_help"]
ProofDisposition = Literal["verified", "pending", "safe_to_execute", "conflict"]


class CheckpointView(Protocol):
    thread_id: str
    checkpoint_id: str
    operation_id: str | None
    phase: str
    status: str
    retry_count: int
    deadline: datetime | None
    revision: int


class TimelineEventView(Protocol):
    event_type: str
    payload: dict[str, JsonValue]


class OperationalTimelinePort(Protocol):
    def load(self, thread_id: str) -> CheckpointView: ...

    def transition(
        self,
        *,
        thread_id: str,
        expected_revision: int,
        checkpoint_id: str,
        phase: str,
        status: str,
        event_type: str,
        occurred_at: datetime,
        data: dict[str, JsonValue],
        operation_id: str | None = None,
        deadline: datetime | None = None,
        retry_count: int | None = None,
        last_error_fingerprint: str | None = None,
    ) -> CheckpointView: ...

    def record_recovery_failure(
        self,
        *,
        thread_id: str,
        expected_revision: int,
        checkpoint_id: str,
        operation_id: str | None,
        error_text: str,
        occurred_at: datetime,
    ) -> CheckpointView: ...

    def events(self, thread_id: str) -> tuple[TimelineEventView, ...]: ...


@dataclass(frozen=True, slots=True)
class ToolRequest:
    thread_id: str
    operation_id: str
    checkpoint_prefix: str
    tool_name: str
    arguments: dict[str, JsonValue]
    expected_proof: dict[str, JsonValue]
    verification_timeout_seconds: int = 10
    execution_timeout_seconds: int = 10
    poll_interval_milliseconds: int = 250
    observation_tool_name: str | None = None
    observation_arguments: dict[str, JsonValue] | None = None
    tool_configuration_sha256: str | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.thread_id, "thread_id"),
            (self.operation_id, "operation_id"),
            (self.checkpoint_prefix, "checkpoint_prefix"),
            (self.tool_name, "tool_name"),
        ):
            if not value or len(value) > 240:
                raise ValueError(f"{field} must contain 1..240 characters")
        validate_json_value(self.arguments)
        validate_json_value(self.expected_proof)
        if (self.observation_tool_name is None) != (self.observation_arguments is None):
            raise ValueError("observation command is incomplete")
        if self.observation_tool_name is not None and (
            not self.observation_tool_name.strip()
            or len(self.observation_tool_name) > 240
        ):
            raise ValueError("observation tool name is invalid")
        if self.observation_arguments is not None:
            validate_json_value(self.observation_arguments)
        if self.tool_configuration_sha256 is not None and (
            len(self.tool_configuration_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.tool_configuration_sha256
            )
        ):
            raise ValueError("tool configuration hash is invalid")
        if not 1 <= self.verification_timeout_seconds <= 60:
            raise ValueError("verification timeout must be within 1..60 seconds")
        if not 1 <= self.execution_timeout_seconds <= 60:
            raise ValueError("execution timeout must be within 1..60 seconds")
        if not 25 <= self.poll_interval_milliseconds <= 2_000:
            raise ValueError("poll interval must be within 25..2000 milliseconds")


@dataclass(frozen=True, slots=True)
class ToolDispatchResult:
    accepted: bool
    provider_ref: str | None
    details: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.provider_ref is not None and not self.provider_ref:
            raise ValueError("provider_ref cannot be empty")
        validate_json_value(self.details)


@dataclass(frozen=True, slots=True)
class ToolProofObservation:
    disposition: ProofDisposition
    observed: dict[str, JsonValue]
    evidence: dict[str, JsonValue]

    def __post_init__(self) -> None:
        validate_json_value(self.observed)
        validate_json_value(self.evidence)


@dataclass(frozen=True, slots=True)
class VerifiedToolResult:
    status: ToolResultStatus
    operation_id: str
    checkpoint_id: str
    proof: dict[str, JsonValue] | None
    retry_count: int


class AsyncToolPort(Protocol):
    async def execute(self, request: ToolRequest) -> ToolDispatchResult: ...

    async def observe(
        self,
        request: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation: ...


class RecoveryExperiencePort(Protocol):
    async def remember_recovery_failure(
        self, request: ToolRequest, error_text: str
    ) -> None: ...


class OwnerAlertPort(Protocol):
    async def request_help(self, request: ToolRequest, error_text: str) -> None: ...


class ToolExecutionRejected(RuntimeError):
    """A provider definitively rejected the call; retrying cannot help."""


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(microsecond=0)


class VerifiedToolOrchestrator:
    """Commit fences around calls and poll proof without blocking the event loop."""

    def __init__(
        self,
        timeline: OperationalTimelinePort,
        tool: AsyncToolPort,
        *,
        recovery_experience: RecoveryExperiencePort | None = None,
        owner_alert: OwnerAlertPort | None = None,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._timeline = timeline
        self._tool = tool
        self._recovery_experience = recovery_experience
        self._owner_alert = owner_alert
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep

    async def run(self, request: ToolRequest) -> VerifiedToolResult:
        state = await asyncio.to_thread(self._timeline.load, request.thread_id)
        if state.operation_id not in {None, request.operation_id}:
            raise RuntimeError("checkpoint belongs to another operation")
        if state.status == "needs_help":
            return self._result("needs_help", request, state, None)
        if state.status == "completed" and state.phase == "state_verified":
            proof = self._last_verified_proof(request.thread_id)
            return self._result("verified", request, state, proof)

        dispatch: ToolDispatchResult | None = None
        if state.phase == "admitted":
            deadline = self._clock() + timedelta(
                seconds=(
                    request.execution_timeout_seconds
                    + request.verification_timeout_seconds
                )
            )
            prepared_data: dict[str, JsonValue] = {
                "arguments": request.arguments,
                "expected_proof": request.expected_proof,
                "tool_name": request.tool_name,
            }
            if request.observation_tool_name is not None:
                prepared_data["observation_tool_name"] = request.observation_tool_name
                prepared_data["observation_arguments"] = (
                    request.observation_arguments or {}
                )
            if request.tool_configuration_sha256 is not None:
                prepared_data["tool_configuration_sha256"] = (
                    request.tool_configuration_sha256
                )
            state = await self._transition(
                state,
                request,
                suffix="prepared",
                phase="tool_prepared",
                status="waiting_for_proof",
                event_type="TOOL_PREPARED",
                data=prepared_data,
                deadline=deadline,
            )
            dispatch_result = await self._dispatch(request, state)
            if isinstance(dispatch_result, VerifiedToolResult):
                return dispatch_result
            state, dispatch = dispatch_result
        elif state.phase == "tool_executed":
            dispatch = self._last_dispatch(request.thread_id)
        elif state.phase not in {"tool_prepared", "verification_pending"}:
            raise RuntimeError(f"cannot resume tool from phase {state.phase!r}")

        return await self._verify_or_resume(request, state, dispatch)

    async def _dispatch(
        self, request: ToolRequest, state: CheckpointView
    ) -> tuple[CheckpointView, ToolDispatchResult] | VerifiedToolResult:
        try:
            dispatch = await asyncio.wait_for(
                self._tool.execute(request),
                timeout=request.execution_timeout_seconds,
            )
            if not dispatch.accepted:
                raise ToolExecutionRejected("provider rejected the operation")
        except ToolExecutionRejected as error:
            state = await self._transition(
                state,
                request,
                suffix="rejected",
                phase="tool_rejected",
                status="failed",
                event_type="TOOL_REJECTED",
                data={"reason": str(error)},
            )
            return self._result("rejected", request, state, None)
        except Exception as error:
            return await self._record_recovery_failure(request, state, error)

        state = await self._transition(
            state,
            request,
            suffix="executed",
            phase="tool_executed",
            status="waiting_for_proof",
            event_type="TOOL_EXECUTED",
            data={"dispatch": _dispatch_payload(dispatch)},
            deadline=state.deadline,
        )
        return state, dispatch

    async def _verify_or_resume(
        self,
        request: ToolRequest,
        state: CheckpointView,
        dispatch: ToolDispatchResult | None,
    ) -> VerifiedToolResult:
        wall_remaining = (
            request.verification_timeout_seconds
            if state.deadline is None
            else max(0.0, (state.deadline - self._clock()).total_seconds())
        )
        end = self._monotonic() + min(
            request.verification_timeout_seconds, wall_remaining
        )
        while True:
            remaining = end - self._monotonic()
            if remaining <= 0:
                state = await self._transition(
                    state,
                    request,
                    suffix="proof-timeout",
                    phase="verification_timed_out",
                    status="failed",
                    event_type="STATE_VERIFICATION_TIMED_OUT",
                    data={"expected_proof": request.expected_proof},
                )
                return self._result("timed_out", request, state, None)
            try:
                observation = await asyncio.wait_for(
                    self._tool.observe(request, dispatch), timeout=remaining
                )
            except Exception as error:
                return await self._record_recovery_failure(request, state, error)
            if observation.disposition == "verified":
                proof = {
                    "evidence": observation.evidence,
                    "expected": request.expected_proof,
                    "observed": observation.observed,
                }
                if observation.observed != request.expected_proof:
                    state = await self._transition(
                        state,
                        request,
                        suffix="proof-mismatch",
                        phase="verification_conflict",
                        status="failed",
                        event_type="STATE_VERIFICATION_CONFLICT",
                        data={"proof": proof},
                    )
                    return self._result("failed", request, state, proof)
                state = await self._transition(
                    state,
                    request,
                    suffix="verified",
                    phase="state_verified",
                    status="completed",
                    event_type="STATE_VERIFIED",
                    data={"proof": proof},
                    retry_count=0,
                )
                return self._result("verified", request, state, proof)
            if observation.disposition == "conflict":
                proof = {
                    "evidence": observation.evidence,
                    "expected": request.expected_proof,
                    "observed": observation.observed,
                }
                state = await self._transition(
                    state,
                    request,
                    suffix="proof-conflict",
                    phase="verification_conflict",
                    status="failed",
                    event_type="STATE_VERIFICATION_CONFLICT",
                    data={"proof": proof},
                )
                return self._result("failed", request, state, proof)
            if (
                observation.disposition == "safe_to_execute"
                and dispatch is None
                and state.phase == "tool_prepared"
            ):
                dispatch_result = await self._dispatch(request, state)
                if isinstance(dispatch_result, VerifiedToolResult):
                    return dispatch_result
                state, dispatch = dispatch_result
                continue
            await self._sleep(
                min(request.poll_interval_milliseconds / 1_000, remaining)
            )

    async def _record_recovery_failure(
        self, request: ToolRequest, state: CheckpointView, error: Exception
    ) -> VerifiedToolResult:
        error_text = f"{type(error).__name__}: {error}"
        state = await asyncio.to_thread(
            self._timeline.record_recovery_failure,
            thread_id=request.thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{request.checkpoint_prefix}:recovery:{state.retry_count + 1}",
            operation_id=request.operation_id,
            error_text=error_text,
            occurred_at=self._clock(),
        )
        if state.status == "needs_help":
            if self._recovery_experience is not None:
                await self._recovery_experience.remember_recovery_failure(
                    request, error_text
                )
            if self._owner_alert is not None:
                await self._owner_alert.request_help(request, error_text)
            return self._result("needs_help", request, state, None)
        return self._result("failed", request, state, None)

    async def _transition(
        self,
        state: CheckpointView,
        request: ToolRequest,
        *,
        suffix: str,
        phase: str,
        status: str,
        event_type: str,
        data: dict[str, JsonValue],
        deadline: datetime | None = None,
        retry_count: int | None = None,
    ) -> CheckpointView:
        return await asyncio.to_thread(
            self._timeline.transition,
            thread_id=request.thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{request.checkpoint_prefix}:{suffix}",
            operation_id=request.operation_id,
            phase=phase,
            status=status,
            event_type=event_type,
            occurred_at=self._clock(),
            deadline=deadline,
            data=data,
            retry_count=retry_count,
            last_error_fingerprint=None,
        )

    def _last_dispatch(self, thread_id: str) -> ToolDispatchResult:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "TOOL_EXECUTED":
                continue
            data = event.payload.get("data")
            raw = data.get("dispatch") if isinstance(data, dict) else None
            if not isinstance(raw, dict):
                break
            return ToolDispatchResult(
                accepted=raw.get("accepted") is True,
                provider_ref=(
                    None
                    if raw.get("provider_ref") is None
                    else str(raw["provider_ref"])
                ),
                details=(
                    raw["details"] if isinstance(raw.get("details"), dict) else {}
                ),
            )
        raise RuntimeError("tool-executed checkpoint lacks its durable dispatch")

    def _last_verified_proof(self, thread_id: str) -> dict[str, JsonValue]:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "STATE_VERIFIED":
                continue
            data = event.payload.get("data")
            proof = data.get("proof") if isinstance(data, dict) else None
            if isinstance(proof, dict):
                return proof
        raise RuntimeError("verified checkpoint lacks its durable proof")

    @staticmethod
    def _result(
        status: ToolResultStatus,
        request: ToolRequest,
        state: CheckpointView,
        proof: dict[str, JsonValue] | None,
    ) -> VerifiedToolResult:
        return VerifiedToolResult(
            status=status,
            operation_id=request.operation_id,
            checkpoint_id=state.checkpoint_id,
            proof=proof,
            retry_count=state.retry_count,
        )


def _dispatch_payload(dispatch: ToolDispatchResult) -> dict[str, JsonValue]:
    return {
        "accepted": dispatch.accepted,
        "details": dispatch.details,
        "provider_ref": dispatch.provider_ref,
    }


__all__ = [
    "AsyncToolPort",
    "OperationalTimelinePort",
    "OwnerAlertPort",
    "RecoveryExperiencePort",
    "ToolDispatchResult",
    "ToolExecutionRejected",
    "ToolProofObservation",
    "ToolRequest",
    "VerifiedToolOrchestrator",
    "VerifiedToolResult",
]
