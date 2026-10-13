"""Accepted helper versions use the existing save/designation and application queue."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from eva.operations.component_application_runtime import ComponentApplicationRuntime
from eva.operations.component_release_publisher import ComponentReleasePublisher
from eva.operations.reviewed_component_application import ReviewedComponentApplications, _save
from eva.operations.saved_artifact_application import (
    apply_in_core, publication_plan, receipt_confirms_application, _verify_source_binding,
)
from eva.contracts.canonical_json import canonical_sha256
from tests.operations.test_saved_artifact_final_assessment import selected
from tools.import_component_release import digest


TASK = "work:eva-chat:saved-application"
REQUEST = "c" * 64


def binding(path, value):
    _save(path, value, initial=True)
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def reference(plan):
    return {"document_id": "procedure-reference:" + canonical_sha256({
            "scope": plan["source_target"]["scope"], "receipt": "e" * 64}),
        "path": plan["destination_path"], "content_sha256": plan["candidate_sha256"],
        "receipt_sha256": "e" * 64,
        "retention_until": plan["source_target"]["retention_until"]}


def completed(plan):
    return {"contract": "EvaSavedArtifactApplication/v1", "status": "saved_and_designated",
        "task_id": TASK, "publication": plan, "reference": reference(plan),
        "old_source_unchanged": True, "new_model_requests": 0}


@pytest.fixture
def published(tmp_path):
    package = selected()
    installed = binding(tmp_path / "installed.json", {"services": {}})
    evidence = binding(tmp_path / "evidence.json", {"synthetic_fixed_gain": True})
    registration = binding(tmp_path / "registration.json", {"synthetic_fixed_criteria": True})
    directory = tmp_path / "candidate"; directory.mkdir(mode=0o700)
    binding(directory / "package.json", package)
    publisher = ComponentReleasePublisher({"repository": str(tmp_path / "repository"),
        "integration_commit": "a" * 40, "installed": installed,
        "project": "test-only", "reviewer": "independent-synthetic-review"},
        run=lambda *a, **k: pytest.fail("saved helper must not build images or run Git"))
    assessment = {"decision": "accept", "public": {"semantic_improvement_verified": True},
        "evidence": evidence, "registration": registration}
    release = publisher(package, assessment, directory)
    return package, release


def wait(app, wanted):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        result = app.status(REQUEST, task_id=TASK)
        if result["state"] == wanted:
            return result
        time.sleep(0.005)
    pytest.fail("application did not reach " + wanted + ": " + str(result))


def test_saved_publication_applies_once_and_survives_queue_restart(tmp_path, published):
    package, release = published
    calls = []
    config = {"receipt_root": str(tmp_path / "queue"),
        "releases": {package["candidate_package_sha256"]: release}, "poll_seconds": 0.01}
    def apply(update):
        assert update["kind"] == "saved_artifact" and update["task_id"] == TASK
        assert update["candidate_content"] == package["candidate_content"]
        calls.append(update)
        _save(update["receipt"], completed(update["publication"]), initial=True)
    with closing(ReviewedComponentApplications(config, ready_to_apply=lambda _: "ready", apply_release=apply)) as app:
        app.request(package["candidate_package_sha256"], REQUEST, TASK)
        result = wait(app, "completed")
        assert result["saved_artifact"]["reference"]["path"] != package["source_target"]["artifact_path"]
        assert result["saved_artifact"]["old_source_unchanged"] is True
    with closing(ReviewedComponentApplications(config, ready_to_apply=lambda _: "ready", apply_release=apply)) as app:
        assert app.request(package["candidate_package_sha256"], REQUEST, TASK)["state"] == "completed"
    assert len(calls) == 1


def test_stop_prevents_saved_dispatch(tmp_path, published):
    package, release = published
    config = {"receipt_root": str(tmp_path / "queue"),
        "releases": {package["candidate_package_sha256"]: release}, "poll_seconds": 0.01}
    with closing(ReviewedComponentApplications(config, ready_to_apply=lambda _: "cancelled",
            apply_release=lambda _: pytest.fail("stopped task must not save"))) as app:
        app.request(package["candidate_package_sha256"], REQUEST, TASK)
        assert wait(app, "cancelled")["installed"] is False


@pytest.mark.parametrize("bad_field,bad_value", [
    ("receipt_sha256", "z" * 64), ("document_id", "procedure-reference:wrong"),
    ("content_sha256", "f" * 64), ("path", "/other/helper.py"),
    ("retention_until", "2000-01-01T00:00:00Z"),
    ("document_id", "procedure-reference:" + canonical_sha256({
        "scope": {"tenant_id": "tenant", "installation_id": "installation",
                  "principal_id": "another-person", "project_id": "project"}, "receipt": "e" * 64})),
])
def test_saved_queue_does_not_accept_unbound_success(tmp_path, published, bad_field, bad_value):
    package, release = published
    calls = []
    config = {"receipt_root": str(tmp_path / "queue"),
        "releases": {package["candidate_package_sha256"]: release}, "poll_seconds": 0.01}
    def apply(update):
        calls.append(update)
        receipt = completed(update["publication"])
        receipt["reference"][bad_field] = bad_value
        _save(update["receipt"], receipt, initial=True)
    with closing(ReviewedComponentApplications(config, ready_to_apply=lambda _: "ready", apply_release=apply)) as app:
        app.request(package["candidate_package_sha256"], REQUEST, TASK)
        assert wait(app, "unconfirmed")["installed"] is False
        app.request(package["candidate_package_sha256"], REQUEST, TASK)
    assert len(calls) == 1


def core_ports(monkeypatch, package, *, write_status="verified"):
    from eva.adapters.postgres import memory_outbox
    from eva.application.background_execution import authorize_background_dispatch
    plan = publication_plan(package)
    source = {"source_target": deepcopy(package["source_target"]),
              "baseline_content": package["baseline_content"]}
    turn = SimpleNamespace(tenant_id="tenant", installation_id="installation",
        principal_id="person", project_id="project",
        audience=SimpleNamespace(kind="private_person"))
    saves, designations = [], []
    old = SimpleNamespace(content=json.dumps({"use_when": "active minute calculation"}))
    monkeypatch.setattr(memory_outbox, "PostgresMemoryOutbox",
        lambda *a, **k: SimpleNamespace(resolve_source=lambda *a, **k: old))
    def save(actual, call, *, step_index):
        assert actual is turn and step_index == 0 and call.name == "save_workspace_document"
        authorize_background_dispatch(actual)
        saves.append(call)
        return SimpleNamespace(output={"status": write_status})
    def designate(actual, call, *, step_index):
        assert actual is turn and step_index == 1 and call.name == "designate_reusable_procedure"
        authorize_background_dispatch(actual)
        designations.append(call)
        return SimpleNamespace(output={"status": "designated", "reference": reference(plan)})
    procedures = SimpleNamespace(_host=lambda actual: None,
        resolve_improvement_source=lambda *a: deepcopy(source), execute=designate)
    return plan, turn, procedures, SimpleNamespace(execute=save), saves, designations


def test_core_uses_verified_save_and_designation_without_replacing_original(monkeypatch):
    package = selected()
    plan, turn, procedures, tools, saves, designations = core_ports(monkeypatch, package)
    result = apply_in_core(plan, package["candidate_content"], TASK,
        engine=object(), procedures=procedures, tools=tools, source_turn=turn, check_ready=lambda _: "ready")
    assert receipt_confirms_application(result, plan, TASK)
    assert saves[0].arguments["path"] == plan["destination_path"] != package["source_target"]["artifact_path"]
    assert saves[0].arguments["content"] == package["candidate_content"]
    assert package["expected_gain"] in designations[0].arguments["use_when"]
    assert result["new_model_requests"] == 0 and len(saves) == len(designations) == 1


@pytest.mark.parametrize("readiness", ["cancelled", "waiting"])
def test_core_rechecks_stop_and_busy_state_before_write(monkeypatch, readiness):
    package = selected()
    plan, turn, procedures, tools, saves, designations = core_ports(monkeypatch, package)
    with pytest.raises(ValueError, match="stopped or no longer idle"):
        apply_in_core(plan, package["candidate_content"], TASK, engine=object(),
            procedures=procedures, tools=tools, source_turn=turn, check_ready=lambda _: readiness)
    assert saves == designations == []


def test_failed_save_cannot_be_designated(monkeypatch):
    package = selected()
    plan, turn, procedures, tools, saves, designations = core_ports(monkeypatch, package, write_status="uncertain")
    with pytest.raises(ValueError, match="write is not verified"):
        apply_in_core(plan, package["candidate_content"], TASK, engine=object(),
            procedures=procedures, tools=tools, source_turn=turn, check_ready=lambda _: "ready")
    assert len(saves) == 1 and not designations


def test_changed_owner_or_baseline_never_reaches_save(monkeypatch):
    package = selected()
    plan, turn, procedures, tools, saves, designations = core_ports(monkeypatch, package)
    other = deepcopy(plan["source_target"]); other["source_call_id"] = "another-save"
    procedures.resolve_improvement_source = lambda *a: {"source_target": other}
    with pytest.raises(ValueError, match="baseline changed"):
        apply_in_core(plan, package["candidate_content"], TASK, engine=object(),
            procedures=procedures, tools=tools, source_turn=turn, check_ready=lambda _: "ready")
    assert saves == designations == []


def test_different_application_actor_never_reaches_save(monkeypatch):
    package = selected()
    plan, turn, procedures, tools, saves, designations = core_ports(monkeypatch, package)
    turn.principal_id = "another-person"
    with pytest.raises(ValueError, match="actor or project differs"):
        apply_in_core(plan, package["candidate_content"], TASK, engine=object(),
            procedures=procedures, tools=tools, source_turn=turn, check_ready=lambda _: "ready")
    assert saves == designations == []


def test_current_ingress_must_match_original_task_source_and_audience():
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.application.conversation import ConversationTurn
    turn = ConversationTurn.private("tenant", "installation", "person", "conversation",
        "message", "Improve my helper", "2026-10-11T18:00:00Z", project_id="project")
    spec = {"audience": "private", "source": {
        "tenant_id": "tenant", "installation_id": "installation", "principal_id": "person",
        "conversation_id": "conversation", "message_id": "message",
        "audience": {"kind": "private_person", "principal_id": "person"}},
        "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn))}
    _verify_source_binding(spec, turn)
    for key, value in (("audience", "household"), ("source_ingress_sha256", "a" * 64)):
        changed = deepcopy(spec); changed[key] = value
        with pytest.raises(ValueError, match="original ingress differs"):
            _verify_source_binding(changed, turn)


def test_core_import_needs_no_host_tools_package():
    import sys
    src = Path(__file__).resolve().parents[3] / "src"
    result = subprocess.run([sys.executable, "-I", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);import eva.operations.saved_artifact_application", str(src)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_runtime_reserves_unknown_receipt_before_one_fixed_core_dispatch(tmp_path):
    package = selected(); plan = publication_plan(package)
    path = tmp_path / "application.json"; calls = []
    def run(argv, **kwargs):
        assert argv[:5] == ["docker", "exec", "-i", "eva-core", "python"]
        assert json.loads(path.read_text())["status"] == "dispatch_result_unknown"
        request = json.loads(kwargs["input"])
        assert request["publication"] == plan and request["scope_sha256"] == "a" * 64
        calls.append(request)
        return SimpleNamespace(returncode=0, stdout=json.dumps(completed(plan)))
    runtime = ComponentApplicationRuntime({"core_container": "eva-core", "scope_sha256": "a" * 64}, run=run)
    release = {"kind": "saved_artifact", "receipt": path, "task_id": TASK,
        "publication": plan, "candidate_content": package["candidate_content"]}
    runtime.apply_release(release)
    assert receipt_confirms_application(json.loads(path.read_text()), plan, TASK)
    with pytest.raises(FileExistsError):
        runtime.apply_release(release)
    assert len(calls) == 1
