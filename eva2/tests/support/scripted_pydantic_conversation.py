"""Public-Pydantic, providerless fixture for the conversation adapter.

The fixture deliberately knows nothing about Eva's model adapter.  It scripts
only the public ``Model`` boundary exposed by Pydantic AI 2.36 and records what
the agent offered to the model.  No HTTP client, credential, MCP server or
provider is involved.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
from typing import AsyncIterator, Literal

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage


@dataclass(frozen=True, slots=True)
class ScriptedCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ScriptedPhase:
    kind: Literal["text", "deferred", "stall", "error"]
    text_deltas: tuple[str, ...] = ()
    calls: tuple[ScriptedCall, ...] = ()
    usage: RequestUsage | None = None
    stall_after_output: bool = False
    error_message: str = "scripted provider error"

    @classmethod
    def text(
        cls,
        *deltas: str,
        usage: RequestUsage | None = None,
        stall_after_output: bool = False,
    ) -> "ScriptedPhase":
        if not deltas or any(not delta for delta in deltas):
            raise ValueError("scripted text phase needs non-empty deltas")
        return cls(
            "text",
            tuple(deltas),
            usage=usage,
            stall_after_output=stall_after_output,
        )

    @classmethod
    def deferred(
        cls,
        *calls: ScriptedCall,
        usage: RequestUsage | None = None,
    ) -> "ScriptedPhase":
        if not calls:
            raise ValueError("scripted deferred phase needs at least one call")
        return cls("deferred", calls=tuple(calls), usage=usage)

    @classmethod
    def stall(cls) -> "ScriptedPhase":
        return cls("stall")


@dataclass(frozen=True, slots=True)
class CapturedPydanticRequest:
    messages: tuple[ModelMessage, ...]
    offered_tool_names: tuple[str, ...]
    instructions: str | None
    model_settings: dict[str, object]


def tool_returns(messages: tuple[ModelMessage, ...]) -> tuple[ToolReturnPart, ...]:
    return tuple(
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )


def tool_calls(messages: tuple[ModelMessage, ...]) -> tuple[ToolCallPart, ...]:
    return tuple(
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    )


class ScriptedPydanticConversation:
    """A deterministic FunctionModel supporting sync and streamed agent runs."""

    def __init__(self, phases: tuple[ScriptedPhase, ...]) -> None:
        if not phases:
            raise ValueError("at least one scripted phase is required")
        self._phases = deque(phases)
        self._never = asyncio.Event()
        self.captures: list[CapturedPydanticRequest] = []
        self.cancelled_requests = 0
        self.model = FunctionModel(
            self._respond,
            stream_function=self._stream,
            model_name="providerless-conversation-script",
        )

    @property
    def remaining_phases(self) -> int:
        return len(self._phases)

    def _capture(self, messages: list[ModelMessage], info: AgentInfo) -> ScriptedPhase:
        if not self._phases:
            raise AssertionError(
                "conversation adapter made an unexpected model request"
            )
        self.captures.append(
            CapturedPydanticRequest(
                tuple(messages),
                tuple(tool.name for tool in info.function_tools),
                info.instructions,
                dict(info.model_settings or {}),
            )
        )
        return self._phases.popleft()

    async def _stall(self) -> None:
        try:
            await self._never.wait()
        except asyncio.CancelledError:
            self.cancelled_requests += 1
            raise

    async def _respond(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        phase = self._capture(messages, info)
        if phase.kind == "stall":
            await self._stall()
            raise AssertionError("unreachable stalled response")
        if phase.kind == "error":
            raise RuntimeError(phase.error_message)
        if phase.kind == "text":
            parts = [TextPart("".join(phase.text_deltas))]
        else:
            parts = [
                ToolCallPart(call.name, call.arguments, call.call_id)
                for call in phase.calls
            ]
        return ModelResponse(parts=parts, usage=phase.usage or RequestUsage())

    async def _stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        phase = self._capture(messages, info)
        if phase.kind == "stall":
            await self._stall()
            return
        if phase.kind == "error":
            raise RuntimeError(phase.error_message)
        if phase.kind == "text":
            for delta in phase.text_deltas:
                yield delta
            if phase.stall_after_output:
                await self._stall()
            return
        for index, call in enumerate(phase.calls):
            yield {
                index: DeltaToolCall(
                    name=call.name,
                    json_args=json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    tool_call_id=call.call_id,
                )
            }


__all__ = [
    "CapturedPydanticRequest",
    "ScriptedCall",
    "ScriptedPhase",
    "ScriptedPydanticConversation",
    "tool_calls",
    "tool_returns",
]
