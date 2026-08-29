"""Fail-closed validation for the future offline managed-B wheelhouse.

This module deliberately validates local bytes only.  It never downloads,
installs, imports, or executes a wheel, and it has no Docker or B dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


MAX_WHEEL_BYTES = 64 * 1024 * 1024
MANIFEST_CONTRACT = "EvaManagedBWheelhouseManifest/v1"
ACTIVE_MANIFEST_CONTRACT = "EvaManagedBWheelhouseManifest/v2"
ALIGNED_COVERAGE_CONTRACT = "EvaManagedBAlignedWheelCoverage/v1"
ACTIVE_COVERAGE_PATH = "release/managed-b-wheel-coverage-4d-amd64-v1.json"
ACTIVE_LOCK_PATH = "requirements-4d.lock"
BASELINE_MANIFEST_PATH = "release/managed-b-wheelhouse-amd64-v1.json"
DEPENDENCY_BINDING_PATH = "release/4d-dependency-source-binding.json"
REMOVED_DBOS_WHEELS = (
    "dbos-2.30.0-py3-none-any.whl",
    "python_dateutil-2.9.0.post0-py2.py3-none-any.whl",
    "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
    "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
    "six-1.17.0-py2.py3-none-any.whl",
)
PYNACL_AMD64_WHEEL = (
    "pynacl-1.6.2-cp38-abi3-manylinux_2_26_x86_64."
    "manylinux_2_28_x86_64.whl"
)
PYNACL_AMD64_ARTIFACT = (
    "8a66d6fb6ae7661c58995f9c6435bda2b1e68b54b598a6a10247bfcdadac996c",
    1437614,
)


class MaterializedWheelhouse:
    """A private exact wheelhouse whose caller owns its temporary parent."""

    def __init__(self, path: Path, receipt: dict[str, object]) -> None:
        self.path = path
        self.receipt = receipt


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeError(f"{label} repeats a JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise RuntimeError(f"{label} must be a lowercase sha256")
    return value


def _manifest(raw: bytes) -> dict[str, Any]:
    value = _json_object(raw, label="managed B wheelhouse manifest")
    if raw.rstrip(b"\n") != _canonical_json(value) or raw.endswith(b"\n\n"):
        raise RuntimeError("managed B wheelhouse manifest is not canonical")
    contract = value.get("contract")
    if contract == MANIFEST_CONTRACT:
        expected = {
            "contract",
            "coveragePath",
            "coverageSha256",
            "expectedWheelCount",
            "onlyBinary",
            "requirementsLockSha256",
            "target",
        }
        if set(value) != expected:
            raise RuntimeError("managed B wheelhouse manifest contract differs")
        if value.get("coveragePath") != "release/python-wheel-coverage.json":
            raise RuntimeError("managed B wheelhouse coverage path differs")
    elif contract == ACTIVE_MANIFEST_CONTRACT:
        expected = {
            "baselineManifestPath",
            "baselineManifestSha256",
            "contract",
            "coveragePath",
            "coverageSha256",
            "dependencyBindingPath",
            "dependencyBindingSha256",
            "expectedWheelCount",
            "onlyBinary",
            "requirementsLockPath",
            "requirementsLockSha256",
            "selectedManifestSha256",
            "target",
        }
        if set(value) != expected:
            raise RuntimeError("managed B wheelhouse manifest contract differs")
        if (
            value.get("baselineManifestPath") != BASELINE_MANIFEST_PATH
            or value.get("coveragePath") != ACTIVE_COVERAGE_PATH
            or value.get("dependencyBindingPath") != DEPENDENCY_BINDING_PATH
            or value.get("requirementsLockPath") != ACTIVE_LOCK_PATH
        ):
            raise RuntimeError("managed B wheelhouse active input path differs")
        _require_sha256(
            value.get("baselineManifestSha256"),
            label="wheelhouse baseline manifest sha256",
        )
        _require_sha256(
            value.get("dependencyBindingSha256"),
            label="wheelhouse dependency binding sha256",
        )
        _require_sha256(
            value.get("selectedManifestSha256"),
            label="wheelhouse selected manifest sha256",
        )
    else:
        raise RuntimeError("managed B wheelhouse manifest contract differs")
    _require_sha256(value.get("coverageSha256"), label="wheelhouse coverage sha256")
    _require_sha256(
        value.get("requirementsLockSha256"), label="wheelhouse requirements lock sha256"
    )
    if value.get("onlyBinary") is not True:
        raise RuntimeError("managed B wheelhouse must require binary-only dependencies")
    count = value.get("expectedWheelCount")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 256:
        raise RuntimeError("managed B wheelhouse expected count is invalid")
    target = value.get("target")
    if not isinstance(target, dict) or set(target) != {
        "architecture",
        "os",
        "pythonAbi",
        "pythonVersion",
    } or not all(isinstance(item, str) and item for item in target.values()):
        raise RuntimeError("managed B wheelhouse target is invalid")
    if contract == ACTIVE_MANIFEST_CONTRACT and target != {
        "architecture": "amd64",
        "os": "Ubuntu Server 24.04.4 LTS",
        "pythonAbi": "cp312",
        "pythonVersion": "3.12.14",
    }:
        raise RuntimeError("managed B active wheelhouse target differs")
    return value


def _expected_wheels(
    coverage_raw: bytes, manifest: dict[str, Any], lock_raw: bytes
) -> tuple[dict[str, tuple[str, int]], str]:
    if _sha256(coverage_raw) != manifest["coverageSha256"]:
        raise RuntimeError("managed B wheelhouse coverage digest differs")
    if _sha256(lock_raw) != manifest["requirementsLockSha256"]:
        raise RuntimeError("managed B wheelhouse requirements lock digest differs")
    coverage = _json_object(coverage_raw, label="wheel coverage")
    coverage_contract = coverage.get("contract")
    if coverage.get("onlyBinary") is not True or coverage_contract not in {
        "Eva2PythonWheelCoverage/v1",
        ALIGNED_COVERAGE_CONTRACT,
    }:
        raise RuntimeError("managed B wheelhouse coverage contract differs")
    if coverage_contract == ALIGNED_COVERAGE_CONTRACT:
        if set(coverage) != {
            "baselineCoveragePath",
            "baselineCoverageSha256",
            "contract",
            "dependencySourceBindingPath",
            "dependencySourceBindingSha256",
            "onlyBinary",
            "requirementsLockPath",
            "requirementsLockSha256",
            "status",
            "targets",
        }:
            raise RuntimeError("managed B aligned wheel coverage fields differ")
        if (
            coverage.get("baselineCoveragePath")
            != "eva2/release/python-wheel-coverage.json"
            or coverage.get("dependencySourceBindingPath")
            != "eva2/release/4d-dependency-source-binding.json"
            or coverage.get("requirementsLockPath") != "eva2/requirements-4d.lock"
            or coverage.get("requirementsLockSha256")
            != manifest["requirementsLockSha256"]
            or coverage.get("status")
            != "derived_from_hash_pinned_baseline_minus_unused_dbos_transitives_plus_pynacl_no_install_or_execution"
        ):
            raise RuntimeError("managed B aligned wheel coverage authority differs")
        _require_sha256(
            coverage.get("baselineCoverageSha256"),
            label="aligned baseline coverage sha256",
        )
        _require_sha256(
            coverage.get("dependencySourceBindingSha256"),
            label="aligned dependency binding sha256",
        )
    targets = coverage.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("managed B wheelhouse coverage targets are invalid")
    if coverage_contract == ALIGNED_COVERAGE_CONTRACT and len(targets) != 1:
        raise RuntimeError("managed B aligned wheel coverage target set differs")
    target = next(
        (
            item
            for item in targets
            if isinstance(item, dict) and item.get("architecture") == manifest["target"]["architecture"]
            and item.get("os") == manifest["target"]["os"]
            and item.get("pythonAbi") == manifest["target"]["pythonAbi"]
            and item.get("pythonVersion") == manifest["target"]["pythonVersion"]
        ),
        None,
    )
    if not isinstance(target, dict):
        raise RuntimeError("managed B wheelhouse target is absent from coverage")
    if coverage_contract == ALIGNED_COVERAGE_CONTRACT and (
        set(target)
        != {
            "acceptedWheelPlatforms",
            "allArtifactsMatchedRequirementsLockHashes",
            "architecture",
            "artifacts",
            "lockedPackageCount",
            "os",
            "pythonAbi",
            "pythonVersion",
            "resolvedWheelCount",
        }
        or target.get("acceptedWheelPlatforms")
        != ["manylinux_2_28_x86_64", "manylinux2014_x86_64"]
    ):
        raise RuntimeError("managed B aligned wheel coverage target differs")
    artifacts = target.get("artifacts")
    if (
        target.get("lockedPackageCount") != manifest["expectedWheelCount"]
        or target.get("resolvedWheelCount") != manifest["expectedWheelCount"]
        or target.get("allArtifactsMatchedRequirementsLockHashes") is not True
        or not isinstance(artifacts, list)
        or len(artifacts) != manifest["expectedWheelCount"]
    ):
        raise RuntimeError("managed B wheelhouse coverage count differs")
    try:
        lock_lines = lock_raw.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("managed B wheelhouse requirements lock is not UTF-8") from error
    lock_hashes = set()
    for line in lock_lines:
        marker = "--hash=sha256:"
        if marker in line:
            lock_hashes.add(line.split(marker, 1)[1].strip().split()[0])
    expected: dict[str, tuple[str, int]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "byteLength",
            "filename",
            "sha256",
        }:
            raise RuntimeError("managed B wheelhouse artifact is invalid")
        filename, digest, length = (
            artifact["filename"],
            _require_sha256(artifact["sha256"], label="wheel artifact sha256"),
            artifact["byteLength"],
        )
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".whl")
            or isinstance(length, bool)
            or not isinstance(length, int)
            or not 0 < length <= MAX_WHEEL_BYTES
            or digest not in lock_hashes
            or filename in expected
        ):
            raise RuntimeError("managed B wheelhouse artifact is not sealed")
        expected[filename] = (digest, length)
    if list(expected) != sorted(expected):
        raise RuntimeError("managed B wheelhouse artifacts are not lexicographically ordered")
    selected_manifest_sha256 = _sha256(
        _canonical_json([[filename, digest] for filename, (digest, _length) in expected.items()])
    )
    if (
        manifest["contract"] == ACTIVE_MANIFEST_CONTRACT
        and selected_manifest_sha256 != manifest["selectedManifestSha256"]
    ):
        raise RuntimeError("managed B wheelhouse selected manifest differs")
    return expected, _sha256(_canonical_json([
        {"byteLength": length, "filename": filename, "sha256": digest}
        for filename, (digest, length) in expected.items()
    ]))


def inspect_runtime_dependency_alignment(
    active_manifest_raw: bytes,
    aligned_coverage_raw: bytes,
    requirements_4d_raw: bytes,
    baseline_manifest_raw: bytes,
    baseline_coverage_raw: bytes,
    baseline_lock_raw: bytes,
    dependency_binding_raw: bytes,
) -> dict[str, object]:
    """Prove the exact 38-to-35 dependency-input transformation without wheel I/O."""

    active_manifest = _manifest(active_manifest_raw)
    if active_manifest["contract"] != ACTIVE_MANIFEST_CONTRACT:
        raise RuntimeError("managed B active wheelhouse manifest must be v2")
    if _sha256(baseline_manifest_raw) != active_manifest["baselineManifestSha256"]:
        raise RuntimeError("managed B wheelhouse baseline manifest digest differs")
    if _sha256(dependency_binding_raw) != active_manifest["dependencyBindingSha256"]:
        raise RuntimeError("managed B wheelhouse dependency binding digest differs")
    baseline_manifest = _manifest(baseline_manifest_raw)
    if baseline_manifest["contract"] != MANIFEST_CONTRACT:
        raise RuntimeError("managed B wheelhouse historical manifest must remain v1")
    baseline, _baseline_tree = _expected_wheels(
        baseline_coverage_raw, baseline_manifest, baseline_lock_raw
    )
    active, active_tree = _expected_wheels(
        aligned_coverage_raw, active_manifest, requirements_4d_raw
    )
    coverage = _json_object(aligned_coverage_raw, label="aligned wheel coverage")
    if (
        _sha256(baseline_coverage_raw) != coverage["baselineCoverageSha256"]
        or _sha256(dependency_binding_raw)
        != coverage["dependencySourceBindingSha256"]
    ):
        raise RuntimeError("managed B aligned wheel coverage provenance differs")
    expected_active = dict(baseline)
    for filename in REMOVED_DBOS_WHEELS:
        if expected_active.pop(filename, None) is None:
            raise RuntimeError("managed B historical wheel set omits a DBOS-only input")
    expected_active[PYNACL_AMD64_WHEEL] = PYNACL_AMD64_ARTIFACT
    expected_active = dict(sorted(expected_active.items()))
    if len(baseline) != 38 or len(active) != 35 or active != expected_active:
        raise RuntimeError("managed B aligned wheel set is not the exact 38-to-35 delta")
    binding = _json_object(dependency_binding_raw, label="runtime dependency binding")
    standalone = binding.get("standaloneLock")
    dependencies = binding.get("dependencies")
    protocol = binding.get("wheelhouseInstallProtocol")
    if (
        binding.get("contract") != "Eva2RuntimeDependencySourceBinding/v1"
        or not isinstance(standalone, dict)
        or standalone.get("path") != "eva2/requirements-4d.lock"
        or standalone.get("sha256") != active_manifest["requirementsLockSha256"]
        or standalone.get("packageCount") != 35
        or not isinstance(dependencies, dict)
        or not isinstance(protocol, dict)
    ):
        raise RuntimeError("managed B runtime dependency authority differs")
    pynacl = dependencies.get("pynacl")
    websockets = dependencies.get("websockets")
    if (
        set(dependencies) != {"pynacl", "websockets"}
        or not isinstance(pynacl, dict)
        or not isinstance(websockets, dict)
        or pynacl.get("version") != "1.6.2"
        or websockets.get("version") != "17.0.1"
        or binding.get("removedDistributions")
        != ["dbos", "python-dateutil", "pyyaml", "six"]
    ):
        raise RuntimeError("managed B runtime selected dependency differs")
    if (
        protocol.get("wheelCount") != 35
        or protocol.get("selectedManifestSha256")
        != active_manifest["selectedManifestSha256"]
    ):
        raise RuntimeError("managed B runtime selected manifest authority differs")
    return {
        "contract": "EvaManagedBRuntimeDependencyAlignmentReceipt/v1",
        "expected_wheel_count": 35,
        "network_used": False,
        "requirements_lock_sha256": active_manifest["requirementsLockSha256"],
        "requirements_lock_path": active_manifest["requirementsLockPath"],
        "selected_manifest_sha256": active_manifest["selectedManifestSha256"],
        "status": "version_matched_runtime_dependency_inputs",
        "wheelhouse_tree_sha256": active_tree,
    }


def _wheel_bytes(path: Path, *, expected_size: int) -> str:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("managed B wheelhouse contains a non-regular wheel")
    if before.st_size != expected_size:
        raise RuntimeError("managed B wheelhouse wheel size differs")
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    after = path.lstat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_ctime_ns,
    ):
        raise RuntimeError("managed B wheelhouse wheel changed while hashing")
    return digest.hexdigest()


def _sealed_source_entries(
    root: Path, expected: dict[str, tuple[str, int]]
) -> dict[str, Path]:
    try:
        metadata = root.lstat()
    except OSError as error:
        raise RuntimeError("managed B wheel artifact root is inaccessible") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("managed B wheel artifact root must be a regular directory")
    entries = list(os.scandir(root))
    if {entry.name for entry in entries} != set(expected):
        raise RuntimeError("managed B wheel artifact root file set differs from its manifest")
    result: dict[str, Path] = {}
    for entry in entries:
        source = Path(entry.path)
        metadata = source.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise RuntimeError("managed B wheel artifact root contains an unsafe entry")
        digest, length = expected[entry.name]
        if _wheel_bytes(source, expected_size=length) != digest:
            raise RuntimeError("managed B wheel artifact digest differs")
        result[entry.name] = source
    return result


def _copy_wheel(source: Path, destination: Path, *, expected_digest: str, expected_size: int) -> None:
    source_before = source.lstat()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, flags)
    except OSError as error:
        raise RuntimeError("managed B wheel artifact cannot be opened safely") from error
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != source_before.st_dev
            or opened.st_ino != source_before.st_ino
            or opened.st_size != expected_size
        ):
            raise RuntimeError("managed B wheel artifact changed before copying")
        destination_fd = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            digest = hashlib.sha256()
            length = 0
            while block := os.read(source_fd, 1024 * 1024):
                digest.update(block)
                length += len(block)
                view = memoryview(block)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise RuntimeError("managed B wheel copy could not make progress")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    source_after = source.lstat()
    if (
        source_before.st_dev,
        source_before.st_ino,
        source_before.st_mode,
        source_before.st_size,
        source_before.st_ctime_ns,
    ) != (
        source_after.st_dev,
        source_after.st_ino,
        source_after.st_mode,
        source_after.st_size,
        source_after.st_ctime_ns,
    ):
        raise RuntimeError("managed B wheel artifact changed while copying")
    if length != expected_size or digest.hexdigest() != expected_digest:
        raise RuntimeError("managed B wheel copy digest differs")
    destination.chmod(0o444)


def materialize_wheelhouse(
    manifest_raw: bytes,
    coverage_raw: bytes,
    artifact_root: Path,
    output_parent: Path,
    requirements_lock_raw: bytes,
) -> MaterializedWheelhouse:
    """Copy an exact local artifact set into a fresh private wheelhouse.

    ``artifact_root`` is deliberately caller-supplied.  This function makes no
    claim about how those bytes were acquired; it only makes their later
    offline use reproducible when they exactly match the sealed manifest.
    """

    manifest = _manifest(manifest_raw)
    expected, tree_sha256 = _expected_wheels(
        coverage_raw, manifest, requirements_lock_raw
    )
    sources = _sealed_source_entries(artifact_root, expected)
    try:
        parent_metadata = output_parent.lstat()
    except OSError as error:
        raise RuntimeError("managed B wheelhouse output parent is inaccessible") from error
    if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(parent_metadata.st_mode):
        raise RuntimeError("managed B wheelhouse output parent must be a regular directory")
    destination_root = Path(tempfile.mkdtemp(prefix="eva2-wheelhouse-", dir=output_parent))
    destination_root.chmod(0o700)
    try:
        for filename in sorted(expected):
            digest, length = expected[filename]
            _copy_wheel(
                sources[filename],
                destination_root / filename,
                expected_digest=digest,
                expected_size=length,
            )
        validated = validate_wheelhouse(
            manifest_raw, coverage_raw, destination_root, requirements_lock_raw
        )
        destination_root.chmod(0o500)
        if destination_root.lstat().st_mode & 0o222:
            raise RuntimeError("managed B wheelhouse cannot be made read-only")
    except Exception:
        destination_root.chmod(0o700)
        for entry in destination_root.iterdir():
            entry.unlink()
        destination_root.rmdir()
        raise
    receipt = {
        **validated,
        "contract": "EvaManagedBWheelhouseMaterializationReceipt/v1",
        "input_tree_sha256": tree_sha256,
        "nonclaims": [
            "The local artifact root is not proof of proxy-authenticated acquisition.",
            "No package was installed, imported, executed, or built into a Docker image.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
    }
    return MaterializedWheelhouse(destination_root, receipt)


def validate_wheelhouse(
    manifest_raw: bytes,
    coverage_raw: bytes,
    wheelhouse: Path,
    requirements_lock_raw: bytes,
) -> dict[str, object]:
    """Validate exact local wheel bytes without installation or network access."""

    manifest = _manifest(manifest_raw)
    expected, tree_sha256 = _expected_wheels(
        coverage_raw, manifest, requirements_lock_raw
    )
    try:
        metadata = wheelhouse.lstat()
    except OSError as error:
        raise RuntimeError("managed B wheelhouse is inaccessible") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("managed B wheelhouse must be a regular directory")
    root = wheelhouse.resolve(strict=True)
    entries = list(os.scandir(root))
    actual_names = {entry.name for entry in entries}
    if actual_names != set(expected):
        raise RuntimeError("managed B wheelhouse file set differs from its sealed manifest")
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            raise RuntimeError("managed B wheelhouse contains a non-regular entry")
        digest, length = expected[entry.name]
        if _wheel_bytes(Path(entry.path), expected_size=length) != digest:
            raise RuntimeError("managed B wheelhouse wheel digest differs")
    return {
        "contract": "EvaManagedBWheelhouseReceipt/v1",
        "coverage_sha256": manifest["coverageSha256"],
        "expected_wheel_count": manifest["expectedWheelCount"],
        "network_used": False,
        "requirements_lock_sha256": manifest["requirementsLockSha256"],
        "status": "passed",
        "wheelhouse_tree_sha256": tree_sha256,
    }


def inspect_wheelhouse_manifest(
    manifest_raw: bytes, coverage_raw: bytes, requirements_lock_raw: bytes
) -> dict[str, object]:
    """Validate the sealed expected wheel list before physical bytes arrive."""

    manifest = _manifest(manifest_raw)
    _expected, tree_sha256 = _expected_wheels(
        coverage_raw, manifest, requirements_lock_raw
    )
    return {
        "contract": "EvaManagedBWheelhouseManifestReceipt/v1",
        "coverage_sha256": manifest["coverageSha256"],
        "expected_wheel_count": manifest["expectedWheelCount"],
        "network_used": False,
        "requirements_lock_sha256": manifest["requirementsLockSha256"],
        "status": "metadata_validated_no_wheelhouse_bytes",
        "wheelhouse_tree_sha256": tree_sha256,
    }


def expected_wheel_artifacts(
    manifest_raw: bytes, coverage_raw: bytes, requirements_lock_raw: bytes
) -> dict[str, tuple[str, int]]:
    """Return the sealed wheel name, digest and size map without reading wheel bytes."""

    manifest = _manifest(manifest_raw)
    expected, _tree_sha256 = _expected_wheels(
        coverage_raw, manifest, requirements_lock_raw
    )
    return dict(expected)


__all__ = [
    "ACTIVE_MANIFEST_CONTRACT",
    "ALIGNED_COVERAGE_CONTRACT",
    "MANIFEST_CONTRACT",
    "MaterializedWheelhouse",
    "expected_wheel_artifacts",
    "inspect_wheelhouse_manifest",
    "inspect_runtime_dependency_alignment",
    "materialize_wheelhouse",
    "validate_wheelhouse",
]
