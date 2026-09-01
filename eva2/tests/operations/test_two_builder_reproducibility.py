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
    verify_single_builder_b,
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


def _build(
    side: str,
    image_id: str,
    archive_sha256: str,
    *,
    buildkit_result_digest: str,
) -> dict[str, object]:
    candidate_receipt = {
        "builder": _builder(side),
        "candidate_buildkit_result_digest": buildkit_result_digest,
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


def _runtime_layer(content: bytes = b"deterministic runtime file\n") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as layer:
        info = tarfile.TarInfo("opt/eva/runtime.txt")
        info.mode = 0o444
        info.mtime = 1788260000
        info.size = len(content)
        layer.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _archive(
    path: Path,
    *,
    mtime: int,
    user: str = "65532:65532",
    runtime_content: bytes = b"deterministic runtime file\n",
) -> tuple[str, str]:
    layer = _runtime_layer(runtime_content)
    stored_layer = gzip.compress(layer, mtime=0)
    layer_digest = hashlib.sha256(stored_layer).hexdigest()
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
    config_digest = hashlib.sha256(config_raw).hexdigest()
    config_name = f"blobs/sha256/{config_digest}"
    layer_name = f"blobs/sha256/{layer_digest}"
    image_manifest = _canonical(
        {
            "config": {
                "digest": f"sha256:{config_digest}",
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "size": len(config_raw),
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_digest}",
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "size": len(stored_layer),
                }
            ],
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "schemaVersion": 2,
        }
    )
    image_digest = hashlib.sha256(image_manifest).hexdigest()
    image_name = f"blobs/sha256/{image_digest}"
    index = _canonical(
        {
            "manifests": [
                {
                    "digest": f"sha256:{image_digest}",
                    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                    "size": len(image_manifest),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    manifest = json.dumps(
        [{"Config": config_name, "Layers": [layer_name], "RepoTags": None}],
        separators=(",", ":"),
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, raw in (
            ("manifest.json", manifest),
            ("index.json", index),
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
            (config_name, config_raw),
            (image_name, image_manifest),
            (layer_name, stored_layer),
        ):
            info = tarfile.TarInfo(name)
            info.mode = 0o444
            info.mtime = mtime
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    path.chmod(0o444)
    return f"sha256:{image_digest}", f"sha256:{config_digest}"


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


def _replace_candidate_archive(
    build_path: Path,
    archive_path: Path,
    *,
    image_id: str,
    buildkit_result_digest: str,
) -> None:
    build = json.loads(build_path.read_bytes())
    candidate = build["candidate"]
    receipt = candidate["receipt"]
    candidate["image_id"] = image_id
    receipt["candidate_buildkit_result_digest"] = buildkit_result_digest
    receipt["candidate_image_config_digest"] = image_id
    receipt["candidate_image_id"] = image_id
    receipt["candidate_image_archive_sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    _write_build(build_path, archive_path, build)


def _fixture(
    tmp_path: Path,
) -> tuple[tuple[Path, Path], tuple[Path, Path]]:
    roots = (tmp_path / "artifact-a", tmp_path / "artifact-b")
    for root in roots:
        root.mkdir(mode=0o700)
    archives = tuple(root / "candidate.tar" for root in roots)
    image_id, buildkit_result_digest = _archive(archives[0], mtime=1)
    assert _archive(archives[1], mtime=2) == (
        image_id,
        buildkit_result_digest,
    )
    builds = (tmp_path / "build-a.json", tmp_path / "build-b.json")
    for side, path, archive in zip(("a", "b"), builds, archives, strict=True):
        _write_build(
            path,
            archive,
            _build(
                side,
                image_id,
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                buildkit_result_digest=buildkit_result_digest,
            ),
        )
    return builds, archives


def _single_builder_fixture(
    tmp_path: Path, *, side: str = "a"
) -> dict[str, object]:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    builds, archives = _fixture(tmp_path)
    index = 0 if side == "a" else 1
    build_path = builds[index]
    archive_path = archives[index]
    build = json.loads(build_path.read_bytes())
    builder = build["candidate"]["receipt"]["builder"]
    identity = builder["before"]
    observation_challenge = ("c" if side == "a" else "f") * 64
    admission_challenge = ("d" if side == "a" else "0") * 64
    task_id = "RB-003" if side == "a" else "RB-004"
    run_id = f"rb00{3 if side == 'a' else 4}-builder-{side}-fixture"
    preflight_contract = (
        "EvaRB003PrivateEmptyBuilderPreflight/v1"
        if side == "a"
        else "EvaRB004PrivateEmptyBuilderPreflight/v1"
    )
    postflight_contract = (
        "EvaRB003PrivateEmptyBuilderPostflight/v1"
        if side == "a"
        else "EvaRB004PrivateEmptyBuilderPostflight/v1"
    )
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
        "contract": preflight_contract,
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
        "taskId": task_id,
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
        "contract": postflight_contract,
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
        "taskId": task_id,
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
        "side": side,
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
    if fixture["side"] == "a":
        return verify_single_builder_a(**values)  # type: ignore[arg-type]
    return verify_single_builder_b(**values)  # type: ignore[arg-type]


def _admit_builder_a(tmp_path: Path) -> tuple[dict[str, object], Path]:
    fixture = _single_builder_fixture(tmp_path / "builder-a", side="a")
    admission = _verify_single(fixture)
    builder = admission["builder"]
    assert isinstance(builder, dict)
    admission["builder"] = {
        key: builder[key]
        for key in (
            "backend",
            "buildkitNodeId",
            "buildkitVersion",
            "daemonId",
            "dockerServerVersion",
        )
    }
    admission.pop("taskId")
    replay = admission["replayKeys"]
    chain = admission["chain"]
    assert isinstance(replay, dict)
    assert isinstance(chain, dict)
    replay["archiveSha256"] = chain["archiveSha256"]
    replay["releaseAuthorityReceiptSha256"] = chain[
        "releaseAuthorityReceiptSha256"
    ]
    admission["replayKeys"] = {
        key: replay[key]
        for key in (
            "admissionChallenge",
            "archiveSha256",
            "buildReceiptSha256",
            "buildRunChallenge",
            "candidateReceiptSha256",
            "daemonId",
            "observationChallenge",
            "postflightReceiptSha256",
            "preflightReceiptSha256",
            "releaseAuthorityReceiptSha256",
            "stateRootIdentitySha256",
        )
    }
    path = tmp_path / "builder-a-admission.json"
    _write(path, admission)
    return fixture, path


def _verify_builder_b(
    fixture: dict[str, object],
    builder_a_admission_path: Path,
    builder_a_preflight_path: Path,
    **overrides: object,
) -> dict[str, object]:
    return _verify_single(
        fixture,
        builder_a_admission_path=builder_a_admission_path,
        builder_a_preflight_path=builder_a_preflight_path,
        **overrides,
    )


def _two_builder_fixture(tmp_path: Path) -> dict[str, object]:
    builder_a, admission_a = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")
    admission_b_value = _verify_builder_b(
        builder_b,
        admission_a,
        builder_a["preflight"],
    )
    admission_b = tmp_path / "builder-b-admission.json"
    _write(admission_b, admission_b_value)
    admissions = (admission_a, admission_b)
    return {
        "admissionHashes": tuple(
            hashlib.sha256(path.read_bytes()).hexdigest() for path in admissions
        ),
        "admissions": admissions,
        "archives": (builder_a["archive"], builder_b["archive"]),
        "authoritySha256": builder_a["authoritySha256"],
        "builds": (builder_a["build"], builder_b["build"]),
    }


def _verify_two(fixture: dict[str, object], **overrides: object) -> dict[str, object]:
    values = {
        "admission_paths": fixture["admissions"],
        "build_paths": fixture["builds"],
        "archive_paths": fixture["archives"],
        "expected_admission_sha256": fixture["admissionHashes"],
        "expected_source_commit": COMMIT,
        "expected_authority_sha256": fixture["authoritySha256"],
    }
    values.update(overrides)
    return verify_two_builds(**values)  # type: ignore[arg-type]


def _replace_builder_identity(
    fixture: dict[str, object], *, field: str, value: object
) -> None:
    preflight_path = fixture["preflight"]
    build_path = fixture["build"]
    postflight_path = fixture["postflight"]
    archive_path = fixture["archive"]
    assert isinstance(preflight_path, Path)
    assert isinstance(build_path, Path)
    assert isinstance(postflight_path, Path)
    assert isinstance(archive_path, Path)
    preflight = json.loads(preflight_path.read_bytes())
    build = json.loads(build_path.read_bytes())
    postflight = json.loads(postflight_path.read_bytes())
    preflight["builderIdentity"][field] = value
    postflight["builderIdentity"][field] = value
    binding = build["candidate"]["receipt"]["builder"]
    binding["before"][field] = value
    binding["after"][field] = value
    if field == "daemonId":
        preflight["dockerInfo"]["daemonId"] = value
    elif field == "containerdContainersNamespace":
        preflight["dockerInfo"]["containerd"]["Namespaces"]["Containers"] = value
    elif field == "containerdPluginsNamespace":
        preflight["dockerInfo"]["containerd"]["Namespaces"]["Plugins"] = value
    _write(preflight_path, preflight)
    _write_build(build_path, archive_path, build)
    postflight["preflightReceiptSha256"] = hashlib.sha256(
        preflight_path.read_bytes()
    ).hexdigest()
    postflight["buildReceiptSha256"] = hashlib.sha256(
        build_path.read_bytes()
    ).hexdigest()
    _write(postflight_path, postflight)


def _replace_challenge(
    fixture: dict[str, object], *, field: str, value: str
) -> dict[str, object]:
    preflight_path = fixture["preflight"]
    build_path = fixture["build"]
    postflight_path = fixture["postflight"]
    archive_path = fixture["archive"]
    assert isinstance(preflight_path, Path)
    assert isinstance(build_path, Path)
    assert isinstance(postflight_path, Path)
    assert isinstance(archive_path, Path)
    preflight = json.loads(preflight_path.read_bytes())
    build = json.loads(build_path.read_bytes())
    postflight = json.loads(postflight_path.read_bytes())
    overrides: dict[str, object] = {}
    if field == "admissionChallenge":
        preflight[field] = value
        postflight[field] = value
        overrides["admission_challenge"] = value
    elif field == "observationChallenge":
        preflight[field] = value
        postflight[field] = value
        overrides["expected_observation_challenge"] = value
    else:
        build["candidate"]["receipt"]["builder"]["runChallenge"] = value
        postflight["buildRunChallenge"] = value
        overrides["expected_build_run_challenge"] = value
    _write(preflight_path, preflight)
    _write_build(build_path, archive_path, build)
    postflight["preflightReceiptSha256"] = hashlib.sha256(
        preflight_path.read_bytes()
    ).hexdigest()
    postflight["buildReceiptSha256"] = hashlib.sha256(
        build_path.read_bytes()
    ).hexdigest()
    _write(postflight_path, postflight)
    return overrides


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


def test_admits_independent_builder_b_against_full_builder_a_admission(
    tmp_path: Path,
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")

    result = _verify_builder_b(
        builder_b,
        admission_path,
        builder_a["preflight"],
    )

    assert result["status"] == "passed"
    assert result["taskId"] == "RB-004"
    assert result["sourceCommit"] == COMMIT
    assert result["releaseAuthoritySha256"] == "e" * 64
    assert result["builder"]["daemonId"] == "daemon-b"


def test_rejects_builder_a_proof_reused_as_builder_b(tmp_path: Path) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)

    with pytest.raises(TwoBuilderError, match="builder-B preflight"):
        verify_single_builder_b(
            preflight_path=builder_a["preflight"],
            build_path=builder_a["build"],
            postflight_path=builder_a["postflight"],
            archive_path=builder_a["archive"],
            release_authority_path=builder_a["release"],
            builder_a_admission_path=admission_path,
            builder_a_preflight_path=builder_a["preflight"],
            expected_source_commit=COMMIT,
            expected_authority_sha256=builder_a["authoritySha256"],
            expected_observation_challenge=builder_a["observationChallenge"],
            expected_build_run_challenge=builder_a["buildRunChallenge"],
            admission_challenge=builder_a["admissionChallenge"],
        )


def test_rejects_spliced_builder_a_preflight(tmp_path: Path) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")
    preflight_path = builder_a["preflight"]
    assert isinstance(preflight_path, Path)
    preflight = json.loads(preflight_path.read_bytes())
    preflight["builderIdentity"]["daemonId"] = "spliced-daemon"
    _write(preflight_path, preflight)

    with pytest.raises(TwoBuilderError, match="differs from its admission"):
        _verify_builder_b(builder_b, admission_path, preflight_path)


@pytest.mark.parametrize(
    "field",
    [
        "buildkitNodeId",
        "containerdContainersNamespace",
        "containerdPluginsNamespace",
        "containerdSocketIdentitySha256",
        "daemonId",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ],
)
def test_rejects_builder_b_sharing_any_required_builder_a_identity(
    tmp_path: Path, field: str
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")
    preflight_path = builder_a["preflight"]
    assert isinstance(preflight_path, Path)
    builder_a_preflight = json.loads(preflight_path.read_bytes())
    _replace_builder_identity(
        builder_b,
        field=field,
        value=builder_a_preflight["builderIdentity"][field],
    )

    with pytest.raises(TwoBuilderError, match="not independent from builder-A"):
        _verify_builder_b(builder_b, admission_path, preflight_path)


@pytest.mark.parametrize(
    "field",
    ["admissionChallenge", "observationChallenge", "buildRunChallenge"],
)
def test_rejects_builder_b_reusing_any_builder_a_challenge(
    tmp_path: Path, field: str
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")
    preflight_path = builder_a["preflight"]
    assert isinstance(preflight_path, Path)
    overrides = _replace_challenge(
        builder_b,
        field=field,
        value=str(builder_a[field]),
    )

    with pytest.raises(TwoBuilderError, match="was reused"):
        _verify_builder_b(
            builder_b,
            admission_path,
            preflight_path,
            **overrides,
        )


def test_reads_oci_compressed_layers_using_their_diff_ids(tmp_path: Path) -> None:
    archive = tmp_path / "compressed-candidate.tar"
    image_id, buildkit_result_digest = _archive(archive, mtime=1)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    result = _image_semantics(
        archive,
        expected_archive_sha256=archive_sha256,
        expected_image_id=image_id,
    )

    assert result["buildkitResultDigest"] == buildkit_result_digest
    assert result["diffIds"] == [
        "sha256:" + hashlib.sha256(_runtime_layer()).hexdigest()
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
    fixture = _two_builder_fixture(tmp_path)
    archives = fixture["archives"]

    result = _verify_two(fixture)

    assert result["status"] == "passed"
    assert result["candidate"]["imageId"].startswith("sha256:")
    assert result["candidate"]["runtimeContentSha256"]
    assert result["candidate"]["ociManifestSha256"] == result["candidate"][
        "imageId"
    ].removeprefix("sha256:")
    assert (
        result["candidate"]["archiveSha256"]
        == hashlib.sha256(archives[0].read_bytes()).hexdigest()
    )
    assert result["builders"][0]["daemonId"] != result["builders"][1]["daemonId"]
    assert (
        result["builders"][0]["buildReceiptSha256"]
        != result["builders"][1]["buildReceiptSha256"]
    )
    assert [builder["taskId"] for builder in result["builders"]] == [
        "RB-003",
        "RB-004",
    ]
    assert "one host" in result["nonclaims"][0]


def test_rejects_reused_admission_evidence(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    admissions = fixture["admissions"]
    hashes = fixture["admissionHashes"]
    _write(admissions[1], json.loads(admissions[0].read_bytes()))

    with pytest.raises(TwoBuilderError, match="builder-B admission"):
        _verify_two(
            fixture,
            expected_admission_sha256=(hashes[0], hashes[0]),
        )


def test_rejects_unadmitted_receipt_hash(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    hashes = fixture["admissionHashes"]

    with pytest.raises(TwoBuilderError, match="admission receipt hash differs"):
        _verify_two(
            fixture,
            expected_admission_sha256=(hashes[0], "0" * 64),
        )


def test_rejects_swapped_builder_admissions(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    admissions = fixture["admissions"]
    hashes = fixture["admissionHashes"]

    with pytest.raises(TwoBuilderError, match="builder-A admission"):
        _verify_two(
            fixture,
            admission_paths=(admissions[1], admissions[0]),
            expected_admission_sha256=(hashes[1], hashes[0]),
        )


def test_rejects_admission_chain_spliced_to_other_build(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    admissions = fixture["admissions"]
    left = json.loads(admissions[0].read_bytes())
    right = json.loads(admissions[1].read_bytes())
    right["chain"]["buildReceiptSha256"] = left["chain"]["buildReceiptSha256"]
    right["replayKeys"]["buildReceiptSha256"] = left["chain"][
        "buildReceiptSha256"
    ]
    _write(admissions[1], right)
    hashes = (
        fixture["admissionHashes"][0],
        hashlib.sha256(admissions[1].read_bytes()).hexdigest(),
    )

    with pytest.raises(TwoBuilderError, match="chain differs from candidate"):
        _verify_two(fixture, expected_admission_sha256=hashes)


def test_rejects_challenge_reused_across_admissions(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    admissions = fixture["admissions"]
    left = json.loads(admissions[0].read_bytes())
    right = json.loads(admissions[1].read_bytes())
    right["replayKeys"]["observationChallenge"] = left["replayKeys"][
        "admissionChallenge"
    ]
    _write(admissions[1], right)
    hashes = (
        fixture["admissionHashes"][0],
        hashlib.sha256(admissions[1].read_bytes()).hexdigest(),
    )

    with pytest.raises(TwoBuilderError, match="challenge was reused"):
        _verify_two(fixture, expected_admission_sha256=hashes)


def test_rejects_image_id_not_bound_to_oci_manifest(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    right = json.loads(builds[1].read_bytes())
    claimed_id = "sha256:" + "f" * 64
    right["candidate"]["image_id"] = claimed_id
    receipt = right["candidate"]["receipt"]
    receipt["candidate_image_config_digest"] = claimed_id
    receipt["candidate_image_id"] = claimed_id
    _write_build(builds[1], archives[1], right)

    with pytest.raises(TwoBuilderError, match="image ID differs from OCI index"):
        _verify_two(fixture)


@pytest.mark.parametrize(
    "field",
    [
        "buildkitNodeId",
        "containerdContainersNamespace",
        "containerdPluginsNamespace",
        "containerdSocketIdentitySha256",
        "daemonId",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ],
)
def test_rejects_shared_builder_identity(tmp_path: Path, field: str) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    left = json.loads(builds[0].read_bytes())
    right = json.loads(builds[1].read_bytes())
    left_identity = left["candidate"]["receipt"]["builder"]["before"]
    right_binding = right["candidate"]["receipt"]["builder"]
    right_binding["before"][field] = left_identity[field]
    right_binding["after"][field] = left_identity[field]
    _write_build(builds[1], archives[1], right)

    with pytest.raises(TwoBuilderError, match="not independent"):
        _verify_two(fixture)


def test_rejects_toolchain_or_input_drift(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    right_build = json.loads(builds[1].read_bytes())
    right_builder = right_build["candidate"]["receipt"]["builder"]
    right_builder["before"]["buildkitVersion"] = "v0.27.0"
    right_builder["after"]["buildkitVersion"] = "v0.27.0"
    _write_build(builds[1], archives[1], right_build)
    with pytest.raises(TwoBuilderError, match="toolchains differ"):
        _verify_two(fixture)

    left_build = json.loads(builds[0].read_bytes())
    right_build = _build(
        "b",
        str(left_build["candidate"]["image_id"]),
        hashlib.sha256(archives[1].read_bytes()).hexdigest(),
        buildkit_result_digest=str(
            left_build["candidate"]["receipt"][
                "candidate_buildkit_result_digest"
            ]
        ),
    )
    right_build["source"]["dockerfile_sha256"] = "3" * 64
    _write_build(builds[1], archives[1], right_build)
    with pytest.raises(TwoBuilderError, match="input authorities differ"):
        _verify_two(fixture)


def test_rejects_full_oci_config_drift(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    different_root = tmp_path / "config-drift"
    different_root.mkdir(mode=0o700)
    different = different_root / "candidate.tar"
    image_id, config_digest = _archive(different, mtime=3, user="0:0")
    _replace_candidate_archive(
        builds[1],
        different,
        image_id=image_id,
        buildkit_result_digest=config_digest,
    )

    with pytest.raises(TwoBuilderError, match="OCI config differs"):
        _verify_two(
            fixture,
            archive_paths=(archives[0], different),
        )


def test_rejects_uncompressed_layer_and_runtime_file_byte_drift(
    tmp_path: Path,
) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    different_root = tmp_path / "runtime-file-drift"
    different_root.mkdir(mode=0o700)
    different = different_root / "candidate.tar"
    image_id, config_digest = _archive(
        different,
        mtime=3,
        runtime_content=b"changed runtime file bytes\n",
    )
    _replace_candidate_archive(
        builds[1],
        different,
        image_id=image_id,
        buildkit_result_digest=config_digest,
    )

    with pytest.raises(TwoBuilderError, match="uncompressed layers differ"):
        _verify_two(
            fixture,
            archive_paths=(archives[0], different),
        )


def test_rejects_reused_build_receipt_even_at_a_second_path(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    reused = json.loads(builds[0].read_bytes())
    _write_build(builds[1], archives[1], reused)

    with pytest.raises(TwoBuilderError, match="build receipt was reused"):
        _verify_two(fixture)


def test_rejects_candidate_receipt_not_bound_to_build(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    archives = fixture["archives"]
    persisted = json.loads(
        (archives[1].parent / "candidate-image-receipt.json").read_bytes()
    )
    persisted["candidate_source_commit"] = "b" * 40
    _write(archives[1].parent / "candidate-image-receipt.json", persisted)

    with pytest.raises(TwoBuilderError, match="persisted candidate receipt differs"):
        _verify_two(fixture)


def test_rejects_reused_run_challenge_or_midbuild_identity_change(
    tmp_path: Path,
) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    left = json.loads(builds[0].read_bytes())
    right = json.loads(builds[1].read_bytes())
    right_builder = right["candidate"]["receipt"]["builder"]
    right_builder["runChallenge"] = left["candidate"]["receipt"]["builder"][
        "runChallenge"
    ]
    _write_build(builds[1], archives[1], right)
    with pytest.raises(TwoBuilderError, match="challenge was reused"):
        _verify_two(fixture)

    right = _build(
        "b",
        str(left["candidate"]["image_id"]),
        hashlib.sha256(archives[1].read_bytes()).hexdigest(),
        buildkit_result_digest=str(
            left["candidate"]["receipt"]["candidate_buildkit_result_digest"]
        ),
    )
    right["candidate"]["receipt"]["builder"]["after"]["daemonId"] = "changed"
    _write_build(builds[1], archives[1], right)
    with pytest.raises(TwoBuilderError, match="changed during build"):
        _verify_two(fixture)
