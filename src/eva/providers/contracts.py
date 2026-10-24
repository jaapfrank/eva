"""Neutral contracts shared by runtime composition and provider plug-ins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from collections.abc import Callable

import sqlalchemy as sa

from eva.application.conversation import (
    ConversationExternalToolPort,
    ConversationToolDescriptor,
)
from eva.application.sensory import SensoryEventSourcePort


@dataclass(frozen=True, slots=True)
class ExternalProviderSettings:
    providers: tuple[LoadedProvider, ...]


@dataclass(frozen=True, slots=True)
class LoadedProvider:
    provider_id: str
    factory: RuntimeProviderFactory = field(repr=False)
    settings: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderContribution:
    provider_id: str
    conversation_tools: tuple[ConversationToolDescriptor, ...]
    external_tools: tuple[ConversationExternalToolPort, ...]
    event_source: SensoryEventSourcePort | None = None
    digital_action_names: tuple[str, ...] = ()


class RuntimeProviderFactory(Protocol):
    provider_id: str

    def load_settings(self) -> Any | None: ...

    def build(self, settings: Any, engine: sa.Engine) -> ProviderContribution: ...


@dataclass(frozen=True, slots=True)
class ExternalProviderBundle:
    conversation_tools: tuple[ConversationToolDescriptor, ...]
    external_tools: tuple[ConversationExternalToolPort, ...]
    event_source: SensoryEventSourcePort | None
    digital_action_names: tuple[str, ...] = ()


__all__ = [
    "ExternalProviderBundle",
    "ExternalProviderSettings",
    "LoadedProvider",
    "ProviderContribution",
    "RuntimeProviderFactory",
]


@dataclass(frozen=True, slots=True)
class ProviderServiceContext:
    engine: Any
    work_server: Any
    shared_output: Any
    model: Any
    memory: Any
    identity: Any
    budget: Any
    principal_id: str | None
    current_authority: Callable[[], Any]
    source_store: Any
    foreground_active: Callable[[], bool]
    household_provision_factory: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderServices:
    tasks: tuple[Callable[[], Any], ...] = ()
    owns_sensory_decisions: bool = False
