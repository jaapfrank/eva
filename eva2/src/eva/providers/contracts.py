"""Neutral contracts shared by runtime composition and provider plug-ins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

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


class RuntimeProviderFactory(Protocol):
    provider_id: str

    def load_settings(self) -> Any | None: ...

    def build(self, settings: Any, engine: sa.Engine) -> ProviderContribution: ...


@dataclass(frozen=True, slots=True)
class ExternalProviderBundle:
    conversation_tools: tuple[ConversationToolDescriptor, ...]
    external_tools: tuple[ConversationExternalToolPort, ...]
    event_source: SensoryEventSourcePort | None


__all__ = [
    "ExternalProviderBundle",
    "ExternalProviderSettings",
    "LoadedProvider",
    "ProviderContribution",
    "RuntimeProviderFactory",
]
