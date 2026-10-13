from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import pytest

from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import canonical_json_bytes
from tools.portable_restore import (
    PortableRestoreError,
    decode_column_preserving_record,
    restore_column_preserving_records,
    verify_empty_restore_target,
)
from eva.operations.portable_restore_authority import PortableRestoreAuthorityError
from tests.portable.restore_authority_fixture import (
    ARCHIVE_MANIFEST_RAW,
    RESTORE_AUTHORITY_ARGUMENTS,
    restore_authority_for_archive,
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


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


class _RestoreTarget:
    def __init__(self, *, isolation: str = "serializable", nonempty: str | None = None) -> None:
        self.isolation = isolation
        self.nonempty = nonempty
        self.queries: list[str] = []

    def exec_driver_sql(self, sql: str) -> _Result:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return _Result(self.isolation)
        if "pg_advisory_xact_lock" in sql:
            return _Result(None)
        if "to_regclass" in sql:
            return _Result(True)
        if sql.startswith("SELECT COUNT(*) FROM "):
            table = sql.removeprefix("SELECT COUNT(*) FROM ")
            return _Result(1 if table == self.nonempty else 0)
        raise AssertionError(sql)


class _WritingRestoreTarget:
    def __init__(self, *, fail_insert: bool = False) -> None:
        self.fail_insert = fail_insert
        self.queries: list[tuple[str, tuple[object, ...] | None]] = []

    def exec_driver_sql(
        self, sql: str, params: tuple[object, ...] | None = None
    ) -> _Result:
        self.queries.append((sql, params))
        if sql == "SHOW transaction_isolation":
            return _Result("serializable")
        if "pg_advisory_xact_lock" in sql:
            return _Result(None)
        if "to_regclass" in sql:
            return _Result(True)
        if sql.startswith("SELECT COUNT(*) FROM "):
            return _Result(0)
        if sql.startswith("SAVEPOINT ") or sql.startswith("RELEASE SAVEPOINT"):
            return _Result(None)
        if sql.startswith("ROLLBACK TO SAVEPOINT"):
            return _Result(None)
        if sql.startswith("INSERT INTO "):
            if self.fail_insert:
                raise RuntimeError("synthetic target failure")
            return _Result(None)
        raise AssertionError(sql)


def _empty_record_sets() -> dict[str, list[dict[str, object]]]:
    return {str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS}


def _tenant_row() -> dict[str, object]:
    return {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "status": "active",
        "authority_policy_version": 1,
        "revision": 1,
        "retention_policy_id": "test",
        "retention_until": "2027-08-26T12:00:00Z",
        "created_at": "2026-08-26T12:00:00Z",
        "updated_at": "2026-08-26T12:00:00Z",
    }


def test_restore_target_must_be_serializable_locked_and_empty() -> None:
    target = _RestoreTarget()

    verify_empty_restore_target(target, table_specs=CANONICAL_EXPORT_SPECS)

    assert target.queries[0] == "SHOW transaction_isolation"
    assert "pg_advisory_xact_lock" in target.queries[1]
    assert len([sql for sql in target.queries if sql.startswith("SELECT COUNT")]) == len(
        CANONICAL_EXPORT_SPECS
    )


def test_restore_target_rejects_weaker_isolation_or_existing_data() -> None:
    with pytest.raises(PortableRestoreError, match="not serializable"):
        verify_empty_restore_target(
            _RestoreTarget(isolation="read committed"),
            table_specs=CANONICAL_EXPORT_SPECS,
        )
    with pytest.raises(PortableRestoreError, match="not empty: eva_core.tenants"):
        verify_empty_restore_target(
            _RestoreTarget(nonempty="eva_core.tenants"),
            table_specs=CANONICAL_EXPORT_SPECS,
        )
    with pytest.raises(PortableRestoreError, match="table specification"):
        verify_empty_restore_target(
            _RestoreTarget(),
            table_specs=({"table": "eva_core.tenants; SELECT 1"},),
        )


def test_column_preserving_restore_prevalidates_then_writes_to_empty_target() -> None:
    records = _empty_record_sets()
    records["eva_core.tenants"] = [_record("eva_core.tenants", _tenant_row())]
    target = _WritingRestoreTarget()

    assert (
        restore_column_preserving_records(
            target,
            records,
            **RESTORE_AUTHORITY_ARGUMENTS,
            table_specs=CANONICAL_EXPORT_SPECS,
        )
        == 1
    )

    inserts = [query for query in target.queries if query[0].startswith("INSERT INTO ")]
    assert len(inserts) == 1
    assert inserts[0][0].startswith("INSERT INTO eva_core.tenants")
    assert inserts[0][1] == (
        "11111111-1111-4111-8111-111111111111",
        "active",
        1,
        1,
        "test",
        "2027-08-26T12:00:00Z",
        "2026-08-26T12:00:00Z",
        "2026-08-26T12:00:00Z",
    )
    assert any(sql == "SAVEPOINT eva_portable_restore_write" for sql, _ in target.queries)
    assert any(
        sql == "RELEASE SAVEPOINT eva_portable_restore_write" for sql, _ in target.queries
    )


def test_latest_erasure_generation_blocks_old_archive_before_target_access() -> None:
    target = _WritingRestoreTarget()

    with pytest.raises(PortableRestoreAuthorityError, match="not authorized"):
        restore_column_preserving_records(
            target,
            _empty_record_sets(),
            archive_manifest_raw=ARCHIVE_MANIFEST_RAW,
            restore_authority_raw=restore_authority_for_archive(
                ARCHIVE_MANIFEST_RAW, admit=False, generation=2
            ),
            table_specs=CANONICAL_EXPORT_SPECS,
        )

    assert target.queries == []


def test_restore_rejects_an_invalid_connector_redaction_before_target_is_touched() -> None:
    records = _empty_record_sets()
    connector_spec = _spec("eva_core.connector_bindings")
    row = {
        property_name: "placeholder"
        for property_name in connector_spec["transform_spec"]["portablePropertyOrder"]
    }
    row.update(
        {
            "tenant_id": "11111111-1111-4111-8111-111111111111",
            "installation_id": "22222222-2222-4222-8222-222222222222",
            "binding_id": "33333333-3333-4333-8333-333333333333",
        }
    )
    records["eva_core.connector_bindings"] = [
        _record("eva_core.connector_bindings", row)
    ]
    target = _WritingRestoreTarget()

    with pytest.raises(PortableRestoreError, match="connector redaction is invalid"):
        restore_column_preserving_records(
            target,
            records,
            **RESTORE_AUTHORITY_ARGUMENTS,
            table_specs=CANONICAL_EXPORT_SPECS,
        )

    assert target.queries == []


def test_column_preserving_restore_rolls_back_its_savepoint_on_target_failure() -> None:
    records = _empty_record_sets()
    records["eva_core.tenants"] = [_record("eva_core.tenants", _tenant_row())]
    target = _WritingRestoreTarget(fail_insert=True)

    with pytest.raises(RuntimeError, match="synthetic target failure"):
        restore_column_preserving_records(
            target,
            records,
            **RESTORE_AUTHORITY_ARGUMENTS,
            table_specs=CANONICAL_EXPORT_SPECS,
        )

    queries = [sql for sql, _ in target.queries]
    assert "ROLLBACK TO SAVEPOINT eva_portable_restore_write" in queries
    assert queries[-1] == "RELEASE SAVEPOINT eva_portable_restore_write"
