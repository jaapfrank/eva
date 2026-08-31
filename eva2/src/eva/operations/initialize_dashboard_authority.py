"""Initialize the one verified owner binding used by Eva's local dashboard.

This is an explicit installation operation.  It never invents authority at
runtime: the operator supplies the four fixed UUIDs, the immutable release
hash, and a private dashboard access-token file.  A partial or differing
existing scope is rejected instead of repaired in place.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import sys
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.credentials.runtime_files import (
    required_environment as _required_environment,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.evidence import EvidenceEnvelopeV2


_TENANTS = metadata.tables["eva_core.tenants"]
_INSTALLATIONS = metadata.tables["eva_core.installations"]
_PRINCIPALS = metadata.tables["eva_core.principals"]
_BINDINGS = metadata.tables["eva_core.principal_bindings"]
_EVIDENCE = metadata.tables["eva_evidence.evidence_envelopes"]
_RETENTION_POLICY = "retention:dashboard-owner:10y"


@dataclass(frozen=True, slots=True)
class DashboardAuthorityReceipt:
    binding_id: str
    evidence_id: str
    installation_id: str
    release_sha256: str
    status: str
    subject_sha256: str
    tenant_id: str


def _uuid(value: str, *, name: str) -> UUID:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise RuntimeError(f"{name} must be a UUID") from error
    if str(parsed) != value:
        raise RuntimeError(f"{name} must use canonical UUID text")
    return parsed


def _sha256(value: str, *, name: str) -> bytes:
    if len(value) != 64:
        raise RuntimeError(f"{name} must be a SHA-256 value")
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a SHA-256 value") from error
    if raw.hex() != value.lower():
        raise RuntimeError(f"{name} must use lowercase hexadecimal")
    return raw


def _token_subject_sha256(token: str) -> bytes:
    if len(token) < 32 or len(token) > 512 or any(character.isspace() for character in token):
        raise RuntimeError("dashboard access token has an invalid shape")
    return hashlib.sha256(
        b"eva2-dashboard-owner-token-v1\0" + token.encode("utf-8")
    ).digest()


def initialize_dashboard_authority(
    database_dsn: str,
    *,
    tenant_id: str,
    installation_id: str,
    principal_id: str,
    binding_id: str,
    release_sha256: str,
    access_token: str,
    now: datetime | None = None,
) -> DashboardAuthorityReceipt:
    tenant = _uuid(tenant_id, name="dashboard tenant")
    installation = _uuid(installation_id, name="dashboard installation")
    principal = _uuid(principal_id, name="dashboard principal")
    binding = _uuid(binding_id, name="dashboard binding")
    release_hash = _sha256(release_sha256, name="dashboard release")
    subject_hash = _token_subject_sha256(access_token)
    evidence_id = uuid5(binding, f"dashboard-owner-verification:v1:{subject_hash.hex()}")
    captured_at = (now or datetime.now(UTC)).replace(microsecond=0)
    if captured_at.tzinfo is None:
        raise RuntimeError("dashboard authority time must include UTC offset")
    captured_at = captured_at.astimezone(UTC)
    retention_until = captured_at + timedelta(days=3650)
    timestamp = captured_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    retention = retention_until.strftime("%Y-%m-%dT%H:%M:%SZ")
    subject = {
        "binding_id": str(binding),
        "channel": "owner-local",
        "installation_id": str(installation),
        "principal_id": str(principal),
        "provider": "eva-dashboard",
        "subject_sha256": subject_hash.hex(),
        "tenant_id": str(tenant),
    }
    subject_payload_hash = canonical_sha256(subject)
    evidence = EvidenceEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "contract": "EvidenceEnvelope/v2",
            "evidence_id": str(evidence_id),
            "tenant_id": str(tenant),
            "aggregate_type": "dashboard_owner_binding",
            "aggregate_id": str(binding),
            "sequence": 1,
            "idempotency_key": "initial-verification:v1",
            "producer": "eva.dashboard.authority.installer",
            "subject_contract": "DashboardOwnerBinding/v1",
            "subject_id": str(binding),
            "subject_payload_sha256": subject_payload_hash,
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [],
            "captured_at": timestamp,
            "privacy_class": "restricted",
            "retention_until": retention,
        }
    )
    evidence_payload = evidence.model_dump(mode="json")
    evidence_payload_hash = bytes.fromhex(canonical_sha256(evidence_payload))
    expected = {
        "tenant": {
            "tenant_id": tenant,
            "status": "active",
            "authority_policy_version": 1,
            "revision": 1,
            "retention_policy_id": _RETENTION_POLICY,
        },
        "installation": {
            "tenant_id": tenant,
            "installation_id": installation,
            "status": "active",
            "active_release_sha256": release_hash,
            "support_tier": "tier1_linux_container",
            "current_identity_id": None,
            "current_identity_pack_version": None,
            "current_policy_id": None,
            "current_policy_pack_version": None,
            "object_store_id": None,
            "object_store_payload": None,
            "object_store_payload_sha256": None,
            "revision": 1,
            "retention_policy_id": _RETENTION_POLICY,
        },
        "principal": {
            "tenant_id": tenant,
            "principal_id": principal,
            "principal_type": "human",
            "status": "active",
            "display_name": None,
            "privacy_class": "restricted",
            "revision": 1,
            "retention_policy_id": _RETENTION_POLICY,
        },
        "evidence": {
            "tenant_id": tenant,
            "installation_id": installation,
            "evidence_id": evidence_id,
            "aggregate_type": evidence.aggregate_type,
            "aggregate_id": evidence.aggregate_id,
            "sequence": 1,
            "idempotency_key": evidence.idempotency_key,
            "producer": evidence.producer,
            "subject_contract": evidence.subject_contract,
            "subject_id": evidence.subject_id,
            "subject_payload_sha256": bytes.fromhex(subject_payload_hash),
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [],
            "privacy_class": "restricted",
            "retention_policy_id": _RETENTION_POLICY,
        },
        "binding": {
            "tenant_id": tenant,
            "principal_binding_id": binding,
            "principal_id": principal,
            "provider_id": "eva-dashboard",
            "channel_id": "owner-local",
            "external_subject_sha256": subject_hash,
            "verification_evidence_id": evidence_id,
            "status": "verified",
            "revision": 1,
            "retention_policy_id": _RETENTION_POLICY,
        },
    }
    try:
        engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn))
    except ValueError as error:
        raise RuntimeError("dashboard authority initialization requires PostgreSQL") from error
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"eva2-dashboard-authority-v1:{tenant}"},
            )
            rows = {
                "tenant": connection.execute(
                    sa.select(_TENANTS).where(_TENANTS.c.tenant_id == tenant)
                ).mappings().one_or_none(),
                "installation": connection.execute(
                    sa.select(_INSTALLATIONS).where(
                        _INSTALLATIONS.c.tenant_id == tenant,
                        _INSTALLATIONS.c.installation_id == installation,
                    )
                ).mappings().one_or_none(),
                "principal": connection.execute(
                    sa.select(_PRINCIPALS).where(
                        _PRINCIPALS.c.tenant_id == tenant,
                        _PRINCIPALS.c.principal_id == principal,
                    )
                ).mappings().one_or_none(),
                "evidence": connection.execute(
                    sa.select(_EVIDENCE).where(
                        _EVIDENCE.c.tenant_id == tenant,
                        _EVIDENCE.c.installation_id == installation,
                        _EVIDENCE.c.evidence_id == evidence_id,
                    )
                ).mappings().one_or_none(),
                "binding": connection.execute(
                    sa.select(_BINDINGS).where(
                        _BINDINGS.c.tenant_id == tenant,
                        _BINDINGS.c.principal_binding_id == binding,
                    )
                ).mappings().one_or_none(),
            }
            present = [name for name, row in rows.items() if row is not None]
            if present and len(present) != len(rows):
                raise RuntimeError("dashboard authority scope is partial")
            if present:
                for name, required in expected.items():
                    row = rows[name]
                    assert row is not None
                    if any(row[key] != value for key, value in required.items()):
                        raise RuntimeError("dashboard authority scope differs")
                stored_evidence = rows["evidence"]
                assert stored_evidence is not None
                try:
                    stored_payload = EvidenceEnvelopeV2.model_validate(
                        stored_evidence["payload"]
                    )
                except ValueError as error:
                    raise RuntimeError("dashboard authority evidence differs") from error
                stable_evidence = evidence.model_dump(mode="json")
                stable_stored = stored_payload.model_dump(mode="json")
                for time_key in ("captured_at", "retention_until"):
                    stable_evidence.pop(time_key)
                    stable_stored.pop(time_key)
                if stable_stored != stable_evidence or stored_evidence[
                    "payload_sha256"
                ] != bytes.fromhex(canonical_sha256(stored_evidence["payload"])):
                    raise RuntimeError("dashboard authority evidence differs")
                status = "already_initialized"
            else:
                common_time = {
                    "retention_until": retention_until,
                    "created_at": captured_at,
                    "updated_at": captured_at,
                }
                connection.execute(
                    sa.insert(_TENANTS).values(
                        **expected["tenant"],
                        retention_until=retention_until,
                        created_at=captured_at,
                        updated_at=captured_at,
                    )
                )
                connection.execute(
                    sa.insert(_INSTALLATIONS).values(
                        **expected["installation"],
                        **common_time,
                    )
                )
                connection.execute(
                    sa.insert(_PRINCIPALS).values(
                        **expected["principal"], **common_time
                    )
                )
                connection.execute(
                    sa.insert(_EVIDENCE).values(
                        **expected["evidence"],
                        captured_at=captured_at,
                        retention_until=retention_until,
                        payload=evidence_payload,
                        payload_sha256=evidence_payload_hash,
                    )
                )
                connection.execute(
                    sa.insert(_BINDINGS).values(
                        **expected["binding"],
                        verified_at=captured_at,
                        **common_time,
                    )
                )
                status = "initialized"
    finally:
        engine.dispose()
    return DashboardAuthorityReceipt(
        binding_id=str(binding),
        evidence_id=str(evidence_id),
        installation_id=str(installation),
        release_sha256=release_hash.hex(),
        status=status,
        subject_sha256=subject_hash.hex(),
        tenant_id=str(tenant),
    )


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="eva-initialize-dashboard-authority").parse_args(argv)
    try:
        if os.environ.get("EVA_DATABASE_DSN", "").strip():
            raise RuntimeError("EVA_DATABASE_DSN is forbidden; use EVA_DATABASE_DSN_FILE")
        database_dsn = read_runtime_secret_file(
            _required_environment("EVA_DATABASE_DSN_FILE"),
            setting_name="EVA_DATABASE_DSN_FILE",
        )
        if os.environ.get("EVA_DASHBOARD_ACCESS_TOKEN", "").strip():
            raise RuntimeError(
                "EVA_DASHBOARD_ACCESS_TOKEN is forbidden; use EVA_DASHBOARD_ACCESS_TOKEN_FILE"
            )
        access_token = read_runtime_secret_file(
            _required_environment("EVA_DASHBOARD_ACCESS_TOKEN_FILE"),
            setting_name="EVA_DASHBOARD_ACCESS_TOKEN_FILE",
        )
        receipt = initialize_dashboard_authority(
            database_dsn,
            tenant_id=_required_environment("EVA_DASHBOARD_TENANT_ID"),
            installation_id=_required_environment("EVA_DASHBOARD_INSTALLATION_ID"),
            principal_id=_required_environment("EVA_RUNTIME_PRINCIPAL_ID"),
            binding_id=_required_environment("EVA_DASHBOARD_PRINCIPAL_BINDING_ID"),
            release_sha256=_required_environment("EVA_DASHBOARD_RELEASE_SHA256"),
            access_token=access_token,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(asdict(receipt), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["DashboardAuthorityReceipt", "initialize_dashboard_authority", "main"]
