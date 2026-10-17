"""Narrow, fail-closed Streamable HTTP client for one read-only MCP tool.

This is deliberately not a general MCP client or capability broker.  It can
only attest and call the exact, pre-admitted tool in ``McpReadOnlyToolImport``.
It owns no credentials, conversation context, durable state, or execution
receipt; those belong to later Eva layers.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256, decode_json_bytes
from eva.contracts.capability import McpReadOnlyToolImport

_PROTOCOL_VERSION = "2025-06-18"
_ACCEPT = "application/json, text/event-stream"
_SESSION_BODY_MULTIPLIER = 3
_RESPONSE_POLL_SECONDS = 0.05


class McpReadOnlyError(RuntimeError):
    """A fixed MCP import could not be safely attested or called."""


@dataclass(frozen=True)
class McpReadOnlyResult:
    """Only the schema-validated structured result of the fixed tool."""

    server_id: str
    tool_name: str
    resource_id: str
    structured_content: dict[str, JsonValue]


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


class McpReadOnlyStreamableHttpAdapter:
    """Call one exact read-only MCP tool through its fixed Streamable HTTP URL."""

    def __init__(self, import_: McpReadOnlyToolImport) -> None:
        self._import = import_
        _validate_closed_object_schema(import_.input_schema, name="input_schema")
        _validate_closed_object_schema(import_.output_schema, name="output_schema")
        self._opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}), _NoRedirect()
        )

    def read_fixed(
        self, *, resource_id: str, arguments: dict[str, JsonValue]
    ) -> McpReadOnlyResult:
        """Perform the bounded MCP lifecycle and call the one admitted tool.

        ``resource_id`` is intentionally a separate required parameter so a
        caller cannot accidentally turn this fixed import into a generic
        resource reader.
        """

        if resource_id != self._import.resource_id:
            raise McpReadOnlyError("MCP resource is not admitted by this import")
        _validate_value_against_closed_object_schema(
            arguments, self._import.input_schema, name="MCP arguments"
        )

        deadline = time.monotonic() + self._import.timeout_milliseconds / 1000
        session = McpStreamableHttpSession(
            endpoint=self._import.endpoint,
            opener=self._opener,
            deadline=deadline,
            maximum_request_bytes=self._import.maximum_request_bytes,
            maximum_response_bytes=self._import.maximum_response_bytes,
        )
        try:
            initialized = session.request(
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "eva2-read-only-mcp", "version": "1"},
                },
                initialization=True,
            )
            _require_initialize_result(initialized)
            session.complete_initialization(initialized)
            session.notify_initialized()
            tools = session.request(request_id=2, method="tools/list", params={})
            _require_admitted_tool(tools, self._import)
            result = session.request(
                request_id=3,
                method="tools/call",
                params={"name": self._import.tool_name, "arguments": arguments},
            )
            structured_content = _require_structured_result(result)
            _validate_value_against_closed_object_schema(
                structured_content, self._import.output_schema, name="MCP structured result"
            )
        except McpReadOnlyError:
            raise
        except (OSError, socket.timeout, TimeoutError, ValueError, TypeError) as exc:
            raise McpReadOnlyError("MCP read-only call failed safely") from exc

        return McpReadOnlyResult(
            server_id=self._import.server_id,
            tool_name=self._import.tool_name,
            resource_id=self._import.resource_id,
            structured_content=structured_content,
        )


class McpStreamableHttpSession:
    def __init__(
        self,
        *,
        endpoint: str,
        opener: urllib_request.OpenerDirector,
        deadline: float,
        maximum_request_bytes: int,
        maximum_response_bytes: int,
        response_progress_timeout_seconds: float = _RESPONSE_POLL_SECONDS,
    ) -> None:
        if response_progress_timeout_seconds <= 0:
            raise ValueError("MCP response progress timeout must be positive")
        self._endpoint = endpoint
        self._opener = opener
        self._deadline = deadline
        self._maximum_request_bytes = maximum_request_bytes
        self._maximum_response_bytes = maximum_response_bytes
        self._maximum_session_bytes = maximum_response_bytes * _SESSION_BODY_MULTIPLIER
        self._response_progress_timeout_seconds = response_progress_timeout_seconds
        self._received_bytes = 0
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    def request(
        self,
        *,
        request_id: int,
        method: str,
        params: dict[str, JsonValue],
        initialization: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = self._post(payload, expect_response=True, initialization=initialization)
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise McpReadOnlyError("MCP response does not bind to its request")
        if "error" in response or not isinstance(response.get("result"), dict):
            raise McpReadOnlyError("MCP server rejected the read-only request")
        return response["result"]

    def notify_initialized(self) -> None:
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
            initialization=False,
        )

    def complete_initialization(self, result: Mapping[str, Any]) -> None:
        protocol_version = result.get("protocolVersion")
        if protocol_version != _PROTOCOL_VERSION:
            raise McpReadOnlyError("MCP server did not negotiate the admitted protocol version")
        self._protocol_version = protocol_version

    def _post(
        self,
        payload: dict[str, JsonValue],
        *,
        expect_response: bool,
        initialization: bool,
    ) -> dict[str, Any] | None:
        body = canonical_json_bytes(payload)
        if len(body) > self._maximum_request_bytes:
            raise McpReadOnlyError("MCP request exceeds its admitted byte budget")
        timeout = self._remaining_timeout()
        headers = {"Accept": _ACCEPT, "Content-Type": "application/json"}
        if not initialization:
            if self._protocol_version is None:
                raise McpReadOnlyError("MCP session was not initialized")
            headers["MCP-Protocol-Version"] = self._protocol_version
            if self._session_id is not None:
                headers["Mcp-Session-Id"] = self._session_id
        request = urllib_request.Request(
            self._endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:  # noqa: S310
                status = response.status
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if initialization:
                    self._set_session_id(response.headers.get("Mcp-Session-Id"))
                raw = self._read_bounded(response)
        except urllib_error.HTTPError as exc:
            raise McpReadOnlyError("MCP server returned an HTTP error") from exc
        except urllib_error.URLError as exc:
            raise McpReadOnlyError("MCP endpoint is unavailable") from exc

        if not expect_response:
            if status != 202 or raw:
                raise McpReadOnlyError("MCP initialized notification was not accepted")
            return None
        if status != 200:
            raise McpReadOnlyError("MCP request did not return a successful response")
        if content_type == "application/json":
            return _decode_rpc_json(raw)
        if content_type == "text/event-stream":
            return _decode_single_rpc_sse(raw)
        raise McpReadOnlyError("MCP response has an unsupported content type")

    def _remaining_timeout(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise McpReadOnlyError("MCP read-only call exceeded its time budget")
        return remaining

    def _read_bounded(self, response: Any) -> bytes:
        socket_reader = _response_socket(response)
        socket_timeout_enforceable = True
        chunks: list[bytes] = []
        received = 0
        while True:
            remaining = self._remaining_timeout()
            if socket_timeout_enforceable:
                try:
                    socket_reader.settimeout(
                        min(remaining, self._response_progress_timeout_seconds)
                    )
                except OSError:
                    # urllib may already have buffered a complete Content-Length
                    # response and closed the socket.  Remaining reads are then
                    # in-memory only and cannot extend the deadline.
                    socket_timeout_enforceable = False
            try:
                chunk = response.read(1)
            except (socket.timeout, TimeoutError) as exc:
                if time.monotonic() >= self._deadline:
                    raise McpReadOnlyError("MCP read-only call exceeded its time budget") from exc
                raise McpReadOnlyError("MCP response did not make timely progress") from exc
            if not chunk:
                break
            received += len(chunk)
            if received > self._maximum_response_bytes:
                raise McpReadOnlyError("MCP response exceeds its admitted byte budget")
            chunks.append(chunk)
            self._remaining_timeout()
        raw = b"".join(chunks)
        self._received_bytes += received
        if self._received_bytes > self._maximum_session_bytes:
            raise McpReadOnlyError("MCP session exceeds its admitted byte budget")
        return raw

    def _set_session_id(self, session_id: str | None) -> None:
        if session_id is None:
            return
        if not session_id or len(session_id) > 512 or any(
            not 0x21 <= ord(character) <= 0x7E for character in session_id
        ):
            raise McpReadOnlyError("MCP server supplied an invalid session identifier")
        self._session_id = session_id


def _require_initialize_result(result: Mapping[str, Any]) -> None:
    if result.get("protocolVersion") != _PROTOCOL_VERSION:
        raise McpReadOnlyError("MCP server did not negotiate the admitted protocol version")
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
        raise McpReadOnlyError("MCP server does not declare tool capability")


def _require_admitted_tool(
    result: Mapping[str, Any], import_: McpReadOnlyToolImport
) -> None:
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise McpReadOnlyError("MCP tools list is invalid")
    matching = [tool for tool in tools if isinstance(tool, dict) and tool.get("name") == import_.tool_name]
    if len(matching) != 1:
        raise McpReadOnlyError("MCP admitted tool is missing or ambiguous")
    tool = matching[0]
    if canonical_sha256(tool.get("inputSchema")) != import_.input_schema_sha256:
        raise McpReadOnlyError("MCP admitted tool input schema drifted")
    if canonical_sha256(tool.get("outputSchema")) != import_.output_schema_sha256:
        raise McpReadOnlyError("MCP admitted tool output schema drifted")


def _require_structured_result(result: Mapping[str, Any]) -> dict[str, JsonValue]:
    if result.get("isError") is True:
        raise McpReadOnlyError("MCP tool reported an execution error")
    structured_content = result.get("structuredContent")
    if not isinstance(structured_content, dict):
        raise McpReadOnlyError("MCP tool did not return structured content")
    try:
        validate_json_value(structured_content)
    except ValueError as exc:
        raise McpReadOnlyError("MCP structured result is not safe JSON") from exc
    return structured_content


def _decode_rpc_json(raw: bytes) -> dict[str, Any]:
    try:
        return decode_json_bytes(raw, maximum_bytes=len(raw))
    except ValueError as exc:
        raise McpReadOnlyError("MCP response is not valid JSON-RPC JSON") from exc


def _response_socket(response: Any) -> Any:
    socket_reader = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if socket_reader is None or not callable(getattr(socket_reader, "settimeout", None)):
        raise McpReadOnlyError("MCP response cannot enforce its time budget")
    return socket_reader


def _decode_single_rpc_sse(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise McpReadOnlyError("MCP SSE response is not UTF-8") from exc

    events = [event for event in text.replace("\r\n", "\n").split("\n\n") if event]
    responses: list[dict[str, Any]] = []
    for event in events:
        data_lines: list[str] = []
        for line in event.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if not separator:
                raise McpReadOnlyError("MCP SSE response is malformed")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field not in {"event", "id", "retry"}:
                raise McpReadOnlyError("MCP SSE response contains an unknown field")
        if not data_lines:
            raise McpReadOnlyError("MCP SSE event has no JSON-RPC data")
        responses.append(_decode_rpc_json("\n".join(data_lines).encode("utf-8")))
    if len(responses) != 1:
        raise McpReadOnlyError("MCP SSE response is not a single RPC response")
    response = responses[0]
    if "method" in response:
        raise McpReadOnlyError("MCP server attempted an unsupported callback")
    return response


def _validate_closed_object_schema(schema: Mapping[str, JsonValue], *, name: str) -> None:
    allowed_root = {"type", "additionalProperties", "properties", "required"}
    if set(schema) - allowed_root or schema.get("type") != "object":
        raise McpReadOnlyError(f"{name} is outside Eva's read-only schema profile")
    if schema.get("additionalProperties") is not False:
        raise McpReadOnlyError(f"{name} must close additional properties")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise McpReadOnlyError(f"{name} must declare an object properties map")
    for property_name, property_schema in properties.items():
        if not isinstance(property_name, str) or not isinstance(property_schema, dict):
            raise McpReadOnlyError(f"{name} contains an invalid property")
        if set(property_schema) != {"type"} or property_schema.get("type") != "string":
            raise McpReadOnlyError(f"{name} only permits string properties")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise McpReadOnlyError(f"{name} has an invalid required list")
    if len(required) != len(set(required)):
        raise McpReadOnlyError(f"{name} has an invalid required list")
    if any(item not in properties for item in required):
        raise McpReadOnlyError(f"{name} requires an undeclared property")


def _validate_value_against_closed_object_schema(
    value: object, schema: Mapping[str, JsonValue], *, name: str
) -> None:
    if not isinstance(value, dict):
        raise McpReadOnlyError(f"{name} must be an object")
    properties = schema["properties"]
    assert isinstance(properties, dict)
    required = schema.get("required", [])
    assert isinstance(required, list)
    if set(value) - set(properties):
        raise McpReadOnlyError(f"{name} contains a property outside the admitted schema")
    if any(item not in value for item in required):
        raise McpReadOnlyError(f"{name} is missing a required property")
    if any(not isinstance(item, str) for item in value.values()):
        raise McpReadOnlyError(f"{name} only permits string values")
    validate_json_value(value)


__all__ = [
    "McpReadOnlyError",
    "McpReadOnlyResult",
    "McpReadOnlyStreamableHttpAdapter",
    "McpStreamableHttpSession",
]
