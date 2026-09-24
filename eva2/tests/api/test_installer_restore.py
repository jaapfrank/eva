from base64 import b64encode
from fastapi.testclient import TestClient
from eva.adapters.api.installer_wizard import (
    InstallerBootstrap,
    create_installer_wizard,
)
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from tests.api.test_installer_wizard import _receipt


class Restore:
    def __init__(self):
        self.calls = []

    def prepare(self, archive, passphrase):
        self.calls.append((archive, passphrase))
        return {"confirmation": "test-token", "summary": {"records": 3}}

    def execute(self, confirmation):
        self.calls.append(confirmation)
        if confirmation != "test-token":
            raise ValueError("PRIVATE diagnostic")
        return {"status": "restored"}


def client(workflow):
    bootstrap = InstallerBootstrap.create()
    app = create_installer_wizard(
        _receipt(status="ready"),
        bootstrap,
        identity=load_current_identity_pack(),
        restore_workflow=workflow,
    )
    c = TestClient(app, headers={"host": "127.0.0.1"}, client=("127.0.0.1", 40000))
    return c, bootstrap


def login(c, b):
    return c.post("/internal/v1/bootstrap", json={"token": b.token})


def test_restore_requires_session_and_same_origin():
    w = Restore()
    c, b = client(w)
    route = "/internal/v1/onboarding/restore/prepare"
    data = {"archive": b64encode(b"archive").decode(), "passphrase": "secret"}
    assert c.post(route, json=data).status_code == 401
    assert login(c, b).status_code == 204
    assert (
        c.post(route, json=data, headers={"origin": "https://evil.invalid"}).status_code
        == 403
    )
    assert not w.calls
    assert c.post(route, json=data).status_code == 200
    assert w.calls == [(b"archive", "secret")]
    assert (
        c.post(
            "/internal/v1/onboarding/restore/execute",
            json={"confirmation": "test-token"},
        ).json()["status"]
        == "restored"
    )


def test_restore_rejects_bad_shape_and_does_not_leak_diagnostic():
    w = Restore()
    c, b = client(w)
    login(c, b)
    assert (
        c.post(
            "/internal/v1/onboarding/restore/prepare",
            json={"archive": "%%%", "passphrase": "x"},
        ).status_code
        == 409
    )
    response = c.post(
        "/internal/v1/onboarding/restore/execute", json={"confirmation": "wrong"}
    )
    assert response.status_code == 409 and "PRIVATE" not in response.text
    assert "Reservekopie herstellen" in c.get("/").text


def test_restore_rejects_target_override_and_oversized_input():
    w = Restore()
    c, b = client(w)
    login(c, b)
    route = '/internal/v1/onboarding/restore/prepare'
    assert c.post(route, json={'archive':'YQ==','passphrase':'x','dsn':'other-target'}).status_code == 409
    assert c.post(route, content=b'{}', headers={'content-length':'100000000'}).status_code == 413
    assert not w.calls


def test_partial_gateway_start_reports_retained_target_without_private_receipt():
    from fastapi import FastAPI
    from eva.adapters.api.installer_restore_api import register_installer_restore_routes
    from eva.application.installer_restore import InactiveRecoveryMaterialRetained

    class FailedGateway:
        def execute(self, confirmation):
            raise InactiveRecoveryMaterialRetained(
                "/PRIVATE/target", gateway_start_attempted=True,
                gateway_connection={"private": "PRIVATE_TOKEN"})

    app = FastAPI()
    register_installer_restore_routes(app, workflow=FailedGateway(),
                                     require_write=lambda request: None, security_headers={})
    response = TestClient(app).post("/internal/v1/onboarding/restore/execute",
                                   json={"confirmation": "fixture"})
    assert response.status_code == 409
    assert response.json()["recovery_state"] == "materialized_inactive"
    assert "gateway en documentproviders kunnen al gestart" in response.json()["detail"]
    assert "start niet opnieuw" in response.json()["detail"]
    assert "PRIVATE" not in response.text
