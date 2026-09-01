from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
from typing import Any

from eva.application.orchestration import (
    ToolDispatchResult,
    ToolProofObservation,
    ToolRequest,
    VerifiedToolOrchestrator,
)


NOW = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


@dataclass(frozen=True)
class State:
    thread_id: str = "thread:1"
    checkpoint_id: str = "checkpoint:ingress"
    operation_id: str | None = None
    phase: str = "admitted"
    status: str = "active"
    retry_count: int = 0
    last_error_fingerprint: str | None = None
    deadline: datetime | None = None
    revision: int = 1


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: dict[str, Any]


class Timeline:
    def __init__(self) -> None:
        self.state = State()
        self.recorded: list[Event] = []

    def load(self, thread_id: str) -> State:
        assert thread_id == self.state.thread_id
        return self.state

    def transition(self, **values: Any) -> State:
        assert values["expected_revision"] == self.state.revision
        self.state = State(
            thread_id=self.state.thread_id,
            checkpoint_id=values["checkpoint_id"],
            operation_id=values["operation_id"],
            phase=values["phase"],
            status=values["status"],
            retry_count=(
                self.state.retry_count
                if values["retry_count"] is None
                else values["retry_count"]
            ),
            last_error_fingerprint=values["last_error_fingerprint"],
            deadline=values["deadline"],
            revision=self.state.revision + 1,
        )
        self.recorded.append(Event(values["event_type"], {"data": values["data"]}))
        return self.state

    def record_recovery_failure(self, **values: Any) -> State:
        assert values["expected_revision"] == self.state.revision
        fingerprint = hashlib.sha256(values["error_text"].encode()).hexdigest()
        retry = (
            min(self.state.retry_count + 1, 3)
            if fingerprint == self.state.last_error_fingerprint
            else 1
        )
        self.state = replace(
            self.state,
            checkpoint_id=values["checkpoint_id"],
            operation_id=values["operation_id"],
            status="needs_help" if retry == 3 else "failed",
            retry_count=retry,
            last_error_fingerprint=fingerprint,
            revision=self.state.revision + 1,
        )
        self.recorded.append(
            Event("RECOVERY_NEEDS_HELP" if retry == 3 else "RECOVERY_FAILED", {})
        )
        return self.state

    def events(self, thread_id: str) -> tuple[Event, ...]:
        assert thread_id == self.state.thread_id
        return tuple(self.recorded)


class DelayedThermostat:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.observe_calls = 0

    async def execute(self, request: ToolRequest) -> ToolDispatchResult:
        self.execute_calls += 1
        await asyncio.sleep(0.04)
        return ToolDispatchResult(True, "ha-context:42", {"accepted": True})

    async def observe(
        self, request: ToolRequest, dispatch: ToolDispatchResult | None
    ) -> ToolProofObservation:
        self.observe_calls += 1
        await asyncio.sleep(0.03)
        if self.observe_calls < 3:
            return ToolProofObservation(
                "pending",
                {"entity_id": "climate.woonkamer", "temperature": 20},
                {"source": "home_assistant"},
            )
        return ToolProofObservation(
            "verified",
            request.expected_proof,
            {"source": "home_assistant", "state_updated": True},
        )


def _request() -> ToolRequest:
    return ToolRequest(
        thread_id="thread:1",
        operation_id="operation:thermostat:1",
        checkpoint_prefix="checkpoint:thermostat:1",
        tool_name="home_assistant.set_temperature",
        arguments={"entity_id": "climate.woonkamer", "temperature": 21},
        expected_proof={"entity_id": "climate.woonkamer", "temperature": 21},
        verification_timeout_seconds=3,
        poll_interval_milliseconds=25,
    )


def test_tool_request_keeps_historical_positional_timeout_order() -> None:
    request = ToolRequest(
        "thread:positional",
        "operation:positional",
        "checkpoint:positional",
        "tool.positional",
        {},
        {},
        3,
        4,
        25,
    )

    assert request.verification_timeout_seconds == 3
    assert request.execution_timeout_seconds == 4
    assert request.poll_interval_milliseconds == 25
    assert request.observation_tool_name is None


