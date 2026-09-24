"""Private general-tool work on the existing durable delegated lifecycle."""
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eva.contracts.base import Identifier, Sha256
from eva.contracts.delegated_work import DelegatedWorkGrant, DelegatedWorkResult, DelegatedWorkSpec


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


class DelegatedDigitalWorkGrant(DelegatedWorkGrant):
    contract: Literal["DelegatedDigitalWorkGrant/v4"]
    schema_version: Literal[4]
    capability_ids: list[Identifier] = Field(min_length=1, max_length=32)
    maximum_model_runs: int = Field(ge=1, le=32)
    maximum_completed_model_responses: int = Field(ge=1, le=32)
    maximum_tool_calls: int = Field(ge=1, le=32)
    maximum_cost_usd_micros: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def _first_slice_is_explicitly_isolated(self):
        if self.context_refs or self.workspace_ref is not None or self.child_network_access:
            raise ValueError("digital work uses host tools, not ambient child access")
        # These are host connector meta-actions; discovered tools remain subject
        # to the existing connector mandate and current dispatch authorization.
        allowed = {"get_delegated_work", "connect_service", "discover_connector_tools", "manage_connected_services", "use_connector_tool",
                   "list_reusable_procedures", "resolve_reusable_procedure",
                   "designate_reusable_procedure", "save_workspace_document", "save_work_progress",
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
