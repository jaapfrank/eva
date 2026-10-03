"""Opt-in runtime provider for tools discovered through one MCP gateway."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from copy import copy
import hashlib
import json
import re
import os
from typing import Any

import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayArgumentBinding,
    McpGatewayConfig,
    McpGatewayProofBinding,
    McpGatewayToolAdapter,
    McpGatewayToolSpec,
)
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import (
    ConversationConflict, ConversationToolDescriptor, ConversationTurn,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.contracts.base import validate_json_value
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.connectors.browser_autostart import BrowserAutostartConfig, ManagedBrowserAutostart, managed_browser_session_id
from eva.adapters.connectors.browser_session import BrowserSessionClient
from eva.adapters.connectors.mcp_registration import canonical_mcp_endpoint
from eva.application.schema_validation import validate_schema_value
from eva.providers.contracts import ProviderContribution


_CONTRACT_V1 = "EvaMcpGatewayProvider/v1"
_CONTRACT_V2 = "EvaMcpGatewayProvider/v2"
_CONTRACT_V3 = "EvaMcpGatewayProvider/v3"
_CONTRACT_V4 = "EvaMcpGatewayProvider/v4"
_TOOL_FIELDS_V1 = {
    "completion_guidance",
    "description",
    "eva_name",
    "execute_catalog_name",
    "execute_catalog_sha256",
    "execute_tool_name",
    "input_schema",
    "observe_catalog_name",
    "observe_catalog_sha256",
    "observe_tool_name",
    "proof_bindings",
}
_TOOL_FIELDS_V2 = _TOOL_FIELDS_V1 | {
    "execute_argument_bindings",
    "observe_argument_bindings",
}

_TOOL_FIELDS_V3 = _TOOL_FIELDS_V2 | {"operation_kind"}


@dataclass(frozen=True, slots=True)
class McpGatewayProviderSettings:
    base_url: str
    bearer_token: str = field(repr=False)
    allow_plain_http: bool
    specs: tuple[McpGatewayToolSpec, ...]
    principal_sha256_allowlist: tuple[str, ...]
    browser_client: BrowserSessionClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not self.principal_sha256_allowlist
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for value in self.principal_sha256_allowlist)
            or len(set(self.principal_sha256_allowlist)) != len(self.principal_sha256_allowlist)
        ):
            raise ValueError("MCP gateway principal allowlist is invalid")
        if self.browser_client is not None and (
            len(self.principal_sha256_allowlist) != 1 or not self.specs
            or any(spec.operation_kind != "invoke" or not spec.descriptor.name.startswith("browser_") for spec in self.specs)
        ):
            raise ValueError("managed direct browser requires a dedicated single-owner catalog")

    def authorize_turn(self, turn: ConversationTurn) -> None:
        principal = hashlib.sha256(turn.principal_id.encode("utf-8")).hexdigest()
        if (
            turn.audience.kind != "private_person"
            or turn.audience.principal_id != turn.principal_id
            or principal not in self.principal_sha256_allowlist
        ):
            raise ConversationConflict("MCP gateway is unavailable for this audience")


class _ManagedDirectBrowserAdapter(McpGatewayToolAdapter):
    """Reuse the managed-browser lifecycle only for a configured single-owner catalog."""
    def __init__(self, config, specs, *, client, principal_sha256):
        super().__init__(config, specs)
        self._owner_binding = canonical_sha256({
            "contract": "EvaDirectBrowserOwner/v1", "principal_sha256": principal_sha256,
            "gateway": config.base_url, "controller": client.origin,
        })
        self._browser = ManagedBrowserAutostart(
            BrowserAutostartConfig(canonical_mcp_endpoint(config.base_url), client), self._owner_binding)

    async def execute(self, command):
        spec = self._by_execute_name.get(command.tool_name)
        if spec is None:
            return await super().execute(command)
        self._validate_request_binding(command, spec)
        schema = self._verified_input_schemas.get((spec.execute_tool_name, "execute"))
        if schema is None:
            raise RuntimeError("MCP gateway execute catalog is not verified")
        validate_schema_value(command.arguments, schema, "execute_arguments", provider_schema=True)
        from eva.adapters.postgres.connector_results import recovered_result
        if recovered_result(command, "invoke") is not None:
            return await super().execute(command)
        revision = await self._browser.ensure_available(self._owner_binding)
        # Never mutate shared configuration: concurrent calls keep their own generation.
        adapter = copy(self)
        adapter._config = replace(self._config, session_id=managed_browser_session_id(
            self._config.bearer_token, self._owner_binding, revision))
        return await McpGatewayToolAdapter.execute(adapter, command)


def _environment_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def _required_text(payload: dict[str, Any], name: str, limit: int) -> str:
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > limit
    ):
        raise RuntimeError("MCP gateway tool configuration is invalid")
    return value


def _argument_bindings(
    payload: object,
    *,
    schema: dict[str, Any],
    identity_default: bool,
) -> tuple[McpGatewayArgumentBinding, ...]:
    if identity_default:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeError("MCP gateway argument bindings are invalid")
        payload = [
            {"argument_name": name, "provider_name": name} for name in properties
        ]
    if not isinstance(payload, list) or len(payload) > 128:
        raise RuntimeError("MCP gateway argument bindings are invalid")
    bindings: list[McpGatewayArgumentBinding] = []
    for binding in payload:
        if not isinstance(binding, dict) or set(binding) != {
            "argument_name",
            "provider_name",
        }:
            raise RuntimeError("MCP gateway argument bindings are invalid")
        try:
            bindings.append(
                McpGatewayArgumentBinding(
                    _required_text(binding, "argument_name", 80),
                    _required_text(binding, "provider_name", 80),
                )
            )
        except ValueError as exc:
            raise RuntimeError("MCP gateway argument bindings are invalid") from exc
    return tuple(bindings)


def _tool_spec(payload: object, *, contract: str) -> McpGatewayToolSpec:
    expected_fields = {
        _CONTRACT_V1: _TOOL_FIELDS_V1,
        _CONTRACT_V2: _TOOL_FIELDS_V2,
        _CONTRACT_V3: _TOOL_FIELDS_V3,
        _CONTRACT_V4: _TOOL_FIELDS_V3,
    }.get(contract)
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise RuntimeError("MCP gateway tool configuration is invalid")
    schema = payload.get("input_schema")
    try:
        validate_json_value(schema)
    except ValueError as exc:
        raise RuntimeError("MCP gateway tool configuration is invalid") from exc
    if not isinstance(schema, dict):
        raise RuntimeError("MCP gateway tool configuration is invalid")
    operation_kind = payload.get("operation_kind") if contract in {_CONTRACT_V3, _CONTRACT_V4} else "effect"
    allowed_kinds = {"read", "effect", "invoke"} if contract == _CONTRACT_V4 else {"read", "effect"}
    if not isinstance(operation_kind, str) or operation_kind not in allowed_kinds:
        raise RuntimeError("MCP gateway operation kind is invalid")
    bindings_payload = payload.get("proof_bindings")
    if (
        not isinstance(bindings_payload, list)
        or (operation_kind == "effect" and not bindings_payload)
        or len(bindings_payload) > 32
    ):
        raise RuntimeError("MCP gateway proof bindings are invalid")
    bindings: list[McpGatewayProofBinding] = []
    for binding in bindings_payload:
        if not isinstance(binding, dict) or set(binding) != {
            "argument_name",
            "observed_name",
        }:
            raise RuntimeError("MCP gateway proof bindings are invalid")
        try:
            bindings.append(
                McpGatewayProofBinding(
                    _required_text(binding, "argument_name", 80),
                    _required_text(binding, "observed_name", 80),
                )
            )
        except ValueError as exc:
            raise RuntimeError("MCP gateway proof bindings are invalid") from exc
    try:
        descriptor = ConversationToolDescriptor(
            name=_required_text(payload, "eva_name", 120),
            description=_required_text(payload, "description", 4_096),
            input_schema=schema,
            completion_guidance=_required_text(payload, "completion_guidance", 4_096),
        )
        return McpGatewayToolSpec(
            contract=contract,
            operation_kind=operation_kind,
            descriptor=descriptor,
            execute_tool_name=_required_text(payload, "execute_tool_name", 240),
            observe_tool_name=_required_text(payload, "observe_tool_name", 240),
            execute_catalog_name=_required_text(payload, "execute_catalog_name", 240),
            observe_catalog_name=_required_text(payload, "observe_catalog_name", 240),
            execute_catalog_sha256=_required_text(
                payload, "execute_catalog_sha256", 64
            ),
            observe_catalog_sha256=_required_text(
                payload, "observe_catalog_sha256", 64
            ),
            execute_argument_bindings=_argument_bindings(
                payload.get("execute_argument_bindings"),
                schema=schema,
                identity_default=contract == _CONTRACT_V1,
            ),
            observe_argument_bindings=_argument_bindings(
                payload.get("observe_argument_bindings"),
                schema=schema,
                identity_default=contract == _CONTRACT_V1,
            ),
            proof_bindings=tuple(bindings),
        )
    except ValueError as exc:
        raise RuntimeError("MCP gateway tool configuration is invalid") from exc


def parse_mcp_gateway_catalog(raw: str) -> tuple[McpGatewayToolSpec, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP gateway catalog is invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "tools"}
        or not isinstance(payload.get("contract"), str)
        or payload.get("contract") not in {_CONTRACT_V1, _CONTRACT_V2, _CONTRACT_V3, _CONTRACT_V4}
        or not isinstance(payload.get("tools"), list)
        or not 1 <= len(payload["tools"]) <= 128
    ):
        raise RuntimeError("MCP gateway catalog is invalid")
    contract = payload["contract"]
    specs = tuple(_tool_spec(item, contract=contract) for item in payload["tools"])
    names = [item.descriptor.name for item in specs]
    if len(names) != len(set(names)):
        raise RuntimeError("MCP gateway catalog contains duplicate Eva tools")
    return specs


class McpGatewayProviderFactory:
    """Load only explicitly selected, hash-bound gateway tools."""

    provider_id = "mcp_gateway"

    @staticmethod
    def load_settings() -> McpGatewayProviderSettings | None:
        if "EVA_MCP_GATEWAY_TOKEN" in os.environ:
            raise RuntimeError(
                "EVA_MCP_GATEWAY_TOKEN is forbidden; use EVA_MCP_GATEWAY_TOKEN_FILE"
            )
        base_url = _environment_value("EVA_MCP_GATEWAY_URL")
        token_file = _environment_value("EVA_MCP_GATEWAY_TOKEN_FILE")
        catalog_file = _environment_value("EVA_MCP_GATEWAY_CATALOG_FILE")
        additional_catalog_file = _environment_value("EVA_MCP_GATEWAY_ADDITIONAL_CATALOG_FILE")
        configured = (bool(base_url), bool(token_file), bool(catalog_file))
        if not any(configured) and not additional_catalog_file:
            return None
        if not all(configured):
            raise RuntimeError("MCP gateway provider configuration is incomplete")
        allow_plain_raw = _environment_value(
            "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP"
        ).casefold()
        if allow_plain_raw not in {"false", "true"}:
            raise RuntimeError("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP is invalid")
        principals = tuple(sorted(set(filter(None, (
            item.strip() for item in _environment_value(
                "EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST"
            ).split(",")
        )))))
        if not principals or any(re.fullmatch(r"[0-9a-f]{64}", p) is None for p in principals):
            raise RuntimeError("MCP gateway principal allowlist is required and must contain SHA-256 values")
        token = read_runtime_secret_file(
            token_file, setting_name="EVA_MCP_GATEWAY_TOKEN_FILE"
        )
        catalog = read_runtime_secret_file(
            catalog_file, setting_name="EVA_MCP_GATEWAY_CATALOG_FILE"
        )
        specs = parse_mcp_gateway_catalog(catalog)
        if additional_catalog_file:
            # Keep each catalog's original contract and immutable tool binding.
            # Adding an invoke tool must not rewrite older effect/read receipts.
            additional = read_runtime_secret_file(
                additional_catalog_file,
                setting_name="EVA_MCP_GATEWAY_ADDITIONAL_CATALOG_FILE",
            )
            specs = (*specs, *parse_mcp_gateway_catalog(additional))
            names = [spec.descriptor.name for spec in specs]
            execute_names = [spec.execute_tool_name for spec in specs]
            if (len(specs) > 128 or len(set(names)) != len(names)
                    or len(set(execute_names)) != len(execute_names)):
                raise RuntimeError("MCP gateway combined catalog contains duplicate tools or exceeds limit")
        browser_client = None
        controller = _environment_value("EVA_BROWSER_CONTROL_URL")
        # Dedicated installer browser catalog: do not reinterpret mixed or
        # multi-principal gateway providers used by existing installations.
        if (controller and len(principals) == 1 and specs
                and all(spec.operation_kind == "invoke" and spec.descriptor.name.startswith("browser_") for spec in specs)):
            control_token_file = _environment_value("EVA_BROWSER_CONTROL_TOKEN_FILE")
            if not control_token_file:
                raise RuntimeError("managed browser controller token is required")
            browser_client = BrowserSessionClient(controller, read_runtime_secret_file(
                control_token_file, setting_name="EVA_BROWSER_CONTROL_TOKEN_FILE"))
        settings = McpGatewayProviderSettings(
            base_url=base_url,
            bearer_token=token,
            allow_plain_http=allow_plain_raw == "true",
            specs=specs,
            principal_sha256_allowlist=principals,
            browser_client=browser_client,
        )
        try:
            McpGatewayConfig(
                settings.base_url,
                settings.bearer_token,
                allow_plain_http=settings.allow_plain_http,
            )
        except ValueError as exc:
            raise RuntimeError("MCP gateway provider URL is invalid") from exc
        return settings

    @staticmethod
    def build(
        settings: McpGatewayProviderSettings, engine: sa.Engine
    ) -> ProviderContribution:
        config = McpGatewayConfig(
                settings.base_url,
                settings.bearer_token,
                allow_plain_http=settings.allow_plain_http,
            )
        adapter = (McpGatewayToolAdapter(config, settings.specs) if settings.browser_client is None else
            _ManagedDirectBrowserAdapter(config, settings.specs, client=settings.browser_client,
                principal_sha256=settings.principal_sha256_allowlist[0]))
        # Uvicorn calls synchronous app factories inside its running event loop.
        # Complete admission in an owned worker loop before exposing any tools.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(asyncio.run, adapter.verify_catalog()).result()
        return ProviderContribution(
            provider_id="mcp_gateway",
            conversation_tools=adapter.conversation_tools(settings.specs),
            external_tools=(
                PostgresExternalConversationTools(
                    engine,
                    adapter.planner(),
                    adapter,
                    authorize_turn=settings.authorize_turn,
                ),
            ),
        )


__all__ = [
    "McpGatewayProviderFactory",
    "McpGatewayProviderSettings",
    "parse_mcp_gateway_catalog",
]
