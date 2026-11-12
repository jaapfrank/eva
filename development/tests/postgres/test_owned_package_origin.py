"""Real queue/source authority for package publication; no model or installation."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.work_source_retention import WorkSourceUnavailable
from eva.bootstrap.owned_package_host import _metadata, current_package_origin, handle_package_request
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from tests.postgres.test_delegated_digital_work import delegated_engine
from tests.postgres.test_work_source_retention import _retained_work
from tests.postgres.test_memory_source_lifecycle import complete_dream_exchange
from tests.postgres.test_dream_memory_handoff import _case, _dream
from tests.postgres.test_runtime_resource_usage import NOW


def test_original_private_alias_stop_erasure_and_exact_scope_are_preserved(delegated_engine, tmp_path):
    for case in ('ordinary', 'wrong_scope', 'erased', 'stopped', 'superseded', 'continuation'):
        location = tmp_path / case; location.mkdir()
        _, installation = _case(delegated_engine, location)
        source = complete_dream_exchange(delegated_engine, _dream(installation, 'package-source'))
        store, profile, spec, owner = _retained_work(delegated_engine, source, 'package-' + case)
        service = DelegatedWorkOwnerService(store, **owner, digital_profile=profile, clock=lambda: NOW,
            conversation_scope=source.outbox._scope_value, private_bridge_principal_loader=lambda: source.turn.principal_id)
        authority = SimpleNamespace(owner=SimpleNamespace(_engine=delegated_engine,
            _scope=source.outbox._scope_value, _authority=store.scope,
            _owner=owner['owner_principal_id'], _binding=owner['principal_binding_id']))
        metadata = _metadata(authority, service, store, spec.work_id)
        assert str(owner['owner_principal_id']) != source.turn.principal_id
        origin = {k: metadata[k] for k in ('task_id', 'execution_id', 'scope_sha256',
                                          'source_ingress_sha256', 'spec_sha256', 'workspace_key', 'owner_binding_sha256')}
        record, turn = current_package_origin(authority, origin, service=service, store=store)
        assert record.spec == spec and turn == source.turn
        request = {'action': 'metadata', 'task_id': spec.work_id, 'scope_sha256': metadata['scope_sha256']}
        assert handle_package_request(request, authority=authority, service=service, store=store)['workspace_key'] == metadata['workspace_key']
        if case == 'wrong_scope':
            with pytest.raises(ValueError, match='scope'):
                handle_package_request({**request, 'scope_sha256': '0' * 64}, authority=authority, service=service, store=store)
        elif case == 'erased':
            source.journal.erase_message_content(principal_id=source.turn.principal_id, message_id=source.turn.message_id,
                operation_sha256='e' * 64, erased_at=NOW + timedelta(seconds=1))
            with pytest.raises(WorkSourceUnavailable): current_package_origin(authority, origin, service=service, store=store)
        elif case == 'stopped':
            store.request_stop(spec.work_id, now=NOW, **owner)
            with pytest.raises(ValueError, match='stopped'): current_package_origin(authority, origin, service=service, store=store)
        elif case == 'superseded':
            from eva.contracts.delegated_work import DelegatedWorkUsage
            call = store.start_call(store.claim_next(worker_id='synthetic', release=profile.release, now=NOW), now=NOW)
            store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code='provider_transient',
                error_fingerprint='a' * 64, ambiguous=False, now=NOW)
            child = profile.build_spec(source=spec.source.model_dump(mode='json'), source_ingress_sha256=spec.source_ingress_sha256,
                grant=spec.grant, work_id=spec.work_id + ':resume', execution_id=spec.execution_id, episode_id=spec.episode_id,
                generation=2, parent_task_id=spec.work_id, owner_principal_id=spec.owner_principal_id,
                principal_binding_id=spec.principal_binding_id, task=spec.task, created_at=NOW + timedelta(seconds=1), deadline=NOW + timedelta(minutes=20))
            store.admit(spec=child, title='Continuation', summary='Original preserved', worker_role='Digital',
                retention_policy_id='retention:test', retention_until=NOW + timedelta(days=7), occurred_at=NOW + timedelta(seconds=1))
            original, _ = current_package_origin(authority, origin, service=service, store=store)
            actual, _ = current_package_origin(authority, origin, service=service, store=store, leaf=True)
            assert original.spec == spec and actual.spec == child

        elif case == 'continuation':
            from eva.contracts.delegated_work import DelegatedWorkUsage
            from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
            from eva.adapters.postgres.owned_package_context import package_notices, package_context
            clock = [NOW]
            service._clock = lambda: clock[0]
            call = store.start_call(store.claim_next(worker_id='synthetic', release=profile.release, now=NOW), now=NOW)
            store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code='provider_transient',
                error_fingerprint='a' * 64, ambiguous=False, now=NOW)
            request = {'action': 'continue', 'origin': origin, 'scope_sha256': origin['scope_sha256'],
                'review_id': '1' * 64, 'package_sha256': '2' * 64, 'reason': 'application_ready'}
            first = handle_package_request(request, authority=authority, service=service, store=store)
            assert first['state'] == 'scheduled'
            clock[0] += timedelta(seconds=3)
            repeated = handle_package_request(request, authority=authority, service=service, store=store)
            assert repeated['schedule']['schedule_id'] == first['schedule']['schedule_id']
            assert repeated['schedule']['run_at'] == first['schedule']['run_at']
            notices = package_notices(delegated_engine, spec)
            assert len(notices) == 1 and notices[0]['classification'] == 'host_status_not_owner_instruction'
            assert 'geen nieuwe eigenaarsberichten' in package_context(delegated_engine, spec)[0]
            scheduler = ScheduledOwnerWork(service)
            assert len(scheduler.list()['schedules']) == 1
            assert scheduler.run_once() == 'dispatched'
            latest = current_package_origin(authority, origin, service=service, store=store, leaf=True)[0]
            assert latest.spec.execution_id == spec.execution_id
            assert latest.spec.source == spec.source and latest.spec.task == spec.task and latest.spec.grant == spec.grant
            assert latest.spec.parent_task_id == spec.work_id
            assert len(package_notices(delegated_engine, latest.spec)) == 1
            assert handle_package_request(request, authority=authority, service=service, store=store)['state'] == 'scheduled'
            store.request_stop(latest.spec.work_id, now=clock[0], **owner)
            with pytest.raises(ValueError, match='stopped'):
                handle_package_request(request, authority=authority, service=service, store=store)


def test_private_package_followup_is_bound_to_a_closed_predecessor(delegated_engine, tmp_path):
    from dataclasses import replace
    from eva.contracts.delegated_work import DelegatedWorkUsage

    _, installation = _case(delegated_engine, tmp_path)
    source = complete_dream_exchange(delegated_engine, _dream(installation, 'package-link'))
    store, profile, old, owner = _retained_work(delegated_engine, source, 'package-link', admit=False)
    profile = replace(profile, capability_ids=('apply_reviewed_component_improvement',))
    previous = profile.build_spec(
        source=old.source.model_dump(mode='json'), source_ingress_sha256=old.source_ingress_sha256,
        work_id=old.work_id, episode_id=old.episode_id, generation=1, parent_task_id=old.parent_task_id,
        owner_principal_id=old.owner_principal_id, principal_binding_id=old.principal_binding_id,
        task=old.task, created_at=NOW, deadline=NOW + timedelta(minutes=20),
    )
    store.admit(spec=previous, title='Package fixture', summary='Closed source fixture',
                worker_role='Digital', retention_policy_id='retention:test',
                retention_until=NOW + timedelta(days=7), occurred_at=NOW)
    service = DelegatedWorkOwnerService(
        store, **owner, digital_profile=profile, clock=lambda: NOW,
        conversation_scope=source.outbox._scope_value,
        private_bridge_principal_loader=lambda: source.turn.principal_id,
    )
    followup = replace(source.turn, message_id='package-link:new',
                       text='Use the package from my previous task.')
    source.journal.admit(followup)

    def admit(turn, key):
        return service.execute('digital', turn, task='Submit retained package for review.',
                               request_key=key, prior_work_id=previous.work_id)

    with pytest.raises(ValueError, match='same private conversation'):
        admit(followup, 'package-link-active')
    call = store.start_call(store.claim_next(worker_id='fixture', release=profile.release, now=NOW), now=NOW)
    store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code='internal_error',
                         error_fingerprint='a' * 64, ambiguous=True, now=NOW)
    other_chat = replace(followup, conversation_id='package-link:other-chat')
    with pytest.raises(ValueError, match='same private conversation'):
        admit(other_chat, 'package-link-other-chat')
    result = admit(followup, 'package-link-valid')
    current_id = result['work']['work_id']
    store.start_call(store.claim_next(worker_id='fixture', release=profile.release, now=NOW), now=NOW)
    authority = SimpleNamespace(owner=SimpleNamespace(
        _engine=delegated_engine, _scope=source.outbox._scope_value, _authority=store.scope,
        _owner=owner['owner_principal_id'], _binding=owner['principal_binding_id']))
    metadata = _metadata(authority, service, store, current_id)
    linked = metadata['linked_package_origin']
    assert linked['task_id'] == previous.work_id
    assert linked['spec_sha256'] == previous.sha256
    assert linked['workspace_key'] != metadata['workspace_key']
    assert linked['owner_binding_sha256'] == metadata['owner_binding_sha256']
    assert store.load(previous.work_id, **owner).state == 'ambiguous'
