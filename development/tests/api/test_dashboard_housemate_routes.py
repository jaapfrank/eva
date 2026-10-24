from fastapi import FastAPI
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_initiatives import install_dashboard_initiatives
from eva.bootstrap import dashboard_housemate


def test_overview_uses_owner_access_and_never_browser_supplied_scope(monkeypatch):
    calls = []
    def project(runtime):
        calls.append(runtime)
        return {"contract": "EvaOwnerHousemateState/v1", "current_learning": []}
    monkeypatch.setattr(dashboard_housemate, "housemate_state", project)
    app = FastAPI(); runtime = object()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=runtime)
    client = TestClient(app); url = "/internal/v1/dashboard/initiatives/state"
    headers = {"authorization": "Bearer " + "t" * 32}
    assert client.get(url).status_code == 401
    assert client.get(url + "?principal_id=someone", headers=headers).status_code == 422
    assert client.get(url + "?source=other", headers=headers).status_code == 422
    assert calls == []
    response = client.get(url, headers=headers)
    assert response.status_code == 200 and response.json()["current_learning"] == []
    assert calls == [runtime] and response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_overview_does_not_leak_partial_private_data_on_source_failure(monkeypatch):
    def fail(runtime): raise RuntimeError("private source text must never appear in error")
    monkeypatch.setattr(dashboard_housemate, "housemate_state", fail)
    app = FastAPI(); install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=object())
    response = TestClient(app).get("/internal/v1/dashboard/initiatives/state", headers={"authorization": "Bearer " + "t" * 32})
    assert response.status_code == 503 and "private source text" not in response.text


# Reuse the actual typed-source/HousemateDevelopment composition from the focused
# source test instead of replacing the API response with handwritten JSON.
from tests.bootstrap.test_dashboard_housemate import rig, development_rig


def test_actual_typed_development_response_survives_existing_get_contract(development_rig):
    import json
    import os
    from pathlib import Path
    runtime=development_rig.rig.runtime
    app=FastAPI();install_dashboard_initiatives(app,access=DashboardAccess("t"*32),runtime=runtime)
    response=TestClient(app).get("/internal/v1/dashboard/initiatives/state",headers={"authorization":"Bearer "+"t"*32})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    payload=response.json()
    assert payload["current_learning"] == [] and payload["personality_deltas"][0]["kind"] == "personality_delta"
    assert payload["personality_deltas"][0]["processing_state"] == "processed"
    assert payload["inner_state"]["open_threads"]
    output=os.environ.get("EVA_E29_CONTRACT_OUTPUT")
    if output:
        path=Path(output);path.write_text(json.dumps(payload));path.chmod(0o600)
