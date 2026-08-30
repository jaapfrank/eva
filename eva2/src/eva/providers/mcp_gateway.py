"""Opt-in runtime provider for tools discovered through one MCP gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from typing import Any

import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayConfig,
    McpGatewayProofBinding,
    McpGatewayToolAdapter,
    McpGatewayToolSpec,
)
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import ConversationToolDescriptor
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.contracts.base import validate_json_value
from eva.providers.runtime import ProviderContribution


_CONTRACT = "EvaMcpGatewayProvider/v1"
_TOOL_FIELDS = {
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


@dataclass(frozen=True, slots=True)
class McpGatewayProviderSettings:
    base_url: str
    bearer_token: str = field(repr=False)
    allow_plain_http: bool
    specs: tuple[McpGatewayToolSpec, ...]


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


def _tool_spec(payload: object) -> McpGatewayToolSpec:
    if not isinstance(payload, dict) or set(payload) != _TOOL_FIELDS:
        raise RuntimeError("MCP gateway tool configuration is invalid")
    schema = payload.get("input_schema")
    try:
        validate_json_value(schema)
    except ValueError as exc:
        raise RuntimeError("MCP gateway tool configuration is invalid") from exc
    if not isinstance(schema, dict):
        raise RuntimeError("MCP gateway tool configuration is invalid")
    bindings_payload = payload.get("proof_bindings")
    if (
        not isinstance(bindings_payload, list)
        or not bindings_payload
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
        or payload.get("contract") != _CONTRACT
        or not isinstance(payload.get("tools"), list)
        or not 1 <= len(payload["tools"]) <= 128
    ):
        raise RuntimeError("MCP gateway catalog is invalid")
    specs = tuple(_tool_spec(item) for item in payload["tools"])
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
        configured = (bool(base_url), bool(token_file), bool(catalog_file))
        if not any(configured):
            return None
        if not all(configured):
            raise RuntimeError("MCP gateway provider configuration is incomplete")
        allow_plain_raw = _environment_value(
            "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP"
        ).casefold()
        if allow_plain_raw not in {"false", "true"}:
            raise RuntimeError("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP is invalid")
        token = read_runtime_secret_file(
            token_file, setting_name="EVA_MCP_GATEWAY_TOKEN_FILE"
        )
        catalog = read_runtime_secret_file(
            catalog_file, setting_name="EVA_MCP_GATEWAY_CATALOG_FILE"
        )
        settings = McpGatewayProviderSettings(
            base_url=base_url,
            bearer_token=token,
            allow_plain_http=allow_plain_raw == "true",
            specs=parse_mcp_gateway_catalog(catalog),
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
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(
                settings.base_url,
                settings.bearer_token,
                allow_plain_http=settings.allow_plain_http,
            ),
            settings.specs,
        )
        asyncio.run(adapter.verify_catalog())
        return ProviderContribution(
            provider_id="mcp_gateway",
            conversation_tools=adapter.conversation_tools(settings.specs),
            external_tools=(
                PostgresExternalConversationTools(
                    engine,
                    adapter.planner(),
                    adapter,
                ),
            ),
        )


__all__ = [
    "McpGatewayProviderFactory",
    "McpGatewayProviderSettings",
    "parse_mcp_gateway_catalog",
]
