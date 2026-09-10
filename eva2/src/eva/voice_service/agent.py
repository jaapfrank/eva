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

    async def events(self, payload: dict) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False) as client:
            async with client.stream("POST", self.url + "/internal/v1/voice-session/respond",
                headers={"Authorization": "Bearer " + self.token}, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if len(line) > 131072:
                        raise ValueError("voice response exceeds bound")
                    if line:
                        yield json.loads(line)

class BridgeState:
    def __init__(self, backend, notify: Callable[[dict], None] = lambda value: None):
        self.backend, self.notify = backend, notify
        self.session_id = str(uuid4())
        self.dispatched: set[str] = set()
        self.message_users: dict[str, str] = {}
        self.active: dict[str, str] = {}
        self.suppressed: set[str] = set()
        self.deliveries: dict[str, object] = {}
        self.delivery_text: dict[str, str] = {}
        self.snapshots: dict[str, list[dict]] = {}
        self.results: dict[str, str] = {}

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

    def deliver(self, session, message_id: str, text: str):
        if message_id in self.state.suppressed:
            return
        self.state.delivery_text[message_id] = text
        try:
            self.state.deliveries[message_id] = session.say(text, allow_interruptions=True)
        except RuntimeError:
            self.state.notify({"type": "voice_delivery_failed", "message_id": message_id})

    def replay_unheard(self, session):
        # Native interruption cancels queued handles too. Restore only a result
        # with no played text in its own handle, never the tail of a spoken reply.
        for message_id, handle in list(self.state.deliveries.items()):
            if (handle.interrupted and handle.done()
                    and not any(item.type == "message" and item.role == "assistant"
                                and item.text_content.strip() for item in handle.chat_items)
                    and message_id not in self.state.suppressed):
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
                if kind == "delta":
                    # Speak one confirmed answer through one native speech handle.
                    # This avoids separate sentence updates restarting after barge-in.
                    continue
                if kind == "task_stop_requested":
                    target = event["message_id"].rsplit(":", 1)[-1]
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
            state.active.pop(message_id, None)
        return json.dumps({"type": "done", "message_id": message_id})
