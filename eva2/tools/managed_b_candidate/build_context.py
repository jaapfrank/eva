"""Create a private, deterministic Docker context from sealed managed-B inputs.

No Docker command is run here.  The base OCI archive remains an external input
and is bound only in the receipt; it is deliberately never copied into context.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .base_image import (
    MaterializedBaseImage,
    validate_materialized_base_image,
)
from eva.operations.managed_b_offline_build import validate_offline_candidate_inputs
from eva.operations.managed_b_wheelhouse import (
    MaterializedWheelhouse,
    expected_wheel_artifacts,
)
from tools.prepare_managed_b_candidate_context import (
    CandidateBuildInputs,
    RUNTIME_REQUIREMENTS_LOCK,
    validate_candidate_build_inputs,
)


@dataclass(frozen=True, slots=True)
class MaterializedBuildContext:
    path: Path
    receipt: dict[str, object]


def _tree_hash(entries: list[dict[str, object]]) -> str:
    raw = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _regular_private_parent(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("managed B context output parent is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RuntimeError("managed B context output parent must be a private directory")


def _write_source(root: Path, files: dict[str, tuple[bytes, int]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for name, (content, mode) in sorted(files.items()):
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise RuntimeError("managed B source context copy could not make progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        final_mode = 0o555 if mode & 0o111 else 0o444
        destination.chmod(final_mode)
        entries.append(
            {
                "mode": final_mode,
                "path": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    return entries


def _copy_wheel(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    expected_size: int,
) -> dict[str, object]:
    before = source.lstat()
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_mode & 0o222
        or before.st_size != expected_size
    ):
        raise RuntimeError("managed B wheelhouse changed before context copy")
    source_fd = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_nlink,
        ):
            raise RuntimeError("managed B wheelhouse changed before context copy")
        destination_fd = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            digest = hashlib.sha256()
            size = 0
            while block := os.read(source_fd, 1024 * 1024):
                digest.update(block)
                size += len(block)
                view = memoryview(block)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise RuntimeError("managed B wheel context copy could not make progress")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    after = source.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_ctime_ns,
    ):
        raise RuntimeError("managed B wheelhouse changed during context copy")
    if size != expected_size or digest.hexdigest() != expected_digest:
        raise RuntimeError("managed B wheelhouse wheel differs from its sealed manifest")
    destination.chmod(0o444)
    return {
        "mode": 0o444,
        "path": f"wheelhouse/{source.name}",
        "sha256": digest.hexdigest(),
        "size": size,
    }


def _seal_directories(root: Path) -> None:
    for directory, _subdirectories, _files in os.walk(root, topdown=False, followlinks=False):
        path = Path(directory)
        if path != root:
            path.chmod(0o555)
    root.chmod(0o500)


def _actual_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for directory, subdirectories, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        directory_metadata = directory_path.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_mode & 0o222
        ):
            raise RuntimeError("managed B build context contains a writable directory")
        for name in subdirectories:
            path = directory_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError("managed B build context contains an unsafe entry")
        for name in files:
            path = directory_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("managed B build context contains an unsafe entry")
        for name in files:
            path = directory_path / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o222:
                raise RuntimeError("managed B build context contains a writable file")
            digest = hashlib.sha256()
            with path.open("rb", buffering=0) as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            entries.append(
                {
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "path": path.relative_to(root).as_posix(),
                    "sha256": digest.hexdigest(),
                    "size": metadata.st_size,
                }
            )
    return sorted(entries, key=lambda entry: str(entry["path"]))


def _remove_unfinished_context(root: Path) -> None:
    """Remove only the fresh context we created after restoring private modes."""

    for directory, _subdirectories, _files in os.walk(root, topdown=False, followlinks=False):
        Path(directory).chmod(0o700)
    for directory, _subdirectories, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(directory)
        for name in files:
            (path / name).unlink()
        if path != root:
            path.rmdir()
    root.rmdir()


def materialize_build_context(
    candidate: CandidateBuildInputs,
    wheelhouse_manifest_raw: bytes,
    wheel_coverage_raw: bytes,
    wheelhouse: MaterializedWheelhouse,
    base_image: MaterializedBaseImage,
    output_parent: Path,
) -> MaterializedBuildContext:
    """Materialize exact B01+B02b bytes and bind B03c without using Docker."""

    source_files = validate_candidate_build_inputs(candidate)
    lock = source_files[RUNTIME_REQUIREMENTS_LOCK][0]
    wheel_receipt = validate_offline_candidate_inputs(
        candidate,
        wheelhouse_manifest_raw,
        wheel_coverage_raw,
        wheelhouse,
        lock,
    )
    expected_wheels = expected_wheel_artifacts(
        wheelhouse_manifest_raw,
        wheel_coverage_raw,
        lock,
    )
    base_receipt = validate_materialized_base_image(base_image)
    _regular_private_parent(output_parent)
    root = Path(tempfile.mkdtemp(prefix="eva2-build-context-", dir=output_parent))
    root.chmod(0o700)
    try:
        entries = _write_source(root, source_files)
        wheel_destination = root / "wheelhouse"
        wheel_destination.mkdir(mode=0o700)
        if {path.name for path in wheelhouse.path.iterdir()} != set(expected_wheels):
            raise RuntimeError("managed B wheelhouse file set differs before context copy")
        for name, (digest, size) in sorted(expected_wheels.items()):
            entries.append(
                _copy_wheel(
                    wheelhouse.path / name,
                    wheel_destination / name,
                    expected_digest=digest,
                    expected_size=size,
                )
            )
        validate_offline_candidate_inputs(
            candidate,
            wheelhouse_manifest_raw,
            wheel_coverage_raw,
            wheelhouse,
            lock,
        )
        entries.sort(key=lambda entry: str(entry["path"]))
        expected_hash = _tree_hash(entries)
        _seal_directories(root)
        actual = _actual_entries(root)
        if actual != entries or _tree_hash(actual) != expected_hash:
            raise RuntimeError("managed B build context tree differs after sealing")
    except Exception:
        _remove_unfinished_context(root)
        raise
    receipt = {
        "base_image_archive_sha256": base_receipt["archive_sha256"],
        "base_image_digest": base_receipt["base_image_digest"],
        "base_image_platform": base_receipt["platform"],
        "candidate_dockerfile_sha256": candidate.dockerfile_sha256,
        "candidate_source_archive_sha256": candidate.source_archive_sha256,
        "candidate_source_commit": candidate.source_commit,
        "context_files": entries,
        "context_tree_sha256": expected_hash,
        "contract": "EvaManagedBBuildContextReceipt/v1",
        "network_used": False,
        "nonclaims": [
            "No Docker context was sent to a builder and no image was built, loaded, tagged, pushed, signed, or released.",
            "The base OCI archive remains outside this Docker context.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
        "requirements_lock_sha256": candidate.requirements_lock_sha256,
        "requirements_lock_path": RUNTIME_REQUIREMENTS_LOCK,
        "status": "materialized",
        "wheelhouse_tree_sha256": wheel_receipt["wheelhouse_tree_sha256"],
    }
    return MaterializedBuildContext(root, receipt)


def validate_materialized_build_context(context: MaterializedBuildContext) -> dict[str, object]:
    if not isinstance(context, MaterializedBuildContext):
        raise RuntimeError("managed B build context must be materialized")
    receipt = context.receipt
    expected = receipt.get("context_files")
    if (
        receipt.get("contract") != "EvaManagedBBuildContextReceipt/v1"
        or receipt.get("status") != "materialized"
        or receipt.get("network_used") is not False
        or not isinstance(expected, list)
        or not all(isinstance(item, dict) for item in expected)
    ):
        raise RuntimeError("managed B build context receipt differs")
    metadata = context.path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o222
    ):
        raise RuntimeError("managed B build context must remain read-only")
    actual = _actual_entries(context.path)
    if actual != expected or _tree_hash(actual) != receipt.get("context_tree_sha256"):
        raise RuntimeError("managed B build context receipt no longer matches its tree")
    if any(item["path"] == "base.oci.tar" for item in actual):
        raise RuntimeError("managed B base OCI archive must remain outside build context")
    return receipt


__all__ = [
    "MaterializedBuildContext",
    "materialize_build_context",
    "validate_materialized_build_context",
]
