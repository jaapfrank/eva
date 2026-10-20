"""Silent completion neither creates TTS nor poisons the following spoken turn."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from eva.voice_service.codex_backend import EvaVoiceBackend
from eva.voice_service.codex_media import CodexMediaSession

@pytest.mark.asyncio
async def test_backend_omits_silent_status_from_voice_results_and_followup_context():
    class Core:
        def __init__(self):self.calls=[]
        async def events(self,payload):
            self.calls.append(payload)
            reply={"text":"Opdracht uitgevoerd.","response_mode":"silent"} if len(self.calls)==1 else {"text":"Met mij gaat het goed."}
            yield {"type":"final","reply":reply}
    core=Core();events=[];bridge=EvaVoiceBackend(core,str(uuid4()),lambda:None,events.append)
    assert await bridge.dispatch("Doe de lamp uit","one")==""
    assert bridge.results=={} and bridge.active=={}
    assert events[-1]["response_mode"]=="silent"
    assert await bridge.dispatch("Hoe gaat het?","two")=="Met mij gaat het goed."
    assert core.calls[1]["work_results"]==[] and "Opdracht uitgevoerd" not in str(core.calls[1])
    await bridge.close()


@pytest.mark.asyncio
async def test_native_silent_completion_keeps_session_ready_for_next_reply():
    class Bridge:
        async def dispatch(self,text,key,**kwargs):return "" if key=="one" else "Hoi!"
        def transcript(self,*a,**kw):pass
    session=CodexMediaSession({},None,lambda:None)
    session.bridge=Bridge();spoken=[]
    def say(text):
        assert text
        spoken.append(text)
        async def finished():pass
        return SimpleNamespace(task=asyncio.create_task(finished()),interrupted=False)
    session.say=say;session._core_pending=1
    await session.core_turn("Doe de lamp uit","one",0)
    assert session._core_pending==0 and not session.closed and spoken==[]
    session._core_pending=1
    await session.core_turn("Hoi","two",0)
    assert session._core_pending==0 and not session.closed and spoken==["Hoi!"]
    session.bridge=None
    await session.aclose()
