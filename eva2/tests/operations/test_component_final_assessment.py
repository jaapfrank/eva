from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import json
import threading
import time
from urllib.error import HTTPError
import pytest

from eva.application.component_development import compare_development_evaluations
from eva.application.component_improvements import draft_component, prepare_component_release, bind_selected_release
from eva.operations.component_final_assessment import FinalComponentAssessments, HostFinalAssessment, validate_selected_package
from eva.operations.component_evaluation_service import EvaluationServer
from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient
from eva.contracts.canonical_json import canonical_sha256
from tests.application.test_component_improvements import arguments
from tests.application.test_component_development import entry
from tests.operations.test_reviewed_component_application import prepared, apps, REVIEW_ID

SCOPE, REQUEST = "c"*64, "d"*64
TASK = "work:final:test"

@pytest.fixture
def package(tmp_path):
    root = tmp_path / "source"; root.mkdir()
    source = "VALUE = 1\n"
    (root / "example.py").write_text(source)
    draft = draft_component(root, arguments(source, "VALUE = 1", "VALUE = 3"))
    draft["draft_step"] = 1
    report = entry(2, hits=3)["report"]
    report.update(relative_path=draft["path"], baseline_sha256=draft["source_sha256"],
                  candidate_sha256=draft["candidate_sha256"], evaluation_step=2)
    choice = compare_development_evaluations([{"draft_step": 1, "evaluation_step": 2, "report": report}])
    choice["selection_step"] = 3
    return bind_selected_release(prepare_component_release(draft, report), choice)

class Applications:
    _task = staticmethod(lambda task: None)
    def __init__(self): self.calls = []
    def register(self, review, release): self.calls.append(("register", review, release))
    def request(self, review, request, task): self.calls.append(("request", review, request, task))
    def review_status(self, review): return {"state": "queued", "installed": False}

@contextmanager
def queue(tmp_path, package, *, ready=None, assessor=None, publisher=None):
    applications = Applications()
    config = {"receipt_root": str(tmp_path / "receipts"), "scope_sha256": SCOPE,
              "protocols": {package["path"]: {"baseline_sha256": package["baseline_sha256"],
                                             "registration_sha256": "e"*64}}}
    value = FinalComponentAssessments(config,
        assessors={package["path"]: assessor or (lambda *args: {"decision": "reject", "public": {"decision": "reject"}})},
        publisher=publisher or (lambda *args: pytest.fail("rejected candidate must not publish")),
        applications=applications, task_state=ready or (lambda task: "ready"))
    try: yield value, applications, config
    finally: value.close()

def wait(value, review, wanted):
    deadline = time.monotonic()+4
    while time.monotonic() < deadline:
        result = value.status(review, scope_sha256=SCOPE)
        if result["state"] == wanted: return result
        time.sleep(.01)
    pytest.fail("final handoff did not reach "+wanted)

def test_exact_winner_frozen_and_cannot_be_changed(package):
    assert validate_selected_package(package) == package
    forged = deepcopy(package)
    forged["candidate_content"] += "SECRET = 1\n"
    with pytest.raises(ValueError): validate_selected_package(forged)
    forged = deepcopy(package)
    forged["development_selection"]["selected_evaluation_step"] = 99
    forged["development_selection_sha256"] = canonical_sha256(forged["development_selection"])
    forged["candidate_package_sha256"] = canonical_sha256({k:v for k,v in forged.items() if k != "candidate_package_sha256"})
    with pytest.raises(ValueError): validate_selected_package(forged)

def test_waits_for_task_finish_rejects_once_and_survives_restart(tmp_path, package):
    state, invocations = ["waiting"], []
    def assessor(*args):
        invocations.append(args)
        return {"decision":"reject","public":{"decision":"reject","reason":"no_measured_gain"}}
    with queue(tmp_path, package, ready=lambda task: state[0], assessor=assessor) as (q, app, config):
        result = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)
        review = result["review_id"]
        wait(q, review, "waiting")
        assert not invocations and not app.calls
        state[0] = "ready"
        assert wait(q, review, "rejected")["installed"] is False
        assert q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["state"] == "rejected"
        with pytest.raises(ValueError):
            q.request(package, request_id=REQUEST, task_id="work:other", scope_sha256=SCOPE)
    with queue(tmp_path, package, assessor=assessor) as (q, app, _):
        assert q.status(review, scope_sha256=SCOPE)["state"] == "rejected"
        assert len(invocations) == 1 and not app.calls

def test_stop_during_assessment_prevents_publication_and_status_remains_available(tmp_path, package):
    entered, release, state = threading.Event(), threading.Event(), ["ready"]
    def assessor(*args):
        entered.set()
        assert release.wait(4)
        return {"decision":"accept","public":{"semantic_improvement_verified": True}}
    with queue(tmp_path, package, ready=lambda task: state[0], assessor=assessor) as (q, app, _):
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        assert entered.wait(4)
        assert q.status(review, scope_sha256=SCOPE)["state"] == "assessing"
        state[0] = "cancelled"
        release.set()
        assert wait(q, review, "cancelled")["installed"] is False
        assert not app.calls

