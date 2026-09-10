"""Owner-bound LiveKit connector endpoint; no client-selectable principal."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from queue import SimpleQueue
from threading import Thread
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_voice import DashboardVoice
from eva.adapters.api.dashboard_conversation import grounded_reply_wire, _record_stream_failure
from eva.application.conversation import ConversationTurn, RecentConversationMessage, stream_conversation_text
from eva.application.voice_context import VoiceContext, voice_context

class HeardMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=16000)
    occurred_at: str
    interrupted: bool = False

class ActiveWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    text: str = Field(min_length=1, max_length=4096)

class WorkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    text: str = Field(min_length=1, max_length=16000)

class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    message_id: UUID
    text: str = Field(min_length=1, max_length=4096)
    recent: list[HeardMessage] = Field(default_factory=list, max_length=24)
    active_work: list[ActiveWork] = Field(default_factory=list, max_length=8)
    work_results: list[WorkResult] = Field(default_factory=list, max_length=4)

def install_voice_turns(app: FastAPI, *, access: DashboardAccess,
                        runtime, config: DashboardVoice | None) -> None:
    def admit(request: Request):
        if config is None or access.token is None:
            raise HTTPException(503, "voice session unavailable")
        access.require(request)
        try:
            grant = config.issue_session()
            config.check(grant)
            return grant
        except ValueError:
            raise HTTPException(403, "voice authority unavailable") from None

    @app.get("/internal/v1/voice-session/ready")
    def ready(request: Request):
        admit(request)
        return {"contract": "EvaVoiceSessionReady/v1"}

    @app.post("/internal/v1/voice-session/respond")
    def respond(request: Request, payload: VoiceRequest):
        grant = admit(request)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        conversation_id = f"voice:{payload.session_id}"
        def message_id(value):
            return f"voice:{payload.session_id}:{value}"
        turn = ConversationTurn.private(
            grant.tenant_id, grant.installation_id, grant.principal_id,
            conversation_id, message_id(payload.message_id), payload.text, stamp,
        )
        try:
            recent = tuple(RecentConversationMessage(
                role="Jaap" if item.role == "user" else "Eva",
                text=item.text, occurred_at=item.occurred_at,
                playback_status=(None if item.role == "user" else
                                 "interrupted" if item.interrupted else "completed"),
            ) for item in payload.recent)
            if len({item.message_id for item in payload.active_work}) != len(payload.active_work):
                raise ValueError("duplicate work")
            if any(item.message_id == payload.message_id for item in payload.active_work):
                raise ValueError("self work")
        except ValueError:
            raise HTTPException(422, "voice context invalid") from None
        events: SimpleQueue = SimpleQueue()
        delivery_closed = False
        def event(value):
            nonlocal delivery_closed
            if delivery_closed:
                return
            try:
                config.check(grant)
            except (ValueError, RuntimeError):
                delivery_closed = True
                events.put({"type": "error", "code": "voice_authority_changed"})
                return
            events.put(value)
        def stop(target):
            config.check(grant)
            runtime.request_turn_stop(replace(turn, message_id=target),
                                      request_message_id=turn.message_id + ":" + target[-36:])
        context = VoiceContext(recent=recent,
            active_work=tuple((message_id(item.message_id), item.text) for item in payload.active_work),
            stop=stop, event=event,
            known_results=tuple((message_id(item.message_id), item.text) for item in payload.work_results))
        def run():
            stage = "respond"
            try:
                config.check(grant)
                with voice_context(context), stream_conversation_text(
                    lambda text: event({"type": "delta", "text": text})
                ):
                    reply = runtime.respond(turn)
                event({"type": "final", "reply": grounded_reply_wire(reply)})
                stage = "after_response"
                runtime.after_response(turn, reply)
            except Exception as error:
                if stage == "respond":
                    event({"type": "error", "code": "voice_turn_failed"})
                _record_stream_failure(error, turn, stage)
        def stream():
            Thread(target=run, daemon=True, name="eva-voice-turn").start()
            while True:
                item = events.get()
                try:
                    config.check(grant)
                except (ValueError, RuntimeError):
                    item = {"type": "error", "code": "voice_authority_changed"}
                yield json.dumps(item, ensure_ascii=False).encode() + b"\n"
                if item["type"] in {"final", "error"}:
                    return
        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store"})
