"""Pre-reviewed, exact-entry original input gate for the existing native parent.
No case selection, sample creation, clock creation, rearm, SDK or tool dispatch.
"""
from pathlib import Path
import hashlib,json,os,subprocess,time,math
from urllib.request import urlopen

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
def zero(value):return type(value) is int and value==0
def zeros(value,keys):return type(value) is dict and set(value)==set(keys) and all(zero(v) for v in value.values())
def finite(value):return type(value) in (int,float) and math.isfinite(value)
def proc_starttime(pid):return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
def require(value,message):
    if value is not True:raise ValueError(message)

def read_authority(path):
    import sqlite3
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as c:
        rows=c.execute('select participant,sample_id,family,value,revision from samples order by participant,sample_id').fetchall()
        meta=c.execute('select value from metadata where id=1').fetchone()[0]
    return {'metadata_sha256':hashlib.sha256(meta.encode()).hexdigest(),'rows':[{'participant':p,'sample_id':s,'family':f,'value':json.loads(v),'raw_sha256':hashlib.sha256(v.encode()).hexdigest(),'revision':r} for p,s,f,v,r in rows]}

def observe_workers(policy):
    observed={}
    for kind,spec in policy['workers'].items():
        item=json.loads(subprocess.check_output(['docker','inspect',spec['name']],text=True))[0]
        require(item['State']['Running'] is True,'native worker is not running')
        require(item['Image']==spec['image'],'native image differs')
        require(item['Config']['Labels'].get('eva2.probe')==policy['resource_label'],'native owner label differs')
        require(item['Config']['User']=='1000:65532','native UID/GID differs')
        env=dict(v.split('=',1) for v in item['Config']['Env'] if '=' in v)
        require(all(env.get(k)==v for k,v in spec['model_env'].items()),'native model roles differ')
        network=item['NetworkSettings']['Networks'][policy['network_name']]
        with urlopen('http://'+network['IPAddress']+':8080'+spec['health_path'],timeout=2) as response:
            require(response.status==200,'native health response differs');health=json.loads(response.read())
        pid=item['State']['Pid'];require(type(pid) is int and pid>0,'INVALID_NATIVE_PID')
        mounts={m['Destination']:m for m in item['Mounts']};require(len(mounts)==len(item['Mounts']),'DUPLICATE_MOUNT_TARGET')
        nspid=next(line.split()[1:] for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if line.startswith('NSpid:'))
        instance={'id':item['Id'],'pid':pid,'proc_starttime':proc_starttime(pid),'started_at':item['State']['StartedAt'],'mounts_sha256':hashlib.sha256(json.dumps(mounts,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'namespace_pid':int(nspid[-1]),'pid_namespace':os.readlink('/proc/'+str(pid)+'/ns/pid')}
        observed[kind]={'instance':instance,'health':health,'model_roles_match':True,'uid':1000,'gid':65532}
    return observed

def validate(policy,ready,live,authority,workers,now,boot_id,parent_pid,parent_starttime):
    require(policy['contract']=='PreReviewedExactNativeInputPolicy/v1','policy schema differs')
    require(policy['decision']=='CONDITIONAL_ONE_ORIGINAL_INPUT_AFTER_EXACT_NATIVE_READY','policy is not admitted')
    require(policy['maximum_inputs']==1 and policy['no_sample_or_deadline_creation'] is True,'policy scope differs')
    require(ready['contract']=='MatchingEva2NativeReadyBeforeOriginalInput/v1','ready schema differs')
    require(ready['entry']==policy['entry'],'entry differs')
    require(type(ready['pid']) is int and ready['pid']==parent_pid,'parent PID differs')
    require(ready['parent_starttime']==parent_starttime,'parent starttime differs')
    require(ready['source_admission_sha256']==policy['source_admission_sha256'],'source admission differs')
    deadline=ready['absolute_deadline']
    require(deadline['boot_id']==boot_id and type(deadline['wall_seconds']) is int and deadline['wall_seconds']==840,'clock domain or original wall limit differs')
    require(finite(now) and finite(deadline['monotonic_start']) and finite(deadline['monotonic_deadline']) and deadline['monotonic_deadline']>now,'original deadline expired')
    require(deadline['monotonic_deadline']-deadline['monotonic_start']==840,'original deadline span differs')
    require(ready['original_input_sent'] is False,'original input already sent')
    for name in ('provider_attempts','reserved_actions'):require(type(ready[name]) is int and ready[name]==0,'ready dispatch count differs')
    require(zeros(ready['pre_input_dispatches'],('sdk','tunnel')),'pre-input transport dispatch occurred')
    require(set(workers)==set(('core','memory','delegated')),'worker set differs')
    for healths in (ready['health'],{k:v['health'] for k,v in workers.items()}):
        require(healths['core'].get('status')=='alive','core unhealthy')
        require(healths['memory'].get('status')=='healthy' and healths['memory'].get('database')=='connected','memory unhealthy')
        require(healths['delegated'].get('contract')=='EvaWorkerLiveness/v1' and healths['delegated'].get('state')=='ready','delegated unhealthy')
    require(set(ready['native_instances'])==set(workers),'NATIVE_INSTANCE_SET_MISMATCH')
    for kind,m in ready['mounted_source_readbacks'].items():
        instance=workers[kind]['instance']
        require(ready['native_instances'][kind]==instance,'NATIVE_INSTANCE_CHANGED')
        require(type(m['pid']) is int and m['pid']==instance['namespace_pid'] and m['proc_starttime']==instance['proc_starttime'] and m['pid_namespace']==instance['pid_namespace'],'READCHECK_INSTANCE_MISMATCH')
        require(kind in workers and m['uid']==1000 and m['gid']==65532 and m['before_capture_and_native_worker'] is True,'native readcheck identity differs')
        checks=m['checks'];expected=policy['actual_mount_maps'][kind]
        require(len(checks)==len(expected) and len({c['target'] for c in checks})==len(checks),'mount map cardinality differs')
        by={c['target']:c for c in checks}
        require(all(e['target'] in by and by[e['target']]['readable'] is True and by[e['target']]['sha256']==e['sha256'] for e in expected),'actual native mount hash/readability differs')
    require(set(ready['mounted_source_readbacks'])==set(workers),'native readchecks missing')
    reader=ready['reader_readiness']
    require(reader['contract']=='CanonicalReaderBeforeInput/v1' and reader['wrapper_sha256']==policy['reader_wrapper_sha256'] and reader['canonical_sha256']==policy['canonical_reader_sha256'] and reader['static_envelope_sha256']==policy['first_body_static_sha256'],'READER_BINDING_MISMATCH')
    require(type(reader['body_sha256']) is str and len(reader['body_sha256'])==64 and all(c in '0123456789abcdef' for c in reader['body_sha256']),'INVALID_ACTUAL_BODY_HASH')
    require(reader['reply_absent'] is True and reader['memory_settled'] is False and reader['ordinary_role_scope_verified'] is True and reader['read_only_verified'] is True and reader['memory_engine_unchanged'] is True,'READER_NOT_READY')
    for parent in (ready['parent_before_input'],live):
        require(parent['errors']==[],'parent accounting error')
        ledger=parent['ledger'];require(ledger['sample_id']==policy['entry'] and ledger['attempts']==[] and ledger['stop'] is None and ledger['stop_requested'] is False and zero(ledger['active_async_attempts']),'live SDK ledger not ready')
        actions=parent['actions'];require(zero(actions['reserved_dispatches']) and zero(actions['pending']) and actions['attempts']==[] and actions['refused']==[],'action accounting not ready')
        require(set(parent['workers'])==set(workers),'control worker set differs')
        require(all(w['seen'] is True and w['connected'] is True and w['terminal'] is None and w['disconnect'] is None and zero(w['unsettled_events']) for w in parent['workers'].values()),'native control not ready')
    require(authority['metadata_sha256']==policy['authority_metadata_sha256'],'aggregate metadata differs')
    own=[r for r in authority['rows'] if r['participant']=='eva2' and r['sample_id']==policy['entry']]
    require(len(own)==1,'exact own row missing or ambiguous');row=own[0];value=row['value']
    require(zero(row['revision']) and value['kind']=='active' and value['usage_unknown'] is False and value['envelope_exceeded'] is False,'own row not fresh active')
    require(value['identity']==policy['own_identity'] and value['reservation']==policy['full_original_reservation'],'original identity/reservation differs')
    require(value['report']=={'attempts':[],'stop':None,'stop_requested':False},'own row has dispatch or stop')
    historical=[{k:r[k] for k in ('participant','sample_id','family','raw_sha256','revision')} for r in authority['rows'] if r not in own]
    require(historical==policy['historical_rows'],'historical values/revisions differ')
    return {'entry':policy['entry'],'same_parent':True,'original_clock_remaining_seconds':deadline['monotonic_deadline']-now,'three_workers_ready':True,'zero_pre_input_dispatch':True,'history_preserved':True}

def _write_once(p,value):
    raw=(json.dumps(value,indent=2)+'\n').encode();fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd=os.open(Path(p).parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)

def release_original_input(policy_path,policy_sha256,review_path,review_sha256,ready_path,controller,pre_input_dispatches):
    require(os.geteuid()==0,'root parent required')
    require(sha(policy_path)==policy_sha256 and sha(review_path)==review_sha256,'pre-reviewed policy binding differs')
    policy=json.loads(Path(policy_path).read_bytes());review=json.loads(Path(review_path).read_bytes())
    require(review['decision']=='GO_CONDITIONAL_EXACT_PROGRAMMED_INPUT_GATE' and review['policy_sha256']==policy_sha256 and review['gate_source_sha256']==sha(__file__),'independent conditional GO differs')
    for path,digest in policy['file_sha256'].items():require(sha(path)==digest,'immutable source/resource binding differs')
    base=Path(policy['private_base']).resolve();require(base.stat().st_uid==0 and base.stat().st_mode&0o077==0,'private gate root is not protected')
    outputs=[Path(policy[k]) for k in ('final_admission_path','release_path','consumed_path')]
    require(all(p.resolve().is_relative_to(base) and not p.exists() for p in outputs),'prior input release or invalid output path')
    require(not Path(policy['owner_stop_path']).exists(),'owner stop requested')
    ready=json.loads(Path(ready_path).read_bytes());deadline_path=Path(policy['absolute_deadline_path'])
    require(sha(deadline_path)==ready['absolute_deadline_sha256'] and json.loads(deadline_path.read_bytes())==ready['absolute_deadline'],'absolute clock receipt differs')
    require(sha(policy['source_admission_path'])==policy['source_admission_sha256'],'source admission changed')
    for kind,path in policy['actual_mount_map_paths'].items():
        policy['actual_mount_maps'][kind]=json.loads(Path(path).read_bytes())
    pid=os.getpid();start=proc_starttime(pid);boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(zeros(pre_input_dispatches,('sdk','tunnel')),'live transport attempted before input')
    summary=validate(policy,ready,controller.report(),read_authority(policy['authority_path']),observe_workers(policy),time.monotonic(),boot,pid,start)
    final={'contract':'MatchingEva2OriginalInputAdmission/v1','admitted':True,'original_input_admitted':True,'entry':policy['entry'],'native_ready_sha256':sha(ready_path),'source_admission_sha256':policy['source_admission_sha256'],'parent_pid':pid,'parent_starttime':start,'absolute_deadline_sha256':sha(deadline_path),'programmed_policy_sha256':policy_sha256,'independent_review_sha256':review_sha256,'validated':summary}
    _write_once(outputs[0],final)
    require(time.monotonic()<ready['absolute_deadline']['monotonic_deadline'] and not Path(policy['owner_stop_path']).exists(),'deadline/stop after final admission retention')
    require(zeros(pre_input_dispatches,('sdk','tunnel')) and controller.report()['ledger']['stop'] is None,'live stop or dispatch before deploy/release')
    release={'contract':'MatchingEva2OriginalInputRelease/v1','final_admission_sha256':sha(outputs[0]),'native_ready_sha256':sha(ready_path),'absolute_deadline_sha256':sha(deadline_path),'parent_pid':pid,'parent_starttime':start,'entry':policy['entry'],'programmed_policy_sha256':policy_sha256}
    _write_once(outputs[1],release)
    return {'final_sha256':sha(outputs[0]),'release_sha256':sha(outputs[1]),'validated':summary}
