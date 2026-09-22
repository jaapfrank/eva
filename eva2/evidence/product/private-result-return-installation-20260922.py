"""Reviewed three-image installation. Default is read-only preflight; never force-kill."""
import argparse, base64, copy, hashlib, hmac, json, os, pathlib, re, subprocess, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from uuid import UUID

P = pathlib.Path('/var/lib/eva2/private-result-return-20260922')
RECORD = pathlib.Path('/var/lib/eva2/managed-b-slot3/core-worker-active-compose.json')
MEDIA_ENV = pathlib.Path('/var/lib/eva2/voice-session/runtime.env')
DB = 'eva2-managed-b-slot3-postgres-1'
SERVICES = {'core': 'core', 'worker': 'delegated-work-worker', 'media': 'media'}
TERMINAL = {'succeeded', 'verified', 'failed', 'ambiguous', 'needs_help', 'stopped'}

def require(value, message):
    if not value: raise RuntimeError(message)

def run(argv, data=None, timeout=60):
    return subprocess.run(argv, input=data, text=True, check=True, capture_output=True, timeout=timeout).stdout.strip()

def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(path): return json.loads(path.read_text())
def inspect(name): return json.loads(run(['docker', 'inspect', name]))[0]
def render(argv, overlay=None):
    return json.loads(run(argv + (['-f', '-'] if overlay else []) + ['--profile', '*', 'config', '--format', 'json'], json.dumps(overlay) if overlay else None))

def host(value):
    value = copy.deepcopy(value)
    value['Mounts'] = sorted(value.get('Mounts', []), key=lambda item: item['Target'])
    return value

def configuration(value):
    value = copy.deepcopy(value)
    value.pop('Hostname', None); value.pop('Image', None)
    value['Env'] = dict(row.split('=', 1) for row in value.get('Env', []))
    value['Labels'] = {k: v for k, v in (value.get('Labels') or {}).items() if not k.startswith('com.docker.compose.')}
    return value

def mounts(value): return sorted(value['Mounts'], key=lambda item: item['Destination'])

def runtime_equal(current, previous, image):
    require(current['Image'] == image, 'runtime image changed')
    require(configuration(current['Config']) == configuration(previous['Config']), 'runtime configuration changed')
    require(host(current['HostConfig']) == host(previous['HostConfig']), 'runtime host configuration changed')
    require(mounts(current) == mounts(previous), 'runtime mounts changed')
    require(set(current['NetworkSettings']['Networks']) == set(previous['NetworkSettings']['Networks']), 'runtime networks changed')
    require(current['State']['Running'], 'runtime not running')

