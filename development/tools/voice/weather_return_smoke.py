"""Synthetic Dutch microphone -> EVA -> saved task -> speaker-wire regression.

Run in the installed native-speaker image with its existing read-only bindings,
the repository tests mounted at /repo/development/tests and a 16 kHz mono PCM16
question at /testaudio/first.wav. The device acknowledges playback synthetically;
this does not establish acoustic quality or that a person heard the answer.
Provider event logging here is for this synthetic fixture only, not production.
"""
import asyncio,json,time,wave,sys
sys.path.insert(0,'/repo/development/tests/operations')
from test_native_duplex_speaker_wire import SpeakerPeer,opened
from speaker_livekit_audio import SpeakerAudioOutput
from livekit import rtc
from speaker_service import ServiceConfig
from speaker_livekit_service import load_media_authority
from eva.voice_service.agent import BridgeState,EvaAgent,HouseholdCoreClient
from eva.voice_service.codex_media import CodexMediaSession
from pathlib import Path
sys.path.insert(0,'/run/speaker-deploy')
from launch import prepare
async def main():
 prepare()
 c=ServiceConfig.load(Path('/run/eva-speaker-runtime/config.json'))
 a=load_media_authority(policy_file=Path('/run/household-voice/current.json'),token_file=Path('/run/household-voice/token'),identity_file=Path('/run/identity/current.json'))
 b=c.grants[0].binding
 core=HouseholdCoreClient(c.eva_url,c.eva_token,satellite_id=b.satellite_id,device_id=b.device_id)
 assert await core.ready()
 returned=asyncio.Event();results=[]
 events=[];start=time.monotonic();clips=asyncio.Queue();done=asyncio.Event()
 def notify(e):
  events.append(e['type'])
  if e['type']=='background_result_part':results.append(e.get('text',''));returned.set()
  if e['type'] in {'work_finished','work_failed'}:done.set()
 state=BridgeState(core,notify,replay_unheard=False)
 state.session_id=(await core.work_return('open',state.session_id))['session_id']
 session=CodexMediaSession({'wake_greeting':True,'codex_executable':'/opt/codex/bin/codex','codex_auth_file':'/run/eva/model-lease/auth.json'},state,a.validate_current)
 session.prewarm();await session.prepared
 peer=SpeakerPeer();peer.finished.set();peer.stopped.set()
 async def microphone():
  while True:
   if clips.empty():yield rtc.AudioFrame(bytes(640),16000,1,320);await asyncio.sleep(.02);continue
   with wave.open(await clips.get(),'rb') as w:
    while data:=w.readframes(320):
     yield rtc.AudioFrame(data.ljust(640,b'\0'),16000,1,320)
     await asyncio.sleep(.02)
 async def renew():
  while True:await asyncio.sleep(4);await core.work_return('renew',state.session_id)
 task=asyncio.create_task(renew())
 try:
  async with opened(peer,timeout=5) as wire:
   sink=SpeakerAudioOutput(wire);session.output.audio=sink;session.input.audio=microphone()
   await clips.put('/testaudio/first.wav')
   woke=time.monotonic();agent=EvaAgent(state);await asyncio.wait_for(session.start(agent),45)
   poll=asyncio.create_task(agent.poll_saved_work(session,session.current))
   ready=time.monotonic()-woke
   @session.channel.on('message')
   def debug_message(raw):
    print(json.dumps({'synthetic_provider_event':json.loads(raw)},ensure_ascii=False),flush=True)
   async def media_stats():
    await asyncio.sleep(15)
    stats=await session.peer.getStats()
    print(json.dumps({'synthetic_stats':[vars(v) for v in stats.values()], 'recent':session.bridge.recent},default=str),flush=True)
   asyncio.create_task(media_stats())
   await asyncio.wait_for(done.wait(),90)
   await asyncio.wait_for(returned.wait(),110)
   pcm_at_return=sum(peer.frames.values())
   await asyncio.sleep(.1)
   await asyncio.wait_for(asyncio.gather(*(asyncio.shield(h.task) for h in list(session.speech_handles))),45)
   await asyncio.sleep(.5)
   sink.flush();await asyncio.wait_for(sink.wait_for_playout(),5)
   print(json.dumps({'result':'weather_return_smoke','ready_after_wake_seconds':round(ready,2),
       'core_completed':events.count('work_finished'),'core_failed':events.count('work_failed'),
       'saved_result_parts':len(results),'result_preview':''.join(results), 'pcm_after_result':sum(peer.frames.values())-pcm_at_return, 'transcripts':session.bridge.recent,
       'pcm_samples':sum(peer.frames.values()),'closed_early':session.closed,
       'elapsed_seconds':round(time.monotonic()-woke,2)}),flush=True)
   poll.cancel();await asyncio.gather(poll,return_exceptions=True)
   await session.aclose();await sink.aclose()
 finally:
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  await session.aclose();await core.work_return('close',state.session_id)
asyncio.run(main())
