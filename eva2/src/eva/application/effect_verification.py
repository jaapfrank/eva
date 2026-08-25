"""Declarative, connector-neutral projection for independent effect proof."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256


_FIELD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EffectVerificationError(ValueError):
    """A verification projection or the selected state is invalid."""


@dataclass(frozen=True, slots=True)
class EffectVerificationField:
    """Map one host-owned expected value to one provider-state value."""

    name: str
    expected_source: Literal["arguments", "resource"]
    expected_pointer: str
    observed_pointer: str

    def __post_init__(self) -> None:
        if _FIELD_NAME.fullmatch(self.name) is None:
            raise EffectVerificationError("verification field name is invalid")
        if self.expected_source not in {"arguments", "resource"}:
            raise EffectVerificationError("verification expected source is invalid")
        _pointer_tokens(self.observed_pointer)
        if self.expected_source == "resource":
            if self.expected_pointer != "":
                raise EffectVerificationError(
                    "resource verification field cannot use a pointer"
                )
        else:
            _pointer_tokens(self.expected_pointer)

    def authority_payload(self) -> dict[str, JsonValue]:
        return {
            "expectedPointer": self.expected_pointer,
            "expectedSource": self.expected_source,
            "name": self.name,
            "observedPointer": self.observed_pointer,
        }


@dataclass(frozen=True, slots=True)
class EffectVerificationProjection:
    """Select the exact comparable proof fields without executable code."""

    fields: tuple[EffectVerificationField, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.fields) <= 32:
            raise EffectVerificationError(
                "effect verification requires between 1 and 32 fields"
            )
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise EffectVerificationError("verification field names must be unique")
        if names != sorted(names):
            raise EffectVerificationError(
                "verification fields must be sorted by name"
            )

    def authority_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": "EffectVerificationProjection/v1",
            "fields": [field.authority_payload() for field in self.fields],
        }

    def project_expected(
        self, resource: str, arguments: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        validate_json_value(arguments)
        selected: dict[str, JsonValue] = {}
        for field in self.fields:
            source: JsonValue = (
                resource if field.expected_source == "resource" else arguments
            )
            selected[field.name] = _resolve(source, field.expected_pointer)
        validate_json_value(selected)
        return selected

    def project_observed(self, state: JsonValue) -> dict[str, JsonValue]:
        validate_json_value(state)
        selected = {
            field.name: _resolve(state, field.observed_pointer)
            for field in self.fields
        }
        validate_json_value(selected)
        return selected

    def matches(
        self,
        *,
        resource: str,
        arguments: dict[str, JsonValue],
        observed_state: JsonValue,
    ) -> bool:
        """Compare canonical JSON, so booleans never equal numeric lookalikes."""

        return canonical_sha256(self.project_expected(resource, arguments)) == (
            canonical_sha256(self.project_observed(observed_state))
        )

    @classmethod
    def from_authority_payload(
        cls, payload: object
    ) -> "EffectVerificationProjection":
        if not isinstance(payload, dict) or set(payload) != {"contract", "fields"}:
            raise EffectVerificationError("verification projection fields differ")
        if payload.get("contract") != "EffectVerificationProjection/v1":
            raise EffectVerificationError("verification projection contract differs")
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list):
            raise EffectVerificationError("verification projection fields are absent")
        fields: list[EffectVerificationField] = []
        for raw in raw_fields:
            if not isinstance(raw, dict) or set(raw) != {
                "expectedPointer",
                "expectedSource",
                "name",
                "observedPointer",
            }:
                raise EffectVerificationError("verification field authority differs")
            if not all(
                isinstance(raw[key], str)
                for key in (
                    "expectedPointer",
                    "expectedSource",
                    "name",
                    "observedPointer",
                )
            ):
                raise EffectVerificationError(
                    "verification field authority values are invalid"
                )
            try:
                fields.append(
                    EffectVerificationField(
                        name=raw["name"],
                        expected_source=raw["expectedSource"],  # type: ignore[arg-type]
                        expected_pointer=raw["expectedPointer"],
                        observed_pointer=raw["observedPointer"],
                    )
                )
            except (KeyError, TypeError) as error:
                raise EffectVerificationError(
                    "verification field authority is invalid"
                ) from error
        projection = cls(tuple(fields))
        if projection.authority_payload() != payload:
            raise EffectVerificationError("verification projection is not canonical")
        return projection


def _resolve(value: JsonValue, pointer: str) -> JsonValue:
    current = value
    for token in _pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                raise EffectVerificationError(
                    "verification pointer selects an absent object field"
                )
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise EffectVerificationError(
                    "verification pointer contains a noncanonical array index"
                )
            index = int(token)
            if index >= len(current):
                raise EffectVerificationError(
                    "verification pointer selects an absent array item"
                )
            current = current[index]
            continue
        raise EffectVerificationError(
            "verification pointer descends through a scalar value"
        )
    return current


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise EffectVerificationError("verification pointer is not JSON Pointer")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        while index < len(raw):
            if raw[index] == "~" and (
                index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}
            ):
                raise EffectVerificationError("verification pointer escape is invalid")
            index += 2 if raw[index] == "~" else 1
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


__all__ = [
    "EffectVerificationError",
    "EffectVerificationField",
    "EffectVerificationProjection",
]
