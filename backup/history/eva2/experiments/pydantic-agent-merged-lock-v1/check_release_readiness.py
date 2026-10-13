"""Inspect the historical release-readiness decision for the 2.36 experiment.

This checker deliberately does not inspect Eva's active release metadata. It
keeps the old decision replayable after the product dependency lock advances.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


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
EXPECTED_HISTORICAL_INPUTS = {
    "dependency_source_binding": (
        "eva2/release/4d-dependency-source-binding.json",
        "7cf000530ff9349f8078582116d7ac1ce8bdf2e42fa93e90c62c10bd9916cbf7",
    ),
    "wheel_coverage": (
        "eva2/release/managed-b-wheel-coverage-4d-amd64-v1.json",
        "0eceb7bce89a847680cccf8c0a12a82fc6a3cbe3701f1dc0d1ea09c413ac382c",
    ),
    "wheelhouse_manifest": (
        "eva2/release/managed-b-wheelhouse-amd64-v2.json",
        "4d9d8f108288ebedd2af39652dfcbe4a341097037157c124873c17e8ae47806f",
    ),
    "license_inventory": (
        "eva2/release/license-inventory.json",
        "d1788bd146100e069ac3050bfe274b809557ee21f6938f568c3254f330a78520",
    ),
    "release_input_authority": (
        "eva2/release/managed-b-release-input-authority-v1.json",
        "4216f6b39c7ccad72be9c90e88f9d249f1f7ae6ca2edb40adb92503bab0f688e",
    ),
}
EXPECTED_HISTORICAL_MECHANISMS = {
    "offline_dockerfile": (
        "eva2/deploy/living-core/Dockerfile",
        "861862a3e325f2dda7d1a6b4f14ec7f58e0de4c34e27d27d56e8aa2fe5756898",
    ),
    "sealed_source_preparer": (
        "eva2/tools/prepare_managed_b_candidate_context.py",
        "114b20dd8f5e9b9d6958f65fd790bb10464d4284c987d396ac7e4f41fbbeee05",
    ),
    "offline_input_validator": (
        "eva2/tools/managed_b_candidate/offline_build.py",
        "c7e199e88d37aae538d1bedf0d4bbbc6f48a376ae499367af9d8454637ecb6eb",
    ),
}
EXPECTED_NON_CLAIMS = [
    "This historical snapshot does not describe the current product dependency lock or current release readiness.",
    "This historical snapshot does not make any claim about Pydantic AI 2.37.0.",
    "The ephemeral offline install was not a durable product build input or release receipt.",
    "No image was built, scanned, signed, deployed or promoted by this historical experiment.",
]
SHA256 = re.compile(r"[0-9a-f]{64}")


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


def _historical_bindings(
    entries: object, expected: dict[str, tuple[str, str]], label: str
) -> None:
    if not isinstance(entries, list):
        raise ReleaseReadinessError(f"{label} is not a list")
    actual: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "path", "sha256"}:
            raise ReleaseReadinessError(f"{label} schema differs")
        identifier = entry["id"]
        path = entry["path"]
        digest = entry["sha256"]
        if (
            not isinstance(identifier, str)
            or not isinstance(path, str)
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or identifier in actual
        ):
            raise ReleaseReadinessError(f"{label} binding differs")
        actual[identifier] = (path, digest)
    if actual != expected:
        raise ReleaseReadinessError(f"{label} binding differs")


def _read_local(path: str, digest: str) -> bytes:
    expected_prefix = "eva2/experiments/pydantic-agent-merged-lock-v1/"
    if not path.startswith(expected_prefix):
        raise ReleaseReadinessError("candidate binding escapes experiment")
    target = HERE / path.removeprefix(expected_prefix)
    if target.is_symlink() or not target.is_file():
        raise ReleaseReadinessError("candidate binding is inaccessible")
    raw = target.read_bytes()
    if _sha(raw) != digest:
        raise ReleaseReadinessError("candidate binding digest differs")
    return raw


def inspect_release_readiness(raw: bytes) -> dict[str, Any]:
    requirements = _load(raw, "historical release readiness snapshot")
    canonical = _canonical(requirements)
    if raw not in (canonical, canonical + b"\n"):
        raise ReleaseReadinessError("historical snapshot is not canonical JSON")
    if set(requirements) != {
        "candidate",
        "contract",
        "ephemeralValidationAtDecision",
        "forbiddenPromotionConditionsAtDecision",
        "historicalReleaseInputs",
        "historicalReusableMechanisms",
        "nonClaims",
        "requiredFreshArtifactsAtDecision",
        "status",
    }:
        raise ReleaseReadinessError("historical snapshot schema differs")
    if (
        requirements["contract"]
        != "Eva2PydanticAgentReleaseReadinessHistoricalSnapshot/v1"
        or requirements["status"] != "historical_snapshot_not_current_release_gate"
    ):
        raise ReleaseReadinessError("historical snapshot classification differs")
    if requirements["nonClaims"] != EXPECTED_NON_CLAIMS:
        raise ReleaseReadinessError("historical non-claims differ")

    candidate = requirements["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {
        "lockPath",
        "lockSha256",
        "packageCount",
        "selectedManifestSha256",
        "wheelAuthorityPath",
        "wheelAuthoritySha256",
        "wheelCount",
    }:
        raise ReleaseReadinessError("candidate summary schema differs")
    _read_local(candidate["lockPath"], candidate["lockSha256"])
    authority = _load(
        _read_local(candidate["wheelAuthorityPath"], candidate["wheelAuthoritySha256"]),
        "historical candidate wheel authority",
    )
    expected_wheels = authority["expectedCandidateWheelhouse"]
    if (
        candidate["packageCount"] != 38
        or candidate["wheelCount"] != 38
        or candidate["selectedManifestSha256"]
        != expected_wheels["selectedManifestSha256"]
    ):
        raise ReleaseReadinessError("candidate summary differs from wheel authority")

    _historical_bindings(
        requirements["historicalReleaseInputs"],
        EXPECTED_HISTORICAL_INPUTS,
        "historical release inputs",
    )
    _historical_bindings(
        requirements["historicalReusableMechanisms"],
        EXPECTED_HISTORICAL_MECHANISMS,
        "historical reusable mechanisms",
    )
    if requirements["ephemeralValidationAtDecision"] != {
        "durableReceiptPresent": False,
        "networklessInstallPassed": True,
        "pipCheckPassed": True,
        "source": "temporary wheelhouse assembled from one exact local image plus two hash-bound replacement wheels",
        "wheelBytesValidated": 38,
    }:
        raise ReleaseReadinessError("historical ephemeral validation differs")

    fresh = requirements["requiredFreshArtifactsAtDecision"]
    fresh_by_id = {item["id"]: item for item in fresh}
    if len(fresh_by_id) != len(fresh) or set(fresh_by_id) != EXPECTED_FRESH_IDS:
        raise ReleaseReadinessError("historical release gate set differs")
    if any(item["status"] not in MISSING_STATUSES for item in fresh):
        raise ReleaseReadinessError("a historical release gate was not missing")
    if any(
        not item.get("requiredContract") or not item.get("acceptance") for item in fresh
    ):
        raise ReleaseReadinessError("a historical release gate lacks acceptance")
    lock_sha = candidate["lockSha256"]
    for gate_id in {
        "candidate_dependency_source_binding",
        "candidate_wheel_coverage",
        "candidate_wheelhouse_manifest",
        "candidate_license_inventory",
        "sealed_candidate_source_archive",
    }:
        if fresh_by_id[gate_id]["acceptance"].get("candidateLockSha256") != lock_sha:
            raise ReleaseReadinessError(
                f"historical gate is not candidate-bound: {gate_id}"
            )

    return {
        "contract": "Eva2PydanticAgentReleaseReadinessHistoricalInspection/v1",
        "status": "historical",
        "candidatePackageCount": 38,
        "candidateWheelCount": 38,
        "historicalMissingGateCountAtDecision": len(fresh),
        "historicalMissingGatesAtDecision": sorted(fresh_by_id),
        "currentReleaseGate": False,
        "claimsPydanticAi237": False,
    }


def main() -> None:
    receipt = inspect_release_readiness(REQUIREMENTS.read_bytes())
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
