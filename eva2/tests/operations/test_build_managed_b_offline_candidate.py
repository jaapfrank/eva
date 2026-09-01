from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from tools.build_managed_b_offline_candidate import (
    ACTIVE_COVERAGE_PATH,
    WHEELHOUSE_MANIFEST_PATH,
    build_managed_b_offline_candidate,
    main,
)
from tools.managed_b_candidate.candidate_image import (
    CandidateImage,
    validate_offline_candidate_image,
)


COMMIT = "a" * 40


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-managed-offline-cli-test-", dir="/tmp"
    ) as path:
        yield Path(path)


@pytest.fixture
def private_output(native_tmp_path: Path) -> Path:
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    return output


def _stub_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    wheel_count: int = 38,
) -> list[str]:
    calls: list[str] = []
    candidate = SimpleNamespace(
        base_image_digest="sha256:" + "1" * 64,
        build_image_reference="docker.io/library/python@sha256:" + "1" * 64,
        runtime_libraries_image_digest="sha256:" + "2" * 64,
        runtime_libraries_image_reference=(
            "docker.io/library/python@sha256:" + "2" * 64
        ),
        runtime_image_digest="sha256:" + "3" * 64,
        runtime_image_reference=("gcr.io/distroless/cc-debian13@sha256:" + "3" * 64),
        dockerfile_sha256="2" * 64,
        requirements_lock_sha256="3" * 64,
        source_archive_sha256="4" * 64,
        source_commit=COMMIT,
        source_date_epoch=1787480000,
    )
    files = {
        WHEELHOUSE_MANIFEST_PATH: (b"manifest", 0o644),
        ACTIVE_COVERAGE_PATH: (b"coverage", 0o644),
        "requirements-4d.lock": (b"lock", 0o644),
    }
    wheelhouse = SimpleNamespace(
        path=tmp_path / "materialized-wheelhouse",
        receipt={"expected_wheel_count": 38, "wheelhouse_tree_sha256": "5" * 64},
    )

    def materialized_base(name: str, digest: str, archive: str):
        return SimpleNamespace(
            path=tmp_path / name / "base.oci.tar",
            receipt={
                "archive_sha256": archive * 64,
                "base_image_digest": digest,
                "local_image_id": digest,
            },
        )

    build = materialized_base("build", candidate.base_image_digest, "6")
    runtime_libraries = materialized_base(
        "runtime-libraries", candidate.runtime_libraries_image_digest, "9"
    )
    runtime = materialized_base("runtime", candidate.runtime_image_digest, "a")
    context = SimpleNamespace(
        path=tmp_path / "context",
        receipt={"context_tree_sha256": "7" * 64},
    )
    image = SimpleNamespace(
        image_id="sha256:" + "8" * 64,
        receipt={
            "build_image_archive_sha256": "6" * 64,
            "build_image_digest": candidate.base_image_digest,
            "build_image_reference": candidate.build_image_reference,
            "candidate_image_id": "sha256:" + "8" * 64,
            "candidate_image_size": 123456,
            "candidate_runtime_library_path": "LD_LIBRARY_PATH=/usr/local/lib",
            "candidate_source_commit": COMMIT,
            "candidate_source_date_epoch": candidate.source_date_epoch,
            "context_tree_sha256": "7" * 64,
            "contract": "EvaManagedBOfflineCandidateImageReceipt/v2",
            "network_used": False,
            "nonclaims": [],
            "status": "built_local_candidate",
            "runtime_libraries_image_archive_sha256": "9" * 64,
            "runtime_libraries_image_digest": candidate.runtime_libraries_image_digest,
            "runtime_libraries_image_reference": candidate.runtime_libraries_image_reference,
            "runtime_image_archive_sha256": "a" * 64,
            "runtime_image_digest": candidate.runtime_image_digest,
            "runtime_image_reference": candidate.runtime_image_reference,
        },
    )

    def prepare(commit: str):
        calls.append("prepare")
        assert commit == COMMIT
        return candidate

    def validate(value):
        calls.append("validate")
        assert value is candidate
        return files

    def inspect(manifest: bytes, coverage: bytes, lock: bytes):
        calls.append("inspect_manifest")
        assert (manifest, coverage, lock) == (b"manifest", b"coverage", b"lock")
        return {"expected_wheel_count": wheel_count}

    def wheels(manifest, coverage, artifact_root, output_parent, lock):
        calls.append("materialize_wheelhouse")
        assert (manifest, coverage, lock) == (b"manifest", b"coverage", b"lock")
        assert artifact_root == tmp_path / "artifacts"
        assert output_parent == tmp_path / "output"
        return wheelhouse

    def base_image(digest, output_parent, *, repository="docker.io/library/python"):
        calls.append(f"materialize_base:{repository}")
        assert output_parent == tmp_path / "output"
        return {
            candidate.base_image_digest: build,
            candidate.runtime_libraries_image_digest: runtime_libraries,
            candidate.runtime_image_digest: runtime,
        }[digest]

    def build_context(*args):
        calls.append("materialize_context")
        assert args == (
            candidate,
            b"manifest",
            b"coverage",
            wheelhouse,
            build,
            runtime_libraries,
            runtime,
            tmp_path / "output",
        )
        return context

    def build_image(*args):
        calls.append("build_image")
        assert args == (
            context,
            build,
            runtime_libraries,
            runtime,
            tmp_path / "output",
        )
        return image

    module = "tools.build_managed_b_offline_candidate"
    monkeypatch.setattr(f"{module}.prepare_candidate_build_inputs", prepare)
    monkeypatch.setattr(f"{module}.validate_candidate_build_inputs", validate)
    monkeypatch.setattr(f"{module}.inspect_wheelhouse_manifest", inspect)
    monkeypatch.setattr(f"{module}.materialize_wheelhouse", wheels)
    monkeypatch.setattr(f"{module}.materialize_pinned_base_image", base_image)
    monkeypatch.setattr(f"{module}.materialize_build_context", build_context)
    monkeypatch.setattr(f"{module}.build_offline_candidate_image", build_image)
    return calls


