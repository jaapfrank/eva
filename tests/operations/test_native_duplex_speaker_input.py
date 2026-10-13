"""Real authenticated network; device/microphone/playback are explicit doubles."""
import asyncio
import struct
from types import SimpleNamespace

import pytest

from test_native_duplex_speaker_endpoint import tls_pair, configured, client, wait_released
from test_native_duplex_speaker_wire import run_async, wire
from speaker_session import HouseholdSpeakerSession

Packet, Kind = wire.Packet, wire.Kind


class NativePeer:
    def __init__(self):
        self.opened = asyncio.Event()
        self.captures = asyncio.Queue()
        self.answers = asyncio.Queue()
        self.packets = []
        self.epoch = self.sequence = 0
        self.socket = None
        self.session = None
        self.stop_gate = self.capture_end_gate = None
        self.receipts = set()
        self.bad_stop_count = False

    async def send(self, kind, *, epoch=None, sequence=None, payload=b""):
        await self.socket.send(Packet(kind, self.session,
            self.epoch if epoch is None else epoch,
            self.sequence if sequence is None else sequence, payload).encode())

    async def wake(self):
        await self.opened.wait()
        await self.send(Kind.WAKE, epoch=0, sequence=0)

    async def question(self, marker):
        for value in [marker] * 6 + [0] * 4:
            self.sequence += 1
            await self.send(Kind.MICROPHONE, payload=bytes([value, 0]) * 320)

    async def handle(self, socket):
        self.socket = socket
        async for raw in socket:
            p = Packet.decode(raw)
            self.packets.append(p)
            if p.kind == Kind.OPEN:
                self.session = p.session
                await self.send(Kind.OPENED, epoch=0, sequence=0)
                self.opened.set()
            elif p.kind == Kind.CAPTURE:
                self.epoch, self.sequence = p.generation, 0
                await self.send(Kind.CAPTURED, sequence=0)
                self.captures.put_nowait(self.epoch)
            elif p.kind == Kind.CAPTURE_END:
                async def end_capture(epoch):
                    if self.capture_end_gate is not None:
                        await self.capture_end_gate.wait()
                    await self.send(Kind.CAPTURE_ENDED, epoch=epoch, sequence=0)
                self.receipts.add(asyncio.create_task(end_capture(p.generation)))
            elif p.kind == Kind.BEGIN:
                await self.send(Kind.BEGUN, epoch=p.generation, sequence=p.sequence)
            elif p.kind == Kind.AUDIO:
                count = sum(len(x.payload)//2 for x in self.packets
                            if x.kind == Kind.AUDIO and x.generation == p.generation)
                await self.send(Kind.ACCEPTED, epoch=p.generation, sequence=p.sequence,
                                payload=struct.pack("!I", count))
            elif p.kind == Kind.END:
                self.answers.put_nowait(p.generation)  # Simulate ongoing playback.
            elif p.kind == Kind.STOP:
                async def stop(generation):
                    if self.stop_gate is not None:
                        await self.stop_gate.wait()
                    await self.send(Kind.STOPPED, epoch=generation, sequence=0,
                                    payload=struct.pack("!I", 999999 if self.bad_stop_count else 0))
                self.receipts.add(asyncio.create_task(stop(p.generation)))
            else:
                raise AssertionError(f"unexpected server record {p.kind}")
        await asyncio.gather(*self.receipts, return_exceptions=True)


class Detector:
    def Process10ms(self, frame):
        return .95 if frame[0] else .0


class Speech:
    def __init__(self):
        self.calls = []

    async def transcribe(self, chunks, *, on_speech_end):
        frames = [await anext(chunks) for _ in range(20)]
        self.calls.append(frames)
        await on_speech_end(180)
        await chunks.aclose()
        return "vraag " + str(len(self.calls))


class Core:
    def __init__(self):
        self.requests = []

    async def respond(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=kwargs["text"], continue_conversation=True)


