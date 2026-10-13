#!/usr/bin/env python3
"""Validate Eva's semantic component catalog against the path inventory."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any


CONTRACT = "EvaComponentCatalog/v1"
INVENTORY_CONTRACT = "EvaRepositoryInventory/v1"
MANAGED_TOOL_REGISTRY_CONTRACT = "EvaManagedEvidenceToolRegistry/v1"
MAX_JSON_BYTES = 4 * 1024 * 1024
SCOPE_PREFIXES = (
    "src/eva/",
    "connector_sdk/",
    "connectors/",
    "deploy/",
    "migrations/",
    "tests/",
)
LIFECYCLE_STATUSES = {
    "planned",
    "implemented",
    "tested",
    "reviewed",
    "live_proven",
    "user_accepted",
}
DISPOSITIONS = {
    "unique_core",
    "dependency_adapter",
    "operational",
    "test",
    "replacement_pending",
}
UPSTREAM_STATUSES = {
    "adopted",
    "candidate",
    "no_suitable_upstream",
    "not_applicable",
}
REPLACEMENT_STATUSES = {"retained", "evaluating", "replacement_pending"}
TOP_LEVEL_FIELDS = {
    "components",
    "contract",
    "inventory",
    "managedToolRegistry",
    "schemaVersion",
    "scopePrefixes",
    "status",
}
INVENTORY_FIELDS = {"contract", "path"}
MANAGED_TOOL_BINDING_FIELDS = {"contract", "path", "requiredTools"}
MANAGED_TOOL_REGISTRY_FIELDS = {"contract", "schemaVersion", "status", "tools"}
MANAGED_TOOL_FIELDS = {
    "invocationPolicy",
    "lifecycleStatus",
    "owner",
    "path",
    "purpose",
    "riskClass",
    "sideEffects",
    "sourceSha256",
    "verification",
}
COMPONENT_FIELDS = {
    "blockers",
    "disposition",
    "forbiddenScope",
    "id",
    "lifecycleStatus",
    "name",
    "necessity",
    "ownerPaths",
    "purpose",
    "replacement",
    "upstream",
    "verification",
}
UPSTREAM_FIELDS = {"name", "reviewedOn", "status", "url", "version"}
REPLACEMENT_FIELDS = {"reason", "status", "target"}
VERIFICATION_FIELDS = {"evidencePaths", "testPaths"}
COMPONENT_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
DATE = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MANAGED_TOOL_RISK_POLICIES = {
    "repository_read_only": "ci_or_read_only_operator",
    "live_read_only": "explicit_read_only_operator",
    "isolated_development_write": "isolated_disposable_only",
    "external_migration_write": "quarantined_explicit_operator_only",
}
MANAGED_TOOL_LIFECYCLE_STATUSES = {"managed", "quarantined"}


@dataclass(frozen=True, slots=True, order=True)
class Violation:
    code: str
    path: str
    detail: str


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise RuntimeError(f"{label} must be a regular non-symlink file")
    if metadata.st_size > MAX_JSON_BYTES:
        raise RuntimeError(f"{label} exceeds the byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _closed_fields(
    value: object,
    expected: set[str],
    *,
    code: str,
    path: str,
    violations: list[Violation],
) -> bool:
    if not isinstance(value, dict):
        violations.append(Violation(code, path, "must be an object"))
        return False
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        violations.append(
            Violation(code, path, f"missing={missing!r}; extra={extra!r}")
        )
        return False
    return True


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _nullable_string(value: object) -> bool:
    return value is None or _nonempty_string(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_file(root: Path, value: object, *, prefix: str) -> Path | None:
    if not _nonempty_string(value) or "\\" in value:
        return None
    assert isinstance(value, str)
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not value.startswith(prefix)
    ):
        return None
    path = root.joinpath(*relative.parts)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if (
        resolved != path
        or not resolved.is_relative_to(root)
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
    ):
        return None
    return path


def _check_managed_tool_registry(
    root: Path,
    binding: object,
    registry: dict[str, Any],
    violations: list[Violation],
) -> None:
    required_tools: list[str] = []
    if _closed_fields(
        binding,
        MANAGED_TOOL_BINDING_FIELDS,
        code="managed_tool_binding_fields",
        path="$.managedToolRegistry",
        violations=violations,
    ):
        assert isinstance(binding, dict)
        if binding["contract"] != MANAGED_TOOL_REGISTRY_CONTRACT:
            violations.append(
                Violation(
                    "managed_tool_binding_contract",
                    "$.managedToolRegistry.contract",
                    "invalid",
                )
            )
        if binding["path"] != (
            "design/architecture/evidence-tool-registry-v1.json"
        ):
            violations.append(
                Violation(
                    "managed_tool_binding_path",
                    "$.managedToolRegistry.path",
                    "invalid",
                )
            )
        raw_required_tools = binding["requiredTools"]
        if not _string_list(raw_required_tools, allow_empty=False):
            violations.append(
                Violation(
                    "managed_tool_required_paths",
                    "$.managedToolRegistry.requiredTools",
                    "invalid",
                )
            )
        else:
            required_tools = list(raw_required_tools)
            for tool_path in required_tools:
                if _repository_file(root, tool_path, prefix="tools/") is None:
                    violations.append(
                        Violation(
                            "managed_tool_required_path",
                            "$.managedToolRegistry.requiredTools",
                            tool_path,
                        )
                    )

    if not _closed_fields(
        registry,
        MANAGED_TOOL_REGISTRY_FIELDS,
        code="managed_tool_registry_fields",
        path="toolRegistry",
        violations=violations,
    ):
        return
    if registry["contract"] != MANAGED_TOOL_REGISTRY_CONTRACT:
        violations.append(
            Violation(
                "managed_tool_registry_contract", "toolRegistry.contract", "invalid"
            )
        )
    if registry["schemaVersion"] != 1:
        violations.append(
            Violation(
                "managed_tool_registry_schema", "toolRegistry.schemaVersion", "invalid"
            )
        )
    if registry["status"] != "implemented":
        violations.append(
            Violation("managed_tool_registry_status", "toolRegistry.status", "invalid")
        )
    raw_tools = registry["tools"]
    if not isinstance(raw_tools, list) or not raw_tools:
        violations.append(
            Violation("managed_tools", "toolRegistry.tools", "must be non-empty")
        )
        return

    registered_paths: list[str] = []
    for index, raw_tool in enumerate(raw_tools):
        location = f"toolRegistry.tools[{index}]"
        if not _closed_fields(
            raw_tool,
            MANAGED_TOOL_FIELDS,
            code="managed_tool_fields",
            path=location,
            violations=violations,
        ):
            continue
        assert isinstance(raw_tool, dict)
        tool_path = raw_tool["path"]
        path = _repository_file(root, tool_path, prefix="tools/")
        if path is None:
            violations.append(Violation("managed_tool_path", location, str(tool_path)))
        else:
            assert isinstance(tool_path, str)
            registered_paths.append(tool_path)
            source_sha256 = raw_tool["sourceSha256"]
            if (
                not isinstance(source_sha256, str)
                or SHA256.fullmatch(source_sha256) is None
                or _sha256(path) != source_sha256
            ):
                violations.append(
                    Violation("managed_tool_hash", tool_path, "source hash differs")
                )
        for field in ("owner", "purpose"):
            if not _nonempty_string(raw_tool[field]):
                violations.append(
                    Violation("managed_tool_text", f"{location}.{field}", "invalid")
                )
        if not _string_list(raw_tool["sideEffects"], allow_empty=False):
            violations.append(
                Violation(
                    "managed_tool_side_effects",
                    f"{location}.sideEffects",
                    "invalid",
                )
            )
        risk = raw_tool["riskClass"]
        expected_policy = MANAGED_TOOL_RISK_POLICIES.get(risk)
        if expected_policy is None or raw_tool["invocationPolicy"] != expected_policy:
            violations.append(
                Violation("managed_tool_risk_policy", location, "risk/policy mismatch")
            )
        lifecycle = raw_tool["lifecycleStatus"]
        if lifecycle not in MANAGED_TOOL_LIFECYCLE_STATUSES:
            violations.append(Violation("managed_tool_lifecycle", location, "invalid"))
        if risk == "external_migration_write" and lifecycle != "quarantined":
            violations.append(
                Violation(
                    "managed_tool_quarantine",
                    location,
                    "external migration write must remain quarantined",
                )
            )

        verification = raw_tool["verification"]
        if _closed_fields(
            verification,
            VERIFICATION_FIELDS,
            code="managed_tool_verification_fields",
            path=f"{location}.verification",
            violations=violations,
        ):
            assert isinstance(verification, dict)
            test_paths = verification["testPaths"]
            evidence_paths = verification["evidencePaths"]
            if not _string_list(test_paths, allow_empty=False):
                violations.append(
                    Violation(
                        "managed_tool_tests",
                        f"{location}.verification.testPaths",
                        "invalid",
                    )
                )
            else:
                for test_path in test_paths:
                    if _repository_file(root, test_path, prefix="tests/") is None:
                        violations.append(
                            Violation(
                                "managed_tool_test_path", str(tool_path), test_path
                            )
                        )
            if not _string_list(evidence_paths, allow_empty=True):
                violations.append(
                    Violation(
                        "managed_tool_evidence",
                        f"{location}.verification.evidencePaths",
                        "invalid",
                    )
                )

    if len(registered_paths) != len(set(registered_paths)):
        violations.append(
            Violation("managed_tool_duplicate", "toolRegistry.tools", "duplicate path")
        )
    missing = sorted(set(required_tools) - set(registered_paths))
    extra = sorted(set(registered_paths) - set(required_tools))
    if missing or extra:
        violations.append(
            Violation(
                "managed_tool_coverage",
                "toolRegistry.tools",
                f"missing={missing!r}; extra={extra!r}",
            )
        )


def _valid_owner_path(value: object) -> bool:
    if not _nonempty_string(value) or "\\" in value:
        return False
    assert isinstance(value, str)
    wildcard = value.endswith("/**")
    base = value[:-3] if wildcard else value
    if any(token in base for token in ("*", "?", "[", "]")):
        return False
    parsed = PurePosixPath(base)
    if parsed.is_absolute() or parsed.as_posix() != base:
        return False
    if any(part in {"", ".", ".."} for part in parsed.parts):
        return False
    return any(
        base == prefix[:-1] or base.startswith(prefix) for prefix in SCOPE_PREFIXES
    )


def _owner_path_matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(f"{pattern[:-3]}/")
    return path == pattern


def _string_list(value: object, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and len(value) == len(set(value))
        and all(_nonempty_string(item) for item in value)
    )


def check(
    root: Path,
    catalog_path: Path | None = None,
    inventory_path: Path | None = None,
    tool_registry_path: Path | None = None,
) -> list[Violation]:
    root = root.resolve()
    catalog_path = catalog_path or (
        root / "design/architecture/component-catalog-v1.json"
    )
    inventory_path = inventory_path or (
        root / "docs/eva-repository-inventory.generated.json"
    )
    tool_registry_path = tool_registry_path or (
        root / "design/architecture/evidence-tool-registry-v1.json"
    )
    catalog = _load_object(catalog_path, label="component catalog")
    inventory = _load_object(inventory_path, label="repository inventory")
    tool_registry = _load_object(tool_registry_path, label="managed tool registry")
    violations: list[Violation] = []

    _closed_fields(
        catalog,
        TOP_LEVEL_FIELDS,
        code="catalog_fields",
        path="$",
        violations=violations,
    )
    if catalog.get("contract") != CONTRACT:
        violations.append(Violation("catalog_contract", "$.contract", "invalid"))
    if catalog.get("schemaVersion") != 1:
        violations.append(
            Violation("catalog_schema_version", "$.schemaVersion", "invalid")
        )
    if catalog.get("status") not in LIFECYCLE_STATUSES:
        violations.append(Violation("catalog_status", "$.status", "invalid"))
    if catalog.get("scopePrefixes") != list(SCOPE_PREFIXES):
        violations.append(
            Violation("catalog_scope", "$.scopePrefixes", "must equal the closed scope")
        )

    _check_managed_tool_registry(
        root,
        catalog.get("managedToolRegistry"),
        tool_registry,
        violations,
    )

    inventory_binding = catalog.get("inventory")
    if _closed_fields(
        inventory_binding,
        INVENTORY_FIELDS,
        code="inventory_binding_fields",
        path="$.inventory",
        violations=violations,
    ):
        assert isinstance(inventory_binding, dict)
        if inventory_binding.get("contract") != INVENTORY_CONTRACT:
            violations.append(
                Violation(
                    "inventory_binding_contract", "$.inventory.contract", "invalid"
                )
            )
        if (
            inventory_binding.get("path")
            != "docs/eva-repository-inventory.generated.json"
        ):
            violations.append(
                Violation("inventory_binding_path", "$.inventory.path", "invalid")
            )
    if inventory.get("schema") != INVENTORY_CONTRACT:
        violations.append(
            Violation("inventory_contract", "inventory.schema", "invalid")
        )
    raw_files = inventory.get("files")
    if not isinstance(raw_files, list):
        violations.append(
            Violation("inventory_files", "inventory.files", "must be a list")
        )
        return sorted(set(violations))
    scoped_paths: list[str] = []
    inventory_tracked_paths: set[str] = set()
    for index, row in enumerate(raw_files):
        if not isinstance(row, dict):
            violations.append(
                Violation(
                    "inventory_file", f"inventory.files[{index}]", "must be an object"
                )
            )
            continue
        path = row.get("path")
        state = row.get("gitState")
        if not isinstance(path, str) or state not in {
            "tracked",
            "untracked",
            "tombstone",
        }:
            violations.append(
                Violation(
                    "inventory_file", f"inventory.files[{index}]", "invalid path/state"
                )
            )
            continue
        if state == "tracked" and path.startswith(SCOPE_PREFIXES):
            scoped_paths.append(path)
            inventory_tracked_paths.add(path)
            present = row.get("present")
            content_sha256 = row.get("contentSha256")
            if present is not None or content_sha256 is not None:
                candidate = root / path
                if (
                    present is not True
                    or not isinstance(content_sha256, str)
                    or SHA256.fullmatch(content_sha256) is None
                    or not candidate.is_file()
                    or candidate.is_symlink()
                    or _sha256(candidate) != content_sha256
                ):
                    violations.append(
                        Violation("inventory_current_drift", path, "bytes differ")
                    )
    if len(scoped_paths) != len(set(scoped_paths)):
        violations.append(
            Violation("inventory_duplicate_path", "inventory.files", "duplicate")
        )
    scoped_paths = sorted(set(scoped_paths))
    try:
        visible = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                check=True,
                capture_output=True,
                timeout=20,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        visible = []
    current_tracked_paths = {
        path
        for path in visible
        if path
        and path.startswith(SCOPE_PREFIXES)
        and (root / path).is_file()
        and not (root / path).is_symlink()
    }
    if current_tracked_paths and current_tracked_paths != inventory_tracked_paths:
        missing = sorted(current_tracked_paths - inventory_tracked_paths)
        stale = sorted(inventory_tracked_paths - current_tracked_paths)
        violations.append(
            Violation(
                "inventory_current_paths",
                "inventory.files",
                f"missing={missing!r}; stale={stale!r}",
            )
        )

    raw_components = catalog.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        violations.append(
            Violation("catalog_components", "$.components", "must be non-empty")
        )
        return sorted(set(violations))

    component_ids: set[str] = set()
    valid_components: list[tuple[str, list[str]]] = []
    all_patterns: list[tuple[str, str]] = []
    for index, component in enumerate(raw_components):
        location = f"$.components[{index}]"
        if not _closed_fields(
            component,
            COMPONENT_FIELDS,
            code="component_fields",
            path=location,
            violations=violations,
        ):
            continue
        assert isinstance(component, dict)
        component_id = component["id"]
        if (
            not isinstance(component_id, str)
            or COMPONENT_ID.fullmatch(component_id) is None
        ):
            violations.append(Violation("component_id", f"{location}.id", "invalid"))
            continue
        if component_id in component_ids:
            violations.append(
                Violation("component_id_duplicate", f"{location}.id", component_id)
            )
            continue
        component_ids.add(component_id)
        for field in ("name", "purpose", "necessity"):
            if not _nonempty_string(component[field]):
                violations.append(
                    Violation("component_text", f"{location}.{field}", "invalid")
                )
        disposition = component["disposition"]
        if disposition not in DISPOSITIONS:
            violations.append(
                Violation("component_disposition", f"{location}.disposition", "invalid")
            )
        if component["lifecycleStatus"] not in LIFECYCLE_STATUSES:
            violations.append(
                Violation("component_status", f"{location}.lifecycleStatus", "invalid")
            )
        if not _string_list(component["forbiddenScope"], allow_empty=False):
            violations.append(
                Violation(
                    "component_forbidden_scope", f"{location}.forbiddenScope", "invalid"
                )
            )
        if not _string_list(component["blockers"], allow_empty=True):
            violations.append(
                Violation("component_blockers", f"{location}.blockers", "invalid")
            )

        upstream = component["upstream"]
        if _closed_fields(
            upstream,
            UPSTREAM_FIELDS,
            code="component_upstream_fields",
            path=f"{location}.upstream",
            violations=violations,
        ):
            assert isinstance(upstream, dict)
            if upstream["status"] not in UPSTREAM_STATUSES:
                violations.append(
                    Violation(
                        "component_upstream_status",
                        f"{location}.upstream.status",
                        "invalid",
                    )
                )
            if not all(
                _nullable_string(upstream[field])
                for field in ("name", "url", "version")
            ):
                violations.append(
                    Violation(
                        "component_upstream_value", f"{location}.upstream", "invalid"
                    )
                )
            reviewed_on = upstream["reviewedOn"]
            if reviewed_on is not None and (
                not isinstance(reviewed_on, str) or DATE.fullmatch(reviewed_on) is None
            ):
                violations.append(
                    Violation(
                        "component_upstream_date",
                        f"{location}.upstream.reviewedOn",
                        "invalid",
                    )
                )

        replacement = component["replacement"]
        if _closed_fields(
            replacement,
            REPLACEMENT_FIELDS,
            code="component_replacement_fields",
            path=f"{location}.replacement",
            violations=violations,
        ):
            assert isinstance(replacement, dict)
            if replacement["status"] not in REPLACEMENT_STATUSES:
                violations.append(
                    Violation(
                        "component_replacement_status",
                        f"{location}.replacement.status",
                        "invalid",
                    )
                )
            if not _nullable_string(replacement["target"]) or not _nonempty_string(
                replacement["reason"]
            ):
                violations.append(
                    Violation(
                        "component_replacement_value",
                        f"{location}.replacement",
                        "invalid",
                    )
                )
            if disposition == "replacement_pending" and (
                replacement["status"] != "replacement_pending"
                or replacement["target"] is None
                or not component["blockers"]
            ):
                violations.append(
                    Violation(
                        "component_replacement_incomplete",
                        f"{location}.replacement",
                        "pending replacement needs target and blockers",
                    )
                )
            if (
                disposition != "replacement_pending"
                and replacement["status"] == "replacement_pending"
            ):
                violations.append(
                    Violation(
                        "component_replacement_mismatch",
                        f"{location}.replacement",
                        "disposition mismatch",
                    )
                )

        verification = component["verification"]
        if _closed_fields(
            verification,
            VERIFICATION_FIELDS,
            code="component_verification_fields",
            path=f"{location}.verification",
            violations=violations,
        ):
            assert isinstance(verification, dict)
            test_paths = verification["testPaths"]
            evidence_paths = verification["evidencePaths"]
            if not _string_list(test_paths, allow_empty=False):
                violations.append(
                    Violation(
                        "component_test_paths",
                        f"{location}.verification.testPaths",
                        "invalid",
                    )
                )
            else:
                for pattern in test_paths:
                    if not _valid_owner_path(pattern) or not pattern.startswith(
                        "tests/"
                    ):
                        violations.append(
                            Violation(
                                "component_test_path",
                                f"{location}.verification.testPaths",
                                str(pattern),
                            )
                        )
                    elif not any(
                        _owner_path_matches(pattern, path) for path in scoped_paths
                    ):
                        violations.append(
                            Violation("component_test_path_dead", component_id, pattern)
                        )
            if not _string_list(evidence_paths, allow_empty=True):
                violations.append(
                    Violation(
                        "component_evidence_paths",
                        f"{location}.verification.evidencePaths",
                        "invalid",
                    )
                )

        owner_paths = component["ownerPaths"]
        if not _string_list(owner_paths, allow_empty=False):
            violations.append(
                Violation("component_owner_paths", f"{location}.ownerPaths", "invalid")
            )
            continue
        assert isinstance(owner_paths, list)
        valid_patterns: list[str] = []
        for pattern in owner_paths:
            if not _valid_owner_path(pattern):
                violations.append(
                    Violation(
                        "component_owner_path", f"{location}.ownerPaths", str(pattern)
                    )
                )
                continue
            valid_patterns.append(pattern)
            all_patterns.append((component_id, pattern))
        valid_components.append((component_id, valid_patterns))

    for component_id, patterns in valid_components:
        if not any(
            _owner_path_matches(pattern, path)
            for pattern in patterns
            for path in scoped_paths
        ):
            violations.append(
                Violation("component_dead", component_id, "owns no tracked scoped path")
            )
    for component_id, pattern in all_patterns:
        if not any(_owner_path_matches(pattern, path) for path in scoped_paths):
            violations.append(Violation("owner_path_dead", component_id, pattern))

    for path in scoped_paths:
        owners = sorted(
            component_id
            for component_id, patterns in valid_components
            if any(_owner_path_matches(pattern, path) for pattern in patterns)
        )
        if not owners:
            violations.append(Violation("owner_missing", path, "no primary component"))
        elif len(owners) > 1:
            violations.append(Violation("owner_ambiguous", path, ",".join(owners)))
    return sorted(set(violations))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--tool-registry", type=Path)
    arguments = parser.parse_args()
    violations = check(
        arguments.root,
        arguments.catalog,
        arguments.inventory,
        arguments.tool_registry,
    )
    result = {
        "contract": "EvaComponentCatalogCheck/v1",
        "ok": not violations,
        "violations": [asdict(item) for item in violations],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
