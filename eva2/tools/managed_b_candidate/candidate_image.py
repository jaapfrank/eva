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
    reference = f"docker.io/library/python@{digest}"
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


def _verify_untagged_candidate(image_id: str, runner: Runner) -> dict[str, Any]:
    inspection = _inspect(image_id, runner)
    if _image_id(str(inspection.get("Id", "")), label="candidate") != image_id:
        raise RuntimeError("managed B candidate inspection ID differs")
    tags = inspection.get("RepoTags")
    if tags not in (None, []):
        raise RuntimeError("managed B candidate image must not receive a tag")
    size = inspection.get("Size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise RuntimeError("managed B candidate image size is invalid")
    return inspection


def build_offline_candidate_image(
    context: MaterializedBuildContext,
    base_image: MaterializedBaseImage,
    output_parent: Path,
    *,
    runner: Runner = _run,
) -> CandidateImage:
    """Build and load one untagged image with network disabled throughout RUN steps."""

    context_receipt = validate_materialized_build_context(context)
    base_receipt = validate_materialized_base_image(base_image)
    if context_receipt["base_image_digest"] != base_receipt["base_image_digest"]:
        raise RuntimeError("managed B context base digest differs from the sealed local export")
    if context_receipt["base_image_archive_sha256"] != base_receipt["archive_sha256"]:
        raise RuntimeError("managed B context base archive differs from the sealed local export")
    _verify_current_base(base_receipt, runner)
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
                "--file",
                str(context.path / "deploy/living-core/Dockerfile"),
                "--iidfile",
                str(iidfile),
                str(context.path),
            ],
            600,
        )
        image_id = _read_iid(iidfile)
        candidate_inspection = _verify_untagged_candidate(image_id, runner)
    finally:
        temporary_root.chmod(0o700)
        if iidfile.exists() or iidfile.is_symlink():
            iidfile.unlink()
        temporary_root.rmdir()
    receipt = {
        "base_image_archive_sha256": base_receipt["archive_sha256"],
        "base_image_digest": base_receipt["base_image_digest"],
        "candidate_image_id": image_id,
        "candidate_image_size": candidate_inspection["Size"],
        "context_tree_sha256": context_receipt["context_tree_sha256"],
        "contract": "EvaManagedBOfflineCandidateImageReceipt/v1",
        "network_used": False,
        "nonclaims": [
            "The candidate image is untagged and was not pushed, signed, released, or attached to Compose.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
        "status": "built_local_candidate",
    }
    return CandidateImage(image_id, receipt)


def validate_offline_candidate_image(
    candidate: CandidateImage, *, runner: Runner = _run
) -> dict[str, object]:
    if not isinstance(candidate, CandidateImage):
        raise RuntimeError("managed B candidate image must be materialized")
    receipt = candidate.receipt
    if (
        receipt.get("contract") != "EvaManagedBOfflineCandidateImageReceipt/v1"
        or receipt.get("status") != "built_local_candidate"
        or receipt.get("network_used") is not False
        or receipt.get("candidate_image_id") != candidate.image_id
    ):
        raise RuntimeError("managed B candidate image receipt differs")
    inspection = _verify_untagged_candidate(candidate.image_id, runner)
    if inspection["Size"] != receipt.get("candidate_image_size"):
        raise RuntimeError("managed B candidate image receipt no longer matches")
    return receipt


__all__ = [
    "CandidateImage",
    "build_offline_candidate_image",
    "validate_offline_candidate_image",
]
