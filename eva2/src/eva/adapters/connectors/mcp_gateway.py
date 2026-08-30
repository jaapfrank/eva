"""Generic, proof-first tool execution through an authenticated MCP gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from typing import Any
from urllib import error, parse, request as urllib_request

from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationToolPreparation,
    ConversationTurn,
)
from eva.application.orchestration import (
    ToolDispatchResult,
    ToolExecutionRejected,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256


_GATEWAY_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}")


@dataclass(frozen=True, slots=True)
class McpGatewayConfig:
    base_url: str
    bearer_token: str = field(repr=False)
    request_timeout_seconds: int = 15
    allow_plain_http: bool = False

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/")
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and not self.allow_plain_http)
        ):
            raise ValueError("MCP gateway URL is invalid")
        if not self.bearer_token or len(self.bearer_token) > 8_192:
            raise ValueError("MCP gateway token is invalid")
        if not 2 <= self.request_timeout_seconds <= 30:
            raise ValueError("MCP gateway timeout is invalid")


@dataclass(frozen=True, slots=True)
class McpGatewayProofBinding:
    argument_name: str
    observed_name: str

    def __post_init__(self) -> None:
        if (
            not self.argument_name.isidentifier()
            or not self.observed_name.isidentifier()
            or len(self.argument_name) > 80
            or len(self.observed_name) > 80
        ):
            raise ValueError("MCP gateway proof binding is invalid")


@dataclass(frozen=True, slots=True)
class McpGatewayToolSpec:
    """Sealed Eva-facing tool plus its independent gateway readback."""

    descriptor: ConversationToolDescriptor
    execute_tool_name: str
    observe_tool_name: str
    execute_catalog_name: str
    observe_catalog_name: str
    execute_catalog_sha256: str
    observe_catalog_sha256: str
    proof_bindings: tuple[McpGatewayProofBinding, ...]

    def __post_init__(self) -> None:
        if (
            _GATEWAY_TOOL_NAME.fullmatch(self.execute_tool_name) is None
            or _GATEWAY_TOOL_NAME.fullmatch(self.observe_tool_name) is None
            or _GATEWAY_TOOL_NAME.fullmatch(self.execute_catalog_name) is None
            or _GATEWAY_TOOL_NAME.fullmatch(self.observe_catalog_name) is None
            or len(self.execute_catalog_sha256) != 64
            or len(self.observe_catalog_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.execute_catalog_sha256
                + self.observe_catalog_sha256
            )
            or not self.proof_bindings
            or len({item.observed_name for item in self.proof_bindings})
            != len(self.proof_bindings)
        ):
            raise ValueError("MCP gateway tool specification is invalid")
        properties = self.descriptor.input_schema["properties"]
        required = self.descriptor.input_schema["required"]
        if any(item.argument_name not in properties for item in self.proof_bindings):
            raise ValueError("MCP gateway proof input is absent from the tool schema")
        if any(item.argument_name not in required for item in self.proof_bindings):
            raise ValueError("MCP gateway proof input must be required")

    @property
    def configuration_sha256(self) -> str:
        return canonical_sha256(
            {
                "completion_guidance": self.descriptor.completion_guidance,
                "description": self.descriptor.description,
                "eva_name": self.descriptor.name,
                "execute_catalog_name": self.execute_catalog_name,
                "execute_catalog_sha256": self.execute_catalog_sha256,
                "execute_tool_name": self.execute_tool_name,
                "input_schema": self.descriptor.input_schema,
                "observe_catalog_name": self.observe_catalog_name,
                "observe_catalog_sha256": self.observe_catalog_sha256,
                "observe_tool_name": self.observe_tool_name,
                "proof_bindings": [
                    {
                        "argument_name": item.argument_name,
                        "observed_name": item.observed_name,
                    }
                    for item in self.proof_bindings
                ],
            }
        )


class McpGatewayToolPlanner:
    """Validate a model call and bind its expected proof without provider logic."""

    def __init__(self, specs: tuple[McpGatewayToolSpec, ...]) -> None:
        if not specs or len({item.descriptor.name for item in specs}) != len(specs):
            raise ValueError("MCP gateway action catalog is invalid")
        self._specs = {item.descriptor.name: item for item in specs}
        self.action_names = tuple(self._specs)

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        del turn
        spec = self._specs.get(call.name)
        if spec is None:
            raise ConversationConflict("MCP gateway action is unavailable")
        _validate_schema_value(
            call.arguments, spec.descriptor.input_schema, "arguments"
        )
        expected: dict[str, JsonValue] = {
            item.observed_name: call.arguments[item.argument_name]
            for item in spec.proof_bindings
        }
        return ConversationToolPreparation(
            tool_name=spec.execute_tool_name,
            arguments=call.arguments,
            expected_proof=expected,
            receipt_output={
                "catalog_sha256": spec.execute_catalog_sha256,
                "tool_configuration_sha256": spec.configuration_sha256,
                "transport": "mcp_gateway",
            },
        )


class McpGatewayToolAdapter:
    """Call gateway tools while Eva retains authority and durable replay fences."""

    def __init__(
        self,
        config: McpGatewayConfig,
        specs: tuple[McpGatewayToolSpec, ...],
    ) -> None:
        if not specs:
            raise ValueError("MCP gateway tool catalog cannot be empty")
        execute_names = [item.execute_tool_name for item in specs]
        if len(execute_names) != len(set(execute_names)):
            raise ValueError("MCP gateway execute route is duplicated")
        self._config = config
        self._by_execute_name = {item.execute_tool_name: item for item in specs}

    @staticmethod
    def conversation_tools(
        specs: tuple[McpGatewayToolSpec, ...],
    ) -> tuple[ConversationToolDescriptor, ...]:
        return tuple(item.descriptor for item in specs)

    def planner(self) -> McpGatewayToolPlanner:
        return McpGatewayToolPlanner(tuple(self._by_execute_name.values()))

    async def verify_catalog(self) -> dict[str, str]:
        rows = await asyncio.to_thread(self._request_json, "GET", "/tools", None)
        if not isinstance(rows, list) or len(rows) > 1_024:
            raise RuntimeError("MCP gateway catalog is invalid")
        by_name: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise RuntimeError("MCP gateway catalog row is invalid")
            name = row["name"]
            if name in by_name:
                raise RuntimeError("MCP gateway catalog contains duplicate tools")
            by_name[name] = row
        verified: dict[str, str] = {}
        for spec in self._by_execute_name.values():
            for catalog_name, expected_hash in (
                (spec.execute_catalog_name, spec.execute_catalog_sha256),
                (spec.observe_catalog_name, spec.observe_catalog_sha256),
            ):
                row = by_name.get(catalog_name)
                if row is None:
                    raise RuntimeError("MCP gateway required tool is absent")
                selected = mcp_gateway_catalog_binding(row)
                actual_hash = canonical_sha256(selected)
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        "MCP gateway tool differs from the sealed catalog"
                    )
                verified[catalog_name] = actual_hash
        return verified

    async def execute(self, command: ToolRequest) -> ToolDispatchResult:
        spec = self._by_execute_name.get(command.tool_name)
        if spec is None:
            raise ValueError("MCP gateway command is unsupported")
        output = await asyncio.to_thread(
            self._call_tool, spec.execute_tool_name, command.arguments
        )
        return ToolDispatchResult(
            accepted=True,
            provider_ref=canonical_sha256(output),
            details={"gateway_output_sha256": canonical_sha256(output)},
        )

    async def observe(
        self,
        command: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation:
        del dispatch
        spec = self._by_execute_name.get(command.tool_name)
        if spec is None:
            raise ValueError("MCP gateway command is unsupported")
        output = await asyncio.to_thread(
            self._call_tool, spec.observe_tool_name, command.arguments
        )
        observed: dict[str, JsonValue] = {}
        for binding in spec.proof_bindings:
            value = output.get(binding.observed_name)
            if value is None:
                raise RuntimeError("MCP gateway readback omitted required proof")
            observed[binding.observed_name] = value
        return ToolProofObservation(
            "verified",
            observed,
            {
                "observation_sha256": canonical_sha256(output),
                "source": "mcp_gateway_readback",
            },
        )

    def _call_tool(
        self, tool_name: str, arguments: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        response = self._request_json(
            "POST",
            "/rpc",
            {
                "id": canonical_sha256(
                    {"arguments": arguments, "tool_name": tool_name}
                )[:24],
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"arguments": arguments, "name": tool_name},
            },
        )
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
            raise RuntimeError("MCP gateway returned an invalid RPC envelope")
        if response.get("error") is not None:
            raise ToolExecutionRejected("MCP gateway rejected the tool call")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP gateway returned no tool result")
        if result.get("isError") is True:
            raise ToolExecutionRejected("MCP tool rejected the operation")
        structured = result.get("structuredContent")
        if structured is not None:
            if not isinstance(structured, dict):
                raise RuntimeError("MCP gateway structured output is invalid")
            validate_json_value(structured)
            return structured
        content = result.get("content")
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
            or not isinstance(content[0].get("text"), str)
        ):
            raise RuntimeError("MCP gateway tool output is unsupported")
        text = content[0]["text"]
        if len(text.encode("utf-8")) > 1_048_576:
            raise RuntimeError("MCP gateway tool output is too large")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = {"text": text}
        if not isinstance(decoded, dict):
            raise RuntimeError("MCP gateway JSON tool output must be an object")
        validate_json_value(decoded)
        return decoded

    def _request_json(
        self, method: str, path: str, payload: dict[str, JsonValue] | None
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            self._config.base_url.rstrip("/") + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._config.request_timeout_seconds
            ) as response:
                raw = response.read(2_097_153)
        except error.HTTPError as exc:
            raise RuntimeError(f"MCP gateway request failed ({exc.code})") from exc
        except error.URLError as exc:
            raise RuntimeError("MCP gateway request failed") from exc
        if len(raw) > 2_097_152:
            raise RuntimeError("MCP gateway response is too large")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MCP gateway returned invalid JSON") from exc


def mcp_gateway_catalog_binding(row: dict[str, Any]) -> dict[str, JsonValue]:
    input_schema = row.get("input_schema", row.get("inputSchema"))
    integration_type = row.get("integration_type", row.get("integrationType"))
    request_type = row.get("request_type", row.get("requestType"))
    selected: dict[str, JsonValue] = {
        "annotations": row.get("annotations") or {},
        "deprecated": row.get("deprecated") is True,
        "enabled": row.get("enabled") is True,
        "input_schema": input_schema,
        "integration_type": integration_type,
        "name": row.get("name"),
        "reachable": row.get("reachable") is True,
        "request_type": request_type,
        "visibility": row.get("visibility"),
    }
    validate_json_value(selected)
    if (
        selected["integration_type"] != "MCP"
        or selected["enabled"] is not True
        or selected["reachable"] is not True
        or selected["deprecated"] is not False
        or selected["visibility"] not in {"private", "team", "public"}
        or not isinstance(input_schema, dict)
    ):
        raise RuntimeError("MCP gateway tool is not safely executable")
    return selected


def _validate_schema_value(value: JsonValue, schema: object, path: str) -> None:
    if not isinstance(schema, dict):
        raise ConversationConflict(f"{path} schema is invalid")
    if "const" in schema and value != schema["const"]:
        raise ConversationConflict(f"{path} differs from the sealed value")
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or value not in schema["enum"]
    ):
        raise ConversationConflict(f"{path} is outside the admitted values")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ConversationConflict(f"{path} must be an object")
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or schema.get("additionalProperties") is not False
            or not set(required).issubset(value)
            or not set(value).issubset(properties)
        ):
            raise ConversationConflict(f"{path} fields are invalid")
        for name, item in value.items():
            _validate_schema_value(item, properties[name], f"{path}.{name}")
        return
    if kind == "array":
        items = schema.get("items")
        if not isinstance(value, list) or not isinstance(items, dict):
            raise ConversationConflict(f"{path} must be an array")
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems", 1_000)
        if (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or not minimum <= len(value) <= maximum
        ):
            raise ConversationConflict(f"{path} array length is invalid")
        for index, item in enumerate(value):
            _validate_schema_value(item, items, f"{path}[{index}]")
        return
    expected_type = {
        "boolean": bool,
        "integer": int,
        "null": type(None),
        "number": int,
        "string": str,
    }.get(kind)
    if (
        expected_type is None
        or not isinstance(value, expected_type)
        or (kind in {"integer", "number"} and isinstance(value, bool))
    ):
        raise ConversationConflict(f"{path} has the wrong type")
    if isinstance(value, str):
        minimum = schema.get("minLength", 0)
        maximum = schema.get("maxLength", 65_536)
        if (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or not minimum <= len(value) <= maximum
        ):
            raise ConversationConflict(f"{path} text length is invalid")
    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum", value)
        maximum = schema.get("maximum", value)
        if (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or not minimum <= value <= maximum
        ):
            raise ConversationConflict(f"{path} number is invalid")


__all__ = [
    "McpGatewayConfig",
    "McpGatewayProofBinding",
    "McpGatewayToolAdapter",
    "McpGatewayToolPlanner",
    "McpGatewayToolSpec",
    "mcp_gateway_catalog_binding",
]
