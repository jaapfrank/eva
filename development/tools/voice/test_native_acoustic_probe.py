import asyncio
from pathlib import Path
from types import SimpleNamespace
import wave
import pytest
from native_acoustic_probe import Probe, install


class Source:
    _input_fences={1}
    _utterance=object()
    input_suppressed=True


@pytest.fixture(autouse=True)
def restore_input_observer(monkeypatch):
    from eva.voice_service.codex_media import CodexMediaSession
    monkeypatch.setattr(CodexMediaSession,'input_event',CodexMediaSession.input_event)
    monkeypatch.setattr(CodexMediaSession,'aclose',CodexMediaSession.aclose)


def frame():
    return SimpleNamespace(sample_rate=16000,num_channels=1,samples_per_channel=320,
                           data=b'\x01\x00'*320)


def test_private_probe_bounds_sources_deadline_and_pcm_without_mutation(tmp_path):
    now=[0];p=Probe(tmp_path/'probe',arm_seconds=5,seconds=2,clock=lambda:now[0])
    sources=[Source() for _ in range(3)];f=frame()
    for s in sources:
        for _ in range(110):p.microphone(s,f)
    now[0]=3
    for s in sources:p.microphone(s,f)
    assert p.count==2
    assert (p.root.stat().st_mode & 0o777)==0o700
    for capture in p.root.iterdir():
        assert (capture.stat().st_mode & 0o777)==0o700
        for file in capture.iterdir():assert (file.stat().st_mode & 0o777)==0o600
        with wave.open(str(capture/'microphone.private.wav'),'rb') as w:
            assert w.getnframes()==32000 and w.readframes(32000)==f.data*100
    assert f.data==b'\x01\x00'*320
    expired=Probe(tmp_path/'expired',arm_seconds=1,clock=lambda:now[0])
    now[0]=5;expired.microphone(Source(),f)
    assert not list(expired.root.iterdir())


def test_core_wait_and_wake_greeting_do_not_consume_the_acoustic_window(tmp_path):
    now=[0];probe=Probe(tmp_path/'answer',clock=lambda:now[0]);source=Source()
    source._utterance=None
    probe.microphone(source,frame());probe.output(source,frame())
    now[0]=90;probe.microphone(source,frame())
    assert probe.count==0 and not list(probe.root.iterdir())
    source._utterance=object();probe.microphone(source,frame())
    recording=probe.recordings[source]
    assert recording.deadline==120 and not recording.closed
    source._utterance=None # Keep the person's continuation after Eva finishes.
    now[0]=100;probe.microphone(source,frame())
    probe.finish(source)
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.getnframes()==640


@pytest.mark.asyncio
async def test_deadline_closes_wav_even_without_another_microphone_frame(tmp_path):
    probe=Probe(tmp_path/'timer',seconds=1);source=Source()
    probe.microphone(source,frame())
    recording=probe.recordings[source]
    await asyncio.sleep(1.05)
    assert recording.closed and recording.events.closed
    assert all(file.closed for file in recording.files.values())
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.getnframes()==320


@pytest.mark.asyncio
async def test_observers_preserve_frames_errors_and_cancellation(tmp_path,monkeypatch):
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    from eva.voice_service.agent import HouseholdCoreClient
    received=[];output_received=[];entered=asyncio.Event();f=frame();source=Source()
    async def original_capture(session,frames):
        async for value in frames:
            received.append(value);entered.set()
    async def original_output(source,value):
        output_received.append(value)
        raise RuntimeError('original output failure')
    async def original_close(session):return 'closed'
    monkeypatch.setattr(CodexMediaSession,'capture_native',original_capture)
    monkeypatch.setattr(CodexMediaSession,'aclose',original_close)
    monkeypatch.setattr(SpeakerSource,'capture_frame',original_output)
    probe=install(tmp_path/'installed','a'*32)
    session=SimpleNamespace(source=source,current=lambda:None,
        state=SimpleNamespace(backend=HouseholdCoreClient('http://127.0.0.1:1','unused',
            satellite_id='assist_satellite.test',device_id='a'*32)))
    async def frames():
        yield f
        await asyncio.Event().wait()
    task=asyncio.create_task(CodexMediaSession.capture_native(session,frames()))
    await entered.wait()
    with pytest.raises(RuntimeError,match='original output failure'):
        await SpeakerSource.capture_frame(source,f)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert received==[f] and received[0] is f
    assert output_received==[f] and output_received[0] is f
    recording=probe.recordings[source]
    assert not recording.closed
    deadline=recording.deadline
    # A replacement native capture continues the same sample, not a new window.
    async def resumed_frames():yield f
    await CodexMediaSession.capture_native(session,resumed_frames())
    assert received==[f,f] and probe.count==1
    assert recording.deadline==deadline and not recording.closed
    assert await CodexMediaSession.aclose(session)=='closed'
    assert recording.closed
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.getnframes()==640
    # A closed recording neither re-arms nor changes subsequent output failure.
    with pytest.raises(RuntimeError,match='original output failure'):
        await SpeakerSource.capture_frame(source,f)
    assert probe.count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[RuntimeError('close failed'),asyncio.CancelledError()])
