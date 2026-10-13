from pathlib import Path
import subprocess,json,hashlib,os,time,fcntl,importlib.util
S=Path(__file__).resolve().parent
BASE=Path('/home/administrator/project/.eva2-artifacts/procedure-reference-runtime-r2-20260913')
spec=importlib.util.spec_from_file_location('reviewed_guards',BASE/'common.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
def require(c,m):
 if not c:raise RuntimeError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(n,v):
 with (S/n).open('x') as f:json.dump(v,f,indent=2)
 (S/n).chmod(0o400)
def command(cmd):
 r=subprocess.run(cmd,capture_output=True,text=True);require(r.returncode==0,'Command failed: '+r.stderr[-300:]);return r.stdout

def guard(image,original_id=False):
 sealed=base.sealed();plan=json.loads((S/'installation-plan.json').read_text())
 for f,h in plan['core_config_sha256'].items():require(sha(f)==h,'Compose config changed')
 before=base.read('containers-before.private.json');after=json.loads(command(['docker','inspect',*[c['Name'] for c in before]]));old=base.read('core-before.private.json');core=next(c for c in after if c['Name']=='/'+base.CORE)
 for b in before:
  a=next(c for c in after if c['Name']==b['Name'])
  if a['Name']==core['Name']:continue
  require((a['Id'],a['Image'],a['State']['StartedAt'],a['State']['Running'])==(b['Id'],b['Image'],b['State']['StartedAt'],b['State']['Running']),'Protected service changed')
  require(a['Config']==b['Config'] and base.normalized_host_config(a['HostConfig'])==base.normalized_host_config(b['HostConfig']) and sorted(a['Mounts'],key=lambda m:m['Destination'])==sorted(b['Mounts'],key=lambda m:m['Destination']),'Protected configuration changed')
 require(core['Image']==image and core['State']['Running'],'Wrong or stopped core')
 if original_id:require(core['Id']==old['Id'],'Parent instance changed')
 a=dict(core['Config']);b=dict(old['Config'])
 for c in [a,b]:
  c.pop('Labels',None);c.pop('Image',None);c.pop('Hostname',None);c['Env']=dict(e.split('=',1) for e in c['Env'])
 require(a==b and base.normalized_host_config(core['HostConfig'])==base.normalized_host_config(old['HostConfig']),'Unexpected core configuration')
 require(core['Config']['Hostname']==core['Id'][:12],'Unexpected core hostname')
 require(base.probe('bound-files-probe.py')==base.read('bound-files-expected.json'),'Bound files changed')
 require(base.probe('config-probe.py')==base.read('budget-mandate-expected.json'),'Budget/mandate changed')
 for f,h in sealed['gateway_source_sha256'].items():require(hashlib.sha256(command(['docker','exec','eva2-mcp-gateway','cat',f]).encode()).hexdigest()==h,'Gateway source changed')
 require(sha(sealed['document_journal_path'])==sealed['document_journal_sha256'],'AU006 journal changed')
 proof=json.loads((S/'core-image-proof.json').read_text());expected=proof['source_delta']['eva/application/conversation.py']['candidate' if image==plan['core_image'] else 'parent']
 code="from pathlib import Path;import hashlib;print(hashlib.sha256(Path('/opt/eva/src/eva/application/conversation.py').read_bytes()).hexdigest())"
 require(command(['docker','exec',base.CORE,'python','-c',code]).strip()==expected,'Live core source mismatch')
 pending_code="""import os,json,sqlalchemy as sa
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline,external_timeline_scope
engine=sa.create_engine(sqlalchemy_postgres_dsn(read_runtime_secret_file(os.environ['EVA_DATABASE_DSN_FILE'],setting_name='EVA_DATABASE_DSN_FILE')))
timeline=PostgresOperationalTimeline(engine,external_timeline_scope(os.environ['EVA_RUNTIME_TENANT_ID'],os.environ['EVA_RUNTIME_INSTALLATION_ID'],'scope'))
with engine.connect() as c:
 timeline._scope(c)
 count=c.scalar(sa.text("SELECT count(*) FROM eva_core.operational_checkpoints WHERE phase='model_admitted' AND status='active' AND updated_at>now()-interval '10 minutes'"))
engine.dispose();print(json.dumps({'recent_unfinished_model_requests':count}))
"""
 pending=base.inside(base.CORE,pending_code);require(pending['recent_unfinished_model_requests']==0,'Recent model request still running')
 ready=base.probe('quiescence-probe.py');return {'image':image,'core_id':core['Id'],'source_sha256':expected,'protected_services':len(before)-1,'readiness':ready,'pending_requests':pending}

def wait_guard(image):
 for i in range(25):
  try:return guard(image)
  except Exception:
   if i==24:raise
   time.sleep(1)

def replace_bridge(content,expected):
 plan=json.loads((S/'installation-plan.json').read_text());path=Path(plan['bridge_live_path']);require(sha(path)==expected,'Live bridge source changed');st=path.stat();temp=path.with_name(path.name+'.telegram-recovery.tmp')
 with temp.open('xb') as f:f.write(content)
 os.chown(temp,st.st_uid,st.st_gid);temp.chmod(st.st_mode&0o777);os.replace(temp,path)

def bridge_state(expected):
 p=json.loads((S/'installation-plan.json').read_text());require(sha(p['bridge_live_path'])==expected,'Bridge file mismatch');require(sha('/etc/systemd/system/eva.service')==p['bridge_unit_file_sha256'],'Service unit changed')
 raw=command(['systemctl','show','eva.service','--property=MainPID,ActiveState,SubState,ExecMainStartTimestamp,WorkingDirectory']);d=dict(l.split('=',1) for l in raw.splitlines());require(d['ActiveState']=='active' and d['SubState']=='running' and int(d['MainPID'])>0 and d['WorkingDirectory']=='/home/administrator/project/Bottie','Bridge service not running');return d

def main():
 with (S/'installation.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);p=json.loads((S/'installation-plan.json').read_text());go=json.loads((S/'installation-go.json').read_text())
  require(go=={'decision':'GO','plan_sha256':sha(S/'installation-plan.json'),'script_sha256':sha(__file__),'core_image':p['core_image'],'bridge_sha256':p['bridge_candidate_sha256'],'restart_eva_service':True},'Concrete installation GO missing or different')
  require(not(S/'installation-attempt.json').exists(),'Installation already attempted')
  require(sha(S/'bridge.candidate.js')==p['bridge_candidate_sha256'] and sha(S/'bridge.original.js')==p['bridge_parent_sha256'],'Bridge staged bytes changed')
  before=guard(p['core_parent'],True);oldbridge=bridge_state(p['bridge_parent_sha256']);save('installation-preflight.json',{'core':before,'bridge':oldbridge});save('installation-attempt.json',{'maximum_attempts':1,'model_calls':0})
  core_changed=False;bridge_changed=False
  try:
   core_changed=True;save('core-install-log.json',{'output':command(p['core_activation_command'])});save('core-installed.json',wait_guard(p['core_image']))
   save('before-bridge-cutover.json',guard(p['core_image']))
   replace_bridge((S/'bridge.candidate.js').read_bytes(),p['bridge_parent_sha256']);bridge_changed=True
   save('bridge-restart-log.json',{'output':command(p['bridge_restart_command'])});time.sleep(3);state=bridge_state(p['bridge_candidate_sha256']);require(state['MainPID']!=oldbridge['MainPID'],'Bridge process did not restart');save('bridge-installed.json',state);save('installation-final.json',guard(p['core_image']))
  except BaseException as failure:
   errors=[]
   if bridge_changed:
    try:replace_bridge((S/'bridge.original.js').read_bytes(),p['bridge_candidate_sha256']);command(p['bridge_restart_command']);time.sleep(3);save('bridge-rollback.json',bridge_state(p['bridge_parent_sha256']))
    except BaseException as error:errors.append('bridge: '+str(error))
   if core_changed:
    try:command(p['core_rollback_command']);save('core-rollback.json',wait_guard(p['core_parent']))
    except BaseException as error:errors.append('core: '+str(error))
   save('installation-failed.json',{'failure':str(failure),'rollback_verified':not errors,'rollback_errors':errors})
   if errors:raise RuntimeError('ROLLBACK NOT VERIFIED; stop and inspect preserved evidence') from failure
   raise
 print('Exact core and bridge installed; no model or Telegram request sent')
if __name__=='__main__':main()
