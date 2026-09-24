"""The existing restore session follows successful preparation without reprovision."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tools import installer_prepared_restore as module
from eva.adapters.api.installer_restore_api import register_installer_restore_routes


def test_requires_existing_preparation_and_reuses_one_confirmation_session(monkeypatch):
    state = ["available"]
    calls = []
    workflow = SimpleNamespace(prepare=lambda a,p: calls.append((a,p)) or {"confirmation":"one"},
        execute=lambda token: calls.append(token) or {"inserted_records":2})
    @contextmanager
    def configured(config):
        calls.append("opened")
        try: yield workflow
        finally: calls.append("closed")
    monkeypatch.setattr(module, "configured_restore_workflow", configured)
    preparation = SimpleNamespace(status=lambda:state[0])
    restore = module.PreparedInstallerRestore(preparation=preparation,
        config_builder=lambda target: calls.append("configured") or {})
    with pytest.raises(module.RestoreTargetNotReady): restore.prepare(b"backup","password")
    assert calls == []
    state[0] = "prepared"
    assert restore.prepare(b"backup","password")["confirmation"] == "one"
    assert restore.execute("one")["inserted_records"] == 2
    assert calls.count("configured") == calls.count("opened") == 1
    restore.close()
    assert calls[-1] == "closed"
    with pytest.raises(module.RestoreTargetNotReady): restore.execute("one")


def test_new_session_cannot_execute_old_confirmation_and_changed_state_blocks(monkeypatch):
    state=["prepared"]
    restore=module.PreparedInstallerRestore(preparation=SimpleNamespace(status=lambda:state[0]),
        config_builder=lambda target:pytest.fail("execute must not open a new workflow"))
    with pytest.raises(ValueError, match="check the archive"):restore.execute("old")
    state[0]="needs_inspection"
    with pytest.raises(module.RestoreTargetNotReady):restore.prepare(b"backup","password")
    restore.close()


def test_existing_http_route_explains_missing_target_without_running_builder():
    restore=module.PreparedInstallerRestore(preparation=SimpleNamespace(status=lambda:"running"),
        config_builder=lambda target:pytest.fail("target is not prepared"))
    app=FastAPI()
    register_installer_restore_routes(app,workflow=restore,require_write=lambda request:None,security_headers={})
    response=TestClient(app).post("/internal/v1/onboarding/restore/prepare",json={"archive":"YQ==","passphrase":"password"})
    assert response.status_code==409
    assert "Bereid eerst de doelinstallatie voor" in response.json()["detail"]


def test_bootstrap_connects_both_existing_actions_and_closes(monkeypatch):
    preparation=SimpleNamespace(status=lambda:"prepared")
    captured={}
    def wizard(argv,**kwargs):
        captured.update(kwargs)
        assert kwargs["installation_preparer"] is preparation
        assert kwargs["installation_preparation_status"]()=="prepared"
        return 7
    monkeypatch.setattr(module,"run_installer_wizard",wizard)
    assert module.run_prepared_installer_wizard(preparation=preparation,config_builder=lambda target:{})==7
    assert captured["restore_workflow"]._closed is True
