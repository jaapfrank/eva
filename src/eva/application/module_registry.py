"""Central ModuleRegistry for composing EvaModules into the single Living Core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eva.application.conversation import ConversationToolDescriptor
from eva.contracts.module import (
    EvaModule,
    ModuleHealth,
    ModulePolicyStatus,
    ModuleSpecification,
)
from eva.providers.contracts import ExternalProviderBundle, ProviderContribution


class ProviderModuleAdapter:
    """Adapt an existing ProviderContribution or factory into the uniform EvaModule contract."""

    def __init__(
        self,
        module_id: str,
        builder: Callable[[], ProviderContribution],
        *,
        execution_sources: tuple[str, ...] = (),
        optional_channel: bool = True,
    ) -> None:
        self._spec = ModuleSpecification(
            module_id=module_id,
            execution_sources=execution_sources,
            optional_channel=optional_channel,
        )
        self._builder = builder
        self._contribution: ProviderContribution | None = None
        self._status: ModulePolicyStatus | None = None

    @property
    def specification(self) -> ModuleSpecification:
        if self._contribution is not None:
            caps = tuple(t.name for t in self._contribution.conversation_tools)
            return ModuleSpecification(
                module_id=self._spec.module_id,
                capabilities=caps,
                execution_sources=self._spec.execution_sources,
                optional_channel=self._spec.optional_channel,
            )
        return self._spec

    def validate_policy(self, *, allow_inactive: bool = True) -> ModulePolicyStatus:
        try:
            if self._contribution is None:
                self._contribution = self._builder()
            self._status = ModulePolicyStatus(
                module_id=self._spec.module_id,
                state="active",
            )
            return self._status
        except Exception as exc:
            if not allow_inactive or not self._spec.optional_channel:
                raise
            self._status = ModulePolicyStatus(
                module_id=self._spec.module_id,
                state="inactive_policy",
                reason=str(exc),
            )
            return self._status

    def contribute_tools(self) -> tuple[ConversationToolDescriptor, ...]:
        status = self._status or self.validate_policy(allow_inactive=True)
        if not status.is_active or self._contribution is None:
            return ()
        return self._contribution.conversation_tools

    def contribute_routes(self) -> tuple[Any, ...]:
        status = self._status or self.validate_policy(allow_inactive=True)
        if not status.is_active or self._contribution is None:
            return ()
        return self._contribution.external_tools

    def contribution(self) -> ProviderContribution | None:
        status = self._status or self.validate_policy(allow_inactive=True)
        return self._contribution if status.is_active else None

    def healthcheck(self) -> ModuleHealth:
        status = self._status or self.validate_policy(allow_inactive=True)
        return ModuleHealth(
            module_id=self._spec.module_id,
            healthy=status.is_active,
            state=status.state,
            detail=status.reason,
        )


class AuthorityChannelModuleAdapter:
    """Wrap an optional channel authority (e.g. household_voice or household_research) as an EvaModule."""

    def __init__(
        self,
        module_id: str,
        authority: Any,
        *,
        capabilities: tuple[str, ...] = (),
        execution_sources: tuple[str, ...] = (),
    ) -> None:
        self._spec = ModuleSpecification(
            module_id=module_id,
            capabilities=capabilities,
            execution_sources=execution_sources,
            optional_channel=True,
        )
        self._authority = authority

    @property
    def specification(self) -> ModuleSpecification:
        return self._spec

    def validate_policy(self, *, allow_inactive: bool = True) -> ModulePolicyStatus:
        if self._authority is None:
            return ModulePolicyStatus(
                module_id=self._spec.module_id,
                state="inactive_policy",
                reason="authority_not_configured_or_expired",
            )
        validator = getattr(self._authority, "validate_current", None)
        if callable(validator):
            try:
                active = validator(allow_inactive=allow_inactive)
                if active is False:
                    return ModulePolicyStatus(
                        module_id=self._spec.module_id,
                        state="inactive_policy",
                        reason="policy_expired_or_revoked",
                    )
            except TypeError:
                try:
                    validator()
                except Exception as exc:
                    if not allow_inactive:
                        raise
                    return ModulePolicyStatus(
                        module_id=self._spec.module_id,
                        state="inactive_policy",
                        reason=str(exc),
                    )
        return ModulePolicyStatus(module_id=self._spec.module_id, state="active")

    def contribute_tools(self) -> tuple[ConversationToolDescriptor, ...]:
        return ()

    def contribute_routes(self) -> tuple[Any, ...]:
        return ()

    def healthcheck(self) -> ModuleHealth:
        status = self.validate_policy(allow_inactive=True)
        return ModuleHealth(
            module_id=self._spec.module_id,
            healthy=status.is_active,
            state=status.state,
            detail=status.reason,
        )


@dataclass(frozen=True, slots=True)
class ModuleRegistrySnapshot:
    """Aggregated view of all registered modules and their active contributions."""

    modules: tuple[ModuleHealth, ...]
    conversation_tools: tuple[ConversationToolDescriptor, ...]
    external_tools: tuple[Any, ...]


class ModuleRegistry:
    """Single registry managing all modular capabilities attached to the Eva Core."""

    def __init__(self) -> None:
        self._modules: dict[str, EvaModule] = {}

    def register(self, module: EvaModule) -> None:
        if not isinstance(module, EvaModule):
            raise TypeError("module must satisfy the EvaModule protocol")
        module_id = module.specification.module_id
        if module_id in self._modules:
            raise ValueError(f"Duplicate EvaModule registration: {module_id}")
        self._modules[module_id] = module

    def get(self, module_id: str) -> EvaModule | None:
        return self._modules.get(module_id)

    @property
    def modules(self) -> tuple[EvaModule, ...]:
        return tuple(self._modules.values())

    def snapshot(self, *, allow_inactive: bool = True) -> ModuleRegistrySnapshot:
        health_list: list[ModuleHealth] = []
        tools: list[ConversationToolDescriptor] = []
        routes: list[Any] = []
        seen_tools: set[str] = set()
        for module in self._modules.values():
            status = module.validate_policy(allow_inactive=allow_inactive)
            health_list.append(
                ModuleHealth(
                    module_id=module.specification.module_id,
                    healthy=status.is_active,
                    state=status.state,
                    detail=status.reason,
                )
            )
            if status.is_active:
                for tool in module.contribute_tools():
                    if tool.name in seen_tools:
                        raise RuntimeError(f"Duplicate module tool name: {tool.name}")
                    seen_tools.add(tool.name)
                    tools.append(tool)
                routes.extend(module.contribute_routes())
        return ModuleRegistrySnapshot(
            modules=tuple(health_list),
            conversation_tools=tuple(tools),
            external_tools=tuple(routes),
        )

    def build_provider_bundle(self, *, allow_inactive: bool = False) -> ExternalProviderBundle | None:
        """Build an ExternalProviderBundle compatible with existing runtime composition."""
        contributions: list[ProviderContribution] = []
        for module in self._modules.values():
            status = module.validate_policy(allow_inactive=allow_inactive)
            if not status.is_active:
                continue
            if isinstance(module, ProviderModuleAdapter):
                contrib = module.contribution()
                if contrib is not None:
                    contributions.append(contrib)
            else:
                tools = module.contribute_tools()
                routes = module.contribute_routes()
                if tools or routes:
                    contributions.append(
                        ProviderContribution(
                            provider_id=module.specification.module_id,
                            conversation_tools=tools,
                            external_tools=routes,
                        )
                    )
        if not contributions:
            return None
        conversation_tools: list[ConversationToolDescriptor] = []
        external_tools: list[Any] = []
        event_sources: list[Any] = []
        digital_action_names: list[str] = []
        for contribution in contributions:
            conversation_tools.extend(contribution.conversation_tools)
            external_tools.extend(contribution.external_tools)
            digital_action_names.extend(contribution.digital_action_names)
            if contribution.event_source is not None:
                event_sources.append(contribution.event_source)
        if len(event_sources) > 1:
            raise RuntimeError("multiple external sensory providers require a sidecar fan-in")
        return ExternalProviderBundle(
            conversation_tools=tuple(conversation_tools),
            external_tools=tuple(external_tools),
            event_source=None if not event_sources else event_sources[0],
            digital_action_names=tuple(digital_action_names),
        )


__all__ = [
    "AuthorityChannelModuleAdapter",
    "ModuleRegistry",
    "ModuleRegistrySnapshot",
    "ProviderModuleAdapter",
]
