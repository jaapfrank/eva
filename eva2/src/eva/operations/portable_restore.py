"""Fail-closed decoding boundary for future Eva portable database restore.

Portable archives are deliberately not SQL dumps: connector and credential
fields are redacted before export.  This module only reconstructs rows whose
sealed transform is entirely column-preserving.  Callers must send every other
row through the separate re-authorisation path; they may never guess a secret
or revive an old provider attempt.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PortableRestoreError(PortableArchiveError):
    """A portable record is malformed or cannot safely become a database row."""


def _one_value(result: Any, *, label: str) -> Any:
    row = result.fetchone()
    if row is None:
        raise PortableRestoreError(f"restore target {label} returned no value")
    if isinstance(row, Mapping):
        if len(row) != 1:
            raise PortableRestoreError(f"restore target {label} returned extra values")
        return next(iter(row.values()))
    if hasattr(row, "_mapping"):
        values = tuple(row._mapping.values())
    else:
        values = tuple(row)
    if len(values) != 1:
        raise PortableRestoreError(f"restore target {label} returned extra values")
    return values[0]


def _execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> Any:
    execute = getattr(connection, "exec_driver_sql", None)
    if callable(execute):
        return execute(sql) if params is None else execute(sql, params)
    return connection.execute(sql) if params is None else connection.execute(sql, params)


def _portable_row(record: Mapping[str, Any], *, table: str) -> dict[str, Any]:
    if set(record) != {
        "contract",
        "primary_key",
        "row",
        "row_sha256",
        "schema_version",
        "source_table",
    }:
        raise PortableRestoreError("portable record has an unexpected shape")
    if record.get("schema_version") != 1 or record.get("source_table") != table:
        raise PortableRestoreError("portable record table or schema version differs")
    row = record.get("row")
    if not isinstance(row, dict):
        raise PortableRestoreError("portable record row is not an object")
    row_bytes = canonical_json_bytes(row)
    if record.get("row_sha256") != hashlib.sha256(row_bytes).hexdigest():
        raise PortableRestoreError("portable record row hash differs")
    return row


def _spec_for_table(table_specs: tuple[Mapping[str, Any], ...], table: str) -> Mapping[str, Any]:
    matches = [item for item in table_specs if item.get("table") == table]
    if len(matches) != 1:
        raise PortableRestoreError("restore table is not uniquely sealed")
    return matches[0]


def decode_column_preserving_record(
    record: Mapping[str, Any],
    *,
    table: str,
    table_specs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Return exact database columns only when no exported field was redacted.

    The returned values are still portable JSON values.  A PostgreSQL adapter
    owns the final typed binding and target transaction; this boundary makes
    sure that adapter never receives an invented credential field.
    """

    spec = _spec_for_table(table_specs, table)
    expected_contract = spec.get("record_contract")
    if record.get("contract") != expected_contract:
        raise PortableRestoreError("portable record contract differs from table pin")
    row = _portable_row(record, table=table)
    transform = spec.get("transform_spec")
    if not isinstance(transform, Mapping):
        raise PortableRestoreError("restore transform is absent")
    mappings = transform.get("propertyMappings")
    if not isinstance(mappings, list):
        raise PortableRestoreError("restore transform mappings are absent")

    expected_properties = tuple(transform.get("portablePropertyOrder", ()))
    if tuple(row) != expected_properties:
        raise PortableRestoreError("portable row properties differ from sealed order")

    restored: dict[str, Any] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            raise PortableRestoreError("restore transform mapping is invalid")
        property_name = mapping.get("portableProperty")
        source = mapping.get("source")
        if not isinstance(property_name, str) or not isinstance(source, Mapping):
            raise PortableRestoreError("restore transform mapping is incomplete")
        if source.get("kind") != "column":
            raise PortableRestoreError(
                "portable row contains a redacted or derived field and requires "
                "the dedicated re-authorisation restore path"
            )
        column = source.get("column")
        if not isinstance(column, str) or column in restored:
            raise PortableRestoreError("restore transform source column is invalid")
        restored[column] = row[property_name]

    expected_columns = tuple(spec.get("database_column_order", ()))
    if tuple(restored) != expected_columns:
        raise PortableRestoreError("restored database columns differ from table pin")
    primary_key = record.get("primary_key")
    if not isinstance(primary_key, Mapping) or tuple(primary_key) != tuple(
        spec.get("primary_key", ())
    ):
        raise PortableRestoreError("portable primary key shape differs from table pin")
    if any(restored[column] != value for column, value in primary_key.items()):
        raise PortableRestoreError("portable primary key differs from restored row")
    return restored