def test_delayed_proof_does_not_block_event_loop_and_success_follows_proof() -> None:
    async def scenario() -> None:
        timeline = Timeline()
        tool = DelayedThermostat()
        orchestrator = VerifiedToolOrchestrator(timeline, tool, clock=lambda: NOW)
        heartbeat = 0
        running = True

        async def beat() -> None:
            nonlocal heartbeat
            while running:
                heartbeat += 1
                await asyncio.sleep(0.005)

        task = asyncio.create_task(beat())
        result = await orchestrator.run(_request())
        running = False
        await task

        assert result.status == "verified"
        assert heartbeat >= 15
        assert tool.execute_calls == 1
        assert [event.event_type for event in timeline.recorded] == [
            "TOOL_PREPARED",
            "TOOL_EXECUTED",
            "STATE_VERIFIED",
        ]
        assert result.proof is not None
        assert result.proof["observed"] == _request().expected_proof

    asyncio.run(scenario())


def test_adapter_cannot_claim_verified_for_different_observed_state() -> None:
    class LyingTool(DelayedThermostat):
        async def observe(
            self, request: ToolRequest, dispatch: ToolDispatchResult | None
        ) -> ToolProofObservation:
            return ToolProofObservation(
                "verified",
                {"entity_id": "climate.woonkamer", "temperature": 19},
                {"source": "home_assistant"},
            )

    async def scenario() -> None:
        timeline = Timeline()
        result = await VerifiedToolOrchestrator(
            timeline, LyingTool(), clock=lambda: NOW
        ).run(_request())
        assert result.status == "failed"
        assert timeline.recorded[-1].event_type == "STATE_VERIFICATION_CONFLICT"

    asyncio.run(scenario())


def test_restart_after_prepare_reconciles_before_dispatching() -> None:
    class ReconciledTool(DelayedThermostat):
        async def observe(
            self, request: ToolRequest, dispatch: ToolDispatchResult | None
        ) -> ToolProofObservation:
            assert dispatch is None
            return ToolProofObservation(
                "verified",
                request.expected_proof,
                {"source": "home_assistant", "reconciled": True},
            )

    async def scenario() -> None:
        timeline = Timeline()
        timeline.state = replace(
            timeline.state,
            checkpoint_id="checkpoint:thermostat:1:prepared",
            operation_id="operation:thermostat:1",
            phase="tool_prepared",
            status="waiting_for_proof",
            deadline=datetime(2026, 8, 23, 15, 0, 10, tzinfo=UTC),
            revision=2,
        )
        tool = ReconciledTool()
        result = await VerifiedToolOrchestrator(timeline, tool, clock=lambda: NOW).run(
            _request()
        )
        assert result.status == "verified"
        assert tool.execute_calls == 0
        assert [event.event_type for event in timeline.recorded] == ["STATE_VERIFIED"]

    asyncio.run(scenario())


def test_third_identical_parser_crash_learns_and_alerts_once() -> None:
    class BrokenTool(DelayedThermostat):
        async def execute(self, request: ToolRequest) -> ToolDispatchResult:
            raise RuntimeError("onleesbare providerrespons")

        async def observe(
            self, request: ToolRequest, dispatch: ToolDispatchResult | None
        ) -> ToolProofObservation:
            raise RuntimeError("onleesbare providerrespons")

    class RecoverySink:
        def __init__(self) -> None:
            self.memories: list[str] = []
            self.alerts: list[str] = []

        async def remember_recovery_failure(
            self, request: ToolRequest, error_text: str
        ) -> None:
            self.memories.append(error_text)

        async def request_help(self, request: ToolRequest, error_text: str) -> None:
            self.alerts.append(error_text)

    async def scenario() -> None:
        timeline = Timeline()
        sink = RecoverySink()
        orchestrator = VerifiedToolOrchestrator(
            timeline,
            BrokenTool(),
            recovery_experience=sink,
            owner_alert=sink,
            clock=lambda: NOW,
        )
        results = [await orchestrator.run(_request()) for _ in range(3)]
        assert [result.status for result in results] == [
            "failed",
            "failed",
            "needs_help",
        ]
        assert len(sink.memories) == 1
        assert len(sink.alerts) == 1

    asyncio.run(scenario())
