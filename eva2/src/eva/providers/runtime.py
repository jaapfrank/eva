"""Provider-owned runtime configuration behind generic bootstrap fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
import os
import re
from typing import Any, Protocol

import sqlalchemy as sa

from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantToolAdapter,
)
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_automations import (
    HomeAssistantAutomationAdapter,
    HomeAssistantAutomationConfig,
    HomeAssistantConversationAutomationPlanner,
)
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.adapters.connectors.home_assistant_hacs import HomeAssistantHacsAdapter
from eva.adapters.connectors.home_assistant_management import (
    HomeAssistantConversationHacsPlanner,
    HomeAssistantConversationMigrationPlanner,
    HomeAssistantHacsManagementAdapter,
    HomeAssistantMigrationPlanAdapter,
)
from eva.adapters.connectors.agentmail import AgentMailConfig, AgentMailReadAdapter
from eva.providers.home_assistant_advanced_store import (
    PostgresAdvancedTaskStore,
)
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import (
    ConversationExternalToolPort,
    ConversationToolDescriptor,
)
from eva.application.sensory import SensoryEventSourcePort
from eva.bootstrap.secrets import environment_secret, read_runtime_secret_file


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


@dataclass(frozen=True, slots=True)
class HomeAutomationProviderSettings:
    endpoint_url: str
    credential: str = field(repr=False)
    effects_enabled: bool
    effect_allowlist: tuple[str, ...]
    effect_principal_sha256_allowlist: tuple[str, ...]
    event_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmailProviderSettings:
    inbox_id: str
    credential: str = field(repr=False)


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


def _load_home_automation_settings() -> HomeAutomationProviderSettings | None:
    endpoint = _value("EVA_HOME_ASSISTANT_URL")
    if "EVA_HOME_ASSISTANT_TOKEN" in os.environ:
        raise RuntimeError(
            "EVA_HOME_ASSISTANT_TOKEN is forbidden; use EVA_HOME_ASSISTANT_TOKEN_FILE"
        )
    credential = environment_secret(
        "EVA_HOME_ASSISTANT_TOKEN", "EVA_HOME_ASSISTANT_TOKEN_FILE"
    ).strip()
    if bool(endpoint) != bool(credential):
        raise RuntimeError("Eva external tool provider configuration is incomplete")
    effects_enabled = _boolean("EVA_HOME_ASSISTANT_EFFECTS_ENABLED")
    effect_allowlist = _items("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST")
    principals = _items(
        "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST"
    )
    for value in effect_allowlist:
        entity_id, separator, service = value.partition(":")
        domain, dot, object_id = entity_id.partition(".")
        if (
            separator != ":"
            or domain not in {"light", "switch"}
            or dot != "."
            or not object_id
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                for character in object_id
            )
            or service not in {"turn_on", "turn_off"}
        ):
            raise RuntimeError("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST is invalid")
    if effects_enabled and not effect_allowlist:
        raise RuntimeError(
            "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST is required when effects are enabled"
        )
    if effects_enabled and not principals:
        raise RuntimeError(
            "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST is required when effects are enabled"
        )
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in principals
    ):
        raise RuntimeError(
            "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST is invalid"
        )
    event_domains = _items(
        "EVA_HOME_ASSISTANT_EVENT_DOMAINS", "binary_sensor,light,person"
    )
    if not event_domains:
        raise RuntimeError("Eva external tool event domain set is empty")
    if not endpoint:
        if effects_enabled or effect_allowlist or principals:
            raise RuntimeError("Eva external tool provider configuration is incomplete")
        return None
    return HomeAutomationProviderSettings(
        endpoint_url=endpoint,
        credential=credential,
        effects_enabled=effects_enabled,
        effect_allowlist=effect_allowlist,
        effect_principal_sha256_allowlist=principals,
        event_domains=event_domains,
    )


def _load_email_settings() -> EmailProviderSettings | None:
    if "EVA_AGENTMAIL_API_KEY" in os.environ:
        raise RuntimeError(
            "EVA_AGENTMAIL_API_KEY is forbidden; use EVA_AGENTMAIL_API_KEY_FILE"
        )
    inbox_id = _value("EVA_AGENTMAIL_INBOX_ID")
    credential_file = _value("EVA_AGENTMAIL_API_KEY_FILE")
    if bool(inbox_id) != bool(credential_file):
        raise RuntimeError("Eva external e-mail provider configuration is incomplete")
    if not inbox_id:
        return None
    credential = read_runtime_secret_file(
        credential_file,
        setting_name="EVA_AGENTMAIL_API_KEY_FILE",
    )
    return EmailProviderSettings(inbox_id=inbox_id, credential=credential)


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


class _HomeAutomationFactory:
    provider_id = "home_assistant"

    @staticmethod
    def load_settings() -> HomeAutomationProviderSettings | None:
        return _load_home_automation_settings()

    @staticmethod
    def build(
        settings: HomeAutomationProviderSettings, engine: sa.Engine
    ) -> ProviderContribution:
        return _build_home_automation_bundle(settings, engine)


class _EmailFactory:
    provider_id = "agentmail"

    @staticmethod
    def load_settings() -> EmailProviderSettings | None:
        return _load_email_settings()

    @staticmethod
    def build(
        settings: EmailProviderSettings, engine: sa.Engine
    ) -> ProviderContribution:
        email = AgentMailReadAdapter(
            AgentMailConfig(
                inbox_id=settings.inbox_id,
                api_key=settings.credential,
            )
        )
        return ProviderContribution(
            provider_id="agentmail",
            conversation_tools=email.conversation_tools(),
            external_tools=(
                PostgresExternalConversationTools(
                    engine,
                    email.conversation_read_planner(),
                    email,
                ),
            ),
        )


def _provider_factories() -> tuple[RuntimeProviderFactory, ...]:
    # Import lazily because the generic gateway returns ProviderContribution.
    # The transport belongs to Eva's small provider boundary; every concrete
    # tool behind it remains external and file-configured.
    from eva.providers.mcp_gateway import McpGatewayProviderFactory

    builtins: tuple[RuntimeProviderFactory, ...] = (
        _HomeAutomationFactory(),
        _EmailFactory(),
        McpGatewayProviderFactory(),
    )
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


def _build_home_automation_bundle(
    settings: HomeAutomationProviderSettings,
    engine: sa.Engine,
) -> ProviderContribution:
    provider = HomeAssistantToolAdapter(
        HomeAssistantConfig(settings.endpoint_url, settings.credential)
    )
    management_reader = (
        HomeAssistantAdminAdapter(
            base_url=settings.endpoint_url,
            token=settings.credential,
        )
        if settings.effects_enabled
        else None
    )
    management_writer = (
        HomeAssistantHacsAdapter(
            base_url=settings.endpoint_url,
            token=settings.credential,
        )
        if settings.effects_enabled
        else None
    )
    automation = (
        HomeAssistantAutomationAdapter(
            HomeAssistantAutomationConfig(
                settings.endpoint_url,
                settings.credential,
            )
        )
        if settings.effects_enabled
        else None
    )
    def store_factory(tenant_id: str, installation_id: str) -> PostgresAdvancedTaskStore:
        return PostgresAdvancedTaskStore(
            engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )

    effect_allowlist = frozenset(
        tuple(value.split(":", 1)) for value in settings.effect_allowlist
    )
    effect_principals = frozenset(settings.effect_principal_sha256_allowlist)
    external_tools: list[ConversationExternalToolPort] = [
        PostgresExternalConversationTools(
            engine,
            provider.conversation_read_planner(),
            provider,
        )
    ]
    if settings.effects_enabled:
        external_tools.append(
            PostgresExternalConversationTools(
                engine,
                provider.conversation_effect_planner(
                    allowlist=effect_allowlist,
                    principal_sha256_allowlist=effect_principals,
                ),
                provider,
            )
        )
    if automation is not None:
        external_tools.append(
            PostgresExternalConversationTools(
                engine,
                HomeAssistantConversationAutomationPlanner(provider),
                automation,
            )
        )
    if management_reader is not None:
        external_tools.append(
            PostgresExternalConversationTools(
                engine,
                HomeAssistantConversationMigrationPlanner(management_reader),
                HomeAssistantMigrationPlanAdapter(store_factory),
            )
        )
    if management_reader is not None and management_writer is not None:
        external_tools.append(
            PostgresExternalConversationTools(
                engine,
                HomeAssistantConversationHacsPlanner(
                    management_reader, store_factory
                ),
                HomeAssistantHacsManagementAdapter(
                    management_writer,
                    management_reader,
                    store_factory,
                ),
            )
        )
    available_actions = {
        name for external_tool in external_tools for name in external_tool.action_names
    }
    all_descriptors = (
        *HomeAssistantToolAdapter.conversation_tools(),
        *HomeAssistantConversationAutomationPlanner.conversation_tools(),
        *HomeAssistantConversationMigrationPlanner.conversation_tools(),
        *HomeAssistantConversationHacsPlanner.conversation_tools(),
    )
    return ProviderContribution(
        provider_id="home_assistant",
        conversation_tools=tuple(
            descriptor
            for descriptor in all_descriptors
            if descriptor.name in available_actions
        ),
        external_tools=tuple(external_tools),
        event_source=HomeAssistantEventStream(
            base_url=settings.endpoint_url,
            token=settings.credential,
            admitted_domains=settings.event_domains,
        ),
    )


__all__ = [
    "ExternalProviderBundle",
    "ExternalProviderSettings",
    "EmailProviderSettings",
    "HomeAutomationProviderSettings",
    "LoadedProvider",
    "ProviderContribution",
    "RuntimeProviderFactory",
    "build_external_provider_bundle",
    "load_external_provider_settings",
]
