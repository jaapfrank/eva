"""Same-target missing-input continuation: no model calls or installation."""
from copy import deepcopy
import hashlib
import json
import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _artifact
from eva.operations.runtime_repair_development import RuntimeRepairDevelopment, _put
from tests.operations.test_runtime_repair_diagnostics import input_set


@pytest.fixture
def handoff(input_set, tmp_path, request):
    f=input_set
    if getattr(request,'param',None)=='paired':
        f.target.pop('repair_services');f.target['contract']='EvaRuntimeRepairTarget/v1'
        loader='adapters/postgres/scheduled_work_failure.py'
        f.sources[loader]=b'# synthetic installed loader\n'
        digest=hashlib.sha256(f.sources[loader]).hexdigest();f.target['sources'][loader]=digest
        for frame in f.target['failure']['observation']['frames']:
            if frame['path']==loader:frame['source_sha256']=digest
        f.blocked['verified_source'].update(expected_sha256=digest,actual_sha256=digest)
        f.target['target_id']=canonical_sha256({k:v for k,v in f.target.items() if k!='target_id'})
        f.blocked['target_id']=f.target['target_id'];f.metadata['target_id']=f.target['target_id']
    parent={**f.metadata,'contract':'EvaRuntimeRepairWorkMetadata/v1',
        'task_id':'work:eva-initiative:'+'1'*64,'execution_id':'work:eva-initiative:'+'1'*64,'spec_sha256':'2'*64,
        'state':'succeeded','work_succeeded':True,'usage_measured':True}
    metadata={parent['task_id']:parent}
    def call(action, **kw):
        if action=='binding':return deepcopy(f.target['failure'])
        if action in {'work_metadata','stage_metadata'}:return deepcopy(metadata[kw['task_id']])
        if action=='record_waiting':return {'state':'waiting_for_repair'}
        raise AssertionError(action)
    f.runtime.repair_call=Mock(side_effect=call)
    def read(meta,name,**kwargs):
        if name=='oracle.json':raise FileNotFoundError(name)
        assert name=='blocked.json'
        return deepcopy(f.blocked)
    artifacts=SimpleNamespace(read=Mock(side_effect=read),_seed=Mock(),seed_sources=Mock(),
        read_snapshot=Mock(side_effect=lambda *a,**kw:(read(*a,**kw),json.dumps(f.blocked,indent=3).encode())))
    targets=SimpleNamespace(resolve=lambda _:deepcopy(f.target),source=Mock())
    c=RuntimeRepairDevelopment(tmp_path/'handoff',runtime=f.runtime,targets=targets,
        evaluator=Mock(),artifacts=artifacts,publisher=f.publisher,applications=Mock(),
        scope_sha256=f.target['failure']['scope_sha256'],start=False)
    directory=c.root/f.target['target_id'];directory.mkdir(mode=0o700)
    text='def fixed():\n    return 1\n';path=next(iter(f.target['sources']))
    candidate={'contract':'EvaRuntimeRepairCandidate/v1','target_id':f.target['target_id'],
        'diagnosis':'Synthetic proposal','files':[{'path':path,'baseline_sha256':f.target['sources'][path],
            'candidate_content':text,'candidate_sha256':hashlib.sha256(text.encode()).hexdigest()}]}
    job={'contract':'EvaRuntimeRepairHandoff/v1','target_id':f.target['target_id'],
        'scope_sha256':c.scope_sha256,'incident_id':f.target['failure']['incident_id'],
        'failure':deepcopy(f.target['failure']),'sources':deepcopy(f.target['sources']),
        'repair_services':['core'],'candidate':_put(directory/'candidate.json',candidate),
        'candidate_sha256':canonical_sha256(candidate),'state':'blocked',
        'reason':'host_source_or_stage_could_not_be_verified','consecutive_host_errors':3,
        'host_error_type_sha256':hashlib.sha256(b'builtins.FileNotFoundError').hexdigest(),
        'original_behavior_oracle_task_id':parent['task_id'],'waiting_recorded':True}
    if 'repair_services' not in f.target:job.pop('repair_services')
    c.jobs[job['target_id']]=job;c._save(job)
    yield SimpleNamespace(c=c,job=job,parent=parent,metadata=metadata,f=f)
    c.close()


