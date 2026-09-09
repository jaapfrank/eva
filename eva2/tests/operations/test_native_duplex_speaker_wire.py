import asyncio
from contextlib import asynccontextmanager
import importlib
from functools import wraps
from pathlib import Path
import struct
import sys
import random
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

EXPERIMENT = Path(__file__).resolve().parents[2] / "experiments/native-duplex-v1"
sys.path.insert(0, str(EXPERIMENT))
wire = importlib.import_module("speaker_wire")
Packet, Kind = wire.Packet, wire.Kind


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class SpeakerPeer:
    """Protocol peer with explicitly simulated playback, not a hardware oracle."""
    def __init__(self):
        self.finished = asyncio.Event()
        self.stopped = asyncio.Event()
        self.end_received = asyncio.Event()
        self.stop_received = asyncio.Event()
        self.begin_received = asyncio.Event()
        self.frames = {}
        self.packets = []
        self.corrupt_session = False
        self.wrong_finished_count = False
        self.connection = None
        self.tasks = set()

    async def handle(self, socket):
        self.connection = socket
        async def receipt(packet, kind, payload=b""):
            session = b"foreign!" if self.corrupt_session else packet.session
            await socket.send(Packet(kind, session, packet.generation, packet.sequence, payload).encode())

        async def complete(packet, stop=False):
            await (self.stopped if stop else self.finished).wait()
            count = self.frames.get(packet.generation, 0)
            if self.wrong_finished_count and not stop:
                count += 1
            await receipt(packet, Kind.STOPPED if stop else Kind.FINISHED, struct.pack("!I", count))

        try:
            async for raw in socket:
                p = Packet.decode(raw)
                self.packets.append(p)
                if p.kind == Kind.OPEN:
                    await receipt(p, Kind.OPENED)
                elif p.kind == Kind.BEGIN:
                    self.begin_received.set()
                    await receipt(p, Kind.BEGUN)
                elif p.kind == Kind.AUDIO:
                    self.frames[p.generation] = self.frames.get(p.generation, 0) + len(p.payload)//2
                    await receipt(p, Kind.ACCEPTED, struct.pack("!I", self.frames[p.generation]))
                elif p.kind in {Kind.END, Kind.STOP}:
                    (self.end_received if p.kind == Kind.END else self.stop_received).set()
                    task = asyncio.create_task(complete(p, p.kind == Kind.STOP))
                    self.tasks.add(task)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)


async def pcm(text):
    yield bytes(960)
    yield bytes(200)


@asynccontextmanager
async def opened(peer, *, source=pcm, timeout=0.5):
    async with serve(peer.handle, "127.0.0.1", 0, max_size=2048, compression=None) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}", proxy=None, max_size=2048, compression=None) as socket:
            output = wire.FramedSpeakerOutput(socket=socket, pcm=source, receipt_timeout=timeout)
            await output.start()
            try:
                yield output
            finally:
                peer.stopped.set()
                try:
                    await output.close()
                except Exception:
                    pass  # failures deliberately exercised below


@run_async
async def test_acceptance_does_not_finish_playback():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        task = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(peer.end_received.wait(), 1)
        assert peer.frames == {1: 580} and not task.done()
        peer.finished.set()
        await task


@run_async
async def test_playback_duration_extends_finish_deadline_but_not_stop():
    peer = SpeakerPeer()
    async def longer(text):
        for _ in range(20):  # 400 ms of accepted audio, possibly still queued
            yield bytes(960)
    async with opened(peer, source=longer, timeout=.1) as output:
        task = asyncio.create_task(output.speak(generation=1, text="langere spraak"))
        await asyncio.wait_for(peer.end_received.wait(), 1)
        await asyncio.sleep(.15)
        assert not task.done()  # longer than the control deadline, shorter than playback
        peer.finished.set()
        await task
        with pytest.raises(wire.WireError):
            await asyncio.wait_for(output.interrupt(generation=1), .3)
        assert peer.stop_received.is_set()  # unconfirmed STOP wasn't given 400 ms extra


