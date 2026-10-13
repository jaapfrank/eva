from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile

import pytest

from tools.managed_b_candidate.offline_build import validate_offline_candidate_inputs
from tools.managed_b_candidate.wheelhouse import (
    MaterializedWheelhouse,
    materialize_wheelhouse,
)
from tools.prepare_managed_b_candidate_context import (
    CandidateBuildInputs,
    EXPECTED_OFFLINE_PIP_INSTALL,
)


BUILD_REFERENCE = "docker.io/library/python@sha256:" + "1" * 64
RUNTIME_LIBRARIES_REFERENCE = "docker.io/library/python@sha256:" + "2" * 64
RUNTIME_REFERENCE = "gcr.io/distroless/cc-debian13@sha256:" + "3" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _candidate(lock: bytes) -> CandidateBuildInputs:
    files = {
        "deploy/living-core/Dockerfile": b"\n".join(
            (
                b"FROM " + BUILD_REFERENCE.encode() + b" AS build",
                b"COPY deploy/requirements-4d.lock ./requirements-4d.lock",
                b"COPY wheelhouse ./wheelhouse",
                b"COPY pyproject.toml ./pyproject.toml",
                b"COPY src ./src",
                b"COPY connector_sdk ./connector_sdk",
                EXPECTED_OFFLINE_PIP_INSTALL.encode(),
                b"COPY deploy/alembic.ini ./alembic.ini",
                b"COPY migrations ./migrations",
                b"COPY schemas ./schemas",
                b"FROM " + RUNTIME_LIBRARIES_REFERENCE.encode() + b" AS runtime_libs",
                b"FROM " + RUNTIME_REFERENCE.encode(),
                b"ENV LD_LIBRARY_PATH=/usr/local/lib",
                b"COPY --from=build /usr/local /usr/local",
                b"COPY --from=build /opt/eva /opt/eva",
                b"COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/",
                b"COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/",
            )
        ),
        "deploy/requirements-4d.lock": lock,
        "pyproject.toml": b"[project]\nname='eva2-living-core'\n",
        "src/eva/__init__.py": b"",
        "src/connector_sdk/__init__.py": b"",
        "deploy/alembic.ini": b"[alembic]",
        "deploy/migrations/env.py": b"",
        "deploy/schemas/example.json": b"{}",
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
        build_image_reference=BUILD_REFERENCE,
        runtime_libraries_image_digest="sha256:" + "2" * 64,
        runtime_libraries_image_reference=RUNTIME_LIBRARIES_REFERENCE,
        runtime_image_digest="sha256:" + "3" * 64,
        runtime_image_reference=RUNTIME_REFERENCE,
        dockerfile_sha256=hashlib.sha256(dockerfile).hexdigest(),
        requirements_lock_sha256=hashlib.sha256(lock).hexdigest(),
        source_archive_sha256=hashlib.sha256(raw).hexdigest(),
        source_commit="a" * 40,
        source_date_epoch=1,
    )


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-offline-build-test-", dir="/tmp"
    ) as path:
        yield Path(path)


