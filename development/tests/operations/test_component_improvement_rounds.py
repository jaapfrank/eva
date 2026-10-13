"""Successive improvements keep the original final assessment and release authority."""
from contextlib import contextmanager
from copy import deepcopy
import json
import threading
from urllib.error import HTTPError

import pytest

from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient
from eva.application.component_development import compare_development_evaluations
from eva.application.component_improvements import (
    bind_selected_release, draft_component, prepare_component_release,
)
from eva.operations.component_evaluation_service import EvaluationServer
from eva.operations.component_final_assessment import FinalComponentAssessments
from tools.import_component_release import digest
from tests.application.test_component_development import entry
from tests.application.test_component_improvements import arguments
from tests.operations.test_component_final_assessment import (
    Applications, REQUEST, SCOPE, TASK, package, wait,
)
from tests.operations.test_saved_artifact_final_assessment import (
    changed_target, saved_package, selected,
)


FIRST, NEXT = "e" * 64, "f" * 64


def proposal(tmp_path, source, replacement):
    tmp_path.mkdir()
    (tmp_path / "example.py").write_text(source)
    draft = draft_component(tmp_path, arguments(source, source.strip(), replacement))
    draft["draft_step"] = 1
    report = entry(2, hits=3)["report"]
    report.update(relative_path=draft["path"], baseline_sha256=draft["source_sha256"],
                  candidate_sha256=draft["candidate_sha256"], evaluation_step=2)
    choice = compare_development_evaluations([
        {"draft_step": 1, "evaluation_step": 2, "report": report}])
    choice["selection_step"] = 3
    return bind_selected_release(prepare_component_release(draft, report), choice)


def configuration(tmp_path, value, registration, previous=None):
    protocol = {"baseline_sha256": value["baseline_sha256"],
                "registration_sha256": registration}
    if value.get("source_target") is not None:
        protocol["source_target"] = deepcopy(value["source_target"])
    result = {"receipt_root": str(tmp_path / "receipts"), "scope_sha256": SCOPE,
              "protocols": {value["path"]: protocol}}
    if previous is not None:
        path, old = next(iter(previous["protocols"].items()))
        result["retained_rounds"] = {old["registration_sha256"]:
                                    {"path": path, "protocol": deepcopy(old)}}
    return result


def reject(*args):
    return {"decision": "reject", "public": {"decision": "reject"}}


def never_publish(*args):
    pytest.fail("a rejected round must not publish")


@contextmanager
def dispatcher(config, *, assessor=reject, retained_assessor=reject,
               retained_publisher=never_publish, ready=lambda task: "ready"):
    retained = config.get("retained_rounds", {})
    q = FinalComponentAssessments(config,
        assessors={p: assessor for p in config["protocols"]}, publisher=never_publish,
        applications=Applications(), task_state=ready,
        retained_assessors={r: retained_assessor for r in retained},
        retained_publishers={r: retained_publisher for r in retained})
    try:
        yield q
    finally:
        q.close()


@pytest.mark.parametrize("kind", ["installed", "saved"])
def test_new_round_keeps_old_receipts_and_replay_byte_exact(tmp_path, package, saved_package, kind):
    first = package if kind == "installed" else saved_package
    current = (proposal(tmp_path / "new-source", first["candidate_content"], "VALUE = 5")
               if kind == "installed" else selected(changed_target(first["source_target"], "source_message_id")))
    assert current["path"] == first["path"]
    calls = []
    old = configuration(tmp_path, first, FIRST)
    with dispatcher(old, assessor=lambda *args: (calls.append("first") or reject())) as q:
        review = q.request(first, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "rejected")
    original = {p: p.read_bytes() for p in (tmp_path / "receipts").rglob("*.json")}
    fresh = configuration(tmp_path, current, NEXT, old)
    with dispatcher(fresh, assessor=lambda *args: (calls.append("next") or reject()),
                    retained_assessor=lambda *args: pytest.fail("closed round reran")) as q:
        assert q.request(first, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["state"] == "rejected"
        assert q.round_status(current["path"], scope_sha256=SCOPE)["state"] == "available"
        new_review = q.request(current, request_id="a" * 64,
                               task_id="work:final:next", scope_sha256=SCOPE)["review_id"]
        wait(q, new_review, "rejected")
        assert q.round_status(current["path"], scope_sha256=SCOPE)["state"] == "consumed"
        assert all(p.read_bytes() == raw for p, raw in original.items())
        if kind == "installed":
            other = proposal(tmp_path / "other-source", first["candidate_content"], "VALUE = 7")
            with pytest.raises(ValueError, match="already consumed"):
                q.request(other, request_id="b" * 64, task_id="work:final:other", scope_sha256=SCOPE)
    assert calls == ["first", "next"]
    with dispatcher(fresh, assessor=lambda *args: pytest.fail("closed current round reran")) as q:
        assert q.status(review, scope_sha256=SCOPE)["state"] == "rejected"
        assert q.status(new_review, scope_sha256=SCOPE)["state"] == "rejected"
        assert all(p.read_bytes() == raw for p, raw in original.items())


def test_pending_old_round_uses_original_assessor_and_publisher(tmp_path, package):
    current = proposal(tmp_path / "new-source", package["candidate_content"], "VALUE = 5")
    old = configuration(tmp_path, package, FIRST)
    with dispatcher(old, ready=lambda task: "waiting") as q:
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "waiting")
    calls = []
    def assess(value, directory):
        assert value == package
        calls.append("original-assessor")
        return {"decision": "accept", "public": {"semantic_improvement_verified": True}}
    def publish(value, assessment, directory):
        assert value == package
        calls.append("original-publisher")
        path = directory / "package.json"
        return {"package": {"path": str(path), "sha256": digest(path.read_bytes())}}
    with dispatcher(configuration(tmp_path, current, NEXT, old),
                    retained_assessor=assess, retained_publisher=publish) as q:
        wait(q, review, "application_requested")
        assert [v[0] for v in q.applications.calls] == ["register", "request"]
    assert calls == ["original-assessor", "original-publisher"]


