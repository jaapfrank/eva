"""Disposable translate -> SDK -> native proof; no live mounts/catalog."""
import hashlib,json,os,pathlib,secrets,subprocess,tempfile,time,urllib.request,uuid
IMAGE=os.environ.get('EVA_ATOMIC_PROOF_IMAGE','eva2-atomic-workspace:isolated-20260913')
DYNAMIC=os.environ.get('EVA_ATOMIC_PROOF_JOURNAL_MODE')=='per_path_prefix_v1'
def docker(*args):return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.PIPE).strip()
def main():
 with tempfile.TemporaryDirectory(prefix='eva-atomic-mcp-') as temp:
  root=pathlib.Path(temp); work=root/'work';journal=root/'journal';conf=root/'conf'
  for p in [work,journal,conf]:p.mkdir(mode=0o700)
  for name in ['jwt','encryption','admin']:(conf/name).write_text(secrets.token_hex(32));(conf/name).chmod(0o400)
  mounts=['-v',str(work)+':/workspace','-v',str(journal)+':/journal','-v',str(conf)+':/run/eva2:ro']
  user=str(os.getuid())+':'+str(os.getgid())
  info=json.loads(docker('run','--rm','--network','none','--read-only','--user',user,*mounts,'--entrypoint','node',IMAGE,'-e',"const fs=require('fs'),c=require('crypto');const st=p=>{let s=fs.statSync(p);return {dev:s.dev,ino:s.ino}};console.log(JSON.stringify({root:st('/workspace'),journal:st('/journal'),binary_sha256:fs.readFileSync('/opt/eva2/atomic/workspace-writer-broker.sha256','utf8').trim(),client_sha256:c.createHash('sha256').update(fs.readFileSync('/opt/eva2/atomic/broker-client.mjs')).digest('hex')}))"))
  info.update(version=1,project_journal_identity=secrets.token_hex(32))
  if DYNAMIC:
   resolver_hash=docker('run','--rm','--network','none','--read-only','--user',user,'--entrypoint','node',IMAGE,'-e',"console.log(require('crypto').createHash('sha256').update(require('fs').readFileSync('/opt/eva2/atomic/project-journal.mjs')).digest('hex'))")
   info.update(version=2,journal_mode='per_path_prefix_v1',resolver_sha256=resolver_hash)
  prefix='1'*64+'-' if DYNAMIC else ''
  project_journal=journal/('1'*64) if DYNAMIC else journal
  binding=json.dumps(info,sort_keys=True).encode();(conf/'atomic-binding.json').write_bytes(binding);(conf/'atomic-binding.json').chmod(0o400)
  name='eva-atomic-proof-'+uuid.uuid4().hex[:10];network=name+'-net'
  docker('network','create','--internal',network)
  try:
   docker('run','-d','--name',name,'--network',network,'--read-only','--user',user,'--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,mode=1777','-p','127.0.0.1::9000',*mounts,'-e','EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/eva2/jwt','-e','EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/eva2/encryption','-e','EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/eva2/admin',IMAGE)
   url='http://127.0.0.1:9000/mcp'
   seq=0
   def rpc(method,params):
    nonlocal seq
    seq+=1;data=json.dumps({'jsonrpc':'2.0','id':seq,'method':method,'params':params}).encode()
    code="import urllib.request,sys; r=urllib.request.Request('http://127.0.0.1:9000/mcp',sys.argv[1].encode(),{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'}); print(urllib.request.urlopen(r,timeout=30).read().decode())"
    return json.loads(docker('exec',name,'python3','-c',code,data.decode()))
   for _ in range(40):
    try: init=rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'isolated-proof','version':'1'}});break
    except Exception:time.sleep(.25)
   else:raise RuntimeError('fixture startup failed')
   tools=rpc('tools/list',{})['result']['tools'];assert len(tools)==2
   assert all(hashlib.sha256(binding).hexdigest() in t['description'] for t in tools)
   request={'operation_id':'a'*64,'request_binding_sha256':'b'*64,'expected_generation':0,'path':prefix+'one.txt','content':'Hallo café\n'}
   def call(tool,args): return rpc('tools/call',{'name':tool,'arguments':args})['result']
   absent=call('workspace_observe_bound',request);assert absent['isError'];assert list(journal.iterdir())==[]
   created=call('workspace_create_bound',request);assert not created.get('isError'),created
   assert created['structuredContent']['effect']=='created'
   assert created['structuredContent']['content']==request['content']
   before=(project_journal/'workspace-writer.journal').read_bytes()
   observed=call('workspace_observe_bound',request);assert observed==created
   assert (project_journal/'workspace-writer.journal').read_bytes()==before
   for changed in [dict(request,path='../escape'),dict(request,path='sub/file'),dict(request,executable='/bin/sh'),dict(request,request_binding_sha256='c'*64)]:
    assert call('workspace_create_bound',changed)['isError']
   assert (work/(prefix+'one.txt')).read_text()==request['content']
   docker('restart',name)
   for _ in range(40):
    try: recovered=call('workspace_observe_bound',request);break
    except Exception:time.sleep(.25)
   else:raise RuntimeError('restart failed')
   assert recovered==created
   (work/(prefix+'two.txt')).write_text('already available');(work/(prefix+'two.txt')).chmod(0o600)
   noop=call('workspace_create_bound',dict(request,operation_id='c'*64,expected_generation=1,path=prefix+'two.txt',content='already available'))
   assert noop['isError'] and noop['structuredContent']['effect']=='noop'
   denied=subprocess.run(['docker','run','--rm','--network','none','--read-only','--user','12345:12345',*mounts,'--entrypoint','node',IMAGE,'-e',"try{require('fs').readFileSync(process.argv[1]);process.exit(0)}catch(e){process.exit(e.code==='EACCES'?13:2)}",'/workspace/'+prefix+'one.txt'],capture_output=True)
   assert denied.returncode==13
   (work/(prefix+'one.txt')).write_text('externally changed')
   assert call('workspace_observe_bound',request)['isError']
   assert (work/(prefix+'one.txt')).read_text()=='externally changed'
   if DYNAMIC:
    for digit in ['2','3']:
     other=dict(request,path=digit*64+'-other.txt')
     assert not call('workspace_create_bound',other).get('isError')
     assert not call('workspace_observe_bound',other).get('isError')
    assert len(list(journal.iterdir()))==3
   print(json.dumps({'journal_mode':info.get('journal_mode','fixed'),'passed':True,'transport':'ContextForge translate HTTP -> SDK stdio -> native','gateway_registration':False,'provider_binding_sha256':hashlib.sha256(binding).hexdigest(),'tests':['closed schema','binding in tool descriptions','observe absent does not create journal','created receipt','observe byte-identical and journal unchanged','invalid path and extra executable denied','changed binding denied','changed file denied','same receipt after process restart','noop is not create success','different uid denied private root'],'limits':['no live mounts','no owner mandate or stop state checked','no gateway catalog registration','no crash injection in this HTTP fixture; native crash suite separate']}))
  finally:
   subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(['docker','network','rm',network],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
if __name__=='__main__':main()
