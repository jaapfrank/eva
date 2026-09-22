"""Strict reader for the current-work envelope around a frozen v1 archive."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID
from datetime import datetime

from eva.application.portable_archive import canonical_json_bytes
from tools.read_eva_portable_archive import (
    _root_descriptor, _scan_regular_files, _read_regular, _load_canonical_object,
    _validate_and_derive_open,
)
from tools.portable_v2_profile import (
    CONTRACT, SCHEMA_HEAD, PROFILE, PROFILE_SHA256, EXTERNAL_REQUIREMENTS,
    CURRENT_SPECS, ATTENTION_SPEC, CANONICAL_EXPORT_SPECS, DEPENDENCY_SPECS,
)
from tools.portable_v2_validation import validate_current_records, validate_identity_extension


def _record_lines(raw):
    if raw and not raw.endswith(b"\n"):
        raise ValueError("current record set lacks canonical final newline")
    return [_load_canonical_object(line, label="current record") for line in raw.splitlines()]


def read_current_archive(directory: Path):
    with _root_descriptor(directory) as root:
        actual_files = _scan_regular_files(root)
        manifest_raw = _read_regular(root, "archive.json", maximum=1048576)
        manifest = _load_canonical_object(manifest_raw, label="current manifest")
        if set(manifest) != {"contract", "schema_version", "archive_id", "source_tenant_id",
                "source_installation_id", "created_at", "runtime_identity", "schema_head",
                "profile_sha256", "files", "external_requirements", "target_activation"}:
            raise ValueError("current archive manifest fields differ")
        if ((manifest["contract"], manifest["schema_version"]) != (CONTRACT, 2)
                or manifest["schema_head"] != SCHEMA_HEAD
                or manifest["profile_sha256"] != PROFILE_SHA256
                or manifest["external_requirements"] != list(EXTERNAL_REQUIREMENTS)
                or manifest["target_activation"] != "closed"):
            raise ValueError("current archive profile differs")
        for key in ("archive_id", "source_tenant_id", "source_installation_id"):
            if str(UUID(manifest[key])) != manifest[key]:
                raise ValueError("current archive identity is not canonical")
        datetime.strptime(manifest["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        entries = manifest["files"]
        if not isinstance(entries, dict) or set(entries) != actual_files - {"archive.json"}:
            raise ValueError("current archive file coverage differs")
        contents = {}
        for name, digest in entries.items():
            content = _read_regular(root, name)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("current archive file hash differs")
            contents[name] = content
        supplemental = {"current/profile.json", "current/attention_items.jsonl",
                        *(spec["record_jsonl_relative_path"] for spec in CURRENT_SPECS),
                        *("current/" + spec["table"].split(".")[1] + ".jsonl" for spec in DEPENDENCY_SPECS)}
        if {name for name in entries if not name.startswith("canonical/")} != supplemental:
            raise ValueError("current archive supplemental layout differs")
        if contents["current/profile.json"] != canonical_json_bytes(PROFILE):
            raise ValueError("current archive schema profile differs")
        child = os.open("canonical", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            outputs, base_proof = _validate_and_derive_open(child)
        finally:
            os.close(child)
        base_manifest = _load_canonical_object(contents["canonical/archive.json"], label="canonical manifest")
        if (base_manifest["source_installation_id"] != manifest["source_installation_id"]
                or base_manifest["created_at"] != manifest["created_at"]):
            raise ValueError("canonical archive provenance differs")
        records = {spec["table"]: _record_lines(contents["canonical/" + spec["record_jsonl_relative_path"]])
                   for spec in CANONICAL_EXPORT_SPECS}
        installations = records["eva_core.installations"]
        if len(installations) != 1 or installations[0]["row"]["tenant_id"] != manifest["source_tenant_id"]:
            raise ValueError("canonical tenant provenance differs")
        base_identity = {spec["table"]: {row["row"]["principal_id" if spec["table"] == "eva_core.principals" else "principal_binding_id"] for row in records[spec["table"]]} for spec in DEPENDENCY_SPECS}
        for spec in DEPENDENCY_SPECS:
            extra = _record_lines(contents["current/" + spec["table"].split(".")[1] + ".jsonl"])
            existing = {tuple(row["row"][key] for key in spec["primary_key"]) for row in records[spec["table"]]}
            for row in extra:
                key = tuple(row["row"][key] for key in spec["primary_key"])
                if key in existing:
                    raise ValueError("duplicate current identity dependency")
                existing.add(key)
            records[spec["table"]].extend(extra)
        extra_attention = _record_lines(contents["current/attention_items.jsonl"])
        if any(item["row"].get("kind") != "delegated_work" for item in extra_attention):
            raise ValueError("supplemental attention kind differs")
        records[ATTENTION_SPEC["table"]].extend(extra_attention)
        for spec in CURRENT_SPECS:
            records[spec["table"]] = _record_lines(contents[spec["record_jsonl_relative_path"]])
        projections = validate_current_records(records,
            tenant_id=manifest["source_tenant_id"], installation_id=manifest["source_installation_id"],
            runtime_identity=manifest["runtime_identity"], created_at=manifest["created_at"])
        validate_identity_extension(records, base_identity, tenant_id=manifest["source_tenant_id"])
        # Re-read bound bytes after nested validation to reject concurrent edits.
        for name, content in contents.items():
            if _read_regular(root, name) != content:
                raise ValueError("current archive changed during validation")
        if _read_regular(root, "archive.json") != manifest_raw:
            raise ValueError("current manifest changed during validation")
        return {"manifest": manifest, "manifest_raw": manifest_raw, "records": records,
                "projections": projections, "canonical_outputs": outputs,
                "archive_sha256": hashlib.sha256(manifest_raw).hexdigest()}
