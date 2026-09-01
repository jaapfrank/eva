#!/usr/bin/env python3
"""Verify one candidate built by two independently identified build states."""

from __future__ import annotations

import argparse
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
        "daemonId",
        "dockerServerVersion",
    ):
        field = value.get(key)
        if not isinstance(field, str) or not field or len(field) > 256:
            raise TwoBuilderError(f"builder {key} is invalid")
    for key in (
        "dockerExecutableSha256",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ):
        _sha(value.get(key), label=f"builder {key}")
    return value


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
    if (
        candidate_receipt.get("candidate_image_id") != image_id
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
    expected_diff_ids = [f"sha256:{_sha256(item)}" for item in layer_raw]
    if rootfs.get("type") != "layers" or diff_ids != expected_diff_ids:
        raise TwoBuilderError("candidate layers differ from their DiffIDs")
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise TwoBuilderError("candidate platform differs")
    runtime = _object(config.get("config"), label="candidate runtime config")
    return {
        "archiveSha256": expected_archive_sha256,
        "configSha256": _sha256(config_raw),
        "diffIds": diff_ids,
        "imageId": f"sha256:{_sha256(config_raw)}",
        "runtimeConfigSha256": _sha256(_canonical(runtime)),
    }


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
        if semantics[index]["imageId"] != builds[index]["imageId"]:
            raise TwoBuilderError("archive image ID differs from build receipt")
    for key in ("imageId", "configSha256", "diffIds", "runtimeConfigSha256"):
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
        "candidate": semantics[0],
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