@run_async
async def test_missing_playback_receipt_still_expires():
    peer = SpeakerPeer(); peer.stopped.set()
    async with opened(peer, timeout=.02) as output:
        with pytest.raises(wire.WireError):
            await asyncio.wait_for(output.speak(generation=1, text="geen bevestiging"), .5)


@run_async
async def test_retired_receipt_budget_cannot_be_flooded():
    class Socket:
        async def send(self, raw):
            pass
        async def recv(self):
            return Packet(Kind.OPENED, b"retired!").encode()
        async def close(self):
            pass
    output = wire.FramedSpeakerOutput(socket=Socket(), pcm=pcm, retired_sessions=(b"retired!",))
    with pytest.raises(wire.WireError):
        await asyncio.wait_for(output.start(), .5)
    assert output._retired_receipts == 8


@run_async
async def test_incompressible_pcm_uses_bounded_binary_transport():
    peer = SpeakerPeer()
    peer.finished.set()
    expected = random.Random(761).randbytes(960)

    async def noise(text):
        yield expected

    async with opened(peer, source=noise) as output:
        await output.speak(generation=1, text="transportproef")
        received = [p.payload for p in peer.packets if p.kind == Kind.AUDIO]
        assert received == [expected]


@run_async
async def test_cancelled_caller_cannot_cancel_owned_stop_or_reopen_old_answer():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        reply = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(peer.end_received.wait(), 1)
        stop = asyncio.create_task(output.interrupt(generation=1))
        await asyncio.wait_for(peer.stop_received.wait(), 1)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert not reply.done()
        peer.finished.set()  # deliberately late old completion cannot undo stop
        with pytest.raises((wire.PlaybackInterrupted, wire.WireError)):
            await output.speak(generation=1, text="laat oud antwoord")
        peer.stopped.set()
        with pytest.raises(wire.PlaybackInterrupted):
            await reply
        await output.interrupt(generation=1)
        await output.speak(generation=2, text="nieuw antwoord")
        assert sum(p.kind == Kind.STOP and p.generation == 1 for p in peer.packets) == 1


@run_async
async def test_new_answer_waits_for_actual_old_stop_receipt():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        stop = asyncio.create_task(output.interrupt(generation=1))
        await asyncio.wait_for(peer.stop_received.wait(), 1)
        new = asyncio.create_task(output.speak(generation=2, text="nieuw"))
        await asyncio.sleep(0)
        assert not any(p.kind == Kind.BEGIN for p in peer.packets)
        peer.stopped.set()
        await stop
        await asyncio.wait_for(peer.end_received.wait(), 1)
        peer.finished.set()
        await new


@run_async
async def test_stop_timeout_fails_connection_and_prevents_new_audio():
    peer = SpeakerPeer()
    async with opened(peer, timeout=0.03) as output:
        with pytest.raises(wire.WireError, match="not confirmed"):
            await output.interrupt(generation=1)
        with pytest.raises(wire.WireError):
            await output.speak(generation=2, text="nieuw")
        assert not any(p.kind == Kind.BEGIN for p in peer.packets)


@run_async
async def test_foreign_session_receipt_fails_closed():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        peer.corrupt_session = True
        with pytest.raises(wire.WireError):
            await output.speak(generation=1, text="antwoord")
        assert not any(p.kind == Kind.AUDIO for p in peer.packets)


@run_async
async def test_incomplete_playback_count_is_not_success():
    peer = SpeakerPeer()
    peer.finished.set(); peer.stopped.set(); peer.wrong_finished_count = True
    async with opened(peer) as output:
        with pytest.raises(wire.WireError, match="played frame count differs"):
            await output.speak(generation=1, text="antwoord")
        assert any(p.kind == Kind.STOP for p in peer.packets)


@run_async
async def test_simultaneous_answers_are_rejected():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        first = asyncio.create_task(output.speak(generation=1, text="eerste"))
        await asyncio.wait_for(peer.end_received.wait(), 1)
        with pytest.raises(wire.WireError, match="still active"):
            await output.speak(generation=2, text="tweede")
        peer.finished.set()
        await first


