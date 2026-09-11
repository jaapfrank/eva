import asyncio
import pytest
from livekit.agents import AgentSession
from eva.voice_service.agent import BridgeState, EvaAgent

class SpeechCapture:
    def __init__(self): self.spoken = []
    def say(self, text, **kwargs):
        from types import SimpleNamespace
        self.spoken.append(text)
        return SimpleNamespace(interrupted=False, done=lambda: True, chat_items=[])

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
        session = SpeechCapture()
        async def update(self, value, **kwargs): updates.append(json.loads(value))
    state = BridgeState(PartialBackend())
    ctx = Context()
    await EvaAgent(state).eva_turn(ctx, "test-message", "Test")
    speech = ctx.session.spoken
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
        session = SpeechCapture()
        async def update(self, value, **kwargs): updates.append(json.loads(value))
    state = BridgeState(StoppedBackend(), events.append)
    ctx = Context()
    await EvaAgent(state).eva_turn(ctx, "target", "Test")
    assert not ctx.session.spoken
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
        session = SpeechCapture()
        async def update(self, value, **kwargs): pass
    agent = EvaAgent(state)
    arguments = json.loads(calls[0].arguments)
    await agent.eva_turn(Context(), **arguments)
    await agent.eva_turn(Context(), **arguments)
    assert len(backend.calls) == 1
    assert state.dispatched


@pytest.mark.asyncio
async def test_barge_in_drops_spoken_tail_and_replays_only_unheard_queued_result():
    from types import SimpleNamespace
    from livekit.agents import llm
    state = BridgeState(Backend())
    agent = EvaAgent(state)
    session = SpeechCapture()
    agent.deliver(session, "first", "De lange eerste uitleg.")
    agent.deliver(session, "second", "Het andere onderzoek is klaar.")
    state.active["still-running"] = "Nog een onderzoek"
    state.deliveries["first"] = SimpleNamespace(interrupted=True, done=lambda: True,
        chat_items=[llm.ChatMessage(role="assistant", content=["De lange"], interrupted=True)])
    state.deliveries["second"] = SimpleNamespace(interrupted=True, done=lambda: True, chat_items=[])
    agent.replay_unheard(session)
    assert session.spoken == ["De lange eerste uitleg.", "Het andere onderzoek is klaar.",
                              "Het andere onderzoek is klaar."]
    assert state.active == {"still-running": "Nog een onderzoek"}
    assert not state.suppressed
    agent.replay_unheard(session)
    assert len(session.spoken) == 3


@pytest.mark.asyncio
async def test_explicit_first_part_arrives_before_work_finishes_on_one_speech_handle():
    first_seen = asyncio.Event()
    release = asyncio.Event()
    class ProgressBackend:
        async def events(self, payload):
            yield {"type": "spoken_update", "text": "Een regenboog ontstaat door gebroken zonlicht."}
            await release.wait()
            yield {"type": "final", "reply": {"text": "Elke kleur buigt anders af."}}
    class Session:
        def __init__(self): self.sources = []
        def say(self, source, **kwargs):
            from types import SimpleNamespace
            self.sources.append(source)
            return SimpleNamespace(interrupted=False, done=lambda: False, chat_items=[])
    class Context:
        session = Session()
        async def update(self, *args, **kwargs): pass
    state = BridgeState(ProgressBackend())
    ctx = Context()
    work = asyncio.create_task(EvaAgent(state).eva_turn(ctx, "first", "Leg uit"))
    for _ in range(100):
        if ctx.session.sources: break
        await asyncio.sleep(.01)
    stream = ctx.session.sources[0]
    assert "gebroken zonlicht" in await asyncio.wait_for(anext(stream), 1)
    assert not work.done()
    release.set()
    await work
    assert "anders af" in await asyncio.wait_for(anext(stream), 1)
    with pytest.raises(StopAsyncIteration): await anext(stream)
    assert len(ctx.session.sources) == 1


@pytest.mark.asyncio
async def test_interrupting_first_part_does_not_create_new_handle_for_continuation():
    from types import SimpleNamespace
    state = BridgeState(Backend())
    agent = EvaAgent(state)
    session = SpeechCapture()
    agent.deliver(session, "first", "Een regenboog is gebroken zonlicht.", final=False)
    first_handle = state.deliveries["first"]
    first_handle.interrupted = True
    agent.deliver(session, "first", "Dit is het vervolg.")
    assert state.deliveries["first"] is first_handle
    assert len(session.spoken) == 1


@pytest.mark.asyncio
async def test_real_tts_stream_emits_short_first_part_before_backend_continuation():
    import struct
    from types import SimpleNamespace
    from eva.voice_service.media import streaming_speech_output
    requested = asyncio.Event()
    texts = []
    class Source:
        async def synthesize(self, text):
            texts.append(text)
            requested.set()
            yield SimpleNamespace(data=struct.pack("<h", 1000) * 2400, mime_type="audio/pcm")
    adapter = streaming_speech_output(Source())
    stream = adapter.stream()
    stream.push_text("Door lichtbreking. ")
    async def consume():
        return await anext(stream)
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(requested.wait(), 2)
        assert [text.strip() for text in texts] == ["Door lichtbreking."]
        event = await asyncio.wait_for(task, 2)
        assert event.frame.samples_per_channel > 0
    finally:
        await stream.aclose()
        await adapter.aclose()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
