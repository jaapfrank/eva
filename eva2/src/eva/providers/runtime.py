"""Provider-owned runtime configuration behind generic bootstrap fields."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

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
from eva.adapters.connectors.home_assistant_localtuya import (
    HomeAssistantLocalTuyaAdapter,
)
from eva.adapters.connectors.home_assistant_management import (
    HomeAssistantConversationHacsPlanner,
    HomeAssistantConversationMigrationPlanner,
    HomeAssistantHacsManagementAdapter,
    HomeAssistantMigrationPlanAdapter,
)
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
from eva.bootstrap.secrets import environment_secret


def _value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _items(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in _value(name, default).split(",") if item.strip()}))


def _boolean(name: str, default: bool = False) -> bool:
    raw = _value(name, str(default).lower()).casefold()
    if raw not in {"false", "true"}:
        raise RuntimeError(f"{name} is invalid")
    return raw == "true"


@dataclass(frozen=True, slots=True)
class ExternalProviderSettings:
    endpoint_url: str
    credential: str
    effects_enabled: bool
    effect_allowlist: tuple[str, ...]
    effect_principal_sha256_allowlist: tuple[str, ...]
    event_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExternalProviderBundle:
    conversation_tools: tuple[ConversationToolDescriptor, ...]
    external_tools: tuple[ConversationExternalToolPort, ...]
    event_source: Any
    management_reader: Any
    management_writer: Any
    device_manager: Any


def load_external_provider_settings() -> ExternalProviderSettings | None:
    """Load the current provider without leaking its vocabulary into bootstrap."""

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
    return ExternalProviderSettings(
        endpoint_url=endpoint,
        credential=credential,
        effects_enabled=effects_enabled,
        effect_allowlist=effect_allowlist,
        effect_principal_sha256_allowlist=principals,
        event_domains=event_domains,
    )


def build_external_provider_bundle(
    settings: ExternalProviderSettings | None,
    engine: sa.Engine,
) -> ExternalProviderBundle | None:
    if settings is None:
        return None
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
    device_manager = HomeAssistantLocalTuyaAdapter(
        base_url=settings.endpoint_url,
        token=settings.credential,
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
    return ExternalProviderBundle(
        conversation_tools=(
            *HomeAssistantToolAdapter.conversation_tools(),
            *HomeAssistantConversationAutomationPlanner.conversation_tools(),
            *HomeAssistantConversationMigrationPlanner.conversation_tools(),
            *HomeAssistantConversationHacsPlanner.conversation_tools(),
        ),
        external_tools=tuple(external_tools),
        event_source=HomeAssistantEventStream(
            base_url=settings.endpoint_url,
            token=settings.credential,
            admitted_domains=settings.event_domains,
        ),
        management_reader=management_reader,
        management_writer=management_writer,
        device_manager=device_manager,
    )


__all__ = [
    "ExternalProviderBundle",
    "ExternalProviderSettings",
    "build_external_provider_bundle",
    "load_external_provider_settings",
]
