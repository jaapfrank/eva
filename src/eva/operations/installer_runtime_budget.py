"""One-way materialization of confirmed installer limits for a new runtime slot."""

from __future__ import annotations

from pathlib import Path

from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_identity_storage import write_private_once


class InstallerRuntimeBudgetError(ValueError):
    """A runtime policy could not be derived from confirmed installer authority."""


def materialize_runtime_resource_budget(
    workflow: InstallerBudgetWorkflow,
    *,
    target: Path,
    tenant_id: str,
    installation_id: str,
) -> RuntimeResourceBudget:
    """Write an immutable slot input only when both budget records agree."""

    status = workflow.status()
    capability = workflow.capability_workflow.status().step("budgets")
    if (
        status.phase != "ready"
        or status.budget is None
        or status.budget_sha256 is None
        or capability.state != "ready"
        or capability.evidence_sha256 != status.budget_sha256
    ):
        raise InstallerRuntimeBudgetError(
            "installer budgets are not confirmed for runtime materialization"
        )
    values = status.budget.as_dict()
    values.pop("contract")
    unknown_cost_policy = values.pop("unknown_cost_policy")
    policy = RuntimeResourceBudget(
        tenant_id=tenant_id,
        installation_id=installation_id,
        source_budget_sha256=status.budget_sha256,
        unknown_cost_policy=str(unknown_cost_policy),
        **values,  # type: ignore[arg-type]
    )
    try:
        write_private_once(target, policy.to_bytes())
    except IdentityPackLifecycleError as error:
        raise InstallerRuntimeBudgetError(
            "runtime budget slot artifact conflicts"
        ) from error
    return policy


__all__ = ["InstallerRuntimeBudgetError", "materialize_runtime_resource_budget"]
