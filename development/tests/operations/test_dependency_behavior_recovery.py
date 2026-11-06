"""Behavior verdicts at the real dependency-verifier/recovery boundary.

All Docker/evaluator effects are isolated fakes; no installation or household data.
"""
import json
import pytest

from eva.operations import runtime_dependency_update as dependency
from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
from eva.operations.runtime_image_update import recover_runtime, update_runtime
from tests.operations.test_runtime_dependency_update import artifact
from tests.operations.test_runtime_image_recovery import Installation, probe


def verifier(tmp_path, monkeypatch, *, fault='output'):
    install = Installation(tmp_path)
    driver = tmp_path/'driver.py'
    driver.write_text('print("isolated fixture")')
    import hashlib
    criteria = artifact(tmp_path/'criteria.json', {'cases':[{
        'id':'actual-behavior', 'driver':{'path':str(driver),
            'sha256':hashlib.sha256(driver.read_bytes()).hexdigest()},
        'expected_candidate':{'ok':True}, 'expected_baseline':{'ok':True}}]})
    inventory = {'fixture_identity':'same exact source and dependencies'}
    origin = {'scope_sha256':'a'*64, 'original_work_id':'original-work',
              'original_spec_sha256':'b'*64}
    target = {'origin':origin, 'criteria':criteria,
              'observation':{'inventory':{r:{'image':install.before['services'][r]['image'],
                  **inventory} for r in dependency.ROLES}}}
    release = {'maintenance_target':target, 'dependency_build':{
        'before':install.before, 'candidate':install.after,
        'roles':{r:{'inventory':inventory} for r in dependency.ROLES}},
        'behavior_verifier':{'sha256':'c'*64}, 'fixture_socket':str(tmp_path)}
    def run(args):
        if args[:2] == ['docker','inspect']:
            role = args[-1]
            return json.dumps([{'Id':role, **install.live[role], 'State':{'Running':True}}])
        assert args[:2] == ['docker','exec']
        role = args[2]
        if args[-1] == dependency._INVENTORY:
            return json.dumps({'changed':True} if fault=='identity' else inventory)
        version = dependency.VERSION if install.live[role]['Image']==install.after['services'][role]['image'] else '3.3.4'
        if 'from psycopg import pq' in args[-1]:
            return json.dumps({'version':version,'implementation':'binary','libpq':180000})
        return json.dumps({'original_work_id':origin['original_work_id'],
            'spec_sha256':origin['original_spec_sha256'], 'loaded_driver':version,
            'driver_implementation':'binary','private_content_returned':False,'effects_replayed':False})
    def isolated(self, image, command, **kwargs):
        candidate = image in {r['image'] for r in install.after['services'].values()}
        return {'exit_code':0,'stdout':('not-json' if fault=='invalid_json' else json.dumps({'ok':not(candidate and fault=='output')})),
            'timed_out':fault=='timeout','stdout_truncated':False,'stderr_truncated':False}
    monkeypatch.setattr(ComponentEvaluator, '_container', isolated)
    monkeypatch.setattr(dependency, 'compare_interface_images', lambda *a, **kw:
        {'all_conditions_passed':not(fault=='interfaces' and kw['candidate_only']=='candidate')})
    def verify(observation):
        return dependency.installed_dependency_behavior(observation, release=release,
            settings=EvaluationConfig(image="fixture",command=("python",),deadline_seconds=30),run=run)
    return install, verify


@pytest.mark.parametrize('fault', ['output','interfaces'])
def test_proven_behavior_regression_drives_one_recovery_and_preserves_data(tmp_path, monkeypatch, fault):
    install, verify = verifier(tmp_path, monkeypatch, fault=fault)
    update_runtime(**install.arguments(), verify_behavior=probe)
    install.data.append('result saved after update')
    result = recover_runtime(**install.arguments(), verify_behavior=verify)
    assert result['status']=='baseline_recovered_with_retained_mounts'
    failed = result['behavior_verification']['evidence']
    assert failed['verified'] is False
    assert failed['failure_code'] in {'installed_driver_output_mismatch','installed_dependency_interface_regression'}
    assert result['recovery']['behavior_verification']['verified'] is True
    assert len(install.dispatches())==2
    assert install.data[-1]=='result saved after update'
    recover_runtime(**install.arguments(), verify_behavior=verify)
    assert len(install.dispatches())==2


@pytest.mark.parametrize('fault', ['timeout','invalid_json','identity'])
def test_unknown_or_untrusted_verification_never_authorizes_rollback(tmp_path, monkeypatch, fault):
    install, verify = verifier(tmp_path, monkeypatch, fault=fault)
    update_runtime(**install.arguments(), verify_behavior=probe)
    with pytest.raises((ValueError, RuntimeError)):
        recover_runtime(**install.arguments(), verify_behavior=verify)
    assert len(install.dispatches())==1
    assert 'recovery' not in json.loads(install.receipt.read_text())
