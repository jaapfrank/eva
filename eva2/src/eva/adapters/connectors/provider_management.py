"""Compatibility exports for one replaceable provider-management bundle."""

from eva.adapters.connectors.home_assistant_advanced_store import (
    PostgresAdvancedTaskStore,
)
from eva.adapters.connectors.home_assistant_advanced_tasks import (
    AdvancedTaskApproval,
    AdvancedTaskBlocked,
    AdvancedTaskPackageApproval,
    AdvancedTaskPlan,
    AdvancedTaskRepositoryApproval,
    LocalTuyaCanaryConfiguration,
    LocalTuyaEntityConfiguration,
    TuyaToLocalTuyaPlanner,
    adopt_device_preflight,
    advanced_plan_id,
    require_current_package_preflight,
    require_current_repository_preflight,
    verify_localtuya_canary_readback,
)

__all__ = [
    "AdvancedTaskApproval",
    "AdvancedTaskBlocked",
    "AdvancedTaskPackageApproval",
    "AdvancedTaskPlan",
    "AdvancedTaskRepositoryApproval",
    "LocalTuyaCanaryConfiguration",
    "LocalTuyaEntityConfiguration",
    "PostgresAdvancedTaskStore",
    "TuyaToLocalTuyaPlanner",
    "adopt_device_preflight",
    "advanced_plan_id",
    "require_current_package_preflight",
    "require_current_repository_preflight",
    "verify_localtuya_canary_readback",
]
