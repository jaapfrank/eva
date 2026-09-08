from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_voice import DashboardVoice, install_dashboard_voice
from eva.adapters.connectors.home_assistant_voice import SpeechAudioChunk
from eva.application.conversation import GroundedReply
from eva.contracts.voice import VoiceSession


TOKEN = "v" * 40
ORIGIN = "http://localhost:44711"
PATH = "/internal/v1/dashboard/conversation/voice"
HEADERS = {"authorization": f"Bearer {TOKEN}", "origin": ORIGIN}


def session():
    return VoiceSession.model_validate({
        "contract": "VoiceSession/v1", "schema_version": 1,
        "session_id": "voice:issued", "tenant_id": "tenant:001",
        "installation_id": "installation:001", "principal_id": "person:owner",
        "conversation_id": "dashboard:owner", "audience": "private",
        "channel": "local_test", "device_binding_id": "device:owner",
        "identity_id": "identity:eva", "identity_pack_version": 1,
        "identity_pack_sha256": "a" * 64, "policy_id": "policy:voice",
        "policy_pack_version": 1, "policy_pack_sha256": "b" * 64,
        "privacy": {"contract": "VoicePrivacy/v1", "schema_version": 1,
                    "partial_transcript_retention": "ephemeral", "raw_audio_retention": "none",
                    "external_provider_audio_retention": "disabled",
                    "final_transcript_admission": "conversation_admission_required"},
        "created_at": "2026-09-08T10:00:00Z", "deadline": "2026-09-08T11:00:00Z",
    })


class Media:
    def __init__(self):
        self.frames = []
        self.before_final = lambda: None
        self.synthesized = []
        self.wait_audio = False
        self.closed = Event()

    async def transcribe(self, chunks):
        async for frame in chunks:
            self.frames.append(frame)
        self.before_final()
        return "Wat kun je voor mij doen?"

    async def synthesize(self, text):
        self.synthesized.append(text)
        try:
            yield SpeechAudioChunk(b"synthetic-audio", "audio/mpeg")
            if self.wait_audio:
                await asyncio.Event().wait()
        finally:
            self.closed.set()


class Runtime:
    def __init__(self):
        self.turns = []
        self.memories = []
        self.stops = 0
        self.started = Event()
        self.release = Event()
        self.release.set()

    def respond(self, turn):
        self.turns.append(turn)
        self.started.set()
        assert self.release.wait(5), "test did not release model"
        return GroundedReply(turn.conversation_id, turn.message_id, "Ik help je graag.",
                             (), turn.occurred_at, "test")

    def after_response(self, turn, reply):
        self.memories.append((turn, reply))

    def dashboard_stop_conversation(self):
        self.stops += 1


def setup(*, token=TOKEN, enabled=True):
    runtime, media = Runtime(), Media()
    current = [datetime(2026, 9, 8, 10, 30, tzinfo=UTC)]
    valid = [True]

    def validate(grant):
        if not valid[0] or grant.principal_id != "person:owner":
            raise ValueError("grant revoked")

    config = DashboardVoice(ORIGIN, session, validate, media, media,
                            clock=lambda: current[0])
    app = FastAPI()
    install_dashboard_voice(app, access=DashboardAccess(token), runtime=runtime,
                            config=config if enabled else None)
    return TestClient(app), runtime, media, current, valid


def speak(ws):
    ws.send_json({"type": "start"})
    assert ws.receive_json()["type"] == "listening"
    ws.send_bytes(b"\0\0" * 160)
    ws.send_json({"type": "end"})


@pytest.mark.parametrize("token,enabled,headers,path", [
    (None, True, HEADERS, PATH), (TOKEN, False, HEADERS, PATH),
    (TOKEN, True, {"origin": ORIGIN}, PATH),
    (TOKEN, True, {**HEADERS, "origin": "http://evil.test"}, PATH),
    (TOKEN, True, {"authorization": f"Bearer {TOKEN}"}, PATH),
    (TOKEN, True, HEADERS, PATH + "?token=anything"),
])
def test_reject_before_media(token, enabled, headers, path):
    client, runtime, media, _, _ = setup(token=token, enabled=enabled)
    with client, pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(path, headers=headers):
            pass
    assert error.value.code == 1008
    assert not runtime.turns and not media.frames


