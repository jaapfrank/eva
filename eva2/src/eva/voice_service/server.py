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
    heartbeats = {}
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
            raise HTTPException(401, "Eigenaarsaanmelding vereist")
        try:
            check()
        except ValueError:
            raise HTTPException(403, "Stemtoegang is ingetrokken of verlopen.") from None
        if mutation and request.headers.get("origin") != config["origin"]:
            raise HTTPException(403, "origin rejected")

    @app.get("/")
    def home(request: Request):
        # The empty sign-in page contains no owner data. Browser navigation must
        # not depend on an HTTP Basic dialog (blocked by embedded browsers).
        return HTMLResponse(Path(__file__).with_name("client.html").read_text(),
                            headers={"Cache-Control": "no-store"})

    @app.get("/livekit-client.js")
    def script(request: Request):
        return FileResponse(config["client_js"], media_type="text/javascript")

    @app.get("/session/ready")
    async def session_ready(request: Request):
        admit(request)
        if not await backend.ready():
            raise HTTPException(503, "Eva's stemverbinding is nog niet beschikbaar.")
        return {"ready": True}

    @app.post("/session/end")
    async def end(request: Request):
        admit(request, mutation=True)
        async with lock:
            for lease in list(sessions.values()):
                await lease.close()
        return {"closed": True, "ongoing_core_work_cancelled": False}

    @app.post("/session/end/{session_id}")
    async def end_one(request: Request, session_id: str):
        admit(request, mutation=True)
        async with lock:
            lease = sessions.get(session_id)
            if lease is not None:
                await lease.close()
        return {"closed": True, "ongoing_core_work_cancelled": False}

    @app.post("/session/renew/{session_id}")
    async def renew(request: Request, session_id: str):
        admit(request, mutation=True)
        async with lock:
            deadline = heartbeats.get(session_id)
            if deadline is None or deadline <= asyncio.get_running_loop().time():
                lease = sessions.get(session_id)
                if deadline is not None and lease is not None:
                    await lease.close()
                raise HTTPException(410, "Stemverbinding is verlopen.")
            heartbeats[session_id] = asyncio.get_running_loop().time() + 10
        return {"renewed": True, "expires_in": 10}

    @app.post("/session/leased")
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
            # Browser capture already requests AEC. LiveKit's initial 3s warmup
            # would otherwise discard a real correction during the first answer.
            session = AgentSession(stt=HomeAssistantSTT(stt_source, check), tts=HomeAssistantTTS(tts_source, check),
                                   vad=vad, turn_detection="vad", allow_interruptions=True,
                                   min_interruption_duration=.15, false_interruption_timeout=1.5,
                                   aec_warmup_duration=0)
            @session.on("agent_state_changed")
            def agent_state_changed(event):
                notify({"type": "voice_state", "state": event.new_state})
            @session.on("user_state_changed")
            def user_state_changed(event):
                notify({"type": "user_voice_state", "state": event.new_state})
            async def delete_room():
                async with api.LiveKitAPI(config["livekit_url"], api_key=key, api_secret=secret) as control:
                    try:
                        await control.room.delete_room(api.DeleteRoomRequest(room=room_name))
                    except api.TwirpError as error:
                        if error.code != "not_found":
                            raise
            def forget():
                sessions.pop(room_name, None)
                heartbeats.pop(room_name, None)
            lease = SessionLease(session, room, forget, delete_room)
            sessions[room_name] = lease
            try:
                async with asyncio.timeout(20):
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
            if request.url.path == "/session/leased":
                heartbeats[room_name] = asyncio.get_running_loop().time() + 10
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
                    if (room_name in heartbeats and heartbeats[room_name] <= asyncio.get_running_loop().time()):
                        await lease.close()
                        return
                    if elapsed >= 1800 or (elapsed >= 30 and "owner" not in room.remote_participants):
                        await lease.close()
                        return
            lease.timers.add(asyncio.create_task(watch()))
            return {"url": config["public_livekit_url"], "token": credential("owner"), "room": room_name}

    return app
