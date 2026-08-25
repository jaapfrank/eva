"""Small, provider-free proof that Harness supplies Eva's sub-agent engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import re
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import SubAgent, SubAgents


NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class WorkerSpec:
    """A child definition with no inherited or caller-supplied tools."""

    name: str
    model: Model | KnownModelName | str
    instructions: str = "Return only the requested bounded analysis."


@dataclass(frozen=True)
class DelegationLimit:
    """The small Eva-owned grant around the upstream execution engine."""

    request_limit: int = 3
    per_request_input_tokens_limit: int = 2_000
    total_tokens_limit: int = 4_000
    timeout_seconds: float = 30.0
    max_calls_per_worker: int = 1
    max_workers: int = 4
    max_total_calls: int = 8

    def validate(self, workers: int) -> None:
        integers = (
            self.request_limit,
            self.per_request_input_tokens_limit,
            self.total_tokens_limit,
            self.max_calls_per_worker,
            self.max_workers,
            self.max_total_calls,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in integers
        ):
            raise ValueError("integer delegation limits must be real integers")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise ValueError("delegation timeout must be a finite number")
        if not math.isfinite(self.timeout_seconds):
            raise ValueError("delegation timeout must be a finite number")
        if not 1 <= self.request_limit <= 8:
            raise ValueError("request limit must be between 1 and 8")
        if not 1 <= self.per_request_input_tokens_limit <= 8_000:
            raise ValueError("input token limit must be between 1 and 8000")
        if not 1 <= self.total_tokens_limit <= 32_000:
            raise ValueError("token limit must be between 1 and 32000")
        if self.per_request_input_tokens_limit > self.total_tokens_limit:
            raise ValueError("input token limit cannot exceed the total token limit")
        if not 0.01 <= self.timeout_seconds <= 300:
            raise ValueError("timeout must be between 0.01 and 300 seconds")
        if not 1 <= self.max_calls_per_worker <= 4:
            raise ValueError("per-worker call limit must be between 1 and 4")
        if not 1 <= self.max_workers <= 4 or workers > self.max_workers:
            raise ValueError("worker count exceeds its closed limit")
        if not 1 <= self.max_total_calls <= 8:
            raise ValueError("total call limit must be between 1 and 8")
        if workers * self.max_calls_per_worker > self.max_total_calls:
            raise ValueError("combined worker call budget exceeds its closed limit")


def bounded_subagents(
    workers: Sequence[WorkerSpec],
    *,
    limit: DelegationLimit = DelegationLimit(),
) -> SubAgents[Any]:
    """Configure Harness without disk discovery, supplied tools or recursion."""

    if not workers:
        raise ValueError("at least one sub-agent is required")
    limit.validate(len(workers))
    names = [worker.name for worker in workers]
    if any(not isinstance(name, str) or NAME.fullmatch(name) is None for name in names):
        raise ValueError("every sub-agent needs a canonical explicit name")
    if len(set(names)) != len(names):
        raise ValueError("sub-agent names must be unique")
    if any(
        not isinstance(worker.instructions, str)
        or not 1 <= len(worker.instructions) <= 4_000
        for worker in workers
    ):
        raise ValueError("sub-agent instructions must be 1 to 4000 characters")

    delegates: list[SubAgent[Any]] = []
    for worker in workers:
        child = Agent(
            worker.model,
            name=worker.name,
            instructions=worker.instructions,
        )
        delegates.append(
            SubAgent(
                child,
                usage_limits=UsageLimits(
                    request_limit=limit.request_limit,
                    per_request_input_tokens_limit=limit.per_request_input_tokens_limit,
                    total_tokens_limit=limit.total_tokens_limit,
                    count_tokens_before_request=True,
                ),
                timeout_seconds=limit.timeout_seconds,
                max_calls=limit.max_calls_per_worker,
                contain_errors=False,
            )
        )
    return SubAgents(
        agents=delegates,
        agent_folders=None,
        forward_usage=True,
        inherit_tools=False,
        shared_capabilities=(),
        tool_retries=0,
        contain_errors=False,
    )
