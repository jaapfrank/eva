"""Native capture resume keeps unadmitted speech; output/work interruptions revoke."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_single_core_voice import fixture, turn, settle

_spec=importlib.util.spec_from_file_location('native_input_resume_service',
    Path(__file__).parents[3]/'deploy/native-speaker/codex_service.py')
service=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(service)


def media():
    s,payloads,spoken=fixture();s.__class__=service.NativeCodexMediaSession
    s.renderer.speech_handles=set()
    s.microphone=SimpleNamespace(frames=asyncio.Queue())
    s.microphone.frames.put_nowait(b'opening-prefix')
    return s,payloads,spoken


@pytest.mark.asyncio
@pytest.mark.parametrize('busy',['none','core','queued','active','greeting'])
async def test_resume_preserves_only_listening_input_and_prefix(busy):
    s,payloads,spoken=media()
    pending=None
    try:
        turn(s,'turn.created','input')
        if busy=='core':s._core_pending=1
        if busy=='queued':
            pending=asyncio.create_task(asyncio.Event().wait())
            s.renderer.speech_handles.add(SimpleNamespaceHandle(pending))
        if busy=='active':s.source=SimpleNamespace(active=True,clear_queue=lambda:None,aclose=AsyncMock())
        if busy=='greeting':s.greeting=pending=asyncio.create_task(asyncio.Event().wait())
        s.clear_user_turn();s.interrupt()
        assert s.microphone.frames.qsize()==1
        turn(s,'turn.done','input','Tel rustig tot tien')
        await settle(s)
        assert len(payloads)==1
        assert len(spoken)==1
        assert s._response_floor==(1 if busy=='none' else 2)
        # An unrelated later interrupt must not inherit the one-shot exemption.
        s.interrupt();assert s._response_floor>=2
    finally:
        if pending:pending.cancel();await asyncio.gather(pending,return_exceptions=True)
        s.microphone=None
        await s.aclose()


class SimpleNamespaceHandle:
    def __init__(self,task):self.task=task
    def done(self):return self.task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',['END_SESSION','MUTED'])
@pytest.mark.parametrize('pending_answer',[False,True])
async def test_actual_inherited_handler_resumes_capture_and_keeps_one_answer(monkeypatch,terminal,pending_answer):
    import speaker_livekit_session as inherited
    from speaker_wire import Kind
    s,payloads,spoken=media()
    started=asyncio.Event();resumed=asyncio.Event();controls=asyncio.Queue()
    captures=[];closed=[]
    class Channel:
        control_observer=None
        async def wait_wake(self):pass
        async def begin_capture(self):
            captures.append(object())
            if len(captures)==2:resumed.set()
            return captures[-1]
        async def next_control(self):return await controls.get()
        def start_close(self):pass
        async def close(self):closed.append(True)
    class Input:
        def __init__(self,value):self.value=value
        async def aclose(self):pass
    class Sink:
        def __init__(self,wire):self.closed=False;self.barrier=None
        def clear_buffer(self):pass
        async def aclose(self):pass
    class Backend:
        work_returns=False
        def __init__(self,*args,**kwargs):pass
        async def ready(self):return True
    monkeypatch.setattr(inherited,'HouseholdCoreClient',Backend)
    monkeypatch.setattr(inherited,'SpeakerAudioInput',Input)
    monkeypatch.setattr(inherited,'SpeakerAudioOutput',Sink)
    async def start(agent):started.set()
    s.start=start
    # Actual inherited handler owns these ports; no real capture/provider.
    s.input=SimpleNamespace(audio=None)
    native=inherited.LiveKitHouseholdSpeakerSession(core_url='http://unused',
        household_token='unused',stt_source=None,tts_source=None,vad=None,check=lambda:None)
    native.make_session=lambda:s
    channel=Channel()
    task=asyncio.create_task(native(SimpleNamespace(satellite_id='test',device_id='test'),
        SimpleNamespace(microphone=channel)))
    try:
        await asyncio.wait_for(started.wait(),1)
        turn(s,'turn.created','input')
        controls.put_nowait(Kind.LOCAL_STOP)
        await asyncio.wait_for(resumed.wait(),1)
        assert s.microphone.frames.qsize()==1
        waiting=asyncio.Event()
        if pending_answer:
            original=s.bridge.dispatch
            async def delayed(*args,**kwargs):
                waiting.set();await asyncio.Event().wait()
                return await original(*args,**kwargs)
            s.bridge.dispatch=delayed
        turn(s,'turn.done','input','Tel rustig tot tien')
        if pending_answer:
            await asyncio.wait_for(waiting.wait(),1)
            assert not spoken
        else:
            await settle(s)
            assert len(payloads)==len(spoken)==1
        assert len(captures)==2
        # Terminal stop follows inherited close, never the preservation hook.
        s.microphone=None
        channel.control_observer(getattr(Kind,terminal))
        controls.put_nowait(getattr(Kind,terminal))
        await asyncio.wait_for(task,1)
        assert s.closed and closed
        if pending_answer:assert not spoken
    finally:
        s.microphone=None
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await s.aclose()


@pytest.mark.asyncio
async def test_resume_during_unadmitted_fragment_settlement_keeps_single_answer():
    s,payloads,spoken=media()
    try:
        turn(s,'turn.created','input')
        turn(s,'turn.done','input','Tel rustig tot tien')
        assert not s._input_open and s._input_fragments and s._core_pending==0
        s.clear_user_turn();s.interrupt()
        await settle(s)
        assert len(payloads)==len(spoken)==1
        assert s.microphone.frames.qsize()==1
    finally:
        s.microphone=None
        await s.aclose()


@pytest.mark.asyncio
async def test_local_resume_preserves_open_correction_but_retires_previous_pending_answer():
    s,payloads,spoken=media();entered=asyncio.Event();release=asyncio.Event()
    original=s.bridge.dispatch
    async def delayed(text,key,**kwargs):
        if text=='Eerste vraag':
            entered.set();await release.wait()
        return await original(text,key,**kwargs)
    s.bridge.dispatch=delayed
    try:
        turn(s,'turn.created','old');turn(s,'turn.done','old','Eerste vraag')
        await asyncio.wait_for(entered.wait(),1)
        turn(s,'turn.created','correction')
        old_generation=s._input_generations['old']
        s.clear_user_turn();s.interrupt()
        assert s._response_floor>old_generation
        assert s._input_generations['correction']==s._generation
        turn(s,'turn.done','correction','Mijn nieuwe vraag')
        release.set();await settle(s)
        assert [p['text'] for p in payloads]==['Eerste vraag','Mijn nieuwe vraag']
        assert spoken==['Kern: Mijn nieuwe vraag']
        # Explicit later STOP has no preservation latch.
        s.interrupt()
        assert s._response_floor>s._input_generations['correction']
    finally:
        release.set();s.microphone=None;await s.aclose()


@pytest.mark.asyncio
async def test_actual_stop_receipt_and_new_capture_preserve_short_correction_prefix():
    import sys,struct
    from livekit import rtc
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from eva.voice_service.codex_media import MicrophoneTrack
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    from speaker_livekit_audio import SpeakerAudioOutput,SpeakerAudioInput
    from speaker_wire import FramedSpeakerOutput,Kind
    sys.path.insert(0,str(Path(__file__).parents[1]/'operations'))
    from test_native_duplex_speaker_input import NativePeer
    peer=NativePeer()
    async with serve(peer.handle,'127.0.0.1',0,compression=None) as server:
        port=server.sockets[0].getsockname()[1]
        async with connect(f'ws://127.0.0.1:{port}',proxy=None,compression=None) as socket:
            wire=FramedSpeakerOutput(socket=socket,microphone_enabled=True,receipt_timeout=1)
            await wire.start();await peer.wake();await wire.microphone.wait_wake()
            first=await wire.microphone.begin_capture()
            sink=SpeakerAudioOutput(wire);source=SpeakerSource(sink)
            s=service.NativeCodexMediaSession({},None,lambda:None)
            s.source=source;s.microphone=MicrophoneTrack(input_suppressed=lambda:source.input_suppressed)
            s.microphone.enabled=True;s.microphone.catching_up=False
            s.renderer=SimpleNamespace(speech_handles=set(),interrupt=source.clear_queue)
            old=asyncio.create_task(s.capture_native(SpeakerAudioInput(first)))
            await asyncio.sleep(0)
            new=None
            try:
                await source.capture_frame(rtc.AudioFrame(struct.pack('<960h',*([1000]*960)),48000,1,960))
                assert source.input_suppressed
                await peer.send(Kind.LOCAL_STOP)
                assert await wire.microphone.next_control()==Kind.LOCAL_STOP
                # The actual inherited handler performs this same ordered handshake.
                old.cancel();await asyncio.gather(old,return_exceptions=True)
                s.clear_user_turn();s.interrupt();sink.clear_buffer()
                await asyncio.shield(sink.barrier);await first.aclose()
                assert wire.playback_result(sink.segment.generation).status=='interrupted'
                second=await wire.microphone.begin_capture()
                assert second.epoch>first.epoch and source.input_suppressed
                new=asyncio.create_task(s.capture_native(SpeakerAudioInput(second)))
                # A complete short prefix arrives during the existing 300ms tail.
                for _ in range(10):
                    peer.sequence+=1
                    await peer.send(Kind.MICROPHONE,payload=struct.pack('<320h',*([1234]*320)))
                received=[]
                async def consume():
                    while len(received)<8:
                        frame=await s.microphone.recv()
                        if any(abs(x)>100 for x in memoryview(bytes(frame.planes[0])).cast('h')):received.append(frame)
                await asyncio.wait_for(consume(),1.5)
                assert s.microphone.echo_frames_discarded==0
                assert s.microphone.dropped_frames==0
                assert any(p.kind==Kind.STOP for p in peer.packets)
                assert len([p for p in peer.packets if p.kind==Kind.CAPTURE])==2
            finally:
                for task in (old,new):
                    if task:task.cancel()
                await asyncio.gather(*(task for task in (old,new) if task),return_exceptions=True)
                await sink.aclose();await wire.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['completed','unconfirmed','old_capture','wrong_wire','failed_barrier'])
async def test_post_stop_prefix_never_bypasses_receipt_or_capture_binding(invalid):
    from livekit import rtc
    from eva.voice_service.codex_media import MicrophoneTrack
    loop=asyncio.get_running_loop();barrier=loop.create_future()
    if invalid=='failed_barrier':barrier.set_exception(RuntimeError('STOP failed'))
    else:barrier.set_result(None)
    receipt=SimpleNamespace(status='completed' if invalid=='completed' else 'interrupted',
                            confirmed_frames=None if invalid=='unconfirmed' else 0)
    wire=SimpleNamespace(playback_result=lambda generation:receipt)
    sink=SimpleNamespace(barrier=barrier,wire=wire)
    source=SimpleNamespace(epoch=2,sink=sink,input_suppressed=True)
    class Frames:
        capture=SimpleNamespace(epoch=1 if invalid=='old_capture' else 2,
            owner=SimpleNamespace(_output=object() if invalid=='wrong_wire' else wire),state='active')
        async def __aiter__(self):
            for _ in range(3):yield rtc.AudioFrame(bytes([50,0])*320,16000,1,320)
    s=service.NativeCodexMediaSession({},None,lambda:None)
    s.source=source;s.microphone=MicrophoneTrack(input_suppressed=lambda:source.input_suppressed)
    s._native_stopped_output=(source,2,sink,1,1)
    await s.capture_native(Frames())
    assert s.microphone.frames.empty() and s.microphone.echo_frames_discarded>0
    assert source.input_suppressed
    if invalid=='failed_barrier':assert barrier.exception() is not None


@pytest.mark.asyncio
async def test_paced_prefix_stops_when_capture_is_retired_during_resume(monkeypatch):
    from livekit import rtc
    from eva.voice_service.codex_media import CodexMediaSession
    received=[]
    async def consume(self,frames):
        async for frame in frames:received.append(frame)
    monkeypatch.setattr(CodexMediaSession,'capture_native',consume)
    barrier=asyncio.get_running_loop().create_future();barrier.set_result(None)
    wire=SimpleNamespace(playback_result=lambda generation:SimpleNamespace(status='interrupted',confirmed_frames=0))
    sink=SimpleNamespace(wire=wire,barrier=barrier)
    source=SimpleNamespace(epoch=2,sink=sink,input_suppressed=False)
    capture=SimpleNamespace(epoch=2,owner=SimpleNamespace(_output=wire),state='active')
    class Frames:
        async def __aiter__(self):
            yield rtc.AudioFrame(bytes(640),16000,1,320)
            capture.state='interrupted'
            yield rtc.AudioFrame(bytes(640),16000,1,320)
    frames=Frames();frames.capture=capture
    s=service.NativeCodexMediaSession({},None,lambda:None);s.source=source
    s._native_stopped_output=(source,2,sink,1,1)
    await s.capture_native(frames)
    assert len(received)==1
