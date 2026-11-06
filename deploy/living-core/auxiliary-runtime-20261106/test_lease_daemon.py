from pathlib import Path
import json,os,subprocess,time,tempfile,hashlib
import sys
P=Path(sys.argv[1]).resolve();results={}
for role,fmt,gid,mode in [('eva2-managed-b-slot3-model-access-lease-1','eva-lease',65532,0o2750),('eva2-managed-b-slot3-hindsight-access-lease-1','codex-consumer',1000,0o700)]:
 image=(P/role/'final-image.txt').read_text().strip();d=Path(tempfile.mkdtemp(prefix='daemon-contract-',dir=P));owner=d/'owner';owner.mkdir();owner.chmod(0o700);os.chown(owner,1000,1000);lease=d/'lease';lease.mkdir();lease.chmod(mode);os.chown(lease,1000,gid);source=owner/'auth.json';target=lease/'auth.json'
 def write(token):
  new=owner/'next';new.write_text(json.dumps({'tokens':{'access_token':token,'account_id':'synthetic-account','refresh_token':'NEVER-PUBLISH-REFRESH'}}));new.chmod(0o600);os.chown(new,1000,1000);new.replace(source)
 def wait(predicate):
  end=time.monotonic()+8
  while time.monotonic()<end:
   try:
    v=json.loads(target.read_text())
    if predicate(v):return v
   except (OSError,ValueError):pass
   time.sleep(0.02)
  raise AssertionError('Timed out waiting for synthetic lease transition')
 def token(v):return v.get('tokens',{}).get('access_token') if fmt=='codex-consumer' else v.get('access_token')
 write('synthetic-A');args=['docker','run','-d','--network','none','--read-only','--cpus','1','--memory','256m','--user','1000:'+str(gid),'--mount','type=bind,src='+str(owner)+',dst=/authority,readonly','--mount','type=bind,src='+str(lease)+',dst=/lease','--entrypoint','/usr/local/bin/python',image,'-B','-m','eva.operations.model_credentials','--source','/authority/auth.json','--target','/lease/auth.json','--format',fmt,'--interval-seconds','0.25'];cid=subprocess.check_output(args,text=True).strip()
 try:
  a=wait(lambda v:token(v)=='synthetic-A');assert 'refresh_token'not in json.dumps(a);lock=lease/'auth.lock';inode=lock.stat().st_ino if lock.exists()else None;write('synthetic-B');wait(lambda v:token(v)=='synthetic-B');source.unlink();wait(lambda v:v.get('contract')=='EvaModelAccessLeaseUnavailable/v1');st=target.stat();assert st.st_uid==1000 and st.st_gid==gid and (st.st_mode&0o777)==(0o600 if fmt=='codex-consumer'else 0o640);assert (lease.stat().st_mode&0o7777)==mode
  tamper=False
  if fmt=='codex-consumer':
   assert lock.stat().st_ino==inode;lock.chmod(0o644);write('synthetic-C');time.sleep(0.6);v=json.loads(target.read_text());assert v.get('contract')=='EvaModelAccessLeaseUnavailable/v1';assert lock.stat().st_ino==inode and lock.stat().st_mode&0o777==0o644;tamper=True;lock.chmod(0o600)
  else:write('synthetic-C')
  wait(lambda v:token(v)=='synthetic-C');subprocess.run(['docker','stop','--time','3',cid],check=True,stdout=subprocess.DEVNULL);info=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0];assert not info['State']['Running'];logs=subprocess.check_output(['docker','logs',cid],stderr=subprocess.STDOUT,text=True);assert all(t not in logs for t in ['synthetic-A','synthetic-B','synthetic-C','NEVER-PUBLISH-REFRESH']);results[role]={'image':image,'actualUid':1000,'actualGid':gid,'mainLoopRotationThenUnavailableThenRecovery':True,'atomicTargetOwnerModeCorrect':True,'unsafeLockNotRepaired':tamper if fmt=='codex-consumer'else 'not_applicable','stoppedWithoutRetryOrExternalEffects':True,'externalCalls':0,'syntheticOnly':True};print(role,'PASS',flush=True)
 finally:subprocess.run(['docker','rm','-f',cid],stdout=subprocess.DEVNULL,check=True)
(P/'lease-daemon-results.json').write_text(json.dumps(results,indent=2)+'\n')
