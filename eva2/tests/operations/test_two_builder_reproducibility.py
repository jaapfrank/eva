from __future__ import annotations

import argparse
import hashlib
import gzip
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile

import pytest

import tools.check_two_builder_reproducibility as reproducibility
from tools.check_two_builder_reproducibility import (
    TwoBuilderError,
    _image_semantics,
    run_private_builder_operator,
    verify_single_builder_a,
    verify_single_builder_b,
    verify_two_builds,
)


COMMIT = "a" * 40
SOURCE_DATE_EPOCH = 1788260000
EVA2_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = {
    "alembic.ini": b"[alembic]\n",
    "connector_sdk/__init__.py": b"# connector sdk\n",
    "migrations/env.py": b"# migration\n",
    "pyproject.toml": b"[build-system]\n",
    "requirements-4d.lock": b"example==1 --hash=sha256:" + b"0" * 64 + b"\n",
    "schemas/example.json": b"{}\n",
    "src/eva/runtime.py": b"deterministic runtime file\n",
}
SOURCE_MODES = {name: 0o644 for name in SOURCE_FILES} | {"migrations/env.py": 0o755}


def _canonical(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _builder_measurement(side: str) -> dict[str, object]:
    return {
        "backend": "docker-buildx/docker",
        "buildkitNodeId": f"node-{side}",
        "buildkitVersion": "v0.26.2",
        "buildxExecutableSha256": "8" * 64,
        "buildxVersion": "github.com/docker/buildx v0.18.0 test",
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
        "contract": "EvaMeasuredDockerBuilder/v3",
        "runChallenge": ("a" if side == "a" else "b") * 64,
        "singleHost": True,
    }


def _build(
    side: str,
    image_id: str,
    archive_sha256: str,
    *,
    buildkit_result_digest: str,
    source_archive_sha256: str,
    context_tree_sha256: str = "b" * 64,
) -> dict[str, object]:
    candidate_receipt = {
        "builder": _builder(side),
        "candidate_buildkit_result_digest": buildkit_result_digest,
        "candidate_image_archive_sha256": archive_sha256,
        "candidate_image_config_digest": buildkit_result_digest,
        "candidate_image_id": image_id,
        "candidate_source_commit": COMMIT,
        "candidate_source_date_epoch": SOURCE_DATE_EPOCH,
        "candidate_provenance_attestation_enabled": False,
        "context_tree_sha256": context_tree_sha256,
        "contract": "EvaManagedBOfflineCandidateImageReceipt/v3",
        "network_used": False,
        "status": "built_local_candidate",
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
            "archive_sha256": source_archive_sha256,
            "commit": COMMIT,
            "date_epoch": SOURCE_DATE_EPOCH,
            "dockerfile_sha256": "9" * 64,
            "requirements_lock_sha256": "a" * 64,
        },
        "stages": {
            "build_context": {"tree_sha256": context_tree_sha256},
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


def _runtime_layer(
    content: bytes = b"deterministic runtime file\n",
    *,
    additions: tuple[tuple[tarfile.TarInfo, bytes], ...] = (),
    overrides: dict[str, dict[str, object]] | None = None,
) -> bytes:
    overrides = overrides or {}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as layer:
        directories = {"opt", "opt/eva"}
        for source_name in SOURCE_FILES:
            path = Path(f"opt/eva/{source_name}")
            directories.update(parent.as_posix() for parent in path.parents)
        for name in sorted(directories, key=lambda value: (value.count("/"), value)):
            if name in {".", ""}:
                continue
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755 if name.count("/") <= 2 else 0o555
            info.mtime = SOURCE_DATE_EPOCH
            for field, value in overrides.get(name, {}).items():
                setattr(info, field, value)
            layer.addfile(info)
        for source_name, source_content in sorted(SOURCE_FILES.items()):
            if source_name == "src/eva/runtime.py":
                source_content = content
            info = tarfile.TarInfo(f"opt/eva/{source_name}")
            info.mode = 0o555 if SOURCE_MODES[source_name] == 0o755 else 0o444
            info.mtime = SOURCE_DATE_EPOCH
            info.size = len(source_content)
            for field, value in overrides.get(info.name, {}).items():
                setattr(info, field, value)
            layer.addfile(info, io.BytesIO(source_content))
        for info, payload in additions:
            layer.addfile(info, io.BytesIO(payload) if info.isfile() else None)
    return output.getvalue()


def _source_archive(path: Path) -> str:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content in sorted(SOURCE_FILES.items()):
            info = tarfile.TarInfo(name)
            info.gid = 0
            info.gname = ""
            info.mode = SOURCE_MODES[name]
            info.mtime = SOURCE_DATE_EPOCH
            info.size = len(content)
            info.uid = 0
            info.uname = ""
            archive.addfile(info, io.BytesIO(content))
    path.write_bytes(output.getvalue())
    path.chmod(0o400)
    return hashlib.sha256(output.getvalue()).hexdigest()


def _materialized_context(path: Path) -> str:
    path.mkdir(mode=0o700)
    entries: list[dict[str, object]] = []
    for name, content in sorted(SOURCE_FILES.items()):
        target = path / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o555 if SOURCE_MODES[name] == 0o755 else 0o444)
        entries.append(
            {
                "mode": 0o555 if SOURCE_MODES[name] == 0o755 else 0o444,
                "path": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    for directory in sorted(
        (item for item in path.rglob("*") if item.is_dir()), reverse=True
    ):
        directory.chmod(0o555)
    path.chmod(0o500)
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _archive(
    path: Path,
    *,
    mtime: int,
    user: str = "65532:65532",
    runtime_content: bytes = b"deterministic runtime file\n",
    source_commit: str = COMMIT,
    source_date_epoch: int = SOURCE_DATE_EPOCH,
    runtime_layer: bytes | None = None,
    media_type_overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    media_types = {
        "config": "application/vnd.docker.container.image.v1+json",
        "descriptor": "application/vnd.docker.distribution.manifest.v2+json",
        "index": "application/vnd.oci.image.index.v1+json",
        "layer": "application/vnd.docker.image.rootfs.diff.tar.gzip",
        "manifest": "application/vnd.docker.distribution.manifest.v2+json",
        **(media_type_overrides or {}),
    }
    layer = runtime_layer or _runtime_layer(runtime_content)
    stored_layer = gzip.compress(layer, mtime=0)
    layer_digest = hashlib.sha256(stored_layer).hexdigest()
    config = {
        "architecture": "amd64",
        "config": {
            "Cmd": ["-m", "eva"],
            "Labels": {
                "io.eva2.source-commit": source_commit,
                "io.eva2.source-date-epoch": str(source_date_epoch),
                "io.eva2.source-refresh": "git-archive-v1",
            },
            "User": user,
        },
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
                "mediaType": media_types["config"],
                "size": len(config_raw),
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_digest}",
                    "mediaType": media_types["layer"],
                    "size": len(stored_layer),
                }
            ],
            "mediaType": media_types["manifest"],
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
                    "mediaType": media_types["descriptor"],
                    "platform": {"architecture": "amd64", "os": "linux"},
                    "size": len(image_manifest),
                }
            ],
            "mediaType": media_types["index"],
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
    path.chmod(0o400)
    return f"sha256:{image_digest}", f"sha256:{config_digest}"


