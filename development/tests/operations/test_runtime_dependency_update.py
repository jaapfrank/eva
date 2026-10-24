import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_evaluation import EvaluationConfig,ComponentEvaluator
from eva.operations.runtime_dependency_update import compare_dependency_images,validate_dependency_release,MaintenanceStages
from eva.operations.runtime_repair_development import RuntimeRepairDevelopment
from eva.operations.reviewed_component_application import _artifact

def artifact(path,value):
    path.write_text(json.dumps(value));return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def setup_case(tmp_path):
    driver=tmp_path/'driver.py';driver.write_text('print(42)')
    fixture=tmp_path/'pg';fixture.mkdir()
    criteria=artifact(tmp_path/'criteria.json',dict(contract='EvaIndependentDependencyCriteria/v1',cases=[dict(id='original',driver=dict(path=str(driver),sha256=hashlib.sha256(driver.read_bytes()).hexdigest()),expected_baseline={'interrupted':False},expected_candidate={'interrupted':True},repeats=1)]))
    target=dict(target_id='a'*64,criteria=criteria)
    before=dict(services={'core':dict(image='baseline'),'delegated-work-worker':dict(image='baseline')})
    after=dict(services={'core':dict(image='candidate'),'delegated-work-worker':dict(image='candidate')})
    return target,dict(candidate_sha256='b'*64,before=before,candidate=after),fixture

def test_independent_data_is_not_mounted_and_both_roles_measured(tmp_path,monkeypatch):
    target,build,fixture=setup_case(tmp_path);mounts=[]
    def run(self,image,command,**kw):
        mounts.extend(str(p) for p,_ in kw['extra_mounts'])
        return dict(exit_code=0,stdout=json.dumps(dict(interrupted=image=='candidate')),timed_out=False,stdout_truncated=False,stderr_truncated=False,elapsed_ms=1250)
    monkeypatch.setattr(ComponentEvaluator,'_container',run)
    report=compare_dependency_images(target,build,EvaluationConfig(image='baseline',command=('python',)),fixture_socket=str(fixture))
    assert report['state']=='awaiting_independent_review' and len(report['cases'])==4
    assert target['criteria']['path'] not in mounts
    assert report['applicable_gain_cases']==['original']
    assert report['cases'][0]['elapsed_ms']==1250 and report['cases'][0]['elapsed_seconds_decimal']=='1.250'
    # Exercise the actual durable/canonical consumer, not just the Python dict.
    from eva.operations.runtime_repair_development import _put
    ref = _put(tmp_path/'actual-report.json', report)
    assert ref['sha256'] == canonical_sha256(report)
    assert json.loads(Path(ref['path']).read_text()) == report

def test_candidate_regression_keeps_baseline(tmp_path,monkeypatch):
    target,build,fixture=setup_case(tmp_path)
    monkeypatch.setattr(ComponentEvaluator,'_container',lambda *a,**kw:dict(exit_code=1,stdout='',timed_out=False,stdout_truncated=False,stderr_truncated=False))
    report=compare_dependency_images(target,build,EvaluationConfig(image='baseline',command=('python',)),fixture_socket=str(fixture))
    assert report['state']=='baseline_retained' and report['all_conditions_passed'] is False

def test_original_driver_hash_is_rechecked_before_execution(tmp_path,monkeypatch):
    target,build,fixture=setup_case(tmp_path);(tmp_path/'driver.py').write_text('modified')
    monkeypatch.setattr(ComponentEvaluator,'_container',lambda *a,**kw:pytest.fail('must not execute changed independent driver'))
    with pytest.raises(ValueError,match='driver bytes changed'):compare_dependency_images(target,build,EvaluationConfig(image='baseline',command=('python',)),fixture_socket=str(fixture))