def test_old_answer_phase_keeps_isolated_report_and_original_assessor(tmp_path, package):
    calls = []
    class Assessor:
        ready = False
        def __call__(self, *args):
            calls.append("isolation")
            return {"decision": "needs_independent_answer_assessment", "public": {}}
        def answer_evidence_ready(self):
            return self.ready
        def complete(self, *args):
            calls.append("original-answers")
            return reject()
    assessor = Assessor()
    old = configuration(tmp_path, package, FIRST)
    with dispatcher(old, assessor=assessor) as q:
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "awaiting_independent_answer_assessment")
    current = proposal(tmp_path / "new-source", package["candidate_content"], "VALUE = 5")
    with dispatcher(configuration(tmp_path, current, NEXT, old), retained_assessor=assessor) as q:
        assert q.status(review, scope_sha256=SCOPE)["state"] == "awaiting_independent_answer_assessment"
        assessor.ready = True
        wait(q, review, "rejected")
    assert calls == ["isolation", "original-answers"]


def test_retarget_without_original_registration_is_rejected_without_erasing_receipt(tmp_path, saved_package):
    old = configuration(tmp_path, saved_package, FIRST)
    with dispatcher(old) as q:
        review = q.request(saved_package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "rejected")
    original = {p: p.read_bytes() for p in (tmp_path / "receipts").rglob("*.json")}
    current = selected(changed_target(saved_package["source_target"], "source_message_id"))
    with pytest.raises(ValueError, match="protocol changed"):
        with dispatcher(configuration(tmp_path, current, NEXT)):
            pytest.fail("old registration vanished")
    changed = configuration(tmp_path, current, NEXT, old)
    changed["retained_rounds"][FIRST]["protocol"]["source_target"] = current["source_target"]
    with pytest.raises(ValueError, match="frozen final target"):
        with dispatcher(changed):
            pytest.fail("old final target changed")
    assert all(p.read_bytes() == raw for p, raw in original.items())


def test_authenticated_public_round_status_never_discloses_final_inputs(tmp_path, package):
    config = configuration(tmp_path, package, FIRST)
    token = tmp_path / "token"; token.write_text("synthetic-token")
    with dispatcher(config) as q:
        server = EvaluationServer(("127.0.0.1", 0), evaluator=None,
            token="synthetic-token", allowed_paths=(), final_assessments=q)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        client = ComponentEvaluationClient("http://127.0.0.1:" + str(server.server_port),
            str(token), (package["path"],), "a" * 64,
            final_assessment_scopes=((package["path"], SCOPE),))
        try:
            public = client.round_status(package["path"], scope_sha256=SCOPE)
            assert public["registration_sha256"] == FIRST
            assert public["baseline_sha256"] == package["baseline_sha256"]
            assert public["state"] == "available"
            assert set(public) == {"contract", "path", "scope_sha256", "registration_sha256",
                                   "baseline_sha256", "source_target_sha256", "state", "independent_final", "installed"}
            with pytest.raises(ValueError):
                q.round_status(package["path"], scope_sha256="b" * 64)
            token.write_text("wrong")
            with pytest.raises(HTTPError) as error:
                client.round_status(package["path"], scope_sha256=SCOPE)
            assert error.value.code == 401
        finally:
            server.shutdown(); thread.join(); server.server_close()
    # Unexpected host fields must not be projected into native context.
    from dataclasses import replace
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ComponentEvaluationClient, "_post", lambda *a, **kw: {**public, "oracle": "protected"})
        with pytest.raises(ValueError, match="metadata binding"):
            replace(client).round_status(package["path"], scope_sha256=SCOPE)


def test_host_configuration_loads_retained_rounds_into_existing_dispatcher(tmp_path, package, monkeypatch):
    import eva.operations.component_final_assessment as module
    import eva.operations.component_application_runtime as runtime
    import eva.operations.component_release_publisher as publisher
    current = proposal(tmp_path / "new-source", package["candidate_content"], "VALUE = 5")
    old = configuration(tmp_path, package, FIRST)
    with dispatcher(old) as q:
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "rejected")
    base = configuration(tmp_path, current, NEXT, old)
    # Synthetic trusted host objects exercise only composition, no protected inputs.
    class Host:
        def __init__(self, config): self.config = config
        __call__ = staticmethod(reject)
    class Runtime:
        def __init__(self, config): self.config = config
        ready_to_apply = staticmethod(lambda task:"ready")
    monkeypatch.setattr(module, "HostFinalAssessment", Host)
    monkeypatch.setattr(runtime, "ComponentApplicationRuntime", Runtime)
    monkeypatch.setattr(publisher, "ComponentReleasePublisher", lambda config:never_publish)
    old_protocol = base["retained_rounds"][FIRST]["protocol"]
    config = {**base, "assessments": {current["path"]:{"registration":{"sha256":NEXT}}},
        "publisher":{}, "retained_rounds":{FIRST:{"path":package["path"], "protocol":old_protocol,
            "assessment":{"registration":{"sha256":FIRST}}, "publisher":{}}}}
    q = module.configured_final_assessments(config, applications=Applications(),
        runtime_config={"scope_sha256":SCOPE})
    try:
        assert q.status(review, scope_sha256=SCOPE)["state"] == "rejected"
        assert q.round_status(current["path"], scope_sha256=SCOPE)["state"] == "available"
        assert q.retained_rounds[FIRST]["protocol"] == old_protocol
    finally:
        q.close()
