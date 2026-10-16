"""RTP may precede turn events; ordinary listening must preserve that prefix."""
import asyncio
import json
from types import SimpleNamespace
from av import AudioFrame
import pytest
from eva.voice_service.codex_media import CodexMediaSession


def turn(session,kind,identity,role='assistant'):
    session.provider_message(json.dumps({'type':kind,'turn':{'id':identity,'role':role}}))


class Source:
    active=False
    queued_duration=0
    def __init__(self):self.frames=[];self.cleared=0
    def clear_queue(self):self.frames.clear();self.cleared+=1
    async def capture_frame(self,frame):self.frames.append(bytes(frame.data))
    async def aclose(self):pass


async def audio(session,count):
    class End(Exception):pass
    class Track:
        remaining=count
        async def recv(self):
            if not self.remaining:raise End
            self.remaining-=1
            frame=AudioFrame(format='s16',layout='mono',samples=960)
            frame.sample_rate=48000
            frame.planes[0].update(b'\xbc\x02'*960)
            return frame
    with pytest.raises(End):await session.play(Track())


def test_normal_reply_prefix_preceding_assistant_event_is_delivered():
    async def run():
        session=CodexMediaSession({},None,lambda:None);session.source=Source()
        for index in range(2):
            turn(session,'turn.created',f'user{index}','user')
            # Reproduce the observed 34 frames arriving before either terminal
            # user or initial assistant event, over the independent RTP stream.
            await audio(session,34)
            assert len(session.source.frames)==34
            assert session.audio_suppressed==0
            turn(session,'turn.done',f'user{index}','user')
            turn(session,'turn.created',f'answer{index}')
            await audio(session,2)
            assert len(session.source.frames)==36
            turn(session,'turn.done',f'answer{index}')
        await session.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('busy',['assistant','greeting','offer','native_audio','browser_audio','explicit_stop'])
def test_interruption_remains_muted_until_fresh_assistant_turn(busy):
    async def run():
        session=CodexMediaSession({},None,lambda:None);session.source=Source()
        if busy=='assistant':turn(session,'turn.created','old')
        elif busy=='greeting':session.greeting=asyncio.create_task(asyncio.sleep(10))
        elif busy=='offer':session._speaking_handle=object()
        elif busy=='native_audio':session.source.active=True
        elif busy=='browser_audio':session.source.queued_duration=.06
        else:session.stop_current_audio()
        greeting=session.greeting
        turn(session,'turn.created','user','user')
        if greeting:await asyncio.gather(greeting,return_exceptions=True)
        await audio(session,34)
        assert session.output_suppressed and not session.source.frames
        if busy=='assistant':
            turn(session,'turn.done','old')
            turn(session,'turn.created','old')
            assert session.output_suppressed
        turn(session,'turn.created','fresh')
        await audio(session,2)
        assert not session.output_suppressed and len(session.source.frames)==2
        await session.aclose()
    asyncio.run(run())