@run_async
async def test_one_tls_connection_speech_barge_in_local_stop_resume_and_mute(tls_pair):
    core, speech = Core(), Speech()
    session = HouseholdSpeakerSession(client=core, speech_input=speech, detector_factory=Detector)
    endpoint, token, binding = configured(session, microphone_enabled=True)
    peer = NativePeer()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            await peer.opened.wait()
            await asyncio.sleep(.02)
            assert not peer.captures.qsize() and not core.requests
            await peer.wake()
            assert await asyncio.wait_for(peer.captures.get(), 1) == 1
            await peer.question(1)
            assert await asyncio.wait_for(peer.answers.get(), 1) == 1
            await peer.question(2)  # Speech while the first answer is still playing.
            assert await asyncio.wait_for(peer.answers.get(), 1) == 2
            assert any(p.kind == Kind.STOP and p.generation == 1 for p in peer.packets)
            await peer.send(Kind.LOCAL_STOP)
            assert await asyncio.wait_for(peer.captures.get(), 1) == 2
            assert any(p.kind == Kind.STOP and p.generation == 2 for p in peer.packets)
            await peer.question(3)  # No second wake; fresh capture excludes the stop word.
            assert await asyncio.wait_for(peer.answers.get(), 1) == 3
            await peer.send(Kind.MUTED)
            await asyncio.wait_for(device, 2)
            await wait_released(endpoint)
    assert len(core.requests) == 3
    assert {r["conversation_id"] for r in core.requests} == {binding.conversation_id}
    assert {r["device_id"] for r in core.requests} == {binding.device_id}
    assert [p.generation for p in peer.packets if p.kind == Kind.CAPTURE_END] == [1, 2]
    assert any(p.kind == Kind.STOP and p.generation == 3 for p in peer.packets)


@pytest.mark.parametrize("fault", ["before-wake", "before-captured", "gap", "replay", "foreign", "overflow"])
@run_async
async def test_invalid_or_unconsumed_microphone_input_closes_connection(tls_pair, fault):
    opened = asyncio.Event()
    channel = []
    async def session(binding, output):
        channel.append(output.microphone)
        opened.set()
        await output.microphone.wait_wake()
        await output.microphone.begin_capture()
        await asyncio.Event().wait()  # No consumer for overflow proof.
    endpoint, token, _ = configured(session, microphone_enabled=True)
    peer = NativePeer()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            await opened.wait()
            if fault == "before-wake":
                with pytest.raises(wire.WireError, match="local grant"):
                    await channel[0].begin_capture()
                await peer.send(Kind.MICROPHONE, epoch=1, sequence=1, payload=bytes(640))
            elif fault == "before-captured":
                # Raw PCM cannot activate capture, even after a local wake.
                await peer.send(Kind.WAKE, epoch=0, sequence=0)
                await peer.send(Kind.MICROPHONE, epoch=1, sequence=1, payload=bytes(640))
            else:
                await peer.wake()
                await asyncio.wait_for(peer.captures.get(), 1)
                if fault == "foreign":
                    await socket.send(Packet(Kind.MICROPHONE, b"foreign!", 1, 1, bytes(640)).encode())
                elif fault == "gap":
                    await peer.send(Kind.MICROPHONE, sequence=2, payload=bytes(640))
                elif fault == "replay":
                    for _ in range(2):
                        await peer.send(Kind.MICROPHONE, sequence=1, payload=bytes(640))
                else:
                    for sequence in range(1, 102):
                        await peer.send(Kind.MICROPHONE, sequence=sequence, payload=bytes(640))
            await asyncio.wait_for(socket.wait_closed(), 2)
            await asyncio.gather(device, return_exceptions=True)
            await wait_released(endpoint)
    assert channel[0]._failure is not None


@run_async
async def test_muted_capture_cannot_be_rearmed_by_server(tls_pair):
    rejected, ready = asyncio.Event(), asyncio.Event()
    async def session(binding, output):
        mic = output.microphone
        await mic.wait_wake()
        source = await mic.begin_capture()
        ready.set()
        assert await mic.next_control() == Kind.MUTED
        await source.aclose()
        with pytest.raises(wire.WireError, match="local grant"):
            await mic.begin_capture()
        rejected.set()
    endpoint, token, _ = configured(session, microphone_enabled=True)
    peer = NativePeer()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            await peer.wake()
            await asyncio.wait_for(peer.captures.get(), 1)
            await asyncio.wait_for(ready.wait(), 1)
            await peer.send(Kind.MUTED)
            await asyncio.wait_for(rejected.wait(), 1)
            await asyncio.wait_for(device, 2)


