"""Transport and original-authority checks for native component work."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from eva.operations.component_evaluation import WORK_STRATEGY_PATH
from eva.operations.component_evaluation_service import EvaluationServer
from eva.operations.component_work_execution import ComponentWorkRuntime, validate_execution_binding

BINDING = {"contract": "EvaScopedComponentEvaluationBinding/v1", "task_id": "work:original",
           "draft_step": 1, "evaluation_step": 2, "evaluation_call_id": "eval:1",
           "execution_binding_ref": "a" * 64}


def post(url, path, body, token="token"):
    request = Request(url + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urlopen(request, timeout=3) as result:
        return json.loads(result.read())


@contextmanager
def host(producer=None):
    evaluator = NS(evaluate=lambda request: {"static": True})
    server = EvaluationServer(("127.0.0.1", 0), evaluator=evaluator, token="token",
        allowed_paths=frozenset({WORK_STRATEGY_PATH}), component_suites={WORK_STRATEGY_PATH: evaluator},
        procedure_executor=NS(execute=lambda **kwargs: {"procedure_executed": True}),
        work_execution_producers={} if producer is None else {WORK_STRATEGY_PATH: producer})
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def payload(binding=True):
    value = {"relative_path": WORK_STRATEGY_PATH, "baseline_sha256": "b" * 64, "source": "pass"}
    if binding:
        value["execution_binding"] = dict(BINDING)
    return value


def test_native_work_can_use_existing_procedure_route_while_evaluating():
    observed = []
    def evaluate(request, *, execution_binding, evaluator, static_lock):
        # This is a real nested HTTP request: the former shared-lock route gave409.
        assert static_lock.acquire(blocking=False)
        static_lock.release()
        observed.append(post(url, "/run-procedure", {"source": "pass", "input_data": {}}))
        return {"execution_binding_ref": execution_binding["execution_binding_ref"]}
    with host(NS(evaluate=evaluate)) as (url, server):
        result = post(url, "/evaluate", payload())
        assert result["execution_binding_ref"] == BINDING["execution_binding_ref"]
    assert observed == [{"procedure_executed": True}]


def test_registered_native_component_cannot_silently_fall_back_to_static():
    called = []
    with host(NS(evaluate=lambda *a, **k: called.append(True))) as (url, server):
        with pytest.raises(HTTPError) as error:
            post(url, "/evaluate", payload(False))
        assert error.value.code == 400
    assert not called


def test_unregistered_static_route_rejects_native_locator_but_keeps_ordinary_work():
    with host() as (url, server):
        with pytest.raises(HTTPError) as error:
            post(url, "/evaluate", payload())
        assert error.value.code == 400
        assert post(url, "/evaluate", payload(False)) == {"static": True}
        server.execution_lock.acquire()
        try:
            with pytest.raises(HTTPError) as error:
                post(url, "/run-procedure", {"source": "pass", "input_data": {}})
            assert error.value.code == 409
        finally:
            server.execution_lock.release()


def test_original_authority_endpoint_requires_existing_auth_and_no_dispatch_lock():
    checked = []
    def authorize(binding):
        checked.append(binding)
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"]}
    with host(NS(authorize=authorize)) as (url, server):
        with pytest.raises(HTTPError) as error:
            post(url, "/work-authority", BINDING, token="different")
        assert error.value.code == 401 and not checked
        server.execution_lock.acquire()
        try:
            assert post(url, "/work-authority", BINDING) == {
                "verified": True, "execution_binding_ref": BINDING["execution_binding_ref"]}
        finally:
            server.execution_lock.release()
    assert checked == [BINDING]


def test_current_authority_failure_never_becomes_verification():
    def authorize(binding):
        raise ValueError("stopped")
    with host(NS(authorize=authorize)) as (url, server):
        with pytest.raises(HTTPError) as error:
            post(url, "/work-authority", BINDING)
        assert error.value.code == 400


@pytest.mark.parametrize("change", [
    {"draft_step": True}, {"evaluation_step": 1}, {"execution_binding_ref": "invalid"},
    {"task_id": ""}, {"evaluation_call_id": "bad\\x00id"},
    {"new_grant": {"allow": True}},
])
def test_execution_locator_cannot_add_authority_or_change_step_shape(change):
    with pytest.raises(ValueError):
        validate_execution_binding({**BINDING, **change})


def test_runtime_only_accepts_exact_core_attestation():
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return NS(returncode=0, stdout=json.dumps({
            "verified": True, "execution_binding_ref": BINDING["execution_binding_ref"]}))
    runtime = ComponentWorkRuntime({"core_container": "existing-core", "scope_sha256": "c" * 64},
        path=WORK_STRATEGY_PATH, suite_sha256="d" * 64, run=run)
    assert runtime.verify(BINDING)["verified"]
    argv, kwargs = calls[0]
    assert argv[:5] == ["docker", "exec", "-i", "existing-core", "python"]
    assert "verify_original_execution" in kwargs["input"]
    assert kwargs["capture_output"] is True
    runtime.verify(BINDING, baseline_sha256="b" * 64, candidate_sha256="e" * 64)
    offered = json.loads(calls[-1][0][-1])
    assert offered["baseline_sha256"] == "b" * 64
    assert offered["candidate_sha256"] == "e" * 64
    runtime.run = lambda *a, **k: NS(returncode=0, stdout='{"verified":true}')
    with pytest.raises(ValueError):
        runtime.verify(BINDING)
    runtime.run = lambda *a, **k: NS(returncode=1, stdout="private error")
    with pytest.raises(ValueError):
        runtime.verify(BINDING)


from copy import deepcopy
import hashlib
from eva.operations.component_evaluation import CandidateRequest
from eva.operations.component_work_execution import NativeComponentWorkProducer
from tests.operations.test_component_development_work_evidence import work, seal, BASE, CANDIDATE


def coordinator(work, tmp_path, monkeypatch, produce, *, runtime=None):
    source = tmp_path / "producer.py"
    source.write_text("# trusted test producer\\n")
    source.chmod(0o600)
    config = {"producer_file": str(source),
              "producer_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "settings": {}}
    report = deepcopy(work.report)
    report.update(image_id="image", command_sha256="command")
    original_assessment = work.assessor()
    suite = hashlib.sha256(json.dumps(["image", "command",
        original_assessment.assessment_sha256], separators=(",", ":")).encode()).hexdigest()
    if runtime is None:
        runtime = NS(verify=lambda binding, **kwargs: {"verified": True,
            "execution_binding_ref": binding["execution_binding_ref"]}, suite_sha256=suite)
    else:
        runtime.suite_sha256 = suite
    evaluator = NS(config=None, assessment=original_assessment)
    monkeypatch.setattr("eva.operations.component_work_execution.ComponentEvaluator",
        lambda config: NS(evaluate=lambda request: deepcopy(report)))
    producer = NativeComponentWorkProducer(config, runtime=runtime, assessment=work.config,
        producer=produce)
    request = CandidateRequest(WORK_STRATEGY_PATH, BASE, "pass")
    return producer, request, evaluator


def test_producer_seals_baseline_before_fresh_assessor_and_uses_existing_scoring(
        work, tmp_path, monkeypatch):
    order = []
    # No baseline exists at construction: a consumer cannot adopt it later.
    for root in work.roots.values():
        (root / (BASE + ".json")).unlink()
    def produce(**kw):
        kw["authorize"]()
        order.append(kw["leg"])
        seal(work, BASE if kw["leg"] == "baseline" else CANDIDATE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    result = producer.evaluate(request, execution_binding=BINDING,
        evaluator=evaluator, static_lock=threading.Lock())
    assert order == ["baseline", "candidate"]
    assert result["development_assessment"]["baseline"]["metrics"]["hits"] == 0
    assert result["development_assessment"]["candidate"]["metrics"]["hits"] == 2
    assert result["execution_binding_ref"] == BINDING["execution_binding_ref"]
    assert result["semantic_improvement_verified"] is False


def test_parent_stop_after_baseline_prevents_candidate_work(work, tmp_path, monkeypatch):
    stopped = False
    legs = []
    def verify(binding, **kwargs):
        if stopped:
            raise ValueError("parent stopped")
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"]}
    def produce(**kw):
        nonlocal stopped
        legs.append(kw["leg"])
        stopped = True
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce,
        runtime=NS(verify=verify))
    with pytest.raises(ValueError, match="parent stopped"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert legs == ["baseline"]


def test_scorer_change_during_baseline_prevents_candidate_work(work, tmp_path, monkeypatch):
    legs = []
    def produce(**kw):
        legs.append(kw["leg"])
        seal(work, BASE)
        work.scorer.write_text(work.scorer.read_text() + "\n# changed after baseline\n")
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    with pytest.raises(ValueError, match="assessment binding changed"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert legs == ["baseline"]


def test_candidate_cannot_replace_the_pinned_baseline(work, tmp_path, monkeypatch):
    def produce(**kw):
        if kw["leg"] == "candidate":
            work.pairs[BASE]["runs"][0]["artifact_evidence"]["observed"] = 7
            seal(work, BASE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    result = producer.evaluate(request, execution_binding=BINDING,
        evaluator=evaluator, static_lock=threading.Lock())
    assert result["development_assessment"]["baseline"]["complete"] is False
    assert result["development_assessment"]["candidate"]["complete"] is False


def test_producer_scores_are_never_accepted_as_evidence(work, tmp_path, monkeypatch):
    legs = []
    def produce(**kw):
        legs.append(kw["leg"])
        return {"quality": 100}
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    with pytest.raises(ValueError, match="seal evidence"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert legs == ["baseline"]


def test_changed_registered_suite_starts_no_real_work(work, tmp_path, monkeypatch):
    legs = []
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch,
        lambda **kw: legs.append(kw["leg"]))
    producer.runtime.suite_sha256 = "f" * 64
    with pytest.raises(ValueError, match="suite changed"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert legs == []


def test_offered_candidate_is_bound_before_static_checks_or_producer(
        work, tmp_path, monkeypatch):
    checked = []
    legs = []
    def verify(binding, **offered):
        checked.append(offered)
        raise ValueError("offered component differs from the original draft")
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch,
        lambda **kw: legs.append(kw["leg"]), runtime=NS(verify=verify))
    with pytest.raises(ValueError, match="offered component"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert checked == [{"baseline_sha256": BASE,
        "candidate_sha256": hashlib.sha256(request.source.encode()).hexdigest()}]
    assert not legs


def test_changed_case_set_starts_no_baseline(work, tmp_path, monkeypatch):
    legs = []
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch,
        lambda **kw: legs.append(kw["leg"]))
    Path = __import__("pathlib").Path
    Path(work.config["cases_file"]).write_text("[]")
    with pytest.raises(ValueError, match="assessment changed"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert not legs


def test_failed_static_checks_start_no_baseline(work, tmp_path, monkeypatch):
    legs = []
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch,
        lambda **kw: legs.append(kw["leg"]))
    report = deepcopy(work.report)
    report.update(image_id="image", command_sha256="command")
    report["candidate"]["exit_code"] = 1
    monkeypatch.setattr("eva.operations.component_work_execution.ComponentEvaluator",
        lambda config: NS(evaluate=lambda request: report))
    with pytest.raises(ValueError, match="static component"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert not legs
