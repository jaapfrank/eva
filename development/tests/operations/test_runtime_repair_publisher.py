"""Publication binds existing repair artifacts to exact affected image roles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations import runtime_repair_application, runtime_repair_publisher as publishing


@pytest.mark.parametrize("scoped", [None, "core", "delegated-work-worker"])
def test_publication_builds_only_affected_images_and_retains_both_pins(tmp_path, monkeypatch, scoped):
    services = ("core", "delegated-work-worker")
    selected = services if scoped is None else (scoped,)
    before = dict(zip(services, ("sha256:" + "1" * 64, "sha256:" + "2" * 64)))
    candidates = dict(zip(services, ("sha256:" + "3" * 64, "sha256:" + "4" * 64)))
    pins = {role: {"image": image, "configuration_sha256": "a" * 64} for role, image in before.items()}
    installed = {"services": {role: {"image": image} for role, image in before.items()}}
    path = "application/example.py"
    baseline = b"def behavior():\n    return 1\n"
    changed = "def behavior():\n    return 2\n"
    baseline_hash = hashlib.sha256(baseline).hexdigest()
    changed_hash = hashlib.sha256(changed.encode()).hexdigest()
    target = {"contract": "EvaRuntimeRepairTarget/v1", "target_id": "b" * 64,
              "sources": {path: baseline_hash}, "installation": pins, "criteria": {}}
    if scoped is not None:
        target.update(contract="EvaRuntimeRepairTarget/v2", repair_services=[scoped])
    proposal = {"contract": "EvaRuntimeRepairCandidate/v1", "target_id": target["target_id"],
                "files": [{"path": path, "baseline_sha256": baseline_hash, "candidate_sha256": changed_hash,
                           "candidate_content": changed}], "diagnosis": "Synthetic wrong result"}
    comparison = {"state": "awaiting_independent_review", "services": dict.fromkeys(selected, {})}
    review = {"contract": "EvaIndependentRuntimeRepairReview/v1", "target_id": target["target_id"],
              "candidate_sha256": canonical_sha256(proposal), "comparison_sha256": canonical_sha256(comparison),
              "decision": "accept", "behavioral_reason": "Synthetic original and regressions match.", "findings": []}
    artifacts = {"target": target, "candidate": proposal, "comparison": comparison, "review": review}
    job = {**{key: key for key in artifacts}, "target_id": target["target_id"],
           "candidate_sha256": canonical_sha256(proposal), "review_work": "review_work"}
    monkeypatch.setattr(publishing, "_artifact", lambda ref: (None, deepcopy(artifacts[ref])))
    monkeypatch.setattr(publishing, "current_repair_installation", lambda *_: (installed, pins))
    monkeypatch.setattr(runtime_repair_application, "verify_runtime_repair_release", lambda *_: None)
    source_reads = []

    def read_source(publisher, image, relative, directory):
        assert relative == path
        source_reads.append(image)
        assert image in {pin for role in selected for pin in (before[role], candidates[role])}
        return baseline if image in before.values() else changed.encode()

    monkeypatch.setattr(publishing, "read_image_source", read_source)
    builds = []

    def command(argv, directory):
        if "worktree" in argv:
            Path(argv[-2]).mkdir()
        if "rev-parse" in argv:
            return "c" * 40
        if argv[:2] == ["docker", "build"]:
            context = Path(argv[-1])
            role = context.name.removeprefix("repair-build-")
            builds.append(role)
            assert (context / "Dockerfile").read_text() == f"FROM {before[role]}\nCOPY source-0.py /opt/eva/src/eva/{path}\n"
            assert (context / "source-0.py").read_text() == changed
            Path(argv[argv.index("--iidfile") + 1]).write_text(candidates[role])
        return ""

    publisher = SimpleNamespace(repository=tmp_path, config={"project": "test"}, _command=command)
    release = publishing.publish_runtime_repair(publisher, job, tmp_path)
    build = json.loads(Path(release["build"]["path"]).read_bytes())
    candidate = json.loads(Path(release["candidate"]["path"]).read_bytes())
    expected = {**before, **{role: candidates[role] for role in selected}}
    assert builds == list(selected)
    assert source_reads == [before[selected[0]]] + [pin for role in selected for pin in (before[role], candidates[role])]
    assert build["before"] == before and build["images"] == expected
    assert {role: entry["image"] for role, entry in candidate["services"].items()} == expected
