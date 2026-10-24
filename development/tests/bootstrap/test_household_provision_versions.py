"""Synthetic review/source ports, actual scoped PG and managed source files."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.postgres.conftest import postgres_engine, postgres_gate
from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.bootstrap.household_provision_versions import HouseholdProvisionVersions
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


@pytest.fixture
def version_host(postgres_engine, tmp_path, monkeypatch, request):
    tenant = 'version-host-'+tmp_path.name
    binding = {'tenant_id': tenant, 'installation_id': 'home', 'household_id': 'family'}
    if getattr(request, 'param', {}).get('full_household'):
        from eva.application.household_initiative import HouseholdInitiativeBinding
        binding = HouseholdInitiativeBinding(tenant, 'home', 'family', 'household-device:'+'a'*32,
            'assist_satellite.fixture', 'a'*32, 'household-consent:fixture', 'b'*64).to_payload()
    scope = external_timeline_scope(tenant, 'home', 'fixture')
    timeline = PostgresOperationalTimeline(postgres_engine, scope)
    controls = {'allowed': True, 'verified': 'e'*64, 'effect': 'clear'}
    def authorize(connection, now):
        timeline._scope(connection)
        if not controls['allowed']: raise PermissionError('owner revoked')
    old, new = 'def propose(): return "old"\n', 'def propose(): return "new"\n'
    old_sha, new_sha = (hashlib.sha256(value.encode()).hexdigest() for value in (old, new))
    original_id = 'procedure-reference:'+'a'*64
    private_scope = {'tenant_id': tenant, 'installation_id': 'home', 'principal_id': 'owner', 'project_id': None}
    receipt_sha = 'd'*64
    target_id = 'procedure-reference:'+canonical_sha256({'scope': private_scope, 'receipt': receipt_sha})
    source_target = {'kind': 'saved_artifact', 'procedure_reference_id': original_id,
        'scope': private_scope, 'artifact_path': '/workspace/provision.py', 'content_sha256': old_sha,
        'receipt_sha256': 'c'*64, 'source_call_id': 'saved-original', 'source_message_id': 'original-message',
        'tool_configuration_sha256': 'f'*64, 'retention_until': '2099-01-01T00:00:00Z'}
    review_id = '1'*64
    destination = '/workspace/provision.improved-'+new_sha+'.py'
    publication = {'contract': 'EvaSavedArtifactPublication/v1', 'review_id': review_id,
        'source_target': source_target, 'baseline_sha256': old_sha, 'candidate_sha256': new_sha,
        'destination_path': destination, 'improvement_description': 'Betere bestaande voorziening'}
    reference = {'document_id': target_id, 'path': destination, 'content_sha256': new_sha,
        'receipt_sha256': receipt_sha, 'retention_until': source_target['retention_until']}
    receipt = {'contract': 'EvaSavedArtifactApplication/v1', 'status': 'saved_and_designated',
        'task_id': 'work:fixture', 'publication': publication, 'reference': reference, 'old_source_unchanged': True}
    status = {'review_id': review_id, 'installed': True, 'independent_final': True,
        'assessment': {'decision': 'accept', 'semantic_improvement_verified': True,
                       'baseline_sha256': old_sha, 'candidate_sha256': new_sha},
        'application': {'review_id': review_id, 'state': 'completed', 'installed': True,
                        'task_id': 'work:fixture', 'saved_artifact': receipt}}
    path = tmp_path/'current.json'; review_path = tmp_path/'adoption-review.json'
    (tmp_path/'original.py').write_text(old)
    review_path.write_text(json.dumps({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
                                     'source': {'sha256': old_sha}}))
    config = {'contract': 'EvaHouseholdProcedureBinding/v1', 'enabled': True,
        'household_binding': binding, 'source_file': 'original.py', 'source_sha256': old_sha,
        'reference_id': original_id, 'source_principal_id': 'owner',
        'review_sha256': hashlib.sha256(review_path.read_bytes()).hexdigest()}
    path.write_text(json.dumps(config)); pid=procedure_provision_id(config)
    pointer = {'contract': 'EvaHouseholdProvisionEnrollment/v1', 'provision_id': pid,
               'owner_principal_id': 'owner', 'binding_sha256': canonical_sha256(config)}
    enrollment_path = tmp_path/'enrollment.json'
    enrollment_path.write_text(json.dumps({'household_provision_enrollment': pointer}))
    enrollment_path.chmod(0o600)
    def current(): return json.loads(path.read_text())
    def verify_current(connection, evidence):
        actual=current()
        if (canonical_sha256(actual) != evidence['binding_sha256']
                or hashlib.sha256((tmp_path/actual['source_file']).read_bytes()).hexdigest() != evidence['source_sha256']
                or hashlib.sha256(review_path.read_bytes()).hexdigest() != evidence['review_sha256']):
            raise ValueError('actual installed source differs')
    def callbacks(*args):
        return {'authorize': authorize, 'verify_current': verify_current,
                'verify_realization': lambda conn, evidence: controls['verified'],
                'effect_status': lambda conn, evidence: controls['effect']}
    owner = SimpleNamespace(_owner='owner', _scope=scope, _authorize=authorize)
    controller = SimpleNamespace(engine=postgres_engine, procedure_path=path, review_path=review_path,
        enrollment_path=enrollment_path, owner_loader=lambda: owner, _source=current,
        _load_enrollment=lambda: json.loads(enrollment_path.read_text())['household_provision_enrollment'],
        _authorize_household=lambda bound: None if bound==binding else (_ for _ in ()).throw(ValueError('wrong household')),
        _callbacks=callbacks)
    calls=[]
    evaluator=SimpleNamespace(final_assessment_scopes=(('saved-original.py',canonical_sha256({
        k: private_scope[k] for k in ('tenant_id','installation_id','principal_id')})),),reviewed_releases=(),
        application=lambda *args,**kwargs: calls.append(kwargs) or deepcopy(status))
    host=HouseholdProvisionVersions(controller,evaluator=evaluator)
    sources={original_id: {'path':'saved-original.py','baseline_content':old,'source_target':source_target},
        target_id: {'path':'saved-new.py','baseline_content':new,'source_target':{
            **source_target,'procedure_reference_id':target_id,'artifact_path':destination,
            'content_sha256':new_sha,'receipt_sha256':receipt_sha}}}
    monkeypatch.setattr(host,'_source',lambda ref,config: deepcopy(sources[ref]))
    store=host._store(config)
    store.register(pid,host._evidence(config),request_id='register',now=datetime.now(UTC).replace(microsecond=0))
    adopted=store.command(pid,'adopt',expected_revision=1,binding_sha256=pointer['binding_sha256'],
        request_id='adopt',now=datetime.now(UTC).replace(microsecond=0))
    return SimpleNamespace(host=host,controller=controller,config=config,pid=pid,pointer=pointer,
        status=status,sources=sources,old=old,new=new,target_id=target_id,review_id=review_id,controls=controls,
        adopted=adopted,calls=calls,path=path,review_path=review_path,enrollment_path=enrollment_path)


def stage(f):
    return f.host.stage(f.pid,f.review_id,f.target_id,f.adopted['revision'],
                        f.pointer['binding_sha256'],'stage-reviewed-source')


def test_reviewed_source_applies_and_undo_restores_actual_bytes_and_same_identity(version_host):
    f=version_host
    result=stage(f)
    assert f.path.read_text().startswith('{') and (f.path.parent/'original.py').read_text()==f.old
    changed=f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
                          f.pointer['binding_sha256'],'change-reviewed-source')
    current=json.loads(f.path.read_text())
    assert current['contract']=='EvaHouseholdProcedureBinding/v2' and current['provision_id']==f.pid
    assert (f.path.parent/current['source_file']).read_text()==f.new
    assert changed['receipt']['provision_id']==f.pid and changed['receipt']['status']=='active'
    assert f.host.pending_overview() is None
    restored=f.host.change(f.pid,None,changed['receipt']['revision'],
        changed['enrollment']['binding_sha256'],'undo-reviewed-source',undo=True)
    assert json.loads(f.path.read_text())==f.config
    assert restored['enrollment']==f.pointer and restored['receipt']['status']=='active'
    assert len(f.host.history(f.pid)['entries'])>=6
    assert all(call['action']=='status' if 'action' in call else True for call in f.calls)


@pytest.mark.parametrize('fault',['not_accepted','wrong_owner','changed_code','old_proof_only'])
def test_version_host_refuses_unreviewed_or_changed_publication(version_host,fault):
    f=version_host
    if fault=='not_accepted': f.status['assessment']['decision']='reject'
    elif fault=='wrong_owner': f.status['application']['saved_artifact']['publication']['source_target']['scope']['principal_id']='another'
    elif fault=='changed_code': f.sources[f.target_id]['baseline_content']='changed after save'
    else: f.status.pop('assessment')
    with pytest.raises(ValueError): stage(f)
    assert json.loads(f.path.read_text())==f.config and f.controller._load_enrollment()==f.pointer


@pytest.mark.parametrize('stop_at', ['config', 'pointer'])
def test_pending_partial_source_handoff_stays_visible_and_can_finish(version_host,monkeypatch,stop_at):
    import eva.bootstrap.household_provision_versions as module
    f=version_host; result=stage(f); write=module._write
    def interrupted(path,raw,**kwargs):
        target = f.path if stop_at=='config' else f.enrollment_path
        if Path(path)==target: raise OSError('synthetic config handoff interruption')
        return write(path,raw,**kwargs)
    monkeypatch.setattr(module,'_write',interrupted)
    with pytest.raises(OSError,match='handoff interruption'):
        f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
                      f.pointer['binding_sha256'],'first-change')
    overview=f.host.pending_overview()
    from eva.bootstrap.household_provision_runtime import HouseholdProvisionRuntime
    f.controller._version_controls=lambda: f.host
    assert HouseholdProvisionRuntime.overview(f.controller)['change_pending']==overview['change_pending']
    assert overview['change_pending']['needs_completion'] and overview['actual_working_proof_available'] is False
    assert overview['provision']['status']=='paused'
    monkeypatch.setattr(module,'_write',write)
    pending=overview['change_pending']
    done=f.host.change(f.pid,result['selected_version_id'],pending['expected_revision'],
                      pending['binding_sha256'],'explicit-finish')
    assert done['receipt']['pending_change'] is None
    assert (f.path.parent/json.loads(f.path.read_text())['source_file']).read_text()==f.new


def test_pending_source_drift_outside_retained_endpoints_is_not_overwritten(version_host,monkeypatch):
    import eva.bootstrap.household_provision_versions as module
    f=version_host; result=stage(f); write=module._write
    def interrupted(path,raw,**kwargs):
        if Path(path)==f.path: raise OSError('synthetic interruption')
        return write(path,raw,**kwargs)
    monkeypatch.setattr(module,'_write',interrupted)
    with pytest.raises(OSError):
        f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
                      f.pointer['binding_sha256'],'interrupted-change')
    f.review_path.write_text('unrelated outside change')
    with pytest.raises(ValueError,match='two retained endpoints'):
        f.host.pending_overview()
    assert f.review_path.read_text()=='unrelated outside change'


def test_owner_revoke_after_review_read_cannot_stage_source(version_host,monkeypatch):
    f=version_host; original=f.host._reviewed
    def revoke(*args):
        result=original(*args); f.controls['allowed']=False; return result
    monkeypatch.setattr(f.host,'_reviewed',revoke)
    with pytest.raises(PermissionError): stage(f)
    assert json.loads(f.path.read_text())==f.config and f.controller._load_enrollment()==f.pointer


def test_server_known_reviewed_choices_are_named_and_no_manual_proof_is_needed(version_host):
    f=version_host; owner=f.controller.owner_loader()
    timeline=PostgresOperationalTimeline(f.controller.engine,owner._scope)
    package={'source_target':deepcopy(f.sources[f.config['reference_id']]['source_target']),
        'baseline_sha256':f.config['source_sha256'],'candidate_package_sha256':f.review_id,
        'expected_gain':'Een duidelijkere bestaande voorziening'}
    timeline.create_thread(thread_id='component-draft:own-reviewed-choice',checkpoint_id='component-choice:own',
        phase='recorded',status='completed',event_type='COMPONENT_ACTION_RECORDED',
        occurred_at=datetime.now(UTC).replace(microsecond=0),
        data={'name':'prepare_own_component_release','output':{'status':'verified','operation_result':{
            'package':package,'final_assessment_handoff':{'review_id':f.review_id}}}})
    other=deepcopy(package);other['source_target']['scope']['principal_id']='another-owner'
    timeline.create_thread(thread_id='component-draft:other-reviewed-choice',checkpoint_id='component-choice:other',
        phase='recorded',status='completed',event_type='COMPONENT_ACTION_RECORDED',
        occurred_at=datetime.now(UTC).replace(microsecond=0),
        data={'name':'prepare_own_component_release','output':{'status':'verified','operation_result':{
            'package':other,'final_assessment_handoff':{'review_id':'2'*64}}}})
    choices=f.host.choices()['choices']
    assert len(choices)==1 and choices[0]['label']=='Een duidelijkere bestaande voorziening'
    assert choices[0]['state']=='reviewed_saved'
    result=f.host.stage_choice(f.pid,choices[0]['choice_id'],f.adopted['revision'],
                              f.pointer['binding_sha256'],'stage-by-name')
    assert result['receipt']['status']=='active' and result['selected_version_id']
    assert all(call['review_id']==f.review_id for call in f.calls)


def test_pending_stop_through_existing_owner_controller_survives_file_completion(version_host,monkeypatch):
    from eva.bootstrap.household_provision_runtime import HouseholdProvisionRuntime
    import eva.bootstrap.household_provision_versions as module
    f=version_host; result=stage(f); write=module._write
    def interrupted(path,raw,**kwargs):
        if Path(path)==f.enrollment_path: raise OSError('synthetic pointer interruption')
        return write(path,raw,**kwargs)
    monkeypatch.setattr(module,'_write',interrupted)
    with pytest.raises(OSError):
        f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
                      f.pointer['binding_sha256'],'interrupted-for-stop')
    f.controller._version_controls=lambda: f.host
    overview=HouseholdProvisionRuntime.overview(f.controller)
    pending=overview['change_pending']
    stopped=HouseholdProvisionRuntime.command(f.controller,f.pid,'stop',pending['expected_revision'],
        pending['binding_sha256'],'owner-stop-pending')
    assert stopped['stopped'] is True
    monkeypatch.setattr(module,'_write',write)
    done=f.host.change(f.pid,result['selected_version_id'],stopped['revision'],
                      pending['binding_sha256'],'finish-stopped-version')
    assert done['receipt']['status']=='paused' and done['receipt']['stopped'] is True


def test_post_target_verification_review_drift_is_preserved_before_any_destructive_write(version_host,monkeypatch):
    f=version_host;result=stage(f);verify=f.host._verify_target;calls=[]
    unrelated=b'Unrelated review bytes written during the blocking target read.'
    def changed(connection,evidence):
        answer=verify(connection,evidence);calls.append(1)
        if len(calls)==2:f.review_path.write_bytes(unrelated)
        return answer
    monkeypatch.setattr(f.host,'_verify_target',changed)
    with pytest.raises(ValueError,match='two retained endpoints'):
        f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
                      f.pointer['binding_sha256'],'review-drift-during-target-validation')
    assert len(calls)==2 and f.review_path.read_bytes()==unrelated
    assert json.loads(f.path.read_text())==f.config and f.controller._load_enrollment()==f.pointer
    state=f.host._store(f.config).inspect(f.pid,now=datetime.now(UTC).replace(microsecond=0))
    assert state['status']=='paused' and state['pending_change'] is not None
