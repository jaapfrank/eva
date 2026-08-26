from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.operations.object_keyring import FileObjectKeyResolver
from eva.operations.portable_object_materializer import materialize_verified_portable_objects
from eva.operations.portable_restore import (
    PortableRestoreError,
    restore_column_preserving_records,
)
from test_portable_export_end_to_end import _fixture_record_sets


POSTGRES_DSN = os.getenv("EVA2_TEST_POSTGRES_DSN")


def _terminal_object_reference() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    schedule = json.loads(
        (
            root / "design" / "vertical" / "noor-4d-concurrency-crash-schedules-v1.json"
        ).read_text(encoding="utf-8")
    )
    facts = schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
    return next(
        item["portable_record"]
        for item in facts
        if item["fact_id"] == "correction/object/document"
    )


class _NeverTarget:
    def exec_driver_sql(self, *_: object, **__: object) -> object:
        raise AssertionError("object restore must fail before the target is queried")


class _WritingTarget:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...] | None]] = []

    def exec_driver_sql(
        self, sql: str, params: tuple[object, ...] | None = None
    ) -> object:
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


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


def _current_object_record() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    schedule = json.loads(
        (root / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = json.loads(
        (root / "design/vertical/noor-4d-execution-profile-v1.json").read_text(
            encoding="utf-8"
        )
    )
    record = _terminal_object_reference()
    selected = schedule["portableSnapshotAuthority"]["selectedEncryptedObjects"]["objects"][0]
    key = profile["encryptedObjectFormat"]["keyBinding"]["fourDFixtureKeyRecords"][0]
    return record, selected, key


def _keyring(path: Path, key: dict[str, Any]) -> FileObjectKeyResolver:
    path.write_text(
        json.dumps(
            {
                "contract": "EvaObjectKeyring/v1",
                "keys": [
                    {
                        "tenant_id": key["tenantId"],
                        "installation_id": key["installationId"],
                        "key_ref": key["keyRef"],
                        "key_material_hex": key["keyMaterial"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return FileObjectKeyResolver(path)


def test_object_rows_need_a_verified_cas_materializer_before_any_target_write() -> None:
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    record, _selected, _key = _current_object_record()
    records["eva_evidence.object_refs"] = [record]

    with pytest.raises(
        PortableRestoreError, match="verified object CAS materialization"
    ):
        restore_column_preserving_records(
            _NeverTarget(), records, table_specs=CANONICAL_EXPORT_SPECS
        )


def test_object_row_is_inserted_only_after_its_exact_target_cas_proof(
    private_tmp_path: Path,
) -> None:
    record, selected, key = _current_object_record()
    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    source = private_tmp_path / "source"
    source_path = source / "sha256" / digest[:2] / f"{digest}.eva2xo"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    target_root = private_tmp_path / "target"
    materialized = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target_root,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=_keyring(private_tmp_path / "keys.json", key),
    )
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_evidence.object_refs"] = [record]
    target = _WritingTarget()

    assert (
        restore_column_preserving_records(
            target,
            records,
            table_specs=CANONICAL_EXPORT_SPECS,
            materialized_objects=materialized,
            object_store_root=target_root,
        )
        == 1
    )
    assert any(
        sql.startswith("INSERT INTO eva_evidence.object_refs")
        for sql, _params in target.queries
    )


def test_object_proof_for_another_target_root_is_rejected_before_database_access(
    private_tmp_path: Path,
) -> None:
    record, selected, key = _current_object_record()
    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    source = private_tmp_path / "source"
    source_path = source / "sha256" / digest[:2] / f"{digest}.eva2xo"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    target_root = private_tmp_path / "target"
    materialized = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target_root,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=_keyring(private_tmp_path / "keys.json", key),
    )
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_evidence.object_refs"] = [record]

    with pytest.raises(PortableRestoreError, match="materialized object proof is invalid"):
        restore_column_preserving_records(
            _NeverTarget(),
            records,
            table_specs=CANONICAL_EXPORT_SPECS,
            materialized_objects=materialized,
            object_store_root=private_tmp_path / "another-target",
        )


def test_internally_inconsistent_historical_fixture_stops_before_target_database_access(
    private_tmp_path: Path,
) -> None:
    record_sets, _installation_id, authority = _fixture_record_sets("terminal")
    records = {item.table: list(item.records) for item in record_sets}
    selected = authority["selectedEncryptedObjects"]["objects"][0]
    key = json.loads(
        (Path(__file__).resolve().parents[2] / "design/vertical/noor-4d-execution-profile-v1.json").read_text(
            encoding="utf-8"
        )
    )["encryptedObjectFormat"]["keyBinding"]["fourDFixtureKeyRecords"][0]
    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    source = private_tmp_path / "source"
    source_path = source / "sha256" / digest[:2] / f"{digest}.eva2xo"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    target_root = private_tmp_path / "target"
    materialized = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target_root,
        records_by_table={"eva_evidence.object_refs": records["eva_evidence.object_refs"]},
        key_resolver=_keyring(private_tmp_path / "keys.json", key),
    )
    with pytest.raises(PortableRestoreError, match="model attempt grant and row differ"):
        restore_column_preserving_records(
            _NeverTarget(),
            records,
            table_specs=CANONICAL_EXPORT_SPECS,
            materialized_objects=materialized,
            object_store_root=target_root,
        )


@pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="EVA2_TEST_POSTGRES_DSN is required for the real portable restore gate",
)
def test_object_ref_writes_to_real_postgres_only_after_target_cas_materialization(
    private_tmp_path: Path,
) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    record, selected, key = _current_object_record()
    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    source = private_tmp_path / "source"
    source_path = source / "sha256" / digest[:2] / f"{digest}.eva2xo"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    target_root = private_tmp_path / "target"
    materialized = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target_root,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=_keyring(private_tmp_path / "keys.json", key),
    )
    spec = next(item for item in CANONICAL_EXPORT_SPECS if item["table"] == "eva_evidence.object_refs")
    engine = sqlalchemy.create_engine(POSTGRES_DSN, pool_pre_ping=False)
    try:
        connection = engine.connect().execution_options(isolation_level="SERIALIZABLE")
        try:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("CREATE SCHEMA eva_evidence")
                connection.exec_driver_sql(
                    "CREATE TABLE eva_evidence.object_refs ("
                    "tenant_id uuid NOT NULL, installation_id uuid NOT NULL, object_id uuid NOT NULL, "
                    "object_store_id uuid NOT NULL, owner_principal_id uuid NOT NULL, "
                    "selected_effect_attempt_id uuid NOT NULL, selected_candidate_id uuid NOT NULL, "
                    "ciphertext_sha256 bytea NOT NULL, plaintext_sha256 bytea NOT NULL, "
                    "plaintext_byte_length bigint NOT NULL, envelope_byte_length bigint NOT NULL, "
                    "mime_type text NOT NULL, encryption_algorithm text NOT NULL, wrapped_key_ref uuid NOT NULL, "
                    "physical_cas_key text NOT NULL, state text NOT NULL, payload jsonb NOT NULL, "
                    "payload_sha256 bytea NOT NULL, revision bigint NOT NULL, retention_policy_id text NOT NULL, "
                    "retention_until timestamptz NOT NULL, created_at timestamptz NOT NULL, erased_at timestamptz NULL)"
                )
                assert (
                    restore_column_preserving_records(
                        connection,
                        {"eva_evidence.object_refs": [record]},
                        table_specs=(spec,),
                        materialized_objects=materialized,
                        object_store_root=target_root,
                    )
                    == 1
                )
                restored = connection.exec_driver_sql(
                    "SELECT object_id::text, encode(ciphertext_sha256, 'hex'), "
                    "physical_cas_key, state FROM eva_evidence.object_refs"
                ).one()
                assert restored == (
                    record["row"]["object_id"],
                    digest,
                    f"sha256/{digest[:2]}/{digest}.eva2xo",
                    "active",
                )
            finally:
                transaction.rollback()
        finally:
            connection.close()
    finally:
        engine.dispose()
