#!/usr/bin/env python3
"""Rebuild Eva's sealed runtime dependency metadata from one hash lock.

The command is intentionally offline. It accepts only an already downloaded
wheelhouse whose bytes occur in the hash-locked requirements file.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from email.parser import BytesParser
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile


PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^ ;\\]+)")
_MAX_WHEEL_BYTES = 256 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_OUTPUT_SUFFIX = ".runtime-dependency-refresh"


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


def _load(raw: bytes, *, path: Path) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return value


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_owned_regular(path: Path, *, maximum: int) -> tuple[bytes, int]:
    try:
        before = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{path} is inaccessible") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise RuntimeError(f"{path} is not a safe owned regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"{path} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise RuntimeError(f"{path} changed before it was read")
        chunks: list[bytes] = []
        length = 0
        while block := os.read(descriptor, 1024 * 1024):
            length += len(block)
            if length > maximum:
                raise RuntimeError(f"{path} exceeds its size limit")
            chunks.append(block)
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{path} changed while it was read") from error
    if _file_identity(after) != _file_identity(before) or length != before.st_size:
        raise RuntimeError(f"{path} changed while it was read")
    return b"".join(chunks), stat.S_IMODE(before.st_mode)


def _stage(path: Path, raw: bytes, *, mode: int, label: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".{label}{_OUTPUT_SUFFIX}",
        dir=path.parent,
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("runtime dependency metadata staging stalled")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return temporary


def _fsync_directories(paths: set[Path]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    for path in sorted(paths):
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _replace_outputs(
    outputs: dict[Path, bytes], originals: dict[Path, tuple[bytes, int]]
) -> None:
    """Replace a complete metadata set or restore every original on failure."""

    if set(outputs) != set(originals) or not outputs:
        raise RuntimeError("runtime dependency metadata transaction differs")
    ordered = sorted(outputs)
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    preserve_backups = False
    directories = {path.parent for path in ordered}
    try:
        for path in ordered:
            original, mode = originals[path]
            current, current_mode = _read_owned_regular(path, maximum=16 * 1024 * 1024)
            if current != original or current_mode != mode:
                raise RuntimeError("runtime dependency metadata changed before commit")
            staged[path] = _stage(path, outputs[path], mode=mode, label="stage")
            backups[path] = _stage(path, original, mode=mode, label="backup")
        _fsync_directories(directories)
        for path in ordered:
            os.replace(staged[path], path)
            staged.pop(path)
            replaced.append(path)
        _fsync_directories(directories)
    except Exception as error:
        rollback_errors: list[OSError] = []
        for path in reversed(replaced):
            try:
                os.replace(backups[path], path)
                backups.pop(path)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        try:
            _fsync_directories(directories)
        except OSError as rollback_error:
            rollback_errors.append(rollback_error)
        if rollback_errors:
            preserve_backups = True
            raise RuntimeError(
                "runtime dependency metadata transaction and rollback failed"
            ) from error
        raise RuntimeError(
            "runtime dependency metadata transaction was rolled back"
        ) from error
    finally:
        cleanup = tuple(staged.values())
        if not preserve_backups:
            cleanup += tuple(backups.values())
        for temporary in cleanup:
            temporary.unlink(missing_ok=True)
    _fsync_directories(directories)


def _owned_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{label} is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RuntimeError(f"{label} must be an owned non-writable directory")
    return metadata


def _pins(raw: bytes, *, path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{path} is not UTF-8") from error
    for line in lines:
        match = PIN.match(line)
        if match:
            name, version = _normalize(match.group(1)), match.group(2)
            if result.setdefault(name, version) != version:
                raise RuntimeError(f"{path} repeats {name} with another version")
    if not result:
        raise RuntimeError(f"{path} contains no exact pins")
    return result


def _lock_hashes(raw: bytes) -> set[str]:
    marker = "--hash=sha256:"
    return {
        line.split(marker, 1)[1].strip().split()[0]
        for line in raw.decode("utf-8").splitlines()
        if marker in line
    }


def _wheel_metadata(raw: bytes, *, filename: str) -> tuple[str, str, str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            matches = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            if len(matches) != 1:
                raise RuntimeError(f"{filename} has no unique wheel METADATA")
            information = archive.getinfo(matches[0])
            if (
                information.file_size <= 0
                or information.file_size > _MAX_METADATA_BYTES
            ):
                raise RuntimeError(f"{filename} wheel METADATA size is invalid")
            metadata = BytesParser().parsebytes(archive.read(information))
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"{filename} is not a readable wheel") from error
    name = _normalize(str(metadata["Name"]))
    version = str(metadata["Version"])
    expression = metadata.get("License-Expression")
    if expression:
        return name, version, str(expression), "wheel_license_expression"
    legacy = str(metadata.get("License", "")).strip()
    if not legacy:
        raise RuntimeError(f"{filename} has no machine-readable license metadata")
    return name, version, legacy, "wheel_metadata_license"


def _wheel_rows(
    wheelhouse: Path,
    pins: dict[str, str],
    lock_hashes: set[str],
) -> tuple[list[dict[str, object]], dict[str, tuple[str, str]]]:
    if not wheelhouse.is_absolute() or ".." in wheelhouse.parts:
        raise RuntimeError("wheelhouse must be an explicit absolute path")
    try:
        root_before = wheelhouse.lstat()
    except OSError as error:
        raise RuntimeError("wheelhouse is inaccessible") from error
    if (
        stat.S_ISLNK(root_before.st_mode)
        or not stat.S_ISDIR(root_before.st_mode)
        or root_before.st_uid != os.geteuid()
        or stat.S_IMODE(root_before.st_mode) & 0o077
    ):
        raise RuntimeError("wheelhouse must be an owned private directory")
    entries = list(os.scandir(wheelhouse))
    if not entries or any(not entry.name.endswith(".whl") for entry in entries):
        raise RuntimeError("wheelhouse may contain only wheel files")
    artifacts: list[dict[str, object]] = []
    licenses: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    names = tuple(sorted(entry.name for entry in entries))
    if len(set(names)) != len(names):
        raise RuntimeError("wheelhouse repeats an entry")
    for name in names:
        path = wheelhouse / name
        raw, mode = _read_owned_regular(path, maximum=_MAX_WHEEL_BYTES)
        if mode & 0o222:
            raise RuntimeError(f"{name} must be read-only")
        digest = _sha256(raw)
        if digest not in lock_hashes:
            raise RuntimeError(f"{name} is absent from the hash lock")
        package, version, expression, source = _wheel_metadata(raw, filename=name)
        if pins.get(package) != version or package in seen:
            raise RuntimeError(f"{name} does not uniquely match the lock")
        seen.add(package)
        artifacts.append({"byteLength": len(raw), "filename": name, "sha256": digest})
        licenses[package] = (expression, source)
    try:
        root_after = wheelhouse.lstat()
        names_after = tuple(sorted(entry.name for entry in os.scandir(wheelhouse)))
    except OSError as error:
        raise RuntimeError("wheelhouse changed during validation") from error
    if (
        _file_identity(root_after) != _file_identity(root_before)
        or names_after != names
    ):
        raise RuntimeError("wheelhouse changed during validation")
    if seen != set(pins):
        missing = sorted(set(pins) - seen)
        extra = sorted(seen - set(pins))
        raise RuntimeError(
            f"wheelhouse closure differs; missing={missing}, extra={extra}"
        )
    return artifacts, licenses


def refresh(*, root: Path, wheelhouse: Path) -> dict[str, object]:
    if not root.is_absolute() or ".." in root.parts:
        raise RuntimeError("source root must be one explicit canonical absolute path")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("source root is inaccessible") from error
    if resolved_root != root:
        raise RuntimeError("source root must be one explicit canonical absolute path")
    eva = root / "eva2"
    release = eva / "release"
    _owned_directory(root, label="source root")
    _owned_directory(eva, label="Eva source directory")
    _owned_directory(release, label="Eva release directory")
    lock_path = eva / "requirements-4d.lock"
    roots_path = release / "dependency-roots.in"
    pyproject_path = eva / "pyproject.toml"
    gate_lock_path = release / "gate-toolchain.requirements.lock"
    binding_path = release / "4d-dependency-source-binding.json"
    coverage_path = release / "managed-b-wheel-coverage-4d-amd64-v1.json"
    manifest_path = release / "managed-b-wheelhouse-amd64-v2.json"
    inventory_path = release / "license-inventory.json"
    toolchain_path = release / "gate-toolchain.lock.json"
    output_paths = (
        binding_path,
        coverage_path,
        manifest_path,
        inventory_path,
        toolchain_path,
    )
    originals = {
        path: _read_owned_regular(path, maximum=16 * 1024 * 1024)
        for path in output_paths
    }
    lock_raw, _lock_mode = _read_owned_regular(lock_path, maximum=16 * 1024 * 1024)
    roots_raw, _roots_mode = _read_owned_regular(roots_path, maximum=1024 * 1024)
    pyproject_raw, _pyproject_mode = _read_owned_regular(
        pyproject_path, maximum=1024 * 1024
    )
    gate_lock_raw, _gate_lock_mode = _read_owned_regular(
        gate_lock_path, maximum=16 * 1024 * 1024
    )

    pins = _pins(lock_raw, path=lock_path)
    direct = _pins(roots_raw, path=roots_path)
    lock_sha = _sha256(lock_raw)
    artifacts, wheel_licenses = _wheel_rows(wheelhouse, pins, _lock_hashes(lock_raw))
    count = len(pins)
    selected_manifest_sha = _sha256(
        _canonical([[row["filename"], row["sha256"]] for row in artifacts])
    )

    binding = _load(originals[binding_path][0], path=binding_path)
    dependencies = binding["dependencies"]
    if not isinstance(dependencies, dict):
        raise RuntimeError("runtime dependency binding has no dependency map")
    openai_artifact = next(
        row for row in artifacts if row["filename"].startswith("openai-")
    )
    genai_prices_artifact = next(
        row for row in artifacts if row["filename"].startswith("genai_prices-")
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
    dependencies["genai-prices"] = {
        "reason": "transitive price catalog required by pydantic-ai-slim",
        "selectedWheel": genai_prices_artifact,
        "version": pins["genai-prices"],
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
    binding["decisionDate"] = "2026-09-01"
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
        {item for item in updates if not str(item).startswith("pydantic-ai-slim==")}
        | {"pydantic-ai-slim==2.32.1 -> pydantic-ai-slim==2.37.0"}
    )
    adoption["sha256"] = _sha256(pyproject_raw)
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
    binding_raw = _canonical(binding)
    binding_sha = _sha256(binding_raw)

    coverage = _load(originals[coverage_path][0], path=coverage_path)
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
    coverage_raw = _canonical(coverage)
    coverage_sha = _sha256(coverage_raw)

    manifest = _load(originals[manifest_path][0], path=manifest_path)
    manifest.update(
        {
            "coverageSha256": coverage_sha,
            "dependencyBindingSha256": binding_sha,
            "expectedWheelCount": count,
            "requirementsLockSha256": lock_sha,
            "selectedManifestSha256": selected_manifest_sha,
        }
    )
    manifest_raw = _canonical(manifest)

    old_inventory = _load(originals[inventory_path][0], path=inventory_path)
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
            "metadataAsOf": "2026-09-01",
            "packageCount": count,
            "packages": rows,
        }
    )
    inventory_raw = _canonical(old_inventory)

    toolchain = _load(originals[toolchain_path][0], path=toolchain_path)
    generation = toolchain.get("generationRecord")
    application_lock = (
        generation.get("applicationLock") if isinstance(generation, dict) else None
    )
    if (
        not isinstance(application_lock, dict)
        or application_lock.get("frontend") != "pip 24.0"
    ):
        raise RuntimeError("application lock frontend must remain pip 24.0")
    tool_pins = toolchain.get("requiredDistributions")
    if not isinstance(tool_pins, dict):
        raise RuntimeError("gate toolchain distribution map differs")
    gate_pins = _pins(gate_lock_raw, path=gate_lock_path)
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
    if not isinstance(requirement_locks, list) or len(requirement_locks) != 2:
        raise RuntimeError("gate toolchain requirement lock rows differ")
    lock_rows = {
        str(row.get("role")): row for row in requirement_locks if isinstance(row, dict)
    }
    application_row = lock_rows.get("application_and_contract_runtime")
    gate_row = lock_rows.get(
        "design_gate_build_frontend_and_independent_schema_consumer"
    )
    if application_row is None or gate_row is None:
        raise RuntimeError("gate toolchain requirement lock roles differ")
    application_row.update({"packageCount": count, "sha256": lock_sha})
    gate_row.update({"packageCount": len(gate_pins), "sha256": _sha256(gate_lock_raw)})
    toolchain_raw = _canonical(toolchain)

    _replace_outputs(
        {
            binding_path: binding_raw,
            coverage_path: coverage_raw,
            manifest_path: manifest_raw,
            inventory_path: inventory_raw,
            toolchain_path: toolchain_raw,
        },
        originals,
    )

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
