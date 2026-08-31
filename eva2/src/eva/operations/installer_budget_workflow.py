"""Durable, fail-closed resource budgets for Eva's local installer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Callable, Literal

from eva.application.installer import InstallerUseCaseError, InstallerUserInputError
from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
)
from eva.operations.installer_safety import (
    InstallerTwoPhaseJournal,
    InstallerTwoPhaseMessages,
    owner_only_lock,
    require_owner_only_directory,
    require_utc_timestamp,
)


BudgetPhase = Literal["empty", "staged", "ready"]


class InstallerBudgetWorkflowError(InstallerUseCaseError):
    """Raised when retained installer budgets cannot be trusted."""


class InstallerBudgetInputError(InstallerUserInputError, InstallerBudgetWorkflowError):
    """Raised when a human-supplied budget lies outside Eva's safe bounds."""


@dataclass(frozen=True, slots=True)
class InstallerBudget:
    recall_context_tokens: int
    retrieval_nodes: int
    max_input_tokens: int
    max_output_tokens: int
    profile_context_tokens: int
    memory_records_per_person: int
    memory_bytes_per_person: int
    memory_records_per_group: int
    memory_bytes_per_group: int
    memory_records_per_project: int
    memory_bytes_per_project: int
    memory_records_per_installation: int
    memory_bytes_per_installation: int
    retention_days: int
    background_reflections_per_person_day: int
    background_reflections_per_installation_day: int
    external_monthly_spend_cap_eur_cents: int

    def as_dict(self) -> dict[str, object]:
        return {
            "background_reflections_per_installation_day": self.background_reflections_per_installation_day,
            "background_reflections_per_person_day": self.background_reflections_per_person_day,
            "contract": "EvaInstallerBudget/v2",
            "external_monthly_spend_cap_eur_cents": self.external_monthly_spend_cap_eur_cents,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "memory_bytes_per_person": self.memory_bytes_per_person,
            "memory_bytes_per_group": self.memory_bytes_per_group,
            "memory_bytes_per_installation": self.memory_bytes_per_installation,
            "memory_bytes_per_project": self.memory_bytes_per_project,
            "memory_records_per_person": self.memory_records_per_person,
            "memory_records_per_group": self.memory_records_per_group,
            "memory_records_per_installation": self.memory_records_per_installation,
            "memory_records_per_project": self.memory_records_per_project,
            "profile_context_tokens": self.profile_context_tokens,
            "recall_context_tokens": self.recall_context_tokens,
            "retention_days": self.retention_days,
            "retrieval_nodes": self.retrieval_nodes,
            "unknown_cost_policy": "block_autonomous_spend",
        }


@dataclass(frozen=True, slots=True)
class InstallerBudgetStatus:
    phase: BudgetPhase
    revision: int
    budget_sha256: str | None
    budget: InstallerBudget | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class InstallerBudgetTransition:
    phase: Literal["staged", "ready"]
    revision: int
    budget_sha256: str
    already_in_phase: bool


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerBudgetWorkflowError,
        invalid_message="budget timestamp is invalid",
        non_utc_message="budget timestamp must be UTC",
    )


