"""Local authenticated browser access to an optional LiveKit media session."""
from __future__ import annotations
import asyncio
import base64
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from livekit import api, rtc
from livekit.agents import AgentSession, room_io
from livekit.plugins import silero

from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput, HomeAssistantSpeechOutput
from eva.voice_service.agent import BridgeState, CoreClient, EvaAgent
from eva.voice_service.media import HomeAssistantSTT, HomeAssistantTTS
from eva.voice_service.lifecycle import SessionLease


def create_app():
    config = json.loads(Path(os.environ["EVA_VOICE_CONFIG"]).read_text())
    token = Path(config["owner_token_file"]).read_text().strip()
    ha_token = Path(config["ha_token_file"]).read_text().strip()
    key = config["livekit_api_key"]
    secret = Path(config["livekit_secret_file"]).read_text().strip()
    sessions = {}
    lock = asyncio.Lock()
    vad = silero.VAD.load(min_speech_duration=.1, min_silence_duration=.45)
    backend = CoreClient(config["core_url"], token)
    stt_source = HomeAssistantSpeechInput(base_url=config["ha_url"], token=ha_token,
        pipeline_id=config["pipeline_id"], expected_stt_engine=config["stt_engine"],
        expected_pipeline_language=config["language"])
    tts_source = HomeAssistantSpeechOutput(base_url=config["ha_url"], token=ha_token,
        pipeline_id=config["pipeline_id"], tts_engine=config["tts_engine"],
        tts_language=config["tts_language"])

    @asynccontextmanager
    async def lifespan(app):
        yield
        for lease in list(sessions.values()):
            await lease.close()

    def check():
        try:
            raw = Path(config["authority_file"]).read_bytes()
            identity = Path(config["identity_file"]).read_bytes()
            grant = json.loads(raw)
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if (hashlib.sha256(raw).hexdigest() != config["authority_sha256"]
                    or hashlib.sha256(identity).hexdigest() != config["identity_sha256"]
                    or grant["state"] != "enabled"
                    or not grant["effective_at"] <= stamp < grant["expires_at"]
                    or not secrets.compare_digest(Path(config["owner_token_file"]).read_text().strip(), token)):
                raise ValueError("voice authority changed")
        except (OSError, KeyError, ValueError):
            raise ValueError("voice authority unavailable") from None

    app = FastAPI(lifespan=lifespan)
    def admit(request, *, mutation=False):
        raw = request.headers.get("authorization", "")
        candidate = ""
        try:
            scheme, value = raw.split(" ", 1)
            if scheme.lower() == "bearer":
                candidate = value
            elif scheme.lower() == "basic":
                username, password = base64.b64decode(value, validate=True).decode().split(":", 1)
                if username == "eva-owner":
                    candidate = password
        except (ValueError, UnicodeError):
            pass
        if not candidate or not secrets.compare_digest(candidate, token):
            raise HTTPException(401, "Eigenaarsaanmelding vereist", headers={
                "WWW-Authenticate": 'Basic realm="Eva cockpit", charset="UTF-8"'})
        try:
            check()
        except ValueError:
            raise HTTPException(403, "Stemtoegang is ingetrokken of verlopen.") from None
        if mutation and request.headers.get("origin") != config["origin"]:
            raise HTTPException(403, "origin rejected")

    @app.get("/")
    def home(request: Request):
        admit(request)
        return HTMLResponse(Path(__file__).with_name("client.html").read_text(),
                            headers={"Cache-Control": "no-store"})

    @app.get("/livekit-client.js")
    def script(request: Request):
        admit(request)
        return FileResponse(config["client_js"], media_type="text/javascript")

    @app.post("/session/end")
    async def end(request: Request):
        admit(request, mutation=True)
        async with lock:
            for lease in list(sessions.values()):
                await lease.close()
        return {"closed": True, "ongoing_core_work_cancelled": False}

    @app.post("/session")
    async def start(request: Request):
        admit(request, mutation=True)
        async with lock:
            if sessions:
                raise HTTPException(409, "Er loopt al een stemgesprek. Sluit dat eerst af.")
            if not await backend.ready():
                raise HTTPException(503, "Eva's stemverbinding is nog niet beschikbaar.")
            room_name = "eva-voice-" + uuid4().hex
            def credential(identity, *, agent=False):
                return (api.AccessToken(key, secret).with_identity(identity)
                    .with_ttl(timedelta(minutes=30))
                    .with_grants(api.VideoGrants(room_join=True, room=room_name,
                        can_publish=True, can_subscribe=True, can_publish_data=True, agent=agent)).to_jwt())
            room = rtc.Room()
            tasks = set()
            def notify(value):
                async def send():
                    try:
                        await room.local_participant.publish_data(json.dumps(value).encode(), reliable=True, topic="eva.work")
                    except Exception:
                        pass  # Task effects and final answers remain in the core.
                task = asyncio.create_task(send())
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            state = BridgeState(backend, notify)
            session = AgentSession(stt=HomeAssistantSTT(stt_source, check), tts=HomeAssistantTTS(tts_source, check),
                                   vad=vad, turn_detection="vad", allow_interruptions=True,
                                   min_interruption_duration=.15, false_interruption_timeout=1.5)
            @session.on("agent_state_changed")
            def agent_state_changed(event):
                notify({"type": "voice_state", "state": event.new_state})
            @session.on("user_state_changed")
            def user_state_changed(event):
                notify({"type": "user_voice_state", "state": event.new_state})
            lease = SessionLease(session, room, lambda: sessions.pop(room_name, None))
            sessions[room_name] = lease
            try:
                await room.connect(config["livekit_url"], credential("eva", agent=True))
                await session.start(EvaAgent(state), room=room, room_options=room_io.RoomOptions(
                    participant_identity="owner", close_on_disconnect=True,
                    audio_input=room_io.AudioInputOptions(), video_input=False))
            except BaseException:
                await lease.close()
                raise
            @session.on("close")
            def closed(event):
                asyncio.create_task(lease.close())
            @room.on("participant_disconnected")
            def participant_left(participant):
                if participant.identity == "owner":
                    asyncio.create_task(lease.close())
            async def watch():
                started = asyncio.get_running_loop().time()
                while True:
                    await asyncio.sleep(1)
                    elapsed = asyncio.get_running_loop().time() - started
                    try:
                        check()
                    except ValueError:
                        await lease.close()
                        return
                    if elapsed >= 1800 or (elapsed >= 30 and "owner" not in room.remote_participants):
                        await lease.close()
                        return
            lease.timers.add(asyncio.create_task(watch()))
            return {"url": config["public_livekit_url"], "token": credential("owner"), "room": room_name}

    return app
