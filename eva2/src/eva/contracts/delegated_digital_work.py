"""Private general-tool work on the existing durable delegated lifecycle."""
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eva.contracts.base import Identifier, Sha256
from eva.contracts.delegated_work import (
    DelegatedWorkGrant, DelegatedWorkResult, DelegatedWorkSpec, HouseholdWorkOrigin,
)


HOUSEHOLD_DIGITAL_CAPABILITIES = frozenset({
    "home_assistant_request", "read_home_assistant_inventory", "save_work_progress",
})


class PrivateWorkAudience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["private_person"]
    principal_id: Identifier


class PrivateWorkSource(BaseModel):
    """Exact original-ingress reference understood by owner connector loaders."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tenant_id: Identifier
    installation_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    message_id: Identifier
    audience: PrivateWorkAudience

    @model_validator(mode="after")
    def _private_origin(self):
        if self.audience.principal_id != self.principal_id:
            raise ValueError("private source audience differs from its principal")
        if self.conversation_id.startswith(("initiative-task:", "background-accounting:", "household:")):
            raise ValueError("background source must be an original private ingress")
        return self


class HouseholdWorkAudience(BaseModel):
    """Shared memory scope of the original registered device turn."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["household"]
    principal_id: Identifier
    household_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    consent_operation_id: Identifier


