"""Saved artifacts use the fixed component runner and its existing transport."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eva.adapters.connectors.component_evaluation import (
    ComponentEvaluationClient, configured_component_evaluator,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_evaluation import (
    CandidateRequest, ComponentEvaluator, EvaluationConfig, validate_saved_artifact_target,
)
from eva.operations.component_evaluation_service import EvaluationServer, load_config


IMAGE = "sha256:" + "a" * 64
BASELINE = "VALUE = 1\n"
CANDIDATE = "VALUE = 2\n"


def source_target():
    scope = {"tenant_id":"tenant:synthetic", "installation_id":"installation:synthetic",
             "principal_id":"person:synthetic", "project_id":"project:synthetic"}
    receipt = "b" * 64
    identity = canonical_sha256({"scope":scope, "receipt":receipt})
    return {"kind":"saved_artifact", "procedure_reference_id":"procedure-reference:"+identity,
        "scope":scope, "artifact_path":"/proof-state/synthetic-tool.py",
        "content_sha256":hashlib.sha256(BASELINE.encode()).hexdigest(), "receipt_sha256":receipt,
        "source_call_id":"synthetic:save", "source_message_id":"synthetic:message",
        "tool_configuration_sha256":"c"*64, "retention_until":"2027-10-11T12:00:00Z"}


def saved_request(target=None, baseline=BASELINE, candidate=CANDIDATE):
    target = source_target() if target is None else target
    path = "saved_artifact_" + target["procedure_reference_id"].removeprefix("procedure-reference:") + ".py"
    return CandidateRequest(path, hashlib.sha256(BASELINE.encode()).hexdigest(), candidate, baseline, target)


def leg(stdout="[1]"):
    return {"exit_code":0, "timed_out":False, "elapsed_ms":1, "stdout":stdout, "stderr":"",
            "stdout_truncated":False, "stderr_truncated":False}


def report(request):
    value = {"contract":"EvaIsolatedComponentEvaluation/v1", "image_id":IMAGE,
        "relative_path":request.relative_path, "baseline_sha256":request.baseline_sha256,
        "candidate_sha256":hashlib.sha256(request.source.encode()).hexdigest(),
        "command_sha256":"d"*64, "baseline":leg(), "candidate":leg("[2]"),
        "development_assessment":{"assessment_sha256":"e"*64},
        "installed":False, "semantic_improvement_verified":False}
    if request.source_target is not None:
        value["source_target"] = deepcopy(request.source_target)
    return value


def suite_binding(value):
    basis = [value["image_id"], value["command_sha256"], value["development_assessment"]["assessment_sha256"]]
    if value.get("source_target") is not None: basis.append(canonical_sha256(value["source_target"]))
    return hashlib.sha256(json.dumps(basis, separators=(",", ":")).encode()).hexdigest()


def draft(request):
    return {"path":request.relative_path, "source_sha256":request.baseline_sha256,
        "candidate_sha256":hashlib.sha256(request.source.encode()).hexdigest(),
        "candidate_content":request.source, "baseline_content":request.baseline_source,
        "source_target":request.source_target}


@pytest.mark.parametrize("problem", ["baseline_hash", "manifest_hash", "foreign_person", "foreign_project",
    "receipt", "kind", "extra_field", "missing_field", "relative_path", "traversal", "retention", "source_id"])
def test_saved_artifact_manifest_and_exact_baseline_are_bound(problem):
    target = source_target()
    baseline = BASELINE
    if problem == "baseline_hash": baseline += "# changed\n"
    elif problem == "manifest_hash": target["content_sha256"] = "0"*64
    elif problem == "foreign_person": target["scope"]["principal_id"] = "person:other"
    elif problem == "foreign_project": target["scope"]["project_id"] = "project:other"
    elif problem == "receipt": target["receipt_sha256"] = "f"*64
    elif problem == "kind": target["kind"] = "package"
    elif problem == "extra_field": target["command"] = ["model-selected"]
    elif problem == "missing_field": del target["tool_configuration_sha256"]
    elif problem == "relative_path": target["artifact_path"] = "relative.py"
    elif problem == "traversal": target["artifact_path"] = "/proof-state/../other.py"
    elif problem == "retention": target["retention_until"] = "2027-10-11"
    elif problem == "source_id": target["source_call_id"] = ""
    with pytest.raises(ValueError):
        saved_request(target, baseline)


def test_saved_artifact_sources_must_be_paired_bounded_and_use_exact_flat_virtual_path():
    request = saved_request()
    with pytest.raises(ValueError, match="together"):
        CandidateRequest(request.relative_path, request.baseline_sha256, CANDIDATE, BASELINE)
    with pytest.raises(ValueError, match="together"):
        CandidateRequest(request.relative_path, request.baseline_sha256, CANDIDATE,
                         source_target=request.source_target)
    with pytest.raises(ValueError, match="baseline source hash"):
        saved_request(baseline="x"*65537)
    for path in ("nested/"+request.relative_path, "saved_artifact_"+"f"*64+".py"):
        with pytest.raises(ValueError, match="virtual path"):
            CandidateRequest(path, request.baseline_sha256, CANDIDATE, BASELINE, request.source_target)
    target = source_target()
    copied = validate_saved_artifact_target(target)
    target["scope"]["principal_id"] = "changed:after-validation"
    assert copied["scope"]["principal_id"] == "person:synthetic"


def test_runner_executes_both_exact_sources_at_same_readonly_virtual_module(monkeypatch):
    import eva.operations.component_evaluation as module
    request = saved_request()
    target_path = "/opt/eva/src/eva/" + request.relative_path
    command = ("/usr/local/bin/python", "-I", "-c",
        "import runpy,json,sys; print(json.dumps([runpy.run_path(sys.argv[1])['VALUE']]))", target_path)
    calls, mounted_sources, removed = [], [], []
    def capture(argv, *, deadline, limit):
        if argv[:3] == ["docker", "image", "inspect"]:
            return {**leg(IMAGE), "stdout":IMAGE}
        if argv[:3] == ["docker", "rm", "--force"]:
            removed.append(argv[-1]); return leg("")
        assert argv[:2] == ["docker", "run"]
        calls.append(argv)
        binding = argv[argv.index("--mount")+1]
        parts = dict(item.split("=",1) for item in binding.split(",") if "=" in item)
        source = Path(parts["src"])
        assert source.stat().st_mode & 0o777 == 0o444
        assert parts["dst"] == target_path and binding.endswith(",readonly")
        mounted_sources.append(source.read_text())
        assert argv[argv.index("--network")+1] == "none"
        assert "--read-only" in argv and argv[argv.index("--user")+1] == "65534:65534"
        assert argv[argv.index("--security-opt")+1] == "no-new-privileges"
        bootstrap = argv.index(IMAGE)
        assert argv[bootstrap+1:bootstrap+3] == ["-I", "-c"]
        assert "os.execve" in argv[bootstrap+3] and "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in argv[bootstrap+3]
        selected_command = argv[bootstrap+4:]
        assert tuple(selected_command) == command
        # Execute the actual synthetic suite with the mounted source substituted
        # for its virtual filename; Docker dispatch/cleanup flags remain exercised.
        output = subprocess.run([sys.executable, *selected_command[1:-1], str(source)],
            capture_output=True, text=True, check=False, timeout=5)
        return {**leg(output.stdout), "exit_code":output.returncode, "stderr":output.stderr}
    monkeypatch.setattr(module,"_capture",capture)
    evaluator = ComponentEvaluator(EvaluationConfig(IMAGE, command), source_target=request.source_target)
    value = evaluator.evaluate(request)
    assert mounted_sources == [BASELINE,CANDIDATE]
    assert value["baseline"]["stdout"].strip() == "[1]"
    assert value["candidate"]["stdout"].strip() == "[2]"
    assert value["source_target"] == request.source_target
    assert len(calls) == len(removed) == 2
    assert value["installed"] is False and value["semantic_improvement_verified"] is False
    request.source_target["source_message_id"] = "later:mutation"
    assert value["source_target"]["source_message_id"] == "synthetic:message"


def test_fixed_runner_refuses_other_coherent_scope_without_even_inspecting_image(monkeypatch):
    import eva.operations.component_evaluation as module
    target = source_target()
    target["scope"]["project_id"] = "project:other"
    identity = canonical_sha256({"scope":target["scope"], "receipt":target["receipt_sha256"]})
    target["procedure_reference_id"] = "procedure-reference:" + identity
    monkeypatch.setattr(module,"_capture",lambda *a,**kw:pytest.fail("wrong target reached Docker"))
    evaluator = ComponentEvaluator(EvaluationConfig(IMAGE,("/usr/local/bin/python","-c","fixed-suite")),
        source_target=source_target())
    with pytest.raises(ValueError,match="fixed assessment target"):
        evaluator.evaluate(saved_request(target))


def test_existing_package_runner_keeps_image_baseline_and_single_candidate_mount(monkeypatch):
    import eva.operations.component_evaluation as module
    seen = []
    monkeypatch.setattr(module,"_capture",lambda *args,**kwargs:leg(IMAGE))
    evaluator = ComponentEvaluator(EvaluationConfig(IMAGE,("/usr/local/bin/python","-c","fixed-suite")))
    request = CandidateRequest("contracts/synthetic.py", "b"*64, CANDIDATE)
    def container(image, command, **kwargs):
        seen.append((command,kwargs))
        if len(seen)==1: return leg(json.dumps({"sha256":request.baseline_sha256}))
        return leg("[1]")
    monkeypatch.setattr(evaluator,"_container",container)
    value = evaluator.evaluate(request)
    assert len(seen)==3 and "mount" not in seen[0][1] and "mount" not in seen[1][1]
    assert seen[2][1]["mount"][1] == "/opt/eva/src/eva/contracts/synthetic.py"
    assert "source_target" not in value


@contextmanager
def service(*, suite=None, package=None):
    server = EvaluationServer(("127.0.0.1",0), evaluator=package, token="s"*40,
        allowed_paths=("contracts/synthetic.py",), saved_artifact_suite=suite)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server
    finally: server.shutdown(); thread.join(); server.server_close()


def post(server, payload, token="s"*40, endpoint="/evaluate"):
    request = Request("http://127.0.0.1:"+str(server.server_port)+endpoint,
        json.dumps(payload).encode(), headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urlopen(request,timeout=3) as response: return json.load(response)


def payload(request):
    result = {"relative_path":request.relative_path,"baseline_sha256":request.baseline_sha256,"source":request.source}
    if request.source_target is not None:
        result.update(baseline_source=request.baseline_source,source_target=request.source_target)
    return result


def test_http_routes_saved_artifacts_only_to_fixed_host_suite_and_retains_package_schema():
    calls = []
    class Suite:
        source_target = source_target()
        def __init__(self,name): self.name=name
        def evaluate(self,request): calls.append((self.name,request)); return report(request)
    with service(suite=Suite("saved"),package=Suite("package")) as server:
        request = saved_request()
        assert post(server,payload(request))["source_target"] == request.source_target
        package = CandidateRequest("contracts/synthetic.py","b"*64,CANDIDATE)
        assert "source_target" not in post(server,payload(package))
        for invalid in ({**payload(request),"command":["model-selected"]},
                        {**payload(request),"baseline_source":None,"source_target":None},
                        {**payload(package),"baseline_source":BASELINE}):
            with pytest.raises(HTTPError) as error: post(server,invalid)
            assert error.value.code == 400
        with pytest.raises(HTTPError) as error: post(server,payload(request),token="wrong")
        assert error.value.code == 401
    assert [name for name,_ in calls] == ["saved","package"]


@pytest.mark.parametrize("change", ["foreign_person", "foreign_project", "other_helper"])
def test_fixed_host_suite_rejects_even_coherent_other_manifest_before_runner(change):
    calls = []
    class Suite:
        source_target = source_target()
        def evaluate(self, request): calls.append(request); return report(request)
    target = source_target()
    if change == "foreign_person": target["scope"]["principal_id"] = "person:other"
    elif change == "foreign_project": target["scope"]["project_id"] = "project:other"
    else: target["artifact_path"] = "/proof-state/another-helper.py"
    identity = canonical_sha256({"scope":target["scope"], "receipt":target["receipt_sha256"]})
    target["procedure_reference_id"] = "procedure-reference:" + identity
    request = saved_request(target)
    with service(suite=Suite()) as server:
        with pytest.raises(HTTPError) as error: post(server,payload(request))
        assert error.value.code == 400
    assert calls == []


def test_http_without_saved_suite_cannot_fall_back_to_package_suite():
    class Package:
        def evaluate(self,request): pytest.fail("unconfigured saved source reached package evaluator")
    with service(package=Package()) as server:
        with pytest.raises(HTTPError) as error: post(server,payload(saved_request()))
        assert error.value.code == 400


def test_http_accepts_two_full_size_sources_but_rejects_changed_scope_before_dispatch():
    calls = []
    class Suite:
        def evaluate(self,request): calls.append(request); return report(request)
    target = source_target()
    baseline = "#" + "x"*65534 + "\n"
    target["content_sha256"] = hashlib.sha256(baseline.encode()).hexdigest()
    request = CandidateRequest(saved_request().relative_path,target["content_sha256"],baseline,baseline,target)
    suite = Suite(); suite.source_target = target
    with service(suite=suite) as server:
        assert post(server,payload(request))["baseline_sha256"] == target["content_sha256"]
        invalid = payload(request)
        invalid["source_target"] = deepcopy(target)
        invalid["source_target"]["scope"]["tenant_id"] = "tenant:other"
        with pytest.raises(HTTPError) as error: post(server,invalid)
        assert error.value.code == 400
    assert len(calls)==1


def test_client_requires_saved_suite_and_validates_returned_manifest_and_suite():
    request = saved_request()
    value = report(request)
    calls = []
    class Client(ComponentEvaluationClient):
        def _post(self,endpoint,payload,**kwargs): calls.append((endpoint,payload)); return value
    client = Client("http://unused","unused",(),"0"*64,saved_artifact_suite_sha256=suite_binding(value))
    assert client.evaluate(draft(request)) is value
    assert calls[0][1] == payload(request)
    missing = Client("http://unused","unused",(request.relative_path,),suite_binding(value))
    with pytest.raises(ValueError,match="no installed saved"): missing.evaluate(draft(request))
    assert len(calls)==1
    value["source_target"]["scope"]["principal_id"] = "person:other"
    with pytest.raises(ValueError,match="report binding"): client.evaluate(draft(request))
    value["source_target"] = deepcopy(request.source_target)
    value["development_assessment"]["assessment_sha256"] = "f"*64
    with pytest.raises(ValueError,match="suite changed"): client.evaluate(draft(request))


def test_client_config_binding_is_optional_exact_and_source_dispatch_is_not_model_selected(tmp_path,monkeypatch):
    config = tmp_path / "client.json"
    data = {"url":"http://127.0.0.1:9999","token_file":"/tmp/synthetic-token",
        "allowed_paths":["contracts/synthetic.py"],"suite_sha256":"a"*64,"timeout_seconds":120}
    monkeypatch.setenv("EVA_COMPONENT_EVALUATION_CLIENT_FILE",str(config))
    config.write_text(json.dumps(data))
    assert configured_component_evaluator().saved_artifact_suite_sha256 is None
    data["saved_artifact_suite_sha256"] = "b"*64
    config.write_text(json.dumps(data))
    assert configured_component_evaluator().saved_artifact_suite_sha256 == "b"*64
    data["saved_artifact_suite_sha256"] = "model-selected-command"
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="suite binding"): configured_component_evaluator()


def test_service_config_admits_only_existing_fixed_saved_suite_structure(tmp_path):
    token = tmp_path / "synthetic-token"; token.write_text("s"*40)
    config = tmp_path / "host.json"
    data = {"image":IMAGE,"command":["/usr/local/bin/python","-c","fixed-suite"],
        "token_file":str(token),"bind_host":"127.0.0.1","port":12345,
        "allowed_paths":["contracts/synthetic.py"],"saved_artifact_suite":{
            "source_target":source_target(), "evaluation":{"image":IMAGE,"command":["/usr/local/bin/python","-c","saved-suite"]},
            "assessment":{"cases_file":"/synthetic/cases.json","scorer_file":"/synthetic/score.py","criteria":{}}}}
    config.write_text(json.dumps(data))
    assert load_config(config)[2] == frozenset({"contracts/synthetic.py"})
    del data["saved_artifact_suite"]["source_target"]
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="fixed saved artifact"):
        load_config(config)


@pytest.mark.skipif(not os.environ.get("EVA2_TEST_SAVED_ARTIFACT_EVALUATION_IMAGE"),
                    reason="explicit existing synthetic container image required")
def test_native_saved_sources_use_same_existing_container_runner():
    image = os.environ["EVA2_TEST_SAVED_ARTIFACT_EVALUATION_IMAGE"]
    request = saved_request()
    target = "/opt/eva/src/eva/"+request.relative_path
    command = ("/usr/local/bin/python","-I","-c",
        "import runpy,json,sys; print(json.dumps([runpy.run_path(sys.argv[1])['VALUE']]))",target)
    value = ComponentEvaluator(EvaluationConfig(image,command,deadline_seconds=10),
        source_target=request.source_target).evaluate(request)
    assert value["baseline"]["exit_code"] == value["candidate"]["exit_code"] == 0
    assert value["baseline"]["stdout"].strip() == "[1]" and value["candidate"]["stdout"].strip() == "[2]"
    assert value["source_target"] == request.source_target