def test_completed_missing_input_keeps_old_fault_and_real_blocked_object(handoff):
    h=handoff;before=deepcopy(h.job)
    assert h.c.run_once()=='original_behavior_input_blocked'
    assert _artifact(h.job['diagnostic_origin'])[1]==before
    assert _artifact(h.job['blocked_diagnostic'])[1]==h.f.blocked
    snapshot=_artifact(h.job['blocked_diagnostic_snapshot'])[1]
    exact=json.dumps(h.f.blocked,indent=3).encode()
    assert base64.b64decode(snapshot['raw_base64'])==exact
    assert snapshot['raw_sha256']==hashlib.sha256(exact).hexdigest()
    assert _artifact(h.job['blocked_independent_work'])[1]==h.parent
    assert h.job['consecutive_host_errors']==3
    assert h.job['candidate']==before['candidate']
    assert h.c.run_once()=='supplement_admission_pending'
    supplement=h.job['oracle_supplement']
    frozen=_artifact(supplement['diagnostics'])[1]
    assert frozen['manifest']['parent_task_sha256']==canonical_sha256({'task_id':h.parent['task_id']})
    assert 'PRIVATE_FREEFORM' not in str(frozen)
    assert h.job['consecutive_host_errors']==3
    assert not h.c.applications.mock_calls and not h.c.evaluator.mock_calls


@pytest.mark.parametrize('handoff',['paired'],indirect=True)
def test_actual_legacy_paired_scope_keeps_frozen_target_and_native_scope(handoff):
    h=handoff;before=deepcopy(h.job)
    assert h.c.run_once()=='original_behavior_input_blocked'
    assert h.c.run_once()=='supplement_admission_pending'
    request=_artifact(h.job['oracle_supplement']['request'])[1]
    assert request['repair_scope'] is None
    assert h.job['target_id']==before['target_id']
    assert h.job['candidate']==before['candidate']
    assert 'repair_services' not in h.job
    assert _artifact(h.job['diagnostic_origin'])[1]==before
    assert not h.c.applications.mock_calls


def test_interrupted_snapshot_preserves_job_and_next_controller_can_recover(handoff,monkeypatch):
    from eva.operations import runtime_repair_development as module
    h=handoff;before=deepcopy(h.job);put=module._put
    def fail_last(path,value):
        if path.name=='independent-input-blocked-work.json':raise OSError('synthetic storage interruption')
        return put(path,value)
    monkeypatch.setattr(module,'_put',fail_last)
    assert h.c.run_once()=='blocked'
    assert h.job==before
    assert 'diagnostic_origin' not in h.job
    monkeypatch.setattr(module,'_put',put)
    h.c.close()
    restored=RuntimeRepairDevelopment(h.c.root,runtime=h.c.runtime,targets=h.c.targets,
        evaluator=h.c.evaluator,artifacts=h.c.artifacts,publisher=h.c.publisher,
        applications=h.c.applications,scope_sha256=h.c.scope_sha256,start=False)
    try:
        assert restored.run_once()=='original_behavior_input_blocked'
        assert _artifact(restored.jobs[h.job['target_id']]['diagnostic_origin'])[1]==before
        assert restored.jobs[h.job['target_id']]['consecutive_host_errors']==3
        assert not h.c.applications.mock_calls
    finally:restored.close()


@pytest.mark.parametrize('change',['oracle_exists','target','unmeasured','candidate_seen','invalid_blocked','sealed'])
def test_unrelated_blocked_work_is_not_reopened_or_error_history_cleared(handoff,change):
    h=handoff
    if change=='oracle_exists':h.c.artifacts.read.side_effect=lambda *a,**k:{}
    elif change=='target':h.f.target['target_id']='0'*64
    elif change=='unmeasured':h.parent['usage_measured']=False
    elif change=='candidate_seen':h.parent['candidate_sha256']='9'*64
    elif change=='invalid_blocked':h.f.blocked['oracle_created']=True
    else:h.job['criteria']={'path':'already-sealed','sha256':'4'*64}
    before=deepcopy(h.job)
    assert h.c.run_once() in {'blocked','idle'}
    assert h.job==before
    assert h.c.run_once()=='idle'


def prepare(h):
    assert h.c.run_once()=='original_behavior_input_blocked'
    assert h.c.run_once()=='supplement_admission_pending'


def test_unknown_admission_ack_only_inspects_same_identity(handoff,monkeypatch):
    h=handoff;prepare(h);actions=[]
    def rpc(job,action):
        actions.append(action)
        if action=='admit':raise TimeoutError('possible effect')
        return {'status':'already_adopted','intent_id':h.c._supplement_intent(job)}
    monkeypatch.setattr(h.c,'_supplement_rpc',rpc)
    assert h.c.run_once()=='supplement_admission_unconfirmed'
    assert h.c.run_once()=='original_behavior_oracle'
    assert actions==['admit','inspect']
    assert h.job['consecutive_host_errors']==3
    assert h.job['original_behavior_oracle_task_id']!=h.parent['task_id']
    assert _artifact(h.job['blocked_independent_work'])[1]==h.parent


