import ast,copy,json,pathlib,subprocess,time,urllib.request,os
P=pathlib.Path('/var/lib/eva2/private-result-return-20260922/browser-timer-fix');R=pathlib.Path('/var/lib/eva2/managed-b-slot3/core-worker-active-compose.json')
CORE='eva2-managed-b-slot3-core';WORKER='eva2-managed-b-slot3-delegated-work-worker-1'
def run(a,code=None):return subprocess.run(a,input=code,text=True,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120).stdout.strip()
def inspect(n):return json.loads(run(['docker','inspect',n]))[0]
proof=json.loads((P/'verification.json').read_text());old=json.loads(R.read_text());assert old['verified'];before=inspect(CORE);worker=inspect(WORKER)['Id'];assert before['Image']==proof['parent'] and before['Image']==old['coreImage'] and before['State']['Health']['Status']=='healthy'
protected={n:inspect(n)['Id'] for n in ['eva2-owner-voice-media-1','eva2-owner-voice-livekit-1','eva2-document-workspace-au006','eva2-document-goal-worker-au006','eva2-household-speaker-1','eva2-mcp-gateway']}
overlay=P/'core-image.compose.json';assert not overlay.exists();overlay.write_text(json.dumps({'services':{'core':{'image':proof['candidate']}}}));overlay.chmod(0o600);command=old['command']+['-f',str(overlay)]
def config(a):return json.loads(run(a+['--profile','*','config','--format','json']))
expected=config(old['command']);expected['services']['core']['image']=proof['candidate'];assert config(command)==expected
code=next(ast.literal_eval(n.value) for n in ast.parse(pathlib.Path('/tmp/eva-research-preflight.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='inventory_code' for t in n.targets))
def inventory():return json.loads(run(['docker','exec','-i',WORKER,'python','-'],code))
rows=inventory();terminal={'succeeded','verified','failed','ambiguous','needs_help','stopped'};assert all(x['state'] in terminal for x in rows)
(P/'before-rows.private.json').write_text(json.dumps(rows));(P/'active.before.json').write_bytes(R.read_bytes());(P/'container.before.private.json').write_text(json.dumps(before))
for n in ['before-rows.private.json','active.before.json','container.before.private.json']:(P/n).chmod(0o600)
def ready():
 observations=[]
 for _ in range(45):
  try:
   value=json.load(urllib.request.urlopen('http://127.0.0.1:44710/readyz',timeout=2));observations.append(value)
   if value.get('ready'):return
  except (OSError,ValueError) as e:observations.append({'errorType':type(e).__name__})
  time.sleep(1)
 (P/'readiness-failure.private.json').write_text(json.dumps(observations));raise RuntimeError('readiness failed')
def cfg(v):
 v=copy.deepcopy(v);v.pop('Hostname',None);v['Labels']={k:x for k,x in v['Labels'].items() if not k.startswith('com.docker.compose.') and k!='eva2.shared-voice-source'};v.pop('Image',None);v['Env']=dict(x.split('=',1) for x in v['Env']);return v
def host(v):
 v=copy.deepcopy(v);v['Mounts']=sorted(v['Mounts'],key=lambda x:x['Target']);return v
def validate(image):
 ready();a=inspect(CORE);assert a['Image']==image and cfg(a['Config'])==cfg(before['Config']) and host(a['HostConfig'])==host(before['HostConfig'])
 assert sorted(a['Mounts'],key=lambda x:x['Destination'])==sorted(before['Mounts'],key=lambda x:x['Destination'])
 assert set(a['NetworkSettings']['Networks'])==set(before['NetworkSettings']['Networks'])
 assert inspect(WORKER)['Id']==worker and all(inspect(n)['Id']==i for n,i in protected.items())
 assert inventory()==rows
 jobs=json.load(urllib.request.urlopen('http://127.0.0.1:44711/internal/v1/dashboard/work',timeout=10))['jobs'];assert {x['work_id'] for x in jobs}=={x['work_id'] for x in rows}
 return a

def persist(value):
 p=R.with_suffix('.image-new');p.write_text(json.dumps(value,indent=2)+'\n');p.chmod(0o600);os.replace(p,R)
def record(cmd,a,phase):
 r=copy.deepcopy(old);r.update(command=cmd,verified=True,coreImage=a['Image'],phase=phase);r['observedServices'][CORE].update(id=a['Id'],image=a['Image'],running=True)
 if a['Image']==proof['candidate']:r['privateResultBrowserFixCommit']='20ff4bcf';r['privateResultBrowserFixRollbackCommand']=old['command']+['up','-d','--no-deps','--pull','never','core']
 persist(r)
current=inspect(CORE);assert current['Id']==before['Id'] and current['Config']==before['Config'] and current['HostConfig']==before['HostConfig'];assert config(command)==expected
candidate_id=None;result={'installed':False,'modelCalls':0};persist({**old,'verified':False,'command':None,'lastIntendedCommand':command,'phase':'installing-private-return-browser-fix'})
try:
 run(['docker','stop','--timeout=-1',CORE]);a=inspect(CORE);assert not a['State']['Running'] and a['State']['ExitCode']==0
 assert all(x['state'] in terminal for x in inventory())
 run(command+['up','-d','--no-deps','--pull','never','core']);candidate_id=inspect(CORE)['Id'];a=validate(proof['candidate'])
 run(['docker','exec',CORE,'python','-c','from eva.adapters.api.household_voice_session import install_household_voice_session;from eva.bootstrap.native_voice import NativeHouseholdResponder;assert callable(NativeHouseholdResponder.make_turn)'])
 record(command,a,'installed-private-return-browser-fix');result.update(installed=True,image=a['Image'],coreId=a['Id'],rowsPreserved=len(rows),workerAndProtectedContainersUnchanged=True)
except Exception as error:
 result['failureType']=type(error).__name__
 try:
  current=inspect(CORE);assert current['Id'] in {before['Id'],candidate_id},'unknown core instance'
  if current['State']['Running']:
   run(['docker','stop','--timeout=-1',CORE]);stopped=inspect(CORE)
   assert stopped['Id']==current['Id'] and not stopped['State']['Running'] and stopped['State']['ExitCode']==0,'rollback requires graceful stop'
  run(old['command']+['up','-d','--no-deps','--pull','never','core']);a=validate(proof['parent']);record(old['command'],a,'private-return-browser-fix-rollback-verified');result['rollbackVerified']=True
 except Exception as e:result.update(rollbackVerified=False,rollbackFailureType=type(e).__name__)
finally:
 p=P/'installation-result.json';p.write_text(json.dumps(result,indent=2)+'\n');p.chmod(0o600);print(json.dumps(result))
if not result['installed']:raise SystemExit(1)
