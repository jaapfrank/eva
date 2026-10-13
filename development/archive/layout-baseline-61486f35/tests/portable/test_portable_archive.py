from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eva.application.portable_archive import (
    PortableArchiveError,
    build_checksums_bytes,
    build_portable_record,
    canonical_json_bytes,
    compute_archive_id,
    encode_jsonl_records,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
FREEZE = EVA2_ROOT / "design" / "vertical" / "noor-4d-schema-export-freeze-v1.json"


def _schema_entry(table: str) -> dict[str, object]:
    authority = json.loads(FREEZE.read_bytes())
    return next(
        item
        for item in authority["portableExport"]["generatedPortableRecordSchemas"][
            "schemas"
        ]
        if item["table"] == table
    )


def test_connector_binding_export_separates_source_and_redacted_hashes() -> None:
    entry = _schema_entry("eva_core.connector_bindings")
    tenant_id = "66a4aad5-0550-5211-8b36-398eabe5b7be"
    installation_id = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
    binding_id = "72d71ff7-4b02-5fc5-b89b-64d1da9d57d8"
    owner_id = "b7e71151-6d3f-5adc-93ed-48733b86fc63"
    evidence_id = "deef5361-258c-5313-8a81-64ddf2d964fb"
    secret_ref = "credential:calendar:fixture"
    payload = {
        "schema_version": 1,
        "contract": "ConnectorBinding/v1",
        "binding_id": binding_id,
        "installation_id": installation_id,
        "tenant_id": tenant_id,
        "owner_principal_id": owner_id,
        "provider_id": "calendar",
        "provider_account_id": "provider-account:calendar",
        "provider_resource_id": "primary",
        "confirmed_operations": ["calendar.event.create"],
        "confirmed_scopes": ["calendar.write"],
        "credential_reference": secret_ref,
        "verification_evidence_ref": evidence_id,
        "verified_at": "2026-08-20T12:00:00Z",
        "status": "verified",
    }
    source_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    database_row = {
        "tenant_id": tenant_id,
        "installation_id": installation_id,
        "binding_id": binding_id,
        "owner_principal_id": owner_id,
        "provider_id": "calendar",
        "provider_account_id": "provider-account:calendar",
        "provider_resource_id": "primary",
        "confirmed_operations": ["calendar.event.create"],
        "confirmed_scopes": ["calendar.write"],
        "credential_reference": secret_ref,
        "verification_evidence_id": evidence_id,
        "verified_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        "status": "verified",
        "payload": payload,
        "payload_sha256": bytes.fromhex(source_hash),
        "revision": 1,
        "retention_policy_id": "retention:noor-document:30d",
        "retention_until": datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
        "created_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    }

    record = build_portable_record(
        table=entry["table"],
        record_contract=entry["recordContract"],
        primary_key_columns=("tenant_id", "installation_id", "binding_id"),
        transform_spec=entry["transformSpec"],
        database_row=database_row,
    )

    row = record["row"]
    assert "payload_sha256" not in row
    assert row["source_payload_sha256"] == source_hash
    assert row["credential_reference"] is None
    assert (
        row["credential_reference_sha256"]
        == hashlib.sha256(secret_ref.encode("utf-8")).hexdigest()
    )
    assert row["payload"]["contract"] == "EvaRedactedConnectorBinding/v1"
    assert row["payload"]["source_contract"] == "ConnectorBinding/v1"
    assert row["payload"]["credential_reference"] is None
    assert (
        row["redacted_payload_sha256"]
        == hashlib.sha256(canonical_json_bytes(row["payload"])).hexdigest()
    )

    database_row["payload_sha256"] = bytes(32)
    with pytest.raises(PortableArchiveError, match="source payload hash"):
        build_portable_record(
            table=entry["table"],
            record_contract=entry["recordContract"],
            primary_key_columns=("tenant_id", "installation_id", "binding_id"),
            transform_spec=entry["transformSpec"],
            database_row=database_row,
        )


def test_record_encoding_rejects_driver_order_drift() -> None:
    first = {
        "schema_version": 1,
        "contract": "test/v1",
        "source_table": "eva_core.test",
        "primary_key": {"id": "00000000-0000-0000-0000-000000000002"},
        "row": {"id": "00000000-0000-0000-0000-000000000002"},
        "row_sha256": hashlib.sha256(
            canonical_json_bytes({"id": "00000000-0000-0000-0000-000000000002"})
        ).hexdigest(),
    }
    second = {
        **first,
        "primary_key": {"id": "00000000-0000-0000-0000-000000000001"},
        "row": {"id": "00000000-0000-0000-0000-000000000001"},
    }
    second["row_sha256"] = hashlib.sha256(
        canonical_json_bytes(second["row"])
    ).hexdigest()
    with pytest.raises(PortableArchiveError, match="strict primary-key order"):
        encode_jsonl_records([first, second], primary_key_columns=("id",))


def test_checksums_and_archive_id_are_noncyclic_and_deterministic() -> None:
    payloads = {
        "records/z.jsonl": b"z\n",
        "records/a.jsonl": b"a\n",
    }
    checksums = build_checksums_bytes(payloads)
    assert checksums == (
        hashlib.sha256(b"a\n").hexdigest().encode()
        + b"  records/a.jsonl\n"
        + hashlib.sha256(b"z\n").hexdigest().encode()
        + b"  records/z.jsonl\n"
    )
    checksums_hash = hashlib.sha256(checksums).hexdigest()
    archive_id = compute_archive_id(
        source_installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
        export_sequence=7,
        checksums_sha256=checksums_hash,
    )
    assert archive_id == compute_archive_id(
        source_installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
        export_sequence=7,
        checksums_sha256=checksums_hash,
    )


def test_canonical_json_rejects_every_unicode_noncharacter_class() -> None:
    for value in ("\ufdd0", "\ufdef", "\ufffe", "\U0001ffff"):
        with pytest.raises(PortableArchiveError, match="noncharacter"):
            canonical_json_bytes({"value": value})
