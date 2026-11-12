"""Lost replies and repeated OAuth callbacks never create another package effect."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.owned_package import sha
from eva.operations.owned_package_handoff import OwnedPackageHandoff
from eva.operations.owned_package_application import OwnedPackageRuntime
from tests.operations.test_owned_package_handoff import inputs


def running(tmp_path, monkeypatch):
    workspace, metadata, package, candidate, policy = inputs(tmp_path)
    root = tmp_path / 'receipts'
    def rpc(action, **kwargs):
        if action == 'owner_current': return {'current': True, 'owner_binding_sha256': metadata['owner_binding_sha256']}
        return metadata
    controller = OwnedPackageHandoff({'receipt_root': str(root), 'runtime_policy': policy,
            'owner_scopes': [metadata['scope_sha256']]}, runtime=SimpleNamespace(package_call=rpc),
        programming=None, applications=None, start=False)
    directory = root / candidate['archive_sha256']; directory.mkdir(mode=0o700)
    (directory / 'archive.tar.gz').write_bytes((workspace / 'candidate.tar.gz').read_bytes())
    (directory / 'package.json').write_text(json.dumps(package))
    (directory / 'proxy-token.private').write_text('private-proxy-token')
    (directory / 'application.private.json').write_text(json.dumps({'contract': 'EvaOwnedPackageApplication/v1',
        'task_id': metadata['task_id'], 'package_sha256': package['package_sha256'], 'archive_sha256': package['archive_sha256'],
        'status': 'catalog_verified', 'installed': True, 'container_id': '6' * 64, 'tool_catalog_sha256': '7' * 64}))
    job = {'contract': 'EvaOwnedPackageHandoff/v1', 'review_id': candidate['archive_sha256'],
        'package_sha256': package['package_sha256'], 'task_id': metadata['task_id'], 'request_id': '5' * 64,
        'origin': package['origin'], 'state': 'completed'}
    controller.jobs[job['review_id']] = job
    return controller, package, metadata, directory, job


@pytest.mark.parametrize('lost', [False, True])
def test_reconnect_retains_effect_identity_without_blind_dispatch(tmp_path, monkeypatch, lost):
    controller, package, _, directory, job = running(tmp_path, monkeypatch)
    calls = []
    def rpc(self, receipt, package, message):
        calls.append(message)
        if lost: raise RuntimeError('response lost after possible effect')
        return {'jsonrpc': '2.0', 'id': message['id'], 'result': {'content': [{'type': 'text', 'text': 'private fixture result'}]}}
    monkeypatch.setattr(OwnedPackageRuntime, 'rpc', rpc)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'fixture_read', 'arguments': {}, '_meta': {'eva_effect_key': '8' * 64}}}
    try:
        if lost:
            with pytest.raises(RuntimeError): controller.proxy(job['review_id'], 'Bearer private-proxy-token', request)
        else:
            assert controller.proxy(job['review_id'], 'Bearer private-proxy-token', request)['result']
        repeated = controller.proxy(job['review_id'], 'Bearer private-proxy-token', {**request, 'id': 2})
        assert len(calls) == 1 and repeated['id'] == 2
        if lost: assert 'error' in repeated
        else: assert repeated['result']
        changed = {**request, 'params': {**request['params'], 'arguments': {'changed': True}}}
        with pytest.raises(ValueError): controller.proxy(job['review_id'], 'Bearer private-proxy-token', changed)
        controller.effect_replies.clear(); controller.effect_reply_bytes = 0
        assert 'error' in controller.proxy(job['review_id'], 'Bearer private-proxy-token', request)
        assert len(calls) == 1
        proof = json.loads((directory / 'effects' / ('8' * 64 + '.json')).read_text())
        assert proof['effect_status'] == ('unknown' if lost else 'confirmed')
        assert 'private fixture result' not in (directory / 'effects' / ('8' * 64 + '.json')).read_text()
    finally: controller.close()


def test_unissued_callback_is_rejected_and_confirmed_callback_runs_once(tmp_path, monkeypatch):
    controller, package, metadata, directory, job = running(tmp_path, monkeypatch)
    package['descriptor']['activation'] = {'start_path': '/authorize', 'callback_path': '/callback', 'redirect_env': 'REDIRECT'}
    package['descriptor']['permissions']['network_hosts'] = ['example.invalid']
    package['package_sha256'] = canonical_sha256({k:v for k,v in package.items() if k!='package_sha256'})
    job['package_sha256'] = package['package_sha256']
    (directory / 'package.json').write_text(json.dumps(package))
    calls = []
    def activation(self, receipt, package, *, action, query=''):
        calls.append(action)
        return {'authorization_url': 'https://example.invalid/authorize?state=' + 's' * 48} if action == 'start' else {'authorized': True}
    monkeypatch.setattr(OwnedPackageRuntime, 'activation', activation)
    scope = metadata['scope_sha256']
    try:
        with pytest.raises(ValueError): controller.setup('callback', job['review_id'], scope, query='state='+'x'*48+'&code=opaque')
        assert not calls
        controller.setup('start', job['review_id'], scope)
        query = 'state='+'s'*48+'&code=opaque'
        assert controller.setup('callback', job['review_id'], scope, query=query)['authorization_callback_processed'] is True
        controller.setup('callback', job['review_id'], scope, query=query)
        with pytest.raises(ValueError): controller.setup('callback', job['review_id'], scope, query=query+'-changed')
        assert calls == ['start', 'callback']
    finally: controller.close()


def test_completed_receipt_does_not_hide_a_stopped_runtime(tmp_path, monkeypatch):
    controller, _, metadata, _, job = running(tmp_path, monkeypatch)
    def unavailable(*args): raise ValueError('container stopped')
    monkeypatch.setattr(OwnedPackageRuntime, 'inspect', unavailable)
    try:
        status = controller.status(job['review_id'], task_id=metadata['task_id'])
        assert status['installed'] is False and status['runtime_available'] is False
        assert status['semantic_success_verified'] is False
        assert job['state'] == 'completed'  # Historical receipt retained, no automatic resurrection.
    finally: controller.close()


def test_changed_host_fact_notifies_once_and_lost_admission_uses_same_identity(tmp_path, monkeypatch):
    controller, _, metadata, _, job = running(tmp_path, monkeypatch)
    calls = []
    def call(action, **arguments):
        assert action == 'continue'
        calls.append(arguments)
        if len(calls) == 1: raise RuntimeError('lost admission response')
        return {'state': 'scheduled', 'schedule': {'schedule_id': 'same-durable-admission'}}
    controller.runtime.package_call = call
    try:
        controller._notify_changed_facts()
        assert job['notifications']['application_ready']['state'] == 'admission_unconfirmed'
        controller._notify_changed_facts()
        controller._notify_changed_facts()
        assert len(calls) == 2 and calls[0] == calls[1]
        assert calls[0]['origin'] == job['origin']
        assert 'task_text' not in calls[0]
    finally: controller.close()


def test_only_exact_owner_verified_installed_endpoint_enters_managed_network_scope(monkeypatch):
    from eva.providers.mcp_owner_authority import McpOwnerAuthority
    from eva.application.conversation import ConversationConflict
    authority=object.__new__(McpOwnerAuthority)
    authority._managed_endpoints={}
    authority._read_managed_endpoints=lambda:{}
    authority._mandate={'local_networks':[],'public_https':False}
    calls=[]
    authority.authorize_turn=lambda turn:calls.append('owner')
    authority.authorize_current=lambda **kwargs:calls.append('current')
    endpoint='http://172.28.0.1:44719/owned-package/'+'a'*64+'/mcp'
    verified=[]
    def host_proof(turn,url):
        verified.append(url)
        return {'auth_type':'bearer','auth_token':'fixture-only-private-token'} if url==endpoint else None
    monkeypatch.setattr('eva.adapters.connectors.component_evaluation.owned_package_transport_auth',host_proof)
    authority.authorize_endpoint(object(),endpoint)
    assert calls==['owner','current'] and verified==[endpoint]
    with pytest.raises(ConversationConflict):authority.authorize_endpoint(object(),'http://172.28.0.1:44719/other')
    def denied(turn,url):raise ValueError('host rejected current owner or uninstalled package')
    monkeypatch.setattr('eva.adapters.connectors.component_evaluation.owned_package_transport_auth',denied)
    with pytest.raises(ValueError):authority.authorize_endpoint(object(),endpoint)
    authority.authorize_turn=lambda turn:(_ for _ in ()).throw(ConversationConflict('owner revoked'))
    with pytest.raises(ConversationConflict):authority.authorize_endpoint(object(),endpoint)