def test_missing_answer_evidence_is_visible_and_never_published(tmp_path, package):
    def assessor(*args):
        return {"decision":"needs_independent_answer_assessment",
                "public":{"answer_assessment_required":True,"semantic_improvement_verified":False}}
    with queue(tmp_path, package, assessor=assessor) as (q, app, _):
        review=q.request(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)["review_id"]
        result=wait(q,review,"awaiting_independent_answer_assessment")
        assert not result["installed"] and not app.calls
        with pytest.raises(ValueError):
            q.status(review, scope_sha256="f"*64)

def test_uncertain_assessment_never_reruns_after_restart(tmp_path, package):
    calls=[]
    def fail(*args):
        calls.append(args)
        raise RuntimeError("private protected data must not be returned")
    with queue(tmp_path, package, assessor=fail) as (q, _, _):
        review=q.request(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)["review_id"]
        result=wait(q,review,"unconfirmed")
        assert "private protected" not in json.dumps(result)
    with queue(tmp_path, package, assessor=fail) as (q, _, _):
        assert q.status(review,scope_sha256=SCOPE)["state"]=="unconfirmed"
    assert len(calls)==1

def test_new_accepted_registry_is_durable_but_bound_to_exact_artifacts(prepared, apps):
    release=deepcopy(prepared["releases"][REVIEW_ID])
    prepared["releases"]={}
    instance=apps(prepared,lambda task:"waiting")
    instance.register(REVIEW_ID,release)
    assert instance.review_status(REVIEW_ID)["state"]=="ready"
    instance.close()
    restarted=apps(prepared,lambda task:"waiting")
    assert restarted.review_status(REVIEW_ID)["state"]=="ready"
    changed=deepcopy(release);changed["project"]="changed"
    with pytest.raises(ValueError):
        restarted.register(REVIEW_ID,changed)

def test_authenticated_transport_transfers_exact_package_and_source(package,tmp_path):
    received=[]
    class Final:
        def request(self,value,**kw):
            received.append((value,kw))
            return {"review_id":value["candidate_package_sha256"],"request_id":kw["request_id"],
                    "task_id":kw["task_id"],"state":"queued","installed":False,"independent_final":True}
        def status(self,review_id,**kw):
            assert kw["scope_sha256"]==SCOPE
            return {"review_id":review_id,"state":"rejected","installed":False}
    token=tmp_path/"token";token.write_text("test-token")
    server=EvaluationServer(("127.0.0.1",0),evaluator=None,token="test-token",allowed_paths=(),
                            final_assessments=Final())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        client=ComponentEvaluationClient("http://127.0.0.1:"+str(server.server_port),str(token),
            (package["path"],),"a"*64,final_assessment_scopes=((package["path"],SCOPE),))
        result=client.assess(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)
        assert received==[(package,{"request_id":REQUEST,"task_id":TASK,"scope_sha256":SCOPE})]
        assert client.application("status",review_id=result["review_id"],request_id=REQUEST,
            task_id="work:later",scope_sha256=SCOPE)["state"]=="rejected"
        token.write_text("wrong")
        with pytest.raises(HTTPError) as error:
            client.assess(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)
        assert error.value.code==401 and len(received)==1
    finally:
        server.shutdown();thread.join();server.server_close()


def test_public_projection_never_contains_nested_holdout_text(package):
    from eva.operations.component_final_assessment import public_decision
    value = public_decision({"decision":"reject", "reason":"protected question",
        "included_references":{"baseline":3,"candidate":{"raw":"protected text"},"case-1":99},
        "context_bytes":{"baseline":float("nan"),"candidate":50},
        "quality_gain":"private explanation", "semantic_improvement_verified":True}, package)
    assert value["included_references"] == {"baseline":3}
    assert value["context_bytes"] == {"candidate":50}
    assert value["reason"] == "independent_criteria_not_met"
    assert value["semantic_improvement_verified"] is False
    assert "protected" not in json.dumps(value)


def test_new_answer_evidence_scores_retained_report_without_repeating_final_inputs(tmp_path, package):
    calls=[]
    answer_file = tmp_path / "independent-answer.json"
    class Assessor(HostFinalAssessment):
        def __init__(self):
            self.config = {"answer_evidence_file": str(answer_file)}
        def __call__(self, *args):
            calls.append("isolated")
            return {"decision":"needs_independent_answer_assessment","public":{"answer_assessment_required":True}}
        def complete(self,*args):
            calls.append("answers")
            return {"decision":"reject","public":{"decision":"reject"}}
    assessor=Assessor()
    with queue(tmp_path,package,assessor=assessor) as (q,app,_):
        review=q.request(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)["review_id"]
        wait(q,review,"awaiting_independent_answer_assessment")
        answer_file.write_text("{}")
        wait(q,review,"rejected")
        assert calls==["isolated","answers"] and not app.calls

