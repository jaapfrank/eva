"""Transport and original-authority checks for native component work."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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

# A live synthetic authority, rather than a calendar date that expires and
# prevents every test from reaching its intended guard. The expiry test below
# still supplies its own past deadline.
PARENT_DEADLINE = (datetime.now(UTC) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

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
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"],
                "parent_deadline": PARENT_DEADLINE}
    with host(NS(authorize=authorize)) as (url, server):
        with pytest.raises(HTTPError) as error:
            post(url, "/work-authority", BINDING, token="different")
        assert error.value.code == 401 and not checked
        server.execution_lock.acquire()
        try:
            assert post(url, "/work-authority", BINDING) == {
                "verified": True, "execution_binding_ref": BINDING["execution_binding_ref"],
                "parent_deadline": PARENT_DEADLINE}
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
            "verified": True, "execution_binding_ref": BINDING["execution_binding_ref"],
                "parent_deadline": PARENT_DEADLINE}))
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
              "producer_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "settings": {"accounting_root": str(tmp_path / "physical-accounting"),
                           "limits": {"total_tool_calls": 100, "total_tokens": 400000}}}
    import os
    if os.geteuid() != 0:
        pytest.skip("requires runtime-owned ledger permissions")
    accounting = tmp_path / "physical-accounting" / BINDING["execution_binding_ref"]
    accounting.mkdir(parents=True)
    ledger = accounting / "accounting.json"
    ledger.write_text(json.dumps({"registration_sha256": "c" * 64,
        "requests": [{"request_id": "child-measured", "usage": {
            "input_tokens": 31, "output_tokens": 4, "total_tokens": 35}}],
        "tools": [{}, {}]}))
    ledger.chmod(0o600)
    os.chown(ledger, 65532, 65532)
    resources = tmp_path / "resources.json"
    resources.write_text(json.dumps({"tokens": {"shared_input": 385000,
        "shared_output": 15000, "shared_combined": 400000},
        "tools": {"shared_physical_total": 100}}))
    resources.chmod(0o400)
    config["settings"].update(resources_file=str(resources),
        resources_sha256=hashlib.sha256(resources.read_bytes()).hexdigest())
    report = deepcopy(work.report)
    report.update(image_id="image", command_sha256="command")
    original_assessment = work.assessor()
    suite = hashlib.sha256(json.dumps(["image", "command",
        original_assessment.assessment_sha256], separators=(",", ":")).encode()).hexdigest()
    if runtime is None:
        runtime = NS(verify=lambda binding, **kwargs: {"verified": True,
            "execution_binding_ref": binding["execution_binding_ref"],
                "parent_deadline": PARENT_DEADLINE}, suite_sha256=suite)
    else:
        runtime.suite_sha256 = suite
    from eva.application.conversation import ConversationModelUsage
    if not hasattr(runtime, "active_work_usage"):
        runtime.active_work_usage = lambda task: {
            "model_usage": ConversationModelUsage.measured(model_attempts=2, tool_calls=3,
                input_tokens=120, output_tokens=15).to_wire(),
            "contact_usage": None,
            "contact_disposition": "registered_technical_origin_without_foreground"}
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
        assert kw["settings"]["parent_deadline"] == PARENT_DEADLINE
        assert kw["authorize"]()["parent_deadline"] == PARENT_DEADLINE
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


def test_incomplete_baseline_stops_candidate_and_preserves_measured_usage(work, tmp_path, monkeypatch):
    work.scorer.write_text("def score(cases, outputs):\n"
        "    raise ValueError('measured timing boundary absent')\n")
    for root in work.roots.values():
        (root / (CANDIDATE + ".json")).unlink()
    legs = []
    def produce(**kw):
        kw["authorize"]()
        legs.append(kw["leg"])
        seal(work, BASE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    from pathlib import Path
    ledger = Path(producer.config["settings"]["accounting_root"]) / BINDING["execution_binding_ref"] / "accounting.json"
    original_ledger = ledger.read_bytes()
    result = producer.evaluate(request, execution_binding=BINDING,
        evaluator=evaluator, static_lock=threading.Lock())
    assert legs == ["baseline"]
    assert result["development_assessment"]["baseline"] == {
        "complete": False, "metrics": None, "error": "extra_or_missing_fields"}
    assert result["development_assessment"]["candidate"]["complete"] is False
    assert result["candidate_work_not_run"] == "baseline_assessment_incomplete"
    assert result["execution_binding_ref"] == BINDING["execution_binding_ref"]
    assert result["installed"] is result["semantic_improvement_verified"] is False
    usage = result["work_usage"]
    assert usage["totals"] == {"provider_attempts": 1, "physical_tools": 2,
        "input_tokens": 31, "output_tokens": 4, "unknown_attempts": 0, "retries": 0}
    assert usage["receipt"] == {"path": str(ledger),
        "sha256": hashlib.sha256(original_ledger).hexdigest()}
    assert ledger.read_bytes() == original_ledger
    assert all(not (root / (CANDIDATE + ".json")).exists() for root in work.roots.values())


def test_parent_stop_after_baseline_prevents_candidate_work(work, tmp_path, monkeypatch):
    stopped = False
    legs = []
    def verify(binding, **kwargs):
        if stopped:
            raise ValueError("parent stopped")
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"],
                "parent_deadline": PARENT_DEADLINE}
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


@pytest.mark.parametrize("changes", [
    {"parent_deadline": None}, {"parent_deadline": "2026-10-12T12:15:00+00:00"},
    {"parent_deadline": "2026-10-12T12:15:00.000Z"},
    {"parent_deadline": "2026-1-2T12:15:00Z"},
    {"verified": 1}, {"execution_binding_ref": "f" * 64}, {"new_grant": True},
])
def test_original_deadline_attestation_cannot_be_missing_or_extended(changes):
    value = {"verified": True, "execution_binding_ref": BINDING["execution_binding_ref"],
             "parent_deadline": PARENT_DEADLINE, **changes}
    runtime = ComponentWorkRuntime(
        {"core_container": "existing-core", "scope_sha256": "c" * 64},
        path=WORK_STRATEGY_PATH, suite_sha256="d" * 64,
        run=lambda *a, **k: NS(returncode=0, stdout=json.dumps(value)))
    with pytest.raises((ValueError, TypeError)):
        runtime.verify(BINDING)


def test_configured_deadline_never_replaces_original_task_deadline(work, tmp_path, monkeypatch):
    observed = []
    def produce(**kw):
        observed.append(kw["settings"]["parent_deadline"])
        seal(work, BASE if kw["leg"] == "baseline" else CANDIDATE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    producer.config["settings"]["parent_deadline"] = "2099-01-01T00:00:00Z"
    producer.evaluate(request, execution_binding=BINDING,
                      evaluator=evaluator, static_lock=threading.Lock())
    assert observed == [PARENT_DEADLINE, PARENT_DEADLINE]


def test_perfect_reference_stops_before_candidate_work(work, tmp_path, monkeypatch):
    legs = []
    def produce(**kw):
        legs.append(kw["leg"])
        seal(work, BASE)
    for root in work.roots.values():
        (root/(CANDIDATE+".json")).unlink()
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    producer.config["baseline_maximum"] = {"hits":0}
    result = producer.evaluate(request,execution_binding=BINDING,evaluator=evaluator,
                              static_lock=threading.Lock())
    assert legs == ["baseline"]
    assert result["candidate_work_not_run"] == "baseline_quality_maximum"
    assert result["work_usage"]["totals"]["input_tokens"] == 31
    assert result["work_usage"]["totals"]["physical_tools"] == 2
    assert result["development_assessment"]["candidate"]["complete"] is False


def test_insufficient_original_time_starts_no_variant(work, tmp_path, monkeypatch):
    legs=[]
    producer,request,evaluator=coordinator(work,tmp_path,monkeypatch,lambda **kw:legs.append(kw["leg"]))
    producer.config["minimum_remaining_seconds"]=900
    producer.runtime.verify=lambda binding,**kwargs: {
        "verified":True,"execution_binding_ref":binding["execution_binding_ref"],
        "parent_deadline":"2000-01-01T00:00:00Z"}
    with pytest.raises(ValueError,match="insufficient registered work time"):
        producer.evaluate(request,execution_binding=BINDING,evaluator=evaluator,
                          static_lock=threading.Lock())
    assert not legs


def test_final_authority_cannot_dispatch_through_development_binding():
    from eva.operations.component_work_execution import validate_final_work_binding
    from eva.contracts.canonical_json import canonical_sha256
    value={"contract":"EvaIndependentComponentWorkBinding/v1","task_id":"work:original",
        "review_id":"a"*64,"registration_sha256":"b"*64,"candidate_package_sha256":"a"*64}
    value["execution_binding_ref"]=canonical_sha256(value)
    assert validate_final_work_binding(value)==value
    for change in ({"review_id":"c"*64},{"task_id":"work:other"},{"contract":BINDING["contract"]},
                   {"deadline":"2099-01-01T00:00:00Z"}):
        with pytest.raises(ValueError):
            validate_final_work_binding({**value,**change})


def test_physical_ledger_unknown_never_becomes_free_work(tmp_path):
    import os
    from eva.operations.component_work_execution import _ledger_usage
    if os.geteuid()!=0:pytest.skip("requires runtime-owned ledger permissions")
    path=tmp_path/"accounting.json"
    value={"registration_sha256":"c"*64,"requests":[{"request_id":"one","usage":None}],"tools":[]}
    path.write_text(json.dumps(value));path.chmod(0o600);os.chown(path,65532,65532)
    with pytest.raises(ValueError,match="unmeasured physical"):
        _ledger_usage(path,"c"*64)
    usage,_=_ledger_usage(path,"c"*64,allow_inflight=True)
    assert usage["provider_attempts"]==1 and usage["unknown_attempts"]==1
    value["requests"].append({"request_id":"two","usage":None})
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="more than one unfinished"):
        _ledger_usage(path,"c"*64,allow_inflight=True)


def test_shared_final_accounting_includes_original_contact_and_dev(tmp_path):
    import os
    from eva.operations.component_work_execution import IndependentComponentWorkProducer
    if os.geteuid()!=0:pytest.skip("requires runtime-owned ledger permissions")
    dev="d"*64;final="f"*64
    (tmp_path/dev).mkdir(mode=0o700)
    path=tmp_path/dev/"accounting.json"
    path.write_text(json.dumps({"registration_sha256":"c"*64,
        "requests":[{"request_id":"dev","usage":{"input_tokens":30,"output_tokens":3,"total_tokens":33}}],
        "tools":[{}]}));path.chmod(0o600);os.chown(path,65532,65532)
    parent={"model_usage":{"measurement":"measured","model_attempts":2,"tool_calls":1,
                            "input_tokens":20,"output_tokens":2},
            "terminal_usage":{"measurement":"partial","tool_calls":2},
            "contact_usage":{"measurement":"measured","model_attempts":1,"tool_calls":0,
                             "input_tokens":10,"output_tokens":1}}
    producer=IndependentComponentWorkProducer.__new__(IndependentComponentWorkProducer)
    producer.config={"producer":{"settings":{"accounting_root":str(tmp_path)}},
        "shared_budget":{"provider_attempts":68,"physical_tools":100,"input_tokens":385000,
                         "output_tokens":15000,"construction_attempts":6,"later_attempts":6,"final_attempts":28}}
    producer.runtime=NS(reviewed_work_usage=lambda task:deepcopy(parent))
    producer.registration_sha256="c"*64
    value={"development_ref":dev,"binding":{"execution_binding_ref":final,"task_id":"work:original"},
           "parent_usage":deepcopy(parent)}
    actual=producer._usage(value,final_may_be_absent=True)
    assert actual["total"]=={"provider_attempts":4,"physical_tools":3,
                            "input_tokens":60,"output_tokens":6,"unknown_attempts":0,"retries":0}
    producer.config["shared_budget"]["construction_attempts"]=2
    with pytest.raises(ValueError,match="construction allowance"):
        producer._usage(value,final_may_be_absent=True)


def test_shared_child_limits_include_prior_construction_and_contact():
    from eva.application.conversation import ConversationModelUsage
    from eva.operations.component_work_execution import _construction_remaining_settings
    settings = {"limits": {"total_tool_calls": 100, "total_tokens": 400000,
        "total_provider_attempts": 28}}
    parent = {
        "model_usage": ConversationModelUsage.measured(model_attempts=3, tool_calls=7,
            input_tokens=200, output_tokens=20).to_wire(),
        "contact_usage": ConversationModelUsage.measured(model_attempts=1, tool_calls=1,
            input_tokens=30, output_tokens=2).to_wire(),
        "contact_disposition": "recorded_original_ingress"}
    bounded = _construction_remaining_settings(settings, parent)
    assert bounded["limits"] == {"total_tool_calls": 92, "total_tokens": 399748,
        "total_provider_attempts": 28}
    assert settings["limits"]["total_tool_calls"] == 100
    parent["model_usage"] = ConversationModelUsage.partial(model_attempts=4,
        tool_calls=7, input_tokens=200, output_tokens=20).to_wire()
    with pytest.raises(ValueError, match="unmeasured construction"):
        _construction_remaining_settings(settings, parent)


def separate_stage_resources():
    stages = {
        'development': (45000, 3000, 24, 12),
        'independent_final': (42000, 3000, 24, 12),
        'later': (10500, 1500, 12, 6),
        'parent_operator_assisted_carry_forward': (16260, 3119, 4, 2)}
    return {'contract': 'EvaWorkerStrategyProductionResources/v1',
        'prospective_reserved_stages': {name: {
            'input_tokens': i, 'output_tokens': o, 'total_tokens': i + o,
            'physical_tools': t, 'provider_attempts': p}
            for name, (i, o, t, p) in stages.items()},
        'remaining_joint_allowance': {'input_tokens': 113845, 'output_tokens': 10823,
            'total_tokens': 124668, 'physical_tools': 73, 'provider_attempts': 44}}


def test_separate_development_reservation_does_not_pay_parent_twice():
    from eva.application.conversation import ConversationModelUsage
    from eva.operations.component_work_execution import _construction_remaining_settings
    settings = {'limits': {'total_tokens': 48000, 'total_tool_calls': 24,
        'total_provider_attempts': 12}}
    parent = {'model_usage': ConversationModelUsage.measured(model_attempts=3,
        tool_calls=4, input_tokens=16746, output_tokens=877).to_wire(),
        'contact_usage': None,
        'contact_disposition': 'registered_technical_origin_without_foreground'}
    bounded = _construction_remaining_settings(settings, parent,
        resources=separate_stage_resources())
    # The retained fifth response uses 35,489 child tokens: within its 48,000
    # reservation, but outside the incorrectly parent-debited 30,377 allowance.
    assert bounded['limits']['total_tokens'] == 48000
    assert bounded['limits']['total_tool_calls'] == 24
    assert 35489 < bounded['limits']['total_tokens']
    assert _construction_remaining_settings(settings, parent)['limits']['total_tokens'] == 30377
    assert settings['limits']['total_tokens'] == 48000
    parent['model_usage'] = ConversationModelUsage.partial(model_attempts=3,
        tool_calls=4, input_tokens=16746, output_tokens=877).to_wire()
    with pytest.raises(ValueError, match='unmeasured construction'):
        _construction_remaining_settings(settings, parent, resources=separate_stage_resources())


@pytest.mark.parametrize('change', ['joint', 'stage', 'tools', 'tokens', 'attempts'])
def test_separate_reservations_cannot_expand_registered_work(change):
    from eva.operations.component_work_execution import _validate_separate_development_reservation
    resources = separate_stage_resources()
    limits = {'total_tokens': 48000, 'total_tool_calls': 24, 'total_provider_attempts': 12}
    if change == 'joint':
        resources['remaining_joint_allowance']['physical_tools'] = 63
    elif change == 'stage':
        resources['prospective_reserved_stages']['development']['total_tokens'] += 1
    else:
        key = {'tools': 'total_tool_calls', 'tokens': 'total_tokens',
               'attempts': 'total_provider_attempts'}[change]
        limits[key] += 1
    with pytest.raises(ValueError):
        _validate_separate_development_reservation(resources, limits)


def test_construction_change_during_child_blocks_further_dispatch(work, tmp_path, monkeypatch):
    legs = []
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch,
        lambda **kw: legs.append(kw["leg"]))
    original = producer.runtime.active_work_usage(BINDING["task_id"])
    calls = 0
    def usage(task):
        nonlocal calls
        calls += 1
        value = deepcopy(original)
        if calls > 1:
            value["model_usage"]["input_tokens"] += 1
        return value
    producer.runtime.active_work_usage = usage
    def produce(**kw):
        kw["authorize"]()
        legs.append(kw["leg"])
    producer.produce = produce
    with pytest.raises(ValueError, match="construction changed"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert legs == []


def test_development_report_binds_complete_physical_child_usage(work, tmp_path, monkeypatch):
    def produce(**kw):
        seal(work, BASE if kw["leg"] == "baseline" else CANDIDATE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    result = producer.evaluate(request, execution_binding=BINDING,
        evaluator=evaluator, static_lock=threading.Lock())
    usage = result["work_usage"]
    assert usage["contract"] == "EvaDevelopmentWorkUsage/v1"
    assert usage["registration_sha256"] == "c" * 64
    assert usage["execution_binding_ref"] == BINDING["execution_binding_ref"]
    assert usage["totals"] == {"provider_attempts": 1, "physical_tools": 2,
        "input_tokens": 31, "output_tokens": 4, "unknown_attempts": 0, "retries": 0}
    from pathlib import Path
    assert hashlib.sha256(Path(usage["receipt"]["path"]).read_bytes()).hexdigest() == usage["receipt"]["sha256"]


def test_development_cannot_report_unknown_child_usage_as_zero(work, tmp_path, monkeypatch):
    def produce(**kw):
        seal(work, BASE if kw["leg"] == "baseline" else CANDIDATE)
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    from pathlib import Path
    ledger = Path(producer.config["settings"]["accounting_root"]) / BINDING["execution_binding_ref"] / "accounting.json"
    value = json.loads(ledger.read_text())
    value["requests"][0]["usage"] = None
    ledger.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unmeasured physical accounting"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())


@pytest.mark.parametrize("input_tokens,output_tokens", [
    (385001, 0), (0, 15001), (384880, 0), (0, 14985)])
@pytest.mark.parametrize('separate', [False, True])
def test_child_split_token_overrun_stops_before_next_dispatch(
        work, tmp_path, monkeypatch, input_tokens, output_tokens, separate):
    from pathlib import Path
    dispatched = []
    def produce(**kw):
        path = Path(kw["settings"]["accounting_root"]) / BINDING["execution_binding_ref"] / "accounting.json"
        value = json.loads(path.read_text())
        value["requests"][0]["usage"] = {"input_tokens": input_tokens,
            "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}
        path.write_text(json.dumps(value))
        kw["authorize"]()
        dispatched.append(kw["leg"])
    producer, request, evaluator = coordinator(work, tmp_path, monkeypatch, produce)
    if separate:
        settings = producer.config['settings']
        resources_path = Path(settings['resources_file'])
        resources = json.loads(resources_path.read_bytes())
        resources.update(separate_stage_resources())
        resources_path.chmod(0o600)
        resources_path.write_text(json.dumps(resources))
        resources_path.chmod(0o400)
        settings['resources_sha256'] = hashlib.sha256(resources_path.read_bytes()).hexdigest()
        settings['limits'].update(total_tokens=48000, total_tool_calls=24,
            total_provider_attempts=12)
    with pytest.raises(ValueError, match="construction and child budget exceeded"):
        producer.evaluate(request, execution_binding=BINDING,
            evaluator=evaluator, static_lock=threading.Lock())
    assert not dispatched
