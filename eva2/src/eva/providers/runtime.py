"""Provider-owned runtime configuration behind generic bootstrap fields."""

from __future__ import annotations

from importlib import metadata
import os
import re

import sqlalchemy as sa

from eva.application.conversation import (
    ConversationExternalToolPort,
    ConversationToolDescriptor,
)
from eva.application.sensory import SensoryEventSourcePort
from eva.providers.contracts import (
    ExternalProviderBundle,
    ExternalProviderSettings,
    LoadedProvider,
    ProviderContribution,
    RuntimeProviderFactory,
)


def _value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _items(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in _value(name, default).split(",") if item.strip()}))


def _boolean(name: str, default: bool = False) -> bool:
    raw = _value(name, str(default).lower()).casefold()
    if raw not in {"false", "true"}:
        raise RuntimeError(f"{name} is invalid")
    return raw == "true"


_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_ENTRY_POINT_GROUP = "eva2.external_providers"


def load_external_provider_settings() -> ExternalProviderSettings | None:
    """Load explicit provider factories without teaching bootstrap their semantics."""

    loaded: list[LoadedProvider] = []
    for factory in _provider_factories():
        settings = factory.load_settings()
        if settings is not None:
            loaded.append(LoadedProvider(factory.provider_id, factory, settings))
    if not loaded:
        return None
    return ExternalProviderSettings(tuple(loaded))


def build_external_provider_bundle(
    settings: ExternalProviderSettings | None,
    engine: sa.Engine,
) -> ExternalProviderBundle | None:
    if settings is None:
        return None
    conversation_tools: list[ConversationToolDescriptor] = []
    external_tools: list[ConversationExternalToolPort] = []
    event_sources: list[SensoryEventSourcePort] = []
    provider_ids: set[str] = set()
    for loaded in settings.providers:
        if loaded.provider_id in provider_ids:
            raise RuntimeError("external provider ID is duplicated")
        contribution = loaded.factory.build(loaded.settings, engine)
        if contribution.provider_id != loaded.provider_id:
            raise RuntimeError("external provider contribution identity differs")
        provider_ids.add(loaded.provider_id)
        conversation_tools.extend(contribution.conversation_tools)
        external_tools.extend(contribution.external_tools)
        if contribution.event_source is not None:
            event_sources.append(contribution.event_source)
    _require_unique_tool_names(conversation_tools, external_tools)
    if len(event_sources) > 1:
        raise RuntimeError("multiple external sensory providers require a sidecar fan-in")
    return ExternalProviderBundle(
        conversation_tools=tuple(conversation_tools),
        external_tools=tuple(external_tools),
        event_source=None if not event_sources else event_sources[0],
    )


def _require_unique_tool_names(
    descriptors: list[ConversationToolDescriptor],
    external_tools: list[ConversationExternalToolPort],
) -> None:
    descriptor_names = [descriptor.name for descriptor in descriptors]
    action_names = [
        name for external_tool in external_tools for name in external_tool.action_names
    ]
    if len(descriptor_names) != len(set(descriptor_names)):
        raise RuntimeError("external provider tool descriptor is duplicated")
    if len(action_names) != len(set(action_names)):
        raise RuntimeError("external provider action route is duplicated")
    if set(descriptor_names) != set(action_names):
        raise RuntimeError("external provider catalog and action routes differ")


def _provider_factories() -> tuple[RuntimeProviderFactory, ...]:
    from eva.providers.bundled import bundled_provider_factories

    builtins = tuple(bundled_provider_factories())
    requested = _items("EVA_EXTERNAL_PROVIDER_PLUGINS")
    if any(_PROVIDER_ID.fullmatch(provider_id) is None for provider_id in requested):
        raise RuntimeError("EVA_EXTERNAL_PROVIDER_PLUGINS is invalid")
    builtin_ids = {factory.provider_id for factory in builtins}
    if builtin_ids.intersection(requested):
        raise RuntimeError("a built-in provider must not be requested as a plugin")
    discovered = _entry_point_factories(requested)
    factories = (*builtins, *discovered)
    ids = [factory.provider_id for factory in factories]
    if (
        any(_PROVIDER_ID.fullmatch(provider_id) is None for provider_id in ids)
        or len(ids) != len(set(ids))
    ):
        raise RuntimeError("external provider registry contains invalid identities")
    return factories


def _entry_point_factories(
    requested: tuple[str, ...],
) -> tuple[RuntimeProviderFactory, ...]:
    if not requested:
        return ()
    matches = [
        entry_point
        for entry_point in metadata.entry_points().select(group=_ENTRY_POINT_GROUP)
        if entry_point.name in requested
    ]
    if len({entry_point.name for entry_point in matches}) != len(matches):
        raise RuntimeError("an admitted external provider plugin is duplicated")
    selected = {entry_point.name: entry_point for entry_point in matches}
    if set(selected) != set(requested):
        raise RuntimeError("an admitted external provider plugin is not installed")
    factories: list[RuntimeProviderFactory] = []
    for provider_id in requested:
        loaded = selected[provider_id].load()
        factory = loaded() if isinstance(loaded, type) else loaded
        if (
            getattr(factory, "provider_id", None) != provider_id
            or not callable(getattr(factory, "load_settings", None))
            or not callable(getattr(factory, "build", None))
        ):
            raise RuntimeError("external provider plugin contract is invalid")
        factories.append(factory)
    return tuple(factories)


__all__ = [
    "ExternalProviderBundle",
    "ExternalProviderSettings",
    "LoadedProvider",
    "ProviderContribution",
    "RuntimeProviderFactory",
    "build_external_provider_bundle",
    "load_external_provider_settings",
]
