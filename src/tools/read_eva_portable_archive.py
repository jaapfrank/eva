#!/usr/bin/env python3
"""DBOS-free, database-free generic reader for EvaPortableArchive/v1.

This file intentionally imports only Python's standard library.  It validates
the complete archive in two record passes, derives every readback in memory,
then publishes the readback directory with one atomic rename.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_JSON_BYTES = 1_048_576
ARCHIVE_NAMESPACE = uuid.UUID("276dbeb6-d012-5519-9ba9-4cb279c94dd4")
CONTRACT_MANIFEST_SHA256 = (
    "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5"
)
CANONICAL_PROFILE_SHA256 = (
    "d3cd8347c77de4a57c4ad7834b8ba170516b420c9abd5079dac1442d0a9d0ad9"
)
PORTABLE_MANIFEST_SHA256 = (
    "de2e302c8d260707563a8c16af1a6dfc2a3932507013dddb79d7780a3e36dc68"
)
READER_MANIFEST_SHA256 = (
    "5b26d3b51a556b4b214b4d2d1cd664fb5532064d5eb0c7c7db70e833c320e876"
)
OBJECT_SCHEMA_SHA256 = (
    "1e951976fa1021893bb5bc3812a98997560b7fcf636d0e69573e4444ec4930a8"
)
SEALED_RECORD_JSONL_PATHS = (
    "records/001-eva_core-tenants-v1.jsonl",
    "records/002-eva_core-installations-v1.jsonl",
    "records/003-identity-pack-v1.jsonl",
    "records/004-policy-pack-v1.jsonl",
    "records/005-eva_core-principals-v1.jsonl",
    "records/006-eva_core-principal-bindings-v1.jsonl",
    "records/007-relationship-binding-v1.jsonl",
    "records/008-eva_core-conversations-v1.jsonl",
    "records/009-eva_core-messages-v1.jsonl",
    "records/010-eva_core-ingress-requests-v1.jsonl",
    "records/011-eva_core-episodes-v1.jsonl",
    "records/012-eva_core-episode-generations-v1.jsonl",
    "records/013-eva-core-episode-events-v1.jsonl",
    "records/014-outcome-view-v1.jsonl",
    "records/015-commitment-v1.jsonl",
    "records/016-consent-grant-v1.jsonl",
    "records/017-eva-core-model-attempts-v1.jsonl",
    "records/018-eva-core-plans-v1.jsonl",
    "records/019-eva-core-plan-steps-v1.jsonl",
    "records/020-attention-wake-v1.jsonl",
    "records/021-eva-core-connector-bindings-v1.jsonl",
    "records/022-eva-core-capability-manifests-v1.jsonl",
    "records/023-eva-core-capability-activations-v1.jsonl",
    "records/024-eva-effects-read-intents-v1.jsonl",
    "records/025-eva-effects-read-attempts-v1.jsonl",
    "records/026-read-observation-v1.jsonl",
    "records/027-eva-effects-effect-intents-v1.jsonl",
    "records/028-eva-effects-effect-attempts-v1.jsonl",
    "records/029-effect-receipt-v1.jsonl",
    "records/030-effect-readback-v1.jsonl",
    "records/031-eva-effects-deliveries-v1.jsonl",
    "records/032-eva-effects-circuit-breakers-v1.jsonl",
    "records/033-eva-memory-source-events-v1.jsonl",
    "records/034-eva-memory-fact-assertions-v1.jsonl",
    "records/035-eva-memory-current-facts-v1.jsonl",
    "records/036-eva-memory-erasure-tombstones-v1.jsonl",
    "records/037-eva-evidence-evidence-envelopes-v1.jsonl",
    "records/038-eva-evidence-evidence-links-v1.jsonl",
    "records/039-eva-evidence-object-refs-v1.jsonl",
)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHECKSUM_LINE_RE = re.compile(rb"^([0-9a-f]{64})  ([^\r\n]+)$")


class ArchiveValidationError(ValueError):
    """The archive cannot be accepted or partially reconstructed."""


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArchiveValidationError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise ArchiveValidationError(f"floating-point JSON value is forbidden: {value}")


def _reject_constant(value: str) -> None:
    raise ArchiveValidationError(f"non-finite JSON value is forbidden: {value}")


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        raise ArchiveValidationError("JSON nesting exceeds 64")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ArchiveValidationError("JSON integer exceeds the safe range")
        return
    if isinstance(value, float):
        raise ArchiveValidationError("floating-point JSON values are forbidden")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ArchiveValidationError("JSON string is not NFC")
        if "\x00" in value or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ArchiveValidationError("JSON string contains a forbidden code point")
        if any(
            0xFDD0 <= ord(ch) <= 0xFDEF or (ord(ch) & 0xFFFF) in {0xFFFE, 0xFFFF}
            for ch in value
        ):
            raise ArchiveValidationError("JSON string contains a Unicode noncharacter")
        return
    if isinstance(value, list):
        if len(value) > 1024:
            raise ArchiveValidationError("JSON array exceeds 1024 entries")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 1024:
            raise ArchiveValidationError("JSON object exceeds 1024 entries")
        for key, item in value.items():
            _validate_json_value(key, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
        return
    raise ArchiveValidationError(f"unsupported JSON type: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ArchiveValidationError("canonical JSON exceeds 1048576 bytes")
    return encoded


def _load_canonical_object(
    raw: bytes, *, label: str, allow_trailing_lf: bool = False
) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise ArchiveValidationError(f"{label} exceeds 1048576 bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ArchiveValidationError(f"{label} contains a UTF-8 BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveValidationError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ArchiveValidationError(f"{label} root is not an object")
    suffix = b"\n" if allow_trailing_lf else b""
    if _canonical_bytes(value) + suffix != raw:
        raise ArchiveValidationError(f"{label} is not EvaCanonicalJSON/v1")
    return value


def _safe_relative_path(value: str) -> None:
    if not value or value.startswith("/") or "\\" in value:
        raise ArchiveValidationError("unsafe archive-relative path")
    if any(ord(ch) < 0x20 or ch in "\u2028\u2029" for ch in value):
        raise ArchiveValidationError("archive path contains forbidden characters")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ArchiveValidationError("archive path contains an unsafe segment")


@contextmanager
def _root_descriptor(root: Path) -> Iterator[int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(str(root), flags)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArchiveValidationError("archive root is not a directory")
    except OSError as exc:
        raise ArchiveValidationError(
            "archive root cannot be opened without links"
        ) from exc
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _scan_regular_files(root_descriptor: int) -> set[str]:
    files: set[str] = set()

    def walk(directory_descriptor: int, prefix: str) -> None:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            names = os.listdir(directory_descriptor)
        except OSError as exc:
            raise ArchiveValidationError("archive directory cannot be listed") from exc
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            _safe_relative_path(relative)
            try:
                metadata = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
            except OSError as exc:
                raise ArchiveValidationError(
                    "archive entry cannot be inspected"
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child_descriptor = os.open(
                        name, directory_flags, dir_fd=directory_descriptor
                    )
                except OSError as exc:
                    raise ArchiveValidationError(
                        "archive directory cannot be opened without links"
                    ) from exc
                try:
                    if not stat.S_ISDIR(os.fstat(child_descriptor).st_mode):
                        raise ArchiveValidationError(
                            "archive contains a linked/special directory"
                        )
                    walk(child_descriptor, relative)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                if relative in files:
                    raise ArchiveValidationError("archive path appears more than once")
                files.add(relative)
            else:
                raise ArchiveValidationError("archive contains a linked/special entry")

    walk(root_descriptor, "")
    return files


def _read_regular(
    root_descriptor: int, relative: str, *, maximum: int | None = None
) -> bytes:
    _safe_relative_path(relative)
    read_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    directory_flags = read_flags | getattr(os, "O_DIRECTORY", 0)
    opened: list[int] = []
    parent = root_descriptor
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            parent = os.open(part, directory_flags, dir_fd=parent)
            opened.append(parent)
            if not stat.S_ISDIR(os.fstat(parent).st_mode):
                raise ArchiveValidationError(f"{relative} has a non-directory parent")
        descriptor = os.open(parts[-1], read_flags, dir_fd=parent)
        opened.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ArchiveValidationError(
                f"{relative} is not a single-link regular file"
            )
        if maximum is not None and before.st_size > maximum:
            raise ArchiveValidationError(f"{relative} exceeds its byte limit")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        raw = b"".join(chunks)
        if identity_before != identity_after or len(raw) != before.st_size:
            raise ArchiveValidationError(f"{relative} changed while being read")
        return raw
    except OSError as exc:
        raise ArchiveValidationError(
            f"{relative} cannot be opened without links"
        ) from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _parse_checksums(root: int, actual_files: set[str]) -> tuple[dict[str, str], str]:
    raw = _read_regular(root, "checksums.sha256", maximum=16_777_216)
    if raw and not raw.endswith(b"\n"):
        raise ArchiveValidationError("checksums.sha256 must end in LF")
    entries: dict[str, str] = {}
    previous: bytes | None = None
    for line in raw.splitlines():
        match = CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ArchiveValidationError("malformed checksums.sha256 line")
        digest = match.group(1).decode("ascii")
        try:
            relative = match.group(2).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ArchiveValidationError("checksum path is not UTF-8") from exc
        _safe_relative_path(relative)
        encoded_path = relative.encode("utf-8")
        if previous is not None and encoded_path <= previous:
            raise ArchiveValidationError(
                "checksum paths are duplicated or out of order"
            )
        previous = encoded_path
        if relative in {"archive.json", "checksums.sha256"}:
            raise ArchiveValidationError("noncyclic envelope file self-hashes")
        entries[relative] = digest
    expected_files = set(entries) | {"archive.json", "checksums.sha256"}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ArchiveValidationError(
            f"checksum path set differs (missing={missing}, extra={extra})"
        )
    for relative, expected in entries.items():
        actual = hashlib.sha256(_read_regular(root, relative)).hexdigest()
        if actual != expected:
            raise ArchiveValidationError(f"checksum mismatch for {relative}")
    return entries, hashlib.sha256(raw).hexdigest()


def _resolve_pointer(document: Any, fragment: str) -> Any:
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise ArchiveValidationError("unsupported JSON Schema anchor")
    current = document
    for raw in fragment[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            current = (
                current[int(token)] if isinstance(current, list) else current[token]
            )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ArchiveValidationError(
                "JSON Schema reference cannot be resolved"
            ) from exc
    return current


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _schema_valid(
    value: Any,
    schema: Any,
    registry: dict[str, dict[str, Any]],
    root: dict[str, Any],
) -> bool:
    try:
        _validate_schema(value, schema, registry, root, path="$probe")
    except ArchiveValidationError:
        return False
    return True


_SCHEMA_VALIDATION_KEYWORDS = {
    "$ref",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "type",
    "const",
    "enum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "uniqueItems",
    "prefixItems",
    "items",
    "contains",
    "minContains",
    "maxContains",
    "minProperties",
    "maxProperties",
    "required",
    "properties",
    "patternProperties",
    "propertyNames",
    "additionalProperties",
}
_SCHEMA_ANNOTATION_KEYWORDS = {
    "$id",
    "$schema",
    "$defs",
    "$comment",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
    "discriminator",
}


def _preflight_schema(schema: Any, *, label: str) -> None:
    """Fail closed if an embedded schema asks for validation we do not implement."""

    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ArchiveValidationError(f"{label}: schema is not an object/boolean")
    unknown = set(schema) - _SCHEMA_VALIDATION_KEYWORDS - _SCHEMA_ANNOTATION_KEYWORDS
    if unknown:
        raise ArchiveValidationError(
            f"{label}: unsupported keyword {sorted(unknown)[0]!r}"
        )
    if "format" in schema and schema["format"] != "uuid":
        raise ArchiveValidationError(f"{label}: unsupported format")
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = schema.get(keyword, [])
        if not isinstance(children, list):
            raise ArchiveValidationError(f"{label}: {keyword} is not an array")
        for index, child in enumerate(children):
            _preflight_schema(child, label=f"{label}.{keyword}[{index}]")
    for keyword in (
        "not",
        "if",
        "then",
        "else",
        "items",
        "contains",
        "propertyNames",
        "additionalProperties",
    ):
        if keyword in schema:
            _preflight_schema(schema[keyword], label=f"{label}.{keyword}")
    for keyword in ("$defs", "properties", "patternProperties"):
        children = schema.get(keyword, {})
        if not isinstance(children, dict):
            raise ArchiveValidationError(f"{label}: {keyword} is not an object")
        for name, child in children.items():
            _preflight_schema(child, label=f"{label}.{keyword}.{name}")


def _validate_schema(
    value: Any,
    schema: Any,
    registry: dict[str, dict[str, Any]],
    root: dict[str, Any],
    *,
    path: str,
) -> None:
    if schema is True:
        return
    if schema is False:
        raise ArchiveValidationError(f"{path}: false JSON Schema")
    if not isinstance(schema, dict):
        raise ArchiveValidationError("embedded JSON Schema is not an object/boolean")
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            raise ArchiveValidationError("JSON Schema $ref is not text")
        base, marker, fragment = reference.partition("#")
        target_root = root if not base else registry.get(base)
        if target_root is None:
            raise ArchiveValidationError(f"unbound JSON Schema reference: {base}")
        target = _resolve_pointer(target_root, fragment if marker else "")
        _validate_schema(value, target, registry, target_root, path=path)
    if "allOf" in schema:
        for child in schema["allOf"]:
            _validate_schema(value, child, registry, root, path=path)
    if "anyOf" in schema and not any(
        _schema_valid(value, child, registry, root) for child in schema["anyOf"]
    ):
        raise ArchiveValidationError(f"{path}: no anyOf branch matched")
    if (
        "oneOf" in schema
        and sum(
            _schema_valid(value, child, registry, root) for child in schema["oneOf"]
        )
        != 1
    ):
        raise ArchiveValidationError(f"{path}: oneOf did not match exactly once")
    if "not" in schema and _schema_valid(value, schema["not"], registry, root):
        raise ArchiveValidationError(f"{path}: forbidden JSON Schema branch matched")
    if "if" in schema:
        branch = (
            "then" if _schema_valid(value, schema["if"], registry, root) else "else"
        )
        if branch in schema:
            _validate_schema(value, schema[branch], registry, root, path=path)
    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(types, list) or not any(
            isinstance(item, str) and _schema_type_matches(value, item)
            for item in types
        ):
            raise ArchiveValidationError(f"{path}: JSON type differs")
    if "const" in schema and value != schema["const"]:
        raise ArchiveValidationError(f"{path}: const differs")
    if "enum" in schema and value not in schema["enum"]:
        raise ArchiveValidationError(f"{path}: enum differs")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ArchiveValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ArchiveValidationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ArchiveValidationError(f"{path}: string pattern differs")
        if schema.get("format") == "uuid":
            try:
                canonical_uuid = str(uuid.UUID(value))
            except (ValueError, AttributeError) as exc:
                raise ArchiveValidationError(f"{path}: canonical UUID differs") from exc
            if canonical_uuid != value:
                raise ArchiveValidationError(f"{path}: canonical UUID differs")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ArchiveValidationError(f"{path}: integer is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ArchiveValidationError(f"{path}: integer is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ArchiveValidationError(f"{path}: integer is below exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ArchiveValidationError(f"{path}: integer is above exclusiveMaximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ArchiveValidationError(f"{path}: array is too short")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ArchiveValidationError(f"{path}: array is too long")
        if schema.get("uniqueItems"):
            encoded = [_canonical_bytes(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ArchiveValidationError(f"{path}: array values are not unique")
        prefixes = schema.get("prefixItems", [])
        for index, child in enumerate(prefixes):
            if index < len(value):
                _validate_schema(
                    value[index], child, registry, root, path=f"{path}[{index}]"
                )
        items = schema.get("items")
        if items is not None:
            for index in range(len(prefixes), len(value)):
                _validate_schema(
                    value[index], items, registry, root, path=f"{path}[{index}]"
                )
        if "contains" in schema:
            count = sum(
                _schema_valid(item, schema["contains"], registry, root)
                for item in value
            )
            if count < schema.get("minContains", 1):
                raise ArchiveValidationError(f"{path}: contains count is too small")
            if "maxContains" in schema and count > schema["maxContains"]:
                raise ArchiveValidationError(f"{path}: contains count is too large")
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            raise ArchiveValidationError(f"{path}: object has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ArchiveValidationError(f"{path}: object has too many properties")
        required = schema.get("required", [])
        if any(key not in value for key in required):
            raise ArchiveValidationError(f"{path}: required property is absent")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        handled: set[str] = set()
        for key, item in value.items():
            if key in properties:
                handled.add(key)
                _validate_schema(
                    item, properties[key], registry, root, path=f"{path}.{key}"
                )
            for pattern, child in patterns.items():
                if re.search(pattern, key):
                    handled.add(key)
                    _validate_schema(item, child, registry, root, path=f"{path}.{key}")
            if "propertyNames" in schema:
                _validate_schema(
                    key, schema["propertyNames"], registry, root, path=f"{path}.<key>"
                )
        additional = schema.get("additionalProperties", True)
        for key in set(value) - handled:
            if additional is False:
                raise ArchiveValidationError(f"{path}: unexpected property {key!r}")
            if isinstance(additional, dict):
                _validate_schema(
                    value[key], additional, registry, root, path=f"{path}.{key}"
                )


def _load_schema(
    root: int,
    relative: str,
    expected_sha256: str,
    registry: dict[str, dict[str, Any]],
    *,
    allow_trailing_lf: bool = False,
) -> dict[str, Any]:
    raw = _read_regular(root, relative, maximum=MAX_JSON_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ArchiveValidationError(f"schema hash mismatch: {relative}")
    schema = _load_canonical_object(
        raw, label=relative, allow_trailing_lf=allow_trailing_lf
    )
    schema_id = schema.get("$id")
    if not isinstance(schema_id, str) or schema_id in registry:
        raise ArchiveValidationError(f"schema ID is absent or duplicated: {relative}")
    _preflight_schema(schema, label=relative)
    registry[schema_id] = schema
    return schema


def _load_schema_registry(
    root: int, manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    contract_manifest_raw = _read_regular(root, "deploy/schemas/contracts/manifest.json")
    contract_manifest_hash = hashlib.sha256(contract_manifest_raw).hexdigest()
    if contract_manifest_hash != CONTRACT_MANIFEST_SHA256:
        raise ArchiveValidationError("contract manifest differs from pinned authority")
    if contract_manifest_hash != manifest["json_schema_manifest_sha256"]:
        raise ArchiveValidationError("archive contract schema manifest hash differs")
    contract_manifest = _load_canonical_object(
        contract_manifest_raw,
        label="deploy/schemas/contracts/manifest.json",
        allow_trailing_lf=True,
    )
    schemas = contract_manifest.get("schemas")
    if not isinstance(schemas, dict) or len(schemas) != 64:
        raise ArchiveValidationError("contract schema manifest is not the bound 64-set")
    profile_raw = _read_regular(
        root, "deploy/schemas/contracts/canonical-json-profile-v1.json"
    )
    profile_hash = hashlib.sha256(profile_raw).hexdigest()
    if profile_hash != CANONICAL_PROFILE_SHA256:
        raise ArchiveValidationError("canonical profile differs from pinned authority")
    if contract_manifest.get("profileSha256") != CANONICAL_PROFILE_SHA256:
        raise ArchiveValidationError("contract manifest profile pin differs")
    profile = _load_canonical_object(
        profile_raw,
        label="deploy/schemas/contracts/canonical-json-profile-v1.json",
        allow_trailing_lf=True,
    )
    if profile.get("profile") != "EvaCanonicalJSON/v1":
        raise ArchiveValidationError("canonical JSON profile contract differs")
    for contract, expected_hash in schemas.items():
        relative = f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        _load_schema(root, relative, expected_hash, registry, allow_trailing_lf=True)

    portable_raw = _read_regular(root, "deploy/schemas/portable/manifest.json")
    portable_manifest_hash = hashlib.sha256(portable_raw).hexdigest()
    if portable_manifest_hash != PORTABLE_MANIFEST_SHA256:
        raise ArchiveValidationError("portable manifest differs from pinned authority")
    portable_manifest = _load_canonical_object(
        portable_raw, label="deploy/schemas/portable/manifest.json"
    )
    portable_entries = portable_manifest.get("schemas")
    if not isinstance(portable_entries, list) or len(portable_entries) != 39:
        raise ArchiveValidationError("portable schema manifest is not the bound 39-set")
    for ordinal, entry in enumerate(portable_entries, start=1):
        if entry.get("exportOrdinal") != ordinal:
            raise ArchiveValidationError("portable schema ordinals differ")
        _load_schema(
            root,
            entry["recordSchemaRelativePath"],
            entry["record_schema_sha256"],
            registry,
        )
    reader_raw = _read_regular(root, "deploy/schemas/readback/manifest.json")
    if hashlib.sha256(reader_raw).hexdigest() != READER_MANIFEST_SHA256:
        raise ArchiveValidationError("reader manifest differs from pinned authority")
    reader_manifest = _load_canonical_object(
        reader_raw, label="deploy/schemas/readback/manifest.json"
    )
    if (
        reader_manifest.get("portableRecordSchemaManifestSha256")
        != portable_manifest_hash
    ):
        raise ArchiveValidationError("generic reader manifest dependencies differ")
    object_schema_sha256 = reader_manifest.get("objectManifestRecordSchemaSha256")
    if (
        not isinstance(object_schema_sha256, str)
        or SHA256_RE.fullmatch(object_schema_sha256) is None
    ):
        raise ArchiveValidationError("generic reader object schema hash is absent")
    if object_schema_sha256 != OBJECT_SCHEMA_SHA256:
        raise ArchiveValidationError("object schema differs from pinned authority")
    _load_schema(
        root,
        "deploy/schemas/portable/object-manifest-record-v1.schema.json",
        object_schema_sha256,
        registry,
    )
    for ordinal, entry in enumerate(reader_manifest.get("schemas", []), start=1):
        if entry.get("outputOrdinal") != ordinal:
            raise ArchiveValidationError("generic reader schema ordinals differ")
        _load_schema(
            root,
            entry["schemaRelativePath"],
            entry["record_schema_sha256"],
            registry,
        )
    if len(reader_manifest.get("schemas", [])) != 8:
        raise ArchiveValidationError(
            "generic reader schema manifest is not the bound 8-set"
        )
    return registry, portable_entries, reader_manifest


def _parse_jsonl(raw: bytes, *, label: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise ArchiveValidationError(f"{label} must use LF-terminated records")
    result = []
    for index, line in enumerate(raw[:-1].split(b"\n"), start=1):
        if not line:
            raise ArchiveValidationError(f"{label} contains a blank JSONL line")
        result.append(_load_canonical_object(line, label=f"{label}:{index}"))
    return result


def _pk_sort_key(primary_key: dict[str, Any], columns: list[str]) -> tuple[Any, ...]:
    result = []
    for column in columns:
        value = primary_key[column]
        if isinstance(value, str):
            result.append(value.encode("utf-8"))
        elif isinstance(value, int) and not isinstance(value, bool):
            result.append((value + (1 << 63)).to_bytes(16, "big"))
        else:
            raise ArchiveValidationError("portable primary key has an unsupported type")
    return tuple(result)


_CANONICAL_VALUE_PROPERTIES = {
    "normalized_value",
    "resource_scope",
    "scope",
    "step_definition",
    "value",
}


def _canonical_digest_property(property_name: str, row: dict[str, Any]) -> str | None:
    direct = f"{property_name}_sha256"
    if direct in row:
        return direct
    if property_name.endswith("_payload"):
        without_payload = property_name.removesuffix("_payload") + "_sha256"
        if without_payload in row:
            return without_payload
    if property_name.endswith("_episode_state"):
        without_episode = property_name.replace("_episode_state", "_state_sha256")
        if without_episode in row:
            return without_episode
    return None


def _validate_payload_hashes(table: str, row: dict[str, Any]) -> None:
    for property_name, value in row.items():
        if not (
            property_name == "payload"
            or property_name.endswith("_payload")
            or property_name.endswith("_episode_state")
            or property_name in _CANONICAL_VALUE_PROPERTIES
        ):
            continue
        if table == "eva_core.connector_bindings" and property_name == "payload":
            continue
        if property_name == "credential_grant_payload":
            expected_presence = value is not None
            if row.get("credential_reauthorization_required") is not expected_presence:
                raise ArchiveValidationError(
                    f"{table} credential reauthorization flag differs"
                )
            continue
        digest_name = _canonical_digest_property(property_name, row)
        if digest_name is None:
            continue
        if value is None:
            if row[digest_name] is not None:
                raise ArchiveValidationError(
                    f"{table}.{digest_name} is present for a null canonical value"
                )
            continue
        expected = hashlib.sha256(_canonical_bytes(value)).hexdigest()
        if row[digest_name] != expected:
            raise ArchiveValidationError(
                f"{table}.{digest_name} does not bind its canonical value"
            )
    if table == "eva_core.connector_bindings":
        if "payload_sha256" in row:
            raise ArchiveValidationError(
                "connector export contains ambiguous payload_sha256"
            )
        source_hash = row.get("source_payload_sha256")
        redacted_hash = row.get("redacted_payload_sha256")
        if not isinstance(source_hash, str) or SHA256_RE.fullmatch(source_hash) is None:
            raise ArchiveValidationError("connector source attestation is malformed")
        if (
            not isinstance(redacted_hash, str)
            or SHA256_RE.fullmatch(redacted_hash) is None
        ):
            raise ArchiveValidationError("connector redacted payload hash is malformed")
        actual = hashlib.sha256(_canonical_bytes(row.get("payload"))).hexdigest()
        if redacted_hash != actual:
            raise ArchiveValidationError("connector redacted payload hash differs")


def _pass_one_records(
    root: int,
    manifest: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    portable_entries: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]], dict[str, dict[tuple[Any, ...], dict[str, Any]]]
]:
    record_sets = manifest.get("record_sets")
    if not isinstance(record_sets, list) or len(record_sets) != 39:
        raise ArchiveValidationError("archive must contain exactly 39 record sets")
    records_by_table: dict[str, list[dict[str, Any]]] = {}
    indexes: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {}
    for ordinal, (record_set, entry, sealed_path) in enumerate(
        zip(record_sets, portable_entries, SEALED_RECORD_JSONL_PATHS, strict=True),
        start=1,
    ):
        exact = (
            record_set.get("contract_type") == entry["recordContract"]
            and record_set.get("relative_path") == sealed_path
            and record_set.get("contract_schema_sha256")
            == entry["record_schema_sha256"]
            and record_set.get("minimum_schema_version") == 1
            and record_set.get("maximum_schema_version") == 1
        )
        if not exact:
            raise ArchiveValidationError(f"record set {ordinal} authority differs")
        relative = record_set.get("relative_path")
        if not isinstance(relative, str):
            raise ArchiveValidationError("record set relative path is absent")
        raw = _read_regular(root, relative)
        if hashlib.sha256(raw).hexdigest() != record_set.get("jsonl_sha256"):
            raise ArchiveValidationError(f"record set hash differs: {relative}")
        records = _parse_jsonl(raw, label=relative)
        if len(records) != record_set.get("record_count"):
            raise ArchiveValidationError(f"record set count differs: {relative}")
        schema_path = entry["recordSchemaRelativePath"]
        schema_id = _load_canonical_object(
            _read_regular(root, schema_path), label=schema_path
        )["$id"]
        schema = registry[schema_id]
        primary_columns = schema["properties"]["primary_key"]["required"]
        table = entry["table"]
        table_index: dict[tuple[Any, ...], dict[str, Any]] = {}
        previous: tuple[Any, ...] | None = None
        seen_rows: set[bytes] = set()
        for line_number, record in enumerate(records, start=1):
            _validate_schema(
                record, schema, registry, schema, path=f"{relative}:{line_number}"
            )
            if record["source_table"] != table:
                raise ArchiveValidationError("portable source_table differs")
            row_bytes = _canonical_bytes(record["row"])
            if hashlib.sha256(row_bytes).hexdigest() != record["row_sha256"]:
                raise ArchiveValidationError("portable row_sha256 differs")
            if row_bytes in seen_rows:
                raise ArchiveValidationError("duplicate complete portable row")
            seen_rows.add(row_bytes)
            key = tuple(record["primary_key"][column] for column in primary_columns)
            sort_key = _pk_sort_key(record["primary_key"], primary_columns)
            if previous is not None and sort_key <= previous:
                raise ArchiveValidationError("portable primary keys are out of order")
            previous = sort_key
            if key in table_index:
                raise ArchiveValidationError("duplicate portable primary key")
            table_index[key] = record
            _validate_payload_hashes(table, record["row"])
        records_by_table[table] = records
        indexes[table] = table_index
    return records_by_table, indexes


_FOREIGN_KEY_DSL = """
eva_core.installations|tenant_id>eva_core.tenants|tenant_id
eva_core.installations|tenant_id,installation_id,current_identity_id,current_identity_pack_version>eva_core.identity_pack_versions|tenant_id,installation_id,identity_id,pack_version
eva_core.installations|tenant_id,installation_id,current_policy_id,current_policy_pack_version>eva_core.policy_pack_versions|tenant_id,installation_id,policy_id,policy_version
eva_core.identity_pack_versions|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.policy_pack_versions|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.principals|tenant_id>eva_core.tenants|tenant_id
eva_core.principal_bindings|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.principal_bindings|tenant_id,verification_evidence_id>eva_evidence.evidence_envelopes|tenant_id,evidence_id
eva_core.relationships|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.relationships|tenant_id,installation_id,identity_id,identity_pack_version>eva_core.identity_pack_versions|tenant_id,installation_id,identity_id,pack_version
eva_core.relationships|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.relationships|tenant_id,related_principal_id>eva_core.principals|tenant_id,principal_id
eva_core.relationships|tenant_id,installation_id,source_event_id>eva_memory.source_events|tenant_id,installation_id,source_event_id
eva_core.conversations|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.conversations|tenant_id,owner_principal_id>eva_core.principals|tenant_id,principal_id
eva_core.conversations|tenant_id,installation_id,current_episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.messages|tenant_id,installation_id,conversation_id>eva_core.conversations|tenant_id,installation_id,conversation_id
eva_core.messages|tenant_id,installation_id,episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.messages|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.messages|tenant_id,installation_id,identity_id,identity_pack_version>eva_core.identity_pack_versions|tenant_id,installation_id,identity_id,pack_version
eva_core.messages|tenant_id,installation_id,provenance_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_core.messages|tenant_id,installation_id,object_ref_id>eva_evidence.object_refs|tenant_id,installation_id,object_id
eva_core.ingress_requests|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.ingress_requests|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.ingress_requests|tenant_id,principal_binding_id>eva_core.principal_bindings|tenant_id,principal_binding_id
eva_core.ingress_requests|tenant_id,installation_id,conversation_id>eva_core.conversations|tenant_id,installation_id,conversation_id
eva_core.ingress_requests|tenant_id,installation_id,message_id>eva_core.messages|tenant_id,installation_id,message_id
eva_core.ingress_requests|tenant_id,installation_id,episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.ingress_requests|tenant_id,installation_id,pending_attention_id>eva_core.attention_items|tenant_id,installation_id,attention_id
eva_core.episodes|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.episodes|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.episodes|tenant_id,installation_id,conversation_id>eva_core.conversations|tenant_id,installation_id,conversation_id
eva_core.episodes|tenant_id,installation_id,ingress_request_id>eva_core.ingress_requests|tenant_id,installation_id,ingress_request_id
eva_core.episodes|tenant_id,installation_id,episode_id,current_generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.episodes|tenant_id,installation_id,episode_id,last_event_id>eva_core.episode_events|tenant_id,installation_id,episode_id,event_id
eva_core.episode_generations|tenant_id,installation_id,episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.episode_generations|tenant_id,installation_id,episode_id,predecessor_generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.episode_generations|tenant_id,installation_id,correction_message_id>eva_core.messages|tenant_id,installation_id,message_id
eva_core.episode_generations|tenant_id,installation_id,identity_id,identity_pack_version>eva_core.identity_pack_versions|tenant_id,installation_id,identity_id,pack_version
eva_core.episode_generations|tenant_id,installation_id,policy_id,policy_pack_version>eva_core.policy_pack_versions|tenant_id,installation_id,policy_id,policy_version
eva_core.episode_events|tenant_id,installation_id,episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.episode_outcomes|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.commitments|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.commitments|tenant_id,owner_principal_id>eva_core.principals|tenant_id,principal_id
eva_core.commitments|tenant_id,installation_id,transferred_to_episode_id>eva_core.episodes|tenant_id,installation_id,episode_id
eva_core.consent_grants|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_core.consent_grants|tenant_id,principal_binding_id>eva_core.principal_bindings|tenant_id,principal_binding_id
eva_core.consent_grants|tenant_id,installation_id,authentication_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_core.consent_grants|tenant_id,installation_id,source_message_id>eva_core.messages|tenant_id,installation_id,message_id
eva_core.consent_grants|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.model_attempts|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.plans|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.plans|tenant_id,installation_id,model_attempt_id>eva_core.model_attempts|tenant_id,installation_id,attempt_id
eva_core.plan_steps|tenant_id,installation_id,plan_id>eva_core.plans|tenant_id,installation_id,plan_id
eva_core.plan_steps|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.plan_steps|tenant_id,installation_id,read_intent_id>eva_effects.read_intents|tenant_id,installation_id,read_intent_id
eva_core.plan_steps|tenant_id,installation_id,effect_intent_id>eva_effects.effect_intents|tenant_id,installation_id,effect_intent_id
eva_core.attention_items|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_core.attention_items|tenant_id,installation_id,commitment_id>eva_core.commitments|tenant_id,installation_id,commitment_id
eva_core.connector_bindings|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.connector_bindings|tenant_id,owner_principal_id>eva_core.principals|tenant_id,principal_id
eva_core.connector_bindings|tenant_id,installation_id,verification_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_core.capability_manifests|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_core.capability_activations|tenant_id,installation_id,capability_manifest_id>eva_core.capability_manifests|tenant_id,installation_id,capability_manifest_id
eva_core.capability_activations|tenant_id,installation_id,connector_binding_id>eva_core.connector_bindings|tenant_id,installation_id,binding_id
eva_core.capability_activations|tenant_id,installation_id,policy_id,policy_pack_version>eva_core.policy_pack_versions|tenant_id,installation_id,policy_id,policy_version
eva_effects.read_intents|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_effects.read_intents|tenant_id,installation_id,plan_step_id>eva_core.plan_steps|tenant_id,installation_id,plan_step_id
eva_effects.read_intents|tenant_id,installation_id,effect_intent_id>eva_effects.effect_intents|tenant_id,installation_id,effect_intent_id
eva_effects.read_intents|tenant_id,installation_id,circuit_breaker_id>eva_effects.circuit_breakers|tenant_id,installation_id,breaker_id
eva_effects.read_intents|tenant_id,installation_id,capability_activation_id>eva_core.capability_activations|tenant_id,installation_id,activation_id
eva_effects.read_attempts|tenant_id,installation_id,read_intent_id>eva_effects.read_intents|tenant_id,installation_id,read_intent_id
eva_effects.read_observations|tenant_id,installation_id,read_intent_id,read_attempt_id>eva_effects.read_attempts|tenant_id,installation_id,read_intent_id,read_attempt_id
eva_effects.read_observations|tenant_id,installation_id,evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_effects.effect_intents|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_effects.effect_intents|tenant_id,installation_id,plan_step_id>eva_core.plan_steps|tenant_id,installation_id,plan_step_id
eva_effects.effect_intents|tenant_id,installation_id,connector_binding_id>eva_core.connector_bindings|tenant_id,installation_id,binding_id
eva_effects.effect_intents|tenant_id,installation_id,capability_activation_id>eva_core.capability_activations|tenant_id,installation_id,activation_id
eva_effects.effect_intents|tenant_id,installation_id,supersedes_effect_intent_id>eva_effects.effect_intents|tenant_id,installation_id,effect_intent_id
eva_effects.effect_intents|tenant_id,installation_id,consent_grant_id>eva_core.consent_grants|tenant_id,installation_id,consent_grant_id
eva_effects.effect_attempts|tenant_id,installation_id,effect_intent_id>eva_effects.effect_intents|tenant_id,installation_id,effect_intent_id
eva_effects.effect_attempts|tenant_id,installation_id,transport_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_effects.effect_attempts|tenant_id,installation_id,object_store_id>eva_core.installations|tenant_id,installation_id,object_store_id
eva_effects.effect_receipts|tenant_id,installation_id,effect_intent_id,effect_attempt_id>eva_effects.effect_attempts|tenant_id,installation_id,effect_intent_id,effect_attempt_id
eva_effects.effect_receipts|tenant_id,installation_id,evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_effects.effect_readbacks|tenant_id,installation_id,effect_intent_id,read_intent_id>eva_effects.read_intents|tenant_id,installation_id,effect_intent_id,read_intent_id
eva_effects.effect_readbacks|tenant_id,installation_id,read_intent_id,read_attempt_id>eva_effects.read_attempts|tenant_id,installation_id,read_intent_id,read_attempt_id
eva_effects.effect_readbacks|tenant_id,installation_id,evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_effects.deliveries|tenant_id,installation_id,effect_intent_id>eva_effects.effect_intents|tenant_id,installation_id,effect_intent_id
eva_effects.deliveries|tenant_id,installation_id,episode_id,generation>eva_core.episode_generations|tenant_id,installation_id,episode_id,generation
eva_effects.deliveries|tenant_id,installation_id,ingress_request_id>eva_core.ingress_requests|tenant_id,installation_id,ingress_request_id
eva_effects.deliveries|tenant_id,installation_id,effect_intent_id,receipt_effect_attempt_id>eva_effects.effect_attempts|tenant_id,installation_id,effect_intent_id,effect_attempt_id
eva_effects.circuit_breakers|tenant_id,installation_id,connector_binding_id>eva_core.connector_bindings|tenant_id,installation_id,binding_id
eva_effects.circuit_breakers|tenant_id,installation_id,attention_id>eva_core.attention_items|tenant_id,installation_id,attention_id
eva_memory.source_events|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_memory.source_events|tenant_id,principal_id>eva_core.principals|tenant_id,principal_id
eva_memory.source_events|tenant_id,installation_id,corrects_source_event_id>eva_memory.source_events|tenant_id,installation_id,source_event_id
eva_memory.source_events|tenant_id,installation_id,supersedes_source_event_id>eva_memory.source_events|tenant_id,installation_id,source_event_id
eva_memory.source_events|tenant_id,installation_id,retracts_source_event_id>eva_memory.source_events|tenant_id,installation_id,source_event_id
eva_memory.fact_assertions|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_memory.fact_assertions|tenant_id,installation_id,superseded_by_fact_id>eva_memory.fact_assertions|tenant_id,installation_id,fact_id
eva_memory.fact_assertions|tenant_id,installation_id,retraction_source_event_id>eva_memory.source_events|tenant_id,installation_id,source_event_id
eva_memory.current_facts|tenant_id,installation_id,fact_id>eva_memory.fact_assertions|tenant_id,installation_id,fact_id
eva_memory.erasure_tombstones|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_evidence.evidence_envelopes|tenant_id,installation_id>eva_core.installations|tenant_id,installation_id
eva_evidence.evidence_envelopes|tenant_id,installation_id,object_ref_id>eva_evidence.object_refs|tenant_id,installation_id,object_id
eva_evidence.evidence_links|tenant_id,installation_id,from_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_evidence.evidence_links|tenant_id,installation_id,to_evidence_id>eva_evidence.evidence_envelopes|tenant_id,installation_id,evidence_id
eva_evidence.object_refs|tenant_id,installation_id,object_store_id>eva_core.installations|tenant_id,installation_id,object_store_id
eva_evidence.object_refs|tenant_id,owner_principal_id>eva_core.principals|tenant_id,principal_id
eva_evidence.object_refs|tenant_id,installation_id,selected_effect_attempt_id>eva_effects.effect_attempts|tenant_id,installation_id,effect_attempt_id
eva_evidence.object_refs|tenant_id,installation_id,selected_candidate_id>eva_effects.effect_attempts|tenant_id,installation_id,object_candidate_id
""".strip()


def _foreign_keys() -> list[tuple[str, tuple[str, ...], str, tuple[str, ...]]]:
    result = []
    for line in _FOREIGN_KEY_DSL.splitlines():
        source, target = line.split(">", 1)
        source_table, source_columns = source.split("|", 1)
        target_table, target_columns = target.split("|", 1)
        result.append(
            (
                source_table,
                tuple(source_columns.split(",")),
                target_table,
                tuple(target_columns.split(",")),
            )
        )
    return result


def _validate_foreign_keys(records: dict[str, list[dict[str, Any]]]) -> None:
    target_sets: dict[tuple[str, tuple[str, ...]], set[tuple[Any, ...]]] = {}
    for _, _, target_table, target_columns in _foreign_keys():
        selector = (target_table, target_columns)
        if selector not in target_sets:
            target_sets[selector] = {
                tuple(record["row"][column] for column in target_columns)
                for record in records[target_table]
            }
    for source_table, source_columns, target_table, target_columns in _foreign_keys():
        target_values = target_sets[(target_table, target_columns)]
        for record in records[source_table]:
            value = tuple(record["row"][column] for column in source_columns)
            if any(item is None for item in value):
                continue
            if value not in target_values:
                raise ArchiveValidationError(
                    f"unresolved portable FK {source_table}{source_columns} -> "
                    f"{target_table}{target_columns}"
                )


def _rows_by(
    records: list[dict[str, Any]], *columns: str
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    result: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        result[tuple(record["row"][column] for column in columns)].append(record)
    return result


def _validate_reference_rules(records: dict[str, list[dict[str, Any]]]) -> None:
    principals = {row["row"]["principal_id"] for row in records["eva_core.principals"]}
    source_events = {
        row["row"]["source_event_id"] for row in records["eva_memory.source_events"]
    }
    evidence = {
        row["row"]["evidence_id"]: row
        for row in records["eva_evidence.evidence_envelopes"]
    }
    tombstones = {
        row["row"]["erasure_request_id"]
        for row in records["eva_memory.erasure_tombstones"]
    }
    for record in records["eva_core.relationships"]:
        row = record["row"]
        if row["source_event_id"] not in source_events:
            raise ArchiveValidationError("relationship source event is unresolved")
        if any(item not in evidence for item in row["channel_binding_evidence_refs"]):
            raise ArchiveValidationError(
                "relationship evidence reference is unresolved"
            )
    for record in records["eva_core.conversations"]:
        row = record["row"]
        participants = row["participant_principal_ids"]
        if not participants or row["owner_principal_id"] not in participants:
            raise ArchiveValidationError("conversation owner is not a participant")
        if any(item not in principals for item in participants):
            raise ArchiveValidationError("conversation participant is unresolved")

    steps_by_plan = _rows_by(records["eva_core.plan_steps"], "plan_id")
    for (plan_id,), steps in steps_by_plan.items():
        ids = {item["row"]["plan_step_id"] for item in steps}
        graph = {
            item["row"]["plan_step_id"]: item["row"]["dependency_step_ids"]
            for item in steps
        }
        if any(
            dependency not in ids for values in graph.values() for dependency in values
        ):
            raise ArchiveValidationError(f"plan {plan_id} dependency is unresolved")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ArchiveValidationError(
                    f"plan {plan_id} contains a dependency cycle"
                )
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)

    for record in records["eva_memory.source_events"]:
        row = record["row"]
        if any(item not in evidence for item in row["provenance_refs"]):
            raise ArchiveValidationError("memory provenance evidence is unresolved")
        if (
            row["erasure_request_id"] is not None
            and row["erasure_request_id"] not in tombstones
        ):
            raise ArchiveValidationError("memory erasure request is unresolved")
    for record in records["eva_memory.fact_assertions"]:
        row = record["row"]
        if not row["source_event_ids"] or any(
            item not in source_events for item in row["source_event_ids"]
        ):
            raise ArchiveValidationError("memory fact source event is unresolved")
    fact_by_id = {
        row["row"]["fact_id"]: row for row in records["eva_memory.fact_assertions"]
    }
    for record in records["eva_memory.current_facts"]:
        row = record["row"]
        fact = fact_by_id[row["fact_id"]]["row"]
        if fact["assertion_status"] != "active" or any(
            fact[field] != row[field]
            for field in ("subject_id", "predicate", "scope", "scope_sha256")
        ):
            raise ArchiveValidationError(
                "current fact pointer is not an equal active fact"
            )

    by_aggregate: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records["eva_evidence.evidence_envelopes"]:
        row = record["row"]
        if row["evidence_id"] in row["provenance_refs"]:
            raise ArchiveValidationError("evidence cannot cite itself")
        if any(item not in evidence for item in row["provenance_refs"]):
            raise ArchiveValidationError("evidence provenance is unresolved")
        by_aggregate[(row["aggregate_type"], row["aggregate_id"])].append(record)
    for aggregate_rows in by_aggregate.values():
        aggregate_rows.sort(key=lambda item: item["row"]["sequence"])
        prior_hashes: set[str] = set()
        for record in aggregate_rows:
            predecessor = record["row"]["predecessor_sha256"]
            if predecessor is not None and predecessor not in prior_hashes:
                raise ArchiveValidationError(
                    "evidence predecessor is not earlier in aggregate"
                )
            prior_hashes.add(record["row"]["payload_sha256"])

    acyclic_edges = [
        (row["row"]["from_evidence_id"], row["row"]["to_evidence_id"])
        for row in records["eva_evidence.evidence_links"]
        if row["row"]["acyclic"]
    ]
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in acyclic_edges:
        graph[source].append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_evidence(evidence_id: str) -> None:
        if evidence_id in visiting:
            raise ArchiveValidationError("acyclic evidence relation contains a cycle")
        if evidence_id in visited:
            return
        visiting.add(evidence_id)
        for target in graph[evidence_id]:
            visit_evidence(target)
        visiting.remove(evidence_id)
        visited.add(evidence_id)

    for evidence_id in graph:
        visit_evidence(evidence_id)

    attempts = {
        row["row"]["effect_attempt_id"]: row["row"]
        for row in records["eva_effects.effect_attempts"]
    }
    for record in records["eva_evidence.object_refs"]:
        row = record["row"]
        attempt = attempts[row["selected_effect_attempt_id"]]
        if (
            row["selected_candidate_id"] != attempt["object_candidate_id"]
            or attempt["object_candidate_state"] != "readback_verified"
            or row["object_store_id"] != attempt["object_store_id"]
            or row["ciphertext_sha256"] != attempt["object_candidate_ciphertext_sha256"]
            or row["envelope_byte_length"]
            != attempt["object_candidate_envelope_byte_length"]
        ):
            raise ArchiveValidationError("selected object candidate binding differs")


def _validate_reachable_tenant_closure(
    records: dict[str, list[dict[str, Any]]],
) -> None:
    """Prove tenant-scoped principals/bindings are all and only installation-reachable."""

    direct_bindings = {
        record["row"]["principal_binding_id"]
        for table in ("eva_core.ingress_requests", "eva_core.consent_grants")
        for record in records[table]
        if record["row"].get("principal_binding_id") is not None
    }
    binding_rows = records["eva_core.principal_bindings"]
    actual_bindings = {record["row"]["principal_binding_id"] for record in binding_rows}
    relationship_binding_evidence = {
        evidence_id
        for record in records["eva_core.relationships"]
        for evidence_id in record["row"]["channel_binding_evidence_refs"]
    }
    expected_bindings = direct_bindings | {
        record["row"]["principal_binding_id"]
        for record in binding_rows
        if record["row"]["verification_evidence_id"] in relationship_binding_evidence
    }
    if actual_bindings != expected_bindings:
        raise ArchiveValidationError("principal_binding closure is not exact")

    expected_principals = {record["row"]["principal_id"] for record in binding_rows}
    scalar_references = (
        ("eva_core.relationships", "principal_id"),
        ("eva_core.relationships", "related_principal_id"),
        ("eva_core.conversations", "owner_principal_id"),
        ("eva_core.messages", "principal_id"),
        ("eva_core.ingress_requests", "principal_id"),
        ("eva_core.episodes", "principal_id"),
        ("eva_core.commitments", "owner_principal_id"),
        ("eva_core.consent_grants", "principal_id"),
        ("eva_core.connector_bindings", "owner_principal_id"),
        ("eva_memory.source_events", "principal_id"),
        ("eva_evidence.object_refs", "owner_principal_id"),
    )
    for table, column in scalar_references:
        expected_principals.update(
            record["row"][column]
            for record in records[table]
            if record["row"].get(column) is not None
        )
    for record in records["eva_core.conversations"]:
        expected_principals.update(record["row"]["participant_principal_ids"])
    actual_principals = {
        record["row"]["principal_id"] for record in records["eva_core.principals"]
    }
    if actual_principals != expected_principals:
        raise ArchiveValidationError("principal closure is not exact")


def _validate_object_store_bindings(
    records: dict[str, list[dict[str, Any]]],
) -> None:
    installations = records["eva_core.installations"]
    if len(installations) != 1:
        raise ArchiveValidationError("ObjectStore requires one installation")
    installation = installations[0]["row"]
    object_refs = records["eva_evidence.object_refs"]
    triple = (
        installation["object_store_id"],
        installation["object_store_payload"],
        installation["object_store_payload_sha256"],
    )
    present = tuple(value is not None for value in triple)
    if any(present) and not all(present):
        raise ArchiveValidationError(
            "installation ObjectStore triple must be all-null or all-present"
        )
    if not any(present):
        if object_refs:
            raise ArchiveValidationError("ObjectRefs require a non-absent ObjectStore")
        return

    store = installation["object_store_payload"]
    if not isinstance(store, dict):
        raise ArchiveValidationError(
            "installation ObjectStore payload is not an object"
        )
    logical_store_id = store.get("object_store_id")
    if (
        store.get("contract") != "ObjectStore/v1"
        or not isinstance(logical_store_id, str)
        or str(uuid.uuid5(ARCHIVE_NAMESPACE, logical_store_id))
        != installation["object_store_id"]
        or store.get("installation_id") != installation["installation_id"]
        or store.get("state")
        not in {"design_only_not_runnable", "active", "read_only", "unavailable"}
        or store.get("encryption_algorithm") != "XChaCha20-Poly1305"
        or store.get("absolute_host_path_in_contract") is not False
    ):
        raise ArchiveValidationError("installation ObjectStore authority differs")
    if (
        hashlib.sha256(_canonical_bytes(store)).hexdigest()
        != installation["object_store_payload_sha256"]
    ):
        raise ArchiveValidationError("installation ObjectStore payload hash differs")
    maximum = store.get("maximum_object_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= 1_073_741_824
    ):
        raise ArchiveValidationError("ObjectStore maximum_object_bytes differs")

    scalar_payload_fields = {
        "object_id": "object_id",
        "ciphertext_sha256": "ciphertext_sha256",
        "plaintext_sha256": "plaintext_sha256",
        "byte_length": "plaintext_byte_length",
        "mime_type": "mime_type",
        "encryption_algorithm": "encryption_algorithm",
        "wrapped_key_ref": "wrapped_key_ref",
        "retention_until": "retention_until",
    }
    for record in object_refs:
        row = record["row"]
        payload = row["payload"]
        if payload.get("contract") != "ObjectRef/v1" or any(
            payload.get(payload_name) != row.get(row_name)
            for payload_name, row_name in scalar_payload_fields.items()
        ):
            raise ArchiveValidationError("ObjectRef payload/scalar binding differs")
        if (
            row["tenant_id"] != installation["tenant_id"]
            or row["installation_id"] != installation["installation_id"]
            or row["object_store_id"] != installation["object_store_id"]
            or row["encryption_algorithm"] != store["encryption_algorithm"]
            or row["plaintext_byte_length"] > maximum
        ):
            raise ArchiveValidationError("ObjectRef installation ObjectStore differs")
        digest = row["ciphertext_sha256"]
        if row["physical_cas_key"] != (f"sha256/{digest[:2]}/{digest}.eva2xo"):
            raise ArchiveValidationError("ObjectRef physical CAS key differs")
        if (row["state"] == "active") != (row["erased_at"] is None):
            raise ArchiveValidationError("ObjectRef active/shredded state differs")


def _validated_episode_head_state(
    episode: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    state = event["after_episode_state"]
    if (
        hashlib.sha256(_canonical_bytes(state)).hexdigest()
        != episode["state_payload_sha256"]
    ):
        raise ArchiveValidationError("episode current state payload hash differs")
    if (
        event["after_state"] != episode["state"]
        or event["event_id"] != episode["last_event_id"]
        or event["episode_id"] != episode["episode_id"]
        or event["after_generation"] != episode["current_generation"]
        or event["after_revision"] != episode["revision"]
        or state["state"] != episode["state"]
        or state["current_generation"] != episode["current_generation"]
        or state["supersedes_generation"] != episode["supersedes_generation"]
        or state["updated_at"] != episode["updated_at"]
        or state["tenant_id"] != episode["tenant_id"]
        or state["episode_id"] != episode["episode_id"]
        or state["last_event_id"] != episode["last_event_id"]
        or state["principal_id"] != episode["principal_id"]
        or state["conversation_id"] != episode["conversation_id"]
        or state["ingress_request_id"] != episode["ingress_request_id"]
    ):
        raise ArchiveValidationError(
            "episode head scalars differ from last event state"
        )
    return state


def _is_canonical_uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _validate_episode_state_snapshot(
    event: dict[str, Any], state: dict[str, Any], *, phase: str
) -> None:
    if phase not in {"before", "after"}:
        raise ArchiveValidationError("episode state snapshot phase is unsupported")
    if (
        hashlib.sha256(_canonical_bytes(state)).hexdigest()
        != event[f"{phase}_state_sha256"]
    ):
        raise ArchiveValidationError(f"episode event {phase}-state hash differs")
    generation = event[f"{phase}_generation"]
    expected_supersedes = None if generation == 1 else generation - 1
    snapshot_ids = (
        state["tenant_id"],
        state["episode_id"],
        state["principal_id"],
        state["conversation_id"],
        state["ingress_request_id"],
        state["last_event_id"],
    )
    if not all(_is_canonical_uuid(value) for value in snapshot_ids):
        raise ArchiveValidationError(
            f"episode {phase}-state snapshot UUID is not canonical"
        )
    if (
        state["state"] != event[f"{phase}_state"]
        or state["current_generation"] != generation
        or state["supersedes_generation"] != expected_supersedes
        or state["tenant_id"] != event["tenant_id"]
        or state["episode_id"] != event["episode_id"]
    ):
        raise ArchiveValidationError(f"episode {phase}-state snapshot binding differs")
    if phase == "after" and (
        state["last_event_id"] != event["event_id"]
        or state["updated_at"] != event["occurred_at"]
    ):
        raise ArchiveValidationError("episode after-state snapshot binding differs")


def _validate_scope_and_heads(
    manifest: dict[str, Any], records: dict[str, list[dict[str, Any]]]
) -> None:
    installations = records["eva_core.installations"]
    tenants = records["eva_core.tenants"]
    if len(installations) != 1 or len(tenants) != 1:
        raise ArchiveValidationError(
            "archive requires exactly one tenant and installation"
        )
    installation_id = manifest["source_installation_id"]
    installation = installations[0]["row"]
    tenant_id = installation["tenant_id"]
    if installation["installation_id"] != installation_id:
        raise ArchiveValidationError("archive installation anchor differs")
    for table, table_records in records.items():
        for record in table_records:
            row = record["row"]
            if row.get("tenant_id") != tenant_id:
                raise ArchiveValidationError(f"{table} escapes the source tenant")
            if "installation_id" in row and row["installation_id"] != installation_id:
                raise ArchiveValidationError(f"{table} escapes the source installation")

    events_by_id = {
        (row["row"]["episode_id"], row["row"]["event_id"]): row
        for row in records["eva_core.episode_events"]
    }
    for event_record in records["eva_core.episode_events"]:
        event = event_record["row"]
        after = event["after_episode_state"]
        _validate_episode_state_snapshot(event, after, phase="after")
        before = event["before_episode_state"]
        if before is None:
            if event["before_state_sha256"] is not None:
                raise ArchiveValidationError(
                    "initial episode event has a before-state hash"
                )
        else:
            _validate_episode_state_snapshot(event, before, phase="before")
    generations = {
        (row["row"]["episode_id"], row["row"]["generation"]): row
        for row in records["eva_core.episode_generations"]
    }
    for episode_record in records["eva_core.episodes"]:
        episode = episode_record["row"]
        event_record = events_by_id.get(
            (episode["episode_id"], episode["last_event_id"])
        )
        if event_record is None:
            raise ArchiveValidationError("episode last_event_id is unresolved")
        event = event_record["row"]
        generation = generations[
            (episode["episode_id"], episode["current_generation"])
        ]["row"]
        _validated_episode_head_state(episode, event)
        if generation["state"] == "superseded":
            raise ArchiveValidationError("episode points to a superseded generation")


def _pass_two_records(
    manifest: dict[str, Any], records: dict[str, list[dict[str, Any]]]
) -> None:
    _validate_foreign_keys(records)
    _validate_reference_rules(records)
    _validate_reachable_tenant_closure(records)
    _validate_object_store_bindings(records)
    _validate_scope_and_heads(manifest, records)


def _validate_object_envelope(raw: bytes, manifest_record: dict[str, Any]) -> None:
    if len(raw) != manifest_record["envelope_byte_length"]:
        raise ArchiveValidationError("encrypted object envelope length differs")
    if hashlib.sha256(raw).hexdigest() != manifest_record["ciphertext_sha256"]:
        raise ArchiveValidationError("encrypted object envelope SHA-256 differs")
    if len(raw) < 12 + 24 + 16 or raw[:8] != b"EVA2XO01":
        raise ArchiveValidationError("encrypted object magic/version differs")
    aad_length = int.from_bytes(raw[8:12], "big")
    if not 1 <= aad_length <= 1024 or 12 + aad_length + 24 + 16 > len(raw):
        raise ArchiveValidationError("encrypted object AAD length differs")
    aad_raw = raw[12 : 12 + aad_length]
    aad = _load_canonical_object(aad_raw, label="encrypted object AAD")
    expected_aad = {
        "contract": "Eva4DObjectAAD/v1",
        "installation_id": manifest_record["installation_id"],
        "key_ref": manifest_record["wrapped_key_ref"],
        "mime_type": manifest_record["mime_type"],
        "object_id": manifest_record["object_id"],
        "plaintext_byte_length": manifest_record["plaintext_byte_length"],
        "plaintext_sha256": manifest_record["plaintext_sha256"],
        "tenant_id": manifest_record["tenant_id"],
    }
    if aad != expected_aad:
        raise ArchiveValidationError("encrypted object AAD differs from ObjectRef")
    encrypted_message_length = len(raw) - 12 - aad_length
    if encrypted_message_length != 24 + manifest_record["plaintext_byte_length"] + 16:
        raise ArchiveValidationError(
            "encrypted object nonce/ciphertext/tag layout differs"
        )


def _validate_objects(
    root: int,
    manifest: dict[str, Any],
    records: dict[str, list[dict[str, Any]]],
    registry: dict[str, dict[str, Any]],
    reader_manifest: dict[str, Any],
) -> tuple[bytes, list[dict[str, Any]], set[str]]:
    relative = "objects/manifest-v1.jsonl"
    raw = _read_regular(root, relative)
    manifest_records = _parse_jsonl(raw, label=relative)
    object_schema = registry[
        "https://schemas.eva.local/portable/object-manifest-record-v1.schema.json"
    ]
    active_refs = [
        record
        for record in records["eva_evidence.object_refs"]
        if record["row"]["state"] == "active"
    ]
    active_refs.sort(
        key=lambda record: (
            record["row"]["ciphertext_sha256"].encode("utf-8"),
            uuid.UUID(record["row"]["object_id"]).bytes,
        )
    )
    if len(manifest_records) != len(active_refs):
        raise ArchiveValidationError("object manifest does not equal active ObjectRefs")
    object_paths: set[str] = set()
    for index, (item, reference) in enumerate(
        zip(manifest_records, active_refs, strict=True), start=1
    ):
        _validate_schema(
            item, object_schema, registry, object_schema, path=f"{relative}:{index}"
        )
        row = reference["row"]
        expected = {
            "schema_version": 1,
            "contract": "EvaEncryptedObjectManifestRecord/v1",
            "tenant_id": row["tenant_id"],
            "installation_id": row["installation_id"],
            "object_id": row["object_id"],
            "object_store_id": row["object_store_id"],
            "ciphertext_sha256": row["ciphertext_sha256"],
            "plaintext_sha256": row["plaintext_sha256"],
            "plaintext_byte_length": row["plaintext_byte_length"],
            "envelope_byte_length": row["envelope_byte_length"],
            "mime_type": row["mime_type"],
            "encryption_algorithm": row["encryption_algorithm"],
            "wrapped_key_ref": row["wrapped_key_ref"],
            "relative_path": "objects/sha256/"
            + row["ciphertext_sha256"][:2]
            + "/"
            + row["ciphertext_sha256"]
            + ".eva2xo",
            "retention_until": row["retention_until"],
            "object_ref_row_sha256": reference["row_sha256"],
        }
        if item != expected:
            raise ArchiveValidationError(
                "object manifest record differs from ObjectRef"
            )
        object_path = item["relative_path"]
        if object_path in object_paths:
            raise ArchiveValidationError("object manifest repeats a blob path")
        object_paths.add(object_path)
        _validate_object_envelope(_read_regular(root, object_path), item)

    content_hash = hashlib.sha256(raw).hexdigest()
    chunks = manifest["encrypted_object_manifest_chunks"]
    if manifest_records:
        expected_chunks = [
            {
                "relative_path": relative,
                "record_schema_sha256": reader_manifest[
                    "objectManifestRecordSchemaSha256"
                ],
                "jsonl_sha256": content_hash,
                "record_count": len(manifest_records),
            }
        ]
    else:
        expected_chunks = []
    if chunks != expected_chunks or manifest["encrypted_object_count"] != len(
        manifest_records
    ):
        raise ArchiveValidationError("archive object chunk/count binding differs")
    return raw, manifest_records, object_paths


def _referenced_pack_hashes(
    records: dict[str, list[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    installation = records["eva_core.installations"][0]["row"]
    identity_rows = {
        (row["row"]["identity_id"], row["row"]["pack_version"]): row["row"]
        for row in records["eva_core.identity_pack_versions"]
    }
    policy_rows = {
        (row["row"]["policy_id"], row["row"]["policy_version"]): row["row"]
        for row in records["eva_core.policy_pack_versions"]
    }
    for pack in (*identity_rows.values(), *policy_rows.values()):
        if pack["canonical_sha256"] != pack["payload_sha256"]:
            raise ArchiveValidationError(
                "pack canonical SHA-256 differs from its canonical payload"
            )
    identity_selectors: set[tuple[str, int]] = set()
    policy_selectors: set[tuple[str, int]] = set()

    def add_selector(
        target: set[tuple[str, int]], identifier: Any, version: Any
    ) -> None:
        if identifier is None and version is None:
            return
        if (
            not isinstance(identifier, str)
            or isinstance(version, bool)
            or not isinstance(version, int)
        ):
            raise ArchiveValidationError("pack selector is only partially populated")
        target.add((identifier, version))

    add_selector(
        identity_selectors,
        installation["current_identity_id"],
        installation["current_identity_pack_version"],
    )
    add_selector(
        policy_selectors,
        installation["current_policy_id"],
        installation["current_policy_pack_version"],
    )
    for record in records["eva_core.episode_generations"]:
        row = record["row"]
        add_selector(
            identity_selectors, row["identity_id"], row["identity_pack_version"]
        )
        add_selector(policy_selectors, row["policy_id"], row["policy_pack_version"])
        try:
            identity_hash = identity_rows[
                (row["identity_id"], row["identity_pack_version"])
            ]["canonical_sha256"]
            policy_hash = policy_rows[(row["policy_id"], row["policy_pack_version"])][
                "canonical_sha256"
            ]
        except KeyError as exc:
            raise ArchiveValidationError(
                "episode generation references an absent pack"
            ) from exc
        if (
            row["identity_pack_sha256"] != identity_hash
            or row["policy_pack_sha256"] != policy_hash
        ):
            raise ArchiveValidationError(
                "episode generation pack hash differs from referenced pack"
            )
    for record in records["eva_core.messages"]:
        row = record["row"]
        add_selector(
            identity_selectors, row["identity_id"], row["identity_pack_version"]
        )
    try:
        identity_hashes = sorted(
            {
                identity_rows[selector]["canonical_sha256"]
                for selector in identity_selectors
            }
        )
        policy_hashes = sorted(
            {policy_rows[selector]["canonical_sha256"] for selector in policy_selectors}
        )
    except KeyError as exc:
        raise ArchiveValidationError(
            "selected identity/policy pack is unresolved"
        ) from exc
    if not identity_hashes or not policy_hashes:
        raise ArchiveValidationError("archive has no selected identity or policy pack")
    return identity_hashes, policy_hashes


_PENDING_EPISODE_STATES = {
    "accepted",
    "planning",
    "executing",
    "reporting",
    "waiting_user",
    "waiting_external",
    "blocked",
}
_PENDING_EFFECT_STATES = {
    "prepared",
    "dispatch_authorized",
    "waiting_breaker",
    "ambiguous",
    "not_observed",
    "manual_reconciliation",
    "blocked",
}
_PENDING_DELIVERY_STATES = {
    "prepared",
    "dispatch_authorized",
    "provider_accepted",
    "ambiguous",
}


def _validate_manifest_derivations(
    manifest: dict[str, Any],
    records: dict[str, list[dict[str, Any]]],
    identity_hashes: list[str],
    object_records: list[dict[str, Any]],
    checksums_hash: str,
) -> None:
    if manifest["identity_pack_sha256s"] != identity_hashes:
        raise ArchiveValidationError("archive identity pack hash set differs")
    pending_episodes = sum(
        row["row"]["state"] in _PENDING_EPISODE_STATES
        for row in records["eva_core.episodes"]
    )
    pending_effects = sum(
        row["row"]["state"] in _PENDING_EFFECT_STATES
        for row in records["eva_effects.effect_intents"]
    )
    pending_deliveries = sum(
        row["row"]["state"] in _PENDING_DELIVERY_STATES
        for row in records["eva_effects.deliveries"]
    )
    if (
        manifest["pending_episode_count"],
        manifest["pending_effect_intent_count"],
        manifest["pending_delivery_count"],
    ) != (pending_episodes, pending_effects, pending_deliveries):
        raise ArchiveValidationError("archive pending counters differ")
    deadlines = [
        row["row"]["retention_until"]
        for table_records in records.values()
        for row in table_records
        if row["row"].get("retention_until") is not None
    ] + [row["retention_until"] for row in object_records]
    if not deadlines or manifest["retention_until"] != min(deadlines):
        raise ArchiveValidationError("archive retention minimum differs")
    if manifest["checksums_sha256"] != checksums_hash:
        raise ArchiveValidationError("archive checksums_sha256 differs")
    expected_id = str(
        uuid.uuid5(
            ARCHIVE_NAMESPACE,
            "archive:"
            + manifest["source_installation_id"]
            + ":"
            + str(manifest["export_sequence"])
            + ":"
            + checksums_hash,
        )
    )
    if manifest["archive_id"] != expected_id:
        raise ArchiveValidationError("archive_id UUIDv5 binding differs")


def _expected_payload_paths(
    manifest: dict[str, Any],
    contract_manifest: dict[str, Any],
    portable_entries: list[dict[str, Any]],
    reader_manifest: dict[str, Any],
    object_paths: set[str],
) -> set[str]:
    expected = {
        "deploy/schemas/contracts/manifest.json",
        "deploy/schemas/contracts/canonical-json-profile-v1.json",
        "deploy/schemas/portable/manifest.json",
        "deploy/schemas/portable/object-manifest-record-v1.schema.json",
        "deploy/schemas/readback/manifest.json",
        "objects/manifest-v1.jsonl",
    }
    expected.update(
        f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        for contract in contract_manifest["schemas"]
    )
    expected.update(entry["recordSchemaRelativePath"] for entry in portable_entries)
    expected.update(entry["schemaRelativePath"] for entry in reader_manifest["schemas"])
    expected.update(item["relative_path"] for item in manifest["record_sets"])
    expected.update(object_paths)
    return expected


def _one_record_by(records: list[dict[str, Any]], **criteria: Any) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if all(record["row"].get(key) == value for key, value in criteria.items())
    ]
    if len(matches) != 1:
        raise ArchiveValidationError(
            f"expected one record for {criteria}, found {len(matches)}"
        )
    return matches[0]


def _derive_identity(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    installation_record = records["eva_core.installations"][0]
    installation = installation_record["row"]
    current_identities = [
        record
        for record in records["eva_core.identity_pack_versions"]
        if record["row"]["state"] == "current"
    ]
    current_policies = [
        record
        for record in records["eva_core.policy_pack_versions"]
        if record["row"]["state"] == "current"
    ]
    if len(current_identities) != 1 or len(current_policies) != 1:
        raise ArchiveValidationError(
            "archive requires exactly one current identity and policy pack"
        )
    identity_record = _one_record_by(
        records["eva_core.identity_pack_versions"],
        identity_id=installation["current_identity_id"],
        pack_version=installation["current_identity_pack_version"],
    )
    policy_record = _one_record_by(
        records["eva_core.policy_pack_versions"],
        policy_id=installation["current_policy_id"],
        policy_version=installation["current_policy_pack_version"],
    )
    if (
        identity_record is not current_identities[0]
        or policy_record is not current_policies[0]
    ):
        raise ArchiveValidationError("installation pack pointers are not current")
    return {
        "schema_version": 1,
        "contract": "EvaGenericReaderIdentity/v1",
        "installation_record": installation_record,
        "identity_pack_record": identity_record,
        "policy_pack_record": policy_record,
    }


def _derive_conversations(
    records: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    principals = {
        row["row"]["principal_id"]: row for row in records["eva_core.principals"]
    }
    outputs = []
    for conversation in records["eva_core.conversations"]:
        row = conversation["row"]
        messages = [
            item
            for item in records["eva_core.messages"]
            if item["row"]["conversation_id"] == row["conversation_id"]
        ]
        messages.sort(
            key=lambda item: (item["row"]["created_at"], item["row"]["message_id"])
        )
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderConversation/v1",
                "conversation_record": conversation,
                "participant_principal_records": [
                    principals[principal_id]
                    for principal_id in row["participant_principal_ids"]
                ],
                "message_records": messages,
            }
        )
    return outputs


def _episode_children(
    records: dict[str, list[dict[str, Any]]], table: str, episode_id: str
) -> list[dict[str, Any]]:
    return [
        item for item in records[table] if item["row"].get("episode_id") == episode_id
    ]


def _derive_episodes(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    outputs = []
    for episode in records["eva_core.episodes"]:
        episode_row = episode["row"]
        episode_id = episode_row["episode_id"]
        generations = _episode_children(
            records, "eva_core.episode_generations", episode_id
        )
        generations.sort(key=lambda item: item["row"]["generation"])
        events = _episode_children(records, "eva_core.episode_events", episode_id)
        events.sort(
            key=lambda item: (item["row"]["after_revision"], item["row"]["event_id"])
        )
        if not events or not generations:
            raise ArchiveValidationError("episode is missing generation/event history")
        head_events = [
            event
            for event in events
            if (
                event["row"]["episode_id"],
                event["row"]["event_id"],
            )
            == (episode_id, episode_row["last_event_id"])
        ]
        if len(head_events) != 1:
            raise ArchiveValidationError("episode last_event_id is not exact")
        current_episode_state = _validated_episode_head_state(
            episode_row, head_events[0]["row"]
        )
        outcomes = _episode_children(records, "eva_core.episode_outcomes", episode_id)
        if len(outcomes) > 1:
            raise ArchiveValidationError("episode has multiple outcomes")
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderEpisode/v1",
                "episode_record": episode,
                "current_episode_state": current_episode_state,
                "generation_records": generations,
                "event_records": events,
                "outcome_record": outcomes[0] if outcomes else None,
                "commitment_records": _episode_children(
                    records, "eva_core.commitments", episode_id
                ),
                "consent_grant_records": _episode_children(
                    records, "eva_core.consent_grants", episode_id
                ),
                "model_attempt_records": _episode_children(
                    records, "eva_core.model_attempts", episode_id
                ),
                "plan_records": _episode_children(
                    records, "eva_core.plans", episode_id
                ),
                "plan_step_records": _episode_children(
                    records, "eva_core.plan_steps", episode_id
                ),
                "attention_item_records": _episode_children(
                    records, "eva_core.attention_items", episode_id
                ),
                "identity_pack_sha256s": sorted(
                    {item["row"]["identity_pack_sha256"] for item in generations}
                ),
                "policy_pack_sha256s": sorted(
                    {item["row"]["policy_pack_sha256"] for item in generations}
                ),
            }
        )
    return outputs


_PENDING_VARIANTS = (
    (
        "eva_core.episodes",
        _PENDING_EPISODE_STATES,
        "state",
        None,
        None,
        "episode_state",
    ),
    (
        "eva_core.commitments",
        {"open", "waiting", "blocked"},
        "state",
        "due_at",
        None,
        "commitment_state",
    ),
    (
        "eva_core.attention_items",
        {"pending", "claimed", "waiting"},
        "state",
        "deadline",
        "not_before",
        "attention_wake",
    ),
    (
        "eva_core.model_attempts",
        {"reserved", "dispatch_authorized", "ambiguous", "rate_limited"},
        "state",
        "credential_grant_deadline",
        None,
        "model_response_or_user_authorized_retry",
    ),
    (
        "eva_effects.read_attempts",
        {"prepared", "dispatch_authorized", "ambiguous"},
        "state",
        "deadline",
        None,
        "read_attempt_response",
    ),
    (
        "eva_effects.effect_intents",
        _PENDING_EFFECT_STATES,
        "state",
        "deadline",
        None,
        "effect_receipt_or_independent_readback",
    ),
    (
        "eva_effects.effect_attempts",
        {
            "reserved",
            "dispatch_authorized",
            "provider_accepted",
            "ambiguous_transport",
            "rate_limited",
            "internal_failure",
        },
        "state",
        "deadline",
        None,
        "effect_attempt_response_or_independent_readback",
    ),
    (
        "eva_effects.circuit_breakers",
        {"open", "half_open"},
        "state",
        None,
        "retry_at",
        "breaker_probe",
    ),
    (
        "eva_effects.deliveries",
        _PENDING_DELIVERY_STATES,
        "state",
        "ack_challenge_expires_at",
        None,
        "delivery_receipt_or_client_ack",
    ),
)


def _derive_pending(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    outputs = []
    for (
        table,
        states,
        state_field,
        deadline_field,
        wake_field,
        authority,
    ) in _PENDING_VARIANTS:
        for record in records[table]:
            row = record["row"]
            state = row[state_field]
            if state not in states:
                continue
            outputs.append(
                {
                    "schema_version": 1,
                    "contract": "EvaGenericReaderPendingWork/v1",
                    "source_table": table,
                    "state": state,
                    "deadline": row[deadline_field] if deadline_field else None,
                    "wake_at": row[wake_field] if wake_field else None,
                    "reconciliation_authority": authority,
                    "source_record": record,
                }
            )
    observed_intents = {
        item["row"]["read_intent_id"]
        for item in records["eva_effects.read_observations"]
    }
    for record in records["eva_effects.read_intents"]:
        row = record["row"]
        if row["read_intent_id"] in observed_intents:
            continue
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderPendingWork/v1",
                "source_table": "eva_effects.read_intents",
                "state": "pending",
                "deadline": row["deadline"],
                "wake_at": None,
                "reconciliation_authority": "read_budget_and_observation",
                "source_record": record,
            }
        )
    outputs.sort(
        key=lambda item: (
            item["source_table"].encode("utf-8"),
            _canonical_bytes(item["source_record"]["primary_key"]),
        )
    )
    return outputs


def _child_sort(record: dict[str, Any]) -> tuple[int, bytes]:
    return (
        record["row"].get("ordinal", 0),
        _canonical_bytes(record["primary_key"]),
    )


def _derive_effects(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    outputs = []
    evidence_ids = {
        record["row"]["evidence_id"]
        for record in records["eva_evidence.evidence_envelopes"]
    }
    for intent in records["eva_effects.read_intents"]:
        intent_id = intent["row"]["read_intent_id"]
        attempts = [
            row
            for row in records["eva_effects.read_attempts"]
            if row["row"]["read_intent_id"] == intent_id
        ]
        observations = [
            row
            for row in records["eva_effects.read_observations"]
            if row["row"]["read_intent_id"] == intent_id
        ]
        referenced = sorted({row["row"]["evidence_id"] for row in observations})
        if any(item not in evidence_ids for item in referenced):
            raise ArchiveValidationError("read output evidence is unresolved")
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderEffect/v1",
                "intent_kind": "read",
                "read_intent_record": intent,
                "read_attempt_records": sorted(attempts, key=_child_sort),
                "read_observation_records": sorted(observations, key=_child_sort),
                "evidence_ids": referenced,
            }
        )
    for intent in records["eva_effects.effect_intents"]:
        intent_id = intent["row"]["effect_intent_id"]
        attempts = [
            row
            for row in records["eva_effects.effect_attempts"]
            if row["row"]["effect_intent_id"] == intent_id
        ]
        receipts = [
            row
            for row in records["eva_effects.effect_receipts"]
            if row["row"]["effect_intent_id"] == intent_id
        ]
        readbacks = [
            row
            for row in records["eva_effects.effect_readbacks"]
            if row["row"]["effect_intent_id"] == intent_id
        ]
        deliveries = [
            row
            for row in records["eva_effects.deliveries"]
            if row["row"]["effect_intent_id"] == intent_id
        ]
        referenced = {row["row"]["evidence_id"] for row in (*receipts, *readbacks)}
        referenced.update(
            row["row"]["transport_evidence_id"]
            for row in attempts
            if row["row"]["transport_evidence_id"] is not None
        )
        if any(item not in evidence_ids for item in referenced):
            raise ArchiveValidationError("effect output evidence is unresolved")
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderEffect/v1",
                "intent_kind": "effect",
                "effect_intent_record": intent,
                "effect_attempt_records": sorted(attempts, key=_child_sort),
                "effect_receipt_records": sorted(receipts, key=_child_sort),
                "effect_readback_records": sorted(readbacks, key=_child_sort),
                "delivery_records": sorted(deliveries, key=_child_sort),
                "evidence_ids": sorted(referenced),
            }
        )
    outputs.sort(
        key=lambda item: (
            (item.get("read_intent_record") or item.get("effect_intent_record"))["row"][
                "episode_id"
            ],
            (item.get("read_intent_record") or item.get("effect_intent_record"))["row"][
                "generation"
            ],
            (item.get("read_intent_record") or item.get("effect_intent_record"))[
                "row"
            ].get(
                "logical_read_key",
                (item.get("read_intent_record") or item.get("effect_intent_record"))[
                    "row"
                ].get("logical_effect_key"),
            ),
        )
    )
    return outputs


def _derive_memories(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    outputs = [
        {
            "schema_version": 1,
            "contract": "EvaGenericReaderMemory/v1",
            "record_kind": "source_event",
            "is_current": False,
            "source_event_record": record,
        }
        for record in records["eva_memory.source_events"]
    ]
    current_by_fact = {
        record["row"]["fact_id"]: record
        for record in records["eva_memory.current_facts"]
    }
    outputs.extend(
        {
            "schema_version": 1,
            "contract": "EvaGenericReaderMemory/v1",
            "record_kind": "fact_assertion",
            "is_current": record["row"]["fact_id"] in current_by_fact,
            "fact_assertion_record": record,
            "current_fact_record": current_by_fact.get(record["row"]["fact_id"]),
        }
        for record in records["eva_memory.fact_assertions"]
    )
    outputs.extend(
        {
            "schema_version": 1,
            "contract": "EvaGenericReaderMemory/v1",
            "record_kind": "erasure_tombstone",
            "is_current": False,
            "erasure_tombstone_record": record,
        }
        for record in records["eva_memory.erasure_tombstones"]
    )

    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        record = (
            item.get("source_event_record")
            or item.get("fact_assertion_record")
            or item.get("erasure_tombstone_record")
        )
        row = record["row"]
        return (
            item["record_kind"],
            row.get("subject_id") or "",
            row.get("predicate") or "",
            row.get("valid_from") or "",
            _canonical_bytes(record["primary_key"]),
        )

    outputs.sort(key=key)
    return outputs


def _derive_evidence(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    links = records["eva_evidence.evidence_links"]
    object_refs = {
        record["row"]["object_id"]: record
        for record in records["eva_evidence.object_refs"]
    }
    outputs = []
    for evidence in records["eva_evidence.evidence_envelopes"]:
        row = evidence["row"]
        selected = []
        if row["object_ref_id"] is not None:
            selected = [object_refs[row["object_ref_id"]]]
        outputs.append(
            {
                "schema_version": 1,
                "contract": "EvaGenericReaderEvidence/v1",
                "evidence_record": evidence,
                "outbound_link_records": [
                    item
                    for item in links
                    if item["row"]["from_evidence_id"] == row["evidence_id"]
                ],
                "inbound_link_records": [
                    item
                    for item in links
                    if item["row"]["to_evidence_id"] == row["evidence_id"]
                ],
                "selected_object_ref_records": selected,
            }
        )
    outputs.sort(
        key=lambda item: (
            item["evidence_record"]["row"]["aggregate_type"],
            item["evidence_record"]["row"]["aggregate_id"],
            item["evidence_record"]["row"]["sequence"],
        )
    )
    return outputs


def _jsonl_bytes(values: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(value) + b"\n" for value in values)


def _schema_for_contract(
    registry: dict[str, dict[str, Any]], contract: str
) -> dict[str, Any]:
    candidates = []
    for schema in registry.values():
        definition = schema.get("properties", {}).get("contract", {})
        if definition.get("const") == contract:
            candidates.append(schema)
    if len(candidates) != 1:
        raise ArchiveValidationError(
            f"output schema lookup for {contract} is ambiguous"
        )
    return candidates[0]


def _derive_outputs(
    manifest: dict[str, Any],
    records: dict[str, list[dict[str, Any]]],
    object_manifest_raw: bytes,
    identity_hashes: list[str],
    policy_hashes: list[str],
    registry: dict[str, dict[str, Any]],
    reader_manifest: dict[str, Any],
) -> dict[str, bytes]:
    identity = _derive_identity(records)
    output_values = {
        "readback/identity.json": ("EvaGenericReaderIdentity/v1", [identity], False),
        "readback/conversations.jsonl": (
            "EvaGenericReaderConversation/v1",
            _derive_conversations(records),
            True,
        ),
        "readback/episodes.jsonl": (
            "EvaGenericReaderEpisode/v1",
            _derive_episodes(records),
            True,
        ),
        "readback/pending-work.jsonl": (
            "EvaGenericReaderPendingWork/v1",
            _derive_pending(records),
            True,
        ),
        "readback/effects.jsonl": (
            "EvaGenericReaderEffect/v1",
            _derive_effects(records),
            True,
        ),
        "readback/memories.jsonl": (
            "EvaGenericReaderMemory/v1",
            _derive_memories(records),
            True,
        ),
        "readback/evidence.jsonl": (
            "EvaGenericReaderEvidence/v1",
            _derive_evidence(records),
            True,
        ),
    }
    outputs: dict[str, bytes] = {}
    receipts = []
    reader_entries = {entry["contract"]: entry for entry in reader_manifest["schemas"]}
    for relative, (contract, values, is_jsonl) in output_values.items():
        schema = _schema_for_contract(registry, contract)
        for index, value in enumerate(values, start=1):
            _validate_schema(
                value, schema, registry, schema, path=f"{relative}:{index}"
            )
        content = _jsonl_bytes(values) if is_jsonl else _canonical_bytes(values[0])
        outputs[relative] = content
        entry = reader_entries[contract]
        receipts.append(
            {
                "relative_path": relative,
                "contract": contract,
                "record_schema_sha256": entry["record_schema_sha256"],
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "record_count": len(values),
            }
        )
    outputs["readback/object-manifest.jsonl"] = object_manifest_raw
    receipts.append(
        {
            "relative_path": "readback/object-manifest.jsonl",
            "contract": "EvaEncryptedObjectManifestRecord/v1",
            "record_schema_sha256": reader_manifest["objectManifestRecordSchemaSha256"],
            "content_sha256": hashlib.sha256(object_manifest_raw).hexdigest(),
            "record_count": object_manifest_raw.count(b"\n"),
        }
    )
    summary_record_sets = []
    for ordinal, record_set in enumerate(manifest["record_sets"], start=1):
        summary_record_sets.append(
            {
                "export_ordinal": ordinal,
                "table": records[
                    record_set["contract_type"]
                    .removeprefix("EvaCanonicalRecord/")
                    .removesuffix("/v1")
                ][0]["source_table"]
                if record_set["record_count"]
                else record_set["contract_type"]
                .removeprefix("EvaCanonicalRecord/")
                .removesuffix("/v1"),
                "relative_path": record_set["relative_path"],
                "record_count": record_set["record_count"],
                "jsonl_sha256": record_set["jsonl_sha256"],
                "record_schema_sha256": record_set["contract_schema_sha256"],
            }
        )
    summary = {
        "schema_version": 1,
        "contract": "EvaGenericReaderSummary/v1",
        "archive_id": manifest["archive_id"],
        "source_installation_id": manifest["source_installation_id"],
        "export_sequence": manifest["export_sequence"],
        "record_sets": summary_record_sets,
        "canonical_record_count": sum(
            item["record_count"] for item in manifest["record_sets"]
        ),
        "encrypted_object_count": manifest["encrypted_object_count"],
        "object_manifest_jsonl_sha256": hashlib.sha256(object_manifest_raw).hexdigest(),
        "object_manifest_record_schema_sha256": reader_manifest[
            "objectManifestRecordSchemaSha256"
        ],
        "identity_pack_sha256s": identity_hashes,
        "policy_pack_sha256s": policy_hashes,
        "pending_episode_count": manifest["pending_episode_count"],
        "pending_effect_intent_count": manifest["pending_effect_intent_count"],
        "pending_delivery_count": manifest["pending_delivery_count"],
        "checksums_sha256": manifest["checksums_sha256"],
        "validation_status": "valid",
        "output_receipts": receipts,
    }
    summary_schema = _schema_for_contract(registry, "EvaGenericReaderSummary/v1")
    _validate_schema(
        summary, summary_schema, registry, summary_schema, path="readback/summary.json"
    )
    outputs["readback/summary.json"] = _canonical_bytes(summary)
    return outputs


def _validate_and_derive_open(
    archive_root: int,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    actual_files = _scan_regular_files(archive_root)
    archive_raw = _read_regular(archive_root, "archive.json", maximum=MAX_JSON_BYTES)
    manifest = _load_canonical_object(archive_raw, label="archive.json")
    checksum_entries, checksums_hash = _parse_checksums(archive_root, actual_files)
    registry, portable_entries, reader_manifest = _load_schema_registry(
        archive_root, manifest
    )
    archive_schema = registry[
        "https://schemas.eva.local/contracts/EvaPortableArchive-v1.schema.json"
    ]
    _validate_schema(
        manifest, archive_schema, registry, archive_schema, path="archive.json"
    )
    expected_flags = {
        "canonical_profile": "EvaCanonicalJSON/v1",
        "pending_state_in_record_sets": True,
        "dbos_data_included": False,
        "credentials_included": False,
        "installation_master_key_included": False,
        "telemetry_included": False,
        "absolute_host_paths_included": False,
    }
    if any(manifest.get(key) != value for key, value in expected_flags.items()):
        raise ArchiveValidationError("archive exclusion/canonical flags differ")

    records, _ = _pass_one_records(archive_root, manifest, registry, portable_entries)
    _pass_two_records(manifest, records)
    object_raw, object_records, object_paths = _validate_objects(
        archive_root, manifest, records, registry, reader_manifest
    )
    identity_hashes, policy_hashes = _referenced_pack_hashes(records)
    _validate_manifest_derivations(
        manifest,
        records,
        identity_hashes,
        object_records,
        checksums_hash,
    )
    contract_manifest = _load_canonical_object(
        _read_regular(archive_root, "deploy/schemas/contracts/manifest.json"),
        label="deploy/schemas/contracts/manifest.json",
        allow_trailing_lf=True,
    )
    expected_payloads = _expected_payload_paths(
        manifest,
        contract_manifest,
        portable_entries,
        reader_manifest,
        object_paths,
    )
    if set(checksum_entries) != expected_payloads:
        missing = sorted(expected_payloads - set(checksum_entries))
        extra = sorted(set(checksum_entries) - expected_payloads)
        raise ArchiveValidationError(
            f"archive payload layout differs (missing={missing}, extra={extra})"
        )
    outputs = _derive_outputs(
        manifest,
        records,
        object_raw,
        identity_hashes,
        policy_hashes,
        registry,
        reader_manifest,
    )
    receipt = {
        "archive_json_sha256": hashlib.sha256(archive_raw).hexdigest(),
        "checksums_sha256": checksums_hash,
        "readback_summary_sha256": hashlib.sha256(
            outputs["readback/summary.json"]
        ).hexdigest(),
        "validation_status": "valid",
    }
    return outputs, receipt


def validate_and_derive(archive_root: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Validate every input before returning any in-memory readback bytes."""

    with _root_descriptor(archive_root.absolute()) as root_descriptor:
        return _validate_and_derive_open(root_descriptor)


