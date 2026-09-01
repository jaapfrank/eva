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
class McpGatewayArgumentBinding:
    """Map one Eva-facing argument to one provider-facing argument."""

    argument_name: str
    provider_name: str

    def __post_init__(self) -> None:
        if (
            not self.argument_name.isidentifier()
            or not self.provider_name.isidentifier()
            or len(self.argument_name) > 80
            or len(self.provider_name) > 80
        ):
            raise ValueError("MCP gateway argument binding is invalid")


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
    contract: str = "EvaMcpGatewayProvider/v1"
    execute_argument_bindings: tuple[McpGatewayArgumentBinding, ...] = ()
    observe_argument_bindings: tuple[McpGatewayArgumentBinding, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.contract
            not in {"EvaMcpGatewayProvider/v1", "EvaMcpGatewayProvider/v2"}
            or _GATEWAY_TOOL_NAME.fullmatch(self.execute_tool_name) is None
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
        if self.contract == "EvaMcpGatewayProvider/v1":
            identity = tuple(
                McpGatewayArgumentBinding(name, name) for name in properties
            )
            for field_name in (
                "execute_argument_bindings",
                "observe_argument_bindings",
            ):
                bindings = getattr(self, field_name)
                if not bindings:
                    object.__setattr__(self, field_name, identity)
                elif bindings != identity:
                    raise ValueError(
                        "MCP gateway v1 requires identity argument projection"
                    )
        for bindings in (
            self.execute_argument_bindings,
            self.observe_argument_bindings,
        ):
            if (
                len({item.argument_name for item in bindings}) != len(bindings)
                or len({item.provider_name for item in bindings}) != len(bindings)
                or any(item.argument_name not in properties for item in bindings)
            ):
                raise ValueError("MCP gateway argument projection is invalid")
        if any(item.argument_name not in properties for item in self.proof_bindings):
            raise ValueError("MCP gateway proof input is absent from the tool schema")
        if any(item.argument_name not in required for item in self.proof_bindings):
            raise ValueError("MCP gateway proof input must be required")

    @property
    def configuration_sha256(self) -> str:
        payload: dict[str, JsonValue] = {
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
        if self.contract == "EvaMcpGatewayProvider/v2":
            payload["contract"] = self.contract
            payload["execute_argument_bindings"] = [
                {
                    "argument_name": item.argument_name,
                    "provider_name": item.provider_name,
                }
                for item in self.execute_argument_bindings
            ]
            payload["observe_argument_bindings"] = [
                {
                    "argument_name": item.argument_name,
                    "provider_name": item.provider_name,
                }
                for item in self.observe_argument_bindings
            ]
        return canonical_sha256(payload)


class McpGatewayToolPlanner:
    """Validate a model call and bind its expected proof without provider logic."""

    def __init__(
        self,
        specs: tuple[McpGatewayToolSpec, ...],
        provider_input_schemas: (
            dict[tuple[str, str], dict[str, JsonValue]] | None
        ) = None,
    ) -> None:
        if not specs or len({item.descriptor.name for item in specs}) != len(specs):
            raise ValueError("MCP gateway action catalog is invalid")
        if provider_input_schemas is None:
            if any(item.contract != "EvaMcpGatewayProvider/v1" for item in specs):
                raise ValueError("MCP gateway v2 planner requires verified schemas")
            provider_input_schemas = {
                (tool_name, phase): item.descriptor.input_schema
                for item in specs
                for tool_name, phase in (
                    (item.execute_tool_name, "execute"),
                    (item.observe_tool_name, "observe"),
                )
            }
        self._specs = {item.descriptor.name: item for item in specs}
        self._provider_input_schemas = provider_input_schemas
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
        execute_arguments = _project_arguments(
            call.arguments, spec.execute_argument_bindings
        )
        observe_arguments = _project_arguments(
            call.arguments, spec.observe_argument_bindings
        )
        execute_schema = self._provider_input_schemas.get(
            (spec.execute_tool_name, "execute")
        )
        observe_schema = self._provider_input_schemas.get(
            (spec.observe_tool_name, "observe")
        )
        if execute_schema is None or observe_schema is None:
            raise ConversationConflict("MCP gateway catalog was not verified")
        _validate_schema_value(
            execute_arguments,
            execute_schema,
            "execute_arguments",
            provider_schema=True,
        )
        _validate_schema_value(
            observe_arguments,
            observe_schema,
            "observe_arguments",
            provider_schema=True,
        )
        expected: dict[str, JsonValue] = {
            item.observed_name: call.arguments[item.argument_name]
            for item in spec.proof_bindings
        }
        return ConversationToolPreparation(
            tool_name=spec.execute_tool_name,
            arguments=execute_arguments,
            expected_proof=expected,
            observation_tool_name=spec.observe_tool_name,
            observation_arguments=observe_arguments,
            tool_configuration_sha256=spec.configuration_sha256,
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
        self._verified_input_schemas: dict[tuple[str, str], dict[str, JsonValue]] = {}
        self._verified_output_schemas: dict[str, dict[str, JsonValue]] = {}

    @staticmethod
    def conversation_tools(
        specs: tuple[McpGatewayToolSpec, ...],
    ) -> tuple[ConversationToolDescriptor, ...]:
        return tuple(item.descriptor for item in specs)

    def planner(self) -> McpGatewayToolPlanner:
        if not self._verified_input_schemas:
            raise RuntimeError("MCP gateway catalog must be verified before use")
        return McpGatewayToolPlanner(
            tuple(self._by_execute_name.values()),
            dict(self._verified_input_schemas),
        )

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
        input_schemas: dict[tuple[str, str], dict[str, JsonValue]] = {}
        output_schemas: dict[str, dict[str, JsonValue]] = {}
        for spec in self._by_execute_name.values():
            for phase, tool_name, catalog_name, expected_hash, bindings in (
                (
                    "execute",
                    spec.execute_tool_name,
                    spec.execute_catalog_name,
                    spec.execute_catalog_sha256,
                    spec.execute_argument_bindings,
                ),
                (
                    "observe",
                    spec.observe_tool_name,
                    spec.observe_catalog_name,
                    spec.observe_catalog_sha256,
                    spec.observe_argument_bindings,
                ),
            ):
                row = by_name.get(catalog_name)
                if row is None:
                    raise RuntimeError("MCP gateway required tool is absent")
                selected = mcp_gateway_catalog_binding(row, contract=spec.contract)
                actual_hash = canonical_sha256(selected)
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        "MCP gateway tool differs from the sealed catalog"
                    )
                verified[catalog_name] = actual_hash
                input_schema = selected["input_schema"]
                if not isinstance(input_schema, dict):
                    raise RuntimeError("MCP gateway input schema is invalid")
                _validate_projection_contract(
                    spec.descriptor.input_schema,
                    input_schema,
                    bindings,
                )
                input_schemas[(tool_name, phase)] = input_schema
                output_schema = selected.get("output_schema")
                if output_schema is not None:
                    if not isinstance(output_schema, dict):
                        raise RuntimeError("MCP gateway output schema is invalid")
                    output_schemas[tool_name] = output_schema
        self._verified_input_schemas = input_schemas
        self._verified_output_schemas = output_schemas
        return verified

    async def execute(self, command: ToolRequest) -> ToolDispatchResult:
        spec = self._by_execute_name.get(command.tool_name)
        if spec is None:
            raise ValueError("MCP gateway command is unsupported")
        self._validate_request_binding(command, spec)
        execute_schema = self._verified_input_schemas.get(
            (spec.execute_tool_name, "execute")
        )
        if execute_schema is None:
            raise RuntimeError("MCP gateway execute catalog is not verified")
        _validate_schema_value(
            command.arguments,
            execute_schema,
            "execute_arguments",
            provider_schema=True,
        )
        output = await asyncio.to_thread(
            self._call_tool,
            spec.execute_tool_name,
            command.arguments,
            _rpc_id(command, "execute", spec.execute_tool_name, command.arguments),
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
        self._validate_request_binding(command, spec)
        observe_tool_name = command.observation_tool_name or spec.observe_tool_name
        observe_arguments = (
            command.observation_arguments
            if command.observation_arguments is not None
            else command.arguments
        )
        observe_schema = self._verified_input_schemas.get(
            (spec.observe_tool_name, "observe")
        )
        if observe_schema is None:
            raise RuntimeError("MCP gateway observe catalog is not verified")
        _validate_schema_value(
            observe_arguments,
            observe_schema,
            "observe_arguments",
            provider_schema=True,
        )
        output = await asyncio.to_thread(
            self._call_tool,
            observe_tool_name,
            observe_arguments,
            _rpc_id(command, "observe", observe_tool_name, observe_arguments),
        )
        output_schema = self._verified_output_schemas.get(observe_tool_name)
        if output_schema is not None:
            _validate_schema_value(output, output_schema, "observe_output")
        observed: dict[str, JsonValue] = {}
        for binding in spec.proof_bindings:
            if binding.observed_name not in output:
                raise RuntimeError("MCP gateway readback omitted required proof")
            observed[binding.observed_name] = output[binding.observed_name]
        return ToolProofObservation(
            "verified",
            observed,
            {
                "observation_sha256": canonical_sha256(output),
                "source": "mcp_gateway_readback",
            },
        )

    @staticmethod
    def _validate_request_binding(
        command: ToolRequest, spec: McpGatewayToolSpec
    ) -> None:
        if command.tool_configuration_sha256 != spec.configuration_sha256:
            raise RuntimeError("MCP gateway durable tool binding differs")
        if (
            command.observation_tool_name is None
            and command.observation_arguments is None
            and spec.contract == "EvaMcpGatewayProvider/v1"
        ):
            return
        if (
            command.observation_tool_name != spec.observe_tool_name
            or command.observation_arguments is None
        ):
            raise RuntimeError("MCP gateway durable tool binding differs")

    def _call_tool(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        rpc_id: str,
    ) -> dict[str, JsonValue]:
        response = self._request_json(
            "POST",
            "/rpc",
            {
                "id": rpc_id,
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"arguments": arguments, "name": tool_name},
            },
        )
        if (
            not isinstance(response, dict)
            or response.get("jsonrpc") != "2.0"
            or response.get("id") != rpc_id
        ):
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


def mcp_gateway_catalog_binding(
    row: dict[str, Any],
    *,
    contract: str = "EvaMcpGatewayProvider/v1",
) -> dict[str, JsonValue]:
    if contract not in {"EvaMcpGatewayProvider/v1", "EvaMcpGatewayProvider/v2"}:
        raise RuntimeError("MCP gateway catalog contract is invalid")
    input_schema = row.get("input_schema", row.get("inputSchema"))
    integration_type = row.get("integration_type", row.get("integrationType"))
    request_type = row.get("request_type", row.get("requestType"))
    output_schema = row.get("output_schema", row.get("outputSchema"))
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
    if contract == "EvaMcpGatewayProvider/v2" and output_schema is not None:
        selected["output_schema"] = output_schema
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


def _validate_projection_contract(
    eva_schema: dict[str, JsonValue],
    provider_schema: dict[str, JsonValue],
    bindings: tuple[McpGatewayArgumentBinding, ...],
) -> None:
    eva_properties = eva_schema.get("properties")
    eva_required = eva_schema.get("required")
    provider_properties = provider_schema.get("properties")
    provider_required = provider_schema.get("required", [])
    provider_additional = provider_schema.get("additionalProperties", True)
    if (
        not isinstance(eva_properties, dict)
        or not isinstance(eva_required, list)
        or not isinstance(provider_properties, dict)
        or not isinstance(provider_required, list)
        or provider_schema.get("type") != "object"
        or not isinstance(provider_additional, (bool, dict))
        or any(not isinstance(item, str) for item in provider_required)
        or len(provider_required) != len(set(provider_required))
        or not set(provider_required).issubset(provider_properties)
    ):
        raise RuntimeError("MCP gateway provider input schema is unsupported")
    by_provider_name = {item.provider_name: item.argument_name for item in bindings}
    if (
        not set(by_provider_name).issubset(provider_properties)
        or not set(provider_required).issubset(by_provider_name)
        or any(by_provider_name[name] not in eva_required for name in provider_required)
    ):
        raise RuntimeError("MCP gateway argument projection is incomplete")


def _rpc_id(
    request: ToolRequest,
    phase: str,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> str:
    return canonical_sha256(
        {
            "arguments": arguments,
            "operation_id": request.operation_id,
            "phase": phase,
            "tool_configuration_sha256": request.tool_configuration_sha256,
            "tool_name": tool_name,
        }
    )[:32]


def _project_arguments(
    arguments: dict[str, JsonValue],
    bindings: tuple[McpGatewayArgumentBinding, ...],
) -> dict[str, JsonValue]:
    projected = {
        binding.provider_name: arguments[binding.argument_name]
        for binding in bindings
        if binding.argument_name in arguments
    }
    validate_json_value(projected)
    return projected


def _validate_schema_value(
    value: JsonValue,
    schema: object,
    path: str,
    *,
    provider_schema: bool = False,
) -> None:
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
        required = schema.get("required", [] if provider_schema else None)
        additional = schema.get("additionalProperties", True)
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or (provider_schema and not isinstance(additional, (bool, dict)))
            or (
                not provider_schema
                and additional is not False
            )
            or not set(required).issubset(value)
            or not set(value).issubset(properties)
        ):
            raise ConversationConflict(f"{path} fields are invalid")
        for name, item in value.items():
            _validate_schema_value(
                item,
                properties[name],
                f"{path}.{name}",
                provider_schema=provider_schema,
            )
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
            _validate_schema_value(
                item,
                items,
                f"{path}[{index}]",
                provider_schema=provider_schema,
            )
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
    "McpGatewayArgumentBinding",
    "McpGatewayConfig",
    "McpGatewayProofBinding",
    "McpGatewayToolAdapter",
    "McpGatewayToolPlanner",
    "McpGatewayToolSpec",
    "mcp_gateway_catalog_binding",
]