def effective_matches(document, service, current):
    """Cover every declared field of these three existing Compose services."""
    r = document['services'][service]; c = current['Config']; h = current['HostConfig']
    allowed = {'profiles','cap_drop','command','container_name','depends_on','entrypoint','environment','healthcheck','image','labels','networks','network_mode','ports','pull_policy','read_only','restart','security_opt','tmpfs','user','volumes'}
    require(set(r) <= allowed, 'unreviewed Compose field')
    require(r['image'] == current['Image'], 'Compose image differs from runtime')
    image = json.loads(run(['docker','image','inspect',current['Image']]))[0]['Config']
    env = dict(x.split('=',1) for x in image.get('Env',[])); env.update({k:str(v) for k,v in r.get('environment',{}).items()})
    require(env == dict(x.split('=',1) for x in c.get('Env',[])), 'Compose environment differs')
    for key, field in [('command','Cmd'),('entrypoint','Entrypoint'),('user','User')]:
        declared = r.get(key)
        expected = image.get(field) if declared is None else declared
        require(expected == c.get(field), 'Compose process differs')
    require(c.get('WorkingDir') == image.get('WorkingDir'), 'working directory differs')
    health = copy.deepcopy(image.get('Healthcheck'))
    if 'healthcheck' in r:
        require(set(r['healthcheck']) <= {'test','interval','timeout','start_period','retries'}, 'unknown health field')
        def duration(s):
            total = 0; end = 0
            for m in re.finditer(r'(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)', s):
                require(m.start() == end, 'invalid health duration'); end = m.end()
                total += float(m.group(1)) * {'ns':1,'us':1000,'ms':1000000,'s':1000000000,'m':60000000000,'h':3600000000000}[m.group(2)]
            require(end == len(s), 'invalid health duration'); return int(total)
        health = health or {}
        for key, value in r['healthcheck'].items():
            name = {'test':'Test','interval':'Interval','timeout':'Timeout','start_period':'StartPeriod','retries':'Retries'}[key]
            health[name] = duration(value) if key in {'interval','timeout','start_period'} else value
    require(health == c.get('Healthcheck'), 'Compose healthcheck differs')
    require(r['cap_drop'] == h['CapDrop'] and not h['CapAdd'] and not h['Privileged'], 'capabilities differ')
    require(r['security_opt'] == h['SecurityOpt'] and r['read_only'] == h['ReadonlyRootfs'], 'security differs')
    require(h['RestartPolicy'] == {'Name':r['restart'],'MaximumRetryCount':0}, 'restart policy differs')
    require({v.split(':',1)[0]:v.split(':',1)[1] if ':' in v else '' for v in r['tmpfs']} == h['Tmpfs'], 'tmpfs differs')
    expected_ports = {str(v['target'])+'/'+v['protocol']:[{'HostIp':v['host_ip'],'HostPort':str(v['published'])}] for v in r.get('ports',[])}
    require(expected_ports == (h['PortBindings'] or {}) and not h['PublishAllPorts'], 'ports differ')
    expected_mounts = []
    for v in r['volumes']:
        require(set(v) <= {'type','source','target','read_only','bind','volume'} and set(v.get('bind',{})) <= {'create_host_path'} and not v.get('volume'), 'unreviewed mount option')
        source = document['volumes'][v['source']]['name'] if v['type']=='volume' else v['source']
        expected_mounts.append((v['type'],source,v['target'],not v.get('read_only',False)))
    observed = [(v['Type'],v['Name'] if v['Type']=='volume' else v['Source'],v['Destination'],v['RW']) for v in current['Mounts'] if v['Type']!='tmpfs']
    require(sorted(expected_mounts)==sorted(observed), 'Compose mounts differ')
    if 'network_mode' in r: require(r['network_mode']==h['NetworkMode'], 'network mode differs')
    else:
        require(all(v is None or v=={} for v in r['networks'].values()), 'unreviewed network option')
        require({document['networks'][n]['name'] for n in r['networks']}==set(current['NetworkSettings']['Networks']), 'networks differ')
    require(all(c['Labels'].get(k)==v for k,v in r.get('labels',{}).items()), 'labels differ')

def scope_from_worker(name):
    code = "from eva.bootstrap.delegated_work_service import OpenAIDelegatedWorkSettings;import json;s=OpenAIDelegatedWorkSettings.from_environment().service.scope;print(json.dumps([str(s.tenant_id),str(s.installation_id)]))"
    return [str(UUID(v)) for v in json.loads(run(['docker','exec',name,'python','-c',code]))]

def inventory(scope):
    # UUID parsing precedes SQL interpolation. Local existing PostgreSQL route only.
    tenant, installation = (str(UUID(v)) for v in scope)
    query = f"BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SELECT coalesce(json_agg(row_to_json(q) ORDER BY work_id),'[]'::json) FROM (SELECT work_id,state,call_id,revision,spec_payload->>'contract' AS contract,spec_payload->>'worker_program_sha256' AS worker_program_sha256 FROM eva_core.delegated_work_runs WHERE tenant_id='{tenant}' AND installation_id='{installation}') q; ROLLBACK;"
    return json.loads(run(['docker','exec','-i',DB,'psql','-XAtq','-v','ON_ERROR_STOP=1','-U','postgres','-d','eva2'], query))

