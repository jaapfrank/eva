import asyncio
import struct
from types import SimpleNamespace

import httpx
import pytest
from livekit.agents import AgentSession
from livekit.agents.voice import io

from eva.voice_service.agent import BridgeState, CoreClient, EvaAgent, work_receipt_text
from eva.voice_service.media import streaming_speech_output


@pytest.mark.parametrize("text", [
    "Zoek in de actuele officiële documentatie wat cloud doet voor spraak.",
    "Onderzoek het onderwerp", "Vergelijk deze opties", "Kun je even zoeken naar bronnen?",
    "Schrijf een verhaal over een kat", "Maak een plan voor mijn onderzoek",
])
def test_explicit_work_receipt(text):
    assert work_receipt_text(text) == "Ik kijk er even voor je naar."


@pytest.mark.parametrize("text", [
    "Zoek niet verder.", "Kun je stoppen met zoeken?", "Wat betekent ‘zoek het uit’?",
    "Waarom vergelijk je deze dingen?", "Controleer je altijd je bronnen?",
    "Kun je uitleggen waarom de lucht blauw is?", "Zoek bronnen, nee, laat maar.",
    "Bereken drie maal zeven.", "Hoeveel is zeventien keer negen?", '"Zoek het uit"',
])
def test_simple_questions_and_cancellations_have_no_receipt(text):
    assert work_receipt_text(text) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content_type,work", [
    (200, "application/x-ndjson", True), (200, "application/x-ndjson", False),
    (403, "application/x-ndjson", True), (500, "application/x-ndjson", True),
    (200, "text/html", True),
])
async def test_receipt_requires_http_acceptance_and_precedes_body(monkeypatch, status, content_type, work):
    release = asyncio.Event()
    requested = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            await release.wait()
            yield b'{"type":"final","reply":{"text":"Done"}}\n'
    async def route(request):
        requested.append(request)
        return httpx.Response(status, headers={"content-type": content_type}, stream=Body())
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(route), **kw))
    events = CoreClient("http://core", "test-token").events({"text": "Onderzoek dit" if work else "Hoe gaat het?"})
    if status != 200 or content_type != "application/x-ndjson":
        with pytest.raises((httpx.HTTPStatusError, ValueError)):
            await anext(events)
    elif work:
        event = await asyncio.wait_for(anext(events), 1)
        assert event == {"type": "request_acknowledged", "text": "Ik kijk er even voor je naar."}
        assert not release.is_set()
        release.set()
        assert (await anext(events))["type"] == "final"
    else:
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(.01)
        assert not pending.done()
        release.set()
        assert (await pending)["type"] == "final"
    assert requested[0].headers["authorization"] == "Bearer test-token"
    await events.aclose()


@pytest.mark.asyncio
async def test_native_receipt_interrupt_allows_second_turn_and_later_result():
    first_audio = asyncio.Event()
    release = asyncio.Event()
    synthesized = []
    class Source:
        async def synthesize(self, text):
            synthesized.append(text)
            for _ in range(40):
                yield SimpleNamespace(data=struct.pack("<h", 1000) * 2400, mime_type="audio/pcm")
                await asyncio.sleep(.01)
    class Sink(io.AudioOutput):
        def __init__(self):
            super().__init__(label="receipt-test", capabilities=io.AudioOutputCapabilities(pause=False), sample_rate=24000)
            self.duration = 0
            self.frames = 0
        async def capture_frame(self, frame):
            await super().capture_frame(frame)
            self.duration += frame.samples_per_channel / frame.sample_rate
            self.frames += 1
            first_audio.set()
        def flush(self):
            super().flush()
            self.on_playback_finished(playback_position=self.duration, interrupted=False)
        def clear_buffer(self):
            self.on_playback_finished(playback_position=self.duration, interrupted=True)
    class Backend:
        def __init__(self): self.calls = []
        async def events(self, payload):
            self.calls.append(payload)
            if payload["text"] == "Onderzoek dit":
                for _ in range(2):
                    yield {"type": "request_acknowledged", "text": "Ik kijk er even voor je naar."}
                await release.wait()
                text = "Het echte resultaat is binnen."
            else:
                text = "Je tweede vraag is ontvangen."
            yield {"type": "final", "reply": {"text": text}}
    backend = Backend()
    state = BridgeState(backend)
    adapter = streaming_speech_output(Source())
    heard = []
    async with AgentSession(tts=adapter) as session:
        sink = Sink()
        session.output.audio = sink
        @session.on("conversation_item_added")
        def item_added(event):
            if event.item.type == "message" and event.item.role == "assistant":
                heard.append(event.item.text_content)
        await session.start(EvaAgent(state))
        async def run(text): await session.run(user_input=text)
        first = asyncio.create_task(run("Onderzoek dit"))
        try:
            await asyncio.wait_for(first_audio.wait(), 3)
            assert not release.is_set()
            first_id = backend.calls[0]["message_id"]
            receipt = state.receipt_deliveries[first_id]
            await asyncio.wait_for(session.interrupt(), 3)
            assert receipt.interrupted
            await asyncio.wait_for(run("Hoe gaat het?"), 5)
            for _ in range(300):
                if any("tweede vraag" in text for text in heard): break
                await asyncio.sleep(.01)
            assert any("tweede vraag" in text for text in heard), heard
            assert backend.calls[1]["active_work"][0]["message_id"] == first_id
            frames_before_result = sink.frames
            release.set()
            for _ in range(300):
                if any("echte resultaat" in text for text in heard): break
                await asyncio.sleep(.01)
            assert any("echte resultaat" in text for text in heard), heard
            assert sink.frames > frames_before_result
            assert state.deliveries[first_id] is not receipt
            assert sum("Ik kijk" in text for text in synthesized) == 1
        finally:
            release.set()
            await asyncio.wait_for(first, 5)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_stop_interrupts_receipt_and_suppresses_final():
    class Handle:
        interrupted = False
        def interrupt(self): self.interrupted = True
    class Session:
        def __init__(self): self.handles = []
        def say(self, text, **kwargs):
            handle = Handle()
            self.handles.append(handle)
            return handle
    class Backend:
        async def events(self, payload):
            yield {"type": "request_acknowledged", "text": "Ik kijk er even voor je naar."}
            yield {"type": "task_stop_requested", "message_id": "voice:target"}
            yield {"type": "final", "reply": {"text": "Niet uitspreken"}}
    class Context:
        session = Session()
        async def update(self, *args, **kwargs): pass
    context = Context()
    state = BridgeState(Backend())
    await EvaAgent(state).eva_turn(context, "target", "Onderzoek dit")
    assert len(context.session.handles) == 1
    assert context.session.handles[0].interrupted
    assert "target" in state.suppressed
