"""Current runtime recovery without inventing frozen canonical identity records.

The v3 profile retains the existing portable transforms and restoration holds.
Identity signature authority is supplied by the operator, never by the archive.
Nonempty object references are explicitly unsupported in this bounded profile.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from eva.adapters.objects.identity_verifier import verify_identity_pack_bytes
from eva.application.identity_verifier import IdentityPackTrustBinding
from eva.contracts.lifecycle import AttentionWakeV2
from eva.application.portable_archive import (
    canonical_json_bytes, encode_jsonl_records, publish_directory_atomically,
    discard_staging_tree, write_payload_tree,
)
from tools.portable_export import _schema_payloads
from tools.portable_restore import validate_portable_restore_records
from tools.portable_v2_export import CurrentSnapshot, _records
from tools.portable_v2_profile import ALL_SPECS, CANONICAL_EXPORT_SPECS, SCHEMA_HEAD, EXTERNAL_REQUIREMENTS
from tools.portable_v2_validation import validate_current_records, validate_identity_extension
from tools.postgres_portable_export import (
    validate_portable_v1_attention_rows, validate_portable_v1_no_inflight_external_work,
)
from tools.read_eva_portable_archive import (
    _root_descriptor, _scan_regular_files, _read_regular, _load_canonical_object,
    _parse_jsonl, _pk_sort_key, _validate_schema, _validate_payload_hashes,
    _validate_foreign_keys, _validate_reference_rules, _validate_scope_and_heads,
    _validate_object_store_bindings, _validate_reachable_tenant_closure,
)

CONTRACT = "EvaPortableArchive/v3"
IDENTITY_PATH = "runtime/identity.json"
PROFILE = json.loads(json.dumps({
    "contract": "EvaPortableRuntimeProfile/v1", "schema_head": SCHEMA_HEAD,
    "table_specs": ALL_SPECS, "identity_path": IDENTITY_PATH,
    "identity_authority": "externally_supplied_IdentityPackTrustBinding",
    "objects": "nonempty_object_refs_unsupported",
    "activation_policy": "transactional_database_hold_before_runtime_start",
    "external_requirements": EXTERNAL_REQUIREMENTS,
}))
PROFILE_SHA256 = hashlib.sha256(canonical_json_bytes(PROFILE)).hexdigest()
_FIELDS = {"contract", "schema_version", "archive_id", "source_tenant_id",
    "source_installation_id", "created_at", "runtime_identity", "schema_head",
    "profile_sha256", "files", "external_requirements", "target_activation", "export_sequence"}


def _schemas():
    return _schema_payloads(Path(__file__).resolve().parents[1] / "schemas")


def _closure(records, canonical, tenant_id):
    """Reuse legacy closure validation after identifying current-only dependencies."""
    bindings = {r["row"]["principal_binding_id"] for table in
        ("eva_core.ingress_requests", "eva_core.consent_grants") for r in records[table]
        if r["row"].get("principal_binding_id") is not None}
    evidence = {value for r in records["eva_core.relationships"]
                for value in r["row"]["channel_binding_evidence_refs"]}
    bindings.update(r["row"]["principal_binding_id"] for r in records["eva_core.principal_bindings"]
                    if r["row"]["verification_evidence_id"] in evidence)
    principals = {r["row"]["principal_id"] for r in records["eva_core.principal_bindings"]
                  if r["row"]["principal_binding_id"] in bindings}
    for table, column in (
        ("relationships", "principal_id"), ("relationships", "related_principal_id"),
        ("conversations", "owner_principal_id"), ("messages", "principal_id"),
        ("ingress_requests", "principal_id"), ("episodes", "principal_id"),
        ("commitments", "owner_principal_id"), ("consent_grants", "principal_id"),
        ("connector_bindings", "owner_principal_id"),
    ):
        principals.update(r["row"][column] for r in records["eva_core." + table]
                          if r["row"].get(column) is not None)
    principals.update(r["row"]["principal_id"] for r in records["eva_memory.source_events"]
                      if r["row"].get("principal_id") is not None)
    for record in records["eva_core.conversations"]:
        principals.update(record["row"]["participant_principal_ids"])
    base = {"eva_core.principals": principals, "eva_core.principal_bindings": bindings}
    legacy = dict(canonical)
    for table, field in (("eva_core.principals", "principal_id"),
                         ("eva_core.principal_bindings", "principal_binding_id")):
        legacy[table] = [r for r in records[table] if r["row"][field] in base[table]]
    _validate_reachable_tenant_closure(legacy)
    validate_identity_extension(records, base, tenant_id=tenant_id)


def read_runtime_archive(directory: Path, *, identity_trust: IdentityPackTrustBinding):
    """Verify exact bytes, row semantics and externally trusted runtime identity."""
    if not isinstance(identity_trust, IdentityPackTrustBinding):
        raise ValueError("external identity trust binding is required")
    schema_bytes = _schemas()
    expected_files = {"profile.json", IDENTITY_PATH, *schema_bytes,
                      *(s["record_jsonl_relative_path"] for s in ALL_SPECS)}
    with _root_descriptor(directory) as root:
        manifest_raw = _read_regular(root, "archive.json")
        manifest = _load_canonical_object(manifest_raw, label="runtime manifest")
        if (set(manifest) != _FIELDS or manifest["contract"] != CONTRACT
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 3
                or manifest["schema_head"] != SCHEMA_HEAD or manifest["profile_sha256"] != PROFILE_SHA256
                or manifest["external_requirements"] != list(EXTERNAL_REQUIREMENTS)
                or manifest["target_activation"] != "closed"
                or type(manifest["export_sequence"]) is not int or manifest["export_sequence"] < 1):
            raise ValueError("runtime archive profile differs")
        for field in ("archive_id", "source_tenant_id", "source_installation_id"):
            if str(UUID(manifest[field])) != manifest[field]:
                raise ValueError("runtime archive scope is not canonical")
        datetime.strptime(manifest["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        entries = manifest["files"]
        if (not isinstance(entries, dict) or set(entries) != expected_files
                or _scan_regular_files(root) != expected_files | {"archive.json"}):
            raise ValueError("runtime archive file coverage differs")
        contents = {name: _read_regular(root, name) for name in sorted(expected_files)}
        if any(hashlib.sha256(raw).hexdigest() != entries[name] for name, raw in contents.items()):
            raise ValueError("runtime archive file hash differs")
        if (contents["profile.json"] != canonical_json_bytes(PROFILE)
                or any(contents[name] != raw for name, raw in schema_bytes.items())):
            raise ValueError("runtime archive schema authority differs")
        identity_raw = contents[IDENTITY_PATH]
        if len(identity_raw) > 65536:
            raise ValueError("runtime identity exceeds its bound")
        verify_identity_pack_bytes(identity_raw, trust=identity_trust)
        schemas = {name: json.loads(raw) for name, raw in schema_bytes.items()}
        registry = {s["$id"]: s for s in schemas.values() if "$id" in s}
        records = {}
        for spec in ALL_SPECS:
            table = spec["table"]
            rows = _parse_jsonl(contents[spec["record_jsonl_relative_path"]], label=table)
            previous = None
            for record in rows:
                if (set(record) != {"contract", "schema_version", "source_table", "primary_key", "row", "row_sha256"}
                        or record["contract"] != spec["record_contract"]
                        or type(record["schema_version"]) is not int or record["schema_version"] != 1
                        or record["source_table"] != table
                        or set(record["primary_key"]) != set(spec["primary_key"])
                        or any(record["primary_key"][key] != record["row"][key] for key in spec["primary_key"])
                        or hashlib.sha256(canonical_json_bytes(record["row"])).hexdigest() != record["row_sha256"]):
                    raise ValueError("runtime record envelope differs")
                key = _pk_sort_key(record["primary_key"], list(spec["primary_key"]))
                if previous is not None and key <= previous:
                    raise ValueError("runtime primary keys are duplicate or unordered")
                previous = key
                delegated = table == "eva_core.attention_items" and record["row"].get("kind") == "delegated_work"
                if delegated:
                    wake = AttentionWakeV2.model_validate(record["row"]["payload"])
                    if wake.kind != "delegated_work" or any(
                        getattr(wake, key) != record["row"][key]
                        for key in ("kind", "state", "generation", "ordinal", "reason", "not_before", "deadline", "lease_owner", "lease_expires_at")
                    ):
                        raise ValueError("delegated attention payload differs from row")
                elif "record_schema_relative_path" in spec:
                    schema = schemas[spec["record_schema_relative_path"]]
                    _validate_schema(record, schema, registry, schema, path=table)
                _validate_payload_hashes(table, record["row"])
            records[table] = rows
        if records["eva_evidence.object_refs"]:
            raise ValueError("runtime archive nonempty object_refs are unsupported; no objects discarded")
        validate_portable_restore_records(records, table_specs=ALL_SPECS)
        canonical = {s["table"]: records[s["table"]] for s in CANONICAL_EXPORT_SPECS}
        _validate_foreign_keys(canonical)
        _validate_reference_rules(canonical)
        _validate_object_store_bindings(canonical)
        _validate_scope_and_heads(manifest, canonical)
        if canonical["eva_core.tenants"][0]["row"]["tenant_id"] != manifest["source_tenant_id"]:
            raise ValueError("runtime archive tenant provenance differs")
        _closure(records, canonical, manifest["source_tenant_id"])
        ordinary = dict(canonical)
        ordinary["eva_core.attention_items"] = [r for r in canonical["eva_core.attention_items"]
                                                  if r["row"]["kind"] != "delegated_work"]
        validate_portable_v1_attention_rows({"eva_core.attention_items":
            [record["row"] for record in ordinary["eva_core.attention_items"]]})
        validate_portable_v1_no_inflight_external_work(ordinary)
        projections = validate_current_records(records, tenant_id=manifest["source_tenant_id"],
            installation_id=manifest["source_installation_id"], runtime_identity=manifest["runtime_identity"],
            created_at=manifest["created_at"])
        if (_scan_regular_files(root) != expected_files | {"archive.json"}
                or _read_regular(root, "archive.json") != manifest_raw
                or any(_read_regular(root, name) != raw for name, raw in contents.items())):
            raise ValueError("runtime archive changed during validation")
        return {"manifest": manifest, "manifest_raw": manifest_raw,
            "archive_sha256": hashlib.sha256(manifest_raw).hexdigest(), "records": records,
            "projections": projections, "runtime_identity_bytes": identity_raw}


def export_runtime_archive(connection, *, tenant_id, installation_id, runtime_identity,
                           destination: Path, created_at: str, export_sequence: int,
                           runtime_identity_bytes: bytes, identity_trust: IdentityPackTrustBinding,
                           object_blob_root: Path | None = None):
    """Export a read-only snapshot; never publish bytes rejected by the reader.

    The caller supplies the actual mounted identity bytes and trusted binding.
    Cross-store quiescence and external component capture remain operator duties.
    """
    verify_identity_pack_bytes(runtime_identity_bytes, trust=identity_trust)
    with connection.begin():
        revision = connection.exec_driver_sql("SELECT version_num FROM public.eva_alembic_version").scalar_one()
        if revision != SCHEMA_HEAD:
            raise ValueError("runtime portable source schema head differs")
    with CurrentSnapshot(connection, tenant_id=tenant_id, installation_id=installation_id,
                         runtime_identity=runtime_identity) as snapshot:
        raw = snapshot.read_all()
        if raw["eva_evidence.object_refs"]:
            raise ValueError("runtime archive nonempty object_refs are unsupported; no objects discarded")
        records = {s["table"]: _records(s, raw[s["table"]]) for s in ALL_SPECS}
    payloads = {s["record_jsonl_relative_path"]: encode_jsonl_records(records[s["table"]],
                    primary_key_columns=s["primary_key"]) for s in ALL_SPECS}
    payloads.update(_schemas())
    payloads.update({"profile.json": canonical_json_bytes(PROFILE), IDENTITY_PATH: runtime_identity_bytes})
    manifest = {"contract": CONTRACT, "schema_version": 3, "archive_id": str(uuid4()),
        "source_tenant_id": tenant_id, "source_installation_id": installation_id,
        "created_at": created_at, "runtime_identity": runtime_identity, "schema_head": SCHEMA_HEAD,
        "profile_sha256": PROFILE_SHA256, "export_sequence": export_sequence,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()},
        "external_requirements": list(EXTERNAL_REQUIREMENTS), "target_activation": "closed"}
    payloads["archive.json"] = canonical_json_bytes(manifest)
    destination = destination.absolute()
    staging = destination.parent / ("." + destination.name + ".staging-" + uuid4().hex)
    try:
        write_payload_tree(staging, payloads)
        publish_directory_atomically(staging=staging, destination=destination,
            validator=lambda path: read_runtime_archive(path, identity_trust=identity_trust))
        return manifest
    except BaseException:
        discard_staging_tree(staging)
        raise
