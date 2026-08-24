from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from eva.operations.managed_b_wheelhouse import (
    inspect_wheelhouse_manifest,
    materialize_wheelhouse,
    validate_wheelhouse,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _inputs(tmp_path: Path) -> tuple[bytes, bytes, bytes, Path]:
    wheels = [
        ("alpha-1.0-py3-none-any.whl", b"alpha-wheel"),
        ("beta-2.0-py3-none-any.whl", b"beta-wheel"),
    ]
    artifacts = [
        {"filename": name, "sha256": hashlib.sha256(content).hexdigest(), "byteLength": len(content)}
        for name, content in wheels
    ]
    coverage = _canonical(
        {
            "contract": "Eva2PythonWheelCoverage/v1",
            "onlyBinary": True,
            "targets": [
                {
                    "architecture": "amd64",
                    "os": "Ubuntu",
                    "pythonAbi": "cp312",
                    "pythonVersion": "3.12.14",
                    "lockedPackageCount": 2,
                    "resolvedWheelCount": 2,
                    "allArtifactsMatchedRequirementsLockHashes": True,
                    "artifacts": artifacts,
                }
            ],
        }
    )
    lock = b"\n".join(
        f"pkg==1 --hash=sha256:{artifact['sha256']}".encode() for artifact in artifacts
    )
    manifest = _canonical(
        {
            "contract": "EvaManagedBWheelhouseManifest/v1",
            "coveragePath": "release/python-wheel-coverage.json",
            "coverageSha256": hashlib.sha256(coverage).hexdigest(),
            "expectedWheelCount": 2,
            "onlyBinary": True,
            "requirementsLockSha256": hashlib.sha256(lock).hexdigest(),
            "target": {
                "architecture": "amd64",
                "os": "Ubuntu",
                "pythonAbi": "cp312",
                "pythonVersion": "3.12.14",
            },
        }
    )
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    for name, content in wheels:
        (wheelhouse / name).write_bytes(content)
    return manifest, coverage, lock, wheelhouse


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="eva2-wheelhouse-test-", dir="/tmp") as path:
        yield Path(path)


def test_wheelhouse_requires_the_exact_sealed_files(tmp_path: Path) -> None:
    manifest, coverage, lock, wheelhouse = _inputs(tmp_path)

    receipt = validate_wheelhouse(manifest, coverage, wheelhouse, lock)

    assert receipt["status"] == "passed"
    assert receipt["network_used"] is False
    assert receipt["expected_wheel_count"] == 2


def test_materializes_exact_local_bytes_into_a_new_read_only_wheelhouse(
    tmp_path: Path, native_tmp_path: Path
) -> None:
    manifest, coverage, lock, artifacts = _inputs(tmp_path / "source")
    output_parent = native_tmp_path / "output"
    output_parent.mkdir()

    materialized = materialize_wheelhouse(
        manifest, coverage, artifacts, output_parent, lock
    )

    assert materialized.receipt["status"] == "passed"
    assert materialized.receipt["network_used"] is False
    assert materialized.receipt["input_tree_sha256"] == materialized.receipt[
        "wheelhouse_tree_sha256"
    ]
    assert {path.name for path in materialized.path.iterdir()} == {
        "alpha-1.0-py3-none-any.whl",
        "beta-2.0-py3-none-any.whl",
    }
    assert materialized.path.stat().st_mode & 0o222 == 0
    assert all(path.stat().st_mode & 0o222 == 0 for path in materialized.path.iterdir())


def test_materializer_refuses_hardlinks_extra_and_changed_sources(tmp_path: Path) -> None:
    manifest, coverage, lock, artifacts = _inputs(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    (artifacts / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    with pytest.raises(RuntimeError, match="file set"):
        materialize_wheelhouse(manifest, coverage, artifacts, output_parent, lock)

    manifest, coverage, lock, artifacts = _inputs(tmp_path / "linked")
    output_parent = tmp_path / "output-linked"
    output_parent.mkdir()
    (artifacts / "alpha-1.0-py3-none-any.whl").unlink()
    (artifacts / "alpha-1.0-py3-none-any.whl").hardlink_to(
        artifacts / "beta-2.0-py3-none-any.whl"
    )
    with pytest.raises(RuntimeError, match="unsafe entry"):
        materialize_wheelhouse(manifest, coverage, artifacts, output_parent, lock)

    manifest, coverage, lock, artifacts = _inputs(tmp_path / "changed")
    output_parent = tmp_path / "output-changed"
    output_parent.mkdir()
    (artifacts / "alpha-1.0-py3-none-any.whl").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="(size|digest) differs"):
        materialize_wheelhouse(manifest, coverage, artifacts, output_parent, lock)


@pytest.mark.parametrize("mutation", ("missing", "extra", "sdist", "tampered"))
def test_wheelhouse_refuses_missing_extra_sdist_and_tampered_files(
    tmp_path: Path, mutation: str
) -> None:
    manifest, coverage, lock, wheelhouse = _inputs(tmp_path)
    if mutation == "missing":
        (wheelhouse / "alpha-1.0-py3-none-any.whl").unlink()
    elif mutation == "extra":
        (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    elif mutation == "sdist":
        (wheelhouse / "alpha-1.0-py3-none-any.whl").unlink()
        (wheelhouse / "alpha-1.0.tar.gz").write_bytes(b"source")
    else:
        (wheelhouse / "alpha-1.0-py3-none-any.whl").write_bytes(b"changed")

    with pytest.raises(RuntimeError):
        validate_wheelhouse(manifest, coverage, wheelhouse, lock)


def test_wheelhouse_refuses_symlinks_and_manifest_or_lock_drift(tmp_path: Path) -> None:
    manifest, coverage, lock, wheelhouse = _inputs(tmp_path)
    (wheelhouse / "alpha-1.0-py3-none-any.whl").unlink()
    (wheelhouse / "alpha-1.0-py3-none-any.whl").symlink_to(
        wheelhouse / "beta-2.0-py3-none-any.whl"
    )
    with pytest.raises(RuntimeError):
        validate_wheelhouse(manifest, coverage, wheelhouse, lock)

    manifest, coverage, lock, wheelhouse = _inputs(tmp_path / "second")
    with pytest.raises(RuntimeError, match="not canonical"):
        validate_wheelhouse(manifest + b" ", coverage, wheelhouse, lock)
    with pytest.raises(RuntimeError, match="requirements lock digest"):
        validate_wheelhouse(manifest, coverage, wheelhouse, lock + b"\n")
    with pytest.raises(RuntimeError, match="inaccessible"):
        validate_wheelhouse(manifest, coverage, tmp_path / "absent", lock)


def test_committed_manifest_binds_the_current_metadata_not_wheel_bytes() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_raw = (root / "release" / "managed-b-wheelhouse-amd64-v1.json").read_bytes()
    manifest = json.loads(manifest_raw)
    coverage = (root / "release" / "python-wheel-coverage.json").read_bytes()
    lock = (root / "requirements.lock").read_bytes()

    assert manifest["coverageSha256"] == hashlib.sha256(coverage).hexdigest()
    assert manifest["requirementsLockSha256"] == hashlib.sha256(lock).hexdigest()
    assert manifest["expectedWheelCount"] == 38
    assert inspect_wheelhouse_manifest(manifest_raw, coverage, lock)["status"] == (
        "metadata_validated_no_wheelhouse_bytes"
    )
