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
from eva.operations.runtime_repair_application import completed_repair_proof
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
    images={s:'sha256:'+str(i)*64 for i,s in enumerate(before,3)}
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