def test_typed_maintenance_reuses_existing_controller_and_persists(tmp_path):
    root=tmp_path/'host';runtime=SimpleNamespace();publisher=SimpleNamespace()
    controller=RuntimeRepairDevelopment(root,runtime=runtime,targets=None,evaluator=None,artifacts=None,publisher=publisher,applications=None,scope_sha256='a'*64,start=False)
    identity='b'*64;(root/identity).mkdir()
    job=dict(contract='EvaDependencyMaintenanceHandoff/v1',target_id=identity,scope_sha256='a'*64,state='waiting_for_builder')
    controller.jobs[identity]=job;called=[]
    def advance(value):called.append(value['target_id']);value['state']='completed';return 'completed'
    controller.maintenance=SimpleNamespace(observe_due=lambda:None,advance=advance)
    try:
        assert controller.run_once()=='completed'
        assert called==[identity] and json.loads((root/identity/'handoff.json').read_text())['state']=='completed'
        assert controller.thread is None
    finally:controller.close()

def test_repair_jobs_not_starved_by_unavailable_update_source(tmp_path):
    root=tmp_path/'host';controller=RuntimeRepairDevelopment(root,runtime=None,targets=None,evaluator=None,artifacts=None,publisher=None,applications=None,scope_sha256='a'*64,start=False)
    def unavailable():raise OSError('offline')
    controller.maintenance=SimpleNamespace(observe_due=unavailable)
    try:assert controller.run_once()=='idle' and controller.maintenance.last_observation_error is True
    finally:controller.close()

def test_unconfirmed_build_never_restarts_build(tmp_path):
    target=artifact(tmp_path/'target.json',dict(target_id='a'*64))
    stages=MaintenanceStages(None,runtime=None,artifacts=None,applications=None,publisher=None,settings=None)
    stages.controller=SimpleNamespace(root=tmp_path);stages.call=lambda *a,**kw:None
    job=dict(target_id='a'*64,target=target,state='build_dispatch_unconfirmed')
    assert stages.advance(job)=='blocked' and 'without_repeating' in job['reason']

def test_source_only_release_cannot_be_substituted_for_dependency_proof():
    with pytest.raises(ValueError,match='exact dependency'):validate_dependency_release('a'*64,dict(kind='runtime_repair'))


def test_fixture_show_version_string_and_invalid_values(tmp_path):
    from eva.operations.runtime_dependency_update import _fixture_mounts
    socket=tmp_path/'pg';socket.mkdir()
    protocol={'metadataPathInContainer':'/run/eva-dependency-review/fixture.json','socketMountInContainer':'/run/eva-dependency-pg','network':'none','testOnly':True,'productionCredentialsOrData':False}
    value=dict(contract='EvaIndependentPgFixture/v1',testOnly=True,socketDirectory='/run/eva-dependency-pg',port=5432,database='eva2_4b_disposable_e419_unit',user='test_user',systemIdentifier='12345',serverVersionNum='180006')
    meta=tmp_path/'fixture.json';meta.write_text(json.dumps(value))
    mounts=_fixture_mounts({'fixture':protocol},socket)
    assert mounts[0]==(meta,'/run/eva-dependency-review/fixture.json')
    meta.write_text(json.dumps({**value,'serverVersionNum':'180006.0'}))
    with pytest.raises(ValueError,match='server version'):_fixture_mounts({'fixture':protocol},socket)



def test_both_measured_report_producers_reach_actual_canonical_store(tmp_path, monkeypatch):
    target, build, fixture = setup_case(tmp_path)
    inputs = tmp_path/'inputs'; inputs.mkdir()
    item = inputs/'source.json'; item.write_text('{"retained":true}')
    manifest = artifact(tmp_path/'inputs-manifest.json', {
        'evaPackageOverlaid':False, 'runtimeModulesReplaced':False,
        'files':[{'path':'source.json', 'sha256':hashlib.sha256(item.read_bytes()).hexdigest()}]})
    driver = tmp_path/'driver.py'
    cases = [dict(id=name, path=str(driver), sha256=hashlib.sha256(driver.read_bytes()).hexdigest(),
        destination='/review/driver.py', command=['/usr/local/bin/python','-I','/review/driver.py'],
        input_manifest=manifest, mount=dict(path=str(inputs),readonly=True,destination='/review/inputs'),
        deadline=60, repeats=1, same_expected_baseline_candidate=True, expected_JSON={'ok':True})
        for name in ('normal_used_path','restore')]
    target['interfaces'] = artifact(tmp_path/'interfaces.json', dict(
        contract='EvaIndependentDependencyInterfaces/v1', criteria=target['criteria'],
        expectations_host_only=True, network='none', cases=cases))
    def run(self, image, command, **kwargs):
        actual = {'ok':True} if command[-1]=='/review/driver.py' else {'interrupted':image=='candidate'}
        return dict(exit_code=0, stdout=json.dumps(actual), timed_out=False,
            stdout_truncated=False, stderr_truncated=False, elapsed_ms=31506)
    monkeypatch.setattr(ComponentEvaluator, '_container', run)
    report = compare_dependency_images(target, build,
        EvaluationConfig(image='baseline',command=('python',)),fixture_socket=str(fixture))
    assert report['all_conditions_passed'] and report['interfaces']['all_conditions_passed']
    assert len(report['cases'])==4 and len(report['interfaces']['cases'])==8
    for row in report['cases']+report['interfaces']['cases']:
        assert row['elapsed_ms']==31506 and row['elapsed_seconds_decimal']=='31.506'
    from eva.operations.runtime_repair_development import _put
    saved = _put(tmp_path/'both-producers-report.json',report)
    assert saved['sha256']==canonical_sha256(report)
    assert json.loads(Path(saved['path']).read_text())==report



