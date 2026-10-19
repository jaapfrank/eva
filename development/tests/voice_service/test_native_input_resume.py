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
        assert s.microphone.frames.qsize()==(1 if busy=='none' else 0)
        turn(s,'turn.done','input','Tel rustig tot tien')
        await settle(s)
        assert len(payloads)==1
        assert len(spoken)==(1 if busy=='none' else 0)
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