def verify_empty_restore_target(
    connection: Any,
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> None:
    """Require the caller's current serializable transaction to have no Eva rows.

    This is an admission check, not an import.  The later importer must call it
    while traffic to the new target is closed and before it writes a first row.
    It intentionally checks every canonical table, not just installations, so
    a target with a stray tenant or a prior failed restore never looks empty.
    """

    isolation = _one_value(
        _execute(connection, "SHOW transaction_isolation"), label="isolation"
    )
    if isolation != "serializable":
        raise PortableRestoreError("restore target transaction is not serializable")
    _one_value(
        _execute(
            connection,
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended('EvaPortableRestore/v1', 0))",
        ),
        label="restore lock",
    )
    seen: set[str] = set()
    for spec in table_specs:
        table = spec.get("table")
        if (
            not isinstance(table, str)
            or table in seen
            or len(table.split(".")) != 2
            or any(not _IDENTIFIER.fullmatch(part) for part in table.split("."))
        ):
            raise PortableRestoreError("restore target table specification is invalid")
        seen.add(table)
        present = _one_value(
            _execute(connection, f"SELECT pg_catalog.to_regclass('{table}') IS NOT NULL"),
            label=f"table {table}",
        )
        if present is not True:
            raise PortableRestoreError(f"restore target lacks sealed table {table}")
        row_count = _one_value(
            _execute(connection, f"SELECT COUNT(*) FROM {table}"),
            label=f"row count {table}",
        )
        if isinstance(row_count, bool) or not isinstance(row_count, int):
            raise PortableRestoreError(f"restore target {table} count is invalid")
        if row_count:
            raise PortableRestoreError(f"restore target is not empty: {table}")


