"""Exact packaged resources share one read-only baseline context in both legs."""
from copy import deepcopy
import csv
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from eva.application.skill_packages import AgentSkillPackage, SkillResource
from eva.contracts.component_target import saved_artifact_virtual_path
from eva.operations import component_evaluation as module
from eva.operations.component_evaluation import CandidateRequest, ComponentEvaluator, EvaluationConfig
from eva.operations.component_final_assessment import HostFinalAssessment
from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient
from tests.operations.test_saved_artifact_component_evaluation import (
    source_target, leg, report, draft, suite_binding, service, post, payload, IMAGE,
)
from tests.operations.test_saved_artifact_final_assessment import host_config, capture_evaluation


SCRIPT = ("import json\nfrom pathlib import Path\nfrom _helper import label\n"
          "print(json.dumps({'selected':'before','helper':label(),"
          "'binary':(Path(__file__).parents[1]/'references/data.bin').read_bytes().hex()}))\n")


def context(resource_path="scripts/sources.py"):
    package = AgentSkillPackage("https://example.invalid/synthetic/skill", "a" * 40, "skill", "MIT", "LICENSE", (
        SkillResource("SKILL.md", b"---\nname: synthetic-skill\ndescription: Synthetic exact-context check.\n---\nRead the test input.\n"),
        SkillResource("LICENSE", b"Synthetic MIT license notice\n"),
        SkillResource(resource_path, SCRIPT.encode()),
        SkillResource("scripts/_helper.py", b"def label():\n    return 'unchanged helper'\n"),
        SkillResource("references/data.bin", bytes([0, 255, 17, 10])),
    ))
    # Intentionally noncanonical storage: transport must retain these raw bytes.
    raw = json.dumps(package.to_wire(), ensure_ascii=False, indent=2) + "\n"
    target = source_target()
    target.update(artifact_path="/proof-state/synthetic.skill.json",
        content_sha256=hashlib.sha256(SCRIPT.encode()).hexdigest(), skill_package_binding={
            "package_content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "package_manifest": package.manifest(), "resource_path": resource_path})
    return package, raw, target


def request_for(raw=None, target=None):
    _, original, original_target = context()
    target = original_target if target is None else target
    return CandidateRequest(saved_artifact_virtual_path(target), target["content_sha256"],
        SCRIPT.replace("'before'", "'after'"), SCRIPT, target, original if raw is None else raw)


def selected(request):
    from eva.application.component_improvements import draft_component_content, prepare_component_release, bind_selected_release
    from eva.application.component_development import compare_development_evaluations
    from tests.application.test_component_development import entry
    value = draft_component_content({"path": request.relative_path, "source_sha256": request.baseline_sha256,
        "old_text": "'before'", "new_text": "'after'", "problem": "synthetic output mismatch",
        "expected_gain": "correct ordinary output", "verification_plan": "fixed synthetic runner"},
        SCRIPT.encode(), source_target=request.source_target)
    value["draft_step"] = 1
    measured = entry(2, hits=3)["report"]
    measured.update(relative_path=request.relative_path, baseline_sha256=request.baseline_sha256,
        candidate_sha256=hashlib.sha256(request.source.encode()).hexdigest(),
        source_target=deepcopy(request.source_target), evaluation_step=2)
    choice = compare_development_evaluations([{"draft_step": 1, "evaluation_step": 2, "report": measured}])
    choice["selection_step"] = 3
    return bind_selected_release(prepare_component_release(value, measured), choice)


def test_missing_package_copy_is_refused_before_docker_but_selected_package_stays_context_free(monkeypatch):
    full = request_for()
    bare = replace(full, baseline_package_content=None)
    monkeypatch.setattr(module, "_capture", lambda *a, **k: pytest.fail("missing context reached Docker"))
    with pytest.raises(ValueError, match="context is missing"):
        ComponentEvaluator(EvaluationConfig(IMAGE, ("/usr/local/bin/python", "fixed"))).evaluate(bare)
    release = selected(full)
    assert "baseline_package_content" not in release
    from eva.operations.component_final_assessment import validate_selected_package
    assert validate_selected_package(release) is release


@pytest.mark.parametrize("fault", ["raw_bytes", "manifest", "resource", "malformed_envelope", "missing_resource"])
def test_package_copy_must_bind_exact_envelope_manifest_and_baseline(fault):
    package, raw, target = context()
    if fault == "raw_bytes":
        raw += " "  # Equivalent JSON is still a different saved source.
    elif fault == "manifest":
        target["skill_package_binding"]["package_manifest"]["origin"]["license_id"] = "different"
    elif fault == "resource":
        package = replace(package, files=tuple(
            replace(item, content=item.content.replace(b"before", b"other"))
            if item.path == "scripts/sources.py" else item for item in package.files))
        raw = json.dumps(package.to_wire())
        target["skill_package_binding"]["package_content_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    elif fault == "malformed_envelope":
        value = package.to_wire(); value["files"][0]["content_base64"] = "not base64"
        raw = json.dumps(value)
        target["skill_package_binding"]["package_content_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    else:
        # A coherent manifest may never silently supply an unrelated resource.
        target["skill_package_binding"]["resource_path"] = "scripts/absent.py"
    with pytest.raises(ValueError):
        request_for(raw, target)


def test_package_context_cannot_be_added_to_plain_source_or_saved_python():
    _, raw, _ = context()
    with pytest.raises(ValueError, match="requires a saved package"):
        CandidateRequest("example.py", "a" * 64, "pass", baseline_package_content=raw)
    from tests.operations.test_saved_artifact_component_evaluation import saved_request
    with pytest.raises(ValueError, match="requires a saved package"):
        replace(saved_request(), baseline_package_content=raw)


def test_both_legs_use_actual_package_path_and_exact_unchanged_files(monkeypatch):
    request = request_for()
    package, _, _ = context()
    target = "/eva-skill/scripts/sources.py"
    command = ("/usr/local/bin/python", target)
    mounts, cleanups = [], []
    def capture(argv, *, deadline, limit):
        if argv[:3] == ["docker", "image", "inspect"]: return leg(IMAGE)
        if argv[:3] == ["docker", "rm", "--force"]:
            cleanups.append(argv[-1]); return leg("")
        specifications = [argv[i + 1] for i, value in enumerate(argv) if value == "--mount"]
        decoded = [dict(item.split("=", 1) for item in next(csv.reader([value])) if "=" in item)
                   for value in specifications]
        assert len(decoded) == 2
        full, overlay = decoded
        assert full["dst"] == "/eva-skill" and overlay["dst"] == target
        package_root, chosen = Path(full["src"]), Path(overlay["src"])
        assert package_root.stat().st_mode & 0o777 == 0o555
        assert chosen.stat().st_mode & 0o777 == 0o444
        for item in package.files:
            assert (package_root / item.path).read_bytes() == item.content
            assert (package_root / item.path).stat().st_mode & 0o777 == 0o444
        assert all(next(csv.reader([value]))[-1] == "readonly" for value in specifications)
        assert argv[argv.index("--network") + 1] == "none"
        assert "--read-only" in argv and "--cap-drop" in argv
        assert argv[argv.index("--user") + 1] == "65534:65534"
        mounts.append((full["src"], chosen.read_bytes()))
        # Run the real script and helper, with only the selected resource varied.
        script = package_root / "scripts/sources.py"
        original = script.read_bytes(); script.chmod(0o644); script.write_bytes(chosen.read_bytes())
        try:
            result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=5,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
        finally:
            script.write_bytes(original); script.chmod(0o444)
        return {**leg(result.stdout), "exit_code": result.returncode, "stderr": result.stderr}
    monkeypatch.setattr(module, "_capture", capture)
    value = ComponentEvaluator(EvaluationConfig(IMAGE, command), source_target=request.source_target).evaluate(request)
    assert mounts[0][0] == mounts[1][0] and len(cleanups) == 2
    assert mounts[0][1] == SCRIPT.encode() and mounts[1][1] == request.source.encode()
    baseline, candidate = (json.loads(value[leg_name]["stdout"]) for leg_name in ("baseline", "candidate"))
    assert baseline == {"selected": "before", "helper": "unchanged helper", "binary": "00ff110a"}
    assert candidate == {**baseline, "selected": "after"}
    assert "baseline_package_content" not in value


def test_package_context_crosses_strict_http_transport_but_never_the_draft():
    request = request_for(); value = report(request); calls = []
    class Client(ComponentEvaluationClient):
        def _post(self, endpoint, payload, **kwargs):
            calls.append(payload); return value
    client = Client("http://unused", "unused", (), "0" * 64, saved_artifact_suite_sha256=suite_binding(value))
    source_draft = draft(request); frozen = deepcopy(source_draft)
    assert client.evaluate(source_draft, baseline_package_content=request.baseline_package_content) is value
    assert source_draft == frozen and "baseline_package_content" not in source_draft
    assert calls[0]["baseline_package_content"] == request.baseline_package_content
    with pytest.raises(ValueError, match="context is missing"):
        client.evaluate(source_draft)
    assert len(calls) == 1
    class Suite:
        source_target = request.source_target
        def evaluate(self, incoming):
            assert incoming.baseline_package_content == request.baseline_package_content
            return report(incoming)
    with service(suite=Suite()) as server:
        transported = {**payload(request), "baseline_package_content": request.baseline_package_content}
        assert post(server, transported)["source_target"] == request.source_target
        from urllib.error import HTTPError
        with pytest.raises(HTTPError): post(server, payload(request))
        from tests.operations.test_saved_artifact_component_evaluation import saved_request
        with pytest.raises(HTTPError): post(server, {**payload(saved_request()), "baseline_package_content": None})


def final_config(tmp_path, request):
    config = host_config(tmp_path, request.source_target)
    baseline = tmp_path / "baseline.skill.json"
    baseline.write_text(request.baseline_package_content)
    config["bindings"]["baseline_skill_package"] = {"path": str(baseline),
        "sha256": hashlib.sha256(baseline.read_bytes()).hexdigest()}
    return config


def test_final_assessor_gets_exact_context_only_from_its_pinned_host_binding(tmp_path, monkeypatch):
    request = request_for(); release = selected(request)
    config = final_config(tmp_path, request); assessor = HostFinalAssessment(config)
    requests = capture_evaluation(monkeypatch, assessor)
    output = tmp_path / "result"; output.mkdir(mode=0o700)
    assert assessor(release, output)["decision"] == "reject"
    actual, = requests
    assert actual.baseline_package_content == request.baseline_package_content
    stored = json.loads((output / "isolated-final.private.json").read_text())
    assert stored["final_assessment_binding"]["bindings"]["baseline_skill_package"] == hashlib.sha256(
        request.baseline_package_content.encode()).hexdigest()
    assert "baseline_package_content" not in stored and "baseline_package_content" not in release


@pytest.mark.parametrize("fault", ["absent", "hash", "envelope"])
def test_final_assessor_refuses_unpinned_or_changed_baseline_context(tmp_path, fault):
    request = request_for(); config = final_config(tmp_path, request)
    if fault == "absent": config["bindings"].clear()
    elif fault == "hash": config["bindings"]["baseline_skill_package"]["sha256"] = "f" * 64
    else: Path(config["bindings"]["baseline_skill_package"]["path"]).write_text(request.baseline_package_content + " ")
    with pytest.raises(ValueError, match="baseline|inputs changed"):
        HostFinalAssessment(config)


def test_final_assessor_rechecks_context_before_dispatch(tmp_path, monkeypatch):
    request = request_for(); config = final_config(tmp_path, request); assessor = HostFinalAssessment(config)
    requests = capture_evaluation(monkeypatch, assessor)
    Path(config["bindings"]["baseline_skill_package"]["path"]).write_text(request.baseline_package_content + " ")
    with pytest.raises(ValueError, match="inputs changed"):
        assessor(selected(request), tmp_path)
    assert requests == [] and assessor.scorer.checks == []


@pytest.mark.skipif(not os.environ.get("EVA2_TEST_COMPONENT_IMAGE"), reason="explicit local immutable image required")
def test_actual_offline_containers_execute_selected_script_with_unchanged_helper_and_binary_context():
    image = os.environ["EVA2_TEST_COMPONENT_IMAGE"]
    assert image.startswith("sha256:")
    request = request_for()
    result = ComponentEvaluator(EvaluationConfig(image, ("/usr/local/bin/python", "/eva-skill/scripts/sources.py")),
        source_target=request.source_target).evaluate(request)
    for name, expected in (("baseline", "before"), ("candidate", "after")):
        assert result[name]["exit_code"] == 0, result[name]
        assert json.loads(result[name]["stdout"]) == {
            "selected": expected, "helper": "unchanged helper", "binary": "00ff110a"}
    assert result["installed"] is False and result["semantic_improvement_verified"] is False


def test_specific_saved_suite_uses_existing_client_path_binding_without_replacing_legacy_default():
    request = request_for(); value = report(request); calls = []
    class Client(ComponentEvaluationClient):
        def _post(self, endpoint, payload, **kwargs):
            calls.append(payload); return value
    client = Client("http://unused", "unused", (request.relative_path,), "0" * 64,
        suite_bindings=((request.relative_path, suite_binding(value)),))
    assert client.evaluate(draft(request), baseline_package_content=request.baseline_package_content) is value
    # An explicit wrong path suite must not silently fall back to a matching old default.
    wrong = replace(client, saved_artifact_suite_sha256=suite_binding(value),
        suite_bindings=((request.relative_path, "f" * 64),))
    with pytest.raises(ValueError, match="suite changed"):
        wrong.evaluate(draft(request), baseline_package_content=request.baseline_package_content)
    unlisted = replace(client, allowed_paths=())
    with pytest.raises(ValueError, match="no installed saved"):
        unlisted.evaluate(draft(request), baseline_package_content=request.baseline_package_content)
    assert len(calls) == 2


def test_http_specific_saved_suite_retains_old_default_and_fails_closed_on_wrong_target():
    import threading
    from urllib.error import HTTPError
    from eva.operations.component_evaluation_service import EvaluationServer
    from tests.operations.test_saved_artifact_component_evaluation import saved_request
    specific, legacy = request_for(), saved_request()
    calls = []
    class Suite:
        def __init__(self, name, target): self.name, self.source_target = name, target
        def evaluate(self, incoming):
            calls.append((self.name, incoming)); return report(incoming)
    server = EvaluationServer(("127.0.0.1", 0), evaluator=None, token="s" * 40,
        allowed_paths=(specific.relative_path,),
        component_suites={specific.relative_path: Suite("specific", specific.source_target)},
        saved_artifact_suite=Suite("legacy", legacy.source_target))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        post(server, {**payload(specific), "baseline_package_content": specific.baseline_package_content})
        post(server, payload(legacy))
        other_target = deepcopy(specific.source_target); other_target["artifact_path"] = "/proof-state/other.skill.json"
        other = request_for(target=other_target)
        with pytest.raises(HTTPError):
            post(server, {**payload(other), "baseline_package_content": other.baseline_package_content})
        plain = CandidateRequest(specific.relative_path, specific.baseline_sha256, specific.source)
        with pytest.raises(HTTPError): post(server, payload(plain))
    finally:
        server.shutdown(); thread.join(); server.server_close()
    assert [name for name, _ in calls] == ["specific", "legacy"]


def test_host_config_pins_specific_saved_suite_to_its_existing_mapping_key(tmp_path):
    from eva.operations.component_evaluation_service import load_config
    request = request_for()
    token = tmp_path / "token"; token.write_text("s" * 40)
    config = {"image": IMAGE, "command": ["/usr/local/bin/python", "fixed"],
        "token_file": str(token), "bind_host": "127.0.0.1", "port": 12345,
        "allowed_paths": [request.relative_path], "component_suites": {request.relative_path: {
            "evaluation": {"image": IMAGE, "command": ["/usr/local/bin/python", "/eva-skill/scripts/sources.py"]},
            "assessment": {}, "source_target": request.source_target}}}
    path = tmp_path / "config.json"; path.write_text(json.dumps(config))
    assert load_config(path)[2] == frozenset({request.relative_path})
    suite = config["component_suites"].pop(request.relative_path)
    config["component_suites"]["other.py"] = suite; config["allowed_paths"].append("other.py")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="virtual path"):
        load_config(path)
