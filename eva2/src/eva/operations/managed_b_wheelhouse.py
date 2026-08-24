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
from typing import Any


MAX_WHEEL_BYTES = 64 * 1024 * 1024
MANIFEST_CONTRACT = "EvaManagedBWheelhouseManifest/v1"


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
    expected = {
        "contract",
        "coveragePath",
        "coverageSha256",
        "expectedWheelCount",
        "onlyBinary",
        "requirementsLockSha256",
        "target",
    }
    if set(value) != expected or value.get("contract") != MANIFEST_CONTRACT:
        raise RuntimeError("managed B wheelhouse manifest contract differs")
    if value.get("coveragePath") != "release/python-wheel-coverage.json":
        raise RuntimeError("managed B wheelhouse coverage path differs")
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
    return value


def _expected_wheels(
    coverage_raw: bytes, manifest: dict[str, Any], lock_raw: bytes
) -> tuple[dict[str, tuple[str, int]], str]:
    if _sha256(coverage_raw) != manifest["coverageSha256"]:
        raise RuntimeError("managed B wheelhouse coverage digest differs")
    if _sha256(lock_raw) != manifest["requirementsLockSha256"]:
        raise RuntimeError("managed B wheelhouse requirements lock digest differs")
    coverage = _json_object(coverage_raw, label="wheel coverage")
    if coverage.get("contract") != "Eva2PythonWheelCoverage/v1" or coverage.get("onlyBinary") is not True:
        raise RuntimeError("managed B wheelhouse coverage contract differs")
    targets = coverage.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("managed B wheelhouse coverage targets are invalid")
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
    return expected, _sha256(_canonical_json([
        {"byteLength": length, "filename": filename, "sha256": digest}
        for filename, (digest, length) in expected.items()
    ]))


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


__all__ = [
    "MANIFEST_CONTRACT",
    "inspect_wheelhouse_manifest",
    "validate_wheelhouse",
]
