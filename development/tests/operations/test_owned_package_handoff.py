"""Infrastructure contracts only; these fixtures never count as Eva's own win."""
import copy
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.owned_package import archive_files, sha, snapshot_candidate, read_workspace_file
from eva.operations.owned_package_application import validate_review, validate_policy, configuration_status
from eva.operations.owned_package_handoff import OwnedPackageHandoff


def archive(files, *, extra=None):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, raw in files.items():
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o444
            stream.addfile(info, io.BytesIO(raw))
        if extra is not None:
            stream.addfile(extra)
    return target.getvalue()


def inputs(tmp_path):
    workspace_key = 'a' * 64
    workspace = tmp_path / 'workspaces' / workspace_key
    (workspace / 'package').mkdir(parents=True)
    files = {'main.py': b'def answer(): return 42\n', 'test_main.py': b'assert 1 == 1\n',
        'config.schema.json': b'{"type":"object","properties":{},"required":[],"additionalProperties":false}'}
    raw = archive(files)
    descriptor = {'contract': 'EvaOwnedPackage/v1', 'name': 'fixture-package', 'version': '0.1.0',
        'files': {name: sha(content) for name, content in files.items()}, 'entrypoint': 'main.py',
        'tests': ['test_main.py'], 'config_schema': 'config.schema.json',
        'permissions': {'network_hosts': [], 'secret_names': ['PACKAGE_BEARER'], 'persistent_data': False},
        'tool_names': ['fixture_read'], 'transport': {'kind': 'http-json-rpc', 'port': 8765,
                                                    'path': '/mcp', 'bearer_env': 'PACKAGE_BEARER'}}
    candidate = {'contract': 'EvaOwnedPackageCandidate/v1', 'archive': 'candidate.tar.gz',
                 'archive_sha256': sha(raw), 'descriptor': descriptor}
    (workspace / 'candidate.tar.gz').write_bytes(raw)
    (workspace / 'package' / 'candidate.json').write_text(json.dumps(candidate))
    metadata = {'workspace_key': workspace_key, 'task_id': 'work:original', 'execution_id': 'execution:original',
        'owner_binding_sha256': '9' * 64,
        'scope_sha256': 'b' * 64, 'source_ingress_sha256': 'c' * 64, 'spec_sha256': 'd' * 64,
        'session_key': 'e' * 64, 'task_text': 'Original permitted need, not a fabricated owner message.',
        'can_publish_package': True,
        'state': 'call_started', 'active_lease': True}
    package = snapshot_candidate(tmp_path / 'workspaces', metadata, sha(raw))[0]
    policy = {'image': 'sha256:' + 'f' * 64, 'network': 'none', 'root': str(tmp_path / 'installations'),
        'memory_mb': 256, 'cpus': 1, 'pids': 32, 'network_hosts': [], 'environment': {}, 'secret_files': {}}
    return workspace, metadata, package, candidate, policy


def review_evidence(package):
    review = {'contract': 'EvaOwnedPackageReview/v1', 'package_sha256': package['package_sha256'],
        'decision': 'accept', 'reason': 'Fixture for validation of the host boundary, not an actual quality verdict.',
        'checks': [{'name': 'fixture', 'passed': True, 'evidence': 'Separate sandbox output is independently bound.'}],
        'rights_verified': True}
    work = {**package['origin'], 'task_id': 'work:independent', 'execution_id': 'execution:independent',
        'workspace_key': '1' * 64, 'spec_sha256': '2' * 64, 'state': 'succeeded', 'active_lease': False,
        'session_key': '3' * 64, 'review_origin': package['origin'], 'review_package_sha256': package['package_sha256']}
    verification = {'contract': 'EvaOwnedPackageIndependentVerification/v1', 'package_sha256': package['package_sha256'],
        'review_spec_sha256': work['spec_sha256'], 'image': 'sha256:' + 'f' * 64, 'test_sha256': '4' * 64,
        'stdout_sha256': sha(b''), 'stderr_sha256': sha(b''), 'exit_code': 0, 'timed_out': False, 'truncated': False}
    return review, work, verification


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'hardlink', 'device'])
def test_archive_cannot_escape_or_supply_special_files(kind):
    item = tarfile.TarInfo('../outside' if kind == 'traversal' else 'linked')
    if kind == 'symlink': item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'
    if kind == 'hardlink': item.type = tarfile.LNKTYPE; item.linkname = 'main.py'
    if kind == 'device': item.type = tarfile.CHRTYPE
    with pytest.raises(ValueError):
        archive_files(archive({'main.py': b'x'}, extra=item))


