"""Pure validation for independently reviewed component releases and updater receipts.

Separated from persistent receipt storage and background dispatch in
``reviewed_component_application.py`` so release provenance, reviewer authority,
behavior proofs, and baseline rollback receipts can be verified without threads,
locks, or side effects.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_release_import import canonical, digest, load_object
from eva.operations.image_update import image_update_services

_ID = re.compile(r"[0-9a-f]{64}\Z")
ARTIFACTS = frozenset({"package", "review", "source_import", "build", "installed", "candidate"})


def validate_identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("application identifier must be a SHA-256 hex value")
    return value


def validate_host_path(value: Any, *, exists: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("application paths must be absolute without symbolic links")
    if exists:
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ValueError("application inputs must be host-owned regular files")
    return path


def load_verified_artifact(binding: Any) -> tuple[Path, dict[str, Any]]:
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("application artifact binding differs")
    validate_identifier(binding["sha256"])
    path = validate_host_path(binding["path"])
    if digest(path.read_bytes()) != binding["sha256"]:
        raise ValueError("application artifact bytes changed")
    return path, load_object(path)


def verify_behavior_source(binding: Any) -> tuple[Path, bytes]:
    """Read a host-owned probe once; candidate content supplies no host program."""
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("behavior verifier binding differs")
    validate_identifier(binding["sha256"])
    path = validate_host_path(binding["path"])
    raw = path.read_bytes()
    if len(raw) > 65536 or digest(raw) != binding["sha256"]:
        raise ValueError("behavior verifier source differs")
    return path, raw


def behavior_proof_confirms(
    proof: Any,
    *,
    release: dict[str, Any],
    configuration: dict[str, Any],
    images: dict[str, str],
    containers: dict[str, Any],
    target: str,
) -> bool:
    return (
        isinstance(proof, dict)
        and proof.get("contract") == "EvaImageBehaviorVerification/v1"
        and proof.get("project") == release["project"]
        and proof.get("configuration_sha256") == canonical_sha256(configuration)
        and proof.get("target") == target
        and proof.get("images") == images
        and set(containers) == set(images)
        and all(isinstance(value, str) and value for value in containers.values())
        and proof.get("containers") == containers
        and proof.get("verified") is True
        and isinstance(proof.get("evidence"), dict)
        and proof["evidence"].get("verified") is True
        and proof["evidence"].get("verifier_sha256") == release["behavior_verifier"]["sha256"]
    )


def verify_reviewed_release(
    review_id: str,
    release: Any,
    *,
    review_authorities: dict[str, Any] | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    """Verify release provenance, independent v2 reviewer authority, and image bindings."""
    if isinstance(release, dict) and release.get("kind") == "owned_package":
        from eva.operations.owned_package_application import verify_release

        return verify_release(review_id, release)
    if isinstance(release, dict) and release.get("kind") == "dependency_update":
        from eva.operations.runtime_dependency_update import validate_dependency_release

        return validate_dependency_release(review_id, release)
    if isinstance(release, dict) and release.get("kind") == "runtime_repair":
        from eva.operations.runtime_repair_application import verify_runtime_repair_release

        return verify_runtime_repair_release(review_id, release)
    if isinstance(release, dict) and release.get("kind") == "saved_artifact":
        from eva.operations.saved_artifact_application import verify_saved_release

        return verify_saved_release(
            review_id,
            release,
            review_authorities=review_authorities,
            allow_legacy=allow_legacy,
        )
    if (
        not isinstance(release, dict)
        or set(release) - {"behavior_verifier"} != set(ARTIFACTS) | {"project", "update_receipt"}
        or not isinstance(release["project"], str)
        or not release["project"]
    ):
        raise ValueError("reviewed release configuration differs")
    paths: dict[str, Path] = {}
    data: dict[str, dict[str, Any]] = {}
    for name in ARTIFACTS:
        paths[name], data[name] = load_verified_artifact(release[name])
    package, review, imported, build = (
        data[name] for name in ("package", "review", "source_import", "build")
    )
    package_id = package.get("candidate_package_sha256")
    if (
        package.get("contract") != "EvaComponentReleaseCandidate/v1"
        or package.get("state") != "awaiting_measured_gain_review"
        or package.get("installed") is not False
        or package_id
        != digest(canonical({k: v for k, v in package.items() if k != "candidate_package_sha256"}))
    ):
        raise ValueError("approved package binding differs")
    source = package.get("candidate_content")
    if (
        not isinstance(source, str)
        or digest(source.encode("utf-8")) != package.get("candidate_sha256")
        or digest(canonical(package.get("evaluation"))) != package.get("evaluation_sha256")
    ):
        raise ValueError("approved source binding differs")
    if review_id != package_id:
        raise ValueError("review identifier must equal the exact package hash")
    from eva.operations.component_review_authority import validate_component_review

    evidence = validate_component_review(
        package,
        review,
        paths["review"],
        review_authorities=review_authorities,
        allow_legacy=allow_legacy,
    )
    if (
        imported.get("contract") != "EvaComponentSourceImport/v1"
        or imported.get("state") != "source_imported_not_installed"
        or imported.get("installed") is not False
        or imported.get("candidate_package_sha256") != package_id
        or imported.get("evaluation_sha256") != package["evaluation_sha256"]
        or imported.get("baseline_sha256") != package.get("baseline_sha256")
        or imported.get("candidate_sha256") != package["candidate_sha256"]
        or imported.get("path") != "src/eva/" + package["path"]
        or imported.get("review_sha256") != digest(canonical(review))
        or imported.get("reviewer") != review["reviewer"]
        or imported.get("evidence") != evidence
    ):
        raise ValueError("source import is not bound to the accepted review")
    changed = image_update_services(data["installed"], data["candidate"])
    if set(changed) != {"core", "delegated-work-worker"}:
        raise ValueError("reviewed application requires both core and worker images")
    before = {name: data["installed"]["services"][name]["image"] for name in changed}
    after = {name: data["candidate"]["services"][name]["image"] for name in changed}
    for image in (*before.values(), *after.values()):
        if not isinstance(image, str) or not image.startswith("sha256:"):
            raise ValueError("application images must be immutable")
        validate_identifier(image[7:])
    if (
        build.get("installed") is not False
        or build.get("before") != before
        or build.get("images") != after
        or build.get("sourceHashes", {}).get(package["path"]) != package["candidate_sha256"]
    ):
        raise ValueError("build images are not bound to the reviewed source")
    receipt = validate_host_path(release["update_receipt"], exists=False)
    if receipt in paths.values():
        raise ValueError("updater receipt collides with an input artifact")
    parent = receipt.parent
    info = parent.stat()
    if not parent.is_dir() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("updater receipt directory must be host-owned")
    behavior: dict[str, Any] = {}
    if "behavior_verifier" in release:
        binding = release["behavior_verifier"]
        verify_behavior_source(binding)
        if build.get("behavior_verifier") != binding:
            raise ValueError("build behavior verifier binding differs")
        behavior = {
            "behavior_verifier": binding,
            "baseline_source_sha256": package["baseline_sha256"],
            "candidate_source_sha256": package["candidate_sha256"],
        }
    return {
        "review_id": review_id,
        "project": release["project"],
        "installed": paths["installed"],
        "candidate": paths["candidate"],
        "receipt": receipt,
        "candidate_images": after,
        "accepted_review_sha256": digest(canonical(review)),
        "source_path": package["path"],
        **behavior,
    }


def validate_task_identifier(task_id: Any) -> str:
    if (
        not isinstance(task_id, str)
        or not 1 <= len(task_id) <= 256
        or any(ord(c) < 33 or ord(c) > 126 for c in task_id)
    ):
        raise ValueError("application requires an exact originating task ID")
    return task_id


def validate_application_job_payload(
    job: Any,
    path_name: str,
    *,
    allowed_states: set[str] | frozenset[str],
) -> None:
    if (
        not isinstance(job, dict)
        or job.get("contract") != "EvaReviewedComponentApplication/v1"
        or path_name != validate_identifier(job.get("request_id")) + ".json"
        or job.get("state") not in allowed_states
    ):
        raise ValueError("application receipt differs")
    validate_task_identifier(job.get("task_id"))
    validate_identifier(job.get("review_id"))
    validate_identifier(job.get("release_sha256"))
    validate_identifier(job.get("accepted_review_sha256"))


def evaluate_updater_receipt(
    release: dict[str, Any],
    receipt: dict[str, Any],
    *,
    verified: dict[str, Any],
    task_id: str,
) -> dict[str, Any] | None:
    """Pure reconciliation of a host updater receipt against a verified release."""
    if release.get("kind") == "owned_package":
        from eva.operations.owned_package_application import receipt_confirms

        if receipt_confirms(receipt, verified["package"], task_id):
            return {
                "state": "completed",
                "reason": "package_catalog_verified_semantic_outcome_open",
            }
        return None

    if release.get("kind") == "saved_artifact":
        from eva.operations.saved_artifact_application import receipt_confirms_application

        if not receipt_confirms_application(receipt, verified["publication"], task_id):
            return None
        return {
            "state": "completed",
            "saved_artifact": {
                "reference": receipt["reference"],
                "publication": receipt["publication"],
                "old_source_unchanged": True,
            },
            "clear_reason": True,
        }

    candidate = load_verified_artifact(release["candidate"])[1]
    candidate_images = verified.get("candidate_images")
    images = (
        dict(candidate_images)
        if isinstance(candidate_images, dict) and candidate_images
        else {
            name: candidate["services"][name]["image"]
            for name in ("core", "delegated-work-worker")
        }
    )
    services = receipt.get("services", {})
    behavior_required = "behavior_verifier" in release

    if behavior_required and receipt.get("status") == "baseline_recovered_with_retained_mounts":
        baseline = load_verified_artifact(release["installed"])[1]
        baseline_images = {name: baseline["services"][name]["image"] for name in images}
        restored = receipt.get("recovery", {}).get("behavior_verification", {})
        if (
            receipt.get("contract") != "EvaImageUpdate/v1"
            or receipt.get("project") != release["project"]
            or set(services) != set(images)
            or any(
                services[name].get("previous_image") != image
                for name, image in baseline_images.items()
            )
            or not behavior_proof_confirms(
                restored,
                release=release,
                configuration=baseline,
                images=baseline_images,
                containers=receipt.get("recovery", {}).get("current_containers", {}),
                target="baseline",
            )
        ):
            raise ValueError("baseline recovery behavior evidence differs")
        return {"state": "recovered", "reason": "candidate_failed_baseline_preserved"}

    behavior = receipt.get("behavior_verification", {})
    verified_behavior = not behavior_required or behavior_proof_confirms(
        behavior,
        release=release,
        configuration=candidate,
        images=images,
        containers={name: row.get("current_container") for name, row in services.items()},
        target="candidate",
    )
    success = (
        receipt.get("contract") == "EvaImageUpdate/v1"
        and receipt.get("project") == release["project"]
        and (
            receipt.get("status") == "behavior_verified_with_retained_mounts" and verified_behavior
            if behavior_required
            else receipt.get("status") == "running_with_retained_mounts"
        )
        and set(services) == set(images)
        and all(
            services[name].get("candidate_image") == image
            and services[name].get("current_container")
            for name, image in images.items()
        )
    )
    if success:
        return {"state": "completed", "clear_reason": True}
    if behavior_required:
        return {"state": "unconfirmed", "reason": "behavior_verification_unconfirmed"}
    return None


__all__ = [
    "ARTIFACTS",
    "behavior_proof_confirms",
    "evaluate_updater_receipt",
    "load_verified_artifact",
    "validate_application_job_payload",
    "validate_host_path",
    "validate_identifier",
    "validate_task_identifier",
    "verify_behavior_source",
    "verify_reviewed_release",
]
