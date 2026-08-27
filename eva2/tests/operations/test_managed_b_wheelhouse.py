from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from eva.operations.managed_b_wheelhouse import (
    inspect_4d_dependency_alignment,
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


def _committed_alignment_inputs() -> tuple[bytes, bytes, bytes, bytes, bytes, bytes, bytes]:
    root = Path(__file__).resolve().parents[2]
    return (
        (root / "release" / "managed-b-wheelhouse-amd64-v2.json").read_bytes(),
        (root / "release" / "managed-b-wheel-coverage-4d-amd64-v1.json").read_bytes(),
        (root / "requirements-4d.lock").read_bytes(),
        (root / "release" / "managed-b-wheelhouse-amd64-v1.json").read_bytes(),
        (root / "release" / "python-wheel-coverage.json").read_bytes(),
        (root / "requirements.lock").read_bytes(),
        (root / "release" / "4d-dependency-source-binding.json").read_bytes(),
    )


def test_committed_v2_manifest_is_the_exact_version_matched_4d_delta() -> None:
    inputs = _committed_alignment_inputs()

    receipt = inspect_4d_dependency_alignment(*inputs)

    assert receipt == {
        "contract": "EvaManagedB4DDependencyAlignmentReceipt/v1",
        "expected_wheel_count": 39,
        "network_used": False,
        "requirements_lock_path": "requirements-4d.lock",
        "requirements_lock_sha256": (
            "b05efcbc41cf8dce64177c82e15430c510ef4f12e30242a4e5ea75d803c1f708"
        ),
        "selected_manifest_sha256": (
            "0e877383ffdef9d148a8d60729c91c2461685a31f001ee19787cfd96a4e56930"
        ),
        "status": "version_matched_4d_dependency_inputs",
        "wheelhouse_tree_sha256": (
            "63705d899207a8ce64a27086b38fb9699016fc64a0afb2f6a8c8d423b7c73629"
        ),
    }
    assert hashlib.sha256(inputs[3]).hexdigest() == (
        "07736abff46636ec48d5ffb454469e2ab4e8b7905ac725a695ecb2d34684d58d"
    )


def test_offline_install_receipt_stays_bound_to_the_sealed_39_wheel_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads(
        (
            root
            / "evidence/living-core/managed-b-amd64-offline-wheel-install-2026-08-27.json"
        ).read_bytes()
    )
    alignment = inspect_4d_dependency_alignment(*_committed_alignment_inputs())

    assert receipt["contract"] == "EvaManagedBOfflineWheelInstallReceipt/v1"
    assert receipt["status"] == "passed_offline_dependency_install_not_release"
    assert receipt["execution"]["network"] == "none"
    assert receipt["base_image"]["platform"] == "linux/amd64"
    assert receipt["base_image"]["python_version"] == "3.12.14"
    assert receipt["inputs"]["requirements_4d_lock_sha256"] == alignment[
        "requirements_lock_sha256"
    ]
    assert receipt["inputs"]["wheelhouse_tree_sha256"] == alignment[
        "wheelhouse_tree_sha256"
    ]
    assert receipt["observed"]["wheel_count"] == alignment["expected_wheel_count"]
    assert receipt["observed"]["runtime_distribution_count"] == 39
    assert receipt["observed"]["runtime_has_pip"] is False
    assert receipt["observed"]["runtime_has_wheel"] is False
    assert receipt["observed"]["dbos_distribution_version"] == "2.30.0+eva.1"
    assert receipt["observed"]["pynacl_distribution_version"] == "1.6.2"
    assert any("release readiness" in claim for claim in receipt["nonclaims"])


def test_current_candidate_image_receipt_stays_bound_to_the_sealed_39_wheel_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads(
        (
            root
            / "evidence/living-core/managed-b-current-candidate-image-smoke-2026-08-27.json"
        ).read_bytes()
    )
    alignment = inspect_4d_dependency_alignment(*_committed_alignment_inputs())

    assert receipt["contract"] == "EvaManagedBCandidateImageSmokeReceipt/v1"
    assert receipt["status"] == "passed_current_local_candidate_build_and_nonroot_smoke_not_deployed"
    assert receipt["network"] == {
        "candidate_build": "none",
        "runtime_smoke": "none",
        "wheel_acquisition": "network was used before sealing the exact wheelhouse; its provenance is not claimed",
    }
    assert receipt["inputs"]["requirements_4d_lock_sha256"] == alignment[
        "requirements_lock_sha256"
    ]
    assert receipt["inputs"]["wheelhouse_tree_sha256"] == alignment[
        "wheelhouse_tree_sha256"
    ]
    assert receipt["inputs"]["wheel_count"] == alignment["expected_wheel_count"]
    assert receipt["inputs"]["source_commit"] == "286095cba394019d1a80631b5e67c5789e5539be"
    assert receipt["observed"]["candidate_image_id"].startswith("sha256:")
    assert receipt["observed"]["candidate_image_size_bytes"] > 0
    assert "uid 65532" in receipt["observed"]["runtime_smoke"]
    assert any("release readiness" in claim for claim in receipt["nonclaims"])


def test_render_fixed_candidate_image_receipt_stays_bound_to_the_sealed_wheels() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads(
        (
            root
            / "evidence/living-core/managed-b-render-fixed-candidate-image-smoke-2026-08-27.json"
        ).read_bytes()
    )
    alignment = inspect_4d_dependency_alignment(*_committed_alignment_inputs())

    assert receipt["contract"] == "EvaManagedBRenderFixedCandidateImageSmokeReceipt/v1"
    assert receipt["status"] == "passed_render_fixed_candidate_image_smoke_not_deployed"
    assert receipt["network"] == {"candidate_build": "none", "runtime_smoke": "none"}
    assert receipt["inputs"]["requirements_4d_lock_sha256"] == alignment[
        "requirements_lock_sha256"
    ]
    assert receipt["inputs"]["wheelhouse_tree_sha256"] == alignment[
        "wheelhouse_tree_sha256"
    ]
    assert receipt["inputs"]["wheel_count"] == alignment["expected_wheel_count"]
    assert receipt["inputs"]["source_commit"] == "28104541f093d335729f0a1cf920914c97ca3036"
    assert receipt["observed"]["candidate_image_id"].startswith("sha256:")
    assert receipt["observed"]["candidate_image_size_bytes"] > 0


def test_current_candidate_schema_restart_receipt_stays_bound_to_sealed_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads(
        (
            root
            / "evidence/living-core/managed-b-current-candidate-schema-restart-2026-08-27.json"
        ).read_bytes()
    )
    alignment = inspect_4d_dependency_alignment(*_committed_alignment_inputs())

    assert receipt["contract"] == "EvaManagedBCurrentCandidateSchemaRestartReceipt/v1"
    assert receipt["status"] == (
        "passed_current_candidate_schema_restart_and_drift_rejection_not_deployed"
    )
    image = receipt["image_source"]
    assert image["requirements_4d_lock_sha256"] == alignment["requirements_lock_sha256"]
    assert image["wheelhouse_tree_sha256"] == alignment["wheelhouse_tree_sha256"]
    assert image["wheel_count"] == alignment["expected_wheel_count"]
    assert image["source_commit"] == "73648ea9b8d2022f639ce21c4a088f71b99cf4aa"
    assert receipt["observed"]["first_start"]["status"] == "passed"
    assert receipt["observed"]["second_start"]["route"] == []
    assert receipt["observed"]["drift_rejection"] == {
        "exit_code": 2,
        "missing_constraint": "fk_episodes_last_event",
        "status": "failed_closed",
    }
    assert "JSON receipt" in receipt["observed"]["stdout_protocol"]


def test_render_fixed_candidate_schema_restart_stays_bound_to_sealed_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads(
        (
            root
            / "evidence/living-core/managed-b-render-fixed-candidate-schema-restart-2026-08-27.json"
        ).read_bytes()
    )
    alignment = inspect_4d_dependency_alignment(*_committed_alignment_inputs())

    assert receipt["contract"] == "EvaManagedBRenderFixedCandidateSchemaRestartReceipt/v1"
    assert receipt["status"] == (
        "passed_render_fixed_candidate_schema_restart_and_drift_rejection_not_deployed"
    )
    image = receipt["image_source"]
    assert image["requirements_4d_lock_sha256"] == alignment["requirements_lock_sha256"]
    assert image["wheelhouse_tree_sha256"] == alignment["wheelhouse_tree_sha256"]
    assert image["wheel_count"] == alignment["expected_wheel_count"]
    assert image["source_commit"] == "28104541f093d335729f0a1cf920914c97ca3036"
    assert receipt["observed"]["first_start"]["status"] == "passed"
    assert receipt["observed"]["second_start"]["route"] == []
    assert receipt["observed"]["drift_rejection"] == {
        "exit_code": 2,
        "missing_constraint": "fk_episodes_last_event",
        "status": "failed_closed",
    }


def test_4d_alignment_refuses_stock_dbos_or_an_unrelated_wheel_change() -> None:
    active_manifest, coverage_raw, lock, baseline_manifest, baseline_coverage, baseline_lock, binding = (
        _committed_alignment_inputs()
    )
    coverage = json.loads(coverage_raw)
    artifacts = coverage["targets"][0]["artifacts"]
    patched = next(item for item in artifacts if item["filename"].startswith("dbos-2.30.0+"))
    patched.update(
        filename="dbos-2.30.0-py3-none-any.whl",
        sha256="363f8209f808fd42c59ad0f19f7e2c25abdd17d73b581dd8ada6336c4ef47e8e",
        byteLength=268218,
    )
    changed_coverage = _canonical(coverage)
    manifest = json.loads(active_manifest)
    manifest["coverageSha256"] = hashlib.sha256(changed_coverage).hexdigest()
    manifest["selectedManifestSha256"] = hashlib.sha256(
        _canonical([[item["filename"], item["sha256"]] for item in artifacts])
    ).hexdigest()

    with pytest.raises(RuntimeError, match="artifact is not sealed"):
        inspect_4d_dependency_alignment(
            _canonical(manifest),
            changed_coverage,
            lock,
            baseline_manifest,
            baseline_coverage,
            baseline_lock,
            binding,
        )

    active_manifest, coverage_raw, lock, baseline_manifest, baseline_coverage, baseline_lock, binding = (
        _committed_alignment_inputs()
    )
    coverage = json.loads(coverage_raw)
    artifacts = coverage["targets"][0]["artifacts"]
    artifacts[0]["byteLength"] += 1
    changed_coverage = _canonical(coverage)
    manifest = json.loads(active_manifest)
    manifest["coverageSha256"] = hashlib.sha256(changed_coverage).hexdigest()
    with pytest.raises(RuntimeError, match="exact 38-to-39 delta"):
        inspect_4d_dependency_alignment(
            _canonical(manifest),
            changed_coverage,
            lock,
            baseline_manifest,
            baseline_coverage,
            baseline_lock,
            binding,
        )


def test_4d_alignment_refuses_baseline_binding_lock_or_selected_manifest_drift() -> None:
    inputs = list(_committed_alignment_inputs())
    inputs[4] += b" "
    with pytest.raises(RuntimeError, match="coverage digest differs"):
        inspect_4d_dependency_alignment(*inputs)

    inputs = list(_committed_alignment_inputs())
    inputs[6] += b" "
    with pytest.raises(RuntimeError, match="binding digest differs"):
        inspect_4d_dependency_alignment(*inputs)

    inputs = list(_committed_alignment_inputs())
    inputs[2] += b"\n# changed"
    with pytest.raises(RuntimeError, match="requirements lock digest differs"):
        inspect_4d_dependency_alignment(*inputs)

    inputs = list(_committed_alignment_inputs())
    manifest = json.loads(inputs[0])
    manifest["selectedManifestSha256"] = "0" * 64
    inputs[0] = _canonical(manifest)
    with pytest.raises(RuntimeError, match="selected manifest differs"):
        inspect_4d_dependency_alignment(*inputs)


def test_4d_alignment_refuses_an_extra_target_even_when_rehashed() -> None:
    inputs = list(_committed_alignment_inputs())
    coverage = json.loads(inputs[1])
    coverage["targets"].append(copy.deepcopy(coverage["targets"][0]))
    changed_coverage = _canonical(coverage)
    manifest = json.loads(inputs[0])
    manifest["coverageSha256"] = hashlib.sha256(changed_coverage).hexdigest()
    inputs[0] = _canonical(manifest)
    inputs[1] = changed_coverage

    with pytest.raises(RuntimeError, match="target set differs"):
        inspect_4d_dependency_alignment(*inputs)
