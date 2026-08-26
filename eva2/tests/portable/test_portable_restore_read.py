from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import build_portable_record, canonical_json_bytes
from eva.contracts.execution import CapabilityGrant, CredentialGrant, ReadAttempt
from eva.operations.portable_restore import (
    PortableRestoreError,
    decode_terminal_read_attempt,
    restore_column_preserving_records,
)


def _record() -> dict[str, Any]:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    installation_id = "22222222-2222-4222-8222-222222222222"
    attempt_id = "33333333-3333-4333-8333-333333333333"
    intent_id = "44444444-4444-4444-8444-444444444444"
    capability_grant_id = "55555555-5555-4555-8555-555555555555"
    credential_grant_id = "66666666-6666-4666-8666-666666666666"
    binding_id = "77777777-7777-4777-8777-777777777777"
    episode_id = "88888888-8888-4888-8888-888888888888"
    created_at = "2026-08-20T12:00:00Z"
    deadline = "2026-08-20T12:01:00Z"
    resource_scope = {
        "resource_type": "home_assistant",
        "resource_id": "light.kitchen",
        "scope_sha256": "a" * 64,
    }
    capability = CapabilityGrant(
        schema_version=1,
        contract="CapabilityGrant/v1",
        grant_id=capability_grant_id,
        tenant_id=tenant_id,
        episode_id=episode_id,
        generation=1,
        capability_id="home.read",
        connector_binding_id=binding_id,
        operation="home.read",
        resource_scope=resource_scope,
        authorized_payload_sha256="b" * 64,
        policy_sha256="c" * 64,
        nonce="capability-nonce",
        issued_at=created_at,
        deadline=deadline,
    ).model_dump(mode="json")
    credential = CredentialGrant(
        schema_version=1,
        contract="CredentialGrant/v1",
        credential_grant_id=credential_grant_id,
        connector_binding_id=binding_id,
        model_provider_profile_id=None,
        worker_id="worker:fixture",
        executable_sha256="d" * 64,
        provider_scopes=["home.read"],
        secret_descriptor_id="legacy-secret:home",
        nonce="credential-nonce",
        issued_at=created_at,
        deadline=deadline,
    ).model_dump(mode="json")
    attempt = ReadAttempt(
        schema_version=1,
        contract="ReadAttempt/v1",
        read_attempt_id=attempt_id,
        read_intent_id=intent_id,
        ordinal=1,
        state="observed",
        capability_grant_id=capability_grant_id,
        credential_grant_id=credential_grant_id,
        connector_manifest_sha256="e" * 64,
        request_sha256="f" * 64,
        response_sha256="0" * 64,
        deadline=deadline,
        created_at=created_at,
    ).model_dump(mode="json")
    moment = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    row = {
        "tenant_id": tenant_id,
        "installation_id": installation_id,
        "read_attempt_id": attempt_id,
        "read_intent_id": intent_id,
        "ordinal": 1,
        "state": "observed",
        "capability_grant_id": capability_grant_id,
        "capability_grant_payload": capability,
        "capability_grant_sha256": hashlib.sha256(
            canonical_json_bytes(capability)
        ).digest(),
        "capability_grant_nonce": "capability-nonce",
        "capability_grant_deadline": deadline,
        "credential_grant_id": credential_grant_id,
        "credential_grant_payload": credential,
        "credential_grant_sha256": hashlib.sha256(
            canonical_json_bytes(credential)
        ).digest(),
        "credential_grant_nonce": "credential-nonce",
        "credential_grant_deadline": deadline,
        "connector_manifest_sha256": bytes.fromhex("e" * 64),
        "request_sha256": bytes.fromhex("f" * 64),
        "response_sha256": bytes.fromhex("0" * 64),
        "deadline": deadline,
        "payload": attempt,
        "payload_sha256": hashlib.sha256(canonical_json_bytes(attempt)).digest(),
        "revision": 2,
        "retention_policy_id": "test",
        "retention_until": datetime(2027, 8, 20, tzinfo=timezone.utc),
        "created_at": moment,
        "updated_at": moment,
    }
    spec = next(
        spec for spec in CANONICAL_EXPORT_SPECS if spec["table"] == "eva_effects.read_attempts"
    )
    return build_portable_record(
        table=spec["table"],
        record_contract=spec["record_contract"],
        primary_key_columns=spec["primary_key"],
        transform_spec=spec["transform_spec"],
        database_row=row,
    )


def _rehash(record: dict[str, Any]) -> None:
    record["row_sha256"] = hashlib.sha256(
        canonical_json_bytes(record["row"])
    ).hexdigest()


def test_finished_read_keeps_audit_result_but_not_its_old_secret() -> None:
    restored = decode_terminal_read_attempt(_record(), table_specs=CANONICAL_EXPORT_SPECS)

    assert restored["state"] == "observed"
    assert restored["response_sha256"] == "0" * 64
    assert restored["payload"]["state"] == "observed"
    assert restored["credential_grant_payload"]["secret_descriptor_id"] == (
        "portable-restore-redacted:66666666-6666-4666-8666-666666666666"
    )
    assert "legacy-secret" not in canonical_json_bytes(restored).decode("utf-8")


def test_read_restore_rejects_a_still_executable_old_call() -> None:
    record = copy.deepcopy(_record())
    payload = record["row"]["payload"]
    assert isinstance(payload, dict)
    record["row"]["state"] = "dispatch_authorized"
    record["row"]["response_sha256"] = None
    payload["state"] = "dispatch_authorized"
    payload["response_sha256"] = None
    record["row"]["payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()
    _rehash(record)

    with pytest.raises(PortableRestoreError, match="still executable"):
        decode_terminal_read_attempt(record, table_specs=CANONICAL_EXPORT_SPECS)


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


def test_restore_writer_inserts_only_the_safe_terminal_read_history() -> None:
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_effects.read_attempts"] = [_record()]
    target = _EmptyTarget()

    assert (
        restore_column_preserving_records(
            target, records, table_specs=CANONICAL_EXPORT_SPECS
        )
        == 1
    )

    insert = next(query for query in target.queries if query[0].startswith("INSERT INTO "))
    assert insert[0].startswith("INSERT INTO eva_effects.read_attempts")
    assert insert[1] is not None
    bound_values = "\n".join(str(value) for value in insert[1])
    assert "portable-restore-redacted:66666666-6666-4666-8666-666666666666" in bound_values
    assert "legacy-secret" not in bound_values