def _entry(
    name: str,
    *,
    content: bytes = b"",
    entry_type: bytes = tarfile.REGTYPE,
    linkname: str = "",
    mode: int = 0o444,
) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.linkname = linkname
    info.mode = mode
    info.mtime = SOURCE_DATE_EPOCH
    info.size = len(content)
    info.type = entry_type
    return info, content


def _runtime_semantics(tmp_path: Path, layer: bytes) -> dict[str, object]:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive = tmp_path / "candidate.tar"
    source_archive = tmp_path / "source-archive.tar"
    source_sha256 = _source_archive(source_archive)
    image_id, _ = _archive(archive, mtime=1, runtime_layer=layer)
    return _image_semantics(
        archive,
        source_archive_path=source_archive,
        expected_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        expected_image_id=image_id,
        expected_source_archive_sha256=source_sha256,
        expected_source_commit=COMMIT,
        expected_source_date_epoch=SOURCE_DATE_EPOCH,
    )


def _write(path: Path, value: object) -> None:
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(_canonical(value))
    path.chmod(0o600)


def _write_build(path: Path, archive: Path, value: dict[str, object]) -> None:
    _write(path, value)
    candidate = value["candidate"]
    assert isinstance(candidate, dict)
    receipt = candidate["receipt"]
    _write(archive.parent / "candidate-image-receipt.json", receipt)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _legacy_prior_document(task_id: str) -> dict[str, object]:
    side = "a" if task_id == "RB-003" else "b"
    return {
        "contract": "EvaSingleBuilderAdmissionReceipt/v1",
        "releaseAuthoritySha256": _digest(f"historic-{side}-authority"),
        "replayKeys": {
            "admissionChallenge": _digest(f"historic-{side}-admission"),
            "buildReceiptSha256": _digest(f"historic-{side}-build"),
            "buildRunChallenge": _digest(f"historic-{side}-build-run"),
            "candidateReceiptSha256": _digest(f"historic-{side}-candidate"),
            "daemonId": f"historic-daemon-{side}",
            "observationChallenge": _digest(f"historic-{side}-observation"),
            "postflightReceiptSha256": _digest(f"historic-{side}-postflight"),
            "preflightReceiptSha256": _digest(f"historic-{side}-preflight"),
            "stateRootIdentitySha256": _digest(f"historic-{side}-state"),
        },
        "runId": f"rb00{3 if side == 'a' else 4}-builder-{side}-historic",
        "sourceCommit": "b" * 40,
        "status": "passed",
        "taskId": task_id,
    }


TEST_HISTORICAL_ADMISSION_SHA256S = {
    task_id: hashlib.sha256(_canonical(_legacy_prior_document(task_id))).hexdigest()
    for task_id in ("RB-003", "RB-004")
}


@pytest.fixture(autouse=True)
def _test_historical_admission_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reproducibility,
        "_EXPECTED_HISTORICAL_ADMISSION_SHA256S",
        TEST_HISTORICAL_ADMISSION_SHA256S,
    )


def _legacy_prior_admission(path: Path, *, task_id: str) -> Path:
    _write(path, _legacy_prior_document(task_id))
    return path


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
    receipt["candidate_image_config_digest"] = buildkit_result_digest
    receipt["candidate_image_id"] = image_id
    receipt["candidate_image_archive_sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    _write_build(build_path, archive_path, build)


def _fixture(
    tmp_path: Path,
) -> tuple[tuple[Path, Path], tuple[Path, Path], tuple[Path, Path]]:
    roots = (tmp_path / "artifact-a", tmp_path / "artifact-b")
    for root in roots:
        root.mkdir(mode=0o700)
    archives = tuple(root / "candidate.tar" for root in roots)
    source_archives = tuple(root / "source-archive.tar" for root in roots)
    source_archive_hashes = tuple(_source_archive(path) for path in source_archives)
    assert source_archive_hashes[0] == source_archive_hashes[1]
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
                source_archive_sha256=source_archive_hashes[0],
            ),
        )
    return builds, archives, source_archives


