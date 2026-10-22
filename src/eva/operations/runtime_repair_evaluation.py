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

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
from eva.operations.reviewed_component_application import _artifact


_RUNNER = r'''
import contextlib,hashlib,importlib.util,io,json,pathlib,sys
cfg=json.loads(pathlib.Path('/repair/binding.json').read_text())
root=pathlib.Path('/opt/eva/src/eva')
for relative,digest in cfg['sources'].items():
    p=root/relative
    assert p.resolve()==p and hashlib.sha256(p.read_bytes()).hexdigest()==digest
spec=importlib.util.spec_from_file_location('eva_independent_repair_oracle','/repair/oracle.py')
oracle=importlib.util.module_from_spec(spec)
result={'contract':'EvaRuntimeRepairLeg/v1','target_id':cfg['target_id'],
        'criteria_sha256':cfg['criteria_sha256'],'regression_checks':0,
        'original_error_reproduced':False,'behavior_verified':False,'complete':False}
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(oracle)
        count=oracle.regressions()
        assert type(count) is int and 1<=count<=10000
        result['regression_checks']=count
        try:
            observed=oracle.exercise()
        except Exception as error:
            t=type(error)
            expected=cfg['observation']
            same=hashlib.sha256((t.__module__+'.'+t.__qualname__).encode()).hexdigest()==expected['error_type_sha256']
            frames=set()
            trace=error.__traceback__
            while trace is not None:
                p=pathlib.Path(trace.tb_frame.f_code.co_filename).resolve()
                if p.is_relative_to(root):
                    frames.add((p.relative_to(root).as_posix(),trace.tb_frame.f_code.co_name+':'+str(trace.tb_lineno)))
                trace=trace.tb_next
            result['original_error_reproduced']=same and any(
                (f['path'],f['function']) in frames for f in expected['frames'])
        else:
            result['behavior_verified']=oracle.check_outcome(observed) is True
        result['complete']=True
except Exception:
    pass
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
        if (not 1 <= len(raw) <= 262144 or hashlib.sha256(raw).hexdigest() != entry['candidate_sha256']
                or entry['candidate_sha256'] == entry['baseline_sha256']):
            raise ValueError('repair candidate bytes differ or make no change')
        ast.parse(raw, filename=entry['path'])
        total += len(raw);seen.add(entry['path'])
    if total > 524288:
        raise ValueError('repair candidate exceeds source bound')
    return json.loads(canonical_json_bytes(value))


def _leg(result, *, target, criteria_sha256):
    if (result['exit_code'] != 0 or any(result[key] is not False for key in
            ('timed_out', 'stdout_truncated', 'stderr_truncated'))):
        raise ValueError('isolated runtime repair comparison is incomplete')
    value = json.loads(result['stdout'])
    fields = {'contract', 'target_id', 'criteria_sha256', 'regression_checks',
              'original_error_reproduced', 'behavior_verified', 'complete'}
    if (not isinstance(value, dict) or set(value) != fields
            or value['contract'] != 'EvaRuntimeRepairLeg/v1'
            or value['target_id'] != target['target_id']
            or value['criteria_sha256'] != criteria_sha256
            or value['complete'] is not True or type(value['regression_checks']) is not int
            or not 1 <= value['regression_checks'] <= 10000
            or any(type(value[k]) is not bool for k in ('original_error_reproduced', 'behavior_verified'))):
        raise ValueError('isolated runtime repair evidence differs')
    return value


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
        from eva.operations.reviewed_component_application import _verified_behavior_source
        oracle_path, oracle_raw = _verified_behavior_source(criteria['oracle'])
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
                  'services': {}, 'installed': False, 'semantic_improvement_verified': False}
        with tempfile.TemporaryDirectory(prefix='eva-runtime-repair-') as temporary:
            directory = Path(temporary);directory.chmod(0o755)
            oracle = directory/'oracle.py';oracle.write_bytes(oracle_raw);oracle.chmod(0o444)
            runner = directory/'runner.py';runner.write_text(_RUNNER);runner.chmod(0o444)
            proposed = {}
            for index,entry in enumerate(candidate['files']):
                source = directory/('candidate-'+str(index)+'.py')
                source.write_text(entry['candidate_content']);source.chmod(0o444)
                proposed[entry['path']] = source
            for service,runtime in target['installation'].items():
                self.targets.current(identity)
                executor = self.executor(replace(self.config, image=runtime['image']))
                legs = {}
                for leg in ('baseline','candidate'):
                    self.targets.current(identity)
                    sources = dict(target['sources'])
                    if leg == 'candidate':
                        sources.update({e['path']:e['candidate_sha256'] for e in candidate['files']})
                    binding = {'target_id':identity, 'sources':sources,
                        'criteria_sha256':target['criteria']['sha256'],
                        'observation':target['failure']['observation']}
                    bindfile = directory/('binding-'+service+'-'+leg+'.json')
                    bindfile.write_bytes(canonical_json_bytes(binding));bindfile.chmod(0o444)
                    mounts = [(oracle,'/repair/oracle.py'),(runner,'/repair/runner.py'),
                              (bindfile,'/repair/binding.json')]
                    if leg == 'candidate':
                        mounts.extend((p,'/opt/eva/src/eva/'+relative) for relative,p in proposed.items())
                    result = executor._container(runtime['image'],
                        ('/usr/local/bin/python','/repair/runner.py'),extra_mounts=tuple(mounts))
                    legs[leg] = _leg(result,target=target,criteria_sha256=target['criteria']['sha256'])
                report['services'][service] = legs
        self.targets.current(identity)
        eligible = all(legs['baseline']['original_error_reproduced'] is True
            and legs['baseline']['behavior_verified'] is False
            and legs['candidate']['original_error_reproduced'] is False
            and legs['candidate']['behavior_verified'] is True
            and legs['baseline']['regression_checks'] == legs['candidate']['regression_checks']
            for legs in report['services'].values())
        report['state'] = 'awaiting_independent_review' if eligible else 'baseline_retained'
        return report