def test_crash_after_existing_application_request_does_not_hide_receipt(prepared,apps,tmp_path,package):
    app=apps(prepared)
    from tests.operations.test_reviewed_component_application import REQUEST_ID,TASK_ID,wait_for
    app.request(REVIEW_ID,REQUEST_ID,TASK_ID)
    wait_for(app,"completed")
    with queue(tmp_path,package) as (q,_,_):
        q.applications=app
        result=q._result({"review_id":REVIEW_ID,"request_id":REQUEST_ID,"task_id":TASK_ID,
                          "state":"unconfirmed"})
        assert result["installed"] is True
        assert result["final_assessment_state"]=="unconfirmed"
        assert result["application"]["state"]=="completed"


def test_answer_phase_survives_busy_task_without_reinvoking_candidate(tmp_path,package):
    calls,state,observed=[],["ready"],threading.Event()
    class Assessor:
        ready=False
        def __call__(self,*args):
            calls.append("isolated")
            return {"decision":"needs_independent_answer_assessment","public":{}}
        def answer_evidence_ready(self): return self.ready
        def complete(self,*args):
            calls.append("answers")
            return {"decision":"reject","public":{}}
    assessor=Assessor()
    def task_state(task):
        if assessor.ready and state[0]=="waiting": observed.set()
        return state[0]
    with queue(tmp_path,package,assessor=assessor,ready=task_state) as (q,app,_):
        review=q.request(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)["review_id"]
        wait(q,review,"awaiting_independent_answer_assessment")
        state[0]="waiting";assessor.ready=True
        assert observed.wait(3)
        assert q.status(review,scope_sha256=SCOPE)["state"]=="awaiting_independent_answer_assessment"
        state[0]="ready"
        wait(q,review,"rejected")
        assert calls==["isolated","answers"] and not app.calls

def test_status_and_stop_available_while_publisher_is_running(tmp_path,package):
    entered,release,state=threading.Event(),threading.Event(),["ready"]
    def assessor(*args):
        return {"decision":"accept","public":{"semantic_improvement_verified":True}}
    def publisher(value,result,directory):
        entered.set()
        assert release.wait(4)
        from tools.import_component_release import digest
        file=directory/"package.json"
        return {"package":{"path":str(file),"sha256":digest(file.read_bytes())}}
    with queue(tmp_path,package,assessor=assessor,publisher=publisher,
               ready=lambda task:state[0]) as (q,app,_):
        review=q.request(package,request_id=REQUEST,task_id=TASK,scope_sha256=SCOPE)["review_id"]
        assert entered.wait(4)
        assert q.status(review,scope_sha256=SCOPE)["state"]=="publishing"
        state[0]="cancelled";release.set()
        wait(q,review,"cancelled")
        assert not app.calls


def test_transient_readiness_read_does_not_consume_or_repeat_assessment(tmp_path, package):
    state, invocations = ["unavailable"], []
    def ready(task):
        if state[0] == "unavailable":
            raise RuntimeError("core restarting")
        return state[0]
    def assessor(*args):
        invocations.append(args)
        return {"decision": "reject", "public": {"decision": "reject"}}
    with queue(tmp_path, package, ready=ready, assessor=assessor) as (q, app, _):
        state[0] = "waiting"
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        state[0] = "unavailable"
        wait(q, review, "waiting")
        time.sleep(.6)
        assert not invocations and not app.calls
        state[0] = "ready"
        wait(q, review, "rejected")
        assert len(invocations) == 1 and not app.calls


def test_retained_final_status_is_canonical_without_changing_original_decision(tmp_path, package):
    from eva.contracts.canonical_json import canonical_json_bytes
    legacy = {"decision": "reject", "candidate_sha256": package["candidate_sha256"],
        "baseline_sha256": package["baseline_sha256"],
        "median_seconds": {"baseline": 4.250311392002914, "candidate": 4.105493601004127},
        "context_bytes": {"baseline": 100, "candidate": 99},
        "binding": {"isolated_report_sha256": "a" * 64}}
    with queue(tmp_path, package) as (q, _, _):
        review = q.request(package, request_id=REQUEST, task_id=TASK, scope_sha256=SCOPE)["review_id"]
        wait(q, review, "rejected")
        q.jobs[review]["public"] = deepcopy(legacy)
        status = q.status(review, scope_sha256=SCOPE)
        assert canonical_json_bytes(status)
        from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
        call = ConversationActionCall("status-read", "get_reviewed_component_application", {"review_id": review})
        assert ConversationActionReceipt(call, status).output == status
        assert "median_seconds" not in status["assessment"]
        assert status["assessment"]["median_microseconds"] == {
            "baseline": 4250311, "candidate": 4105494}
        assert status["assessment"]["binding"] == legacy["binding"]
        assert q.jobs[review]["public"] == legacy


def test_public_projection_keeps_scaled_time_and_drops_invalid_numbers(package):
    from eva.operations.component_final_assessment import public_decision
    from eva.contracts.canonical_json import canonical_json_bytes
    result = {"decision": "reject", "median_seconds": {
        "baseline": float("inf"), "candidate": 2},
        "input_tokens": {"baseline": 3.5, "candidate": 4}}
    public = public_decision(result, package)
    assert public["median_microseconds"] == {"candidate": 2000000}
    assert public["input_tokens"] == {"candidate": 4}
    assert public_decision(public, package) == public
    assert canonical_json_bytes(public)
