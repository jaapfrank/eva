#!/usr/bin/env python3
"""Verify one candidate built by two independently identified build states."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
from typing import Any, BinaryIO, Callable, Iterator

if __package__:
    from tools.managed_b_candidate.private_receipt import (
        PrivateReceiptError,
        canonical_json,
        write_new_private_json,
    )
else:  # Direct execution from tools.
    from managed_b_candidate.private_receipt import (
        PrivateReceiptError,
        canonical_json,
        write_new_private_json,
    )


BUILDER_CONTRACT = "EvaMeasuredDockerBuilder/v3"
BUILD_CONTRACT = "EvaManagedBOfflineCandidateBuildReceipt/v2"
RESULT_CONTRACT = "EvaTwoBuilderReproducibilityReceipt/v2"
SINGLE_BUILDER_CONTRACT = "EvaSingleBuilderAdmissionReceipt/v2"
LEGACY_SINGLE_BUILDER_CONTRACT = "EvaSingleBuilderAdmissionReceipt/v1"
BUILDER_A_PREFLIGHT_CONTRACT = "EvaRB003PrivateEmptyBuilderPreflight/v1"
BUILDER_A_POSTFLIGHT_CONTRACT = "EvaRB003PrivateEmptyBuilderPostflight/v1"
BUILDER_B_PREFLIGHT_CONTRACT = "EvaRB004PrivateEmptyBuilderPreflight/v1"
BUILDER_B_POSTFLIGHT_CONTRACT = "EvaRB004PrivateEmptyBuilderPostflight/v1"
RELEASE_AUTHORITY_CONTRACT = "EvaManagedBReleaseInputReceipt/v1"
CLEANUP_CONTRACT = "EvaRR002PrivateBuilderCleanupReceipt/v2"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_SOURCE_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 1_024
_MAX_JSON_MEMBER_BYTES = 4 * 1024 * 1024
_MAX_LAYER_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_LAYER_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_LAYER_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_TOTAL_LAYER_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_LAYER_MEMBERS = 50_000
_MAX_TOTAL_LAYER_MEMBERS = 100_000
_MAX_BUILDX_EXECUTABLE_BYTES = 256 * 1024 * 1024
_RR002_ARTIFACT_BASE = Path("/home/administrator/project/.eva2-artifacts")
_EXPECTED_HISTORICAL_ADMISSION_SHA256S = {
    "RB-003": "b60b16fe4065b1deef7a207ef05d98e259608f436e1b7bd6a07ca62f4271de70",
    "RB-004": "20f7e8e6b4add92fa5f19d30a1fb8e5f8337e8c415ebbd8c5aaead7fc7eeee49",
}
_RUNTIME_SOURCE_ROOTS = (
    "requirements-4d.lock",
    "pyproject.toml",
    "src",
    "connector_sdk",
    "alembic.ini",
    "migrations",
    "schemas",
)
_UNIT_NAME = re.compile(r"[A-Za-z0-9_.@-]{1,128}\Z")
_NAMESPACE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
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
    "buildxExecutableSha256",
    "buildxVersion",
    "containerdImageStore",
    "dockerExecutableSha256",
    "dockerServerVersion",
)
_CHAIN_FIELDS = (
    "archiveSha256",
    "buildReceiptSha256",
    "candidateReceiptSha256",
    "cleanupReceiptSha256",
    "postflightReceiptSha256",
    "preflightReceiptSha256",
    "releaseAuthorityReceiptSha256",
)
_CHALLENGE_FIELDS = (
    "admissionChallenge",
    "buildRunChallenge",
    "observationChallenge",
)
_REPLAY_KEYS = frozenset(
    (*_CHAIN_FIELDS, *_CHALLENGE_FIELDS, *_INDEPENDENT_BUILDER_FIELDS)
)
_ADMISSION_KEYS = {
    "builder",
    "candidate",
    "chain",
    "contract",
    "priorAdmissionReceiptSha256s",
    "releaseAuthoritySha256",
    "replayKeys",
    "runId",
    "sourceCommit",
    "status",
    "taskId",
}


class TwoBuilderError(RuntimeError):
    """One captured builder, build, archive, or comparison is invalid."""


OperatorRunner = Callable[
    [list[str], Path | None, dict[str, str], frozenset[int]],
    subprocess.CompletedProcess[str],
]


def _canonical(value: object) -> bytes:
    return canonical_json(value)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_stat(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@contextmanager
def _safe_file_stream(
    path: Path, *, label: str, maximum: int
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.name in {"", ".", ".."}
    ):
        raise TwoBuilderError(f"{label} path must be explicit and absolute")
    directory = -1
    descriptor = -1
    try:
        if path.parent.resolve(strict=True) != path.parent:
            raise TwoBuilderError(f"{label} parent must not use links")
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        parent = os.fstat(directory)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise TwoBuilderError(f"{label} parent must be private and owned")
        metadata = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        opened = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_size <= 0
            or opened.st_size > maximum
            or any(
                getattr(metadata, field) != getattr(opened, field)
                for field in stable_fields
            )
        ):
            raise TwoBuilderError(f"{label} is not one safe private file")
        stream = os.fdopen(descriptor, "rb", closefd=False)
        try:
            yield stream, opened
        finally:
            stream.close()
        final = os.fstat(descriptor)
        if any(
            getattr(opened, field) != getattr(final, field)
            for field in stable_fields
        ):
            raise TwoBuilderError(f"{label} changed while being read")
    except TwoBuilderError:
        raise
    except (OSError, RuntimeError) as error:
        raise TwoBuilderError(f"{label} is inaccessible") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory >= 0:
            os.close(directory)


def _safe_file(path: Path, *, label: str, maximum: int) -> bytes:
    with _safe_file_stream(path, label=label, maximum=maximum) as (stream, opened):
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise TwoBuilderError(f"{label} changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise TwoBuilderError(f"{label} changed while being read")
        return b"".join(chunks)


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


def _json_member(
    members: dict[str, bytes], name: str, *, label: str
) -> tuple[Any, bytes]:
    try:
        raw = members[name]
    except KeyError as error:
        raise TwoBuilderError(f"{label} is absent") from error
    if not 0 < len(raw) <= _MAX_JSON_MEMBER_BYTES:
        raise TwoBuilderError(f"{label} exceeds JSON member limit")
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwoBuilderError(f"{label} is invalid JSON") from error


def _builder_measurement(value: object) -> dict[str, object]:
    value = _object(value, label="builder measurement")
    expected_keys = {
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "buildxExecutableSha256",
        "buildxVersion",
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
        "buildxVersion",
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
        "buildxExecutableSha256",
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
    source_date_epoch = source.get("date_epoch")
    if (
        isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch <= 0
    ):
        raise TwoBuilderError("source date epoch is invalid")
    candidate = _object(value.get("candidate"), label="build candidate")
    candidate_receipt = _object(candidate.get("receipt"), label="candidate receipt")
    builder = _builder(candidate_receipt.get("builder"))
    image_id = _sha(candidate.get("image_id"), label="candidate image ID", image=True)
    buildkit_result_digest = _sha(
        candidate_receipt.get("candidate_buildkit_result_digest"),
        label="candidate BuildKit result digest",
        image=True,
    )
    context = _object(stages.get("build_context"), label="build-context stage")
    if (
        candidate_receipt.get("contract")
        != "EvaManagedBOfflineCandidateImageReceipt/v3"
        or candidate_receipt.get("status") != "built_local_candidate"
        or candidate_receipt.get("candidate_image_id") != image_id
        or candidate_receipt.get("candidate_image_config_digest")
        != buildkit_result_digest
        or candidate_receipt.get("candidate_source_commit") != expected_commit
        or candidate_receipt.get("candidate_source_date_epoch")
        != source_date_epoch
        or candidate_receipt.get("context_tree_sha256")
        != context.get("tree_sha256")
        or candidate_receipt.get("candidate_provenance_attestation_enabled")
        is not False
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
        "sourceDateEpoch": source_date_epoch,
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


def _read_layer(name: str, raw: bytes, *, remaining: int) -> tuple[bytes, str]:
    """Bound one layer expansion and calculate its DiffID in the same pass."""

    path = PurePosixPath(name)
    if len(path.parts) == 3 and path.parts[:2] == ("blobs", "sha256"):
        if _SHA256.fullmatch(path.name) is None or _sha256(raw) != path.name:
            raise TwoBuilderError("candidate compressed layer digest differs")
    maximum = min(_MAX_LAYER_UNCOMPRESSED_BYTES, remaining)
    stream: Any = io.BytesIO(raw)
    try:
        if raw.startswith(b"\x1f\x8b"):
            stream = gzip.GzipFile(fileobj=stream, mode="rb")
        output = io.BytesIO()
        digest = hashlib.sha256()
        total = 0
        while chunk := stream.read(min(1024 * 1024, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise TwoBuilderError("candidate layer exceeds expansion limit")
            digest.update(chunk)
            output.write(chunk)
    except (EOFError, OSError) as error:
        raise TwoBuilderError("candidate compressed layer is invalid") from error
    finally:
        stream.close()
    return output.getvalue(), f"sha256:{digest.hexdigest()}"


def _canonical_source_archive(
    files: dict[str, tuple[bytes, int]], *, source_date_epoch: int
) -> bytes:
    output = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=output, mode="w", format=tarfile.PAX_FORMAT
        ) as archive:
            for name, (content, mode) in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.gid = 0
                member.gname = ""
                member.mtime = source_date_epoch
                member.mode = mode
                member.size = len(content)
                member.uid = 0
                member.uname = ""
                archive.addfile(member, io.BytesIO(content))
    except (tarfile.TarError, ValueError) as error:
        raise TwoBuilderError("candidate source archive is invalid") from error
    return output.getvalue()


def _source_runtime_entries(
    path: Path,
    *,
    expected_sha256: str,
    source_date_epoch: int,
) -> dict[str, dict[str, object]]:
    """Read the immutable source archive and name every file copied to /opt/eva."""

    raw = _safe_file(
        path, label="candidate source archive", maximum=_MAX_SOURCE_ARCHIVE_BYTES
    )
    if _sha256(raw) != expected_sha256:
        raise TwoBuilderError("candidate source archive bytes differ from authority")
    files: dict[str, tuple[bytes, int]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                name = _member_name(member)
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or name in files
                    or member.uid != 0
                    or member.gid != 0
                    or member.uname != ""
                    or member.gname != ""
                    or member.mtime != source_date_epoch
                    or member.mode not in {0o644, 0o755}
                ):
                    raise TwoBuilderError("candidate source archive is not canonical")
                stream = archive.extractfile(member)
                if stream is None:
                    raise TwoBuilderError("candidate source archive is unreadable")
                files[name] = (stream.read(), member.mode)
    except (tarfile.TarError, OSError) as error:
        raise TwoBuilderError("candidate source archive is invalid") from error
    if _canonical_source_archive(
        files, source_date_epoch=source_date_epoch
    ) != raw:
        raise TwoBuilderError("candidate source archive is not canonical")

    selected: dict[str, dict[str, object]] = {}
    present_roots: set[str] = set()
    for name, (content, mode) in files.items():
        for root in _RUNTIME_SOURCE_ROOTS:
            if name == root or name.startswith(f"{root}/"):
                present_roots.add(root)
                runtime_path = f"opt/eva/{name}"
                selected[runtime_path] = {
                    "gid": 0,
                    "kind": "file",
                    "mtime": source_date_epoch,
                    "mode": 0o555 if mode == 0o755 else 0o444,
                    "path": runtime_path,
                    "sha256": _sha256(content),
                    "size": len(content),
                    "uid": 0,
                }
                break
    if present_roots != set(_RUNTIME_SOURCE_ROOTS) or not selected:
        raise TwoBuilderError("candidate source archive omits runtime source inputs")
    return selected


def _sealed_context_source_archive(
    root: Path, *, expected_tree_sha256: str, source_date_epoch: int
) -> bytes:
    """Recreate the already sealed source archive from the persisted context."""

    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise TwoBuilderError("materialized build context path is unsafe")
    entries: list[dict[str, object]] = []
    source_files: dict[str, tuple[bytes, int]] = {}
    for directory, subdirectories, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        subdirectories.sort()
        filenames.sort()
        directory_path = Path(directory)
        directory_metadata = directory_path.lstat()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or stat.S_ISLNK(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            raise TwoBuilderError("materialized build context is unsafe")
        for name in (*subdirectories, *filenames):
            child = directory_path / name
            metadata = child.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise TwoBuilderError("materialized build context is unsafe")
        for name in filenames:
            path = directory_path / name
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise TwoBuilderError("materialized build context file is unsafe")
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened = os.fstat(descriptor)
                chunks: list[bytes] = []
                while block := os.read(descriptor, 1024 * 1024):
                    chunks.append(block)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if (
                _stable_stat(before) != _stable_stat(opened)
                or _stable_stat(opened) != _stable_stat(after)
            ):
                raise TwoBuilderError("materialized build context changed while read")
            content = b"".join(chunks)
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(opened.st_mode)
            entries.append(
                {
                    "mode": mode,
                    "path": relative,
                    "sha256": _sha256(content),
                    "size": len(content),
                }
            )
            if relative != "wheelhouse" and not relative.startswith("wheelhouse/"):
                source_files[relative] = (
                    content,
                    0o755 if mode & 0o111 else 0o644,
                )
    entries.sort(key=lambda entry: str(entry["path"]))
    tree_raw = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    if _sha256(tree_raw) != expected_tree_sha256:
        raise TwoBuilderError("materialized build context tree differs")
    return _canonical_source_archive(
        source_files, source_date_epoch=source_date_epoch
    )


def _write_new_private_bytes(path: Path, content: bytes, *, label: str) -> None:
    if not content or path.parent.resolve(strict=True) != path.parent:
        raise TwoBuilderError(f"{label} destination is unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise TwoBuilderError(f"{label} write did not progress")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as error:
        raise TwoBuilderError(f"{label} could not be written") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _runtime_source_tree(
    layers: list[bytes], expected: dict[str, dict[str, object]]
) -> str:
    """Verify source-copied runtime files through final OCI overlay contents."""

    observed: dict[str, dict[str, object]] = {}
    expected_paths = set(expected)
    expected_mtimes = {entry.get("mtime") for entry in expected.values()}
    if len(expected_mtimes) != 1:
        raise TwoBuilderError("candidate source archive timestamp differs")
    source_date_epoch = next(iter(expected_mtimes))
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int):
        raise TwoBuilderError("candidate source archive timestamp differs")
    expected_directories = {
        ancestor.as_posix(): {
            "gid": 0,
            "kind": "directory",
            "mtime": source_date_epoch,
            "mode": (
                0o755
                if len(ancestor.parts) <= 3
                else 0o555
            ),
            "path": ancestor.as_posix(),
            "size": 0,
            "uid": 0,
        }
        for name in expected_paths
        for ancestor in PurePosixPath(name).parents
        if ancestor.as_posix() not in {".", ""}
    }
    expected_state = {**expected_directories, **expected}
    total_members = 0
    total_member_bytes = 0
    for layer_raw in layers:
        layer_entries: dict[str, dict[str, object]] = {}
        whiteouts: set[str] = set()
        opaque_directories: set[str] = set()
        layer_members = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(layer_raw), mode="r:") as layer:
                for member in layer:
                    layer_members += 1
                    total_members += 1
                    total_member_bytes += member.size
                    if (
                        layer_members > _MAX_LAYER_MEMBERS
                        or total_members > _MAX_TOTAL_LAYER_MEMBERS
                        or member.size < 0
                        or member.size > _MAX_LAYER_MEMBER_BYTES
                        or total_member_bytes > _MAX_TOTAL_LAYER_MEMBER_BYTES
                    ):
                        raise TwoBuilderError("candidate runtime layer exceeds member limit")
                    raw_name = member.name.removeprefix("./").rstrip("/")
                    if not raw_name:
                        continue
                    name = _member_name(tarfile.TarInfo(raw_name))
                    path = PurePosixPath(name)
                    basename = path.name
                    parent = path.parent.as_posix()
                    if basename.startswith(".wh."):
                        if (
                            member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                            or member.size != 0
                            or member.linkname
                        ):
                            raise TwoBuilderError("candidate runtime whiteout is invalid")
                        if basename == ".wh..wh..opq":
                            opaque_directories.add(parent)
                        else:
                            target = basename.removeprefix(".wh.")
                            if not target:
                                raise TwoBuilderError(
                                    "candidate runtime whiteout is invalid"
                                )
                            whiteouts.add((path.parent / target).as_posix())
                        continue
                    # Everything below /opt/eva is executable product input at
                    # runtime.  It must therefore be present in the canonical
                    # source archive, not merely live outside one of the known
                    # source roots (for example as a top-level import hook or
                    # a forgotten build/wheelhouse directory).
                    relevant = (
                        name in {"opt", "opt/eva"}
                        or name.startswith("opt/eva/")
                    )
                    if not relevant:
                        continue
                    if name in layer_entries:
                        raise TwoBuilderError("candidate runtime source path is duplicated")
                    entry: dict[str, object] = {
                        "gid": member.gid,
                        "kind": (
                            "directory"
                            if member.isdir()
                            else "file"
                            if member.isfile() and not member.islnk()
                            else "other"
                        ),
                        "mtime": member.mtime,
                        "mode": stat.S_IMODE(member.mode),
                        "path": name,
                        "size": member.size,
                        "uid": member.uid,
                    }
                    if entry["kind"] == "file":
                        stream = layer.extractfile(member)
                        if stream is None:
                            raise TwoBuilderError(
                                "candidate runtime source file is unreadable"
                            )
                        content = stream.read(_MAX_LAYER_MEMBER_BYTES + 1)
                        if len(content) != member.size:
                            raise TwoBuilderError(
                                "candidate runtime source file is unreadable"
                            )
                        entry["sha256"] = _sha256(content)
                    layer_entries[name] = entry
        except (tarfile.TarError, OSError) as error:
            raise TwoBuilderError("candidate runtime layer is invalid") from error

        # OCI opaque markers hide only children from lower layers.  Entries in
        # this layer survive regardless of tar member order.
        for directory in opaque_directories:
            prefix = "" if directory == "." else f"{directory}/"
            observed = {
                key: value
                for key, value in observed.items()
                if not key.startswith(prefix)
            }
        for removed in whiteouts:
            observed = {
                key: value
                for key, value in observed.items()
                if key != removed and not key.startswith(f"{removed}/")
            }
        for name, entry in layer_entries.items():
            if entry["kind"] != "directory":
                observed = {
                    key: value
                    for key, value in observed.items()
                    if not key.startswith(f"{name}/")
                }
            observed[name] = entry
    if observed != expected_state:
        raise TwoBuilderError("candidate runtime source differs from source archive")
    return _sha256(_canonical([observed[name] for name in sorted(observed)]))


def _oci_image_binding(
    members: dict[str, bytes],
    *,
    expected_image_id: str,
    config_name: str,
    layers: list[str],
) -> dict[str, str]:
    """Bind the claimed image ID through the OCI index and manifest blobs."""

    layout, _ = _json_member(
        members, "oci-layout", label="candidate OCI layout"
    )
    index, _ = _json_member(members, "index.json", label="candidate OCI index")
    layout = _object(layout, label="candidate OCI layout")
    index = _object(index, label="candidate OCI index")
    manifests = index.get("manifests")
    if (
        layout != {"imageLayoutVersion": "1.0.0"}
        or index.get("schemaVersion") != 2
        or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
        or not isinstance(manifests, list)
        or len(manifests) != 1
    ):
        raise TwoBuilderError("candidate OCI index contract differs")
    descriptor = _object(manifests[0], label="candidate OCI image descriptor")
    platform = _object(descriptor.get("platform"), label="candidate OCI platform")
    manifest_digest = _sha(
        descriptor.get("digest"),
        label="candidate OCI image manifest digest",
        image=True,
    )
    if (
        descriptor.get("mediaType")
        != "application/vnd.docker.distribution.manifest.v2+json"
        or platform.get("architecture") != "amd64"
        or platform.get("os") != "linux"
    ):
        raise TwoBuilderError("candidate OCI descriptor contract differs")
    if manifest_digest != expected_image_id:
        raise TwoBuilderError("candidate image ID differs from OCI manifest")
    manifest_name = f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}"
    image_manifest, manifest_raw = _json_member(
        members, manifest_name, label="candidate OCI image manifest"
    )
    if (
        f"sha256:{_sha256(manifest_raw)}" != manifest_digest
        or descriptor.get("size") != len(manifest_raw)
    ):
        raise TwoBuilderError("candidate OCI image manifest digest differs")
    image_manifest = _object(image_manifest, label="candidate OCI image manifest")
    layer_descriptors = image_manifest.get("layers")
    if (
        image_manifest.get("schemaVersion") != 2
        or image_manifest.get("mediaType")
        != "application/vnd.docker.distribution.manifest.v2+json"
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
        or config_descriptor.get("mediaType")
        != "application/vnd.docker.container.image.v1+json"
        or config_descriptor.get("digest") != expected_config_digest
        or config_descriptor.get("size") != len(members[config_name])
    ):
        raise TwoBuilderError("candidate OCI config binding differs")
    for layer_name, value in zip(layers, layer_descriptors, strict=True):
        layer_descriptor = _object(value, label="candidate OCI layer descriptor")
        expected_digest = f"sha256:{_sha256(members[layer_name])}"
        if (
            layer_name
            != f"blobs/sha256/{expected_digest.removeprefix('sha256:')}"
            or layer_descriptor.get("mediaType")
            != "application/vnd.docker.image.rootfs.diff.tar.gzip"
            or layer_descriptor.get("digest") != expected_digest
            or layer_descriptor.get("size") != len(members[layer_name])
        ):
            raise TwoBuilderError("candidate OCI layer descriptor differs")
    return {
        "ociIndexSha256": _sha256(members["index.json"]),
        "ociManifestSha256": _sha256(manifest_raw),
    }


def _image_semantics(
    path: Path,
    *,
    source_archive_path: Path,
    expected_archive_sha256: str,
    expected_image_id: str,
    expected_source_archive_sha256: str,
    expected_source_commit: str,
    expected_source_date_epoch: int,
) -> dict[str, object]:
    members: dict[str, bytes] = {}
    member_count = 0
    extracted_bytes = 0
    try:
        with _safe_file_stream(
            path, label="candidate archive", maximum=_MAX_ARCHIVE_BYTES
        ) as (source, metadata):
            archive_digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                archive_digest.update(chunk)
            if archive_digest.hexdigest() != expected_archive_sha256:
                raise TwoBuilderError(
                    "candidate archive hash differs from build receipt"
                )
            source.seek(0)
            with tarfile.open(fileobj=source, mode="r:") as archive:
                for member in archive:
                    member_count += 1
                    extracted_bytes += member.size
                    if (
                        member_count > _MAX_ARCHIVE_MEMBERS
                        or member.size < 0
                        or member.size > min(
                            metadata.st_size, _MAX_ARCHIVE_MEMBER_BYTES
                        )
                        or extracted_bytes > _MAX_ARCHIVE_BYTES
                    ):
                        raise TwoBuilderError("candidate archive member is invalid")
                    name = _member_name(member)
                    if member.isdir():
                        if member.size != 0 or member.linkname:
                            raise TwoBuilderError(
                                "candidate archive member is invalid"
                            )
                        continue
                    if (
                        not member.isfile()
                        or member.islnk()
                        or member.issym()
                        or name in members
                    ):
                        raise TwoBuilderError("candidate archive member is invalid")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise TwoBuilderError(
                            "candidate archive member is unreadable"
                        )
                    content = stream.read(member.size + 1)
                    if len(content) != member.size:
                        raise TwoBuilderError(
                            "candidate archive member is unreadable"
                        )
                    members[name] = content
    except (tarfile.TarError, OSError) as error:
        raise TwoBuilderError("candidate archive is invalid") from error
    manifest, _ = _json_member(
        members, "manifest.json", label="candidate Docker manifest"
    )
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
    config, config_raw = _json_member(
        members, config_name, label="candidate config"
    )
    config = _object(config, label="candidate config")
    rootfs = _object(config.get("rootfs"), label="candidate rootfs")
    diff_ids = rootfs.get("diff_ids")
    oci_binding = _oci_image_binding(
        members,
        expected_image_id=expected_image_id,
        config_name=config_name,
        layers=layers,
    )
    layer_raw: list[bytes] = []
    expected_diff_ids: list[str] = []
    remaining = _MAX_TOTAL_LAYER_UNCOMPRESSED_BYTES
    for name in layers:
        try:
            stored = members.pop(name)
        except KeyError as error:
            raise TwoBuilderError("candidate config or layer is absent") from error
        uncompressed, diff_id = _read_layer(name, stored, remaining=remaining)
        remaining -= len(uncompressed)
        layer_raw.append(uncompressed)
        expected_diff_ids.append(diff_id)
    if rootfs.get("type") != "layers" or diff_ids != expected_diff_ids:
        raise TwoBuilderError("candidate layers differ from their DiffIDs")
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise TwoBuilderError("candidate platform differs")
    runtime = _object(config.get("config"), label="candidate runtime config")
    labels = _object(runtime.get("Labels"), label="candidate source labels")
    if (
        labels.get("io.eva2.source-commit") != expected_source_commit
        or labels.get("io.eva2.source-date-epoch")
        != str(expected_source_date_epoch)
        or labels.get("io.eva2.source-refresh") != "git-archive-v1"
    ):
        raise TwoBuilderError("candidate image source identity differs")
    source_entries = _source_runtime_entries(
        source_archive_path,
        expected_sha256=expected_source_archive_sha256,
        source_date_epoch=expected_source_date_epoch,
    )
    runtime_source_tree_sha256 = _runtime_source_tree(layer_raw, source_entries)
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
        "runtimeSourceTreeSha256": runtime_source_tree_sha256,
        "sourceCommit": expected_source_commit,
        "sourceDateEpoch": expected_source_date_epoch,
        "sourceRefresh": "git-archive-v1",
    }


_CANDIDATE_FIELDS = {
    "archiveSha256",
    "buildkitResultDigest",
    "configSha256",
    "diffIds",
    "imageId",
    "ociIndexSha256",
    "ociManifestSha256",
    "runtimeConfigSha256",
    "runtimeLayerSequenceSha256",
    "runtimeSourceTreeSha256",
    "sourceCommit",
    "sourceDateEpoch",
    "sourceRefresh",
}
_PROOF_REPLAY_FIELDS = (
    "buildReceiptSha256",
    "candidateReceiptSha256",
    "cleanupReceiptSha256",
    "postflightReceiptSha256",
    "preflightReceiptSha256",
)
_DENY_REPLAY_FIELDS = frozenset(
    (*_CHALLENGE_FIELDS, *_INDEPENDENT_BUILDER_FIELDS, *_PROOF_REPLAY_FIELDS)
)


def _candidate_summary(
    semantics: dict[str, object], *, image_id: object
) -> dict[str, object]:
    return {**semantics, "imageId": image_id}


def _candidate_document(value: object, *, label: str) -> dict[str, Any]:
    candidate = _object(value, label=label)
    _exact_keys(candidate, _CANDIDATE_FIELDS, label=label)
    _sha(candidate.get("archiveSha256"), label=f"{label} archive")
    for field in (
        "configSha256",
        "ociIndexSha256",
        "ociManifestSha256",
        "runtimeConfigSha256",
        "runtimeLayerSequenceSha256",
        "runtimeSourceTreeSha256",
    ):
        _sha(candidate.get(field), label=f"{label} {field}")
    for field in ("buildkitResultDigest", "imageId"):
        _sha(candidate.get(field), label=f"{label} {field}", image=True)
    diff_ids = candidate.get("diffIds")
    if not isinstance(diff_ids, list) or not diff_ids:
        raise TwoBuilderError(f"{label} DiffIDs are invalid")
    for diff_id in diff_ids:
        _sha(diff_id, label=f"{label} DiffID", image=True)
    source_commit = candidate.get("sourceCommit")
    source_date_epoch = candidate.get("sourceDateEpoch")
    if (
        not isinstance(source_commit, str)
        or _COMMIT.fullmatch(source_commit) is None
        or isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch <= 0
        or candidate.get("sourceRefresh") != "git-archive-v1"
    ):
        raise TwoBuilderError(f"{label} source identity is invalid")
    if candidate["configSha256"] != str(
        candidate["buildkitResultDigest"]
    ).removeprefix("sha256:"):
        raise TwoBuilderError(f"{label} OCI config identity differs")
    if candidate["ociManifestSha256"] != str(candidate["imageId"]).removeprefix(
        "sha256:"
    ):
        raise TwoBuilderError(f"{label} OCI manifest identity differs")
    return candidate


def _admission_document(
    value: object,
    *,
    label: str,
    expected_task_id: str,
    expected_run_prefix: str,
    expected_source_commit: str,
    expected_authority_sha256: str,
) -> dict[str, object]:
    admission = _object(value, label=label)
    _exact_keys(admission, _ADMISSION_KEYS, label=label)
    run_id = admission.get("runId")
    if (
        admission.get("contract") != SINGLE_BUILDER_CONTRACT
        or admission.get("status") != "passed"
        or admission.get("taskId") != expected_task_id
        or admission.get("sourceCommit") != expected_source_commit
        or admission.get("releaseAuthoritySha256")
        != expected_authority_sha256
        or not isinstance(run_id, str)
        or not run_id.startswith(expected_run_prefix)
    ):
        raise TwoBuilderError(f"{label} contract differs")
    builder = _builder_measurement(admission.get("builder"))
    candidate = _candidate_document(
        admission.get("candidate"), label=f"{label} candidate"
    )
    if candidate["sourceCommit"] != expected_source_commit:
        raise TwoBuilderError(f"{label} candidate source differs")
    chain = _object(admission.get("chain"), label=f"{label} chain")
    _exact_keys(chain, set(_CHAIN_FIELDS), label=f"{label} chain")
    for field in _CHAIN_FIELDS:
        _sha(chain.get(field), label=f"{label} {field}")
    replay = _object(admission.get("replayKeys"), label=f"{label} replay keys")
    _exact_keys(replay, set(_REPLAY_KEYS), label=f"{label} replay keys")
    for field in _CHAIN_FIELDS:
        if replay.get(field) != chain[field]:
            raise TwoBuilderError(f"{label} replay chain differs")
    for field in _INDEPENDENT_BUILDER_FIELDS:
        if replay.get(field) != builder[field]:
            raise TwoBuilderError(f"{label} replay identity differs")
    challenges = tuple(
        _challenge(replay.get(field), label=f"{label} {field}")
        for field in _CHALLENGE_FIELDS
    )
    if len(set(challenges)) != len(challenges):
        raise TwoBuilderError(f"{label} challenges were reused")
    prior_hashes = admission.get("priorAdmissionReceiptSha256s")
    historical_hashes = list(_EXPECTED_HISTORICAL_ADMISSION_SHA256S.values())
    if (
        not isinstance(prior_hashes, list)
        or prior_hashes[: len(historical_hashes)] != historical_hashes
        or len(set(prior_hashes)) != len(prior_hashes)
    ):
        raise TwoBuilderError(f"{label} historical admissions are incomplete")
    for prior_hash in prior_hashes:
        _sha(prior_hash, label=f"{label} prior admission")
    return {
        "admission": admission,
        "builder": builder,
        "candidate": candidate,
        "chain": chain,
        "challenges": challenges,
        "priorAdmissionReceiptSha256s": prior_hashes,
        "replay": replay,
        "runId": run_id,
    }


def _prior_admission_tokens(
    value: object, *, label: str
) -> set[str]:
    """Read v2, or historical v1, solely as a cross-commit replay denylist."""

    prior = _object(value, label=label)
    contract = prior.get("contract")
    if contract == SINGLE_BUILDER_CONTRACT:
        task_id = prior.get("taskId")
        if task_id not in {"RB-003", "RB-004"}:
            raise TwoBuilderError(f"{label} task differs")
        source_commit = prior.get("sourceCommit")
        authority = prior.get("releaseAuthoritySha256")
        if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
            raise TwoBuilderError(f"{label} source differs")
        authority = _sha(authority, label=f"{label} authority")
        validated = _admission_document(
            prior,
            label=label,
            expected_task_id=task_id,
            expected_run_prefix=(
                "rb003-builder-a-" if task_id == "RB-003" else "rb004-builder-b-"
            ),
            expected_source_commit=source_commit,
            expected_authority_sha256=authority,
        )
        replay = _object(validated["replay"], label=f"{label} replay keys")
    elif contract == LEGACY_SINGLE_BUILDER_CONTRACT:
        if prior.get("status") != "passed":
            raise TwoBuilderError(f"{label} legacy contract differs")
        replay = _object(prior.get("replayKeys"), label=f"{label} replay keys")
        required_legacy = {
            *_CHALLENGE_FIELDS,
            "daemonId",
            "stateRootIdentitySha256",
            "buildReceiptSha256",
            "candidateReceiptSha256",
            "postflightReceiptSha256",
            "preflightReceiptSha256",
        }
        if not required_legacy.issubset(replay):
            raise TwoBuilderError(f"{label} legacy replay keys differ")
    else:
        raise TwoBuilderError(f"{label} contract differs")
    tokens: set[str] = set()
    for field in _DENY_REPLAY_FIELDS.intersection(replay):
        value = replay[field]
        if field in _CHALLENGE_FIELDS:
            token = _challenge(value, label=f"{label} {field}")
        elif field.endswith("Sha256"):
            token = _sha(value, label=f"{label} {field}")
        elif not isinstance(value, str) or not value or len(value) > 256:
            raise TwoBuilderError(f"{label} {field} is invalid")
        else:
            token = value
        tokens.add(token)
    return tokens


def _ordered_prior_admission_paths(
    paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    """Require both fixed RB-003/RB-004 receipts; all remain denylist-only."""

    if len(set(paths)) != len(paths):
        raise TwoBuilderError("one prior builder admission path was reused")
    anchored: dict[str, Path] = {}
    extras: list[Path] = []
    expected_by_hash = {
        digest: task_id
        for task_id, digest in _EXPECTED_HISTORICAL_ADMISSION_SHA256S.items()
    }
    for path in paths:
        prior, raw = _json_file(path, label="prior builder admission")
        task_id = expected_by_hash.get(_sha256(raw))
        if task_id is None:
            extras.append(path)
            continue
        run_id = prior.get("runId")
        expected_prefix = (
            "rb003-builder-a-" if task_id == "RB-003" else "rb004-builder-b-"
        )
        if (
            prior.get("contract") != LEGACY_SINGLE_BUILDER_CONTRACT
            or prior.get("status") != "passed"
            or prior.get("taskId") not in {None, task_id}
            or not isinstance(run_id, str)
            or not run_id.startswith(expected_prefix)
            or task_id in anchored
        ):
            raise TwoBuilderError(f"historical {task_id} admission role differs")
        _prior_admission_tokens(prior, label=f"historical {task_id} admission")
        anchored[task_id] = path
    if set(anchored) != set(_EXPECTED_HISTORICAL_ADMISSION_SHA256S):
        raise TwoBuilderError("historical RB-003/RB-004 admissions are incomplete")
    return tuple(
        [anchored[task_id] for task_id in _EXPECTED_HISTORICAL_ADMISSION_SHA256S]
        + extras
    )


def _verify_single_builder(
    *,
    preflight_path: Path,
    build_path: Path,
    postflight_path: Path,
    archive_path: Path,
    source_archive_path: Path,
    release_authority_path: Path,
    cleanup_path: Path,
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
    prior_admission_paths: tuple[Path, ...],
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

    prior_admission_paths = _ordered_prior_admission_paths(prior_admission_paths)
    paths = (
        preflight_path,
        build_path,
        postflight_path,
        archive_path,
        source_archive_path,
        release_authority_path,
        cleanup_path,
        *prior_admission_paths,
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
        "cleanup": _json_file(cleanup_path, label=f"{builder_label} cleanup"),
    }
    hashes = {name: _sha256(raw) for name, (_value, raw) in documents.items()}
    identities = [
        _physical_identity(path, label="single-builder artifact") for path in paths
    ]
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
    cleanup = documents["cleanup"][0]
    _exact_keys(
        cleanup,
        {
            "containerdPidAbsent",
            "contract",
            "dockerPidAbsent",
            "networkNamespaceAbsent",
            "role",
            "runId",
            "runtimePathsAbsent",
            "status",
            "unitsStopped",
        },
        label=f"{builder_label} cleanup",
    )
    if (
        cleanup.get("contract") != CLEANUP_CONTRACT
        or cleanup.get("status") != "private_builder_removed"
        or cleanup.get("role") != ("a" if expected_task_id == "RB-003" else "b")
        or cleanup.get("runId") != run_id
        or any(
            cleanup.get(field) is not True
            for field in (
                "containerdPidAbsent",
                "dockerPidAbsent",
                "networkNamespaceAbsent",
                "runtimePathsAbsent",
                "unitsStopped",
            )
        )
    ):
        raise TwoBuilderError(f"{builder_label} cleanup was not proven")
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
        source_archive_path=source_archive_path,
        expected_archive_sha256=str(build["archiveSha256"]),
        expected_image_id=str(build["imageId"]),
        expected_source_archive_sha256=str(
            build["authority"]["sourceArchiveSha256"]
        ),
        expected_source_commit=expected_source_commit,
        expected_source_date_epoch=int(build["authority"]["sourceDateEpoch"]),
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
        "cleanupReceiptSha256": hashes["cleanup"],
        "postflightReceiptSha256": hashes["postflight"],
        "preflightReceiptSha256": hashes["preflight"],
        "releaseAuthorityReceiptSha256": hashes["releaseAuthority"],
    }
    replay_keys = {
        "admissionChallenge": admission_challenge,
        "archiveSha256": chain_hashes["archiveSha256"],
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
        "cleanupReceiptSha256": hashes["cleanup"],
        "postflightReceiptSha256": hashes["postflight"],
        "preflightReceiptSha256": hashes["preflight"],
        "releaseAuthorityReceiptSha256": hashes["releaseAuthority"],
    }
    prior_hashes: list[str] = []
    prior_tokens: set[str] = set()
    for path in prior_admission_paths:
        prior, raw = _json_file(path, label="prior builder admission")
        prior_hashes.append(_sha256(raw))
        tokens = _prior_admission_tokens(prior, label="prior builder admission")
        if prior_tokens.intersection(tokens):
            raise TwoBuilderError("one prior builder admission was reused")
        prior_tokens.update(tokens)
    current_tokens = {
        str(value)
        for key, value in replay_keys.items()
        if key in _DENY_REPLAY_FIELDS
    }
    if current_tokens.intersection(prior_tokens):
        raise TwoBuilderError(
            f"{builder_label} receipt, challenge, or identity was reused"
        )

    return {
        "builder": preflight_builder,
        "candidate": _candidate_summary(semantics, image_id=build["imageId"]),
        "chain": chain_hashes,
        "contract": SINGLE_BUILDER_CONTRACT,
        "priorAdmissionReceiptSha256s": prior_hashes,
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
    source_archive_path: Path,
    release_authority_path: Path,
    cleanup_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
    expected_observation_challenge: str,
    expected_build_run_challenge: str,
    admission_challenge: str,
    prior_admission_paths: tuple[Path, ...],
) -> dict[str, object]:
    """Admit one fresh, empty, offline builder-A chain exactly once."""

    return _verify_single_builder(
        preflight_path=preflight_path,
        build_path=build_path,
        postflight_path=postflight_path,
        archive_path=archive_path,
        source_archive_path=source_archive_path,
        release_authority_path=release_authority_path,
        cleanup_path=cleanup_path,
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
    validated = _admission_document(
        admission,
        label="builder-A prior admission",
        expected_task_id="RB-003",
        expected_run_prefix="rb003-builder-a-",
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
    )
    chain = _object(validated["chain"], label="builder-A prior chain")
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
        or preflight.get("runId") != validated["runId"]
    ):
        raise TwoBuilderError("builder-A prior preflight contract differs")
    identity = _builder_measurement(preflight.get("builderIdentity"))
    if validated["builder"] != identity:
        raise TwoBuilderError("builder-A admission identity differs from preflight")
    return identity


def verify_single_builder_b(
    *,
    preflight_path: Path,
    build_path: Path,
    postflight_path: Path,
    archive_path: Path,
    source_archive_path: Path,
    release_authority_path: Path,
    cleanup_path: Path,
    builder_a_admission_path: Path,
    builder_a_preflight_path: Path,
    expected_source_commit: str,
    expected_authority_sha256: str,
    expected_observation_challenge: str,
    expected_build_run_challenge: str,
    admission_challenge: str,
    prior_admission_paths: tuple[Path, ...],
) -> dict[str, object]:
    """Admit builder B only when its full chain is independent of admitted A."""

    if not prior_admission_paths:
        raise TwoBuilderError("builder-B requires prior admission denylists")

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
        source_archive_path=source_archive_path,
        release_authority_path=release_authority_path,
        cleanup_path=cleanup_path,
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
        prior_admission_paths=(builder_a_admission_path, *prior_admission_paths),
    )


def _admitted_builder_evidence(
    *,
    path: Path,
    cleanup_path: Path,
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
    validated = _admission_document(
        admission,
        label=f"builder-{role} admission",
        expected_task_id=expected_task,
        expected_run_prefix=expected_prefix,
        expected_source_commit=expected_source_commit,
        expected_authority_sha256=expected_authority_sha256,
    )
    chain = _object(validated["chain"], label=f"builder-{role} admission chain")
    cleanup, cleanup_raw = _json_file(
        cleanup_path, label=f"builder-{role} cleanup receipt"
    )
    _exact_keys(
        cleanup,
        {
            "containerdPidAbsent",
            "contract",
            "dockerPidAbsent",
            "networkNamespaceAbsent",
            "role",
            "runId",
            "runtimePathsAbsent",
            "status",
            "unitsStopped",
        },
        label=f"builder-{role} cleanup receipt",
    )
    if (
        chain.get("archiveSha256") != build["archiveSha256"]
        or chain.get("buildReceiptSha256") != build_receipt_sha256
        or chain.get("candidateReceiptSha256") != candidate_receipt_sha256
        or chain.get("cleanupReceiptSha256") != _sha256(cleanup_raw)
        or cleanup.get("contract") != CLEANUP_CONTRACT
        or cleanup.get("status") != "private_builder_removed"
        or cleanup.get("role") != role.lower()
        or cleanup.get("runId") != validated["runId"]
        or any(
            cleanup.get(field) is not True
            for field in (
                "containerdPidAbsent",
                "dockerPidAbsent",
                "networkNamespaceAbsent",
                "runtimePathsAbsent",
                "unitsStopped",
            )
        )
    ):
        raise TwoBuilderError(f"builder-{role} admission chain differs from candidate")

    candidate = _object(validated["candidate"], label="admitted candidate")
    expected_candidate = _candidate_summary(semantics, image_id=build["imageId"])
    if candidate != expected_candidate:
        raise TwoBuilderError(
            f"builder-{role} admitted candidate differs from archive semantics"
        )

    build_builder = _object(build["builder"], label=f"builder-{role} build binding")
    identity = _object(
        build_builder.get("identity"), label=f"builder-{role} build identity"
    )
    if validated["builder"] != identity:
        raise TwoBuilderError(f"builder-{role} admission builder differs from build")
    replay = _object(validated["replay"], label="admission replay keys")
    if replay.get("buildRunChallenge") != build_builder["runChallenge"]:
        raise TwoBuilderError(f"builder-{role} admission build challenge differs")
    return {
        "chain": chain,
        "challenges": validated["challenges"],
        "priorAdmissionReceiptSha256s": validated[
            "priorAdmissionReceiptSha256s"
        ],
        "receiptSha256": expected_receipt_sha256,
        "runId": validated["runId"],
        "taskId": expected_task,
    }


def verify_two_builds(
    *,
    admission_paths: tuple[Path, Path],
    build_paths: tuple[Path, Path],
    archive_paths: tuple[Path, Path],
    source_archive_paths: tuple[Path, Path],
    cleanup_paths: tuple[Path, Path],
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
            source_archive_paths,
            cleanup_paths,
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
        *source_archive_paths,
        *cleanup_paths,
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
    source_archive_file_identities = evidence_file_identities[6:8]
    cleanup_file_identities = evidence_file_identities[8:10]
    candidate_receipt_file_identities = evidence_file_identities[10:12]
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
            source_archive_path=source_archive_path,
            expected_archive_sha256=str(build["archiveSha256"]),
            expected_image_id=str(build["imageId"]),
            expected_source_archive_sha256=str(
                build["authority"]["sourceArchiveSha256"]
            ),
            expected_source_commit=expected_source_commit,
            expected_source_date_epoch=int(build["authority"]["sourceDateEpoch"]),
        )
        for path, source_archive_path, build in zip(
            archive_paths, source_archive_paths, builds, strict=True
        )
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
        "runtimeSourceTreeSha256": "candidate runtime source trees differ",
    }
    for key, message in semantic_comparisons.items():
        if semantics[0][key] != semantics[1][key]:
            raise TwoBuilderError(message)
    if builds[0]["imageId"] != builds[1]["imageId"]:
        raise TwoBuilderError("candidate image IDs differ")

    admissions = [
        _admitted_builder_evidence(
            path=admission_path,
            cleanup_path=cleanup_path,
            role=role,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_source_commit=expected_source_commit,
            expected_authority_sha256=expected_authority_sha256,
            build=build,
            build_receipt_sha256=build_sha256,
            candidate_receipt_sha256=candidate_sha256,
            semantics=image_semantics,
        )
        for admission_path, cleanup_path, role, expected_receipt_sha256, build, build_sha256, candidate_sha256, image_semantics in zip(
            admission_paths,
            cleanup_paths,
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
    if admissions[0]["receiptSha256"] not in admissions[1][
        "priorAdmissionReceiptSha256s"
    ]:
        raise TwoBuilderError("builder-B admission does not deny builder-A replay")
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
        "cleanupReceiptSha256",
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
                "sourceArchiveFileIdentitySha256": source_archive_file_identities[index],
                "candidateArchiveSha256": build["archiveSha256"],
                "candidateReceiptFileIdentitySha256": (
                    candidate_receipt_file_identities[index]
                ),
                "candidateReceiptSha256": candidate_receipt_sha256[index],
                "cleanupReceiptFileIdentitySha256": cleanup_file_identities[index],
                "cleanupReceiptSha256": admission["chain"]["cleanupReceiptSha256"],
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


def _run_operator_command(
    command: list[str],
    cwd: Path | None,
    environment: dict[str, str],
    allowed: frozenset[int],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=3600,
    )


def _checked_operator_command(
    runner: OperatorRunner,
    command: list[str],
    *,
    cwd: Path | None,
    environment: dict[str, str],
    allowed: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str]:
    completed = runner(command, cwd, environment, allowed)
    if completed.returncode not in allowed:
        raise TwoBuilderError(
            f"private builder command failed: {Path(command[0]).name}"
        )
    return completed


def _explicit_executable(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise TwoBuilderError(f"{label} executable path must be absolute")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise TwoBuilderError(f"{label} executable is inaccessible") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        raise TwoBuilderError(f"{label} executable is unsafe")
    return resolved


def _private_directory(path: Path, *, label: str, under_tmp: bool) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise TwoBuilderError(f"{label} must be an absolute directory")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise TwoBuilderError(f"{label} is inaccessible") from error
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or (under_tmp and Path("/tmp") not in path.parents)
    ):
        raise TwoBuilderError(f"{label} must be private, owned, and link-free")
    return path


def _require_within(path: Path, root: Path, *, label: str) -> None:
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise TwoBuilderError(f"{label} must stay inside private runtime root") from error


def _runtime_identity(path: Path, *, socket: bool, label: str) -> str:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise TwoBuilderError(f"{label} is inaccessible") from error
    expected = stat.S_ISSOCK if socket else stat.S_ISDIR
    if (
        resolved != path
        or not expected(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise TwoBuilderError(
            f"{label} must be private, owned, link-free, and have the right type"
        )
    return _sha256(
        json.dumps(
            {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": stat.S_IMODE(metadata.st_mode),
                "path": str(path),
                "uid": metadata.st_uid,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )


def _copy_private_buildx_executable(source: Path, target: Path) -> str:
    input_descriptor = -1
    output_descriptor = -1
    created = False
    copied_ok = False
    try:
        before = source.lstat()
        if (
            source.resolve(strict=True) != source
            or not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o022
            or not 0 < before.st_size <= _MAX_BUILDX_EXECUTABLE_BYTES
            or target.parent.resolve(strict=True) != target.parent
        ):
            raise TwoBuilderError("Buildx executable source is unsafe")
        parent = target.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise TwoBuilderError("private Docker plugin directory is unsafe")
        input_descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(input_descriptor)
        output_descriptor = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        created = True
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(input_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise TwoBuilderError("Buildx executable changed while copied")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                if written <= 0:
                    raise TwoBuilderError("Buildx executable copy did not progress")
                view = view[written:]
            remaining -= len(chunk)
        if os.read(input_descriptor, 1):
            raise TwoBuilderError("Buildx executable changed while copied")
        os.fchmod(output_descriptor, 0o500)
        os.fsync(output_descriptor)
        after = os.fstat(input_descriptor)
        copied = os.fstat(output_descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(opened, field)
            or getattr(opened, field) != getattr(after, field)
            for field in stable_fields
        ) or (
            not stat.S_ISREG(copied.st_mode)
            or copied.st_uid != os.geteuid()
            or copied.st_nlink != 1
            or stat.S_IMODE(copied.st_mode) != 0o500
            or copied.st_size != opened.st_size
        ):
            raise TwoBuilderError("Buildx executable copy differs")
        directory = os.open(
            target.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        copied_ok = True
        return digest.hexdigest()
    except TwoBuilderError:
        raise
    except OSError as error:
        raise TwoBuilderError("Buildx executable could not be copied") from error
    finally:
        if output_descriptor >= 0:
            os.close(output_descriptor)
        if input_descriptor >= 0:
            os.close(input_descriptor)
        if created and not copied_ok:
            try:
                target.unlink()
            except OSError:
                pass


def _operator_environment(
    runtime_root: Path, *, docker_socket: Path, executables: dict[str, Path]
) -> dict[str, str]:
    directories: dict[str, Path] = {}
    for variable, name in (
        ("DOCKER_CONFIG", "docker-client-config"),
        ("BUILDX_CONFIG", "buildx-client-config"),
        ("HOME", "home"),
        ("TMPDIR", "tmp"),
    ):
        directory = runtime_root / name
        directory.mkdir(mode=0o700)
        if any(directory.iterdir()):
            raise TwoBuilderError(f"private builder {name} must start empty")
        directories[variable] = directory
    binary_directory = runtime_root / "operator-bin"
    binary_directory.mkdir(mode=0o700)
    for name, executable in executables.items():
        if name == "buildx":
            continue
        (binary_directory / name).symlink_to(executable)
    plugin_directory = directories["DOCKER_CONFIG"] / "cli-plugins"
    plugin_directory.mkdir(mode=0o700)
    buildx_sha256 = _copy_private_buildx_executable(
        executables["buildx"], plugin_directory / "docker-buildx"
    )
    return {
        "BUILDX_CONFIG": str(directories["BUILDX_CONFIG"]),
        "DOCKER_CONFIG": str(directories["DOCKER_CONFIG"]),
        "DOCKER_HOST": f"unix://{docker_socket}",
        "EVA_RR002_BUILDX_SHA256": buildx_sha256,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(directories["HOME"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": str(binary_directory),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TEMP": str(directories["TMPDIR"]),
        "TMP": str(directories["TMPDIR"]),
        "TMPDIR": str(directories["TMPDIR"]),
    }


def _json_output(completed: subprocess.CompletedProcess[str], *, label: str) -> Any:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise TwoBuilderError(f"{label} output is invalid JSON") from error


def _select_private_buildkit_builder(output: str) -> dict[str, Any]:
    """Return one semantic default builder despite duplicate Buildx rows."""

    try:
        builders = [json.loads(line) for line in output.splitlines() if line]
    except json.JSONDecodeError as error:
        raise TwoBuilderError("BuildKit builder output is invalid") from error
    selected: dict[str, dict[str, Any]] = {}
    for row in builders:
        if not isinstance(row, dict) or row.get("Name") != "default" or row.get("Err"):
            continue
        identity = json.dumps(row, separators=(",", ":"), sort_keys=True)
        selected[identity] = row
    if len(selected) != 1:
        raise TwoBuilderError("private BuildKit builder differs")
    builder = next(iter(selected.values()))
    if builder.get("Driver") != "docker":
        raise TwoBuilderError("private BuildKit builder differs")
    return builder


def _unit_binding(
    runner: OperatorRunner,
    *,
    systemctl: Path,
    unit: str,
    namespace_path: str,
    config_path: Path,
    config_option: str,
    environment: dict[str, str],
) -> dict[str, str]:
    if _UNIT_NAME.fullmatch(unit) is None:
        raise TwoBuilderError("private builder unit name is invalid")
    completed = _checked_operator_command(
        runner,
        [
            str(systemctl),
            "show",
            unit,
            (
                "--property=MainPID,Delegate,NetworkNamespacePath,ActiveState,"
                "SubState,ExecStart"
            ),
        ],
        cwd=None,
        environment=environment,
    )
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    if (
        values.get("ActiveState") != "active"
        or values.get("SubState") != "running"
        or values.get("Delegate") != "yes"
        or values.get("NetworkNamespacePath") != namespace_path
        or f"{config_option} {config_path}" not in values.get("ExecStart", "")
        or not values.get("MainPID", "").isdigit()
        or values["MainPID"] == "0"
    ):
        raise TwoBuilderError("private builder unit is not bound and running")
    return values


def _network_observation(
    runner: OperatorRunner,
    *,
    ip: Path,
    sudo: Path,
    namespace: str,
    environment: dict[str, str],
) -> dict[str, Any]:
    prefix = [
        str(sudo),
        "-n",
        str(ip),
        "netns",
        "exec",
        namespace,
        str(ip),
        "-json",
    ]
    routes = _json_output(
        _checked_operator_command(
            runner,
            [*prefix, "route", "show"],
            cwd=None,
            environment=environment,
        ),
        label="network routes",
    )
    links = _json_output(
        _checked_operator_command(
            runner,
            [*prefix, "link", "show"],
            cwd=None,
            environment=environment,
        ),
        label="network links",
    )
    observation = {"links": links, "routes": routes}
    _offline_namespace(observation, label="private builder network")
    return observation


def _docker_command(docker: Path, socket: Path, *arguments: str) -> list[str]:
    return [str(docker), "--host", f"unix://{socket}", *arguments]


def _measure_private_builder(
    runner: OperatorRunner,
    *,
    docker: Path,
    ctr: Path,
    ip: Path,
    sudo: Path,
    docker_socket: Path,
    containerd_socket: Path,
    runtime_root: Path,
    network_namespace: str,
    containers_namespace: str,
    plugins_namespace: str,
    environment: dict[str, str],
) -> dict[str, object]:
    for path, label in (
        (docker_socket, "Docker socket"),
        (containerd_socket, "containerd socket"),
    ):
        _require_within(path, runtime_root, label=label)
        _runtime_identity(path, socket=True, label=f"private {label}")
    info = _object(
        _json_output(
            _checked_operator_command(
                runner,
                _docker_command(docker, docker_socket, "info", "--format", "{{json .}}"),
                cwd=None,
                environment=environment,
            ),
            label="Docker info",
        ),
        label="Docker info",
    )
    version = _object(
        _json_output(
            _checked_operator_command(
                runner,
                _docker_command(
                    docker,
                    docker_socket,
                    "version",
                    "--format",
                    "{{json .Server}}",
                ),
                cwd=None,
                environment=environment,
            ),
            label="Docker version",
        ),
        label="Docker version",
    )
    buildx_version_result = _checked_operator_command(
        runner,
        _docker_command(docker, docker_socket, "buildx", "version"),
        cwd=None,
        environment=environment,
    )
    buildx_version = buildx_version_result.stdout.strip()
    if (
        not buildx_version
        or len(buildx_version) > 512
        or "\n" in buildx_version
        or "\r" in buildx_version
        or any(ord(character) < 32 for character in buildx_version)
    ):
        raise TwoBuilderError("private Buildx version is invalid")
    buildx_path = Path(environment["DOCKER_CONFIG"]) / "cli-plugins/docker-buildx"
    buildx_sha256 = _sha256(
        _safe_file(
            buildx_path,
            label="private Buildx executable",
            maximum=_MAX_BUILDX_EXECUTABLE_BYTES,
        )
    )
    if buildx_sha256 != environment.get("EVA_RR002_BUILDX_SHA256"):
        raise TwoBuilderError("private Buildx executable differs")
    buildx = _checked_operator_command(
        runner,
        _docker_command(
            docker, docker_socket, "buildx", "ls", "--format", "{{json .}}"
        ),
        cwd=None,
        environment=environment,
    )
    selected = _select_private_buildkit_builder(buildx.stdout)
    nodes = selected.get("Nodes")
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
        raise TwoBuilderError("private BuildKit node differs")
    node = nodes[0]
    node_ids = node.get("IDs")
    containerd = _object(info.get("Containerd"), label="Docker containerd info")
    namespaces = _object(
        containerd.get("Namespaces"), label="Docker containerd namespaces"
    )
    docker_root = Path(str(info.get("DockerRootDir", "")))
    _require_within(docker_root, runtime_root, label="Docker state root")
    if (
        info.get("OSType") != "linux"
        or info.get("Architecture") not in {"amd64", "x86_64"}
        or info.get("HttpProxy") not in {None, ""}
        or info.get("HttpsProxy") not in {None, ""}
        or containerd.get("Address") != str(containerd_socket)
        or namespaces.get("Containers") != containers_namespace
        or namespaces.get("Plugins") != plugins_namespace
        or ["driver-type", "io.containerd.snapshotter.v1"]
        not in info.get("DriverStatus", [])
        or version.get("Os") != "linux"
        or version.get("Arch") != "amd64"
        or node.get("Endpoint") != "default"
        or node.get("Status") != "running"
        or not isinstance(node_ids, list)
        or len(node_ids) != 1
        or not isinstance(node_ids[0], str)
        or not node_ids[0]
    ):
        raise TwoBuilderError("private builder identity differs")
    identity = {
        "backend": "docker-buildx/docker",
        "buildkitNodeId": node_ids[0],
        "buildkitVersion": node.get("Version"),
        "buildxExecutableSha256": buildx_sha256,
        "buildxVersion": buildx_version,
        "containerdContainersNamespace": containers_namespace,
        "containerdImageStore": "io.containerd.snapshotter.v1",
        "containerdPluginsNamespace": plugins_namespace,
        "containerdSocketIdentitySha256": _runtime_identity(
            containerd_socket, socket=True, label="private containerd socket"
        ),
        "daemonId": info.get("ID"),
        "dockerExecutableSha256": _sha256(docker.read_bytes()),
        "dockerServerVersion": version.get("Version"),
        "socketIdentitySha256": _runtime_identity(
            docker_socket, socket=True, label="private Docker socket"
        ),
        "stateRootIdentitySha256": _runtime_identity(
            docker_root, socket=False, label="private Docker state root"
        ),
    }
    return {
        "dockerInfo": info,
        "identity": _builder_measurement(identity),
        "network": _network_observation(
            runner,
            ip=ip,
            sudo=sudo,
            namespace=network_namespace,
            environment=environment,
        ),
    }


def _ctr_command(
    ctr: Path, socket: Path, namespace: str, *arguments: str
) -> list[str]:
    return [
        str(ctr),
        "--address",
        str(socket),
        "--namespace",
        namespace,
        *arguments,
    ]


def _nonempty_lines(value: str) -> list[str]:
    return [line for line in value.splitlines() if line]


def _pid_file(path: Path, *, label: str) -> int:
    raw = _safe_file(path, label=label, maximum=32)
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise TwoBuilderError(f"{label} is invalid") from error
    if value <= 1:
        raise TwoBuilderError(f"{label} is invalid")
    return value


def _source_checkout(
    runner: OperatorRunner,
    *,
    git: Path,
    source_root: Path,
    expected_commit: str,
    environment: dict[str, str],
) -> int:
    if _COMMIT.fullmatch(expected_commit) is None:
        raise TwoBuilderError("expected source commit is invalid")
    top = _checked_operator_command(
        runner,
        [str(git), "-C", str(source_root), "rev-parse", "--show-toplevel"],
        cwd=None,
        environment=environment,
    ).stdout.strip()
    commit = _checked_operator_command(
        runner,
        [str(git), "-C", str(source_root), "rev-parse", "HEAD"],
        cwd=None,
        environment=environment,
    ).stdout.strip()
    epoch = _checked_operator_command(
        runner,
        [str(git), "-C", str(source_root), "show", "-s", "--format=%ct", "HEAD"],
        cwd=None,
        environment=environment,
    ).stdout.strip()
    detached = _checked_operator_command(
        runner,
        [str(git), "-C", str(source_root), "symbolic-ref", "-q", "HEAD"],
        cwd=None,
        environment=environment,
        allowed=frozenset({1}),
    )
    dirty = _checked_operator_command(
        runner,
        [
            str(git),
            "-C",
            str(source_root),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        cwd=None,
        environment=environment,
    ).stdout
    if (
        top != str(source_root)
        or commit != expected_commit
        or detached.stdout
        or dirty
        or not epoch.isdigit()
        or int(epoch) <= 0
    ):
        raise TwoBuilderError(
            "private builder source must be one clean detached exact-commit clone"
        )
    build_module = source_root / "tools" / "build_managed_b_offline_candidate.py"
    try:
        metadata = build_module.lstat()
    except OSError as error:
        raise TwoBuilderError("candidate-local build operator is absent") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise TwoBuilderError("candidate-local build operator is unsafe")
    return int(epoch)


def _receipt_docker_info(
    measurement: dict[str, object],
    *,
    docker_unit: dict[str, str],
    containerd_unit: dict[str, str],
    docker_config_path: Path,
    containerd_config_path: Path,
    docker_config_sha256: str,
    containerd_config_sha256: str,
) -> dict[str, object]:
    info = _object(measurement["dockerInfo"], label="private Docker info")
    containerd = _object(info.get("Containerd"), label="private containerd info")
    namespaces = _object(
        containerd.get("Namespaces"), label="private containerd namespaces"
    )
    return {
        "containerCount": info.get("Containers"),
        "containerd": {
            "Address": containerd.get("Address"),
            "Namespaces": {
                "Containers": namespaces.get("Containers"),
                "Plugins": namespaces.get("Plugins"),
            },
        },
        "containerdConfigPath": str(containerd_config_path),
        "containerdConfigSha256": containerd_config_sha256,
        "containerdUnit": containerd_unit,
        "daemonId": info.get("ID"),
        "dockerConfigPath": str(docker_config_path),
        "dockerConfigSha256": docker_config_sha256,
        "dockerRootDir": info.get("DockerRootDir"),
        "dockerUnit": docker_unit,
        "driverStatus": info.get("DriverStatus"),
        "httpProxy": info.get("HttpProxy") or "",
        "httpsProxy": info.get("HttpsProxy") or "",
        "imageCount": info.get("Images"),
    }


def _builder_inventory(
    runner: OperatorRunner,
    *,
    docker: Path,
    ctr: Path,
    docker_socket: Path,
    containerd_socket: Path,
    containers_namespace: str,
    environment: dict[str, str],
) -> tuple[list[str], list[str]]:
    docker_images = _nonempty_lines(
        _checked_operator_command(
            runner,
            _docker_command(
                docker,
                docker_socket,
                "image",
                "ls",
                "--all",
                "--no-trunc",
                "--format",
                "{{.ID}} {{.Repository}}:{{.Tag}}",
            ),
            cwd=None,
            environment=environment,
        ).stdout
    )
    containerd_images = _nonempty_lines(
        _checked_operator_command(
            runner,
            _ctr_command(
                ctr,
                containerd_socket,
                containers_namespace,
                "images",
                "list",
                "-q",
            ),
            cwd=None,
            environment=environment,
        ).stdout
    )
    return docker_images, containerd_images


def _base_references(build: dict[str, Any]) -> tuple[str, str, str]:
    candidate = _object(build.get("candidate"), label="build candidate")
    receipt = _object(candidate.get("receipt"), label="candidate receipt")
    stages = _object(build.get("stages"), label="build stages")
    references: list[str] = []
    for receipt_key, stage_key in (
        ("build_image_reference", "build_image"),
        ("runtime_libraries_image_reference", "runtime_libraries_image"),
        ("runtime_image_reference", "runtime_image"),
    ):
        reference = receipt.get(receipt_key)
        stage = _object(stages.get(stage_key), label=f"{stage_key} stage")
        if (
            not isinstance(reference, str)
            or "@" not in reference
            or reference.rsplit("@", 1)[-1] != stage.get("digest")
        ):
            raise TwoBuilderError("candidate base-image reference differs")
        references.append(reference)
    return references[0], references[1], references[2]


def _stopped_unit(
    runner: OperatorRunner,
    *,
    systemctl: Path,
    unit: str,
    environment: dict[str, str],
) -> bool:
    completed = _checked_operator_command(
        runner,
        [
            str(systemctl),
            "show",
            unit,
            "--property=MainPID,ActiveState,SubState,LoadState",
        ],
        cwd=None,
        environment=environment,
        allowed=frozenset({0, 4}),
    )
    if completed.returncode == 4 and not completed.stdout.strip():
        return True
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    return (
        values.get("MainPID") == "0"
        and values.get("ActiveState") == "inactive"
        and values.get("SubState") == "dead"
        and values.get("LoadState") in {"loaded", "not-found"}
    )


def _process_absent(pid: int) -> bool:
    if pid <= 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _cleanup_private_builder(
    runner: OperatorRunner,
    *,
    role: str,
    run_id: str,
    runtime_root: Path,
    docker_socket: Path,
    containerd_socket: Path,
    docker_pid: int,
    containerd_pid: int,
    network_namespace: str,
    docker_unit: str,
    containerd_unit: str,
    candidate_image_id: str | None,
    docker: Path,
    ip: Path,
    systemctl: Path,
    sudo: Path,
    rm: Path,
    environment: dict[str, str],
    cleanup_receipt_path: Path,
) -> dict[str, object]:
    if runtime_root.parent != Path("/tmp") or not runtime_root.name.startswith(
        "eva2-rr002-"
    ):
        raise TwoBuilderError("private builder cleanup target is unsafe")
    if candidate_image_id is not None:
        _checked_operator_command(
            runner,
            _docker_command(
                docker, docker_socket, "image", "rm", candidate_image_id
            ),
            cwd=None,
            environment=environment,
            allowed=frozenset({0, 1}),
        )
    units_stopped = True
    for unit in (docker_unit, containerd_unit):
        _checked_operator_command(
            runner,
            [str(sudo), "-n", str(systemctl), "stop", unit],
            cwd=None,
            environment=environment,
        )
        units_stopped = units_stopped and _stopped_unit(
            runner,
            systemctl=systemctl,
            unit=unit,
            environment=environment,
        )
    _checked_operator_command(
        runner,
        [str(sudo), "-n", str(ip), "netns", "delete", network_namespace],
        cwd=None,
        environment=environment,
        allowed=frozenset({0, 1}),
    )
    netns = _checked_operator_command(
        runner,
        [str(ip), "netns", "list"],
        cwd=None,
        environment=environment,
    ).stdout
    netns_absent = all(
        line.split(maxsplit=1)[0] != network_namespace
        for line in netns.splitlines()
        if line.strip()
    )
    _checked_operator_command(
        runner,
        [
            str(sudo),
            "-n",
            str(rm),
            "-rf",
            "--one-file-system",
            "--",
            str(runtime_root),
        ],
        cwd=None,
        environment=environment,
    )
    # Both admitted sockets must live below runtime_root.  On a rejected
    # outside-socket claim, cleanup deliberately ignores that external path.
    paths_absent = not (runtime_root.exists() or runtime_root.is_symlink())
    pids_absent = _process_absent(docker_pid) and _process_absent(containerd_pid)
    if not (units_stopped and netns_absent and paths_absent and pids_absent):
        raise TwoBuilderError("private builder cleanup could not be proven")
    cleanup = {
        "containerdPidAbsent": True,
        "contract": CLEANUP_CONTRACT,
        "dockerPidAbsent": True,
        "networkNamespaceAbsent": True,
        "role": role,
        "runId": run_id,
        "runtimePathsAbsent": True,
        "status": "private_builder_removed",
        "unitsStopped": True,
    }
    write_new_private_json(
        cleanup_receipt_path, cleanup, label="private builder cleanup receipt"
    )
    return cleanup


def _run_private_builder_operator(
    arguments: argparse.Namespace,
    *,
    runner: OperatorRunner = _run_operator_command,
) -> dict[str, object]:
    """Measure, build, admit, and remove one externally provisioned builder."""

    role = arguments.role
    if role not in {"a", "b"}:
        raise TwoBuilderError("private builder role is invalid")
    task_id = "RB-003" if role == "a" else "RB-004"
    preflight_contract = (
        BUILDER_A_PREFLIGHT_CONTRACT
        if role == "a"
        else BUILDER_B_PREFLIGHT_CONTRACT
    )
    postflight_contract = (
        BUILDER_A_POSTFLIGHT_CONTRACT
        if role == "a"
        else BUILDER_B_POSTFLIGHT_CONTRACT
    )
    executables = {
        name: _explicit_executable(getattr(arguments, name), label=name)
        for name in (
            "python",
            "git",
            "docker",
            "buildx",
            "ctr",
            "ip",
            "systemctl",
            "sudo",
            "rm",
        )
    }
    runtime_root = _private_directory(
        arguments.runtime_root, label="private runtime root", under_tmp=True
    )
    if runtime_root.parent != Path("/tmp") or not runtime_root.name.startswith(
        "eva2-rr002-"
    ):
        raise TwoBuilderError("private runtime root name is unsafe")
    source_root = _private_directory(
        arguments.source_root, label="private detached source", under_tmp=True
    )
    _private_directory(
        source_root.parent, label="private detached clone", under_tmp=True
    )
    _require_within(source_root, runtime_root, label="private detached source")
    if _NAMESPACE_NAME.fullmatch(arguments.network_namespace) is None:
        raise TwoBuilderError("private network namespace is invalid")
    if not arguments.network_namespace.startswith("eva2-rr002-"):
        raise TwoBuilderError("private network namespace is not RR-002 scoped")
    namespace_path = f"/run/netns/{arguments.network_namespace}"
    if arguments.containers_namespace == arguments.plugins_namespace:
        raise TwoBuilderError("containerd namespaces must differ")
    for namespace in (
        arguments.containers_namespace,
        arguments.plugins_namespace,
    ):
        if _NAMESPACE_NAME.fullmatch(namespace) is None:
            raise TwoBuilderError("containerd namespace is invalid")
    if (
        arguments.docker_unit == arguments.containerd_unit
        or not arguments.docker_unit.startswith("eva2-rr002-")
        or not arguments.containerd_unit.startswith("eva2-rr002-")
    ):
        raise TwoBuilderError("private builder units are not RR-002 scoped")
    for socket_path, label in (
        (arguments.docker_socket, "Docker socket"),
        (arguments.containerd_socket, "containerd socket"),
    ):
        _require_within(socket_path, runtime_root, label=label)
        _runtime_identity(socket_path, socket=True, label=f"private {label}")
    environment = _operator_environment(
        runtime_root,
        docker_socket=arguments.docker_socket,
        executables=executables,
    )
    if "DOCKER_CONTEXT" in environment or environment["DOCKER_HOST"] in {
        "unix:///run/docker.sock",
        "unix:///var/run/docker.sock",
    }:
        raise TwoBuilderError("system Docker context is forbidden")
    source_epoch = _source_checkout(
        runner,
        git=executables["git"],
        source_root=source_root,
        expected_commit=arguments.expected_source_commit,
        environment=environment,
    )
    authority_document, authority_raw = _json_file(
        arguments.release_authority, label="release authority receipt"
    )
    _release_authority(
        authority_document,
        expected_source_commit=arguments.expected_source_commit,
        expected_authority_sha256=arguments.expected_authority_sha256,
    )
    config_hashes: dict[str, str] = {}
    for name, path in (
        ("docker", arguments.docker_config),
        ("containerd", arguments.containerd_config),
    ):
        _require_within(path, runtime_root, label=f"{name} daemon config")
        config_hashes[name] = _sha256(
            _safe_file(path, label=f"{name} daemon config", maximum=1024 * 1024)
        )
    docker_pid = _pid_file(arguments.docker_pid_file, label="Docker PID file")
    containerd_pid = _pid_file(
        arguments.containerd_pid_file, label="containerd PID file"
    )
    for path, label in (
        (arguments.docker_pid_file, "Docker PID file"),
        (arguments.containerd_pid_file, "containerd PID file"),
    ):
        _require_within(path, runtime_root, label=label)
    docker_unit = _unit_binding(
        runner,
        systemctl=executables["systemctl"],
        unit=arguments.docker_unit,
        namespace_path=namespace_path,
        config_path=arguments.docker_config,
        config_option="--config-file",
        environment=environment,
    )
    containerd_unit = _unit_binding(
        runner,
        systemctl=executables["systemctl"],
        unit=arguments.containerd_unit,
        namespace_path=namespace_path,
        config_path=arguments.containerd_config,
        config_option="--config",
        environment=environment,
    )
    if int(docker_unit["MainPID"]) != docker_pid or int(
        containerd_unit["MainPID"]
    ) != containerd_pid:
        raise TwoBuilderError("private builder PID binding differs")
    output_parent = _private_directory(
        arguments.output_parent, label="candidate output parent", under_tmp=False
    )
    expected_output = (
        _RR002_ARTIFACT_BASE
        / f"rr002-builder-{role}"
    )
    if output_parent != expected_output:
        raise TwoBuilderError("candidate output parent is not the fixed RR-002 root")
    if arguments.release_authority.parent != output_parent:
        raise TwoBuilderError("release authority is outside its fixed RR-002 root")
    allowed_existing = {arguments.release_authority}
    if set(output_parent.iterdir()) != allowed_existing:
        raise TwoBuilderError("candidate output parent contains unspecified state")
    source_archive_path = output_parent / "source-archive.tar"
    receipt_paths = (
        arguments.preflight_receipt,
        arguments.build_receipt,
        arguments.postflight_receipt,
        arguments.admission_receipt,
        arguments.cleanup_receipt,
        source_archive_path,
    )
    if len(set(receipt_paths)) != len(receipt_paths):
        raise TwoBuilderError("private builder receipt paths must differ")
    for path in receipt_paths:
        if path.parent != output_parent:
            raise TwoBuilderError("private builder receipt is outside its role root")
        if not path.is_absolute() or path.parent.resolve(strict=True) == runtime_root:
            raise TwoBuilderError("private builder receipt path is unsafe")
        try:
            path.parent.resolve(strict=True).relative_to(runtime_root)
        except ValueError:
            pass
        else:
            raise TwoBuilderError("private builder receipts cannot be temporary")

    observation_challenge = secrets.token_hex(32)
    admission_challenge = secrets.token_hex(32)
    run_id = (
        f"rb00{3 if role == 'a' else 4}-builder-{role}-"
        f"{observation_challenge[:16]}"
    )
    candidate_image_id: str | None = None
    verification_inputs: dict[str, object] | None = None
    build_error: BaseException | None = None
    try:
        before = _measure_private_builder(
            runner,
            docker=executables["docker"],
            ctr=executables["ctr"],
            ip=executables["ip"],
            sudo=executables["sudo"],
            docker_socket=arguments.docker_socket,
            containerd_socket=arguments.containerd_socket,
            runtime_root=runtime_root,
            network_namespace=arguments.network_namespace,
            containers_namespace=arguments.containers_namespace,
            plugins_namespace=arguments.plugins_namespace,
            environment=environment,
        )
        docker_images, containerd_images = _builder_inventory(
            runner,
            docker=executables["docker"],
            ctr=executables["ctr"],
            docker_socket=arguments.docker_socket,
            containerd_socket=arguments.containerd_socket,
            containers_namespace=arguments.containers_namespace,
            environment=environment,
        )
        disk_usage = _checked_operator_command(
            runner,
            _docker_command(
                executables["docker"],
                arguments.docker_socket,
                "buildx",
                "du",
                "--builder",
                "default",
            ),
            cwd=None,
            environment=environment,
        ).stdout
        before_info = _receipt_docker_info(
            before,
            docker_unit=docker_unit,
            containerd_unit=containerd_unit,
            docker_config_path=arguments.docker_config,
            containerd_config_path=arguments.containerd_config,
            docker_config_sha256=config_hashes["docker"],
            containerd_config_sha256=config_hashes["containerd"],
        )
        if (
            docker_images
            or containerd_images
            or disk_usage != "Reclaimable:\t0B\nTotal:\t\t0B\n"
            or before_info["imageCount"] != 0
            or before_info["containerCount"] != 0
        ):
            raise TwoBuilderError("private builder did not start empty")
        preflight = {
            "admissionChallenge": admission_challenge,
            "builderIdentity": before["identity"],
            "buildkitDiskUsageRaw": disk_usage,
            "containerdImages": containerd_images,
            "contract": preflight_contract,
            "dockerImages": docker_images,
            "dockerInfo": {**before_info, "sourceDateEpoch": source_epoch},
            "networkNamespace": before["network"],
            "observationChallenge": observation_challenge,
            "releaseAuthoritySha256": arguments.expected_authority_sha256,
            "runId": run_id,
            "sourceCommit": arguments.expected_source_commit,
            "status": "empty_offline_builder_observed",
            "taskId": task_id,
        }
        write_new_private_json(
            arguments.preflight_receipt,
            preflight,
            label="private builder preflight receipt",
        )
        build_command = [
            str(executables["python"]),
            "-m",
            "tools.build_managed_b_offline_candidate",
            arguments.expected_source_commit,
            "--wheel-artifact-root",
            str(arguments.wheel_artifact_root),
            "--output-parent",
            str(output_parent),
            "--receipt",
            str(arguments.build_receipt),
        ]
        for role_name, option_name in (
            ("build", "build_image_archive"),
            ("runtime-libraries", "runtime_libraries_image_archive"),
            ("runtime", "runtime_image_archive"),
        ):
            archive = getattr(arguments, option_name)
            digest = getattr(arguments, f"{option_name}_sha256")
            build_command.extend(
                [
                    f"--{role_name}-image-archive",
                    str(archive),
                    f"--{role_name}-image-archive-sha256",
                    digest,
                ]
            )
        _checked_operator_command(
            runner,
            build_command,
            cwd=source_root,
            environment=environment,
        )
        build, build_raw = _json_file(
            arguments.build_receipt, label="managed B build receipt"
        )
        parsed_build = _build_inputs(
            build, expected_commit=arguments.expected_source_commit
        )
        if parsed_build["authority"]["sourceDateEpoch"] != source_epoch:
            raise TwoBuilderError("build source epoch differs from detached clone")
        candidate_image_id = str(parsed_build["imageId"])
        materialized = _object(build.get("materialized"), label="build materialized")
        context_path = Path(str(materialized.get("build_context_path", "")))
        _require_within(context_path, output_parent, label="materialized build context")
        source_archive = _sealed_context_source_archive(
            context_path,
            expected_tree_sha256=str(
                parsed_build["authority"]["buildContextTreeSha256"]
            ),
            source_date_epoch=source_epoch,
        )
        if _sha256(source_archive) != parsed_build["authority"]["sourceArchiveSha256"]:
            raise TwoBuilderError("materialized source archive differs from authority")
        _write_new_private_bytes(
            source_archive_path,
            source_archive,
            label="candidate source archive",
        )
        artifact_root = Path(str(materialized.get("candidate_artifact_path", "")))
        _require_within(artifact_root, output_parent, label="candidate artifact")
        archive_path = artifact_root / "candidate.tar"
        after = _measure_private_builder(
            runner,
            docker=executables["docker"],
            ctr=executables["ctr"],
            ip=executables["ip"],
            sudo=executables["sudo"],
            docker_socket=arguments.docker_socket,
            containerd_socket=arguments.containerd_socket,
            runtime_root=runtime_root,
            network_namespace=arguments.network_namespace,
            containers_namespace=arguments.containers_namespace,
            plugins_namespace=arguments.plugins_namespace,
            environment=environment,
        )
        if after["identity"] != before["identity"]:
            raise TwoBuilderError("private builder identity changed after build")
        docker_images, containerd_images = _builder_inventory(
            runner,
            docker=executables["docker"],
            ctr=executables["ctr"],
            docker_socket=arguments.docker_socket,
            containerd_socket=arguments.containerd_socket,
            containers_namespace=arguments.containers_namespace,
            environment=environment,
        )
        after_info = _receipt_docker_info(
            after,
            docker_unit=docker_unit,
            containerd_unit=containerd_unit,
            docker_config_path=arguments.docker_config,
            containerd_config_path=arguments.containerd_config,
            docker_config_sha256=config_hashes["docker"],
            containerd_config_sha256=config_hashes["containerd"],
        )
        base_exit_codes: dict[str, int] = {}
        for reference in _base_references(build):
            completed = _checked_operator_command(
                runner,
                _docker_command(
                    executables["docker"],
                    arguments.docker_socket,
                    "image",
                    "inspect",
                    reference,
                ),
                cwd=None,
                environment=environment,
                allowed=frozenset({1}),
            )
            base_exit_codes[reference] = completed.returncode
        postflight = {
            "admissionChallenge": admission_challenge,
            "buildReceiptSha256": _sha256(build_raw),
            "buildRunChallenge": parsed_build["builder"]["runChallenge"],
            "builderIdentity": after["identity"],
            "candidateArchiveSha256": parsed_build["archiveSha256"],
            "candidateImageId": candidate_image_id,
            "containerdImages": containerd_images,
            "contract": postflight_contract,
            "dockerImages": docker_images,
            "dockerInfo": after_info,
            "networkNamespace": after["network"],
            "observationChallenge": observation_challenge,
            "postflightBuilderMatchesBuildAfter": True,
            "preflightBuilderMatchesBuildBefore": True,
            "preflightReceiptSha256": _sha256(
                _safe_file(
                    arguments.preflight_receipt,
                    label="preflight receipt",
                    maximum=_MAX_JSON_BYTES,
                )
            ),
            "releaseAuthorityReceiptSha256": _sha256(authority_raw),
            "releaseAuthoritySha256": arguments.expected_authority_sha256,
            "runId": run_id,
            "sealedBaseImageInspectExitCodes": base_exit_codes,
            "sealedBaseImagesLoadedIntoDocker": 0,
            "sourceCommit": arguments.expected_source_commit,
            "status": "candidate_only_offline_postflight_observed",
            "taskId": task_id,
        }
        write_new_private_json(
            arguments.postflight_receipt,
            postflight,
            label="private builder postflight receipt",
        )
        common = {
            "preflight_path": arguments.preflight_receipt,
            "build_path": arguments.build_receipt,
            "postflight_path": arguments.postflight_receipt,
            "archive_path": archive_path,
            "source_archive_path": source_archive_path,
            "release_authority_path": arguments.release_authority,
            "cleanup_path": arguments.cleanup_receipt,
            "expected_source_commit": arguments.expected_source_commit,
            "expected_authority_sha256": arguments.expected_authority_sha256,
            "expected_observation_challenge": observation_challenge,
            "expected_build_run_challenge": parsed_build["builder"]["runChallenge"],
            "admission_challenge": admission_challenge,
            "prior_admission_paths": tuple(arguments.prior_admission),
        }
        if role == "b" and (
            arguments.builder_a_admission is None
            or arguments.builder_a_preflight is None
        ):
            raise TwoBuilderError("builder-B requires builder-A evidence")
        verification_inputs = common
    except Exception as error:
        build_error = error
    cleanup = _cleanup_private_builder(
        runner,
        role=role,
        run_id=run_id,
        runtime_root=runtime_root,
        docker_socket=arguments.docker_socket,
        containerd_socket=arguments.containerd_socket,
        docker_pid=docker_pid,
        containerd_pid=containerd_pid,
        network_namespace=arguments.network_namespace,
        docker_unit=arguments.docker_unit,
        containerd_unit=arguments.containerd_unit,
        candidate_image_id=candidate_image_id,
        docker=executables["docker"],
        ip=executables["ip"],
        systemctl=executables["systemctl"],
        sudo=executables["sudo"],
        rm=executables["rm"],
        environment=environment,
        cleanup_receipt_path=arguments.cleanup_receipt,
    )
    if build_error is not None:
        raise build_error
    if verification_inputs is None:
        raise TwoBuilderError("private builder produced no verification inputs")
    if role == "a":
        admission = verify_single_builder_a(**verification_inputs)
    else:
        admission = verify_single_builder_b(
            **verification_inputs,
            builder_a_admission_path=arguments.builder_a_admission,
            builder_a_preflight_path=arguments.builder_a_preflight,
        )
    write_new_private_json(
        arguments.admission_receipt,
        admission,
        label="private builder admission receipt",
    )
    return {"admission": admission, "cleanup": cleanup}


def _cleanup_unit_pid(
    runner: OperatorRunner,
    *,
    systemctl: Path,
    unit: str,
    environment: dict[str, str],
) -> int:
    completed = _checked_operator_command(
        runner,
        [str(systemctl), "show", unit, "--property=MainPID"],
        cwd=None,
        environment=environment,
        allowed=frozenset({0, 4}),
    )
    if completed.returncode == 4 and not completed.stdout.strip():
        return 0
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    value = values.get("MainPID", "")
    if not value.isdigit():
        raise TwoBuilderError("private builder cleanup PID is unknown")
    return int(value)


def run_private_builder_operator(
    arguments: argparse.Namespace,
    *,
    runner: OperatorRunner = _run_operator_command,
) -> dict[str, object]:
    """Run one RR-002 builder and tear it down after every scoped failure."""

    runtime_root = _private_directory(
        arguments.runtime_root, label="private runtime root", under_tmp=True
    )
    if runtime_root.parent != Path("/tmp") or not runtime_root.name.startswith(
        "eva2-rr002-"
    ):
        raise TwoBuilderError("private runtime root name is unsafe")
    if (
        arguments.role not in {"a", "b"}
        or _NAMESPACE_NAME.fullmatch(arguments.network_namespace) is None
        or not arguments.network_namespace.startswith("eva2-rr002-")
        or arguments.docker_unit == arguments.containerd_unit
        or not arguments.docker_unit.startswith("eva2-rr002-")
        or not arguments.containerd_unit.startswith("eva2-rr002-")
    ):
        raise TwoBuilderError("private builder cleanup scope is unsafe")
    executables = {
        name: _explicit_executable(getattr(arguments, name), label=name)
        for name in ("ip", "systemctl", "sudo", "rm")
    }
    cleanup_environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": ":".join(
            dict.fromkeys(str(path.parent) for path in executables.values())
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    try:
        return _run_private_builder_operator(arguments, runner=runner)
    except BaseException as original_error:
        if runtime_root.exists() or runtime_root.is_symlink():
            try:
                docker_pid = _cleanup_unit_pid(
                    runner,
                    systemctl=executables["systemctl"],
                    unit=arguments.docker_unit,
                    environment=cleanup_environment,
                )
                containerd_pid = _cleanup_unit_pid(
                    runner,
                    systemctl=executables["systemctl"],
                    unit=arguments.containerd_unit,
                    environment=cleanup_environment,
                )
                _cleanup_private_builder(
                    runner,
                    role=arguments.role,
                    run_id=f"rr002-builder-{arguments.role}-failed",
                    runtime_root=runtime_root,
                    docker_socket=arguments.docker_socket,
                    containerd_socket=arguments.containerd_socket,
                    docker_pid=docker_pid,
                    containerd_pid=containerd_pid,
                    network_namespace=arguments.network_namespace,
                    docker_unit=arguments.docker_unit,
                    containerd_unit=arguments.containerd_unit,
                    candidate_image_id=None,
                    docker=arguments.docker,
                    ip=executables["ip"],
                    systemctl=executables["systemctl"],
                    sudo=executables["sudo"],
                    rm=executables["rm"],
                    environment=cleanup_environment,
                    cleanup_receipt_path=arguments.cleanup_receipt,
                )
            except Exception as cleanup_error:
                raise TwoBuilderError(
                    "private builder failed and cleanup could not be proven"
                ) from cleanup_error
        raise original_error


def _two_builder_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-a", type=Path, required=True)
    parser.add_argument("--admission-b", type=Path, required=True)
    parser.add_argument("--build-a", type=Path, required=True)
    parser.add_argument("--build-b", type=Path, required=True)
    parser.add_argument("--archive-a", type=Path, required=True)
    parser.add_argument("--archive-b", type=Path, required=True)
    parser.add_argument("--source-archive-a", type=Path, required=True)
    parser.add_argument("--source-archive-b", type=Path, required=True)
    parser.add_argument("--cleanup-a", type=Path, required=True)
    parser.add_argument("--cleanup-b", type=Path, required=True)
    parser.add_argument("--expected-admission-a-sha256", required=True)
    parser.add_argument("--expected-admission-b-sha256", required=True)
    parser.add_argument("--expected-release-authority-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def _two_builder_main(argv: list[str]) -> int:
    arguments = _two_builder_parser().parse_args(argv)
    try:
        result = verify_two_builds(
            admission_paths=(arguments.admission_a, arguments.admission_b),
            build_paths=(arguments.build_a, arguments.build_b),
            archive_paths=(arguments.archive_a, arguments.archive_b),
            source_archive_paths=(
                arguments.source_archive_a,
                arguments.source_archive_b,
            ),
            cleanup_paths=(arguments.cleanup_a, arguments.cleanup_b),
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


def _private_builder_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{Path(sys.argv[0]).name} private-builder",
        description=(
            "Measure, build, admit, and remove one externally provisioned "
            "RR-002 private builder."
        ),
    )
    parser.add_argument("--role", choices=("a", "b"), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--release-authority", type=Path, required=True)
    parser.add_argument("--expected-authority-sha256", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--docker-socket", type=Path, required=True)
    parser.add_argument("--containerd-socket", type=Path, required=True)
    parser.add_argument("--docker-config", type=Path, required=True)
    parser.add_argument("--containerd-config", type=Path, required=True)
    parser.add_argument("--docker-pid-file", type=Path, required=True)
    parser.add_argument("--containerd-pid-file", type=Path, required=True)
    parser.add_argument("--docker-unit", required=True)
    parser.add_argument("--containerd-unit", required=True)
    parser.add_argument("--network-namespace", required=True)
    parser.add_argument("--containers-namespace", required=True)
    parser.add_argument("--plugins-namespace", required=True)
    parser.add_argument("--wheel-artifact-root", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    for role in ("build", "runtime-libraries", "runtime"):
        parser.add_argument(f"--{role}-image-archive", type=Path, required=True)
        parser.add_argument(f"--{role}-image-archive-sha256", required=True)
    parser.add_argument("--preflight-receipt", type=Path, required=True)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--postflight-receipt", type=Path, required=True)
    parser.add_argument("--admission-receipt", type=Path, required=True)
    parser.add_argument("--cleanup-receipt", type=Path, required=True)
    parser.add_argument(
        "--prior-admission",
        type=Path,
        action="append",
        required=True,
        help="Prior A/B v1 or v2 receipt used only as a replay denylist.",
    )
    parser.add_argument("--builder-a-admission", type=Path)
    parser.add_argument("--builder-a-preflight", type=Path)
    for name in (
        "python",
        "git",
        "docker",
        "buildx",
        "ctr",
        "ip",
        "systemctl",
        "sudo",
        "rm",
    ):
        parser.add_argument(
            f"--{name}-executable",
            dest=name,
            type=Path,
            required=True,
        )
    return parser


def _private_builder_main(argv: list[str]) -> int:
    arguments = _private_builder_parser().parse_args(argv)
    try:
        result = run_private_builder_operator(arguments)
    except (OSError, PrivateReceiptError, TwoBuilderError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "private-builder":
        return _private_builder_main(values[1:])
    return _two_builder_main(values)


if __name__ == "__main__":
    raise SystemExit(main())
