from __future__ import annotations

from pathlib import Path
import stat

import pytest

from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_runtime_budget import (
    InstallerRuntimeBudgetError,
    materialize_runtime_resource_budget,
)


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


def _workflow(root: Path) -> InstallerBudgetWorkflow:
    capabilities = root / "capabilities"
    budgets = root / "budgets"
    capabilities.mkdir(mode=0o700)
    budgets.mkdir(mode=0o700)
    capability = InstallerCapabilityWorkflow(
        workspace=capabilities, clock=lambda: "2026-08-29T05:10:00Z"
    )
    capability.select(step_id="budgets", wanted=True)
    return InstallerBudgetWorkflow(
        workspace=budgets,
        capability_workflow=capability,
        clock=lambda: "2026-08-29T05:11:00Z",
    )


def test_runtime_budget_requires_both_confirmed_records(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    staged = workflow.stage(**_values())
    target = private_tmp_path / "runtime-budget.json"

    with pytest.raises(InstallerRuntimeBudgetError, match="not confirmed"):
        materialize_runtime_resource_budget(
            workflow,
            target=target,
            tenant_id="tenant:test",
            installation_id="installation:test",
        )
    assert not target.exists()

    workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    policy = materialize_runtime_resource_budget(
        workflow,
        target=target,
        tenant_id="tenant:test",
        installation_id="installation:test",
    )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert policy.max_input_tokens == 12_000
    assert policy.max_output_tokens == 1_200
    assert RuntimeResourceBudget.from_bytes(
        target.read_bytes(),
        expected_tenant_id="tenant:test",
        expected_installation_id="installation:test",
    ) == policy


def test_runtime_budget_is_idempotent_but_never_overwrites_another_slot(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    staged = workflow.stage(**_values())
    workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    target = private_tmp_path / "runtime-budget.json"

    first = materialize_runtime_resource_budget(
        workflow,
        target=target,
        tenant_id="tenant:test",
        installation_id="installation:test",
    )
    second = materialize_runtime_resource_budget(
        workflow,
        target=target,
        tenant_id="tenant:test",
        installation_id="installation:test",
    )
    assert first == second

    with pytest.raises(InstallerRuntimeBudgetError, match="conflicts"):
        materialize_runtime_resource_budget(
            workflow,
            target=target,
            tenant_id="tenant:test",
            installation_id="installation:other",
        )
    assert RuntimeResourceBudget.from_bytes(
        target.read_bytes(),
        expected_tenant_id="tenant:test",
        expected_installation_id="installation:test",
    ) == first


def test_confirmed_65536_input_budget_roundtrips_through_runtime_manifest(
    private_tmp_path: Path,
) -> None:
    from eva.adapters.api.installer_budget_api import INSTALLER_BUDGET_SECTION
    from eva.operations.installer_budget_workflow import InstallerBudgetInputError

    values = _values()
    assert values["max_input_tokens"] == 12_000
    values["max_input_tokens"] = 65_536
    workflow = _workflow(private_tmp_path)
    with pytest.raises(InstallerBudgetInputError, match="safe range"):
        workflow.stage(**{**values, "max_input_tokens": 65_537})
    staged = workflow.stage(**values)
    workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    target = private_tmp_path / "operator-runtime-budget.json"
    policy = materialize_runtime_resource_budget(workflow, target=target,
        tenant_id="tenant:operator", installation_id="installation:operator")
    restarted = RuntimeResourceBudget.from_bytes(target.read_bytes(),
        expected_tenant_id="tenant:operator", expected_installation_id="installation:operator")
    assert restarted == policy
    assert restarted.max_input_tokens == 65_536
    assert restarted.max_output_tokens == 1_200
    assert restarted.external_monthly_spend_cap_eur_cents == 0
    assert restarted.unknown_cost_policy == "block_autonomous_spend"
    assert restarted.source_budget_sha256 == staged.budget_sha256
    assert 'data-budget="max_input_tokens" min="4000" max="65536" value="12000"' in INSTALLER_BUDGET_SECTION
