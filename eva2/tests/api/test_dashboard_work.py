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
