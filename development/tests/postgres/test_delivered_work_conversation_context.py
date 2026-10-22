"""Acknowledged work participates in its original private chat under current authority."""
from datetime import timedelta
from dataclasses import replace
import pytest
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn, ConversationConflict
from eva.application.conversation_reply import ConversationReplyContext
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_delivery_store import setup, admit, succeed, NOW


def prepared(engine, label):
    store,spec,dest,delivery,current,auth=setup(engine,label)
    t=ConversationTurn.private(dest.origin_tenant_id,dest.origin_installation_id,dest.origin_principal_id,
        dest.origin_conversation_id,dest.origin_message_id,'Research the public question.',
        NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    journal=PostgresConversationJournal(engine,external_timeline_scope(t.tenant_id,t.installation_id,'fixture'))
    journal.admit(t);admit(store,spec,dest);succeed(store,spec)
    return store,spec,dest,delivery,current,auth,t,journal


def read(delivery, auth, **values):
    return delivery.recent_acknowledged_context(**auth,conversation_id=values.get('conversation_id','telegram:42'),
        before=values.get('before',NOW+timedelta(seconds=6)),now=values.get('now',NOW+timedelta(seconds=6)))


def acknowledge(delivery):
    claim=delivery.claim_next(now=NOW+timedelta(seconds=4))
    assert claim is not None
    assert delivery.acknowledge(claim,'telegram:42:77',now=NOW+timedelta(seconds=5))=='acknowledged'


def test_only_acknowledged_result_in_exact_chat_and_time_is_context(database):
    store,spec,dest,delivery,current,auth,t,journal=prepared(database,'context-ack')
    assert read(delivery,auth) is None
    acknowledge(delivery)
    found=read(delivery,auth)
    assert found['text']==store.load(spec.work_id,**auth).result.output
    assert found['occurred_at']=='2026-08-26T12:00:05Z'
    assert read(delivery,auth,conversation_id='telegram:99') is None
    assert read(delivery,auth,before=NOW+timedelta(seconds=4)) is None
    assert read(delivery,auth,now=NOW+timedelta(days=2)) is None
    from eva.application.telegram_ingress import TelegramPrincipalBinding
    current[0]=type(current[0])((TelegramPrincipalBinding(42,str(auth['owner_principal_id']),('99',),str(auth['principal_binding_id'])),))
    assert read(delivery,auth) is None


def test_forgotten_source_is_not_passively_reintroduced(database):
    _,_,_,delivery,_,auth,t,journal=prepared(database,'context-erased')
    acknowledge(delivery)
    journal.erase_message_content(principal_id=t.principal_id,message_id=t.message_id,
        operation_sha256='a'*64,erased_at=NOW+timedelta(seconds=6))
    assert read(delivery,auth) is None


def test_forget_during_last_authorization_is_rechecked_before_return(database,monkeypatch):
    _,_,_,delivery,_,auth,t,journal=prepared(database,'context-erasure-race')
    acknowledge(delivery)
    original=delivery.authorize_delivery;calls=[]
    def authorize(*args,**kwargs):
        result=original(*args,**kwargs);calls.append(result)
        if len(calls)==2:
            journal.erase_message_content(principal_id=t.principal_id,message_id=t.message_id,
                operation_sha256='b'*64,erased_at=NOW+timedelta(seconds=6))
        return result
    monkeypatch.setattr(delivery,'authorize_delivery',authorize)
    assert read(delivery,auth) is None
    assert len(calls)==2


def test_quote_binding_changes_replay_identity_and_never_stores_quoted_plaintext(database):
    _,_,_,_,_,_,t,journal=prepared(database,'context-replay')
    quoted=ConversationReplyContext(source_ref='telegram:42:2',text='PRIVATE_QUOTATION_MARKER',
        occurred_at='2026-08-26T12:00:00Z')
    followup=replace(t,message_id='telegram:42:3',text='Summarize this.',reply_context=quoted)
    journal.admit(followup);journal.admit(followup)
    data=journal._timeline.events('conversation:'+followup.message_id)[0].payload['data']
    assert data['text']=='Summarize this.' and data['reply_context_sha256']==quoted.context_sha256
    assert quoted.text not in str(data)
    with pytest.raises(ConversationConflict,match='different ingress'):
        journal.admit(replace(followup,reply_context=replace(quoted,text='Another quotation')))


def test_uncertain_delivery_is_not_context(database):
    _,_,_,delivery,_,auth,_,_=prepared(database,'context-ambiguous')
    claim=delivery.claim_next(now=NOW+timedelta(seconds=4))
    assert delivery.mark_ambiguous(claim,'TimeoutError',now=NOW+timedelta(seconds=5))=='ambiguous'
    assert read(delivery,auth) is None
