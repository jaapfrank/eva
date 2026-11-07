"""Failure-bound handoff on the existing component evaluation host.

This controller advances finite native work stages; the existing worker owns all
model calls, grants, STOP and accounting. It neither plans new goals nor calls a
model itself. Private receipts survive a host restart without replaying updates.
"""
from copy import deepcopy
import base64
import fcntl
import hashlib
import json
from pathlib import Path
import threading

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _artifact, _path, _save
from eva.operations.runtime_repair_target import public_repair_target, repair_services, _sha
from eva.operations.runtime_repair_evaluation import validate_repair_candidate, validate_repair_oracle


_TERMINAL={'completed','baseline_retained','rejected','blocked','cancelled'}


# Fixed host code, not a model-selected program or a new core endpoint. Reuse
# native follow-up admission so the completed parent's STOP remains effective
# at every later worker action. No succeeded execution is resumed.
_ORACLE_SUPPLEMENT_RPC = r'''
import hashlib,json,re,sys
from pathlib import Path
import sqlalchemy as sa
from eva.bootstrap.runtime_repair_host import _context, _work_metadata
from eva.adapters.postgres.self_repair import current_repair_binding
from eva.adapters.postgres.initiative_adoption import adopt_private_initiative, bind_completed_followup
from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
from eva.application.repair_assessment import repair_assessment_task
from eva.contracts.canonical_json import canonical_sha256

def handle(request, *, authority=None, service=None, store=None, source_root=None):
    fields={'action','incident_id','scope_sha256','failure','parent','target_id','sources',
            'repair_scope','blocked_sha256','blocked_original_sha256','supplement_sha256'}
    if not isinstance(request,dict) or set(request)!=fields or request['action'] not in {'admit','inspect'}:
        raise ValueError('exact original-behavior supplementary request required')
    for key in ('scope_sha256','target_id','blocked_sha256','blocked_original_sha256','supplement_sha256'):
        if not isinstance(request[key],str) or not re.fullmatch(r'[0-9a-f]{64}',request[key]):
            raise ValueError('supplement identity differs')
    with _context(authority,service,store) as (authority,service,store,settings):
        binding=current_repair_binding(authority,request['incident_id'],now=service.now())
        if binding!=request['failure'] or binding['scope_sha256']!=request['scope_sha256']:
            raise ValueError('supplementary assessment authority changed')
        parent=request['parent']
        actual=_work_metadata(authority,service,store,binding,parent['task_id'],require_running=False)
        if (actual!=parent or not actual['work_succeeded'] or not actual['usage_measured']
                or actual['role']!='original_behavior_oracle' or actual['target_id']!=request['target_id']
                or actual['candidate_sha256'] is not None or actual['comparison_sha256'] is not None):
            raise ValueError('supplement parent is not the exact completed blind oracle')
        retained=store.load(parent['task_id'],**service._owner)
        events=authority.timeline.events(retained.spec.origin.thread_id)
        adopted=next(e.payload['data'] for e in events if e.event_type=='PERSONAL_INTENT_ADOPTED')
        if adopted.get('repair_supplement_parent_task_id') is not None:
            raise ValueError('an oracle supplement cannot create another supplement')
        completed=next((e.payload['data'] for e in reversed(events)
                        if e.event_type=='PERSONAL_INITIATIVE_COMPLETED'),None)
        if completed is None:
            raise ValueError('original independent initiative is not completed')
        from eva.bootstrap.runtime_repair_work import _assessment_runtime_role
        runtime_role=_assessment_runtime_role(request['repair_scope'],target_id=request['target_id'],
                                             binding=binding,sources=request['sources'])
        from eva.application.component_improvements import component_source
        from eva.application.self_repair import repairable_source, MAX_REPAIR_SOURCE_BYTES
        root=Path(source_root) if source_root is not None else Path('/opt/eva/src/eva')
        frames={f['path']:f['source_sha256'] for f in binding['observation']['frames']}
        if not isinstance(request['sources'],dict) or not 1<=len(request['sources'])<=8:
            raise ValueError('original source scope differs')
        for path,digest in request['sources'].items():
            if not repairable_source(path) or frames.get(path)!=digest:
                raise ValueError('supplement changed the repair source scope')
            try:source=component_source(root,path)
            except FileNotFoundError:
                if runtime_role!='delegated-work-worker':raise
                continue
            if (runtime_role=='delegated-work-worker' or not source.is_file()
                    or source.stat().st_size>MAX_REPAIR_SOURCE_BYTES
                    or hashlib.sha256(source.read_bytes()).hexdigest()!=digest):
                raise ValueError('installed supplementary assessment source changed')
        identity={'contract':'EvaOriginalBehaviorSupplement/v1','incident_id':binding['incident_id'],
            'target_id':request['target_id'],'role':'original_behavior_oracle',
            'parent_task_id':parent['task_id'],'parent_spec_sha256':parent['spec_sha256'],
            'blocked_sha256':request['blocked_sha256'],'blocked_original_sha256':request['blocked_original_sha256'],
            'supplement_sha256':request['supplement_sha256']}
        key=canonical_sha256(identity);intent='personal-interest:'+key
        task=repair_assessment_task('original_behavior_oracle',target_id=request['target_id'],
            sources=request['sources'],observation=binding['observation'])
        task+=('\nDe host heeft ontbrekende oorspronkelijke diagnostische invoer aangevuld. '
            'Begin opnieuw met describe en lees uitsluitend de gebonden bestanden onder '
            '/workspace/repair/diagnostics. Dit is aanvullende broninformatie, geen gewenste '
            'uitkomst of beoordelingscriterium. Je krijgt nog steeds geen kandidaat. '
            'Maak eigen oorspronkelijke gedragsgevallen; meld blocked.json als bewijs ontbreekt. '
            'Wijzig niets aan de lopende installatie. Invoerbinding: '+request['supplement_sha256'])
        owner=authority.owner;now=service.now()
        followup={'intent_id':adopted['intent_id'],'content_sha256':completed['content_sha256']}
        with owner._engine.begin() as connection:
            owner._authorize(connection,now)
            c,e=operational_checkpoints.c,operational_events.c
            connection.execute(sa.select(operational_checkpoints).where(
                c.tenant_id==owner._scope.tenant_id,c.installation_id==owner._scope.installation_id,
                c.thread_id==authority.thread).with_for_update()).one()
            bound=bind_completed_followup(connection,owner._scope,followup,
                principal_id=str(owner._owner),now=now)
            def revalidate():
                if current_repair_binding(authority,binding['incident_id'],now=now,_connection=connection)!=binding:
                    raise ValueError('supplement source or mandate changed')
            revalidate()
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                {'key':'eva-oracle-supplement:'+canonical_sha256({'incident':binding['incident_id'],'parent':parent['task_id']})})
            payload={**{k:adopted[k] for k in ('principal_id','source_principal_id','repair_source')},
                'conversation_id':'initiative-task:'+key,'source_message_id':'initiative-execute:'+key,
                'intent_id':intent,'origin':'eva_initiative','execution_mode':'autonomous_task',
                'adoption_source':'automatic_runtime_repair_assessment','unprompted':True,
                'kind':'personal_interest','title':'Oorspronkelijk herstelgedrag met aangevulde bron controleren ('+key[:12]+')',
                'motivation':'Ontbrekende oorspronkelijke broninformatie is afzonderlijk aangevuld.',
                'next_exploration':task,'repair_incident_id':binding['incident_id'],
                'repair_mandate_sha256':binding['mandate_sha256'],'repair_role':'original_behavior_oracle',
                'repair_target_id':request['target_id'],'repair_candidate_sha256':None,'repair_comparison_sha256':None,
                'repair_supplement_parent_task_id':parent['task_id'],
                'repair_supplement_blocked_sha256':request['blocked_sha256'],
                'repair_supplement_blocked_original_sha256':request['blocked_original_sha256'],
                'repair_supplement_sha256':request['supplement_sha256'],
                'followup_source':bound,'authority':'current_host_self_repair_mandate_required'}
            existing=connection.execute(sa.select(e.payload,e.payload_sha256).where(
                e.tenant_id==owner._scope.tenant_id,e.installation_id==owner._scope.installation_id,
                e.event_type=='PERSONAL_INTENT_ADOPTED',
                e.payload['data']['repair_supplement_parent_task_id'].astext==parent['task_id'])).all()
            if existing:
                if (len(existing)!=1 or canonical_sha256(existing[0].payload)!=bytes(existing[0].payload_sha256).hex()
                        or existing[0].payload['data']!=payload):
                    raise ValueError('original oracle already has another supplementary child')
                return {'status':'already_adopted','intent_id':intent,'task_id':'work:eva-initiative:'+key}
            if request['action']=='inspect':return {'status':'absent','intent_id':intent}
            if service.digital_profile is None:
                if settings is None:raise ValueError('current digital profile required')
                from eva.bootstrap.delegated_digital_composition import build_digital_runner
                service.digital_profile=build_digital_runner(settings,store).profile
            authority.digital_grant(service.digital_profile)
            return adopt_private_initiative(connection,owner._scope,payload,now=now,revalidate=revalidate)

if __name__=='__main__':print(json.dumps(handle(json.loads(sys.argv[1]))))
'''


