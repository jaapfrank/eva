"""Independent repair stages remain native work with original source authority."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.bootstrap.native_initiative_work import admit_native_initiative
from eva.bootstrap.runtime_repair_work import admit_repair_assessment
from tests.postgres.test_autonomous_self_repair import database, postgres_engine, native_digital, costless_native, prepare, NOW
from tests.postgres.test_native_initiative_digital_work import started, result


def setup(fixture,tmp_path):
    f,repair,turn,journal,observation=prepare(fixture)
    f.profile=replace(f.profile,capability_ids=('write_programming_file','use_programming_tool',
        'use_connector_tool','draft_own_component_improvement','prepare_own_component_release'))
    f.service.digital_profile=f.profile
    f.policy['digital_work']={'worker_profile_release_sha256':f.profile.release.worker_profile_release_sha256,
        'capability_ids':list(f.profile.capability_ids)}
    f.path.write_text(json.dumps(f.policy))
    root=tmp_path/'source';path=root/'application/example.py';path.parent.mkdir(parents=True)
    path.write_text('def example(value):\n    return value.strip()\n')
    observation=replace(observation,frames=(('application/example.py','example:2',
        hashlib.sha256(path.read_bytes()).hexdigest()),))
    incident=repair.observe(turn,observation)['incident_id']
    def phase(role='original_behavior_oracle',**kw):
        return admit_repair_assessment(f.auth,f.service,incident_id=incident,role=role,target_id='8'*64,
            sources={'application/example.py':observation.frames[0][2]},source_root=root,**kw)
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    return f,turn,journal,incident,phase,claim


def finish(f,claim):
    receipt=admit_native_initiative(f.auth,claim,f.service)
    work=f.store.load(receipt.work_id,**f.service._owner)
    call=started(f,work);f.store.record_success(call,result(call),now=NOW)
    return work


def test_separate_stage_waits_for_builder_then_has_only_local_assessment_capabilities(native_digital,tmp_path):
    f,turn,_,_,phase,builder=setup(native_digital,tmp_path)
    assert phase()['status']=='not_admitted'
    built=finish(f,builder)
    assert 'use_connector_tool' not in built.spec.grant.capability_ids
    admitted=phase();assert admitted['status']=='adopted'
    assert phase()['status']=='already_adopted'
    assessment=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    receipt=admit_native_initiative(f.auth,assessment,f.service)
    work=f.store.load(receipt.work_id,**f.service._owner)
    assert set(work.spec.grant.capability_ids)=={'write_programming_file','use_programming_tool'}
    assert work.spec.origin.kind=='eva_initiative' and work.spec.execution_id!=built.spec.execution_id
    assert 'candidate_sha256' not in work.spec.task
    assert 'original_behavior_oracle' in work.spec.task


@pytest.mark.parametrize('change',['original_stop','original_erase','mandate_revoke'])
def test_newly_claimed_assessment_cannot_outlive_original_authority(native_digital,tmp_path,change):
    f,turn,journal,_,phase,builder=setup(native_digital,tmp_path)
    finish(f,builder);assert phase()['status']=='adopted'
    assessment=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    if change=='original_stop':assert f.control.stop(builder.intent_id,now=NOW)['status']=='stopped'
    elif change=='original_erase':journal.erase_message_content(principal_id=turn.principal_id,
        message_id=turn.message_id,operation_sha256='7'*64,erased_at=NOW)
    else:f.auth.revoke()
    with pytest.raises((ValueError,InitiativeMandateDenied)):
        admit_native_initiative(f.auth,assessment,f.service)


def test_original_behavior_stage_cannot_receive_candidate_or_stale_code(native_digital,tmp_path):
    _,_,_,_,phase,_=setup(native_digital,tmp_path)
    with pytest.raises(ValueError,match='must not receive a candidate'):
        phase(candidate_sha256='6'*64)
    (tmp_path/'source/application/example.py').write_text('# Source changed\n')
    with pytest.raises(ValueError,match='source changed'):phase()


def test_review_identity_binds_exact_candidate_and_comparison(native_digital,tmp_path):
    f,turn,_,_,phase,builder=setup(native_digital,tmp_path)
    finish(f,builder)
    first=phase('independent_review',candidate_sha256='5'*64,comparison_sha256='6'*64)
    assert first['status']=='adopted'
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    finish(f,claim)
    assert phase('independent_review',candidate_sha256='5'*64,
                 comparison_sha256='6'*64)['intent_id']==first['intent_id']
    second=phase('independent_review',candidate_sha256='7'*64,comparison_sha256='8'*64)
    assert second['status']=='adopted' and second['intent_id']!=first['intent_id']