def _closed_recovery_case(tmp_path):
    """Synthetic unit data only; never represents an executed independent proof."""
    roles=('core','delegated-work-worker')
    before={r:'sha256:'+str(i+1)*64 for i,r in enumerate(roles)}
    after={r:'sha256:'+str(i+3)*64 for i,r in enumerate(roles)}
    images=dict(contract='EvaRetainedRuntimeImages/v1', before=before, after=after,
        artifacts={image:dict(path='/not-executed/'+image[7:],sha256='9'*64,size=10)
                   for image in set(before.values())|set(after.values())})
    target=dict(origin={'original_work_id':'unit-not-real'},criteria={'sha256':'a'*64},
        interfaces={'sha256':'b'*64});target['target_id']=canonical_sha256(target)
    build=dict(target_id=target['target_id'],before={'services':{r:{'image':i} for r,i in before.items()}},
        candidate={'services':{r:{'image':i} for r,i in after.items()}})
    build['candidate_sha256']=canonical_sha256(build)
    comparison=dict(target_id=target['target_id'],candidate_sha256=build['candidate_sha256'],
        all_conditions_passed=True,interfaces={'unit_fixture':True})
    review=dict(target_id=target['target_id'],candidate_sha256=build['candidate_sha256'],
        decision='accept',comparison_sha256=canonical_sha256(comparison))
    source=tmp_path/'frozen-source';source.mkdir();helper=source/'helper.py';helper.write_text('# unit source')
    candidate={'host':{'pythonPath':str(source),'sourceFiles':[{
        'path':str(helper),'sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}]}}
    data=dict(target=target,build=build,comparison=comparison,review=review,images=images,helper_candidate=candidate)
    inputs={'contract':'EvaIndependentImageRecoveryInput/v1',
        **{k:artifact(tmp_path/(k+'.json'),v) for k,v in data.items()}}
    harness=tmp_path/'unit-harness.py';harness.write_text('# intercepted; no Docker or subprocess executed')
    harness_ref={'path':str(harness),'sha256':hashlib.sha256(harness.read_bytes()).hexdigest()}
    interface=artifact(tmp_path/'interface.json',dict(contract='EvaIndependentHeldImageRecoveryInterface/v1',
        inputContract=inputs['contract'],requiredInputKeys=list(inputs),
        receiptContract='EvaIndependentHeldImageRecoveryReceipt/v1',sameClosedCriteria=True,
        changesToCriteria068d4f=False,harness=harness_ref))
    receipt=dict(contract='EvaIndependentHeldImageRecoveryReceipt/v1',status='passed',
        harness_sha256=harness_ref['sha256'],origin_sha256=canonical_sha256(target['origin']),
        target_id=target['target_id'],candidate_sha256=build['candidate_sha256'],
        criteria_sha256='a'*64,interfaces_sha256='b'*64,comparison_sha256=canonical_sha256(comparison),
        review_sha256=inputs['review']['sha256'],archives_sha256=inputs['images']['sha256'],
        helper_candidate_sha256=inputs['helper_candidate']['sha256'],before_images=before,candidate_images=after,
        data_restore_proof_sha256=canonical_sha256(comparison['interfaces']),models=0,production_writes=0,
        own_compose_stopped=True,own_daemon_stopped=True,elapsed_ms=31506,
        held_load=dict(contract='EvaHeldRuntimeImagesRestored/v1',images=list(images['artifacts']),
            activated=False,services_started=False),
        isolated_store=dict(initially_empty=True,tcp_listener=False,network_configuration_changes=False,
            private_endpoint='unix:///unit-private/docker.sock',private_data_root='/unit-private/store'),
        recovery=dict(status='baseline_recovered_with_retained_mounts',single_compensation=True,
            production_rollback=False,candidate_failure_injected_only_in_fixture=True,
            baseline_expected_version='3.3.4',baseline_outcome_does_not_require_candidate_fix=True),
        phases=[dict(phase=k,status='completed') for k in ('held_exact_image_import',
            'single_isolated_update_controlled_failure','single_isolated_baseline_recovery')],
        actual_import_checks={leg+':'+r:dict(pure=v,binary=v,implementation='binary',libpq=180006)
            for leg,v in [('baseline','3.3.4'),('candidate','3.3.6')] for r in roles})
    return inputs,interface,receipt


def test_same_job_closed_recovery_receipt_and_original_output_ref(tmp_path):
    from subprocess import CompletedProcess
    from eva.operations.runtime_dependency_update import run_dependency_image_recovery
    inputs,interface,receipt=_closed_recovery_case(tmp_path);calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        assert len(argv)==4 and argv[0] and 'unit-harness.py' in argv[1]
        assert json.loads(Path(argv[2]).read_text())==inputs
        Path(argv[3]).write_text(json.dumps(receipt))
        return CompletedProcess(argv,0,'{}','')
    folder=tmp_path/'private-run'
    ref=run_dependency_image_recovery(inputs,interface,folder,guard=lambda:True,run=run)
    assert ref=={'path':str(folder/'output.private.json'),
        'sha256':hashlib.sha256((folder/'output.private.json').read_bytes()).hexdigest()}
    assert run_dependency_image_recovery(inputs,interface,folder,guard=lambda:True,run=run)==ref
    assert len(calls)==1  # Complete original proof re-read; no second dispatch.


@pytest.mark.parametrize('fault',['cleanup','target','source','imports','phase','production'])
def test_incomplete_or_other_job_recovery_never_closes(tmp_path,fault):
    from eva.operations.runtime_dependency_update import validate_dependency_image_recovery
    inputs,interface,receipt=_closed_recovery_case(tmp_path)
    if fault=='cleanup':receipt['own_daemon_stopped']=False
    elif fault=='target':receipt['target_id']='f'*64
    elif fault=='source':receipt['archives_sha256']='f'*64
    elif fault=='imports':receipt['actual_import_checks'].pop('baseline:core')
    elif fault=='phase':receipt['phases'][0]['status']='dispatch_result_unknown'
    else:receipt['production_writes']=1
    with pytest.raises(ValueError):validate_dependency_image_recovery(inputs,interface,receipt)


def test_unknown_recovery_dispatch_is_not_repeated_and_revocation_prevents_start(tmp_path):
    from subprocess import CompletedProcess
    from eva.operations.runtime_dependency_update import run_dependency_image_recovery
    inputs,interface,receipt=_closed_recovery_case(tmp_path);calls=[]
    def fail(argv,**kwargs):calls.append(argv);return CompletedProcess(argv,1,'','unit-failed')
    folder=tmp_path/'unknown'
    with pytest.raises(ValueError,match='authority denied'):
        run_dependency_image_recovery(inputs,interface,folder,guard=lambda:False,run=fail)
    assert calls==[] and not (folder/'dispatch.private.json').exists()
    with pytest.raises(RuntimeError,match='incomplete'):
        run_dependency_image_recovery(inputs,interface,folder,guard=lambda:True,run=fail)
    with pytest.raises(RuntimeError,match='unknown'):
        run_dependency_image_recovery(inputs,interface,folder,guard=lambda:True,run=fail)
    assert len(calls)==1
