from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.application.portable_archive import build_portable_record, canonical_json_bytes
from eva.contracts.execution import CredentialGrant
from eva.contracts.planning import ModelAttempt
from eva.operations.portable_restore import (
    PortableRestoreError,
    decode_ambiguous_model_attempt,
)


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
