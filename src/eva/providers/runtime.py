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
    ProviderServices,
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
    digital_action_names: list[str] = []
    for loaded in settings.providers:
        if loaded.provider_id in provider_ids:
            raise RuntimeError("external provider ID is duplicated")
        contribution = loaded.factory.build(loaded.settings, engine)
        if contribution.provider_id != loaded.provider_id:
            raise RuntimeError("external provider contribution identity differs")
        provider_ids.add(loaded.provider_id)
        conversation_tools.extend(contribution.conversation_tools)
        external_tools.extend(contribution.external_tools)
        if not set(contribution.digital_action_names).issubset(
                tool.name for tool in contribution.conversation_tools):
            raise RuntimeError("provider digital catalog differs")
        digital_action_names.extend(contribution.digital_action_names)
        if contribution.event_source is not None:
            event_sources.append(contribution.event_source)
    _require_unique_tool_names(conversation_tools, external_tools)
    if len(event_sources) > 1:
        raise RuntimeError("multiple external sensory providers require a sidecar fan-in")
    return ExternalProviderBundle(
        conversation_tools=tuple(conversation_tools),
        external_tools=tuple(external_tools),
        event_source=None if not event_sources else event_sources[0],
        digital_action_names=tuple(digital_action_names),
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
    from eva.providers.bundled import bundled_provider_factories, bundled_provider_ids

    disabled = _items("EVA_EXTERNAL_PROVIDERS_DISABLED")
    if any(_PROVIDER_ID.fullmatch(value) is None for value in disabled):
        raise RuntimeError("EVA_EXTERNAL_PROVIDERS_DISABLED is invalid")
    builtins = tuple(bundled_provider_factories(exclude=disabled))
    requested = _items("EVA_EXTERNAL_PROVIDER_PLUGINS")
    if any(_PROVIDER_ID.fullmatch(provider_id) is None for provider_id in requested):
        raise RuntimeError("EVA_EXTERNAL_PROVIDER_PLUGINS is invalid")
    builtin_ids = bundled_provider_ids()
    if builtin_ids.intersection(requested):
        raise RuntimeError("a built-in provider must not be requested as a plugin")
    if set(disabled) - (builtin_ids | set(requested)):
        raise RuntimeError("disabled external provider is not registered")
    discovered = _entry_point_factories(tuple(name for name in requested if name not in disabled))
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


def build_provider_owner_media(provider_settings, runtime_settings, **identity):
    media = []
    for loaded in (() if provider_settings is None else provider_settings.providers):
        build = getattr(loaded.factory, "build_owner_media", None)
        if callable(build):
            item = build(loaded.settings, runtime_settings, **identity)
            if item is not None:
                media.append(item)
    if len(media) > 1:
        raise RuntimeError("multiple owner media providers are not selected")
    return media[0] if media else None


def build_provider_services(settings, context):
    tasks, owns_sensory = [], False
    for loaded in (() if settings is None else settings.providers):
        build = getattr(loaded.factory, "build_services", None)
        if not callable(build):
            continue
        contribution = build(loaded.settings, context)
        if not isinstance(contribution, ProviderServices):
            raise RuntimeError("provider service contribution differs")
        if contribution.owns_sensory_decisions and owns_sensory:
            raise RuntimeError("multiple providers claim sensory decisions")
        owns_sensory |= contribution.owns_sensory_decisions
        tasks.extend(contribution.tasks)
    return ProviderServices(tuple(tasks), owns_sensory)


def load_provider_digital_settings():
    configured = load_external_provider_settings()
    candidates = [item.settings for item in (() if configured is None else configured.providers)
                  if getattr(item.factory, "supports_household_digital_work", False) is True]
    if len(candidates) > 1:
        raise RuntimeError("multiple household digital providers are not selected")
    return candidates[0] if candidates else None
