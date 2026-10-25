"""Failure-bound comparisons through the existing isolated component executor.

The independent test producer sees the original failure/baseline, not candidate
code. Its frozen oracle is a separate host-owned input. Passing this comparison
only permits independent review; it never approves or installs a repair.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile

from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES, MAX_REPAIR_TOTAL_SOURCE_BYTES
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
from eva.operations.reviewed_component_application import _artifact


_RUNNER = r'''
import contextlib,hashlib,io,json,marshal,pathlib,sys
cfg=json.loads(pathlib.Path('/repair/binding.json').read_text())
root=pathlib.Path('/opt/eva/src/eva')
expected_code={};executed={}
for relative,digest in cfg['sources'].items():
    p=root/relative
    assert p.resolve()==p and hashlib.sha256(p.read_bytes()).hexdigest()==digest
    expected_code[str(p)]=compile(p.read_bytes(),str(p),'exec',dont_inherit=True)
# Never consume pre-existing packaged bytecode for a source replacement.
sys.dont_write_bytecode=True
sys.pycache_prefix='/tmp/eva-repair-fresh-source'
def audit(event,args):
    if event=='exec' and args and args[0].co_filename in expected_code:
        code=args[0];digest=hashlib.sha256(marshal.dumps(code)).hexdigest()
        # marshal encodings can vary with string interning/reference flags.
        # Code-object equality compares the compiled code/constants instead.
        assert code==expected_code[code.co_filename]
        executed[pathlib.Path(code.co_filename).relative_to(root).as_posix()]=digest
sys.addaudithook(audit)
result={'contract':'EvaRuntimeRepairCaseResult/v1','target_id':cfg['target_id'],
        'criteria_sha256':cfg['criteria_sha256'],'case_id':cfg['case_id'],
        'outcome':None,'executed_sources':{}}
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    try:
        namespace={'__name__':'eva_independent_repair_driver','__file__':'/repair/driver.py'}
        exec(compile(pathlib.Path('/repair/driver.py').read_bytes(),'/repair/driver.py','exec',dont_inherit=True),namespace)
        value=namespace['run']()
        # The expected value and oracle are never mounted in this container.
        encoded=json.dumps(value,allow_nan=False,separators=(',',':'))
        assert len(encoded.encode())<=16384
        result['outcome']={'kind':'value','value':json.loads(encoded)}
    except Exception as error:
        t=type(error);frames=[];trace=error.__traceback__
        while trace is not None:
            p=pathlib.Path(trace.tb_frame.f_code.co_filename).resolve()
            if p.is_relative_to(root):
                frames.append({'path':p.relative_to(root).as_posix(),
                    'function':trace.tb_frame.f_code.co_name+':'+str(trace.tb_lineno)})
            trace=trace.tb_next
        result['outcome']={'kind':'error','error_type_sha256':hashlib.sha256(
            (t.__module__+'.'+t.__qualname__).encode()).hexdigest(),'frames':frames[-16:]}
result['executed_sources']=executed
print(json.dumps(result,separators=(',',':')))
'''


def validate_repair_candidate(target, value):
    """Validate exact multi-file proposal bytes; no model-selected host paths."""
    if (not isinstance(value, dict) or set(value) != {'contract', 'target_id', 'files', 'diagnosis'}
            or value['contract'] != 'EvaRuntimeRepairCandidate/v1'
            or value['target_id'] != target['target_id']
            or not isinstance(value['diagnosis'], str) or not 1 <= len(value['diagnosis']) <= 4000
            or not isinstance(value['files'], list) or not 1 <= len(value['files']) <= 8):
        raise ValueError('runtime repair candidate binding differs')
    seen, total = set(), 0
    for entry in value['files']:
        if (not isinstance(entry, dict) or set(entry) != {
                'path', 'baseline_sha256', 'candidate_sha256', 'candidate_content'}
                or not isinstance(entry['path'], str) or entry['path'] in seen
                or entry['path'] not in target['sources']
                or entry['baseline_sha256'] != target['sources'][entry['path']]
                or not isinstance(entry['candidate_content'], str)):
            raise ValueError('repair file is outside the current host target')
        from eva.operations.runtime_repair_target import repairable_source
        if not repairable_source(entry['path']):
            raise ValueError('repair file affects a protected component')
        raw = entry['candidate_content'].encode('utf-8')
        if (not 1 <= len(raw) <= MAX_REPAIR_SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != entry['candidate_sha256']
                or entry['candidate_sha256'] == entry['baseline_sha256']):
            raise ValueError('repair candidate bytes differ or make no change')
        ast.parse(raw, filename=entry['path'])
        total += len(raw);seen.add(entry['path'])
    if total > MAX_REPAIR_TOTAL_SOURCE_BYTES:
        raise ValueError('repair candidate exceeds source bound')
    return json.loads(canonical_json_bytes(value))


def validate_repair_oracle(target, value):
    """Independent expected outcomes remain host-side, outside candidate execution."""
    import re
    if (not isinstance(value, dict) or set(value) != {
            'contract', 'target_id', 'original_case_id', 'cases'}
            or value['contract'] != 'EvaIndependentRuntimeRepairCases/v1'
            or value['target_id'] != target['target_id']
            or not isinstance(value['cases'], list) or not 2 <= len(value['cases']) <= 16):
        raise ValueError('independent repair cases differ')
    seen = set()
    for case in value['cases']:
        if (not isinstance(case, dict) or set(case) != {'id', 'driver_source', 'expected'}
                or not isinstance(case['id'], str) or re.fullmatch(r'[A-Za-z0-9_]{1,64}', case['id']) is None
                or case['id'] in seen or not isinstance(case['driver_source'], str)
                or not 1 <= len(case['driver_source'].encode()) <= 8192):
            raise ValueError('bounded independent repair case required')
        seen.add(case['id'])
        ast.parse(case['driver_source'], filename='independent-repair-driver.py')
        expected = case['expected']
        if not isinstance(expected, dict) or expected.get('kind') not in {'value', 'error'}:
            raise ValueError('independent expected outcome differs')
        if expected['kind'] == 'value':
            if set(expected) != {'kind', 'value'} or len(canonical_json_bytes(expected)) > 16384:
                raise ValueError('bounded independent expected value required')
        elif (set(expected) != {'kind', 'error_type_sha256'}
                or not isinstance(expected['error_type_sha256'], str)
                or re.fullmatch(r'[0-9a-f]{64}', expected['error_type_sha256']) is None):
            raise ValueError('independent expected exception differs')
    if not isinstance(value['original_case_id'], str) or value['original_case_id'] not in seen:
        raise ValueError('independent original failure case missing')
    original = next(c for c in value['cases'] if c['id'] == value['original_case_id'])
    if (original['expected']['kind'] == 'error' and original['expected']['error_type_sha256']
            == target['failure']['observation']['error_type_sha256']):
        raise ValueError('independent original case cannot retain the observed failure')
    return json.loads(canonical_json_bytes(value))


def _case_result(result, *, target, criteria_sha256, case_id):
    if (result['exit_code'] != 0 or any(result[key] is not False for key in
            ('timed_out', 'stdout_truncated', 'stderr_truncated'))):
        raise ValueError('isolated runtime repair comparison is incomplete')
    value = json.loads(result['stdout'])
    fields = {'contract', 'target_id', 'criteria_sha256', 'case_id', 'outcome', 'executed_sources'}
    if (not isinstance(value, dict) or set(value) != fields
            or value['contract'] != 'EvaRuntimeRepairCaseResult/v1'
            or value['target_id'] != target['target_id'] or value['case_id'] != case_id
            or value['criteria_sha256'] != criteria_sha256
            or not isinstance(value['outcome'], dict) or not isinstance(value['executed_sources'], dict)
            or set(value['executed_sources']) - set(target['sources'])):
        raise ValueError('isolated runtime repair evidence differs')
    return value


def _matches(outcome, expected):
    if outcome.get('kind') == 'error':
        outcome = {k: v for k, v in outcome.items() if k != 'frames'}
    return canonical_sha256(outcome) == canonical_sha256(expected)


class RuntimeRepairEvaluator:
    def __init__(self, targets, config, *, executor=ComponentEvaluator):
        if not isinstance(config, EvaluationConfig):
            raise ValueError('trusted existing evaluator limits required')
        self.targets, self.config, self.executor = targets, config, executor

    def evaluate(self, identity, candidate):
        target = self.targets.current(identity)
        candidate = validate_repair_candidate(target, candidate)
        _, criteria = _artifact(target['criteria'])
        if (not isinstance(criteria, dict) or set(criteria) != {
                'contract', 'target_id', 'original_failure_sha256', 'oracle', 'independent_work'}
                or criteria['contract'] != 'EvaIndependentRuntimeRepairCriteria/v1'
                or criteria['target_id'] != identity
                or criteria['original_failure_sha256'] != target['failure']['incident_event_sha256']):
            raise ValueError('independent repair criterion does not bind this failure')
        oracle_path, oracle_cases = _artifact(criteria['oracle'])
        oracle_raw = oracle_path.read_bytes()
        oracle_cases = validate_repair_oracle(target, oracle_cases)
        # The host producer's durable, separate work receipt is required; a
        # developer cannot present an arbitrary local test as independent work.
        _, independent = _artifact(criteria['independent_work'])
        if (independent.get('contract') != 'EvaIndependentRepairWorkReceipt/v1'
                or independent.get('target_id') != identity
                or independent.get('role') != 'original_behavior_oracle'
                or independent.get('candidate_seen') is not False
                or independent.get('oracle_sha256') != hashlib.sha256(oracle_raw).hexdigest()
                or independent.get('source_verified') is not True
                or independent.get('work_succeeded') is not True
                or independent.get('usage_measured') is not True):
            raise ValueError('separate original behavior work was not confirmed')
        candidate_id = canonical_sha256(candidate)
        report = {'contract': 'EvaRuntimeRepairComparison/v1', 'target_id': identity,
                  'candidate_sha256': candidate_id, 'criteria_sha256': target['criteria']['sha256'],
                  'services': {}, 'installed': False, 'semantic_improvement_verified': False,
                  'expected_outcomes_outside_candidate': True}
        eligible = True
        with tempfile.TemporaryDirectory(prefix='eva-runtime-repair-') as temporary:
            directory = Path(temporary);directory.chmod(0o755)
            runner = directory/'runner.py';runner.write_text(_RUNNER);runner.chmod(0o444)
            proposed = {}
            for index,entry in enumerate(candidate['files']):
                source = directory/('candidate-'+str(index)+'.py')
                source.write_text(entry['candidate_content']);source.chmod(0o444)
                proposed[entry['path']] = source
            cache = {}
            for service,runtime in target['installation'].items():
                self.targets.current(identity)
                executor = self.executor(replace(self.config, image=runtime['image']))
                legs = {}
                for leg in ('baseline','candidate'):
                    rows, executed = [], set()
                    for case in oracle_cases['cases']:
                        self.targets.current(identity)
                        key = (runtime['image'], leg, case['id'])
                        if key not in cache:
                            sources = dict(target['sources'])
                            if leg == 'candidate':
                                sources.update({e['path']:e['candidate_sha256'] for e in candidate['files']})
                            binding = {'target_id':identity, 'sources':sources,
                                'criteria_sha256':target['criteria']['sha256'], 'case_id':case['id']}
                            file_id = canonical_sha256(list(key))
                            bindfile = directory/('binding-'+file_id+'.json')
                            bindfile.write_bytes(canonical_json_bytes(binding));bindfile.chmod(0o444)
                            driver = directory/('driver-'+file_id+'.py')
                            driver.write_text(case['driver_source']);driver.chmod(0o444)
                            # There is no oracle or expected value in these mounts.
                            mounts = [(driver,'/repair/driver.py'),(runner,'/repair/runner.py'),
                                      (bindfile,'/repair/binding.json')]
                            if leg == 'candidate':
                                mounts.extend((p,'/opt/eva/src/eva/'+relative) for relative,p in proposed.items())
                            result = executor._container(runtime['image'],
                                ('/usr/local/bin/python','/repair/runner.py'),extra_mounts=tuple(mounts))
                            cache[key] = _case_result(result,target=target,
                                criteria_sha256=target['criteria']['sha256'],case_id=case['id'])
                        result = cache[key];outcome = result['outcome']
                        executed.update(result['executed_sources'])
                        original = case['id'] == oracle_cases['original_case_id']
                        observed = target['failure']['observation']
                        reproduced = (outcome.get('kind') == 'error'
                            and outcome.get('error_type_sha256') == observed['error_type_sha256']
                            and any({'path':f['path'],'function':f['function']} in outcome.get('frames',())
                                for f in observed['frames']))
                        matched = _matches(outcome, case['expected'])
                        passed = reproduced if leg == 'baseline' and original else matched
                        eligible = eligible and passed
                        rows.append({'id':case['id'], 'original_failure':original,
                            'original_error_reproduced':reproduced,'expected_outcome_matched':matched,
                            'outcome_sha256':canonical_sha256(outcome),
                            'expected_sha256':canonical_sha256(case['expected']),
                            'executed_sources':result['executed_sources'], 'passed':passed})
                    # Changed modules must really have executed from source, not
                    # merely exist on disk while a cached/spoofed module is used.
                    source_execution = set(proposed) <= executed
                    eligible = eligible and source_execution
                    legs[leg] = {'cases':rows,'changed_sources_executed':source_execution}
                report['services'][service] = legs
        self.targets.current(identity)
        report['state'] = 'awaiting_independent_review' if eligible else 'baseline_retained'
        return report
