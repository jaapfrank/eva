"""Actual Docker comparison of a synthetic defect; no family data/model/installation."""
from copy import deepcopy
import hashlib
import os

import pytest

from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
from eva.operations.reviewed_component_application import _save
from eva.operations.runtime_repair_evaluation import RuntimeRepairEvaluator, validate_repair_candidate


BASELINE = 'def product_behavior(value):\n    return value.strip()\n'
PATH = 'application/new_component.py'
def oracle_cases(identity):
    def case(name,value,expected):
        return {'id':name,'driver_source':
            'from eva.application.new_component import product_behavior\ndef run():\n    return product_behavior('+repr(value)+')\n',
            'expected':{'kind':'value','value':expected}}
    return {'contract':'EvaIndependentRuntimeRepairCases/v1','target_id':identity,
        'original_case_id':'original','cases':[
            case('original',None,''),case('normal','  hello  ','hello'),case('empty','','')]}



@pytest.fixture
def comparison(tmp_path):
    def artifact(name,value):
        path=tmp_path/name;_save(path,value,initial=True)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    identity='a'*64
    oracle_binding=artifact('oracle.json',oracle_cases(identity))
    oracle_hash=oracle_binding['sha256']
    independent=artifact('independent-work.json',{'contract':'EvaIndependentRepairWorkReceipt/v1',
        'target_id':identity,'role':'original_behavior_oracle','candidate_seen':False,
        'oracle_sha256':oracle_hash,'source_verified':True,'work_succeeded':True,'usage_measured':True})
    criteria=artifact('criteria.json',{'contract':'EvaIndependentRuntimeRepairCriteria/v1',
        'target_id':identity,'original_failure_sha256':'b'*64,
        'oracle':oracle_binding,'independent_work':independent})
    image=os.environ.get('EVA_RUNTIME_REPAIR_TEST_IMAGE','sha256:'+'9'*64)
    source_sha=hashlib.sha256(BASELINE.encode()).hexdigest()
    target={'target_id':identity,'sources':{PATH:source_sha},'criteria':criteria,
        'failure':{'incident_event_sha256':'b'*64,'observation':{
            'error_type_sha256':hashlib.sha256(b'builtins.AttributeError').hexdigest(),
            'frames':[{'path':PATH,'function':'product_behavior:2','source_sha256':source_sha}]}},
        'installation':{role:{'image':image,'configuration_sha256':'c'*64}
            for role in ('core','delegated-work-worker')}}
    class Targets:
        def current(self,offered):
            assert offered==identity
            return deepcopy(target)
    baseline=tmp_path/'baseline.py';baseline.write_text(BASELINE);baseline.chmod(0o444)
    # This fixture is explicitly synthetic. Production reads the real installed
    # sources, whereas this test mounts one deliberate bug in an isolated image.
    class Executor(ComponentEvaluator):
        def _container(self,image,command,*,extra_mounts):
            # Expected outcomes stay exclusively in the host oracle receipt.
            assert not any('oracle' in dst for _,dst in extra_mounts)
            from pathlib import Path
            import json
            binding=next(Path(src) for src,dst in extra_mounts if dst=='/repair/binding.json')
            assert 'expected' not in json.loads(binding.read_text())
            destination='/opt/eva/src/eva/'+PATH
            if not any(dst==destination for _,dst in extra_mounts):
                extra_mounts=(*extra_mounts,(baseline,destination))
            return super()._container(image,command,extra_mounts=extra_mounts)
    evaluator=RuntimeRepairEvaluator(Targets(),EvaluationConfig(image=image,
        command=('/usr/local/bin/python',),deadline_seconds=30),executor=Executor)
    def candidate(source):
        return {'contract':'EvaRuntimeRepairCandidate/v1','target_id':identity,
            'diagnosis':'Synthetic missing optional input handling', 'files':[{
                'path':PATH,'baseline_sha256':source_sha,
                'candidate_sha256':hashlib.sha256(source.encode()).hexdigest(),'candidate_content':source}]}
    return evaluator,target,candidate


def test_candidate_cannot_add_unobserved_protected_or_stale_sources(comparison):
    _,target,candidate=comparison
    value=candidate(BASELINE+'\n')
    value['files'][0]['path']='operations/component_evaluation.py'
    with pytest.raises(ValueError):validate_repair_candidate(target,value)
    value=candidate(BASELINE+'\n');value['files'][0]['baseline_sha256']='0'*64
    with pytest.raises(ValueError):validate_repair_candidate(target,value)


def test_unconfirmed_or_nonindependent_oracle_is_not_executed(comparison):
    evaluator,target,candidate=comparison
    # Change the independent receipt and rebind its current hash. A byte-valid
    # artifact still cannot claim to have seen no candidate after seeing it.
    import json
    from pathlib import Path
    criterion_path=Path(target['criteria']['path']);criteria=json.loads(criterion_path.read_text())
    work_path=Path(criteria['independent_work']['path']);work=json.loads(work_path.read_text())
    work['candidate_seen']=True;work_path.write_text(json.dumps(work))
    criteria['independent_work']['sha256']=hashlib.sha256(work_path.read_bytes()).hexdigest()
    criterion_path.write_text(json.dumps(criteria));target['criteria']['sha256']=hashlib.sha256(criterion_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='separate original behavior work'):
        evaluator.evaluate(target['target_id'],candidate(BASELINE+'\n'))


@pytest.mark.skipif(not os.environ.get('EVA_RUNTIME_REPAIR_TEST_IMAGE'),reason='explicit local disposable Docker image required')
@pytest.mark.parametrize('outcome',['repair','wrong_answer','regression','read_oracle'])
def test_real_isolation_compares_original_failure_and_independent_regressions(comparison,outcome):
    evaluator,target,candidate=comparison
    if outcome=='repair':source="def product_behavior(value):\n    return '' if value is None else value.strip()\n"
    elif outcome=='wrong_answer':source="def product_behavior(value):\n    return 'wrong' if value is None else value.strip()\n"
    elif outcome=='regression':source="def product_behavior(value):\n    return ''\n"
    else:source="def product_behavior(value):\n    if value is None:\n        return open('/repair/oracle.json').read()\n    return value.strip()\n"
    result=evaluator.evaluate(target['target_id'],candidate(source))
    assert result['state']==('awaiting_independent_review' if outcome=='repair' else 'baseline_retained')
    assert result['installed'] is False and result['semantic_improvement_verified'] is False
    assert result['expected_outcomes_outside_candidate'] is True
    for legs in result['services'].values():
        assert legs['baseline']['cases'][0]['original_error_reproduced'] is True
        assert all(row['passed'] for row in legs['baseline']['cases'])
        assert legs['baseline']['changed_sources_executed'] is True
        assert legs['candidate']['changed_sources_executed'] is True
