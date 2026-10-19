import asyncio
from pathlib import Path
from types import SimpleNamespace
import wave
import pytest
from native_acoustic_probe import Probe, install


class Source:
    _input_fences={1}


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
    received=[];output_received=[];entered=asyncio.Event();f=frame();source=Source()
    async def original_capture(session,frames):
        async for value in frames:
            received.append(value);entered.set()
    async def original_output(source,value):
        output_received.append(value)
        raise RuntimeError('original output failure')
    monkeypatch.setattr(CodexMediaSession,'capture_native',original_capture)
    monkeypatch.setattr(SpeakerSource,'capture_frame',original_output)
    probe=install(tmp_path/'installed')
    session=SimpleNamespace(source=source,current=lambda:None)
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
    assert probe.recordings[source].closed
    # A closed recording neither re-arms nor changes subsequent output failure.
    with pytest.raises(RuntimeError,match='original output failure'):
        await SpeakerSource.capture_frame(source,f)
    assert probe.count==1


@pytest.mark.asyncio
async def test_revoked_capture_records_nothing(tmp_path,monkeypatch):
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    async def original(session,frames):
        async for value in frames:raise AssertionError('revoked frame reached media')
    monkeypatch.setattr(CodexMediaSession,'capture_native',original)
    monkeypatch.setattr(SpeakerSource,'capture_frame',lambda *args:None)
    probe=install(tmp_path/'revoked')
    def revoked():raise ValueError('authority revoked')
    session=SimpleNamespace(source=Source(),current=revoked)
    async def frames():yield frame()
    with pytest.raises(ValueError,match='authority revoked'):
        await CodexMediaSession.capture_native(session,frames())
    assert probe.count==0 and not list(probe.root.iterdir())
