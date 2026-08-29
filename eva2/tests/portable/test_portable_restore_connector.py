from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest

from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import build_portable_record, canonical_json_bytes
from eva.contracts.execution import ConnectorBinding
from tools.portable_restore import (
    PortableRestoreError,
    decode_reauthorization_required_connector_binding,
    restore_column_preserving_records,
)
from tests.portable.restore_authority_fixture import RESTORE_AUTHORITY_ARGUMENTS


def _spec() -> dict[str, Any]:
    return next(
        item
        for item in CANONICAL_EXPORT_SPECS
        if item["table"] == "eva_core.connector_bindings"
    )


def _connector_record() -> dict[str, Any]:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    installation_id = "22222222-2222-4222-8222-222222222222"
    binding_id = "33333333-3333-4333-8333-333333333333"
    owner_principal_id = "44444444-4444-4444-8444-444444444444"
    evidence_id = "55555555-5555-4555-8555-555555555555"
    original_secret_descriptor = "legacy-secret-descriptor:calendar"
    payload = ConnectorBinding(
        schema_version=1,
        contract="ConnectorBinding/v1",
        binding_id=binding_id,
        installation_id=installation_id,
        tenant_id=tenant_id,
        owner_principal_id=owner_principal_id,
        provider_id="calendar",
        provider_account_id="provider-account:calendar",
        provider_resource_id="primary",
        confirmed_operations=["calendar.event.create"],
        confirmed_scopes=["calendar.write"],
        credential_reference=original_secret_descriptor,
        verification_evidence_ref=evidence_id,
        verified_at="2026-08-20T12:00:00Z",
        status="verified",
    ).model_dump(mode="json")
    source_hash = hashlib.sha256(canonical_json_bytes(payload)).digest()
    moment = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    database_row = {
        "tenant_id": tenant_id,
        "installation_id": installation_id,
        "binding_id": binding_id,
        "owner_principal_id": owner_principal_id,
        "provider_id": "calendar",
        "provider_account_id": "provider-account:calendar",
        "provider_resource_id": "primary",
        "confirmed_operations": ["calendar.event.create"],
        "confirmed_scopes": ["calendar.write"],
        "credential_reference": original_secret_descriptor,
        "verification_evidence_id": evidence_id,
        "verified_at": moment,
        "status": "verified",
        "payload": payload,
        "payload_sha256": source_hash,
        "revision": 1,
        "retention_policy_id": "retention:test",
        "retention_until": datetime(2027, 8, 20, 12, 0, tzinfo=timezone.utc),
        "created_at": moment,
        "updated_at": moment,
    }
    spec = _spec()
    return build_portable_record(
        table=spec["table"],
        record_contract=spec["record_contract"],
        primary_key_columns=tuple(spec["primary_key"]),
        transform_spec=spec["transform_spec"],
        database_row=database_row,
    )


def _rehash(record: dict[str, Any]) -> None:
    record["row_sha256"] = hashlib.sha256(
        canonical_json_bytes(record["row"])
    ).hexdigest()


def test_redacted_connector_becomes_only_a_reauthorization_required_binding() -> None:
    record = _connector_record()

    restored = decode_reauthorization_required_connector_binding(
        record, table_specs=CANONICAL_EXPORT_SPECS
    )

    sentinel = "portable-restore-redacted:33333333-3333-4333-8333-333333333333"
    assert restored["credential_reference"] == sentinel
    assert restored["status"] == "reauthorization_required"
    assert restored["payload"]["credential_reference"] == sentinel
    assert restored["payload"]["status"] == "reauthorization_required"
    assert restored["payload_sha256"] == hashlib.sha256(
        canonical_json_bytes(restored["payload"])
    ).hexdigest()
    assert "legacy-secret-descriptor" not in canonical_json_bytes(restored).decode("utf-8")
    assert tuple(restored) == tuple(_spec()["database_column_order"])


def test_connector_restore_rejects_an_inconsistent_redacted_payload() -> None:
    record = copy.deepcopy(_connector_record())
    redacted_payload = record["row"]["payload"]
    assert isinstance(redacted_payload, dict)
    redacted_payload["provider_id"] = "different-provider"
    record["row"]["redacted_payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(redacted_payload)
    ).hexdigest()
    _rehash(record)

    with pytest.raises(PortableRestoreError, match="payload and row differ"):
        decode_reauthorization_required_connector_binding(
            record, table_specs=CANONICAL_EXPORT_SPECS
        )


def test_connector_restore_rejects_a_redaction_that_claims_to_be_authorized() -> None:
    record = copy.deepcopy(_connector_record())
    record["row"]["credential_reauthorization_required"] = False
    _rehash(record)

    with pytest.raises(PortableRestoreError, match="redaction is invalid"):
        decode_reauthorization_required_connector_binding(
            record, table_specs=CANONICAL_EXPORT_SPECS
        )


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


class _EmptyTarget:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...] | None]] = []

    def exec_driver_sql(
        self, sql: str, params: tuple[object, ...] | None = None
    ) -> _Result:
        self.queries.append((sql, params))
        if sql == "SHOW transaction_isolation":
            return _Result("serializable")
        if "pg_advisory_xact_lock" in sql or "to_regclass" in sql:
            return _Result(True)
        if sql.startswith("SELECT COUNT(*) FROM "):
            return _Result(0)
        if (
            sql.startswith("SAVEPOINT ")
            or sql.startswith("RELEASE SAVEPOINT")
            or sql.startswith("INSERT INTO ")
        ):
            return _Result(None)
        raise AssertionError(sql)


def test_restore_writer_inserts_only_the_safe_connector_relink_state() -> None:
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_core.connector_bindings"] = [_connector_record()]
    target = _EmptyTarget()

    assert (
        restore_column_preserving_records(
            target,
            records,
            **RESTORE_AUTHORITY_ARGUMENTS,
            table_specs=CANONICAL_EXPORT_SPECS,
        )
        == 1
    )

    insert = next(query for query in target.queries if query[0].startswith("INSERT INTO "))
    assert insert[0].startswith("INSERT INTO eva_core.connector_bindings")
    assert insert[1] is not None
    assert (
        "portable-restore-redacted:33333333-3333-4333-8333-333333333333"
        in insert[1]
    )
    assert "reauthorization_required" in insert[1]
    assert all(
        "legacy-secret-descriptor" not in str(value)
        for value in insert[1]
    )
