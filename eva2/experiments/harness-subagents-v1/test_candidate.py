"""Provider-free acceptance checks for the exact Harness sub-agent candidate."""

from __future__ import annotations

import asyncio
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
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from smoke_candidate import DelegationLimit, bounded_subagents


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
    assert version("pydantic-ai-harness") == "0.24.0"


def test_real_harness_delegation_returns_child_result() -> None:
    worker = Agent(TestModel(custom_output_text="child evidence"), name="reviewer")
    parent = Agent(
        _parent_model("reviewer"), capabilities=[bounded_subagents([worker])]
    )

    result = asyncio.run(parent.run("delegate this"))

    assert result.output == "parent finished"
    assert _returns(result) == ["child evidence"]


def test_candidate_disables_implicit_agents_and_tool_inheritance() -> None:
    offered_to_child: list[str] = []
    child_history: list[str] = []

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered_to_child.extend(tool.name for tool in info.function_tools)
        child_history.extend(repr(message) for message in messages)
        return ModelResponse(parts=[TextPart("isolated")])

    worker = Agent(FunctionModel(child), name="isolated")
    capability = bounded_subagents([worker])
    parent = Agent(_parent_model("isolated"), capabilities=[capability])

    result = asyncio.run(parent.run("delegate this"))

    assert _returns(result) == ["isolated"]
    assert capability.agent_folders is None
    assert capability.inherit_tools is False
    assert "delegate_task" not in offered_to_child
    assert "bounded task 1" in "".join(child_history)
    assert "delegate this" not in "".join(child_history)


def test_second_call_is_stopped_by_upstream_call_budget() -> None:
    worker = Agent(TestModel(custom_output_text="ran once"), name="single")
    parent = Agent(
        _parent_model("single", calls=2), capabilities=[bounded_subagents([worker])]
    )

    result = asyncio.run(parent.run("try twice"))

    returns = _returns(result)
    assert returns[0] == "ran once"
    assert "delegate budget" in returns[1].lower()
    assert "do not delegate" in returns[1].lower()


def test_invalid_or_unnamed_grants_fail_closed() -> None:
    with pytest.raises(ValueError, match="positive"):
        bounded_subagents(
            [Agent(TestModel(), name="worker")],
            limit=DelegationLimit(max_calls=0),
        )
    with pytest.raises(ValueError, match="explicit name"):
        bounded_subagents([Agent(TestModel())])
    with pytest.raises(ValueError, match="at least one"):
        bounded_subagents([])
