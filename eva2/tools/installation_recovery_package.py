"""Bind staged recovery components using the existing portable transport.

This is a content envelope, not snapshot coordination, restore permission or
runtime activation. Source labels are inventory supplied by the trusted caller,
not evidence that a process was stopped. Secret values stay separately
provisioned. File modes describe later role-bound materialization; this module
does not chmod or restore any component.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from uuid import UUID, uuid4

from eva.application.portable_archive import canonical_json_bytes
from tools.read_eva_portable_archive import (
    _root_descriptor, _scan_regular_files, _read_regular, _load_canonical_object,
)
from tools.portable_v2_reader import read_current_archive
from tools.portable_transport import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES

CONTRACT = "EvaInstallationRecovery/v1"
IDENTITY_FIELDS = ("created_at", "source_tenant_id", "source_installation_id", "runtime_identity")
ROLES = {
    "document_artifact": ("documents/", "0600"),
    "document_journal": ("documents/", "0600"),
    "document_marker": ("documents/", "0400"),
    "provider_binding": ("config/", "0400"),
    "provider_catalog": ("config/", "0400"),
    "document_pool": ("config/", "0400"),
    "document_plan": ("config/", "0400"),
    "owner_mandate": ("config/", "0400"),
    "channel_bindings": ("config/", "0400"),
}
FIXED = {"memory/hindsight.dump": "memory_dump", "gateway/contextforge.sqlite": "gateway_database"}
FIELDS = {"contract", "schema_version", "archive_id", *IDENTITY_FIELDS,
          "core_archive_sha256", "source_components", "key_references", "files"}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _label(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None


def _private_root(fd):
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("installation package root must be private and owned by the operator")


def _metadata(source_components, key_references):
    if not isinstance(source_components, dict) or set(source_components) != {"memory", "gateway", "documents"}:
        raise ValueError("installation component inventory differs")
    for kind, entries in source_components.items():
        if not isinstance(entries, list) or not entries or (kind != "documents" and len(entries) != 1):
            raise ValueError("installation component inventory is empty or ambiguous")
        names = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"name", "image_sha256"}
                    or not _label(entry["name"]) or not _digest(entry["image_sha256"])
                    or entry["name"] in names):
                raise ValueError("installation component identity is invalid")
            names.add(entry["name"])
    required = {"gateway_encryption", "gateway_jwt", "gateway_access"}
    if (not isinstance(key_references, dict) or not required <= set(key_references)
            or not set(key_references) <= required | {"object_keyring"}
            or any(not _label(value) for value in key_references.values())):
        raise ValueError("installation requires separate key reference labels")


def _role(name, requested=None):
    if name.startswith("core/"):
        if requested not in (None, "core_archive"):
            raise ValueError("core archive role differs")
        return "core_archive", "0600"
    if name in FIXED:
        if requested not in (None, FIXED[name]):
            raise ValueError("fixed component role differs")
        return FIXED[name], "0600"
    if requested not in ROLES or not name.startswith(ROLES[requested][0]):
        raise ValueError("installation file role or layout differs")
    return requested, ROLES[requested][1]


def _files(fd):
    names = _scan_regular_files(fd)
    if len(names) > MAX_ARCHIVE_FILES or not set(FIXED) | {"core/archive.json"} <= names:
        raise ValueError("installation package components are missing or oversized")
    return names


def _entries(fd, names, roles):
    result, total = {}, 0
    for name in sorted(names):
        role, mode = _role(name, roles.get(name))
        raw = _read_regular(fd, name, maximum=MAX_ARCHIVE_BYTES)
        total += len(raw)
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("installation package exceeds transport byte limit")
        result[name] = {"sha256": _sha(raw), "size": len(raw), "role": role, "mode": mode}
    return result


def _core(fd):
    # The parent descriptor stays held. The existing validator refuses links in
    # the final core directory and validates all nested canonical/current files.
    return read_current_archive(Path(f"/proc/self/fd/{fd}/core"))


def _validate(fd, raw):
    manifest = _load_canonical_object(raw, label="installation recovery manifest")
    if (set(manifest) != FIELDS or manifest["contract"] != CONTRACT
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or str(UUID(manifest["archive_id"])) != manifest["archive_id"]):
        raise ValueError("installation recovery manifest differs")
    _metadata(manifest["source_components"], manifest["key_references"])
    names = _files(fd)
    entries = manifest["files"]
    if not isinstance(entries, dict) or set(entries) != names - {"archive.json"}:
        raise ValueError("installation file inventory differs")
    roles = {}
    for name, entry in entries.items():
        if (not isinstance(entry, dict) or set(entry) != {"sha256", "size", "role", "mode"}
                or not _digest(entry["sha256"]) or type(entry["size"]) is not int
                or entry["size"] < 0):
            raise ValueError("installation file entry is invalid")
        role, mode = _role(name, entry["role"])
        if (entry["role"], entry["mode"]) != (role, mode):
            raise ValueError("installation file materialization mode differs")
        roles[name] = role
    if _entries(fd, set(entries), roles) != entries:
        raise ValueError("installation component bytes differ")
    core = _core(fd)
    if (manifest["core_archive_sha256"] != core["archive_sha256"]
            or any(manifest[key] != core["manifest"][key] for key in IDENTITY_FIELDS)):
        raise ValueError("installation and core archive provenance differ")
    # Nested validation and a second full hash pass must see the same bytes.
    if _entries(fd, set(entries), roles) != entries or _files(fd) != names:
        raise ValueError("installation package changed during validation")
    if _read_regular(fd, "archive.json", maximum=16777216) != raw:
        raise ValueError("installation manifest changed during validation")
    return {"manifest": manifest, "manifest_raw": raw, "archive_sha256": _sha(raw),
            "core_archive": core}


def read_installation_package(root: Path):
    """Validate an exact inventory; do not infer authorization or quiescence."""
    with _root_descriptor(root) as fd:
        _private_root(fd)
        return _validate(fd, _read_regular(fd, "archive.json", maximum=16777216))


def write_installation_package(root: Path, *, file_roles, source_components, key_references):
    """Seal already staged components with a new manifest, without overwriting.

    file_roles maps each documents/ or config/ file to one role in ROLES.
    Fixed memory/gateway and all core files have implicit roles. Original native
    bindings/journals stay byte-identical; the trusted caller stages them first.
    """
    _metadata(source_components, key_references)
    with _root_descriptor(root) as fd:
        _private_root(fd)
        names = _files(fd)
        if "archive.json" in names:
            raise ValueError("installation manifest already exists")
        additional = names - {name for name in names if name.startswith("core/")} - set(FIXED)
        if not isinstance(file_roles, dict) or set(file_roles) != additional:
            raise ValueError("installation file roles must cover all additional files")
        entries = _entries(fd, names, file_roles)
        core = _core(fd)
        if _entries(fd, names, file_roles) != entries or _files(fd) != names:
            raise ValueError("installation components changed during preparation")
        manifest = {"contract": CONTRACT, "schema_version": 1, "archive_id": str(uuid4()),
                    **{key: core["manifest"][key] for key in IDENTITY_FIELDS},
                    "core_archive_sha256": core["archive_sha256"],
                    "source_components": source_components, "key_references": key_references,
                    "files": entries}
        raw = canonical_json_bytes(manifest)
        if len(raw) > 16777216:
            raise ValueError("installation manifest exceeds byte limit")
        out = os.open("archive.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        return _validate(fd, raw)