async def test_terminal_close_stops_observation_before_cleanup_failure(tmp_path,monkeypatch,failure):
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    async def capture(session,frames):pass
    async def close(session):
        assert probe.recordings[source].closed
        raise failure
    monkeypatch.setattr(CodexMediaSession,'capture_native',capture)
    monkeypatch.setattr(CodexMediaSession,'aclose',close)
    monkeypatch.setattr(SpeakerSource,'capture_frame',lambda *args:None)
    probe=install(tmp_path/'terminal','test-device')
    source=Source();probe.microphone(source,frame())
    session=SimpleNamespace(source=source)
    with pytest.raises(type(failure)):await CodexMediaSession.aclose(session)
    probe.microphone(source,frame())
    assert probe.count==1 and probe.recordings[source].samples['microphone']==320


@pytest.mark.asyncio
async def test_revoked_capture_records_nothing(tmp_path,monkeypatch):
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    async def original(session,frames):
        async for value in frames:raise AssertionError('revoked frame reached media')
    monkeypatch.setattr(CodexMediaSession,'capture_native',original)
    monkeypatch.setattr(SpeakerSource,'capture_frame',lambda *args:None)
    probe=install(tmp_path/'revoked','test-device')
    def revoked():raise ValueError('authority revoked')
    session=SimpleNamespace(source=Source(),current=revoked)
    async def frames():yield frame()
    with pytest.raises(ValueError,match='authority revoked'):
        await CodexMediaSession.capture_native(session,frames())
    assert probe.count==0 and not list(probe.root.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize('device',[None,'b'*32])
async def test_other_or_missing_household_binding_is_never_recorded(tmp_path,monkeypatch,device):
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    from eva.voice_service.agent import HouseholdCoreClient
    received=[]
    async def original(session,frames):
        async for value in frames:received.append(value)
    monkeypatch.setattr(CodexMediaSession,'capture_native',original)
    monkeypatch.setattr(SpeakerSource,'capture_frame',lambda *args:None)
    probe=install(tmp_path/'bound','a'*32)
    session=SimpleNamespace(source=Source(),current=lambda:None,
        state=SimpleNamespace(backend=None if device is None else HouseholdCoreClient(
            'http://127.0.0.1:1','unused',satellite_id='assist_satellite.other',device_id=device)))
    f=frame()
    async def frames():yield f
    await CodexMediaSession.capture_native(session,frames())
    assert received==[f] and received[0] is f
    assert probe.count==0 and not list(probe.root.iterdir())


def test_input_trigger_waits_for_unsuppressed_audio_without_answer(tmp_path):
    now=[0];probe=Probe(tmp_path/'input',trigger='input',clock=lambda:now[0])
    source=Source();source._utterance=None
    probe.microphone(source,frame())
    assert probe.count==0
    now[0]=8;source.input_suppressed=False
    probe.microphone(source,frame())
    recording=probe.recordings[source]
    assert recording.deadline==38
    source.input_suppressed=True
    probe.microphone(source,frame())
    probe.finish(source)
    import json
    events=[json.loads(line) for line in (recording.root/'timing.private.jsonl').read_text().splitlines()]
    assert [e['echo_gate'] for e in events]==[False,True]
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.getnframes()==640


def test_provider_observation_whitelist_bound_and_cleanup(tmp_path):
    import json
    now=[0];probe=Probe(tmp_path/'events',trigger='input',clock=lambda:now[0])
    source=Source();source.input_suppressed=False
    probe.provider(source,{'type':'turn.done'})
    assert probe.count==0
    probe.microphone(source,frame())
    event={'type':'turn.done','turn':{'role':'user','transcript':'a'*5000,
           'id':'secret'},'sdp':'secret','token':'secret'}
    for _ in range(520):probe.provider(source,event)
    probe.provider(source,{'type':'unknown','transcript':'secret'})
    probe.finish(source)
    probe.provider(source,event)
    rows=[json.loads(line) for line in (probe.recordings[source].root/'timing.private.jsonl').read_text().splitlines()]
    provider=[row for row in rows if row['kind']=='provider']
    assert len(provider)==512
    assert set(provider[0])=={'kind','monotonic','type','role','text'}
    assert len(provider[0]['text'])==4096
    assert 'secret' not in json.dumps(rows)


def test_input_provider_observer_preserves_binding_and_original_result(tmp_path,monkeypatch):
    import json
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    seen=[]
    def original(session,event):seen.append(event);return 'unchanged'
    async def capture(session,frames):pass
    monkeypatch.setattr(CodexMediaSession,'input_event',original)
    monkeypatch.setattr(CodexMediaSession,'capture_native',capture)
    monkeypatch.setattr(SpeakerSource,'capture_frame',lambda *args:None)
    probe=install(tmp_path/'bound-events','chosen',trigger='input')
    source=Source();source.input_suppressed=False
    probe.microphone(source,frame())
    session=SimpleNamespace(source=source,state=SimpleNamespace(backend=SimpleNamespace(_device={'device_id':'other'})))
    event={'type':'input_transcript.added','start_ms':0,'end_ms':50,
           'item':{'id':'private-item-id','type':'input_transcript','text':'neutral test'}}
    assert CodexMediaSession.input_event(session,event)=='unchanged'
    session.state.backend._device['device_id']='chosen'
    assert CodexMediaSession.input_event(session,event)=='unchanged'
    probe.finish(source)
    rows=[json.loads(line) for line in (probe.recordings[source].root/'timing.private.jsonl').read_text().splitlines()]
    provider=[row for row in rows if row['kind']=='provider']
    assert len(provider)==1
    assert provider[0]['text']=='neutral test'
    assert set(provider[0])=={'kind','monotonic','type','text'}
    assert 'private-item-id' not in json.dumps(rows)
    assert seen==[event,event]


@pytest.mark.parametrize('trigger',['',None,'other'])
def test_invalid_trigger_does_not_create_directory(tmp_path,trigger):
    with pytest.raises(ValueError):Probe(tmp_path/'invalid',trigger=trigger)
    assert not (tmp_path/'invalid').exists()


def test_runtime_identity_brackets_one_recording_without_extending_capture(tmp_path):
    import json
    now=[0];revision=['before'];calls=[]
    def identity():
        calls.append(revision[0])
        return {'valid':True,'revision':revision[0]}
    probe=Probe(tmp_path/'bound',seconds=2,clock=lambda:now[0],
                runtime_identity=identity,max_recordings=1)
    source=Source();probe.microphone(source,frame())
    recording=probe.recordings[source]
    now[0]=1;probe.microphone(source,frame())
    revision[0]='after';now[0]=2;probe.microphone(source,frame())
    probe.finish(source);probe.microphone(Source(),frame())
    assert recording.closed and recording.deadline==2 and probe.count==1
    assert calls==['before','after']
    for phase in calls:
        path=recording.root/('runtime-'+phase+'.private.json')
        assert json.loads(path.read_text())=={'valid':True,'revision':phase}
        assert path.stat().st_mode & 0o777==0o600
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.getnframes()==640


def test_identity_publication_is_complete_private_and_after_wav_close(tmp_path,monkeypatch):
    import json
    import os
    published=[];recordings=[]
    link=os.link
    def publish(source,target):
        assert not target.exists()
        assert json.loads(Path(source).read_text())=={'valid':True}
        assert Path(source).stat().st_mode & 0o777==0o600
        published.append(target.name)
        return link(source,target)
    monkeypatch.setattr(os,'link',publish)
    def identity():
        if recordings:
            recording=recordings[0]
            assert all(file.closed for file in recording.files.values())
            assert recording.events.closed
            with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
                assert w.getnframes()==640
                assert len(w.readframes(640))==1280
        return {'valid':True}
    probe=Probe(tmp_path/'atomic',runtime_identity=identity)
    source=Source();probe.microphone(source,frame());probe.microphone(source,frame())
    recordings.append(probe.recordings[source]);probe.finish(source)
    assert published==['runtime-before.private.json','runtime-after.private.json']
    assert not list(recordings[0].root.glob('.runtime-*'))
    # Re-publication must not replace the first complete manifest.
    monkeypatch.setattr(os,'link',link)
    before=(recordings[0].root/'runtime-after.private.json').read_bytes()
    recordings[0].runtime_identity=lambda:{'valid':False}
    recordings[0].identity('after')
    assert (recordings[0].root/'runtime-after.private.json').read_bytes()==before
    assert not list(recordings[0].root.glob('.runtime-*'))


@pytest.mark.parametrize('failure',['error','oversize'])
def test_failed_runtime_identity_invalidates_evidence_without_losing_pcm(tmp_path,failure):
    import json
    def identity():
        if failure=='error':raise ValueError('private credential must not escape')
        return {'sensitive':'x'*262145}
    probe=Probe(tmp_path/'failed-identity',runtime_identity=identity)
    source=Source();probe.microphone(source,frame());probe.finish(source)
    recording=probe.recordings[source]
    for phase in ('before','after'):
        value=json.loads((recording.root/('runtime-'+phase+'.private.json')).read_text())
        assert value=={'valid':False,'errors':['runtime_identity:unavailable']}
    with wave.open(str(recording.root/'microphone.private.wav'),'rb') as w:
        assert w.readframes(320)==frame().data