def test_final_audio_uses_same_private_turn_and_memory_callback():
    client, runtime, media, _, _ = setup()
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        assert ws.receive_json()["session_id"] != "voice:issued"
        speak(ws)
        reply, audio, end, finished = [ws.receive_json() for _ in range(4)]
        assert [x["type"] for x in (reply, audio, end, finished)] == [
            "reply", "audio", "audio_end", "turn_finished"]
        assert reply["message_id"] == runtime.turns[0].message_id
        assert runtime.turns[0].principal_id == "person:owner"
        assert runtime.turns[0].conversation_id == "dashboard:owner"
        assert runtime.memories[0][0] is runtime.turns[0]
        assert media.closed.is_set()
        speak(ws)
        for _ in range(4):
            assert ws.receive_json()["generation"] == 2
        assert runtime.turns[0].message_id != runtime.turns[1].message_id


@pytest.mark.parametrize("change", ["clock", "policy"])
def test_recheck_after_stt_before_model(change):
    client, runtime, media, current, valid = setup()

    def revoke():
        if change == "clock":
            current[0] = datetime(2026, 9, 8, 11, 1, tzinfo=UTC)
        else:
            valid[0] = False
    media.before_final = revoke
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        ws.receive_json()
        speak(ws)
        assert ws.receive_json() == {"type": "error", "generation": 1,
                                     "code": "voice_turn_failed"}
        assert ws.receive_json()["type"] == "turn_finished"
    assert not runtime.turns


def test_interrupt_during_model_suppresses_audio_without_cancelling_effects():
    client, runtime, media, _, _ = setup()
    runtime.release.clear()
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        ws.receive_json()
        speak(ws)
        assert runtime.started.wait(2)
        ws.send_json({"type": "interrupt"})
        assert ws.receive_json()["type"] == "clear_audio"
        runtime.release.set()
        assert ws.receive_json()["type"] == "turn_finished"
    assert runtime.stops == 0
    assert len(runtime.memories) == 1
    assert not media.synthesized


def test_interrupt_closes_stalled_tts_and_explicit_stop_reaches_runtime():
    client, runtime, media, _, _ = setup()
    media.wait_audio = True
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        ws.receive_json()
        speak(ws)
        assert ws.receive_json()["type"] == "reply"
        assert ws.receive_json()["type"] == "audio"
        ws.send_json({"type": "stop"})
        events = [ws.receive_json()["type"] for _ in range(3)]
        assert set(events) == {"clear_audio", "turn_finished", "stop_requested"}
        assert media.closed.wait(2)
    assert runtime.stops == 1


@pytest.mark.parametrize("payload", [
    {"type": "start", "principal_id": "person:someone"},
    {"type": "end"}, {"type": "final", "text": "injected"}, [], {"type": []},
])
def test_client_cannot_supply_authority_or_final_transcript(payload):
    client, runtime, _, _, _ = setup()
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        ws.receive_json()
        ws.send_json(payload)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert not runtime.turns


@pytest.mark.parametrize("frame", [b"", b"a", b"a" * 65538])
def test_reject_invalid_pcm(frame):
    client, runtime, _, _, _ = setup()
    with client, client.websocket_connect(PATH, headers=HEADERS) as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(frame)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert not runtime.turns


def test_disconnect_during_model_keeps_memory_callback_owned():
    client, runtime, media, _, _ = setup()
    runtime.release.clear()
    with client:
        with client.websocket_connect(PATH, headers=HEADERS) as ws:
            ws.receive_json()
            speak(ws)
            assert runtime.started.wait(2)
            ws.close()
            runtime.release.set()
    assert len(runtime.turns) == len(runtime.memories) == 1
    assert not media.synthesized


@pytest.mark.parametrize("intervention", ["interrupt", "revoke"])
def test_no_new_tts_after_interruption_or_revocation_during_reply_send(intervention):
    from eva.adapters.api.dashboard_voice import _Connection

    async def scenario():
        reply_waiting, release_reply = asyncio.Event(), asyncio.Event()
        valid = [True]

        class Socket:
            async def send_json(self, payload):
                if payload["type"] == "reply":
                    reply_waiting.set()
                    await release_reply.wait()

        def validate(grant):
            if not valid[0]:
                raise ValueError("revoked")

        media, runtime = Media(), Runtime()
        config = DashboardVoice(ORIGIN, session, validate, media, media,
                                clock=lambda: datetime(2026, 9, 8, 10, 30, tzinfo=UTC))
        connection = _Connection(Socket(), config, session(), runtime)
        connection.generation = 1
        queue = asyncio.Queue()
        await queue.put(b"\0\0")
        await queue.put(None)
        processing = asyncio.create_task(connection.process(queue))
        await asyncio.wait_for(reply_waiting.wait(), 2)
        if intervention == "interrupt":
            connection.suppressed = True
        else:
            valid[0] = False
        release_reply.set()
        await processing
        assert not media.synthesized
        assert len(runtime.memories) == 1

    asyncio.run(scenario())
