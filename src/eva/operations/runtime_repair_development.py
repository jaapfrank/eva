"""Failure-bound handoff on the existing component evaluation host.

This controller advances finite native work stages; the existing worker owns all
model calls, grants, STOP and accounting. It neither plans new goals nor calls a
model itself. Private receipts survive a host restart without replaying updates.
"""
from copy import deepcopy
import fcntl
import hashlib
from pathlib import Path
import threading

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _artifact, _path, _save
from eva.operations.runtime_repair_target import public_repair_target, _sha
from eva.operations.runtime_repair_evaluation import validate_repair_candidate, validate_repair_oracle


_TERMINAL={'completed','baseline_retained','rejected','blocked','cancelled'}


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
        return self.targets.current(job['target_id'])

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
        if meta['incident_id']!=binding['incident_id']:
            raise ValueError('repair handoff work differs')
        with self.lock:
            target=self.targets.resolve(request['incident_id'])
            identity=target['target_id'];job=self.jobs.get(identity)
            role=meta['role']
            if request['action']=='describe':
                self.artifacts.seed_sources(meta,target,source=self.targets.source)
                if role=='independent_review':
                    if (job is None or meta['target_id']!=identity
                            or meta['candidate_sha256']!=job['candidate_sha256']
                            or meta['comparison_sha256']!=job.get('comparison_sha256')):
                        raise ValueError('independent review belongs to another snapshot')
                    self.artifacts.seed_review(meta,{name:_artifact(job[name])[1] for name in
                        ('target','candidate','oracle','comparison')})
                elif role not in (None,'original_behavior_oracle'):
                    raise ValueError('unknown repair phase')
                response = {**public_repair_target(target),'role':role,
                    'installed_source_snapshot':'/workspace/repair/source/eva'}
                if role is None:
                    from eva.operations.runtime_repair_target import repair_builder_handoff
                    response['builder_handoff'] = repair_builder_handoff(target)
                return response
            if role is not None or meta['task_id']!='work:eva-initiative:'+binding['intent_id'].removeprefix('personal-interest:'):
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

    def _stage(self,job,role):
        kwargs={'role':role,'target_id':job['target_id'],'sources':job['sources'],
            'candidate_sha256':None,'comparison_sha256':None}
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
        return meta

    def request_maintenance(self,request):
        if self.maintenance is None:raise ValueError('component maintenance unavailable')
        return self.maintenance.request(request)

    def run_once(self):
        if self.maintenance is not None:
            try:self.maintenance.observe_due()
            except Exception:
                # Source I/O failure must not starve a retained repair/application.
                self.maintenance.last_observation_error=True
        with self.lock:
            pending=[j for j in self.jobs.values() if j['state'] not in _TERMINAL]
            if not pending:return 'idle'
            # A waiting/permanently broken older repair cannot starve another.
            after=next((i+1 for i,j in enumerate(pending) if j['target_id']==self._last_target),0)
            job=pending[after % len(pending)];self._last_target=job['target_id']
            try:
                outcome=self._advance(job)
                job.pop('consecutive_host_errors',None)
                return outcome
            except Exception as error:
                count=job.get('consecutive_host_errors',0)+1
                kind=type(error).__module__+'.'+type(error).__qualname__
                job.update(consecutive_host_errors=count,
                    host_error_type_sha256=hashlib.sha256(kind.encode()).hexdigest())
                if job['state']=='application_request_pending':
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
        if state=='waiting_for_builder':
            target=self.targets.resolve(job['incident_id'])
            if target['target_id']!=job['target_id'] or target['failure']!=job['failure']:
                raise ValueError('submitted repair source changed')
            meta=self._metadata(job,job['task_id'])
            if meta['state'] in {'admitted','call_started'}:return 'waiting_for_builder'
            if not meta.get('work_succeeded') or not meta.get('usage_measured'):
                job.update(state='blocked',reason='builder_not_completed_with_measured_usage');return 'blocked'
            job['sources']=target['sources']
            if self._stage(job,'original_behavior_oracle'):job['state']='original_behavior_oracle'
        elif state=='original_behavior_oracle':
            meta=self._completed_stage(job,state)
            if meta is None:return job['state']
            target=self.targets.resolve(job['incident_id'])
            oracle=validate_repair_oracle(target,self.artifacts.read(meta,'oracle.json',maximum=524288))
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
            self._current(job)
            if self._stage(job,'independent_review'):job['state']='independent_review'
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