class HouseholdWorkSource(BaseModel):
    """Exact household ingress; the sponsoring owner is never its speaker."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tenant_id: Identifier
    installation_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    message_id: Identifier
    audience: HouseholdWorkAudience

    @model_validator(mode="after")
    def _household_origin(self):
        prefix = "household:" + self.audience.household_id + ":conversation:"
        if (self.audience.principal_id != self.principal_id
                or not self.principal_id.startswith("household-device:")
                or not self.conversation_id.startswith(prefix)
                or not self.message_id.startswith(self.conversation_id + ":request:")
                or not self.audience.consent_operation_id.startswith("household-consent:")):
            raise ValueError("household source differs from its bound audience")
        return self


class DelegatedDigitalWorkGrant(DelegatedWorkGrant):
    contract: Literal["DelegatedDigitalWorkGrant/v4"]
    schema_version: Literal[4]
    capability_ids: list[Identifier] = Field(min_length=1, max_length=32)
    maximum_model_runs: int = Field(ge=1, le=32)
    maximum_completed_model_responses: int = Field(ge=1, le=32)
    maximum_tool_calls: int = Field(ge=1, le=32)
    # Cumulative input spans bounded model runs; the model still enforces each request.
    maximum_input_tokens: int = Field(ge=1, le=3_200_000)
    maximum_total_tokens: int = Field(ge=1, le=3_300_000)
    maximum_cost_usd_micros: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def _first_slice_is_explicitly_isolated(self):
        if self.context_refs or self.workspace_ref is not None or self.child_network_access:
            raise ValueError("digital work uses host tools, not ambient child access")
        # These are host connector meta-actions; discovered tools remain subject
        # to the existing connector mandate and current dispatch authorization.
        allowed = {"research_current_options", "get_delegated_work", "connect_service", "discover_connector_tools", "manage_connected_services", "use_connector_tool",
                   "list_reusable_procedures", "resolve_reusable_procedure",
                   "designate_reusable_procedure", "save_workspace_document", "save_work_progress",
                   "list_programming_tools", "use_programming_tool", "write_programming_file", "start_programming_process",
                   "read_own_component_source", "draft_own_component_improvement",
                   "evaluate_own_component_improvement", "prepare_own_component_release", "run_reusable_procedure", "home_assistant_request", "read_home_assistant_inventory"}
        if (not self.capability_ids or len(set(self.capability_ids)) != len(self.capability_ids)
                or not set(self.capability_ids).issubset(allowed)):
            raise ValueError("digital work capabilities must be explicit connector actions")
        if self.maximum_completed_model_responses > self.maximum_model_runs:
            raise ValueError("completed response limit exceeds admitted model runs")
        if self.maximum_total_tokens < max(self.maximum_input_tokens, self.maximum_output_tokens):
            raise ValueError("digital work total token budget is invalid")
        return self


class DelegatedDigitalWorkSpec(DelegatedWorkSpec):
    contract: Literal["DelegatedDigitalWork/v4"]
    schema_version: Literal[4]
    grant: DelegatedDigitalWorkGrant
    source: PrivateWorkSource
    source_ingress_sha256: Sha256
    execution_id: Identifier

    @model_validator(mode="after")
    def _initial_execution_identity(self):
        if self.generation == 1 and self.execution_id != self.work_id:
            raise ValueError("first digital attempt must own its execution identity")
        return self


class DelegatedHouseholdDigitalWorkSpec(DelegatedDigitalWorkSpec):
    """Home Assistant work retained and returned only in its household scope."""
    contract: Literal["DelegatedHouseholdDigitalWork/v1"]
    schema_version: Literal[1]
    audience: Literal["household"]
    source: HouseholdWorkSource
    origin: HouseholdWorkOrigin
    destination_sha256: Sha256

    @model_validator(mode="after")
    def _household_binding(self):
        from datetime import timedelta
        from uuid import NAMESPACE_URL, uuid5
        from eva.contracts.canonical_json import canonical_sha256
        from eva.contracts.timestamps import parse_utc_timestamp

        source, origin = self.source, self.origin
        if (not set(self.grant.capability_ids).issubset(HOUSEHOLD_DIGITAL_CAPABILITIES)
                or any(getattr(source, key) != getattr(origin, key) for key in
                       ("tenant_id", "installation_id", "conversation_id", "message_id"))
                or source.principal_id != origin.actor_id
                or source.audience.household_id != origin.household_id
                or source.audience.consent_operation_id != origin.consent_operation_id):
            raise ValueError("household digital source or capability grant differs from its origin")
        if (parse_utc_timestamp(self.deadline) > parse_utc_timestamp(self.created_at) + timedelta(minutes=5)
                or self.created_at < origin.received_at
                or (self.generation == 1 and self.created_at != origin.received_at)):
            raise ValueError("household digital deadline or original receipt differs")
        expected_episode = str(uuid5(NAMESPACE_URL, "household-digital-work:" +
                                     canonical_sha256(origin.destination())))
        expected_destination = canonical_sha256({"origin": origin.destination(),
            "work_id": self.work_id, "generation": self.generation})
        if self.episode_id != expected_episode or self.destination_sha256 != expected_destination:
            raise ValueError("household digital episode or destination differs")
        return self


class DelegatedDigitalWorkResult(DelegatedWorkResult):
    """Multi-response completion; only price may remain unmeasured."""
    contract: Literal["DelegatedDigitalWorkResult/v4"]
    schema_version: Literal[4]

    @model_validator(mode="after")
    def _bind_success_output_and_usage(self):
        if len(self.output.encode("utf-8")) > 65_536:
            raise ValueError("digital work output exceeds 65,536 UTF-8 bytes")
        if self.output_sha256 != hashlib.sha256(self.output.encode()).hexdigest():
            raise ValueError("digital work output hash differs")
        usage = self.usage
        counters = (usage.completed_model_responses, usage.tool_calls,
                    usage.input_tokens, usage.output_tokens, usage.total_tokens)
        if any(value is None for value in counters):
            raise ValueError("successful digital work requires all non-cost counters")
        if usage.completed_model_responses < 1:
            raise ValueError("successful digital work requires completed model work")
        # A resumed attempt may use already verified tool receipts without a
        # new dispatch. Its per-attempt tool count is then correctly zero.
        expected = "partial" if usage.cost_usd_micros is None else "measured"
        if usage.measurement != expected:
            raise ValueError("successful digital work may leave only cost unmeasured")
        return self
