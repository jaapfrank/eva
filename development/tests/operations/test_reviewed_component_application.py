from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest

from eva.operations.reviewed_component_application import ReviewedComponentApplications
from eva.operations import component_release_import as importer


REVIEW_ID, REQUEST_ID = "a" * 64, "b" * 64
TASK_ID = "work:eva-chat:controlled-test"


def write_json(path, value):
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": importer.digest(path.read_bytes())}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    baseline, candidate = "VALUE = 0\n", "VALUE = 1\n"
    component = "contracts/timestamps.py"
    checkout = tmp_path / "checkout"
    target = checkout / "src/eva" / component
    target.parent.mkdir(parents=True)
    target.write_text(baseline)
    result = {"exit_code": 0, "timed_out": False,
              "stdout_truncated": False, "stderr_truncated": False}
    evaluation = {
        "contract": "EvaIsolatedComponentEvaluation/v1", "installed": False,
        "relative_path": component, "baseline_sha256": importer.digest(baseline.encode()),
        "candidate_sha256": importer.digest(candidate.encode()),
        "baseline": dict(result), "candidate": dict(result),
    }
    package = {
        "contract": "EvaComponentReleaseCandidate/v1",
        "state": "awaiting_measured_gain_review", "installed": False,
        "path": component, "candidate_content": candidate,
        "baseline_sha256": evaluation["baseline_sha256"],
        "candidate_sha256": evaluation["candidate_sha256"],
        "evaluation": evaluation, "evaluation_sha256": importer.digest(importer.canonical(evaluation)),
        "patch": "--- a/" + component + "\n+++ b/" + component +
                 "\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n",
    }
    package["candidate_package_sha256"] = importer.digest(importer.canonical(package))
    evidence = write_json(tmp_path / "evidence.json", {"independent_controlled_evidence": True})
    review = {"contract": "EvaComponentReleaseReview/v1", "decision": "accept",
              "reviewer": "independent-test", "candidate_package_sha256": package["candidate_package_sha256"],
              "measured_gain_evidence": dict(evidence), "independent_review_evidence": dict(evidence)}
    package_binding = write_json(tmp_path / "package.json", package)
    review_binding = write_json(tmp_path / "review.json", review)
    commands = {
        ("rev-parse", "--show-toplevel"): str(checkout),
        ("branch", "--show-current"): "codex/reviewed-release",
        ("status", "--porcelain", "--untracked-files=all"): "",
        ("rev-parse", "HEAD"): "a" * 40,
    }
    monkeypatch.setattr(importer, "git", lambda root, *args: commands[args])
    _, _, raw, imported = importer.prepare(Path(package_binding["path"]), Path(review_binding["path"]), checkout)
    target.write_bytes(raw)
    imported["state"] = "source_imported_not_installed"
    before = {"core": "sha256:" + "1" * 64, "delegated-work-worker": "sha256:" + "2" * 64}
    after = {"core": "sha256:" + "3" * 64, "delegated-work-worker": "sha256:" + "4" * 64}
    installed = {"services": {name: {"image": image} for name, image in before.items()}}
    installed["services"]["memory"] = {"image": "unchanged-database"}
    updated = deepcopy(installed)
    for name, image in after.items():
        updated["services"][name]["image"] = image
    release = {
        "package": package_binding, "review": review_binding,
        "source_import": write_json(tmp_path / "source-import.json", imported),
        "build": write_json(tmp_path / "build.json", {
            "before": before, "sourceHashes": {component: package["candidate_sha256"]},
            "images": after, "installed": False}),
        "installed": write_json(tmp_path / "installed.json", installed),
        "candidate": write_json(tmp_path / "candidate.json", updated),
        "project": "controlled-project", "update_receipt": str(tmp_path / "update.json"),
    }
    return {"receipt_root": str(tmp_path / "requests"), "releases": {REVIEW_ID: release},
            "poll_seconds": 0.01}


@pytest.fixture
def apps():
    opened = []
    def create(config, ready=lambda task: "ready", apply=None):
        if apply is None:
            apply = completed_update
        instance = ReviewedComponentApplications(config, ready_to_apply=ready, apply_release=apply)
        opened.append(instance)
        return instance
    yield create
    for instance in opened:
        instance.close()