def _wheel_inputs(
    tmp_path: Path, native_tmp_path: Path, lock: bytes
) -> tuple[bytes, bytes, MaterializedWheelhouse]:
    content = b"one-wheel"
    digest = hashlib.sha256(content).hexdigest()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "one-1.0-py3-none-any.whl").write_bytes(content)
    coverage = _canonical(
        {
            "contract": "Eva2PythonWheelCoverage/v1",
            "onlyBinary": True,
            "targets": [
                {
                    "allArtifactsMatchedRequirementsLockHashes": True,
                    "architecture": "amd64",
                    "artifacts": [
                        {
                            "byteLength": len(content),
                            "filename": "one-1.0-py3-none-any.whl",
                            "sha256": digest,
                        }
                    ],
                    "lockedPackageCount": 1,
                    "os": "Ubuntu",
                    "pythonAbi": "cp312",
                    "pythonVersion": "3.12.14",
                    "resolvedWheelCount": 1,
                }
            ],
        }
    )
    manifest = _canonical(
        {
            "contract": "EvaManagedBWheelhouseManifest/v1",
            "coveragePath": "deploy/release/python-wheel-coverage.json",
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
    output = native_tmp_path / "output"
    output.mkdir()
    return (
        manifest,
        coverage,
        materialize_wheelhouse(manifest, coverage, wheelhouse, output, lock),
    )


def test_preflight_binds_candidate_source_and_exact_wheelhouse(
    tmp_path: Path, native_tmp_path: Path
) -> None:
    content = b"one-wheel"
    lock = b"one==1 --hash=sha256:" + hashlib.sha256(content).hexdigest().encode()
    candidate = _candidate(lock)
    manifest, coverage, wheelhouse = _wheel_inputs(tmp_path, native_tmp_path, lock)

    receipt = validate_offline_candidate_inputs(
        candidate, manifest, coverage, wheelhouse, lock
    )

    assert receipt["status"] == "passed"
    assert receipt["network_used"] is False
    assert receipt["wheelhouse_wheel_count"] == 1
    assert receipt["requirements_lock_path"] == "deploy/requirements-4d.lock"
    assert "Docker context" in receipt["nonclaims"][0]


def test_preflight_refuses_a_wheelhouse_lock_different_from_the_source(
    tmp_path: Path, native_tmp_path: Path
) -> None:
    content = b"one-wheel"
    lock = b"one==1 --hash=sha256:" + hashlib.sha256(content).hexdigest().encode()
    candidate = _candidate(lock)
    manifest, coverage, wheelhouse = _wheel_inputs(tmp_path, native_tmp_path, lock)

    with pytest.raises(RuntimeError, match="lock differs from source lock"):
        validate_offline_candidate_inputs(
            candidate, manifest, coverage, wheelhouse, lock + b"\n# changed"
        )


def test_preflight_revalidates_the_candidate_archive_before_wheel_bytes(
    tmp_path: Path, native_tmp_path: Path
) -> None:
    content = b"one-wheel"
    lock = b"one==1 --hash=sha256:" + hashlib.sha256(content).hexdigest().encode()
    candidate = _candidate(lock)
    manifest, coverage, wheelhouse = _wheel_inputs(tmp_path, native_tmp_path, lock)
    candidate = CandidateBuildInputs(
        archive_bytes=candidate.archive_bytes + b"tampered",
        base_image_digest=candidate.base_image_digest,
        build_image_reference=candidate.build_image_reference,
        runtime_libraries_image_digest=candidate.runtime_libraries_image_digest,
        runtime_libraries_image_reference=candidate.runtime_libraries_image_reference,
        runtime_image_digest=candidate.runtime_image_digest,
        runtime_image_reference=candidate.runtime_image_reference,
        dockerfile_sha256=candidate.dockerfile_sha256,
        requirements_lock_sha256=candidate.requirements_lock_sha256,
        source_archive_sha256=candidate.source_archive_sha256,
        source_commit=candidate.source_commit,
        source_date_epoch=candidate.source_date_epoch,
    )

    with pytest.raises(RuntimeError, match="archive digest differs"):
        validate_offline_candidate_inputs(
            candidate, manifest, coverage, wheelhouse, lock
        )


def test_preflight_refuses_a_materialized_wheelhouse_that_becomes_writable(
    tmp_path: Path, native_tmp_path: Path
) -> None:
    content = b"one-wheel"
    lock = b"one==1 --hash=sha256:" + hashlib.sha256(content).hexdigest().encode()
    candidate = _candidate(lock)
    manifest, coverage, wheelhouse = _wheel_inputs(tmp_path, native_tmp_path, lock)
    wheelhouse.path.chmod(0o700)

    with pytest.raises(RuntimeError, match="must remain read-only"):
        validate_offline_candidate_inputs(
            candidate, manifest, coverage, wheelhouse, lock
        )
