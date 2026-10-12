"""Only an explicitly registered evaluation transports host-derived scoped metadata."""
from copy import deepcopy
import hashlib
import json

import pytest

from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient, configured_component_evaluator
from eva.application.component_development import WORK_STRATEGY_EVALUATION_PATH, make_scoped_component_evaluation_binding


def setup_client(*, registered=True):
    path = WORK_STRATEGY_EVALUATION_PATH
    draft = {"path": path, "source_sha256": "a" * 64, "candidate_content": "return \"bounded strategy\""}
    draft["candidate_sha256"] = hashlib.sha256(draft["candidate_content"].encode()).hexdigest()
    report = {"contract": "EvaIsolatedComponentEvaluation/v1", "relative_path": path,
        "baseline_sha256": draft["source_sha256"], "candidate_sha256": draft["candidate_sha256"],
        "image_id": "sha256:" + "b" * 64, "command_sha256": "c" * 64,
        "development_assessment": {"assessment_sha256": "d" * 64},
        "installed": False, "semantic_improvement_verified": False}
    suite = hashlib.sha256(json.dumps([report["image_id"], report["command_sha256"],
        report["development_assessment"]["assessment_sha256"]], separators=(",", ":")).encode()).hexdigest()
    binding = make_scoped_component_evaluation_binding(task_id="original-work", draft_step=1,
        evaluation_step=2, evaluation_call_id="evaluate-original", spec_sha256="e" * 64,
        source_ingress_sha256="f" * 64, draft_thread_id="component-draft:" + "0" * 64,
        draft_event_payload_sha256="1" * 64, path=path, baseline_sha256=draft["source_sha256"],
        candidate_sha256=draft["candidate_sha256"], suite_sha256=suite)
    calls = []
    class Client(ComponentEvaluationClient):
        def _post(self, endpoint, payload, **kwargs):
            calls.append((endpoint, deepcopy(payload)))
            return deepcopy(report)
    client = Client("http://unused", "unused", (path,), "0" * 64,
        suite_bindings=((path, suite),), work_execution_paths=(path,) if registered else ())
    report["execution_binding_ref"] = binding["execution_binding_ref"]
    return client, draft, binding, report, calls


def test_registered_client_transports_exact_binding_with_existing_payload_and_pins_echo_suite():
    client, draft, binding, report, calls = setup_client()
    assert client.evaluate(draft, execution_binding=binding) == report
    assert calls == [("/evaluate", {"relative_path": draft["path"], "source": draft["candidate_content"],
        "baseline_sha256": draft["source_sha256"], "execution_binding": binding})]
    report["execution_binding_ref"] = "f" * 64
    with pytest.raises(ValueError, match="report binding"):
        client.evaluate(draft, execution_binding=binding)
    report["execution_binding_ref"] = binding["execution_binding_ref"]
    report["development_assessment"]["assessment_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="suite changed"):
        client.evaluate(draft, execution_binding=binding)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong-contract", "bool-step", "wrong-order", "empty-task", "invalid-call", "wrong-ref"])
def test_registered_client_rejects_missing_or_malformed_binding_before_transport(mutation):
    client, draft, binding, _, calls = setup_client()
    if mutation == "missing": binding = None
    if mutation == "extra": binding["scope"] = "model-chosen"
    if mutation == "wrong-contract": binding["contract"] = "different"
    if mutation == "bool-step": binding["draft_step"] = True
    if mutation == "wrong-order": binding["evaluation_step"] = binding["draft_step"]
    if mutation == "empty-task": binding["task_id"] = ""
    if mutation == "invalid-call": binding["evaluation_call_id"] = "call\nchanged"
    if mutation == "wrong-ref": binding["execution_binding_ref"] = "A" * 64
    with pytest.raises(ValueError, match="binding required"):
        client.evaluate(draft, execution_binding=binding)
    assert not calls


def test_registered_candidate_source_must_match_its_hash_before_transport():
    client, draft, binding, _, calls = setup_client()
    draft["candidate_content"] += "changed"
    with pytest.raises(ValueError, match="source hash differs"):
        client.evaluate(draft, execution_binding=binding)
    assert not calls


def test_unregistered_static_client_keeps_original_payload_and_refuses_binding():
    client, draft, binding, report, calls = setup_client(registered=False)
    del report["execution_binding_ref"]
    assert client.evaluate(draft) == report
    assert calls[0][1] == {"relative_path": draft["path"], "source": draft["candidate_content"],
        "baseline_sha256": draft["source_sha256"]}
    with pytest.raises(ValueError, match="path is not configured"):
        client.evaluate(draft, execution_binding=binding)
    assert len(calls) == 1


def test_saved_artifact_cannot_acquire_work_authority_even_if_virtual_path_is_registered():
    from tests.operations.test_saved_artifact_component_evaluation import saved_request, draft, report, suite_binding
    request = saved_request()
    value = report(request)
    calls = []
    class Client(ComponentEvaluationClient):
        def _post(self, *args, **kwargs): calls.append(args); return value
    client = Client("http://unused", "unused", (request.relative_path,), "0" * 64,
        saved_artifact_suite_sha256=suite_binding(value), work_execution_paths=(request.relative_path,))
    with pytest.raises(ValueError, match="path is not configured"):
        client.evaluate(draft(request), execution_binding=setup_client()[2])
    assert not calls


def test_config_defaults_to_static_and_registers_only_an_allowed_subset(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    data = {"url": "http://127.0.0.1:9999", "token_file": "/unused", "allowed_paths":
        [WORK_STRATEGY_EVALUATION_PATH, "application/static.py"], "suite_sha256": "a" * 64,
        "timeout_seconds": 120}
    monkeypatch.setenv("EVA_COMPONENT_EVALUATION_CLIENT_FILE", str(path))
    path.write_text(json.dumps(data))
    assert configured_component_evaluator().work_execution_paths == ()
    data["work_execution_paths"] = [WORK_STRATEGY_EVALUATION_PATH]
    path.write_text(json.dumps(data))
    client = configured_component_evaluator()
    assert client.work_execution_paths == (WORK_STRATEGY_EVALUATION_PATH,)
    assert client.allowed_paths == tuple(data["allowed_paths"])


@pytest.mark.parametrize("paths", [None, "bootstrap/delegated_digital_runner.py", [1],
    ["unregistered.py"], [WORK_STRATEGY_EVALUATION_PATH] * 2])
def test_invalid_work_registration_is_refused(tmp_path, monkeypatch, paths):
    path = tmp_path / "client.json"
    data = {"url": "http://127.0.0.1:9999", "token_file": "/unused", "allowed_paths":
        [WORK_STRATEGY_EVALUATION_PATH], "suite_sha256": "a" * 64, "timeout_seconds": 120,
        "work_execution_paths": paths}
    monkeypatch.setenv("EVA_COMPONENT_EVALUATION_CLIENT_FILE", str(path))
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="work execution paths"):
        configured_component_evaluator()


def test_direct_client_constructor_cannot_register_an_unallowed_path():
    with pytest.raises(ValueError, match="work execution paths"):
        ComponentEvaluationClient("http://unused", "unused", ("ordinary.py",), "a" * 64,
            work_execution_paths=(WORK_STRATEGY_EVALUATION_PATH,))