def test_sequences_only_the_existing_offline_candidate_stages(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path, private_output: Path
) -> None:
    artifact_root = native_tmp_path / "artifacts"
    artifact_root.mkdir()
    calls = _stub_pipeline(monkeypatch, native_tmp_path)

    receipt = build_managed_b_offline_candidate(COMMIT, artifact_root, private_output)

    assert calls == [
        "prepare",
        "validate",
        "inspect_manifest",
        "materialize_wheelhouse",
        "materialize_base:docker.io/library/python",
        "materialize_base:docker.io/library/python",
        "materialize_base:gcr.io/distroless/cc-debian13",
        "materialize_context",
        "build_image",
    ]
    assert receipt["status"] == "built_local_untagged_candidate"
    assert receipt["network_used"] is False
    assert receipt["candidate"]["image_id"] == "sha256:" + "8" * 64
    assert receipt["candidate"]["receipt"]["contract"] == (
        "EvaManagedBOfflineCandidateImageReceipt/v2"
    )
    assert receipt["source"]["commit"] == COMMIT
    assert receipt["stages"]["wheelhouse"]["count"] == 38
    assert receipt["materialized"] == {
        "build_image_archive_path": str(native_tmp_path / "build" / "base.oci.tar"),
        "build_context_path": str(native_tmp_path / "context"),
        "runtime_image_archive_path": str(native_tmp_path / "runtime" / "base.oci.tar"),
        "runtime_libraries_image_archive_path": str(
            native_tmp_path / "runtime-libraries" / "base.oci.tar"
        ),
        "wheelhouse_path": str(native_tmp_path / "materialized-wheelhouse"),
    }

    decoded = json.loads(json.dumps(receipt))
    reconstructed = CandidateImage(
        image_id=decoded["candidate"]["image_id"],
        receipt=decoded["candidate"]["receipt"],
    )

    def inspect_runner(
        command: list[str], _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "image", "inspect", reconstructed.image_id]
        inspection = [
            {
                "Config": {
                    "Env": ["LD_LIBRARY_PATH=/usr/local/lib"],
                    "Labels": {
                        "io.eva2.source-commit": COMMIT,
                        "io.eva2.source-date-epoch": "1787480000",
                        "io.eva2.source-refresh": "git-archive-v1",
                    },
                },
                "Id": reconstructed.image_id,
                "RepoTags": None,
                "Size": 123456,
            }
        ]
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(inspection), stderr=""
        )

    assert (
        validate_offline_candidate_image(reconstructed, runner=inspect_runner)
        == decoded["candidate"]["receipt"]
    )


def test_refuses_a_relative_artifact_root_before_opening_source(
    monkeypatch: pytest.MonkeyPatch, private_output: Path
) -> None:
    monkeypatch.setattr(
        "tools.build_managed_b_offline_candidate.prepare_candidate_build_inputs",
        lambda _commit: (_ for _ in ()).throw(AssertionError("source opened")),
    )

    with pytest.raises(RuntimeError, match="explicit absolute"):
        build_managed_b_offline_candidate(COMMIT, Path("wheelhouse"), private_output)


def test_refuses_a_nonprivate_output_parent(native_tmp_path: Path) -> None:
    output = native_tmp_path / "output"
    output.mkdir(mode=0o755)

    with pytest.raises(RuntimeError, match="must be private"):
        build_managed_b_offline_candidate(COMMIT, native_tmp_path / "artifacts", output)


def test_refuses_a_non_38_wheel_authority_before_copy_or_docker(
    monkeypatch: pytest.MonkeyPatch,
    native_tmp_path: Path,
    private_output: Path,
) -> None:
    artifact_root = native_tmp_path / "artifacts"
    artifact_root.mkdir()
    calls = _stub_pipeline(monkeypatch, native_tmp_path, wheel_count=37)

    with pytest.raises(RuntimeError, match="exact 38-wheel"):
        build_managed_b_offline_candidate(COMMIT, artifact_root, private_output)

    assert calls == ["prepare", "validate", "inspect_manifest"]


def test_cli_emits_one_json_failure_without_starting_a_build(
    monkeypatch: pytest.MonkeyPatch,
    native_tmp_path: Path,
    private_output: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "tools.build_managed_b_offline_candidate.build_managed_b_offline_candidate",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("sealed input differs")),
    )

    result = main(
        [
            COMMIT,
            "--wheel-artifact-root",
            str(native_tmp_path / "artifacts"),
            "--output-parent",
            str(private_output),
        ]
    )

    assert result == 2
    assert capsys.readouterr().out == (
        '{"error": "sealed input differs", "status": "failed"}\n'
    )
