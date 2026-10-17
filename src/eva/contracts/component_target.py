"""Pure validation of host-resolved saved-artifact identity and hash bindings."""

from datetime import datetime
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
    if (not isinstance(target, dict) or set(target) != _SAVED_TARGET_KEYS
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
    if (not path.is_absolute() or str(path) != artifact or len(artifact) > 4096
            or path.suffix != ".py" or any(part in {".", ".."} for part in path.parts)
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
    if relative_path is not None and relative_path != "saved_artifact_" + reference_hash + ".py":
        raise ValueError("saved artifact virtual path differs")
    if baseline_sha256 is not None and target["content_sha256"] != baseline_sha256:
        raise ValueError("saved artifact baseline manifest hash differs")
    return json.loads(canonical_json_bytes(target))


__all__ = ["validate_saved_artifact_target"]
