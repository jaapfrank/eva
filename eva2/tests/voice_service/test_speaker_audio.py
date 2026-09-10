"""LiveKit I/O with a real websocket and explicitly simulated speaker receipts."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from livekit import rtc
from eva.voice_service.agent import BridgeState, EvaAgent

sys.path.insert(0, str(Path(__file__).parents[1]/"operations"))
from test_native_duplex_speaker_wire import SpeakerPeer, opened, Kind
from speaker_livekit_audio import SpeakerAudioInput, SpeakerAudioOutput
from speaker_input import CaptureEnded


def test_livekit_output_waits_for_finished_and_stop_barrier():
    async def run():
        peer=SpeakerPeer()
        async with opened(peer,timeout=1) as wire:
            sink=SpeakerAudioOutput(wire)
            await sink.capture_frame(rtc.AudioFrame(bytes(960),24000,1,480))
            sink.flush()
            pending=asyncio.create_task(sink.wait_for_playout())
            await asyncio.wait_for(peer.end_received.wait(),1)
            assert not pending.done()
            peer.finished.set()
            event=await pending
            assert not event.interrupted and event.playback_position==.02
            peer.finished.clear();peer.end_received.clear()
            await sink.capture_frame(rtc.AudioFrame(bytes(960),24000,1,480))
            # Zero STOPPED frames is allowed as a lower bound, not proof that
            # the user heard nothing. The subsequent generation still waits.
            sink.clear_buffer()
            next_frame=asyncio.create_task(sink.capture_frame(rtc.AudioFrame(bytes(960),24000,1,480)))
            await asyncio.wait_for(peer.stop_received.wait(),1)
            assert not next_frame.done()
            assert not any(p.kind==Kind.BEGIN and p.generation==3 for p in peer.packets)
            peer.frames[2]=0
            peer.stopped.set()
            await asyncio.wait_for(next_frame,1)
            assert wire.playback_result(2).status=="interrupted"
            assert wire.playback_result(2).confirmed_frames==0
            peer.finished.set();sink.flush()
            event=await sink.wait_for_playout()
            assert not event.interrupted
            await sink.aclose()
    asyncio.run(run())


def test_input_keeps_pcm_shape_and_hardware_empty_output_never_replays():
    async def run():
        class Capture:
            calls=0
            async def __anext__(self):
                self.calls+=1
                if self.calls>1:raise CaptureEnded("local mute")
                return bytes(640)
            async def aclose(self):pass
        source=SpeakerAudioInput(Capture())
        frame=await source.__anext__()
        assert frame.sample_rate==16000 and frame.num_channels==1 and frame.samples_per_channel==320
        try:await source.__anext__()
        except StopAsyncIteration:pass
        else:raise AssertionError("Muted capture continued")
        await source.aclose()
        state=BridgeState(None,replay_unheard=False)
        state.deliveries["result"]=SimpleNamespace(interrupted=True,done=lambda:True,chat_items=[])
        state.delivery_text["result"]="Possibly already heard"
        session=SimpleNamespace(say=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError("Uncertain hardware output replayed")))
        EvaAgent(state).replay_unheard(session)
    asyncio.run(run())


def test_native_livekit_speech_handle_uses_speaker_receipts_and_interrupts():
    from livekit.agents import Agent, AgentSession
    async def run():
        peer=SpeakerPeer()
        async with opened(peer,timeout=2) as wire:
            sink=SpeakerAudioOutput(wire)
            session=AgentSession(aec_warmup_duration=0)
            session.output.audio=sink
            await session.start(Agent(instructions="Transport test; no model or tools."))
            async def audio():
                for _ in range(5):
                    yield rtc.AudioFrame(bytes(960),24000,1,480)
            try:
                first=session.say("Eerste transportproef.",audio=audio(),allow_interruptions=True)
                await asyncio.wait_for(peer.end_received.wait(),2)
                assert not first.done()
                session.interrupt()
                await asyncio.wait_for(peer.stop_received.wait(),2)
                peer.stopped.set()
                await asyncio.wait_for(first.wait_for_playout(),2)
                assert first.interrupted
                peer.end_received.clear()
                second=session.say("Tweede transportproef.",audio=audio(),allow_interruptions=True)
                await asyncio.wait_for(peer.end_received.wait(),2)
                peer.finished.set()
                await asyncio.wait_for(second.wait_for_playout(),2)
                assert not second.interrupted
            finally:
                peer.stopped.set();peer.finished.set()
                await session.aclose()
                await sink.aclose()
    asyncio.run(run())


def test_full_buffer_and_renderer_failure_release_producer_and_cleanup():
    from speaker_wire import WireError
    async def run():
        class FailedWire:
            async def speak_pcm(self, **kwargs):raise WireError("transport failed")
        sink=SpeakerAudioOutput(FailedWire())
        try:
            await asyncio.wait_for(sink.capture_frame(rtc.AudioFrame(bytes(960*101),24000,1,480*101)),.5)
        except WireError:pass
        else:raise AssertionError("Failed renderer left audio accepted")
        sink.flush()
        await asyncio.wait_for(sink.aclose(),.5)
    asyncio.run(run())


def test_already_waiting_frame_cannot_start_after_close():
    from speaker_wire import WireError
    async def run():
        peer=SpeakerPeer()
        async with opened(peer,timeout=1) as wire:
            sink=SpeakerAudioOutput(wire)
            frame=rtc.AudioFrame(bytes(960),24000,1,480)
            await sink.capture_frame(frame)
            sink.flush()
            await asyncio.wait_for(peer.end_received.wait(),1)
            waiting=asyncio.create_task(sink.capture_frame(frame))
            await asyncio.sleep(.02)
            assert not waiting.done()
            closing=asyncio.create_task(sink.aclose())
            await asyncio.wait_for(peer.stop_received.wait(),1)
            peer.stopped.set()
            await asyncio.wait_for(closing,1)
            try: await asyncio.wait_for(waiting,1)
            except WireError: pass
            else: raise AssertionError("Queued frame reopened closed output")
            assert not any(p.kind==Kind.BEGIN and p.generation>1 for p in peer.packets)
    asyncio.run(run())
