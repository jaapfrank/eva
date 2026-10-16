from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_work import install_dashboard_work


def test_context_reads_use_existing_access_and_reject_client_scope(monkeypatch):
    from eva.bootstrap import dashboard_work_context as context
    calls=[];runtime=object()
    monkeypatch.setattr(context,'conversation_jobs',lambda r:calls.append((r,'conversation')) or {'contract':'EvaConversationWork/v1','jobs':[]})
    monkeypatch.setattr(context,'work_context',lambda r,w:calls.append((r,w)) or {'contract':'EvaOwnerWorkContext/v1','work_id':w,'origin':{'state':'unavailable'}})
    app=FastAPI();install_dashboard_work(app,access=DashboardAccess(),runtime=runtime);client=TestClient(app)
    conv='/internal/v1/dashboard/conversation/work';work='/internal/v1/dashboard/work/context'
    assert client.get(conv).status_code==200
    assert client.post(work,json={'work_id':'work:mine'}).headers['cache-control']=='no-store'
    assert calls==[(runtime,'conversation'),(runtime,'work:mine')]
    calls.clear()
    assert client.get(conv+'?principal_id=other').status_code==422
    assert client.post(work,json={'work_id':'work:mine','owner':'other'}).status_code==422
    assert client.post(work,json={'work_id':'work:mine'},headers={'origin':'https://other.invalid'}).status_code==403
    assert not calls


def test_context_denial_and_errors_do_not_publish_private_diagnostics(monkeypatch):
    from eva.bootstrap import dashboard_work_context as context
    class Denied:
        def require(self,request):raise HTTPException(403,'owner required')
    app=FastAPI();install_dashboard_work(app,access=Denied(),runtime=object());client=TestClient(app)
    assert client.get('/internal/v1/dashboard/conversation/work').status_code==403
    assert client.post('/internal/v1/dashboard/work/context',json={'work_id':'work:mine'}).status_code==403
    def fail(*args):raise RuntimeError('private diagnostic')
    monkeypatch.setattr(context,'work_context',fail)
    app=FastAPI();install_dashboard_work(app,access=DashboardAccess(),runtime=object());client=TestClient(app)
    response=client.post('/internal/v1/dashboard/work/context',json={'work_id':'work:mine'})
    assert response.status_code==503 and 'private diagnostic' not in response.text
