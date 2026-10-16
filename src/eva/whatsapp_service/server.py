"""Duplex PCM bridge. Eva's existing voice service owns conversation and models.

Only the private WhatsApp transport connects here. This service never transcribes,
generates, synthesizes, stores audio, or creates a second conversation engine.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from livekit import rtc


def private_text(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError("private configuration required")
        return os.read(descriptor, 65537).decode().strip()
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class CallSettings:
    owner_token_file: str
    voice_token_file: str
    core_url: str
    voice_url: str
    voice_origin: str
    livekit_url: str

    def __post_init__(self):
        for field in ("core_url", "voice_url", "voice_origin", "livekit_url"):
            value = getattr(self, field)
            url = urlsplit(value)
            if (url.scheme not in ({"ws", "wss"} if field == "livekit_url" else {"http", "https"})
                    or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or url.username or url.password or url.path not in {"", "/"}
                    or url.query or url.fragment):
                raise ValueError("call bridge requires existing loopback services")


class VoiceLease:
    def __init__(self, settings, contact, call_id, *, client=None):
        self.settings, self.contact, self.call_id = settings, contact, call_id
        self.token = private_text(settings.owner_token_file)
        self.client = client or httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False)
        self.owns_client = client is None
        self.room = None

    async def authorize(self):
        if not secrets.compare_digest(self.token, private_text(self.settings.owner_token_file)):
            raise ValueError("call authority changed")
        response = await self.client.post(self.settings.core_url + "/internal/v1/whatsapp/authorize",
            headers={"Authorization": "Bearer " + self.token},
            json={"contact_phone": self.contact,
                  "message_id": hashlib.sha256(("whatsapp-call:" + self.call_id).encode()).hexdigest()})
        response.raise_for_status()
        if response.json() != {"authorized": True}:
            raise ValueError("call not authorized")

    async def voice(self, path):
        response = await self.client.post(self.settings.voice_url + path, json={}, headers={
            "Authorization": "Bearer " + private_text(self.settings.voice_token_file),
            "Origin": self.settings.voice_origin})
        response.raise_for_status()
        return response.json()

    async def open(self):
        await self.authorize()
        value = await self.voice("/session/leased")
        if (not isinstance(value, dict) or not isinstance(value.get("room"), str)
                or re.fullmatch(r"eva-voice-[0-9a-f]{32}", value["room"]) is None):
            raise ValueError("invalid existing voice lease")
        self.room = value["room"]
        if not isinstance(value.get("token"), str) or len(value["token"]) > 16384:
            raise ValueError("invalid existing voice credential")
        await self.authorize()
        return value

    async def renew(self):
        await self.authorize()
        value = await self.voice("/session/renew/" + self.room)
        if value.get("renewed") is not True:
            raise ValueError("voice lease not renewed")

    async def close(self):
        try:
            if self.room is not None:
                await self.voice("/session/end/" + self.room)
        finally:
            if self.owns_client:
                await self.client.aclose()


class CallAudio:
    def __init__(self, socket, lease, *, room_factory=rtc.Room):
        self.socket, self.lease = socket, lease
        self.room = room_factory()
        self.source = rtc.AudioSource(16000, 1, queue_size_ms=120)
        self.tasks = set()
        self.closed = asyncio.Event()
        self.ready = asyncio.Event()
        self.sending = asyncio.Lock()

    async def send_audio(self, track):
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1, frame_size_ms=20)
        try:
            await self.ready.wait()
            async for event in stream:
                frame = event.frame
                if frame.sample_rate != 16000 or frame.num_channels != 1:
                    raise ValueError("voice PCM format differs")
                async with self.sending:
                    await self.socket.send_bytes(bytes(frame.data))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.closed.set()
        finally:
            await stream.aclose()

    async def receive_audio(self):
        while True:
            message = await self.socket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not data or len(data) % 2 or len(data) > 64000:
                raise ValueError("invalid call PCM frame")
            frame = rtc.AudioFrame(data=data, sample_rate=16000, num_channels=1, samples_per_channel=len(data) // 2)
            await self.source.capture_frame(frame)

    async def watch(self):
        while True:
            await asyncio.sleep(3)
            await self.lease.renew()

    async def run(self):
        @self.room.on("track_subscribed")
        def subscribed(track, publication, participant):
            if participant.identity != "eva" or track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            task = asyncio.create_task(self.send_audio(track))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        @self.room.on("disconnected")
        def disconnected(*args):
            self.closed.set()

        @self.room.on("participant_disconnected")
        def participant_left(participant):
            if participant.identity == "eva":
                self.closed.set()

        try:
            value = await self.lease.open()
            await self.room.connect(self.lease.settings.livekit_url, value["token"])
            track = rtc.LocalAudioTrack.create_audio_track("whatsapp-microphone", self.source)
            await self.room.local_participant.publish_track(track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            await self.lease.authorize()
            await self.socket.send_json({"type": "ready", "sample_rate": 16000, "channels": 1, "encoding": "pcm_s16le"})
            self.ready.set()
            owned = [asyncio.create_task(self.receive_audio()), asyncio.create_task(self.watch()),
                     asyncio.create_task(self.closed.wait())]
            try:
                done, _ = await asyncio.wait(owned, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in owned:
                    task.cancel()
                await asyncio.gather(*owned, return_exceptions=True)
        finally:
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            try:
                await self.room.disconnect()
            finally:
                try:
                    await self.source.aclose()
                finally:
                    await self.lease.close()


def create_app(settings=None, *, lease_factory=VoiceLease, audio_factory=CallAudio):
    if settings is None:
        settings = CallSettings(**json.loads(private_text(os.environ["EVA_WHATSAPP_CALL_CONFIG"])))
    active = set()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for socket in list(active):
            await socket.close(code=1001)

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    def health():
        return {"status": "alive", "transport": "existing_eva_voice", "active_calls": len(active)}

    @app.get("/ready")
    async def ready(request: Request):
        expected = 'Bearer ' + private_text(settings.owner_token_file)
        if request.query_params or not secrets.compare_digest(request.headers.get('authorization', ''), expected):
            raise HTTPException(401, 'private call transport required')
        if active:
            raise HTTPException(409, 'call media already in use')
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
                response = await client.get(settings.voice_url + '/session/ready', headers={
                    'Authorization': 'Bearer ' + private_text(settings.voice_token_file),
                    'Origin': settings.voice_origin})
                response.raise_for_status()
                if response.json().get('ready') is not True:
                    raise ValueError('voice not ready')
            return {'ready': True, 'transport': 'existing_eva_voice'}
        except Exception:
            raise HTTPException(503, 'existing Eva voice unavailable') from None

    @app.websocket("/call-audio")
    async def call_audio(socket: WebSocket):
        contact, call_id = socket.headers.get("x-eva-contact", ""), socket.headers.get("x-eva-call-id", "")
        try:
            expected = "Bearer " + private_text(settings.owner_token_file)
            if (socket.query_params or not secrets.compare_digest(socket.headers.get("authorization", ""), expected)
                    or re.fullmatch(r"[1-9][0-9]{6,14}", contact) is None
                    or re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", call_id) is None or active):
                raise ValueError("call not admitted")
        except Exception:
            await socket.close(code=1008)
            return
        # No await between checking and reserving the one existing private route.
        active.add(socket)
        try:
            await socket.accept()
            await audio_factory(socket, lease_factory(settings, contact, call_id)).run()
        except (Exception, WebSocketDisconnect):
            pass  # No provider bodies, credentials, caller IDs or audio in logs.
        finally:
            active.discard(socket)
            try:
                await socket.close()
            except RuntimeError:
                pass

    return app
