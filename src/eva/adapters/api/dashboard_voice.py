"""Authenticated voice transport into the ordinary conversation runtime.

Disabled unless bootstrap explicitly supplies a current owner/session grant and
media providers. Binary input is 16 kHz mono signed little-endian PCM. The client
sends start/end/interrupt/stop controls; all authority and message IDs are server
owned. Output carries a generation so clients can discard already buffered audio.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from eva.adapters.api.dashboard_voice_client import DASHBOARD_VOICE_CLIENT_JS

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.application.conversation import ConversationTurn, GroundedReply, silent_completion_scope
from eva.application.voice import SpeechAudioChunk, admit_final_voice_turn
from eva.contracts.voice import VoiceSession, VoiceTurn


class SpeechInput(Protocol):
    async def transcribe(self, chunks: AsyncIterable[bytes]) -> str: ...


class SpeechOutput(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[SpeechAudioChunk]: ...


class VoiceRuntime(Protocol):
    def respond(self, turn: ConversationTurn) -> GroundedReply: ...
    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None: ...
    def dashboard_stop_conversation(self) -> object: ...


@dataclass(frozen=True)
class DashboardVoice:
    allowed_origin: str
    issue_session: Callable[[], VoiceSession]
    # Must compare current owner, identity, policy AND provider consent with grant.
    validate_current: Callable[[VoiceSession], None]
    speech_input: SpeechInput
    speech_output: SpeechOutput
    language: str = "nl-NL"
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def check(self, session: VoiceSession) -> None:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("voice clock must be timezone aware")
        start = datetime.fromisoformat(session.created_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(session.deadline.replace("Z", "+00:00"))
        if not start <= now < end:
            raise ValueError("voice session expired")
        self.validate_current(session)


def install_dashboard_voice(
    app: FastAPI, *, access: DashboardAccess,
    runtime: VoiceRuntime, config: DashboardVoice | None,
) -> None:
    @app.get("/internal/v1/dashboard/conversation/voice-client.js")
    def voice_client(request: Request) -> Response:
        access.require(request)
        return Response(DASHBOARD_VOICE_CLIENT_JS, media_type="text/javascript",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.websocket("/internal/v1/dashboard/conversation/voice")
    async def voice(socket: WebSocket) -> None:
        # No permissive development mode, query credentials or cross-origin media.
        if (config is None or access.token is None or socket.query_params
                or socket.headers.get("origin") != config.allowed_origin):
            await socket.close(code=1008)
            return
        try:
            access.require(socket)  # WebSocket and Request share HTTPConnection headers.
            session = config.issue_session().model_copy(
                update={"session_id": f"voice-session:{uuid4()}"}
            )
            config.check(session)
        except (HTTPException, ValueError):
            await socket.close(code=1008)
            return
        await socket.accept()
        connection = _Connection(socket, config, session, runtime)
        await connection.run()


class _Connection:
    def __init__(self, socket: WebSocket, config: DashboardVoice,
                 session: VoiceSession, runtime: VoiceRuntime) -> None:
        self.socket, self.config, self.session, self.runtime = socket, config, session, runtime
        self.generation = 0
        self.suppressed = False
        self.connected = True
        self.capture: asyncio.Queue[bytes | None] | None = None
        self.capture_bytes = 0
        self.task: asyncio.Task[None] | None = None
        self.tts_task: asyncio.Task[None] | None = None
        self.send_lock = asyncio.Lock()

    async def send(self, kind: str, **payload: object) -> None:
        async with self.send_lock:
            if self.connected:
                await self.socket.send_json({"type": kind, "generation": self.generation, **payload})

    async def run(self) -> None:
        try:
            await self.send("ready", session_id=self.session.session_id,
                            sample_rate=16000, encoding="pcm_s16le", channels=1)
            while True:
                self.config.check(self.session)
                deadline = datetime.fromisoformat(self.session.deadline.replace("Z", "+00:00"))
                remaining = (deadline - self.config.clock()).total_seconds()
                message = await asyncio.wait_for(self.socket.receive(), timeout=min(remaining, 120))
                if message["type"] == "websocket.disconnect":
                    break
                self.config.check(self.session)
                if message.get("bytes") is not None:
                    chunk = message["bytes"]
                    if (self.capture is None or not chunk or len(chunk) > 65536
                            or len(chunk) % 2 or self.capture_bytes + len(chunk) > 3_840_000):
                        raise ValueError("invalid voice frame")
                    self.capture_bytes += len(chunk)
                    self.capture.put_nowait(chunk)
                    continue
                raw = message.get("text", "")
                if len(raw) > 128:
                    raise ValueError("invalid voice control")
                control = json.loads(raw)
                if not isinstance(control, dict) or set(control) != {"type"}:
                    raise ValueError("invalid voice control")
                kind = control["type"]
                if not isinstance(kind, str):
                    raise ValueError("invalid voice control")
                if kind == "start":
                    if self.task is not None and not self.task.done():
                        await self.send("busy")
                        continue
                    self.config.check(self.session)
                    self.generation += 1
                    self.suppressed = False
                    self.capture_bytes = 0
                    self.capture = asyncio.Queue(maxsize=64)
                    self.task = asyncio.create_task(self.process(self.capture))
                    await self.send("listening")
                elif kind == "end":
                    if self.capture is None or not self.capture_bytes:
                        raise ValueError("no voice capture")
                    self.capture.put_nowait(None)
                    self.capture = None
                elif kind in {"interrupt", "stop"}:
                    self.suppressed = True
                    if self.tts_task is not None:
                        self.tts_task.cancel()
                    await self.send("clear_audio")
                    if kind == "stop":
                        # Explicit stop uses the existing durable action cancellation.
                        await asyncio.to_thread(self.runtime.dashboard_stop_conversation)
                        await self.send("stop_requested")
                else:
                    raise ValueError("unknown voice control")
        except (ValueError, asyncio.QueueFull, TimeoutError):
            await self.socket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            self.connected = False
            self.suppressed = True
            if self.tts_task is not None:
                self.tts_task.cancel()
            if self.task is not None:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)

    async def process(self, queue: asyncio.Queue[bytes | None]) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            sequence = 0
            while (chunk := await queue.get()) is not None:
                yield chunk
                sequence += 1
                await self.send("audio_ack", sequence=sequence)
        try:
            self.config.check(self.session)
            text = await self.config.speech_input.transcribe(chunks())
            self.config.check(self.session)
            if self.suppressed:
                return
            stamp = self.config.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            bound = {key: getattr(self.session, key) for key in (
                "session_id", "tenant_id", "installation_id", "principal_id",
                "conversation_id", "audience",
            )}
            final = VoiceTurn(
                contract="VoiceTurn/v1", schema_version=1, **bound,
                voice_turn_id=f"voice-turn:{uuid4()}",
                conversation_message_id=f"dashboard:{uuid4()}",
                transcript_state="final", final_transcript=text,
                final_transcript_sha256=hashlib.sha256(text.encode()).hexdigest(),
                language=self.config.language, reply_generation=self.generation,
                finalized_at=stamp,
            )
            turn = admit_final_voice_turn(session=self.session, turn=final)
            # Cancelling a to_thread await does not stop tools. Keep ownership until
            # respond AND the canonical memory callback finish, even on disconnect.
            def respond() -> GroundedReply:
                with silent_completion_scope():
                    reply = self.runtime.respond(turn)
                self.runtime.after_response(turn, reply)
                return reply
            work = asyncio.create_task(asyncio.to_thread(respond))
            try:
                reply = await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
            self.config.check(self.session)
            if self.suppressed or not self.connected:
                return
            await self.send("reply", text=reply.text, message_id=turn.message_id,
                            **({"response_mode": "silent"} if reply.response_mode == "silent" else {}))
            if reply.response_mode == "silent":
                return
            self.config.check(self.session)
            if self.suppressed or not self.connected:
                return
            self.tts_task = asyncio.create_task(self.speak(reply.text))
            try:
                await self.tts_task
            except asyncio.CancelledError:
                if not self.suppressed:
                    raise
            if not self.suppressed:
                await self.send("audio_end")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider exceptions may contain credentials or private transcripts.
            await self.send("error", code="voice_turn_failed")
        finally:
            self.capture = None
            await self.send("turn_finished")

    async def speak(self, text: str) -> None:
        self.config.check(self.session)
        if self.suppressed or not self.connected:
            return
        stream = self.config.speech_output.synthesize(text)
        try:
            async for chunk in stream:
                self.config.check(self.session)
                if self.suppressed:
                    return
                await self.send("audio", mime_type=chunk.mime_type,
                                data=base64.b64encode(chunk.data).decode("ascii"))
        finally:
            await stream.aclose()
