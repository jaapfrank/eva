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
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse

from tools.check_container_vulnerabilities import TRIVY_IMAGE, finding_counts

from .candidate_image import CandidateImage, validate_offline_candidate_image


SYFT_VERSION = "1.51.0"
SYFT_IMAGE = (
    "ghcr.io/anchore/syft@sha256:"
    "678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0"
)
TRIVY_VERSION = "0.74.0"
TOOL_AUTHORITY_PATH = "release/candidate-artifact-tool-authority-v1.json"
_MAX_JSON_BYTES = 256 * 1024 * 1024
_SHA256 = frozenset("0123456789abcdef")
_TRIVY_CACHE_FILES = frozenset({"db/metadata.json", "db/trivy.db"})


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
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


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
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


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
        f"type=bind,src={archive.parent},dst=/archive,readonly",
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
    return [
        *_container_prefix(archive=archive),
        "--env",
        "TRIVY_CACHE_DIR=/cache",
        "--mount",
        f"type=bind,src={cache.path},dst=/cache,readonly",
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


def validate_spdx(raw: bytes, policy_raw: bytes) -> dict[str, Any]:
    """Validate the release-selected SPDX subset before hashing it."""

    policy = _json(policy_raw, label="SBOM policy")
    required_fields = policy.get("requiredPackageFields")
    required_relationships = policy.get("requiredRelationships")
    if (
        policy.get("contract") != "Eva2SbomPolicy/v1"
        or policy.get("authoritativeFormat") != "SPDX-2.3-json"
        or policy.get("requiredHashes") != ["SHA256"]
        or policy.get("packageChecksumPolicy")
        != "optional_per_SPDX_2.3_validate_when_present_never_fabricate"
        or not isinstance(required_fields, list)
        or not all(isinstance(field, str) for field in required_fields)
        or not isinstance(required_relationships, list)
        or not all(isinstance(kind, str) for kind in required_relationships)
    ):
        raise RuntimeError("SBOM policy differs")
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
        raise RuntimeError("Syft SPDX package set is empty")
    for package in packages:
        if not isinstance(package, dict) or any(
            field not in package for field in required_fields
        ):
            raise RuntimeError("Syft SPDX package fields are incomplete")
        if any(
            not isinstance(package[field], str) or not package[field]
            for field in required_fields
            if field != "checksums"
        ):
            raise RuntimeError("Syft SPDX package field is invalid")
        checksums = package.get("checksums")
        if checksums is not None and (
            not isinstance(checksums, list)
            or not checksums
            or any(
                not isinstance(checksum, dict)
                or checksum.get("algorithm") != "SHA256"
                or not isinstance(checksum.get("checksumValue"), str)
                or len(checksum["checksumValue"]) != 64
                or any(
                    character not in _SHA256
                    for character in checksum["checksumValue"]
                )
                for checksum in checksums
            )
        ):
            raise RuntimeError("Syft SPDX package checksum is invalid")
    relationships = document.get("relationships")
    if not isinstance(relationships, list):
        raise RuntimeError("Syft SPDX relationships are absent")
    actual_relationships = {
        row.get("relationshipType") for row in relationships if isinstance(row, dict)
    }
    if not set(required_relationships).issubset(actual_relationships):
        raise RuntimeError("Syft SPDX required relationships are absent")
    return document


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
    output_parent: Path,
    trivy_cache: Path,
    trivy_cache_authority_raw: bytes,
    tool_authority_raw: bytes,
    sbom_policy_raw: bytes,
    license_inventory_raw: bytes,
    runner: Runner = _run,
) -> CandidateArtifactAnalysis:
    """Export once, run both pinned tools, and retain immutable raw evidence."""

    candidate_receipt = validate_offline_candidate_image(candidate, runner=runner)
    _tool_authority(tool_authority_raw)
    cache = validate_trivy_cache(
        trivy_cache, trivy_cache_authority_raw, tool_authority_raw
    )
    inventory = _json(license_inventory_raw, label="license inventory")
    inventory_claims = inventory.get("claims")
    if (
        inventory.get("contract") != "Eva2PythonLicenseInventory/v1"
        or not isinstance(inventory_claims, dict)
        or inventory_claims.get("everyLockedPackageInventoried") is not True
    ):
        raise RuntimeError("license inventory differs")
    _private_parent(output_parent)
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
        counts = finding_counts(trivy_payload)
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
            "candidate_receipt_sha256": _sha256(_canonical(candidate_receipt)),
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
            },
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
        receipt_raw = _canonical(receipt)
        _write(reports / "analysis-receipt.json", receipt_raw)
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
    receipt = analysis.receipt
    if (
        receipt.get("contract") != "EvaManagedBCandidateArtifactAnalysis/v1"
        or receipt.get("status") != "passed_candidate_artifacts"
        or receipt.get("network_used") is not False
        or receipt.get("findings") != {"HIGH": 0, "CRITICAL": 0}
        or not isinstance(receipt.get("files"), dict)
    ):
        raise RuntimeError("candidate artifact analysis is not release-clean")
    root = analysis.path
    metadata = root.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o222
    ):
        raise RuntimeError("candidate artifact analysis directory is unsafe")
    for relative, digest in receipt["files"].items():
        path = root / relative
        if relative == "image/image.tar":
            _readonly_file(path, maximum=4 * 1024 * 1024 * 1024)
            actual_digest = _file_sha256(path)
        else:
            actual_digest = _sha256(_regular_readonly(path))
        if actual_digest != digest:
            raise RuntimeError("candidate artifact analysis file differs")
    retained = _json(
        _regular_readonly(root / "reports/analysis-receipt.json"),
        label="candidate artifact receipt",
    )
    if retained != receipt:
        raise RuntimeError("candidate artifact retained receipt differs")
    return receipt


__all__ = [
    "CandidateArtifactAnalysis",
    "SYFT_IMAGE",
    "SYFT_VERSION",
    "TOOL_AUTHORITY_PATH",
    "TRIVY_VERSION",
    "VerifiedTrivyCache",
    "analyze_candidate_image",
    "syft_command",
    "trivy_command_for_archive",
    "validate_candidate_artifact_analysis",
    "validate_spdx",
    "validate_trivy_cache",
]