def _single_builder_fixture(
    tmp_path: Path, *, side: str = "a"
) -> dict[str, object]:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    builds, archives, source_archives = _fixture(tmp_path)
    index = 0 if side == "a" else 1
    build_path = builds[index]
    archive_path = archives[index]
    source_archive_path = source_archives[index]
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
    prior_admissions = tuple(
        _legacy_prior_admission(
            tmp_path / f"prior-{task_id.lower()}.json", task_id=task_id
        )
        for task_id in ("RB-003", "RB-004")
    )
    release_path = tmp_path / "release-authority.json"
    _write(
        release_path,
        {
            "authority_sha256": authority_sha256,
            "contract": "EvaManagedBReleaseInputReceipt/v1",
            "network_used": False,
            "source_archive_sha256": hashlib.sha256(
                source_archive_path.read_bytes()
            ).hexdigest(),
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
    cleanup_path = tmp_path / "cleanup.json"
    _write(
        cleanup_path,
        {
            "containerdPidAbsent": True,
            "contract": "EvaRR002PrivateBuilderCleanupReceipt/v2",
            "dockerPidAbsent": True,
            "networkNamespaceAbsent": True,
            "role": side,
            "runId": run_id,
            "runtimePathsAbsent": True,
            "status": "private_builder_removed",
            "unitsStopped": True,
        },
    )
    return {
        "admissionChallenge": admission_challenge,
        "archive": archive_path,
        "authoritySha256": authority_sha256,
        "build": build_path,
        "buildRunChallenge": builder["runChallenge"],
        "cleanup": cleanup_path,
        "observationChallenge": observation_challenge,
        "postflight": postflight_path,
        "preflight": preflight_path,
        "priorAdmissions": prior_admissions,
        "release": release_path,
        "side": side,
        "sourceArchive": source_archive_path,
    }


def _verify_single(fixture: dict[str, object], **overrides: object) -> dict[str, object]:
    values = {
        "preflight_path": fixture["preflight"],
        "build_path": fixture["build"],
        "postflight_path": fixture["postflight"],
        "archive_path": fixture["archive"],
        "source_archive_path": fixture["sourceArchive"],
        "release_authority_path": fixture["release"],
        "cleanup_path": fixture["cleanup"],
        "expected_source_commit": COMMIT,
        "expected_authority_sha256": fixture["authoritySha256"],
        "expected_observation_challenge": fixture["observationChallenge"],
        "expected_build_run_challenge": fixture["buildRunChallenge"],
        "admission_challenge": fixture["admissionChallenge"],
        "prior_admission_paths": fixture["priorAdmissions"],
    }
    values.update(overrides)
    if fixture["side"] == "a":
        return verify_single_builder_a(**values)  # type: ignore[arg-type]
    return verify_single_builder_b(**values)  # type: ignore[arg-type]


def _admit_builder_a(tmp_path: Path) -> tuple[dict[str, object], Path]:
    fixture = _single_builder_fixture(tmp_path / "builder-a", side="a")
    admission = _verify_single(fixture)
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
        "sourceArchives": (
            builder_a["sourceArchive"],
            builder_b["sourceArchive"],
        ),
        "cleanups": (builder_a["cleanup"], builder_b["cleanup"]),
        "authoritySha256": builder_a["authoritySha256"],
        "builds": (builder_a["build"], builder_b["build"]),
    }


def _verify_two(fixture: dict[str, object], **overrides: object) -> dict[str, object]:
    values = {
        "admission_paths": fixture["admissions"],
        "build_paths": fixture["builds"],
        "archive_paths": fixture["archives"],
        "source_archive_paths": fixture["sourceArchives"],
        "cleanup_paths": fixture["cleanups"],
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


class _FakePrivateBuilderRunner:
    def __init__(
        self,
        arguments: argparse.Namespace,
        *,
        fail_at: str | None = None,
    ) -> None:
        self.arguments = arguments
        self.fail_at = fail_at
        self.built = False
        self.image_id: str | None = None
        self.network_present = True
        self.stopped_units: set[str] = set()
        self.stop_failures = 0
        self.calls: list[tuple[list[str], Path | None, dict[str, str]]] = []

    def _completed(
        self, command: list[str], returncode: int = 0, stdout: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    def _unit_output(self, unit: str, properties: str) -> str:
        is_docker = unit == self.arguments.docker_unit
        pid = 99_999_998 if is_docker else 99_999_997
        if unit in self.stopped_units:
            values = {
                "ActiveState": "inactive",
                "LoadState": "loaded",
                "MainPID": "0",
                "SubState": "dead",
            }
        else:
            config = (
                self.arguments.docker_config
                if is_docker
                else self.arguments.containerd_config
            )
            option = "--config-file" if is_docker else "--config"
            values = {
                "ActiveState": "active",
                "Delegate": "yes",
                "ExecStart": f"argv[]=/private/daemon {option} {config}",
                "LoadState": "loaded",
                "MainPID": str(pid),
                "NetworkNamespacePath": (
                    f"/run/netns/{self.arguments.network_namespace}"
                ),
                "SubState": "running",
            }
        requested = properties.removeprefix("--property=").split(",")
        return "".join(f"{key}={values.get(key, '')}\n" for key in requested)

    def _docker_info(self) -> str:
        value = {
            "Architecture": "amd64",
            "Containerd": {
                "Address": str(self.arguments.containerd_socket),
                "Namespaces": {
                    "Containers": self.arguments.containers_namespace,
                    "Plugins": self.arguments.plugins_namespace,
                },
            },
            "Containers": 0,
            "DockerRootDir": str(self.arguments.runtime_root / "docker-root"),
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
            "HttpProxy": "",
            "HttpsProxy": "",
            "ID": "daemon-a",
            "Images": 1 if self.built else 0,
            "OSType": "linux",
        }
        return json.dumps(value)

    def _build(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if self.fail_at == "build":
            return self._completed(command, 2)
        artifact = self.arguments.output_parent / "eva2-candidate-fixture"
        artifact.mkdir(mode=0o700)
        archive = artifact / "candidate.tar"
        image_id, config_digest = _archive(archive, mtime=1)
        context = self.arguments.output_parent / "eva2-build-context-fixture"
        context_tree_sha256 = _materialized_context(context)
        preflight = json.loads(self.arguments.preflight_receipt.read_bytes())
        release = json.loads(self.arguments.release_authority.read_bytes())
        build = _build(
            "a",
            image_id,
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            buildkit_result_digest=config_digest,
            source_archive_sha256=release["source_archive_sha256"],
            context_tree_sha256=context_tree_sha256,
        )
        candidate_receipt = build["candidate"]["receipt"]
        identity = preflight["builderIdentity"]
        candidate_receipt["builder"] = {
            "after": dict(identity),
            "before": dict(identity),
            "contract": "EvaMeasuredDockerBuilder/v3",
            "runChallenge": "b" * 64,
            "singleHost": True,
        }
        candidate_receipt.update(
            {
                "build_image_reference": (
                    "docker.io/library/python@sha256:" + "d" * 64
                ),
                "runtime_libraries_image_reference": (
                    "docker.io/library/python@sha256:" + "f" * 64
                ),
                "runtime_image_reference": (
                    "gcr.io/distroless/cc-debian13@sha256:" + "1" * 64
                ),
            }
        )
        build["materialized"] = {
            "build_context_path": str(context),
            "candidate_artifact_path": str(artifact),
        }
        _write_build(self.arguments.build_receipt, archive, build)
        self.built = True
        self.image_id = image_id
        return self._completed(command, stdout=json.dumps(build))

    def __call__(
        self,
        command: list[str],
        cwd: Path | None,
        environment: dict[str, str],
        allowed: frozenset[int],
    ) -> subprocess.CompletedProcess[str]:
        del allowed
        self.calls.append((list(command), cwd, dict(environment)))
        name = Path(command[0]).name
        if name == "git":
            if "--show-toplevel" in command:
                return self._completed(command, stdout=str(self.arguments.source_root.parent) + "\n")
            if command[-2:] == ["rev-parse", "HEAD"]:
                commit = "f" * 40 if self.fail_at == "source" else COMMIT
                return self._completed(command, stdout=commit + "\n")
            if "--format=%ct" in command:
                return self._completed(command, stdout=f"{SOURCE_DATE_EPOCH}\n")
            if "symbolic-ref" in command:
                return self._completed(command, 1)
            if "status" in command:
                return self._completed(command)
        if name == "systemctl" and "show" in command:
            return self._completed(
                command,
                stdout=self._unit_output(command[2], command[-1]),
            )
        if name == "sudo" and Path(command[2]).name == "systemctl":
            unit = command[-1]
            if self.fail_at == "cleanup" and self.stop_failures == 0:
                self.stop_failures += 1
                return self._completed(command, 1)
            self.stopped_units.add(unit)
            return self._completed(command)
        if name == "sudo" and Path(command[2]).name == "ip":
            if "exec" in command:
                if "route" in command:
                    return self._completed(command, stdout="[]")
                return self._completed(
                    command,
                    stdout=json.dumps(
                        [
                            {
                                "flags": ["LOOPBACK"],
                                "ifname": "lo",
                                "operstate": "DOWN",
                            }
                        ]
                    ),
                )
            if "delete" in command:
                self.network_present = False
                return self._completed(command)
        if name == "ip" and command[-2:] == ["netns", "list"]:
            value = f"{self.arguments.network_namespace}\n" if self.network_present else ""
            return self._completed(command, stdout=value)
        if name == "sudo" and Path(command[2]).name == "rm":
            shutil.rmtree(self.arguments.runtime_root)
            return self._completed(command)
        if name == "docker":
            assert command[1:3] == [
                "--host",
                f"unix://{self.arguments.docker_socket}",
            ]
            operation = command[3:]
            if operation[:2] == ["info", "--format"]:
                return self._completed(command, stdout=self._docker_info())
            if operation[:2] == ["version", "--format"]:
                return self._completed(
                    command,
                    stdout=json.dumps(
                        {"Arch": "amd64", "Os": "linux", "Version": "29.1.3"}
                    ),
                )
            if operation[:2] == ["buildx", "version"]:
                plugin = (
                    Path(environment["DOCKER_CONFIG"])
                    / "cli-plugins/docker-buildx"
                )
                assert plugin.read_bytes() == self.arguments.buildx.read_bytes()
                assert plugin.stat().st_mode & 0o777 == 0o500
                return self._completed(
                    command,
                    stdout="github.com/docker/buildx v0.18.0 test\n",
                )
            if operation[:2] == ["buildx", "ls"]:
                return self._completed(
                    command,
                    stdout=json.dumps(
                        {
                            "Driver": "docker",
                            "Name": "default",
                            "Nodes": [
                                {
                                    "Endpoint": "default",
                                    "IDs": ["node-a"],
                                    "Status": "running",
                                    "Version": "v0.26.2",
                                }
                            ],
                        }
                    )
                    + "\n",
                )
            if operation[:2] == ["buildx", "du"]:
                return self._completed(
                    command, stdout="Reclaimable:\t0B\nTotal:\t\t0B\n"
                )
            if operation[:2] == ["image", "ls"]:
                assert "--all" in operation
                output = (
                    f"{self.image_id} <none>:<none>\n" if self.built else ""
                )
                return self._completed(command, stdout=output)
            if operation[:2] == ["image", "inspect"]:
                return self._completed(command, 1)
            if operation[:2] == ["image", "rm"]:
                self.built = False
                return self._completed(command)
        if name == "ctr":
            assert command[1:5] == [
                "--address",
                str(self.arguments.containerd_socket),
                "--namespace",
                self.arguments.containers_namespace,
            ]
            output = f"moby-dangling@{self.image_id}\n" if self.built else ""
            return self._completed(command, stdout=output)
        if name.startswith("python"):
            assert cwd == self.arguments.source_root
            assert environment["DOCKER_HOST"] == (
                f"unix://{self.arguments.docker_socket}"
            )
            return self._build(command)
        raise AssertionError(f"unexpected private-builder command: {command}")


def _private_builder_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fail_at: str | None = None
) -> tuple[argparse.Namespace, _FakePrivateBuilderRunner, tuple[socket.socket, ...]]:
    artifact_base = tmp_path / "artifacts"
    artifact_base.mkdir(mode=0o700)
    monkeypatch.setattr(reproducibility, "_RR002_ARTIFACT_BASE", artifact_base)
    output_parent = artifact_base / "rr002-builder-a"
    output_parent.mkdir(mode=0o700)
    source_input = tmp_path / "source-input.tar"
    source_archive_sha256 = _source_archive(source_input)
    release = output_parent / "release-authority.json"
    _write(
        release,
        {
            "authority_sha256": "e" * 64,
            "contract": "EvaManagedBReleaseInputReceipt/v1",
            "network_used": False,
            "source_archive_sha256": source_archive_sha256,
            "source_commit": COMMIT,
            "status": "version_matched_runtime_dependency_inputs_bound_not_built_or_deployed",
        },
    )
    runtime_root = Path(tempfile.mkdtemp(prefix="eva2-rr002-test-", dir="/tmp"))
    runtime_root.chmod(0o700)
    clone_root = runtime_root / "source"
    source_root = clone_root / "eva2"
    (source_root / "tools").mkdir(mode=0o700, parents=True)
    clone_root.chmod(0o700)
    source_root.chmod(0o700)
    build_operator = source_root / "tools/build_managed_b_offline_candidate.py"
    build_operator.write_text("# exact candidate-local operator\n")
    build_operator.chmod(0o600)
    docker_root = runtime_root / "docker-root"
    docker_root.mkdir(mode=0o700)
    socket_objects: list[socket.socket] = []
    socket_paths: list[Path] = []
    for name in ("docker.sock", "containerd.sock"):
        value = socket.socket(socket.AF_UNIX)
        path = runtime_root / name
        value.bind(str(path))
        path.chmod(0o600)
        socket_objects.append(value)
        socket_paths.append(path)
    docker_config = runtime_root / "dockerd.json"
    containerd_config = runtime_root / "containerd.toml"
    _write(docker_config, {})
    containerd_config.write_text("version = 3\n")
    containerd_config.chmod(0o600)
    docker_pid = runtime_root / "dockerd.pid"
    containerd_pid = runtime_root / "containerd.pid"
    docker_pid.write_text("99999998\n")
    docker_pid.chmod(0o600)
    containerd_pid.write_text("99999997\n")
    containerd_pid.chmod(0o600)
    wheel_root = tmp_path / "wheels"
    wheel_root.mkdir(mode=0o700)
    base_archives: dict[str, Path] = {}
    for name in ("build", "runtime_libraries", "runtime"):
        path = tmp_path / f"{name}.tar"
        path.write_bytes(b"sealed base archive")
        path.chmod(0o400)
        base_archives[name] = path
    prior = [
        _legacy_prior_admission(
            tmp_path / f"prior-{task_id.lower()}.json", task_id=task_id
        )
        for task_id in ("RB-003", "RB-004")
    ]
    buildx = tmp_path / "docker-buildx"
    buildx.write_bytes(b"fixture buildx executable")
    buildx.chmod(0o500)

    def executable(name: str) -> Path:
        return Path(shutil.which(name) or "")

    arguments = argparse.Namespace(
        admission_receipt=output_parent / "admission.json",
        build_image_archive=base_archives["build"],
        build_image_archive_sha256="c" * 64,
        build_receipt=output_parent / "build.json",
        buildx=buildx,
        builder_a_admission=None,
        builder_a_preflight=None,
        cleanup_receipt=output_parent / "cleanup.json",
        containerd_config=containerd_config,
        containerd_pid_file=containerd_pid,
        containerd_socket=socket_paths[1],
        containerd_unit="eva2-rr002-a-containerd.service",
        containers_namespace="eva2-rr002-a-containers",
        ctr=executable("ctr"),
        docker=executable("docker"),
        docker_config=docker_config,
        docker_pid_file=docker_pid,
        docker_socket=socket_paths[0],
        docker_unit="eva2-rr002-a-docker.service",
        expected_authority_sha256="e" * 64,
        expected_source_commit=COMMIT,
        git=executable("git"),
        ip=executable("ip"),
        network_namespace="eva2-rr002-a-netns",
        output_parent=output_parent,
        plugins_namespace="eva2-rr002-a-plugins",
        postflight_receipt=output_parent / "postflight.json",
        preflight_receipt=output_parent / "preflight.json",
        prior_admission=prior,
        python=executable("python3"),
        release_authority=release,
        rm=executable("rm"),
        role="a",
        runtime_image_archive=base_archives["runtime"],
        runtime_image_archive_sha256="0" * 64,
        runtime_libraries_image_archive=base_archives["runtime_libraries"],
        runtime_libraries_image_archive_sha256="e" * 64,
        runtime_root=runtime_root,
        source_root=source_root,
        sudo=executable("sudo"),
        systemctl=executable("systemctl"),
        wheel_artifact_root=wheel_root,
    )
    runner = _FakePrivateBuilderRunner(arguments, fail_at=fail_at)
    return arguments, runner, tuple(socket_objects)


def test_private_builder_operator_measures_builds_admits_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    monkeypatch.setenv("HOME", "/must-not-enter-private-builder")
    arguments, runner, sockets = _private_builder_fixture(tmp_path, monkeypatch)
    try:
        result = run_private_builder_operator(arguments, runner=runner)
    finally:
        for value in sockets:
            value.close()

    assert result["admission"]["status"] == "passed"
    assert result["cleanup"]["status"] == "private_builder_removed"
    assert arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()
    assert not arguments.runtime_root.exists()
    assert runner.stopped_units == {
        arguments.docker_unit,
        arguments.containerd_unit,
    }
    for command, _cwd, environment in runner.calls:
        assert "DOCKER_CONTEXT" not in environment
        assert environment.get("HOME") != "/must-not-enter-private-builder"
        if Path(command[0]).name == "docker":
            assert command[1:3] == [
                "--host",
                f"unix://{arguments.docker_socket}",
            ]
        if Path(command[0]).name == "ctr":
            assert command[1:5] == [
                "--address",
                str(arguments.containerd_socket),
                "--namespace",
                arguments.containers_namespace,
            ]
    network_calls = [
        command
        for command, _cwd, _environment in runner.calls
        if "exec" in command and "netns" in command
    ]
    assert network_calls
    assert all(
        command[:3] == [
            str(arguments.sudo.resolve()),
            "-n",
            str(arguments.ip.resolve()),
        ]
        for command in network_calls
    )
    build_calls = [
        (command, cwd, environment)
        for command, cwd, environment in runner.calls
        if "tools.build_managed_b_offline_candidate" in command
    ]
    assert len(build_calls) == 1
    command, cwd, environment = build_calls[0]
    assert command[:3] == [
        str(arguments.python.resolve()),
        "-m",
        "tools.build_managed_b_offline_candidate",
    ]
    assert cwd == arguments.source_root
    assert environment["DOCKER_CONFIG"].startswith(str(arguments.runtime_root))
    assert environment["BUILDX_CONFIG"].startswith(str(arguments.runtime_root))
    assert environment["HOME"].startswith(str(arguments.runtime_root))
    assert environment["EVA_RR002_BUILDX_SHA256"] == hashlib.sha256(
        arguments.buildx.read_bytes()
    ).hexdigest()
    assert result["admission"]["builder"]["buildxExecutableSha256"] == (
        environment["EVA_RR002_BUILDX_SHA256"]
    )
    assert result["admission"]["builder"]["buildxVersion"] == (
        "github.com/docker/buildx v0.18.0 test"
    )


def test_private_builder_accepts_duplicate_identical_default_buildx_rows() -> None:
    builder = {
        "Driver": "docker",
        "Name": "default",
        "Nodes": [{"Endpoint": "default", "Status": "running"}],
    }

    selected = reproducibility._select_private_buildkit_builder(
        json.dumps(builder) + "\n" + json.dumps(builder, sort_keys=True) + "\n"
    )

    assert selected == builder


def test_private_builder_rejects_conflicting_default_buildx_rows() -> None:
    first = {"Driver": "docker", "Name": "default", "Nodes": []}
    second = {"Driver": "docker-container", "Name": "default", "Nodes": []}

    with pytest.raises(TwoBuilderError, match="private BuildKit builder differs"):
        reproducibility._select_private_buildkit_builder(
            json.dumps(first) + "\n" + json.dumps(second) + "\n"
        )


@pytest.mark.parametrize("failure", ["source", "build", "cleanup"])
def test_private_builder_failure_cleans_and_never_publishes_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    arguments, runner, sockets = _private_builder_fixture(
        tmp_path, monkeypatch, fail_at=failure
    )
    try:
        with pytest.raises(TwoBuilderError):
            run_private_builder_operator(arguments, runner=runner)
    finally:
        for value in sockets:
            value.close()

    assert not arguments.runtime_root.exists()
    assert not arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()
    assert runner.stopped_units == {
        arguments.docker_unit,
        arguments.containerd_unit,
    }


def test_private_builder_rejects_missing_buildx_and_still_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, runner, sockets = _private_builder_fixture(tmp_path, monkeypatch)
    arguments.buildx = tmp_path / "missing-docker-buildx"
    try:
        with pytest.raises(TwoBuilderError, match="buildx executable is inaccessible"):
            run_private_builder_operator(arguments, runner=runner)
    finally:
        for value in sockets:
            value.close()

    assert not arguments.runtime_root.exists()
    assert not arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()


def test_private_builder_rejects_changed_private_buildx_and_still_cleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, runner, sockets = _private_builder_fixture(tmp_path, monkeypatch)
    original = reproducibility._copy_private_buildx_executable

    def wrong_authority(source: Path, target: Path) -> str:
        original(source, target)
        return "0" * 64

    monkeypatch.setattr(
        reproducibility, "_copy_private_buildx_executable", wrong_authority
    )
    try:
        with pytest.raises(TwoBuilderError, match="private Buildx executable differs"):
            run_private_builder_operator(arguments, runner=runner)
    finally:
        for value in sockets:
            value.close()

    assert not arguments.runtime_root.exists()
    assert not arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()


def test_private_builder_rejects_system_socket_and_still_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, runner, sockets = _private_builder_fixture(tmp_path, monkeypatch)
    private_socket = arguments.docker_socket
    arguments.docker_socket = Path("/var/run/docker.sock")
    try:
        with pytest.raises(TwoBuilderError, match="inside private runtime root"):
            run_private_builder_operator(arguments, runner=runner)
    finally:
        arguments.docker_socket = private_socket
        for value in sockets:
            value.close()

    assert not arguments.runtime_root.exists()
    assert not arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()


def test_private_builder_socket_validation_failure_still_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, runner, sockets = _private_builder_fixture(tmp_path, monkeypatch)
    arguments.docker_socket.chmod(0o666)
    try:
        with pytest.raises(TwoBuilderError, match="must be private"):
            run_private_builder_operator(arguments, runner=runner)
    finally:
        for value in sockets:
            value.close()

    assert not arguments.runtime_root.exists()
    assert not arguments.admission_receipt.exists()
    assert arguments.cleanup_receipt.exists()


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
    assert result["contract"] == "EvaSingleBuilderAdmissionReceipt/v2"
    assert result["taskId"] == "RB-003"
    assert result["priorAdmissionReceiptSha256s"]
    assert result["chain"]["cleanupReceiptSha256"] == hashlib.sha256(
        fixture["cleanup"].read_bytes()
    ).hexdigest()


def test_rejects_missing_or_failed_cleanup_receipt(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    cleanup = json.loads(fixture["cleanup"].read_bytes())
    cleanup["networkNamespaceAbsent"] = False
    _write(fixture["cleanup"], cleanup)

    with pytest.raises(TwoBuilderError, match="cleanup was not proven"):
        _verify_single(fixture)

    fixture["cleanup"].unlink()
    with pytest.raises(TwoBuilderError, match="cleanup.*inaccessible"):
        _verify_single(fixture)


def test_two_builder_verifier_rejects_swapped_cleanup_receipts(
    tmp_path: Path,
) -> None:
    fixture = _two_builder_fixture(tmp_path)
    cleanups = fixture["cleanups"]

    with pytest.raises(TwoBuilderError, match="admission chain differs"):
        _verify_two(fixture, cleanup_paths=(cleanups[1], cleanups[0]))


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


def test_a_output_is_directly_usable_by_b_and_b_uses_same_v2_shape(
    tmp_path: Path,
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")

    admission_a = json.loads(admission_path.read_bytes())
    admission_b = _verify_builder_b(
        builder_b,
        admission_path,
        builder_a["preflight"],
    )

    assert set(admission_a) == set(admission_b)
    assert set(admission_a["builder"]) == set(admission_b["builder"])
    assert set(admission_a["candidate"]) == set(admission_b["candidate"])
    assert set(admission_a["chain"]) == set(admission_b["chain"])
    assert set(admission_a["replayKeys"]) == set(admission_b["replayKeys"])
    assert admission_a["contract"] == admission_b["contract"]
    assert hashlib.sha256(admission_path.read_bytes()).hexdigest() in admission_b[
        "priorAdmissionReceiptSha256s"
    ]


def test_legacy_v1_admission_cannot_be_positive_builder_a_proof(
    tmp_path: Path,
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    admission = json.loads(admission_path.read_bytes())
    admission["contract"] = "EvaSingleBuilderAdmissionReceipt/v1"
    _write(admission_path, admission)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")

    with pytest.raises(TwoBuilderError, match="builder-A prior admission contract"):
        _verify_builder_b(builder_b, admission_path, builder_a["preflight"])


def test_rejects_builder_a_proof_reused_as_builder_b(tmp_path: Path) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)

    with pytest.raises(TwoBuilderError, match="builder-B preflight"):
        verify_single_builder_b(
            preflight_path=builder_a["preflight"],
            build_path=builder_a["build"],
            postflight_path=builder_a["postflight"],
            archive_path=builder_a["archive"],
            source_archive_path=builder_a["sourceArchive"],
            release_authority_path=builder_a["release"],
            cleanup_path=builder_a["cleanup"],
            builder_a_admission_path=admission_path,
            builder_a_preflight_path=builder_a["preflight"],
            expected_source_commit=COMMIT,
            expected_authority_sha256=builder_a["authoritySha256"],
            expected_observation_challenge=builder_a["observationChallenge"],
            expected_build_run_challenge=builder_a["buildRunChallenge"],
            admission_challenge=builder_a["admissionChallenge"],
            prior_admission_paths=builder_a["priorAdmissions"],
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
    tmp_path.chmod(0o700)
    archive = tmp_path / "compressed-candidate.tar"
    source_archive = tmp_path / "source-archive.tar"
    source_archive_sha256 = _source_archive(source_archive)
    image_id, buildkit_result_digest = _archive(archive, mtime=1)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    result = _image_semantics(
        archive,
        source_archive_path=source_archive,
        expected_archive_sha256=archive_sha256,
        expected_image_id=image_id,
        expected_source_archive_sha256=source_archive_sha256,
        expected_source_commit=COMMIT,
        expected_source_date_epoch=SOURCE_DATE_EPOCH,
    )

    assert result["buildkitResultDigest"] == buildkit_result_digest
    assert result["diffIds"] == [
        "sha256:" + hashlib.sha256(_runtime_layer()).hexdigest()
    ]
    assert result["sourceCommit"] == COMMIT
    assert result["sourceDateEpoch"] == SOURCE_DATE_EPOCH


@pytest.mark.parametrize(
    "field", ("config", "descriptor", "index", "layer", "manifest")
)
def test_rejects_wrong_oci_or_docker_media_type(
    tmp_path: Path, field: str
) -> None:
    tmp_path.chmod(0o700)
    archive = tmp_path / "wrong-media-type.tar"
    source_archive = tmp_path / "source-archive.tar"
    source_archive_sha256 = _source_archive(source_archive)
    image_id, _ = _archive(
        archive,
        mtime=1,
        media_type_overrides={field: "application/vnd.example.invalid"},
    )

    with pytest.raises(TwoBuilderError, match=r"candidate (OCI|image)"):
        _image_semantics(
            archive,
            source_archive_path=source_archive,
            expected_archive_sha256=hashlib.sha256(
                archive.read_bytes()
            ).hexdigest(),
            expected_image_id=image_id,
            expected_source_archive_sha256=source_archive_sha256,
            expected_source_commit=COMMIT,
            expected_source_date_epoch=SOURCE_DATE_EPOCH,
        )


@pytest.mark.parametrize("mode", [0o4444, 0o446])
def test_rejects_unsafe_runtime_source_file_mode(tmp_path: Path, mode: int) -> None:
    layer = _runtime_layer(
        overrides={"opt/eva/src/eva/runtime.py": {"mode": mode}}
    )

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, layer)


def test_rejects_runtime_source_timestamp_drift(tmp_path: Path) -> None:
    layer = _runtime_layer(
        overrides={
            "opt/eva/src/eva/runtime.py": {"mtime": SOURCE_DATE_EPOCH + 1}
        }
    )

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, layer)


@pytest.mark.parametrize(("field", "value"), [("uid", 1000), ("gid", 1000)])
def test_rejects_non_root_runtime_source_owner(
    tmp_path: Path, field: str, value: int
) -> None:
    layer = _runtime_layer(
        overrides={"opt/eva/src/eva/runtime.py": {field: value}}
    )

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, layer)


@pytest.mark.parametrize(
    ("path", "override"),
    [
        ("opt", {"mode": 0o777}),
        ("opt/eva", {"uid": 1000}),
        ("opt/eva/src/eva", {"type": tarfile.SYMTYPE}),
        ("opt/eva/src/eva", {"type": tarfile.REGTYPE}),
    ],
)
def test_rejects_unsafe_runtime_source_ancestor(
    tmp_path: Path, path: str, override: dict[str, object]
) -> None:
    layer = _runtime_layer(overrides={path: override})

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, layer)


@pytest.mark.parametrize(
    ("entry_type", "linkname"),
    [
        (tarfile.REGTYPE, ""),
        (tarfile.SYMTYPE, "runtime.py"),
        (tarfile.LNKTYPE, "opt/eva/src/eva/runtime.py"),
        (tarfile.CHRTYPE, ""),
        (tarfile.FIFOTYPE, ""),
    ],
)
def test_rejects_unexpected_runtime_source_entry(
    tmp_path: Path, entry_type: bytes, linkname: str
) -> None:
    addition = _entry(
        "opt/eva/src/sitecustomize.py",
        content=b"unexpected import hook\n" if entry_type == tarfile.REGTYPE else b"",
        entry_type=entry_type,
        linkname=linkname,
    )

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, _runtime_layer(additions=(addition,)))


@pytest.mark.parametrize(
    "name",
    (
        "opt/eva/sitecustomize.py",
        "opt/eva/wheelhouse/package.whl",
        "opt/eva/build/generated.py",
        "opt/eva/src/eva2_living_core.egg-info/PKG-INFO",
    ),
)
def test_rejects_every_unexpected_runtime_destination_below_opt_eva(
    tmp_path: Path, name: str
) -> None:
    addition = _entry(name, content=b"unexpected generated destination\n")

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _runtime_semantics(tmp_path, _runtime_layer(additions=(addition,)))


def test_ignores_installed_dependencies_outside_product_source(tmp_path: Path) -> None:
    addition = _entry(
        "usr/local/lib/python3.12/site-packages/generated.py",
        content=b"installed dependency\n",
    )

    assert _runtime_semantics(tmp_path, _runtime_layer(additions=(addition,)))[
        "runtimeSourceTreeSha256"
    ]


@pytest.mark.parametrize(
    ("constant", "maximum"),
    [
        ("_MAX_LAYER_UNCOMPRESSED_BYTES", 1024),
        ("_MAX_TOTAL_LAYER_UNCOMPRESSED_BYTES", 1024),
    ],
)
def test_rejects_gzip_layer_expansion_beyond_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    maximum: int,
) -> None:
    monkeypatch.setattr(reproducibility, constant, maximum)

    with pytest.raises(TwoBuilderError, match="layer exceeds expansion limit"):
        _runtime_semantics(tmp_path, b"\0" * (maximum + 1))


