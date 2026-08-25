"""Language-neutral contracts for one bounded parent-to-worker handoff."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from eva.contracts.base import (
    Identifier,
    NaturalText,
    Sha256,
    Timestamp,
    Version1Contract,
)
from eva.contracts.canonical_json import canonical_sha256


class DelegatedWorkError(RuntimeError):
    """The durable handoff differs from its closed state machine."""


class _DelegatedContract(Version1Contract):
    schema_version: Literal[1]

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, raw: object) -> Self:
        return cls.model_validate(raw)


WorkerName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
    ),
]


class DelegatedWorkGrant(_DelegatedContract):
    """The complete derived authority of one child; no ambient inheritance."""

    contract: Literal["DelegatedWorkGrant/v1"]
    context_refs: list[Identifier] = Field(max_length=32)
    capability_ids: list[Identifier] = Field(max_length=16)
    workspace_ref: Identifier | None
    child_network_access: bool
    maximum_model_runs: Literal[1] = 1
    maximum_completed_model_responses: Literal[1] = 1
    maximum_input_tokens: int = Field(ge=1, le=100_000)
    maximum_output_tokens: int = Field(ge=1, le=100_000)
    maximum_total_tokens: int = Field(ge=1, le=200_000)
    maximum_cost_usd_micros: int = Field(ge=0, le=1_000_000_000)
    timeout_seconds: int = Field(ge=1, le=300)
    maximum_claims_before_call_start: int = Field(ge=1, le=3)
    delegation_depth: Literal[1]

    @model_validator(mode="after")
    def _first_slice_is_explicitly_isolated(self) -> DelegatedWorkGrant:
        if len(set(self.context_refs)) != len(self.context_refs):
            raise ValueError("delegated work context references must be unique")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("delegated work capabilities must be unique")
        if self.maximum_total_tokens < max(
            self.maximum_input_tokens, self.maximum_output_tokens
        ):
            raise ValueError("delegated work total token budget is invalid")
        if (
            self.context_refs
            or self.capability_ids
            or self.workspace_ref is not None
            or self.child_network_access
        ):
            raise ValueError(
                "delegated work v1 is explicitly context-, tool-, workspace- and network-free"
            )
        return self


class DelegatedWorkSpec(_DelegatedContract):
    """One private child task bound to its parent and exact released worker."""

    contract: Literal["DelegatedWork/v1"]
    work_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    parent_task_id: Identifier
    owner_principal_id: Identifier
    principal_binding_id: Identifier
    audience: Literal["private"]
    worker_name: WorkerName
    worker_profile_id: Identifier
    worker_profile_release_sha256: Sha256
    task: NaturalText
    model_profile_id: Identifier
    model_profile_release_sha256: Sha256
    worker_program_sha256: Sha256
    engine_release_sha256: Sha256
    output_schema_sha256: Sha256
    grant: DelegatedWorkGrant
    created_at: Timestamp
    deadline: Timestamp

    @model_validator(mode="after")
    def _closed_task_and_time_bounds(self) -> DelegatedWorkSpec:
        if len(self.task.encode("utf-8")) > 16_384:
            raise ValueError("delegated work task exceeds 16,384 UTF-8 bytes")
        if self.deadline <= self.created_at:
            raise ValueError("delegated work deadline must follow creation")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


class DelegatedWorkUsage(_DelegatedContract):
    """Portable counters with explicit completeness; unknown is never zero."""

    contract: Literal["DelegatedWorkUsage/v1"]
    measurement: Literal["measured", "partial", "unmeasured"]
    completed_model_responses: int | None = Field(default=None, ge=0, le=1_000)
    tool_calls: int | None = Field(default=None, ge=0, le=1_000)
    input_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=2_000_000)
    cost_usd_micros: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def _measurement_is_honest(self) -> DelegatedWorkUsage:
        values = (
            self.completed_model_responses,
            self.tool_calls,
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cost_usd_micros,
        )
        if self.measurement == "unmeasured" and any(
            value is not None for value in values
        ):
            raise ValueError("unmeasured delegated work usage cannot carry values")
        if self.measurement == "measured" and any(value is None for value in values):
            raise ValueError("measured delegated work usage must carry every value")
        if self.measurement == "partial" and not any(
            value is not None for value in values
        ):
            raise ValueError("partial delegated work usage must carry a value")
        tokens = (self.input_tokens, self.output_tokens, self.total_tokens)
        if all(value is not None for value in tokens):
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("delegated work total tokens differ")
        elif any(value is not None for value in tokens):
            raise ValueError("delegated work token counters must be present together")
        return self

    @classmethod
    def unmeasured(cls) -> DelegatedWorkUsage:
        return cls(
            contract="DelegatedWorkUsage/v1",
            schema_version=1,
            measurement="unmeasured",
        )


class DelegatedWorkResult(_DelegatedContract):
    contract: Literal["DelegatedWorkResult/v1"]
    work_id: Identifier
    call_id: Identifier
    output: NaturalText
    output_sha256: Sha256
    spec_sha256: Sha256
    usage: DelegatedWorkUsage
    completed_at: Timestamp

    @model_validator(mode="after")
    def _bind_success_output_and_usage(self) -> DelegatedWorkResult:
        if len(self.output.encode("utf-8")) > 65_536:
            raise ValueError("delegated work output exceeds 65,536 UTF-8 bytes")
        if self.output_sha256 != hashlib.sha256(self.output.encode()).hexdigest():
            raise ValueError("delegated work output hash differs")
        if self.usage.measurement != "measured":
            raise ValueError("successful delegated work usage must be measured")
        if self.usage.completed_model_responses != 1:
            raise ValueError("successful delegated work must report one model response")
        return self


__all__ = [
    "DelegatedWorkError",
    "DelegatedWorkGrant",
    "DelegatedWorkResult",
    "DelegatedWorkSpec",
    "DelegatedWorkUsage",
]
