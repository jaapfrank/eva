"""The scheduled failure oracle gets only bound original diagnostic inputs."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations import runtime_repair_diagnostics as diagnostics
from eva.operations.reviewed_component_application import _artifact
from eva.operations.runtime_repair_development import _put


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def shape():
    def timeline(phase, status, event_type, keys):
        return {
            "checkpoint": {"phase": phase, "status": status, "revision": 1, "operation_id_present": False},
            "events": [{"sequence": 1, "event_type": event_type, "matches_checkpoint_head": True,
                        "canonical_payload_hash_matches": True,
                        "data_keys": sorted(keys), "return_binding_present": False}],
            "event_count": 1,
        }

    return {
        "contract": "EvaScheduledFailureOriginalShape/v1",
        "schedule": timeline("blocked", "needs_help", "SCHEDULED_WORK_BLOCKED",
            {"operation", "work_id", "run_at", "reason", "blocker_code", "automatic_retry"}),
        "recovery_stop": timeline("stopped", "completed", "PRIVATE_WORK_RECOVERY_STOPPED",
            {"work_id", "execution_id", "source_ingress_sha256", "spec_sha256"}),
        "loader_selected_sequences": [1], "first_sequence_equals_checkpoint_revision": True,
        "marker_operation": "resume", "automatic_retry": False,
        "predecessor": {"contract": "DelegatedDigitalWork/v4", "generation": 1, "audience": "private",
                        "state": "ambiguous", "error_code": "provider_unavailable", "call_started_present": True,
                        "lease_present": False, "result_present": False, "stop_requested_present": False},
    }


@pytest.fixture
def input_set(monkeypatch, tmp_path, request):
    paths = (
        "bootstrap/self_repair.py", "adapters/postgres/operational_timeline.py", "contracts/canonical_json.py",
        "adapters/postgres/scheduled_work.py", "adapters/postgres/private_work_return.py",
    )
    sources = {path: ("# installed " + path + "\n").encode() for path in paths}
    target_file = "adapters/postgres/scheduled_work_failure.py"
    baseline = "a" * 64
    target_id = "b" * 64
    identity = "c" * 64
    binding = {"contract": "EvaCurrentRepairBinding/v1", "incident_id": "self-repair:" + identity,
               "intent_id": "personal-interest:" + identity, "incident_event_sha256": "d" * 64,
               "scope_sha256": "e" * 64, "mandate_sha256": "f" * 64, "source_sha256": "0" * 64,
               "observation": {"error_type_sha256": "1" * 64, "frames": [
                   {"path": paths[0], "function": "_failure_observation:10", "source_sha256": sha(sources[paths[0]])},
                   {"path": target_file, "function": "load:93", "source_sha256": baseline},
               ]}}
    target = {"contract": "EvaRuntimeRepairTarget/v2", "target_id": target_id, "repair_services": ["core"],
              "failure": binding, "sources": {target_file: baseline}, "excluded_paths": [paths[0]],
              "installation": {"core": {"image": "sha256:" + "2" * 64, "configuration_sha256": "3" * 64},
                               "delegated-work-worker": {"image": "sha256:" + "4" * 64, "configuration_sha256": "5" * 64}}}
    artifact_path = target_file
    if getattr(request, "param", None) == "paired-v1":
        target["contract"] = "EvaRuntimeRepairTarget/v1"
        del target["repair_services"]
        target_id = canonical_sha256({key: value for key, value in target.items() if key != "target_id"})
        target["target_id"] = target_id
        artifact_path = "/workspace/repair/source/eva/" + target_file
    blocked = {"status": "blocked", "role": "original_behavior_oracle", "target_id": target_id,
               "verified_source": {"path": artifact_path, "expected_sha256": baseline, "actual_sha256": baseline},
               "observations": ["PRIVATE_FREEFORM_SHOULD_NOT_BE_COPIED"],
               "missing_required_controls": ["../../untrusted/path"], "resume_action": "Freeform text",
               "oracle_created": False, "behavioral_tests_executed": False, "candidate_submitted": False,
               "installation_changed": False}
    blocked_receipt = _put(tmp_path / "old-blocked.json", blocked)
    image = target["installation"]["core"]["image"]
    generation = "6" * 64
    commands = []
    facts = shape()

    def command(argv, directory):
        commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps([{"Image": image, "Id": generation,
                "State": {"Running": True, "StartedAt": "fixture-start"},
                "HostConfig": {"ReadonlyRootfs": True}, "Mounts": []}])
        assert argv[:4] == ["docker", "run", "--rm", "--network"]
        assert "--read-only" in argv and "--cap-drop" in argv and image in argv
        assert argv[-3] in sources
        return base64.b64encode(sources[argv[-3]]).decode()

    def run(argv, **kwargs):
        assert argv[:7] == ["docker", "exec", generation, "python", "-B", "-c", diagnostics._CORE_CONTEXT_READER]
        assert json.loads(argv[-1])["binding"] == binding
        assert "SET TRANSACTION READ ONLY" in argv[-2]
        assert kwargs["timeout"] == 45
        return SimpleNamespace(returncode=0, stdout=json.dumps(facts))

    publisher = SimpleNamespace(_command=command)
    runtime = SimpleNamespace(config={"core_container": "fixture-core", "scope_sha256": binding["scope_sha256"]},
                              repair_call=Mock(return_value=binding), run=run)
    install = Mock(return_value=({}, deepcopy(target["installation"])))
    monkeypatch.setattr(diagnostics, "current_repair_installation", install)
    metadata = {"role": "original_behavior_oracle", "target_id": target_id, "incident_id": binding["incident_id"],
                "candidate_sha256": None, "comparison_sha256": None}
    return SimpleNamespace(target=target, metadata=metadata, blocked=blocked, blocked_receipt=blocked_receipt,
                           parent_sha="7" * 64, directory=tmp_path, publisher=publisher, runtime=runtime,
                           install=install, commands=commands, sources=sources, facts=facts)


def prepare(f):
    return diagnostics.prepare_scheduled_failure_diagnostics(
        f.publisher, f.runtime, f.target, f.directory, blocked_receipt=f.blocked_receipt,
        parent_task_sha256=f.parent_sha,
    )


def seed(f, receipt, artifacts, *, parent_sha=None):
    return diagnostics.seed_scheduled_failure_diagnostics(
        artifacts, f.metadata, f.target, receipt, blocked_sha256=f.blocked_receipt["sha256"],
        parent_task_sha256=parent_sha or f.parent_sha,
    )


@pytest.mark.parametrize("input_set", ["core-v2", "paired-v1"], indirect=True)
def test_candidate_blind_diagnostics_are_frozen_from_exact_image(input_set):
    f = input_set
    compile(diagnostics._CORE_CONTEXT_READER, "<fixed core context reader>", "exec")
    original = deepcopy(f.target)
    blocked_path = Path(f.blocked_receipt["path"])
    original_blocked_bytes = blocked_path.read_bytes()
    receipt = prepare(f)
    _, frozen = _artifact(receipt)
    assert f.runtime.repair_call.call_count == 2
    assert f.install.call_count == 2
    assert [argv[-3] for argv in f.commands if argv[1] == "run"] == list(f.sources)
    assert f.target == original
    assert frozen["manifest"]["blocked_sha256"] == f.blocked_receipt["sha256"]
    assert frozen["manifest"]["parent_task_sha256"] == f.parent_sha
    assert frozen["facts"] == f.facts
    assert "PRIVATE_FREEFORM" not in json.dumps(frozen)
    artifacts = SimpleNamespace(_seed=Mock())
    paths = seed(f, receipt, artifacts)
    assert paths["diagnostic_read_only_sources"] == "/workspace/repair/diagnostics/eva"
    metadata, files = artifacts._seed.call_args.args
    assert metadata == f.metadata
    assert files["repair/diagnostics/eva/bootstrap/self_repair.py"] == f.sources["bootstrap/self_repair.py"]
    assert all(relative.startswith("repair/diagnostics/") for relative in files)
    assert "repair/source/eva/bootstrap/self_repair.py" not in files
    assert blocked_path.read_bytes() == original_blocked_bytes


@pytest.mark.parametrize("change", ["image", "caller", "blocked", "private_context", "changed_authority"])
def test_diagnostics_refuse_drift_without_sealing(input_set, change):
    f = input_set
    if change == "image":
        f.install.return_value[1]["core"]["image"] = "sha256:" + "8" * 64
    elif change == "caller":
        f.sources["bootstrap/self_repair.py"] = b"changed original caller"
    elif change == "blocked":
        f.blocked["oracle_created"] = True
        f.blocked_receipt = _put(f.directory / "another-blocked.json", f.blocked)
    elif change == "private_context":
        f.facts["unapproved_private_record"] = "do not transfer"
    else:
        original = f.target["failure"]
        f.runtime.repair_call.side_effect = [original, {**original, "source_sha256": "8" * 64}]
    with pytest.raises(ValueError):
        prepare(f)
    assert not (f.directory / "oracle-diagnostics.private.json").exists()


@pytest.mark.parametrize("change", ["candidate", "role", "parent", "tampered_source"])
def test_diagnostics_seed_only_exact_successor_scope(input_set, change):
    f = input_set
    receipt = prepare(f)
    parent = f.parent_sha
    if change == "candidate":
        f.metadata["candidate_sha256"] = "8" * 64
    elif change == "role":
        f.metadata["role"] = "independent_review"
    elif change == "parent":
        parent = "8" * 64
    else:
        _, value = _artifact(receipt)
        value["source_bytes"]["bootstrap/self_repair.py"] = base64.b64encode(b"changed").decode()
        receipt = _put(f.directory / "tampered.json", value)
    artifacts = SimpleNamespace(_seed=Mock())
    with pytest.raises(ValueError):
        seed(f, receipt, artifacts, parent_sha=parent)
    artifacts._seed.assert_not_called()


@pytest.mark.parametrize("input_set", ["paired-v1"], indirect=True)
@pytest.mark.parametrize("change", ["identity", "extra_editable", "worker_only", "loader_not_observed"])
def test_diagnostics_refuse_changed_paired_scope(input_set, change):
    f = input_set
    if change == "identity":
        f.target["installation"]["delegated-work-worker"]["image"] = "sha256:" + "8" * 64
    elif change == "extra_editable":
        f.target["sources"]["bootstrap/self_repair.py"] = "8" * 64
    elif change == "worker_only":
        f.target["contract"] = "EvaRuntimeRepairTarget/v2"
        f.target["repair_services"] = ["delegated-work-worker"]
    else:
        f.target["sources"]["adapters/postgres/scheduled_work_failure.py"] = "8" * 64
        f.target["target_id"] = canonical_sha256({
            key: value for key, value in f.target.items() if key != "target_id"
        })
    with pytest.raises(ValueError):
        diagnostics._target_binding(f.target)


@pytest.mark.parametrize("input_set", ["paired-v1"], indirect=True)
@pytest.mark.parametrize("change", ["traversal", "different_root", "expected_hash", "actual_hash"])
def test_blocked_workspace_source_requires_exact_spelling_and_digests(input_set, change):
    source = input_set.blocked["verified_source"]
    if change == "traversal":
        source["path"] = source["path"].replace("/postgres/", "/postgres/../postgres/")
    elif change == "different_root":
        source["path"] = source["path"].replace("/repair/source/", "/repair/diagnostics/")
    else:
        key = "expected_sha256" if change == "expected_hash" else "actual_sha256"
        source[key] = "8" * 64
    with pytest.raises(ValueError, match="exact prior candidate-blind blocked oracle"):
        diagnostics.validate_blocked_diagnostic(input_set.target, input_set.blocked)