def test_rejects_oversized_outer_json_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reproducibility, "_MAX_JSON_MEMBER_BYTES", 1)

    with pytest.raises(TwoBuilderError, match="exceeds JSON member limit"):
        _runtime_semantics(tmp_path, _runtime_layer())


def test_rejects_explicit_gzip_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reproducibility, "_MAX_LAYER_UNCOMPRESSED_BYTES", 1024)
    compressed = gzip.compress(b"0" * 1025, mtime=0)

    with pytest.raises(TwoBuilderError, match="layer exceeds expansion limit"):
        reproducibility._read_layer("layer.tar.gz", compressed, remaining=2048)


def test_rejects_truncated_gzip_layer() -> None:
    compressed = gzip.compress(_runtime_layer(), mtime=0)[:-1]

    with pytest.raises(TwoBuilderError, match="compressed layer is invalid"):
        reproducibility._read_layer(
            "layer.tar.gz", compressed, remaining=len(_runtime_layer()) * 2
        )


@pytest.mark.parametrize(
    ("constant", "maximum"),
    [
        ("_MAX_LAYER_MEMBERS", 2),
        ("_MAX_TOTAL_LAYER_MEMBERS", 2),
        ("_MAX_LAYER_MEMBER_BYTES", 8),
        ("_MAX_TOTAL_LAYER_MEMBER_BYTES", 8),
    ],
)
def test_rejects_runtime_tar_beyond_member_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    maximum: int,
) -> None:
    monkeypatch.setattr(reproducibility, constant, maximum)

    with pytest.raises(TwoBuilderError, match="runtime layer exceeds member limit"):
        _runtime_semantics(tmp_path, _runtime_layer())


