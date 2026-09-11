from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from eva.application.conversation import ConversationConflict
from eva.application.people_memory_overview import MemorySourcePage

SOURCE = str(uuid4())
URL = f"/internal/v1/dashboard/memory-sources/{SOURCE}"
HEADERS = {"authorization": "Bearer " + "t" * 32}

class Runtime:
    def __init__(self): self.calls = []
    def dashboard_memory_sources(self, **kwargs): return MemorySourcePage(())
    def dashboard_memory_source(self, source_id):
        self.calls.append(("get", source_id))
        return {"contract": "EvaOwnerMemorySource/v1", "source_id": source_id,
                "content": "eigen bron", "occurred_at": "2026-09-11T10:00:00Z",
                "retention_state": "retained", "origin": "Telegram"}
    def dashboard_forget_memory_source(self, source_id):
        self.calls.append(("forget", source_id))
        return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id, "state": "erased"}
    def dashboard_correct_memory_source(self, source_id, text):
        self.calls.append(("correct", source_id, text))
        return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id, "state": "correction_queued"}

def test_owner_memory_controls_require_auth_and_server_bound_scope():
    runtime = Runtime()
    client = TestClient(create_app(runtime, dashboard_access_token="t" * 32))
    assert client.get(URL).status_code == 401
    for action, payload in [("forget", {"confirm": True}), ("correct", {"text": "groen"})]:
        assert client.post(URL + "/" + action, json=payload).status_code == 401
        assert client.post(URL + "/" + action + "?principal_id=other", headers=HEADERS, json=payload).status_code == 422
        assert client.post(URL + "/" + action, headers={**HEADERS, "origin": "https://other.invalid"}, json=payload).status_code == 403
    for payload in ({}, {"confirm": False}, {"confirm": 1}, {"confirm": True, "principal_id": "other"}):
        assert client.post(URL + "/forget", headers=HEADERS, json=payload).status_code == 422
    assert runtime.calls == []
    for action, payload in [("forget", {"confirm": True}), ("correct", {"text": "Mijn favoriete kleur is groen."})]:
        response = client.post(URL + "/" + action, json=payload, headers=HEADERS)
        assert response.status_code == (202 if action == "correct" else 200)
        assert response.headers["cache-control"] == "no-store"
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200 and response.json()["content"] == "eigen bron"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/internal/v1/dashboard/memory-sources", headers=HEADERS).status_code == 200

@pytest.mark.parametrize("error,status", [(LookupError("private detail"),404),
    (RuntimeError("private detail"),503), (ValueError("private detail"),422)])
def test_memory_control_failures_never_claim_success_or_leak_provider_details(error, status):
    class Failing(Runtime):
        def dashboard_forget_memory_source(self, source_id): raise error
    client = TestClient(create_app(Failing(), dashboard_access_token="t" * 32))
    response = client.post(URL + "/forget", json={"confirm": True}, headers=HEADERS)
    assert response.status_code == status
    assert "private detail" not in response.text
    assert response.headers["cache-control"] == "no-store"