@pytest.mark.parametrize("size", [0, 320, 639, 642, 960])
def test_microphone_format_cannot_be_confused_with_output_pcm(size):
    with pytest.raises(wire.WireError, match="20ms"):
        Packet(Kind.MICROPHONE, b"session!", 1, 1, bytes(size)).encode()


@run_async
async def test_revoke_closes_capture_admission_before_waiting_for_speaker_stop(tls_pair):
    async def session(binding, output):
        await output.microphone.wait_wake()
        await output.microphone.begin_capture()
        await output.speak(generation=1, text="testantwoord")
    endpoint, token, binding = configured(session, microphone_enabled=True)
    peer = NativePeer()
    peer.stop_gate, peer.capture_end_gate = asyncio.Event(), asyncio.Event()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            await peer.wake()
            await asyncio.wait_for(peer.captures.get(), 1)
            await asyncio.wait_for(peer.answers.get(), 1)
            revoked = asyncio.create_task(endpoint.revoke(device_id=binding.device_id))
            async with asyncio.timeout(1):
                while not any(p.kind == Kind.STOP for p in peer.packets):
                    await asyncio.sleep(.001)
            # More than the full input queue while both hardware receipts wait.
            for sequence in range(1, 102):
                await peer.send(Kind.MICROPHONE, sequence=sequence, payload=bytes(640))
            await asyncio.sleep(.02)
            assert socket.state.name == "OPEN" and not revoked.done()
            assert any(p.kind == Kind.CAPTURE_END for p in peer.packets)
            peer.capture_end_gate.set()
            peer.stop_gate.set()
            await asyncio.wait_for(revoked, 1)
            await asyncio.wait_for(device, 1)
            await wait_released(endpoint)


@run_async
async def test_bad_stop_receipt_still_joins_owned_microphone_end(tls_pair):
    observed = []
    async def session(binding, output):
        observed.append(output)
        await output.microphone.wait_wake()
        await output.microphone.begin_capture()
        await output.speak(generation=1, text="testantwoord")
    endpoint, token, binding = configured(session, microphone_enabled=True)
    peer = NativePeer()
    peer.bad_stop_count = True
    peer.capture_end_gate = asyncio.Event()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            await peer.wake()
            await asyncio.wait_for(peer.captures.get(), 1)
            await asyncio.wait_for(peer.answers.get(), 1)
            await asyncio.wait_for(endpoint.revoke(device_id=binding.device_id), 1)
            await wait_released(endpoint)
            output = observed[0]
            assert output._closer.done() and output._reader.done()
            assert output.microphone._capture.closer.done()
            assert not output._pending and socket.state.name == "CLOSED"
            # Release only the test device's delayed response after server cleanup.
            peer.capture_end_gate.set()
            await asyncio.gather(device, return_exceptions=True)


@pytest.mark.parametrize("terminal", [Kind.END_SESSION, Kind.MUTED])
@run_async
async def test_terminal_control_overtakes_pending_interrupt_without_rearming(terminal):
    """End/mute can arrive after local interrupt, including during capture close."""
    from speaker_input import SpeakerMicrophone, CaptureSource
    output = SimpleNamespace(_pending={}, _open=True)
    channel = SpeakerMicrophone(output)
    channel._wake.set()
    capture = CaptureSource(channel, 1)
    capture.state = "active"
    channel._capture = capture
    session = b"session!"
    channel.receive(Packet(Kind.LOCAL_STOP, session, 1))
    assert capture.state == "interrupted"
    channel.receive(Packet(terminal, session, 1))
    assert await channel.next_control() == terminal
    with pytest.raises(wire.WireError, match="fresh local grant"):
        await channel.begin_capture()

    # A consumed wake interrupt may already be waiting for CAPTURE_ENDED.
    capture.state = "closing"
    channel.receive(Packet(terminal, session, 1))
    assert capture.state == "closing"
    channel.receive(Packet(Kind.CAPTURE_ENDED, session, 1))
    assert capture.state == "ended"
    with pytest.raises(wire.WireError, match="fresh local grant"):
        await channel.begin_capture()
