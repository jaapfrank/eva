"""The independent oracle receives a complete original import-only context."""
import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations import runtime_repair_baseline as baseline
from eva.operations.reviewed_component_application import _artifact
from eva.operations.runtime_repair_development import _put
from test_runtime_repair_diagnostics import input_set, prepare as prepare_diagnostics, sha


@pytest.fixture
def import_context(input_set):
    f = input_set
    loader = baseline._LOADER
    core_bytes = b"# original observed loader\\n"
    loader_sha = sha(core_bytes)
    f.target["sources"][loader] = loader_sha
    for frame in f.target["failure"]["observation"]["frames"]:
        if frame["path"] == loader:
            frame["source_sha256"] = loader_sha
    if "repair_services" not in f.target:
        f.target["target_id"] = canonical_sha256({
            key: value for key, value in f.target.items() if key != "target_id"
        })
    f.metadata["target_id"] = f.target["target_id"]
    f.blocked["target_id"] = f.target["target_id"]
    f.blocked["verified_source"]["expected_sha256"] = loader_sha
    f.blocked["verified_source"]["actual_sha256"] = loader_sha
    f.blocked_receipt = _put(f.directory / "first-blocked.json", f.blocked)
    f.previous = prepare_diagnostics(f)
    f.failed_sha = "8" * 64
    _, observed = baseline._target_binding(f.target)
    f.second_block = {
        "status": "blocked", "target_id": f.target["target_id"], "role": "original_behavior_oracle",
        "input_binding": f.previous["sha256"],
        "verified_sources": {**observed, "diagnostic_manifest_sources": "Bound context files"},
        "new_evidence": ["PRIVATE_AUTHOR_NOTE_MUST_NOT_BE_COPIED"],
        "missing_required_control": "Complete original dependency tree",
        "observed_import_failures": [
            {"module": "eva.adapters.postgres.scheduled_work_failure", "dependency": "eva.contracts.timestamps",
             "error": "ModuleNotFoundError", "via": "eva.adapters.postgres.operational_timeline"},
            {"module": "eva.contracts.canonical_json", "dependency": "eva.contracts.base",
             "error": "ModuleNotFoundError"},
        ],
        "reproduction_probe": "Import only", "probe_limits": "No behaviour exercised",
        "assessment": "Incomplete import source", "resume_requirement": "Trusted complete original",
        "oracle_created": False, "candidate_reviewed_or_submitted": False, "installation_modified": False,
    }
    f.second_receipt = _put(f.directory / "second-blocked.json", f.second_block)
    f.programming_image = "sha256:" + "9" * 64
    files = {**f.sources, loader: core_bytes, "__init__.py": b"# package\\n",
             "contracts/__init__.py": b"# package\\n", "contracts/timestamps.py": b"# timestamp\\n",
             "contracts/base.py": b"# base\\n"}
    imports = sorted(set(files) - set(baseline._CONTEXT_FILES))
    manifest = {path: {"sha256": sha(raw), "size": len(raw)} for path, raw in files.items()}
    f.baseline = {"python": "3.12.16", "distributions": {"SQLAlchemy": "2.1.8"},
                  "import_files": imports, "sources": manifest,
                  "source_bytes": {path: base64.b64encode(raw).decode() for path, raw in files.items()}}
    f.programming = {"python": "3.12.14", "distributions": {"SQLAlchemy": "2.0.52"},
                     "import_files": imports, "sources": {path: manifest[path] for path in imports}}
    f.files = files

    def command(argv, **kwargs):
        assert argv[:5] == ["docker", "run", "--rm", "--pull", "never"]
        assert "--network" in argv and "none" in argv
        assert "--read-only" in argv and "--cap-drop" in argv and "ALL" in argv
        assert "10001:10001" in argv and "-B" in argv and kwargs["timeout"] == 45
        if argv[-3] == "collect":
            assert f.target["installation"]["core"]["image"] in argv
            assert "--mount" not in argv
            report = f.baseline
        else:
            assert argv[-3] == "verify" and f.programming_image in argv
            mount = argv[argv.index("--mount") + 1]
            assert mount.endswith(",dst=/workspace,readonly")
            workspace = Path(mount.removeprefix("type=bind,src=").removesuffix(",dst=/workspace,readonly"))
            for path, raw in f.files.items():
                actual = workspace / "repair" / "diagnostics" / "eva" / path
                assert actual.read_bytes() == raw and actual.stat().st_mode & 0o777 == 0o444
            report = f.programming
        return SimpleNamespace(returncode=0, stdout=json.dumps(report))

    f.publisher.run = Mock(side_effect=command)
    return f


