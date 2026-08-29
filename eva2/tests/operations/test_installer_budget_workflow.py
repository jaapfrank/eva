from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.operations.installer_budget_workflow import (
    InstallerBudgetInputError,
    InstallerBudgetWorkflow,
    InstallerBudgetWorkflowError,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow


def _values() -> dict[str, int]:
    return {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
    }


def _workflows(root: Path) -> tuple[InstallerCapabilityWorkflow, InstallerBudgetWorkflow]:
    capabilities = root / "capabilities"
    budgets = root / "budgets"
    capabilities.mkdir(mode=0o700)
    budgets.mkdir(mode=0o700)
    capability = InstallerCapabilityWorkflow(
        workspace=capabilities, clock=lambda: "2026-08-29T05:00:00Z"
    )
    return capability, InstallerBudgetWorkflow(
        workspace=budgets,
        capability_workflow=capability,
        clock=lambda: "2026-08-29T05:01:00Z",
    )


def test_budget_requires_selection_and_rejects_unsafe_values(
    private_tmp_path: Path,
) -> None:
    capability, workflow = _workflows(private_tmp_path)
    with pytest.raises(InstallerBudgetWorkflowError, match="selected"):
        workflow.stage(**_values())
    capability.select(step_id="budgets", wanted=True)
    invalid = _values()
    invalid["max_input_tokens"] = 16_001
    with pytest.raises(InstallerBudgetInputError, match="safe range"):
        workflow.stage(**invalid)
    invalid = _values()
    invalid["background_reflections_per_installation_day"] = 3
    with pytest.raises(InstallerBudgetInputError, match="smaller"):
        workflow.stage(**invalid)
    invalid = _values()
    invalid["memory_records_per_group"] = 0
    with pytest.raises(InstallerBudgetInputError, match="both be zero"):
        workflow.stage(**invalid)
    invalid = _values()
    invalid["memory_records_per_installation"] = 1_000
    with pytest.raises(InstallerBudgetInputError, match="scope budget"):
        workflow.stage(**invalid)


def test_budget_confirmation_survives_restart_and_binds_capability(
    private_tmp_path: Path,
) -> None:
    capability, workflow = _workflows(private_tmp_path)
    capability.select(step_id="budgets", wanted=True)
    staged = workflow.stage(**_values())
    confirmed = workflow.confirm(expected_budget_sha256=staged.budget_sha256)

    resumed_capability = InstallerCapabilityWorkflow(
        workspace=capability.workspace, clock=lambda: "2026-08-29T05:02:00Z"
    )
    resumed = InstallerBudgetWorkflow(
        workspace=workflow.workspace,
        capability_workflow=resumed_capability,
    )
    status = resumed.status()
    step = resumed_capability.status().step("budgets")
    assert confirmed.phase == "ready"
    assert status.phase == "ready"
    assert status.budget is not None
    assert status.budget.external_monthly_spend_cap_eur_cents == 0
    assert status.budget.as_dict()["unknown_cost_policy"] == "block_autonomous_spend"
    assert step.state == "ready"
    assert step.evidence_sha256 == status.budget_sha256
    repeated = resumed.confirm(expected_budget_sha256=status.budget_sha256)
    assert repeated.already_in_phase is True
    assert resumed_capability.status().revision == 2


def test_budget_confirmation_recovers_after_capability_write_failure(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, workflow = _workflows(private_tmp_path)
    capability.select(step_id="budgets", wanted=True)
    staged = workflow.stage(**_values())

    def fail(_self: object, **_arguments: object) -> object:
        raise RuntimeError("synthetic process boundary")

    monkeypatch.setattr(InstallerCapabilityWorkflow, "record_verification", fail)
    with pytest.raises(InstallerBudgetWorkflowError, match="could not be confirmed"):
        workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    assert workflow.status().phase == "ready"
    monkeypatch.undo()

    resumed_capability = InstallerCapabilityWorkflow(
        workspace=capability.workspace, clock=lambda: "2026-08-29T05:02:00Z"
    )
    resumed = InstallerBudgetWorkflow(
        workspace=workflow.workspace,
        capability_workflow=resumed_capability,
    )
    recovered = resumed.confirm(expected_budget_sha256=staged.budget_sha256)
    assert recovered.already_in_phase is True
    assert resumed_capability.status().step("budgets").state == "ready"


def test_budget_tampering_fails_closed(private_tmp_path: Path) -> None:
    capability, workflow = _workflows(private_tmp_path)
    capability.select(step_id="budgets", wanted=True)
    workflow.stage(**_values())
    payload = json.loads(workflow.state_path.read_bytes())
    payload["budget"]["max_output_tokens"] = 1_199
    workflow.state_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    with pytest.raises(InstallerBudgetWorkflowError, match="state differs"):
        workflow.status()
