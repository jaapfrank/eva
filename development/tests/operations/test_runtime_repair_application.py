"""Repair-specific acceptance and last predispatch STOP checks; no real updater."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import ComponentApplicationRuntime
from eva.operations.reviewed_component_application import ReviewedComponentApplications, _verify_release
from eva.operations.runtime_repair_application import (
    completed_repair_proof, record_repair_peer, verify_installed_repair, verify_repair_peer,
)
from eva.operations.runtime_repair_development import _put
from tests.operations.test_runtime_repair_development import flow


@pytest.fixture
def accepted(flow):
    f=flow;c=f.controller;c.request({**f.request,'action':'submit'})
    f.meta.update(state='succeeded',work_succeeded=True,usage_measured=True)
    c.run_once()
    f.metadata['work:eva-initiative:'+'1'*64].update(state='succeeded',work_succeeded=True,usage_measured=True)
    for _ in range(3):c.run_once()
    meta=f.metadata['work:eva-initiative:'+'2'*64]
    f.review.update(decision='accept',behavioral_reason='The original failure and regressions are independently exercised.',
        comparison_sha256=meta['comparison_sha256'])
    meta.update(state='succeeded',work_succeeded=True,usage_measured=True)
    assert c.run_once()=='publishing'
    job=c.jobs[f.identity];directory=c.root/f.identity
    target=json.loads(Path(job['target']['path']).read_text())
    before={s:r['image'] for s,r in target['installation'].items()}
    images={s:'sha256:'+str(i)*64 if f.repair_role in {None, s} else image
            for i,(s,image) in enumerate(before.items(),3)}
    installed={'services':{s:{'image':image,'volumes':[{'type':'volume','source':'retained','target':'/data'}]}
        for s,image in before.items()},'volumes':{'retained':{'name':'unchanged-retained'}}}
    proposed=deepcopy(installed)
    for service,image in images.items():proposed['services'][service]['image']=image
    build={'contract':'EvaRuntimeRepairBuild/v1','target_id':f.identity,'candidate_sha256':job['candidate_sha256'],
        'before':before,'images':images,'installed':False,'sourceHashes':{'application/example.py':f.candidate['files'][0]['candidate_sha256']}}
    release={'kind':'runtime_repair','target':job['target'],'repair_candidate':job['candidate'],
        'comparison':job['comparison'],'review':job['review'],'review_work':job['review_work'],
        'build':_put(directory/'build.json',build),'installed':_put(directory/'installed.json',installed),
        'candidate':_put(directory/'proposed.json',proposed),'project':'synthetic-repair-project',
        'update_receipt':str(directory/'update.json'),'behavior_verifier':target['criteria']}
    return f,job,release,images


def test_only_exact_accepted_review_and_unchanged_settings_form_a_managed_release(accepted):
    f,job,release,images=accepted
    checked=_verify_release(job['candidate_sha256'],release)
    assert checked['candidate_images']==images and checked['kind']=='runtime_repair'
    changed=deepcopy(release)
    review=json.loads(Path(changed['review']['path']).read_text());review['decision']='reject'
    changed['review']=_put(f.tmp_path/'rejected-review.json',review)
    with pytest.raises(ValueError):_verify_release(job['candidate_sha256'],changed)
    changed=deepcopy(release)
    compose=json.loads(Path(changed['candidate']['path']).read_text())
    compose['services']['core']['volumes'][0]['source']='other-volume'
    changed['candidate']=_put(f.tmp_path/'changed-settings.json',compose)
    with pytest.raises(ValueError,match='configuration or data'):_verify_release(job['candidate_sha256'],changed)


def test_completion_requires_real_bound_update_and_behavior_receipt_not_hash_strings(accepted):
    f,job,release,images=accepted
    with pytest.raises(FileNotFoundError):completed_repair_proof(job,release)
    proposed=json.loads(Path(release['candidate']['path']).read_text())
    containers={s:'synthetic-'+s for s in images}
    behavior={'contract':'EvaImageBehaviorVerification/v1','project':release['project'],
        'configuration_sha256':canonical_sha256(proposed),'target':'candidate','images':images,
        'containers':containers,'verified':True,'evidence':{'verified':True,
            'verifier_sha256':release['behavior_verifier']['sha256']}}
    receipt={'contract':'EvaImageUpdate/v1','project':release['project'],
        'status':'behavior_verified_with_retained_mounts',
        'services':{s:{'candidate_image':image,'current_container':containers[s]} for s,image in images.items()},
        'behavior_verification':behavior}
    _put(Path(release['update_receipt']),receipt)
    proof=completed_repair_proof(job,release)
    assert proof['comparison_sha256']==job['comparison_sha256']
    receipt['behavior_verification']['verified']=False
    Path(release['update_receipt']).write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='has not been confirmed'):completed_repair_proof(job,release)


def test_review_stop_while_queued_prevents_every_updater_dispatch(accepted,monkeypatch):
    f,job,release,images=accepted
    import eva.operations.runtime_image_update as updates
    dispatched=[];monkeypatch.setattr(updates,'update_runtime',lambda **kwargs:dispatched.append(kwargs))
    runtime=ComponentApplicationRuntime({'core_container':'synthetic-core','scope_sha256':'c'*64,
        'runtime_repair_enabled':True})
    def call(action,**kwargs):
        if action=='binding':return deepcopy(job['failure'])
        raise ValueError('review received STOP before the queued update could dispatch')
    monkeypatch.setattr(runtime,'repair_call',call)
    ready=['waiting']
    apps=ReviewedComponentApplications({'receipt_root':str(f.tmp_path/'applications'),
        'releases':{},'poll_seconds':.01},ready_to_apply=lambda _:ready[0],apply_release=runtime.apply_release)
    try:
        apps.register(job['candidate_sha256'],release)
        request='9'*64;apps.request(job['candidate_sha256'],request,task_id=job['task_id'])
        assert apps.status(request,task_id=job['task_id'])['installed'] is False
        ready[0]='ready'
        with apps.condition:apps.condition.notify_all()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=apps.status(request,task_id=job['task_id'])
            if status['state']=='unconfirmed':break
            time.sleep(.01)
        assert status['state']=='unconfirmed'
        assert dispatched==[] and not Path(release['update_receipt']).exists()
    finally:apps.close()


@pytest.mark.parametrize('flow', ['core', 'delegated-work-worker'], indirect=True)
def test_single_role_release_and_application_fence_the_unchanged_peer(accepted, monkeypatch):
    f, job, release, images = accepted
    role = f.repair_role
    peer, = images.keys() - {role}
    checked = _verify_release(job['candidate_sha256'], release)
    assert checked['candidate_images'] == {role: images[role]}
    assert images[peer] == checked['repair_target']['installation'][peer]['image']
    checked['task_id'] = job['task_id']

    changed = deepcopy(release)
    proposed = json.loads(Path(release['candidate']['path']).read_bytes())
    proposed['services'][peer]['image'] = 'sha256:' + '8'*64
    changed['candidate'] = _put(f.tmp_path/'changed-peer.json', proposed)
    with pytest.raises(ValueError, match='retained configuration'):
        _verify_release(job['candidate_sha256'], changed)

    identifier = '7'*64
    labels = {'com.docker.compose.project': checked['project'], 'com.docker.compose.service': peer,
              'com.docker.compose.config-hash': 'peer-hash'}
    current = {'Id': identifier, 'Image': images[peer], 'RestartCount': 0,
               'State': {'Running': True, 'Pid': 20, 'StartedAt': '2026-11-07T00:00:00Z'},
               'Config': {'Labels': labels, 'Env': ['B=2', 'A=1']},
               'HostConfig': {'ReadonlyRootfs': True, 'Tmpfs': {}},
               'Mounts': [{'Destination': '/data', 'Type': 'volume', 'Name': 'unchanged-retained', 'RW': True}],
               'NetworkSettings': {'Networks': {}}}
    selected_id = '5'*64
    selected = deepcopy(current)
    selected['Id'] = selected_id
    selected['Image'] = checked['repair_target']['installation'][role]['image']
    selected['Config']['Labels'].update({'com.docker.compose.service': role,
                                         'com.docker.compose.config-hash': 'role-hash'})
    dispatched = []

    def run(argv, **options):
        if argv[-4:] == ['ps', '--all', '-q', peer]:
            return identifier
        if argv[-4:] == ['ps', '--all', '-q', role] or argv[-3:] == ['ps', '-q', role]:
            return selected_id
        if argv == ['docker', 'inspect', identifier]:
            return json.dumps([current])
        if argv == ['docker', 'inspect', selected_id]:
            return json.dumps([selected])
        if argv[-3:] == ['config', '--hash', peer]:
            return peer + ' peer-hash'
        if argv[-3:] == ['config', '--hash', role]:
            return role + ' role-hash'
        if argv[:4] == ['docker', 'exec', selected_id, 'python']:
            return json.dumps(source_hashes)
        if 'up' in argv:
            dispatched.append(argv[-1])
            return ''
        raise AssertionError(argv)

    record_repair_peer(checked, run)
    witness = verify_repair_peer(checked, run)
    assert witness['service'] == peer and witness['container'] == identifier
    # Docker's env order is presentation, but its process generation isn't.
    current['Config']['Env'].reverse()
    assert verify_repair_peer(checked, run) == witness

    runtime = ComponentApplicationRuntime({'core_container': 'synthetic-core',
        'scope_sha256': 'c'*64, 'runtime_repair_enabled': True})
    monkeypatch.setattr(runtime, 'ready_to_apply', lambda _: 'ready')
    monkeypatch.setattr(runtime, 'repair_call', lambda action, **kw:
        checked['repair_target']['failure'] if action == 'binding' else checked['repair_review_work'])
    command, before_recovery = runtime._single_role_repair_command(checked, run)
    pending = {'contract': 'EvaImageUpdate/v1', 'project': checked['project'],
        'status': 'dispatched_result_unknown',
        'services': {role: {'previous_image': selected['Image'], 'candidate_image': images[role],
                            'previous_container': selected_id, 'mounts': selected['Mounts']}}}
    checked['receipt'].write_text(json.dumps(pending))
    argv = ['docker', 'compose', '--project-name', checked['project'],
        '--file', str(checked['candidate']), '--profile', '*', 'up', '--detach',
        '--no-deps', '--no-build', '--pull', 'never', '--wait', role]
    with pytest.raises(ValueError, match='dispatch scope'):
        command([*argv, peer], deadline=None)
    current['State']['Pid'] += 1
    with pytest.raises(ValueError, match='peer changed'):
        command(argv, deadline=None)
    current['State']['Pid'] -= 1
    monkeypatch.setattr(runtime, 'ready_to_apply', lambda _: 'cancelled')
    with pytest.raises(ValueError, match='not ready'):
        command(argv, deadline=None)
    assert dispatched == []
    monkeypatch.setattr(runtime, 'ready_to_apply', lambda _: 'ready')
    selected['HostConfig']['ReadonlyRootfs'] = False
    with pytest.raises(ValueError, match='read-only source'):
        command(argv, deadline=None)
    assert dispatched == []
    selected['HostConfig']['ReadonlyRootfs'] = True
    command(argv, deadline=None)
    assert dispatched == [role]

    pending['dispatch_callback_returned'] = True
    pending['status'] = 'incomplete_inspect_retained_installation'
    checked['receipt'].write_text(json.dumps(pending))
    selected['State']['Running'] = False
    with pytest.raises(ValueError, match='selected repair generation'):
        before_recovery()
    selected['State']['Running'] = True
    selected['Image'] = images[role]
    assert before_recovery() is True
    selected['State']['Pid'] += 1
    pending['status'] = 'recovery_result_unknown'
    pending['recovery'] = {'dispatch_attempted': True}
    checked['receipt'].write_text(json.dumps(pending))
    recovery_argv = [str(checked['installed']) if item == str(checked['candidate']) else item for item in argv]
    with pytest.raises(ValueError, match='generation changed during recovery'):
        command(recovery_argv, deadline=None)
    assert dispatched == [role]

    from eva.operations.component_evaluation import EvaluationConfig
    configuration = json.loads(Path(release['candidate']['path']).read_bytes())
    source_hashes = {entry['path']: entry['candidate_sha256'] for entry in f.candidate['files']}
    observation = {'target': 'candidate', 'images': {role: images[role]}, 'containers': {role: selected_id}}
    evaluated = []
    drift = False

    class Executor:
        def __init__(self, config):
            assert config.image == images[role]

        def _container(self, image, command, *, extra_mounts):
            assert image == images[role]
            binding = next(json.loads(path.read_bytes()) for path, dest in extra_mounts if dest == '/repair/binding.json')
            case = next(case for case in f.oracle['cases'] if case['id'] == binding['case_id'])
            evaluated.append(case['id'])
            if drift:
                selected['State']['Pid'] += 1
            proof = {key: binding[key] for key in ('target_id', 'criteria_sha256', 'case_id')}
            proof.update(contract='EvaRuntimeRepairCaseResult/v1', outcome=case['expected'],
                         executed_sources=source_hashes)
            return {'exit_code': 0, 'timed_out': False, 'stdout_truncated': False, 'stderr_truncated': False,
                    'stdout': json.dumps(proof)}

    config = EvaluationConfig(image=images[role], command=('python',))
    evidence = verify_installed_repair(observation, release=checked, config=config, run=run, executor=Executor)
    assert evidence['verified'] is True and evidence['unchanged_peer'] == witness
    assert set(evidence['services']) == {role} and evaluated == ['original', 'normal']
    drift = True
    with pytest.raises(ValueError, match='generation changed during behavior'):
        verify_installed_repair(observation, release=checked, config=config, run=run, executor=Executor)
    behavior = {'contract': 'EvaImageBehaviorVerification/v1', 'project': release['project'],
        'configuration_sha256': canonical_sha256(configuration), 'target': 'candidate',
        'images': {role: images[role]}, 'containers': {role: selected_id},
        'verified': True, 'evidence': evidence}
    receipt = {'contract': 'EvaImageUpdate/v1', 'project': release['project'],
        'status': 'behavior_verified_with_retained_mounts',
        'services': {role: {'candidate_image': images[role], 'current_container': selected_id}},
        'behavior_verification': behavior}
    del evidence['unchanged_peer']
    checked['receipt'].write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='peer preservation'):
        completed_repair_proof(job, release)
    evidence['unchanged_peer'] = witness
    checked['receipt'].write_text(json.dumps(receipt))
    assert completed_repair_proof(job, release)['target_id'] == job['target_id']
