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


class DelegatedResearchGrant(DelegatedWorkGrant):
    """Explicit v2 authority: one existing public research tool, no ambient context."""

    contract: Literal["DelegatedResearchGrant/v2"]
    schema_version: Literal[2]
    maximum_tool_calls: int = Field(ge=1, le=16)

    @model_validator(mode="after")
    def _first_slice_is_explicitly_isolated(self) -> DelegatedResearchGrant:
        if (self.context_refs or self.workspace_ref is not None or self.child_network_access
                or self.capability_ids != ["research.public_sources"]):
            raise ValueError("research grant only permits the host public research tool")
        if self.maximum_total_tokens < max(self.maximum_input_tokens, self.maximum_output_tokens):
            raise ValueError("research total token budget is invalid")
        return self


class PublicResearchRequest(_DelegatedContract):
    """The explicit public query; never inherit a conversation or private memory."""

    contract: Literal["PublicResearchRequest/v1"]
    question: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    options: list[Annotated[str, StringConstraints(min_length=1, max_length=120)]] = Field(min_length=1, max_length=5)
    research_mode: Literal["topic", "comparison"]
    public_review: Literal["public_unlinked"]

    def arguments(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"contract", "schema_version"})


class DelegatedResearchSpec(DelegatedWorkSpec):
    """Versioned research work on the same durable lifecycle as tool-free v1."""

    contract: Literal["DelegatedResearchWork/v2"]
    schema_version: Literal[2]
    grant: DelegatedResearchGrant
    research: PublicResearchRequest

    @model_validator(mode="after")
    def _bind_public_question(self) -> DelegatedResearchSpec:
        if self.task != self.research.question:
            raise ValueError("research task must equal its admitted public question")
        return self


class HouseholdWorkOrigin(_DelegatedContract):
    """Immutable, server-admitted household origin; the sponsor is not the speaker."""
    contract: Literal["HouseholdWorkOrigin/v1"]
    tenant_id: Identifier
    installation_id: Identifier
    household_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    actor_id: Identifier
    satellite_id: str = Field(pattern=r"^assist_satellite\.[a-z0-9_]{1,183}$")
    device_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    consent_operation_id: Identifier
    household_policy_id: Identifier
    household_policy_sha256: Sha256
    research_policy_id: Identifier
    research_policy_sha256: Sha256
    session_id: Identifier
    session_generation: Literal[1] = 1
    conversation_id: Identifier
    message_id: Identifier
    received_at: Timestamp

    @model_validator(mode="after")
    def _scope(self):
        from uuid import UUID
        if (self.actor_id != "household-device:" + self.device_id
                or str(UUID(self.session_id)) != self.session_id
                or not self.conversation_id.startswith("household:" + self.household_id + ":conversation:")
                or not self.message_id.startswith(self.conversation_id + ":request:")
                or not self.consent_operation_id.startswith("household-consent:")):
            raise ValueError("household work origin differs from its device/session")
        return self

    def destination(self):
        return {key: getattr(self, key) for key in (
            "tenant_id", "installation_id", "household_id", "actor_id", "satellite_id",
            "device_id", "session_id", "session_generation", "conversation_id",
            "consent_operation_id", "household_policy_sha256", "research_policy_sha256")}


class DelegatedHouseholdResearchSpec(DelegatedResearchSpec):
    """Explicit v3 research, never a private task relabeled by a destination."""
    contract: Literal["DelegatedHouseholdResearchWork/v3"]
    schema_version: Literal[3]
    audience: Literal["household"]
    origin: HouseholdWorkOrigin
    destination_sha256: Sha256

    @model_validator(mode="after")
    def _household_binding(self):
        from uuid import NAMESPACE_URL, uuid5
        from datetime import timedelta
        from eva.contracts.timestamps import parse_utc_timestamp
        if (parse_utc_timestamp(self.deadline) > parse_utc_timestamp(self.created_at) + timedelta(minutes=5)
                or self.created_at < self.origin.received_at):
            raise ValueError("household deadline or original receipt differs")
        expected_episode = str(uuid5(NAMESPACE_URL, "household-public-research:" +
                                     canonical_sha256(self.origin.destination())))
        expected_destination = canonical_sha256({"origin": self.origin.destination(),
            "work_id": self.work_id, "generation": self.generation})
        if (self.episode_id != expected_episode or self.destination_sha256 != expected_destination
                or (self.generation == 1 and self.created_at != self.origin.received_at)):
            raise ValueError("household task origin/episode/destination binding differs")
        return self


def load_delegated_work_spec(raw: object) -> DelegatedWorkSpec:
    if isinstance(raw, dict) and raw.get("contract") == "DelegatedHouseholdDigitalWork/v1":
        from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec
        return DelegatedHouseholdDigitalWorkSpec.model_validate(raw)
    if isinstance(raw, dict) and raw.get("contract") == "DelegatedDigitalWork/v4":
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
        return DelegatedDigitalWorkSpec.model_validate(raw)
    if isinstance(raw, dict) and raw.get("contract") == "DelegatedHouseholdResearchWork/v3":
        return DelegatedHouseholdResearchSpec.model_validate(raw)
    if isinstance(raw, dict) and raw.get("contract") == "DelegatedResearchWork/v2":
        return DelegatedResearchSpec.model_validate(raw)
    return DelegatedWorkSpec.model_validate(raw)


class DelegatedWorkUsage(_DelegatedContract):
    """Portable counters with explicit completeness; unknown is never zero."""

    contract: Literal["DelegatedWorkUsage/v1"]
    measurement: Literal["measured", "partial", "unmeasured"]
    completed_model_responses: int | None = Field(default=None, ge=0, le=1_000)
    tool_calls: int | None = Field(default=None, ge=0, le=1_000)
    input_tokens: int | None = Field(default=None, ge=0, le=3_200_000)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=3_300_000)
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


def load_delegated_work_result(raw: object) -> DelegatedWorkResult:
    if isinstance(raw, dict) and raw.get("contract") == "DelegatedDigitalWorkResult/v4":
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult
        return DelegatedDigitalWorkResult.model_validate(raw)
    return DelegatedWorkResult.model_validate(raw)


__all__ = [
    "DelegatedWorkError",
    "DelegatedWorkGrant",
    "DelegatedWorkResult",
    "load_delegated_work_result",
    "DelegatedWorkSpec",
    "DelegatedWorkUsage",
]
