from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_work import install_dashboard_work


def test_work_controls_delegate_and_reject_browser_scope():
    calls = []
    class Runtime:
        def dashboard_delegated_work(self, operation, work_id):
            calls.append((operation, work_id))
            return {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                    "work": {"work_id": work_id, "state": "stopped"}}
    app = FastAPI()
    install_dashboard_work(app, access=DashboardAccess(), runtime=Runtime())
    client = TestClient(app)
    url = "/internal/v1/dashboard/work"
    assert client.get(url).status_code == 200
    for operation in ("get", "result", "stop", "resume"):
        response = client.post(url + "/" + operation, json={"work_id": "work:owned"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert calls == [("list", None)] + [(op, "work:owned") for op in ("get", "result", "stop", "resume")]
    calls.clear()
    assert client.get(url + "?principal_id=other").status_code == 422
    for payload in ({"work_id": "owned", "owner": "other"}, {"work_id": []}, {}, {"work_id": ""}):
        assert client.post(url + "/stop", json=payload).status_code == 422
    assert client.post(url + "/start", json={"work_id": "owned"}).status_code == 422
    assert client.post(url + "/stop?tenant_id=other", json={"work_id": "owned"}).status_code == 422
    assert not calls


def test_access_denial_and_failure_expose_no_detail():
    class Denied:
        def require(self, request):
            raise HTTPException(403, "owner required")
    class Runtime:
        def dashboard_delegated_work(self, operation, work_id):
            raise RuntimeError("private diagnostic must not escape")
    app = FastAPI()
    install_dashboard_work(app, access=Denied(), runtime=Runtime())
    client = TestClient(app)
    assert client.get("/internal/v1/dashboard/work").status_code == 403
    assert client.post("/internal/v1/dashboard/work/stop", json={"work_id": "x"}).status_code == 403
    app = FastAPI()
    install_dashboard_work(app, access=DashboardAccess(), runtime=Runtime())
    response = TestClient(app).get("/internal/v1/dashboard/work")
    assert response.status_code == 503 and "private diagnostic" not in response.text


def test_private_return_controls_reject_scope_and_preserve_claim_identity():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_work import install_dashboard_work
    calls = []
    class Runtime:
        def dashboard_delegated_work(self, operation, **identity):
            calls.append((operation, identity))
            return {'contract': 'EvaPrivateWorkReturn/v1', 'status': 'acknowledged'}
    app = FastAPI(); install_dashboard_work(app, access=DashboardAccess('x'*40), runtime=Runtime())
    client = TestClient(app); path='/internal/v1/dashboard/work/return/'
    headers={'Authorization':'Bearer '+'x'*40}
    assert client.post(path+'poll', json={}).status_code == 401
    assert client.post(path+'poll', headers=headers, json={'owner':'other'}).status_code == 422
    identity={'work_id':'work:1', 'operation_id':'a'*64}
    assert client.post(path+'ack', headers=headers, json=identity).status_code == 200
    assert calls == [('return_ack', identity)]


def test_history_is_read_only_server_bound_and_not_cacheable():
    calls = []
    class Runtime:
        def dashboard_delegated_work(self, operation, **payload):
            calls.append((operation, payload))
            return {"contract": "EvaPrivateWorkReturn/v1", "status": "history", "results": []}
    app = FastAPI()
    install_dashboard_work(app, access=DashboardAccess(), runtime=Runtime())
    client = TestClient(app)
    path = "/internal/v1/dashboard/work/return/"
    response = client.post(path + "history", json={})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls == [("return_history", {})]
    for url, payload in [(path + "history", {"owner": "other"}),
                         (path + "history?owner=other", {}), (path + "replay", {})]:
        assert client.post(url, json=payload).status_code == 422
    assert len(calls) == 1