def _put(path,value):
    if path.exists():
        from eva.operations.component_release_import import load_object
        if load_object(_path(path))!=value:
            raise ValueError('frozen repair receipt changed')
    else:
        _save(path,value,initial=True)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_repair_review(target,candidate,comparison,review):
    if (not isinstance(review,dict) or set(review)!={'contract','target_id','candidate_sha256',
            'comparison_sha256','decision','behavioral_reason','findings'}
            or review['contract']!='EvaIndependentRuntimeRepairReview/v1'
            or review['target_id']!=target['target_id']
            or review['candidate_sha256']!=canonical_sha256(candidate)
            or review['comparison_sha256']!=canonical_sha256(comparison)
            or review['decision'] not in {'accept','reject'}
            or not isinstance(review['behavioral_reason'],str)
            or not 1<=len(review['behavioral_reason'])<=8000
            or not isinstance(review['findings'],list) or len(review['findings'])>64
            or any(not isinstance(f,str) or not 1<=len(f)<=2000 for f in review['findings'])):
        raise ValueError('exact independent semantic repair review required')
    if review['decision']=='accept' and comparison['state']!='awaiting_independent_review':
        raise ValueError('independent review cannot accept a failed comparison')
    return deepcopy(review)


def _require_single_role_assessment(job, role, metadata, target_id):
    if job is None or metadata.get('target_id') != target_id:
        raise ValueError('independent assessment belongs to another repair target')
    if role == 'original_behavior_oracle':
        if (metadata.get('candidate_sha256') is not None
                or metadata.get('comparison_sha256') is not None):
            raise ValueError('original behavior assessment must be candidate-blind')
    elif (role != 'independent_review'
            or metadata.get('candidate_sha256') != job['candidate_sha256']
            or metadata.get('comparison_sha256') != job.get('comparison_sha256')):
        raise ValueError('independent review belongs to another snapshot')


