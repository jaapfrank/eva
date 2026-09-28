from types import SimpleNamespace

from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app

TOKEN = "t" * 32
URL = "/internal/v1/dashboard/components"
HEADERS = {"authorization": "Bearer " + TOKEN}

class Runtime:
    def health(self):
        return SimpleNamespace(database="ready", memory_projection="unavailable",
                               model_provider="credentials_expired", detail="SECRET credential")


def test_components_require_owner_access_and_do_not_overclaim_channels():
    client = TestClient(create_app(Runtime(), dashboard_access_token=TOKEN))
    assert client.get(URL).status_code == 401
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "SECRET" not in response.text
    data = response.json()
    rows = {row["id"]: row for row in data["components"]}
    assert rows["database"]["state"] == "ready"
    assert rows["memory"]["state"] == rows["model_credentials"]["state"] == "attention"
    assert rows["database"]["observed_at"] == data["checked_at"]
    for key in ("telegram", "home_assistant", "voice_pe", "worker"):
        assert rows[key]["state"] == "unknown"
        assert rows[key]["observed_at"] is None
    assert rows["web_voice"]["state"] == "not_configured"


def test_failed_health_read_is_unknown_and_does_not_leak_exception():
    class Broken(Runtime):
        def health(self):
            raise RuntimeError("SECRET provider URL")
    response = TestClient(create_app(Broken(), dashboard_access_token=TOKEN)).get(URL, headers=HEADERS)
    assert response.status_code == 200
    assert "SECRET" not in response.text
    rows = {row["id"]: row for row in response.json()["components"]}
    assert all(rows[key]["state"] == "unknown" for key in ("database", "memory", "model_credentials"))
    assert rows["website"]["state"] == "ready"


def test_ready_runtime_does_not_prove_a_model_credential_check():
    class Ready(Runtime):
        def health(self):
            return SimpleNamespace(database="ready", memory_projection="ready", model_provider="ready")
    data = TestClient(create_app(Ready(), dashboard_access_token=TOKEN)).get(URL, headers=HEADERS).json()
    row = next(row for row in data["components"] if row["id"] == "model_credentials")
    assert row["state"] == "unknown"
    assert row["observed_at"] is None
