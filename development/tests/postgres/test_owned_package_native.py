"""Native peer review follows real queue/authority transitions, without models."""
from types import SimpleNamespace
import pytest

from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started
from eva.bootstrap.owned_package_host import _metadata, handle_package_request
from eva.bootstrap.native_initiative_work import admit_native_initiative
from eva.contracts.delegated_work import DelegatedWorkUsage


def test_native_package_peer_is_separate_local_and_revoked_with_its_source(native_digital, monkeypatch):
    from eva.adapters.postgres import initiative_execution
    from eva.bootstrap import owned_package_host
    monkeypatch.setattr(owned_package_host, "datetime", initiative_execution.datetime)
    f = native_digital
    _, original = admitted(f)
    call = started(f, original)
    f.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code='internal_error',
        error_fingerprint='a' * 64, ambiguous=False, now=NOW)
    metadata = _metadata(f.auth, f.service, f.store, original.spec.work_id)
    origin = {k: metadata[k] for k in ('task_id', 'execution_id', 'owner_binding_sha256',
        'scope_sha256', 'source_ingress_sha256', 'spec_sha256', 'workspace_key')}
    request = {'action': 'admit_review', 'origin': origin,
        'package_sha256': '7' * 64, 'scope_sha256': origin['scope_sha256']}
    reply = handle_package_request(request, authority=f.auth, service=f.service, store=f.store)
    assert reply['status'] == 'adopted'
    repeated = handle_package_request(request, authority=f.auth, service=f.service, store=f.store)
    assert repeated['already_exists'] is True and repeated['intent_id'] == reply['intent_id']
    claim = f.interests.claim_next(tenant_id=f.claim.tenant_id, installation_id=f.claim.installation_id, now=NOW)
    assert claim.intent_id == reply['intent_id']
    peer_receipt = admit_native_initiative(f.auth, claim, f.service)
    peer = f.store.load(peer_receipt.work_id, **f.service._owner)
    assert peer.spec.execution_id != original.spec.execution_id
    assert set(peer.spec.grant.capability_ids) <= {'get_reviewed_component_application',
        'write_programming_file', 'start_programming_process', 'use_programming_tool',
        'list_programming_tools', 'save_work_progress'}
    peer_metadata = _metadata(f.auth, f.service, f.store, peer.spec.work_id)
    assert peer_metadata['review_origin'] == origin
    assert peer_metadata['review_package_sha256'] == '7' * 64
    assert peer_metadata['can_publish_package'] is False
    guard, turn = f.auth.bind_work(peer.spec)
    f.control.stop(f.claim.intent_id, now=NOW)
    with pytest.raises(ValueError, match='waiting source'):
        guard.check(turn)
