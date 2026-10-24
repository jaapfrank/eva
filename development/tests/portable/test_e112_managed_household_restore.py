"""Existing host-prepare route restores a single inactive managed provision tree."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import pytest

from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.portable.test_portable_runtime_archive import runtime_archive
from tools import installation_host_extension_restore as host
from tools.installation_recovery_package import write_installation_package


@pytest.fixture
def managed_package(runtime_archive, tmp_path, request):
    args, _ = runtime_archive
    root = tmp_path / 'managed-package'; root.mkdir(mode=0o700)
    shutil.copytree(args['destination'], root / 'core')
    old, new = b'def propose(): return "old"\n', b'def propose(): return "new"\n'
    binding = {'tenant_id': 'synthetic-restoration', 'installation_id': 'home', 'household_id': 'family'}
    def review(raw):
        return canonical_json_bytes({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
                                     'source': {'sha256': hashlib.sha256(raw).hexdigest()}})
    old_review, new_review = review(old), review(new)
    old_config = {'contract': 'EvaHouseholdProcedureBinding/v1', 'enabled': True,
        'household_binding': binding, 'source_file': 'original.py', 'source_sha256': hashlib.sha256(old).hexdigest(),
        'reference_id': 'procedure-reference:'+'a'*64, 'source_principal_id': 'synthetic-owner',
        'review_sha256': hashlib.sha256(old_review).hexdigest()}
    pid = procedure_provision_id(old_config)
    current = {**old_config, 'contract': 'EvaHouseholdProcedureBinding/v2', 'provision_id': pid,
        'source_file': 'improved.py', 'source_sha256': hashlib.sha256(new).hexdigest(),
        'reference_id': 'procedure-reference:'+'b'*64, 'review_sha256': hashlib.sha256(new_review).hexdigest()}
    payload = {'managed-procedure/current.json': canonical_json_bytes(current),
        'managed-procedure/adoption-review.json': new_review,
        'managed-procedure/original.py': old, 'managed-procedure/improved.py': new,
        'managed-enrollment.json': canonical_json_bytes({'household_provision_enrollment': {
            'contract': 'EvaHouseholdProvisionEnrollment/v1', 'provision_id': pid,
            'owner_principal_id': 'synthetic-owner', 'binding_sha256': canonical_sha256(current)}})}
    for config, code, review_raw in ((old_config, old, old_review), (current, new, new_review)):
        evidence = {key: config[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}
        evidence['binding_sha256'] = canonical_sha256(config)
        version = PostgresHouseholdProvisions._version_id(evidence)
        payload['managed-procedure/versions/'+version+'.json'] = canonical_json_bytes({
            'contract': 'EvaHouseholdProvisionVersion/v1', 'configuration': config,
            'source': code.decode(), 'review': review_raw.decode(), 'origin': {'kind': 'retained_installed'}})
    options = getattr(request, 'param', {})
    if options.get('pointer_changes'):
        changed = json.loads(payload['managed-enrollment.json'])
        changed['household_provision_enrollment'].update(options['pointer_changes'])
        payload['managed-enrollment.json'] = canonical_json_bytes(changed)
    if options.get('unregistered'):
        payload = {'managed-procedure/current.json': canonical_json_bytes(old_config),
            'managed-procedure/adoption-review.json': old_review,
            'managed-procedure/original.py': old,
            'managed-enrollment.json': canonical_json_bytes(options.get('enrollment',
                {'household_provision_enrollment': None}))}
    roles, mapping = {}, {}
    for relative, raw in payload.items():
        leaf = relative.removeprefix('managed-procedure/')
        if relative in {'managed-enrollment.json', 'managed-procedure/current.json'}:
            archived, role = 'config/household-procedure/'+leaf, 'provider_binding'
        else:
            archived = 'documents/household-procedure/'+leaf
            role = 'document_artifact' if leaf.endswith('.py') else 'document_marker'
        target = root / archived; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(raw); target.chmod(0o600); roles[archived] = role; mapping[relative] = archived
    for relative in ('memory/hindsight.dump', 'gateway/contextforge.sqlite'):
        target = root / relative; target.parent.mkdir(mode=0o700, parents=True)
        target.write_bytes(b'Unrelated opaque synthetic fixture, not a live component.'); target.chmod(0o600)
    package = write_installation_package(root, file_roles=roles,
        source_components={key: [{'name': key+'-fixture', 'image_sha256': 'b'*64}]
                           for key in ('memory', 'gateway', 'documents')},
        key_references={'gateway_encryption': 'key:test', 'gateway_jwt': 'key:test', 'gateway_access': 'key:test'},
        identity_trust=args['identity_trust'])
    parent = tmp_path / 'managed-work'; parent.mkdir(mode=0o700)
    selected = {'destination': parent / 'household-provisions',
        'runtime_owner': {'uid': os.geteuid(), 'gid': os.getegid()}, 'files': mapping,
        'configuration': 'managed-procedure/current.json', 'review': 'managed-procedure/adoption-review.json',
        'enrollment': 'managed-enrollment.json'}
    configuration = {'retained_paths': sorted(mapping.values()), 'dependency_bundles': []}
    return root, package, configuration, selected, payload, args['identity_trust'], pid


def prepare(fixture, tmp_path, *, selected=None, guard=lambda: None):
    root, package, configuration, original, payload, trust, pid = fixture
    selected = original if selected is None else selected
    result = host.prepare_host_extension(root, tmp_path / 'prepared-host', configuration=configuration,
        verify_guard=guard, identity_trust=trust, managed_household=selected)
    return result


def test_existing_prepare_hook_restores_exact_same_managed_sources_and_versions(managed_package, tmp_path):
    root, package, configuration, selected, payload, trust, pid = managed_package
    result = prepare(managed_package, tmp_path)
    assert result['runtimeActivated'] is False and result['functionalHostRestoreProven'] is False
    assert result['managedHousehold'] == {'status': 'materialized_inactive', 'provision_id': pid,
        'runtime_owner': selected['runtime_owner'], 'files': len(payload), 'retained_versions': 2,
        'runtimeActivated': False, 'adoptionOrPauseChanged': False}
    for relative, expected in payload.items():
        path = selected['destination'] / relative
        assert path.read_bytes() == expected and stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in [selected['destination'], *[p for p in selected['destination'].rglob('*') if p.is_dir()]]:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    host.read_host_preparation(root, tmp_path / 'prepared-host', identity_trust=trust, managed_household=selected)


@pytest.mark.parametrize('fault', ['missing_source', 'missing_old_version', 'wrong_review', 'wrong_enrollment',
                                   'source_link', 'target_exists', 'target_link', 'unsafe_parent'])
def test_managed_restore_refuses_incomplete_or_changed_references_and_destination(managed_package, tmp_path, fault):
    root, package, configuration, selected, payload, trust, pid = managed_package
    if fault == 'missing_source': selected['files'].pop('managed-procedure/improved.py')
    elif fault == 'missing_old_version':
        path = next(name for name in selected['files'] if '/versions/' in name)
        selected['files'].pop(path)
    elif fault == 'wrong_review': selected['review'] = 'managed-procedure/original.py'
    elif fault == 'wrong_enrollment': selected['enrollment'] = selected['configuration']
    elif fault == 'source_link':
        path = root / selected['files']['managed-procedure/improved.py']
        raw = path.read_bytes(); path.unlink(); (tmp_path / 'outside.py').write_bytes(raw)
        path.symlink_to(tmp_path / 'outside.py')
    elif fault == 'target_exists': selected['destination'].mkdir(mode=0o700)
    elif fault == 'target_link': selected['destination'].symlink_to(selected['destination'].parent, target_is_directory=True)
    else: selected['destination'].parent.chmod(0o750)
    with pytest.raises((ValueError, FileExistsError)):
        prepare(managed_package, tmp_path)
    assert not (selected['destination'] / 'managed-procedure/current.json').exists()


def test_managed_restore_rechecks_source_after_blocking_guard(managed_package, tmp_path):
    root, package, configuration, selected, payload, trust, pid = managed_package
    def guard():
        if list(selected['destination'].parent.glob('.managed-household-*')):
            (root / selected['files']['managed-procedure/improved.py']).write_bytes(b'changed during guard')
    with pytest.raises(ValueError, match='retained source changed'):
        prepare(managed_package, tmp_path, guard=guard)
    assert not selected['destination'].exists()


def test_managed_readback_refuses_later_runtime_file_or_owner_drift(managed_package, tmp_path):
    root, package, configuration, selected, payload, trust, pid = managed_package
    prepare(managed_package, tmp_path)
    changed = selected['destination'] / selected['configuration']; changed.chmod(0o640)
    with pytest.raises(ValueError, match='runtime ownership'):
        host.read_host_preparation(root, tmp_path / 'prepared-host', identity_trust=trust, managed_household=selected)


def test_prepare_materializes_real_uid65532_tree_without_changing_package_owner(managed_package, tmp_path):
    if os.geteuid() != 0:
        pytest.skip('actual chown/setuid probe requires the isolated root operator')
    root, package, configuration, selected, payload, trust, pid = managed_package
    parent = Path(tempfile.mkdtemp(prefix='eva2-e112-private-managed-', dir='/tmp'))
    try:
        parent.chmod(0o700); os.chown(parent, 65532, 65532)
        selected = {**selected, 'destination': parent / 'household-provisions',
                    'runtime_owner': {'uid': 65532, 'gid': 65532}}
        prepare(managed_package, tmp_path, selected=selected)
        read, write = os.pipe(); child = os.fork()
        if child == 0:
            try:
                os.close(read); os.setgroups([]); os.setgid(65532); os.setuid(65532)
                for relative, expected in payload.items():
                    path = selected['destination'] / relative
                    assert path.read_bytes() == expected and path.stat().st_uid == 65532
                os.write(write, b'actual-uid65532-readback-ok'); os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write); evidence = os.read(read, 128); os.close(read)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0 and evidence == b'actual-uid65532-readback-ok'
        assert root.stat().st_uid == 0
    finally:
        shutil.rmtree(parent)


@pytest.mark.parametrize('fault',['target_bytes','parent_mode'])
def test_managed_restore_rechecks_runtime_tree_after_blocking_guard(managed_package,tmp_path,fault):
    root, package, configuration, selected, payload, trust, pid = managed_package
    def guard():
        staged=list(selected['destination'].parent.glob('.managed-household-*'))
        if staged:
            if fault=='target_bytes':(staged[0]/selected['configuration']).write_bytes(b'late changed target')
            else:selected['destination'].parent.chmod(0o750)
    with pytest.raises(ValueError,match='restored bytes|parent changed'):
        prepare(managed_package,tmp_path,guard=guard)
    assert not selected['destination'].exists()


@pytest.mark.parametrize('managed_package',[{'unregistered':True}],indirect=True)
def test_current_v1_explicit_null_enrollment_is_restored_without_registration(managed_package,tmp_path):
    root, package, configuration, selected, payload, trust, pid = managed_package
    result=prepare(managed_package,tmp_path)
    assert result['managedHousehold']['retained_versions']==0
    assert result['managedHousehold']['adoptionOrPauseChanged'] is False
    assert result['runtimeActivated'] is False
    assert json.loads((selected['destination']/selected['configuration']).read_text())['contract']=='EvaHouseholdProcedureBinding/v1'
    assert (selected['destination']/selected['enrollment']).read_bytes()==payload[selected['enrollment']]
    assert json.loads(payload[selected['enrollment']])=={'household_provision_enrollment':None}
    assert sorted(str(p.relative_to(selected['destination'])) for p in selected['destination'].rglob('*') if p.is_file())==sorted(payload)
    host.read_host_preparation(root,tmp_path/'prepared-host',identity_trust=trust,managed_household=selected)


@pytest.mark.parametrize('managed_package',[
    {'unregistered':True,'enrollment':{}},
    {'unregistered':True,'enrollment':{'household_provision_enrollment':False}},
    {'unregistered':True,'enrollment':{'household_provision_enrollment':[]}},
    {'unregistered':True,'enrollment':{'household_provision_enrollment':None,'unexpected':True}},
],indirect=True)
def test_null_enrollment_support_keeps_missing_keys_wrong_types_and_extra_fields_rejected(managed_package,tmp_path):
    with pytest.raises(ValueError,match='enrollment differs'):
        prepare(managed_package,tmp_path)
    assert not managed_package[3]['destination'].exists()


@pytest.mark.parametrize('managed_package',[
    {'pointer_changes':{'owner_principal_id':'different-owner'}},
    {'pointer_changes':{'binding_sha256':'0'*64}},
],indirect=True)
def test_null_enrollment_fix_does_not_allow_existing_pointer_owner_or_digest_changes(managed_package,tmp_path):
    with pytest.raises(ValueError,match='enrollment differs'):
        prepare(managed_package,tmp_path)
    assert not managed_package[3]['destination'].exists()
