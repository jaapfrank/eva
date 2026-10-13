"""Validate and materialize pinned base images for a networkless BuildKit run.

The source digest, selected linux/amd64 manifest, config and layers are bound
inside one sealed OCI archive.  A candidate builder consumes a read-only OCI
layout directly; it never needs a registry name, tag or preloaded Docker image.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any


_DIGEST_PREFIX = "sha256:"
_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024
_OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_DEFAULT_REPOSITORY = "docker.io/library/python"


@dataclass(frozen=True, slots=True)
class MaterializedBaseImage:
    """One private, read-only local OCI archive for a later offline build."""

    path: Path
    receipt: dict[str, object]


@dataclass(frozen=True, slots=True)
class MaterializedBaseImageLayout:
    """One read-only OCI layout derived from a validated sealed archive."""

    path: Path
    receipt: dict[str, object]


def _digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(_DIGEST_PREFIX)
        or len(value) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise RuntimeError(f"{label} must be a sha256 digest")
    return value


def _reference(digest: str, repository: str = _DEFAULT_REPOSITORY) -> str:
    if (
        not repository
        or "@" in repository
        or repository.startswith("/")
        or repository.endswith("/")
        or any(character.isspace() for character in repository)
    ):
        raise RuntimeError("base image repository is invalid")
    return f"{repository}@{_digest(digest, label='base image digest')}"


def _hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while block := handle.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def _json(raw: bytes, *, label: str) -> dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        raise RuntimeError(f"{label} is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _member_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    if (
        not member.name
        or "\x00" in member.name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("base OCI archive contains an unsafe path")
    return path.as_posix()


def _descriptor(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "annotations", "artifactType", "data", "digest", "mediaType", "platform", "size", "urls"
    }:
        raise RuntimeError(f"{label} descriptor is invalid")
    if "urls" in value or _digest(value.get("digest"), label=f"{label} digest") is None:
        raise RuntimeError(f"{label} descriptor must not use external URLs")
    if not isinstance(value.get("mediaType"), str) or not value["mediaType"]:
        raise RuntimeError(f"{label} media type is invalid")
    if isinstance(value.get("size"), bool) or not isinstance(value.get("size"), int) or value["size"] <= 0:
        raise RuntimeError(f"{label} size is invalid")
    return value


def _expected_repo_digests(expected: str) -> set[str]:
    short = expected.removeprefix("docker.io/library/")
    return {expected, short, f"library/{short}"}


def _validate_inspection(
    expected: str,
    inspection: Mapping[str, object],
) -> tuple[str, str]:
    repo_digests = inspection.get("RepoDigests")
    if not isinstance(repo_digests, list) or not any(
        isinstance(item, str) and item in _expected_repo_digests(expected)
        for item in repo_digests
    ):
        raise RuntimeError("local base image inspection lacks the pinned repo digest")
    if inspection.get("Os") != "linux" or inspection.get("Architecture") != "amd64":
        raise RuntimeError("local base image platform differs from linux/amd64")
    image_id = _digest(inspection.get("Id"), label="local base image ID")
    observed_digest = next(
        item for item in repo_digests if isinstance(item, str) and item in _expected_repo_digests(expected)
    )
    return image_id, observed_digest


def _archive_entries(path: Path) -> tuple[dict[str, tarfile.TarInfo], tarfile.TarFile]:
    try:
        archive = tarfile.open(path, mode="r:")
    except (OSError, tarfile.TarError) as error:
        raise RuntimeError("base OCI archive is invalid") from error
    entries: dict[str, tarfile.TarInfo] = {}
    try:
        for member in archive:
            name = _member_name(member)
            if name in entries:
                raise RuntimeError("base OCI archive repeats a path")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise RuntimeError("base OCI archive contains a non-regular entry")
            if member.isdir():
                if name not in {"blobs", "blobs/sha256"}:
                    raise RuntimeError("base OCI archive contains an unexpected directory")
            elif not member.isfile():
                raise RuntimeError("base OCI archive contains a non-regular entry")
            elif name not in {"oci-layout", "index.json", "manifest.json"}:
                parts = PurePosixPath(name).parts
                if (
                    len(parts) != 3
                    or parts[:2] != ("blobs", "sha256")
                    or len(parts[2]) != 64
                    or any(character not in "0123456789abcdef" for character in parts[2])
                ):
                    raise RuntimeError("base OCI archive contains an unexpected file")
            entries[name] = member
    except Exception:
        archive.close()
        raise
    return entries, archive


def _member_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo, *, label: str) -> bytes:
    handle = archive.extractfile(member)
    if handle is None:
        raise RuntimeError(f"{label} is unreadable")
    data = handle.read(_MAX_JSON_BYTES + 1)
    if len(data) > _MAX_JSON_BYTES:
        raise RuntimeError(f"{label} is too large")
    return data


def _verify_blob(archive: tarfile.TarFile, member: tarfile.TarInfo, digest: str) -> None:
    if member.size <= 0:
        raise RuntimeError("base OCI blob is empty")
    handle = archive.extractfile(member)
    if handle is None:
        raise RuntimeError("base OCI blob is unreadable")
    value = hashlib.sha256()
    while block := handle.read(1024 * 1024):
        value.update(block)
    if value.hexdigest() != digest.removeprefix(_DIGEST_PREFIX):
        raise RuntimeError("base OCI blob digest differs")


def _required_blob(
    entries: Mapping[str, tarfile.TarInfo], archive: tarfile.TarFile, descriptor: dict[str, Any], *, label: str
) -> tarfile.TarInfo:
    digest = _digest(descriptor["digest"], label=f"{label} digest")
    member = entries.get(f"blobs/sha256/{digest.removeprefix(_DIGEST_PREFIX)}")
    if member is None or member.size != descriptor["size"]:
        raise RuntimeError(f"{label} blob differs")
    _verify_blob(archive, member, digest)
    return member


def validate_pinned_base_image_archive(
    archive_path: Path,
    base_image_digest: str,
    inspection: Mapping[str, object] | None = None,
    *,
    repository: str = _DEFAULT_REPOSITORY,
) -> dict[str, object]:
    """Validate an existing local OCI archive without Docker or network access."""

    expected = _reference(base_image_digest, repository)
    try:
        metadata = archive_path.lstat()
    except OSError as error:
        raise RuntimeError("base OCI archive is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise RuntimeError("base OCI archive must be one safe private regular file")
    if not 0 < metadata.st_size <= _MAX_ARCHIVE_BYTES:
        raise RuntimeError("base OCI archive size is invalid")
    entries, archive = _archive_entries(archive_path)
    try:
        required = {"oci-layout", "index.json", "manifest.json"}
        if not required.issubset(entries):
            raise RuntimeError("base OCI archive omits required metadata")
        layout = _json(_member_bytes(archive, entries["oci-layout"], label="OCI layout"), label="OCI layout")
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise RuntimeError("base OCI layout contract differs")
        index_raw = _member_bytes(archive, entries["index.json"], label="OCI index")
        index = _json(index_raw, label="OCI index")
        roots = index.get("manifests")
        if (
            index.get("schemaVersion") != 2
            or index.get("mediaType") != _OCI_INDEX_MEDIA_TYPE
            or not isinstance(roots, list)
        ):
            raise RuntimeError("base OCI root index differs")
        if len(roots) != 1:
            raise RuntimeError("base OCI root index must contain exactly one source descriptor")
        root = _descriptor(roots[0], label="base OCI root")
        if root["mediaType"] != _OCI_INDEX_MEDIA_TYPE or root["digest"] != base_image_digest:
            raise RuntimeError("base OCI root digest differs from the pinned source digest")
        root_member = _required_blob(entries, archive, root, label="base OCI root")
        nested = _json(
            _member_bytes(archive, root_member, label="base OCI nested index"),
            label="base OCI nested index",
        )
        descriptors = nested.get("manifests")
        if (
            nested.get("schemaVersion") != 2
            or nested.get("mediaType") != _OCI_INDEX_MEDIA_TYPE
            or not isinstance(descriptors, list)
        ):
            raise RuntimeError("base OCI nested index differs")
        root_index_digest = root["digest"]
        selected = [
            _descriptor(item, label="base OCI platform")
            for item in descriptors
            if isinstance(item, dict)
            and item.get("platform") == {"architecture": "amd64", "os": "linux"}
        ]
        if len(selected) != 1 or selected[0]["mediaType"] != _OCI_MANIFEST_MEDIA_TYPE:
            raise RuntimeError("base OCI must contain exactly one linux/amd64 manifest")
        manifest_member = _required_blob(entries, archive, selected[0], label="base OCI manifest")
        manifest = _json(_member_bytes(archive, manifest_member, label="base OCI manifest"), label="base OCI manifest")
        config = _descriptor(manifest.get("config"), label="base OCI config")
        if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE:
            raise RuntimeError("base OCI manifest contract differs")
        config_member = _required_blob(entries, archive, config, label="base OCI config")
        config_json = _json(_member_bytes(archive, config_member, label="base OCI config"), label="base OCI config")
        if config_json.get("os") != "linux" or config_json.get("architecture") != "amd64":
            raise RuntimeError("base OCI config platform differs")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise RuntimeError("base OCI manifest layers are invalid")
        for item in layers:
            _required_blob(entries, archive, _descriptor(item, label="base OCI layer"), label="base OCI layer")
        compatibility = json.loads(_member_bytes(archive, entries["manifest.json"], label="Docker manifest").decode("utf-8"))
        if (
            not isinstance(compatibility, list)
            or len(compatibility) != 1
            or not isinstance(compatibility[0], dict)
            or compatibility[0].get("Config") != config_member.name
            or compatibility[0].get("Layers")
            != [
                f"blobs/sha256/{_digest(item['digest'], label='base OCI layer digest').removeprefix(_DIGEST_PREFIX)}"
                for item in layers
            ]
            or compatibility[0].get("RepoTags") not in (None, [])
        ):
            raise RuntimeError("base OCI Docker compatibility manifest differs")
    finally:
        archive.close()
    source: dict[str, str] = {}
    if inspection is not None:
        image_id, observed_repo_digest = _validate_inspection(
            expected,
            inspection,
        )
        source = {
            "source_local_image_id": image_id,
            "source_local_repo_digest": observed_repo_digest,
        }
    return {
        "contract": "EvaPinnedBaseImageArchive/v3",
        "archive_sha256": _hash_file(archive_path),
        "archive_size": metadata.st_size,
        "base_image_digest": base_image_digest,
        "base_image_repository": repository,
        "network_used": False,
        "oci_config_digest": config["digest"],
        "oci_root_index_digest": root_index_digest,
        "oci_selected_manifest_digest": selected[0]["digest"],
        "platform": "linux/amd64",
        "status": "sealed_archive_validated",
        **source,
        "nonclaims": [
            "This local export does not prove registry or proxy provenance, signatures, or release eligibility.",
            "No candidate image, B configuration, container, volume, secret, or live service was opened.",
        ],
    }


def _docker_inspect(reference: str) -> Mapping[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference], check=True, capture_output=True, text=True, timeout=30
    )
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("local base image inspection is invalid")
    return value[0]


def _docker_export(reference: str, destination: Path) -> None:
    subprocess.run(
        [
            "docker",
            "image",
            "save",
            "--output",
            str(destination),
            reference,
        ],
        check=True,
        timeout=120,
    )


def materialize_pinned_base_image(
    base_image_digest: str,
    output_parent: Path,
    *,
    repository: str = _DEFAULT_REPOSITORY,
    inspector: Callable[[str], Mapping[str, object]] = _docker_inspect,
    exporter: Callable[[str, Path], None] = _docker_export,
) -> MaterializedBaseImage:
    """Export and validate one already-local image; this never pulls or builds."""

    reference = _reference(base_image_digest, repository)
    inspection = inspector(reference)
    try:
        parent = output_parent.lstat()
    except OSError as error:
        raise RuntimeError("base image output parent is inaccessible") from error
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError("base image output parent must be a regular directory")
    destination_root = Path(tempfile.mkdtemp(prefix="eva2-base-image-", dir=output_parent))
    archive_path = destination_root / "base.oci.tar"
    try:
        exporter(reference, archive_path)
        receipt = validate_pinned_base_image_archive(
            archive_path,
            base_image_digest,
            inspection,
            repository=repository,
        )
        archive_path.chmod(0o444)
        destination_root.chmod(0o500)
        if archive_path.lstat().st_mode & 0o222 or destination_root.lstat().st_mode & 0o222:
            raise RuntimeError("base image export cannot be made read-only")
    except Exception:
        destination_root.chmod(0o700)
        shutil.rmtree(destination_root)
        raise
    return MaterializedBaseImage(archive_path, receipt)


def materialize_pinned_base_image_archive(
    source_path: Path,
    expected_archive_sha256: str,
    base_image_digest: str,
    output_parent: Path,
    *,
    repository: str = _DEFAULT_REPOSITORY,
) -> MaterializedBaseImage:
    """Copy one hash-authorized OCI archive without consulting Docker."""

    _reference(base_image_digest, repository)
    if (
        len(expected_archive_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_archive_sha256)
    ):
        raise RuntimeError("base OCI archive authority must be a sha256 value")
    if not source_path.is_absolute() or ".." in source_path.parts:
        raise RuntimeError("base OCI archive source must be an explicit absolute path")
    try:
        source_metadata = source_path.lstat()
        parent_metadata = output_parent.lstat()
    except OSError as error:
        raise RuntimeError("base OCI archive input is inaccessible") from error
    if (
        stat.S_ISLNK(source_metadata.st_mode)
        or not stat.S_ISREG(source_metadata.st_mode)
        or source_metadata.st_nlink != 1
        or source_metadata.st_uid != os.geteuid()
        or source_metadata.st_mode & 0o022
        or not 0 < source_metadata.st_size <= _MAX_ARCHIVE_BYTES
    ):
        raise RuntimeError("base OCI archive source must be one safe regular file")
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(parent_metadata.st_mode) & 0o077
    ):
        raise RuntimeError("base image output parent must be private")
    destination_root = Path(tempfile.mkdtemp(prefix="eva2-base-image-", dir=output_parent))
    destination_root.chmod(0o700)
    destination = destination_root / "base.oci.tar"
    source_descriptor = -1
    destination_descriptor = -1
    digest = hashlib.sha256()
    try:
        source_descriptor = os.open(
            source_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (source_metadata.st_dev, source_metadata.st_ino, source_metadata.st_size)
        ):
            raise RuntimeError("base OCI archive source changed before copying")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        while block := os.read(source_descriptor, 1024 * 1024):
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise RuntimeError("base OCI archive copy was incomplete")
                view = view[written:]
        final = os.fstat(source_descriptor)
        if (final.st_dev, final.st_ino, final.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            raise RuntimeError("base OCI archive source changed while copying")
        if digest.hexdigest() != expected_archive_sha256:
            raise RuntimeError("base OCI archive bytes differ from their authority")
    except Exception:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
            destination_descriptor = -1
        if source_descriptor >= 0:
            os.close(source_descriptor)
            source_descriptor = -1
        shutil.rmtree(destination_root)
        raise
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
    try:
        receipt = validate_pinned_base_image_archive(
            destination,
            base_image_digest,
            repository=repository,
        )
        if receipt["archive_sha256"] != expected_archive_sha256:
            raise RuntimeError("base OCI archive changed after copying")
        destination.chmod(0o400)
        destination_root.chmod(0o500)
    except Exception:
        destination_root.chmod(0o700)
        shutil.rmtree(destination_root)
        raise
    return MaterializedBaseImage(destination, receipt)


def validate_materialized_base_image(
    materialized: MaterializedBaseImage,
) -> dict[str, object]:
    """Revalidate a prior local export before a later builder consumes it."""

    if not isinstance(materialized, MaterializedBaseImage):
        raise RuntimeError("managed B base image must be materialized")
    receipt = materialized.receipt
    required = {
        "archive_sha256",
        "archive_size",
        "base_image_digest",
        "base_image_repository",
        "contract",
        "network_used",
        "oci_config_digest",
        "oci_root_index_digest",
        "oci_selected_manifest_digest",
        "platform",
        "status",
    }
    if (
        not required.issubset(receipt)
        or receipt.get("contract") != "EvaPinnedBaseImageArchive/v3"
        or receipt.get("status") != "sealed_archive_validated"
        or receipt.get("network_used") is not False
        or receipt.get("platform") != "linux/amd64"
    ):
        raise RuntimeError("managed B base image receipt differs")
    try:
        archive_metadata = materialized.path.lstat()
        parent_metadata = materialized.path.parent.lstat()
    except OSError as error:
        raise RuntimeError("managed B base image is inaccessible") from error
    if (
        stat.S_ISLNK(archive_metadata.st_mode)
        or not stat.S_ISREG(archive_metadata.st_mode)
        or archive_metadata.st_mode & 0o222
        or stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_mode & 0o222
    ):
        raise RuntimeError("managed B base image must remain read-only")
    validated = validate_pinned_base_image_archive(
        materialized.path,
        str(receipt["base_image_digest"]),
        repository=str(receipt["base_image_repository"]),
    )
    for key in (
        "archive_sha256",
        "archive_size",
        "base_image_digest",
        "base_image_repository",
        "oci_config_digest",
        "oci_root_index_digest",
        "oci_selected_manifest_digest",
        "platform",
    ):
        if receipt[key] != validated[key]:
            raise RuntimeError("managed B base image receipt no longer matches its archive")
    source_image_id = receipt.get("source_local_image_id")
    source_repo_digest = receipt.get("source_local_repo_digest")
    if source_image_id is not None or source_repo_digest is not None:
        expected_reference = (
            f"{receipt['base_image_repository']}@{receipt['base_image_digest']}"
        )
        if (
            not isinstance(source_image_id, str)
            or _digest(source_image_id, label="source local base image ID")
            != source_image_id
            or source_repo_digest not in _expected_repo_digests(expected_reference)
        ):
            raise RuntimeError("managed B base image source inspection differs")
        validated["source_local_image_id"] = source_image_id
        validated["source_local_repo_digest"] = source_repo_digest
    return validated


def _layout_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("base OCI layout contains a link")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise RuntimeError("base OCI layout contains a non-regular file")
        entries.append(
            {
                "path": relative,
                "sha256": _hash_file(path),
                "size": metadata.st_size,
            }
        )
    return entries


def _layout_tree_sha256(entries: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _validate_layout_directories(root: Path) -> None:
    expected = {"blobs", "blobs/sha256"}
    actual: set[str] = set()
    for path in root.rglob("*"):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        actual.add(relative)
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
            or relative == "ingest"
            and any(path.iterdir())
        ):
            raise RuntimeError("base OCI layout contains an unsafe directory")
    if actual not in (expected, expected | {"ingest"}):
        raise RuntimeError("base OCI layout directory set differs")


def materialize_base_image_layout(
    materialized: MaterializedBaseImage,
    output_parent: Path,
    *,
    role: str,
) -> MaterializedBaseImageLayout:
    """Extract one already-validated archive as a sealed OCI layout."""

    receipt = validate_materialized_base_image(materialized)
    if role not in {"build", "runtime_libraries", "runtime"}:
        raise RuntimeError("base OCI layout role is invalid")
    root = output_parent / f"{role}-oci-layout"
    root.mkdir(mode=0o700)
    try:
        entries, archive = _archive_entries(materialized.path)
        try:
            for name, member in entries.items():
                if name == "manifest.json":
                    continue
                destination = root / name
                if member.isdir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError("base OCI layout source is unreadable")
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                    0o400,
                )
                try:
                    remaining = member.size
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise RuntimeError("base OCI layout extraction was incomplete")
                        view = memoryview(block)
                        while view:
                            written = os.write(descriptor, view)
                            if written <= 0:
                                raise RuntimeError("base OCI layout extraction was incomplete")
                            view = view[written:]
                        remaining -= len(block)
                    if source.read(1):
                        raise RuntimeError("base OCI layout entry size differs")
                finally:
                    os.close(descriptor)
        finally:
            archive.close()
        actual = _layout_entries(root)
        tree_sha256 = _layout_tree_sha256(actual)
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o500)
        root.chmod(0o700)
    except Exception:
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o700)
        root.chmod(0o700)
        shutil.rmtree(root)
        raise
    layout_receipt = {
        "archive_sha256": receipt["archive_sha256"],
        "base_image_digest": receipt["base_image_digest"],
        "base_image_repository": receipt["base_image_repository"],
        "contract": "EvaPinnedBaseImageLayout/v1",
        "entries": actual,
        "layout_tree_sha256": tree_sha256,
        "oci_config_digest": receipt["oci_config_digest"],
        "oci_selected_manifest_digest": receipt["oci_selected_manifest_digest"],
        "platform": "linux/amd64",
        "role": role,
        "content_read_only": True,
        "status": "materialized_private_store",
    }
    return MaterializedBaseImageLayout(root, layout_receipt)


def validate_materialized_base_image_layout(
    layout: MaterializedBaseImageLayout,
) -> dict[str, object]:
    if not isinstance(layout, MaterializedBaseImageLayout):
        raise RuntimeError("managed B base image layout must be materialized")
    receipt = layout.receipt
    metadata = layout.path.lstat()
    actual = _layout_entries(layout.path)
    _validate_layout_directories(layout.path)
    if (
        receipt.get("contract") != "EvaPinnedBaseImageLayout/v1"
        or receipt.get("status") != "materialized_private_store"
        or receipt.get("content_read_only") is not True
        or receipt.get("platform") != "linux/amd64"
        or receipt.get("entries") != actual
        or receipt.get("layout_tree_sha256") != _layout_tree_sha256(actual)
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeError("managed B base image layout no longer matches its receipt")
    return receipt


__all__ = [
    "MaterializedBaseImage",
    "MaterializedBaseImageLayout",
    "materialize_pinned_base_image",
    "materialize_pinned_base_image_archive",
    "materialize_base_image_layout",
    "validate_materialized_base_image_layout",
    "validate_materialized_base_image",
    "validate_pinned_base_image_archive",
]
