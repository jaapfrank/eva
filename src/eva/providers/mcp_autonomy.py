"""Opt-in composition of owner-admitted MCP registration and tool execution.

The fixed meta tools reuse the normal conversation journal and effect pipeline.
Neither discovery text nor a model-supplied source object creates an owner turn.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
import os

from sqlalchemy.engine import Engine

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
from eva.adapters.connectors.browser_autostart import BrowserAutostartConfig, ManagedBrowserAutostart
from eva.adapters.connectors.browser_session import BrowserSessionClient
from eva.adapters.connectors.mcp_registration import (
    ContextForgeRegistrationClient, McpRegistrationConfig,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, TimelineNotFound, external_timeline_scope,
)
from eva.application.conversation import (
    ConversationConflict, ConversationObservationBinding, ConversationTurn,
)
from eva.providers.contracts import ProviderContribution
from eva.providers.mcp_discovery import McpDiscoveryToolAdapter
from eva.providers.mcp_registration import McpRegistrationToolAdapter, connector_id_for
from eva.providers.mcp_gateway import McpGatewayProviderFactory, McpGatewayProviderSettings


@dataclass(frozen=True, slots=True)
class McpAutonomySettings:
    mandate_file: str
    gateway: McpGatewayConfig
    registration: McpRegistrationConfig
    managed_endpoints_file: str | None = None
    configured_catalog: McpGatewayProviderSettings | None = None
    browser_autostart: BrowserAutostartConfig | None = None


def _value(name: str) -> str:
    return os.environ.get(name, "").strip()


class _SourceTurnLoader:
    def __init__(self, engine: Engine, authority) -> None:
        self._authority = authority
        self._timeline = PostgresOperationalTimeline(engine, authority.scope)

    def __call__(self, source: dict) -> ConversationTurn:
        fields = {"tenant_id", "installation_id", "principal_id", "conversation_id", "message_id"}
        if (not isinstance(source, dict) or set(source) != fields | {"audience"}
                or any(not isinstance(source[key], str) or not source[key] for key in fields)):
            raise ConversationConflict("connector source reference is invalid")
        if source["conversation_id"].startswith("initiative-task:"):
            raise ConversationConflict("native initiative is not an owner connector instruction")
        # The ingress journal predates audience storage. The real-turn planner
        # authorizes this private audience before sealing the durable request;
        # replay retains that attestation rather than inferring it from names.
        if (external_timeline_scope(source["tenant_id"], source["installation_id"],
                    "connector source scope is required") != self._authority.scope
                or source["audience"] != {"kind": "private_person",
                                          "principal_id": source["principal_id"]}):
            raise ConversationConflict("connector source is not a private owner ingress")
        thread_id = "conversation:" + source["message_id"]
        try:
            self._timeline.load(thread_id)  # Retain the ordinary corruption check.
        except TimelineNotFound:
            raise ConversationConflict("connector source ingress is absent") from None
        events = self._timeline.events(thread_id)
        if not events or events[0].event_type != "INGRESS_COMMITTED":
            raise ConversationConflict("connector source ingress is absent")
        event = events[0]
        data = event.payload.get("data")
        if (not isinstance(data, dict) or data.get("kind") != "conversation_ingress"
                or any(data.get(key) != source[key] for key in
                       ("principal_id", "conversation_id", "message_id"))):
            raise ConversationConflict("connector stored ingress differs")
        try:
            observation = data.get("observation_binding")
            binding = None
            if observation is not None:
                if not isinstance(observation, dict):
                    raise ValueError("invalid observation")
                binding = ConversationObservationBinding(**{
                    key: value for key, value in observation.items() if key != "binding_sha256"
                })
                if binding.binding_sha256 != observation.get("binding_sha256"):
                    raise ValueError("invalid observation digest")
            turn = ConversationTurn.private(
                source["tenant_id"], source["installation_id"], source["principal_id"],
                source["conversation_id"], source["message_id"], data["text"],
                event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                project_id=data.get("project_id"), observation_binding=binding,
                media_context_sha256=data.get("media_context_sha256"),
            )
            if PostgresConversationJournal._ingress_data(turn) != data:
                raise ValueError("stored ingress fields differ")
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ConversationConflict("connector stored ingress is invalid") from None
        self._authority.authorize_turn(turn)
        return turn


class McpAutonomyProviderFactory:
    provider_id = "mcp_autonomy"

    @staticmethod
    def load_settings() -> McpAutonomySettings | None:
        mandate = _value("EVA_MCP_AUTONOMY_MANDATE_FILE")
        registration_file = _value("EVA_MCP_REGISTRATION_TOKEN_FILE")
        visibility = _value("EVA_MCP_REGISTRATION_VISIBILITY")
        exclusive = _value("EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY")
        managed = _value("EVA_MCP_MANAGED_ENDPOINTS_FILE")
        browser_endpoint = _value("EVA_BROWSER_MCP_ENDPOINT")
        if not any((mandate, registration_file, visibility, exclusive, managed, browser_endpoint)):
            return None
        if "EVA_MCP_REGISTRATION_TOKEN" in os.environ or "EVA_MCP_GATEWAY_TOKEN" in os.environ:
            raise RuntimeError("MCP autonomy credentials require private runtime files")
        base_url, token_file = _value("EVA_MCP_GATEWAY_URL"), _value("EVA_MCP_GATEWAY_TOKEN_FILE")
        plain = _value("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP").casefold()
        if (not all((mandate, registration_file, base_url, token_file))
                or plain not in {"true", "false"} or exclusive not in {"", "true", "false"}):
            raise RuntimeError("MCP autonomy configuration is incomplete or invalid")
        gateway = McpGatewayConfig(base_url, read_runtime_secret_file(token_file,
            setting_name="EVA_MCP_GATEWAY_TOKEN_FILE"), allow_plain_http=plain == "true")
        registration = McpRegistrationConfig(base_url, read_runtime_secret_file(registration_file,
            setting_name="EVA_MCP_REGISTRATION_TOKEN_FILE"), allow_plain_http=plain == "true",
            visibility=visibility or "private", exclusive_owner_gateway=exclusive == "true")
        browser = None
        if browser_endpoint:
            control_url = _value("EVA_BROWSER_CONTROL_URL")
            control_file = _value("EVA_BROWSER_CONTROL_TOKEN_FILE")
            if not control_url or not control_file:
                raise RuntimeError("managed browser autostart configuration is incomplete")
            browser = BrowserAutostartConfig(browser_endpoint, BrowserSessionClient(
                control_url, read_runtime_secret_file(control_file,
                    setting_name="EVA_BROWSER_CONTROL_TOKEN_FILE")))
        return McpAutonomySettings(mandate, gateway, registration, managed or None,
                                   McpGatewayProviderFactory.load_settings(), browser)

    @staticmethod
    def build(settings: McpAutonomySettings, engine: Engine) -> ProviderContribution:
        # No mandate file means no import, no authority object, and no meta tools.
        from eva.providers.mcp_owner_authority import McpOwnerAuthority

        from eva.providers.private_owner_authority import configured_private_owner_resolver

        authority = McpOwnerAuthority(engine, settings.mandate_file, settings.managed_endpoints_file,
            private_owner_resolver=configured_private_owner_resolver(
                engine, tenant_id=_value("EVA_RUNTIME_TENANT_ID"),
                installation_id=_value("EVA_RUNTIME_INSTALLATION_ID")))
        registry = PostgresMcpConnectorRegistry(engine, authority.scope,
            authority.owner_principal_id, settings.gateway,
            authority.authorize_turn, authority.authorize_current,
            canonical_owner=authority.canonical_owner,
            configured_catalog=settings.configured_catalog)
        registration = McpRegistrationToolAdapter(
            ContextForgeRegistrationClient(settings.registration), registry, authority,
            authority.owner_key, _SourceTurnLoader(engine, authority))
        browser=None
        if settings.browser_autostart is not None:
            aliases={}
            catalog=settings.configured_catalog
            if (catalog is not None and len(catalog.principal_sha256_allowlist)==1 and catalog.specs
                    and all(spec.operation_kind=='invoke' and spec.descriptor.name.startswith('browser_') for spec in catalog.specs)
                    and catalog.browser_client is not None
                    and catalog.browser_client.origin==settings.browser_autostart.client.origin):
                from eva.adapters.connectors.browser_autostart import direct_browser_owner_binding
                aliases[registry._configured_id()]=direct_browser_owner_binding(
                    catalog.principal_sha256_allowlist[0],catalog.base_url,catalog.browser_client.origin)
            browser=ManagedBrowserAutostart(settings.browser_autostart,
                connector_id_for(authority.owner_key,settings.browser_autostart.endpoint),session_aliases=aliases)
        discovery = McpDiscoveryToolAdapter(registry, browser_autostart=browser)
        return ProviderContribution(
            provider_id="mcp_autonomy",
            conversation_tools=registration.conversation_tools() + discovery.conversation_tools(),
            external_tools=tuple(PostgresExternalConversationTools(
                engine, adapter.planner(), adapter, authorize_turn=authority.authorize_turn,
            ) for adapter in (registration, discovery)),
        )


__all__ = ["McpAutonomyProviderFactory", "McpAutonomySettings"]
