from __future__ import annotations

import hashlib
import gzip
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools.check_two_builder_reproducibility import (
    TwoBuilderError,
    _image_semantics,
    verify_single_builder_a,
    verify_two_builds,
)


COMMIT = "a" * 40


def _canonical(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _builder_measurement(side: str) -> dict[str, object]:
    return {
        "backend": "docker-buildx/docker",
        "buildkitNodeId": f"node-{side}",
        "buildkitVersion": "v0.26.2",
        "containerdContainersNamespace": f"eva-test-{side}",
        "containerdImageStore": "io.containerd.snapshotter.v1",
        "containerdPluginsNamespace": f"eva-test-{side}-plugins",
        "containerdSocketIdentitySha256": ("6" if side == "a" else "7") * 64,
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
        "candidate_buildkit_result_digest": image_id,
        "candidate_image_archive_sha256": archive_sha256,
        "candidate_image_config_digest": image_id,
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


def _compressed_archive(path: Path) -> str:
    layer = b"deterministic uncompressed layer"
    compressed = gzip.compress(layer, mtime=0)
    blob_digest = hashlib.sha256(compressed).hexdigest()
    config = {
        "architecture": "amd64",
        "config": {"Cmd": ["-m", "eva"], "User": "65532:65532"},
        "os": "linux",
        "rootfs": {
            "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
            "type": "layers",
        },
    }
    config_raw = json.dumps(config, separators=(",", ":"), sort_keys=True).encode()
    config_name = hashlib.sha256(config_raw).hexdigest() + ".json"
    layer_name = f"blobs/sha256/{blob_digest}"
    manifest = json.dumps(
        [{"Config": config_name, "Layers": [layer_name], "RepoTags": None}],
        separators=(",", ":"),
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, raw in (
            ("manifest.json", manifest),
            (config_name, config_raw),
            (layer_name, compressed),
        ):
            info = tarfile.TarInfo(name)
            info.mode = 0o444
            info.mtime = 1
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


def _single_builder_fixture(tmp_path: Path) -> dict[str, object]:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    builds, archives = _fixture(tmp_path)
    build_path = builds[0]
    archive_path = archives[0]
    build = json.loads(build_path.read_bytes())
    builder = build["candidate"]["receipt"]["builder"]
    identity = builder["before"]
    observation_challenge = "c" * 64
    admission_challenge = "d" * 64
    run_id = "rb003-builder-a-fixture"
    authority_sha256 = "e" * 64
    release_path = tmp_path / "release-authority.json"
    _write(
        release_path,
        {
            "authority_sha256": authority_sha256,
            "contract": "EvaManagedBReleaseInputReceipt/v1",
            "network_used": False,
            "source_archive_sha256": "8" * 64,
            "source_commit": COMMIT,
            "status": "version_matched_runtime_dependency_inputs_bound_not_built_or_deployed",
        },
    )
    loopback = {
        "flags": ["LOOPBACK"],
        "ifname": "lo",
        "operstate": "DOWN",
    }
    preflight_path = tmp_path / "preflight.json"
    preflight = {
        "admissionChallenge": admission_challenge,
        "builderIdentity": identity,
        "buildkitDiskUsageRaw": "Reclaimable:\t0B\nTotal:\t\t0B\n",
        "containerdImages": [],
        "contract": "EvaRB003PrivateEmptyBuilderPreflight/v1",
        "dockerImages": [],
        "dockerInfo": {
            "containerCount": 0,
            "containerd": {
                "Address": "/private/containerd.sock",
                "Namespaces": {
                    "Containers": identity["containerdContainersNamespace"],
                    "Plugins": identity["containerdPluginsNamespace"],
                },
            },
            "daemonId": identity["daemonId"],
            "dockerRootDir": "/private/docker-root",
            "driverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
            "httpProxy": "",
            "httpsProxy": "",
            "imageCount": 0,
        },
        "networkNamespace": {"links": [loopback], "routes": []},
        "observationChallenge": observation_challenge,
        "releaseAuthoritySha256": authority_sha256,
        "runId": run_id,
        "sourceCommit": COMMIT,
        "status": "empty_offline_builder_observed",
        "taskId": "RB-003",
    }
    _write(preflight_path, preflight)
    postflight_path = tmp_path / "postflight.json"
    image_id = build["candidate"]["image_id"]
    archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    base_references = {
        "docker.io/library/python@sha256:" + "d" * 64: 1,
        "docker.io/library/python@sha256:" + "f" * 64: 1,
        "gcr.io/distroless/cc-debian13@sha256:" + "1" * 64: 1,
    }
    postflight = {
        "admissionChallenge": admission_challenge,
        "buildReceiptSha256": hashlib.sha256(build_path.read_bytes()).hexdigest(),
        "buildRunChallenge": builder["runChallenge"],
        "builderIdentity": identity,
        "candidateArchiveSha256": archive_sha256,
        "candidateImageId": image_id,
        "containerdImages": [f"moby-dangling@{image_id}"],
        "contract": "EvaRB003PrivateEmptyBuilderPostflight/v1",
        "dockerImages": [f"{image_id} <none>:<none>"],
        "dockerInfo": {"containerCount": 0, "imageCount": 1},
        "networkNamespace": {"links": [loopback], "routes": []},
        "observationChallenge": observation_challenge,
        "postflightBuilderMatchesBuildAfter": True,
        "preflightBuilderMatchesBuildBefore": True,
        "preflightReceiptSha256": hashlib.sha256(
            preflight_path.read_bytes()
        ).hexdigest(),
        "releaseAuthorityReceiptSha256": hashlib.sha256(
            release_path.read_bytes()
        ).hexdigest(),
        "releaseAuthoritySha256": authority_sha256,
        "runId": run_id,
        "sealedBaseImageInspectExitCodes": base_references,
        "sealedBaseImagesLoadedIntoDocker": 0,
        "sourceCommit": COMMIT,
        "status": "candidate_only_offline_postflight_observed",
        "taskId": "RB-003",
    }
    _write(postflight_path, postflight)
    return {
        "admissionChallenge": admission_challenge,
        "archive": archive_path,
        "authoritySha256": authority_sha256,
        "build": build_path,
        "buildRunChallenge": builder["runChallenge"],
        "observationChallenge": observation_challenge,
        "postflight": postflight_path,
        "preflight": preflight_path,
        "release": release_path,
    }


def _verify_single(fixture: dict[str, object], **overrides: object) -> dict[str, object]:
    values = {
        "preflight_path": fixture["preflight"],
        "build_path": fixture["build"],
        "postflight_path": fixture["postflight"],
        "archive_path": fixture["archive"],
        "release_authority_path": fixture["release"],
        "expected_source_commit": COMMIT,
        "expected_authority_sha256": fixture["authoritySha256"],
        "expected_observation_challenge": fixture["observationChallenge"],
        "expected_build_run_challenge": fixture["buildRunChallenge"],
        "admission_challenge": fixture["admissionChallenge"],
    }
    values.update(overrides)
    return verify_single_builder_a(**values)  # type: ignore[arg-type]


def test_admits_one_fresh_empty_builder_a_chain(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)

    result = _verify_single(fixture)

    assert result["status"] == "passed"
    assert result["sourceCommit"] == COMMIT
    assert result["releaseAuthoritySha256"] == "e" * 64
    assert result["builder"]["daemonId"] == "daemon-a"
    assert result["chain"]["preflightReceiptSha256"] == hashlib.sha256(
        fixture["preflight"].read_bytes()
    ).hexdigest()


def test_reads_oci_compressed_layers_using_their_diff_ids(tmp_path: Path) -> None:
    archive = tmp_path / "compressed-candidate.tar"
    image_id = _compressed_archive(archive)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    result = _image_semantics(
        archive,
        expected_archive_sha256=archive_sha256,
    )

    assert result["buildkitResultDigest"] == image_id
    assert result["diffIds"] == [
        "sha256:"
        + hashlib.sha256(b"deterministic uncompressed layer").hexdigest()
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dockerImages", ["sha256:" + "0" * 64]),
        ("containerdImages", ["present"]),
        ("buildkitDiskUsageRaw", "Reclaimable:\t0B\nTotal:\t\t1B\n"),
    ],
)
def test_rejects_nonempty_builder_a_start(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    preflight = json.loads(fixture["preflight"].read_bytes())
    preflight[field] = value
    _write(fixture["preflight"], preflight)

    with pytest.raises(TwoBuilderError, match="not fresh and empty"):
        _verify_single(fixture)


def test_rejects_wrong_build_or_release_source_commit(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    build = json.loads(fixture["build"].read_bytes())
    build["source"]["commit"] = "b" * 40
    build["candidate"]["receipt"]["candidate_source_commit"] = "b" * 40
    _write_build(fixture["build"], fixture["archive"], build)
    with pytest.raises(TwoBuilderError, match="build source commit differs"):
        _verify_single(fixture)

    fixture = _single_builder_fixture(tmp_path / "release-case")
    release = json.loads(fixture["release"].read_bytes())
    release["source_commit"] = "b" * 40
    _write(fixture["release"], release)
    with pytest.raises(TwoBuilderError, match="release authority source commit differs"):
        _verify_single(fixture)


def test_rejects_wrong_release_authority_or_source_archive(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    release = json.loads(fixture["release"].read_bytes())
    release["authority_sha256"] = "f" * 64
    _write(fixture["release"], release)
    with pytest.raises(TwoBuilderError, match="release authority digest differs"):
        _verify_single(fixture)

    fixture = _single_builder_fixture(tmp_path / "archive-case")
    release = json.loads(fixture["release"].read_bytes())
    release["source_archive_sha256"] = "f" * 64
    _write(fixture["release"], release)
    with pytest.raises(TwoBuilderError, match="source archive differs"):
        _verify_single(fixture)


def test_rejects_archive_buildkit_result_splicing(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    build = json.loads(fixture["build"].read_bytes())
    build["candidate"]["receipt"]["candidate_buildkit_result_digest"] = (
        "sha256:" + "f" * 64
    )
    _write_build(fixture["build"], fixture["archive"], build)

    with pytest.raises(TwoBuilderError, match="archive BuildKit result differs"):
        _verify_single(fixture)


def test_rejects_receipt_hash_or_challenge_splicing(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    postflight = json.loads(fixture["postflight"].read_bytes())
    postflight["preflightReceiptSha256"] = "0" * 64
    _write(fixture["postflight"], postflight)
    with pytest.raises(TwoBuilderError, match="postflight chain differs"):
        _verify_single(fixture)

    fixture = _single_builder_fixture(tmp_path / "challenge-case")
    preflight = json.loads(fixture["preflight"].read_bytes())
    preflight["observationChallenge"] = "f" * 64
    _write(fixture["preflight"], preflight)
    with pytest.raises(TwoBuilderError, match="not fresh and empty"):
        _verify_single(fixture)

    fixture = _single_builder_fixture(tmp_path / "reuse-case")
    with pytest.raises(TwoBuilderError, match="challenges were reused"):
        _verify_single(
            fixture,
            admission_challenge=fixture["buildRunChallenge"],
        )


def test_rejects_builder_identity_splicing(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    preflight = json.loads(fixture["preflight"].read_bytes())
    preflight["builderIdentity"]["daemonId"] = "different-daemon"
    preflight["dockerInfo"]["daemonId"] = "different-daemon"
    _write(fixture["preflight"], preflight)

    with pytest.raises(TwoBuilderError, match="build binding differs"):
        _verify_single(fixture)


def test_rejects_replay_against_prior_admission(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    result = _verify_single(fixture)
    prior = tmp_path / "prior-admission.json"
    _write(prior, result)

    with pytest.raises(TwoBuilderError, match="was reused"):
        _verify_single(fixture, prior_admission_paths=(prior,))


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
    right["candidate"]["receipt"]["candidate_buildkit_result_digest"] = different_id
    right["candidate"]["receipt"]["candidate_image_config_digest"] = different_id
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