def test_snapshot_binds_all_bytes_and_original_identity(tmp_path):
    workspace, metadata, package, candidate, _ = inputs(tmp_path)
    assert package['origin']['task_id'] == metadata['task_id']
    assert package['origin']['source_ingress_sha256'] == metadata['source_ingress_sha256']
    (workspace / 'candidate.tar.gz').write_bytes(archive({'other.py': b'modified'}))
    with pytest.raises(ValueError, match='exact archive'):
        snapshot_candidate(tmp_path / 'workspaces', metadata, candidate['archive_sha256'])


def test_snapshot_does_not_accept_another_workspace_or_symlink(tmp_path):
    workspace, metadata, _, _, _ = inputs(tmp_path)
    secret = tmp_path / 'private.txt'; secret.write_bytes(b'not a package')
    (workspace / 'leak').symlink_to(secret)
    with pytest.raises(OSError): read_workspace_file(tmp_path / 'workspaces', metadata['workspace_key'], 'leak')
    with pytest.raises(FileNotFoundError): read_workspace_file(tmp_path / 'workspaces', '0' * 64, 'candidate.tar.gz')


@pytest.mark.parametrize('change', ['self', 'same_execution', 'same_workspace', 'scope', 'failed', 'lease', 'timeout', 'truncated', 'nonzero', 'rights', 'false_check'])
def test_self_approval_and_unknown_or_failed_verification_are_rejected(tmp_path, change):
    _, _, package, _, _ = inputs(tmp_path)
    review, work, proof = review_evidence(package)
    if change == 'self': work['task_id'] = package['origin']['task_id']
    if change == 'same_execution': work['execution_id'] = package['origin']['execution_id']
    if change == 'same_workspace': work['workspace_key'] = package['origin']['workspace_key']
    if change == 'scope': work['owner_binding_sha256'] = '0' * 64
    if change == 'failed': work['state'] = 'failed'
    if change == 'lease': work['active_lease'] = True
    if change == 'timeout': proof['timed_out'] = True
    if change == 'truncated': proof['truncated'] = True
    if change == 'nonzero': proof['exit_code'] = 1
    if change == 'rights': review['rights_verified'] = False
    if change == 'false_check': review['checks'][0]['passed'] = False
    with pytest.raises(ValueError): validate_review(package, review, work, proof)


def test_host_network_and_ambient_environment_cannot_be_requested(tmp_path):
    _, _, package, _, policy = inputs(tmp_path)
    for modified in ({**policy, 'network': 'host'}, {**policy, 'environment': {'PYTHONPATH': '/host'}}):
        with pytest.raises(ValueError): validate_policy(modified, package)


def test_review_can_cross_channel_scope_with_the_same_verified_canonical_owner(tmp_path):
    _, _, package, _, _ = inputs(tmp_path)
    review, work, proof = review_evidence(package)
    work['scope_sha256'] = '8' * 64
    validate_review(package, review, work, proof)


def test_missing_configuration_is_explicit_and_does_not_fake_success(tmp_path):
    workspace, _, package, _, policy = inputs(tmp_path)
    files = archive_files((workspace / 'candidate.tar.gz').read_bytes())
    files['config.schema.json'] = b'{"type":"object","required":["CLIENT_ID"],"properties":{"CLIENT_ID":{"type":"string"}},"additionalProperties":false}'
    package['descriptor']['permissions']['secret_names'].append('CLIENT_SECRET')
    assert configuration_status(policy, package, files) == {
        'ready': False, 'missing_configuration': ['CLIENT_ID'], 'missing_secrets': ['CLIENT_SECRET']}


def test_remote_configuration_schema_is_not_fetched(tmp_path):
    workspace, _, package, _, policy = inputs(tmp_path)
    files = archive_files((workspace / 'candidate.tar.gz').read_bytes())
    files['config.schema.json'] = b'{"type":"object","$ref":"http://169.254.169.254/metadata"}'
    with pytest.raises(ValueError, match='remote'):
        configuration_status(policy, package, files)


