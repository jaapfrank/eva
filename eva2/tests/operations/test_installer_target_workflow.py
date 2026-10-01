"""Durable execution boundaries around the existing preparation operations."""
from dataclasses import fields
from types import SimpleNamespace
import json
import pytest

from tools import installer_target_workflow as module


@pytest.fixture
def flow(tmp_path, monkeypatch):
    workspace = tmp_path / "workflow"
    workspace.mkdir(mode=0o700)
    plan = {"plan_sha256": "a" * 64}
    values = {field.name: None for field in fields(module.InstallerTargetSettings)}
    values.update(namespace="new-eva", database_services=("postgres",), schema_service="schema-installer")
    values["image_roles"] = {"core": "core-image"}
    settings = module.InstallerTargetSettings(**values)
    events = []
    monkeypatch.setattr(module, "bind_installer_runtime_inputs", lambda **kwargs: events.append("inputs") or "bound-inputs")
    def materialize(**kwargs):
        events.append("materialization")
        kwargs["target_root"].mkdir(mode=0o700)
        return {}
    monkeypatch.setattr(module, "materialize_installer_runtime_files", materialize)
    monkeypatch.setattr(module, "bind_installer_target_graph", lambda **kwargs:
        events.append("bindings") or SimpleNamespace(graph={"candidate": True}, pending=()))
    from tools import installer_runtime_access
    def establish_access(**kwargs):
        events.append("access")
        kwargs["target_root"].mkdir(mode=0o700)
        return SimpleNamespace(graph={"services": {"core": {}}}, receipt={})
    monkeypatch.setattr(installer_runtime_access, "establish_installer_runtime_access", establish_access)
    def provision(**kwargs):
        events.append("resources")
        assert kwargs["launch_graph"] == {"services": {"core": {}}}
        kwargs["workspace"].mkdir(mode=0o700)
        receipt={"status": "prepared", "schema_completed": True}
        (kwargs["workspace"] / "preparation-receipt.json").write_text(json.dumps(receipt))
        return receipt
    monkeypatch.setattr(module, "prepare_restore_target", provision)
    def new():
        return module.InstallerTargetWorkflow(workspace=workspace, settings=settings, plan_reader=lambda: dict(plan))
    return new, plan, events


def test_same_preparation_callback_reads_durable_success_after_restart(flow):
    new, plan, events = flow
    workflow = new()
    assert workflow.status() == "available"
    workflow(plan)
    assert events == ["inputs", "materialization", "bindings", "access", "inputs", "resources"]
    assert new().status() == "prepared"
    assert json.loads((workflow.access_root / "launch-graph.json").read_text()) == {"services": {"core": {}}}
    with pytest.raises(ValueError, match="already attempted"):
        new()(plan)
    assert events.count("resources") == 1


def test_partial_materialization_is_not_replayed_after_restart(flow, monkeypatch):
    new, plan, events = flow
    def fail(**kwargs):
        kwargs["target_root"].mkdir(mode=0o700)
        (kwargs["target_root"] / "kept").write_text("retained")
        raise RuntimeError("private value")
    monkeypatch.setattr(module, "materialize_installer_runtime_files", fail)
    workflow = new()
    with pytest.raises(RuntimeError, match="inspect retained"):
        workflow(plan)
    assert new().status() == "needs_inspection"
    assert (workflow.material_root / "kept").read_text() == "retained"
    assert "private value" not in workflow.receipt_path.read_text()
    assert "resources" not in events


def test_input_rotation_after_access_prevents_resources(flow, monkeypatch):
    new, plan, events = flow
    bindings = iter(("before", "after"))
    monkeypatch.setattr(module, "bind_installer_runtime_inputs", lambda **kwargs: next(bindings))
    with pytest.raises(RuntimeError):
        new()(plan)
    assert new().status() == "needs_inspection"
    assert "resources" not in events


def test_unfinished_claim_and_orphaned_files_require_inspection(flow):
    new, plan, _ = flow
    workflow = new()
    workflow.material_root.mkdir(mode=0o700)
    assert new().status() == "needs_inspection"
    module.write_private_once(workflow.receipt_path, module.canonical_json_bytes({
        "contract": "EvaInstallerTargetWorkflow/v1", "status": "running"}))
    assert new().status() == "needs_inspection"


def test_incomplete_bindings_do_not_provision(flow, monkeypatch):
    new, plan, events = flow
    monkeypatch.setattr(module, "bind_installer_target_graph", lambda **kwargs:
        SimpleNamespace(graph=None, pending=("core:/identity",)))
    with pytest.raises(RuntimeError):
        new()(plan)
    assert "resources" not in events and "access" not in events
    assert new().status() == "needs_inspection"



def test_ordinary_launcher_uses_live_wizard_stores_and_current_plan(flow, tmp_path, monkeypatch):
    from eva.bootstrap import installer_wizard_cli
    new, plan, events = flow
    template = new().settings
    secrets, budget = object(), object()
    def wizard(argv, **kwargs):
        preparation = kwargs["installation_preparation_factory"](
            workspace=tmp_path, secret_store=secrets, budget_workflow=budget,
            plan_reader=lambda: dict(plan),
        )
        assert preparation.settings.secret_store is secrets
        assert preparation.settings.budget_workflow is budget
        assert preparation.settings.release_manifest is template.release_manifest
        assert preparation.workspace == tmp_path / "target"
        assert events == []
        plan["plan_sha256"] = "b" * 64
        preparation(dict(plan))
        assert preparation.status() == "prepared"
        assert events.count("resources") == 1
        return 0
    monkeypatch.setattr(installer_wizard_cli, "run_installer_wizard", wizard)
    assert module.run_target_installer_wizard(settings=template, argv=["--workspace", str(tmp_path)]) == 0
    assert template.secret_store is None


@pytest.mark.parametrize("fail", [False, True])
def test_fresh_start_is_durable_and_never_reprovisions(flow, monkeypatch, fail):
    from dataclasses import replace
    from tools import installation_fresh_start
    new, plan, events = flow
    workflow = new()
    start = module.InstallerFreshStartSettings("postgres", "core", ("core",), {"access_token": "private-token"})
    workflow.settings = replace(workflow.settings, fresh_start=start)
    def run(**kwargs):
        events.append("start")
        assert json.loads(workflow.receipt_path.read_text())["eva_started"] is None
        assert kwargs["launch_graph"] == {"services": {"core": {}}}
        assert kwargs["materialized_bind_root"] == workflow.access_root
        assert kwargs["authority"] is start.authority
        if fail:
            raise installation_fresh_start.FreshRuntimeStartError({"runtime_started": None, "started": []})
        return {"status": "runtime_started_verified", "runtime_started": True}
    monkeypatch.setattr(installation_fresh_start, "start_fresh_runtime", run)
    if fail:
        with pytest.raises(RuntimeError, match="inspect retained"):
            workflow(plan)
    else:
        workflow(plan)
    assert new().status() == ("needs_inspection" if fail else "runtime_ready")
    saved = json.loads(workflow.receipt_path.read_text())
    assert saved["eva_started"] is (None if fail else True)
    assert "private-token" not in workflow.receipt_path.read_text()
    with pytest.raises(ValueError, match="already attempted"):
        new()(plan)
    assert events.count("resources") == events.count("start") == 1
