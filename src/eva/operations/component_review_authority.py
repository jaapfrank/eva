"""Bind component approvals to independently configured host assessment authority.

A label or a self-authored decision file is not an approval authority. The caller
must supply the existing host's frozen registration, scorer and target bindings.
Legacy reviews are readable only when an explicit historical recovery asks for it.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from eva.operations.component_release_import import canonical, checked_evidence, digest, parse_object

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_NAMES = ("measured_gain_evidence", "independent_review_evidence")


def _host_file(path):
    path = Path(path)
    if not path.is_absolute():
        path = path.absolute()
    if ".." in path.parts or path.resolve() != path:
        raise ValueError("review authority paths must not follow symbolic links")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise ValueError("review authority requires host-owned regular files")
    return path



def _review_context(path):
    path = Path(path)
    if not path.is_absolute():
        path = path.absolute()
    if path.exists() or path.is_symlink():
        return _host_file(path)
    if ".." in path.parts or path.resolve() != path:
        raise ValueError("review authority paths must not follow symbolic links")
    info = path.parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise ValueError("new review context requires a host-owned directory")
    return path


def _evidence(review_file, entry, *, json_object=False):
    if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
            or not isinstance(entry["path"], str) or not isinstance(entry["sha256"], str)
            or not _SHA.fullmatch(entry["sha256"])):
        raise ValueError("review authority evidence binding differs")
    path = Path(entry["path"])
    if not path.is_absolute():
        path = review_file.parent / path
    path = _host_file(path)
    with path.open("rb") as stream:
        raw = stream.read(8_388_609)
    if not raw or len(raw) > 8_388_608 or digest(raw) != entry["sha256"]:
        raise ValueError("review evidence is absent, oversized, or changed")
    binding = {"path": str(path), "sha256": digest(raw)}
    return (binding, parse_object(raw)) if json_object else binding


def validate_component_review(package, review, review_file, *, review_authorities=None,
                              allow_legacy=False):
    """Return normalized evidence only after exact package and authority checks.

    ``review_authorities`` comes from host configuration, never from the review.
    ``allow_legacy`` grants only validation of retained bytes; callers must never
    use it for registration, a new request, or an updater dispatch.
    """
    package_id = package.get("candidate_package_sha256") if isinstance(package, dict) else None
    if (not isinstance(package_id, str) or not _SHA.fullmatch(package_id)
            or package.get("contract") != "EvaComponentReleaseCandidate/v1"
            or package.get("state") != "awaiting_measured_gain_review"
            or package.get("installed") is not False
            or digest(canonical({k: v for k, v in package.items()
                                 if k != "candidate_package_sha256"})) != package_id
            or not isinstance(package.get("baseline_sha256"), str)
            or not _SHA.fullmatch(package["baseline_sha256"])
            or not isinstance(package.get("candidate_content"), str)
            or digest(package["candidate_content"].encode()) != package.get("candidate_sha256")
            or digest(canonical(package.get("evaluation"))) != package.get("evaluation_sha256")):
        raise ValueError("component review package binding differs")
    if (not isinstance(review, dict) or review.get("decision") != "accept"
            or review.get("candidate_package_sha256") != package_id
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
            or len(review["reviewer"].encode()) > 1024):
        raise ValueError("operator review does not accept this exact package")
    review_file = Path(review_file)
    if review.get("contract") == "EvaComponentReleaseReview/v1" and allow_legacy:
        return {name: checked_evidence(review_file, review.get(name)) for name in _NAMES}
    if review.get("contract") != "EvaComponentReleaseReview/v2":
        raise ValueError("component review requires host-authorized review v2")
    review_file = _review_context(review_file)
    decision_evidence, decision = _evidence(review_file, review.get("measured_gain_evidence"),
                                             json_object=True)
    evidence = {"measured_gain_evidence": decision_evidence,
                "independent_review_evidence": _evidence(review_file, review.get("independent_review_evidence"))}
    identity = review.get("reviewer_identity")
    prefix = "host-final-assessment:"
    if not isinstance(identity, str) or not identity.startswith(prefix):
        raise ValueError("component reviewer identity differs")
    registration_id = identity[len(prefix):]
    if not _SHA.fullmatch(registration_id):
        raise ValueError("component reviewer identity differs")
    authority = review_authorities.get(registration_id) if isinstance(review_authorities, dict) else None
    if (not isinstance(authority, dict) or set(authority) != {
            "registration", "scorer", "path", "source_target", "baseline_sha256"}
            or authority["path"] != package.get("path")
            or authority["source_target"] != package.get("source_target")
            or authority["baseline_sha256"] != package.get("baseline_sha256")):
        raise ValueError("component review has no matching host authority")
    registration, _ = _evidence(review_file, authority["registration"], json_object=True)
    scorer = _evidence(review_file, authority["scorer"])
    if (registration["sha256"] != registration_id
            or evidence["independent_review_evidence"] != registration):
        raise ValueError("component review registration binding differs")
    binding = decision.get("binding")
    if (decision.get("contract") != "EvaIndependentComponentFinalDecision/v1"
            or decision.get("decision") != "accept"
            or decision.get("semantic_improvement_verified") is not True
            or decision.get("answer_assessment_required") is not False
            or decision.get("candidate_package_sha256") != package_id
            or decision.get("candidate_sha256") != package.get("candidate_sha256")
            or decision.get("baseline_sha256") != package.get("baseline_sha256")
            or decision.get("reviewer_identity") != identity
            or not isinstance(binding, dict)
            or binding.get("registration_sha256") != registration_id
            or binding.get("scorer_sha256") != scorer["sha256"]
            or binding.get("candidate_sha256") != package.get("candidate_sha256")
            or binding.get("baseline_sha256") != package.get("baseline_sha256")):
        raise ValueError("independent component decision binding differs")
    return evidence
