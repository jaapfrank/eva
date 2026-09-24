"""Preparation uses fresh server choices and does not repeat uncertain effects."""
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from eva.adapters.api.installer_installation_plan_api import register_installer_installation_plan_routes


def client_for(prepare, plan=None, stored_status="available"):
    plan = plan or {"plan_sha256": "a" * 64, "status": "ready_for_release_preparation", "blockers": []}
    app = FastAPI()
    def write(request):
        if request.headers.get("x-owner") != "yes":
            raise HTTPException(403)
    register_installer_installation_plan_routes(app, build_plan=lambda: dict(plan),
        require_read=lambda request: None, require_write=write,
        prepare_target=prepare, read_preparation_status=lambda: stored_status, security_headers={"Cache-Control": "no-store"})
    return TestClient(app), plan


def post(client, **values):
    return client.post("/internal/v1/onboarding/install/prepare", headers={"x-owner": "yes"},
        json=values or {"plan_sha256": "a" * 64})


def test_unavailable_backend_is_visible_and_never_claims_installation():
    client, _ = client_for(None)
    assert client.get("/internal/v1/onboarding/install/preparation").json()["status"] == "unavailable"
    assert post(client).status_code == 409


def test_current_plan_only_and_owner_write_required():
    calls = []
    client, plan = client_for(calls.append)
    assert client.post("/internal/v1/onboarding/install/prepare", json={"plan_sha256": "a" * 64}).status_code == 403
    plan["plan_sha256"] = "b" * 64
    assert post(client).status_code == 409
    assert not calls
    assert post(client, plan_sha256="b" * 64).json()["status"] == "prepared"
    assert calls == [plan]
    assert post(client, plan_sha256="b" * 64).status_code == 409
    assert len(calls) == 1


def test_blockers_and_arbitrary_browser_configuration_are_rejected():
    calls = []
    client, plan = client_for(calls.append)
    assert post(client, plan_sha256="a" * 64, dsn="postgresql://foreign").status_code == 400
    plan["blockers"] = [{"code": "secret_missing"}]
    assert post(client).status_code == 409
    assert not calls


def test_partial_failure_retains_uncertain_state_and_sanitizes_error():
    calls = []
    def prepare(plan):
        calls.append(plan)
        raise RuntimeError("private credential must not leak")
    client, _ = client_for(prepare)
    response = post(client)
    assert response.status_code == 409
    assert response.json()["status"] == "needs_inspection"
    assert "credential" not in response.text
    assert post(client).status_code == 409
    assert len(calls) == 1


def test_second_request_does_not_start_while_preparation_runs():
    entered, finish = Event(), Event()
    def prepare(plan):
        entered.set()
        assert finish.wait(5)
    client, _ = client_for(prepare)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(post, client)
        try:
            assert entered.wait(5)
            assert client.get("/internal/v1/onboarding/install/preparation").json()["status"] == "running"
            assert post(client).status_code == 409
        finally:
            finish.set()
        assert future.result().json()["status"] == "prepared"


def test_restart_reads_retained_preparation_before_enabling_actions():
    calls = []
    for status in ("prepared", "needs_inspection", "running"):
        client, _ = client_for(calls.append, stored_status=status)
        assert client.get("/internal/v1/onboarding/install/preparation").json()["status"] == status
        assert post(client).status_code == 409
    assert not calls