def mounted_path(container, target):
    m = next(v for v in container['Mounts'] if target==v['Destination'] or target.startswith(v['Destination']+'/'))
    return pathlib.Path(m['Source']+target[len(m['Destination']):])

def rooms(media):
    cfg = read(mounted_path(media, '/run/eva/config.json'))
    require(cfg['livekit_url']=='ws://127.0.0.1:7880', 'unexpected existing LiveKit route')
    secret = mounted_path(media,cfg['livekit_secret_file']).read_text().strip().encode()
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    stamp = int(time.time())
    token = encode(b'{"alg":"HS256","typ":"JWT"}')+'.'+encode(json.dumps({'iss':cfg['livekit_api_key'],'nbf':stamp-5,'exp':stamp+30,'video':{'roomList':True}},separators=(',',':')).encode())
    token += '.'+encode(hmac.new(secret,token.encode(),hashlib.sha256).digest())
    req = Request('http://127.0.0.1:7880/twirp/livekit.RoomService/ListRooms',data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urlopen(req,timeout=10) as response: return len(json.load(response).get('rooms',[]))

def jobs():
    with urlopen('http://127.0.0.1:44711/internal/v1/dashboard/work',timeout=10) as response: return {r['work_id'] for r in json.load(response)['jobs']}

def ready(role, container, baseline):
    errors=[]
    for _ in range(45):
        try:
            c=inspect(container); require(c['State']['Running'],'not running')
            if role in {'core','worker'}: require(c['State']['Health']['Status']=='healthy','not healthy')
            if role=='core':
                with urlopen('http://127.0.0.1:44710/readyz',timeout=2) as response: require(json.load(response).get('ready'),'core not ready')
            if role=='media':
                cfg=read(mounted_path(baseline,'/run/eva/config.json')); token=mounted_path(baseline,cfg['owner_token_file']).read_text().strip()
                req=Request('http://127.0.0.1:44716/session/ready',headers={'Authorization':'Bearer '+token})
                with urlopen(req,timeout=3) as response: require(response.status==200,'media not ready')
            return
        except Exception as error: errors.append(type(error).__name__)
        time.sleep(1)
    raise RuntimeError('readiness failed: '+','.join(sorted(set(errors))))

def stop_exact(name, identity, allow_already_exited=False):
    c=inspect(name); require(c['Id']==identity,'unknown container at stop')
    if allow_already_exited and not c['State']['Running']:
        return  # Preserve failed-candidate evidence; no live process is killed.
    if c['State']['Running']:
        # Infinite Docker grace period: client timeout never requests SIGKILL.
        run(['docker','stop','--timeout=-1',identity],timeout=90)
    c=inspect(name); require(c['Id']==identity and not c['State']['Running'] and c['State']['ExitCode']==0,'non-graceful shutdown')

def write_preserving(path, raw):
    with path.open('wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())

def save_private(path, value):
    path.write_text(json.dumps(value,indent=2)+'\n');path.chmod(0o600)

def persist(value):
    stat=RECORD.stat(); temp=RECORD.with_suffix('.private-return-new')
    save_private(temp,value);os.chown(temp,stat.st_uid,stat.st_gid);os.replace(temp,RECORD)

def main(execute):
    os.umask(0o077); require(os.geteuid()==0,'existing root management route required')
    sealed_paths=[P/'images.json',P/'commands.private.json',P/'source-verification.json']
    hashes={p.name:sha(p.read_bytes()) for p in sealed_paths}
    images=read(sealed_paths[0]);commands=read(sealed_paths[1]);proof=read(sealed_paths[2]);roles=images['roles']
    require(set(roles)=={'core','worker','media'} and proof.get('mediaReviewFixPending') is False and proof.get('pairedReleaseEqual') is True,'candidate review incomplete')
    require(proof['core']['release']==proof['worker']['release'],'paired source releases differ')
    require(all(proof[r]['allOtherSourcePreserved'] and proof[r]['runtimeUIDReadable'] for r in roles),'source proof incomplete')
    if execute:
        review=read(P/'install-review.json')
        require(review.get('decision')=='GO' and review.get('scriptSha256')==sha(pathlib.Path(__file__).read_bytes()) and review.get('inputs')==hashes,'exact independent review seal required')
    before={r:inspect(v['container']) for r,v in roles.items()};old=read(RECORD);record_raw=RECORD.read_bytes();env_raw=MEDIA_ENV.read_bytes()
    require(old['verified'] and old['command']==commands['core'],'active composition changed')
    for r,v in roles.items():
        require(before[r]['Id']==v['beforeId'] and before[r]['Image']==v['parent'] and before[r]['State']['Running'],'parent baseline changed')
        require(v['configUnchanged'] and v['parentLayersPreserved'],'image proof incomplete')
        a,b=json.loads(run(['docker','image','inspect',v['parent'],v['candidate']]))
        require(a['Config']==b['Config'] and b['RootFS']['Layers'][:len(a['RootFS']['Layers'])]==a['RootFS']['Layers'],'image configuration or ancestry changed')
    require(before['media']['Config']['User']=='65532:65532','media UID changed')
    scope=scope_from_worker(roles['worker']['container']); rows=inventory(scope)
    require(len(rows)==5 and all(r['state'] in TERMINAL for r in rows),'historical work not quiescent')
    prior_jobs=jobs(); require(prior_jobs=={r['work_id'] for r in rows},'job inventory differs')
    require(rooms(before['media'])==0,'LiveKit room active')
    base_cfg={k:render(v) for k,v in commands.items()}
    for r in roles: effective_matches(base_cfg['media' if r=='media' else 'core'],SERVICES[r],before[r])
    overlay={'services':{SERVICES[r]:{'image':roles[r]['candidate']} for r in ['core','worker']}}
    expected=copy.deepcopy(base_cfg['core'])
    for r in ['core','worker']:expected['services'][SERVICES[r]]['image']=roles[r]['candidate']
    require(render(commands['core'],overlay)==expected,'unexpected paired Compose delta')
    media_expected=copy.deepcopy(base_cfg['media']);media_expected['services']['media']['image']=roles['media']['candidate']
    require(render(commands['media'],{'services':{'media':{'image':roles['media']['candidate']}}})==media_expected,'unexpected media Compose delta')
    text=env_raw.decode();pattern=r'(?m)^EVA_VOICE_MEDIA_IMAGE=([^\r\n]+)$';matches=list(re.finditer(pattern,text))
    require(len(matches)==1 and matches[0].group(1)==roles['media']['parent'],'media environment image pin differs')
    new_env=re.sub(pattern,lambda m:'EVA_VOICE_MEDIA_IMAGE='+roles['media']['candidate'],text).encode()
    all_ids=run(['docker','ps','-aq']).splitlines();others=json.loads(run(['docker','inspect',*all_ids]))
    protected={c['Name']:(c['Id'],c['Image'],c['State']['Running'],c['State']['StartedAt']) for c in others if c['Id'] not in {b['Id'] for b in before.values()}}
    inputs={p:sha(pathlib.Path(p).read_bytes()) for cmd in commands.values() for i,p in enumerate(cmd) if i and cmd[i-1] in {'-f','--env-file'}}
    def unchanged_inputs():
        require(all(sha(pathlib.Path(p).read_bytes())==h for p,h in inputs.items() if p!=str(MEDIA_ENV)),'release inputs changed')
    def protect():
        for n,expected_identity in protected.items():
            c=inspect(n);require((c['Id'],c['Image'],c['State']['Running'],c['State']['StartedAt'])==expected_identity,'protected container changed')
    def current_baseline():
        for r,v in roles.items():
            c=inspect(v['container']); require(c['Id']==before[r]['Id'] and c['Config']==before[r]['Config'] and host(c['HostConfig'])==host(before[r]['HostConfig']) and c['State']['StartedAt']==before[r]['State']['StartedAt'] and c['State']['Running'],'fresh baseline changed')
    current_baseline();unchanged_inputs();protect()
    if not execute:
        print(json.dumps({'preflight':True,'mutated':False,'historicalRows':len(rows),'rooms':0,'inputs':hashes,'scriptSha256':sha(pathlib.Path(__file__).read_bytes())}));return
    require(RECORD.read_bytes()==record_raw and MEDIA_ENV.read_bytes()==env_raw,'management record changed')
    require(all(sha(p.read_bytes())==hashes[p.name] for p in sealed_paths),'reviewed inputs changed')
    stage=P/('installation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'));stage.mkdir(mode=0o700)
    save_private(stage/'containers.before.private.json',before);save_private(stage/'rows.before.private.json',rows);save_private(stage/'protected.private.json',protected)
    (stage/'record.before.private.json').write_bytes(record_raw);(stage/'media-env.before.private').write_bytes(env_raw)
    candidate_overlay=stage/'core-worker-images.compose.json';save_private(candidate_overlay,overlay)
    candidate_core=commands['core']+['-f',str(candidate_overlay)]
    own={r:{v['beforeId']} for r,v in roles.items()};result={'installed':False,'modelCalls':0,'stage':str(stage)}
    def start(command, targets, *, candidate):
        try:
            run(command+['up','-d','--no-deps','--pull','never',*targets],timeout=120)
        finally:
            # Compose may create one service before failing on another. Adopt
            # only exact images/configuration and this invocation's provenance.
            expected_files=','.join(command[i+1] for i,arg in enumerate(command[:-1]) if arg=='-f')
            for r,v in roles.items():
                if SERVICES[r] not in targets: continue
                try:
                    current=inspect(v['container']); baseline=before[r]
                    labels=current['Config']['Labels']; prior_labels=baseline['Config']['Labels']
                    require(current['Image']==v['candidate' if candidate else 'parent'],'partial image differs')
                    require(configuration(current['Config'])==configuration(baseline['Config']),'partial configuration differs')
                    require(host(current['HostConfig'])==host(baseline['HostConfig']),'partial host configuration differs')
                    require(mounts(current)==mounts(baseline),'partial mounts differ')
                    require(set(current['NetworkSettings']['Networks'])==set(baseline['NetworkSettings']['Networks']),'partial networks differ')
                    require(labels.get('com.docker.compose.project')==prior_labels.get('com.docker.compose.project') and labels.get('com.docker.compose.service')==SERVICES[r] and labels.get('com.docker.compose.project.config_files')==expected_files,'partial Compose provenance differs')
                    own[r].add(current['Id'])
                except Exception:
                    pass  # Unknown/changed container remains unowned and blocks rollback.
    def validate(candidate):
        for r,v in roles.items():
            ready(r,v['container'],before[r]);runtime_equal(inspect(v['container']),before[r],v['candidate' if candidate else 'parent'])
        unchanged_inputs();protect();require(inventory(scope)==rows and jobs()==prior_jobs,'work inventory changed')
        if candidate:
            for r,v in roles.items():
                code="import json,hashlib,pathlib; expected="+repr(v['files'])+"; print(json.dumps(all(hashlib.sha256((pathlib.Path('/opt/eva/src')/p).read_bytes()).hexdigest()==h for p,h in expected.items())))"
                require(json.loads(run(['docker','exec',v['container'],'python','-c',code])),'installed source hash differs')
            release_code="import json;from eva.bootstrap.delegated_work_provider import WORKER_PROGRAM_SHA256;from eva.bootstrap.delegated_research import research_program_hash;print(json.dumps({'toolFree':WORKER_PROGRAM_SHA256,'research':research_program_hash()}))"
            for r in ['core','worker']:require(json.loads(run(['docker','exec',roles[r]['container'],'python','-c',release_code]))==proof[r]['release'],'installed paired release differs')
    def record(candidate,phase):
        value=copy.deepcopy(old);value.update(verified=True,phase=phase,command=candidate_core if candidate else commands['core'],coreImage=roles['core']['candidate' if candidate else 'parent'],privateReturnBaseline=str(stage))
        for r in ['core','worker']:
            c=inspect(roles[r]['container']);value['observedServices'][roles[r]['container']].update(id=c['Id'],image=c['Image'],running=c['State']['Running'])
        if 'workerImage' in value:value['workerImage']=roles['worker']['candidate' if candidate else 'parent']
        persist(value)
    persist({**old,'verified':False,'command':None,'phase':'installing-private-result-return','lastIntendedCommand':candidate_core})
    try:
        require(rooms(before['media'])==0,'room appeared before stop')
        stop_exact(roles['media']['container'],before['media']['Id'])
        require(rooms(before['media'])==0,'room appeared after media stop')
        stop_exact(roles['core']['container'],before['core']['Id'])
        require(inventory(scope)==rows,'task arrived during quiescence')
        stop_exact(roles['worker']['container'],before['worker']['Id'])
        require(inventory(scope)==rows,'task changed after worker stop');unchanged_inputs();protect()
        write_preserving(MEDIA_ENV,new_env);require(render(commands['media'])==media_expected,'media environment changed more than image')
        start(candidate_core,['core','delegated-work-worker'],candidate=True)
        ready('core',roles['core']['container'],before['core']);ready('worker',roles['worker']['container'],before['worker'])
        start(commands['media'],['media'],candidate=True);validate(True);record(True,'installed-private-result-return')
        result.update(installed=True,rowsPreserved=len(rows),protectedContainersPreserved=True,images={r:v['candidate'] for r,v in roles.items()})
    except Exception as error:
        result['failureType']=type(error).__name__;failures=[]
        try:
            unchanged_inputs();require(all(inspect(v['container'])['Id'] in own[r] for r,v in roles.items()),'unknown replacement container')
            require(rooms(before['media'])==0 and all(r['state'] in TERMINAL for r in inventory(scope)),'rollback blocked by active work or room')
            for r in ['media','core','worker']:stop_exact(roles[r]['container'],inspect(roles[r]['container'])['Id'],allow_already_exited=True)
            require(inventory(scope)==rows,'rollback inventory differs')
        except Exception as e:failures.append('rollback-precondition:'+type(e).__name__)
        if not failures:
            try:write_preserving(MEDIA_ENV,env_raw)
            except Exception as e:failures.append('media-env:'+type(e).__name__)
            try:start(commands['core'],['core','delegated-work-worker'],candidate=False)
            except Exception as e:failures.append('core-worker:'+type(e).__name__)
            try:start(commands['media'],['media'],candidate=False)
            except Exception as e:failures.append('media:'+type(e).__name__)
            try:
                require(MEDIA_ENV.read_bytes()==env_raw and render(commands['core'])==base_cfg['core'] and render(commands['media'])==base_cfg['media'],'rollback inputs differ')
                validate(False)
                if not failures:record(False,'private-result-return-rollback-verified')
            except Exception as e:failures.append('rollback-readback:'+type(e).__name__)
        result.update(rollbackVerified=not failures,rollbackErrors=failures)
        if failures:persist({**old,'verified':False,'command':None,'phase':'private-result-return-rollback-unverified','privateReturnBaseline':str(stage)})
    finally:
        save_private(stage/'installation-result.json',result);print(json.dumps(result))
    if not result['installed']:raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    try:main(args.execute)
    except Exception as error:
        print(json.dumps({'failed':True,'failureType':type(error).__name__,'credentialsPrinted':False}));raise SystemExit(1) from None