@pytest.mark.parametrize("operation", ["interrupt", "close"])
@run_async
async def test_stop_owns_and_closes_stalled_synthesis(operation):
    peer = SpeakerPeer()
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def slow(text):
        try:
            started.set()
            await asyncio.Event().wait()
            yield bytes(960)
        finally:
            cleaned.set()

    async with opened(peer, source=slow) as output:
        task = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(started.wait(), 1)
        peer.stopped.set()
        if operation == "close":
            await output.close()
        else:
            await output.interrupt(generation=1)
        assert cleaned.is_set()
        with pytest.raises(wire.PlaybackInterrupted):
            await task


@run_async
async def test_cancelled_close_caller_preserves_receipt_reader_until_stop():
    peer = SpeakerPeer()
    async with opened(peer) as output:
        reply = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(peer.end_received.wait(), 1)
        close = asyncio.create_task(output.close())
        await asyncio.wait_for(peer.stop_received.wait(), 1)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert not output._reader.done()
        peer.stopped.set()
        await output.close()
        with pytest.raises(wire.PlaybackInterrupted):
            await reply


@run_async
async def test_disconnect_during_stalled_synthesis_closes_source_without_external_close():
    peer = SpeakerPeer()
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def slow(text):
        try:
            started.set()
            await asyncio.Event().wait()
            yield bytes(960)
        finally:
            cleaned.set()

    async with opened(peer, source=slow) as output:
        reply = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(started.wait(), 1)
        await peer.connection.close()
        with pytest.raises(wire.WireError):
            await asyncio.wait_for(reply, 1)
        assert cleaned.is_set() and not output._open


@run_async
async def test_repeated_generations_do_not_recancel_source_cleanup():
    peer = SpeakerPeer()
    started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

    async def slow(text):
        try:
            started.set()
            await asyncio.Event().wait()
            yield bytes(960)
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    async with opened(peer, source=slow) as output:
        reply = asyncio.create_task(output.speak(generation=1, text="antwoord"))
        await asyncio.wait_for(started.wait(), 1)
        peer.stopped.set()
        first = asyncio.create_task(output.interrupt(generation=1))
        await asyncio.wait_for(cleaning.wait(), 1)
        second = asyncio.create_task(output.interrupt(generation=2))
        await asyncio.sleep(0)
        release.set()
        await first; await second
        assert cleaned.is_set()
        with pytest.raises(wire.PlaybackInterrupted):
            await reply


@run_async
async def test_reply_lane_stops_old_output_and_runs_next_generation():
    lane_module = importlib.import_module("reply_lane")
    calls = []

    class Client:
        async def respond(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="antwoord", continue_conversation=True)

    peer = SpeakerPeer()
    async with opened(peer) as output:
        binding = lane_module.HouseholdBinding("assist_satellite.test", "test-device", "test-conversation", "nl-NL")
        lane = lane_module.HouseholdDuplexReplyLane(binding=binding, client=Client(), output=output)
        first = await lane.speech_started()
        await lane.submit_final(generation=first, text="eerste vraag")
        await asyncio.wait_for(peer.end_received.wait(), 1)
        next_turn = asyncio.create_task(lane.speech_started())
        await asyncio.wait_for(peer.stop_received.wait(), 1)
        assert not next_turn.done()
        peer.stopped.set()
        second = await next_turn
        assert second == 2 and not lane.closed
        peer.end_received.clear()
        await lane.submit_final(generation=second, text="nieuwe vraag")
        await asyncio.wait_for(peer.end_received.wait(), 1)
        assert not lane.closed
        peer.finished.set()
        await lane.close()
        assert [call["text"] for call in calls] == ["eerste vraag", "nieuwe vraag"]
        assert all(call["conversation_id"] == "test-conversation" for call in calls)
        assert peer.frames.keys() == {1, 2}


@pytest.mark.parametrize("mutation", [
    lambda b: b[:10], lambda b: b+b"x", lambda b: b"bad!"+b[4:],
    lambda b: b[:5]+b"x"+b[6:], lambda b: b[:4]+bytes([77])+b[5:],
    lambda b: b[:8]+bytes(8)+b[16:],
])
def test_malformed_records_rejected(mutation):
    raw = Packet(Kind.OPEN, b"session1").encode()
    with pytest.raises(wire.WireError):
        Packet.decode(mutation(raw))
