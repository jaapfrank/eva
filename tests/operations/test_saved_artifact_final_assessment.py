"""Fixed final-assessment manifests survive selected release and queue handoff."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from eva.application.component_development import compare_development_evaluations
from eva.application.component_improvements import bind_selected_release, prepare_component_release
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_final_assessment import (
    FinalComponentAssessments, HostFinalAssessment, validate_selected_package,
)
from tools.import_component_release import canonical, digest
from tests.application.test_component_development import entry
from tests.application.test_saved_component_improvements import proposal, saved_source
from tests.operations.test_component_final_assessment import (
    Applications, REQUEST, SCOPE, TASK, package as installed_package, wait,
)


IMAGE = "sha256:" + "a" * 64
COMMAND = ("/usr/local/bin/python", "-I", "-c", "print('synthetic fixed suite')")


def selected(target=None):
    raw, original, path = saved_source()
    target = original if target is None else target
    path = "saved_artifact_" + target["procedure_reference_id"].split(":", 1)[1] + ".py"
    draft = proposal(raw, target, path)
    draft["draft_step"] = 1
    report = entry(2, hits=3)["report"]
    report.update(relative_path=path, baseline_sha256=draft["source_sha256"],
        candidate_sha256=draft["candidate_sha256"], source_target=deepcopy(target), evaluation_step=2)
    choice = compare_development_evaluations([{"draft_step":1, "evaluation_step":2, "report":report}])
    choice["selection_step"] = 3
    return bind_selected_release(prepare_component_release(draft, report), choice)


def changed_target(target, field):
    value = deepcopy(target)
    if field in {"principal_id", "project_id"}:
        value["scope"][field] = "another-" + field
        identity = canonical_sha256({"scope":value["scope"], "receipt":value["receipt_sha256"]})
        value["procedure_reference_id"] = "procedure-reference:" + identity
    else:
        value[field] = {"artifact_path":"/own/another.py", "source_call_id":"another-save",
                        "source_message_id":"another-message"}[field]
    return value


@pytest.fixture
def saved_package():
    return selected()


def host_config(tmp_path, target=None):
    registration = tmp_path / "registration.json"
    registration.write_bytes(canonical({"contract":"SyntheticFixedFinalRegistration/v1"}))
    scorer = tmp_path / "scorer.py"
    scorer.write_text(
        "from tools.import_component_release import canonical,digest\n"
        "checks=[]\n"
        "def check_candidate(raw,sha):\n"
        "    assert digest(raw)==sha\n"
        "    checks.append(sha)\n"
        "def assess(report, *, candidate_source, answer_evidence=None):\n"
        "    assert digest(candidate_source)==report['candidate_sha256']\n"
        "    return {'decision':'reject','binding':{\n"
        "        'candidate_sha256':report['candidate_sha256'],\n"
        "        'baseline_sha256':report['baseline_sha256'],\n"
        "        'registration_sha256':report['final_assessment_binding']['registration_sha256'],\n"
        "        'isolated_report_sha256':digest(canonical(report))}}\n")
    config = {"evaluation":{"image":IMAGE, "command":list(COMMAND)},
        "registration":{"path":str(registration), "sha256":digest(registration.read_bytes())},
        "scorer":{"path":str(scorer), "sha256":digest(scorer.read_bytes())}, "bindings":{}}
    if target is not None:
        config["source_target"] = deepcopy(target)
    return config


def capture_evaluation(monkeypatch, assessor):
    requests = []
    def evaluate(request):
        requests.append(request)
        value = {"contract":"EvaIsolatedComponentEvaluation/v1", "image_id":IMAGE,
            "command_sha256":digest(json.dumps(COMMAND, separators=(",", ":")).encode()),
            "relative_path":request.relative_path, "baseline_sha256":request.baseline_sha256,
            "candidate_sha256":digest(request.source.encode())}
        if request.source_target is not None:
            value["source_target"] = deepcopy(request.source_target)
        return value
    monkeypatch.setattr(assessor.evaluator, "evaluate", evaluate)
    return requests


def test_fixed_host_passes_exact_saved_baseline_manifest_to_existing_evaluator(tmp_path, saved_package, monkeypatch):
    config = host_config(tmp_path, saved_package["source_target"])
    assessor = HostFinalAssessment(config)
    config["source_target"]["source_call_id"] = "caller-mutation"
    requests = capture_evaluation(monkeypatch, assessor)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    result = assessor(saved_package, output)
    request, = requests
    assert request.baseline_source == saved_package["baseline_content"] == "VALUE = 1\n"
    assert request.source == saved_package["candidate_content"] == "VALUE = 2\n"
    assert request.source_target == assessor.evaluator.source_target == saved_package["source_target"]
    report = json.loads((output / "isolated-final.private.json").read_text())
    assert report["source_target"] == saved_package["source_target"]
    assert report["final_assessment_binding"]["source_target_sha256"] == canonical_sha256(saved_package["source_target"])
    assert result["decision"] == "reject" and result["public"]["semantic_improvement_verified"] is False


@pytest.mark.parametrize("field", ["principal_id", "project_id", "artifact_path", "source_call_id"])
def test_other_coherent_saved_target_never_reaches_scorer_or_evaluator(tmp_path, saved_package, monkeypatch, field):
    assessor = HostFinalAssessment(host_config(tmp_path, saved_package["source_target"]))
    requests = capture_evaluation(monkeypatch, assessor)
    other = selected(changed_target(saved_package["source_target"], field))
    assert validate_selected_package(other) is other
    with pytest.raises(ValueError, match="fixed independent target"):
        assessor(other, tmp_path)
    assert not requests and not assessor.scorer.checks


def test_installed_protocol_cannot_admit_saved_package_without_fixed_target(tmp_path, saved_package, monkeypatch):
    assessor = HostFinalAssessment(host_config(tmp_path))
    requests = capture_evaluation(monkeypatch, assessor)
    with pytest.raises(ValueError, match="fixed independent target"):
        assessor(saved_package, tmp_path)
    assert not requests and not assessor.scorer.checks


def test_existing_installed_code_host_flow_keeps_optional_target_absent(tmp_path, installed_package, monkeypatch):
    assessor = HostFinalAssessment(host_config(tmp_path))
    requests = capture_evaluation(monkeypatch, assessor)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    assert assessor(installed_package, output)["decision"] == "reject"
    request, = requests
    assert request.baseline_source is None and request.source_target is None
    report = json.loads((output / "isolated-final.private.json").read_text())
    assert "source_target" not in report and "source_target_sha256" not in report["final_assessment_binding"]


def test_host_target_cannot_shift_after_construction(tmp_path, saved_package, monkeypatch):
    assessor = HostFinalAssessment(host_config(tmp_path, saved_package["source_target"]))
    requests = capture_evaluation(monkeypatch, assessor)
    assessor.config["source_target"]["source_call_id"] = "later-mutation"
    with pytest.raises(ValueError, match="target changed"):
        assessor(saved_package, tmp_path)
    assert not requests


def test_saved_final_rehashes_baseline_even_if_package_and_choice_are_resealed(tmp_path, saved_package, monkeypatch):
    assessor = HostFinalAssessment(host_config(tmp_path, saved_package["source_target"]))
    requests = capture_evaluation(monkeypatch, assessor)
    base = {key:value for key,value in saved_package.items() if key not in {
        "development_selection", "development_selection_sha256", "candidate_package_sha256"}}
    base["baseline_content"] = "VALUE = 9\n"
    forged = bind_selected_release(base, saved_package["development_selection"])
    with pytest.raises(ValueError, match="baseline source hash"):
        assessor(forged, tmp_path)
    assert not requests and not assessor.scorer.checks


@contextmanager
def saved_queue(tmp_path, package, *, ready=None, assessor=None, protocol=None):
    protocol = {"baseline_sha256":package["baseline_sha256"], "registration_sha256":"e"*64,
        "source_target":deepcopy(package["source_target"])} if protocol is None else protocol
    config = {"receipt_root":str(tmp_path / "receipts"), "scope_sha256":SCOPE,
        "protocols":{package["path"]:protocol}}
    applications = Applications()
    value = FinalComponentAssessments(config,
        assessors={package["path"]:assessor or (lambda *a:{"decision":"reject", "public":{"decision":"reject"}})},
        publisher=lambda *a:pytest.fail("rejected source must not publish"), applications=applications,
        task_state=ready or (lambda t:"ready"))
    try:
        yield value, config, applications
    finally:
        value.close()


def test_exact_saved_queue_dispatches_once_and_survives_restart(tmp_path, saved_package):
    calls = []
    def assess(package, output):
        calls.append(package)
        return {"decision":"reject", "public":{"decision":"reject"}}
    with saved_queue(tmp_path, saved_package, assessor=assess) as (queue, config, app):
        review = queue.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(queue, review, "rejected")
        assert not app.calls
    with saved_queue(tmp_path, saved_package, assessor=assess) as (queue, config, app):
        assert queue.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["state"] == "rejected"
    assert calls == [saved_package]


@pytest.mark.parametrize("field", ["artifact_path", "source_call_id"])
def test_queue_request_refuses_another_target_with_same_virtual_path_and_baseline(tmp_path, saved_package, field):
    other = selected(changed_target(saved_package["source_target"], field))
    assert other["path"] == saved_package["path"] and other["baseline_sha256"] == saved_package["baseline_sha256"]
    with saved_queue(tmp_path, saved_package) as (queue, config, app):
        with pytest.raises(ValueError, match="frozen final target"):
            queue.request(other, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)
        assert not queue.jobs and not app.calls


def test_saved_package_not_admitted_by_legacy_protocol(tmp_path, saved_package):
    protocol = {"baseline_sha256":saved_package["baseline_sha256"], "registration_sha256":"e"*64}
    with saved_queue(tmp_path, saved_package, protocol=protocol) as (queue, config, app):
        with pytest.raises(ValueError, match="frozen final target"):
            queue.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)
        assert not queue.jobs


def test_protocol_manifest_cannot_hide_foreign_person_under_original_virtual_path(tmp_path, saved_package):
    protocol = {"baseline_sha256":saved_package["baseline_sha256"], "registration_sha256":"e"*64,
        "source_target":changed_target(saved_package["source_target"], "principal_id")}
    with pytest.raises(ValueError, match="virtual path"):
        with saved_queue(tmp_path, saved_package, protocol=protocol):
            pytest.fail("incoherent source protocol was accepted")


def test_queue_protocol_and_real_host_assessor_require_same_fixed_target(tmp_path, saved_package):
    config = host_config(tmp_path, changed_target(saved_package["source_target"], "artifact_path"))
    assessor = HostFinalAssessment(config)
    with pytest.raises(ValueError, match="fixed assessment target"):
        with saved_queue(tmp_path, saved_package, assessor=assessor):
            pytest.fail("host assessor target was not bound")


def test_waiting_queue_rechecks_target_before_dispatch(tmp_path, saved_package):
    state, calls = ["waiting"], []
    def assess(*args):
        calls.append(args)
        return {"decision":"reject", "public":{"decision":"reject"}}
    with saved_queue(tmp_path, saved_package, ready=lambda t:state[0], assessor=assess) as (queue, config, app):
        review = queue.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(queue, review, "waiting")
        queue.protocols[saved_package["path"]]["source_target"]["source_call_id"] = "changed-after-queue"
        state[0] = "ready"
        assert wait(queue, review, "unconfirmed")["installed"] is False
        assert not calls and not app.calls


def test_restart_revalidates_saved_queue_target_before_starting_dispatch(tmp_path, saved_package):
    with saved_queue(tmp_path, saved_package, ready=lambda t:"waiting") as (queue, config, app):
        review = queue.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(queue, review, "waiting")
    protocol = deepcopy(config["protocols"][saved_package["path"]])
    protocol["source_target"]["source_message_id"] = "changed-on-restart"
    with pytest.raises(ValueError, match="frozen final target"):
        with saved_queue(tmp_path, saved_package, protocol=protocol):
            pytest.fail("changed saved source was dispatched")


@pytest.mark.parametrize("field", ["source_target", "baseline_sha256", "image_id", "final_assessment_binding"])
def test_answer_completion_revalidates_retained_saved_report(tmp_path, saved_package, monkeypatch, field):
    config = host_config(tmp_path, saved_package["source_target"])
    answer = tmp_path / "answers.json"; answer.write_text("{}")
    config["answer_evidence_file"] = str(answer)
    assessor = HostFinalAssessment(config)
    requests = capture_evaluation(monkeypatch, assessor)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    assessor(saved_package, output)
    path = output / "isolated-final.private.json"
    report = json.loads(path.read_text())
    if field == "source_target":
        report[field]["source_message_id"] = "another-message"
    elif field == "final_assessment_binding":
        report[field]["scorer_sha256"] = "f"*64
    else:
        report[field] = "f"*64
    path.write_bytes(canonical(report))
    monkeypatch.setattr(assessor, "_decide", lambda *a,**kw:pytest.fail("changed report reached scorer"))
    with pytest.raises(ValueError, match="binding|target"):
        assessor.complete(saved_package, output)
    assert len(requests) == 1


def test_answer_completion_keeps_exact_saved_report_without_repeating_runner(tmp_path, saved_package, monkeypatch):
    config = host_config(tmp_path, saved_package["source_target"])
    answer = tmp_path / "answers.json"; answer.write_text("{}")
    config["answer_evidence_file"] = str(answer)
    assessor = HostFinalAssessment(config)
    requests = capture_evaluation(monkeypatch, assessor)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    assessor(saved_package, output)
    assert assessor.complete(saved_package, output)["decision"] == "reject"
    assert len(requests) == 1 and (output / "decision-answer.private.json").exists()
