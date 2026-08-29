from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile

import pytest

from eva.operations.managed_b_base_image import materialize_pinned_base_image
from eva.operations.managed_b_build_context import (
    materialize_build_context,
    validate_materialized_build_context,
)
from eva.operations.managed_b_wheelhouse import materialize_wheelhouse
from tools.prepare_managed_b_candidate_context import (
    CandidateBuildInputs,
    EXPECTED_OFFLINE_PIP_INSTALL,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="eva2-build-context-test-", dir="/tmp") as path:
        yield Path(path)


def _candidate(lock: bytes) -> CandidateBuildInputs:
    files = {
        "deploy/living-core/Dockerfile": b"\n".join(
            (
                b"FROM docker.io/library/python@sha256:" + b"1" * 64,
                b"COPY requirements-4d.lock ./requirements-4d.lock",
                b"COPY wheelhouse ./wheelhouse",
                EXPECTED_OFFLINE_PIP_INSTALL.encode(),
                b"COPY src ./src",
                b"COPY connector_sdk ./connector_sdk",
                b"COPY alembic.ini ./alembic.ini",
                b"COPY migrations ./migrations",
                b"COPY schemas ./schemas",
            )
        ),
        "requirements-4d.lock": lock,
        "src/eva/__init__.py": b"",
        "connector_sdk/__init__.py": b"",
        "alembic.ini": b"[alembic]",
        "migrations/env.py": b"",
        "schemas/example.json": b"{}",
    }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.gid = member.uid = 0
            member.gname = member.uname = ""
            member.mode = 0o644
            member.mtime = 1
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    raw = archive.getvalue()
    dockerfile = files["deploy/living-core/Dockerfile"]
    return CandidateBuildInputs(
        archive_bytes=raw,
        base_image_digest="sha256:" + "1" * 64,
        dockerfile_sha256=hashlib.sha256(dockerfile).hexdigest(),
        requirements_lock_sha256=hashlib.sha256(lock).hexdigest(),
        source_archive_sha256=hashlib.sha256(raw).hexdigest(),
        source_commit="a" * 40,
        source_date_epoch=1,
    )


