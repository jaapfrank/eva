import asyncio
import pytest
from livekit.agents import AgentSession
from eva.voice_service.agent import BridgeState, EvaAgent

class Backend:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = []
    async def events(self, payload):
        self.calls.append(payload)
        if payload["text"] == "Onderzoek het onderwerp":
            self.started.set()
            await self.release.wait()
            text = "Het echte resultaat is binnen."
        else:
            text = "Je tweede vraag is ontvangen."
        yield {"type": "final", "reply": {"text": text}}

@pytest.mark.asyncio
async def test_livekit_background_tool_allows_second_turn_without_duplicate_dispatch():
    backend = Backend()
    state = BridgeState(backend)
    agent = EvaAgent(state)
    heard = []
    async with AgentSession() as session:
        @session.on("conversation_item_added")
        def item_added(event):
            if event.item.type == "message" and event.item.role == "assistant": heard.append(event.item.text_content)
        await session.start(agent)
        await asyncio.wait_for(session.run(user_input="Onderzoek het onderwerp"), 5)
        await asyncio.wait_for(backend.started.wait(), 5)
        assert state.active
        await asyncio.wait_for(session.run(user_input="Hoe gaat het?"), 5)
        for _ in range(100):
            if any("tweede" in item for item in heard): break
            await asyncio.sleep(.02)
        assert any("tweede" in item for item in heard), heard
        assert len(state.active) == 1
        backend.release.set()
        for _ in range(100):
            if any("resultaat" in item for item in heard): break
            await asyncio.sleep(.02)
        assert any("resultaat" in item for item in heard), heard
        assert len(backend.calls) == 2
        assert backend.calls[1]["active_work"][0]["text"] == "Onderzoek het onderwerp"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["error", "corrected_final"])
async def test_pending_draft_is_discarded_on_error_or_changed_final(ending):
    import json
    class PartialBackend:
        async def events(self, payload):
            yield {"type": "delta", "text": "Ik heb het bestand definitief gewijzigd"}
            await asyncio.sleep(.02)
            if ending == "error":
                yield {"type": "error"}
            else:
                yield {"type": "final", "reply": {"text": "Het bestand is niet gewijzigd."}}
    updates = []
    class Context:
        async def update(self, value, **kwargs): updates.append(json.loads(value))
    state = BridgeState(PartialBackend())
    await EvaAgent(state).eva_turn(Context(), "test-message", "Test")
    speech = [item["text"] for item in updates if item["type"] == "speech"]
    assert len(speech) == 1, speech
    assert "definitief gewijzigd" not in speech[0]
    assert speech[0].startswith("Het antwoord is niet bevestigd" if ending == "error" else "Het bestand is niet gewijzigd.")
    assert not state.active


@pytest.mark.asyncio
async def test_stopped_work_keeps_stop_status_when_backend_terminates():
    import json
    class StoppedBackend:
        async def events(self, payload):
            yield {"type": "delta", "text": "Dit oude resultaat mag niet terugkomen"}
            yield {"type": "task_stop_requested", "message_id": "voice:session:target"}
            yield {"type": "error"}
    updates, events = [], []
    class Context:
        async def update(self, value, **kwargs): updates.append(json.loads(value))
    state = BridgeState(StoppedBackend(), events.append)
    await EvaAgent(state).eva_turn(Context(), "target", "Test")
    assert events[-1] == {"type": "task_stop_requested", "message_id": "target"}
    assert not any(item["type"] == "speech" for item in updates)
    assert not state.active


@pytest.mark.asyncio
async def test_cancelled_generation_does_not_consume_user_before_tool_admission():
    import json
    from livekit.agents import llm
    from eva.voice_service.agent import EvaLLM
    backend = Backend()
    state = BridgeState(backend)
    model = EvaLLM(state)
    chat = llm.ChatContext()
    chat.add_message(role="user", content="Hoe gaat het?")
    calls = []
    for _ in range(2):
        async with model.chat(chat_ctx=chat) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.tool_calls:
                    calls.extend(chunk.delta.tool_calls)
        assert not state.dispatched
    assert len(calls) == 2
    assert calls[0].arguments == calls[1].arguments
    class Context:
        async def update(self, value, **kwargs): pass
    agent = EvaAgent(state)
    arguments = json.loads(calls[0].arguments)
    await agent.eva_turn(Context(), **arguments)
    await agent.eva_turn(Context(), **arguments)
    assert len(backend.calls) == 1
    assert state.dispatched
