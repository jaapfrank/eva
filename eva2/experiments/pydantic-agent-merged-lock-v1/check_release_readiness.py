"""Check the fail-closed release requirements for the merged-lock experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "release-readiness-requirements.json"
EXPECTED_FRESH_IDS = {
    "candidate_dependency_source_binding",
    "candidate_wheel_coverage",
    "candidate_wheelhouse_manifest",
    "durable_materialized_wheelhouse",
    "candidate_license_inventory",
    "candidate_release_input_authority",
    "sealed_candidate_source_archive",
    "offline_candidate_image",
    "candidate_sbom_and_scan",
    "candidate_runtime_parity",
    "candidate_ab_rollback",
}
MISSING_STATUSES = {
    "missing_product_artifact",
    "missing_durable_artifact",
    "missing_release_evidence",
}


class ReleaseReadinessError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _load(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseReadinessError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseReadinessError(f"{label} is not an object")
    return value


def _read_bound(path: str, digest: str) -> bytes:
    target = ROOT / path
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(ROOT.resolve())
        raw = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise ReleaseReadinessError(f"bound file is inaccessible: {path}") from exc
    if target.is_symlink() or _sha(raw) != digest:
        raise ReleaseReadinessError(f"bound file digest differs: {path}")
    return raw


def _assert_current_release_is_old(requirements: dict[str, Any]) -> None:
    current = {item["id"]: item for item in requirements["currentReleaseInputs"]}
    if set(current) != {
        "dependency_source_binding",
        "wheel_coverage",
        "wheelhouse_manifest",
        "license_inventory",
        "release_input_authority",
    }:
        raise ReleaseReadinessError("current release input set differs")
    values = {
        name: _load(_read_bound(item["path"], item["sha256"]), name)
        for name, item in current.items()
    }
    product_sha = requirements["candidate"]["lockSha256"]
    old_sha = "ce92ff9385c0b708ea0596b4cbfdfe5473379a744d1eb3dc6c1080959d17334f"
    observed = {
        values["dependency_source_binding"]["standaloneLock"]["sha256"],
        values["wheel_coverage"]["requirementsLockSha256"],
        values["wheelhouse_manifest"]["requirementsLockSha256"],
    }
    authority_lock = next(
        item["sha256"]
        for item in values["release_input_authority"]["requiredInputs"]
        if item["path"] == "requirements-4d.lock"
    )
    observed.add(authority_lock)
    if observed != {old_sha} or product_sha in observed:
        raise ReleaseReadinessError(
            "current release metadata is not exclusively old-lock-bound"
        )
    versions = {
        item["name"]: item["version"]
        for item in values["license_inventory"]["packages"]
        if item["name"] in {"pydantic-ai-slim", "pydantic-graph"}
    }
    if versions != {"pydantic-ai-slim": "2.32.1", "pydantic-graph": "2.32.1"}:
        raise ReleaseReadinessError(
            "current license inventory is not the old candidate"
        )


def _assert_reusable_mechanisms(requirements: dict[str, Any]) -> None:
    mechanisms = {
        item["id"]: item for item in requirements["currentReusableMechanisms"]
    }
    if set(mechanisms) != {
        "offline_dockerfile",
        "sealed_source_preparer",
        "offline_input_validator",
    }:
        raise ReleaseReadinessError("reusable release mechanism set differs")
    raw = {
        name: _read_bound(item["path"], item["sha256"])
        for name, item in mechanisms.items()
    }
    dockerfile = raw["offline_dockerfile"]
    for required in (
        b"COPY requirements-4d.lock ./requirements-4d.lock",
        b"COPY wheelhouse ./wheelhouse",
        b"--no-index",
        b"--only-binary=:all:",
        b"--require-hashes",
        b"python -m pip uninstall -y pip wheel",
    ):
        if required not in dockerfile:
            raise ReleaseReadinessError("offline Dockerfile contract differs")
    if b"external_inputs_required" not in raw["sealed_source_preparer"]:
        raise ReleaseReadinessError("source preparer no longer separates wheelhouse")
    if b"validate_offline_candidate_inputs" not in raw["offline_input_validator"]:
        raise ReleaseReadinessError("offline joint validator is unavailable")


def inspect_release_readiness(raw: bytes) -> dict[str, Any]:
    requirements = _load(raw, "release readiness requirements")
    canonical = _canonical(requirements)
    if raw not in (canonical, canonical + b"\n"):
        raise ReleaseReadinessError("release requirements are not canonical JSON")
    if (
        requirements.get("contract")
        != "Eva2PydanticAgentReleaseReadinessRequirements/v1"
    ):
        raise ReleaseReadinessError("release requirements contract differs")
    if (
        requirements.get("status")
        != "blocked_until_all_fresh_artifacts_and_evidence_pass"
    ):
        raise ReleaseReadinessError("release requirements must remain fail-closed")

    candidate = requirements["candidate"]
    _read_bound(candidate["lockPath"], candidate["lockSha256"])
    authority_raw = _read_bound(
        candidate["wheelAuthorityPath"], candidate["wheelAuthoritySha256"]
    )
    wheel_authority = _load(authority_raw, "candidate wheel authority")
    expected_wheels = wheel_authority["expectedCandidateWheelhouse"]
    if (
        candidate["packageCount"] != 38
        or candidate["wheelCount"] != 38
        or candidate["selectedManifestSha256"]
        != expected_wheels["selectedManifestSha256"]
    ):
        raise ReleaseReadinessError("candidate summary differs from wheel authority")

    ephemeral = requirements["ephemeralValidation"]
    if ephemeral != {
        "durableReceiptPresent": False,
        "networklessInstallPassed": True,
        "pipCheckPassed": True,
        "source": "temporary wheelhouse assembled from one exact local image plus two hash-bound replacement wheels",
        "wheelBytesValidated": 38,
    }:
        raise ReleaseReadinessError("ephemeral validation is overstated or differs")

    fresh = requirements["requiredFreshArtifacts"]
    fresh_by_id = {item["id"]: item for item in fresh}
    if len(fresh_by_id) != len(fresh) or set(fresh_by_id) != EXPECTED_FRESH_IDS:
        raise ReleaseReadinessError("fresh release gate set differs")
    if any(item["status"] not in MISSING_STATUSES for item in fresh):
        raise ReleaseReadinessError("a fresh release gate is not explicitly missing")
    if any(
        not item.get("requiredContract") or not item.get("acceptance") for item in fresh
    ):
        raise ReleaseReadinessError("a fresh release gate lacks contract or acceptance")

    lock_sha = candidate["lockSha256"]
    candidate_bound = {
        "candidate_dependency_source_binding",
        "candidate_wheel_coverage",
        "candidate_wheelhouse_manifest",
        "candidate_license_inventory",
        "sealed_candidate_source_archive",
    }
    for gate_id in candidate_bound:
        if fresh_by_id[gate_id]["acceptance"].get("candidateLockSha256") != lock_sha:
            raise ReleaseReadinessError(f"fresh gate is not candidate-bound: {gate_id}")

    _assert_current_release_is_old(requirements)
    _assert_reusable_mechanisms(requirements)
    return {
        "contract": "Eva2PydanticAgentReleaseReadinessInspection/v1",
        "status": "blocked",
        "candidatePackageCount": 38,
        "candidateWheelCount": 38,
        "currentReleaseMetadataReusable": False,
        "offlineBuildMechanismsReusableAfterFreshInputs": True,
        "ephemeralInstallIsReleaseEvidence": False,
        "blockerCount": len(fresh),
        "blockers": sorted(fresh_by_id),
    }


def main() -> None:
    receipt = inspect_release_readiness(REQUIREMENTS.read_bytes())
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
