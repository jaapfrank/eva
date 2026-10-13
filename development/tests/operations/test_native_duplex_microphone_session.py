"""Continuous capture/HA-boundary proofs; no physical microphone/echo claim."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve

from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput, VoiceMediaError


ROOT = Path(__file__).parents[3]


def load(name):
    spec = importlib.util.spec_from_file_location(
        "session_test_" + name, ROOT / f"experiments/native-duplex-v1/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mic_module = load("microphone_session")
lane_module = load("reply_lane")
Binding = lane_module.HouseholdBinding
BINDING = Binding("assist_satellite.room", "a" * 32, "household-conversation", "nl")


class Detector:
    def Process10ms(self, frame):
        return .95 if frame[0] else .0


def frame(marker):
    return bytes([marker, 0]) * 160


class Source:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self):
        self.closed.set()

    def feed(self, *markers):
        self.queue.put_nowait(b"".join(frame(marker) for marker in markers))


def microphone(source, **kwargs):
    return mic_module.BufferedMicrophone(source, detector_factory=Detector,
        speech_frames=1, pre_roll_frames=2, **kwargs)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


class Output:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()
        self.stopped = []

    async def speak(self, *, generation, text):
        self.started.put_nowait((generation, text))
        await self.release.wait()

    async def interrupt(self, *, generation):
        self.stopped.append(generation)


class Client:
    def __init__(self, keep_listening=True):
        self.requests = []
        self.keep_listening = keep_listening

    async def respond(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=kwargs["text"], continue_conversation=self.keep_listening)


class HA:
    def __init__(self):
        self.calls = []

    async def transcribe(self, chunks, *, on_speech_end):
        received = [await anext(chunks) for _ in range(5)]
        self.calls.append(received)
        await on_speech_end(40)
        await chunks.aclose()
        return "vraag " + str(len(self.calls))


def test_ha_real_websocket_boundary_preserves_already_transmitted_next_question():
    async def run():
        source = Source()
        captured, finals = [], []
        final_release = asyncio.Event()

        async def event(socket, kind, data=None):
            await socket.send(json.dumps({"id": 1, "type": "event", "event": {
                "type": kind, "data": data or {}}}))

        async def handler(socket):
            number = len(captured)
            captured.append([])
            await socket.send(json.dumps({"type": "auth_required"}))
            assert json.loads(await socket.recv())["access_token"] == "synthetic"
            await socket.send(json.dumps({"type": "auth_ok"}))
            request = json.loads(await socket.recv())
            assert request["start_stage"] == request["end_stage"] == "stt"
            await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
            await event(socket, "run-start", {"runner_data": {"stt_binary_handler_id": 7}})
            await event(socket, "stt-start")
            for _ in range(8 if number == 0 else 4):
                packet = await socket.recv()
                assert packet[0] == 7
                captured[number].append(packet[1:])
            await event(socket, "stt-vad-start", {"timestamp": 0})
            await event(socket, "stt-vad-end", {"timestamp": 40 if number == 0 else 20})
            if number == 0:
                await final_release.wait()  # Later speech is captured before this final.
            await event(socket, "stt-end", {"stt_output": {"text": f"vraag {number}"}})
            await event(socket, "run-end")
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            adapter = HomeAssistantSpeechInput(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                token="synthetic", pipeline_id="test-pipeline")
            async with microphone(source) as mic:
                source.feed(1, 0, 0, 0, 2, 0, 0, 0)
                first = await asyncio.wait_for(mic.next_utterance(), 1)
                async def transcribe(lease):
                    async def end(timestamp):
                        await mic.finish(lease, timestamp)
                    result = await adapter.transcribe(lease, on_speech_end=end)
                    finals.append(result)
                one = asyncio.create_task(transcribe(first))
                second = await asyncio.wait_for(mic.next_utterance(), 2)
                assert not one.done() and not source.closed.is_set()
                await transcribe(second)
                assert not source.closed.is_set()
                final_release.set()
                await one
                assert captured == [
                    [frame(x) for x in (1, 0, 0, 0, 2, 0, 0, 0)],
                    [frame(x) for x in (2, 0, 0, 0)],
                ]
                assert finals == ["vraag 1", "vraag 0"]
        assert source.closed.is_set()
    asyncio.run(run())


def test_long_reply_has_no_idle_stt_and_new_question_stops_it_without_wakeword():
    async def run():
        source, ha, output, client = Source(), HA(), Output(), Client(False)
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=client, output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=ha, lane=lane, idle_seconds=.03))
        source.feed(1, 0, 0, 0, 0)
        assert await asyncio.wait_for(output.started.get(), 1) == (1, "vraag 1")
        await asyncio.sleep(.08)  # More than two listening windows during playback.
        assert len(ha.calls) == 1 and not session.done() and not output.stopped
        source.feed(2, 0, 0, 0, 0)
        assert await asyncio.wait_for(output.started.get(), 1) == (2, "vraag 2")
        assert 1 in output.stopped
        assert ha.calls[1][0:2] == [frame(0), frame(2)]  # retained pre-roll + first word
        output.release.set()
        await asyncio.wait_for(session, 1)
        assert source.closed.is_set() and lane.closed
        assert {call["conversation_id"] for call in client.requests} == {BINDING.conversation_id}
    asyncio.run(run())


def test_late_old_stt_final_cannot_answer_after_a_new_onset():
    async def run():
        source, output, client = Source(), Output(), Client(False)
        release, cancelled = asyncio.Event(), asyncio.Event()
        class SlowHA(HA):
            async def transcribe(self, chunks, *, on_speech_end):
                answer = await super().transcribe(chunks, on_speech_end=on_speech_end)
                if answer == "vraag 1":
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        await release.wait()
                return answer
        ha = SlowHA()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=client, output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=ha, lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await until(lambda: len(ha.calls) == 1)
        source.feed(2, 0, 0, 0, 0)
        assert await asyncio.wait_for(output.started.get(), 1) == (2, "vraag 2")
        await asyncio.wait_for(cancelled.wait(), 1)
        release.set()
        output.release.set()
        await asyncio.wait_for(session, 1)
        assert [r["text"] for r in client.requests] == ["vraag 2"]
    asyncio.run(run())


def test_old_false_playback_completion_cannot_close_new_listening_turn():
    async def run():
        output, client = Output(), Client(False)
        old_release, new_started = asyncio.Event(), asyncio.Event()
        class ResistantOutput(Output):
            async def speak(self, *, generation, text):
                if generation == 1:
                    new_started.set()
                    try:
                        await old_release.wait()
                    except asyncio.CancelledError:
                        await old_release.wait()
                else:
                    await self.release.wait()
        output = ResistantOutput()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=client, output=output)
        await lane.submit_final(generation=await lane.speech_started(), text="old")
        await new_started.wait()
        two = await lane.speech_started()
        completed = asyncio.create_task(lane.playback_completed())
        old_release.set()
        await asyncio.sleep(.02)
        assert not completed.done() and not lane.closed
        await lane.submit_final(generation=two, text="new")
        output.release.set()
        result = await asyncio.wait_for(completed, 1)
        assert result.generation == two and result.continue_conversation is False
        await lane.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["overrun", "disconnect", "bad-frame", "detector"])
def test_capture_failure_stops_playback_and_closes_owned_source(failure):
    async def run():
        source, ha, output, client = Source(), HA(), Output(), Client()
        mic = microphone(source, max_frames=20)
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=client, output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=mic, speech_input=ha, lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await asyncio.wait_for(output.started.get(), 1)
        if failure == "overrun":
            source.feed(*([2] * 30))
        elif failure == "disconnect":
            source.queue.put_nowait(None)
        elif failure == "bad-frame":
            source.queue.put_nowait(b"x")
        else:
            mic._detector.Process10ms = lambda _: float("nan")
            source.feed(2)
        with pytest.raises(VoiceMediaError):
            await asyncio.wait_for(session, 1)
        assert 1 in output.stopped and source.closed.is_set() and lane.closed
    asyncio.run(run())


@pytest.mark.parametrize("timestamp", [True, -10, 15, 0, 1000])
def test_invalid_or_unoffered_boundary_never_discards_audio(timestamp):
    async def run():
        source = Source()
        async with microphone(source) as mic:
            source.feed(1, 0, 0, 0)
            lease = await asyncio.wait_for(mic.next_utterance(), 1)
            assert await anext(lease) == frame(1)
            with pytest.raises(VoiceMediaError):
                await mic.finish(lease, timestamp)
            assert await anext(lease) == frame(0)
    asyncio.run(run())


def test_session_cancel_stops_audio_before_slow_microphone_cleanup():
    async def run():
        cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
        class SlowSource(Source):
            async def aclose(self):
                cleanup_entered.set()
                await cleanup_release.wait()
                await super().aclose()
        source, output = SlowSource(), Output()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(), output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=HA(), lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await asyncio.wait_for(output.started.get(), 1)
        session.cancel()
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        assert output.stopped and not session.done()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await session
        assert source.closed.is_set()
    asyncio.run(run())


def test_old_stt_cleanup_is_not_cancelled_twice_when_session_closes():
    async def run():
        cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
        cleanup_interrupted = []
        class SlowCleanupHA(HA):
            async def transcribe(self, chunks, *, on_speech_end):
                answer = await super().transcribe(chunks, on_speech_end=on_speech_end)
                if answer == "vraag 1":
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleanup_entered.set()
                        try:
                            await cleanup_release.wait()
                        except asyncio.CancelledError:
                            cleanup_interrupted.append(True)
                            raise
                return answer
        source, output, ha = Source(), Output(), SlowCleanupHA()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(), output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=ha, lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await until(lambda: len(ha.calls) == 1)
        source.feed(2, 0, 0, 0, 0)
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        await asyncio.wait_for(output.started.get(), 1)
        session.cancel()
        await source.closed.wait()
        session.cancel()  # Parent cancellation during owned cleanup is harmless.
        await asyncio.sleep(.01)
        assert not cleanup_interrupted and not session.done()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await session
        assert not cleanup_interrupted
    asyncio.run(run())


def test_concurrent_microphone_close_callers_cannot_cancel_owned_cleanup():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowSource(Source):
            async def aclose(self):
                entered.set()
                await release.wait()
                await super().aclose()
        source = SlowSource()
        mic = microphone(source)
        await mic.__aenter__()
        source.feed(0)
        await until(lambda: mic._next > 0)
        one = asyncio.create_task(mic.close())
        await entered.wait()
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        two = asyncio.create_task(mic.close())
        await asyncio.sleep(.01)
        assert not two.done() and not source.closed.is_set()
        release.set()
        await asyncio.wait_for(two, 1)
        assert source.closed.is_set()
    asyncio.run(run())


def test_already_detected_onset_has_priority_before_waiter_delivery():
    async def run():
        deliver = asyncio.Event()
        class DelayedMicrophone(mic_module.BufferedMicrophone):
            calls = 0
            async def next_utterance(self):
                self.calls += 1
                if self.calls == 2:
                    await deliver.wait()
                return await super().next_utterance()
        source, output = Source(), Output()
        mic = DelayedMicrophone(source, detector_factory=Detector, speech_frames=1, pre_roll_frames=2)
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(False), output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=mic, speech_input=HA(), lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await asyncio.wait_for(output.started.get(), 1)
        source.feed(2, 0, 0, 0, 0)
        await until(lambda: mic.onset_pending)
        output.release.set()  # Old answer says the conversation is over.
        await asyncio.sleep(.01)
        assert not session.done() and not lane.closed
        deliver.set()
        assert await asyncio.wait_for(output.started.get(), 1) == (2, "vraag 2")
        await asyncio.wait_for(session, 1)
    asyncio.run(run())


@pytest.mark.parametrize("after_reply", [False, True])
def test_idle_closes_only_before_speech_or_after_confirmed_playback(after_reply):
    async def run():
        source, output, ha = Source(), Output(), HA()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(True), output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=ha, lane=lane, idle_seconds=.02))
        if after_reply:
            source.feed(1, 0, 0, 0, 0)
            await asyncio.wait_for(output.started.get(), 1)
            output.release.set()
        await asyncio.wait_for(session, 1)
        assert len(ha.calls) == int(after_reply)
        assert source.closed.is_set() and lane.closed
    asyncio.run(run())


def test_capture_failure_stops_audio_before_slow_source_cleanup_finishes():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowSource(Source):
            async def aclose(self):
                entered.set()
                await release.wait()
                await super().aclose()
        source, output = SlowSource(), Output()
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(), output=output)
        session = asyncio.create_task(mic_module.run_conversation(
            microphone=microphone(source), speech_input=HA(), lane=lane))
        source.feed(1, 0, 0, 0, 0)
        await asyncio.wait_for(output.started.get(), 1)
        source.queue.put_nowait(b"x")
        await asyncio.wait_for(entered.wait(), 1)
        await until(lambda: bool(output.stopped))
        assert not release.is_set() and not session.done()
        release.set()
        with pytest.raises(VoiceMediaError):
            await asyncio.wait_for(session, 1)
    asyncio.run(run())


@pytest.mark.parametrize("started", [False, True])
def test_closing_before_first_capture_still_closes_the_source(started):
    async def run():
        source = Source()
        mic = microphone(source)
        if started:
            await mic.__aenter__()
            mic._task.cancel()  # Cancellation before the coroutine's first step.
        await mic.close()
        assert source.closed.is_set()
    asyncio.run(run())


def test_source_cleanup_failure_is_reported_to_the_session_owner():
    async def run():
        class FailedSource(Source):
            async def aclose(self):
                raise OSError("synthetic device cleanup failure")
        lane = lane_module.HouseholdDuplexReplyLane(binding=BINDING, client=Client(), output=Output())
        with pytest.raises(VoiceMediaError, match="cleanup failed"):
            await mic_module.run_conversation(microphone=microphone(FailedSource()),
                                             speech_input=HA(), lane=lane, idle_seconds=.01)
        assert lane.closed
    asyncio.run(run())
