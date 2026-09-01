from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import MappingProxyType

import pytest

import tools.managed_b_candidate.artifact_analysis as artifact_analysis
from tools.check_container_vulnerabilities import TRIVY_IMAGE
from tools.managed_b_candidate.artifact_analysis import (
    SYFT_IMAGE,
    CandidateArtifactAnalysis,
    analyze_candidate_image,
    validate_candidate_artifact_analysis,
    validate_spdx,
    validate_trivy_cache,
)
from tools.managed_b_candidate.candidate_image import CandidateImage


ROOT = Path(__file__).resolve().parents[2]
IMAGE_ID = "sha256:" + "a" * 64
ARCHIVE_RAW = b"one exact candidate image archive"
ARCHIVE_SHA256 = hashlib.sha256(ARCHIVE_RAW).hexdigest()


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-artifact-analysis-test-", dir="/tmp"
    ) as path:
        yield Path(path)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _tool_authority() -> bytes:
    return (ROOT / "release/candidate-artifact-tool-authority-v1.json").read_bytes()


def _cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> tuple[Path, bytes]:
    root = tmp_path / "trivy-cache"
    database = root / "db"
    database.mkdir(parents=True)
    contents = {
        "db/metadata.json": b'{"UpdatedAt":"2026-08-31T07:08:27Z"}',
        "db/trivy.db": b"synthetic-trivy-database",
    }
    for relative, raw in contents.items():
        path = root / relative
        path.write_bytes(raw)
        path.chmod(0o444)
    database.chmod(0o555)
    root.chmod(0o555)
    authority = {
        "contract": "EvaTrivyCacheAuthority/v1",
        "databaseUpdatedAt": "2026-08-31T07:08:27Z",
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            for relative, raw in sorted(contents.items())
        ],
        "scannerImage": TRIVY_IMAGE,
        "scannerVersion": "0.74.0",
    }
    authority_raw = _canonical(authority)
    if monkeypatch is not None:
        monkeypatch.setattr(
            artifact_analysis,
            "TRIVY_CACHE_AUTHORITY_SHA256",
            hashlib.sha256(authority_raw).hexdigest(),
        )
    return root, authority_raw


def _policy() -> bytes:
    return (ROOT / "release/sbom-policy.json").read_bytes()


def _inventory() -> bytes:
    return _canonical(
        {
            "claims": {"everyLockedPackageInventoried": True},
            "contract": "Eva2PythonLicenseInventory/v1",
        }
    )


