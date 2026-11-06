"""Approvals require exact package identity and independent configured authority."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from eva.operations.component_release_import import canonical, digest
from eva.operations.component_review_authority import validate_component_review


def write_json(path, value):
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def write_review_authority(directory, package, *, reviewer="controlled-independent-reviewer"):
    """Controlled host fixtures, never an acceptance result for production code."""
    directory.mkdir(parents=True, exist_ok=True)
    registration = write_json(directory / "registration.json", {"controlled_independent_registration": True,
        "path": package["path"], "baseline_sha256": package["baseline_sha256"],
        "source_target": package.get("source_target")})
    scorer = directory / "scorer.py"
    scorer.write_text("# Fixed independent fixture scorer; never imported by these tests.\n")
    scorer_binding = {"path": str(scorer), "sha256": digest(scorer.read_bytes())}
    identity = "host-final-assessment:" + registration["sha256"]
    decision = {"contract": "EvaIndependentComponentFinalDecision/v1", "decision": "accept",
        "candidate_package_sha256": package["candidate_package_sha256"],
        "candidate_sha256": package["candidate_sha256"], "baseline_sha256": package["baseline_sha256"],
        "reviewer_identity": identity, "semantic_improvement_verified": True,
        "answer_assessment_required": False,
        "binding": {"registration_sha256": registration["sha256"],
            "scorer_sha256": scorer_binding["sha256"], "candidate_sha256": package["candidate_sha256"],
            "baseline_sha256": package["baseline_sha256"]}}
    evidence = write_json(directory / "decision.json", decision)
    review = {"contract": "EvaComponentReleaseReview/v2", "decision": "accept",
        "candidate_package_sha256": package["candidate_package_sha256"], "reviewer": reviewer,
        "reviewer_identity": identity, "measured_gain_evidence": evidence,
        "independent_review_evidence": deepcopy(registration)}
    authorities = {registration["sha256"]: {"registration": deepcopy(registration), "scorer": deepcopy(scorer_binding),
        "path": package["path"], "source_target": package.get("source_target"),
        "baseline_sha256": package["baseline_sha256"]}}
    return review, authorities


@pytest.fixture
def accepted(tmp_path):
    package = {"contract": "EvaComponentReleaseCandidate/v1", "state": "awaiting_measured_gain_review",
        "installed": False, "path": "contracts/timestamps.py", "candidate_content": "VALUE = 1\n",
        "candidate_sha256": digest(b"VALUE = 1\n"), "baseline_sha256": "b" * 64,
        "evaluation": {"controlled": True}, "evaluation_sha256": digest(canonical({"controlled": True}))}
    package["candidate_package_sha256"] = digest(canonical(package))
    review, authorities = write_review_authority(tmp_path, package)
    file = tmp_path / "review.json";write_json(file, review)
    return package, review, file, authorities


def validate(data, **kw):
    package, review, file, authorities = data
    return validate_component_review(package, review, file, review_authorities=authorities, **kw)


def test_exact_package_review_identity_and_host_authority_are_required(accepted):
    package, review, file, authorities = accepted
    assert validate(accepted) == {name: review[name] for name in
        ("measured_gain_evidence", "independent_review_evidence")}
    for authority in (None, {}, {"f" * 64: next(iter(authorities.values()))}):
        with pytest.raises(ValueError, match="host authority"):
            validate_component_review(package, review, file, review_authorities=authority)


@pytest.mark.parametrize("field,value", [("candidate_package_sha256", "a" * 64),
    ("candidate_sha256", "a" * 64), ("baseline_sha256", "a" * 64),
    ("decision", "reject"), ("semantic_improvement_verified", False),
    ("answer_assessment_required", True), ("reviewer_identity", "any-name")])
def test_wrong_or_incomplete_decision_is_not_authority(accepted, field, value):
    _, review, _, _ = accepted
    path = Path(review["measured_gain_evidence"]["path"])
    decision = json.loads(path.read_text());decision[field] = value
    review["measured_gain_evidence"] = write_json(path, decision)
    with pytest.raises(ValueError, match="decision binding"):
        validate(accepted)


@pytest.mark.parametrize("field", ["registration_sha256", "scorer_sha256", "candidate_sha256", "baseline_sha256"])
def test_decision_must_bind_the_actual_host_scorer_and_registration(accepted, field):
    _, review, _, _ = accepted
    path = Path(review["measured_gain_evidence"]["path"])
    decision = json.loads(path.read_text());decision["binding"][field] = "f" * 64
    review["measured_gain_evidence"] = write_json(path, decision)
    with pytest.raises(ValueError, match="decision binding"):
        validate(accepted)


@pytest.mark.parametrize("field,value", [("path", "application/other.py"),
    ("source_target", {"kind": "saved_artifact"}), ("baseline_sha256", "a" * 64)])
def test_anchor_limits_component_baseline_and_saved_target(accepted, field, value):
    next(iter(accepted[3].values()))[field] = value
    with pytest.raises(ValueError, match="host authority"):
        validate(accepted)


@pytest.mark.parametrize("artifact", ["registration", "scorer", "decision"])
def test_host_artifacts_are_immutable_and_symlinks_or_writable_inputs_are_not_authority(accepted, artifact, tmp_path):
    _, review, _, authorities = accepted
    authority = next(iter(authorities.values()))
    binding = (review["measured_gain_evidence"] if artifact == "decision" else authority[artifact])
    path = Path(binding["path"])
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="changed"):
        validate(accepted)
    # Correct bytes cannot make writable/symlink host input trusted.
    path.write_bytes(original)
    path.chmod(0o666)
    with pytest.raises(ValueError, match="host-owned"):
        validate(accepted)
    path.chmod(0o600)
    link = tmp_path / (artifact + "-link");link.symlink_to(path)
    binding["path"] = str(link)
    with pytest.raises(ValueError, match="symbolic links"):
        validate(accepted)


def test_legacy_review_is_only_explicitly_readable_never_new_authority(accepted):
    package, review, file, _ = accepted
    review["contract"] = "EvaComponentReleaseReview/v1"
    review.pop("reviewer_identity")
    with pytest.raises(ValueError, match="review v2"):
        validate(accepted)
    assert validate_component_review(package, review, file, allow_legacy=True)
    Path(review["measured_gain_evidence"]["path"]).write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        validate_component_review(package, review, file, allow_legacy=True)


def test_claimed_authority_inside_review_cannot_replace_host_configuration(accepted):
    package, review, file, authorities = accepted
    review["review_authorities"] = deepcopy(authorities)
    with pytest.raises(ValueError, match="host authority"):
        validate_component_review(package, review, file)


def test_publisher_can_validate_before_review_is_written_only_in_trusted_directory(accepted, tmp_path):
    package, review, file, authorities = accepted
    file.unlink()
    assert validate_component_review(package, review, file, review_authorities=authorities)
    assert not file.exists()  # Validation does not write an approval.
    writable = tmp_path / "writable";writable.mkdir();writable.chmod(0o777)
    with pytest.raises(ValueError, match="host-owned directory"):
        validate_component_review(package, review, writable / "review.json", review_authorities=authorities)
    link = tmp_path / "linked-parent";link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        validate_component_review(package, review, link / "missing-review.json", review_authorities=authorities)
    with pytest.raises(FileNotFoundError):
        validate_component_review(package, review, tmp_path / "absent" / "review.json", review_authorities=authorities)


def test_same_candidate_bytes_do_not_authorize_a_different_full_package(accepted):
    package, review, _, _ = accepted
    package["expected_gain"] = "A different claim with the same source bytes"
    package["candidate_package_sha256"] = digest(canonical({k: v for k, v in package.items()
        if k != "candidate_package_sha256"}))
    review["candidate_package_sha256"] = package["candidate_package_sha256"]
    with pytest.raises(ValueError, match="decision binding"):
        validate(accepted)


def test_registration_evidence_must_be_the_exact_external_host_file(accepted, tmp_path):
    _, review, _, _ = accepted
    original = Path(review["independent_review_evidence"]["path"])
    duplicate = tmp_path / "same-bytes-other-registration.json"
    duplicate.write_bytes(original.read_bytes())
    review["independent_review_evidence"]["path"] = str(duplicate)
    with pytest.raises(ValueError, match="registration binding"):
        validate(accepted)


def test_ambiguous_or_nonobject_public_decision_cannot_supply_approval(accepted):
    _, review, _, _ = accepted
    path = Path(review["measured_gain_evidence"]["path"])
    for raw in (b'{"decision":"reject","decision":"accept"}', b'[]'):
        path.write_bytes(raw)
        review["measured_gain_evidence"]["sha256"] = digest(raw)
        with pytest.raises(ValueError, match="duplicate JSON field|JSON object"):
            validate(accepted)
