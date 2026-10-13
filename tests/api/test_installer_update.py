from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from eva.adapters.api.installer_update_api import register_installer_update_routes


class Update:
    calls = 0
    def status(self):
        return {"status": "ready" if not self.calls else "running_with_retained_mounts",
                "can_apply": not self.calls, "label": "Prepared test version"}
    def apply(self):
        self.calls += 1
        return self.status()


def test_update_requires_existing_session_and_rejects_browser_paths():
    app = FastAPI()
    workflow = Update()
    def session(request):
        if request.headers.get("x-test-owner") != "yes":
            raise HTTPException(401)
    register_installer_update_routes(app, workflow=workflow, require_read=session,
                                     require_write=session, security_headers={})
    with TestClient(app) as client:
        assert client.post("/installer/update", json={}).status_code == 401
        client.headers["x-test-owner"] = "yes"
        assert client.get("/installer/update").json()["can_apply"]
        assert client.post("/installer/update", json={"candidate": "/other"}).status_code == 400
        assert workflow.calls == 0
        assert client.post("/installer/update", json={}).json()["status"] == "running_with_retained_mounts"
        assert client.post("/installer/update", json={}).status_code == 409
        assert workflow.calls == 1


def test_update_without_prepared_package_is_not_available():
    app = FastAPI()
    register_installer_update_routes(app, workflow=None, require_read=lambda r: None,
                                     require_write=lambda r: None, security_headers={})
    with TestClient(app) as client:
        assert not client.get("/installer/update").json()["can_apply"]
        assert client.post("/installer/update", json={}).status_code == 409


def test_update_is_in_existing_owner_wizard_with_existing_session():
    from eva.adapters.api.installer_wizard import InstallerBootstrap, create_installer_wizard
    from eva.adapters.objects.identity_verifier import load_current_identity_pack
    from tests.api.test_installer_wizard import _receipt
    bootstrap = InstallerBootstrap.create()
    workflow = Update()
    app = create_installer_wizard(_receipt(), bootstrap, identity=load_current_identity_pack(),
                                  update_workflow=workflow)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post("/internal/v1/bootstrap", json={"token": bootstrap.token})
        assert response.status_code == 204
        page = client.get("/")
        assert "Eva bijwerken" in page.text
        assert client.get("/installer/update").json()["can_apply"]
        assert client.post("/installer/update", json={}, headers={"origin": "http://other.invalid"}).status_code == 403
        assert workflow.calls == 0
        assert client.post("/installer/update", json={}).status_code == 200
