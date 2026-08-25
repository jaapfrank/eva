"""Small, provider-free proof that Harness supplies Eva's sub-agent engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.agent import AbstractAgent
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import SubAgent, SubAgents


@dataclass(frozen=True)
class DelegationLimit:
    """The small Eva-owned grant around the upstream execution engine."""

    request_limit: int = 3
    total_tokens_limit: int = 4_000
    timeout_seconds: float = 30.0
    max_calls: int = 1

    def validate(self) -> None:
        if (
            min(
                self.request_limit,
                self.total_tokens_limit,
                self.max_calls,
            )
            <= 0
            or self.timeout_seconds <= 0
        ):
            raise ValueError("delegation limits must be positive")


def bounded_subagents(
    agents: Sequence[AbstractAgent[Any, Any]],
    *,
    limit: DelegationLimit = DelegationLimit(),
) -> SubAgents[Any]:
    """Configure Harness without disk discovery, tool inheritance or recursion."""

    limit.validate()
    if not agents:
        raise ValueError("at least one named sub-agent is required")
    delegates: list[SubAgent[Any]] = []
    for agent in agents:
        if not agent.name:
            raise ValueError("every sub-agent needs an explicit name")
        delegates.append(
            SubAgent(
                agent,
                usage_limits=UsageLimits(
                    request_limit=limit.request_limit,
                    total_tokens_limit=limit.total_tokens_limit,
                ),
                timeout_seconds=limit.timeout_seconds,
                max_calls=limit.max_calls,
                contain_errors=True,
            )
        )
    return SubAgents(
        agents=delegates,
        agent_folders=None,
        forward_usage=True,
        inherit_tools=False,
        tool_retries=0,
        contain_errors=True,
    )
