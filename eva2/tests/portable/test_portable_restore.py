from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_restore import (
    PortableRestoreError,
    decode_column_preserving_record,
)


def _spec(table: str) -> Mapping[str, Any]:
    return next(item for item in CANONICAL_EXPORT_SPECS if item["table"] == table)


def _record(table: str, row: dict[str, Any]) -> dict[str, Any]:
    spec = _spec(table)
    return {
        "contract": spec["record_contract"],
        "primary_key": {
            column: row[column]
            for column in spec["primary_key"]
        },
        "row": row,
        "row_sha256": hashlib.sha256(canonical_json_bytes(row)).hexdigest(),
        "schema_version": 1,
        "source_table": table,
    }


def test_column_preserving_record_maps_back_to_exact_database_columns() -> None:
    table = "eva_evidence.evidence_links"
    row = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "installation_id": "22222222-2222-4222-8222-222222222222",
        "evidence_link_id": "33333333-3333-4333-8333-333333333333",
        "from_evidence_id": "44444444-4444-4444-8444-444444444444",
        "to_evidence_id": "55555555-5555-4555-8555-555555555555",
        "relation": "references",
        "acyclic": False,
        "retention_policy_id": "test",
        "retention_until": "2027-08-26T12:00:00Z",
        "created_at": "2026-08-26T12:00:00Z",
    }

    restored = decode_column_preserving_record(
        _record(table, row), table=table, table_specs=CANONICAL_EXPORT_SPECS
    )

    assert restored == row
    assert tuple(restored) == tuple(_spec(table)["database_column_order"])


def test_redacted_connector_record_cannot_be_generic_restored() -> None:
    table = "eva_core.connector_bindings"
    spec = _spec(table)
    row = {
        property_name: "placeholder"
        for property_name in spec["transform_spec"]["portablePropertyOrder"]
    }
    row.update(
        {
            "tenant_id": "11111111-1111-4111-8111-111111111111",
            "installation_id": "22222222-2222-4222-8222-222222222222",
            "binding_id": "33333333-3333-4333-8333-333333333333",
        }
    )

    with pytest.raises(PortableRestoreError, match="re-authorisation"):
        decode_column_preserving_record(
            _record(table, row), table=table, table_specs=CANONICAL_EXPORT_SPECS
        )


def test_restore_rejects_changed_row_or_primary_key() -> None:
    table = "eva_evidence.evidence_links"
    row = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "installation_id": "22222222-2222-4222-8222-222222222222",
        "evidence_link_id": "33333333-3333-4333-8333-333333333333",
        "from_evidence_id": "44444444-4444-4444-8444-444444444444",
        "to_evidence_id": "55555555-5555-4555-8555-555555555555",
        "relation": "references",
        "acyclic": False,
        "retention_policy_id": "test",
        "retention_until": "2027-08-26T12:00:00Z",
        "created_at": "2026-08-26T12:00:00Z",
    }
    record = _record(table, row)
    record["row"]["relation"] = "proves"
    with pytest.raises(PortableRestoreError, match="row hash"):
        decode_column_preserving_record(
            record, table=table, table_specs=CANONICAL_EXPORT_SPECS
        )

    record = _record(table, row)
    record["primary_key"]["evidence_link_id"] = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(PortableRestoreError, match="primary key differs"):
        decode_column_preserving_record(
            record, table=table, table_specs=CANONICAL_EXPORT_SPECS
        )
