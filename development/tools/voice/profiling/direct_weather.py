"""Synthetic Codex voice + native web search; no EVA core or durable worker."""
import asyncio,json,time,wave,sys
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
sys.path.insert(0,'/repo/development/tests/operations')
from test_native_duplex_speaker_wire import SpeakerPeer,opened
from speaker_livekit_audio import SpeakerAudioOutput
from livekit import rtc
from eva.voice_service.agent import BridgeState
import eva.voice_service.codex_media as media
from eva.voice_service.codex_session import CodexVoiceSession
import instrument
import deep_instrument

today=datetime.now(ZoneInfo('Europe/Amsterdam')).date()
calendar=f'Today is {today.isoformat()} in Europe/Amsterdam. Tomorrow is {(today+timedelta(days=1)).isoformat()}.'
base_request=CodexVoiceSession.request
async def request(self,method,params):
 if method=='thread/start':
  params={**params,'model':'gpt-6-sol','modelProvider':'openai',
   'config':{'experimental_realtime_webrtc_call_base_url':'https://chatgpt.com/backend-api/codex','features.shell_tool':False,'web_search':'live','model_reasoning_effort':'none'},
   'baseInstructions':calendar+' For a simple current factual question, immediately call native web search with the location and exact date. Do not write a plan or preamble; the voice handles acknowledgements. Stop when a reliable source answers the question; use at most two searches. Return the answer in one or two Dutch sentences with an inline source citation. Preserve uncertainty and never invent missing data. No files, shell, personal data or external actions.'}
 result=await base_request(self,method,params)
 if method=='thread/start':print(json.dumps({'direct_backend_requested':'gpt-6-sol','eva_core_connected':False,'model_returned':result.get('model'),'model_provider_returned':result.get('modelProvider')}),flush=True)
 return result
CodexVoiceSession.request=request
media.VOICE_PROMPT='Speak briefly in Dutch. Answer small talk directly. For current weather immediately use the attached Codex backend with native web search and then speak the answer concisely. You are testing direct Codex voice, not EVA. Never invent current weather or claim a search without a result. '+calendar
class NoEva:
 def __getattr__(self,name):raise AssertionError('EVA core access forbidden in direct probe')
async def noop(*a,**k):pass
async def main():
 state=BridgeState(NoEva());session=media.CodexMediaSession({'wake_greeting':False,'codex_executable':'/opt/codex/bin/codex','codex_auth_file':'/run/eva/model-lease/auth.json'},state,lambda:None)
 session.prewarm();await session.prepared
 peer=SpeakerPeer();peer.finished.set();peer.stopped.set()
 async def microphone():
  instrument.mark('microphone_clip_start')
  with wave.open('/testaudio/first.wav','rb') as w:
   while data:=w.readframes(320):
    yield rtc.AudioFrame(data.ljust(640,b'\0'),16000,1,320);await asyncio.sleep(.02)
  instrument.mark('microphone_clip_end')
  while True:yield rtc.AudioFrame(bytes(640),16000,1,320);await asyncio.sleep(.02)
 try:
  async with opened(peer,timeout=5) as wire:
   sink=SpeakerAudioOutput(wire);session.output.audio=sink;session.input.audio=microphone()
   finished=asyncio.Event();turns=[]
   def event(raw):
    d=json.loads(raw);kind=d.get('type')
    if kind in {'turn.done','delegation.created','delegation.context.appended','error'}:
     instrument.mark('direct_voice_event', kind=kind)
    if kind=='turn.done' and d.get('turn',{}).get('role')=='assistant':
     turns.append(d['turn'].get('transcript',''))
     # An acknowledgement alone does not finish the weather test.

     if deep_instrument.projection.backend_completed:finished.set()
   session.channel.on('message',event)
   await session.start(SimpleNamespace(state=state,refresh_saved_context=noop))
   try:await asyncio.wait_for(finished.wait(),75)
   except TimeoutError:print(json.dumps({'direct_timeout':True}),flush=True)
   await asyncio.sleep(1)
   print(json.dumps({'direct_result':True,'assistant_turn_count':len(turns),'eva_core_connected':False,'closed_early':session.closed}),flush=True)
   await session.aclose();await sink.aclose()
 finally:await session.aclose()
asyncio.run(main())
