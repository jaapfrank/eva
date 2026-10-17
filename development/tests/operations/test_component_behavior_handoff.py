"""Trusted behavior handoff and receipt boundaries; no deployment/model calls."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import ComponentApplicationRuntime
from eva.operations.component_release_publisher import ComponentReleasePublisher
from eva.operations.reviewed_component_application import ReviewedComponentApplications, _verify_release
from tests.operations.test_reviewed_component_application import prepared, write_json, REVIEW_ID, REQUEST_ID, TASK_ID


@pytest.fixture
def behavior(prepared, tmp_path):
    path = tmp_path / "trusted_probe.py"
    path.write_text("def verify(observation, *, source_sha256, run):\n    return {'verified': True, 'source_sha256': source_sha256, 'target_seen': observation['target']}\n")
    binding = {"path": str(path), "sha256": __import__('hashlib').sha256(path.read_bytes()).hexdigest()}
    release = prepared["releases"][REVIEW_ID]
    release["behavior_verifier"] = binding
    build = json.loads(Path(release["build"]["path"]).read_bytes())
    build["behavior_verifier"] = binding
    release["build"] = write_json(Path(release["build"]["path"]), build)
    return prepared, release, binding


def runtime(binding, **kwargs):
    return ComponentApplicationRuntime({"core_container": "synthetic-core", "scope_sha256": "a" * 64,
        "behavior_verifiers": {"contracts/timestamps.py": binding}}, **kwargs)


def receipt(release, *, baseline=False):
    installed = json.loads(Path(release["installed"]["path"]).read_bytes())
    candidate = json.loads(Path(release["candidate"]["path"]).read_bytes())
    names = ("core", "delegated-work-worker")
    images = {name: (installed if baseline else candidate)["services"][name]["image"] for name in names}
    containers = {name: "observed-" + name for name in names}
    proof = {"contract": "EvaImageBehaviorVerification/v1", "target": "baseline" if baseline else "candidate",
        "project": release["project"], "images": images, "containers": containers,
        "configuration_sha256": canonical_sha256(installed if baseline else candidate),
        "verified": True, "evidence": {"verified": True, "verifier_sha256": release["behavior_verifier"]["sha256"]}}
    value = {"contract": "EvaImageUpdate/v1", "project": release["project"],
        "status": "baseline_recovered_with_retained_mounts" if baseline else "behavior_verified_with_retained_mounts",
        "services": {name: {"previous_image": installed["services"][name]["image"],
            "candidate_image": candidate["services"][name]["image"], "current_container": containers[name]} for name in names}}
    if baseline:
        value["recovery"] = {"behavior_verification": proof, "dispatch_attempted": True, "current_containers": containers}
    else:
        value["behavior_verification"] = proof
    return value


def reconciler(config, state="unconfirmed", inspect=None):
    # Exercise the real synchronous reconciliation without its background worker.
    app = ReviewedComponentApplications.__new__(ReviewedComponentApplications)
    app.releases = config["releases"]
    app.inspect_release = inspect
    app.root = Path(config["receipt_root"])
    app.root.mkdir(mode=0o700, exist_ok=True)
    release = app.releases[REVIEW_ID]
    verified = _verify_release(REVIEW_ID, release)
    job = {"contract": "EvaReviewedComponentApplication/v1", "request_id": REQUEST_ID,
        "review_id": REVIEW_ID, "task_id": TASK_ID, "state": state,
        "release_sha256": canonical_sha256(release), "accepted_review_sha256": verified["accepted_review_sha256"]}
    app.jobs = {REQUEST_ID: job}
    app.condition = threading.Condition()
    return app, job


def test_fixed_probe_checks_package_bound_source_hash_for_each_target(behavior):
    _, release, binding = behavior
    checked = _verify_release(REVIEW_ID, release)
    callback = runtime(binding)._behavior_verifier(checked)
    for target in ("candidate", "baseline"):
        result = callback({"target": target})
        assert result["verified"] is True
        assert result["verifier_sha256"] == binding["sha256"]
        assert result["source_sha256"] == checked[target + "_source_sha256"]


def test_runtime_rejects_different_probe_authority_even_with_valid_source(behavior):
    _, release, binding = behavior
    checked = _verify_release(REVIEW_ID, release)
    other = Path(binding["path"]).with_name("other.py")
    other.write_text(Path(binding["path"]).read_text())
    with pytest.raises(ValueError, match="authority differs"):
        runtime(binding)._behavior_verifier(checked | {"behavior_verifier": binding | {"path": str(other)}})


def test_probe_hash_change_is_rejected_before_execution(behavior):
    _, release, binding = behavior
    checked = _verify_release(REVIEW_ID, release)
    host = runtime(binding)
    Path(binding["path"]).write_text("raise AssertionError('changed probe may never execute')")
    with pytest.raises(ValueError, match="source differs"):
        host._behavior_verifier(checked)


def test_release_cannot_change_probe_without_matching_build_attestation(behavior):
    _, release, binding = behavior
    other = Path(binding["path"]).with_name("different.py")
    other.write_text("def verify(*a, **k): return {'verified': True}\n")
    release["behavior_verifier"] = {"path": str(other), "sha256": __import__('hashlib').sha256(other.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="build behavior verifier binding"):
        _verify_release(REVIEW_ID, release)


@pytest.mark.parametrize("baseline", [False, True])
def test_valid_bound_receipt_distinguishes_candidate_and_recovered_reference(behavior, baseline):
    config, release, _ = behavior
    write_json(Path(release["update_receipt"]), receipt(release, baseline=baseline))
    app, job = reconciler(config)
    app._reconcile(job)
    assert job["state"] == ("recovered" if baseline else "completed")
    assert app._result(job)["installed"] is (not baseline)


@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("corruption", ["contract", "project", "configuration", "containers", "false_evidence", "verifier"])
def test_fake_behavior_receipt_cannot_confirm_installation_or_recovery(behavior, baseline, corruption):
    config, release, _ = behavior
    value = receipt(release, baseline=baseline)
    proof = value["recovery"]["behavior_verification"] if baseline else value["behavior_verification"]
    if corruption == "contract": proof["contract"] = "OtherProof/v1"
    elif corruption == "project": proof["project"] = "another-installation"
    elif corruption == "configuration": proof["configuration_sha256"] = "0" * 64
    elif corruption == "containers": proof["containers"] = {"other-service": "unrelated"}
    elif corruption == "false_evidence": proof["evidence"]["verified"] = False
    else: proof["evidence"]["verifier_sha256"] = "0" * 64
    write_json(Path(release["update_receipt"]), value)
    app, job = reconciler(config)
    app._reconcile(job)
    assert job["state"] == "unconfirmed"
    assert app._result(job)["installed"] is False


def test_unknown_fresh_inspection_clears_green_and_can_be_rechecked_on_later_status(behavior):
    config, release, _ = behavior
    write_json(Path(release["update_receipt"]), receipt(release))
    calls = []
    def inspect(update):
        calls.append(update)
        if len(calls) == 1:
            value = receipt(release)
            value["status"] = "incomplete_inspect_retained_installation"
            write_json(Path(release["update_receipt"]), value)
            raise RuntimeError("synthetic transient UNKNOWN")
        write_json(Path(release["update_receipt"]), receipt(release))
    app, job = reconciler(config, state="completed", inspect=inspect)
    first = app.status(REQUEST_ID, task_id=TASK_ID)
    assert first["state"] == "unconfirmed" and first["installed"] is False
    second = app.status(REQUEST_ID, task_id=TASK_ID)
    assert second["state"] == "completed" and len(calls) == 2


def test_later_regression_uses_existing_inspection_without_application_replay(behavior):
    config, release, _ = behavior
    write_json(Path(release["update_receipt"]), receipt(release))
    calls = []
    def inspect(update):
        calls.append(update)
        write_json(Path(release["update_receipt"]), receipt(release, baseline=True))
    app, job = reconciler(config, state="completed", inspect=inspect)
    value = app.status(REQUEST_ID, task_id=TASK_ID)
    assert value["state"] == "recovered" and value["installed"] is False
    assert len(calls) == 1


def test_runtime_failed_update_hands_off_to_same_inspect_recovery_once(behavior, monkeypatch):
    from eva.operations import runtime_image_update as updater
    _, release, binding = behavior
    checked = _verify_release(REVIEW_ID, release)
    calls = []
    def fail(**arguments):
        calls.append(("apply", arguments))
        write_json(arguments["receipt"], {"status": "incomplete_inspect_retained_installation"})
        raise RuntimeError("synthetic failed startup")
    def recover(**arguments):
        calls.append(("recover", arguments))
        assert arguments["receipt"] == checked["receipt"]
        assert arguments["verify_behavior"]({"target": "baseline"})["source_sha256"] == checked["baseline_source_sha256"]
        return {"status": "baseline_recovered_with_retained_mounts"}
    monkeypatch.setattr(updater, "update_runtime", fail)
    monkeypatch.setattr(updater, "recover_runtime", recover)
    result = runtime(binding).apply_release(checked)
    assert result["status"] == "baseline_recovered_with_retained_mounts"
    assert [name for name, _ in calls] == ["apply", "recover"]


@pytest.mark.parametrize("change", [None, "source", "mount", "config"])
def test_publisher_follows_only_current_images_and_exact_source_configuration(behavior, tmp_path, change):
    _, release, _ = behavior
    publisher = ComponentReleasePublisher.__new__(ComponentReleasePublisher)
    publisher.config = {"project": release["project"], "installed": release["installed"], "follow_current_images": True}
    installed = json.loads(Path(release["installed"]["path"]).read_bytes())
    package = json.loads(Path(release["package"]["path"]).read_bytes())
    new = {"core": "sha256:" + "5" * 64, "delegated-work-worker": "sha256:" + "6" * 64}
    calls = []
    def command(args, directory):
        calls.append(args)
        if "ps" in args: return args[-1]
        if args[:2] == ["docker", "inspect"]:
            name = args[-1]
            return json.dumps([{"Image": new[name], "Mounts": [{"Destination": "/foreign", "Type": "bind", "Source": "/foreign", "RW": True}] if change == "mount" else [],
                "Config": {"Labels": {"com.docker.compose.project": release["project"], "com.docker.compose.service": name,
                    "com.docker.compose.config-hash": "expected-" + name}}}])
        if args[:2] == ["docker", "run"]:
            assert "--network" in args and args[args.index("--network") + 1] == "none"
            return "f" * 64 if change == "source" else package["baseline_sha256"]
        assert "--hash" in args
        return args[-1] + " " + ("different" if change == "config" else "expected-" + args[-1])
    publisher._command = command
    directory = tmp_path / "publication"
    directory.mkdir()
    if change:
        with pytest.raises(ValueError): publisher._current_installation(installed, package, directory)
    else:
        selected = publisher._current_installation(installed, package, directory)
        assert {name: selected["services"][name]["image"] for name in new} == new
        for name in new:
            assert installed["services"][name]["image"] != new[name]
        assert selected["services"]["memory"] == installed["services"]["memory"]
    assert not any("build" in command or "up" in command for command in calls)


def test_missing_current_receipt_cannot_keep_completed_green(behavior):
    config, release, _ = behavior
    app, job = reconciler(config, state="completed", inspect=lambda update: None)
    assert not Path(release["update_receipt"]).exists()
    value = app.status(REQUEST_ID, task_id=TASK_ID)
    assert value["state"] == "unconfirmed" and value["installed"] is False


@pytest.mark.parametrize("route", ["apply_failure", "status_inspect"])
@pytest.mark.parametrize("readiness", ["ready", "waiting", "cancelled", "exception"])
def test_current_foreground_readiness_guards_compensation_on_both_runtime_routes(behavior, monkeypatch, route, readiness):
    from eva.operations import runtime_image_update as updater
    _, release, binding = behavior
    checked = _verify_release(REVIEW_ID, release) | {"task_id": TASK_ID}
    host = runtime(binding)
    effects, checks = [], []
    def ready(task_id):
        checks.append(task_id)
        if readiness == "exception": raise RuntimeError("synthetic readiness UNKNOWN")
        return readiness
    monkeypatch.setattr(host, "ready_to_apply", ready)
    def fail(**arguments):
        write_json(arguments["receipt"], {"status": "incomplete_inspect_retained_installation"})
        raise RuntimeError("synthetic candidate failure")
    def recover(**arguments):
        # The real updater invokes this only after inspection establishes a
        # necessary image compensation. This isolates the root handoff.
        guard = arguments["before_recovery_dispatch"]
        if guard() is not True: raise ValueError("foreground or stop blocks compensation")
        effects.append("one compensation")
        return {"status": "baseline_recovered_with_retained_mounts"}
    monkeypatch.setattr(updater, "update_runtime", fail)
    monkeypatch.setattr(updater, "recover_runtime", recover)
    call = host.apply_release if route == "apply_failure" else host.inspect_release
    if readiness == "ready":
        assert call(checked)["status"] == "baseline_recovered_with_retained_mounts"
        assert effects == ["one compensation"]
    else:
        with pytest.raises((ValueError, RuntimeError)): call(checked)
        assert effects == []
    assert checks == [TASK_ID]


@pytest.mark.parametrize("target", ["candidate", "baseline"])
def test_fixed_probe_receives_bounded_stdout_runner_not_subprocess_contract(tmp_path, monkeypatch, target):
    from eva.operations import runtime_image_update as updater
    path = tmp_path / "runner_probe.py"
    path.write_text("def verify(observation, *, source_sha256, run):\n    output = run(['synthetic-boundary-check'], deadline=7, limit=1234)\n    if not isinstance(output, str): raise TypeError('expected bounded stdout text')\n    return {'verified': output == 'bounded fixture output', 'source_sha256': source_sha256}\n")
    binding = {"path": str(path), "sha256": __import__('hashlib').sha256(path.read_bytes()).hexdigest()}
    calls = []
    def bounded(argv, *, deadline, limit):
        calls.append((argv, deadline, limit))
        return "bounded fixture output"
    monkeypatch.setattr(updater, "_command", bounded)
    checked = {"source_path": "contracts/timestamps.py", "behavior_verifier": binding,
        "baseline_source_sha256": "a" * 64, "candidate_source_sha256": "b" * 64}
    host = runtime(binding, run=lambda *a, **k: pytest.fail("subprocess-shaped run is not the bounded probe runner"))
    result = host._behavior_verifier(checked)({"target": target})
    assert result["verified"] is True
    assert result["source_sha256"] == checked[target + "_source_sha256"]
    assert calls == [(["synthetic-boundary-check"], 7, 1234)]