@pytest.mark.parametrize(
    "whiteout",
    [
        _entry("opt/eva/src/.wh.bad", content=b"not empty"),
        _entry("opt/eva/src/.wh.bad", entry_type=tarfile.SYMTYPE, linkname="bad"),
    ],
)
def test_rejects_malformed_runtime_whiteout(
    tmp_path: Path, whiteout: tuple[tarfile.TarInfo, bytes]
) -> None:
    with pytest.raises(TwoBuilderError, match="runtime whiteout is invalid"):
        _runtime_semantics(tmp_path, _runtime_layer(additions=(whiteout,)))


def test_opaque_whiteout_hides_only_lower_layer_children(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source_archive = tmp_path / "source-archive.tar"
    source_sha256 = _source_archive(source_archive)
    expected = reproducibility._source_runtime_entries(
        source_archive,
        expected_sha256=source_sha256,
        source_date_epoch=SOURCE_DATE_EPOCH,
    )
    lower = _runtime_layer(
        additions=(
            _entry("opt/eva/src/stale.py", content=b"lower layer only\n"),
        )
    )
    upper = _runtime_layer(
        additions=(_entry("opt/eva/src/.wh..wh..opq"),)
    )

    assert reproducibility._runtime_source_tree([lower, upper], expected)

    same_layer_extra = _runtime_layer(
        additions=(
            _entry("opt/eva/src/stale.py", content=b"same layer\n"),
            _entry("opt/eva/src/.wh..wh..opq"),
        )
    )
    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        reproducibility._runtime_source_tree([lower, same_layer_extra], expected)


@pytest.mark.parametrize("mode", [0o444, 0o644])
def test_rejects_group_or_other_readable_evidence_file(
    tmp_path: Path, mode: int
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    fixture["build"].chmod(mode)

    with pytest.raises(TwoBuilderError, match="safe private file"):
        _verify_single(fixture)


def test_rejects_evidence_in_non_private_parent(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    tmp_path.chmod(0o750)

    with pytest.raises(TwoBuilderError, match="parent must be private and owned"):
        _verify_single(fixture)


def test_rejects_evidence_not_owned_by_effective_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    effective_user = reproducibility.os.geteuid()
    monkeypatch.setattr(reproducibility.os, "geteuid", lambda: effective_user + 1)

    with pytest.raises(TwoBuilderError, match="private and owned"):
        _verify_single(fixture)


def test_rejects_symlinked_evidence_file(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    build_path = fixture["build"]
    target = tmp_path / "real-build.json"
    build_path.rename(target)
    build_path.symlink_to(target)

    with pytest.raises(TwoBuilderError, match="inaccessible"):
        _verify_single(fixture)


def test_rejects_evidence_through_symlinked_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    fixture = _single_builder_fixture(real)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(TwoBuilderError, match="parent must not use links"):
        _verify_single(fixture, build_path=alias / "build-a.json")


def test_rejects_hardlinked_evidence_file(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)
    (tmp_path / "second-build-link.json").hardlink_to(fixture["build"])

    with pytest.raises(TwoBuilderError, match="safe private file"):
        _verify_single(fixture)


def test_requires_nonempty_prior_admission_denylist(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)

    with pytest.raises(TwoBuilderError, match="historical RB-003/RB-004"):
        _verify_single(fixture, prior_admission_paths=())


def test_rejects_omitted_historical_admission(tmp_path: Path) -> None:
    fixture = _single_builder_fixture(tmp_path)

    with pytest.raises(TwoBuilderError, match="historical RB-003/RB-004"):
        _verify_single(
            fixture,
            prior_admission_paths=(fixture["priorAdmissions"][0],),
        )


def test_rejects_historical_admission_under_the_other_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    monkeypatch.setattr(
        reproducibility,
        "_EXPECTED_HISTORICAL_ADMISSION_SHA256S",
        {
            "RB-003": TEST_HISTORICAL_ADMISSION_SHA256S["RB-004"],
            "RB-004": TEST_HISTORICAL_ADMISSION_SHA256S["RB-003"],
        },
    )

    with pytest.raises(TwoBuilderError, match="admission role differs"):
        _verify_single(fixture)


def test_builder_b_requires_its_own_nonempty_prior_denylist(tmp_path: Path) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")

    with pytest.raises(TwoBuilderError, match="builder-B requires prior"):
        _verify_builder_b(
            builder_b,
            admission_path,
            builder_a["preflight"],
            prior_admission_paths=(),
        )


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


def test_rejects_candidate_source_date_epoch_not_bound_to_build_source(
    tmp_path: Path,
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    build = json.loads(fixture["build"].read_bytes())
    build["candidate"]["receipt"]["candidate_source_date_epoch"] += 1
    _write_build(fixture["build"], fixture["archive"], build)

    with pytest.raises(TwoBuilderError, match="candidate receipt binding differs"):
        _verify_single(fixture)


def test_rejects_old_image_bytes_with_only_receipts_rewritten_to_new_commit(
    tmp_path: Path,
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    old_root = tmp_path / "old-image"
    old_root.mkdir(mode=0o700)
    old_archive = old_root / "candidate.tar"
    image_id, config_digest = _archive(
        old_archive,
        mtime=7,
        source_commit="b" * 40,
        source_date_epoch=SOURCE_DATE_EPOCH - 1,
    )
    _replace_candidate_archive(
        fixture["build"],
        old_archive,
        image_id=image_id,
        buildkit_result_digest=config_digest,
    )

    with pytest.raises(TwoBuilderError, match="image source identity differs"):
        _verify_single(fixture, archive_path=old_archive)


def test_rejects_old_runtime_layers_with_labels_and_receipts_rewritten(
    tmp_path: Path,
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    old_root = tmp_path / "old-runtime"
    old_root.mkdir(mode=0o700)
    old_archive = old_root / "candidate.tar"
    image_id, config_digest = _archive(
        old_archive,
        mtime=9,
        runtime_content=b"arbitrary old runtime bytes\n",
    )
    _replace_candidate_archive(
        fixture["build"],
        old_archive,
        image_id=image_id,
        buildkit_result_digest=config_digest,
    )
    postflight = json.loads(fixture["postflight"].read_bytes())
    postflight["buildReceiptSha256"] = hashlib.sha256(
        fixture["build"].read_bytes()
    ).hexdigest()
    postflight["candidateArchiveSha256"] = hashlib.sha256(
        old_archive.read_bytes()
    ).hexdigest()
    postflight["candidateImageId"] = image_id
    postflight["dockerImages"] = [f"{image_id} <none>:<none>"]
    postflight["containerdImages"] = [f"moby-dangling@{image_id}"]
    _write(fixture["postflight"], postflight)

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
        _verify_single(fixture, archive_path=old_archive)


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
    spliced_digest = "sha256:" + "f" * 64
    build["candidate"]["receipt"][
        "candidate_buildkit_result_digest"
    ] = spliced_digest
    build["candidate"]["receipt"][
        "candidate_image_config_digest"
    ] = spliced_digest
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
        _verify_single(
            fixture,
            prior_admission_paths=(*fixture["priorAdmissions"], prior),
        )


@pytest.mark.parametrize(
    ("prior_field", "current_value"),
    [
        ("observationChallenge", "admissionChallenge"),
        ("daemonId", "daemonId"),
        ("preflightReceiptSha256", "preflightReceiptSha256"),
    ],
)
def test_rejects_cross_commit_prior_challenge_identity_or_proof_hash_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_field: str,
    current_value: str,
) -> None:
    fixture = _single_builder_fixture(tmp_path)
    current = _verify_single(fixture)
    prior_path = fixture["priorAdmissions"][0]
    prior = json.loads(prior_path.read_bytes())
    if current_value == "admissionChallenge":
        reused_value = fixture["admissionChallenge"]
    elif current_value == "daemonId":
        reused_value = current["builder"]["daemonId"]
    else:
        reused_value = current["chain"]["preflightReceiptSha256"]
    prior["replayKeys"][prior_field] = reused_value
    _write(prior_path, prior)
    expected = dict(TEST_HISTORICAL_ADMISSION_SHA256S)
    expected["RB-003"] = hashlib.sha256(prior_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        reproducibility, "_EXPECTED_HISTORICAL_ADMISSION_SHA256S", expected
    )

    with pytest.raises(TwoBuilderError, match="was reused"):
        _verify_single(fixture)


@pytest.mark.parametrize(
    ("prior_field", "current_value"),
    [
        ("observationChallenge", "admissionChallenge"),
        ("daemonId", "daemonId"),
        ("preflightReceiptSha256", "preflightReceiptSha256"),
    ],
)
def test_builder_b_rejects_cross_commit_historical_reuse(
    tmp_path: Path,
    prior_field: str,
    current_value: str,
) -> None:
    builder_a, admission_path = _admit_builder_a(tmp_path)
    builder_b = _single_builder_fixture(tmp_path / "builder-b", side="b")
    current = _verify_builder_b(
        builder_b,
        admission_path,
        builder_a["preflight"],
    )
    prior_path = tmp_path / "additional-historical-denylist.json"
    prior = _legacy_prior_document("RB-003")
    prior["runId"] = "rb003-builder-a-additional-history"
    if current_value == "admissionChallenge":
        reused_value = builder_b["admissionChallenge"]
    elif current_value == "daemonId":
        reused_value = current["builder"]["daemonId"]
    else:
        reused_value = current["chain"]["preflightReceiptSha256"]
    prior["replayKeys"][prior_field] = reused_value
    _write(prior_path, prior)

    with pytest.raises(TwoBuilderError, match="was reused"):
        _verify_builder_b(
            builder_b,
            admission_path,
            builder_a["preflight"],
            prior_admission_paths=(
                *builder_b["priorAdmissions"],
                prior_path,
            ),
        )


def test_accepts_same_image_semantics_with_different_tar_envelopes(
    tmp_path: Path,
) -> None:
    fixture = _two_builder_fixture(tmp_path)
    archives = fixture["archives"]

    result = _verify_two(fixture)

    assert result["status"] == "passed"
    assert result["candidate"]["imageId"].startswith("sha256:")
    assert result["candidate"]["runtimeLayerSequenceSha256"]
    assert result["candidate"]["configSha256"] == result["candidate"][
        "buildkitResultDigest"
    ].removeprefix("sha256:")
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
    assert "expected admission" in result["nonclaims"][1]


def test_direct_cli_help_starts_without_package_import_failure() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(EVA2_ROOT / "tools/check_two_builder_reproducibility.py"),
            "--help",
        ],
        cwd=EVA2_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--expected-admission-a-sha256" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stderr


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
    receipt["candidate_image_id"] = claimed_id
    _write_build(builds[1], archives[1], right)

    with pytest.raises(TwoBuilderError, match="image ID differs from OCI manifest"):
        _verify_two(fixture)


def test_rejects_receipt_config_not_bound_to_buildkit_result(tmp_path: Path) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    right = json.loads(builds[1].read_bytes())
    right["candidate"]["receipt"]["candidate_image_config_digest"] = (
        "sha256:" + "f" * 64
    )
    _write_build(builds[1], archives[1], right)

    with pytest.raises(TwoBuilderError, match="candidate receipt binding differs"):
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
        source_archive_sha256=hashlib.sha256(
            fixture["sourceArchives"][1].read_bytes()
        ).hexdigest(),
    )
    right_build["source"]["dockerfile_sha256"] = "3" * 64
    _write_build(builds[1], archives[1], right_build)
    with pytest.raises(TwoBuilderError, match="input authorities differ"):
        _verify_two(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("buildxExecutableSha256", "7" * 64),
        ("buildxVersion", "github.com/docker/buildx v0.19.0 test"),
    ],
)
def test_rejects_buildx_toolchain_drift(
    tmp_path: Path, field: str, value: str
) -> None:
    fixture = _two_builder_fixture(tmp_path)
    builds = fixture["builds"]
    archives = fixture["archives"]
    right_build = json.loads(builds[1].read_bytes())
    right_builder = right_build["candidate"]["receipt"]["builder"]
    right_builder["before"][field] = value
    right_builder["after"][field] = value
    _write_build(builds[1], archives[1], right_build)

    with pytest.raises(TwoBuilderError, match="toolchains differ"):
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

    with pytest.raises(TwoBuilderError, match="runtime source differs"):
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
        source_archive_sha256=hashlib.sha256(
            fixture["sourceArchives"][1].read_bytes()
        ).hexdigest(),
    )
    right["candidate"]["receipt"]["builder"]["after"]["daemonId"] = "changed"
    _write_build(builds[1], archives[1], right)
    with pytest.raises(TwoBuilderError, match="changed during build"):
        _verify_two(fixture)
