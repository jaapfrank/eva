"""Run a single synthetic, isolated direct-voice trace with the installed image.

Example: python3 development/tools/voice/profiling/run_direct_weather.py \
 --audio-dir /tmp/eva-research-fix/dutch-audio --output /tmp/direct-deep.jsonl
The directory must contain first.wav (16 kHz mono PCM16 synthetic speech).
No production service is replaced. Native tool lifecycle events are observed;
provider-internal queue time and actual acoustic output are not observable.
"""
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--eva',action='store_true',help='Exercise the installed EVA core and saved research return instead of the direct backend');p.add_argument('--audio-dir',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
p.add_argument('--test-deps',type=Path,default=Path('/tmp/eva-voice-probe-venv/lib/python3.12/site-packages'))
p.add_argument('--foreground', action='store_true', help='Expect a direct EVA answer, without a saved background task')
p.add_argument('--core-question', help='Synthetic public text question; bypass microphone and voice for backend isolation')
p.add_argument('--farewell-only',action='store_true',help='Only exercise greeting and farewell in the social probe')
p.add_argument('--social',action='store_true',help='Test social greeting, own activity and farewell closure')
p.add_argument('--dialogue',action='store_true',help='Test follow-up and interruption in one voice session')
p.add_argument('--candidate-media',action='store_true',help='Mount only the candidate media module for isolated validation')
a=p.parse_args();folder=Path(__file__).resolve().parent;root=folder.parents[3]
assert (a.audio_dir/'first.wav').is_file()
x=json.loads(subprocess.check_output(['docker','inspect','eva2-household-speaker-1']))[0]
cmd=['docker','run','--rm','--name','eva-voice-deep-probe','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,size=256m,uid=65532,gid=65532,mode=0700']
lease=next(m for m in x['Mounts'] if m['Destination']=='/run/eva/model-lease')
executable=next(m for m in x['Mounts'] if m['Destination']=='/opt/codex')
mounts=([(m['Source'],m['Destination']) for m in x['Mounts'] if m['Type']=='bind'] if a.eva else [(lease['Source'],lease['Destination']),(executable['Source'],executable['Destination'])])+[
 (folder/('eva_social.py' if a.social else 'eva_dialogue.py' if a.dialogue else 'eva_public_question.py' if a.core_question else 'eva_weather.py' if a.eva else 'direct_weather.py'),'/test.py'),(folder/'synthetic_media_timing.py','/instrument.py'),
 (folder/'deep_instrument.py','/deep_instrument.py'),(folder/'safe_codex_trace.py','/safe_codex_trace.py'),
 (a.audio_dir.resolve(),'/testaudio'),(root/'development/tests','/repo/development/tests'),(a.test_deps.resolve(),'/test-deps')]
if a.eva:cmd+=['--tmpfs','/run/eva-speaker-runtime:rw,size=1m,uid=65532,gid=65532,mode=0700']
if a.farewell_only:
 assert a.social
 cmd+=['-e','EVA_FAREWELL_ONLY=1']
if a.foreground:
 assert a.eva
 cmd+=['-e','EVA_PROBE_FOREGROUND=1']
if a.core_question:
 assert a.eva
 cmd+=['-e','EVA_TEST_PUBLIC_QUESTION='+a.core_question]
if a.candidate_media:
 for module in ('codex_media.py','codex_backend.py','codex_speaker_audio.py','codex_session.py'):
  mounts.append((root/'src/eva/voice_service'/module,'/app/eva/voice_service/'+module))
for src,dst in mounts:cmd+=['-v',str(src)+':'+dst+':ro']
cmd+=['-e','PYTHONPATH=/opt/eva-voice-deps:/app:/app/speaker:/opt/eva/src:/test-deps',x['Image'],'/test.py']
with a.output.open('x') as out:subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT,check=True)
print('Content-free trace saved:',a.output)
