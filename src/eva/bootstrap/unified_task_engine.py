"""Unified task engine for Eva2 background work specs.

Consolidates routing, spec classification, and runner dispatch across all
admitted background task contracts:
- ``DelegatedWorkSpec`` (tool-free child work)
- ``DelegatedResearchSpec`` (private public-source research)
- ``DelegatedHouseholdResearchSpec`` (household public-source research)
- ``DelegatedDigitalWorkSpec`` (private connector & programming work)
- ``DelegatedHouseholdDigitalWorkSpec`` (household connector work)
- ``DelegatedInitiativeDigitalWorkSpec`` (autonomous initiative, AIDE², E4.15 & E4.16 self-repair)

Preserves all existing ``DelegatedWorkReleaseSelector`` hashes, authority checks,
and durable PostgreSQL leases without introducing a second queue or daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eva.contracts.delegated_digital_work import (
    DelegatedDigitalWorkSpec,
    DelegatedHouseholdDigitalWorkSpec,
    DelegatedInitiativeDigitalWorkSpec,
)
from eva.contracts.delegated_work import (
    DelegatedHouseholdResearchSpec,
    DelegatedResearchSpec,
    DelegatedWorkSpec,
)


@dataclass(frozen=True, slots=True)
class TaskRouteClassification:
    """Resolved execution lane and audience for a background work specification."""

    contract: str
    lane: str  # "digital" | "research" | "child"
    audience: str  # "private" | "household" | "initiative"
    spec_type_name: str


def classify_task_spec(spec: DelegatedWorkSpec) -> TaskRouteClassification:
    """Classify any admitted ``DelegatedWorkSpec`` into its execution lane and audience."""

    if isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="digital",
            audience="initiative",
            spec_type_name=type(spec).__name__,
        )
    if isinstance(spec, DelegatedHouseholdDigitalWorkSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="digital",
            audience="household",
            spec_type_name=type(spec).__name__,
        )
    if isinstance(spec, DelegatedDigitalWorkSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="digital",
            audience="private",
            spec_type_name=type(spec).__name__,
        )
    if isinstance(spec, DelegatedHouseholdResearchSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="research",
            audience="household",
            spec_type_name=type(spec).__name__,
        )
    if isinstance(spec, DelegatedResearchSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="research",
            audience="private",
            spec_type_name=type(spec).__name__,
        )
    if isinstance(spec, DelegatedWorkSpec):
        return TaskRouteClassification(
            contract=spec.contract,
            lane="child",
            audience=getattr(spec, "audience", "private"),
            spec_type_name=type(spec).__name__,
        )
    raise TypeError(f"unsupported delegated work spec type: {type(spec).__name__}")


class UnifiedTaskEngine:
    """Single task dispatcher across child, research, household, digital, and initiative runners."""

    def __init__(
        self,
        *,
        existing_runner: Any,
        digital_runner: Any | None = None,
        research_runner: Any | None = None,
    ) -> None:
        if existing_runner is None:
            raise ValueError("UnifiedTaskEngine requires an existing base runner")
        self.existing = existing_runner
        self.digital = digital_runner
        self.research = research_runner

    def select_runner(self, spec: DelegatedWorkSpec) -> Any:
        """Select the runner owning execution for ``spec``."""

        if isinstance(spec, DelegatedDigitalWorkSpec):
            if self.digital is None:
                raise RuntimeError("digital work runner is not configured on UnifiedTaskEngine")
            return self.digital
        if isinstance(spec, DelegatedResearchSpec) and self.research is not None:
            return self.research
        return self.existing

    async def run(
        self,
        spec: DelegatedWorkSpec,
        *,
        call_id: str,
        cancellation_event: Any = None,
    ) -> Any:
        runner = self.select_runner(spec)
        return await runner.run(
            spec,
            call_id=call_id,
            cancellation_event=cancellation_event,
        )


__all__ = [
    "TaskRouteClassification",
    "UnifiedTaskEngine",
    "classify_task_spec",
]
