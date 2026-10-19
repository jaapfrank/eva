"""Only provable internal digital zero changes; original speech remains exact."""
from array import array
import pytest
from eva.voice_service.media import InternalDigitalSilence


def pcm(values): return array('h', values).tobytes()


@pytest.mark.parametrize('chunk', [1, 73, 480, 4096, 50000])
def test_internal_zero_cap_preserves_soft_speech_and_full_leading_trailing_silence(chunk):
    values = [0]*13000 + [1,-1,2,-2,800] + [0]*16000 + [-900,1,-1] + [0]*15000
    source=pcm(values);cadence=InternalDigitalSilence();result=b''
    for offset in range(0,len(source),chunk*2):result+=cadence.push(source[offset:offset+chunk*2])
    result+=b''.join(cadence.finish())
    assert result==pcm([0]*13000+[1,-1,2,-2,800]+[0]*10800+[-900,1,-1]+[0]*15000)
    assert cadence.removed_samples==5200


@pytest.mark.parametrize('zeros',[0,100,10799,10800,10801])
def test_short_pauses_remain_exact_and_threshold_is_explicit(zeros):
    c=InternalDigitalSilence();out=c.push(pcm([1]+[0]*zeros+[-1]))+b''.join(c.finish())
    assert out==pcm([1]+[0]*min(zeros,10800)+[-1])


def test_all_zero_provider_and_leading_silence_unchanged():
    c=InternalDigitalSilence();data=pcm([0]*20000)
    assert c.push(data)+b''.join(c.finish())==data
    assert c.removed_samples==0


def test_cancelled_stream_does_not_flush_held_tail_into_next_stream():
    old=InternalDigitalSilence();assert old.push(pcm([10]+[0]*15000))==pcm([10]+[0]*10800)
    assert old.pending==4200
    # Cancel discards this per-part instance; the replacement has no old PCM.
    replacement=InternalDigitalSilence();data=pcm([3,-3,0,2])
    assert replacement.push(data)+b''.join(replacement.finish())==data


def test_partial_sample_rejected():
    with pytest.raises(ValueError):InternalDigitalSilence().push(b'\x00')


@pytest.mark.asyncio
async def test_explicit_utterance_encloses_all_frames_and_finishes_before_ack():
    from tests.voice_service.test_literal_speech import renderer, Source
    events=[]
    class Sink(Source):
        def begin_utterance(self): events.append('begin');return 7
        async def capture_frame(self,frame):
            assert events[0]=='begin' and 'finish' not in events
            await super().capture_frame(frame)
        def finish_utterance(self,token): assert token==7;events.append('finish');return True
        async def confirm_playout(self,marker):
            assert events==['begin','finish']
            events.append('ack');return True
    session=renderer();session.source=Sink()
    try:
        handle=session.say('De eerste zin. De tweede zin.')
        await handle.task
        assert events==['begin','finish','ack'] and not handle.interrupted
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_cancel_with_held_silence_never_finishes_or_acknowledges_old_utterance():
    import asyncio
    from types import SimpleNamespace
    from tests.voice_service.test_literal_speech import renderer, Source, audio
    entered=asyncio.Event();closed=asyncio.Event();events=[]
    class Sink(Source):
        def begin_utterance(self):events.append('begin');return 9
        def finish_utterance(self,token):assert token==9;events.append('finish');return True
        async def confirm_playout(self,marker):events.append('ack');return True
    class Stream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):closed.set()
        async def __aiter__(self):
            yield SimpleNamespace(frame=audio(1))
            for _ in range(40):yield SimpleNamespace(frame=audio(0))
            entered.set();await asyncio.Event().wait()
    session=renderer();session.source=Sink()
    session._literal_tts.synthesize=lambda text:Stream()
    try:
        handle=session.say('Een onderbroken antwoord.')
        await entered.wait();before=len(session.source.frames)
        handle.task.cancel()
        with pytest.raises(asyncio.CancelledError):await handle.task
        assert closed.is_set() and events==['begin']
        assert len(session.source.frames)==before
    finally:await session.aclose()
