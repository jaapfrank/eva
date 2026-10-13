"""Fail-closed candidate for Home Assistant's official Assist MCP endpoint.

This candidate deliberately stays outside the product runtime until the live
Home Assistant catalog has been captured and admitted.  It uses the official
MCP endpoint for the effect and a separate state reader for proof.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import socket
import time
from typing import Any, Literal, Protocol
from urllib import error, parse, request as urllib_request

from eva.application.orchestration import (
    ToolDispatchResult,
    ToolExecutionRejected,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_PROTOCOL_VERSION = "2025-06-18"
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MAX_CATALOG_TOOLS = 128
_MAX_CATALOG_BYTES = 512 * 1024


class HomeAssistantOfficialMcpError(RuntimeError):
    """The official Home Assistant MCP boundary failed closed."""


@dataclass(frozen=True, slots=True)
class HomeAssistantOfficialMcpConfig:
    base_url: str
    token: str
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Home Assistant MCP base URL is invalid")
        if (
            not 16 <= len(self.token) <= 4_096
            or any(character.isspace() for character in self.token)
        ):
            raise ValueError("Home Assistant MCP token is invalid")
        if not 1 <= self.timeout_seconds <= 10:
            raise ValueError("Home Assistant MCP timeout must be within 1..10 seconds")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/mcp/assist"


@dataclass(frozen=True, slots=True)
class AssistToolAdmission:
    tool_name: str
    effect_class: Literal["read", "effect"]
    input_schema_sha256: str
    required_arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ValueError("Home Assistant MCP tool name is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", self.input_schema_sha256) is None:
            raise ValueError("Home Assistant MCP schema hash is invalid")
        if (
            len(set(self.required_arguments)) != len(self.required_arguments)
            or any(not item or len(item) > 128 for item in self.required_arguments)
        ):
            raise ValueError("Home Assistant MCP required arguments are invalid")


@dataclass(frozen=True, slots=True)
class AssistTool:
    name: str
    description: str
    input_schema: dict[str, JsonValue]

    @property
    def input_schema_sha256(self) -> str:
        return canonical_sha256(self.input_schema)


@dataclass(frozen=True, slots=True)
class AssistCatalog:
    tools: tuple[AssistTool, ...]
    sha256: str

    def exact(self, name: str) -> AssistTool:
        matches = tuple(tool for tool in self.tools if tool.name == name)
        if len(matches) != 1:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP admitted tool is missing or ambiguous"
            )
        return matches[0]


@dataclass(frozen=True, slots=True)
class AssistCallResult:
    tool_name: str
    result: JsonValue
    response_sha256: str


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class HomeAssistantOfficialMcpClient:
    """Attest and call one admitted Assist tool over Streamable HTTP."""

    def __init__(self, config: HomeAssistantOfficialMcpConfig) -> None:
        self._config = config
        self._opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}), _NoRedirect()
        )

    def catalog(self) -> AssistCatalog:
        session = self._session()
        session.initialize()
        raw = session.request(2, "tools/list", {})
        return _catalog(raw)

    def call(
        self,
        *,
        admission: AssistToolAdmission,
        arguments: dict[str, JsonValue],
        expected_catalog_sha256: str,
    ) -> AssistCallResult:
        validate_json_value(arguments)
        session = self._session()
        session.initialize()
        catalog = _catalog(session.request(2, "tools/list", {}))
        if catalog.sha256 != expected_catalog_sha256:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP tool catalog drifted before the call"
            )
        tool = catalog.exact(admission.tool_name)
        if tool.input_schema_sha256 != admission.input_schema_sha256:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP admitted tool schema drifted"
            )
        _validate_arguments(arguments, tool.input_schema, admission.required_arguments)
        raw = session.request(
            3,
            "tools/call",
            {"name": admission.tool_name, "arguments": arguments},
        )
        result = _tool_result(raw, admission.effect_class)
        return AssistCallResult(
            admission.tool_name,
            result,
            hashlib.sha256(canonical_json_bytes(result)).hexdigest(),
        )

    def _session(self) -> "_StreamableHttpSession":
        return _StreamableHttpSession(
            endpoint=self._config.endpoint,
            token=self._config.token,
            opener=self._opener,
            deadline=time.monotonic() + self._config.timeout_seconds,
        )


class _StreamableHttpSession:
    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        opener: urllib_request.OpenerDirector,
        deadline: float,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._opener = opener
        self._deadline = deadline
        self._session_id: str | None = None
        self._initialized = False

    def initialize(self) -> None:
        result = self.request(
            1,
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "eva2-home-assistant", "version": "1"},
            },
            initializing=True,
        )
        if (
            result.get("protocolVersion") != _PROTOCOL_VERSION
            or not isinstance(result.get("capabilities"), dict)
            or not isinstance(result["capabilities"].get("tools"), dict)
        ):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP initialization is incompatible"
            )
        self._initialized = True
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
            initializing=False,
        )

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, JsonValue],
        *,
        initializing: bool = False,
    ) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            expect_response=True,
            initializing=initializing,
        )
        assert response is not None
        if (
            response.get("jsonrpc") != "2.0"
            or response.get("id") != request_id
            or "error" in response
            or not isinstance(response.get("result"), dict)
        ):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP response is not bound to the request"
            )
        return response["result"]

    def _post(
        self,
        payload: dict[str, JsonValue],
        *,
        expect_response: bool,
        initializing: bool,
    ) -> dict[str, Any] | None:
        body = canonical_json_bytes(payload)
        if len(body) > 64 * 1024:
            raise HomeAssistantOfficialMcpError("Home Assistant MCP request is too large")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if not initializing:
            if not self._initialized:
                raise HomeAssistantOfficialMcpError(
                    "Home Assistant MCP session was not initialized"
                )
            headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
            if self._session_id is not None:
                headers["Mcp-Session-Id"] = self._session_id
        request = urllib_request.Request(
            self._endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=self._remaining()) as response:  # noqa: S310
                if initializing:
                    self._set_session_id(response.headers.get("Mcp-Session-Id"))
                status = response.status
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                raw = self._read_bounded(response)
        except (error.HTTPError, error.URLError, OSError, socket.timeout) as exc:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP endpoint failed safely"
            ) from exc
        if not expect_response:
            if status != 202 or raw:
                raise HomeAssistantOfficialMcpError(
                    "Home Assistant MCP initialization notification was rejected"
                )
            return None
        if status != 200:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP request was not successful"
            )
        return _decode_response(raw, content_type)

    def _read_bounded(self, response: Any) -> bytes:
        reader = getattr(
            getattr(getattr(response, "fp", None), "raw", None), "_sock", None
        )
        if reader is None or not callable(getattr(reader, "settimeout", None)):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP response cannot enforce its deadline"
            )
        chunks: list[bytes] = []
        socket_timeout_enforceable = True
        while True:
            if socket_timeout_enforceable:
                try:
                    reader.settimeout(min(self._remaining(), 0.1))
                except OSError:
                    # urllib can buffer the complete Content-Length body and close
                    # the socket. Remaining reads are then in-memory only.
                    socket_timeout_enforceable = False
            try:
                chunk = response.read(1)
            except socket.timeout as exc:
                if time.monotonic() >= self._deadline:
                    raise HomeAssistantOfficialMcpError(
                        "Home Assistant MCP response exceeded its deadline"
                    ) from exc
                continue
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunks) > 1024 * 1024:
                raise HomeAssistantOfficialMcpError(
                    "Home Assistant MCP response is too large"
                )
        return b"".join(chunks)

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP lifecycle exceeded its deadline"
            )
        return remaining

    def _set_session_id(self, session_id: str | None) -> None:
        if session_id is None:
            return
        if not session_id or len(session_id) > 512 or any(
            not 0x21 <= ord(character) <= 0x7E for character in session_id
        ):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP session identifier is invalid"
            )
        self._session_id = session_id


class HomeAssistantStateReader(Protocol):
    async def state(self, entity_id: str) -> dict[str, JsonValue]: ...


class HomeAssistantOfficialMcpEffectAdapter:
    """Use MCP for one admitted effect and an independent reader for proof."""

    def __init__(
        self,
        client: HomeAssistantOfficialMcpClient,
        *,
        catalog_sha256: str,
        admissions: tuple[AssistToolAdmission, ...],
        state_reader: HomeAssistantStateReader,
    ) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", catalog_sha256) is None:
            raise ValueError("Home Assistant MCP catalog hash is invalid")
        effect_admissions = {
            admission.tool_name: admission
            for admission in admissions
            if admission.effect_class == "effect"
        }
        if not effect_admissions or len(effect_admissions) != len(admissions):
            raise ValueError("Home Assistant MCP effect admissions are invalid")
        self._client = client
        self._catalog_sha256 = catalog_sha256
        self._admissions = effect_admissions
        self._state_reader = state_reader

    async def execute(self, command: ToolRequest) -> ToolDispatchResult:
        tool_name, arguments, _entity_id = _effect_fields(command)
        admission = self._admissions.get(tool_name)
        if admission is None:
            raise ToolExecutionRejected("Home Assistant MCP tool is not admitted")
        try:
            result = await asyncio.to_thread(
                self._client.call,
                admission=admission,
                arguments=arguments,
                expected_catalog_sha256=self._catalog_sha256,
            )
        except HomeAssistantOfficialMcpError as exc:
            raise ToolExecutionRejected(str(exc)) from exc
        return ToolDispatchResult(
            True,
            None,
            {
                "response_sha256": result.response_sha256,
                "tool_name": result.tool_name,
            },
        )

    async def observe(
        self,
        command: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation:
        del dispatch
        _tool_name, _arguments, entity_id = _effect_fields(command)
        if command.expected_proof.get("entity_id") != entity_id:
            raise ValueError("Home Assistant MCP proof target differs")
        state = await self._state_reader.state(entity_id)
        observed = _select_observed(command.expected_proof, state)
        return ToolProofObservation(
            "verified" if observed == command.expected_proof else "pending",
            observed,
            {
                "entity_id": entity_id,
                "source": "home_assistant_rest_after_official_mcp_assist",
            },
        )


def _catalog(result: Mapping[str, Any]) -> AssistCatalog:
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list) or not 1 <= len(raw_tools) <= _MAX_CATALOG_TOOLS:
        raise HomeAssistantOfficialMcpError("Home Assistant MCP tool catalog is invalid")
    tools: list[AssistTool] = []
    for raw in raw_tools:
        if not isinstance(raw, dict) or set(raw) - {
            "annotations",
            "description",
            "inputSchema",
            "name",
            "outputSchema",
            "title",
        }:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP tool descriptor is invalid"
            )
        name = raw.get("name")
        description = raw.get("description", "")
        schema = raw.get("inputSchema")
        if (
            not isinstance(name, str)
            or _TOOL_NAME.fullmatch(name) is None
            or not isinstance(description, str)
            or len(description) > 8_192
            or not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
        ):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP tool descriptor is invalid"
            )
        safe_schema = dict(schema)
        validate_json_value(safe_schema)
        tools.append(AssistTool(name, description, safe_schema))
    tools.sort(key=lambda item: item.name)
    if len({tool.name for tool in tools}) != len(tools):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP tool names are ambiguous"
        )
    payload = [
        {
            "description": tool.description,
            "inputSchema": tool.input_schema,
            "name": tool.name,
        }
        for tool in tools
    ]
    if len(canonical_json_bytes(payload)) > _MAX_CATALOG_BYTES:
        raise HomeAssistantOfficialMcpError("Home Assistant MCP catalog is too large")
    return AssistCatalog(tuple(tools), canonical_sha256(payload))


def _validate_arguments(
    arguments: dict[str, JsonValue],
    schema: dict[str, JsonValue],
    required_arguments: tuple[str, ...],
) -> None:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    if set(arguments) - set(properties):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP arguments exceed the admitted schema"
        )
    if any(name not in properties for name in required_arguments):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP admission requires an unknown argument"
        )
    if any(name not in arguments for name in required_arguments):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP required argument is missing"
        )
    for name, value in arguments.items():
        property_schema = properties[name]
        if not isinstance(property_schema, dict):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP property schema is invalid"
            )
        expected_type = property_schema.get("type")
        if not _matches_json_type(value, expected_type):
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP argument type is invalid"
            )


def _matches_json_type(value: JsonValue, expected: object) -> bool:
    if isinstance(expected, list):
        return any(_matches_json_type(value, item) for item in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return False


def _tool_result(
    result: Mapping[str, Any], effect_class: Literal["read", "effect"]
) -> JsonValue:
    if result.get("isError") is True:
        raise HomeAssistantOfficialMcpError("Home Assistant MCP tool reported an error")
    content = result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP tool result is not one text payload"
        )
    try:
        payload = json.loads(content[0]["text"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP tool result is not JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP tool result contract is invalid"
        )
    if isinstance(payload.get("success"), bool):
        if payload["success"] is not True:
            raise HomeAssistantOfficialMcpError("Home Assistant rejected the Assist tool")
        if "result" not in payload:
            raise HomeAssistantOfficialMcpError(
                "Home Assistant MCP successful result is empty"
            )
        result_value = payload["result"]
        validate_json_value(result_value)
        return result_value

    response_type = payload.get("response_type")
    data = payload.get("data")
    if response_type == "error":
        raise HomeAssistantOfficialMcpError("Home Assistant rejected the Assist intent")
    if (
        response_type not in {"action_done", "query_answer"}
        or not isinstance(data, dict)
        or not isinstance(data.get("success"), list)
        or not isinstance(data.get("failed"), list)
        or data["failed"]
        or (effect_class == "effect" and response_type != "action_done")
        or (effect_class == "effect" and not data["success"])
    ):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP tool result contract is invalid"
        )
    validate_json_value(payload)
    return payload


def _decode_response(raw: bytes, content_type: str) -> dict[str, Any]:
    if content_type == "application/json":
        payload = raw
    elif content_type == "text/event-stream":
        events = [item for item in raw.replace(b"\r\n", b"\n").split(b"\n\n") if item]
        if len(events) != 1:
            raise HomeAssistantOfficialMcpError("Home Assistant MCP SSE is ambiguous")
        data = [line[6:] for line in events[0].split(b"\n") if line.startswith(b"data: ")]
        if len(data) != 1:
            raise HomeAssistantOfficialMcpError("Home Assistant MCP SSE is malformed")
        payload = data[0]
    else:
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP response content type is unsupported"
        )
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP response is invalid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise HomeAssistantOfficialMcpError(
            "Home Assistant MCP response is not a JSON object"
        )
    return decoded


def _effect_fields(
    command: ToolRequest,
) -> tuple[str, dict[str, JsonValue], str]:
    if set(command.arguments) != {"mcp_arguments", "mcp_tool_name", "readback_entity_id"}:
        raise ValueError("Home Assistant MCP command has wrong fields")
    tool_name = command.arguments["mcp_tool_name"]
    arguments = command.arguments["mcp_arguments"]
    entity_id = command.arguments["readback_entity_id"]
    if (
        not isinstance(tool_name, str)
        or _TOOL_NAME.fullmatch(tool_name) is None
        or not isinstance(arguments, dict)
        or not isinstance(entity_id, str)
        or re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z0-9_]+", entity_id) is None
    ):
        raise ValueError("Home Assistant MCP command values are invalid")
    validate_json_value(arguments)
    return tool_name, arguments, entity_id


def _select_observed(
    expected: dict[str, JsonValue], state: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    if set(expected) - {"attributes", "entity_id", "state"} or "entity_id" not in expected:
        raise ValueError("Home Assistant MCP expected proof is invalid")
    if state.get("entity_id") != expected["entity_id"]:
        raise ValueError("Home Assistant MCP state readback target differs")
    observed: dict[str, JsonValue] = {"entity_id": state["entity_id"]}
    if "state" in expected:
        observed["state"] = state.get("state")
    if "attributes" in expected:
        wanted = expected["attributes"]
        actual = state.get("attributes")
        if not isinstance(wanted, dict) or not isinstance(actual, dict):
            raise ValueError("Home Assistant MCP readback attributes are invalid")
        observed["attributes"] = {key: actual.get(key) for key in wanted}
    validate_json_value(observed)
    return observed


__all__ = [
    "AssistCatalog",
    "AssistToolAdmission",
    "HomeAssistantOfficialMcpClient",
    "HomeAssistantOfficialMcpConfig",
    "HomeAssistantOfficialMcpEffectAdapter",
    "HomeAssistantOfficialMcpError",
]
