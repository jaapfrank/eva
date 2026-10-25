"""Failure-bound multi-file publication using the existing image-only publisher."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import tempfile

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _artifact, _save
from eva.operations.runtime_repair_development import _put, validate_repair_review
from eva.operations.runtime_repair_evaluation import validate_repair_candidate
from eva.operations.runtime_image_update import require_current_storage


def current_repair_installation(publisher,directory):
    """Current immutable images, original retained storage and exact Compose config."""
    installed=_artifact(publisher.config['installed'])[1]
    selected=deepcopy(installed);observed={}
    prefix=['docker','compose','--project-name',publisher.config['project'],
        '--file',publisher.config['installed']['path'],'--profile','*']
    for service in ('core','delegated-work-worker'):
        ids=publisher._command([*prefix,'ps','--all','-q',service],directory).split()
        if len(ids)!=1:raise ValueError('one current repair runtime service required')
        current=json.loads(publisher._command(['docker','inspect',ids[0]],directory))[0]
        labels=current['Config'].get('Labels',{})
        if labels.get('com.docker.compose.project')!=publisher.config['project'] or labels.get('com.docker.compose.service')!=service:
            raise ValueError('repair runtime project/service changed')
        require_current_storage(installed,service,current)
        selected['services'][service]['image']=current['Image'];observed[service]=current
    with tempfile.TemporaryDirectory(prefix='eva-repair-installation-') as temporary:
        snapshot=Path(temporary)/'compose.private.json';_save(snapshot,selected,initial=True)
        for service,current in observed.items():
            hashed=publisher._command(['docker','compose','--project-name',publisher.config['project'],
                '--file',str(snapshot),'--profile','*','config','--hash',service],directory).split()
            if hashed!=[service,current['Config']['Labels'].get('com.docker.compose.config-hash')]:
                raise ValueError('repair runtime configuration differs beyond images')
    top={k:v for k,v in selected.items() if k!='services'}
    pins={service:{'image':selected['services'][service]['image'],
        'configuration_sha256':canonical_sha256({'top':top,'service':{
            k:v for k,v in selected['services'][service].items() if k!='image'}})} for service in observed}
    return selected,pins


def read_image_source(publisher,image,path,directory):
    from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES, repairable_source
    if not repairable_source(path):raise ValueError('protected repair source')
    # Data stays bounded inside the immutable image; no candidate program runs.
    script=('import base64,pathlib,sys;p=pathlib.Path("/opt/eva/src/eva")/sys.argv[1];'
        f'assert p.resolve()==p and 1<=p.stat().st_size<={MAX_REPAIR_SOURCE_BYTES};print(base64.b64encode(p.read_bytes()).decode())')
    encoded=publisher._command(['docker','run','--rm','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001',
        '--entrypoint','python',image,'-I','-c',script,path],directory)
    raw=base64.b64decode(encoded,validate=True)
    if not 1<=len(raw)<=MAX_REPAIR_SOURCE_BYTES:raise ValueError('bounded image source required')
    raw.decode('utf-8')
    return raw


def publish_runtime_repair(publisher,job,directory):
    from eva.operations.runtime_repair_application import verify_runtime_repair_release
    release_file=directory/'published-release.json'
    if release_file.exists():
        from eva.operations.component_release_import import load_object
        release=load_object(release_file);verify_runtime_repair_release(job['candidate_sha256'],release)
        return release
    target=_artifact(job['target'])[1];candidate=validate_repair_candidate(target,_artifact(job['candidate'])[1])
    comparison=_artifact(job['comparison'])[1];review=_artifact(job['review'])[1]
    validate_repair_review(target,candidate,comparison,review)
    if review['decision']!='accept':raise ValueError('rejected repair cannot be published')
    installed,pins=current_repair_installation(publisher,directory)
    if pins!=target['installation']:raise ValueError('repair installation changed before publication')
    before={s:r['image'] for s,r in pins.items()};images={}
    # Preserve the shared checkout. The branch records only this exact repair
    # scope; installed baseline differences receive a separate provenance commit.
    checkout=directory/'source';branch='codex/eva-runtime-repair-'+job['candidate_sha256'][:20]
    if not checkout.exists():
        head=publisher._command(['git','-c','safe.directory='+str(publisher.repository),'-C',
            str(publisher.repository),'rev-parse','HEAD'],directory)
        publisher._command(['git','-c','safe.directory='+str(publisher.repository),'-C',str(publisher.repository),
            'worktree','add','-b',branch,str(checkout),head],directory)
    relative=[]
    for entry in candidate['files']:
        path=checkout/'src/eva'/entry['path'];relative.append('src/eva/'+entry['path'])
        if path.is_symlink() or path.resolve()!=path:raise ValueError('repair source checkout changed')
        raw=read_image_source(publisher,before['core'],entry['path'],directory)
        if hashlib.sha256(raw).hexdigest()!=entry['baseline_sha256']:raise ValueError('installed repair baseline changed')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    prefix=['git','-c','safe.directory='+str(checkout),'-C',str(checkout)]
    publisher._command([*prefix,'add','--',*relative],directory)
    # --allow-empty makes a retried pre-publication source reservation harmless;
    # no update can be requested until the exact release is registered.
    publisher._command([*prefix,'-c','user.name=Eva repair','-c','user.email=eva@localhost',
        'commit','--allow-empty','-m','Record observed installed repair baseline','--',*relative],directory)
    for entry in candidate['files']:(checkout/'src/eva'/entry['path']).write_text(entry['candidate_content'])
    publisher._command([*prefix,'add','--',*relative],directory)
    publisher._command([*prefix,'-c','user.name=Eva repair','-c','user.email=eva@localhost',
        'commit','--allow-empty','-m','Apply independently accepted runtime repair','--',*relative],directory)
    commit=publisher._command([*prefix,'rev-parse','HEAD'],directory)
    for service,image in before.items():
        context=directory/('repair-build-'+service);context.mkdir(mode=0o700,exist_ok=True)
        copies=[]
        for index,entry in enumerate(candidate['files']):
            raw=read_image_source(publisher,image,entry['path'],directory)
            if hashlib.sha256(raw).hexdigest()!=entry['baseline_sha256']:raise ValueError('peer repair source changed')
            file=context/('source-'+str(index)+'.py');file.write_text(entry['candidate_content']);file.chmod(0o444)
            copies.append('COPY '+file.name+' /opt/eva/src/eva/'+entry['path']+'\n')
        (context/'Dockerfile').write_text('FROM '+image+'\n'+''.join(copies))
        iid=directory/(service+'-repair-image.txt')
        publisher._command(['docker','build','--network=none','--pull=false','--iidfile',str(iid),str(context)],directory)
        proposed=iid.read_text().strip()
        if not proposed.startswith('sha256:') or len(proposed)!=71:raise ValueError('invalid repair image identity')
        for entry in candidate['files']:
            if hashlib.sha256(read_image_source(publisher,proposed,entry['path'],directory)).hexdigest()!=entry['candidate_sha256']:
                raise ValueError('packaged repair source differs')
        images[service]=proposed
    selected=deepcopy(installed)
    for service,image in images.items():selected['services'][service]['image']=image
    build={'contract':'EvaRuntimeRepairBuild/v1','target_id':job['target_id'],
        'candidate_sha256':job['candidate_sha256'],'installed':False,'before':before,'images':images,
        'sourceHashes':{e['path']:e['candidate_sha256'] for e in candidate['files']},
        'source_commit':commit,'source_branch':branch}
    release={'kind':'runtime_repair','target':job['target'],'repair_candidate':job['candidate'],
        'comparison':job['comparison'],'review':job['review'],'review_work':job['review_work'],
        'build':_put(directory/'repair-build.json',build),
        'installed':_put(directory/'repair-installed-compose.private.json',installed),
        'candidate':_put(directory/'repair-candidate-compose.private.json',selected),
        'project':publisher.config['project'],'update_receipt':str(directory/'repair-update.private.json'),
        'behavior_verifier':target['criteria']}
    verify_runtime_repair_release(job['candidate_sha256'],release)
    _put(release_file,release)
    return release
