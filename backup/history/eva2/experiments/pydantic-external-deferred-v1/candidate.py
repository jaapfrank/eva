"""Providerless proof of Pydantic AI's external deferred-tool boundary.

This experiment deliberately does not import Eva product code.  It demonstrates
the exact framework seam that can sit on either side of Eva's durable action
port without giving Pydantic AI authority to execute a provider call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.toolsets.external import ExternalToolset
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolDefinition,
)
from pydantic_ai.usage import UsageLimits


_ONE_PHASE_LIMITS = UsageLimits(request_limit=1, tool_calls_limit=1)


@dataclass(frozen=True, slots=True)
class SelectedAction:
    """Framework-neutral action selected by the model."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DurableActionReceipt:
    """Minimal stand-in for Eva's durable ConversationActionReceipt."""

    call: SelectedAction
    output: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DeferredSelection:
    """One stopped model phase plus optional in-process continuation history."""

    call: SelectedAction
    message_history: tuple[ModelMessage, ...]
    model_requests: int
    external_execution_attempts: int


class DeferredBoundaryError(RuntimeError):
    """The model/framework crossed the one-call deferred boundary."""


class GuardedExternalToolset(ExternalToolset[None]):
    """ExternalToolset that makes accidental framework execution observable."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        super().__init__(definitions, id="eva-durable-actions")
        self.execution_attempts = 0

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: Any,
    ) -> Any:
        self.execution_attempts += 1
        raise AssertionError(
            "Pydantic attempted provider I/O instead of returning a deferred call"
        )


def select_one_action(
    model: Model,
    *,
    prompt: str,
    tools: Sequence[ToolDefinition],
) -> DeferredSelection | str:
    """Run one model phase and stop before all external execution.

    Production mapping:
    - ``ToolDefinition`` comes from ``ConversationToolDescriptor``.
    - ``SelectedAction`` maps to ``ConversationActionCall``.
    - the caller, not this function, invokes ``ConversationActionPort.execute``.
    """

    if not prompt.strip():
        raise ValueError("prompt is required")
    if not tools:
        raise ValueError("at least one external tool is required")
    toolset = GuardedExternalToolset(list(tools))
    agent: Agent[None, str | DeferredToolRequests] = Agent(model, retries=0)
    result = agent.run_sync(
        prompt,
        output_type=[str, DeferredToolRequests],
        toolsets=[toolset],
        usage_limits=_ONE_PHASE_LIMITS,
    )
    if result.usage.requests != 1:
        raise DeferredBoundaryError("selection used more than one model request")
    if isinstance(result.output, str):
        if toolset.execution_attempts:
            raise DeferredBoundaryError(
                "external tool was executed during text selection"
            )
        return result.output
    requests = result.output
    if requests.approvals or len(requests.calls) != 1:
        raise DeferredBoundaryError(
            "selection did not return exactly one external call"
        )
    call = requests.calls[0]
    try:
        arguments = call.args_as_dict(raise_if_invalid=True)
    except (AssertionError, ValueError) as error:
        raise DeferredBoundaryError(
            "selected arguments are not one JSON object"
        ) from error
    selected = SelectedAction(call.tool_call_id, call.tool_name, arguments)
    return DeferredSelection(
        selected,
        tuple(result.all_messages()),
        result.usage.requests,
        toolset.execution_attempts,
    )


def rebuild_message_history(
    *, prompt: str, call: SelectedAction
) -> tuple[ModelMessage, ...]:
    """Rebuild only the history needed to resume after a process crash."""

    return (
        ModelRequest(parts=[UserPromptPart(prompt)]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    call.name,
                    call.arguments,
                    call.call_id,
                )
            ]
        ),
    )


def _validate_resume_binding(
    history: Sequence[ModelMessage], *, prompt: str, call: SelectedAction
) -> None:
    prompts = [
        part.content
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    if prompts != [prompt]:
        raise DeferredBoundaryError("receipt continuation prompt differs")
    if not isinstance(history[-1], ModelResponse):
        raise DeferredBoundaryError("receipt continuation has no terminal model call")
    calls = [part for part in history[-1].parts if isinstance(part, ToolCallPart)]
    if len(calls) != 1:
        raise DeferredBoundaryError("receipt continuation call count differs")
    pending = calls[0]
    try:
        arguments = pending.args_as_dict(raise_if_invalid=True)
    except (AssertionError, ValueError) as error:
        raise DeferredBoundaryError(
            "receipt continuation arguments are invalid"
        ) from error
    if (
        pending.tool_call_id != call.call_id
        or pending.tool_name != call.name
        or arguments != call.arguments
    ):
        raise DeferredBoundaryError("receipt continuation call differs")


def resume_after_receipt(
    model: Model,
    *,
    prompt: str,
    receipt: DurableActionReceipt,
    message_history: Sequence[ModelMessage] | None = None,
) -> str:
    """Run one answer phase from a durable receipt, with no offered tools."""

    history = (
        tuple(message_history)
        if message_history is not None
        else rebuild_message_history(
            prompt=prompt,
            call=receipt.call,
        )
    )
    if not history:
        raise DeferredBoundaryError("receipt continuation history is empty")
    _validate_resume_binding(history, prompt=prompt, call=receipt.call)
    agent: Agent[None, str] = Agent(model, retries=0)
    result = agent.run_sync(
        None,
        output_type=str,
        message_history=history,
        deferred_tool_results=DeferredToolResults(
            calls={receipt.call.call_id: receipt.output}
        ),
        usage_limits=_ONE_PHASE_LIMITS,
    )
    if result.usage.requests != 1:
        raise DeferredBoundaryError(
            "receipt continuation used more than one model request"
        )
    if not isinstance(result.output, str) or not result.output.strip():
        raise DeferredBoundaryError("receipt continuation returned no final text")
    return result.output


__all__ = [
    "DeferredBoundaryError",
    "DeferredSelection",
    "DurableActionReceipt",
    "GuardedExternalToolset",
    "SelectedAction",
    "rebuild_message_history",
    "resume_after_receipt",
    "select_one_action",
]