def _integer(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise InstallerBudgetInputError(f"{label} is outside the safe range")
    return value


def _budget(**values: object) -> InstallerBudget:
    expected = {
        "background_reflections_per_installation_day",
        "background_reflections_per_person_day",
        "external_monthly_spend_cap_eur_cents",
        "max_input_tokens",
        "max_output_tokens",
        "memory_bytes_per_person",
        "memory_bytes_per_group",
        "memory_bytes_per_installation",
        "memory_bytes_per_project",
        "memory_records_per_person",
        "memory_records_per_group",
        "memory_records_per_installation",
        "memory_records_per_project",
        "profile_context_tokens",
        "recall_context_tokens",
        "retention_days",
        "retrieval_nodes",
    }
    if set(values) != expected:
        raise InstallerBudgetInputError("installer budget fields differ")
    result = InstallerBudget(
        recall_context_tokens=_integer(
            values["recall_context_tokens"],
            label="recall context",
            minimum=2_000,
            maximum=4_000,
        ),
        retrieval_nodes=_integer(
            values["retrieval_nodes"],
            label="retrieval nodes",
            minimum=20,
            maximum=100,
        ),
        max_input_tokens=_integer(
            values["max_input_tokens"],
            label="input tokens",
            minimum=4_000,
            maximum=16_000,
        ),
        max_output_tokens=_integer(
            values["max_output_tokens"],
            label="output tokens",
            minimum=100,
            maximum=1_200,
        ),
        profile_context_tokens=_integer(
            values["profile_context_tokens"],
            label="profile context",
            minimum=100,
            maximum=1_000,
        ),
        memory_records_per_person=_integer(
            values["memory_records_per_person"],
            label="memory records",
            minimum=100,
            maximum=10_000,
        ),
        memory_bytes_per_person=_integer(
            values["memory_bytes_per_person"],
            label="memory bytes",
            minimum=1_048_576,
            maximum=104_857_600,
        ),
        memory_records_per_group=_integer(
            values["memory_records_per_group"],
            label="group memory records",
            minimum=0,
            maximum=100_000,
        ),
        memory_bytes_per_group=_integer(
            values["memory_bytes_per_group"],
            label="group memory bytes",
            minimum=0,
            maximum=1_073_741_824,
        ),
        memory_records_per_project=_integer(
            values["memory_records_per_project"],
            label="project memory records",
            minimum=0,
            maximum=100_000,
        ),
        memory_bytes_per_project=_integer(
            values["memory_bytes_per_project"],
            label="project memory bytes",
            minimum=0,
            maximum=1_073_741_824,
        ),
        memory_records_per_installation=_integer(
            values["memory_records_per_installation"],
            label="installation memory records",
            minimum=100,
            maximum=1_000_000,
        ),
        memory_bytes_per_installation=_integer(
            values["memory_bytes_per_installation"],
            label="installation memory bytes",
            minimum=1_048_576,
            maximum=10_737_418_240,
        ),
        retention_days=_integer(
            values["retention_days"],
            label="retention days",
            minimum=7,
            maximum=3_650,
        ),
        background_reflections_per_person_day=_integer(
            values["background_reflections_per_person_day"],
            label="person reflection budget",
            minimum=0,
            maximum=100,
        ),
        background_reflections_per_installation_day=_integer(
            values["background_reflections_per_installation_day"],
            label="installation reflection budget",
            minimum=0,
            maximum=1_000,
        ),
        external_monthly_spend_cap_eur_cents=_integer(
            values["external_monthly_spend_cap_eur_cents"],
            label="external spend cap",
            minimum=0,
            maximum=1_000_000,
        ),
    )
    if (
        result.background_reflections_per_installation_day
        < result.background_reflections_per_person_day
    ):
        raise InstallerBudgetInputError(
            "installation reflection budget is smaller than the person budget"
        )
    for records, bytes_, label in (
        (
            result.memory_records_per_group,
            result.memory_bytes_per_group,
            "group",
        ),
        (
            result.memory_records_per_project,
            result.memory_bytes_per_project,
            "project",
        ),
    ):
        if (records == 0) != (bytes_ == 0):
            raise InstallerBudgetInputError(
                f"{label} memory records and bytes must both be zero or non-zero"
            )
    if result.memory_records_per_installation < max(
        result.memory_records_per_person,
        result.memory_records_per_group,
        result.memory_records_per_project,
    ):
        raise InstallerBudgetInputError(
            "installation memory record budget is smaller than a scope budget"
        )
    if result.memory_bytes_per_installation < max(
        result.memory_bytes_per_person,
        result.memory_bytes_per_group,
        result.memory_bytes_per_project,
    ):
        raise InstallerBudgetInputError(
            "installation memory byte budget is smaller than a scope budget"
        )
    return result


def _budget_sha256(value: InstallerBudget) -> str:
    return hashlib.sha256(canonical_json_bytes(value.as_dict())).hexdigest()


def _budget_value(value: object) -> InstallerBudget:
    if not isinstance(value, dict) or value.get("contract") != "EvaInstallerBudget/v2":
        raise InstallerBudgetWorkflowError("installer budget is invalid")
    values = dict(value)
    values.pop("contract", None)
    if values.pop("unknown_cost_policy", None) != "block_autonomous_spend":
        raise InstallerBudgetWorkflowError("installer budget cost policy differs")
    return _budget(**values)


_BUDGET_MESSAGES = InstallerTwoPhaseMessages(
    invalid="installer budget workflow is invalid",
    digest_invalid="installer budget hash is invalid",
    retained_digest_differs="installer budget state differs",
    expected_digest_differs="installer budget hash differs",
    state_differs="installer budget state differs",
    history_invalid="installer budget history is invalid",
    update_differs="installer budget update differs",
    write_failed="installer budget workflow write failed",
    different_retained="different installer budgets are already retained",
    missing_staged="no installer budgets are staged",
    confirmation_precedes="budget confirmation precedes staging",
)


@dataclass(frozen=True, slots=True)
class InstallerBudgetWorkflow:
    workspace: Path
    capability_workflow: InstallerCapabilityWorkflow
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        require_owner_only_directory(
            self.workspace,
            error=InstallerBudgetWorkflowError,
            unavailable_message="installer budget workspace is unavailable",
            invalid_message="installer budget workspace must be an owner-only directory",
        )

    @property
    def state_path(self) -> Path:
        return self.workspace / "budget-workflow.json"

    def _lock(self):
        return owner_only_lock(
            self.workspace / ".budget-workflow.lock",
            error=InstallerBudgetWorkflowError,
            unavailable_message="installer budget lock is unavailable",
        )

    def _journal(self) -> InstallerTwoPhaseJournal[InstallerBudget]:
        return InstallerTwoPhaseJournal(
            path=self.state_path,
            contract="EvaInstallerBudgetWorkflow/v1",
            value_field="budget",
            digest_field="budget_sha256",
            read_label="installer budget workflow",
            decode=_budget_value,
            encode=InstallerBudget.as_dict,
            value_digest=_budget_sha256,
            event_timestamp=_timestamp,
            update_timestamp=_timestamp,
            error=InstallerBudgetWorkflowError,
            messages=_BUDGET_MESSAGES,
        )

    def status(self) -> InstallerBudgetStatus:
        with self._lock():
            retained = self._journal().load()
            if retained is not None:
                return InstallerBudgetStatus(
                    phase=retained.phase,
                    revision=retained.revision,
                    budget_sha256=retained.digest,
                    budget=retained.value,
                    updated_at=retained.updated_at,
                )
            return InstallerBudgetStatus(
                phase="empty",
                revision=0,
                budget_sha256=None,
                budget=None,
                updated_at=None,
            )

    def stage(self, **values: object) -> InstallerBudgetTransition:
        if self.capability_workflow.status().step("budgets").state != "selected":
            raise InstallerBudgetWorkflowError(
                "installer budgets must be selected before staging"
            )
        budget = _budget(**values)
        with self._lock():
            change = self._journal().stage(budget, at=_timestamp(self.clock()))
        return InstallerBudgetTransition(
            phase=change.record.phase,
            revision=change.record.revision,
            budget_sha256=change.record.digest,
            already_in_phase=change.already_in_phase,
        )

    def confirm(self, *, expected_budget_sha256: object) -> InstallerBudgetTransition:
        journal = self._journal()
        expected_hash = journal.require_digest(expected_budget_sha256)
        with self._lock():
            change = journal.confirm(
                expected_hash,
                confirmed_at=lambda: _timestamp(self.clock()),
            )
        try:
            self.capability_workflow.record_verification(
                step_id="budgets",
                outcome="ready",
                evidence_sha256=change.record.digest,
            )
        except Exception as exc:
            raise InstallerBudgetWorkflowError(
                "installer budget capability could not be confirmed"
            ) from exc
        return InstallerBudgetTransition(
            phase="ready",
            revision=change.record.revision,
            budget_sha256=change.record.digest,
            already_in_phase=change.already_in_phase,
        )


__all__ = [
    "InstallerBudget",
    "InstallerBudgetInputError",
    "InstallerBudgetStatus",
    "InstallerBudgetTransition",
    "InstallerBudgetWorkflow",
    "InstallerBudgetWorkflowError",
]
