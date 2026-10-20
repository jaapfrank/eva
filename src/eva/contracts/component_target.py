"""Pure validation of host-resolved saved-artifact identity and hash bindings."""

from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re

_SAVED_TARGET_KEYS = {"kind", "procedure_reference_id", "scope", "artifact_path",
    "content_sha256", "receipt_sha256", "source_call_id", "source_message_id",
    "tool_configuration_sha256", "retention_until"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_saved_artifact_target(target, *, relative_path=None, baseline_sha256=None):
    """Validate a host-resolved source manifest; this does not grant source access.

    Current authority, source availability and retention are checked by the
    existing procedure resolver before dispatch. No executable path or suite is
    taken from this manifest: its only runner path is derived from the reference.
    """
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    if (not isinstance(target, dict)
            or set(target) not in (_SAVED_TARGET_KEYS, _SAVED_TARGET_KEYS | {"skill_package_binding"})
            or target.get("kind") != "saved_artifact"):
        raise ValueError("saved artifact target fields differ")
    scope = target["scope"]
    if (not isinstance(scope, dict) or set(scope) != {
            "tenant_id", "installation_id", "principal_id", "project_id"}
            or any(not isinstance(scope[key], str) or not scope[key].strip()
                   or len(scope[key]) > 256 or "\x00" in scope[key]
                   for key in ("tenant_id", "installation_id", "principal_id"))
            or (scope["project_id"] is not None and (
                not isinstance(scope["project_id"], str) or not scope["project_id"].strip()
                or len(scope["project_id"]) > 200 or "\x00" in scope["project_id"]))):
        raise ValueError("saved artifact source scope is invalid")
    for key in ("content_sha256", "receipt_sha256", "tool_configuration_sha256"):
        if not isinstance(target[key], str) or not _SHA256.fullmatch(target[key]):
            raise ValueError("saved artifact source hash is invalid")
    for key in ("source_call_id", "source_message_id"):
        if (not isinstance(target[key], str) or not target[key].strip()
                or len(target[key]) > 1024 or "\x00" in target[key]):
            raise ValueError("saved artifact source identity is invalid")
    artifact = target["artifact_path"]
    if not isinstance(artifact, str):
        raise ValueError("saved artifact path is invalid")
    path = PurePosixPath(artifact)
    package_binding = target.get("skill_package_binding")
    if "skill_package_binding" in target:
        _validate_skill_binding(package_binding, resource_sha256=target["content_sha256"])
    if (not path.is_absolute() or str(path) != artifact or len(artifact) > 4096
            or (not artifact.endswith(".skill.json") if package_binding is not None else path.suffix != ".py")
            or any(part in {".", ".."} for part in path.parts)
            or "\x00" in artifact):
        raise ValueError("saved artifact path is invalid")
    retention = target["retention_until"]
    if not isinstance(retention, str) or len(retention) > 64:
        raise ValueError("saved artifact retention is invalid")
    try:
        until = datetime.fromisoformat(retention.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("saved artifact retention is invalid") from None
    if until.tzinfo is None or until.utcoffset() is None:
        raise ValueError("saved artifact retention is unbound")
    reference_hash = canonical_sha256({"scope": scope, "receipt": target["receipt_sha256"]})
    if target["procedure_reference_id"] != "procedure-reference:" + reference_hash:
        raise ValueError("saved artifact reference scope and receipt binding differs")
    if relative_path is not None and relative_path != saved_artifact_virtual_path(target):
        raise ValueError("saved artifact virtual path differs")
    if baseline_sha256 is not None and target["content_sha256"] != baseline_sha256:
        raise ValueError("saved artifact baseline manifest hash differs")
    return json.loads(canonical_json_bytes(target))


def saved_artifact_virtual_path(target):
    identity = target["procedure_reference_id"].removeprefix("procedure-reference:")
    package = target.get("skill_package_binding")
    suffix = ("_" + hashlib.sha256(package["resource_path"].encode("utf-8")).hexdigest()
              if package is not None else "")
    return "saved_artifact_" + identity + suffix + ".py"


def _resource_path(value):
    if (not isinstance(value, str) or not value or len(value) > 240 or "\\" in value
            or ":" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid saved package resource path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or ".." in path.parts:
        raise ValueError("invalid saved package resource path")
    return value


def _validate_skill_binding(binding, *, resource_sha256):
    from eva.contracts.canonical_json import canonical_json_bytes
    if (not isinstance(binding, dict) or set(binding) != {
            "package_content_sha256", "package_manifest", "resource_path"}
            or not isinstance(binding["package_content_sha256"], str)
            or not _SHA256.fullmatch(binding["package_content_sha256"])):
        raise ValueError("saved skill package binding differs")
    selected = _resource_path(binding["resource_path"])
    if not selected.endswith(".py"):
        raise ValueError("saved skill improvement requires a Python resource")
    manifest = binding["package_manifest"]
    if (not isinstance(manifest, dict)
            or set(manifest) not in ({"contract", "origin", "files"},
                                     {"contract", "origin", "files", "derivation"})
            or manifest.get("contract") != "EvaAgentSkillPackage/v1"):
        raise ValueError("saved skill manifest differs")
    origin = manifest["origin"]
    if (not isinstance(origin, dict) or set(origin) != {
            "repository_url", "revision", "subdirectory", "license_id", "license_path"}
            or any(not isinstance(value, str) or len(value) > 2048 or "\x00" in value
                   for value in origin.values())
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", origin["revision"])):
        raise ValueError("saved skill origin is unbound")
    license_path = _resource_path(origin["license_path"])
    files = manifest["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 128:
        raise ValueError("saved skill resource count differs")
    paths = set();total = 0;selected_entry = None
    for entry in files:
        if (not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}
                or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str) or not _SHA256.fullmatch(entry["sha256"])):
            raise ValueError("saved skill resource manifest differs")
        name = _resource_path(entry["path"])
        if name.casefold() in paths:
            raise ValueError("saved skill resources are ambiguous")
        paths.add(name.casefold());total += entry["bytes"]
        if name == selected:selected_entry = entry
    if (total > 262144 or selected_entry is None or selected_entry["sha256"] != resource_sha256
            or license_path == selected or license_path.casefold() not in paths or "skill.md" not in paths):
        raise ValueError("saved skill resource baseline or license binding differs")
    if "derivation" in manifest:
        data = manifest["derivation"]
        if (not isinstance(data, dict) or set(data) != {
                "parent_package_sha256", "resource_path", "baseline_resource_sha256"}
                or any(not isinstance(data[key], str) or not _SHA256.fullmatch(data[key])
                       for key in ("parent_package_sha256", "baseline_resource_sha256"))):
            raise ValueError("saved skill derivation binding differs")
        _resource_path(data["resource_path"])
    if len(canonical_json_bytes(manifest)) > 65536:
        raise ValueError("saved skill manifest exceeds bound")


__all__ = ["validate_saved_artifact_target", "saved_artifact_virtual_path"]
