import asyncio
from types import SimpleNamespace

import pytest
from livekit import rtc
from livekit.agents import stt, utils, vad

from eva.voice_service.media import HomeAssistantSTT
from eva.adapters.connectors.home_assistant_voice import NoSpeechRecognized


class Detector:
    def __init__(self):
        self.events = utils.aio.Chan()

    def stream(self):
        return self

    def push_frame(self, frame):
        pass

    def flush(self):
        pass

    def end_input(self):
        self.events.close()

    async def aclose(self):
        self.events.close()

    def __aiter__(self):
        return self.events.__aiter__()

    def emit(self, kind, frames=()):
        self.events.send_nowait(SimpleNamespace(
            type=kind, frames=frames, timestamp=1, silence_duration=.45, inference_duration=0))


def frame(value):
    return rtc.AudioFrame(bytes([value, 0]) * 160, 16000, 1, 160)


async def collect(stream, events):
    async for event in stream:
        events.append(event)


def test_pcm_reaches_provider_before_end_and_final_waits_for_complete_segment():
    async def run():
        detector = Detector()
        arrived = asyncio.Event()
        audio = bytearray()

        async def transcribe(chunks):
            async for chunk in chunks:
                audio.extend(chunk)
                arrived.set()
            return "Wacht, ik bedoel morgen."

        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector).stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        await asyncio.wait_for(arrived.wait(), 1)
        assert not any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        detector.emit(vad.VADEventType.INFERENCE_DONE, [frame(2)])
        detector.emit(vad.VADEventType.INFERENCE_DONE, [frame(3)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        stream.end_input()
        await asyncio.wait_for(consumer, 1)
        assert bytes(audio) == bytes(frame(1).data) + bytes(frame(2).data) + bytes(frame(3).data)
        assert [e.type for e in events] == [stt.SpeechEventType.START_OF_SPEECH,
            stt.SpeechEventType.END_OF_SPEECH, stt.SpeechEventType.FINAL_TRANSCRIPT]
        assert events[-1].alternatives[0].text == "Wacht, ik bedoel morgen."
        await stream.aclose()
    asyncio.run(run())


def test_next_utterance_is_preserved_while_first_final_waits():
    async def run():
        detector = Detector()
        first_complete = asyncio.Event()
        release = asyncio.Event()
        captured = []

        async def transcribe(chunks):
            audio = b"".join([chunk async for chunk in chunks])
            captured.append(audio)
            if len(captured) == 1:
                first_complete.set()
                await release.wait()
            return str(len(captured))

        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector).stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        await asyncio.wait_for(first_complete.wait(), 1)
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(2)])
        detector.emit(vad.VADEventType.INFERENCE_DONE, [frame(3)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        release.set()
        stream.end_input()
        await asyncio.wait_for(consumer, 1)
        assert captured == [bytes(frame(1).data), bytes(frame(2).data) + bytes(frame(3).data)]
        assert [e.alternatives[0].text for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT] == ["1", "2"]
        await stream.aclose()
    asyncio.run(run())


def test_early_provider_final_is_not_admitted():
    async def run():
        detector = Detector()
        async def transcribe(chunks):
            return "Onvolledige opdracht"
        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector).stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        with pytest.raises(RuntimeError, match="before consuming"):
            await asyncio.wait_for(consumer, 1)
        assert not any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        await stream.aclose()
    asyncio.run(run())


def test_close_cancels_provider_and_rejects_late_input():
    async def run():
        detector = Detector()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def transcribe(chunks):
            try:
                async for chunk in chunks:
                    started.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector).stream()
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(stream.aclose(), 1)
        assert cancelled.is_set()
        with pytest.raises(RuntimeError, match="closed"):
            stream.push_frame(frame(2))
    asyncio.run(run())


def test_input_backlog_has_a_bound_before_native_queue():
    async def run():
        detector = Detector()
        stream = HomeAssistantSTT(SimpleNamespace(), vad=detector).stream()
        huge = rtc.AudioFrame(bytes(16000 * 2 * 7), 16000, 1, 16000 * 7)
        with pytest.raises(RuntimeError, match="backlog"):
            stream.push_frame(huge)
        await stream.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["overrun", "revoked"])
