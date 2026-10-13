"""Read-only capacity gate for Eva's existing optional idle workers."""
from datetime import UTC, datetime
import math
import os
from pathlib import Path


def spare_compute_available() -> bool:
    """Compare real load with visible CPU capacity, including a container quota."""
    try:
        cpus = float(len(os.sched_getaffinity(0)))
        quota = Path("/sys/fs/cgroup/cpu.max")
        if quota.exists():
            maximum, period = quota.read_text().split()
            if maximum != "max":
                cpus = min(cpus, int(maximum) / int(period))
        load = os.getloadavg()[0]
        return math.isfinite(load) and load >= 0 and cpus > 0 and load < cpus
    except (OSError, AttributeError, ValueError, ZeroDivisionError):
        return False


def idle_capacity_available(*, model, reflection_budget, delegated_work_store, initiative_capacity=None) -> bool:
    # Subscription credits are not inferred from machine idleness. Existing
    # model admission and real per-operation budgets still apply at execution.
    if model.autonomous_admission_blocker() is not None:
        return False
    reflection_available = (reflection_budget is not None
        and reflection_budget.has_installation_capacity(now=datetime.now(UTC).replace(microsecond=0)))
    if not reflection_available:
        try:
            if initiative_capacity is None or initiative_capacity() is not True:
                return False
        except Exception:
            return False  # Missing, changed or revoked private authority never starts work.
    if (delegated_work_store is not None
            and delegated_work_store.has_pending_or_running_work()):
        return False
    return spare_compute_available()
