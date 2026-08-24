from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from eva.contracts.capability import McpReadOnlyToolImport
from eva.contracts.canonical_json import canonical_sha256


def _value() -> dict[str, object]:
    input_schema = {"additionalProperties": False, "properties": {}, "type": "object"}
    output_schema = {
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "type": "object",
    }
    return {
        "contract": "McpReadOnlyToolImport/v1",
        "schema_version": 1,
        "server_id": "mcp:public-read",
        "transport": "streamable_http",
        "server_image_sha256": "a" * 64,
        "sandbox_profile_sha256": "b" * 64,
        "endpoint": "http://mcp-sidecar:8080/mcp",
        "tool_name": "public.read",
        "effect_class": "read",
        "resource_id": "resource:public:fixed",
        "timeout_milliseconds": 2000,
        "maximum_request_bytes": 1024,
        "maximum_response_bytes": 4096,
        "input_schema": input_schema,
        "input_schema_sha256": canonical_sha256(input_schema),
        "output_schema": output_schema,
        "output_schema_sha256": canonical_sha256(output_schema),
        "provider_scopes": [],
    }


def test_read_only_mcp_import_binds_exact_public_tool_contract() -> None:
    imported = McpReadOnlyToolImport.model_validate(_value())
    assert imported.effect_class == "read"
    assert imported.provider_scopes == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("effect_class", "effect"),
        ("timeout_milliseconds", 999),
        ("resource_id", "resource:*"),
        ("provider_scopes", ["never"]),
        ("secret_ref", "secret:forbidden"),
    ),
)
def test_read_only_mcp_import_rejects_escalation(field: str, value: object) -> None:
    candidate = _value()
    candidate[field] = value
    with pytest.raises(ValidationError):
        McpReadOnlyToolImport.model_validate(candidate)


def test_read_only_mcp_import_rejects_remote_schema_reference_or_hash_drift() -> None:
    candidate = _value()
    schema = deepcopy(candidate["input_schema"])
    assert isinstance(schema, dict)
    schema["$ref"] = "https://untrusted.invalid/schema.json"
    candidate["input_schema"] = schema
    candidate["input_schema_sha256"] = canonical_sha256(schema)
    with pytest.raises(ValidationError, match="may not contain \\$ref"):
        McpReadOnlyToolImport.model_validate(candidate)

    candidate = _value()
    candidate["output_schema_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="does not bind"):
        McpReadOnlyToolImport.model_validate(candidate)