def _spdx(
    *, relationships: list[str] | None = None, include_checksum: bool = True
) -> str:
    kinds = relationships or ["DESCRIBES", "CONTAINS", "DEPENDENCY_OF"]
    package = {
        "SPDXID": "SPDXRef-Package-eva",
        "downloadLocation": "NOASSERTION",
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "MIT",
        "name": "eva2-living-core",
        "versionInfo": "0.1.0",
    }
    if include_checksum:
        package["checksums"] = [
            {"algorithm": "SHA256", "checksumValue": "b" * 64}
        ]
    return json.dumps(
        {
            "SPDXID": "SPDXRef-DOCUMENT",
            "creationInfo": {"creators": ["Tool: syft-1.51.0"]},
            "documentNamespace": "https://example.invalid/eva/sbom/a",
            "packages": [package],
            "relationships": [
                {
                    "relatedSpdxElement": "SPDXRef-Package-eva",
                    "relationshipType": kind,
                    "spdxElementId": "SPDXRef-DOCUMENT",
                }
                for kind in kinds
            ],
            "spdxVersion": "SPDX-2.3",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _candidate() -> CandidateImage:
    return CandidateImage(
        IMAGE_ID,
        {
            "candidate_image_archive_sha256": ARCHIVE_SHA256,
            "candidate_image_config_digest": "sha256:" + "d" * 64,
            "candidate_image_exporter": "docker",
            "candidate_image_id": IMAGE_ID,
            "candidate_image_size": 123,
            "candidate_provenance_attestation_enabled": False,
            "candidate_runtime_library_path": "LD_LIBRARY_PATH=/usr/local/lib",
            "candidate_source_commit": "e" * 40,
            "candidate_source_date_epoch": 1788257221,
            "candidate_timestamp_rewrite_enabled": True,
            "contract": "EvaManagedBOfflineCandidateImageReceipt/v3",
            "network_used": False,
            "status": "built_local_candidate",
        },
    )


def test_synthetic_spdx_fixture_matches_the_pinned_policy() -> None:
    document = validate_spdx(_spdx().encode(), _policy())

    assert document["packages"][0]["SPDXID"] == "SPDXRef-Package-eva"


@pytest.mark.parametrize(
    "case",
    [
        "bare_id",
        "whitespace_name",
        "duplicate_id",
        "missing_field",
        "malformed_relationship",
        "unknown_endpoint",
    ],
)
def test_spdx_policy_rejects_malformed_packages_and_relationships(case: str) -> None:
    document = json.loads(_spdx())
    package = document["packages"][0]
    if case == "bare_id":
        package["SPDXID"] = "Package-eva"
    elif case == "whitespace_name":
        package["name"] = " \t"
    elif case == "duplicate_id":
        document["packages"].append(dict(package))
    elif case == "missing_field":
        del package["versionInfo"]
    elif case == "malformed_relationship":
        document["relationships"][0] = {"relationshipType": "DESCRIBES"}
    elif case == "unknown_endpoint":
        document["relationships"][0]["relatedSpdxElement"] = "SPDXRef-Unknown"

    with pytest.raises(RuntimeError, match="SPDX"):
        validate_spdx(_canonical(document), _policy())


def test_spdx_relationship_may_target_a_known_top_level_file() -> None:
    document = json.loads(_spdx())
    file_id = "SPDXRef-File-app.py"
    document["files"] = [{"SPDXID": file_id}]
    contains = next(
        relationship
        for relationship in document["relationships"]
        if relationship["relationshipType"] == "CONTAINS"
    )
    contains["relatedSpdxElement"] = file_id

    assert validate_spdx(_canonical(document), _policy())["files"] == [
        {"SPDXID": file_id}
    ]


@pytest.mark.parametrize(
    ("collection", "elements"),
    [
        ("files", None),
        ("files", [{"SPDXID": "File-bare"}]),
        ("files", [{"SPDXID": "SPDXRef-File-a"}] * 2),
        ("snippets", None),
        ("snippets", [{}]),
        ("snippets", [{"SPDXID": "SPDXRef-Package-eva"}]),
    ],
)
def test_spdx_rejects_malformed_or_duplicate_file_and_snippet_ids(
    collection: str, elements: list[dict[str, str]] | None
) -> None:
    document = json.loads(_spdx())
    document[collection] = elements

    with pytest.raises(RuntimeError, match=rf"SPDX {collection}.*invalid"):
        validate_spdx(_canonical(document), _policy())


def _rb005_proof(
    tmp_path: Path,
    candidate: CandidateImage,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    candidate_receipt_sha256 = hashlib.sha256(
        _canonical(candidate.receipt)
    ).hexdigest()
    archive_sha256 = candidate.receipt["candidate_image_archive_sha256"]
    source_commit = candidate.receipt["candidate_source_commit"]
    input_authority_sha256 = "1" * 64
    release_authority_sha256 = "2" * 64
    diff_ids = ["sha256:" + "3" * 64]
    private_candidate = {
        "archiveSha256": archive_sha256,
        "buildkitResultDigest": "sha256:" + "4" * 64,
        "configSha256": "5" * 64,
        "diffIds": diff_ids,
        "imageId": candidate.image_id,
        "ociIndexSha256": "6" * 64,
        "ociManifestSha256": "7" * 64,
        "runtimeConfigSha256": "8" * 64,
        "runtimeLayerSequenceSha256": "9" * 64,
    }
    candidate_receipt_hashes = ["b" * 64, candidate_receipt_sha256]
    admission_receipt_hashes = ["c" * 64, "d" * 64]
    builders = [
        {
            "admissionChain": {
                "archiveSha256": archive_sha256,
                "candidateReceiptSha256": candidate_receipt_hashes[index],
            },
            "admissionReceiptSha256": admission_receipt_hashes[index],
            "candidateArchiveSha256": archive_sha256,
            "candidateReceiptSha256": candidate_receipt_hashes[index],
            "taskId": task_id,
        }
        for index, task_id in enumerate(("RB-003", "RB-004"))
    ]
    private = {
        "builders": builders,
        "candidate": private_candidate,
        "contract": "EvaTwoBuilderReproducibilityReceipt/v2",
        "inputAuthoritySha256": input_authority_sha256,
        "nonclaims": [],
        "releaseAuthoritySha256": release_authority_sha256,
        "sourceCommit": source_commit,
        "status": "passed",
    }
    private_path = tmp_path / "rb005-private-receipt.json"
    private_raw = _canonical(private)
    private_path.write_bytes(private_raw)
    private_path.chmod(0o600)
    public = {
        "candidate": {
            "archiveByteIdentical": True,
            "archiveSha256": archive_sha256,
            "configSha256": private_candidate["configSha256"],
            "diffIdCount": len(diff_ids),
            "diffIdsCanonicalSha256": hashlib.sha256(
                _canonical(diff_ids)
            ).hexdigest(),
            "imageId": candidate.image_id,
            "ociManifestSha256": private_candidate["ociManifestSha256"],
            "runtimeLayerSequenceSha256": private_candidate[
                "runtimeLayerSequenceSha256"
            ],
        },
        "comparison": {
            "allSevenBuilderIdentitiesDiffer": True,
            "allSixAdmissionBuildObservationChallengesDiffer": True,
            "builderRoles": ["RB-003", "RB-004"],
            "fullOciIndexManifestConfigAndLayerBinding": True,
            "sameInputAuthority": True,
            "sameReleaseAuthority": True,
            "sameSourceCommit": True,
        },
        "contract": "EvaWorkboardTaskEvidence/v1",
        "inputAuthoritySha256": input_authority_sha256,
        "networkUsed": False,
        "physicalRun": {
            "builderA": {
                "admissionReceiptSha256": admission_receipt_hashes[0],
                "candidateReceiptSha256": candidate_receipt_hashes[0],
                "taskId": "RB-003",
            },
            "builderB": {
                "admissionReceiptSha256": admission_receipt_hashes[1],
                "candidateReceiptSha256": candidate_receipt_hashes[1],
                "taskId": "RB-004",
            },
            "newBuildPerformed": False,
            "privateReceiptMode": "0600",
            "resultContract": "EvaTwoBuilderReproducibilityReceipt/v2",
            "resultReceiptSha256": hashlib.sha256(private_raw).hexdigest(),
            "status": "passed",
        },
        "releaseAuthoritySha256": release_authority_sha256,
        "sourceCommit": source_commit,
        "taskId": "RB-005",
    }
    public_path = (
        tmp_path
        / "eva2/evidence/product/RB-005-two-builder-reproducibility-2026-09-01.json"
    )
    public_path.parent.mkdir(parents=True)
    public_raw = _canonical(public)
    public_path.write_bytes(public_raw)
    public_path.chmod(0o644)
    monkeypatch.setattr(
        artifact_analysis,
        "RB005_ANCHORS",
        MappingProxyType(
            {
                "archive_sha256": archive_sha256,
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "image_id": candidate.image_id,
                "input_authority_sha256": input_authority_sha256,
                "private_receipt_sha256": hashlib.sha256(private_raw).hexdigest(),
                "public_evidence_path": (
                    "eva2/evidence/product/"
                    "RB-005-two-builder-reproducibility-2026-09-01.json"
                ),
                "public_evidence_sha256": hashlib.sha256(public_raw).hexdigest(),
                "release_authority_sha256": release_authority_sha256,
                "source_commit": source_commit,
            }
        ),
    )
    monkeypatch.setattr(
        artifact_analysis,
        "LICENSE_INVENTORY_SHA256",
        hashlib.sha256(_inventory()).hexdigest(),
    )
    return public_path, private_path


def _runner(
    *,
    archive_raw: bytes = ARCHIVE_RAW,
    spdx: str | None = None,
    findings: list[dict[str, str]] | None = None,
    trivy_stdout: str | None = None,
) -> tuple[list[list[str]], object]:
    commands: list[list[str]] = []

    def run(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["docker", "save"]:
            destination = Path(command[command.index("--output") + 1])
            destination.write_bytes(archive_raw)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if SYFT_IMAGE in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=spdx or _spdx(), stderr=""
            )
        if TRIVY_IMAGE in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=trivy_stdout
                or json.dumps(
                    {
                        "Results": [
                            {
                                "Class": "os-pkgs",
                                "Target": "candidate (debian 13)",
                                "Type": "debian",
                                "Vulnerabilities": findings or [],
                            }
                        ]
                    },
                    separators=(",", ":"),
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    return commands, run


def _rewrite_public_evidence(
    path: Path, value: dict[str, object]
) -> None:
    path.write_bytes(_canonical(value))


def _restore_modes(root: Path) -> None:
    for directory, _subdirectories, _files in os.walk(root, topdown=False):
        Path(directory).chmod(0o700)


def _replace_readonly(path: Path, raw: bytes) -> None:
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o444)


def _seal_analysis_directories(root: Path) -> None:
    (root / "image").chmod(0o555)
    (root / "reports").chmod(0o555)
    root.chmod(0o500)


def test_exports_once_and_binds_syft_and_trivy_to_the_same_archive(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, cache_authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    commands, runner = _runner()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )

    analysis = analyze_candidate_image(
        candidate,
        rb005_public_evidence_path=public_evidence,
        rb005_private_receipt_path=private_receipt,
        output_parent=output,
        trivy_cache=cache,
        trivy_cache_authority_raw=cache_authority,
        tool_authority_raw=_tool_authority(),
        sbom_policy_raw=_policy(),
        license_inventory_raw=_inventory(),
        runner=runner,  # type: ignore[arg-type]
    )

    assert sum(command[:2] == ["docker", "save"] for command in commands) == 1
    scans = [command for command in commands if command[:2] == ["docker", "run"]]
    assert len(scans) == 2
    archive_mounts = {
        next(value for value in command if value.startswith("type=bind,src=") and "/image,dst=/archive" in value)
        for command in scans
    }
    assert len(archive_mounts) == 1
    for command in scans:
        assert command[command.index("--network") + 1] == "none"
        assert "--read-only" in command
        assert command[command.index("--pull") + 1] == "never"
    syft = next(command for command in scans if SYFT_IMAGE in command)
    assert "SYFT_CHECK_FOR_APP_UPDATE=false" in syft
    trivy = next(command for command in scans if TRIVY_IMAGE in command)
    assert "--skip-db-update" in trivy
    assert trivy[trivy.index("--cache-backend") + 1] == "memory"
    assert any(value.endswith(",dst=/cache,readonly") for value in trivy)

    raw_trivy = (analysis.path / "reports/trivy.raw.json").read_bytes()
    binding = json.loads((analysis.path / "reports/sbom-binding.json").read_bytes())
    assert binding["format"] == "SPDX-2.3-json"
    assert binding["generator"] == SYFT_IMAGE
    assert binding["artifact"]["sha256"] == "a" * 64
    assert binding["vulnerabilityScanSha256"] == hashlib.sha256(
        raw_trivy
    ).hexdigest()
    assert analysis.receipt["files"]["image/image.tar"] == hashlib.sha256(
        ARCHIVE_RAW
    ).hexdigest()
    assert analysis.receipt["rb005"] == artifact_analysis.RB005_ANCHORS
    original_read_bytes = Path.read_bytes

    def refuse_archive_read_bytes(path: Path) -> bytes:
        if path == analysis.path / "image/image.tar":
            raise AssertionError("image archive must be hashed as a stream")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", refuse_archive_read_bytes)
    assert validate_candidate_artifact_analysis(analysis)["status"] == (
        "passed_candidate_artifacts"
    )
    _restore_modes(analysis.path)
    _restore_modes(cache)


def test_rb005_mismatch_fails_before_any_docker_command(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )
    private = json.loads(private_receipt.read_bytes())
    private["candidate"]["imageId"] = "sha256:" + "f" * 64
    private_raw = _canonical(private)
    private_receipt.write_bytes(private_raw)
    private_receipt.chmod(0o600)
    public = json.loads(public_evidence.read_bytes())
    public["physicalRun"]["resultReceiptSha256"] = hashlib.sha256(
        private_raw
    ).hexdigest()
    _rewrite_public_evidence(public_evidence, public)
    commands, runner = _runner()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate inspection must not run")
        ),
    )

    with pytest.raises(RuntimeError, match="RB-005"):
        analyze_candidate_image(
            candidate,
            rb005_public_evidence_path=public_evidence,
            rb005_private_receipt_path=private_receipt,
            output_parent=native_tmp_path,
            trivy_cache=native_tmp_path,
            trivy_cache_authority_raw=b"unused",
            tool_authority_raw=b"unused",
            sbom_policy_raw=b"unused",
            license_inventory_raw=b"unused",
            runner=runner,  # type: ignore[arg-type]
        )
    assert commands == []


def test_candidate_image_object_cannot_be_swapped_after_rb005_binding(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )
    swapped = CandidateImage("sha256:" + "f" * 64, candidate.receipt)
    commands, runner = _runner()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate inspection must not run")
        ),
    )

    with pytest.raises(RuntimeError, match="image object differs"):
        analyze_candidate_image(
            swapped,
            rb005_public_evidence_path=public_evidence,
            rb005_private_receipt_path=private_receipt,
            output_parent=native_tmp_path,
            trivy_cache=native_tmp_path,
            trivy_cache_authority_raw=b"unused",
            tool_authority_raw=b"unused",
            sbom_policy_raw=b"unused",
            license_inventory_raw=b"unused",
            runner=runner,  # type: ignore[arg-type]
        )
    assert commands == []


