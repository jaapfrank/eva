#!/usr/bin/env python3
"""Rebuild Eva's sealed runtime dependency metadata from one hash lock.

The command is intentionally offline. It accepts only an already downloaded
wheelhouse whose bytes occur in the hash-locked requirements file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from email.parser import BytesParser
from pathlib import Path
import re
import zipfile


PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^ ;\\]+)")


def _normalize(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return value


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(value))
    temporary.replace(path)


def _pins(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match:
            name, version = _normalize(match.group(1)), match.group(2)
            if result.setdefault(name, version) != version:
                raise RuntimeError(f"{path} repeats {name} with another version")
    if not result:
        raise RuntimeError(f"{path} contains no exact pins")
    return result


def _lock_hashes(path: Path) -> set[str]:
    marker = "--hash=sha256:"
    return {
        line.split(marker, 1)[1].strip().split()[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if marker in line
    }


def _wheel_metadata(path: Path) -> tuple[str, str, str, str]:
    with zipfile.ZipFile(path) as archive:
        matches = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(matches) != 1:
            raise RuntimeError(f"{path.name} has no unique wheel METADATA")
        metadata = BytesParser().parsebytes(archive.read(matches[0]))
    name = _normalize(str(metadata["Name"]))
    version = str(metadata["Version"])
    expression = metadata.get("License-Expression")
    if expression:
        return name, version, str(expression), "wheel_license_expression"
    legacy = str(metadata.get("License", "")).strip()
    if not legacy:
        raise RuntimeError(f"{path.name} has no machine-readable license metadata")
    return name, version, legacy, "wheel_metadata_license"


def _wheel_rows(
    wheelhouse: Path,
    pins: dict[str, str],
    lock_hashes: set[str],
) -> tuple[list[dict[str, object]], dict[str, tuple[str, str]]]:
    artifacts: list[dict[str, object]] = []
    licenses: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    for path in sorted(wheelhouse.glob("*.whl"), key=lambda item: item.name):
        raw = path.read_bytes()
        digest = _sha256(raw)
        if digest not in lock_hashes:
            raise RuntimeError(f"{path.name} is absent from the hash lock")
        name, version, expression, source = _wheel_metadata(path)
        if pins.get(name) != version or name in seen:
            raise RuntimeError(f"{path.name} does not uniquely match the lock")
        seen.add(name)
        artifacts.append(
            {"byteLength": len(raw), "filename": path.name, "sha256": digest}
        )
        licenses[name] = (expression, source)
    if seen != set(pins):
        missing = sorted(set(pins) - seen)
        extra = sorted(seen - set(pins))
        raise RuntimeError(
            f"wheelhouse closure differs; missing={missing}, extra={extra}"
        )
    return artifacts, licenses


def refresh(*, root: Path, wheelhouse: Path) -> dict[str, object]:
    eva = root / "eva2"
    lock_path = eva / "requirements-4d.lock"
    roots_path = eva / "release/dependency-roots.in"
    pyproject_path = eva / "pyproject.toml"
    binding_path = eva / "release/4d-dependency-source-binding.json"
    coverage_path = eva / "release/managed-b-wheel-coverage-4d-amd64-v1.json"
    manifest_path = eva / "release/managed-b-wheelhouse-amd64-v2.json"
    inventory_path = eva / "release/license-inventory.json"
    toolchain_path = eva / "release/gate-toolchain.lock.json"

    pins = _pins(lock_path)
    direct = _pins(roots_path)
    lock_raw = lock_path.read_bytes()
    lock_sha = _sha256(lock_raw)
    artifacts, wheel_licenses = _wheel_rows(
        wheelhouse.resolve(strict=True), pins, _lock_hashes(lock_path)
    )
    count = len(pins)
    selected_manifest_sha = _sha256(
        _canonical([[row["filename"], row["sha256"]] for row in artifacts])
    )

    binding = _load(binding_path)
    dependencies = binding["dependencies"]
    if not isinstance(dependencies, dict):
        raise RuntimeError("runtime dependency binding has no dependency map")
    openai_artifact = next(
        row for row in artifacts if row["filename"].startswith("openai-")
    )
    pydantic_ai_artifact = next(
        row for row in artifacts if row["filename"].startswith("pydantic_ai_slim-")
    )
    pydantic_graph_artifact = next(
        row for row in artifacts if row["filename"].startswith("pydantic_graph-")
    )
    dependencies["openai"] = {
        "reason": "official model-provider transport",
        "selectedWheel": openai_artifact,
        "version": pins["openai"],
    }
    dependencies["pydantic-ai-slim"] = {
        "reason": "maintained typed model and agent runtime",
        "selectedWheel": pydantic_ai_artifact,
        "version": pins["pydantic-ai-slim"],
    }
    dependencies["pydantic-graph"] = {
        "reason": "transitive typed graph runtime required by pydantic-ai-slim",
        "selectedWheel": pydantic_graph_artifact,
        "version": pins["pydantic-graph"],
    }
    binding["decisionDate"] = "2026-08-31"
    binding["licenseAndSbomRequirements"]["status"] = (
        f"must_match_{count}_selected_distributions"  # type: ignore[index]
    )
    adoption = binding["pyprojectAdoption"]
    if not isinstance(adoption, dict):
        raise RuntimeError("runtime dependency binding has no pyproject adoption")
    additions = adoption.get("directRuntimeAdded")
    if not isinstance(additions, list):
        raise RuntimeError("runtime dependency additions are invalid")
    additions[:] = sorted(set(additions) | {f"openai=={pins['openai']}"})
    updates = adoption.get("directRuntimeUpdated")
    if not isinstance(updates, list):
        raise RuntimeError("runtime dependency updates are invalid")
    updates[:] = sorted(
        set(updates) | {"pydantic-ai-slim==2.32.1 -> pydantic-ai-slim==2.36.0"}
    )
    adoption["sha256"] = _sha256(pyproject_path.read_bytes())
    standalone = binding["standaloneLock"]
    protocol = binding["wheelhouseInstallProtocol"]
    if not isinstance(standalone, dict) or not isinstance(protocol, dict):
        raise RuntimeError("runtime dependency lock protocol is invalid")
    standalone.update({"packageCount": count, "sha256": lock_sha})
    protocol.update(
        {
            "selectedManifestSha256": selected_manifest_sha,
            "wheelCount": count,
        }
    )
    binding["status"] = "sealed_offline_inputs_not_release"
    _write(binding_path, binding)
    binding_sha = _sha256(binding_path.read_bytes())

    coverage = _load(coverage_path)
    coverage.update(
        {
            "dependencySourceBindingSha256": binding_sha,
            "requirementsLockSha256": lock_sha,
            "status": "derived_from_hash_pinned_baseline_with_sealed_runtime_dependency_delta_no_install_or_execution",
        }
    )
    targets = coverage.get("targets")
    if (
        not isinstance(targets, list)
        or len(targets) != 1
        or not isinstance(targets[0], dict)
    ):
        raise RuntimeError("runtime wheel coverage target differs")
    targets[0].update(
        {
            "artifacts": artifacts,
            "lockedPackageCount": count,
            "resolvedWheelCount": count,
        }
    )
    _write(coverage_path, coverage)
    coverage_sha = _sha256(coverage_path.read_bytes())

    manifest = _load(manifest_path)
    manifest.update(
        {
            "coverageSha256": coverage_sha,
            "dependencyBindingSha256": binding_sha,
            "expectedWheelCount": count,
            "requirementsLockSha256": lock_sha,
            "selectedManifestSha256": selected_manifest_sha,
        }
    )
    _write(manifest_path, manifest)

    old_inventory = _load(inventory_path)
    old_rows = {
        _normalize(str(row["name"])): row
        for row in old_inventory.get("packages", [])
        if isinstance(row, dict) and "name" in row
    }
    rows: list[dict[str, object]] = []
    for name, version in sorted(pins.items()):
        old = old_rows.get(name)
        if old is not None and old.get("version") == version:
            row = dict(old)
        else:
            expression, source = wheel_licenses[name]
            row = {
                "licenseExpression": expression,
                "metadataLicenseSource": source,
                "reviewStatus": "metadata_recorded_not_legal_opinion",
            }
        row.update(
            {
                "dependencyClass": (
                    "build_direct"
                    if name == "setuptools"
                    else "runtime_direct"
                    if name in direct
                    else "runtime_transitive"
                ),
                "metadataSource": f"https://pypi.org/pypi/{name}/{version}/json",
                "name": name,
                "version": version,
            }
        )
        rows.append(row)
    old_inventory.update(
        {
            "dependencyLock": "eva2/requirements-4d.lock",
            "packageCount": count,
            "packages": rows,
        }
    )
    _write(inventory_path, old_inventory)

    toolchain = _load(toolchain_path)
    tool_pins = toolchain.get("requiredDistributions")
    if not isinstance(tool_pins, dict):
        raise RuntimeError("gate toolchain distribution map differs")
    gate_pins = _pins(root / "eva2/release/gate-toolchain.requirements.lock")
    gate_only = {
        name: version
        for name, version in tool_pins.items()
        if name not in pins and name not in gate_pins
    }
    union = dict(gate_only)
    union.update(pins)
    union.update(gate_pins)
    toolchain["requiredDistributions"] = dict(sorted(union.items()))
    toolchain["requiredDistributionCount"] = len(union)
    requirement_locks = toolchain.get("requirementLocks")
    if not isinstance(requirement_locks, list) or not requirement_locks:
        raise RuntimeError("gate toolchain requirement lock rows differ")
    requirement_locks[0].update({"packageCount": count, "sha256": lock_sha})
    _write(toolchain_path, toolchain)

    return {
        "contract": "EvaRuntimeDependencyMetadataRefresh/v1",
        "lockSha256": lock_sha,
        "packageCount": count,
        "selectedManifestSha256": selected_manifest_sha,
        "wheelCount": len(artifacts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            refresh(root=arguments.root, wheelhouse=arguments.wheelhouse),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
