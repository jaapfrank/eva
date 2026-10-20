"""No-DB admission checks for exact reviewed package resources; no grant bypass."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from eva.adapters.postgres.initiative_execution import (
    InitiativeMandateDenied, NativeInitiativeGuard, PostgresInitiativeAuthority,
    ReviewedNativeSavedArtifactGuard,
)
from eva.application.skill_packages import SkillResource, AgentSkillPackage
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.saved_artifact_application import (
    _destination, _skill_publication, materialize_candidate_artifact,
)
from tests.application.test_skill_package_improvements import fixture


class ReachedOriginalClaim(Exception):
    """Sentinel at the unchanged original claim validation boundary."""


def accepted_publication():
    original, _, target, release = fixture()
    resource = target["skill_package_binding"]["resource_path"]
    baseline = original.resource(resource) + b"#" + b"x" * 17000 + b"\n"
    original = replace(original, files=tuple(SkillResource(item.path, baseline)
        if item.path == resource else item for item in original.files))
    wire = canonical_json_bytes(original.to_wire()).decode()
    target = deepcopy(target)
    target["content_sha256"] = hashlib.sha256(baseline).hexdigest()
    target["skill_package_binding"].update(package_content_sha256=hashlib.sha256(wire.encode()).hexdigest(),
        package_manifest=original.manifest())
    candidate = baseline.decode().replace("VALUE = 1", "VALUE = 2", 1)
    candidate_sha = hashlib.sha256(candidate.encode()).hexdigest()
    publication = {"contract": "EvaSavedArtifactPublication/v1", "review_id": "a" * 64,
        "source_target": target, "baseline_sha256": target["content_sha256"],
        "candidate_sha256": candidate_sha, "destination_path": _destination(target, candidate_sha),
        "improvement_description": "Synthetic already-reviewed result", **_skill_publication(target, candidate)}
    return publication, candidate, wire


def admission(publication, candidate, monkeypatch, *, granted=True):
    target = publication["source_target"]
    turn = NS(**target["scope"])
    work = NS(spec=NS(grant=NS(capability_ids=("apply_reviewed_component_improvement",) if granted else ()),
        origin=NS(thread_id="synthetic", claim_revision=1)))
    authority = object.__new__(PostgresInitiativeAuthority)
    authority.reviewed_work_current = lambda *a, **k: (work, turn)
    authority.owner = NS(_tenant_id="synthetic", _installation_id="synthetic")
    authority.timeline = NS(events=lambda _: ())
    def original_claim(*args): raise ReachedOriginalClaim
    monkeypatch.setattr("eva.adapters.postgres.conversation_actions.PostgresPersonalInterestStore._claim_from_events",
                        original_claim)
    return authority.bind_reviewed_saved_application("synthetic", publication, candidate)


def test_large_packaged_python_passes_only_new_predicate_and_reaches_original_claim(monkeypatch):
    publication, candidate, _ = accepted_publication()
    assert 16384 < len(candidate.encode()) <= 65536
    assert publication["destination_path"].endswith(".skill.json")
    with pytest.raises(ReachedOriginalClaim):
        admission(publication, candidate, monkeypatch)


@pytest.mark.parametrize("fault", ["missing_manifest", "support", "derivation", "extra_manifest", "destination", "no_grant"])
def test_native_admission_rejects_unreviewed_publication_before_original_claim(monkeypatch, fault):
    publication, candidate, _ = accepted_publication()
    if fault == "missing_manifest": del publication["skill_package"]
    elif fault in {"support", "derivation", "extra_manifest"}:
        manifest = publication["skill_package"]["candidate_manifest"]
        if fault == "support":
            next(item for item in manifest["files"] if item["path"] == "references/note.md")["sha256"] = "f" * 64
        elif fault == "derivation": manifest["derivation"]["parent_package_sha256"] = "f" * 64
        else: manifest["extra"] = "unreviewed"
        # Resealing attacker-supplied metadata is insufficient: host recomputes it.
        publication["skill_package"]["candidate_package_sha256"] = canonical_sha256(manifest)
    elif fault == "destination": publication["destination_path"] += ".py"
    with pytest.raises(InitiativeMandateDenied, match="publication binding"):
        admission(publication, candidate, monkeypatch, granted=fault != "no_grant")


@pytest.mark.parametrize("fault", [None, "support", "license", "origin", "script", "wrong_destination", "wrong_configuration"])
def test_native_write_prefilter_only_passes_exact_reviewed_complete_package_to_existing_guard(monkeypatch, fault):
    publication, candidate, wire = accepted_publication()
    artifact = materialize_candidate_artifact(publication, candidate, {"baseline_package_content": wire})
    if fault in {"support", "license", "script", "origin"}:
        package = AgentSkillPackage.from_wire(json.loads(artifact))
        if fault == "origin": package = replace(package, revision="b" * 40)
        else:
            name = {"support": "references/note.md", "license": "LICENSE",
                    "script": publication["source_target"]["skill_package_binding"]["resource_path"]}[fault]
            package = replace(package, files=tuple(
                SkillResource(item.path, item.content + b" changed") if item.path == name else item
                for item in package.files))
        artifact = canonical_json_bytes(package.to_wire()).decode()
    guard = object.__new__(ReviewedNativeSavedArtifactGuard)
    guard.publication, guard.candidate_content = publication, candidate
    path = publication["destination_path"] + (".other" if fault == "wrong_destination" else "")
    request = NS(arguments={"path": path, "content": artifact},
        tool_configuration_sha256=("f" * 64 if fault == "wrong_configuration"
            else publication["source_target"]["tool_configuration_sha256"]), observation_arguments={"path": path})
    forwarded = []
    # The unchanged superclass still performs the real current grant/dispatch
    # checks. This isolated test proves only the new exact package prefilter.
    monkeypatch.setattr(NativeInitiativeGuard, "authorize_request",
        lambda self, request, *, phase: forwarded.append((request, phase)))
    if fault is None:
        guard.authorize_request(request, phase="execute")
        assert forwarded == [(request, "execute")]
    else:
        with pytest.raises(InitiativeMandateDenied, match="accepted package"):
            guard.authorize_request(request, phase="execute")
        assert forwarded == []
