"""Retained, uniquely named isolated gateway fixture; no models or live resources."""
import hashlib,importlib.util,json,os,pathlib,secrets,subprocess,time,yaml,sys,shutil
ROOT=pathlib.Path('/home/administrator/project/Eva2/eva2')
STAGE=pathlib.Path(os.environ.get('EVA_ATOMIC_PROOF_STAGE','/home/administrator/project/.eva2-artifacts/workspace-broker-v2-20260913'))
HELPER=ROOT/'experiments/tool01-replacement-acceptance-v2/run_physical_acceptance.py'
spec=importlib.util.spec_from_file_location('existing_acceptance_helpers',HELPER);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
assert hashlib.sha256(HELPER.read_bytes()).hexdigest()=='a8dc2a31ea327a54f70850669c0c2c3f3b40893a7bb80863df6bdeb09527d3be'
IMAGE=os.environ.get('EVA_ATOMIC_PROOF_IMAGE','sha256:0ef19e699069c3a5d3a26812c5da3d31141fcd5c3faedb14f92c9dcce1209465')
CF='sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd'
def main():
 STAGE.mkdir(parents=True,exist_ok=True,mode=0o700)
 nonce=secrets.token_hex(5);prefix='eva-atomic-gateway-'+nonce;stage=STAGE/prefix;stage.mkdir(mode=0o700)
 paths={k:stage/k for k in ['work','journal','conf','database']}
 for p in paths.values():p.mkdir(mode=0o700)
 conf=paths['conf'];password=secrets.token_urlsafe(32)
 for key in ['contextforge-jwt','contextforge-encryption','contextforge-admin-password']:
  (conf/key).write_text(password if key.endswith('password') else secrets.token_hex(32));(conf/key).chmod(0o400)
 names={k:prefix+'-'+k for k in ['provider','gateway','network']}
 user=str(os.getuid())+':'+str(os.getgid())
 mounts=['--mount',f'type=bind,src={paths["work"]},dst=/workspace','--mount',f'type=bind,src={paths["journal"]},dst=/journal','--mount',f'type=bind,src={conf},dst=/run/eva2,readonly']
 info=json.loads(h._docker('run','--rm','--network','none','--read-only','--user',user,*mounts,'--entrypoint','node',IMAGE,'-e',"const fs=require('fs'),c=require('crypto');const st=p=>{let s=fs.statSync(p);return {dev:s.dev,ino:s.ino}};console.log(JSON.stringify({root:st('/workspace'),journal:st('/journal'),binary_sha256:fs.readFileSync('/opt/eva2/atomic/workspace-writer-broker.sha256','utf8').trim(),client_sha256:c.createHash('sha256').update(fs.readFileSync('/opt/eva2/atomic/broker-client.mjs')).digest('hex')}))"))
 info.update(version=1,project_journal_identity=secrets.token_hex(32));binding=json.dumps(info,sort_keys=True).encode();(conf/'atomic-binding.json').write_bytes(binding);(conf/'atomic-binding.json').chmod(0o400)
 result={'names':names,'stage':str(stage),'providerImage':IMAGE,'gatewayImage':CF,'providerBindingSha256':hashlib.sha256(binding).hexdigest(),'helperSha256':hashlib.sha256(HELPER.read_bytes()).hexdigest(),'cleanupOwner':'root coordinator after core integration proof','retained':True}
 (STAGE/'retained-stack.json').write_text(json.dumps(result,indent=2))
 h._docker('network','create','--internal',names['network'])
 base=['run','--detach','--pull','never','--user',user,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','768m','--network',names['network'],'--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777']
 env={'EVA_CONTEXTFORGE_JWT_SECRET_FILE':'/run/eva2/contextforge-jwt','EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE':'/run/eva2/contextforge-encryption','EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE':'/run/eva2/contextforge-admin-password'}
 args=base+['--name',names['provider'],*mounts]
 for k,v in env.items():args+=['--env',k+'='+v]
 h._docker(*args,IMAGE)
 cfg=yaml.safe_load((ROOT/'experiments/contextforge-gateway-v1/compose.yml').read_text())['services']['contextforge']['environment']
 cfg.update(env,DATABASE_URL='sqlite:////data/contextforge.db',SSRF_ALLOW_PRIVATE_NETWORKS='true',CACHE_TYPE='none')
 args=base+['--name',names['gateway'],'--mount',f'type=bind,src={conf},dst=/run/eva2,readonly','--mount',f'type=bind,src={paths["database"]},dst=/data','--mount',f'type=bind,src={ROOT}/experiments/contextforge-gateway-v1/start-contextforge.sh,dst=/run/start.sh,readonly']
 for k,v in cfg.items():args+=['--env',str(k)+'='+str(v)]
 h._docker(*args,'--entrypoint','/bin/sh',CF,'/run/start.sh');h._wait_contextforge(names['gateway'])
 token=h._login_contextforge(names['gateway'],password);tokenpath=stage/'gateway-token';tokenpath.write_text(token);tokenpath.chmod(0o600)
 gid=h._register_gateway(names['gateway'],token,name='atomic_'+nonce,url='http://'+names['provider']+':9000/mcp')
 selected={k:h._make_tool_public(names['gateway'],token,h._wait_gateway_tool(names['gateway'],token,gateway_id=gid,original_name=k)) for k in ['workspace_create_bound','workspace_observe_bound']}
 assert all(result['providerBindingSha256'] in row['description'] for row in selected.values())
 result.update(gatewayId=gid,selectedTools=selected,tokenFile=str(tokenpath),coreAccess='Join isolated network; URL http://'+names['gateway']+':4444')
 (stage/'stack.json').write_text(json.dumps(result,indent=2))
 if '--prepare-only' in sys.argv:
  second={k:stage/(k+'-stop') for k in ['work','journal','conf']}
  for p in second.values():p.mkdir(mode=0o700)
  for key in ['contextforge-jwt','contextforge-encryption','contextforge-admin-password']:shutil.copy2(conf/key,second['conf']/key)
  mounts2=['--mount',f'type=bind,src={second["work"]},dst=/workspace','--mount',f'type=bind,src={second["journal"]},dst=/journal','--mount',f'type=bind,src={second["conf"]},dst=/run/eva2,readonly']
  stats=json.loads(h._docker('run','--rm','--network','none','--read-only','--user',user,*mounts2,'--entrypoint','node',IMAGE,'-e',"const fs=require('fs');const st=p=>{let s=fs.statSync(p);return {dev:s.dev,ino:s.ino}};console.log(JSON.stringify({root:st('/workspace'),journal:st('/journal')}))"))
  info2={**info,**stats,'project_journal_identity':secrets.token_hex(32)}
  if os.environ.get('EVA_ATOMIC_REPEATABLE_PROOF')=='1':
   resolver_hash=h._docker('run','--rm','--network','none','--read-only','--entrypoint','node',IMAGE,'-e',"const fs=require('fs'),c=require('crypto');console.log(c.createHash('sha256').update(fs.readFileSync('/opt/eva2/atomic/project-journal.mjs')).digest('hex'))")
   info2.update(version=2,journal_mode='per_path_prefix_v1',resolver_sha256=resolver_hash)
  binding2=json.dumps(info2,sort_keys=True).encode();(second['conf']/'atomic-binding.json').write_bytes(binding2);(second['conf']/'atomic-binding.json').chmod(0o400)
  provider2=prefix+'-provider-stop';args2=base+['--name',provider2,*mounts2]
  for k,v in env.items():args2+=['--env',k+'='+v]
  h._docker(*args2,IMAGE);h._wait_filesystem_mcp(provider2)
  gid2=h._register_gateway(names['gateway'],token,name='atomic_stop_'+nonce,url='http://'+provider2+':9000/mcp')
  selected2={k:h._make_tool_public(names['gateway'],token,h._wait_gateway_tool(names['gateway'],token,gateway_id=gid2,original_name=k)) for k in ['workspace_create_bound','workspace_observe_bound']}
  assert all(hashlib.sha256(binding2).hexdigest() in row['description'] for row in selected2.values())
  result.update(status='prepared-only',journalGenesis='empty directories; no broker dispatch',rootGenesis='empty directories',instances={'happy':{'provider':names['provider'],'selectedTools':selected,'root':str(paths['work']),'journal':str(paths['journal']),'bindingFile':str(conf/'atomic-binding.json')},'stop':{'provider':provider2,'selectedTools':selected2,'root':str(second['work']),'journal':str(second['journal']),'bindingFile':str(second['conf']/'atomic-binding.json')}})
  assert list(second['work'].iterdir())==[] and list(second['journal'].iterdir())==[]

  assert list(paths['work'].iterdir())==[] and list(paths['journal'].iterdir())==[]
  (stage/'stack.json').write_text(json.dumps(result,indent=2));(STAGE/'core-prepared-stack.json').write_text(json.dumps(result,indent=2))
  print(json.dumps({'status':result['status'],'stage':str(stage)}));return

 req={'operation_id':'1'*64,'request_binding_sha256':'2'*64,'expected_generation':0,'path':'gateway-proof.txt','content':'Gateway → immutable artifact\n'}
 def call(name,args):return h._gateway_request(names['gateway'],'POST','/rpc',token=token,payload={'jsonrpc':'2.0','id':name,'method':'tools/call','params':{'name':selected[name]['name'],'arguments':args}})
 created=call('workspace_create_bound',req);assert not created.get('error') and not created['result'].get('isError'),created
 observed=call('workspace_observe_bound',req);assert observed['result']==created['result']
 denied=call('workspace_observe_bound',dict(req,request_binding_sha256='3'*64));assert denied['result']['isError']
 before=(paths['journal']/'workspace-writer.journal').read_bytes();h._docker('restart',names['provider']);h._wait_filesystem_mcp(names['provider'])
 observed2=call('workspace_observe_bound',req);assert observed2['result']==created['result'];assert (paths['journal']/'workspace-writer.journal').read_bytes()==before
 result.update(status='passed',gatewayId=gid,selectedTools=selected,request=req,created=created,observed=observed,denied=denied,observedAfterRestart=observed2,tokenFile=str(tokenpath),coreAccess='Join only this isolated internal network; gateway URL http://'+names['gateway']+':4444; no published ports',limits=['Fixture journal now generation1; do not assume fresh genesis for core plan','No model/live owner/project mounts','Public visibility only inside unique isolated fixture; not a production authorization proof'])
 (stage/'stack.json').write_text(json.dumps(result,indent=2));(STAGE/'retained-stack.json').write_text(json.dumps(result,indent=2));print(json.dumps({'status':result['status'],'names':names,'tokenFile':str(tokenpath)}))
if __name__=='__main__':main()