def test_handoff_waits_for_builder_and_never_approves_itself(tmp_path):
    workspace, metadata, package, candidate, policy = inputs(tmp_path)
    calls = []
    def rpc(action, **args):
        calls.append(action)
        if action == 'metadata': return dict(metadata)
        if action == 'admit_review': return {'status': 'adopted', 'intent_id': 'personal-interest:fixture'}
        if action == 'review_metadata': return {'state': 'not_admitted'}
        raise AssertionError(action)
    programming = SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path / 'workspaces'),
        processes_idle=lambda *a, **k: {'idle': True})
    controller = OwnedPackageHandoff({'receipt_root': str(tmp_path / 'receipts'), 'runtime_policy': policy},
        runtime=SimpleNamespace(package_call=rpc), programming=programming,
        applications=SimpleNamespace(), start=False)
    try:
        reply = controller.request(candidate['archive_sha256'], '5' * 64, metadata['task_id'])
        assert reply['installed'] is False and reply['state'] == 'waiting_for_builder'
        controller.run_once()
        assert 'admit_review' not in calls
        metadata.update(state='failed', active_lease=False)
        controller.run_once()
        assert calls.count('admit_review') == 1
        controller.run_once()
        assert calls.count('admit_review') == 1
        assert controller.status(candidate['archive_sha256'], task_id=metadata['task_id'])['installed'] is False
        assert controller.request(candidate['archive_sha256'], '5' * 64, metadata['task_id'])['state'] == 'awaiting_independent_review'
        assert controller.request(candidate['archive_sha256'], '6' * 64, metadata['task_id'])['state'] == 'awaiting_independent_review'
        metadata['owner_binding_sha256'] = '0' * 64
        with pytest.raises(ValueError): controller.request(candidate['archive_sha256'], '7' * 64, metadata['task_id'])
    finally:
        controller.close()


def test_interrupted_verification_is_not_repeated_on_restart(tmp_path):
    _, metadata, package, candidate, policy = inputs(tmp_path)
    root = tmp_path / 'receipts'; root.mkdir(mode=0o700)
    job = {'contract': 'EvaOwnedPackageHandoff/v1', 'review_id': candidate['archive_sha256'],
        'package_sha256': package['package_sha256'], 'request_id': '5' * 64,
        'task_id': metadata['task_id'], 'origin': package['origin'], 'state': 'verifying'}
    (root / (job['review_id'] + '.json')).write_text(json.dumps(job))
    controller = OwnedPackageHandoff({'receipt_root': str(root), 'runtime_policy': policy},
        runtime=SimpleNamespace(package_call=lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not dispatch'))),
        programming=None, applications=None, start=False)
    try:
        controller.run_once()
        assert controller.jobs[job['review_id']]['state'] == 'unconfirmed'
    finally:
        controller.close()


def test_distinct_review_scope_preserves_canonical_owner_and_failed_checks_cannot_publish(tmp_path):
    workspace, metadata, package, candidate, policy = inputs(tmp_path)
    review, reviewer, _ = review_evidence(package)
    reviewer['scope_sha256'] = '8' * 64  # Native review and private Telegram have different source scopes.
    reviewer['task_text'] = 'Independent host review'
    location = tmp_path / 'workspaces' / reviewer['workspace_key'] / 'package'
    location.mkdir(parents=True)
    (location / 'review.json').write_text(json.dumps(review))
    (location / 'verify.py').write_text("assert False, 'a real negative measurement must beat an accepting review'\n")
    def rpc(action, **args):
        if action == 'metadata': return dict(metadata)
        if action == 'admit_review': return {'status': 'adopted'}
        if action == 'review_metadata': return reviewer
        raise AssertionError(action)
    programming = SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path / 'workspaces'),
        processes_idle=lambda *a, **k: {'idle': True})
    published = []
    controller = OwnedPackageHandoff({'receipt_root': str(tmp_path / 'receipts'), 'runtime_policy': policy},
        runtime=SimpleNamespace(package_call=rpc), programming=programming,
        applications=SimpleNamespace(register=lambda *a: published.append(a)),
        verify=lambda *args: {'exit_code': 1, 'timed_out': False, 'stdout': '', 'stderr': 'assertion failed',
                              'stdout_truncated': False, 'stderr_truncated': False}, start=False)
    try:
        controller.request(candidate['archive_sha256'], '5' * 64, metadata['task_id'])
        metadata.update(state='failed', active_lease=False)
        controller.run_once(); controller.run_once()
        job = controller.jobs[candidate['archive_sha256']]
        assert job['state'] == 'rejected' and not published
        assert job['reason'] == 'independent_package_checks_did_not_pass'
        controller.run_once()
        assert job['state'] == 'rejected' and not published
    finally:
        controller.close()
