from types import SimpleNamespace
from uuid import uuid4
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.voice_turns import install_voice_turns
from eva.application.conversation import GroundedReply, ConversationActionCall
from eva.application.voice_context import current_voice_context, STOP_VOICE_WORK

TOKEN = "v" * 40
PATH = "/internal/v1/voice-session/respond"

def setup():
    class Config:
        valid = True
        def issue_session(self):
            return SimpleNamespace(tenant_id="tenant:001", installation_id="installation:001", principal_id="person:owner")
        def check(self, grant):
            if not self.valid: raise ValueError("revoked")
    class Runtime:
        turns = []
        stops = []
        def respond(self, turn):
            self.turns.append(turn)
            self.context = current_voice_context()
            if self.context.active_work:
                self.context.stop_work(ConversationActionCall("call", STOP_VOICE_WORK, {"message_id": self.context.active_work[0][0]}))
            return GroundedReply(turn.conversation_id, turn.message_id, "In orde.", (), turn.occurred_at, "test")
        def after_response(self, turn, reply): pass
        def request_turn_stop(self, turn, **kwargs): self.stops.append(turn)
    app, config, runtime = FastAPI(), Config(), Runtime()
    install_voice_turns(app, access=DashboardAccess(TOKEN), runtime=runtime, config=config)
    return TestClient(app), config, runtime

def payload():
    return {"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Stop het onderzoek",
            "recent": [{"role": "assistant", "text": "Alleen dit is afgespeeld.", "occurred_at": "2026-09-10T10:00:00Z", "interrupted": True}],
            "active_work": [{"message_id": str(uuid4()), "text": "Onderzoek"}]}

def test_authority_is_server_bound_and_stop_is_same_session():
    client, config, runtime = setup()
    data = payload()
    response = client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=data)
    assert response.status_code == 200
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [row["type"] for row in rows] == ["task_stop_requested", "final"]
    assert runtime.turns[-1].principal_id == "person:owner"
    assert runtime.context.recent[0].text == "Alleen dit is afgespeeld."
    assert runtime.context.recent[0].playback_status == "interrupted"
    assert runtime.stops[-1].conversation_id == runtime.turns[-1].conversation_id
    assert runtime.stops[-1].message_id.endswith(data["active_work"][0]["message_id"])
    assert current_voice_context() is None

def test_credentials_revocation_and_client_identity_rejected():
    client, config, runtime = setup()
    assert client.post(PATH, json=payload()).status_code == 401
    data = {**payload(), "principal_id": "person:other"}
    assert client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=data).status_code == 422
    config.valid = False
    assert client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=payload()).status_code == 403


def test_revocation_during_turn_discards_late_text_and_final():
    from eva.application.conversation import publish_conversation_text
    client, config, runtime = setup()
    original = runtime.respond
    def revoke_during_response(turn):
        result = original(turn)
        config.valid = False
        publish_conversation_text("Dit mag niet uitlekken.", tools_disabled=True)
        return result
    runtime.respond = revoke_during_response
    data = payload()
    data["active_work"] = []
    response = client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=data)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert rows == [{"type": "error", "code": "voice_authority_changed"}]
    assert "uitlekken" not in response.text and "In orde" not in response.text
