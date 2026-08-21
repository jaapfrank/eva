"""EvaCanonicalJSON/v1 parser and encoder.

The profile is intentionally smaller than generic JSON: UTF-8 without BOM,
NFC strings, unique object keys, integers instead of binary floats, bounded
nesting/collections, and lexicographically sorted keys with no insignificant
whitespace.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import MAX_CONTRACT_BYTES, ContractModel, JsonValue, validate_json_value

ContractT = TypeVar("ContractT", bound=ContractModel)


class DuplicateKeyError(ValueError):
    """Raised when an object repeats a key on the wire."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point JSON number is forbidden: {value}")


def decode_json_bytes(
    data: bytes, *, maximum_bytes: int = MAX_CONTRACT_BYTES
) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise TypeError("wire input must be bytes")
    if maximum_bytes < 1:
        raise ValueError("maximum_bytes must be positive")
    if len(data) > maximum_bytes:
        raise ValueError(f"wire payload exceeds {maximum_bytes} bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("wire payload is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("wire payload is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("contract wire payload must be a JSON object")
    validate_json_value(value)
    return value


def canonical_json_bytes(value: BaseModel | JsonValue) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    validate_json_value(payload)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise ValueError(f"canonical payload exceeds {MAX_CONTRACT_BYTES} bytes")
    return encoded


def canonical_sha256(value: BaseModel | JsonValue) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_contract_bytes(
    data: bytes,
    model: type[ContractT],
    *,
    maximum_bytes: int = MAX_CONTRACT_BYTES,
) -> ContractT:
    return model.model_validate(decode_json_bytes(data, maximum_bytes=maximum_bytes))


def require_canonical_bytes(
    data: bytes, *, maximum_bytes: int = MAX_CONTRACT_BYTES
) -> dict[str, Any]:
    decoded = decode_json_bytes(data, maximum_bytes=maximum_bytes)
    if canonical_json_bytes(decoded) != data:
        raise ValueError("wire payload is valid JSON but not EvaCanonicalJSON/v1")
    return decoded


def contract_reader(
    registry: Mapping[str, type[ContractModel]],
) -> Callable[[bytes], ContractModel]:
    """Create a closed reader; unknown contract names and major versions fail."""

    def read(data: bytes) -> ContractModel:
        decoded = require_canonical_bytes(data)
        discriminator = decoded.get("contract")
        if not isinstance(discriminator, str) or discriminator not in registry:
            raise ValueError("unknown contract name or major version")
        return registry[discriminator].model_validate(decoded)

    return read
