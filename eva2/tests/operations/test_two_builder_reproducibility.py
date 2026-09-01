from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools.check_two_builder_reproducibility import (
    TwoBuilderError,
    verify_two_builds,
)


COMMIT = "a" * 40


def _canonical(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _builder_measurement(side: str) -> dict[str, object]:
    return {
        "backend": "host-dockerd",
        "buildkitNodeId": f"node-{side}",
        "buildkitVersion": "v0.26.2",
        "daemonId": f"daemon-{side}",
        "dockerExecutableSha256": "1" * 64,
        "dockerServerVersion": "29.1.3",
        "socketIdentitySha256": ("2" if side == "a" else "3") * 64,
        "stateRootIdentitySha256": ("4" if side == "a" else "5") * 64,
    }


def _builder(side: str) -> dict[str, object]:
    identity = _builder_measurement(side)
    return {
        "after": dict(identity),
        "before": dict(identity),
        "contract": "EvaMeasuredDockerBuilder/v2",
        "runChallenge": ("a" if side == "a" else "b") * 64,
        "singleHost": True,
    }


def _build(side: str, image_id: str, archive_sha256: str) -> dict[str, object]:
    candidate_receipt = {
        "builder": _builder(side),
        "candidate_image_archive_sha256": archive_sha256,
        "candidate_image_id": image_id,
        "candidate_source_commit": COMMIT,
        "network_used": False,
    }
    return {
        "candidate": {"image_id": image_id, "receipt": candidate_receipt},
        "contract": "EvaManagedBOfflineCandidateBuildReceipt/v2",
        "inputs": {
            "wheel_coverage_sha256": "6" * 64,
            "wheelhouse_manifest_sha256": "7" * 64,
        },
        "network_used": False,
        "source": {
            "archive_sha256": "8" * 64,
            "commit": COMMIT,
            "date_epoch": 1788260000,
            "dockerfile_sha256": "9" * 64,
            "requirements_lock_sha256": "a" * 64,
        },
        "stages": {
            "build_context": {"tree_sha256": "b" * 64},
            "build_image": {
                "archive_sha256": "c" * 64,
                "digest": "sha256:" + "d" * 64,
            },
            "runtime_libraries_image": {
                "archive_sha256": "e" * 64,
                "digest": "sha256:" + "f" * 64,
            },
            "runtime_image": {
                "archive_sha256": "0" * 64,
                "digest": "sha256:" + "1" * 64,
            },
            "wheelhouse": {"tree_sha256": "2" * 64},
        },
        "status": "built_local_untagged_candidate",
    }


def _archive(path: Path, *, mtime: int, user: str = "65532:65532") -> str:
    layer = b"deterministic uncompressed layer"
    config = {
        "architecture": "amd64",
        "config": {"Cmd": ["-m", "eva"], "User": user},
        "os": "linux",
        "rootfs": {
            "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
            "type": "layers",
        },
    }
    config_raw = json.dumps(config, separators=(",", ":"), sort_keys=True).encode()
    config_name = hashlib.sha256(config_raw).hexdigest() + ".json"
    manifest = json.dumps(
        [{"Config": config_name, "Layers": ["layer.tar"], "RepoTags": None}],
        separators=(",", ":"),
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, raw in (
            ("manifest.json", manifest),
            (config_name, config_raw),
            ("layer.tar", layer),
        ):
            info = tarfile.TarInfo(name)
            info.mode = 0o444
            info.mtime = mtime
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    path.chmod(0o444)
    return "sha256:" + hashlib.sha256(config_raw).hexdigest()


def _write(path: Path, value: object) -> None:
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(_canonical(value))
    path.chmod(0o444)


def _write_build(path: Path, archive: Path, value: dict[str, object]) -> None:
    _write(path, value)
    candidate = value["candidate"]
    assert isinstance(candidate, dict)
    receipt = candidate["receipt"]
    _write(archive.parent / "candidate-image-receipt.json", receipt)


def _fixture(
    tmp_path: Path,
) -> tuple[tuple[Path, Path], tuple[Path, Path]]:
    roots = (tmp_path / "artifact-a", tmp_path / "artifact-b")
    for root in roots:
        root.mkdir(mode=0o700)
    archives = tuple(root / "candidate.tar" for root in roots)
    image_id = _archive(archives[0], mtime=1)
    assert _archive(archives[1], mtime=2) == image_id
    builds = (tmp_path / "build-a.json", tmp_path / "build-b.json")
    for side, path, archive in zip(("a", "b"), builds, archives, strict=True):
        _write_build(
            path,
            archive,
            _build(side, image_id, hashlib.sha256(archive.read_bytes()).hexdigest()),
        )
    return builds, archives


def test_accepts_same_image_semantics_with_different_tar_envelopes(
    tmp_path: Path,
) -> None:
    builds, archives = _fixture(tmp_path)

    result = verify_two_builds(
        build_paths=builds,
        archive_paths=archives,
        expected_source_commit=COMMIT,
    )

    assert result["status"] == "passed"
    assert result["candidate"]["imageId"].startswith("sha256:")
    assert (
        result["candidate"]["archiveSha256"]
        == hashlib.sha256(archives[0].read_bytes()).hexdigest()
    )
    assert result["builders"][0]["daemonId"] != result["builders"][1]["daemonId"]
    assert (
        result["builders"][0]["buildReceiptSha256"]
        != result["builders"][1]["buildReceiptSha256"]
    )
    assert "one host" in result["nonclaims"][0]


@pytest.mark.parametrize(
    "field",
    ["buildkitNodeId", "daemonId", "socketIdentitySha256", "stateRootIdentitySha256"],
)
def test_rejects_shared_builder_identity(tmp_path: Path, field: str) -> None:
    builds, archives = _fixture(tmp_path)
    left = json.loads(builds[0].read_bytes())
    right = json.loads(builds[1].read_bytes())
    left_identity = left["candidate"]["receipt"]["builder"]["before"]
    right_binding = right["candidate"]["receipt"]["builder"]
    right_binding["before"][field] = left_identity[field]
    right_binding["after"][field] = left_identity[field]
    _write_build(builds[1], archives[1], right)

    with pytest.raises(TwoBuilderError, match="not independent"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )


def test_rejects_toolchain_or_input_drift(tmp_path: Path) -> None:
    builds, archives = _fixture(tmp_path)
    right_build = json.loads(builds[1].read_bytes())
    right_builder = right_build["candidate"]["receipt"]["builder"]
    right_builder["before"]["buildkitVersion"] = "v0.27.0"
    right_builder["after"]["buildkitVersion"] = "v0.27.0"
    _write_build(builds[1], archives[1], right_build)
    with pytest.raises(TwoBuilderError, match="toolchains differ"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )

    right_build = _build(
        "b",
        str(json.loads(builds[0].read_bytes())["candidate"]["image_id"]),
        hashlib.sha256(archives[1].read_bytes()).hexdigest(),
    )
    right_build["source"]["dockerfile_sha256"] = "3" * 64
    _write_build(builds[1], archives[1], right_build)
    with pytest.raises(TwoBuilderError, match="input authorities differ"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )


def test_rejects_runtime_or_archive_drift(tmp_path: Path) -> None:
    builds, archives = _fixture(tmp_path)
    different_root = tmp_path / "different-artifact"
    different_root.mkdir(mode=0o700)
    different = different_root / "candidate.tar"
    different_id = _archive(different, mtime=3, user="0:0")
    right = json.loads(builds[1].read_bytes())
    right["candidate"]["image_id"] = different_id
    right["candidate"]["receipt"]["candidate_image_id"] = different_id
    right["candidate"]["receipt"]["candidate_image_archive_sha256"] = hashlib.sha256(
        different.read_bytes()
    ).hexdigest()
    _write_build(builds[1], different, right)

    with pytest.raises(TwoBuilderError, match="semantics differ"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=(archives[0], different),
            expected_source_commit=COMMIT,
        )


def test_rejects_reused_build_receipt_even_at_a_second_path(tmp_path: Path) -> None:
    builds, archives = _fixture(tmp_path)
    reused = json.loads(builds[0].read_bytes())
    _write_build(builds[1], archives[1], reused)

    with pytest.raises(TwoBuilderError, match="build receipt was reused"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )


def test_rejects_candidate_receipt_not_bound_to_build(tmp_path: Path) -> None:
    builds, archives = _fixture(tmp_path)
    persisted = json.loads(
        (archives[1].parent / "candidate-image-receipt.json").read_bytes()
    )
    persisted["candidate_source_commit"] = "b" * 40
    _write(archives[1].parent / "candidate-image-receipt.json", persisted)

    with pytest.raises(TwoBuilderError, match="persisted candidate receipt differs"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )


def test_rejects_reused_run_challenge_or_midbuild_identity_change(
    tmp_path: Path,
) -> None:
    builds, archives = _fixture(tmp_path)
    left = json.loads(builds[0].read_bytes())
    right = json.loads(builds[1].read_bytes())
    right_builder = right["candidate"]["receipt"]["builder"]
    right_builder["runChallenge"] = left["candidate"]["receipt"]["builder"][
        "runChallenge"
    ]
    _write_build(builds[1], archives[1], right)
    with pytest.raises(TwoBuilderError, match="challenge was reused"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )

    right = _build(
        "b",
        str(left["candidate"]["image_id"]),
        hashlib.sha256(archives[1].read_bytes()).hexdigest(),
    )
    right["candidate"]["receipt"]["builder"]["after"]["daemonId"] = "changed"
    _write_build(builds[1], archives[1], right)
    with pytest.raises(TwoBuilderError, match="changed during build"):
        verify_two_builds(
            build_paths=builds,
            archive_paths=archives,
            expected_source_commit=COMMIT,
        )
