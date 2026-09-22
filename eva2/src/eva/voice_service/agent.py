"""LiveKit async tools wrap Eva turns; this adapter makes no model calls."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import AsyncIterator, Callable
from uuid import UUID, uuid4, uuid5

import httpx
from livekit.agents import Agent, RunContext, function_tool, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from eva.application.conversation_receipt import RECEIVED_INPUT_TEXT

async def authorize_saved_return(backend, session_id, result, check):
    """Revalidate saved bytes at each context/output boundary, including after ack."""
    check()
    identity = {key: result[key] for key in ("work_id", "operation_id")}
    current = await backend.work_return("authorize", session_id, **identity)
    check()
    if (current.get("status") != "authorized"
            or any(current.get(field) != result[field]
                   for field in ("operation_id", "output_sha256", "result_sha256"))):
        raise ValueError("saved return authority changed")


class CoreClient:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token
        self.private_work_returns = False

    async def ready(self) -> bool:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(self.url + "/internal/v1/voice-session/ready",
                headers={"Authorization": "Bearer " + self.token})
            valid = response.status_code == 200 and response.json().get("contract") == "EvaVoiceSessionReady/v1"
            self.private_work_returns = valid and response.json().get("private_work_returns") is True
            return valid

    def request(self, payload):
        return "/internal/v1/voice-session/respond", payload

    async def work_return(self, operation, session_id, **identity):
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(self.url + "/internal/v1/voice-session/work/" + operation,
                headers={"Authorization": "Bearer " + self.token},
                json={"session_id": session_id, **identity})
            response.raise_for_status()
            data = response.json()
            if data.get("contract") != "EvaPrivateWorkReturn/v1":
                raise ValueError("private return contract differs")
            return data

    async def events(self, payload: dict) -> AsyncIterator[dict]:
        path, body = self.request(payload)
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False) as client:
            async with client.stream("POST", self.url + path,
                headers={"Authorization": "Bearer " + self.token}, json=body) as response:
                response.raise_for_status()
                if not response.headers.get("content-type", "").startswith("application/x-ndjson"):
                    raise ValueError("voice core returned another response format")
                yield {"type": "request_acknowledged", "text": RECEIVED_INPUT_TEXT}
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

    async def work_return(self, *args, **kwargs):
        raise ValueError("household devices cannot read private work returns")

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
        self.receipt_deliveries: dict[str, object] = {}
        self.receipts_offered: set[str] = set()
        self.background_returns: set[str] = set()
        self.invalid_background_returns: set[str] = set()
        self.saved_result_context: dict[str, dict] = {}

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
            if message_id in self.state.background_returns:
                continue  # Durable return claims are never automatically replayed.
            if message_id in self.state.active and message_id in self.state.spoken_streams:
                continue
            if (handle.interrupted and handle.done()
                    and not any(item.type == "message" and item.role == "assistant"
                                and item.text_content.strip() for item in handle.chat_items)
                    and message_id not in self.state.suppressed):
                self.state.spoken_streams.pop(message_id, None)
                self.deliver(session, message_id, self.state.delivery_text[message_id])

    def discard_saved_result(self, context_id, result):
        key = "background:" + result["operation_id"]
        self.state.invalid_background_returns.add(key)
        self.state.results.pop(context_id, None)
        self.state.saved_result_context.pop(context_id, None)
        try:
            self.state.notify({"type": "background_result_unavailable", "message_id": key})
        except Exception:
            pass  # Cleanup must still interrupt speech if the UI transport failed.

    async def refresh_saved_context(self):
        for context_id, result in list(self.state.saved_result_context.items()):
            try:
                await authorize_saved_return(self.state.backend, self.state.session_id,
                                             result, lambda: None)
            except Exception:
                self.discard_saved_result(context_id, result)

    async def return_saved_work(self, session, result, check):
        """One claimed result on its original live private session; no model call."""
        import asyncio
        import hashlib
        state = self.state
        text = result["output"]
        if (not isinstance(text, str) or not text.strip() or len(text.encode()) > 65536
                or hashlib.sha256(text.encode()).hexdigest() != result["output_sha256"]):
            raise ValueError("saved return bytes differ")
        identity = {key: result[key] for key in ("work_id", "operation_id")}
        key = "background:" + result["operation_id"]
        if key in state.background_returns:
            return
        async def authorize():
            if key in state.invalid_background_returns:
                raise ValueError("saved result was discarded")
            await authorize_saved_return(state.backend, state.session_id, result, check)
        await authorize()
        state.background_returns.add(key)
        context_id = str(uuid5(UUID(state.session_id), result["work_id"]))
        handle = None
        try:
            context = "Opgeslagen taak " + result["work_id"] + ":\n" + text
            if len(context) > 16000:
                context = context[:15000] + "\n[Verkort in gesprekscontext; vraag het volledige opgeslagen taakresultaat op.]"
            state.results[context_id] = context
            state.saved_result_context[context_id] = {
                field: result[field] for field in
                ("work_id", "operation_id", "output_sha256", "result_sha256")}
            # Full source bytes use bounded, hash-checked browser parts. Queued
            # publication separately checks this result's current authority.
            chunks = [text[offset:offset+2000] for offset in range(0, len(text), 2000)]
            for index, chunk in enumerate(chunks):
                check()
                state.notify({"type": "background_result_part", "message_id": key, "text": chunk,
                    "part": index, "total": len(chunks), **state.saved_result_context[context_id]})
            async def parts():
                for offset in range(0, len(text), 1500):
                    await authorize()
                    yield text[offset:offset+1500]
            # Saved result context has its own current read grant. Do not also
            # retain these bytes in LiveKit history, which feeds payload.recent.
            handle = session.say(parts(), allow_interruptions=True, add_to_chat_ctx=False)
            state.deliveries[key] = handle
            while not handle.done():
                await asyncio.sleep(.5)
                await authorize()
            await state.backend.work_return("ack", state.session_id, **identity)
        except BaseException:
            self.discard_saved_result(context_id, result)
            if handle is not None:
                handle.interrupt()
            raise

    async def poll_saved_work(self, session, check):
        import asyncio
        while True:
            check()
            response = await self.state.backend.work_return("poll", self.state.session_id)
            check()
            result = response.get("result")
            if result is not None:
                try:
                    await self.return_saved_work(session, result, check)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Work stop/retention can revoke a result without closing the
                    # conversation. A failed session renewal still fails closed.
                    check()
                    await self.state.backend.work_return("renew", self.state.session_id)
            await asyncio.sleep(2)

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
        spoken_update_seen = False
        try:
            # Connector context is not a lasting read grant. A result may have
            # expired or been revoked since its previous playback/acknowledgement.
            await self.refresh_saved_context()
            async for event in state.backend.events({
                "session_id": state.session_id, "message_id": message_id, "text": text,
                "recent": state.snapshots.pop(message_id, []), "active_work": active,
                "work_results": [{"message_id": key, "text": value[:16000]}
                                 for key, value in list(state.results.items())[-4:] if key not in state.suppressed],
            }):
                kind = event.get("type")
                if kind == "request_acknowledged":
                    if message_id not in state.receipts_offered and message_id not in state.suppressed:
                        state.receipts_offered.add(message_id)
                        # A receipt is a short, separate utterance. Interrupting
                        # it must not suppress the eventual background result.
                        try:
                            state.receipt_deliveries[message_id] = ctx.session.say(
                                event["text"], allow_interruptions=True, add_to_chat_ctx=False)
                            state.notify({**event, "message_id": message_id})
                        except RuntimeError:
                            state.notify({"type": "voice_delivery_failed", "message_id": message_id})
                    continue
                if kind == "spoken_update":
                    if not spoken_update_seen:
                        spoken_update_seen = True
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
                    for deliveries in (state.deliveries, state.receipt_deliveries):
                        handle = deliveries.get(target)
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
