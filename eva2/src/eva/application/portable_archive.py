"""Framework-neutral construction of Eva's sealed portable archive bytes.

The PostgreSQL adapter supplies already-decoded column values.  This module is
the only place that turns those values into the canonical, redacted portable
record envelope.  It deliberately owns no database connection and imports no
DBOS code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5


MAX_SAFE_INTEGER = 9_007_199_254_740_991
ARCHIVE_UUID_NAMESPACE = UUID("276dbeb6-d012-5519-9ba9-4cb279c94dd4")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class PortableArchiveError(ValueError):
    """The source row or candidate archive violates the sealed 4D contract."""


@dataclass(frozen=True)
class RecordSetBytes:
    """One deterministic canonical table record set."""

    export_ordinal: int
    table: str
    contract: str
    relative_path: str
    record_schema_relative_path: str
    record_schema_sha256: str
    records: tuple[dict[str, Any], ...]
    content: bytes

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def jsonl_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        raise PortableArchiveError("canonical JSON exceeds maximum nesting depth")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise PortableArchiveError("integer is outside the canonical safe range")
        return
    if isinstance(value, float):
        raise PortableArchiveError("floating-point JSON values are forbidden")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise PortableArchiveError("JSON strings must already be NFC")
        if "\x00" in value or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise PortableArchiveError("JSON string contains a forbidden code point")
        if any(
            0xFDD0 <= ord(ch) <= 0xFDEF or (ord(ch) & 0xFFFF) in {0xFFFE, 0xFFFF}
            for ch in value
        ):
            raise PortableArchiveError("JSON string contains a Unicode noncharacter")
        return
    if isinstance(value, list):
        if len(value) > 1024:
            raise PortableArchiveError("canonical JSON collection is too large")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 1024:
            raise PortableArchiveError("canonical JSON object is too large")
        for key, item in value.items():
            if not isinstance(key, str):
                raise PortableArchiveError("JSON object keys must be strings")
            _validate_json_value(key, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
        return
    raise PortableArchiveError(
        f"unsupported canonical JSON type: {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode exactly EvaCanonicalJSON/v1 bytes (without a trailing LF)."""

    _validate_json_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > 1_048_576:
        raise PortableArchiveError("canonical JSON payload exceeds 1048576 bytes")
    return encoded