def _write_outputs_atomically(outputs: dict[str, bytes], destination: Path) -> None:
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ArchiveValidationError("readback destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-", dir=str(destination.parent)
        )
    )
    try:
        for relative, content in outputs.items():
            _safe_relative_path(relative)
            target = staging.joinpath(*relative.split("/"))
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(content)
        with _root_descriptor(staging) as staging_descriptor:
            if _scan_regular_files(staging_descriptor) != set(outputs):
                raise ArchiveValidationError("staged readback path set differs")
        os.replace(staging, destination)
    except BaseException:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="unpacked Eva archive directory")
    parser.add_argument(
        "--output",
        type=Path,
        help="new directory to receive the atomically published readback tree",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="derive and validate in memory without publishing readback files",
    )
    options = parser.parse_args(argv)
    if options.validate_only == (options.output is not None):
        parser.error("choose exactly one of --validate-only or --output")
    return options


def main(argv: list[str] | None = None) -> int:
    options = _arguments(sys.argv[1:] if argv is None else argv)
    try:
        outputs, receipt = validate_and_derive(options.archive)
        if options.output is not None:
            _write_outputs_atomically(outputs, options.output)
    except (ArchiveValidationError, OSError) as exc:
        print(f"portable archive rejected: {exc}", file=sys.stderr)
        return 1
    print(_canonical_bytes(receipt).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
