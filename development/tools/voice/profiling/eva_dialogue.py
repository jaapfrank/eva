"""Synthetic Dutch multi-turn EVA conversation and interruption regression.

Run in the installed native-speaker image with its existing read-only bindings,
the repository tests mounted at /repo/development/tests and a 16 kHz mono PCM16
question at /testaudio/first.wav. The device acknowledges playback synthetically;
this does not establish acoustic quality or that a person heard the answer.
Timing hooks retain technical metadata only; no transcript or audio is logged.
"""
import asyncio,json,time,wave,sys,os
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
 import hashlib
 import eva.voice_service.codex_media as media_module
 print(json.dumps({'result':'loaded_media','path':media_module.__file__,
    'sha256':hashlib.sha256(Path(media_module.__file__).read_bytes()).hexdigest(),
    'response_scoping':hasattr(CodexMediaSession,'end_assistant_turn')}),flush=True)
 assert hasattr(CodexMediaSession,'end_assistant_turn')
 c=ServiceConfig.load(Path('/run/eva-speaker-runtime/config.json'))
 a=load_media_authority(policy_file=Path('/run/household-voice/current.json'),token_file=Path('/run/household-voice/token'),identity_file=Path('/run/identity/current.json'))
 b=c.grants[0].binding
 core=HouseholdCoreClient(c.eva_url,c.eva_token,satellite_id=b.satellite_id,device_id=b.device_id)
 assert await core.ready()
 returned=asyncio.Event();results=[]
 answer_done=asyncio.Queue();input_started=asyncio.Event();turn_index=0;answer_signal=asyncio.Event();last_core_final=0;verified_lookups=0
 events=[];start=time.monotonic();clips=asyncio.Queue();done=asyncio.Event();foreground_audio=asyncio.Event()
 def notify(e):
  nonlocal last_core_final,verified_lookups
  events.append(e['type'])
  if e['type']=='background_result_part':instrument.mark('saved_result_part')
  if e['type']=='background_result_part':results.append(e.get('text',''));returned.set()
  if e['type'] in {'work_finished','work_failed'}:last_core_final=time.monotonic();done.set()
  if e['type']=='action_finished' and e.get('name')=='search_public_info' and e.get('status')=='verified':verified_lookups+=1
  if e['type'] in {'action_started','action_finished'}:instrument.mark('selected_action',name=e.get('name'),kind=e['type'],status=e.get('status'))
 state=BridgeState(core,notify,replay_unheard=False)
 state.session_id=(await core.work_return('open',state.session_id))['session_id']
 session=CodexMediaSession({'wake_greeting':True,'codex_executable':'/opt/codex/bin/codex','codex_auth_file':'/run/eva/model-lease/auth.json'},state,a.validate_current)
 session.prewarm();await session.prepared
 peer=SpeakerPeer();peer.finished.set();peer.stopped.set()
 async def microphone():
  while True:
   if clips.empty():yield rtc.AudioFrame(bytes(640),16000,1,320);await asyncio.sleep(.02);continue
   instrument.mark('microphone_clip_start')
   with wave.open(await clips.get(),'rb') as w:
    while data:=w.readframes(320):
     yield rtc.AudioFrame(data.ljust(640,b'\0'),16000,1,320)
     await asyncio.sleep(.02)
   instrument.mark('microphone_clip_end')
 from eva.voice_service.codex_backend import EvaVoiceBackend,delegation_input
 original_respond=EvaVoiceBackend.respond
 async def traced_respond(bridge,request):
  import hashlib
  body=await request.json()
  try:
   text,key=delegation_input(body)
   # Synthetic microphone source only; record hashes, never text or credentials.
   instrument.mark('handoff_input',input_sha256=hashlib.sha256(text.encode()).hexdigest(),
      input_characters=len(text),history_items=len(body.get('input',[])),
      user_items=sum(x.get('role')=='user' for x in body.get('input',[]) if isinstance(x,dict)))
  except ValueError:pass
  return await original_respond(bridge,request)
 EvaVoiceBackend.respond=traced_respond
 async def renew():
  while True:await asyncio.sleep(4);await core.work_return('renew',state.session_id)
 task=asyncio.create_task(renew())
 try:
  async with opened(peer,timeout=5) as wire:
   sink=SpeakerAudioOutput(wire);session.output.audio=sink;session.input.audio=microphone()
   capture=sink.capture_frame
   async def capture_foreground(frame):
    if done.is_set() and max((abs(v) for v in frame.data),default=0)>80 and not foreground_audio.is_set():
     instrument.mark('foreground_answer_signal',turn_index=turn_index);foreground_audio.set()
    return await capture(frame)
   sink.capture_frame=capture_foreground
   await clips.put('/testaudio/first.wav')
   woke=time.monotonic();agent=EvaAgent(state);await asyncio.wait_for(session.start(agent),45)
   def provider_event(raw):
    value=json.loads(raw);kind=value.get('type');turn=value.get('turn',{})
    if kind=='turn.created' and turn.get('role')=='user':input_started.set()
    if kind=='turn.done' and turn.get('role')=='assistant':answer_done.put_nowait(time.monotonic())
   session.channel.on('message',provider_event)
   poll=asyncio.create_task(agent.poll_saved_work(session,session.current))
   async def finish_answer():
    await asyncio.wait_for(done.wait(),90)
    await asyncio.wait_for(foreground_audio.wait(),45)
    while True:
     completed=await asyncio.wait_for(answer_done.get(),45)
     if completed>=last_core_final:break
    await asyncio.sleep(.6)
    sink.flush();await asyncio.wait_for(sink.wait_for_playout(),8)
    instrument.mark('answer_playout_ack',turn_index=turn_index,
       speaker_frames=sum(peer.frames.values()),recent_messages=len(session.bridge.recent),
       session_open=not session.closed)
   await finish_answer()
   turn_index=1;done.clear();foreground_audio.clear();input_started.clear()
   lookups_before_followup=verified_lookups
   await clips.put('/testaudio/followup.wav')
   await asyncio.wait_for(done.wait(),90)
   assert verified_lookups>lookups_before_followup, 'Changed-date follow-up did not obtain new evidence'
   await asyncio.wait_for(foreground_audio.wait(),45)
   # Interrupt a real answer while its PCM is arriving, rather than relying on
   # a generated test monologue remaining long enough to interrupt.
   turn_index=2;input_started.clear()
   suppressed_before=session.audio_suppressed
   await clips.put('/testaudio/stop.wav')
   await asyncio.wait_for(input_started.wait(),15)
   await asyncio.sleep(.15)
   assert session.output_suppressed, 'Old audio suppression gate was not active'
   assert not session.closed
   instrument.mark('microphone_interruption_confirmed',session_open=not session.closed,
       suppression_gate_active=session.output_suppressed,
       suppressed_frames=session.audio_suppressed-suppressed_before)
   await asyncio.sleep(4)
   turn_index=3;done.clear();foreground_audio.clear();input_started.clear()
   await clips.put('/testaudio/first.wav')
   await finish_answer()
   assert not session.closed
   # Exercise cancellation through the same handle used by authorization guards.
   before_audio=session.source.audible_frames
   before_epoch=session.source.epoch
   revoked=session.say('Dit is een test van het intrekken van een gesproken antwoord. '
       'Deze tekst mag direct worden afgebroken wanneer de controle dat vraagt.')
   async with asyncio.timeout(30):
    while session.source.audible_frames<=before_audio:await asyncio.sleep(.01)
   assert not revoked.done(), 'Offer finished before the intended revocation'
   revoked.interrupt();await asyncio.gather(revoked.task,return_exceptions=True)
   if sink.barrier is not None:await asyncio.wait_for(asyncio.shield(sink.barrier),5)
   assert session.source.epoch>before_epoch and session.output_suppressed
   instrument.mark('revoked_offer_stop_ack',cancelled=revoked.task.cancelled(),
       session_open=not session.closed)
   await asyncio.sleep(1)
   offered=session.say('De controle is klaar.')
   await asyncio.wait_for(offered.task,45)
   assert not offered.interrupted, 'Offered speech lacks its own native playback ACK'
   instrument.mark('offered_speech_native_ack',completed=offered.done(),
       interrupted=offered.interrupted)
   print(json.dumps({'result':'dialogue_regression','core_completed':events.count('work_finished'),
       'core_failed':events.count('work_failed'),'verified_lookups':verified_lookups,'recent_messages':len(session.bridge.recent),
       'interrupted_answer_audio':True,
       'software_playout_only':True,'elapsed_seconds':round(time.monotonic()-start,2)}),flush=True)
   poll.cancel();await asyncio.gather(poll,return_exceptions=True)
   await session.aclose();await sink.aclose()
 finally:
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  await session.aclose();await core.work_return('close',state.session_id)
import instrument
import deep_instrument
asyncio.run(main())