def completed_update(update):
    write_json(update["receipt"], {
        "contract": "EvaImageUpdate/v1", "project": update["project"],
        "status": "running_with_retained_mounts",
        "services": {name: {"candidate_image": image, "current_container": "verified-" + name}
                     for name, image in update["candidate_images"].items()},
    })


def state(app):
    return app.status(REQUEST_ID, task_id=TASK_ID)["state"]


def wait_for(app, expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if state(app) == expected:
            return app.status(REQUEST_ID, task_id=TASK_ID)
        time.sleep(0.005)
    pytest.fail("application did not reach " + expected + "; actual " + state(app))


def test_waits_for_actual_task_and_quiescence_then_confirms_updater_receipt(prepared, apps):
    ready = threading.Event()
    calls = []
    def apply(update):
        calls.append(update)
        assert update["project"] == "controlled-project"
        assert update["installed"] == Path(prepared["releases"][REVIEW_ID]["installed"]["path"])
        completed_update(update)
    app = apps(prepared, lambda task: "ready" if ready.is_set() else "waiting", apply)
    assert app.request(REVIEW_ID, REQUEST_ID, TASK_ID)["state"] == "queued"
    wait_for(app, "waiting")
    assert not calls
    ready.set()
    assert wait_for(app, "completed")["installed"] is True
    assert len(calls) == 1
    assert app.request(REVIEW_ID, REQUEST_ID, TASK_ID)["state"] == "completed"
    assert len(calls) == 1


def test_task_stop_cancels_without_another_model_or_adapter_call(prepared, apps):
    calls = []
    app = apps(prepared, lambda task: "cancelled", calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    assert wait_for(app, "cancelled")["installed"] is False
    assert not calls


def test_explicit_cancel_does_not_wait_for_inflight_readiness(prepared, apps):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def ready(task):
        entered.set()
        assert release.wait(3)
        return "ready"
    app = apps(prepared, ready, calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    assert entered.wait(3)
    try:
        assert app.cancel(REQUEST_ID, task_id=TASK_ID)["state"] == "cancelled"
    finally:
        release.set()
    app.close()
    assert not calls


@pytest.mark.parametrize("artifact", ["package", "review", "source_import", "build", "installed", "candidate"])
def test_changed_registered_artifacts_rejected_immediately_before_dispatch(prepared, apps, artifact):
    ready = threading.Event()
    calls = []
    app = apps(prepared, lambda task: "ready" if ready.is_set() else "waiting", calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "waiting")
    path = Path(prepared["releases"][REVIEW_ID][artifact]["path"])
    path.write_bytes(path.read_bytes() + b"\n")
    ready.set()
    assert wait_for(app, "rejected")["reason"] == "release_binding_changed"
    assert not calls


def test_changed_review_evidence_also_rejected(prepared, apps):
    ready = threading.Event()
    app = apps(prepared, lambda task: "ready" if ready.is_set() else "waiting",
               lambda update: pytest.fail("changed evidence must never dispatch"))
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "waiting")
    review = json.loads(Path(prepared["releases"][REVIEW_ID]["review"]["path"]).read_text())
    Path(review["measured_gain_evidence"]["path"]).write_text("changed")
    ready.set()
    wait_for(app, "rejected")


@pytest.mark.parametrize("artifact,field,value", [
    ("build", "images", {"core": "sha256:" + "9" * 64}),
    ("build", "sourceHashes", {"contracts/timestamps.py": "0" * 64}),
    ("source_import", "review_sha256", "0" * 64),
    ("review", "decision", "reject"),
])
def test_registered_but_internally_unbound_artifact_is_not_accepted(prepared, apps, artifact, field, value):
    binding = prepared["releases"][REVIEW_ID][artifact]
    path = Path(binding["path"])
    data = json.loads(path.read_text())
    data[field] = value
    prepared["releases"][REVIEW_ID][artifact] = write_json(path, data)
    with pytest.raises(ValueError):
        apps(prepared)


def test_non_image_compose_changes_are_not_accepted(prepared, apps):
    release = prepared["releases"][REVIEW_ID]
    path = Path(release["candidate"]["path"])
    data = json.loads(path.read_text())
    data["services"]["core"]["environment"] = {"UNREVIEWED_CONFIG": "changed"}
    release["candidate"] = write_json(path, data)
    with pytest.raises(ValueError, match="image-only update"):
        apps(prepared)


def test_task_scope_and_request_binding_are_immutable(prepared, apps):
    prepared["releases"]["c" * 64] = deepcopy(prepared["releases"][REVIEW_ID])
    app = apps(prepared, lambda task: "waiting")
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    for method in (app.status, app.cancel):
        with pytest.raises(ValueError, match="unavailable in this task"):
            method(REQUEST_ID, task_id="work:another-owner")
    with pytest.raises(ValueError, match="unavailable in this task"):
        app.request(REVIEW_ID, REQUEST_ID, "work:another-owner")
    with pytest.raises(ValueError, match="changed during replay"):
        app.request("c" * 64, REQUEST_ID, TASK_ID)
    with pytest.raises(ValueError, match="identifier"):
        app.request(REVIEW_ID, "../other", TASK_ID)


def test_pre_dispatch_restart_requires_explicit_matching_request(prepared, apps):
    original = apps(prepared, lambda task: "waiting")
    original.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(original, "waiting")
    original.close()
    calls = []
    def apply(update):
        calls.append(update)
        completed_update(update)
    resumed = apps(prepared, apply=apply)
    assert state(resumed) == "interrupted_before_dispatch"
    assert not calls
    resumed.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(resumed, "completed")
    assert len(calls) == 1


@pytest.mark.parametrize("crash_state", ["dispatching", "unconfirmed"])
def test_uncertain_dispatch_survives_restart_without_redispatch(prepared, apps, crash_state):
    calls = []
    def uncertain(update):
        calls.append(update)
        raise RuntimeError("lost response")
    first = apps(prepared, apply=uncertain)
    first.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(first, "unconfirmed")
    first.close()
    path = Path(prepared["receipt_root"]) / (REQUEST_ID + ".json")
    job = json.loads(path.read_text())
    job["state"] = crash_state
    write_json(path, job)
    second = apps(prepared, apply=lambda update: pytest.fail("uncertain dispatch cannot repeat"))
    assert second.request(REVIEW_ID, REQUEST_ID, TASK_ID)["state"] == crash_state
    assert state(second) == crash_state
    assert len(calls) == 1


def test_exact_existing_updater_receipt_recovers_success_without_dispatch(prepared, apps):
    first = apps(prepared, apply=lambda update: None)
    first.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(first, "unconfirmed")
    first.close()
    release = prepared["releases"][REVIEW_ID]
    images = json.loads(Path(release["build"]["path"]).read_text())["images"]
    update = {"receipt": Path(release["update_receipt"]), "project": release["project"],
              "candidate_images": images}
    completed_update(update)
    second = apps(prepared, apply=lambda update: pytest.fail("receipt reconciliation is read-only"))
    assert state(second) == "completed"


def test_wrong_image_receipt_cannot_confirm_success(prepared, apps):
    def wrong(update):
        completed_update({**update, "candidate_images": {
            name: "sha256:" + "0" * 64 for name in update["candidate_images"]}})
    app = apps(prepared, apply=wrong)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    assert wait_for(app, "unconfirmed")["installed"] is False


def test_cancel_after_dispatch_preserves_receipt_and_does_not_start_another_update(prepared, apps):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def apply(update):
        calls.append(update)
        entered.set()
        assert release.wait(3)
        completed_update(update)
    app = apps(prepared, apply=apply)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    assert entered.wait(3)
    try:
        assert app.cancel(REQUEST_ID, task_id=TASK_ID)["state"] == "dispatching"
        with pytest.raises(ValueError, match="already has an application request"):
            app.request(REVIEW_ID, "c" * 64, TASK_ID)
    finally:
        release.set()
    wait_for(app, "completed")
    assert len(calls) == 1


def test_single_host_dispatcher_owns_all_requests(prepared, apps):
    apps(prepared, lambda task: "waiting")
    with pytest.raises(ValueError, match="another application dispatcher"):
        apps(prepared)


def test_readiness_error_is_terminal_without_dispatch(prepared, apps):
    def failed(task):
        raise RuntimeError("database unavailable")
    app = apps(prepared, failed, lambda update: pytest.fail("readiness error must not dispatch"))
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    assert wait_for(app, "rejected")["reason"] == "readiness_check_failed"


def test_uncertain_review_cannot_dispatch_again_under_new_request_or_task(prepared, apps):
    prepared["releases"]["d" * 64] = deepcopy(prepared["releases"][REVIEW_ID])
    calls = []
    app = apps(prepared, apply=calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "unconfirmed")
    for review_id in (REVIEW_ID, "d" * 64):
        with pytest.raises(ValueError, match="already has an application request"):
            app.request(review_id, "e" * 64, "work:another-task")
    assert len(calls) == 1


def test_reformatted_review_alias_cannot_redispatch_same_approval(prepared, apps, tmp_path):
    alias = deepcopy(prepared["releases"][REVIEW_ID])
    original = json.loads(Path(alias["review"]["path"]).read_text())
    path = tmp_path / "review-formatted.json"
    path.write_text(json.dumps(original, indent=2))
    alias["review"] = {"path": str(path), "sha256": importer.digest(path.read_bytes())}
    prepared["releases"]["d" * 64] = alias
    calls = []
    app = apps(prepared, apply=calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "unconfirmed")
    with pytest.raises(ValueError, match="already has an application request"):
        app.request("d" * 64, "e" * 64, "work:another-task")
    assert len(calls) == 1


def test_symbolic_link_artifact_is_not_a_trusted_host_binding(prepared, apps, tmp_path):
    binding = prepared["releases"][REVIEW_ID]["package"]
    link = tmp_path / "linked-package.json"
    link.symlink_to(binding["path"])
    binding["path"] = str(link)
    with pytest.raises(ValueError, match="without symbolic links"):
        apps(prepared)


def test_model_writable_receipt_directory_is_rejected(prepared, apps):
    path = Path(prepared["receipt_root"])
    path.mkdir(mode=0o777)
    path.chmod(0o777)
    with pytest.raises(ValueError, match="private host-owned"):
        apps(prepared)


def test_later_task_reads_completed_review_without_original_request_credentials(prepared, apps):
    prepared["releases"]["d" * 64] = deepcopy(prepared["releases"][REVIEW_ID])
    calls = []
    def apply(update):
        calls.append(update)
        completed_update(update)
    app = apps(prepared, apply=apply)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "completed")
    assert app.review_status("d" * 64) == app.status(REQUEST_ID, task_id=TASK_ID)
    assert app.review_status(REVIEW_ID)["installed"] is True
    assert len(calls) == 1


def test_later_review_status_preserves_uncertainty_without_apply_or_resume(prepared, apps):
    calls = []
    app = apps(prepared, apply=calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "unconfirmed")
    before = app.status(REQUEST_ID, task_id=TASK_ID)
    assert app.review_status(REVIEW_ID) == before
    assert app.review_status(REVIEW_ID) == before
    assert len(calls) == 1


def test_review_status_reconciles_existing_exact_updater_receipt(prepared, apps):
    calls = []
    app = apps(prepared, apply=calls.append)
    app.request(REVIEW_ID, REQUEST_ID, TASK_ID)
    wait_for(app, "unconfirmed")
    completed_update(calls[0])
    assert app.review_status(REVIEW_ID)["state"] == "completed"
    assert len(calls) == 1


def test_unrequested_review_status_is_ready_without_side_effects(prepared, apps):
    app = apps(prepared, lambda task: pytest.fail("read-only status cannot check readiness"),
               lambda update: pytest.fail("read-only status cannot apply"))
    before = set(Path(prepared["receipt_root"]).iterdir())
    assert app.review_status(REVIEW_ID) == {
        "review_id": REVIEW_ID, "state": "ready", "installed": False}
    assert set(Path(prepared["receipt_root"]).iterdir()) == before
    assert not app.jobs
    with pytest.raises(ValueError, match="not registered"):
        app.review_status("e" * 64)
