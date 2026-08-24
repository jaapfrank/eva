"""Fail-closed contracts for Eva-owned, read-only external capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import Identifier, JsonValue, Sha256, Version1Contract, validate_json_value
from .canonical_json import canonical_sha256


def _validate_schema(value: object, *, name: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty JSON object")
    validate_json_value(value)
    pending: list[object] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if "$ref" in current:
                raise ValueError(f"{name} may not contain $ref")
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


class McpReadOnlyToolImport(Version1Contract):
    """One exact, no-secret MCP tool admitted solely for a fixed public read."""

    contract: Literal["McpReadOnlyToolImport/v1"]
    server_id: Identifier
    transport: Literal["streamable_http"]
    server_image_sha256: Sha256
    sandbox_profile_sha256: Sha256
    endpoint: str = Field(min_length=8, max_length=512, pattern=r"^https?://[^@/?#]+(?:/[^?#]*)?$")
    tool_name: Identifier
    effect_class: Literal["read"]
    resource_id: Identifier
    timeout_milliseconds: int = Field(ge=1_000, le=5_000)
    maximum_request_bytes: int = Field(ge=1, le=65_536)
    maximum_response_bytes: int = Field(ge=1, le=1_048_576)
    input_schema: dict[str, JsonValue]
    input_schema_sha256: Sha256
    output_schema: dict[str, JsonValue]
    output_schema_sha256: Sha256
    provider_scopes: list[Literal["never"]] = Field(max_length=0)

    @model_validator(mode="after")
    def _bind_and_restrict(self) -> "McpReadOnlyToolImport":
        _validate_schema(self.input_schema, name="input_schema")
        _validate_schema(self.output_schema, name="output_schema")
        if canonical_sha256(self.input_schema) != self.input_schema_sha256:
            raise ValueError("input_schema_sha256 does not bind input_schema")
        if canonical_sha256(self.output_schema) != self.output_schema_sha256:
            raise ValueError("output_schema_sha256 does not bind output_schema")
        return self
