"""Neutral ports shared by Eva's installer use-cases and delivery adapters.

The HTTP adapter depends only on these shapes.  Concrete file stores, provider
probes, and workflow implementations are assembled by the installer bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol

from eva.contracts.identity import IdentityPack


class InstallerUseCaseError(ValueError):
    """A trusted installer use-case could not complete."""


class InstallerUserInputError(InstallerUseCaseError):
    """A human-supplied installer value is invalid."""


CapabilityState = Literal["pending", "selected", "skipped", "ready", "blocked"]


class InstallerCapabilityStepView(Protocol):
    step_id: str
    required: bool
    state: CapabilityState
    evidence_sha256: str | None
    updated_at: str | None


class InstallerCapabilityStatusView(Protocol):
    revision: int
    head_sha256: str | None
    steps: tuple[InstallerCapabilityStepView, ...]

    def step(self, step_id: str) -> InstallerCapabilityStepView: ...


class InstallerCapabilityTransitionView(Protocol):
    step_id: str
    state: CapabilityState
    revision: int
    head_sha256: str | None
    evidence_sha256: str | None
    already_in_state: bool


class InstallerProfileView(Protocol):
    installation_name: str
    owner_label: str
    language: str
    time_zone: str


class InstallerProfileStatusView(Protocol):
    phase: Literal["empty", "staged", "ready"]
    revision: int
    profile_sha256: str | None
    profile: InstallerProfileView | None
    updated_at: str | None


class InstallerProfileTransitionView(Protocol):
    phase: Literal["staged", "ready"]
    revision: int
    profile_sha256: str
    already_in_phase: bool


class InstallerBudgetView(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class InstallerBudgetStatusView(Protocol):
    phase: Literal["empty", "staged", "ready"]
    revision: int
    budget_sha256: str | None
    budget: InstallerBudgetView | None
    updated_at: str | None


class InstallerBudgetTransitionView(Protocol):
    phase: Literal["staged", "ready"]
    revision: int
    budget_sha256: str
    already_in_phase: bool


class InstallerSecretSlotView(Protocol):
    slot_id: str
    state: Literal["missing", "present", "needs_help"]
    reference: str | None
    created_at: str | None
    generation: int | None


class InstallerSecretLifecycleResultView(Protocol):
    slot_id: str
    operation: Literal["rotate", "remove", "recover"]
    state: str
    generation: int | None
    invalidation_evidence_sha256: str
    completion_evidence_sha256: str
    capability_revision: int
    capability_head_sha256: str | None


class InstallerIdentityStageView(Protocol):
    draft_sha256: str
    source_sha256: str
    base_pack_sha256: str
    changed_fields: tuple[str, ...]
    already_staged: bool


class InstallerIdentityCandidateView(Protocol):
    draft_sha256: str
    candidate_sha256: str
    base_pack_sha256: str
    pack_version: int
    already_issued: bool


class InstallerIdentityTransitionView(Protocol):
    status: str
    previous_sha256: str
    current_sha256: str
    rollback_sha256: str


class InstallerIdentityWorkflowStatusView(Protocol):
    phase: Literal["empty", "staged", "issued", "active", "rolled_back"]
    base_pack_sha256: str
    current_pack_sha256: str
    draft_sha256: str | None
    candidate_sha256: str | None
    updated_at: str | None


class InstallerProviderConfigurationStatusView(Protocol):
    slot_id: str
    state: Literal["missing", "configured"]
    configuration_sha256: str | None
    updated_at: str | None
    revision: int


class InstallerProviderConfigurationView(Protocol):
    slot_id: str
    value: str
    configuration_sha256: str
    updated_at: str
    revision: int


class InstallerProviderVerificationResultView(Protocol):
    slot_id: str
    outcome: Literal["ready", "blocked"]
    reason_code: str
    evidence_sha256: str
    capability_revision: int
    provider_scope_manifest_sha256: str


class InstallerCapabilityPort(Protocol):
    workspace: Path

    def status(self) -> InstallerCapabilityStatusView: ...

    def select(
        self, *, step_id: object, wanted: object
    ) -> InstallerCapabilityTransitionView: ...


class InstallerProfilePort(Protocol):
    workspace: Path

    def status(self) -> InstallerProfileStatusView: ...

    def stage(self, **values: object) -> InstallerProfileTransitionView: ...

    def confirm(
        self, *, expected_profile_sha256: object
    ) -> InstallerProfileTransitionView: ...


class InstallerBudgetPort(Protocol):
    workspace: Path
    capability_workflow: InstallerCapabilityPort

    def status(self) -> InstallerBudgetStatusView: ...

    def stage(self, **values: object) -> InstallerBudgetTransitionView: ...

    def confirm(
        self, *, expected_budget_sha256: object
    ) -> InstallerBudgetTransitionView: ...


class InstallerSecretStorePort(Protocol):
    workspace: Path

    def status(self) -> tuple[InstallerSecretSlotView, ...]: ...

    def store(
        self, *, slot_id: object, secret: object
    ) -> InstallerSecretSlotView: ...


class InstallerSecretLifecyclePort(Protocol):
    capability_workflow: InstallerCapabilityPort
    secret_store: InstallerSecretStorePort

    def rotate(
        self, *, slot_id: object, secret: object
    ) -> InstallerSecretLifecycleResultView: ...

    def remove(self, *, slot_id: object) -> InstallerSecretLifecycleResultView: ...

    def recover(self, *, slot_id: object) -> InstallerSecretLifecycleResultView: ...


class InstallerIdentityPort(Protocol):
    def current_identity(self) -> IdentityPack: ...

    def status(self) -> InstallerIdentityWorkflowStatusView: ...

    def stage(self, **values: object) -> InstallerIdentityStageView: ...

    def issue(self, **values: object) -> InstallerIdentityCandidateView: ...

    def activate(self, **values: object) -> InstallerIdentityTransitionView: ...

    def rollback(self, **values: object) -> InstallerIdentityTransitionView: ...


class InstallerProviderConfigurationPort(Protocol):
    workspace: Path

    def status(self) -> tuple[InstallerProviderConfigurationStatusView, ...]: ...

    def read(self, *, slot_id: object) -> InstallerProviderConfigurationView: ...

    def configure(
        self, *, slot_id: object, value: object
    ) -> InstallerProviderConfigurationView: ...


VerificationOutcome = Literal["ready", "blocked"]
ProviderOperation = Literal["read_only_identity", "bounded_generation"]
UsageMeasurement = Literal["not_applicable", "measured", "unmeasured"]
VerificationReason = Literal[
    "verified",
    "unauthorized",
    "unreachable",
    "timeout",
    "invalid_response",
    "provider_error",
]
_BLOCKED_REASONS = frozenset(
    {"unauthorized", "unreachable", "timeout", "invalid_response", "provider_error"}
)
class InstallerProviderVerificationError(InstallerUseCaseError):
    """A provider verification could not be trusted or completed."""


class InstallerProviderProbeError(RuntimeError):
    """A sanitized expected provider failure with one closed reason code."""

    def __init__(
        self,
        reason_code: VerificationReason,
        *,
        provider_operation: ProviderOperation = "read_only_identity",
        usage_measurement: UsageMeasurement = "not_applicable",
        billable_request_attempted: bool = False,
    ) -> None:
        if reason_code not in _BLOCKED_REASONS:
            raise ValueError("provider probe reason is invalid")
        if provider_operation not in {"read_only_identity", "bounded_generation"}:
            raise ValueError("provider probe operation is invalid")
        if usage_measurement not in {"not_applicable", "unmeasured"}:
            raise ValueError("failed provider probe usage is invalid")
        if not isinstance(billable_request_attempted, bool) or (
            billable_request_attempted and provider_operation != "bounded_generation"
        ):
            raise ValueError("provider probe billing attempt is invalid")
        super().__init__("provider verification did not succeed")
        self.reason_code = reason_code
        self.provider_operation = provider_operation
        self.usage_measurement = usage_measurement
        self.billable_request_attempted = billable_request_attempted


@dataclass(frozen=True, slots=True)
class InstallerProviderProbeObservation:
    check_id: str
    outcome: VerificationOutcome
    reason_code: VerificationReason
    provider_operation: ProviderOperation = "read_only_identity"
    usage_measurement: UsageMeasurement = "not_applicable"
    input_tokens: int | None = None
    output_tokens: int | None = None
    billable_request_attempted: bool = False


class InstallerProviderProbe(Protocol):
    def read_only_probe(
        self, *, secret: memoryview, timeout_seconds: float
    ) -> InstallerProviderProbeObservation: ...


class InstallerProviderVerifierPort(Protocol):
    capability_workflow: InstallerCapabilityPort
    secret_store: InstallerSecretStorePort
    probes: Mapping[str, InstallerProviderProbe]

    def verify(
        self, *, slot_id: object, timeout_seconds: object = 10.0
    ) -> InstallerProviderVerificationResultView: ...

    def for_configured_probe(
        self,
        *,
        slot_id: str,
        probe: InstallerProviderProbe,
        configuration_sha256: str,
    ) -> InstallerProviderVerifierPort: ...

    def scope_manifest(self, slot_id: object) -> dict[str, object]: ...

    def scope_manifest_sha256(self, slot_id: object) -> str: ...


class InstallerInstallationPlanBuilder(Protocol):
    def __call__(
        self,
        *,
        preflight: dict[str, object],
        identity: IdentityPack,
        profile: InstallerProfileStatusView | None,
        capabilities: InstallerCapabilityStatusView | None,
        secrets: tuple[InstallerSecretSlotView, ...] | None,
        budgets: InstallerBudgetStatusView | None,
    ) -> dict[str, object]: ...


__all__ = [
    "InstallerBudgetPort",
    "InstallerBudgetStatusView",
    "InstallerCapabilityPort",
    "InstallerCapabilityStatusView",
    "InstallerIdentityPort",
    "InstallerIdentityWorkflowStatusView",
    "InstallerInstallationPlanBuilder",
    "InstallerProfilePort",
    "InstallerProfileStatusView",
    "InstallerProviderConfigurationPort",
    "InstallerProviderProbe",
    "InstallerProviderProbeError",
    "InstallerProviderProbeObservation",
    "InstallerProviderVerificationError",
    "InstallerProviderVerifierPort",
    "InstallerSecretLifecyclePort",
    "InstallerSecretLifecycleResultView",
    "InstallerSecretSlotView",
    "InstallerSecretStorePort",
    "InstallerUseCaseError",
    "InstallerUserInputError",
]