def _canonical_uuid(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableRestoreError("portable UUID is not text")
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise PortableRestoreError("portable UUID is invalid") from error
    if canonical != value:
        raise PortableRestoreError("portable UUID is not lowercase canonical")
    return canonical


def _canonical_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableRestoreError("portable timestamp is not text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PortableRestoreError("portable timestamp is not canonical UTC seconds") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PortableRestoreError("portable timestamp is not canonical UTC seconds")
    return value


def _canonical_sha256(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PortableRestoreError("portable SHA-256 is not lowercase hex")
    return bytes.fromhex(value)


def _integer(value: Any, *, safe: bool) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortableRestoreError("portable integer is invalid")
    if safe and not -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991:
        raise PortableRestoreError("portable integer is outside the safe range")
    return value


def _json_array(value: Any, *, uuid_values: bool) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PortableRestoreError("portable array is invalid")
    if uuid_values:
        result: list[str] = []
        for item in value:
            canonical = _canonical_uuid(item)
            if canonical is None:  # item is a str, but keep the failure closed.
                raise PortableRestoreError("portable UUID array contains null")
            result.append(canonical)
        return result
    return list(value)


def _bind_value(value: Any, encoding: str) -> tuple[str, Any]:
    """Return one safe PostgreSQL placeholder and its typed portable value."""

    if encoding == "uuidLowercase":
        return "%s", _canonical_uuid(value)
    if encoding == "utf8Text":
        if value is not None and not isinstance(value, str):
            raise PortableRestoreError("portable text is invalid")
        return "%s", value
    if encoding == "utcWholeSeconds":
        return "%s", _canonical_timestamp(value)
    if encoding == "sha256LowercaseHex":
        return "%s", _canonical_sha256(value)
    if encoding == "jsonInteger":
        return "%s", _integer(value, safe=False)
    if encoding == "jsonSafeInteger":
        return "%s", _integer(value, safe=True)
    if encoding == "jsonBoolean":
        if not isinstance(value, bool):
            raise PortableRestoreError("portable boolean is invalid")
        return "%s", value
    if encoding == "canonicalJson":
        return "CAST(%s AS jsonb)", canonical_json_bytes(value).decode("utf-8")
    if encoding == "canonicalUuidArray":
        return "CAST(%s AS uuid[])", _json_array(value, uuid_values=True)
    if encoding == "canonicalTextArray":
        return "CAST(%s AS text[])", _json_array(value, uuid_values=False)
    raise PortableRestoreError("portable column encoding is not sealed for restore")


def _validate_record_sets(
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> tuple[
    tuple[
        Mapping[str, Any],
        tuple[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]], ...],
    ],
    ...,
]:
    expected_tables = tuple(spec.get("table") for spec in table_specs)
    if (
        len(set(expected_tables)) != len(expected_tables)
        or any(not isinstance(table, str) for table in expected_tables)
        or set(records_by_table) != set(expected_tables)
    ):
        raise PortableRestoreError("portable record sets differ from sealed table list")

    prepared: list[
        tuple[
            Mapping[str, Any],
            tuple[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]], ...],
        ]
    ] = []
    for spec in table_specs:
        table = str(spec["table"])
        records = records_by_table[table]
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise PortableRestoreError("portable table record set is not a sequence")
        transform = spec.get("transform_spec")
        mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
        if not isinstance(mappings, list):
            raise PortableRestoreError("restore transform mappings are absent")
        rows: list[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise PortableRestoreError("portable table record is not an object")
            restored = decode_column_preserving_record(
                record, table=table, table_specs=table_specs
            )
            bindings: list[Any] = []
            placeholders: list[str] = []
            for mapping in mappings:
                property_name = mapping.get("portableProperty")
                source = mapping.get("source")
                encoding = mapping.get("encoding")
                if (
                    not isinstance(property_name, str)
                    or not isinstance(source, Mapping)
                    or source.get("kind") != "column"
                    or not isinstance(encoding, str)
                ):
                    raise PortableRestoreError("restore mapping is not column-preserving")
                placeholder, value = _bind_value(restored[str(source["column"])], encoding)
                placeholders.append(placeholder)
                bindings.append(value)
            rows.append((restored, tuple(placeholders), tuple(bindings)))
        prepared.append((spec, tuple(rows)))
    return tuple(prepared)


def restore_column_preserving_records(
    connection: Any,
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> int:
    """Write one verified, all-column archive only into an empty target.

    The entire archive is decoded and typed *before* inspecting or writing the
    target. A row using redaction, a missing set, a changed record or an unknown
    encoding therefore leaves the target untouched. The savepoint also removes
    every inserted row when a later database constraint rejects the archive.

    Normal Eva backups currently contain credential/connector redactions, so
    they correctly fail here until the dedicated re-authorisation importer is
    present. This narrow writer must never turn redacted history into a live
    connector, credential, provider call or retry.
    """

    prepared = _validate_record_sets(records_by_table, table_specs=table_specs)
    verify_empty_restore_target(connection, table_specs=table_specs)
    _execute(connection, "SAVEPOINT eva_portable_restore_write")
    inserted = 0
    try:
        for spec, rows in prepared:
            table = str(spec["table"])
            columns = tuple(spec.get("database_column_order", ()))
            if not columns or any(not isinstance(column, str) or not _IDENTIFIER.fullmatch(column) for column in columns):
                raise PortableRestoreError("restore database columns are invalid")
            rendered_columns = ", ".join(columns)
            for _restored, placeholders, bindings in rows:
                if len(placeholders) != len(columns) or len(bindings) != len(columns):
                    raise PortableRestoreError("restore binding count differs from columns")
                _execute(
                    connection,
                    f"INSERT INTO {table} ({rendered_columns}) VALUES ({', '.join(placeholders)})",
                    bindings,
                )
                inserted += 1
    except BaseException:
        _execute(connection, "ROLLBACK TO SAVEPOINT eva_portable_restore_write")
        _execute(connection, "RELEASE SAVEPOINT eva_portable_restore_write")
        raise
    _execute(connection, "RELEASE SAVEPOINT eva_portable_restore_write")
    return inserted


__all__ = [
    "PortableRestoreError",
    "decode_column_preserving_record",
    "restore_column_preserving_records",
    "verify_empty_restore_target",
]
