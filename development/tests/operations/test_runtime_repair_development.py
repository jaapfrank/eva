"""Finite host handoff state machine; synthetic work receipts, no model or install."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.runtime_repair_development import RuntimeRepairDevelopment
from eva.operations.runtime_repair_target import RuntimeRepairTargets

BASELINE='def example(value):\n    return value.strip()\n'
CANDIDATE="def example(value):\n    return '' if value is None else value.strip()\n"
PATH='application/example.py'


@pytest.fixture
def flow(tmp_path):
    source_sha=hashlib.sha256(BASELINE.encode()).hexdigest()
    binding={'contract':'EvaCurrentRepairBinding/v1','incident_id':'self-repair:'+'a'*64,
        'incident_event_sha256':'b'*64,'scope_sha256':'c'*64,'mandate_sha256':'d'*64,
        'source_sha256':'e'*64,'intent_id':'personal-interest:'+'f'*64,
        'observation':{'error_type_sha256':hashlib.sha256(b'builtins.AttributeError').hexdigest(),
            'frames':[{'path':PATH,'function':'example:2','source_sha256':source_sha}]}}
    primary='work:eva-initiative:'+'f'*64
    meta={'contract':'EvaRuntimeRepairWorkMetadata/v1','incident_id':binding['incident_id'],
        'task_id':primary,'state':'call_started','role':None,'work_succeeded':False,'usage_measured':False}
    calls=[];metadata={primary:meta};inputs={};published=[];holder={}
    class Runtime:
        def repair_call(self,action,*,incident_id,**kwargs):
            calls.append((action,deepcopy(kwargs)))
            assert incident_id==binding['incident_id']
            if action=='binding':return deepcopy(binding)
            if action in {'work_metadata','stage_metadata'}:return deepcopy(metadata[kwargs['task_id']])
            if action=='record_waiting':return {'state':'waiting_for_repair','installed':False}
            if action=='admit_assessment':
                key=('1' if kwargs['role']=='original_behavior_oracle' else '2')*64
                task='work:eva-initiative:'+key
                metadata.setdefault(task,{**meta,'task_id':task,'role':kwargs['role'],
                    'target_id':kwargs['target_id'],'candidate_sha256':kwargs['candidate_sha256'],
                    'comparison_sha256':kwargs['comparison_sha256']})
                return {'status':'adopted','intent_id':'personal-interest:'+key}
            raise AssertionError(action)
    runtime=Runtime()
    targets=RuntimeRepairTargets(tmp_path/'targets',binding=lambda _:deepcopy(binding),
        installation=lambda:{role:{'image':'sha256:'+str(i)*64,'configuration_sha256':'9'*64}
            for i,role in enumerate(('core','delegated-work-worker'),1)},
        source=lambda *_:BASELINE.encode(),criteria=lambda scope:holder['controller'].criteria(scope))
    target=targets.resolve(binding['incident_id']);identity=target['target_id']
    candidate={'contract':'EvaRuntimeRepairCandidate/v1','target_id':identity,'diagnosis':'Missing optional input',
        'files':[{'path':PATH,'baseline_sha256':source_sha,'candidate_sha256':hashlib.sha256(CANDIDATE.encode()).hexdigest(),
            'candidate_content':CANDIDATE}]}
    oracle={'contract':'EvaIndependentRuntimeRepairCases/v1','target_id':identity,'original_case_id':'original',
        'cases':[{'id':'original','driver_source':'def run():\n    return None\n','expected':{'kind':'value','value':''}},
                 {'id':'normal','driver_source':'def run():\n    return 1\n','expected':{'kind':'value','value':1}}]}
    review={'contract':'EvaIndependentRuntimeRepairReview/v1','target_id':identity,
        'candidate_sha256':canonical_sha256(candidate),'comparison_sha256':None,
        'decision':'reject','behavioral_reason':'The synthetic fixture does not prove original behavior.','findings':[]}
    class Artifacts:
        def seed_sources(self,meta,target,*,source):
            assert set(target['sources'])=={PATH}
            assert hashlib.sha256(source(target['installation']['core']['image'],PATH)).hexdigest()==target['sources'][PATH]
        def read(self,meta,name,**kwargs):return deepcopy({'candidate.json':candidate,'oracle.json':oracle,'review.json':review}[name])
        def seed_review(self,meta,offered):inputs[meta['task_id']]=deepcopy(offered)
    class Evaluator:
        def evaluate(self,identity,offered):
            assert offered==candidate
            return {'contract':'EvaRuntimeRepairComparison/v1','target_id':identity,
                'candidate_sha256':canonical_sha256(candidate),'criteria_sha256':targets.current(identity)['criteria']['sha256'],
                'state':'awaiting_independent_review','expected_outcomes_outside_candidate':True,
                'installed':False,'semantic_improvement_verified':False}
    class Publisher:
        def publish_repair(self,*args):published.append(args);raise AssertionError('rejected review cannot publish')
    controller=RuntimeRepairDevelopment(tmp_path/'handoffs',runtime=runtime,targets=targets,
        evaluator=Evaluator(),artifacts=Artifacts(),publisher=Publisher(),applications=SimpleNamespace(),
        scope_sha256=binding['scope_sha256'],start=False)
    holder['controller']=controller
    request={'incident_id':binding['incident_id'],'task_id':primary,'request_id':'8'*64,'scope_sha256':binding['scope_sha256']}
    yield SimpleNamespace(**locals())
    controller.close()


def test_frozen_candidate_uses_separate_native_oracle_and_review_then_retains_rejected_baseline(flow):
    f=flow;c=f.controller
    described=c.request({**f.request,'action':'describe'})
    assert described['criteria_sha256'] is None
    accepted=c.request({**f.request,'action':'submit'})
    assert accepted['accepted'] and not accepted['installed']
    assert c.request({**f.request,'action':'submit'})==accepted
    assert c.run_once()=='waiting_for_builder'
    f.meta.update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert c.run_once()=='original_behavior_oracle'
    oracle_task='work:eva-initiative:'+'1'*64
    admission=next(args for action,args in f.calls if action=='admit_assessment')
    assert admission['candidate_sha256'] is None and admission['comparison_sha256'] is None
    f.metadata[oracle_task].update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert c.run_once()=='comparing'
    assert c.run_once()=='awaiting_independent_review'
    assert c.run_once()=='independent_review'
    review_task='work:eva-initiative:'+'2'*64
    review_meta=f.metadata[review_task]
    f.review['comparison_sha256']=review_meta['comparison_sha256']
    c.request({**f.request,'action':'describe','task_id':review_task})
    assert set(f.inputs[review_task])=={'target','candidate','oracle','comparison'}
    review_meta.update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert c.run_once()=='rejected'
    assert not f.published
    assert any(action=='record_waiting' and args['outcome']=='rejected' for action,args in f.calls)
    assert c.jobs[f.identity]['waiting_recorded'] is True
    assert c.run_once()=='idle'


def test_assessment_cannot_submit_or_read_another_candidate_snapshot(flow):
    f=flow;c=f.controller;c.request({**f.request,'action':'submit'})
    f.metadata['fake-review']={**f.meta,'task_id':'fake-review','role':'independent_review',
        'target_id':f.identity,'candidate_sha256':'0'*64,'comparison_sha256':'1'*64}
    with pytest.raises(ValueError,match='another snapshot'):
        c.request({**f.request,'task_id':'fake-review','action':'describe'})
    with pytest.raises(ValueError,match='original builder'):
        c.request({**f.request,'task_id':'fake-review','action':'submit'})
    assert f.inputs=={}


def test_repeated_host_failure_is_durable_and_does_not_starve_other_repairs(flow,monkeypatch):
    f=flow;c=f.controller;c.request({**f.request,'action':'submit'})
    other=deepcopy(c.jobs[f.identity]);other['target_id']='7'*64;c.jobs[other['target_id']]=other
    (c.root/other['target_id']).mkdir(mode=0o700)
    visited=[]
    def advance(job):
        visited.append(job['target_id'])
        if job['target_id']==f.identity:raise ValueError('private fault text must not be retained')
        return 'waiting_for_builder'
    monkeypatch.setattr(c,'_advance',advance)
    for _ in range(6):c.run_once()
    assert visited==[f.identity,'7'*64]*3
    assert c.jobs[f.identity]['state']=='blocked'
    assert 'private fault text' not in str(c.jobs[f.identity])
    assert c.jobs[f.identity]['consecutive_host_errors']==3


def test_actual_source_workspace_handoff_ends_builder_before_separate_assessment(flow,tmp_path):
    import json
    from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
    f=flow;c=f.controller
    key='a'*64;session='b'*64
    f.meta.update(workspace_key=key,session_key=session)
    programming=SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path/'programming'),
        processes_idle=lambda *args,**kw:dict(idle=True))
    programming.config.workspace_root.mkdir()
    c.artifacts=RuntimeRepairArtifacts(programming)
    described=c.request({**f.request,'action':'describe'})
    folder=programming.config.workspace_root/key/'repair'
    handoff=json.loads((folder/'handoff.json').read_text())
    assert described['builder_handoff']==handoff
    assert (folder/'source/eva'/PATH).read_text()==BASELINE
    assert handoff['target_id']==f.identity and not handoff['installed']
    (folder/'candidate.json').write_text(json.dumps(f.candidate))
    accepted=c.request({**f.request,'action':'submit'})
    assert accepted['accepted'] and accepted['next_action']=='finish_current_work'
    assert not accepted['installed']
    assert c.run_once()=='waiting_for_builder'
    assert not any(action=='admit_assessment' for action,_ in f.calls)
    # The original native stage must actually finish, with measured usage.
    f.meta.update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert c.run_once()=='original_behavior_oracle'
    admitted=next(arguments for action,arguments in f.calls if action=='admit_assessment')
    assert admitted['candidate_sha256'] is None and admitted['comparison_sha256'] is None
    assert not f.published
