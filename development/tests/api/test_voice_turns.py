from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
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
        def admit_voice_turn(self, turn): return turn
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


@pytest.mark.parametrize("stream_deltas", [True, False])
def test_revocation_during_turn_discards_late_text_and_final(stream_deltas):
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
    data["stream_deltas"] = stream_deltas
    response = client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=data)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert rows == [{"type": "error", "code": "voice_authority_changed"}]
    assert "uitlekken" not in response.text and "In orde" not in response.text


@pytest.mark.parametrize("stream_deltas", [None, False])
def test_literal_client_can_omit_provisional_text_without_losing_spoken_updates(stream_deltas, monkeypatch):
    from eva.application.conversation import publish_conversation_text
    from eva.application.voice_context import SPEAK_AND_CONTINUE
    from eva.adapters.api import voice_turns
    original_stream = voice_turns.stream_conversation_text
    streams = []
    def track_stream(callback):
        streams.append(callback)
        return original_stream(callback)
    monkeypatch.setattr(voice_turns, "stream_conversation_text", track_stream)
    client, _, runtime = setup()
    original = runtime.respond
    def respond(turn):
        result = original(turn)
        publish_conversation_text("Voorlopige woorden.", tools_disabled=True)
        current_voice_context().speak_and_continue(ConversationActionCall(
            "thinking", SPEAK_AND_CONTINUE, {"text": "Even denken…"}))
        return result
    runtime.respond = respond
    data = payload()
    data["active_work"] = []
    if stream_deltas is not None:
        data["stream_deltas"] = stream_deltas
    response = client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=data)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [row["type"] for row in rows] == (
        (["delta"] if stream_deltas is None else []) + ["spoken_update", "final"])
    assert len(streams) == (1 if stream_deltas is None else 0)
    assert rows[-2]["text"] == "Even denken…"
    assert rows[-1]["reply"]["text"] == "In orde."


def test_canonical_admitted_turn_reaches_stop_and_after_response():
    from dataclasses import replace
    from threading import Event
    client, _, runtime = setup()
    finished = Event()
    stamp = "2026-09-01T01:02:03Z"
    runtime.admit_voice_turn = lambda turn: replace(turn, occurred_at=stamp)
    retained = []
    def after(turn, reply):
        retained.append((turn, reply))
        finished.set()
    runtime.after_response = after
    response = client.post(PATH, headers={"Authorization": "Bearer " + TOKEN}, json=payload())
    assert json.loads(response.text.splitlines()[-1])["type"] == "final"
    assert finished.wait(3)
    assert runtime.turns[-1].occurred_at == runtime.stops[-1].occurred_at == stamp
    assert retained[0][0].occurred_at == retained[0][1].generated_at == stamp


def test_private_return_http_binds_owner_session_and_rechecks_after_read():
    client, config, runtime = setup()
    calls = []
    def read(turn, operation, **identity):
        calls.append((turn, operation, identity))
        return {'status': 'idle', 'result': None}
    runtime.private_work_return = read
    session = str(uuid4()); path = '/internal/v1/voice-session/work/poll'
    headers = {'Authorization': 'Bearer '+TOKEN}
    assert client.post(path, json={'session_id': session}).status_code == 401
    response = client.post(path, headers=headers, json={'session_id': session})
    assert response.json() == {'contract': 'EvaPrivateWorkReturn/v1', 'status': 'idle', 'result': None}
    turn = calls[-1][0]
    assert turn.principal_id == 'person:owner' and turn.audience.kind == 'private_person'
    assert turn.conversation_id == 'voice:'+session
    assert client.post(path, headers=headers, json={'session_id': session, 'audience': 'household'}).status_code == 422
    assert client.post(path, headers=headers, json={'session_id': session, 'work_results': []}).status_code == 422
    def revoke(*args, **kwargs):
        config.valid = False
        return {'status': 'claimed', 'result': {'output': 'PRIVATE MUST NOT APPEAR'}}
    runtime.private_work_return = revoke
    denied = client.post(path, headers=headers, json={'session_id': session})
    assert denied.status_code == 403 and 'PRIVATE' not in denied.text