class RuntimeRepairDevelopment:
    def __init__(self,root,*,runtime,targets,evaluator,artifacts,publisher,applications,
                 scope_sha256,poll_seconds=2,start=True):
        self.root=Path(root);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        _path(self.root/'.directory-check',exists=False)
        import os,stat
        info=self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077):
            raise ValueError('private host repair handoffs required')
        self.runtime,self.targets,self.evaluator,self.artifacts=runtime,targets,evaluator,artifacts
        self.publisher,self.applications=publisher,applications
        self.scope_sha256=_sha(scope_sha256)
        if type(poll_seconds) not in (int,float) or not .1<=poll_seconds<=60:
            raise ValueError('bounded existing host repair interval required')
        self.interval=poll_seconds;self.lock=threading.RLock();self.closed=threading.Event()
        self.lease=(self.root/'.controller.lock').open('a+b')
        fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.jobs={}
        self.maintenance=None
        self._last_target=None
        self._diagnostic_screened=set()
        from eva.operations.component_release_import import load_object
        for path in self.root.glob('*/handoff.json'):
            job=load_object(_path(path));identity=_sha(path.parent.name)
            if job.get('target_id')!=identity or job.get('scope_sha256')!=self.scope_sha256:
                raise ValueError('retained repair handoff differs')
            self.jobs[identity]=job
        self.thread=None
        if start:
            self.thread=threading.Thread(target=self._run,name='eva-component-repair-handoff',daemon=True)
            self.thread.start()

    def _call(self,job,action,**kwargs):
        if job.get('contract')=='EvaDependencyMaintenanceHandoff/v1':
            return self.maintenance.call(job,action,proof=kwargs) if action=='record_waiting' else self.maintenance.call(job,action,**kwargs)
        return self.runtime.repair_call(action,incident_id=job['incident_id'],**kwargs)

    def _current(self,job):
        if self._call(job,'binding')!=job['failure']:
            raise ValueError('original repair authority changed')
        target=self.targets.current(job['target_id'])
        if 'oracle_supplement' in job:self._supplement_current(job,target,require_completed=True)
        return target

    def _save(self,job):
        _save(self.root/job['target_id']/'handoff.json',job)

    @staticmethod
    def _public(job):
        return {'target_id':job['target_id'],'incident_id':job['incident_id'],
            'state':job['state'],'accepted':True,'installed':job['state']=='completed',
            'next_action':'finish_current_work' if job['state']=='waiting_for_builder' else 'inspect_existing_handoff'}

    def request(self,request):
        if (not isinstance(request,dict) or set(request)!={
                'action','incident_id','task_id','request_id','scope_sha256'}
                or request['action'] not in {'describe','submit','status'}
                or request['scope_sha256']!=self.scope_sha256):
            raise ValueError('exact source-bound runtime repair handoff required')
        _sha(request['request_id'])
        binding=self.runtime.repair_call('binding',incident_id=request['incident_id'])
        meta=self.runtime.repair_call('work_metadata',incident_id=request['incident_id'],
            task_id=request['task_id'],require_running=True)
        if (meta['incident_id']!=binding['incident_id']
                or meta['task_id']!=request['task_id']):
            raise ValueError('repair handoff work differs')
        with self.lock:
            target=self.targets.resolve(request['incident_id'])
            identity=target['target_id'];job=self.jobs.get(identity)
            role=meta['role']
            services=repair_services(target)
            if request['action']=='describe':
                if len(services)==1 and role is not None:
                    _require_single_role_assessment(job,role,meta,identity)
                supplemental={}
                if role=='original_behavior_oracle' and job is not None and 'oracle_supplement' in job:
                    supplement=job['oracle_supplement']
                    current=self._supplement_current(job,target)
                    if meta!=current:
                        raise ValueError('supplementary source belongs to another independent task')
                    _require_single_role_assessment(job,role,meta,identity)
                    from eva.operations.runtime_repair_diagnostics import seed_scheduled_failure_diagnostics
                    supplemental=seed_scheduled_failure_diagnostics(self.artifacts,meta,target,
                        supplement['diagnostics'],blocked_sha256=job['blocked_diagnostic']['sha256'],
                        parent_task_sha256=canonical_sha256({'task_id':supplement['parent']['task_id']}))
                    if self._supplement_current(job,target)!=current:
                        raise ValueError('supplementary attempt changed during source handoff')
                self.artifacts.seed_sources(meta,target,source=self.targets.source)
                if role=='independent_review':
                    if (job is None or meta['target_id']!=identity
                            or meta['candidate_sha256']!=job['candidate_sha256']
                            or meta['comparison_sha256']!=job.get('comparison_sha256')):
                        raise ValueError('independent review belongs to another snapshot')
                    if 'oracle_supplement' in job:self._supplement_current(job,target,require_completed=True)
                    self.artifacts.seed_review(meta,{name:_artifact(job[name])[1] for name in
                        ('target','candidate','oracle','comparison')})
                elif role not in (None,'original_behavior_oracle'):
                    raise ValueError('unknown repair phase')
                response = {**public_repair_target(target),'role':role,
                    'installed_source_snapshot':'/workspace/repair/source/eva',**supplemental}
                if role is None:
                    from eva.operations.runtime_repair_target import repair_builder_handoff
                    response['builder_handoff'] = repair_builder_handoff(target)
                return response
            # A resume changes its attempt ID, never the host-verified execution.
            # Older metadata may identify only the original, unresumed builder.
            execution=meta.get('execution_id',meta['task_id'])
            if role is not None or execution!='work:eva-initiative:'+binding['intent_id'].removeprefix('personal-interest:'):
                raise ValueError('only the original builder may submit a repair')
            if request['action']=='status':
                return {'state':'not_submitted','installed':False,'accepted':False} if job is None else self._public(job)
            candidate=validate_repair_candidate(target,self.artifacts.read(meta,'candidate.json'))
            candidate_id=canonical_sha256(candidate)
            if job is not None:
                if job['candidate_sha256']!=candidate_id or job['task_id']!=meta['task_id']:
                    raise ValueError('this original behavior assessment already has a frozen candidate')
                return self._public(job)
            # Do not expose a new attempt against previously consumed independent
            # answers. A later genuine source/failure produces another target.
            directory=self.root/identity;directory.mkdir(mode=0o700,exist_ok=True)
            candidate_ref=_put(directory/'candidate.json',candidate)
            _put(directory/'scope.json',target)
            if self.runtime.repair_call('binding',incident_id=request['incident_id'])!=binding:
                raise ValueError('repair stopped during handoff')
            job={'contract':'EvaRuntimeRepairHandoff/v1','target_id':identity,
                'scope_sha256':self.scope_sha256,'incident_id':request['incident_id'],
                'failure':binding,'task_id':meta['task_id'],'builder':meta,
                'candidate':candidate_ref,'candidate_sha256':candidate_id,'state':'waiting_for_builder'}
            if len(services)==1:
                job['repair_services']=list(services)
            self._save(job);self.jobs[identity]=job
            return self._public(job)

    def criteria(self,scope):
        job=self.jobs.get(canonical_sha256(scope))
        if job is None or 'criteria' not in job:
            raise ValueError('separate original behavior work is not yet sealed')
        _artifact(job['criteria'])
        return deepcopy(job['criteria'])

    def _metadata(self,job,task):
        return self._call(job,'stage_metadata',task_id=task,require_running=False)

    def _stage(self,job,role,target):
        kwargs={'role':role,'target_id':job['target_id'],'sources':job['sources'],
            'candidate_sha256':None,'comparison_sha256':None}
        services=repair_services(target)
        if len(services)==1:
            if (job.get('repair_services')!=list(services) or target['target_id']!=job['target_id']
                    or target['sources']!=job['sources']):
                raise ValueError('single-role assessment target changed')
            kwargs['repair_scope']={key:value for key,value in target.items()
                if key not in {'target_id','installed','criteria'}}
        if role=='independent_review':
            kwargs.update(candidate_sha256=job['candidate_sha256'],comparison_sha256=job['comparison_sha256'])
        result=self._call(job,'admit_assessment',**kwargs)
        if result['status'] not in {'adopted','already_adopted'}:
            return False
        job[role+'_task_id']='work:eva-initiative:'+result['intent_id'].removeprefix('personal-interest:')
        return True

    def _completed_stage(self,job,role):
        meta=self._metadata(job,job[role+'_task_id'])
        if meta['state'] in {'awaiting_native_admission','admitted','call_started'}:
            return None
        if not meta.get('work_succeeded') or not meta.get('usage_measured') or meta.get('role')!=role:
            job.update(state='blocked',reason='independent_stage_not_completed_with_measured_usage')
            return None
        if 'repair_services' in job:
            _require_single_role_assessment(job,role,meta,job['target_id'])
        # The host resolves a validated same-execution continuation. Persist the
        # actual successful attempt for artifacts and the final review proof.
        job[role+'_task_id']=meta['task_id']
        return meta

    def request_maintenance(self,request):
        if self.maintenance is None:raise ValueError('component maintenance unavailable')
        return self.maintenance.request(request)

    @staticmethod
    def _unsealed(job):
        return not any(key in job for key in ('oracle','criteria','target','comparison','review',
            'release','application_request_id','installed_proof'))

    def _diagnostic_target(self,job):
        target=self.targets.resolve(job['incident_id'])
        if (not self._unsealed(job) or target['target_id']!=job['target_id']
                or target['failure']!=job['failure'] or target['sources']!=job['sources']
                or canonical_sha256(_artifact(job['candidate'])[1])!=job['candidate_sha256']):
            raise ValueError('supplement cannot change a frozen target, candidate or criteria')
        validate_repair_candidate(target,_artifact(job['candidate'])[1])
        return target

    def _record_input_blocker(self,job,target,meta):
        from eva.operations.runtime_repair_diagnostics import validate_blocked_diagnostic
        _require_single_role_assessment(job,'original_behavior_oracle',meta,job['target_id'])
        if not meta.get('work_succeeded') or not meta.get('usage_measured'):
            raise ValueError('only completed independent work can report missing input')
        blocked,raw=self.artifacts.read_snapshot(meta,'blocked.json',maximum=160000)
        validate_blocked_diagnostic(target,blocked)
        directory=self.root/job['target_id']
        if 'oracle_supplement' in job:
            job['supplement_blocked_diagnostic']=_put(directory/'supplement-blocked.json',blocked)
            job.update(state='blocked',reason='supplementary_independent_input_still_insufficient')
            return 'blocked'
        # Preserve the exact prior record, including the old three host errors.
        diagnostic=_put(directory/'independent-input-blocked.json',blocked)
        snapshot=_put(directory/'independent-input-blocked-snapshot.json',{
            'contract':'EvaOriginalBlockedArtifact/v1','task_id':meta['task_id'],
            'raw_sha256':hashlib.sha256(raw).hexdigest(),'raw_base64':base64.b64encode(raw).decode('ascii'),
            'canonical_sha256':canonical_sha256(blocked)})
        independent=_put(directory/'independent-input-blocked-work.json',meta)
        previous=_put(directory/'before-input-supplement.json',deepcopy(job))
        job.update(diagnostic_origin=previous,blocked_diagnostic=diagnostic,
            blocked_diagnostic_snapshot=snapshot,blocked_independent_work=independent,
            state='original_behavior_input_blocked',reason='independent_original_behavior_inputs_missing')
        return job['state']

    def _recover_reported_blocker(self,job):
        self._diagnostic_screened.add(job['target_id'])
        # Older host I/O failures are not restart permission. Only an authentic,
        # completed independent blocked artifact on the still-current target
        # can enter this new evidence-bound stage.
        try:
            target=self._diagnostic_target(job)
            meta=self._metadata(job,job['original_behavior_oracle_task_id'])
            if not meta.get('work_succeeded') or not meta.get('usage_measured'):
                return 'blocked'
            try:self.artifacts.read(meta,'oracle.json',maximum=524288)
            except FileNotFoundError:pass
            else:return 'blocked'
            return self._record_input_blocker(job,target,meta)
        except (OSError,ValueError,KeyError):
            return 'blocked'

    def _prepare_oracle_supplement(self,job):
        target=self._diagnostic_target(job)
        parent=_artifact(job['blocked_independent_work'])[1]
        if self._metadata(job,parent['task_id'])!=parent:
            raise ValueError('independent blocked source changed')
        original=_artifact(job['blocked_diagnostic_snapshot'])[1]
        blocked,raw=self.artifacts.read_snapshot(parent,'blocked.json',maximum=160000)
        if (hashlib.sha256(raw).hexdigest()!=original['raw_sha256']
                or blocked!=_artifact(job['blocked_diagnostic'])[1]):
            raise ValueError('original blocked artifact changed')
        from eva.operations.runtime_repair_diagnostics import prepare_scheduled_failure_diagnostics
        directory=self.root/job['target_id']/'oracle-supplement'
        directory.mkdir(mode=0o700,exist_ok=True)
        diagnostics=prepare_scheduled_failure_diagnostics(self.publisher,self.runtime,target,directory,
            blocked_receipt=job['blocked_diagnostic'],
            parent_task_sha256=canonical_sha256({'task_id':parent['task_id']}))
        _artifact(diagnostics)
        self._diagnostic_target(job)
        scope=({k:v for k,v in target.items() if k not in {'target_id','installed','criteria'}}
               if len(repair_services(target))==1 else None)
        request={'incident_id':job['incident_id'],'scope_sha256':self.scope_sha256,
            'failure':job['failure'],'parent':parent,'target_id':job['target_id'],
            'sources':target['sources'],'repair_scope':scope,
            'blocked_sha256':job['blocked_diagnostic']['sha256'],
            'blocked_original_sha256':original['raw_sha256'],'supplement_sha256':diagnostics['sha256']}
        job['oracle_supplement']={'parent':parent,'diagnostics':diagnostics,
            'request':_put(directory/'request.json',request)}
        job['state']='supplement_admission_pending'
        return job['state']

    def _supplement_rpc(self,job,action):
        request=_artifact(job['oracle_supplement']['request'])[1]
        if (request['parent']!=job['oracle_supplement']['parent']
                or request['failure']!=job['failure'] or request['target_id']!=job['target_id']
                or request['sources']!=job['sources']
                or request['blocked_sha256']!=job['blocked_diagnostic']['sha256']
                or request['blocked_original_sha256']!=_artifact(job['blocked_diagnostic_snapshot'])[1]['raw_sha256']
                or request['supplement_sha256']!=job['oracle_supplement']['diagnostics']['sha256']):
            raise ValueError('frozen supplementary request changed')
        raw=json.dumps({**request,'action':action},ensure_ascii=True)
        if len(raw.encode())>65536:raise ValueError('supplementary request exceeds bound')
        result=self.runtime.run(['docker','exec',self.runtime.config['core_container'],
            'python','-c',_ORACLE_SUPPLEMENT_RPC,raw],text=True,capture_output=True,timeout=45)
        if result.returncode or len(result.stdout.encode())>65536:
            raise ValueError('supplementary admission response unavailable; inspect only')
        value=json.loads(result.stdout)
        if not isinstance(value,dict):raise ValueError('supplementary admission response differs')
        return value

    @staticmethod
    def _supplement_intent(job):
        supplement=job['oracle_supplement'];parent=supplement['parent']
        key=canonical_sha256({'contract':'EvaOriginalBehaviorSupplement/v1','incident_id':job['incident_id'],
            'target_id':job['target_id'],'role':'original_behavior_oracle',
            'parent_task_id':parent['task_id'],'parent_spec_sha256':parent['spec_sha256'],
            'blocked_sha256':job['blocked_diagnostic']['sha256'],
            'blocked_original_sha256':_artifact(job['blocked_diagnostic_snapshot'])[1]['raw_sha256'],
            'supplement_sha256':supplement['diagnostics']['sha256']})
        return 'personal-interest:'+key

    def _supplement_current(self,job,target,*,require_completed=False):
        if (target['target_id']!=job['target_id'] or target['failure']!=job['failure']
                or self.targets.resolve(job['incident_id'])!={k:v for k,v in target.items() if k!='criteria'}):
            raise ValueError('supplementary target is no longer current')
        observed=self._supplement_rpc(job,'inspect')
        if (observed.get('status')!='already_adopted'
                or observed.get('intent_id')!=self._supplement_intent(job)):
            raise ValueError('exact independent supplementary child is unavailable')
        execution=job['oracle_supplement']['task_id']
        current=self._metadata(job,execution)
        _require_single_role_assessment(job,'original_behavior_oracle',current,job['target_id'])
        if (current.get('execution_id')!=execution or current.get('role')!='original_behavior_oracle'
                or (require_completed and (not current.get('work_succeeded') or not current.get('usage_measured')))):
            raise ValueError('exact current supplementary execution is unavailable')
        return current

    def _admit_or_inspect_supplement(self,job):
        self._diagnostic_target(job)
        action='admit' if job['state']=='supplement_admission_pending' else 'inspect'
        if action=='admit':
            # An uncertain acknowledgement can only inspect this same identity.
            # Never re-dispatch a stage or clear an old host-error counter.
            job['state']='supplement_admission_unconfirmed'
            self._save(job)
        result=self._supplement_rpc(job,action)
        if result.get('status') in {'adopted','already_adopted'}:
            if result.get('intent_id')!=self._supplement_intent(job):
                raise ValueError('supplementary response identifies another child')
            task='work:eva-initiative:'+result['intent_id'].removeprefix('personal-interest:')
            job['oracle_supplement']['task_id']=task
            job['original_behavior_oracle_task_id']=task
            job['state']='original_behavior_oracle'
        elif action=='admit' and result.get('status')=='not_admitted':
            job.update(state='blocked',reason='supplementary_native_admission_not_granted')
        elif action=='inspect' and result.get('status')=='absent':
            job['reason']='inspect_exact_supplementary_admission'
        else:raise ValueError('unknown supplementary admission result')
        return job['state']

    def run_once(self):
        if self.maintenance is not None:
            try:self.maintenance.observe_due()
            except Exception:
                # Source I/O failure must not starve a retained repair/application.
                self.maintenance.last_observation_error=True
        with self.lock:
            pending=[j for j in self.jobs.values() if j['state'] not in _TERMINAL or (
                j['state']=='blocked' and j.get('reason')=='host_source_or_stage_could_not_be_verified'
                and 'original_behavior_oracle_task_id' in j and self._unsealed(j)
                and 'oracle_supplement' not in j and j['target_id'] not in self._diagnostic_screened)]
            if not pending:return 'idle'
            # A waiting/permanently broken older repair cannot starve another.
            after=next((i+1 for i,j in enumerate(pending) if j['target_id']==self._last_target),0)
            job=pending[after % len(pending)];self._last_target=job['target_id']
            error_key='supplement_host_errors' if 'diagnostic_origin' in job else 'consecutive_host_errors'
            try:
                outcome=self._advance(job)
                if (job['state']!='blocked'
                        and ('diagnostic_origin' not in job or error_key=='supplement_host_errors')):
                    job.pop(error_key,None)
                return outcome
            except Exception as error:
                count=job.get(error_key,0)+1
                kind=type(error).__module__+'.'+type(error).__qualname__
                job[error_key]=count
                job['supplement_host_error_type_sha256' if error_key=='supplement_host_errors'
                    else 'host_error_type_sha256']=hashlib.sha256(kind.encode()).hexdigest()
                if job['state']=='supplement_admission_unconfirmed':
                    job['reason']='inspect_exact_supplementary_admission'
                elif job['state']=='application_request_pending':
                    # The exact durable queue request can be replayed safely;
                    # the existing queue alone owns possible updater dispatch.
                    job['reason']='inspect_exact_application_queue_request'
                elif job['state'] in {'application_requested','application_unconfirmed'}:
                    # Inspect/recovery only. A missing response never authorizes
                    # a new update or a different application request.
                    job.update(state='application_unconfirmed',reason='inspect_existing_managed_receipt')
                elif count>=3:
                    job.update(state='blocked',reason='host_source_or_stage_could_not_be_verified')
                return job['state']
            finally:
                if job['state'] in _TERMINAL-{'completed'} and not job.get('waiting_recorded'):
                    try:
                        self._call(job,'record_waiting',outcome=job['state'],
                            reason_code=job.get('reason',job['state']),handoff_sha256=canonical_sha256(job))
                        job['waiting_recorded']=True
                    except Exception:
                        # Revocation/STOP must not be bypassed just to write a
                        # presentation event. Host failure evidence still persists.
                        pass
                self._save(job)

    def _advance(self,job):
        if job.get('contract')=='EvaDependencyMaintenanceHandoff/v1':
            return job['state'] if self.maintenance is None else self.maintenance.advance(job)
        state=job['state'];directory=self.root/job['target_id']
        if state=='blocked':return self._recover_reported_blocker(job)
        if state=='original_behavior_input_blocked':return self._prepare_oracle_supplement(job)
        if state in {'supplement_admission_pending','supplement_admission_unconfirmed'}:
            return self._admit_or_inspect_supplement(job)
        if state=='waiting_for_builder':
            target=self.targets.resolve(job['incident_id'])
            if target['target_id']!=job['target_id'] or target['failure']!=job['failure']:
                raise ValueError('submitted repair source changed')
            meta=self._metadata(job,job['task_id'])
            if meta['state'] in {'admitted','call_started'}:return 'waiting_for_builder'
            if not meta.get('work_succeeded') or not meta.get('usage_measured'):
                job.update(state='blocked',reason='builder_not_completed_with_measured_usage');return 'blocked'
            job['sources']=target['sources']
            if self._stage(job,'original_behavior_oracle',target):job['state']='original_behavior_oracle'
        elif state=='original_behavior_oracle':
            meta=self._completed_stage(job,state)
            if meta is None:return job['state']
            target=self.targets.resolve(job['incident_id'])
            if ('oracle_supplement' in job
                    and self._supplement_current(job,target,require_completed=True)!=meta):
                raise ValueError('completed supplementary attempt changed')
            try:offered=self.artifacts.read(meta,'oracle.json',maximum=524288)
            except FileNotFoundError:return self._record_input_blocker(job,target,meta)
            oracle=validate_repair_oracle(target,offered)
            if ('oracle_supplement' in job
                    and self._supplement_current(job,target,require_completed=True)!=meta):
                raise ValueError('completed supplementary attempt changed during oracle read')
            job['oracle']=_put(directory/'oracle.json',oracle)
            receipt={'contract':'EvaIndependentRepairWorkReceipt/v1','target_id':job['target_id'],
                'role':state,'candidate_seen':False,'oracle_sha256':job['oracle']['sha256'],
                'source_verified':True,'work_succeeded':True,'usage_measured':True,'work':meta}
            independent=_put(directory/'oracle-work.json',receipt)
            job['criteria']=_put(directory/'criteria.json',{
                'contract':'EvaIndependentRuntimeRepairCriteria/v1','target_id':job['target_id'],
                'original_failure_sha256':job['failure']['incident_event_sha256'],
                'oracle':job['oracle'],'independent_work':independent})
            target=self.targets.prepare(job['incident_id'])
            job['target']=_put(directory/'target.json',target);job['state']='comparing'
        elif state=='comparing':
            self._current(job)
            report=self.evaluator.evaluate(job['target_id'],_artifact(job['candidate'])[1])
            job['comparison']=_put(directory/'comparison.json',report)
            job['comparison_sha256']=canonical_sha256(report)
            job['state']='awaiting_independent_review' if report['state']=='awaiting_independent_review' else 'baseline_retained'
        elif state=='awaiting_independent_review':
            target=self._current(job)
            if self._stage(job,'independent_review',target):job['state']='independent_review'
        elif state=='independent_review':
            self._current(job);meta=self._completed_stage(job,state)
            if meta is None:return job['state']
            review=validate_repair_review(_artifact(job['target'])[1],_artifact(job['candidate'])[1],
                _artifact(job['comparison'])[1],self.artifacts.read(meta,'review.json',maximum=160000))
            job['review']=_put(directory/'review.json',review)
            job['review_work']=_put(directory/'review-work.json',{
                'contract':'EvaIndependentRepairReviewWorkReceipt/v1','target_id':job['target_id'],
                'candidate_sha256':job['candidate_sha256'],'comparison_sha256':job['comparison_sha256'],
                'review_sha256':job['review']['sha256'],'work':meta})
            job['state']='publishing' if review['decision']=='accept' else 'rejected'
        elif state=='publishing':
            self._current(job)
            release=self.publisher.publish_repair(job,directory)
            review_id=job['candidate_sha256']
            self.applications.register(review_id,release)
            job['application_request_id']=canonical_sha256({'repair':job['target_id'],'candidate':review_id})
            job['release']=_put(directory/'release.json',release)
            job['state']='application_request_pending'
            self._save(job)  # Durable identity BEFORE any possible queue/update effect.
        elif state=='application_request_pending':
            if 'oracle_supplement' in job:self._current(job)
            self.applications.request(job['candidate_sha256'],job['application_request_id'],task_id=job['task_id'])
            job['state']='application_requested'
        elif state in {'application_requested','application_unconfirmed'}:
            # Installation changed the target's image pins, so inspect the exact
            # managed receipt rather than incorrectly resolving a new baseline.
            status=self.applications.status(job['application_request_id'],task_id=job['task_id'])
            if status['state']=='completed' and status.get('installed') is True:
                release=_artifact(job['release'])[1]
                from eva.operations.runtime_repair_application import completed_repair_proof
                proof=completed_repair_proof(job,release)
                self._call(job,'close_verified',proof=proof)
                job.update(state='completed',installed_proof=proof)
            elif status['state'] in {'cancelled','rejected','recovered'}:
                job.update(state='baseline_retained',reason=status['state'])
            elif status['state']=='unconfirmed':
                # Existing application queue owns inspect/recovery; never replay.
                job.update(state='application_unconfirmed',reason='inspect_existing_managed_receipt')
        else:raise ValueError('unknown repair handoff state')
        return job['state']

    def _run(self):
        while not self.closed.wait(self.interval):
            try:self.run_once()
            except Exception:
                # No assertion of success, tool replay, raw exception text or
                # new model task. The retained receipt remains inspectable.
                continue

    def close(self):
        self.closed.set()
        if self.thread is not None:self.thread.join(timeout=1)
        if self.thread is None or not self.thread.is_alive():self.lease.close()
