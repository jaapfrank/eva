from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from tools.check_container_vulnerabilities import TRIVY_IMAGE
from tools.managed_b_candidate.artifact_analysis import (
    SYFT_IMAGE,
    CandidateArtifactAnalysis,
    analyze_candidate_image,
    validate_candidate_artifact_analysis,
    validate_trivy_cache,
)
from tools.managed_b_candidate.candidate_image import CandidateImage


ROOT = Path(__file__).resolve().parents[2]
IMAGE_ID = "sha256:" + "a" * 64


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


def _cache(tmp_path: Path) -> tuple[Path, bytes]:
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
    return root, _canonical(authority)


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
            "candidate_image_id": IMAGE_ID,
            "candidate_image_size": 123,
            "contract": "EvaManagedBOfflineCandidateImageReceipt/v2",
            "network_used": False,
            "status": "built_local_candidate",
        },
    )


def _runner(
    *,
    spdx: str | None = None,
    findings: list[dict[str, str]] | None = None,
) -> tuple[list[list[str]], object]:
    commands: list[list[str]] = []

    def run(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["docker", "save"]:
            destination = Path(command[command.index("--output") + 1])
            destination.write_bytes(b"one exact candidate image archive")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if SYFT_IMAGE in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=spdx or _spdx(), stderr=""
            )
        if TRIVY_IMAGE in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {"Results": [{"Vulnerabilities": findings or []}]},
                    separators=(",", ":"),
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    return commands, run


def _restore_modes(root: Path) -> None:
    for directory, _subdirectories, _files in os.walk(root, topdown=False):
        Path(directory).chmod(0o700)


def test_exports_once_and_binds_syft_and_trivy_to_the_same_archive(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, cache_authority = _cache(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    commands, runner = _runner()

    analysis = analyze_candidate_image(
        candidate,
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
        b"one exact candidate image archive"
    ).hexdigest()
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


def test_refuses_a_writable_trivy_cache(native_tmp_path: Path) -> None:
    cache, authority = _cache(native_tmp_path)
    cache.chmod(0o755)

    with pytest.raises(RuntimeError, match="directories must be immutable"):
        validate_trivy_cache(cache, authority, _tool_authority())

    _restore_modes(cache)


def test_invalid_spdx_fails_closed_and_removes_partial_artifacts(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.artifact_analysis.validate_offline_candidate_image",
        lambda actual, runner: actual.receipt,
    )
    cache, authority = _cache(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(spdx=_spdx(relationships=["DESCRIBES"]))

    with pytest.raises(RuntimeError, match="required relationships"):
        analyze_candidate_image(
            _candidate(),
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
    cache, authority = _cache(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(spdx=_spdx(include_checksum=False))

    analysis = analyze_candidate_image(
        _candidate(),
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
    cache, authority = _cache(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(findings=[{"Severity": "HIGH"}])

    analysis = analyze_candidate_image(
        _candidate(),
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


def test_analysis_type_is_closed() -> None:
    with pytest.raises(RuntimeError, match="analysis is invalid"):
        validate_candidate_artifact_analysis(object())  # type: ignore[arg-type]

    assert CandidateArtifactAnalysis.__dataclass_fields__.keys() == {"path", "receipt"}
