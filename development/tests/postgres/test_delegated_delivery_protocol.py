"""Regression coverage for the shared delivery fence and safe rejection handling."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from eva.adapters.postgres import delegated_work_delivery as delivery_module
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_delegated_delivery_store import NOW, setup, admit, succeed
from tests.postgres.test_delegated_work_store import delegated_engine, principal_bindings


def test_revoked_summary_is_not_claimed_and_error_log_contains_no_private_data(delegated_engine, caplog):
    store, spec, destination, delivery, current, authority = setup(delegated_engine, 'shared-claim-revoked')
    admit(store, spec, destination)
    succeed(store, spec)
    with delegated_engine.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.tenant_id == store.scope.tenant_id,
            principal_bindings.c.principal_binding_id == authority['principal_binding_id']
        ).values(status='revoked'))
    assert delivery.claim_next(now=NOW + timedelta(seconds=4)) is None
    assert 'summary_claim' in caplog.text
    assert canonical_sha256({'work_id': spec.work_id}) in caplog.text
    assert spec.work_id not in caplog.text
    assert str(authority['owner_principal_id']) not in caplog.text


def test_interrupted_claim_rolls_back_insert_and_can_be_claimed_after_recovery(delegated_engine, monkeypatch, caplog):
    store, spec, destination, delivery, current, authority = setup(delegated_engine, 'shared-claim-interrupted')
    admit(store, spec, destination)
    succeed(store, spec)
    original = delivery_module.insert_initial_timeline_thread
    private_error = 'PRIVATE RESULT secret-token=never-log-this'
    def interrupted(connection, *args, **kwargs):
        original(connection, *args, **kwargs)
        if kwargs.get('event_type') == 'DELEGATED_DELIVERY_CLAIMED':
            raise RuntimeError(private_error)
    monkeypatch.setattr(delivery_module, 'insert_initial_timeline_thread', interrupted)
    assert delivery.claim_next(now=NOW + timedelta(seconds=4)) is None
    thread = delivery._delivery_thread(spec.work_id)
    with store.delivery_transaction() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(operational_checkpoints).where(
            operational_checkpoints.c.thread_id == thread)) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(operational_events).where(
            operational_events.c.thread_id == thread)) == 0
    assert private_error not in caplog.text
    assert 'RuntimeError' in caplog.text
    monkeypatch.setattr(delivery_module, 'insert_initial_timeline_thread', original)
    claim = delivery.claim_next(now=NOW + timedelta(seconds=5))
    assert claim is not None and claim.work_id == spec.work_id


def test_destination_authority_is_injected_and_rechecked_before_claim(delegated_engine):
    store, spec, destination, _, current, authority = setup(delegated_engine, 'shared-claim-injected-authority')
    admit(store, spec, destination)
    succeed(store, spec)
    class DestinationAuthority:
        def __init__(self):
            self.checked = []
        def validate(self, route, *, now):
            self.checked.append((route, now))
    adapter = DestinationAuthority()
    delivery = PostgresDelegatedWorkDeliveryStore(store, destination_authority=adapter,
        clock=lambda: NOW + timedelta(seconds=4))
    claim = delivery.claim_next(now=NOW + timedelta(seconds=4))
    assert claim is not None
    assert adapter.checked == [(destination, NOW + timedelta(seconds=4))] * 2


def test_changed_destination_authority_during_claim_does_not_create_a_send(delegated_engine, caplog):
    store, spec, destination, _, current, authority = setup(delegated_engine, 'shared-claim-authority-race')
    admit(store, spec, destination)
    succeed(store, spec)
    class RevokedAuthority:
        calls = 0
        def validate(self, route, *, now):
            self.calls += 1
            if self.calls > 1:
                raise ValueError('PRIVATE revoked account details')
    adapter = RevokedAuthority()
    delivery = PostgresDelegatedWorkDeliveryStore(store, destination_authority=adapter)
    assert delivery.claim_next(now=NOW + timedelta(seconds=4)) is None
    assert adapter.calls == 2
    assert 'PRIVATE revoked account details' not in caplog.text
    with store.delivery_transaction() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(operational_checkpoints).where(
            operational_checkpoints.c.thread_id == delivery._delivery_thread(spec.work_id))) == 0


def test_rejected_summary_does_not_starve_a_later_authorized_result(delegated_engine, caplog):
    store, first, destination, _, current, authority = setup(delegated_engine, 'shared-claim-starvation')
    admit(store, first, destination)
    succeed(store, first)
    second = first.model_copy(update={'work_id': first.work_id + ':next'})
    next_destination = replace(destination, origin_message_id='telegram:42:2')
    admit(store, second, next_destination)
    succeed(store, second)
    class SelectiveAuthority:
        def validate(self, route, *, now):
            if route.origin_message_id == destination.origin_message_id:
                raise ValueError('PRIVATE unavailable original route')
    delivery = PostgresDelegatedWorkDeliveryStore(store, destination_authority=SelectiveAuthority())
    claim = delivery.claim_next(now=NOW + timedelta(seconds=4))
    assert claim is not None and claim.work_id == second.work_id
    assert canonical_sha256({'work_id': first.work_id}) in caplog.text
    assert 'PRIVATE unavailable original route' not in caplog.text
    with store.delivery_transaction() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(operational_checkpoints).where(
            operational_checkpoints.c.thread_id == delivery._delivery_thread(first.work_id))) == 0


def test_concurrent_explicit_artifact_claim_and_restart_never_replay(delegated_engine):
    from tests.postgres.test_saved_work_artifact_delivery import saved_chain
    store, spec, delivery, bindings, authority, summary = saved_chain(delegated_engine, 'shared-artifact-concurrency')
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: delivery.claim_next(now=NOW + timedelta(seconds=14),
            lease_seconds=1, saved_document_work_id=spec.work_id), range(2)))
    claim, = [item for item in claims if item is not None]
    assert claim.artifact_sha256 is not None
    restarted = PostgresDelegatedWorkDeliveryStore(store,
        destination_authority=delivery._destination_authority, clock=lambda: NOW + timedelta(seconds=16))
    assert restarted.claim_next(now=NOW + timedelta(seconds=16), saved_document_work_id=spec.work_id) is None
    assert restarted.status(spec.work_id, **authority) == 'delivery_ambiguous'
    assert restarted.claim_next(now=NOW + timedelta(seconds=180), saved_document_work_id=spec.work_id) is None


def test_document_destination_cannot_substitute_another_owner(delegated_engine):
    from types import SimpleNamespace
    from uuid import UUID
    store, spec, destination, _, current, authority = setup(delegated_engine, 'shared-document-owner')
    wrong_destination = replace(destination, owner_principal_id=UUID(int=99), principal_binding_id=UUID(int=98))
    result = SimpleNamespace(binding={'channel': 'telegram_private',
        'owner_principal_id': str(authority['owner_principal_id']),
        'principal_binding_id': str(authority['principal_binding_id']),
        'destination': wrong_destination.to_payload()})
    class DestinationAuthority:
        def accepts_route(self, route):
            return True
        def validate(self, route, *, now):
            pytest.fail('a mismatched document owner must be rejected before channel authority')
    delivery = PostgresDelegatedWorkDeliveryStore(store, destination_authority=DestinationAuthority())
    delivery._documents = SimpleNamespace(load=lambda work_id: result)
    with pytest.raises(ValueError, match='destination owner differs'):
        delivery.authorize_delivery('document-task:wrong-owner', **authority)
