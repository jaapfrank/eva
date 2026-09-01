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

if __package__:
    from tools.managed_b_candidate.private_receipt import (
        PrivateReceiptError,
        canonical_json,
        write_new_private_json,
    )
else:  # Direct execution from eva2/tools.
    from managed_b_candidate.private_receipt import (
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
_INDEPENDENT_BUILDER_FIELDS = (
    "buildkitNodeId",
    "containerdContainersNamespace",
    "containerdPluginsNamespace",
    "containerdSocketIdentitySha256",
    "daemonId",
    "socketIdentitySha256",
    "stateRootIdentitySha256",
)
_TOOLCHAIN_FIELDS = (
    "backend",
    "buildkitVersion",
    "containerdImageStore",
    "dockerExecutableSha256",
    "dockerServerVersion",
)


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


def _oci_image_binding(
    members: dict[str, bytes],
    *,
    expected_image_id: str,
    config_name: str,
    layers: list[str],
) -> dict[str, str]:
    """Bind the claimed image ID through the OCI index and manifest blobs."""

    try:
        layout = json.loads(members["oci-layout"])
        index = json.loads(members["index.json"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError("candidate OCI index is invalid") from error
    layout = _object(layout, label="candidate OCI layout")
    index = _object(index, label="candidate OCI index")
    manifests = index.get("manifests")
    if (
        layout != {"imageLayoutVersion": "1.0.0"}
        or index.get("schemaVersion") != 2
        or not isinstance(manifests, list)
        or len(manifests) != 1
    ):
        raise TwoBuilderError("candidate OCI index contract differs")
    descriptor = _object(manifests[0], label="candidate OCI image descriptor")
    platform = _object(descriptor.get("platform"), label="candidate OCI platform")
    if (
        descriptor.get("digest") != expected_image_id
        or platform.get("architecture") != "amd64"
        or platform.get("os") != "linux"
    ):
        raise TwoBuilderError("candidate image ID differs from OCI index")
    manifest_name = f"blobs/sha256/{expected_image_id.removeprefix('sha256:')}"
    try:
        manifest_raw = members[manifest_name]
        image_manifest = json.loads(manifest_raw)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError("candidate OCI image manifest is invalid") from error
    if (
        f"sha256:{_sha256(manifest_raw)}" != expected_image_id
        or descriptor.get("size") != len(manifest_raw)
    ):
        raise TwoBuilderError("candidate OCI image manifest digest differs")
    image_manifest = _object(image_manifest, label="candidate OCI image manifest")
    layer_descriptors = image_manifest.get("layers")
    if (
        image_manifest.get("schemaVersion") != 2
        or not isinstance(layer_descriptors, list)
        or len(layer_descriptors) != len(layers)
    ):
        raise TwoBuilderError("candidate OCI image manifest contract differs")
    config_descriptor = _object(
        image_manifest.get("config"), label="candidate OCI config descriptor"
    )
    expected_config_digest = f"sha256:{_sha256(members[config_name])}"
    if (
        config_name
        != f"blobs/sha256/{expected_config_digest.removeprefix('sha256:')}"
        or config_descriptor.get("digest") != expected_config_digest
        or config_descriptor.get("size") != len(members[config_name])
    ):
        raise TwoBuilderError("candidate OCI config descriptor differs")
    for layer_name, value in zip(layers, layer_descriptors, strict=True):
        layer_descriptor = _object(value, label="candidate OCI layer descriptor")
        expected_digest = f"sha256:{_sha256(members[layer_name])}"
        if (
            layer_name
            != f"blobs/sha256/{expected_digest.removeprefix('sha256:')}"
            or layer_descriptor.get("digest") != expected_digest
            or layer_descriptor.get("size") != len(members[layer_name])
        ):
            raise TwoBuilderError("candidate OCI layer descriptor differs")
    return {
        "ociIndexSha256": _sha256(members["index.json"]),
        "ociManifestSha256": _sha256(manifest_raw),
    }


def _image_semantics(
    path: Path, *, expected_archive_sha256: str, expected_image_id: str
) -> dict[str, object]:
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
    oci_binding = _oci_image_binding(
        members,
        expected_image_id=expected_image_id,
        config_name=config_name,
        layers=layers,
    )
    return {
        "archiveSha256": expected_archive_sha256,
        "buildkitResultDigest": f"sha256:{_sha256(config_raw)}",
        "configSha256": _sha256(config_raw),
        "diffIds": diff_ids,
        **oci_binding,
        "runtimeConfigSha256": _sha256(_canonical(runtime)),
        # Ordered DiffIDs authenticate every byte and position of every
        # uncompressed layer.  This compact hash names that layer sequence; it
        # does not claim to be a separately materialised rootfs-tree hash.
        "runtimeLayerSequenceSha256": _sha256(
            _canonical({"orderedUncompressedLayerDiffIds": diff_ids})
        ),
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
        for key in _INDEPENDENT_BUILDER_FIELDS:
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
        archive_path,
        expected_archive_sha256=str(build["archiveSha256"]),
        expected_image_id=str(build["imageId"]),
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
        "candidate": {
            "archiveSha256": semantics["archiveSha256"],
            "buildkitResultDigest": semantics["buildkitResultDigest"],
            "configSha256": semantics["configSha256"],
            "diffIds": semantics["diffIds"],
            "imageId": build["imageId"],
            "runtimeConfigSha256": semantics["runtimeConfigSha256"],
        },
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
    for key in _INDEPENDENT_BUILDER_FIELDS:
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


def _admitted_builder_evidence(
    *,
    path: Path,
    role: str,
    expected_receipt_sha256: str,
    expected_source_commit: str,
    expected_authority_sha256: str,
    build: dict[str, object],
    build_receipt_sha256: str,
    candidate_receipt_sha256: str,
    semantics: dict[str, object],
) -> dict[str, object]:
    """Bind one immutable RB-003/RB-004 admission to its candidate bytes."""

    admission, raw = _json_file(path, label=f"builder-{role} admission")
    if _sha256(raw) != expected_receipt_sha256:
        raise TwoBuilderError(f"builder-{role} admission receipt hash differs")
    expected_task = "RB-003" if role == "A" else "RB-004"
    expected_prefix = f"rb00{3 if role == 'A' else 4}-builder-{role.lower()}-"
    run_id = admission.get("runId")
    if (
        admission.get("contract") != SINGLE_BUILDER_CONTRACT
        or admission.get("status") != "passed"
        or admission.get("sourceCommit") != expected_source_commit
        or admission.get("releaseAuthoritySha256") != expected_authority_sha256
        or not isinstance(run_id, str)
        or not run_id.startswith(expected_prefix)
        or (role == "B" and admission.get("taskId") != expected_task)
        or (role == "A" and "taskId" in admission)
    ):
        raise TwoBuilderError(f"builder-{role} admission contract differs")

    chain = _object(admission.get("chain"), label=f"builder-{role} admission chain")
    for field in (
        "archiveSha256",
        "buildReceiptSha256",
        "candidateReceiptSha256",
        "postflightReceiptSha256",
        "preflightReceiptSha256",
        "releaseAuthorityReceiptSha256",
    ):
        _sha(chain.get(field), label=f"builder-{role} admission {field}")
    if (
        chain.get("archiveSha256") != build["archiveSha256"]
        or chain.get("buildReceiptSha256") != build_receipt_sha256
        or chain.get("candidateReceiptSha256") != candidate_receipt_sha256
    ):
        raise TwoBuilderError(f"builder-{role} admission chain differs from candidate")

    candidate = _object(
        admission.get("candidate"), label=f"builder-{role} admitted candidate"
    )
    expected_candidate = {
        "archiveSha256": semantics["archiveSha256"],
        "buildkitResultDigest": semantics["buildkitResultDigest"],
        "configSha256": semantics["configSha256"],
        "diffIds": semantics["diffIds"],
        "imageId": build["imageId"],
        "runtimeConfigSha256": semantics["runtimeConfigSha256"],
    }
    if candidate != expected_candidate:
        raise TwoBuilderError(
            f"builder-{role} admitted candidate differs from archive semantics"
        )

    build_builder = _object(build["builder"], label=f"builder-{role} build binding")
    identity = _object(
        build_builder.get("identity"), label=f"builder-{role} build identity"
    )
    summary = _object(
        admission.get("builder"), label=f"builder-{role} admission builder"
    )
    summary_fields: tuple[str, ...] = (
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "daemonId",
        "dockerServerVersion",
    )
    if role == "B":
        summary_fields = tuple(identity)
    if (role == "B" and set(summary) != set(summary_fields)) or any(
        summary.get(field) != identity[field] for field in summary_fields
    ):
        raise TwoBuilderError(f"builder-{role} admission builder differs from build")

    replay = _object(
        admission.get("replayKeys"), label=f"builder-{role} admission replay keys"
    )
    for field in (
        "buildReceiptSha256",
        "candidateReceiptSha256",
        "postflightReceiptSha256",
        "preflightReceiptSha256",
    ):
        if replay.get(field) != chain[field]:
            raise TwoBuilderError(f"builder-{role} admission replay chain differs")
    if role == "A" and (
        replay.get("archiveSha256") != chain["archiveSha256"]
        or replay.get("releaseAuthorityReceiptSha256")
        != chain["releaseAuthorityReceiptSha256"]
    ):
        raise TwoBuilderError("builder-A admission replay chain differs")
    identity_replay_fields = _INDEPENDENT_BUILDER_FIELDS
    if role == "A":
        identity_replay_fields = ("daemonId", "stateRootIdentitySha256")
    for field in identity_replay_fields:
        if replay.get(field) != identity[field]:
            raise TwoBuilderError(f"builder-{role} admission replay identity differs")
    challenges = tuple(
        _challenge(replay.get(field), label=f"builder-{role} {field}")
        for field in (
            "admissionChallenge",
            "buildRunChallenge",
            "observationChallenge",
        )
    )
    if len(set(challenges)) != len(challenges):
        raise TwoBuilderError(f"builder-{role} admission challenges were reused")
    if replay.get("buildRunChallenge") != build_builder["runChallenge"]:
        raise TwoBuilderError(f"builder-{role} admission build challenge differs")
    return {
        "chain": chain,
        "challenges": challenges,
        "receiptSha256": expected_receipt_sha256,
        "runId": run_id,
        "taskId": expected_task,
    }


def verify_two_builds(
    *,
    admission_paths: tuple[Path, Path],
    build_paths: tuple[Path, Path],
    archive_paths: tuple[Path, Path],
    expected_admission_sha256: tuple[str, str],
    expected_source_commit: str,
    expected_authority_sha256: str,
) -> dict[str, object]:
    if _COMMIT.fullmatch(expected_source_commit) is None:
        raise TwoBuilderError("expected source commit is invalid")
    if not all(
        len(paths) == 2
        for paths in (
            admission_paths,
            build_paths,
            archive_paths,
            expected_admission_sha256,
        )
    ):
        raise TwoBuilderError("exactly two admitted builder evidence sets are required")
    expected_authority_sha256 = _sha(
        expected_authority_sha256, label="expected release authority"
    )
    expected_admission_sha256 = tuple(
        _sha(value, label="expected admission receipt")
        for value in expected_admission_sha256
    )
    candidate_receipt_paths = tuple(
        archive_path.parent / "candidate-image-receipt.json"
        for archive_path in archive_paths
    )
    evidence_paths = (
        *admission_paths,
        *build_paths,
        *archive_paths,
        *candidate_receipt_paths,
    )
    if len(set(evidence_paths)) != len(evidence_paths):
        raise TwoBuilderError("distinct physical evidence paths are required")
    build_documents = [_json_file(path, label="build receipt") for path in build_paths]
    build_receipt_sha256 = [_sha256(raw) for _value, raw in build_documents]
    if build_receipt_sha256[0] == build_receipt_sha256[1]:
        raise TwoBuilderError("one build receipt was reused")
    evidence_file_identities = [
        _physical_identity(path, label="two-builder evidence")
        for path in evidence_paths
    ]
    if len(set(evidence_file_identities)) != len(evidence_file_identities):
        raise TwoBuilderError("one physical two-builder artifact was reused")
    admission_file_identities = evidence_file_identities[0:2]
    build_file_identities = evidence_file_identities[2:4]
    archive_file_identities = evidence_file_identities[4:6]
    candidate_receipt_file_identities = evidence_file_identities[6:8]
    builds = [
        _build_inputs(
            document[0],
            expected_commit=expected_source_commit,
        )
        for document in build_documents
    ]
    candidate_receipt_sha256: list[str] = []
    for candidate_receipt_path, build in zip(
        candidate_receipt_paths, builds, strict=True
    ):
        candidate_receipt, candidate_raw = _json_file(
            candidate_receipt_path, label="candidate image receipt"
        )
        if candidate_receipt != build["candidateReceipt"]:
            raise TwoBuilderError(
                "persisted candidate receipt differs from its build receipt"
            )
        candidate_receipt_sha256.append(_sha256(candidate_raw))
    if candidate_receipt_sha256[0] == candidate_receipt_sha256[1]:
        raise TwoBuilderError("one candidate receipt was reused")
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
    if any(identities[0][key] != identities[1][key] for key in _TOOLCHAIN_FIELDS):
        raise TwoBuilderError("builder toolchains differ")
    for key in _INDEPENDENT_BUILDER_FIELDS:
        if identities[0][key] == identities[1][key]:
            raise TwoBuilderError(
                "builder identities or state roots are not independent"
            )
    if builds[0]["authority"] != builds[1]["authority"]:
        raise TwoBuilderError("build input authorities differ")
    semantics = [
        _image_semantics(
            path,
            expected_archive_sha256=str(build["archiveSha256"]),
            expected_image_id=str(build["imageId"]),
        )
        for path, build in zip(archive_paths, builds, strict=True)
    ]
    for index in (0, 1):
        if (
            semantics[index]["buildkitResultDigest"]
            != builds[index]["buildkitResultDigest"]
        ):
            raise TwoBuilderError("archive BuildKit result differs from build receipt")
    semantic_comparisons = {
        "diffIds": "candidate uncompressed layers differ",
        "configSha256": "candidate OCI config differs",
        "runtimeConfigSha256": "candidate runtime config differs",
        "buildkitResultDigest": "candidate BuildKit results differ",
        "ociManifestSha256": "candidate OCI image manifests differ",
    }
    for key, message in semantic_comparisons.items():
        if semantics[0][key] != semantics[1][key]:
            raise TwoBuilderError(message)
    if builds[0]["imageId"] != builds[1]["imageId"]:
        raise TwoBuilderError("candidate image IDs differ")

    admissions = [
        _admitted_builder_evidence(
            path=admission_path,
            role=role,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_source_commit=expected_source_commit,
            expected_authority_sha256=expected_authority_sha256,
            build=build,
            build_receipt_sha256=build_sha256,
            candidate_receipt_sha256=candidate_sha256,
            semantics=image_semantics,
        )
        for admission_path, role, expected_receipt_sha256, build, build_sha256, candidate_sha256, image_semantics in zip(
            admission_paths,
            ("A", "B"),
            expected_admission_sha256,
            builds,
            build_receipt_sha256,
            candidate_receipt_sha256,
            semantics,
            strict=True,
        )
    ]
    if admissions[0]["receiptSha256"] == admissions[1]["receiptSha256"]:
        raise TwoBuilderError("one builder admission was reused")
    admission_chains = [
        _object(admission["chain"], label="builder admission chain")
        for admission in admissions
    ]
    if (
        admission_chains[0]["releaseAuthorityReceiptSha256"]
        != admission_chains[1]["releaseAuthorityReceiptSha256"]
    ):
        raise TwoBuilderError("release authority receipt differs between builders")
    for field in (
        "buildReceiptSha256",
        "candidateReceiptSha256",
        "postflightReceiptSha256",
        "preflightReceiptSha256",
    ):
        if admission_chains[0][field] == admission_chains[1][field]:
            raise TwoBuilderError("one builder proof was reused")
    admission_challenges = [
        challenge
        for admission in admissions
        for challenge in admission["challenges"]
    ]
    if len(set(admission_challenges)) != 6:
        raise TwoBuilderError("one admission, build, or observation challenge was reused")

    builder_results: list[dict[str, object]] = []
    for index in range(2):
        identity = identities[index]
        admission = admissions[index]
        build = builds[index]
        builder_results.append(
            {
                "admissionChain": admission["chain"],
                "admissionReceiptFileIdentitySha256": admission_file_identities[index],
                "admissionReceiptSha256": admission["receiptSha256"],
                "admissionRunId": admission["runId"],
                "buildReceiptFileIdentitySha256": build_file_identities[index],
                "buildReceiptSha256": build_receipt_sha256[index],
                "candidateArchiveFileIdentitySha256": archive_file_identities[index],
                "candidateArchiveSha256": build["archiveSha256"],
                "candidateReceiptFileIdentitySha256": (
                    candidate_receipt_file_identities[index]
                ),
                "candidateReceiptSha256": candidate_receipt_sha256[index],
                "runChallenge": challenges[index],
                "taskId": admission["taskId"],
                **{
                    field: identity[field]
                    for field in (*_TOOLCHAIN_FIELDS, *_INDEPENDENT_BUILDER_FIELDS)
                },
            }
        )
    return {
        "builders": builder_results,
        "candidate": {**semantics[0], "imageId": builds[0]["imageId"]},
        "contract": RESULT_CONTRACT,
        "inputAuthoritySha256": _sha256(_canonical(builds[0]["authority"])),
        "nonclaims": [
            "Two daemon and cache states on one host do not prove independence from the shared host, kernel, or operator.",
            (
                "Passed status is meaningful only when the expected admission, "
                "release-authority, and source anchors are independently matched "
                "to the accepted RB-003 and RB-004 public evidence."
            ),
            "Reproducibility and a clean runtime scan do not prove that the candidate is vulnerability-free or ready for promotion.",
        ],
        "releaseAuthoritySha256": expected_authority_sha256,
        "sourceCommit": expected_source_commit,
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-a", type=Path, required=True)
    parser.add_argument("--admission-b", type=Path, required=True)
    parser.add_argument("--build-a", type=Path, required=True)
    parser.add_argument("--build-b", type=Path, required=True)
    parser.add_argument("--archive-a", type=Path, required=True)
    parser.add_argument("--archive-b", type=Path, required=True)
    parser.add_argument("--expected-admission-a-sha256", required=True)
    parser.add_argument("--expected-admission-b-sha256", required=True)
    parser.add_argument("--expected-release-authority-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = verify_two_builds(
            admission_paths=(arguments.admission_a, arguments.admission_b),
            build_paths=(arguments.build_a, arguments.build_b),
            archive_paths=(arguments.archive_a, arguments.archive_b),
            expected_admission_sha256=(
                arguments.expected_admission_a_sha256,
                arguments.expected_admission_b_sha256,
            ),
            expected_source_commit=arguments.expected_source_commit,
            expected_authority_sha256=(
                arguments.expected_release_authority_sha256
            ),
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
