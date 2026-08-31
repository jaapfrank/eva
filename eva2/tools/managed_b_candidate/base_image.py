"""Local, offline validation of the pinned Python base-image export.

The registry digest is bound to a local Docker inspection before export.  The
result is a local OCI archive, not evidence of registry provenance or release.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
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


def _validate_inspection(expected: str, inspection: Mapping[str, object]) -> tuple[str, str]:
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
    inspection: Mapping[str, object],
    *,
    repository: str = _DEFAULT_REPOSITORY,
) -> dict[str, object]:
    """Validate an existing local OCI archive without Docker or network access."""

    expected = _reference(base_image_digest, repository)
    image_id, observed_repo_digest = _validate_inspection(expected, inspection)
    try:
        metadata = archive_path.lstat()
    except OSError as error:
        raise RuntimeError("base OCI archive is inaccessible") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("base OCI archive must be a regular non-symlink file")
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
        if image_id != base_image_digest:
            raise RuntimeError("local base image ID differs from the pinned source digest")
        config_json = _json(_member_bytes(archive, config_member, label="base OCI config"), label="base OCI config")
        if config_json.get("os") != "linux" or config_json.get("architecture") != "amd64":
            raise RuntimeError("base OCI config platform differs")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise RuntimeError("base OCI manifest layers are invalid")
        for item in layers:
            _required_blob(entries, archive, _descriptor(item, label="base OCI layer"), label="base OCI layer")
        compatibility = json.loads(_member_bytes(archive, entries["manifest.json"], label="Docker manifest").decode("utf-8"))
        if not isinstance(compatibility, list) or len(compatibility) != 1 or compatibility[0].get("Config") != config_member.name or compatibility[0].get("Layers") != [f"blobs/sha256/{_digest(item['digest'], label='base OCI layer digest').removeprefix(_DIGEST_PREFIX)}" for item in layers]:
            raise RuntimeError("base OCI Docker compatibility manifest differs")
    finally:
        archive.close()
    return {
        "contract": "EvaPinnedBaseImageExport/v2",
        "archive_sha256": _hash_file(archive_path),
        "archive_size": metadata.st_size,
        "base_image_digest": base_image_digest,
        "base_image_repository": repository,
        "local_image_id": image_id,
        "local_repo_digest": observed_repo_digest,
        "network_used": False,
        "oci_config_digest": config["digest"],
        "oci_root_index_digest": root_index_digest,
        "oci_selected_manifest_digest": selected[0]["digest"],
        "platform": "linux/amd64",
        "status": "local_export_validated",
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
    _validate_inspection(reference, inspection)
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
        "local_image_id",
        "local_repo_digest",
        "network_used",
        "oci_config_digest",
        "oci_root_index_digest",
        "oci_selected_manifest_digest",
        "platform",
        "status",
    }
    if (
        not required.issubset(receipt)
        or receipt.get("contract") != "EvaPinnedBaseImageExport/v2"
        or receipt.get("status") != "local_export_validated"
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
    inspection = {
        "Architecture": "amd64",
        "Id": receipt["local_image_id"],
        "Os": "linux",
        "RepoDigests": [receipt["local_repo_digest"]],
    }
    validated = validate_pinned_base_image_archive(
        materialized.path,
        str(receipt["base_image_digest"]),
        inspection,
        repository=str(receipt["base_image_repository"]),
    )
    for key in (
        "archive_sha256",
        "archive_size",
        "base_image_digest",
        "base_image_repository",
        "local_image_id",
        "local_repo_digest",
        "oci_config_digest",
        "oci_root_index_digest",
        "oci_selected_manifest_digest",
        "platform",
    ):
        if receipt[key] != validated[key]:
            raise RuntimeError("managed B base image receipt no longer matches its archive")
    return validated


__all__ = [
    "MaterializedBaseImage",
    "materialize_pinned_base_image",
    "validate_materialized_base_image",
    "validate_pinned_base_image_archive",
]
