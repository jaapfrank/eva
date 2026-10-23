"""Local operator authorization is exact binding evidence, not remote readiness."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import pytest
from tools import installation_runtime_authorization as module


def config(tmp_path):
    return {'model_authority_file': str(tmp_path/'owner-auth'), 'writers': [{
        'id': 'a'*64, 'profile_sha256': 'b'*64, 'model_lease_path': '/run/model.json',
        'credential_files': [{'path': '/run/ha-token', 'sha256': hashlib.sha256(b'current-token').hexdigest()}]}]}


def test_configuration_requires_complete_explicit_shapes(tmp_path):
    value = config(tmp_path); module.validate_runtime_authorization_configuration(value)
    for change in ({**value, 'approved': True}, {**value, 'writers': []},
                   {**value, 'writers': value['writers']*2}):
        with pytest.raises(ValueError): module.validate_runtime_authorization_configuration(change)
    changed = deepcopy(value); changed['writers'][0]['credential_files'][0]['path'] = '/run/../secret'
    with pytest.raises(ValueError): module.validate_runtime_authorization_configuration(changed)


@pytest.mark.parametrize('role', ['core', 'worker'])
@pytest.mark.parametrize('fault', [None, 'profile', 'lease', 'credential', 'missing_route', 'active_binding', 'late_profile', 'late_credential'])
def test_fresh_target_bindings_and_disabled_restored_connectors(monkeypatch, tmp_path, fault, role):
    configuration = config(tmp_path)
    row = {'Id': 'a'*64, 'Image': 'sha256:'+'d'*64,
        'Config': {'Env': ['EVA_CONVERSATION_AUTH_FILE=/run/model.json', 'EVA_HA_TOKEN_FILE=/run/ha-token']},
        'HostConfig': {}, 'Mounts': [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/run', 'RW': False}]}
    if role == 'worker':
        row['Config']['Env'][0] = 'EVA_MODEL_ACCESS_LEASE_FILE=/run/model.json'
    configuration['writers'][0]['profile_sha256'] = module.runtime_profile_sha256(row)
    if fault == 'profile': row['Config']['Cmd'] = ['changed']
    if fault == 'missing_route':
        row['Config']['Env'].append('EVA_EXTRA_FILE=/run/unknown')
        configuration['writers'][0]['profile_sha256'] = module.runtime_profile_sha256(row)
    (tmp_path/'ha-token').write_bytes(b'changed-token' if fault == 'credential' else b'current-token')
    hold = {'restore_id': 'restore', 'archive_sha256': 'c'*64, 'target_database_oid': 42, 'held': True}
    binding = {'binding_id': 'binding', 'status': 'active' if fault == 'active_binding' else 'reauthorization_required',
               'credential_reference': 'portable-restore-redacted:binding'}
    class Connection:
        def execute(self, statement):
            if 'restore_activation' in str(statement):
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: hold))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [binding]))
    monkeypatch.setattr(module, 'verify_restore_runtime', lambda *a, **k: None)
    monkeypatch.setattr(module, 'inspect_containers', lambda: [row])
    monkeypatch.setattr(module, 'read_installation_package', lambda root: {'archive_sha256': 'c'*64})
    monkeypatch.setattr(module, 'build_access_lease', lambda path: b'expected-access-only-lease')
    monkeypatch.setattr(module, 'read_shared_runtime_file_bytes', lambda *a, **k:
        b'wrong' if fault == 'lease' else b'expected-access-only-lease')
    runtime = {'target': {'core_name': 'core', 'workers': [{'id': 'a'*64, 'name': role}]}, 'target_database': {'oid': 42}}
    guards = []
    def guard():
        guards.append(True)
        if len(guards) == 2 and fault == 'late_profile':
            row['Config']['Cmd'] = ['changed-late']
        if len(guards) == 2 and fault == 'late_credential':
            (tmp_path/'ha-token').write_bytes(b'changed-late')
    def verify():
        return module.verify_runtime_authorization(Connection(), package_root=tmp_path/'archive',
            restore_id='restore', configuration=configuration, runtime_configuration=runtime,
            guard=guard)
    if fault:
        with pytest.raises(ValueError): verify()
    else:
        result = verify()
        assert result['verified'] is True and result['scope'] == 'locally_authorized_target_bindings'
        assert result['upstream_response_verified'] is False
        assert result['pending_connector_reauthorizations'] == 1
        assert result['target_database_oid'] == 42 and len(guards) == 2


@pytest.mark.parametrize('override', [None, 'bind', 'volume', 'tmpfs', 'writable_root', 'other_path'])
def test_standard_image_ca_is_public_only_without_mutable_override(override):
    row = {'HostConfig': {'ReadonlyRootfs': True}, 'Mounts': []}
    path = '/etc/ssl/certs/ca-certificates.crt'
    if override in {'bind', 'volume'}:
        row['Mounts'] = [{'Type': override, 'Destination': '/etc/ssl', 'RW': False}]
    elif override == 'tmpfs': row['HostConfig']['Tmpfs'] = {'/etc': 'rw'}
    elif override == 'writable_root': row['HostConfig']['ReadonlyRootfs'] = False
    elif override == 'other_path': path = '/run/custom-ca.crt'
    assert module._image_public_trust_file(row, 'SSL_CERT_FILE', path) is (override is None)
    assert not module._image_public_trust_file(row, 'OTHER_FILE', path)


def test_profile_ignores_only_mount_serialization_order():
    import copy
    row = {'Id': 'a'*64, 'Image': 'sha256:'+'b'*64,
           'Config': {'Cmd': ['first', 'second'], 'Env': ['A=1', 'B=2']},
           'HostConfig': {}, 'Mounts': [
               {'Destination': '/run/z', 'Source': '/private/z', 'RW': False, 'Type': 'bind'},
               {'Destination': '/run/a', 'Source': '/private/a', 'RW': False, 'Type': 'bind'}]}
    original = copy.deepcopy(row)
    expected = module.runtime_profile_sha256(row)
    row['Mounts'].reverse()
    assert module.runtime_profile_sha256(row) == expected
    assert original['Mounts'][0]['Destination'] == '/run/z'
    for section, key in [('Config', 'Cmd'), ('Config', 'Env')]:
        changed = copy.deepcopy(row); changed[section][key].reverse()
        assert module.runtime_profile_sha256(changed) != expected
    for key, value in [('Source', '/other'), ('RW', True), ('Type', 'volume'), ('Propagation', 'shared')]:
        changed = copy.deepcopy(row); changed['Mounts'][0][key] = value
        assert module.runtime_profile_sha256(changed) != expected


@pytest.mark.parametrize('mounts', [None, {}, [None], [{}], [{'Destination': 'relative'}],
                                  [{'Destination': '/run'}, {'Destination': '/run'}]])
def test_profile_refuses_ambiguous_mount_inventory(mounts):
    with pytest.raises(ValueError, match='mount destinations'):
        module.runtime_profile_sha256({'Mounts': mounts})


def test_profile_accepts_only_observed_null_false_oom_default_equivalence():
    from copy import deepcopy
    row = {'Mounts': [], 'HostConfig': {'OomKillDisable': False, 'Privileged': False}}
    expected = module.runtime_profile_sha256(row)
    row['HostConfig']['OomKillDisable'] = None
    original = deepcopy(row)
    assert module.runtime_profile_sha256(row) == expected
    assert row == original
    row['HostConfig']['OomKillDisable'] = True
    assert module.runtime_profile_sha256(row) != expected

    row['HostConfig']['OomKillDisable'] = False
    row['HostConfig']['Privileged'] = None
    assert module.runtime_profile_sha256(row) != expected
    del row['HostConfig']['OomKillDisable']
    assert module.runtime_profile_sha256(row) != expected


@pytest.mark.parametrize('case', ['core_authority', 'worker_authority', 'secret', 'lease', 'alias'])
def test_only_core_restore_authority_can_use_approved_mutable_mount(tmp_path,case):
    row = {'Mounts':[{'Type':'bind','Source':str(tmp_path),'Destination':'/run','RW':True}]}
    environment = {'EVA_PORTABLE_RESTORE_AUTHORITY_FILE':'/run/authority.json'}
    path = '/run/authority.json'
    lease = '/run/model.json'
    if case == 'secret':environment = {'EVA_HA_TOKEN_FILE':path}
    if case == 'lease':lease = path
    if case == 'alias':environment['EVA_HA_TOKEN_FILE'] = path
    if case == 'core_authority':
        assert module._runtime_file_host(path,row,environment,is_core=True,model_lease_path=lease)==tmp_path/'authority.json'
    else:
        with pytest.raises(ValueError):
            module._runtime_file_host(path,row,environment,is_core=case!='worker_authority',model_lease_path=lease)
