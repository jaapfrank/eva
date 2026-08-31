"""Build one local managed-B candidate image from sealed offline inputs.

This is deliberately a candidate-only operation.  It never opens Compose or
any B runtime input, and it gives Docker neither network access nor a tag/push
destination.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Callable

from .base_image import (
    MaterializedBaseImage,
    validate_materialized_base_image,
)
from .build_context import (
    MaterializedBuildContext,
    validate_materialized_build_context,
)


_IMAGE_PREFIX = "sha256:"


@dataclass(frozen=True, slots=True)
class CandidateImage:
    image_id: str
    receipt: dict[str, object]


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _image_id(value: str, *, label: str) -> str:
    if (
        not value.startswith(_IMAGE_PREFIX)
        or len(value) != len(_IMAGE_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise RuntimeError(f"managed B {label} is not a sha256 image ID")
    return value


def _private_parent(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("managed B candidate image output parent is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise RuntimeError("managed B candidate image output parent must be private")


def _inspect(reference: str, runner: Runner) -> dict[str, Any]:
    result = runner(["docker", "image", "inspect", reference], 30)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("managed B local image inspection is invalid") from error
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("managed B local image inspection is invalid")
    return value[0]


def _verify_current_base(base: dict[str, object], runner: Runner) -> None:
    digest = str(base["base_image_digest"])
    reference = f"{base['base_image_repository']}@{digest}"
    current = _inspect(reference, runner)
    if (
        current.get("Id") != base["local_image_id"]
        or current.get("Os") != "linux"
        or current.get("Architecture") != "amd64"
        or not isinstance(current.get("RepoDigests"), list)
        or base["local_repo_digest"] not in current["RepoDigests"]
    ):
        raise RuntimeError("managed B Docker base image differs from the sealed local export")


def _read_iid(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("managed B candidate image ID file is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 80
    ):
        raise RuntimeError("managed B candidate image ID file is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
        ):
            raise RuntimeError("managed B candidate image ID file changed before reading")
        raw = os.read(descriptor, 81)
    finally:
        os.close(descriptor)
    try:
        return _image_id(raw.decode("ascii", "strict").strip(), label="candidate")
    except UnicodeDecodeError as error:
        raise RuntimeError("managed B candidate image ID file is not ASCII") from error


def _source_identity(receipt: dict[str, object]) -> tuple[str, int]:
    source_commit = receipt.get("candidate_source_commit")
    source_date_epoch = receipt.get("candidate_source_date_epoch")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch <= 0
    ):
        raise RuntimeError("managed B candidate source identity is invalid")
    return source_commit, source_date_epoch


def _image_binding(receipt: dict[str, object], role: str) -> tuple[str, str]:
    digest = receipt.get(f"{role}_image_digest")
    archive = receipt.get(f"{role}_image_archive_sha256")
    reference = receipt.get(f"{role}_image_reference")
    if (
        not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith(_IMAGE_PREFIX)
        or any(character not in "0123456789abcdef" for character in digest[7:])
        or not isinstance(archive, str)
        or len(archive) != 64
        or any(character not in "0123456789abcdef" for character in archive)
        or not isinstance(reference, str)
        or not reference.endswith(f"@{digest}")
    ):
        raise RuntimeError(f"managed B {role} image binding is invalid")
    return digest, reference


def _require_context_base(
    context: dict[str, object], base: dict[str, object], role: str
) -> None:
    if (
        context.get(f"{role}_image_digest") != base.get("base_image_digest")
        or context.get(f"{role}_image_archive_sha256") != base.get("archive_sha256")
        or context.get(f"{role}_image_repository")
        != base.get("base_image_repository")
    ):
        raise RuntimeError(
            f"managed B context {role} image differs from the sealed local export"
        )


def _verify_untagged_candidate(
    image_id: str,
    runner: Runner,
    *,
    source_commit: str,
    source_date_epoch: int,
) -> dict[str, Any]:
    inspection = _inspect(image_id, runner)
    if _image_id(str(inspection.get("Id", "")), label="candidate") != image_id:
        raise RuntimeError("managed B candidate inspection ID differs")
    tags = inspection.get("RepoTags")
    if tags not in (None, []):
        raise RuntimeError("managed B candidate image must not receive a tag")
    size = inspection.get("Size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise RuntimeError("managed B candidate image size is invalid")
    config = inspection.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        not isinstance(labels, dict)
        or labels.get("io.eva2.source-commit") != source_commit
        or labels.get("io.eva2.source-date-epoch") != str(source_date_epoch)
        or labels.get("io.eva2.source-refresh") != "git-archive-v1"
    ):
        raise RuntimeError("managed B candidate image source identity differs")
    return inspection


def build_offline_candidate_image(
    context: MaterializedBuildContext,
    build_image: MaterializedBaseImage,
    runtime_libraries_image: MaterializedBaseImage,
    runtime_image: MaterializedBaseImage,
    output_parent: Path,
    *,
    runner: Runner = _run,
) -> CandidateImage:
    """Build and load one untagged image with network disabled throughout RUN steps."""

    context_receipt = validate_materialized_build_context(context)
    build_receipt = validate_materialized_base_image(build_image)
    runtime_libraries_receipt = validate_materialized_base_image(
        runtime_libraries_image
    )
    runtime_receipt = validate_materialized_base_image(runtime_image)
    bases = {
        "build": build_receipt,
        "runtime_libraries": runtime_libraries_receipt,
        "runtime": runtime_receipt,
    }
    for role, receipt in bases.items():
        _require_context_base(context_receipt, receipt, role)
    source_commit, source_date_epoch = _source_identity(context_receipt)
    for receipt in bases.values():
        _verify_current_base(receipt, runner)
    _private_parent(output_parent)
    temporary_root = Path(tempfile.mkdtemp(prefix="eva2-candidate-image-", dir=output_parent))
    temporary_root.chmod(0o700)
    iidfile = temporary_root / "image.iid"
    try:
        runner(
            [
                "docker",
                "build",
                "--network=none",
                "--platform=linux/amd64",
                "--pull=false",
                "--no-cache",
                "--load",
                "--build-arg",
                f"SOURCE_COMMIT={source_commit}",
                "--build-arg",
                f"SOURCE_DATE_EPOCH={source_date_epoch}",
                "--file",
                str(context.path / "deploy/living-core/Dockerfile"),
                "--iidfile",
                str(iidfile),
                str(context.path),
            ],
            600,
        )
        image_id = _read_iid(iidfile)
        candidate_inspection = _verify_untagged_candidate(
            image_id,
            runner,
            source_commit=source_commit,
            source_date_epoch=source_date_epoch,
        )
        for receipt in bases.values():
            _verify_current_base(receipt, runner)
    finally:
        temporary_root.chmod(0o700)
        if iidfile.exists() or iidfile.is_symlink():
            iidfile.unlink()
        temporary_root.rmdir()
    receipt = {
        "build_image_archive_sha256": build_receipt["archive_sha256"],
        "build_image_digest": build_receipt["base_image_digest"],
        "build_image_reference": (
            f"{build_receipt['base_image_repository']}@{build_receipt['base_image_digest']}"
        ),
        "candidate_image_id": image_id,
        "candidate_image_size": candidate_inspection["Size"],
        "candidate_source_commit": source_commit,
        "candidate_source_date_epoch": source_date_epoch,
        "context_tree_sha256": context_receipt["context_tree_sha256"],
        "contract": "EvaManagedBOfflineCandidateImageReceipt/v2",
        "network_used": False,
        "nonclaims": [
            "The candidate image is untagged and was not pushed, signed, released, or attached to Compose.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
        "status": "built_local_candidate",
        "runtime_image_archive_sha256": runtime_receipt["archive_sha256"],
        "runtime_image_digest": runtime_receipt["base_image_digest"],
        "runtime_image_reference": (
            f"{runtime_receipt['base_image_repository']}@{runtime_receipt['base_image_digest']}"
        ),
        "runtime_libraries_image_archive_sha256": runtime_libraries_receipt[
            "archive_sha256"
        ],
        "runtime_libraries_image_digest": runtime_libraries_receipt[
            "base_image_digest"
        ],
        "runtime_libraries_image_reference": (
            f"{runtime_libraries_receipt['base_image_repository']}@"
            f"{runtime_libraries_receipt['base_image_digest']}"
        ),
    }
    return CandidateImage(image_id, receipt)


def validate_offline_candidate_image(
    candidate: CandidateImage, *, runner: Runner = _run
) -> dict[str, object]:
    if not isinstance(candidate, CandidateImage):
        raise RuntimeError("managed B candidate image must be materialized")
    receipt = candidate.receipt
    if (
        receipt.get("contract") != "EvaManagedBOfflineCandidateImageReceipt/v2"
        or receipt.get("status") != "built_local_candidate"
        or receipt.get("network_used") is not False
        or receipt.get("candidate_image_id") != candidate.image_id
    ):
        raise RuntimeError("managed B candidate image receipt differs")
    for role in ("build", "runtime_libraries", "runtime"):
        _image_binding(receipt, role)
    source_commit, source_date_epoch = _source_identity(receipt)
    inspection = _verify_untagged_candidate(
        candidate.image_id,
        runner,
        source_commit=source_commit,
        source_date_epoch=source_date_epoch,
    )
    if inspection["Size"] != receipt.get("candidate_image_size"):
        raise RuntimeError("managed B candidate image receipt no longer matches")
    return receipt


__all__ = [
    "CandidateImage",
    "build_offline_candidate_image",
    "validate_offline_candidate_image",
]
