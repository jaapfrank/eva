from fastapi import FastAPI
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_initiatives import install_dashboard_initiatives


def test_owner_routes_reject_browser_scope_and_return_exact_artifact():
    calls = []
    class Runtime:
        def dashboard_initiatives(self):
            calls.append("read")
            return {"contract": "EvaOwnerInitiatives/v1", "cards": [], "truncated": False}
        def dashboard_stop_initiative(self, intent):
            calls.append(intent)
            return {"contract": "EvaOwnerInitiativeStop/v1", "intent_id": intent, "status": "stopped"}
        def dashboard_initiative_artifact(self, intent):
            return {"filename": "gezin.md", "content": "# Gezinsplan", "sha256": "a"*64}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t"*32), runtime=Runtime())
    client = TestClient(app)
    url = "/internal/v1/dashboard/initiatives"
    auth = {"authorization": "Bearer " + "t"*32}
    assert client.get(url).status_code == 401
    assert client.post(url + "/stop", json={"intent_id": "test"}).status_code == 401
    assert client.get(url + "?principal_id=other", headers=auth).status_code == 422
    assert client.post(url + "/stop", headers=auth, json={"intent_id": "test", "owner": "other"}).status_code == 422
    assert calls == []
    assert client.get(url, headers=auth).json()["cards"] == []
    result = client.post(url + "/stop", headers=auth, json={"intent_id": "test"})
    assert result.json()["status"] == "stopped"
    assert result.headers["cache-control"] == "no-store"
    artifact = client.get(url + "/test/artifact", headers=auth)
    assert artifact.text == "# Gezinsplan"
    assert artifact.headers["content-disposition"] == "attachment; filename*=UTF-8''gezin.md"
