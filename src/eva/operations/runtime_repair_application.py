"""Typed runtime-repair releases for the existing retained-volume image updater."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.reviewed_component_application import _artifact, _identifier, _path
from eva.operations.image_update import image_update_services
from eva.operations.runtime_repair_target import verified_failure_binding
from eva.operations.runtime_repair_evaluation import (
    _RUNNER, _case_result, _matches, validate_repair_candidate, validate_repair_oracle,
)


def verify_runtime_repair_release(review_id,release):
    fields={'kind','target','repair_candidate','comparison','review','review_work',
        'build','installed','candidate','project','update_receipt','behavior_verifier'}
    if not isinstance(release,dict) or set(release)!=fields or release['kind']!='runtime_repair':
        raise ValueError('exact managed runtime repair release required')
    data={key:_artifact(release[key])[1] for key in
        ('target','repair_candidate','comparison','review','review_work','build','installed','candidate')}
    target,candidate,comparison,review,work,build=(data[k] for k in
        ('target','repair_candidate','comparison','review','review_work','build'))
    verified_failure_binding(target['failure'])
    candidate=validate_repair_candidate(target,candidate)
    if review_id!=canonical_sha256(candidate):raise ValueError('repair release identity differs')
    from eva.operations.runtime_repair_development import validate_repair_review
    validate_repair_review(target,candidate,comparison,review)
    if (review['decision']!='accept' or comparison['target_id']!=target['target_id']
            or comparison['candidate_sha256']!=review_id
            or comparison['criteria_sha256']!=target['criteria']['sha256']
            or comparison['state']!='awaiting_independent_review'
            or comparison['expected_outcomes_outside_candidate'] is not True
            or release['behavior_verifier']!=target['criteria']):
        raise ValueError('independent repair approval is not exact')
    _,criteria=_artifact(target['criteria']);_,oracle=_artifact(criteria['oracle'])
    validate_repair_oracle(target,oracle)
    meta=work.get('work',{})
    if (work.get('contract')!='EvaIndependentRepairReviewWorkReceipt/v1'
            or work.get('target_id')!=target['target_id'] or work.get('candidate_sha256')!=review_id
            or work.get('comparison_sha256')!=canonical_sha256(comparison)
            or work.get('review_sha256')!=release['review']['sha256']
            or meta.get('role')!='independent_review' or meta.get('work_succeeded') is not True
            or meta.get('usage_measured') is not True or meta.get('target_id')!=target['target_id']
            or meta.get('candidate_sha256')!=review_id
            or meta.get('comparison_sha256')!=canonical_sha256(comparison)):
        raise ValueError('separate exact review work evidence required')
    changed=image_update_services(data['installed'],data['candidate'])
    before={s:data['installed']['services'][s]['image'] for s in changed}
    after={s:data['candidate']['services'][s]['image'] for s in changed}
    expected_sources={e['path']:e['candidate_sha256'] for e in candidate['files']}
    if (set(changed)!={'core','delegated-work-worker'}
            or before!={s:r['image'] for s,r in target['installation'].items()}
            or build.get('contract')!='EvaRuntimeRepairBuild/v1'
            or build.get('target_id')!=target['target_id'] or build.get('candidate_sha256')!=review_id
            or build.get('before')!=before or build.get('images')!=after
            or build.get('sourceHashes')!=expected_sources or build.get('installed') is not False):
        raise ValueError('repair build and retained configuration differ')
    for image in (*before.values(),*after.values()):
        if not isinstance(image,str) or not image.startswith('sha256:'):
            raise ValueError('immutable repair image required')
        _identifier(image[7:])
    if not isinstance(release['project'],str) or not release['project']:
        raise ValueError('existing application project required')
    receipt=_path(release['update_receipt'],exists=False)
    if receipt in {_path(v['path']) for v in release.values() if isinstance(v,dict)}:
        raise ValueError('repair update receipt collides with input')
    # Parent must be an existing host-owned protected artifact directory.
    import os,stat
    info=receipt.parent.stat()
    if info.st_uid!=os.geteuid() or info.st_mode&0o022 or not stat.S_ISDIR(info.st_mode):
        raise ValueError('private managed repair receipt directory required')
    return {'review_id':review_id,'kind':'runtime_repair','project':release['project'],
        'installed':_path(release['installed']['path']),'candidate':_path(release['candidate']['path']),
        'receipt':receipt,'candidate_images':after,'accepted_review_sha256':canonical_sha256(review),
        'source_path':'runtime_repair','behavior_verifier':release['behavior_verifier'],
        'repair_target':target,'repair_candidate':candidate,'repair_review_work':meta}


def verify_installed_repair(observation,*,release,config,run,executor=None):
    """Read actual container pins, execute their exact images in the existing sandbox.

    No synthetic driver runs with family credentials in the live core. This
    confirms installed source plus isolated original/regression behavior; it is
    not an actuator replay or a claim about every production input.
    """
    from eva.operations.component_evaluation import ComponentEvaluator
    if executor is None:executor=ComponentEvaluator
    target,candidate=release['repair_target'],release['repair_candidate']
    _,criteria=_artifact(target['criteria']);_,oracle=_artifact(criteria['oracle'])
    validate_repair_oracle(target,oracle)
    candidate_leg=observation['target']=='candidate'
    sources=dict(target['sources'])
    if candidate_leg:sources.update({e['path']:e['candidate_sha256'] for e in candidate['files']})
    expected_images=(release['candidate_images'] if candidate_leg else
        {s:r['image'] for s,r in target['installation'].items()})
    if observation['images']!=expected_images or set(observation['containers'])!=set(expected_images):
        raise ValueError('installed repair observation differs')
    results={};cache={};verified=True
    with tempfile.TemporaryDirectory(prefix='eva-installed-repair-') as temporary:
        directory=Path(temporary);directory.chmod(0o755)
        runner=directory/'runner.py';runner.write_text(_RUNNER);runner.chmod(0o444)
        for service,image in expected_images.items():
            identifier=observation['containers'][service]
            current=json.loads(run(['docker','inspect',identifier]))[0]
            if current['Id']!=identifier or current['Image']!=image or not current['State']['Running']:
                raise ValueError('actual installed repair container changed')
            read_script=('import hashlib,json,pathlib;root=pathlib.Path("/opt/eva/src/eva");'
                'print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in '+repr(sources)+'}))')
            actual=json.loads(run(['docker','exec',identifier,'python','-I','-c',read_script]))
            if actual!=sources:raise ValueError('live container source differs from accepted repair')
            rows=[];executed=set()
            for case in oracle['cases']:
                key=(image,case['id'])
                if key not in cache:
                    binding={'target_id':target['target_id'],'sources':sources,
                        'criteria_sha256':target['criteria']['sha256'],'case_id':case['id']}
                    file_id=hashlib.sha256((image+':'+case['id']).encode()).hexdigest()
                    bindfile=directory/('binding-'+file_id+'.json');bindfile.write_bytes(canonical_json_bytes(binding));bindfile.chmod(0o444)
                    driver=directory/('driver-'+file_id+'.py');driver.write_text(case['driver_source']);driver.chmod(0o444)
                    output=executor(replace(config,image=image))._container(image,
                        ('/usr/local/bin/python','/repair/runner.py'),extra_mounts=(
                        (runner,'/repair/runner.py'),(bindfile,'/repair/binding.json'),(driver,'/repair/driver.py')))
                    cache[key]=_case_result(output,target=target,criteria_sha256=target['criteria']['sha256'],case_id=case['id'])
                result=cache[key];outcome=result['outcome'];executed.update(result['executed_sources'])
                if candidate_leg or case['id']!=oracle['original_case_id']:
                    passed=_matches(outcome,case['expected'])
                else:
                    observed=target['failure']['observation']
                    passed=(outcome.get('kind')=='error' and outcome.get('error_type_sha256')==observed['error_type_sha256']
                        and any({'path':f['path'],'function':f['function']} in outcome.get('frames',()) for f in observed['frames']))
                rows.append({'case_id':case['id'],'passed':passed,'outcome_sha256':canonical_sha256(outcome)})
                verified=verified and passed
            source_executed={e['path'] for e in candidate['files']}<=executed
            verified=verified and source_executed
            results[service]={'cases':rows,'source_hashes':actual,'changed_sources_executed':source_executed}
    return {'verified':verified,'verifier_sha256':release['behavior_verifier']['sha256'],
        'verification_scope':'installed_container_source_and_isolated_original_behavior',
        'target_id':target['target_id'],'candidate_sha256':canonical_sha256(candidate),
        'services':results,'external_actions_replayed':False}


def completed_repair_proof(job,release):
    """Mandatory host acceptance/artifact/install verification before core closure."""
    checked=verify_runtime_repair_release(job['candidate_sha256'],release)
    from eva.operations.component_release_import import load_object
    receipt=load_object(_path(checked['receipt']))
    candidate=_artifact(release['candidate'])[1]
    from eva.operations.reviewed_component_application import _behavior_confirms
    images=checked['candidate_images']
    services=receipt.get('services',{})
    if (receipt.get('contract')!='EvaImageUpdate/v1' or receipt.get('project')!=release['project']
            or receipt.get('status')!='behavior_verified_with_retained_mounts'
            or set(services)!=set(images)
            or any(services[s].get('candidate_image')!=image for s,image in images.items())
            or not _behavior_confirms(receipt.get('behavior_verification'),release=release,
                configuration=candidate,images=images,
                containers={s:services[s].get('current_container') for s in images},target='candidate')):
        raise ValueError('exact applied repair behavior has not been confirmed')
    return {'target_id':job['target_id'],'candidate_sha256':job['candidate_sha256'],
        'comparison_sha256':job['comparison_sha256'],'independent_review_sha256':release['review']['sha256'],
        'application_receipt_sha256':hashlib.sha256(checked['receipt'].read_bytes()).hexdigest(),
        'installed_behavior_sha256':canonical_sha256(receipt['behavior_verification']),
        'review_task_id':checked['repair_review_work']['task_id']}
