"""Fail-closed decoding boundary for future Eva portable database restore.

Portable archives are deliberately not SQL dumps: connector and credential
fields are redacted before export.  This module only reconstructs rows whose
sealed transform is entirely column-preserving.  Callers must send every other
row through the separate re-authorisation path; they may never guess a secret
or revive an old provider attempt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes


class PortableRestoreError(PortableArchiveError):
    """A portable record is malformed or cannot safely become a database row."""


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


__all__ = ["PortableRestoreError", "decode_column_preserving_record"]
