"""Owner authority, bounded intake and explicit dispatch around model settings."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx2
import pytest
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_model_providers import install_dashboard_model_providers
from tests.model.test_optional_model_providers import configured


def test_api_does_not_dispatch_without_explicit_check_or_current_owner(tmp_path,monkeypatch):
    seen=[]
    _,control,identifier=configured(tmp_path,'openai_compatible',lambda request:seen.append(request) or httpx2.Response(401,json={}))
    import eva.bootstrap.optional_model_providers as providers
    import eva.bootstrap.dashboard_work_context as authority
    monkeypatch.setattr(providers,'attach_optional_models',lambda runtime:control)
    allowed=[True];checks=[]
    def owner(runtime):
        checks.append(True)
        if not allowed[0]:raise RuntimeError('revoked')
    monkeypatch.setattr(authority,'owner_turn',owner)
    app=FastAPI();install_dashboard_model_providers(app,access=DashboardAccess(),runtime=SimpleNamespace())
    with TestClient(app) as client:
        result=client.get('/internal/v1/dashboard/model-providers')
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        assert client.post('/internal/v1/dashboard/model-providers/check',json={'id':identifier,'allow_provider_request':False}).status_code==422
        assert client.post('/internal/v1/dashboard/model-providers/select',json={'id':identifier}).status_code==422
        assert not seen
        assert client.post('/internal/v1/dashboard/model-providers/check',json={'id':identifier,'allow_provider_request':True}).status_code==200
        assert len(seen)==1 and len(checks)>=6
        allowed[0]=False
        assert client.get('/internal/v1/dashboard/model-providers').status_code==403
        assert client.post('/internal/v1/dashboard/model-providers/check',json={'id':identifier,'allow_provider_request':True}).status_code==403
        assert len(seen)==1


def test_body_wait_and_probe_admission_recheck_authority(tmp_path,monkeypatch):
    seen=[]
    _,control,identifier=configured(tmp_path,'openai_compatible',lambda request:seen.append(request) or httpx2.Response(401,json={}))
    import eva.bootstrap.optional_model_providers as providers
    import eva.bootstrap.dashboard_work_context as authority
    monkeypatch.setattr(providers,'attach_optional_models',lambda runtime:control)
    allowed=[True]
    def owner(runtime):
        if not allowed[0]:raise RuntimeError('revoked')
    monkeypatch.setattr(authority,'owner_turn',owner)
    app=FastAPI();install_dashboard_model_providers(app,access=DashboardAccess(),runtime=SimpleNamespace())
    async def body(request,*args):
        allowed[0]=False
        return {'id':identifier,'allow_provider_request':True}
    monkeypatch.setattr('eva.adapters.api.dashboard_model_providers.read_small_json_request',body)
    with TestClient(app) as client:
        assert client.post('/internal/v1/dashboard/model-providers/check',json={}).status_code==403
    assert not seen
    # Also check the lower adapter admission, after API validation but before SDK dispatch.
    def revoked(): raise RuntimeError('revoked before dispatch')
    with pytest.raises(RuntimeError):control.check(identifier,admission_check=revoked)
    assert not seen and control.status()['connections'][0]['check_state']=='unchecked'