def test_reexport_hash_is_bound_separately_and_both_scanners_share_it(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )
    cache, cache_authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    commands, runner = _runner(archive_raw=b"different archive")

    analysis = analyze_candidate_image(
        candidate,
        rb005_public_evidence_path=public_evidence,
        rb005_private_receipt_path=private_receipt,
        output_parent=output,
        trivy_cache=cache,
        trivy_cache_authority_raw=cache_authority,
        tool_authority_raw=_tool_authority(),
        sbom_policy_raw=_policy(),
        license_inventory_raw=_inventory(),
        runner=runner,  # type: ignore[arg-type]
    )
    assert sum(command[:2] == ["docker", "save"] for command in commands) == 1
    assert sum(command[:2] == ["docker", "run"] for command in commands) == 2
    assert analysis.receipt["rb005"]["archive_sha256"] == ARCHIVE_SHA256
    assert analysis.receipt["files"]["image/image.tar"] == hashlib.sha256(
        b"different archive"
    ).hexdigest()
    _restore_modes(analysis.path)
    _restore_modes(cache)


def test_refuses_a_writable_trivy_cache(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, authority = _cache(native_tmp_path, monkeypatch)
    cache.chmod(0o755)

    with pytest.raises(RuntimeError, match="directories must be immutable"):
        validate_trivy_cache(cache, authority, _tool_authority())

    _restore_modes(cache)


@pytest.mark.parametrize(
    "packages",
    [[], [{}], [{"SPDXID": "garbage", "name": ""}]],
)
def test_empty_or_malformed_spdx_packages_fail_closed(
    packages: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    native_tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    invalid_spdx = json.loads(_spdx())
    invalid_spdx["packages"] = packages
    _commands, runner = _runner(spdx=json.dumps(invalid_spdx))
    candidate = _candidate()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )

    with pytest.raises(RuntimeError, match="SPDX package"):
        analyze_candidate_image(
            candidate,
            rb005_public_evidence_path=public_evidence,
            rb005_private_receipt_path=private_receipt,
            output_parent=output,
            trivy_cache=cache,
            trivy_cache_authority_raw=authority,
            tool_authority_raw=_tool_authority(),
            sbom_policy_raw=_policy(),
            license_inventory_raw=_inventory(),
            runner=runner,  # type: ignore[arg-type]
        )

    assert list(output.iterdir()) == []
    _restore_modes(cache)


def test_accepts_spdx_optional_package_checksum_without_fabricating_one(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(spdx=_spdx(include_checksum=False))
    candidate = _candidate()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )

    analysis = analyze_candidate_image(
        candidate,
        rb005_public_evidence_path=public_evidence,
        rb005_private_receipt_path=private_receipt,
        output_parent=output,
        trivy_cache=cache,
        trivy_cache_authority_raw=authority,
        tool_authority_raw=_tool_authority(),
        sbom_policy_raw=_policy(),
        license_inventory_raw=_inventory(),
        runner=runner,  # type: ignore[arg-type]
    )

    sbom = json.loads((analysis.path / "reports/sbom.spdx.json").read_bytes())
    assert "checksums" not in sbom["packages"][0]
    assert validate_candidate_artifact_analysis(analysis)["status"] == (
        "passed_candidate_artifacts"
    )
    _restore_modes(analysis.path)
    _restore_modes(cache)


def test_raw_findings_are_retained_but_release_validation_rejects_them(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(findings=[{"Severity": "HIGH"}])
    candidate = _candidate()
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )

    analysis = analyze_candidate_image(
        candidate,
        rb005_public_evidence_path=public_evidence,
        rb005_private_receipt_path=private_receipt,
        output_parent=output,
        trivy_cache=cache,
        trivy_cache_authority_raw=authority,
        tool_authority_raw=_tool_authority(),
        sbom_policy_raw=_policy(),
        license_inventory_raw=_inventory(),
        runner=runner,  # type: ignore[arg-type]
    )

    assert analysis.receipt["status"] == "rejected_vulnerabilities"
    assert analysis.receipt["findings"] == {"HIGH": 1, "CRITICAL": 0}
    assert (analysis.path / "reports/trivy.raw.json").is_file()
    with pytest.raises(RuntimeError, match="not release-clean"):
        validate_candidate_artifact_analysis(analysis)
    _restore_modes(analysis.path)
    _restore_modes(cache)


def test_retained_validation_rejects_anchor_file_and_finding_forgery(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )
    _commands, runner = _runner()
    analysis = analyze_candidate_image(
        candidate,
        rb005_public_evidence_path=public_evidence,
        rb005_private_receipt_path=private_receipt,
        output_parent=output,
        trivy_cache=cache,
        trivy_cache_authority_raw=authority,
        tool_authority_raw=_tool_authority(),
        sbom_policy_raw=_policy(),
        license_inventory_raw=_inventory(),
        runner=runner,  # type: ignore[arg-type]
    )
    reports = analysis.path / "reports"
    original_receipt = json.loads(json.dumps(analysis.receipt))

    _restore_modes(analysis.path)
    forged_anchors = json.loads(json.dumps(original_receipt))
    forged_anchors["candidate_image_id"] = "sha256:" + "f" * 64
    forged_anchors["candidate_receipt_sha256"] = "f" * 64
    for key in (
        "archive_sha256",
        "input_authority_sha256",
        "release_authority_sha256",
        "source_commit",
    ):
        forged_anchors["rb005"][key] = "f" * len(forged_anchors["rb005"][key])
    forged_anchors["rb005"]["image_id"] = "sha256:" + "f" * 64
    forged_anchors["rb005"]["candidate_receipt_sha256"] = "f" * 64
    _replace_readonly(reports / "analysis-receipt.json", _canonical(forged_anchors))
    _seal_analysis_directories(analysis.path)
    with pytest.raises(RuntimeError, match="differs from RB-005"):
        validate_candidate_artifact_analysis(
            CandidateArtifactAnalysis(analysis.path, forged_anchors)
        )

    _restore_modes(analysis.path)
    empty_files_receipt = json.loads(json.dumps(original_receipt))
    empty_files_receipt["files"] = {}
    _replace_readonly(
        reports / "analysis-receipt.json", _canonical(empty_files_receipt)
    )
    _seal_analysis_directories(analysis.path)
    with pytest.raises(RuntimeError, match="receipt file set differs"):
        validate_candidate_artifact_analysis(
            CandidateArtifactAnalysis(analysis.path, empty_files_receipt)
        )

    _restore_modes(analysis.path)
    trivy_raw = (
        b'{"Results":[{"Class":"os-pkgs","Target":"candidate",'
        b'"Type":"debian","Vulnerabilities":[{"Severity":"HIGH"}]}]}'
    )
    _replace_readonly(reports / "trivy.raw.json", trivy_raw)
    binding = json.loads((reports / "sbom-binding.json").read_bytes())
    binding["vulnerabilityScanSha256"] = hashlib.sha256(trivy_raw).hexdigest()
    binding_raw = _canonical(binding)
    _replace_readonly(reports / "sbom-binding.json", binding_raw)
    forged_receipt = json.loads(json.dumps(original_receipt))
    forged_receipt["files"]["reports/trivy.raw.json"] = hashlib.sha256(
        trivy_raw
    ).hexdigest()
    forged_receipt["files"]["reports/sbom-binding.json"] = hashlib.sha256(
        binding_raw
    ).hexdigest()
    _replace_readonly(reports / "analysis-receipt.json", _canonical(forged_receipt))
    _seal_analysis_directories(analysis.path)
    with pytest.raises(RuntimeError, match="findings differ"):
        validate_candidate_artifact_analysis(
            CandidateArtifactAnalysis(analysis.path, forged_receipt)
        )
    _restore_modes(analysis.path)
    _restore_modes(cache)


def test_official_clean_trivy_rows_may_omit_vulnerabilities() -> None:
    payload = {
        "Results": [
            {
                "Class": "os-pkgs",
                "Packages": [],
                "Target": "candidate (debian 13)",
                "Type": "debian",
            },
            {
                "Class": "lang-pkgs",
                "Packages": [],
                "Target": "Python",
                "Type": "python-pkg",
            },
        ]
    }

    assert artifact_analysis._trivy_finding_counts(payload) == {
        "HIGH": 0,
        "CRITICAL": 0,
    }


@pytest.mark.parametrize(
    "trivy_stdout",
    [
        '{"Results":[],"Results":[]}',
        '{"Results":[]}',
        '{"Results":[{}]}',
        (
            '{"Results":[{"Class":"os-pkgs","Target":"candidate",'
            '"Type":"debian","Vulnerabilities":[{"Severity":"LOW"}]}]}'
        ),
    ],
)
def test_malformed_or_unfiltered_trivy_output_fails_closed(
    trivy_stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    native_tmp_path: Path,
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path, monkeypatch)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    public_evidence, private_receipt = _rb005_proof(
        native_tmp_path, candidate, monkeypatch
    )
    _commands, runner = _runner(trivy_stdout=trivy_stdout)

    with pytest.raises(RuntimeError):
        analyze_candidate_image(
            candidate,
            rb005_public_evidence_path=public_evidence,
            rb005_private_receipt_path=private_receipt,
            output_parent=output,
            trivy_cache=cache,
            trivy_cache_authority_raw=authority,
            tool_authority_raw=_tool_authority(),
            sbom_policy_raw=_policy(),
            license_inventory_raw=_inventory(),
            runner=runner,  # type: ignore[arg-type]
        )
    assert list(output.iterdir()) == []
    _restore_modes(cache)


def test_analysis_type_is_closed() -> None:
    with pytest.raises(RuntimeError, match="analysis is invalid"):
        validate_candidate_artifact_analysis(object())  # type: ignore[arg-type]

    assert CandidateArtifactAnalysis.__dataclass_fields__.keys() == {"path", "receipt"}