def test_new_independent_child_gets_exact_inputs_and_cannot_spawn_third(handoff,monkeypatch):
    h=handoff;prepare(h)
    monkeypatch.setattr(h.c,'_supplement_rpc',lambda job,action:{
        'status':'already_adopted','intent_id':h.c._supplement_intent(job)})
    assert h.c.run_once()=='original_behavior_oracle'
    task=h.job['oracle_supplement']['task_id']
    h.metadata[task]={**h.parent,'task_id':task,'execution_id':task,'state':'call_started',
        'work_succeeded':False,'usage_measured':False}
    request={'action':'describe','scope_sha256':h.c.scope_sha256,'request_id':'5'*64,
        'incident_id':h.job['incident_id'],'task_id':task}
    reply=h.c.request(request)
    assert reply['diagnostic_manifest']=='/workspace/repair/diagnostics/manifest.json'
    assert h.c.artifacts._seed.call_args.args[0]['task_id']==task
    with pytest.raises(ValueError,match='another independent task'):
        h.c.request({**request,'task_id':h.parent['task_id']})
    assert h.c.artifacts._seed.call_count==1
    h.metadata[task].update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert h.c.run_once()=='blocked'
    assert h.job['reason']=='supplementary_independent_input_still_insufficient'
    assert h.c.run_once()=='idle'
    assert _artifact(h.job['supplement_blocked_diagnostic'])[1]==h.f.blocked


def test_changed_binding_cannot_seed_supplementary_inputs(handoff,monkeypatch):
    h=handoff;prepare(h)
    monkeypatch.setattr(h.c,'_supplement_rpc',lambda job,action:{
        'status':'already_adopted','intent_id':h.c._supplement_intent(job)})
    assert h.c.run_once()=='original_behavior_oracle'
    task=h.job['oracle_supplement']['task_id']
    h.metadata[task]={**h.parent,'task_id':task,'execution_id':task,'state':'call_started'}
    h.f.target['failure']['source_sha256']='9'*64
    with pytest.raises(ValueError,match='no longer current'):
        h.c.request({'action':'describe','scope_sha256':h.c.scope_sha256,'request_id':'5'*64,
            'incident_id':h.job['incident_id'],'task_id':task})
    assert not h.c.artifacts._seed.called


@pytest.mark.parametrize('changed_execution',[False,True])
def test_only_host_certified_same_execution_continuation_can_read_supplement(handoff,monkeypatch,changed_execution):
    h=handoff;prepare(h)
    monkeypatch.setattr(h.c,'_supplement_rpc',lambda job,action:{
        'status':'already_adopted','intent_id':h.c._supplement_intent(job)})
    assert h.c.run_once()=='original_behavior_oracle'
    original=h.job['oracle_supplement']['task_id'];resumed='work:eva-resume:fixture'
    meta={**h.parent,'task_id':resumed,'execution_id':'work:other' if changed_execution else original,
        'state':'call_started','work_succeeded':False,'usage_measured':False}
    h.metadata[original]=meta;h.metadata[resumed]=meta
    request={'action':'describe','scope_sha256':h.c.scope_sha256,'request_id':'5'*64,
        'incident_id':h.job['incident_id'],'task_id':resumed}
    if changed_execution:
        with pytest.raises(ValueError,match='exact current supplementary execution'):h.c.request(request)
        assert not h.c.artifacts._seed.called
    else:
        assert h.c.request(request)['diagnostic_manifest']=='/workspace/repair/diagnostics/manifest.json'
        assert h.c.artifacts._seed.call_args.args[0]['task_id']==resumed


@pytest.mark.parametrize('stage',['original_behavior_oracle','comparing','publishing','application_request_pending'])
def test_parent_stop_is_rechecked_before_accepting_or_using_independent_result(handoff,monkeypatch,stage):
    h=handoff;prepare(h)
    monkeypatch.setattr(h.c,'_supplement_rpc',lambda job,action:{
        'status':'already_adopted','intent_id':h.c._supplement_intent(job)})
    assert h.c.run_once()=='original_behavior_oracle'
    task=h.job['oracle_supplement']['task_id']
    h.metadata[task]={**h.parent,'task_id':task,'execution_id':task}
    h.job['state']=stage
    h.c.targets.current=lambda _:deepcopy(h.f.target)
    def stopped(job,action):
        assert action=='inspect'
        raise ValueError('completed parent has a durable stop request')
    monkeypatch.setattr(h.c,'_supplement_rpc',stopped)
    before=h.c.artifacts.read.call_count
    with pytest.raises(ValueError,match='durable stop'):
        h.c._advance(h.job)
    assert h.c.artifacts.read.call_count==before
    assert not h.c.evaluator.mock_calls and not h.c.applications.mock_calls
    assert 'oracle' not in h.job
