"""Optional Home Assistant provider plug-in behind Eva's generic tool boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import hashlib

import sqlalchemy as sa

from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantToolAdapter,
)
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter, HomeAssistantHouseholdTool
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
from eva.adapters.credentials.runtime_files import environment_secret
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import ConversationExternalToolPort, ConversationConflict
from eva.providers.home_assistant_advanced_store import PostgresAdvancedTaskStore
from eva.providers.contracts import ProviderContribution
from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.connectors.local_vision import LocalVisionHttpAnalyzer
from eva.providers.home_assistant_capabilities import HomeAssistantCapabilityProfile
from eva.adapters.connectors.home_assistant_context import HomeAssistantDeviceContext
from eva.providers.home_assistant_command_cache import HomeAssistantCommandCache
from eva.contracts.canonical_json import canonical_sha256


def _value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _items(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        sorted({item.strip() for item in _value(name, default).split(",") if item.strip()})
    )


def _boolean(name: str, default: bool = False) -> bool:
    raw = _value(name, str(default).lower()).casefold()
    if raw not in {"false", "true"}:
        raise RuntimeError(f"{name} is invalid")
    return raw == "true"


@dataclass(frozen=True, slots=True)
class HomeAssistantProviderSettings:
    endpoint_url: str
    credential: str = field(repr=False)
    effects_enabled: bool
    effect_allowlist: tuple[str, ...]
    effect_principal_sha256_allowlist: tuple[str, ...]
    event_domains: tuple[str, ...]
    api_enabled: bool = False
    vision_endpoint: str = ""
    vision_model: str = ""
    vision_revision: str = ""


def home_assistant_read_configuration_sha256(endpoint_url: str) -> str:
    """Host-selected destination and read protocol; no token or model-supplied scope."""
    return canonical_sha256({"provider": "home_assistant", "protocol": "existing-read-v1",
                             "endpoint_url": endpoint_url.rstrip("/")})


class HomeAssistantProviderFactory:
    provider_id = "home_assistant"
    supports_digital_work = True
    supports_household_digital_work = True
    execution_sources = (
        "providers/home_assistant.py", "providers/home_assistant_policy.py",
        "providers/home_assistant_capabilities.py",
        "providers/home_assistant_command_cache.py",
        "adapters/connectors/home_assistant_context.py",
        "adapters/connectors/home_assistant_mcp.py",
        "providers/home_assistant_receipts.py", "providers/receipt_policies.py",
        "adapters/connectors/home_assistant_completion.py",
        "adapters/connectors/home_assistant.py",
        "adapters/connectors/home_assistant_api.py",
        "adapters/connectors/home_assistant_service_query.py",
        "adapters/connectors/home_assistant_admin.py",
        "adapters/connectors/home_assistant_automations.py",
    )

    @staticmethod
    def build_owner_media(provider_settings, runtime_settings, **identity):
        from eva.bootstrap.dashboard_voice import build_dashboard_voice
        return build_dashboard_voice(runtime_settings, **identity)

    @staticmethod
    def build_services(settings, context):
        from eva.providers.contracts import ProviderServices
        if context.work_server is None:
            return ProviderServices()
        from eva.bootstrap.native_work_delivery import NativeWorkDelivery
        tasks = [NativeWorkDelivery(context.work_server, settings).run_once]
        owns_sensory = False
        if (context.principal_id is not None and context.shared_output is not None
                and context.budget is not None):
            from eva.bootstrap.household_initiative import HouseholdInitiativeLoop
            loop = HouseholdInitiativeLoop(
                engine=context.engine, voice=context.shared_output,
                server=context.work_server, provider=settings, model=context.model,
                memory=context.memory, identity=context.identity, budget=context.budget,
                principal_id=context.principal_id,
                authorize_initiative=context.current_authority,
                source_store=context.source_store,
                can_decide=lambda: not context.foreground_active())
            tasks.append(loop.run_once)
            owns_sensory = context.source_store is not None
        return ProviderServices(tuple(tasks), owns_sensory)


    @staticmethod
    def load_settings() -> HomeAssistantProviderSettings | None:
        if "home_assistant" in _items("EVA_EXTERNAL_PROVIDERS_DISABLED"):
            return None
        endpoint = _value("EVA_HOME_ASSISTANT_URL")
        if "EVA_HOME_ASSISTANT_TOKEN" in os.environ:
            raise RuntimeError(
                "EVA_HOME_ASSISTANT_TOKEN is forbidden; use "
                "EVA_HOME_ASSISTANT_TOKEN_FILE"
            )
        credential = environment_secret(
            "EVA_HOME_ASSISTANT_TOKEN", "EVA_HOME_ASSISTANT_TOKEN_FILE"
        ).strip()
        if bool(endpoint) != bool(credential):
            raise RuntimeError("Eva external tool provider configuration is incomplete")
        effects_enabled = _boolean("EVA_HOME_ASSISTANT_EFFECTS_ENABLED")
        effect_allowlist = _items("EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST")
        principals = _items("EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST")
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
                "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST is required when effects "
                "are enabled"
            )
        if effects_enabled and not principals:
            raise RuntimeError(
                "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST is required "
                "when effects are enabled"
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
        vision_values = tuple(_value(name) for name in (
            "EVA_LOCAL_VISION_URL", "EVA_LOCAL_VISION_MODEL", "EVA_LOCAL_VISION_REVISION"))
        if any(vision_values):
            if not all(vision_values) or not endpoint or not _boolean("EVA_HOME_ASSISTANT_API_ENABLED"):
                raise RuntimeError("Local vision requires complete model settings and the HA API")
            LocalVisionHttpAnalyzer(endpoint=vision_values[0], model=vision_values[1], revision=vision_values[2])
        if not endpoint:
            if effects_enabled or effect_allowlist or principals:
                raise RuntimeError("Eva external tool provider configuration is incomplete")
            return None
        return HomeAssistantProviderSettings(
            endpoint_url=endpoint,
            credential=credential,
            effects_enabled=effects_enabled,
            effect_allowlist=effect_allowlist,
            effect_principal_sha256_allowlist=principals,
            event_domains=event_domains,
            api_enabled=_boolean("EVA_HOME_ASSISTANT_API_ENABLED"),
            vision_endpoint=vision_values[0],
            vision_model=vision_values[1],
            vision_revision=vision_values[2],
        )

    @staticmethod
    def build(
        settings: HomeAssistantProviderSettings, engine: sa.Engine
    ) -> ProviderContribution:
        capability_profile = HomeAssistantCapabilityProfile(settings)
        reuse_configuration = canonical_sha256({"version": "ha-exact-command-reuse-v1",
            "endpoint": settings.endpoint_url, "credential_sha256": hashlib.sha256(settings.credential.encode()).hexdigest(),
            "effects": settings.effects_enabled, "allowlist": list(settings.effect_allowlist),
            "principals": list(settings.effect_principal_sha256_allowlist),
            "setter_schemas": [tool.input_schema for tool in HomeAssistantToolAdapter.conversation_tools()
                               if tool.name in {"set_home_assistant_light", "set_home_assistant_switch"}]})
        device_context = HomeAssistantDeviceContext(scope_sha256=reuse_configuration)
        inventory_reader = HomeAssistantAdminAdapter(
            base_url=settings.endpoint_url, token=settings.credential,
            device_context=device_context,
        )
        provider = HomeAssistantToolAdapter(
            HomeAssistantConfig(settings.endpoint_url, settings.credential),
            household_inventory=inventory_reader.household_inventory,
            capability_profile=capability_profile,
            device_context=device_context,
        )
        provider._command_cache = HomeAssistantCommandCache(engine,
            configuration_sha256=reuse_configuration, device_context=device_context,
            capability_profile=capability_profile)
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

        def store_factory(
            tenant_id: str, installation_id: str
        ) -> PostgresAdvancedTaskStore:
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
                configuration_sha256=home_assistant_read_configuration_sha256(settings.endpoint_url),
            )
        ]
        household = HomeAssistantHouseholdTool(inventory_reader)

        def authorize_household(turn):
            from eva.application.initiative_execution import require_initiative_context
            native = require_initiative_context(turn)
            if native is not None and native.action_allowed("read_home_assistant_inventory"):
                # The distinct host-owned native read grant verifies the current
                # owner binding; actual destination/arguments are checked at dispatch.
                # It does not add the owner to the ordinary HA effect allowlist.
                return
            if (
                turn.audience.kind not in {"private_person", "household"}
                or turn.audience.principal_id != turn.principal_id
                or hashlib.sha256(turn.principal_id.encode()).hexdigest() not in effect_principals
            ):
                raise ConversationConflict("household inventory is unavailable for this audience")

        external_tools.append(PostgresExternalConversationTools(
            engine, household, household, authorize_turn=authorize_household,
            configuration_sha256=home_assistant_read_configuration_sha256(settings.endpoint_url),
        ))
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
        if settings.api_enabled:
            vision = (LocalVisionHttpAnalyzer(endpoint=settings.vision_endpoint,
                      model=settings.vision_model, revision=settings.vision_revision)
                      if settings.vision_endpoint else None)
            api = HomeAssistantApiTool(base_url=settings.endpoint_url, token=settings.credential, vision=vision, capability_profile=capability_profile)
            api_configuration = home_assistant_read_configuration_sha256(settings.endpoint_url)
            if vision is not None:
                api_configuration = canonical_sha256({"home_assistant": api_configuration,
                    "vision_endpoint": settings.vision_endpoint, "vision_model": settings.vision_model,
                    "vision_revision": settings.vision_revision})

            def authorize_api(turn):
                from eva.application.initiative_execution import require_initiative_context
                native = require_initiative_context(turn)
                if native is not None and native.action_allowed("home_assistant_request"):
                    # The durable initiative guard still checks the exact read
                    # profile and current mandate at dispatch and observation.
                    return
                if (turn.audience.kind not in {"private_person", "household"}
                    or turn.audience.principal_id != turn.principal_id
                    or hashlib.sha256(turn.principal_id.encode()).hexdigest() not in effect_principals):
                    raise ConversationConflict("General Home Assistant API requires an authorized bound audience")

            external_tools.append(PostgresExternalConversationTools(
                engine, api, api, authorize_turn=authorize_api,
                configuration_sha256=api_configuration,
            ))
        available_actions = {
            name for external_tool in external_tools for name in external_tool.action_names
        }
        all_descriptors = (
            *HomeAssistantApiTool.conversation_tools(),
            *HomeAssistantHouseholdTool.conversation_tools(),
            *HomeAssistantToolAdapter.conversation_tools(),
            *HomeAssistantConversationAutomationPlanner.conversation_tools(),
            *HomeAssistantConversationMigrationPlanner.conversation_tools(),
            *HomeAssistantConversationHacsPlanner.conversation_tools(),
        )
        return ProviderContribution(
            provider_id=HomeAssistantProviderFactory.provider_id,
            conversation_tools=tuple(
                descriptor
                for descriptor in all_descriptors
                if descriptor.name in available_actions
            ),
            external_tools=tuple(external_tools),
            digital_action_names=tuple(sorted(available_actions & {
                "home_assistant_request", "read_home_assistant_inventory"})),
            event_source=HomeAssistantEventStream(
                base_url=settings.endpoint_url,
                token=settings.credential,
                admitted_domains=settings.event_domains,
                device_context=device_context,
            ),
        )


__all__ = ["HomeAssistantProviderFactory", "HomeAssistantProviderSettings"]
