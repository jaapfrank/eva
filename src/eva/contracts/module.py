"""Uniform modular extension contract for the single Eva Living Core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from eva.application.conversation import ConversationToolDescriptor

ModuleState = Literal["active", "inactive_policy", "degraded", "disabled"]


@dataclass(frozen=True, slots=True)
class ModulePolicyStatus:
    """Result of validating an optional or core module's authority policy."""

    module_id: str
    state: ModuleState
    reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.state == "active"


@dataclass(frozen=True, slots=True)
class ModuleHealth:
    """Health snapshot reported by a registered EvaModule."""

    module_id: str
    healthy: bool
    state: ModuleState
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleSpecification:
    """Immutable metadata describing a modular capability attached to the Core."""

    module_id: str
    capabilities: tuple[str, ...] = ()
    execution_sources: tuple[str, ...] = ()
    optional_channel: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.module_id, str) or not self.module_id.strip():
            raise ValueError("module_id is required")


@runtime_checkable
class EvaModule(Protocol):
    """Standard plugin interface for all capabilities attached to Eva's Core."""

    @property
    def specification(self) -> ModuleSpecification:
        """Return immutable module identity, capabilities, and source bindings."""

    def validate_policy(self, *, allow_inactive: bool = True) -> ModulePolicyStatus:
        """Validate module authority; inactive optional policies return inactive_policy."""

    def contribute_tools(self) -> tuple[ConversationToolDescriptor, ...]:
        """Return model-visible tool descriptors when the module is active."""

    def contribute_routes(self) -> tuple[Any, ...]:
        """Return external tool execution routes when the module is active."""

    def healthcheck(self) -> ModuleHealth:
        """Report current module readiness without crashing the Core."""


__all__ = [
    "EvaModule",
    "ModuleHealth",
    "ModulePolicyStatus",
    "ModuleSpecification",
    "ModuleState",
]
