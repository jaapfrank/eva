from __future__ import annotations

import copy
import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import pytest

from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import build_portable_record, canonical_json_bytes
from eva.contracts.execution import CredentialGrant
from eva.contracts.planning import ModelAttempt
from tools.portable_restore import (
    PortableRestoreError,
    decode_ambiguous_model_attempt,
    restore_column_preserving_records,
)
from tests.portable.restore_authority_fixture import RESTORE_AUTHORITY_ARGUMENTS


POSTGRES_DSN = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()


def _record() -> dict[str, Any]:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    installation_id = "22222222-2222-4222-8222-222222222222"
    attempt_id = "33333333-3333-4333-8333-333333333333"
    grant_id = "44444444-4444-4444-8444-444444444444"
    created_at = "2026-08-20T12:00:00Z"
    deadline = "2026-08-20T12:01:00Z"
    grant = CredentialGrant(
        schema_version=1,
        contract="CredentialGrant/v1",
        credential_grant_id=grant_id,
        connector_binding_id=None,
        model_provider_profile_id="model:fixture",
        worker_id="worker:fixture",
        executable_sha256="a" * 64,
        provider_scopes=["model.call"],
        secret_descriptor_id="legacy-secret:fixture",
        nonce="nonce:fixture",
        issued_at=created_at,
        deadline=deadline,
    ).model_dump(mode="json")
    attempt = ModelAttempt(
        schema_version=1,
        contract="ModelAttempt/v1",
        attempt_id=attempt_id,
        episode_id="55555555-5555-4555-8555-555555555555",
        generation=1,
        purpose="plan",
        ordinal=1,
        state="responded",
        provider_profile_id="model:fixture",
        request_sha256="b" * 64,
        response_sha256="c" * 64,
        credential_grant_id=grant_id,
        maximum_input_tokens=1,
        maximum_output_tokens=1,
        timeout_ms=1,
        input_tokens_used=1,
        output_tokens_used=1,
        provider_cost_euro_micros=1,
        created_at=created_at,
    ).model_dump(mode="json")
    moment = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    row = {
        "tenant_id": tenant_id, "installation_id": installation_id, "attempt_id": attempt_id,
        "episode_id": attempt["episode_id"], "generation": 1, "purpose": "plan", "ordinal": 1,
        "state": "responded", "provider_profile_id": "model:fixture", "request_sha256": "b" * 64,
        "response_sha256": "c" * 64, "credential_grant_id": grant_id,
        "credential_grant_payload": grant, "credential_grant_sha256": hashlib.sha256(canonical_json_bytes(grant)).digest(),
        "credential_grant_nonce": "nonce:fixture", "credential_grant_deadline": deadline,
        "model_adapter_manifest_sha256": bytes.fromhex("d" * 64), "model_program_sha256": bytes.fromhex("e" * 64),
        "maximum_input_tokens": 1, "maximum_output_tokens": 1, "timeout_ms": 1,
        "input_tokens_used": 1, "output_tokens_used": 1, "provider_cost_euro_micros": 1,
        "provider_reference": "provider-reference", "payload": attempt,
        "payload_sha256": hashlib.sha256(canonical_json_bytes(attempt)).digest(), "revision": 1,
        "retention_policy_id": "test", "retention_until": datetime(2027, 8, 20, tzinfo=timezone.utc),
        "created_at": moment, "updated_at": moment,
    }
    spec = next(x for x in CANONICAL_EXPORT_SPECS if x["table"] == "eva_core.model_attempts")
    return build_portable_record(table=spec["table"], record_contract=spec["record_contract"], primary_key_columns=spec["primary_key"], transform_spec=spec["transform_spec"], database_row=row)


def test_model_attempt_is_restored_only_as_terminal_secretless_history() -> None:
    restored = decode_ambiguous_model_attempt(_record(), table_specs=CANONICAL_EXPORT_SPECS)
    assert restored["state"] == "ambiguous"
    assert restored["response_sha256"] is None
    assert restored["provider_reference"] is None
    assert restored["payload"]["state"] == "ambiguous"
    assert restored["payload"]["response_sha256"] is None
    assert "legacy-secret" not in canonical_json_bytes(restored).decode("utf-8")
    assert restored["credential_grant_payload"]["secret_descriptor_id"].startswith("portable-restore-redacted:")


