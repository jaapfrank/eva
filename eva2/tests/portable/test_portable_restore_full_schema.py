"""Fresh schemas admit empty and non-empty portable archives."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.adapters.postgres.memory import PostgresMemoryRepository
from eva.adapters.postgres.noor_4d_repository import (
    ExactNoor4DRow,
    PostgresNoor4DRepository,
)
from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.application.memory import MemoryAuthority, RememberFact
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.ingress import TurnEnvelope
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact
from eva.operations.install_schema import install_schema
from eva.operations.portable_export import export_portable_archive
from eva.operations.portable_restore import restore_column_preserving_records


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_DSN = os.getenv("EVA2_TEST_POSTGRES_DSN")
PORTABLE_SOURCE_DSN = os.getenv("EVA2_TEST_PORTABLE_SOURCE_DSN")
PORTABLE_TARGET_DSN = os.getenv("EVA2_TEST_PORTABLE_TARGET_DSN")


def _fixture_fact_rows() -> dict[str, dict[str, Any]]:
    schedule = json.loads(
        (
            ROOT / "design" / "vertical" / "noor-4d-concurrency-crash-schedules-v1.json"
        ).read_text(encoding="utf-8")
    )
    facts = schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
    return {str(item["fact_id"]): dict(item["portable_record"]) for item in facts}


def _native_value(value: Any, declaration: str) -> Any:
    if value is None:
        return None
    if declaration == "uuid":
        return UUID(str(value))
    if declaration == "uuid[]":
        return [UUID(str(item)) for item in value]
    if declaration == "bytea":
        return bytes.fromhex(str(value))
    if declaration in {"timestamptz", "timestamptz(0)"}:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    return value


def _exact_fixture_row(fact_id: str) -> ExactNoor4DRow:
    portable = _fixture_fact_rows()[fact_id]
    table = str(portable["source_table"])
    catalog = next(item for item in CATALOG_SPEC["canonical_tables"] if item["table"] == table)
    declarations = {
        declaration.split(" ", 1)[0]: declaration.split(" ", 2)[1]
        for declaration in catalog["columns"]
    }
    row = portable["row"]
    assert isinstance(row, dict)
    values = {
        name: _native_value(value, declarations[name]) for name, value in row.items()
    }
    row_sha256 = portable["row_sha256"]
    assert isinstance(row_sha256, str) and len(row_sha256) == 64
    return ExactNoor4DRow(table, values, row_sha256)


def _archive_records(archive: Path) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    for spec in CANONICAL_EXPORT_SPECS:
        path = archive / str(spec["record_jsonl_relative_path"])
        records[str(spec["table"])] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    return records


def _synthetic_memory_command(
    *, tenant_id: str, installation_id: str, principal_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], RememberFact, str, str]:
    """Make a complete, non-personal memory write through Eva's real contract."""

    evidence_id = uuid5(NAMESPACE_URL, "eva2-portable-recovery-memory-evidence")
    source_event_id = uuid5(
        NAMESPACE_URL, "eva2-portable-recovery-memory-source-event"
    )
    fact_id = uuid5(NAMESPACE_URL, "eva2-portable-recovery-memory-fact")
    current_fact_id = uuid5(NAMESPACE_URL, "eva2-portable-recovery-memory-current")
    conversation_id = uuid5(NAMESPACE_URL, "eva2-portable-recovery-conversation")
    message_id = uuid5(NAMESPACE_URL, "eva2-portable-recovery-message")
    at = "2026-08-27T12:00:00Z"
    retention_until = "2026-09-26T12:00:00Z"
    subject_payload_sha256 = hashlib.sha256(
        b"synthetic portable recovery memory subject"
    ).hexdigest()
    evidence = EvidenceEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "contract": "EvidenceEnvelope/v2",
            "evidence_id": str(evidence_id),
            "tenant_id": tenant_id,
            "aggregate_type": "synthetic_recovery",
            "aggregate_id": "portable-recovery-memory",
            "sequence": 1,
            "idempotency_key": "portable-recovery-memory-evidence",
            "producer": "eva2-recovery-test",
            "subject_contract": "SyntheticRecoveryMemory/v1",
            "subject_id": "portable-recovery-memory",
            "subject_payload_sha256": subject_payload_sha256,
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [],
            "captured_at": at,
            "privacy_class": "internal",
            "retention_until": retention_until,
        }
    )
    source_event = ExperienceSourceEvent.model_validate(
        {
            "schema_version": 1,
            "contract": "ExperienceSourceEvent/v1",
            "source_event_id": str(source_event_id),
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "subject_id": principal_id,
            "event_type": "preference_stated",
            "source_kind": "direct_user_input",
            "source_ref": "portable-recovery-memory-source",
            "content_sha256": hashlib.sha256(
                b"synthetic portable recovery memory content"
            ).hexdigest(),
            "provenance_refs": [str(evidence_id)],
            "observed_at": at,
            "valid_from": at,
            "valid_until": None,
            "ingested_at": at,
            "trust": "confirmed_direct_user",
            "privacy_class": "internal",
            "purpose": "conversation_continuity",
            "retention_until": retention_until,
            "delete_sla_at": retention_until,
            "corrects_source_event_id": None,
            "supersedes_source_event_id": None,
            "retracts_source_event_id": None,
            "erasure_request_id": None,
        }
    )
    value = {"preference": "synthetic-portable-recovery"}
    fact = MemoryFact.model_validate(
        {
            "schema_version": 1,
            "contract": "MemoryFact/v1",
            "fact_id": str(fact_id),
            "tenant_id": tenant_id,
            "subject_id": principal_id,
            "predicate": "preference.portable_recovery",
            "value": value,
            "source_event_ids": [str(source_event_id)],
            "observed_at": at,
            "valid_from": at,
            "valid_until": None,
            "trust": "confirmed_direct_user",
            "content_sha256": canonical_sha256(value),
            "assertion_status": "active",
            "superseded_by_fact_id": None,
            "retraction_source_event_id": None,
        }
    )
    evidence_row = {
        "tenant_id": UUID(tenant_id),
        "installation_id": UUID(installation_id),
        "evidence_id": evidence_id,
        "aggregate_type": evidence.aggregate_type,
        "aggregate_id": evidence.aggregate_id,
        "sequence": evidence.sequence,
        "idempotency_key": evidence.idempotency_key,
        "producer": evidence.producer,
        "subject_contract": evidence.subject_contract,
        "subject_id": evidence.subject_id,
        "subject_payload_sha256": bytes.fromhex(evidence.subject_payload_sha256),
        "object_ref_id": None,
        "object_sha256": None,
        "predecessor_sha256": None,
        "provenance_refs": [],
        "captured_at": at,
        "privacy_class": evidence.privacy_class,
        "retention_policy_id": "synthetic-recovery-test",
        "retention_until": retention_until,
        "payload": evidence.model_dump(mode="json"),
        "payload_sha256": bytes.fromhex(canonical_sha256(evidence)),
    }
    command = RememberFact(
        installation_id=installation_id,
        current_fact_id=str(current_fact_id),
        source_event=source_event,
        fact=fact,
        scope={"kind": "person", "principal_id": principal_id},
        retention_policy_id="synthetic-recovery-test",
        committed_at=at,
    )
    text = "Synthetische herstelturn voor Eva2."
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    turn = TurnEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "TurnEnvelope/v1",
            "installation_id": installation_id,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "principal_binding_id": "synthetic-recovery-binding",
            "authentication_evidence_sha256": subject_payload_sha256,
            "channel_id": "synthetic-recovery",
            "request_id": "synthetic-recovery-request",
            "message_id": str(message_id),
            "conversation_id": str(conversation_id),
            "content_type": "text/plain",
            "text": text,
            "raw_bytes_sha256": text_sha256,
            "received_at": at,
        }
    )
    conversation_row = {
        "tenant_id": UUID(tenant_id),
        "installation_id": UUID(installation_id),
        "conversation_id": conversation_id,
        "owner_principal_id": UUID(principal_id),
        "participant_principal_ids": [UUID(principal_id)],
        "channel_class": "synthetic_recovery",
        "status": "active",
        "current_episode_id": None,
        "revision": 1,
        "retention_policy_id": "synthetic-recovery-test",
        "retention_until": retention_until,
        "created_at": at,
        "updated_at": at,
    }
    message_row = {
        "tenant_id": UUID(tenant_id),
        "installation_id": UUID(installation_id),
        "message_id": message_id,
        "conversation_id": conversation_id,
        "episode_id": None,
        "generation": None,
        "principal_id": UUID(principal_id),
        "identity_id": None,
        "identity_pack_version": None,
        "role": "user",
        "channel_id": "synthetic-recovery",
        "content_type": "text/plain",
        "text_content": text,
        "object_ref_id": None,
        "raw_bytes_sha256": bytes.fromhex(text_sha256),
        "content_sha256": bytes.fromhex(text_sha256),
        "provenance_evidence_id": evidence_id,
        "content_status": "validated",
        "payload_contract": "TurnEnvelope/v1",
        "payload_schema_version": 1,
        "payload": turn.model_dump(mode="json"),
        "payload_sha256": bytes.fromhex(canonical_sha256(turn)),
        "privacy_class": "internal",
        "retention_policy_id": "synthetic-recovery-test",
        "retention_until": retention_until,
        "created_at": at,
    }
    return (
        evidence_row,
        conversation_row,
        message_row,
        command,
        str(fact_id),
        str(message_id),
    )


@pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="EVA2_TEST_POSTGRES_DSN is required for the real full restore gate",
)
def test_fresh_full_schema_admits_the_complete_empty_portable_archive() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    receipt = install_schema(POSTGRES_DSN, alembic_ini=ROOT / "alembic.ini")
    assert receipt.final_revision == "4e_0004_delegated_work_stop"
    records = {str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS}
    engine = sqlalchemy.create_engine(POSTGRES_DSN, pool_pre_ping=False)
    try:
        connection = engine.connect().execution_options(isolation_level="SERIALIZABLE")
        try:
            transaction = connection.begin()
            try:
                assert (
                    restore_column_preserving_records(
                        connection, records, table_specs=CANONICAL_EXPORT_SPECS
                    )
                    == 0
                )
            finally:
                transaction.rollback()
        finally:
            connection.close()
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not PORTABLE_SOURCE_DSN or not PORTABLE_TARGET_DSN,
    reason=(
        "EVA2_TEST_PORTABLE_SOURCE_DSN and EVA2_TEST_PORTABLE_TARGET_DSN are "
        "required for the non-empty source-to-target restore gate"
    ),
)
def test_nonempty_portable_archive_restores_from_one_fresh_database_to_another(
    tmp_path: Path,
) -> None:
    """Export a quiescent source and restore core plus real-memory data elsewhere.

    The fixed synthetic rows are database-consistent and contain no pending
    effect or personal content. One memory fact is written through the normal
    memory authority and read back after restore. Source and target must be
    distinct disposable PostgreSQL databases: this test never opens Eva1, a
    live Eva2 database, or their volumes.
    """

    assert PORTABLE_SOURCE_DSN is not None
    assert PORTABLE_TARGET_DSN is not None
    assert PORTABLE_SOURCE_DSN != PORTABLE_TARGET_DSN
    sqlalchemy = pytest.importorskip("sqlalchemy")
    source_engine = sqlalchemy.create_engine(PORTABLE_SOURCE_DSN, pool_pre_ping=False)
    target_engine = sqlalchemy.create_engine(PORTABLE_TARGET_DSN, pool_pre_ping=False)
    try:
        install_schema(PORTABLE_SOURCE_DSN, alembic_ini=ROOT / "alembic.ini")
        tenant = _exact_fixture_row("static/tenant").values["tenant_id"]
        installation = _exact_fixture_row("static/installation").values[
            "installation_id"
        ]
        principal = _exact_fixture_row("static/principal-owner").values[
            "principal_id"
        ]
        assert isinstance(tenant, UUID)
        assert isinstance(installation, UUID)
        assert isinstance(principal, UUID)
        (
            evidence_row,
            conversation_row,
            message_row,
            memory_command,
            fact_id,
            message_id,
        ) = _synthetic_memory_command(
            tenant_id=str(tenant),
            installation_id=str(installation),
            principal_id=str(principal),
        )
        with source_engine.begin() as connection:
            connection.execute(sqlalchemy.text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(sqlalchemy.text("SET LOCAL ROLE eva_dbos"))
            source = PostgresNoor4DRepository(
                connection, str(tenant), str(installation)
            )
            for fixture_fact_id in (
                "static/tenant",
                "static/installation",
                "static/identity-pack",
                "static/policy-pack",
                "static/principal-owner",
            ):
                assert (
                    source.upsert_exact(_exact_fixture_row(fixture_fact_id)).disposition
                    == "inserted"
                )
            connection.execute(
                metadata.tables["eva_evidence.evidence_envelopes"]
                .insert()
                .values(**evidence_row)
            )
            connection.execute(
                metadata.tables["eva_core.conversations"]
                .insert()
                .values(**conversation_row)
            )
            connection.execute(
                metadata.tables["eva_core.messages"].insert().values(**message_row)
            )
            memory = MemoryAuthority(
                PostgresMemoryRepository(connection, str(tenant), str(installation))
            )
            assert memory.remember(memory_command).disposition == "inserted"

        archive = tmp_path / "portable-source"
        with source_engine.connect() as connection:
            export_receipt = export_portable_archive(
                connection,
                tenant_id=str(tenant),
                installation_id=str(installation),
                export_sequence=1,
                created_at="2026-08-27T12:00:00Z",
                destination=archive,
                object_blob_root=None,
            )
        assert hashlib.sha256((archive / "archive.json").read_bytes()).hexdigest() == export_receipt["archive_json_sha256"]
        records = _archive_records(archive)
        nonempty = {table: len(rows) for table, rows in records.items() if rows}
        assert nonempty == {
            "eva_core.tenants": 1,
            "eva_core.installations": 1,
            "eva_core.identity_pack_versions": 1,
            "eva_core.policy_pack_versions": 1,
            "eva_core.principals": 1,
            "eva_core.conversations": 1,
            "eva_core.messages": 1,
            "eva_memory.source_events": 1,
            "eva_memory.fact_assertions": 1,
            "eva_memory.current_facts": 1,
            "eva_evidence.evidence_envelopes": 1,
        }

        install_schema(PORTABLE_TARGET_DSN, alembic_ini=ROOT / "alembic.ini")
        with target_engine.connect().execution_options(
            isolation_level="SERIALIZABLE"
        ) as connection:
            transaction = connection.begin()
            try:
                assert (
                    restore_column_preserving_records(
                        connection, records, table_specs=CANONICAL_EXPORT_SPECS
                    )
                    == sum(nonempty.values())
                )
                restored = {
                    table: connection.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {table}"
                    ).scalar_one()
                    for table in nonempty
                }
                restored_fact = connection.exec_driver_sql(
                    "SELECT fact_id::text, value->>'preference' "
                    "FROM eva_memory.fact_assertions"
                ).one()
                restored_message = connection.exec_driver_sql(
                    "SELECT message_id::text, text_content FROM eva_core.messages"
                ).one()
                transaction.commit()
            except BaseException:
                transaction.rollback()
                raise
        assert restored == nonempty
        assert restored_fact == (fact_id, "synthetic-portable-recovery")
        assert restored_message == (message_id, "Synthetische herstelturn voor Eva2.")
    finally:
        source_engine.dispose()
        target_engine.dispose()
