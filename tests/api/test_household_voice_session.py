from types import SimpleNamespace
from uuid import UUID
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from eva.adapters.api.household_voice_session import install_household_voice_session
from eva.application.memory_audience import HouseholdConsentReference
from eva.application.conversation import GroundedReply, publish_conversation_text
from eva.application.voice_context import current_voice_context
from test_voice_turns import payload

PATH = "/internal/v1/ha-voice/session"
TOKEN = "h" * 40
DEVICE = {"satellite_id": "assist_satellite.room", "device_id": "a" * 32}
HEADERS = {"Authorization": "Bearer " + TOKEN}


def setup():
    class Authority:
        token = TOKEN
        valid = True
        policy = SimpleNamespace(tenant_id="tenant", installation_id="installation",
            household_id="home", devices={DEVICE["satellite_id"]: DEVICE["device_id"]})
        def validate_current(self):
            if not self.valid: raise ValueError("revoked")
        def active_reference(self, *, principal_id, household_id, now):
            self.validate_current()
            return HouseholdConsentReference(principal_id, household_id, "household-consent:1")
    class Runtime:
        def __init__(self): self.turns=[]; self.retained=[]; self.stops=[]
        def admit_turn_audience(self, turn):
            assert turn.audience.kind == "household"
            assert turn.principal_id == "household-device:" + DEVICE["device_id"]
        def respond(self, turn):
            self.turns.append(turn)
            self.context = current_voice_context()
            if self.context.active_work: self.context.stop(self.context.active_work[0][0])
            return GroundedReply(turn.conversation_id, turn.message_id, "Huisantwoord", (), turn.occurred_at, "test")
        def after_response(self, turn, reply): self.retained.append(turn)
        def request_turn_stop(self, turn, **kwargs): self.stops.append(turn)
    app, runtime, authority = FastAPI(), Runtime(), Authority()
    install_household_voice_session(app, runtime=runtime, authority=authority)
    return TestClient(app), runtime, authority


def test_household_stream_keeps_device_audience_and_stop_namespace():
    client, runtime, authority = setup()
    data = {**payload(), **DEVICE}
    result = client.post(PATH + "/respond", headers=HEADERS, json=data)
    assert result.status_code == 200
    assert json.loads(result.text.splitlines()[-1])["type"] == "final"
    turn = runtime.turns[0]
    assert turn.conversation_id.startswith("household:home:conversation:")
    assert runtime.stops[0].conversation_id == turn.conversation_id
    assert runtime.stops[0].message_id.endswith(UUID(data["active_work"][0]["message_id"]).hex)
    assert client.post(PATH + "/ready", headers=HEADERS, json=DEVICE).status_code == 200


def test_household_denies_owner_key_device_swap_and_private_identity():
    client, runtime, authority = setup()
    data = {**payload(), **DEVICE}
    assert client.post(PATH+"/respond", json=data).status_code == 401
    assert client.post(PATH+"/respond", headers={"Authorization":"Bearer "+"o"*40}, json=data).status_code == 401
    assert client.post(PATH+"/respond", headers=HEADERS, json={**data,"device_id":"b"*32}).status_code == 403
    for key in ("principal_id", "audience", "conversation_id"):
        assert client.post(PATH+"/respond", headers=HEADERS, json={**data,key:"private:owner"}).status_code == 422
    authority.valid = False
    assert client.post(PATH+"/ready", headers=HEADERS, json=DEVICE).status_code == 403
    assert runtime.turns == []


def test_revocation_discards_stream_and_retention_and_cross_bound_reply():
    client, runtime, authority = setup()
    original = runtime.respond
    def revoked(turn):
        reply = original(turn)
        authority.valid = False
        publish_conversation_text("Private", tools_disabled=True)
        return reply
    runtime.respond = revoked
    data = {**payload(), **DEVICE, "active_work": []}
    result = client.post(PATH+"/respond", headers=HEADERS, json=data)
    assert [json.loads(line) for line in result.text.splitlines()] == [{"type":"error","code":"voice_authority_changed"}]
    assert runtime.retained == []
    authority.valid = True
    def wrong(turn):
        return GroundedReply("private:owner", turn.message_id, "Private", (), turn.occurred_at, "test")
    runtime.respond = wrong
    result = client.post(PATH+"/respond", headers=HEADERS, json=data)
    assert "Private" not in result.text and "voice_turn_failed" in result.text
    assert runtime.retained == []


def test_household_work_transport_stays_disabled_without_explicit_composition():
    client, runtime, authority = setup()
    assert client.post(PATH + "/ready", headers=HEADERS, json=DEVICE).json()["household_work_returns"] is False
    assert client.post(PATH + "/work/open", headers=HEADERS, json=DEVICE).status_code == 503
    assert client.post(PATH + "/work/open", json=DEVICE).status_code == 401
    assert client.post(PATH + "/work/open", headers=HEADERS, json={**DEVICE, "device_id": "b"*32}).status_code == 403
    assert runtime.turns == []
