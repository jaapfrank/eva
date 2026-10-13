"""Provider-free acceptance checks for the exact Harness sub-agent candidate."""

from __future__ import annotations

import asyncio
import math
from importlib.metadata import version

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from smoke_candidate import DelegationLimit, WorkerSpec, bounded_subagents


class CountedTestModel(TestModel):
    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage(input_tokens=16)


class CountedFunctionModel(FunctionModel):
    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage(input_tokens=16)


def _parent_model(agent_name: str, calls: int = 1) -> FunctionModel:
    count = 0

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal count
        count += 1
        if count <= calls:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {"agent_name": agent_name, "task": f"bounded task {count}"},
                        tool_call_id=f"delegate-{count}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("parent finished")])

    return FunctionModel(run)


def _returns(result: object) -> list[str]:
    return [
        str(part.content)
        for message in result.all_messages()  # type: ignore[attr-defined]
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "delegate_task"
    ]


def test_exact_candidate_versions() -> None:
    assert version("pydantic-ai-slim") == "2.34.0"
    assert version("pydantic-ai-harness") == "0.25.0"


def test_real_harness_delegation_returns_child_result() -> None:
    capability = bounded_subagents(
        [WorkerSpec("reviewer", CountedTestModel(custom_output_text="child evidence"))]
    )
    parent = Agent(_parent_model("reviewer"), capabilities=[capability])

    result = asyncio.run(parent.run("delegate this"))

    assert result.output == "parent finished"
    assert _returns(result) == ["child evidence"]


def test_child_gets_only_explicit_task_and_no_tools() -> None:
    offered_to_child: list[str] = []
    child_history: list[str] = []

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered_to_child.extend(tool.name for tool in info.function_tools)
        child_history.extend(repr(message) for message in messages)
        return ModelResponse(parts=[TextPart("isolated")])

    capability = bounded_subagents(
        [WorkerSpec("isolated", CountedFunctionModel(child))]
    )
    parent = Agent(_parent_model("isolated"), capabilities=[capability])

    result = asyncio.run(parent.run("private parent conversation"))

    assert _returns(result) == ["isolated"]
    assert capability.agent_folders is None
    assert capability.inherit_tools is False
    assert capability.shared_capabilities == ()
    assert offered_to_child == []
    assert "bounded task 1" in "".join(child_history)
    assert "private parent conversation" not in "".join(child_history)


def test_second_call_is_stopped_by_upstream_call_budget() -> None:
    capability = bounded_subagents(
        [WorkerSpec("single", CountedTestModel(custom_output_text="ran once"))]
    )
    parent = Agent(_parent_model("single", calls=2), capabilities=[capability])

    result = asyncio.run(parent.run("try twice"))

    returns = _returns(result)
    assert returns[0] == "ran once"
    assert "delegate budget" in returns[1].lower()
    assert "do not delegate" in returns[1].lower()


def test_upstream_token_budget_stops_child() -> None:
    child_model = CountedTestModel(custom_output_text="too many tokens")
    capability = bounded_subagents(
        [WorkerSpec("limited", child_model)],
        limit=DelegationLimit(
            per_request_input_tokens_limit=1,
            total_tokens_limit=1,
        ),
    )
    parent = Agent(_parent_model("limited"), capabilities=[capability])

    result = asyncio.run(parent.run("use almost no tokens"))

    assert "usage budget" in _returns(result)[0].lower()
    assert child_model.last_model_request_parameters is None


def test_upstream_timeout_cancels_slow_child() -> None:
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(0.2)
        return ModelResponse(parts=[TextPart("too late")])

    capability = bounded_subagents(
        [WorkerSpec("slow", CountedFunctionModel(slow))],
        limit=DelegationLimit(timeout_seconds=0.01),
    )
    parent = Agent(_parent_model("slow"), capabilities=[capability])

    result = asyncio.run(parent.run("finish quickly"))

    assert "time budget" in _returns(result)[0].lower()


@pytest.mark.parametrize(
    "limit",
    [
        DelegationLimit(max_calls_per_worker=0),
        DelegationLimit(max_calls_per_worker=True),
        DelegationLimit(per_request_input_tokens_limit=8_001),
        DelegationLimit(
            per_request_input_tokens_limit=2_000,
            total_tokens_limit=1_000,
        ),
        DelegationLimit(timeout_seconds=math.nan),
        DelegationLimit(timeout_seconds=math.inf),
        DelegationLimit(timeout_seconds=True),
        DelegationLimit(max_workers=5),
        DelegationLimit(max_total_calls=9),
        DelegationLimit(max_calls_per_worker=3, max_total_calls=2),
    ],
)
def test_invalid_numeric_grants_fail_closed(limit: DelegationLimit) -> None:
    with pytest.raises(ValueError):
        bounded_subagents([WorkerSpec("worker", CountedTestModel())], limit=limit)


def test_invalid_worker_rosters_fail_closed() -> None:
    with pytest.raises(ValueError, match="at least one"):
        bounded_subagents([])
    with pytest.raises(ValueError, match="canonical explicit name"):
        bounded_subagents([WorkerSpec("Bad Name", CountedTestModel())])
    with pytest.raises(ValueError, match="canonical explicit name"):
        bounded_subagents(
            [
                WorkerSpec(
                    True,  # type: ignore[arg-type]
                    CountedTestModel(),
                )
            ]
        )
    with pytest.raises(ValueError, match="unique"):
        bounded_subagents(
            [
                WorkerSpec("worker", CountedTestModel()),
                WorkerSpec("worker", CountedTestModel()),
            ]
        )
    with pytest.raises(ValueError, match="worker count"):
        bounded_subagents(
            [WorkerSpec(f"worker-{index}", CountedTestModel()) for index in range(5)]
        )


@pytest.mark.parametrize("instructions", ["", "x" * 4_001, True, lambda: None])
def test_invalid_worker_instructions_fail_closed(instructions: object) -> None:
    with pytest.raises(ValueError, match="instructions"):
        bounded_subagents(
            [
                WorkerSpec(
                    "worker",
                    CountedTestModel(),
                    instructions=instructions,  # type: ignore[arg-type]
                )
            ]
        )


def test_unexpected_child_crash_never_becomes_parent_tool_text() -> None:
    marker = "private-child-exception-marker"

    def explode(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ValueError(marker)

    capability = bounded_subagents(
        [WorkerSpec("crasher", CountedFunctionModel(explode))]
    )
    assert capability.contain_errors is False
    parent = Agent(_parent_model("crasher"), capabilities=[capability])

    with pytest.raises(ValueError, match=marker):
        asyncio.run(parent.run("do not expose child errors to the parent model"))
