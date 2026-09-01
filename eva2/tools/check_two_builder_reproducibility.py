#!/usr/bin/env python3
"""Verify one candidate built by two independently identified build states."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from typing import Any

from tools.managed_b_candidate.private_receipt import (
    PrivateReceiptError,
    canonical_json,
    write_new_private_json,
)


BUILDER_CONTRACT = "EvaMeasuredDockerBuilder/v2"
BUILD_CONTRACT = "EvaManagedBOfflineCandidateBuildReceipt/v2"
RESULT_CONTRACT = "EvaTwoBuilderReproducibilityReceipt/v2"
SINGLE_BUILDER_CONTRACT = "EvaSingleBuilderAdmissionReceipt/v1"
BUILDER_A_PREFLIGHT_CONTRACT = "EvaRB003PrivateEmptyBuilderPreflight/v1"
BUILDER_A_POSTFLIGHT_CONTRACT = "EvaRB003PrivateEmptyBuilderPostflight/v1"
BUILDER_B_PREFLIGHT_CONTRACT = "EvaRB004PrivateEmptyBuilderPreflight/v1"
BUILDER_B_POSTFLIGHT_CONTRACT = "EvaRB004PrivateEmptyBuilderPostflight/v1"
RELEASE_AUTHORITY_CONTRACT = "EvaManagedBReleaseInputReceipt/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class TwoBuilderError(RuntimeError):
    """One captured builder, build, archive, or comparison is invalid."""


def _canonical(value: object) -> bytes:
    return canonical_json(value)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_file(path: Path, *, label: str, maximum: int) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise TwoBuilderError(f"{label} path must be explicit and absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise TwoBuilderError(f"{label} is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise TwoBuilderError(f"{label} is not one safe immutable file")
    return path.read_bytes()


def _json_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _safe_file(path, label=label, maximum=_MAX_JSON_BYTES)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise TwoBuilderError(f"{label} must contain one object")
    return value, raw


def _physical_identity(path: Path, *, label: str) -> str:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise TwoBuilderError(f"{label} is inaccessible") from error
    if resolved != path or not stat.S_ISREG(metadata.st_mode):
        raise TwoBuilderError(f"{label} must not use links")
    return _sha256(
        _canonical(
            {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "path": str(path),
                "size": metadata.st_size,
            }
        )
    )


def _sha(value: object, *, label: str, image: bool = False) -> str:
    if not isinstance(value, str):
        raise TwoBuilderError(f"{label} is invalid")
    pattern = _IMAGE_ID if image else _SHA256
    if pattern.fullmatch(value) is None:
        raise TwoBuilderError(f"{label} is invalid")
    return value


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TwoBuilderError(f"{label} must be an object")
    return value


def _builder_measurement(value: object) -> dict[str, object]:
    value = _object(value, label="builder measurement")
    expected_keys = {
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "containerdContainersNamespace",
        "containerdImageStore",
        "containerdPluginsNamespace",
        "containerdSocketIdentitySha256",
        "daemonId",
        "dockerExecutableSha256",
        "dockerServerVersion",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    }
    if set(value) != expected_keys:
        raise TwoBuilderError("builder measurement contract differs")
    for key in (
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "containerdContainersNamespace",
        "containerdImageStore",
        "containerdPluginsNamespace",
        "daemonId",
        "dockerServerVersion",
    ):
        field = value.get(key)
        if not isinstance(field, str) or not field or len(field) > 256:
            raise TwoBuilderError(f"builder {key} is invalid")
    for key in (
        "dockerExecutableSha256",
        "containerdSocketIdentitySha256",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ):
        _sha(value.get(key), label=f"builder {key}")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise TwoBuilderError(f"{label} contract differs")


def _challenge(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TwoBuilderError(f"{label} is invalid")
    return value


def _offline_namespace(value: object, *, label: str) -> dict[str, Any]:
    namespace = _object(value, label=label)
    links = namespace.get("links")
    if namespace.get("routes") != [] or not isinstance(links, list) or len(links) != 1:
        raise TwoBuilderError(f"{label} is not route-empty")
    link = _object(links[0], label=f"{label} loopback")
    if (
        link.get("ifname") != "lo"
        or link.get("operstate") != "DOWN"
        or link.get("flags") != ["LOOPBACK"]
    ):
        raise TwoBuilderError(f"{label} is not isolated")
    return namespace


def _release_authority(
    value: object,
    *,
    expected_source_commit: str,
    expected_authority_sha256: str,
) -> dict[str, str]:
    value = _object(value, label="release authority receipt")
    if (
        value.get("contract") != RELEASE_AUTHORITY_CONTRACT
        or value.get("status")
        != "version_matched_runtime_dependency_inputs_bound_not_built_or_deployed"
        or value.get("network_used") is not False
    ):
        raise TwoBuilderError("release authority receipt differs")
    if value.get("source_commit") != expected_source_commit:
        raise TwoBuilderError("release authority source commit differs")
    if value.get("authority_sha256") != expected_authority_sha256:
        raise TwoBuilderError("release authority digest differs")
    return {
        "authoritySha256": _sha(
            value.get("authority_sha256"), label="release authority digest"
        ),
        "sourceArchiveSha256": _sha(
            value.get("source_archive_sha256"), label="release source archive"
        ),
        "sourceCommit": expected_source_commit,
    }


def _builder(value: object) -> dict[str, object]:
    value = _object(value, label="builder binding")
    if set(value) != {"after", "before", "contract", "runChallenge", "singleHost"}:
        raise TwoBuilderError("builder binding contract differs")
    if value.get("contract") != BUILDER_CONTRACT or value.get("singleHost") is not True:
        raise TwoBuilderError("builder binding contract differs")
    challenge = value.get("runChallenge")
    if not isinstance(challenge, str) or _SHA256.fullmatch(challenge) is None:
        raise TwoBuilderError("builder run challenge is invalid")
    before = _builder_measurement(value.get("before"))
    after = _builder_measurement(value.get("after"))
    if before != after:
        raise TwoBuilderError("builder identity changed during build")
    return {"identity": before, "runChallenge": challenge}


def _build_inputs(value: dict[str, Any], *, expected_commit: str) -> dict[str, object]:
    if (
        value.get("contract") != BUILD_CONTRACT
        or value.get("status") != "built_local_untagged_candidate"
        or value.get("network_used") is not False
    ):
        raise TwoBuilderError("build receipt is not an offline candidate")
    source = _object(value.get("source"), label="build source")
    inputs = _object(value.get("inputs"), label="build inputs")
    stages = _object(value.get("stages"), label="build stages")
    if source.get("commit") != expected_commit:
        raise TwoBuilderError("build source commit differs")
    candidate = _object(value.get("candidate"), label="build candidate")
    candidate_receipt = _object(candidate.get("receipt"), label="candidate receipt")
    builder = _builder(candidate_receipt.get("builder"))
    image_id = _sha(candidate.get("image_id"), label="candidate image ID", image=True)
    buildkit_result_digest = _sha(
        candidate_receipt.get("candidate_buildkit_result_digest"),
        label="candidate BuildKit result digest",
        image=True,
    )
    if (
        candidate_receipt.get("candidate_image_id") != image_id
        or candidate_receipt.get("candidate_image_config_digest") != image_id
        or candidate_receipt.get("candidate_source_commit") != expected_commit
        or candidate_receipt.get("network_used") is not False
    ):
        raise TwoBuilderError("candidate receipt binding differs")
    base_images: dict[str, object] = {}
    for role in ("build_image", "runtime_libraries_image", "runtime_image"):
        row = _object(stages.get(role), label=f"{role} stage")
        base_images[role] = {
            "archiveSha256": _sha(row.get("archive_sha256"), label=f"{role} archive"),
            "digest": _sha(row.get("digest"), label=f"{role} digest", image=True),
        }
    context = _object(stages.get("build_context"), label="build-context stage")
    wheelhouse = _object(stages.get("wheelhouse"), label="wheelhouse stage")
    authority = {
        "baseImages": base_images,
        "buildContextTreeSha256": _sha(
            context.get("tree_sha256"), label="build-context tree"
        ),
        "dockerfileSha256": _sha(source.get("dockerfile_sha256"), label="Dockerfile"),
        "requirementsLockSha256": _sha(
            source.get("requirements_lock_sha256"), label="requirements lock"
        ),
        "sourceArchiveSha256": _sha(
            source.get("archive_sha256"), label="source archive"
        ),
        "sourceCommit": expected_commit,
        "sourceDateEpoch": source.get("date_epoch"),
        "wheelCoverageSha256": _sha(
            inputs.get("wheel_coverage_sha256"), label="wheel coverage"
        ),
        "wheelhouseManifestSha256": _sha(
            inputs.get("wheelhouse_manifest_sha256"), label="wheelhouse manifest"
        ),
        "wheelhouseTreeSha256": _sha(
            wheelhouse.get("tree_sha256"), label="wheelhouse tree"
        ),
    }
    if (
        isinstance(authority["sourceDateEpoch"], bool)
        or not isinstance(authority["sourceDateEpoch"], int)
        or authority["sourceDateEpoch"] <= 0
    ):
        raise TwoBuilderError("source date epoch is invalid")
    return {
        "archiveSha256": _sha(
            candidate_receipt.get("candidate_image_archive_sha256"),
            label="candidate archive",
        ),
        "authority": authority,
        "buildkitResultDigest": buildkit_result_digest,
        "builder": builder,
        "candidateReceipt": candidate_receipt,
        "imageId": image_id,
    }


def _member_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    if (
        not member.name
        or "\x00" in member.name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise TwoBuilderError("candidate archive contains an unsafe path")
    return path.as_posix()


def _layer_diff_id(name: str, raw: bytes) -> str:
    """Return the OCI DiffID for one Docker-save layer blob.

    Docker's containerd image store exports OCI-style compressed blobs even
    though the Docker manifest still calls them layers.  The blob filename is
    the digest of the stored (compressed) bytes, while ``rootfs.diff_ids`` is
    deliberately the digest after decompression.
    """

    path = PurePosixPath(name)
    if len(path.parts) == 3 and path.parts[:2] == ("blobs", "sha256"):
        if _SHA256.fullmatch(path.name) is None or _sha256(raw) != path.name:
            raise TwoBuilderError("candidate compressed layer digest differs")
    if raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except (EOFError, OSError) as error:
            raise TwoBuilderError("candidate compressed layer is invalid") from error
    return f"sha256:{_sha256(raw)}"


def _image_semantics(path: Path, *, expected_archive_sha256: str) -> dict[str, object]:
    raw = _safe_file(path, label="candidate archive", maximum=_MAX_ARCHIVE_BYTES)
    if _sha256(raw) != expected_archive_sha256:
        raise TwoBuilderError("candidate archive hash differs from build receipt")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            for member in archive:
                name = _member_name(member)
                if member.isdir():
                    continue
                if not member.isfile() or name in members or member.size > len(raw):
                    raise TwoBuilderError("candidate archive member is invalid")
                stream = archive.extractfile(member)
                if stream is None:
                    raise TwoBuilderError("candidate archive member is unreadable")
                members[name] = stream.read()
    except (tarfile.TarError, OSError) as error:
        raise TwoBuilderError("candidate archive is invalid") from error
    try:
        manifest = json.loads(members["manifest.json"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError("candidate Docker manifest is invalid") from error
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise TwoBuilderError("candidate archive must contain exactly one image")
    row = _object(manifest[0], label="candidate manifest row")
    config_name, layers = row.get("Config"), row.get("Layers")
    if (
        not isinstance(config_name, str)
        or not isinstance(layers, list)
        or not layers
        or not all(isinstance(item, str) for item in layers)
    ):
        raise TwoBuilderError("candidate manifest fields differ")
    try:
        config_raw = members[config_name]
        layer_raw = [members[item] for item in layers]
        config = json.loads(config_raw)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError("candidate config or layer is absent") from error
    config = _object(config, label="candidate config")
    rootfs = _object(config.get("rootfs"), label="candidate rootfs")
    diff_ids = rootfs.get("diff_ids")
    expected_diff_ids = [
        _layer_diff_id(name, raw) for name, raw in zip(layers, layer_raw, strict=True)
    ]
    if rootfs.get("type") != "layers" or diff_ids != expected_diff_ids:
        raise TwoBuilderError("candidate layers differ from their DiffIDs")
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise TwoBuilderError("candidate platform differs")
    runtime = _object(config.get("config"), label="candidate runtime config")
    return {
        "archiveSha256": expected_archive_sha256,
        "buildkitResultDigest": f"sha256:{_sha256(config_raw)}",
        "configSha256": _sha256(config_raw),
        "diffIds": diff_ids,
        "runtimeConfigSha256": _sha256(_canonical(runtime)),
    }


def _verify_single_builder(
    *,
    preflight_path: Path,
    build_path: Path,
    postflight_path: Path,
    archive_path: Path,
    release_authority_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
    expected_observation_challenge: str,
    expected_build_run_challenge: str,
    admission_challenge: str,
    expected_task_id: str,
    expected_preflight_contract: str,
    expected_postflight_contract: str,
    expected_run_prefix: str,
    builder_label: str,
    required_independent_builder_identity: dict[str, object] | None = None,
    prior_admission_paths: tuple[Path, ...] = (),
) -> dict[str, object]:
    """Admit one fresh, empty, offline single-builder chain exactly once."""

    if _COMMIT.fullmatch(expected_source_commit) is None:
        raise TwoBuilderError("expected source commit is invalid")
    expected_authority_sha256 = _sha(
        expected_authority_sha256, label="expected release authority"
    )
    expected_observation_challenge = _challenge(
        expected_observation_challenge, label="expected observation challenge"
    )
    expected_build_run_challenge = _challenge(
        expected_build_run_challenge, label="expected build run challenge"
    )
    admission_challenge = _challenge(
        admission_challenge, label="admission challenge"
    )
    if len(
        {
            expected_observation_challenge,
            expected_build_run_challenge,
            admission_challenge,
        }
    ) != 3:
        raise TwoBuilderError("builder challenges were reused")

    paths = (
        preflight_path,
        build_path,
        postflight_path,
        archive_path,
        release_authority_path,
    )
    if len(set(paths)) != len(paths):
        raise TwoBuilderError("single-builder artifacts require distinct paths")
    documents = {
        "preflight": _json_file(preflight_path, label=f"{builder_label} preflight"),
        "build": _json_file(build_path, label=f"{builder_label} build receipt"),
        "postflight": _json_file(postflight_path, label=f"{builder_label} postflight"),
        "releaseAuthority": _json_file(
            release_authority_path, label="release authority receipt"
        ),
    }
    hashes = {name: _sha256(raw) for name, (_value, raw) in documents.items()}
    identities = [_physical_identity(path, label="single-builder artifact") for path in paths]
    if len(set(identities)) != len(identities):
        raise TwoBuilderError("one physical single-builder artifact was reused")

    authority = _release_authority(
        documents["releaseAuthority"][0],
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
    )
    build_document = documents["build"][0]
    build = _build_inputs(build_document, expected_commit=expected_source_commit)
    if build["authority"]["sourceArchiveSha256"] != authority["sourceArchiveSha256"]:
        raise TwoBuilderError("build source archive differs from release authority")

    preflight = documents["preflight"][0]
    _exact_keys(
        preflight,
        {
            "admissionChallenge",
            "builderIdentity",
            "buildkitDiskUsageRaw",
            "containerdImages",
            "contract",
            "dockerImages",
            "dockerInfo",
            "networkNamespace",
            "observationChallenge",
            "releaseAuthoritySha256",
            "runId",
            "sourceCommit",
            "status",
            "taskId",
        },
        label=f"{builder_label} preflight",
    )
    if (
        preflight.get("contract") != expected_preflight_contract
        or preflight.get("taskId") != expected_task_id
        or preflight.get("status") != "empty_offline_builder_observed"
        or preflight.get("sourceCommit") != expected_source_commit
        or preflight.get("releaseAuthoritySha256") != expected_authority_sha256
        or preflight.get("observationChallenge")
        != expected_observation_challenge
        or preflight.get("admissionChallenge") != admission_challenge
        or preflight.get("dockerImages") != []
        or preflight.get("containerdImages") != []
        or preflight.get("buildkitDiskUsageRaw")
        != "Reclaimable:\t0B\nTotal:\t\t0B\n"
    ):
        raise TwoBuilderError(f"{builder_label} preflight is not fresh and empty")
    run_id = preflight.get("runId")
    if not isinstance(run_id, str) or not run_id.startswith(expected_run_prefix):
        raise TwoBuilderError(f"{builder_label} run ID is invalid")
    preflight_builder = _builder_measurement(preflight.get("builderIdentity"))
    if (
        preflight_builder.get("backend") != "docker-buildx/docker"
        or preflight_builder.get("containerdImageStore")
        != "io.containerd.snapshotter.v1"
    ):
        raise TwoBuilderError(f"{builder_label} backend is not sealed OCI capable")
    if required_independent_builder_identity is not None:
        for key in (
            "buildkitNodeId",
            "containerdContainersNamespace",
            "containerdPluginsNamespace",
            "containerdSocketIdentitySha256",
            "daemonId",
            "socketIdentitySha256",
            "stateRootIdentitySha256",
        ):
            if preflight_builder[key] == required_independent_builder_identity[key]:
                raise TwoBuilderError(
                    f"{builder_label} identity is not independent from builder-A"
                )
    docker_info = _object(preflight.get("dockerInfo"), label="preflight Docker info")
    if (
        docker_info.get("imageCount") != 0
        or docker_info.get("containerCount") != 0
        or docker_info.get("daemonId") != preflight_builder["daemonId"]
        or docker_info.get("httpProxy") != ""
        or docker_info.get("httpsProxy") != ""
        or ["driver-type", "io.containerd.snapshotter.v1"]
        not in docker_info.get("driverStatus", [])
    ):
        raise TwoBuilderError(
            f"{builder_label} Docker store was not empty or offline"
        )
    containerd = _object(
        docker_info.get("containerd"), label="preflight containerd binding"
    )
    namespaces = _object(
        containerd.get("Namespaces"), label="preflight containerd namespaces"
    )
    if (
        not isinstance(containerd.get("Address"), str)
        or not containerd["Address"]
        or namespaces.get("Containers")
        != preflight_builder["containerdContainersNamespace"]
        or namespaces.get("Plugins")
        != preflight_builder["containerdPluginsNamespace"]
    ):
        raise TwoBuilderError(f"{builder_label} containerd binding differs")
    _offline_namespace(preflight.get("networkNamespace"), label="preflight network")

    build_builder = _object(build["builder"], label="build builder")
    if (
        build_builder["identity"] != preflight_builder
        or build_builder["runChallenge"] != expected_build_run_challenge
    ):
        raise TwoBuilderError(f"{builder_label} build binding differs from preflight")
    semantics = _image_semantics(
        archive_path, expected_archive_sha256=str(build["archiveSha256"])
    )
    if semantics["buildkitResultDigest"] != build["buildkitResultDigest"]:
        raise TwoBuilderError("archive BuildKit result differs from build receipt")
    candidate_receipt_path = archive_path.parent / "candidate-image-receipt.json"
    candidate_receipt, candidate_receipt_raw = _json_file(
        candidate_receipt_path, label="candidate image receipt"
    )
    if candidate_receipt != build["candidateReceipt"]:
        raise TwoBuilderError("persisted candidate receipt differs from its build receipt")

    postflight = documents["postflight"][0]
    _exact_keys(
        postflight,
        {
            "admissionChallenge",
            "buildReceiptSha256",
            "buildRunChallenge",
            "builderIdentity",
            "candidateArchiveSha256",
            "candidateImageId",
            "containerdImages",
            "contract",
            "dockerImages",
            "dockerInfo",
            "networkNamespace",
            "observationChallenge",
            "postflightBuilderMatchesBuildAfter",
            "preflightBuilderMatchesBuildBefore",
            "preflightReceiptSha256",
            "releaseAuthorityReceiptSha256",
            "releaseAuthoritySha256",
            "runId",
            "sealedBaseImageInspectExitCodes",
            "sealedBaseImagesLoadedIntoDocker",
            "sourceCommit",
            "status",
            "taskId",
        },
        label=f"{builder_label} postflight",
    )
    postflight_builder = _builder_measurement(postflight.get("builderIdentity"))
    if (
        postflight.get("contract") != expected_postflight_contract
        or postflight.get("taskId") != expected_task_id
        or postflight.get("status") != "candidate_only_offline_postflight_observed"
        or postflight.get("runId") != run_id
        or postflight.get("sourceCommit") != expected_source_commit
        or postflight.get("releaseAuthoritySha256") != expected_authority_sha256
        or postflight.get("releaseAuthorityReceiptSha256")
        != hashes["releaseAuthority"]
        or postflight.get("preflightReceiptSha256") != hashes["preflight"]
        or postflight.get("buildReceiptSha256") != hashes["build"]
        or postflight.get("observationChallenge")
        != expected_observation_challenge
        or postflight.get("buildRunChallenge") != expected_build_run_challenge
        or postflight.get("admissionChallenge") != admission_challenge
        or postflight.get("candidateArchiveSha256") != build["archiveSha256"]
        or postflight.get("candidateImageId") != build["imageId"]
        or postflight_builder != preflight_builder
        or postflight.get("preflightBuilderMatchesBuildBefore") is not True
        or postflight.get("postflightBuilderMatchesBuildAfter") is not True
        or postflight.get("sealedBaseImagesLoadedIntoDocker") != 0
    ):
        raise TwoBuilderError(f"{builder_label} postflight chain differs")
    _offline_namespace(postflight.get("networkNamespace"), label="postflight network")
    post_docker = _object(postflight.get("dockerInfo"), label="postflight Docker info")
    if post_docker.get("imageCount") != 1 or post_docker.get("containerCount") != 0:
        raise TwoBuilderError(
            f"{builder_label} postflight contains unexpected Docker state"
        )
    docker_images = postflight.get("dockerImages")
    containerd_images = postflight.get("containerdImages")
    if (
        not isinstance(docker_images, list)
        or len(docker_images) != 1
        or not docker_images[0].startswith(f"{build['imageId']} ")
        or not isinstance(containerd_images, list)
        or len(containerd_images) != 1
        or not containerd_images[0].endswith(str(build["imageId"]))
    ):
        raise TwoBuilderError(
            f"{builder_label} postflight does not contain only the candidate"
        )
    base_exit_codes = _object(
        postflight.get("sealedBaseImageInspectExitCodes"),
        label="sealed base image inspect results",
    )
    expected_base_digests = {
        str(row["digest"])
        for row in _object(build["authority"]["baseImages"], label="base images").values()
    }
    if (
        len(base_exit_codes) != 3
        or set(base_exit_codes.values()) != {1}
        or {reference.rsplit("@", 1)[-1] for reference in base_exit_codes}
        != expected_base_digests
    ):
        raise TwoBuilderError("sealed base images were present in the Docker store")

    chain_hashes = {
        "archiveSha256": str(build["archiveSha256"]),
        "buildReceiptSha256": hashes["build"],
        "candidateReceiptSha256": _sha256(candidate_receipt_raw),
        "postflightReceiptSha256": hashes["postflight"],
        "preflightReceiptSha256": hashes["preflight"],
        "releaseAuthorityReceiptSha256": hashes["releaseAuthority"],
    }
    replay_keys = {
        "admissionChallenge": admission_challenge,
        "buildRunChallenge": expected_build_run_challenge,
        "buildkitNodeId": str(preflight_builder["buildkitNodeId"]),
        "containerdContainersNamespace": str(
            preflight_builder["containerdContainersNamespace"]
        ),
        "containerdPluginsNamespace": str(
            preflight_builder["containerdPluginsNamespace"]
        ),
        "containerdSocketIdentitySha256": str(
            preflight_builder["containerdSocketIdentitySha256"]
        ),
        "daemonId": str(preflight_builder["daemonId"]),
        "observationChallenge": expected_observation_challenge,
        "socketIdentitySha256": str(preflight_builder["socketIdentitySha256"]),
        "stateRootIdentitySha256": str(preflight_builder["stateRootIdentitySha256"]),
        "buildReceiptSha256": hashes["build"],
        "candidateReceiptSha256": _sha256(candidate_receipt_raw),
        "postflightReceiptSha256": hashes["postflight"],
        "preflightReceiptSha256": hashes["preflight"],
    }
    for path in prior_admission_paths:
        prior, _raw = _json_file(path, label="prior builder admission")
        if (
            prior.get("contract") != SINGLE_BUILDER_CONTRACT
            or prior.get("status") != "passed"
            or prior.get("sourceCommit") != expected_source_commit
            or prior.get("releaseAuthoritySha256") != expected_authority_sha256
        ):
            raise TwoBuilderError("prior builder admission contract differs")
        prior_replay = _object(prior.get("replayKeys"), label="prior replay keys")
        if any(prior_replay.get(key) == value for key, value in replay_keys.items()):
            raise TwoBuilderError(
                f"{builder_label} receipt, challenge, or identity was reused"
            )

    return {
        "builder": preflight_builder,
        "candidate": {**semantics, "imageId": build["imageId"]},
        "chain": chain_hashes,
        "contract": SINGLE_BUILDER_CONTRACT,
        "releaseAuthoritySha256": expected_authority_sha256,
        "replayKeys": replay_keys,
        "runId": run_id,
        "sourceCommit": expected_source_commit,
        "status": "passed",
        "taskId": expected_task_id,
    }


def verify_single_builder_a(
    *,
    preflight_path: Path,
    build_path: Path,
    postflight_path: Path,
    archive_path: Path,
    release_authority_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
    expected_observation_challenge: str,
    expected_build_run_challenge: str,
    admission_challenge: str,
    prior_admission_paths: tuple[Path, ...] = (),
) -> dict[str, object]:
    """Admit one fresh, empty, offline builder-A chain exactly once."""

    return _verify_single_builder(
        preflight_path=preflight_path,
        build_path=build_path,
        postflight_path=postflight_path,
        archive_path=archive_path,
        release_authority_path=release_authority_path,
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
        expected_observation_challenge=expected_observation_challenge,
        expected_build_run_challenge=expected_build_run_challenge,
        admission_challenge=admission_challenge,
        expected_task_id="RB-003",
        expected_preflight_contract=BUILDER_A_PREFLIGHT_CONTRACT,
        expected_postflight_contract=BUILDER_A_POSTFLIGHT_CONTRACT,
        expected_run_prefix="rb003-builder-a-",
        builder_label="builder-A",
        prior_admission_paths=prior_admission_paths,
    )


def _builder_a_prior_identity(
    *,
    admission_path: Path,
    preflight_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
) -> dict[str, object]:
    if admission_path == preflight_path:
        raise TwoBuilderError("builder-A admission and preflight paths must differ")
    admission, _admission_raw = _json_file(
        admission_path, label="builder-A admission"
    )
    if (
        admission.get("contract") != SINGLE_BUILDER_CONTRACT
        or admission.get("status") != "passed"
        or admission.get("sourceCommit") != expected_source_commit
        or admission.get("releaseAuthoritySha256") != expected_authority_sha256
        or not isinstance(admission.get("runId"), str)
        or not admission["runId"].startswith("rb003-builder-a-")
    ):
        raise TwoBuilderError("builder-A prior admission contract differs")
    chain = _object(admission.get("chain"), label="builder-A prior chain")
    preflight, preflight_raw = _json_file(
        preflight_path, label="builder-A prior preflight"
    )
    if chain.get("preflightReceiptSha256") != _sha256(preflight_raw):
        raise TwoBuilderError("builder-A preflight differs from its admission")
    if (
        preflight.get("contract") != BUILDER_A_PREFLIGHT_CONTRACT
        or preflight.get("taskId") != "RB-003"
        or preflight.get("status") != "empty_offline_builder_observed"
        or preflight.get("sourceCommit") != expected_source_commit
        or preflight.get("releaseAuthoritySha256") != expected_authority_sha256
        or preflight.get("runId") != admission["runId"]
    ):
        raise TwoBuilderError("builder-A prior preflight contract differs")
    identity = _builder_measurement(preflight.get("builderIdentity"))
    summary = _object(admission.get("builder"), label="builder-A prior summary")
    for key in (
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "daemonId",
        "dockerServerVersion",
    ):
        if summary.get(key) != identity[key]:
            raise TwoBuilderError("builder-A admission summary differs from preflight")
    replay = _object(admission.get("replayKeys"), label="builder-A prior replay keys")
    required_replay = {
        "admissionChallenge",
        "buildReceiptSha256",
        "buildRunChallenge",
        "candidateReceiptSha256",
        "daemonId",
        "observationChallenge",
        "postflightReceiptSha256",
        "preflightReceiptSha256",
        "stateRootIdentitySha256",
    }
    if not required_replay.issubset(replay):
        raise TwoBuilderError("builder-A prior replay keys differ")
    for key in ("admissionChallenge", "buildRunChallenge", "observationChallenge"):
        _challenge(replay.get(key), label=f"builder-A prior {key}")
    for key in (
        "buildReceiptSha256",
        "candidateReceiptSha256",
        "postflightReceiptSha256",
        "preflightReceiptSha256",
    ):
        if replay.get(key) != chain.get(key):
            raise TwoBuilderError("builder-A replay evidence differs from its chain")
    for key in (
        "buildkitNodeId",
        "containerdContainersNamespace",
        "containerdPluginsNamespace",
        "containerdSocketIdentitySha256",
        "daemonId",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ):
        if key in replay and replay[key] != identity[key]:
            raise TwoBuilderError("builder-A replay identity differs from preflight")
    return identity


def verify_single_builder_b(
    *,
    preflight_path: Path,
    build_path: Path,
    postflight_path: Path,
    archive_path: Path,
    release_authority_path: Path,
    builder_a_admission_path: Path,
    builder_a_preflight_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
    expected_observation_challenge: str,
    expected_build_run_challenge: str,
    admission_challenge: str,
) -> dict[str, object]:
    """Admit builder B only when its full chain is independent of admitted A."""

    builder_a_identity = _builder_a_prior_identity(
        admission_path=builder_a_admission_path,
        preflight_path=builder_a_preflight_path,
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
    )

    return _verify_single_builder(
        preflight_path=preflight_path,
        build_path=build_path,
        postflight_path=postflight_path,
        archive_path=archive_path,
        release_authority_path=release_authority_path,
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
        expected_observation_challenge=expected_observation_challenge,
        expected_build_run_challenge=expected_build_run_challenge,
        admission_challenge=admission_challenge,
        expected_task_id="RB-004",
        expected_preflight_contract=BUILDER_B_PREFLIGHT_CONTRACT,
        expected_postflight_contract=BUILDER_B_POSTFLIGHT_CONTRACT,
        expected_run_prefix="rb004-builder-b-",
        builder_label="builder-B",
        required_independent_builder_identity=builder_a_identity,
        prior_admission_paths=(builder_a_admission_path,),
    )


def verify_two_builds(
    *,
    build_paths: tuple[Path, Path],
    archive_paths: tuple[Path, Path],
    expected_source_commit: str,
) -> dict[str, object]:
    if _COMMIT.fullmatch(expected_source_commit) is None:
        raise TwoBuilderError("expected source commit is invalid")
    if build_paths[0] == build_paths[1] or archive_paths[0] == archive_paths[1]:
        raise TwoBuilderError("two distinct build and archive paths are required")
    build_documents = [_json_file(path, label="build receipt") for path in build_paths]
    build_receipt_sha256 = [_sha256(raw) for _value, raw in build_documents]
    if build_receipt_sha256[0] == build_receipt_sha256[1]:
        raise TwoBuilderError("one build receipt was reused")
    build_file_identities = [
        _physical_identity(path, label="build receipt") for path in build_paths
    ]
    archive_file_identities = [
        _physical_identity(path, label="candidate archive") for path in archive_paths
    ]
    if (
        build_file_identities[0] == build_file_identities[1]
        or archive_file_identities[0] == archive_file_identities[1]
    ):
        raise TwoBuilderError("one physical build artifact was reused")
    builds = [
        _build_inputs(
            document[0],
            expected_commit=expected_source_commit,
        )
        for document in build_documents
    ]
    candidate_receipt_sha256: list[str] = []
    for archive_path, build in zip(archive_paths, builds, strict=True):
        candidate_receipt_path = archive_path.parent / "candidate-image-receipt.json"
        candidate_receipt, candidate_raw = _json_file(
            candidate_receipt_path, label="candidate image receipt"
        )
        if candidate_receipt != build["candidateReceipt"]:
            raise TwoBuilderError(
                "persisted candidate receipt differs from its build receipt"
            )
        candidate_receipt_sha256.append(_sha256(candidate_raw))
    builders = [build["builder"] for build in builds]
    identities = [
        _object(builder, label="builder binding")["identity"] for builder in builders
    ]
    identities = [
        _object(identity, label="builder identity") for identity in identities
    ]
    challenges = [
        _object(builder, label="builder binding")["runChallenge"]
        for builder in builders
    ]
    if challenges[0] == challenges[1]:
        raise TwoBuilderError("builder run challenge was reused")
    if any(
        identities[0][key] != identities[1][key]
        for key in (
            "backend",
            "buildkitVersion",
            "dockerExecutableSha256",
            "dockerServerVersion",
        )
    ):
        raise TwoBuilderError("builder toolchains differ")
    for key in (
        "buildkitNodeId",
        "daemonId",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ):
        if identities[0][key] == identities[1][key]:
            raise TwoBuilderError(
                "builder identities or state roots are not independent"
            )
    if builds[0]["authority"] != builds[1]["authority"]:
        raise TwoBuilderError("build input authorities differ")
    semantics = [
        _image_semantics(path, expected_archive_sha256=str(build["archiveSha256"]))
        for path, build in zip(archive_paths, builds, strict=True)
    ]
    for index in (0, 1):
        if (
            semantics[index]["buildkitResultDigest"]
            != builds[index]["buildkitResultDigest"]
        ):
            raise TwoBuilderError("archive BuildKit result differs from build receipt")
    if builds[0]["imageId"] != builds[1]["imageId"]:
        raise TwoBuilderError("candidate image semantics differ")
    for key in (
        "buildkitResultDigest",
        "configSha256",
        "diffIds",
        "runtimeConfigSha256",
    ):
        if semantics[0][key] != semantics[1][key]:
            raise TwoBuilderError("candidate image semantics differ")
    return {
        "builders": [
            {
                "backend": identity["backend"],
                "buildReceiptFileIdentitySha256": build_file_identity,
                "buildReceiptSha256": build_sha256,
                "buildkitNodeId": identity["buildkitNodeId"],
                "buildkitVersion": identity["buildkitVersion"],
                "candidateArchiveFileIdentitySha256": archive_file_identity,
                "candidateArchiveSha256": build["archiveSha256"],
                "candidateReceiptSha256": candidate_sha256,
                "daemonId": identity["daemonId"],
                "dockerExecutableSha256": identity["dockerExecutableSha256"],
                "dockerServerVersion": identity["dockerServerVersion"],
                "runChallenge": challenge,
                "socketIdentitySha256": identity["socketIdentitySha256"],
                "stateRootIdentitySha256": identity["stateRootIdentitySha256"],
            }
            for identity, challenge, build, build_sha256, build_file_identity, archive_file_identity, candidate_sha256 in zip(
                identities,
                challenges,
                builds,
                build_receipt_sha256,
                build_file_identities,
                archive_file_identities,
                candidate_receipt_sha256,
                strict=True,
            )
        ],
        "candidate": {**semantics[0], "imageId": builds[0]["imageId"]},
        "contract": RESULT_CONTRACT,
        "inputAuthoritySha256": _sha256(_canonical(builds[0]["authority"])),
        "nonclaims": [
            "Two daemon and cache states on one host do not prove independence from the shared host, kernel, or operator.",
            "Reproducibility and a clean runtime scan do not prove that the candidate is vulnerability-free or ready for promotion.",
        ],
        "sourceCommit": expected_source_commit,
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-a", type=Path, required=True)
    parser.add_argument("--build-b", type=Path, required=True)
    parser.add_argument("--archive-a", type=Path, required=True)
    parser.add_argument("--archive-b", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = verify_two_builds(
            build_paths=(arguments.build_a, arguments.build_b),
            archive_paths=(arguments.archive_a, arguments.archive_b),
            expected_source_commit=arguments.expected_source_commit,
        )
        write_new_private_json(
            arguments.receipt, result, label="two-builder result receipt"
        )
    except (PrivateReceiptError, TwoBuilderError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