def test_native_session_input_failure_cancels_waiting_provider_without_late_final(fault):
    from livekit.agents import Agent, AgentSession
    from livekit.agents.voice import io

    async def run():
        detector = Detector()
        ready, waiting, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active = True
        streams, finals = [], []
        class Input(io.AudioInput):
            def __init__(self):
                super().__init__(label="synthetic input")
                self.frames = utils.aio.Chan()
            async def __anext__(self):
                return await self.frames.__anext__()
        async def transcribe(chunks):
            try:
                async for chunk in chunks:
                    pass
                waiting.set()
                await asyncio.Event().wait()
                return "Must never be dispatched"
            finally:
                cancelled.set()
        def check():
            if not active:
                raise RuntimeError("authority revoked")
        class MeasuredSTT(HomeAssistantSTT):
            def stream(self, **kwargs):
                stream = super().stream(**kwargs)
                streams.append(stream)
                ready.set()
                return stream
        adapter = MeasuredSTT(SimpleNamespace(transcribe=transcribe), check, vad=detector)
        microphone = Input()
        async with AgentSession(stt=adapter, turn_detection="stt") as session:
            session.input.audio = microphone
            session.on("user_input_transcribed", lambda event: finals.append(event))
            await session.start(Agent(instructions="Transport test only; no tools or model."))
            await asyncio.wait_for(ready.wait(), 2)
            detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
            detector.emit(vad.VADEventType.END_OF_SPEECH)
            await asyncio.wait_for(waiting.wait(), 2)
            if fault == "revoked":
                active = False
                microphone.frames.send_nowait(frame(1))
            else:
                microphone.frames.send_nowait(rtc.AudioFrame(bytes(16000 * 2 * 7), 16000, 1, 16000 * 7))
            await asyncio.wait_for(cancelled.wait(), 2)
            await asyncio.wait_for(asyncio.gather(streams[0]._task, return_exceptions=True), 2)
            assert streams[0]._task.done()
            assert finals == []
            microphone.frames.close()
    asyncio.run(run())


def test_provider_failure_during_speech_does_not_wait_for_silence():
    async def run():
        detector = Detector()
        async def transcribe(chunks):
            await anext(chunks)
            raise RuntimeError("provider unavailable")
        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector).stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await asyncio.wait_for(consumer, 1)
        assert not any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        await stream.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("streaming", [True, False])
def test_empty_recognition_keeps_same_stream_for_next_utterance(streaming):
    async def run():
        detector = Detector()
        captured = []
        empty_finished = asyncio.Event()

        async def transcribe(chunks):
            captured.append(b"".join([chunk async for chunk in chunks]))
            if len(captured) == 1:
                empty_finished.set()
                raise NoSpeechRecognized("no recognized words")
            return "Hallo Eva"

        adapter = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), vad=detector if streaming else None)
        if not streaming:
            adapter = stt.StreamAdapter(stt=adapter, vad=detector)
        stream = adapter.stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        detector.emit(vad.VADEventType.END_OF_SPEECH, [frame(1)])
        await asyncio.wait_for(empty_finished.wait(), 1)
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(2)])
        detector.emit(vad.VADEventType.END_OF_SPEECH, [frame(2)])
        stream.end_input()
        await asyncio.wait_for(consumer, 1)
        assert len(captured) == 2 and all(captured)
        if streaming:
            assert captured == [bytes(frame(1).data), bytes(frame(2).data)]
        assert [e.alternatives[0].text for e in events
                if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT] == ["Hallo Eva"]
        await stream.aclose()
    asyncio.run(run())


def test_empty_recognition_cannot_bypass_complete_input_or_current_authority():
    async def run(early):
        detector = Detector()
        active = True

        async def transcribe(chunks):
            nonlocal active
            if not early:
                async for _ in chunks:
                    pass
                active = False
            raise NoSpeechRecognized("no recognized words")

        def check():
            if not active:
                raise RuntimeError("authority revoked")

        stream = HomeAssistantSTT(SimpleNamespace(transcribe=transcribe), check, vad=detector).stream()
        events = []
        consumer = asyncio.create_task(collect(stream, events))
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        with pytest.raises(RuntimeError, match="before consuming" if early else "authority revoked"):
            await asyncio.wait_for(consumer, 1)
        assert not any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        await stream.aclose()
    asyncio.run(run(True))
    asyncio.run(run(False))
