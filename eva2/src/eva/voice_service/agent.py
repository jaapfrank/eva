"""LiveKit async tools wrap Eva turns; this adapter makes no model calls."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import AsyncIterator, Callable
from uuid import UUID, uuid4, uuid5

import httpx
from livekit.agents import Agent, RunContext, function_tool, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

class CoreClient:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    async def ready(self) -> bool:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(self.url + "/internal/v1/voice-session/ready",
                headers={"Authorization": "Bearer " + self.token})
            return response.status_code == 200 and response.json().get("contract") == "EvaVoiceSessionReady/v1"

    def request(self, payload):
        return "/internal/v1/voice-session/respond", payload

    async def events(self, payload: dict) -> AsyncIterator[dict]:
        path, body = self.request(payload)
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False) as client:
            async with client.stream("POST", self.url + path,
                headers={"Authorization": "Bearer " + self.token}, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if len(line) > 131072:
                        raise ValueError("voice response exceeds bound")
                    if line:
                        yield json.loads(line)

class HouseholdCoreClient(CoreClient):
    """Fixed provisioned speaker binding; cannot fall back to owner endpoints."""
    def __init__(self, url, token, *, satellite_id, device_id):
        import re
        if not re.fullmatch(r"assist_satellite\.[a-z0-9_]+", satellite_id) or not re.fullmatch(r"[0-9a-f]{32}", device_id):
            raise ValueError("Invalid provisioned household device")
        super().__init__(url, token)
        self._device = {"satellite_id": satellite_id, "device_id": device_id}

    async def ready(self):
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(self.url + "/internal/v1/ha-voice/session/ready",
                headers={"Authorization": "Bearer " + self.token}, json=self._device)
            return response.status_code == 200 and response.json().get("contract") == "EvaHouseholdVoiceSessionReady/v1"

    def request(self, payload):
        if {"satellite_id", "device_id", "principal_id", "audience"} & payload.keys():
            raise ValueError("Voice content cannot select authority")
        return "/internal/v1/ha-voice/session/respond", {**payload, **self._device}

class BridgeState:
    def __init__(self, backend, notify: Callable[[dict], None] = lambda value: None, *, replay_unheard: bool = True):
        self.backend, self.notify = backend, notify
        self.replay_unheard = replay_unheard
        self.session_id = str(uuid4())
        self.dispatched: set[str] = set()
        self.message_users: dict[str, str] = {}
        self.active: dict[str, str] = {}
        self.suppressed: set[str] = set()
        self.deliveries: dict[str, object] = {}
        self.delivery_text: dict[str, str] = {}
        self.snapshots: dict[str, list[dict]] = {}
        self.results: dict[str, str] = {}
        self.spoken_streams: dict[str, object] = {}

    def history(self, chat_ctx: llm.ChatContext, exclude: str) -> list[dict]:
        return [{"role": item.role, "text": item.text_content,
                 "occurred_at": datetime.fromtimestamp(item.created_at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "interrupted": item.interrupted}
                for item in chat_ctx.items if item.type == "message"
                and item.role in {"user", "assistant"} and item.id != exclude
                and item.text_content][-24:]

class EvaLLM(llm.LLM):
    """Deterministic dispatch only; every natural-language reply comes from Eva."""
    def __init__(self, state: BridgeState):
        super().__init__()
        self.state = state

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **kwargs):
        return EvaStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)

class EvaStream(llm.LLMStream):
    async def _run(self):
        state = self._llm.state
        user = next((item for item in reversed(self._chat_ctx.items)
                     if item.type == "message" and item.role == "user"), None)
        if user is not None and user.id not in state.dispatched:
            # Generating a tool call is not admission: LiveKit may cancel this
            # generation when speech is interrupted before the tool starts.
            message_id = str(uuid5(UUID(state.session_id), user.id))
            state.message_users[message_id] = user.id
            state.snapshots[message_id] = state.history(self._chat_ctx, user.id)
            self._event_ch.send_nowait(llm.ChatChunk(id=message_id, delta=llm.ChoiceDelta(
                role="assistant", tool_calls=[llm.FunctionToolCall(
                    name="eva_turn", call_id=message_id,
                    arguments=json.dumps({"message_id": message_id, "text": user.text_content}),
                )])))
            return

class EvaAgent(Agent):
    def __init__(self, state: BridgeState):
        super().__init__(instructions="Eva's bestaande kern levert ieder inhoudelijk antwoord.", llm=EvaLLM(state))
        self.state = state

    def deliver(self, session, message_id: str, text: str, *, final: bool = True):
        if message_id in self.state.suppressed:
            return
        import asyncio
        stream = self.state.spoken_streams.get(message_id)
        if stream is not None:
            stream.put_nowait(text)
            self.state.delivery_text[message_id] += " " + text
            if final:
                stream.put_nowait(None)
            return
        self.state.delivery_text[message_id] = text
        try:
            if final:
                source = text
            else:
                stream = asyncio.Queue(maxsize=4)
                self.state.spoken_streams[message_id] = stream
                stream.put_nowait(text)
                async def parts():
                    while True:
                        part = await stream.get()
                        if part is None:
                            return
                        # LiveKit's maintained markdown/emoji transforms buffer
                        # incomplete paragraphs, even after a sentence boundary.
                        yield part + "\n\n"
                source = parts()
            # A first part and its continuation share one native speech handle:
            # barge-in cancels the entire spoken tail, not only the first sentence.
            self.state.deliveries[message_id] = session.say(source, allow_interruptions=True)
        except RuntimeError:
            self.state.notify({"type": "voice_delivery_failed", "message_id": message_id})

    def replay_unheard(self, session):
        if not self.state.replay_unheard:
            return  # A hardware STOPPED lower bound cannot prove silence.
        # Native interruption cancels queued handles too. Restore only a result
        # with no played text in its own handle, never the tail of a spoken reply.
        for message_id, handle in list(self.state.deliveries.items()):
            if message_id in self.state.active and message_id in self.state.spoken_streams:
                continue
            if (handle.interrupted and handle.done()
                    and not any(item.type == "message" and item.role == "assistant"
                                and item.text_content.strip() for item in handle.chat_items)
                    and message_id not in self.state.suppressed):
                self.state.spoken_streams.pop(message_id, None)
                self.deliver(session, message_id, self.state.delivery_text[message_id])

    async def on_user_turn_completed(self, turn_ctx, new_message):
        self.replay_unheard(self.session)

    @function_tool
    async def eva_turn(self, ctx: RunContext, message_id: str, text: str) -> str:
        """Route one final transcript to Eva's authenticated core."""
        state = self.state
        user_id = state.message_users.get(message_id)
        if user_id is not None:
            if user_id in state.dispatched:
                return json.dumps({"type": "done", "message_id": message_id})
            state.dispatched.add(user_id)
        active = [{"message_id": key, "text": value} for key, value in state.active.items()]
        state.active[message_id] = text
        state.notify({"type": "work_started", "message_id": message_id, "text": text})
        await ctx.update(json.dumps({"type": "running", "message_id": message_id}), template="{message}")
        import asyncio
        final_seen = False
        try:
            async for event in state.backend.events({
                "session_id": state.session_id, "message_id": message_id, "text": text,
                "recent": state.snapshots.pop(message_id, []), "active_work": active,
                "work_results": [{"message_id": key, "text": value[:16000]}
                                 for key, value in list(state.results.items())[-4:] if key not in state.suppressed],
            }):
                kind = event.get("type")
                if kind == "spoken_update":
                    if message_id not in state.spoken_streams:
                        self.deliver(ctx.session, message_id, event["text"], final=False)
                        state.notify({**event, "message_id": message_id})
                    continue
                if kind == "delta":
                    # Speak one confirmed answer through one native speech handle.
                    # This avoids separate sentence updates restarting after barge-in.
                    continue
                if kind == "task_stop_requested":
                    target = event["message_id"].rsplit(":", 1)[-1]
                    try:
                        target = str(UUID(target))
                    except ValueError:
                        pass
                    state.suppressed.add(target)
                    handle = state.deliveries.get(target)
                    if handle is not None:
                        handle.interrupt()
                    state.notify({"type": kind, "message_id": target})
                elif kind == "final":
                    final_seen = True
                    final = event["reply"]["text"]
                    state.results[message_id] = final
                    if message_id not in state.suppressed:
                        state.notify({"type": "work_finished", "message_id": message_id, "text": final})
                        self.deliver(ctx.session, message_id, final)
                elif kind == "error":
                    raise RuntimeError("core response failed")
                else:
                    state.notify({**event, "message_id": message_id})
            if not final_seen:
                raise RuntimeError("core stream ended before its final result")
        except asyncio.CancelledError:
            # Session closure stops delivery; core retains admitted work and effects.
            raise
        except Exception:
            if message_id not in state.suppressed:
                state.notify({"type": "work_failed", "message_id": message_id})
                self.deliver(ctx.session, message_id,
                             "Het antwoord is niet bevestigd. Controleer de taak voordat je haar opnieuw start.")
        finally:
            stream = state.spoken_streams.get(message_id)
            if stream is not None and not stream.full():
                stream.put_nowait(None)
            state.active.pop(message_id, None)
        return json.dumps({"type": "done", "message_id": message_id})
