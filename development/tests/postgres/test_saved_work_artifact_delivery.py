from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest
import sqlalchemy as sa

from eva.adapters.connectors.telegram import TelegramProactiveDelivery
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, operational_events, external_timeline_scope
from eva.adapters.postgres.saved_work_artifacts import saved_work_artifacts
from eva.application.conversation import ConversationTurn, ConversationConflict
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker
from tests.postgres.test_delegated_delivery_store import NOW, _digital_return_chain
from tests.postgres.test_delegated_work_store import delegated_engine

PATH = '/documents/proposal.md'
CONTENT = '# Proposal\n\n' + 'Complete text with source https://example.test/source 😀\n' * 150


def saved_chain(engine, label, damage=None):
    store, specs, destination, delivery, bindings, auth, result = _digital_return_chain(
        engine, label, output='Prepared proposal: ' + PATH, native_source_scope=True)
    spec = specs[-1]
    source = ConversationTurn.private(spec.source.tenant_id, spec.source.installation_id,
        spec.source.principal_id, spec.source.conversation_id, spec.source.message_id,
        'Prepare a private read-only proposal', NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    source_scope = external_timeline_scope(source.tenant_id, source.installation_id, 'fixture source')
    assert source_scope != store.scope
    PostgresConversationJournal(engine, source_scope).admit(source)
    namespace = 'background-work:' + hashlib.sha256(spec.execution_id.encode()).hexdigest()
    admission = {'action_name': 'save_workspace_document', 'action_call_id': 'save:proposal',
        'action_arguments': {'path': PATH, 'content': CONTENT, 'component_step': None}, 'step_index': 296,
        'execution_namespace': namespace, 'conversation_id': source.conversation_id,
        'principal_id': source.principal_id, 'source_message_id': source.message_id,
        'request_arguments': {'path': PATH, 'content': CONTENT},
        'request_expected_proof': {'content': CONTENT},
        'request_observation_arguments': {'path': PATH},
        'request_tool_configuration_sha256': 'ab' * 32,
        'receipt_output': {'transport': 'mcp_gateway', 'operation_kind': 'write',
            'external_state_verified': True, 'tool_configuration_sha256': 'ab' * 32}}
    if damage == 'principal':
        admission['principal_id'] = 'other-person'
    if damage == 'namespace':
        admission['execution_namespace'] = 'background-work:' + 'ff' * 32
    thread = 'tool:' + PostgresExternalConversationTools._historical_operation_id(
        source, 'save_workspace_document', admission, 296)
    timeline = PostgresOperationalTimeline(engine, source_scope)
    timeline.create_thread(thread_id=thread, checkpoint_id=thread + ':save',
        occurred_at=NOW + timedelta(seconds=9), data=admission, event_type='TOOL_REQUEST_ADMITTED')
    proof = {'expected': {'content': CONTENT}, 'observed': {'content': CONTENT},
             'evidence': {'source': 'mcp_gateway_readback'}}
    if damage == 'content':
        proof['observed']['content'] = 'different content'
    timeline.transition(thread_id=thread, expected_revision=1, checkpoint_id=thread + ':verified',
        phase='verified', status='completed', event_type='STATE_VERIFIED',
        occurred_at=NOW + timedelta(seconds=10), data={'proof': proof})
    summary = delivery.claim_next(now=NOW + timedelta(seconds=12))
    assert summary.artifact_sha256 is None
    delivery.acknowledge(summary, 'telegram:42:7', now=NOW + timedelta(seconds=13))
    delivery._clock = lambda: NOW + timedelta(seconds=14)
    return store, spec, delivery, bindings, auth, summary


def test_complete_saved_document_uses_original_recipient_and_keeps_old_ack(delegated_engine, monkeypatch):
    store, spec, delivery, bindings, auth, summary = saved_chain(delegated_engine, 'full-artifact')
    saved = saved_work_artifacts(store, store.load(spec.work_id, **auth))
    assert delivery.status(spec.work_id, **auth) == 'saved_result_ready'
    assert delivery.claim_next(now=NOW + timedelta(seconds=14)) is None  # No unsolicited attachment.
    assert delivery.claim_next(now=NOW + timedelta(seconds=14), saved_document_work_id='work:unrelated') is None
    assert saved.manifest[0]['content_sha256'] == hashlib.sha256(CONTENT.encode()).hexdigest()
    with delegated_engine.connect() as connection:
        old_ack = connection.execute(sa.select(operational_events).where(
            operational_events.c.thread_id == delivery._delivery_thread(spec.work_id))).mappings().all()
    calls = []
    transport = TelegramProactiveDelivery(bot_token='123456:fixture', recipient_resolver=lambda: '42')
    def send(method, body, **kwargs):
        assert method == 'sendDocument'
        calls.append((body, kwargs['document'][1]))
        assert body['chat_id'] == '42' and body['reply_parameters']['message_id'] == 1
        return {'message_id': 8 + len(calls), 'chat': {'id': 42, 'type': 'private'},
                'caption': body['caption'], 'reply_to_message': {'message_id': 1},
                'document': {'file_name': kwargs['document'][0], 'file_size': len(kwargs['document'][1]), 'file_id': 'file:fixture'}}
    monkeypatch.setattr(transport._api, '_call', send)
    monkeypatch.setattr(transport._api, 'download_document', lambda *a, **kw: calls[-1][1])
    clock = lambda: NOW + timedelta(seconds=14)
    assert DelegatedWorkDeliveryWorker(delivery, transport, clock=clock).run_once(saved_document_work_id=spec.work_id) == 'acknowledged'
    assert calls[0][1].decode('utf-8') == saved.output
    assert CONTENT in saved.output and len(calls) == 1
    restarted = PostgresDelegatedWorkDeliveryStore(store, bindings_loader=lambda: bindings[0], clock=clock)
    assert DelegatedWorkDeliveryWorker(restarted, transport, clock=clock).run_once(saved_document_work_id=spec.work_id) == 'idle'
    assert restarted.status(spec.work_id, **auth) == 'delivery_acknowledged'
    with delegated_engine.connect() as connection:
        assert connection.execute(sa.select(operational_events).where(
            operational_events.c.thread_id == delivery._delivery_thread(spec.work_id))).mappings().all() == old_ack
    assert restarted.authorize_delivery(spec.work_id, **auth).operation_id == summary.operation_id


@pytest.mark.parametrize('damage', ['content', 'principal', 'namespace'])
def test_unbound_or_unverified_document_is_not_sent(delegated_engine, damage):
    store, spec, delivery, _, auth, _ = saved_chain(delegated_engine, 'artifact-' + damage, damage)
    if damage == 'namespace':
        assert saved_work_artifacts(store, store.load(spec.work_id, **auth)) is None
    else:
        with pytest.raises((ValueError, ConversationConflict)):
            saved_work_artifacts(store, store.load(spec.work_id, **auth))
    assert delivery.claim_next(now=NOW + timedelta(seconds=14), saved_document_work_id=spec.work_id) is None


@pytest.mark.parametrize('failure', ['revoke', 'timeout', 'expired_claim'])
def test_partial_or_uncertain_artifact_delivery_never_repeats(delegated_engine, monkeypatch, failure):
    store, spec, delivery, bindings, auth, _ = saved_chain(delegated_engine, 'artifact-' + failure)
    transport = TelegramProactiveDelivery(bot_token='123456:fixture', recipient_resolver=lambda: '42')
    calls = []
    def send(method, body, **kwargs):
        calls.append(body)
        if failure == 'timeout':
            raise TimeoutError('acceptance unknown')
        return {'message_id': 9, 'chat': {'id': 42, 'type': 'private'}, 'caption': body['caption'],
                'reply_to_message': {'message_id': 1}, 'document': {'file_name': kwargs['document'][0],
                    'file_size': len(kwargs['document'][1]), 'file_id': 'fixture'}}
    monkeypatch.setattr(transport._api, '_call', send)
    if failure == 'revoke':
        authorize = delivery.authorize_delivery
        count = 0
        def revoked(*args, **kwargs):
            nonlocal count
            result = authorize(*args, **kwargs)
            count += 1
            # Queue performs two authorizations; worker's first read is the third.
            if count == 3:
                bindings[0] = TelegramPrincipalBindings(())
            return result
        monkeypatch.setattr(delivery, 'authorize_delivery', revoked)
    if failure == 'expired_claim':
        claim = delivery.claim_next(now=NOW + timedelta(seconds=14), lease_seconds=1, saved_document_work_id=spec.work_id)
        assert claim.artifact_sha256 is not None
        assert delivery.claim_next(now=NOW + timedelta(seconds=16), saved_document_work_id=spec.work_id) is None
        assert calls == []
    else:
        assert DelegatedWorkDeliveryWorker(delivery, transport,
            clock=lambda: NOW + timedelta(seconds=14)).run_once(saved_document_work_id=spec.work_id) == 'ambiguous'
        assert len(calls) == (0 if failure == 'revoke' else 1)
    assert delivery.claim_next(now=NOW + timedelta(seconds=200), saved_document_work_id=spec.work_id) is None


def test_closed_text_attempt_is_preserved_when_document_format_is_delivered(delegated_engine, monkeypatch):
    from eva.adapters.postgres.operational_timeline import insert_initial_timeline_thread, transition_timeline_thread
    from eva.contracts.canonical_json import canonical_sha256
    store, spec, delivery, _, auth, _ = saved_chain(delegated_engine, 'changed-representation')
    artifact = saved_work_artifacts(store, store.load(spec.work_id, **auth))
    old_sha = canonical_sha256(list(artifact.manifest))
    old_thread = delivery._delivery_thread(spec.work_id, old_sha)
    with delegated_engine.begin() as c:
        store._scope(c)
        insert_initial_timeline_thread(c, store.scope, thread_id=old_thread, checkpoint_id=old_thread+':claimed',
            occurred_at=NOW+timedelta(seconds=14), event_type='DELEGATED_DELIVERY_CLAIMED',
            phase='delivery_claimed', status='active', data={'work_id': spec.work_id, 'artifact_sha256': old_sha})
        transition_timeline_thread(c, store.scope, thread_id=old_thread, expected_revision=1,
            checkpoint_id=old_thread+':ambiguous', occurred_at=NOW+timedelta(seconds=15),
            event_type='DELEGATED_DELIVERY_AMBIGUOUS', phase='delivery_ambiguous', status='needs_help',
            data={'error_code': 'RuntimeError'})
        original=c.execute(sa.select(operational_events).where(operational_events.c.thread_id==old_thread)).mappings().all()
    claim=delivery.claim_next(now=NOW+timedelta(seconds=16), saved_document_work_id=spec.work_id)
    assert claim.artifact_sha256==artifact.sha256 and artifact.sha256!=old_sha
    assert claim.to_payload()['representation']=='utf8_document/v1'
    delivery.acknowledge(claim, 'telegram:42:9:sha256:'+hashlib.sha256(artifact.output.encode()).hexdigest(), now=NOW+timedelta(seconds=17))
    assert delivery.claim_next(now=NOW+timedelta(seconds=18), saved_document_work_id=spec.work_id) is None
    with delegated_engine.connect() as c:
        assert c.execute(sa.select(operational_events).where(operational_events.c.thread_id==old_thread)).mappings().all()==original
