"""Joint local preflight for a future managed-B offline image candidate.

This is intentionally not a Docker builder.  It binds the sealed Git source
archive to the separately verified local wheelhouse before a later, isolated
context-materializer is allowed to consume either input.
"""

from __future__ import annotations

import stat
from typing import Any

from eva.operations.managed_b_wheelhouse import (
    MaterializedWheelhouse,
    validate_wheelhouse,
)
from tools.prepare_managed_b_candidate_context import (
    CandidateBuildInputs,
    validate_candidate_build_inputs,
)


def _validate_materialized_wheelhouse(
    materialized: MaterializedWheelhouse,
    wheelhouse_manifest_raw: bytes,
    wheel_coverage_raw: bytes,
    requirements_lock_raw: bytes,
) -> dict[str, object]:
    if not isinstance(materialized, MaterializedWheelhouse):
        raise RuntimeError("offline candidate wheelhouse must be materialized")
    receipt = materialized.receipt
    if (
        receipt.get("contract") != "EvaManagedBWheelhouseMaterializationReceipt/v1"
        or receipt.get("status") != "passed"
        or receipt.get("network_used") is not False
    ):
        raise RuntimeError("offline candidate wheelhouse materialization receipt differs")
    try:
        metadata = materialized.path.lstat()
    except OSError as error:
        raise RuntimeError("offline candidate wheelhouse is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o222
    ):
        raise RuntimeError("offline candidate wheelhouse must remain read-only")
    validated = validate_wheelhouse(
        wheelhouse_manifest_raw,
        wheel_coverage_raw,
        materialized.path,
        requirements_lock_raw,
    )
    if (
        receipt.get("input_tree_sha256") != validated["wheelhouse_tree_sha256"]
        or receipt.get("wheelhouse_tree_sha256")
        != validated["wheelhouse_tree_sha256"]
    ):
        raise RuntimeError("offline candidate wheelhouse receipt tree differs")
    for entry in materialized.path.iterdir():
        entry_metadata = entry.lstat()
        if (
            stat.S_ISLNK(entry_metadata.st_mode)
            or not stat.S_ISREG(entry_metadata.st_mode)
            or entry_metadata.st_nlink != 1
            or entry_metadata.st_mode & 0o222
        ):
            raise RuntimeError("offline candidate wheelhouse contains an unsafe entry")
    return validated


def validate_offline_candidate_inputs(
    candidate: CandidateBuildInputs,
    wheelhouse_manifest_raw: bytes,
    wheel_coverage_raw: bytes,
    wheelhouse: MaterializedWheelhouse,
    requirements_lock_raw: bytes,
) -> dict[str, Any]:
    """Bind one B01 source archive to one B02b-validated local wheel set."""

    source_files = validate_candidate_build_inputs(candidate)
    source_lock = source_files["requirements.lock"][0]
    if source_lock != requirements_lock_raw:
        raise RuntimeError("offline candidate wheelhouse lock differs from source lock")
    validated_wheelhouse = _validate_materialized_wheelhouse(
        wheelhouse,
        wheelhouse_manifest_raw,
        wheel_coverage_raw,
        requirements_lock_raw,
    )
    return {
        "contract": "EvaManagedBOfflineCandidatePreflightReceipt/v1",
        "base_image_digest": candidate.base_image_digest,
        "candidate_dockerfile_sha256": candidate.dockerfile_sha256,
        "candidate_source_archive_sha256": candidate.source_archive_sha256,
        "candidate_source_commit": candidate.source_commit,
        "candidate_source_date_epoch": candidate.source_date_epoch,
        "network_used": False,
        "nonclaims": [
            "No Docker context, image, tag, push, signature, or release was created.",
            "No base OCI image bytes were inspected or accepted.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
        "requirements_lock_sha256": candidate.requirements_lock_sha256,
        "status": "passed",
        "wheelhouse_tree_sha256": validated_wheelhouse["wheelhouse_tree_sha256"],
        "wheelhouse_wheel_count": validated_wheelhouse["expected_wheel_count"],
    }


__all__ = ["validate_offline_candidate_inputs"]