def prepare(f):
    return baseline.prepare_scheduled_failure_import_closure(
        f.publisher, f.runtime, f.target, f.directory, programming_image=f.programming_image,
        previous_diagnostics=f.previous, failed_work_sha256=f.failed_sha, blocked_receipt=f.second_receipt,
    )


def seed(f, receipt, artifacts):
    return baseline.seed_scheduled_failure_import_closure(
        artifacts, f.metadata, f.target, receipt, previous_diagnostics_sha256=f.previous["sha256"],
        failed_work_sha256=f.failed_sha, blocked_sha256=f.second_receipt["sha256"],
    )


@pytest.mark.parametrize("input_set", ["paired-v1"], indirect=True)
def test_complete_original_import_closure_is_seeded_separately(import_context):
    f = import_context
    compile(baseline._IMPORT_DIAGNOSTIC, "<fixed import diagnostic>", "exec")
    prior = Path(f.previous["path"]).read_bytes()
    failed = Path(f.second_receipt["path"]).read_bytes()
    target = deepcopy(f.target)
    receipt = prepare(f)
    _, value = _artifact(receipt)
    manifest = value["manifest"]
    assert f.publisher.run.call_count == 2 and manifest["import_only"] is True
    assert manifest["programming_image"] == f.programming_image
    assert manifest["previous_diagnostics_sha256"] == f.previous["sha256"]
    assert manifest["failed_work_sha256"] == f.failed_sha
    assert manifest["blocked_sha256"] == f.second_receipt["sha256"]
    assert manifest["baseline_import"]["python"] == "3.12.16"
    assert manifest["programming_import"]["python"] == "3.12.14"
    assert "PRIVATE_AUTHOR_NOTE" not in json.dumps(value)
    artifacts = SimpleNamespace(programming=SimpleNamespace(config=SimpleNamespace(image=f.programming_image)),
                                _seed=Mock())
    reply = seed(f, receipt, artifacts)
    assert reply["import_context_only"] is True
    assert reply["comparison_module_path"] == "/opt/eva/src/eva/" + baseline._LOADER
    assert "normally" in reply["driver_import_policy"]
    _, files = artifacts._seed.call_args.args
    assert files["repair/diagnostics/eva/" + baseline._LOADER] == f.files[baseline._LOADER]
    assert all(path.startswith("repair/diagnostics/") for path in files)
    assert f.target == target and Path(f.previous["path"]).read_bytes() == prior
    assert Path(f.second_receipt["path"]).read_bytes() == failed


@pytest.mark.parametrize("change", ["prior_binding", "oracle_present", "wrong_image_import", "changed_context_source"])
def test_refuses_incomplete_or_changed_import_evidence(import_context, change):
    f = import_context
    if change == "prior_binding":
        f.second_block["input_binding"] = "0" * 64
        f.second_receipt = _put(f.directory / "mismatched-input.json", f.second_block)
    elif change == "oracle_present":
        f.second_block["oracle_created"] = True
        f.second_receipt = _put(f.directory / "unwanted-oracle.json", f.second_block)
    elif change == "wrong_image_import":
        f.programming["sources"][baseline._LOADER] = {"sha256": "0" * 64, "size": 1}
    else:
        source = "bootstrap/self_repair.py"
        raw = b"# changed immutable context"
        f.baseline["sources"][source] = {"sha256": sha(raw), "size": len(raw)}
        f.baseline["source_bytes"][source] = base64.b64encode(raw).decode()
    with pytest.raises(ValueError):
        prepare(f)
    assert not (f.directory / "baseline-import-closure.private.json").exists()


@pytest.mark.parametrize("change", ["candidate", "programming_image"])
def test_seed_refuses_changed_role_or_workspace_image(import_context, change):
    f = import_context
    receipt = prepare(f)
    if change == "candidate":
        f.metadata["candidate_sha256"] = "a" * 64
    image = ("sha256:" + "a" * 64) if change == "programming_image" else f.programming_image
    artifacts = SimpleNamespace(programming=SimpleNamespace(config=SimpleNamespace(image=image)), _seed=Mock())
    with pytest.raises(ValueError):
        seed(f, receipt, artifacts)
    artifacts._seed.assert_not_called()
