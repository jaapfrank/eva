"""Host assessor provenance propagates without giving labels authority."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.operations.component_final_assessment import HostFinalAssessment, configured_review_authorities
from eva.operations.component_release_import import canonical, digest
from eva.operations.component_release_publisher import ComponentReleasePublisher
from tests.operations.test_component_review_authority import write_review_authority, write_json
from tests.operations.test_saved_artifact_final_assessment import selected


def registered(root, label, baseline):
    root.mkdir()
    registration = write_json(root / "registration.json", {"controlled_registration": label})
    scorer = root / "scorer.py"
    scorer.write_text("# Controlled fixed host scorer: " + label + "\n")
    assessment = {"registration": registration,
                  "scorer": {"path": str(scorer), "sha256": digest(scorer.read_bytes())}}
    protocol = {"registration_sha256": registration["sha256"], "baseline_sha256": baseline}
    return assessment, protocol


def host_configuration(tmp_path):
    current, protocol = registered(tmp_path / "current", "current", "a" * 64)
    old, retained = registered(tmp_path / "retained", "retained", "b" * 64)
    return {"assessments": {"contracts/timestamps.py": current},
            "protocols": {"contracts/timestamps.py": protocol},
            "retained_rounds": {retained["registration_sha256"]: {
                "path": "contracts/timestamps.py", "assessment": old,
                "protocol": retained, "publisher": {}}}}


def test_authorities_come_from_both_existing_host_rounds_with_exact_baseline_scope(tmp_path):
    config = host_configuration(tmp_path)
    authorities = configured_review_authorities(config)
    assert len(authorities) == 2
    for identity, authority in authorities.items():
        assert authority["registration"]["sha256"] == identity
        assert authority["path"] == "contracts/timestamps.py"
        assert authority["baseline_sha256"] in {"a" * 64, "b" * 64}
        assert authority["source_target"] is None
    # The returned policy cannot be mutated through the input config.
    config["assessments"]["contracts/timestamps.py"]["scorer"]["sha256"] = "f" * 64
    assert all(a["scorer"]["sha256"] != "f" * 64 for a in authorities.values())


@pytest.mark.parametrize("change", ["registration", "scorer", "protocol", "retained_identity", "source_target"])
def test_unbound_or_changed_host_registration_never_becomes_authority(tmp_path, change):
    config = host_configuration(tmp_path)
    current = config["assessments"]["contracts/timestamps.py"]
    if change in {"registration", "scorer"}:
        Path(current[change]["path"]).write_text("changed")
    elif change == "source_target":
        current["source_target"] = {"kind": "saved_artifact"}
    elif change == "protocol":
        config["protocols"]["contracts/timestamps.py"]["registration_sha256"] = "f" * 64
    else:
        entry = next(iter(config["retained_rounds"].values()))
        config["retained_rounds"] = {"f" * 64: entry}
    with pytest.raises(ValueError):
        configured_review_authorities(config)


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_real_host_decision_adds_exact_package_and_scorer_identity_without_rewriting_scorer_proof(tmp_path, decision):
    package = selected()
    assessment, _ = registered(tmp_path / "authority", "actual-host", package["baseline_sha256"])
    report = {"controlled_isolated_report": True}
    result = {"decision": decision, "semantic_improvement_verified": decision == "accept",
              "binding": {"candidate_sha256": package["candidate_sha256"],
                          "baseline_sha256": package["baseline_sha256"],
                          "registration_sha256": assessment["registration"]["sha256"],
                          "isolated_report_sha256": digest(canonical(report))}}
    original = deepcopy(result)
    host = HostFinalAssessment.__new__(HostFinalAssessment)
    host.config = assessment
    host.scorer = SimpleNamespace(assess=lambda *args, **kwargs: deepcopy(result))
    output = tmp_path / "output"
    output.mkdir()
    verdict = host._decide(package, report, output)
    public = verdict["public"]
    assert public["candidate_package_sha256"] == package["candidate_package_sha256"]
    assert public["reviewer_identity"] == "host-final-assessment:" + assessment["registration"]["sha256"]
    assert public["binding"]["scorer_sha256"] == assessment["scorer"]["sha256"]
    assert json.loads((output / "decision.private.json").read_text()) == original
    assert json.loads(Path(verdict["evidence"]["path"]).read_text()) == public


def publication(tmp_path, *, authorized=True):
    package = selected()
    review, authorities = write_review_authority(tmp_path / "authority", package)
    installed = write_json(tmp_path / "installed.json", {"services": {}})
    called = []
    publisher = ComponentReleasePublisher(
        {"repository": str(tmp_path / "repository"), "integration_commit": "a" * 40,
         "installed": installed, "project": "controlled-only", "reviewer": "arbitrary display label"},
        run=lambda *args, **kwargs: called.append(args),
        review_authorities=authorities if authorized else {})
    verdict = {"decision": "accept",
               "public": json.loads(Path(review["measured_gain_evidence"]["path"]).read_text()),
               "evidence": review["measured_gain_evidence"],
               "registration": review["independent_review_evidence"]}
    output = tmp_path / "publication"
    output.mkdir()
    write_json(output / "package.json", package)
    return package, publisher, verdict, output, called


def test_display_label_cannot_authorize_publication_without_the_registered_host(tmp_path):
    package, publisher, verdict, output, called = publication(tmp_path, authorized=False)
    with pytest.raises(ValueError, match="host authority"):
        publisher(package, verdict, output)
    assert not called
    assert not (output / "review.json").exists()


def test_forged_public_acceptance_is_rejected_before_review_or_build(tmp_path):
    package, publisher, verdict, output, called = publication(tmp_path)
    verdict["public"]["candidate_package_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="retained proof"):
        publisher(package, verdict, output)
    assert not called
    assert not (output / "review.json").exists()


def test_valid_saved_publication_is_bound_to_host_identity_and_never_runs_candidate_on_host(tmp_path):
    package, publisher, verdict, output, called = publication(tmp_path)
    release = publisher(package, verdict, output)
    review = json.loads(Path(release["review"]["path"]).read_text())
    assert review["contract"] == "EvaComponentReleaseReview/v2"
    assert review["reviewer_identity"] == verdict["public"]["reviewer_identity"]
    assert review["candidate_package_sha256"] == package["candidate_package_sha256"]
    assert not called
