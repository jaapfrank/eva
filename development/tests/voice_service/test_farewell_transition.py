import asyncio
import json
import pytest
from eva.voice_service.codex_media import CodexMediaSession


@pytest.mark.parametrize('text',['Doei','Dankje doei','Dank je, doei.','Dank je wel, doei Eva!','Bedankt! Tot ziens.'])
@pytest.mark.parametrize('early_completion',[False,True])
def test_farewell_uses_start_marker_for_audio_before_transcript(text,early_completion):
    async def run():
        session=CodexMediaSession({},None,lambda:None)
        class Source:
            audible=0
            def clear_queue(self):pass
            def playback_marker(self):return (1,self.audible)
            async def confirm_playout(self,marker):
                assert marker==(1,0)
                assert self.audible>marker[1]
                return True
            async def aclose(self):pass
        session.source=Source()
        def event(kind,role,identity,transcript=''):
            session.provider_message(json.dumps({'type':kind,'turn':{'role':role,'id':identity,'transcript':transcript}}))
        event('turn.created','user','u')
        session.source.audible=20 # Real-world RTP already arrived before text.
        if early_completion:
            event('turn.created','assistant','a')
            event('turn.done','assistant','a')
        event('turn.done','user','u',text)
        assert session._farewell_task is not None
        if not early_completion:
            event('turn.created','assistant','a')
            event('turn.done','assistant','a')
        await asyncio.wait_for(session._farewell_task,.2)
        assert session.closed
    asyncio.run(run())


@pytest.mark.parametrize('text',['Dank je','Bedankt voor vandaag','Zeg dankje doei tegen hem','Dank je, doei betekent toch tot ziens?'])
def test_thanks_or_quoted_instruction_does_not_end_session(text):
    session=CodexMediaSession({},None,lambda:None)
    session.user_transcript(text)
    assert session._farewell_task is None


def test_late_old_or_unidentified_completion_cannot_finish_farewell():
    async def run():
        session=CodexMediaSession({},None,lambda:None)
        def event(kind,role,identity):
            session.provider_message(json.dumps({'type':kind,'turn':{'role':role,'id':identity}}))
        event('turn.done','assistant',None)
        assert not session._user_assistant_done
        event('turn.created','assistant','old')
        event('turn.created','user','u')
        event('turn.created','assistant','fresh')
        event('turn.done','assistant','old')
        assert not session._user_assistant_done
        session.user_transcript('Dankje doei')
        assert not session._farewell_done.is_set()
        event('turn.done','assistant',None)
        assert not session._farewell_done.is_set()
        event('turn.done','assistant','fresh')
        assert session._farewell_done.is_set()
        session._farewell_task.cancel()
        await asyncio.gather(session._farewell_task,return_exceptions=True)
        await session.aclose()
    asyncio.run(run())
