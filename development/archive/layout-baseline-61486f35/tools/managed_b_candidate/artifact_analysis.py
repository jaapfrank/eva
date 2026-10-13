"""Create SBOM and vulnerability evidence from one candidate image export.

The candidate is exported exactly once.  Pinned Syft and Trivy containers read
that same immutable archive without network access.  This module is a
candidate-only gate: it never tags, pushes, signs, starts, or promotes an image.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from tools.check_container_vulnerabilities import TRIVY_IMAGE, finding_counts

from .candidate_image import CandidateImage, validate_offline_candidate_image
from .private_receipt import canonical_json, write_new_private_json


SYFT_VERSION = "1.51.0"
SYFT_IMAGE = (
    "ghcr.io/anchore/syft@sha256:"
    "678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0"
)
TRIVY_VERSION = "0.74.0"
TOOL_AUTHORITY_PATH = "release/candidate-artifact-tool-authority-v1.json"
RB005_ANCHORS: Mapping[str, str] = MappingProxyType(
    {
        "archive_sha256": "108406414bf56d0d99c436a19387c39902605ab8df0820e304ac1e486a459acc",
        "candidate_receipt_sha256": "3de27f483266c1d7fb63e209eb0f9facaface8dfb7fca7403302397c3c3c3c1b",
        "image_id": "sha256:3a4f65544be8b0b4e543ba5148b73775b35c40ee5ab4a49dd55eec9b724899f1",
        "input_authority_sha256": "d9e51cc588a21a186bff68afc48e71978319761cbe9814c5b6a5a3da4d2f4a74",
        "private_receipt_sha256": "848c236c76abe66e4c2db1d00324aa3df6987a98d4cfc41e9198de0a59261a4f",
        "public_evidence_path": "evidence/product/RB-005-two-builder-reproducibility-2026-09-01.json",
        "public_evidence_sha256": "77ad4553ce10e20fdfc1e80c92219b61528d23c6e2d641f3771e962a465c4ed3",
        "release_authority_sha256": "20323cc166d5b7055c43dc94a0d5e09c47ab729036e479c166768a2a6e841020",
        "source_commit": "45d2ff6d61793809a514c01b3a3c000ce1632da6",
    }
)
LICENSE_INVENTORY_SHA256 = (
    "0608e0bb2f59f3d05433a17b2275822987020b2d8ae2d8c36af71c02a39007aa"
)
SBOM_POLICY_SHA256 = (
    "1b954e5d4bfaef341b1e2f0611f622b98bdae16e81194453ea6223e6b7d408bd"
)
TRIVY_CACHE_AUTHORITY_SHA256 = (
    "a2c300c5a1d85b95be4a3eaed51004b3f2e8d7f232b9bc0226844e45b1ae8585"
)
TOOL_AUTHORITY_SHA256 = (
    "a981113931a6a243f112319f819c8f1974f5679a20944cf5652c4c90fd387297"
)
_MAX_JSON_BYTES = 256 * 1024 * 1024
_SHA256 = frozenset("0123456789abcdef")
_TRIVY_CACHE_FILES = frozenset({"db/metadata.json", "db/trivy.db"})
_REQUIRED_PACKAGE_FIELDS = (
    "name",
    "versionInfo",
    "downloadLocation",
    "licenseDeclared",
    "licenseConcluded",
)
_REQUIRED_RELATIONSHIPS = ("DESCRIBES", "CONTAINS", "DEPENDENCY_OF")
_SPDX_ID = re.compile(r"SPDXRef-[A-Za-z0-9.-]+\Z")
_CHECKSUM = re.compile(r"[0-9A-Fa-f]{64}\Z")


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class VerifiedTrivyCache:
    path: Path
    authority_sha256: str
    database_updated_at: str
    tree_sha256: str


@dataclass(frozen=True, slots=True)
class CandidateArtifactAnalysis:
    path: Path
    receipt: dict[str, object]


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _canonical(value: object) -> bytes:
    return canonical_json(value)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise RuntimeError(f"{label} must be a lowercase sha256")
    return value


def _json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_JSON_BYTES:
        raise RuntimeError(f"{label} size is invalid")
    try:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _safe_evidence_file(
    path: Path, *, label: str, private: bool, expected_suffix: str | None = None
) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{label} path must be explicit and absolute")
    if expected_suffix is not None:
        suffix = Path(expected_suffix).parts
        if len(path.parts) < len(suffix) or path.parts[-len(suffix) :] != suffix:
            raise RuntimeError(f"{label} path differs")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{label} is inaccessible") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > _MAX_JSON_BYTES
        or metadata.st_uid != os.geteuid()
        or (mode != 0o600 if private else bool(mode & 0o022))
    ):
        raise RuntimeError(f"{label} is not one safe immutable input")
    return path.read_bytes()


def validate_rb005_candidate(
    candidate_receipt: dict[str, object],
    *,
    public_evidence_path: Path,
    private_receipt_path: Path,
) -> dict[str, str]:
    """Bind one validated local candidate to the exact reviewed RB-005 proof."""

    public_raw = _safe_evidence_file(
        public_evidence_path,
        label="public RB-005 evidence",
        private=False,
        expected_suffix=RB005_ANCHORS["public_evidence_path"],
    )
    public = _json(public_raw, label="public RB-005 evidence")
    physical = _object(public.get("physicalRun"), label="public RB-005 physical run")
    public_candidate = _object(
        public.get("candidate"), label="public RB-005 candidate"
    )
    builder_b = _object(physical.get("builderB"), label="public RB-005 builder B")
    private_raw = _safe_evidence_file(
        private_receipt_path, label="private RB-005 receipt", private=True
    )
    private = _json(private_raw, label="private RB-005 receipt")
    if (
        public.get("contract") != "EvaWorkboardTaskEvidence/v1"
        or public.get("taskId") != "RB-005"
        or public.get("networkUsed") is not False
        or physical.get("resultContract") != "EvaTwoBuilderReproducibilityReceipt/v2"
        or physical.get("status") != "passed"
        or physical.get("resultReceiptSha256")
        != RB005_ANCHORS["private_receipt_sha256"]
        or builder_b.get("taskId") != "RB-004"
        or private.get("contract") != "EvaTwoBuilderReproducibilityReceipt/v2"
        or private.get("status") != "passed"
    ):
        raise RuntimeError("public RB-005 evidence is not the passed builder-B proof")
    observed = {
        "archive_sha256": public_candidate.get("archiveSha256"),
        "candidate_receipt_sha256": builder_b.get("candidateReceiptSha256"),
        "image_id": public_candidate.get("imageId"),
        "input_authority_sha256": public.get("inputAuthoritySha256"),
        "private_receipt_sha256": _sha256(private_raw),
        "public_evidence_path": RB005_ANCHORS["public_evidence_path"],
        "public_evidence_sha256": _sha256(public_raw),
        "release_authority_sha256": public.get("releaseAuthoritySha256"),
        "source_commit": public.get("sourceCommit"),
    }
    if observed != RB005_ANCHORS:
        raise RuntimeError("RB-005 evidence anchors differ")
    if {
        "archive_sha256": candidate_receipt.get("candidate_image_archive_sha256"),
        "candidate_receipt_sha256": _sha256(_canonical(candidate_receipt)),
        "image_id": candidate_receipt.get("candidate_image_id"),
        "source_commit": candidate_receipt.get("candidate_source_commit"),
    } != {key: RB005_ANCHORS[key] for key in (
        "archive_sha256",
        "candidate_receipt_sha256",
        "image_id",
        "source_commit",
    )}:
        raise RuntimeError("local candidate differs from passed RB-005 evidence")
    return dict(RB005_ANCHORS)


def _private_parent(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("candidate artifact output parent is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RuntimeError("candidate artifact output parent must be private")


def _safe_mount_path(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{label} mount path is inaccessible") from error
    text = str(resolved)
    if (
        not resolved.is_absolute()
        or "," in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise RuntimeError(f"{label} mount path is unsafe")
    return resolved


def _local_docker_socket() -> None:
    host = os.environ.get("DOCKER_HOST")
    if host is None:
        return
    socket_path = host.removeprefix("unix://")
    if (
        not host.startswith("unix://")
        or not socket_path.startswith("/")
        or "," in socket_path
        or any(ord(character) < 32 or ord(character) == 127 for character in host)
    ):
        raise RuntimeError("candidate analysis requires a local Unix Docker socket")


def _safe_relative(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Trivy cache authority path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("Trivy cache authority path is invalid")
    return path.as_posix()


def _tool_authority(raw: bytes) -> dict[str, Any]:
    value = _json(raw, label="candidate artifact tool authority")
    expected = {
        "contract": "EvaCandidateArtifactToolAuthority/v1",
        "network": "none",
        "status": "candidate_only_pinned_tools",
        "syft": {
            "checkForAppUpdate": False,
            "format": "spdx-json",
            "image": SYFT_IMAGE,
            "version": SYFT_VERSION,
        },
        "trivy": {
            "cacheBackend": "memory",
            "image": TRIVY_IMAGE,
            "skipDatabaseUpdate": True,
            "version": TRIVY_VERSION,
        },
    }
    if value != expected or raw != json.dumps(
        expected, ensure_ascii=False, indent=2, sort_keys=False
    ).encode("utf-8") + b"\n":
        raise RuntimeError("candidate artifact tool authority differs")
    return value


def _cache_entries(root: Path) -> dict[str, tuple[str, int]]:
    entries: dict[str, tuple[str, int]] = {}
    expected_directories = {".", "db"}
    actual_directories: set[str] = set()
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(root).as_posix()
        actual_directories.add(relative_directory)
        metadata = directory_path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o222
        ):
            raise RuntimeError("Trivy cache directories must be immutable")
        for name in subdirectories:
            nested = directory_path / name
            nested_metadata = nested.lstat()
            if stat.S_ISLNK(nested_metadata.st_mode) or not stat.S_ISDIR(
                nested_metadata.st_mode
            ):
                raise RuntimeError("Trivy cache contains an unsafe directory")
        for name in files:
            path = directory_path / name
            file_metadata = path.lstat()
            if (
                stat.S_ISLNK(file_metadata.st_mode)
                or not stat.S_ISREG(file_metadata.st_mode)
                or file_metadata.st_nlink != 1
                or file_metadata.st_mode & 0o222
            ):
                raise RuntimeError("Trivy cache contains an unsafe file")
            relative = path.relative_to(root).as_posix()
            entries[relative] = (_file_sha256(path), file_metadata.st_size)
    if actual_directories != expected_directories or set(entries) != _TRIVY_CACHE_FILES:
        raise RuntimeError("Trivy cache file set differs")
    return entries


def validate_trivy_cache(
    cache: Path, authority_raw: bytes, tool_authority_raw: bytes
) -> VerifiedTrivyCache:
    """Bind one immutable two-file Trivy database cache to its authority."""

    _tool_authority(tool_authority_raw)
    if _sha256(authority_raw) != TRIVY_CACHE_AUTHORITY_SHA256:
        raise RuntimeError("Trivy cache authority is not the pinned authority")
    _safe_mount_path(cache, label="Trivy cache")
    authority = _json(authority_raw, label="Trivy cache authority")
    if set(authority) != {
        "contract",
        "databaseUpdatedAt",
        "files",
        "scannerImage",
        "scannerVersion",
    }:
        raise RuntimeError("Trivy cache authority fields differ")
    if (
        authority.get("contract") != "EvaTrivyCacheAuthority/v1"
        or authority.get("scannerImage") != TRIVY_IMAGE
        or authority.get("scannerVersion") != TRIVY_VERSION
        or not isinstance(authority.get("databaseUpdatedAt"), str)
        or not authority["databaseUpdatedAt"]
        or len(authority["databaseUpdatedAt"]) > 128
        or not isinstance(authority.get("files"), list)
    ):
        raise RuntimeError("Trivy cache authority differs")
    expected: dict[str, tuple[str, int]] = {}
    for row in authority["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            raise RuntimeError("Trivy cache authority file is invalid")
        path = _safe_relative(row["path"])
        digest = _digest(row["sha256"], label="Trivy cache file digest")
        size = row["size"]
        if (
            path in expected
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
        ):
            raise RuntimeError("Trivy cache authority file is invalid")
        expected[path] = (digest, size)
    if set(expected) != _TRIVY_CACHE_FILES:
        raise RuntimeError("Trivy cache authority file set differs")
    actual = _cache_entries(cache)
    if actual != expected:
        raise RuntimeError("Trivy cache bytes differ from authority")
    metadata = _json(
        (cache / "db/metadata.json").read_bytes(), label="Trivy cache metadata"
    )
    if metadata.get("UpdatedAt") != authority["databaseUpdatedAt"]:
        raise RuntimeError("Trivy cache update time differs from authority")
    tree = [
        {"path": path, "sha256": digest, "size": size}
        for path, (digest, size) in sorted(actual.items())
    ]
    return VerifiedTrivyCache(
        cache.resolve(strict=True),
        _sha256(authority_raw),
        authority["databaseUpdatedAt"],
        _sha256(_canonical(tree)),
    )


def _container_prefix(*, archive: Path) -> list[str]:
    _local_docker_socket()
    archive_parent = _safe_mount_path(archive.parent, label="candidate archive")
    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=2g",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "3g",
        "--cpus",
        "1",
        "--mount",
        f"type=bind,src={archive_parent},dst=/archive,readonly",
    ]


def syft_command(*, archive: Path) -> list[str]:
    return [
        *_container_prefix(archive=archive),
        "--env",
        "SYFT_CHECK_FOR_APP_UPDATE=false",
        "--entrypoint",
        "/syft",
        SYFT_IMAGE,
        "scan",
        f"docker-archive:/archive/{archive.name}",
        "--output",
        "spdx-json",
    ]


def trivy_command_for_archive(*, archive: Path, cache: VerifiedTrivyCache) -> list[str]:
    cache_path = _safe_mount_path(cache.path, label="Trivy cache")
    return [
        *_container_prefix(archive=archive),
        "--env",
        "TRIVY_CACHE_DIR=/cache",
        "--mount",
        f"type=bind,src={cache_path},dst=/cache,readonly",
        "--entrypoint",
        "trivy",
        TRIVY_IMAGE,
        "--cache-dir",
        "/cache",
        "image",
        "--skip-db-update",
        "--cache-backend",
        "memory",
        "--input",
        f"/archive/{archive.name}",
        "--scanners",
        "vuln",
        "--severity",
        "HIGH,CRITICAL",
        "--format",
        "json",
    ]


def _validate_spdx_document(raw: bytes) -> dict[str, Any]:
    document = _json(raw, label="Syft SPDX document")
    namespace = document.get("documentNamespace")
    parsed = urlparse(namespace) if isinstance(namespace, str) else None
    creation = document.get("creationInfo")
    creators = creation.get("creators") if isinstance(creation, dict) else None
    if (
        document.get("spdxVersion") != "SPDX-2.3"
        or document.get("SPDXID") != "SPDXRef-DOCUMENT"
        or parsed is None
        or not parsed.scheme
        or len(namespace) > 512
        or not isinstance(creators, list)
        or f"Tool: syft-{SYFT_VERSION}" not in creators
    ):
        raise RuntimeError("Syft SPDX document identity differs")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise RuntimeError("Syft SPDX package set is empty or invalid")
    known_ids = {"SPDXRef-DOCUMENT"}
    for package in packages:
        if not isinstance(package, dict):
            raise RuntimeError("Syft SPDX package is invalid")
        package_id = package.get("SPDXID")
        if (
            not isinstance(package_id, str)
            or _SPDX_ID.fullmatch(package_id) is None
            or package_id in known_ids
            or any(
                not isinstance(package.get(field), str)
                or not package[field].strip()
                for field in _REQUIRED_PACKAGE_FIELDS
            )
        ):
            raise RuntimeError("Syft SPDX package identity or fields are invalid")
        known_ids.add(package_id)
        checksums = package.get("checksums")
        if checksums is not None and (
            not isinstance(checksums, list)
            or not checksums
            or any(
                not isinstance(checksum, dict)
                or checksum.get("algorithm") != "SHA256"
                or not isinstance(checksum.get("checksumValue"), str)
                or _CHECKSUM.fullmatch(checksum["checksumValue"]) is None
                for checksum in checksums
            )
        ):
            raise RuntimeError("Syft SPDX package checksum is invalid")
    for collection_name in ("files", "snippets"):
        if collection_name not in document:
            continue
        elements = document[collection_name]
        if not isinstance(elements, list):
            raise RuntimeError(f"Syft SPDX {collection_name} are invalid")
        for element in elements:
            element_id = element.get("SPDXID") if isinstance(element, dict) else None
            if (
                not isinstance(element_id, str)
                or _SPDX_ID.fullmatch(element_id) is None
                or element_id in known_ids
            ):
                raise RuntimeError(f"Syft SPDX {collection_name} ID is invalid")
            known_ids.add(element_id)
    relationships = document.get("relationships")
    if not isinstance(relationships, list):
        raise RuntimeError("Syft SPDX relationships are invalid")
    relationship_types: set[str] = set()
    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise RuntimeError("Syft SPDX relationship is invalid")
        source = relationship.get("spdxElementId")
        target = relationship.get("relatedSpdxElement")
        kind = relationship.get("relationshipType")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not isinstance(kind, str)
            or not kind.strip()
            or source not in known_ids
            or target not in known_ids
        ):
            raise RuntimeError("Syft SPDX relationship is invalid")
        relationship_types.add(kind)
    if not set(_REQUIRED_RELATIONSHIPS).issubset(relationship_types):
        raise RuntimeError("Syft SPDX required relationships are absent")
    return document


def validate_spdx(raw: bytes, policy_raw: bytes) -> dict[str, Any]:
    """Validate the release-selected SPDX subset before hashing it."""

    if _sha256(policy_raw) != SBOM_POLICY_SHA256:
        raise RuntimeError("SBOM policy is not the pinned policy")
    policy = _json(policy_raw, label="SBOM policy")
    if (
        policy.get("contract") != "Eva2SbomPolicy/v1"
        or policy.get("authoritativeFormat") != "SPDX-2.3-json"
        or policy.get("requiredPackageFields") != list(_REQUIRED_PACKAGE_FIELDS)
        or policy.get("requiredRelationships") != list(_REQUIRED_RELATIONSHIPS)
        or policy.get("requiredHashes") != ["SHA256"]
        or policy.get("packageChecksumPolicy")
        != "optional_per_SPDX_2.3_validate_when_present_never_fabricate"
    ):
        raise RuntimeError("SBOM policy differs")
    return _validate_spdx_document(raw)


def _trivy_finding_counts(payload: dict[str, Any]) -> dict[str, int]:
    results = payload.get("Results")
    if not isinstance(results, list) or not results:
        raise RuntimeError("raw Trivy results are empty or invalid")
    for result in results:
        if not isinstance(result, dict) or any(
            not isinstance(result.get(field), str) or not result[field].strip()
            for field in ("Target", "Class", "Type")
        ):
            raise RuntimeError("raw Trivy result item is invalid")
        vulnerabilities = result.get("Vulnerabilities")
        if vulnerabilities is None:
            continue
        if not isinstance(vulnerabilities, list):
            raise RuntimeError("raw Trivy vulnerabilities are invalid")
        for vulnerability in vulnerabilities:
            if (
                not isinstance(vulnerability, dict)
                or vulnerability.get("Severity") not in {"HIGH", "CRITICAL"}
            ):
                raise RuntimeError("raw Trivy vulnerability severity is invalid")
    try:
        return finding_counts(payload)
    except ValueError as error:
        raise RuntimeError("raw Trivy result is invalid") from error


def _write(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("candidate artifact write did not progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(0o444)


def _readonly_file(path: Path, *, maximum: int) -> os.stat_result:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o222
        or not 0 < metadata.st_size <= maximum
    ):
        raise RuntimeError("candidate artifact file is unsafe")
    return metadata


def _regular_readonly(path: Path, *, maximum: int = _MAX_JSON_BYTES) -> bytes:
    _readonly_file(path, maximum=maximum)
    return path.read_bytes()


def _remove_fresh_tree(root: Path) -> None:
    if root.exists():
        for directory, _subdirectories, _files in os.walk(root, topdown=False):
            Path(directory).chmod(0o700)
        shutil.rmtree(root)


def analyze_candidate_image(
    candidate: CandidateImage,
    *,
    rb005_public_evidence_path: Path,
    rb005_private_receipt_path: Path,
    output_parent: Path,
    trivy_cache: Path,
    trivy_cache_authority_raw: bytes,
    tool_authority_raw: bytes,
    sbom_policy_raw: bytes,
    license_inventory_raw: bytes,
    runner: Runner = _run,
) -> CandidateArtifactAnalysis:
    """Export once, run both pinned tools, and retain immutable raw evidence."""

    rb005 = validate_rb005_candidate(
        candidate.receipt,
        public_evidence_path=rb005_public_evidence_path,
        private_receipt_path=rb005_private_receipt_path,
    )
    if candidate.image_id != rb005["image_id"]:
        raise RuntimeError("candidate image object differs from passed RB-005 evidence")
    _local_docker_socket()
    candidate_receipt = validate_offline_candidate_image(candidate, runner=runner)
    if (
        candidate_receipt != candidate.receipt
        or _sha256(_canonical(candidate_receipt))
        != rb005["candidate_receipt_sha256"]
    ):
        raise RuntimeError("validated candidate receipt differs from RB-005")
    _tool_authority(tool_authority_raw)
    cache = validate_trivy_cache(
        trivy_cache, trivy_cache_authority_raw, tool_authority_raw
    )
    inventory = _json(license_inventory_raw, label="license inventory")
    inventory_claims = inventory.get("claims")
    if (
        _sha256(license_inventory_raw) != LICENSE_INVENTORY_SHA256
        or inventory.get("contract") != "Eva2PythonLicenseInventory/v1"
        or not isinstance(inventory_claims, dict)
        or inventory_claims.get("everyLockedPackageInventoried") is not True
    ):
        raise RuntimeError("license inventory differs")
    _private_parent(output_parent)
    _safe_mount_path(output_parent, label="candidate artifact output")
    root = Path(tempfile.mkdtemp(prefix="eva2-candidate-artifacts-", dir=output_parent))
    root.chmod(0o700)
    image_directory = root / "image"
    reports = root / "reports"
    image_directory.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    archive = image_directory / "image.tar"
    try:
        exported = runner(
            ["docker", "save", candidate.image_id, "--output", str(archive)], 180
        )
        if exported.returncode != 0:
            raise RuntimeError("candidate image export failed")
        archive_metadata = archive.lstat()
        if (
            stat.S_ISLNK(archive_metadata.st_mode)
            or not stat.S_ISREG(archive_metadata.st_mode)
            or archive_metadata.st_nlink != 1
            or archive_metadata.st_size <= 0
        ):
            raise RuntimeError("candidate image archive is unsafe")
        archive.chmod(0o444)
        image_directory.chmod(0o555)
        archive_sha256 = _file_sha256(archive)

        syft = runner(syft_command(archive=archive), 300)
        if syft.returncode != 0:
            raise RuntimeError("pinned Syft analysis failed")
        sbom_raw = syft.stdout.encode("utf-8")
        sbom = validate_spdx(sbom_raw, sbom_policy_raw)
        if _file_sha256(archive) != archive_sha256:
            raise RuntimeError("candidate image archive changed during Syft analysis")

        trivy = runner(trivy_command_for_archive(archive=archive, cache=cache), 300)
        if trivy.returncode != 0:
            raise RuntimeError("pinned Trivy analysis failed")
        trivy_raw = trivy.stdout.encode("utf-8")
        trivy_payload = _json(trivy_raw, label="raw Trivy result")
        counts = _trivy_finding_counts(trivy_payload)
        if _file_sha256(archive) != archive_sha256:
            raise RuntimeError("candidate image archive changed during Trivy analysis")
        verified_after = validate_trivy_cache(
            trivy_cache, trivy_cache_authority_raw, tool_authority_raw
        )
        if verified_after != cache:
            raise RuntimeError("Trivy cache authority changed during analysis")

        sbom_path = reports / "sbom.spdx.json"
        trivy_path = reports / "trivy.raw.json"
        binding_path = reports / "sbom-binding.json"
        _write(sbom_path, sbom_raw)
        _write(trivy_path, trivy_raw)
        binding = {
            "artifact": {
                "id": candidate.image_id,
                "sha256": candidate.image_id.removeprefix("sha256:"),
                "type": "image",
            },
            "contract": "Eva2SbomBinding/v1",
            "documentNamespace": sbom["documentNamespace"],
            "documentSha256": _sha256(sbom_raw),
            "format": "SPDX-2.3-json",
            "generator": SYFT_IMAGE,
            "licenseInventorySha256": _sha256(license_inventory_raw),
            "vulnerabilityScanSha256": _sha256(trivy_raw),
        }
        binding_raw = _canonical(binding)
        _write(binding_path, binding_raw)
        receipt = {
            "candidate_image_id": candidate.image_id,
            "candidate_receipt_sha256": rb005["candidate_receipt_sha256"],
            "contract": "EvaManagedBCandidateArtifactAnalysis/v1",
            "files": {
                "image/image.tar": archive_sha256,
                "reports/sbom-binding.json": _sha256(binding_raw),
                "reports/sbom.spdx.json": _sha256(sbom_raw),
                "reports/trivy.raw.json": _sha256(trivy_raw),
            },
            "findings": counts,
            "license_inventory_sha256": _sha256(license_inventory_raw),
            "network_used": False,
            "nonclaims": [
                "The candidate was not tagged, pushed, signed, started, promoted, or attached to Compose.",
                "An SPDX document and clean scan are candidate evidence, not release approval or proof that no vulnerability exists.",
            ],
            "sbom": {
                "format": "SPDX-2.3-json",
                "generator_image": SYFT_IMAGE,
                "generator_version": SYFT_VERSION,
                "policy_sha256": _sha256(sbom_policy_raw),
            },
            "rb005": rb005,
            "status": (
                "passed_candidate_artifacts"
                if counts == {"HIGH": 0, "CRITICAL": 0}
                else "rejected_vulnerabilities"
            ),
            "tool_authority_sha256": _sha256(tool_authority_raw),
            "trivy": {
                "cache_authority_sha256": cache.authority_sha256,
                "cache_tree_sha256": cache.tree_sha256,
                "database_updated_at": cache.database_updated_at,
                "scanner_image": TRIVY_IMAGE,
                "scanner_version": TRIVY_VERSION,
            },
        }
        receipt_path = reports / "analysis-receipt.json"
        write_new_private_json(
            receipt_path, receipt, label="candidate artifact analysis receipt"
        )
        receipt_path.chmod(0o444)
        reports.chmod(0o555)
        root.chmod(0o500)
        return CandidateArtifactAnalysis(root, receipt)
    except Exception:
        _remove_fresh_tree(root)
        raise


def validate_candidate_artifact_analysis(
    analysis: CandidateArtifactAnalysis,
) -> dict[str, object]:
    """Revalidate retained bytes and reject any non-clean candidate."""

    if not isinstance(analysis, CandidateArtifactAnalysis):
        raise RuntimeError("candidate artifact analysis is invalid")
    root = analysis.path
    try:
        metadata = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("candidate artifact analysis is inaccessible") from error
    if (
        not root.is_absolute()
        or ".." in root.parts
        or resolved != root
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o222
    ):
        raise RuntimeError("candidate artifact analysis directory is unsafe")

    expected_directories = {".", "image", "reports"}
    expected_files = {
        "image/image.tar",
        "reports/analysis-receipt.json",
        "reports/sbom-binding.json",
        "reports/sbom.spdx.json",
        "reports/trivy.raw.json",
    }
    actual_directories: set[str] = set()
    actual_files: set[str] = set()
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(root).as_posix()
        actual_directories.add(relative_directory)
        directory_metadata = directory_path.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_mode & 0o222
        ):
            raise RuntimeError("candidate artifact analysis directory is unsafe")
        for name in subdirectories:
            nested = directory_path / name
            nested_metadata = nested.lstat()
            if stat.S_ISLNK(nested_metadata.st_mode) or not stat.S_ISDIR(
                nested_metadata.st_mode
            ):
                raise RuntimeError("candidate artifact analysis tree is unsafe")
        for name in files:
            path = directory_path / name
            _readonly_file(
                path,
                maximum=(
                    4 * 1024 * 1024 * 1024
                    if path == root / "image/image.tar"
                    else _MAX_JSON_BYTES
                ),
            )
            actual_files.add(path.relative_to(root).as_posix())
    if actual_directories != expected_directories or actual_files != expected_files:
        raise RuntimeError("candidate artifact analysis file set differs")

    retained_raw = _regular_readonly(root / "reports/analysis-receipt.json")
    retained = _json(retained_raw, label="candidate artifact receipt")
    if retained_raw != _canonical(retained) or retained != analysis.receipt:
        raise RuntimeError("candidate artifact retained receipt differs")
    receipt = retained
    if set(receipt) != {
        "candidate_image_id",
        "candidate_receipt_sha256",
        "contract",
        "files",
        "findings",
        "license_inventory_sha256",
        "network_used",
        "nonclaims",
        "rb005",
        "sbom",
        "status",
        "tool_authority_sha256",
        "trivy",
    }:
        raise RuntimeError("candidate artifact receipt fields differ")
    files = _object(receipt.get("files"), label="candidate artifact files")
    expected_receipt_files = expected_files - {"reports/analysis-receipt.json"}
    if set(files) != expected_receipt_files:
        raise RuntimeError("candidate artifact receipt file set differs")
    if (
        receipt.get("contract") != "EvaManagedBCandidateArtifactAnalysis/v1"
        or receipt.get("status") != "passed_candidate_artifacts"
        or receipt.get("network_used") is not False
        or receipt.get("findings") != {"HIGH": 0, "CRITICAL": 0}
        or receipt.get("license_inventory_sha256") != LICENSE_INVENTORY_SHA256
        or receipt.get("tool_authority_sha256") != TOOL_AUTHORITY_SHA256
    ):
        raise RuntimeError("candidate artifact analysis is not release-clean")
    image_id = receipt.get("candidate_image_id")
    if (
        image_id != RB005_ANCHORS["image_id"]
        or receipt.get("candidate_receipt_sha256")
        != RB005_ANCHORS["candidate_receipt_sha256"]
    ):
        raise RuntimeError("candidate identity differs from RB-005")
    for relative, digest_value in files.items():
        digest = _digest(digest_value, label="candidate artifact file digest")
        path = root / relative
        if relative == "image/image.tar":
            actual_digest = _file_sha256(path)
        else:
            actual_digest = _sha256(_regular_readonly(path))
        if actual_digest != digest:
            raise RuntimeError("candidate artifact analysis file differs")

    rb005 = _object(receipt.get("rb005"), label="candidate RB-005 binding")
    if rb005 != RB005_ANCHORS:
        raise RuntimeError("candidate RB-005 binding differs")

    sbom_summary = _object(receipt.get("sbom"), label="candidate SBOM summary")
    if sbom_summary != {
        "format": "SPDX-2.3-json",
        "generator_image": SYFT_IMAGE,
        "generator_version": SYFT_VERSION,
        "policy_sha256": SBOM_POLICY_SHA256,
    }:
        raise RuntimeError("candidate SBOM summary differs")
    sbom_raw = _regular_readonly(root / "reports/sbom.spdx.json")
    sbom = _validate_spdx_document(sbom_raw)
    trivy_raw = _regular_readonly(root / "reports/trivy.raw.json")
    trivy_payload = _json(trivy_raw, label="raw Trivy result")
    if _trivy_finding_counts(trivy_payload) != receipt["findings"]:
        raise RuntimeError("raw Trivy findings differ from analysis receipt")

    trivy_summary = _object(receipt.get("trivy"), label="candidate Trivy summary")
    if set(trivy_summary) != {
        "cache_authority_sha256",
        "cache_tree_sha256",
        "database_updated_at",
        "scanner_image",
        "scanner_version",
    } or (
        trivy_summary.get("cache_authority_sha256")
        != TRIVY_CACHE_AUTHORITY_SHA256
        or trivy_summary.get("scanner_image") != TRIVY_IMAGE
        or trivy_summary.get("scanner_version") != TRIVY_VERSION
        or not isinstance(trivy_summary.get("database_updated_at"), str)
        or not trivy_summary["database_updated_at"]
    ):
        raise RuntimeError("candidate Trivy summary differs")
    _digest(trivy_summary.get("cache_tree_sha256"), label="Trivy cache tree")

    binding = _json(
        _regular_readonly(root / "reports/sbom-binding.json"),
        label="candidate SBOM binding",
    )
    if set(binding) != {
        "artifact",
        "contract",
        "documentNamespace",
        "documentSha256",
        "format",
        "generator",
        "licenseInventorySha256",
        "vulnerabilityScanSha256",
    } or binding != {
        "artifact": {
            "id": image_id,
            "sha256": image_id.removeprefix("sha256:"),
            "type": "image",
        },
        "contract": "Eva2SbomBinding/v1",
        "documentNamespace": sbom["documentNamespace"],
        "documentSha256": _sha256(sbom_raw),
        "format": "SPDX-2.3-json",
        "generator": SYFT_IMAGE,
        "licenseInventorySha256": LICENSE_INVENTORY_SHA256,
        "vulnerabilityScanSha256": _sha256(trivy_raw),
    }:
        raise RuntimeError("candidate SBOM binding differs")
    return receipt


__all__ = [
    "CandidateArtifactAnalysis",
    "LICENSE_INVENTORY_SHA256",
    "RB005_ANCHORS",
    "SBOM_POLICY_SHA256",
    "SYFT_IMAGE",
    "SYFT_VERSION",
    "TOOL_AUTHORITY_PATH",
    "TOOL_AUTHORITY_SHA256",
    "TRIVY_VERSION",
    "TRIVY_CACHE_AUTHORITY_SHA256",
    "VerifiedTrivyCache",
    "analyze_candidate_image",
    "syft_command",
    "trivy_command_for_archive",
    "validate_candidate_artifact_analysis",
    "validate_rb005_candidate",
    "validate_spdx",
    "validate_trivy_cache",
]