def _uuid_lowercase(value: Any) -> str:
    if value is None:
        return value
    try:
        canonical = str(value if isinstance(value, UUID) else UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PortableArchiveError("database UUID is invalid") from exc
    if isinstance(value, str) and value != canonical:
        raise PortableArchiveError("database UUID text is not lowercase canonical")
    return canonical


def _sha256_hex(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise PortableArchiveError("database SHA-256 text is not lowercase hex")
        return value
    try:
        raw = bytes(value)
    except (TypeError, ValueError) as exc:
        raise PortableArchiveError("database SHA-256 is not bytes") from exc
    if len(raw) != 32:
        raise PortableArchiveError("database SHA-256 is not 32 bytes")
    return raw.hex()


def _utc_whole_seconds(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise PortableArchiveError(
                "timestamp is not canonical UTC seconds"
            ) from exc
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise PortableArchiveError("timestamp is not canonical UTC seconds")
        return value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PortableArchiveError("database timestamp is not timezone-aware")
    utc = value.astimezone(timezone.utc)
    if utc.microsecond:
        raise PortableArchiveError("database timestamp is not whole-second precision")
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortableArchiveError("database integer did not decode as an integer")
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        raise PortableArchiveError("database integer exceeds the portable safe range")
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableArchiveError("database text did not decode as a string")
    _validate_json_value(value)
    return value


def _canonical_array(value: Any, *, uuid_values: bool) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise PortableArchiveError("database array did not decode as a sequence")
    encoded = [_uuid_lowercase(item) if uuid_values else _text(item) for item in value]
    if any(item is None for item in encoded):
        raise PortableArchiveError("portable set arrays cannot contain null")
    if len(set(encoded)) != len(encoded):
        raise PortableArchiveError("portable set arrays cannot contain duplicates")
    if encoded != sorted(encoded, key=lambda item: item.encode("utf-8")):
        raise PortableArchiveError("portable set arrays must already be UTF-8 sorted")
    return encoded


def _encode_column(value: Any, encoding: str) -> Any:
    if value is None:
        return None
    if encoding == "uuidLowercase":
        return _uuid_lowercase(value)
    if encoding == "sha256LowercaseHex":
        return _sha256_hex(value)
    if encoding == "utcWholeSeconds":
        return _utc_whole_seconds(value)
    if encoding in {"jsonInteger", "jsonSafeInteger"}:
        return _integer(value)
    if encoding == "utf8Text":
        return _text(value)
    if encoding == "canonicalJson":
        _validate_json_value(value)
        # Round-trip to detach mutable driver-owned mappings/lists.
        return json.loads(canonical_json_bytes(value))
    if encoding == "canonicalTextArray":
        return _canonical_array(value, uuid_values=False)
    if encoding == "canonicalUuidArray":
        return _canonical_array(value, uuid_values=True)
    if encoding == "jsonBoolean":
        if not isinstance(value, bool):
            raise PortableArchiveError("database boolean did not decode as bool")
        return value
    if encoding == "jsonNull":
        if value is not None:
            raise PortableArchiveError("jsonNull transform received a non-null value")
        return None
    raise PortableArchiveError(f"unknown sealed portable encoding: {encoding}")


def _validate_source_contract(payload: dict[str, Any], contract: str) -> None:
    # Product-side source validation may use the already-bound Pydantic contracts.
    # The standalone reader independently validates the embedded JSON schemas.
    if contract == "ConnectorBinding/v1":
        from eva.contracts.execution import ConnectorBinding

        model = ConnectorBinding.model_validate(payload)
    elif contract == "CredentialGrant/v1":
        from eva.contracts.execution import CredentialGrant

        model = CredentialGrant.model_validate(payload)
    else:  # This transform is sealed to exactly these two source contracts.
        raise PortableArchiveError(f"unsupported redaction source contract: {contract}")
    if model.model_dump(mode="json") != payload:
        raise PortableArchiveError(
            "source payload is not the exact closed contract shape"
        )


def _redact_contract_column(
    source: Mapping[str, Any], database_row: Mapping[str, Any]
) -> dict[str, Any] | None:
    value = database_row.get(str(source["column"]))
    if value is None:
        if source.get("nullInputProducesNull") is True:
            return None
        raise PortableArchiveError("required secret-bearing source payload is null")
    if not isinstance(value, dict):
        raise PortableArchiveError("secret-bearing source payload is not a JSON object")
    payload = json.loads(canonical_json_bytes(value))
    source_contract = str(source["sourceContract"])
    if payload.get("contract") != source_contract:
        raise PortableArchiveError("secret-bearing source payload contract differs")
    _validate_source_contract(payload, source_contract)
    expected = _sha256_hex(database_row.get(str(source["sourceHashColumn"])))
    actual = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    if expected != actual:
        raise PortableArchiveError(
            "source payload hash does not bind canonical payload"
        )

    secret_field = str(source["secretField"])
    secret = payload.get(secret_field)
    if not isinstance(secret, str):
        raise PortableArchiveError("source secret descriptor is not text")
    redacted = {
        key: item
        for key, item in payload.items()
        if key not in {"contract", secret_field}
    }
    redacted["contract"] = str(source["exportContract"])
    redacted["source_contract"] = source_contract
    redacted[secret_field] = source.get("secretReplacement")
    redacted[str(source["secretHashField"])] = hashlib.sha256(
        secret.encode("utf-8")
    ).hexdigest()
    redacted["credential_reauthorization_required"] = True
    return json.loads(canonical_json_bytes(redacted))


def build_portable_record(
    *,
    table: str,
    record_contract: str,
    primary_key_columns: Sequence[str],
    transform_spec: Mapping[str, Any],
    database_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Transform one exact database row into its sealed portable wrapper."""

    expected_columns = tuple(transform_spec["databaseColumnOrder"])
    if set(database_row) != set(expected_columns):
        missing = sorted(set(expected_columns) - set(database_row))
        extra = sorted(set(database_row) - set(expected_columns))
        raise PortableArchiveError(
            f"{table} database columns differ (missing={missing}, extra={extra})"
        )
    portable_row: dict[str, Any] = {}
    for mapping in transform_spec["propertyMappings"]:
        prop = str(mapping["portableProperty"])
        source = mapping["source"]
        kind = source["kind"]
        if kind == "column":
            value = database_row[str(source["column"])]
        elif kind == "literal":
            value = source.get("value")
        elif kind == "sha256Utf8Column":
            value = database_row[str(source["column"])]
            if not isinstance(value, str):
                raise PortableArchiveError("SHA-256 text transform input is not text")
            value = hashlib.sha256(value.encode("utf-8")).digest()
        elif kind == "credentialGrantPresence":
            value = (
                source["nullValue"]
                if database_row[str(source["column"])] is None
                else source["nonNullValue"]
            )
        elif kind == "redactedContractColumn":
            value = _redact_contract_column(source, database_row)
        elif kind == "sha256CanonicalPortableProperty":
            dependency = str(source["portableProperty"])
            if dependency not in portable_row:
                raise PortableArchiveError(
                    f"portable digest dependency {dependency!r} is not materialized"
                )
            value = hashlib.sha256(
                canonical_json_bytes(portable_row[dependency])
            ).digest()
        else:
            raise PortableArchiveError(f"unknown sealed transform kind: {kind}")
        portable_row[prop] = _encode_column(value, str(mapping["encoding"]))

    expected_properties = tuple(transform_spec["portablePropertyOrder"])
    if tuple(portable_row) != expected_properties:
        raise PortableArchiveError(f"{table} portable property order differs")
    primary_key = {name: portable_row[name] for name in primary_key_columns}
    row_hash = hashlib.sha256(canonical_json_bytes(portable_row)).hexdigest()
    return {
        "schema_version": 1,
        "contract": record_contract,
        "source_table": table,
        "primary_key": primary_key,
        "row": portable_row,
        "row_sha256": row_hash,
    }


def _primary_key_sort_key(
    record: Mapping[str, Any], primary_key_columns: Sequence[str]
) -> tuple[bytes, ...]:
    primary_key = record.get("primary_key")
    if not isinstance(primary_key, dict) or tuple(primary_key) != tuple(
        primary_key_columns
    ):
        raise PortableArchiveError("portable primary_key shape or order differs")
    result: list[bytes] = []
    for column in primary_key_columns:
        value = primary_key[column]
        if isinstance(value, str):
            result.append(value.encode("utf-8"))
        elif isinstance(value, int) and not isinstance(value, bool):
            # PostgreSQL integer ordering, encoded into a fixed-width biased key.
            result.append((value + (1 << 63)).to_bytes(16, "big", signed=False))
        else:
            raise PortableArchiveError("unsupported primary-key portable type")
    return tuple(result)


def encode_jsonl_records(
    records: Iterable[dict[str, Any]], *, primary_key_columns: Sequence[str]
) -> bytes:
    """Verify driver order and emit one canonical LF-terminated line per row."""

    output = bytearray()
    previous: tuple[bytes, ...] | None = None
    seen_rows: set[bytes] = set()
    for record in records:
        key = _primary_key_sort_key(record, primary_key_columns)
        if previous is not None and key <= previous:
            raise PortableArchiveError("records are not in strict primary-key order")
        previous = key
        row = record.get("row")
        if not isinstance(row, dict):
            raise PortableArchiveError("portable record row is not an object")
        row_bytes = canonical_json_bytes(row)
        expected_hash = hashlib.sha256(row_bytes).hexdigest()
        if record.get("row_sha256") != expected_hash:
            raise PortableArchiveError("portable row_sha256 mismatch")
        if row_bytes in seen_rows:
            raise PortableArchiveError("duplicate complete portable row bytes")
        seen_rows.add(row_bytes)
        output.extend(canonical_json_bytes(record))
        output.append(0x0A)
    return bytes(output)


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise PortableArchiveError("archive path is not a safe relative slash path")
    if any(ord(ch) < 0x20 or ch in "\u2028\u2029" for ch in relative_path):
        raise PortableArchiveError("archive path contains forbidden characters")
    parts = relative_path.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise PortableArchiveError("archive path contains an unsafe segment")


def build_checksums_bytes(payloads: Mapping[str, bytes]) -> bytes:
    """Bind payloads only; archive.json and checksums.sha256 are never inputs."""

    if {"archive.json", "checksums.sha256"} & set(payloads):
        raise PortableArchiveError("noncyclic envelope files cannot self-hash")
    lines = bytearray()
    for relative_path in sorted(payloads, key=lambda item: item.encode("utf-8")):
        _validate_relative_path(relative_path)
        digest = hashlib.sha256(payloads[relative_path]).hexdigest()
        lines.extend(f"{digest}  {relative_path}\n".encode("utf-8"))
    return bytes(lines)


def compute_archive_id(
    *, source_installation_id: str, export_sequence: int, checksums_sha256: str
) -> str:
    installation = _uuid_lowercase(source_installation_id)
    if (
        isinstance(export_sequence, bool)
        or not 1 <= export_sequence <= MAX_SAFE_INTEGER
    ):
        raise PortableArchiveError("export_sequence must be a positive safe integer")
    digest = _sha256_hex(checksums_sha256)
    return str(
        uuid5(
            ARCHIVE_UUID_NAMESPACE,
            f"archive:{installation}:{export_sequence}:{digest}",
        )
    )


def write_payload_tree(root: Path, payloads: Mapping[str, bytes]) -> None:
    """Write a new staging tree without following or creating links."""

    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    for relative_path, content in payloads.items():
        _validate_relative_path(relative_path)
        destination = root.joinpath(*relative_path.split("/"))
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise PortableArchiveError(f"duplicate archive path: {relative_path}")
        with destination.open("xb") as handle:
            handle.write(content)


def copy_regular_file_no_links(source: Path, destination: Path) -> bytes:
    """Read a material source only when it is one unlinked regular file."""

    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
        raise PortableArchiveError("object source is not a single-link regular file")
    content = source.read_bytes()
    after = source.lstat()
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != (
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_size,
        source_stat.st_mtime_ns,
    ):
        raise PortableArchiveError("object source changed while it was read")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(content)
    return content


def publish_directory_atomically(
    *, staging: Path, destination: Path, validator: Callable[[Path], None]
) -> None:
    """Validate a complete staging directory, then publish by one rename."""

    if destination.exists() or destination.is_symlink():
        raise PortableArchiveError("archive destination already exists")
    validator(staging)
    if destination.exists() or destination.is_symlink():
        raise PortableArchiveError("archive destination appeared during validation")
    if staging.parent != destination.parent:
        raise PortableArchiveError("staging and destination must share a parent")
    os.replace(staging, destination)


def discard_staging_tree(staging: Path) -> None:
    """Remove only the explicit unpublished sibling staging directory."""

    if staging.exists() and staging.is_dir() and not staging.is_symlink():
        shutil.rmtree(staging)
