"""Experimental orchestration tests; no physical playback or VAD proof."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import aiohttp
from aiohttp import web
import pytest

ROOT = Path(__file__).parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lane_module = load("duplex_lane_under_test", ROOT / "experiments/native-duplex-v1/reply_lane.py")
client_module = load("duplex_native_client_under_test", ROOT / "deploy/home-assistant/custom_components/eva2_voice/client.py")
Binding = lane_module.HouseholdBinding
Lane = lane_module.HouseholdDuplexReplyLane
BINDING = Binding("assist_satellite.room", "a" * 32, "same-conversation", "nl")


class Output:
    def __init__(self):
        self.spoken = []
        self.invalid = set()
        self.heard = asyncio.Event()

    async def speak(self, *, generation, text):
        assert generation not in self.invalid
        self.spoken.append((generation, text))
        self.heard.set()

    async def interrupt(self, *, generation):
        self.invalid.add(generation)


def test_new_speech_stops_output_without_waiting_for_native_http_cleanup():
    async def run():
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests, interrupts = [], []

        async def respond(request):
            data = await request.json()
            requests.append(data)
            if data["text"] == "old":
                entered.set()
                await release.wait()
            return web.json_response({"request_id": data["request_id"],
                                      "reply": data["text"], "continue_conversation": True})

        async def interrupt(request):
            interrupts.append((request.match_info["id"], await request.json()))
            cleanup.set()
            await release.wait()
            return web.json_response({"status": "interrupted"})

        app = web.Application()
        app.router.add_post("/internal/v1/ha-voice/turns", respond)
        app.router.add_post("/internal/v1/ha-voice/turns/{id}/interrupt", interrupt)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                client = client_module.EvaVoiceClient(session, url=f"http://127.0.0.1:{port}",
                    token="synthetic", satellite_ids=frozenset({BINDING.satellite_id}))
                output = Output()
                lane = Lane(binding=BINDING, client=client, output=output)
                try:
                    first = await lane.speech_started()
                    await lane.submit_final(generation=first, text="old")
                    await asyncio.wait_for(entered.wait(), 2)
                    second = await asyncio.wait_for(lane.speech_started(), .5)
                    assert first in output.invalid and not release.is_set()
                    await asyncio.wait_for(cleanup.wait(), 2)
                    await lane.submit_final(generation=second, text="new")
                    await asyncio.wait_for(output.heard.wait(), 2)
                    assert output.spoken == [(second, "new")]
                    assert {r["conversation_id"] for r in requests} == {BINDING.conversation_id}
                    assert interrupts == [(requests[0]["request_id"], {
                        "satellite_id": BINDING.satellite_id, "device_id": BINDING.device_id})]
                finally:
                    release.set()
                    await lane.close()
                    await client.close()
        finally:
            release.set()
            await runner.cleanup()
    asyncio.run(run())


def test_late_cancel_resistant_answer_cannot_start_old_audio():
    async def run():
        entered, release, old_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Client:
            async def respond(self, **kwargs):
                if kwargs["text"] == "old":
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                    old_done.set()
                return SimpleNamespace(text=kwargs["text"], continue_conversation=True)
        output = Output()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        first = await lane.speech_started()
        await lane.submit_final(generation=first, text="old")
        await entered.wait()
        second = await lane.speech_started()
        await lane.submit_final(generation=second, text="new")
        await output.heard.wait()
        release.set()
        await old_done.wait()
        await lane.close()
        assert output.spoken == [(second, "new")]
    asyncio.run(run())


def test_stale_duplicate_transcripts_and_unconfirmed_stop_are_rejected():
    async def run():
        class Client:
            async def respond(self, **kwargs):
                return SimpleNamespace(text="answer", continue_conversation=True)
        class FailedOutput(Output):
            async def interrupt(self, **kwargs):
                raise RuntimeError("speaker not stopped")
        output = Output()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        one = await lane.speech_started()
        two = await lane.speech_started()
        with pytest.raises(ValueError, match="stale"):
            await lane.submit_final(generation=one, text="stale")
        await lane.submit_final(generation=two, text="new")
        with pytest.raises(ValueError, match="already"):
            await lane.submit_final(generation=two, text="duplicate")
        await lane.close()
        with pytest.raises(RuntimeError, match="closed"):
            await lane.speech_started()
        failed = Lane(binding=BINDING, client=Client(), output=FailedOutput())
        await failed.speech_started()
        with pytest.raises(RuntimeError, match="speaker not stopped"):
            await failed.speech_started()
        with pytest.raises(RuntimeError, match="closed"):
            await failed.submit_final(generation=2, text="unsafe next answer")
        with pytest.raises(RuntimeError, match="speaker not stopped"):
            await failed.close()
    asyncio.run(run())


def test_partial_playback_failure_invalidates_queued_audio_and_closes_lane():
    async def run():
        invalidated = asyncio.Event()
        class Client:
            async def respond(self, **kwargs):
                return SimpleNamespace(text="partial reply", continue_conversation=True)
        class PartialOutput(Output):
            async def speak(self, *, generation, text):
                self.spoken.append((generation, text))
                raise OSError("transport lost after accepting audio")
            async def interrupt(self, *, generation):
                await super().interrupt(generation=generation)
                invalidated.set()
        output = PartialOutput()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        generation = await lane.speech_started()
        await lane.submit_final(generation=generation, text="hello")
        await invalidated.wait()
        await lane.close()
        assert generation in output.invalid
        assert isinstance(lane.failure, OSError)
        with pytest.raises(RuntimeError, match="closed"):
            await lane.speech_started()
    asyncio.run(run())


@pytest.mark.parametrize("finish", ["success", "missing_vad", "stt_error", "cancel"])
def test_ha_stt_vad_interrupts_before_final_text_and_closes_on_failure(finish):
    import json
    from websockets.asyncio.server import serve
    from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput

    async def run():
        old_playing, interrupted = asyncio.Event(), asyncio.Event()
        vad_sent, release_final, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        source_closed = asyncio.Event()
        requests, interrupts = [], []

        class Client:
            async def respond(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(text=kwargs["text"], continue_conversation=True)

        class PlayingOutput(Output):
            async def speak(self, *, generation, text):
                if text == "old answer":
                    old_playing.set()
                    await asyncio.Event().wait()
                await super().speak(generation=generation, text=text)

            async def interrupt(self, *, generation):
                await super().interrupt(generation=generation)
                interrupts.append(generation)
                interrupted.set()

        async def audio():
            try:
                yield b"12"
                await asyncio.Event().wait()
            finally:
                source_closed.set()

        async def event(socket, kind, data=None):
            await socket.send(json.dumps({"id": 1, "type": "event", "event": {
                "type": kind, "data": data}}))

        async def handler(socket):
            try:
                await socket.send(json.dumps({"type": "auth_required"}))
                assert json.loads(await socket.recv())["access_token"] == "synthetic"
                await socket.send(json.dumps({"type": "auth_ok"}))
                request = json.loads(await socket.recv())
                assert request["start_stage"] == request["end_stage"] == "stt"
                await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
                await event(socket, "run-start", {"pipeline": "eva", "language": "nl",
                    "runner_data": {"stt_binary_handler_id": 7}})
                await event(socket, "stt-start", {"engine": "stt.home_assistant_cloud"})
                assert await socket.recv() == b"\x0712"
                if finish != "missing_vad":
                    await event(socket, "stt-vad-start", {"timestamp": 50})
                    # Duplicate provider notification must not cancel the new turn.
                    await event(socket, "stt-vad-start", {"timestamp": 50})
                vad_sent.set()
                await release_final.wait()
                if finish == "stt_error":
                    await event(socket, "error", {"code": "stt-no-text-recognized"})
                elif finish != "cancel":
                    await event(socket, "stt-end", {"stt_output": {"text": "new question"}})
                    await event(socket, "run-end")
                await socket.wait_closed()
            finally:
                disconnected.set()

        output = PlayingOutput()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        old = await lane.speech_started()
        await lane.submit_final(generation=old, text="old answer")
        await asyncio.wait_for(old_playing.wait(), 2)
        async with serve(handler, "127.0.0.1", 0) as server:
            adapter = HomeAssistantSpeechInput(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                token="synthetic", pipeline_id="eva",
                expected_stt_engine="stt.home_assistant_cloud", expected_pipeline_language="nl")
            listening = asyncio.create_task(lane_module.transcribe_new_turn(
                speech_input=adapter, lane=lane, chunks=audio()))
            try:
                await asyncio.wait_for(vad_sent.wait(), 2)
                if finish != "missing_vad":
                    await asyncio.wait_for(interrupted.wait(), 2)
                    assert interrupts == [old]
                    assert not listening.done() and not release_final.is_set()
                    assert len(requests) == 1  # No Eva request for partial speech.
                if finish == "cancel":
                    listening.cancel()
                release_final.set()
                if finish == "success":
                    generation = await asyncio.wait_for(listening, 2)
                    await asyncio.wait_for(output.heard.wait(), 2)
                    assert output.spoken == [(generation, "new question")]
                    assert [r["conversation_id"] for r in requests] == [BINDING.conversation_id] * 2
                    assert interrupts == [old]
                else:
                    with pytest.raises(asyncio.CancelledError if finish == "cancel" else RuntimeError):
                        await asyncio.wait_for(listening, 2)
                    assert len(requests) == 1 and old in output.invalid
                    with pytest.raises(RuntimeError, match="closed"):
                        await lane.speech_started()
                await asyncio.wait_for(disconnected.wait(), 2)
                assert source_closed.is_set()
            finally:
                release_final.set()
                if not listening.done():
                    listening.cancel()
                await asyncio.gather(listening, return_exceptions=True)
                await lane.close()
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("cancel_at", ["playback_failure", "speech_start", "close"])
def test_owned_stop_survives_cancelled_reply_or_cleanup(cancel_at):
    async def run():
        stop_entered, release_stop, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        played = asyncio.Event()
        class Client:
            async def respond(self, **kwargs):
                return SimpleNamespace(text="old", continue_conversation=True)
        class DelayedStop(Output):
            async def speak(self, **kwargs):
                played.set()
                if cancel_at == "playback_failure":
                    raise OSError("partial playback")
                await asyncio.Event().wait()
            async def interrupt(self, **kwargs):
                stop_entered.set()
                await release_stop.wait()
                stopped.set()
        lane = Lane(binding=BINDING, client=Client(), output=DelayedStop())
        generation = await lane.speech_started()
        await lane.submit_final(generation=generation, text="old")
        await played.wait()
        if cancel_at == "playback_failure":
            await stop_entered.wait()
            pending = asyncio.create_task(lane.close())
        else:
            pending = asyncio.create_task(lane.speech_started() if cancel_at == "speech_start" else lane.close())
            await stop_entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not stopped.is_set()
        closing = asyncio.create_task(lane.close())
        await asyncio.sleep(0)
        assert not closing.done()  # Closed admission cannot bypass the stop.
        release_stop.set()
        await closing
        assert stopped.is_set()
    asyncio.run(asyncio.wait_for(run(), 3))


def test_background_reply_failure_cancels_current_speech_input():
    async def run():
        entered, fail_reply, input_started, input_closed = (asyncio.Event() for _ in range(4))
        class Client:
            async def respond(self, **kwargs):
                entered.set()
                await fail_reply.wait()
                raise OSError("native client lost")
        class Input:
            async def transcribe(self, chunks, **kwargs):
                try:
                    input_started.set()
                    await asyncio.Event().wait()
                finally:
                    input_closed.set()
        output = Output()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        generation = await lane.speech_started()
        await lane.submit_final(generation=generation, text="old")
        await entered.wait()
        listening = asyncio.create_task(lane_module.transcribe_new_turn(
            speech_input=Input(), lane=lane, chunks=None))
        await input_started.wait()
        fail_reply.set()
        with pytest.raises(RuntimeError, match="reply failed"):
            await listening
        assert input_closed.is_set() and generation in output.invalid
        assert isinstance(lane.failure, OSError)
    asyncio.run(asyncio.wait_for(run(), 3))


def test_explicit_lane_close_cancels_open_speech_input():
    async def run():
        input_started, input_closed = asyncio.Event(), asyncio.Event()
        class Input:
            async def transcribe(self, chunks, **kwargs):
                try:
                    input_started.set()
                    await asyncio.Event().wait()
                finally:
                    input_closed.set()
        lane = Lane(binding=BINDING, client=None, output=Output())
        listening = asyncio.create_task(lane_module.transcribe_new_turn(
            speech_input=Input(), lane=lane, chunks=None))
        await input_started.wait()
        await lane.close()
        with pytest.raises(RuntimeError, match="lane closed"):
            await listening
        assert input_closed.is_set()
    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("failure_stage", ["client", "playback"])
def test_superseded_reply_failure_preserves_new_input_after_confirmed_stop(failure_stage):
    async def run():
        old_started, release_old, listening, release_text = (asyncio.Event() for _ in range(4))
        async def old_failure():
            old_started.set()
            try:
                await release_old.wait()
            except asyncio.CancelledError:
                await release_old.wait()
            raise OSError("superseded cleanup failed")
        class Client:
            async def respond(self, **kwargs):
                if kwargs["text"] == "old" and failure_stage == "client":
                    await old_failure()
                return SimpleNamespace(text=kwargs["text"], continue_conversation=True)
        class Playback(Output):
            async def speak(self, *, generation, text):
                if text == "old":
                    await old_failure()
                else:
                    await super().speak(generation=generation, text=text)
        class Input:
            async def transcribe(self, chunks, *, on_speech_start):
                await on_speech_start()
                listening.set()
                await release_text.wait()
                return "new"
        output = Playback()
        lane = Lane(binding=BINDING, client=Client(), output=output)
        old = await lane.speech_started()
        await lane.submit_final(generation=old, text="old")
        await old_started.wait()
        old_worker = lane._worker
        next_input = asyncio.create_task(lane_module.transcribe_new_turn(
            speech_input=Input(), lane=lane, chunks=None))
        await listening.wait()
        assert old in output.invalid
        release_old.set()
        await asyncio.gather(old_worker, return_exceptions=True)
        assert not lane.closed and lane.failure is None
        assert not next_input.done()
        release_text.set()
        new = await next_input
        await output.heard.wait()
        assert output.spoken == [(new, "new")]
        await lane.close()
    asyncio.run(asyncio.wait_for(run(), 3))
