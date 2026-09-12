"""Shared closed-schema subset validation for admitted tool arguments.

This preserves the existing subset and ConversationConflict error semantics.
Provider catalog mode relaxes schema defaults; initiative callers remain strict.
It is not a general JSON Schema validator.
"""
from __future__ import annotations

from eva.application.conversation import ConversationConflict
from eva.contracts.base import JsonValue


def validate_schema_value(
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
        properties = schema.get("properties", {} if provider_schema else None)
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
            validate_schema_value(
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
            validate_schema_value(
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


__all__ = ["validate_schema_value"]
