"""Fresh schemas admit empty and non-empty portable archives."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.adapters.postgres.noor_4d_repository import (
    ExactNoor4DRow,
    PostgresNoor4DRepository,
)
from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
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
    """Export a quiescent source and restore its exact four-record core elsewhere.

    The fixed synthetic rows are database-consistent and contain no pending
    effect or personal content.  Source and target must be distinct disposable
    PostgreSQL databases: this test never opens Eva1, a live Eva2 database, or
    their volumes.
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
        assert isinstance(tenant, UUID)
        assert isinstance(installation, UUID)
        with source_engine.begin() as connection:
            connection.execute(sqlalchemy.text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(sqlalchemy.text("SET LOCAL ROLE eva_dbos"))
            source = PostgresNoor4DRepository(
                connection, str(tenant), str(installation)
            )
            for fact_id in (
                "static/tenant",
                "static/installation",
                "static/identity-pack",
                "static/policy-pack",
            ):
                assert source.upsert_exact(_exact_fixture_row(fact_id)).disposition == "inserted"

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
                    == 4
                )
                restored = {
                    table: connection.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {table}"
                    ).scalar_one()
                    for table in nonempty
                }
                transaction.commit()
            except BaseException:
                transaction.rollback()
                raise
        assert restored == nonempty
    finally:
        source_engine.dispose()
        target_engine.dispose()
