"""Declared private capability metadata, never live health or dispatch authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

_MAX_BYTES = 8192
_CLASSIFICATION = "declared_permitted_not_live_health_or_execution_permission"


def build_private_capability_context(policy, mandate_sha, *, observed_at, descriptors) -> dict:
    """Project existing descriptions first; parameters fit atomically or stay omitted.

    The caller supplies a host-validated mandate. Only allowlisted public metadata
    is projected. Native execute_schema takes precedence over foreground schemas.
    """
    if not isinstance(mandate_sha, str) or re.fullmatch(r"[0-9a-f]{64}", mandate_sha) is None:
        raise ValueError("private capability context requires a mandate digest")
    if isinstance(observed_at, str):
        observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if (not isinstance(observed_at, datetime) or observed_at.utcoffset() is None
            or observed_at.microsecond):
        raise ValueError("private capability context requires whole-second aware time")
    if not isinstance(policy, dict) or not isinstance(descriptors, dict):
        raise ValueError("private capability context requires host mappings")
    native = {}
    if policy.get("enabled") is True:
        for cap in policy.get("capabilities", []):
            name = cap.get("action_name")
            if not isinstance(name, str) or not name.strip() or name in native:
                raise ValueError("private native capability names must be unique")
            native[name] = cap
    digital = policy.get("digital_work") if policy.get("enabled") is True else None
    digital_names = (set(digital.get("capability_ids", []))
        if isinstance(digital, dict) and digital.get("enabled", True) is True else set())
    if any(not isinstance(name, str) or not name.strip() for name in digital_names):
        raise ValueError("private digital capability names are invalid")
    rows, schemas, descriptions = [], {}, {}
    for name in sorted(set(native) | digital_names):
        cap = native.get(name)
        descriptor = descriptors.get(name) or (descriptors.get(cap.get("tool_name")) if cap else None) or {}
        if not isinstance(descriptor, dict):
            raise ValueError("private capability descriptor is invalid")
        description = descriptor.get("description", "")
        schema = cap.get("execute_schema") if cap else descriptor.get("parameters")
        if not isinstance(description, str) or (schema is not None and not isinstance(schema, dict)):
            raise ValueError("private capability metadata is invalid")
        row = {"name": name, "routes": (["native"] if cap else []) +
               (["digital_work"] if name in digital_names else []),
               "description": description, "parameters_included": False,
               "definition_sha256": canonical_sha256({"description": description, "parameters": schema})}
        if cap:
            row.update({key: deepcopy(cap.get(key)) for key in (
                "data_scope", "communication_scope", "new_cost_eur_cents", "new_obligation")})
        else:
            row["cost_scope"] = "per_task_grant_unknown_not_free"
        rows.append(row)
        schemas[name] = deepcopy(schema)
        descriptions[name] = description
    result = {"contract": "PrivateCapabilityContext/v1", "source_mandate_sha256": mandate_sha,
        "observed_at": observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "classification": _CLASSIFICATION, "is_execution_permission": False,
        "capabilities": rows, "omitted_schema_count": len(rows), "omitted_description_count": 0}

    def seal():
        result["omitted_description_count"] = sum(
            not descriptions[row["name"]] or row["description"] != descriptions[row["name"]]
            for row in rows)
        result["configuration_sha256"] = canonical_sha256({key: value for key, value in result.items()
            if key not in {"observed_at", "configuration_sha256"}})
        return len(canonical_json_bytes(result))

    # Every description precedes optional schemas. Digest binds the full original
    # description and selected schema even when either detail cannot fit inline.
    def description_size(limit):
        for row in rows:
            row["description"] = descriptions[row["name"]].encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        return seal()

    # Names, scope and definition hashes are mandatory; descriptions are not.
    # Long real tool names can exceed the envelope even at the former 32-byte
    # floor. Keep the largest common description budget that actually fits.
    if description_size(0) > _MAX_BYTES:
        raise ValueError("private capability name and scope metadata exceeds its bound")
    low, high = 0, 256
    while low < high:
        limit = (low + high + 1) // 2
        if description_size(limit) <= _MAX_BYTES:
            low = limit
        else:
            high = limit - 1
    description_size(low)
    for row in rows:
        schema = schemas[row["name"]]
        if schema is None:
            continue
        row["parameters"] = schema
        row["parameters_included"] = True
        result["omitted_schema_count"] -= 1
        if seal() > _MAX_BYTES:
            row.pop("parameters")
            row["parameters_included"] = False
            result["omitted_schema_count"] += 1
            seal()
    assert seal() <= _MAX_BYTES
    return result
