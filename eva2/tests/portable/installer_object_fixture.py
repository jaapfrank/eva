"""Synthetic current-format restore fixture; historical sealed vectors stay unchanged."""

import base64
import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from eva.application.portable_archive import canonical_json_bytes
from tools.portable_export import publish_portable_archive
from tests.portable.test_portable_export_end_to_end import _fixture_record_sets


def archive_with_object(root: Path):
    sets, installation, authority = _fixture_record_sets("terminal.correction")
    normalized = []
    for group in sets:
        rows = copy.deepcopy(group.records)
        if group.table in {
            "eva_core.model_attempts",
            "eva_core.connector_bindings",
            "eva_effects.read_attempts",
            "eva_effects.effect_attempts",
        }:
            for record in rows:
                row = record["row"]
                payload = row["payload"]
                for name in tuple(payload):
                    if name in row and name not in {
                        "status",
                        "state",
                        "credential_reference",
                    }:
                        payload[name] = row[name]
                if "verification_evidence_ref" in payload:
                    payload["verification_evidence_ref"] = row[
                        "verification_evidence_id"
                    ]
                for field, prefix in [
                    ("credential_grant_payload", "credential_grant"),
                    ("capability_grant_payload", "capability_grant"),
                ]:
                    grant = row.get(field)
                    if not isinstance(grant, dict):
                        continue
                    for key, column in [
                        ("credential_grant_id", "credential_grant_id"),
                        ("grant_id", "capability_grant_id"),
                        ("nonce", prefix + "_nonce"),
                        ("deadline", prefix + "_deadline"),
                        ("model_provider_profile_id", "provider_profile_id"),
                    ]:
                        if key in grant and column in row:
                            grant[key] = row[column]
                    if prefix == "capability_grant" and "connector_binding_id" in grant:
                        grant["connector_binding_id"] = row.get(
                            "connector_binding_id", grant["connector_binding_id"]
                        )
                if "capability_grant_sha256" in row:
                    row["capability_grant_sha256"] = hashlib.sha256(
                        canonical_json_bytes(row["capability_grant_payload"])
                    ).hexdigest()
                if "payload_sha256" in row:
                    row["payload_sha256"] = hashlib.sha256(
                        canonical_json_bytes(payload)
                    ).hexdigest()
                if "redacted_payload_sha256" in row:
                    row["redacted_payload_sha256"] = hashlib.sha256(
                        canonical_json_bytes(payload)
                    ).hexdigest()
                record["row_sha256"] = hashlib.sha256(
                    canonical_json_bytes(row)
                ).hexdigest()
        for record in rows:
            row = record["row"]
            if (
                group.table == "eva_effects.deliveries"
                and row["role"] == "confirmation_delivery"
            ):
                row["ingress_request_id"] = None
                record["row_sha256"] = hashlib.sha256(
                    canonical_json_bytes(row)
                ).hexdigest()
            if (
                row.get("delete_sla_at")
                and row.get("retention_until")
                and row["delete_sla_at"] > row["retention_until"]
            ):
                row["delete_sla_at"] = row["retention_until"]
                record["row_sha256"] = hashlib.sha256(
                    canonical_json_bytes(row)
                ).hexdigest()
        normalized.append(
            replace(
                group,
                records=rows,
                content=b"".join(canonical_json_bytes(x) + b"\n" for x in rows),
            )
        )
    selected = authority["selectedEncryptedObjects"]["objects"][0]
    digest = selected["ciphertext_sha256"]
    objects = root / "source-objects"
    file = objects / "sha256" / digest[:2] / (digest + ".eva2xo")
    file.parent.mkdir(parents=True)
    file.write_bytes(base64.b64decode(selected["complete_envelope_base64"]))
    archive = root / "object-archive"
    publish_portable_archive(
        record_sets=tuple(normalized),
        source_installation_id=installation,
        export_sequence=1,
        created_at="2026-09-13T00:00:00Z",
        destination=archive,
        object_blob_root=objects,
    )
    return archive