def test_model_attempt_rejects_changed_redacted_grant() -> None:
    record = copy.deepcopy(_record())
    payload = record["row"]["credential_grant_payload"]
    assert isinstance(payload, dict)
    payload["model_provider_profile_id"] = "different-model"
    record["row_sha256"] = hashlib.sha256(canonical_json_bytes(record["row"])).hexdigest()
    with pytest.raises(PortableRestoreError, match="grant and row differ"):
        decode_ambiguous_model_attempt(record, table_specs=CANONICAL_EXPORT_SPECS)


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


def test_restore_writer_inserts_only_terminal_secretless_model_history() -> None:
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_core.model_attempts"] = [_record()]
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
    assert insert[0].startswith("INSERT INTO eva_core.model_attempts")
    assert insert[1] is not None
    assert "ambiguous" in insert[1]
    bound_values = "\n".join(str(value) for value in insert[1])
    assert "portable-restore-redacted:44444444-4444-4444-8444-444444444444" in bound_values
    assert all(
        "legacy-secret" not in str(value) and "provider-reference" not in str(value)
        for value in insert[1]
    )


@pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="EVA2_TEST_POSTGRES_DSN is required for the real portable restore gate",
)
def test_terminalized_model_history_writes_to_real_postgres() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine(POSTGRES_DSN, pool_pre_ping=False)
    spec = next(
        item
        for item in CANONICAL_EXPORT_SPECS
        if item["table"] == "eva_core.model_attempts"
    )
    records = {"eva_core.model_attempts": [_record()]}
    try:
        connection = engine.connect().execution_options(isolation_level="SERIALIZABLE")
        try:
            with connection.begin():
                connection.exec_driver_sql("CREATE SCHEMA eva_core")
                connection.exec_driver_sql(
                    "CREATE TABLE eva_core.model_attempts ("
                    "tenant_id uuid NOT NULL, installation_id uuid NOT NULL, "
                    "attempt_id uuid NOT NULL, episode_id uuid NOT NULL, generation integer NOT NULL, "
                    "purpose text NOT NULL, ordinal integer NOT NULL, state text NOT NULL, "
                    "provider_profile_id text NOT NULL, request_sha256 bytea NOT NULL, "
                    "response_sha256 bytea, credential_grant_id uuid NOT NULL, "
                    "credential_grant_payload jsonb NOT NULL, credential_grant_sha256 bytea NOT NULL, "
                    "credential_grant_nonce text NOT NULL, credential_grant_deadline timestamptz NOT NULL, "
                    "model_adapter_manifest_sha256 bytea NOT NULL, model_program_sha256 bytea NOT NULL, "
                    "maximum_input_tokens integer NOT NULL, maximum_output_tokens integer NOT NULL, "
                    "timeout_ms integer NOT NULL, input_tokens_used integer NOT NULL, "
                    "output_tokens_used integer NOT NULL, provider_cost_euro_micros bigint NOT NULL, "
                    "provider_reference text, payload jsonb NOT NULL, payload_sha256 bytea NOT NULL, "
                    "revision bigint NOT NULL, retention_policy_id text NOT NULL, "
                    "retention_until timestamptz NOT NULL, created_at timestamptz NOT NULL, "
                    "updated_at timestamptz NOT NULL)"
                )

                assert (
                    restore_column_preserving_records(
                        connection,
                        records,
                        **RESTORE_AUTHORITY_ARGUMENTS,
                        table_specs=(spec,),
                    )
                    == 1
                )
                row = connection.exec_driver_sql(
                    "SELECT state, response_sha256, provider_reference, "
                    "credential_grant_payload->>'secret_descriptor_id' "
                    "FROM eva_core.model_attempts"
                ).one()
                assert row == (
                    "ambiguous",
                    None,
                    None,
                    "portable-restore-redacted:44444444-4444-4444-8444-444444444444",
                )
        finally:
            connection.close()
    finally:
        engine.dispose()