def _wheelhouse(tmp_path: Path, native_tmp_path: Path, lock: bytes) -> tuple[bytes, bytes, object]:
    content = b"one-wheel"
    digest = hashlib.sha256(content).hexdigest()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "one-1.0-py3-none-any.whl").write_bytes(content)
    coverage = _canonical(
        {
            "contract": "Eva2PythonWheelCoverage/v1",
            "onlyBinary": True,
            "targets": [{
                "allArtifactsMatchedRequirementsLockHashes": True,
                "architecture": "amd64",
                "artifacts": [{
                    "byteLength": len(content),
                    "filename": "one-1.0-py3-none-any.whl",
                    "sha256": digest,
                }],
                "lockedPackageCount": 1,
                "os": "Ubuntu",
                "pythonAbi": "cp312",
                "pythonVersion": "3.12.14",
                "resolvedWheelCount": 1,
            }],
        }
    )
    manifest = _canonical(
        {
            "contract": "EvaManagedBWheelhouseManifest/v1",
            "coveragePath": "release/python-wheel-coverage.json",
            "coverageSha256": hashlib.sha256(coverage).hexdigest(),
            "expectedWheelCount": 1,
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
    output = native_tmp_path / "wheelhouse-output"
    output.mkdir(mode=0o700)
    return manifest, coverage, materialize_wheelhouse(manifest, coverage, artifacts, output, lock)


def _base_archive(path: Path) -> tuple[str, dict[str, object]]:
    layer = b"one-layer"
    config = _canonical({"architecture": "amd64", "os": "linux"})
    layer_digest = _digest(layer)
    config_digest = _digest(config)
    manifest = _canonical(
        {"config": {"digest": config_digest, "mediaType": "application/vnd.oci.image.config.v1+json", "size": len(config)}, "layers": [{"digest": layer_digest, "mediaType": "application/vnd.oci.image.layer.v1.tar", "size": len(layer)}], "mediaType": "application/vnd.oci.image.manifest.v1+json", "schemaVersion": 2}
    )
    manifest_digest = _digest(manifest)
    nested = _canonical(
        {"manifests": [{"digest": manifest_digest, "mediaType": "application/vnd.oci.image.manifest.v1+json", "platform": {"architecture": "amd64", "os": "linux"}, "size": len(manifest)}], "mediaType": "application/vnd.oci.image.index.v1+json", "schemaVersion": 2}
    )
    root_digest = _digest(nested)
    index = _canonical(
        {"manifests": [{"digest": root_digest, "mediaType": "application/vnd.oci.image.index.v1+json", "size": len(nested)}], "mediaType": "application/vnd.oci.image.index.v1+json", "schemaVersion": 2}
    )
    files = {
        "oci-layout": _canonical({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        "manifest.json": _canonical([{"Config": f"blobs/sha256/{config_digest[7:]}", "Layers": [f"blobs/sha256/{layer_digest[7:]}"], "RepoTags": None}]),
        f"blobs/sha256/{root_digest[7:]}": nested,
        f"blobs/sha256/{manifest_digest[7:]}": manifest,
        f"blobs/sha256/{config_digest[7:]}": config,
        f"blobs/sha256/{layer_digest[7:]}": layer,
    }
    with tarfile.open(path, "w") as bundle:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    return root_digest, {"Architecture": "amd64", "Id": root_digest, "Os": "linux", "RepoDigests": [f"python@{root_digest}"]}


def _base_image(native_tmp_path: Path):
    source = native_tmp_path / "base-source.tar"
    digest, inspection = _base_archive(source)
    output = native_tmp_path / "base-output"
    output.mkdir(mode=0o700)
    return materialize_pinned_base_image(
        digest,
        output,
        inspector=lambda _reference: inspection,
        exporter=lambda _reference, destination: destination.write_bytes(source.read_bytes()),
    )


def _inputs(tmp_path: Path, native_tmp_path: Path):
    wheel = b"one-wheel"
    lock = b"one==1 --hash=sha256:" + hashlib.sha256(wheel).hexdigest().encode()
    return _candidate(lock), *_wheelhouse(tmp_path, native_tmp_path, lock), _base_image(native_tmp_path)


def test_materializes_only_sealed_source_and_wheels(tmp_path: Path, native_tmp_path: Path) -> None:
    candidate, manifest, coverage, wheelhouse, base_image = _inputs(tmp_path, native_tmp_path)
    output = native_tmp_path / "context-output"
    output.mkdir(mode=0o700)

    context = materialize_build_context(candidate, manifest, coverage, wheelhouse, base_image, output)

    receipt = validate_materialized_build_context(context)
    assert receipt["status"] == "materialized"
    assert receipt["network_used"] is False
    assert not (context.path / "base.oci.tar").exists()
    assert (context.path / "wheelhouse" / "one-1.0-py3-none-any.whl").read_bytes() == b"one-wheel"
    assert context.path.stat().st_mode & 0o222 == 0
    assert (context.path / "wheelhouse").stat().st_mode & 0o222 == 0


def test_revalidation_refuses_writable_nested_directory(tmp_path: Path, native_tmp_path: Path) -> None:
    candidate, manifest, coverage, wheelhouse, base_image = _inputs(tmp_path, native_tmp_path)
    output = native_tmp_path / "context-output"
    output.mkdir(mode=0o700)
    context = materialize_build_context(candidate, manifest, coverage, wheelhouse, base_image, output)
    (context.path / "src").chmod(0o755)

    with pytest.raises(RuntimeError, match="writable directory"):
        validate_materialized_build_context(context)


def test_refuses_a_wheel_added_between_preflight_and_context_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, native_tmp_path: Path
) -> None:
    candidate, manifest, coverage, wheelhouse, base_image = _inputs(tmp_path, native_tmp_path)
    output = native_tmp_path / "context-output"
    output.mkdir(mode=0o700)

    def add_unsealed_wheel(materialized):
        wheelhouse.path.chmod(0o700)
        unexpected = wheelhouse.path / "unexpected-1.0-py3-none-any.whl"
        unexpected.write_bytes(b"not-sealed")
        unexpected.chmod(0o444)
        wheelhouse.path.chmod(0o500)
        return materialized

    monkeypatch.setattr(
        "eva.operations.managed_b_build_context.validate_materialized_base_image",
        add_unsealed_wheel,
    )

    with pytest.raises(RuntimeError, match="file set differs before context copy"):
        materialize_build_context(candidate, manifest, coverage, wheelhouse, base_image, output)

    assert list(output.iterdir()) == []


def test_failed_post_seal_validation_removes_only_its_fresh_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, native_tmp_path: Path
) -> None:
    candidate, manifest, coverage, wheelhouse, base_image = _inputs(tmp_path, native_tmp_path)
    output = native_tmp_path / "context-output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        "eva.operations.managed_b_build_context._actual_entries",
        lambda _root: [],
    )

    with pytest.raises(RuntimeError, match="tree differs after sealing"):
        materialize_build_context(candidate, manifest, coverage, wheelhouse, base_image, output)

    assert list(output.iterdir()) == []
