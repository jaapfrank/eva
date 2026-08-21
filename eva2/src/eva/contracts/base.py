"""Shared, framework-free primitives for Eva's durable contracts.

This package deliberately depends on the Python standard library and Pydantic
only.  Contract instances are immutable and reject fields that are not part of
their declared wire version.
"""

from __future__ import annotations

import json
import math
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

CANONICAL_JSON_PROFILE = "EvaCanonicalJSON/v1"
MAX_CONTRACT_BYTES = 1_048_576
MAX_NESTING_DEPTH = 64
MAX_COLLECTION_ITEMS = 1_024
JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991
RELATIVE_ARTIFACT_PATH_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:/(?:[A-Za-z0-9_-][A-Za-z0-9._-]*"
    r"|\.[A-Za-z0-9_-][A-Za-z0-9._-]*"
    r"|\.\.[A-Za-z0-9._-]+))*$"
)
RELATIVE_ARTIFACT_PATH_LINE_TERMINATOR_PATTERN = r"[\n\r\u2028\u2029]"


def _timestamp(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("must be canonical UTC YYYY-MM-DDTHH:MM:SSZ") from exc
    return value


def _date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("must be an ISO 8601 calendar date") from exc
    return value


Identifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=192,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/@+\-=]*$",
    ),
]
ShortText: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=512)]
NaturalText: TypeAlias = Annotated[
    str, StringConstraints(min_length=1, max_length=65_536)
]
Sha256: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MimeType: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=127,
        pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$",
    ),
]
LanguageTag: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=2, max_length=35, pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$"
    ),
]
RelativeArtifactPath: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=RELATIVE_ARTIFACT_PATH_PATTERN,
    ),
    Field(
        json_schema_extra={
            "not": {"pattern": RELATIVE_ARTIFACT_PATH_LINE_TERMINATOR_PATTERN}
        }
    ),
]
Timestamp: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=20,
        max_length=20,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    ),
    AfterValidator(_timestamp),
]
CalendarDate: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=10,
        max_length=10,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    ),
    AfterValidator(_date),
]
JsonInteger: TypeAlias = Annotated[
    int, Field(ge=-JSON_SAFE_INTEGER_MAX, le=JSON_SAFE_INTEGER_MAX)
]
type JsonScalar = str | JsonInteger | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def _validate_unicode(value: str) -> None:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("all strings and object keys must be NFC normalized")
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("surrogate code points are forbidden")
        if 0xFDD0 <= codepoint <= 0xFDEF or (codepoint & 0xFFFF) in {0xFFFE, 0xFFFF}:
            raise ValueError("Unicode noncharacters are forbidden")
        if codepoint == 0:
            raise ValueError("NUL is forbidden")


def validate_json_value(value: Any, *, depth: int = 0) -> None:
    """Validate the language-neutral subset used by durable JSON fields."""

    if depth > MAX_NESTING_DEPTH:
        raise ValueError("maximum JSON nesting depth exceeded")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX:
            raise ValueError(
                "JSON integer exceeds the exact I-JSON interoperable range"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON numbers are forbidden")
        raise ValueError(
            "floating-point JSON numbers are forbidden; use scaled integers"
        )
    if isinstance(value, str):
        _validate_unicode(value)
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("maximum JSON array length exceeded")
        for item in value:
            validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("maximum JSON object size exceeded")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_unicode(key)
            validate_json_value(item, depth=depth + 1)
        return
    raise ValueError(
        f"value of type {type(value).__name__} is not language-neutral JSON"
    )


class ContractModel(BaseModel):
    """Base for every versioned durable or wire contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        validate_default=True,
        str_strip_whitespace=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _guard_language_neutral_input(cls, value: Any) -> Any:
        validate_json_value(value)
        return value

    @model_validator(mode="after")
    def _guard_direct_construction_size(self) -> "ContractModel":
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_CONTRACT_BYTES:
            raise ValueError(f"contract exceeds {MAX_CONTRACT_BYTES} UTF-8 bytes")
        return self


class Version1Contract(ContractModel):
    """Required version metadata shared by v1 contracts.

    The content hash is computed over the final canonical contract bytes by the
    enclosing record/evidence authority.  A caller-supplied self-hash would be
    circular and could be mistaken for earned evidence, so it is not a field on
    this base type.
    """

    schema_version: Literal[1]


class Version2Contract(ContractModel):
    """Required version metadata for an additive, non-v1-compatible wire shape."""

    schema_version: Literal[2]
