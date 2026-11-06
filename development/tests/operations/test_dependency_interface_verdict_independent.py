"""Independent live-interface verdict/unknown separation; no Docker effects."""
import hashlib
import json
import pytest
from eva.operations.runtime_dependency_update import compare_interface_images
from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
from tests.operations.test_runtime_dependency_update import setup_case, artifact


def fixture(tmp_path, monkeypatch, fault):
    target, build, socket=setup_case(tmp_path)
    inputs=tmp_path/"inputs";inputs.mkdir()
    raw=inputs/"input.json";raw.write_text('{"synthetic":true}')
    manifest=artifact(tmp_path/"inputs.json",{"evaPackageOverlaid":False,"runtimeModulesReplaced":False,"files":[{"path":"input.json","sha256":hashlib.sha256(raw.read_bytes()).hexdigest()}]})
    driver=tmp_path/"driver.py"
    cases=[dict(id=name,path=str(driver),sha256=hashlib.sha256(driver.read_bytes()).hexdigest(),destination="/review/driver.py",command=["/usr/local/bin/python","-I","/review/driver.py"],input_manifest=manifest,mount=dict(path=str(inputs),readonly=True,destination="/review/inputs"),deadline=60,repeats=1,same_expected_baseline_candidate=True,expected_JSON={"ok":True}) for name in ("normal_used_path","restore")]
    target["interfaces"]=artifact(tmp_path/"interfaces.json",dict(contract="EvaIndependentDependencyInterfaces/v1",criteria=target["criteria"],expectations_host_only=True,network="none",cases=cases))
    def output(*args,**kwargs):
        return dict(exit_code=1 if fault=="nonzero" else 0,stdout="invalid-json" if fault=="invalid_json" else json.dumps({"ok":fault!="mismatch"}),timed_out=fault=="timeout",stdout_truncated=fault=="stdout_truncated",stderr_truncated=fault=="stderr_truncated",elapsed_ms=10)
    monkeypatch.setattr(ComponentEvaluator,"_container",output)
    return target,build,EvaluationConfig(image="fixture",command=("python",)),socket


@pytest.mark.parametrize("fault",["timeout","nonzero","stdout_truncated","stderr_truncated","invalid_json"])
def test_live_interface_unknown_never_becomes_negative_behavior(tmp_path,monkeypatch,fault):
    target,build,settings,socket=fixture(tmp_path,monkeypatch,fault)
    with pytest.raises(ValueError):
        compare_interface_images(target,build,settings,fixture_socket=str(socket),candidate_only="candidate")


def test_completed_live_interface_mismatch_is_explicit_negative(tmp_path,monkeypatch):
    target,build,settings,socket=fixture(tmp_path,monkeypatch,"mismatch")
    result=compare_interface_images(target,build,settings,fixture_socket=str(socket),candidate_only="candidate")
    assert result["all_conditions_passed"] is False
    assert len(result["cases"])==4 and all(r["actual"]=={"ok":False} and r["passed"] is False for r in result["cases"])


def test_incomplete_preinstall_comparison_still_retains_baseline(tmp_path,monkeypatch):
    target,build,settings,socket=fixture(tmp_path,monkeypatch,"timeout")
    assert compare_interface_images(target,build,settings,fixture_socket=str(socket))["all_conditions_passed"] is False
