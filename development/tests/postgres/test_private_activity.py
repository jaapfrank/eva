"""One private activity read composes existing sources without executing work."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup, start, NOW
from tests.postgres.test_delegated_work_store import principal_bindings
from tests.postgres.test_recent_activity import action
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import (
    ConversationActionCall, ConversationActionInputRejected, ConversationConflict, GroundedReply,
)
from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.private_activity import PrivateActivityHistory
from eva.bootstrap.recent_activity import RecentActivityActions


def prepared(engine, label, monkeypatch):
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'Europe/Amsterdam')
    service, turn, clock = setup(engine, label, voice=False)
    aliases = TelegramPrincipalBindings((TelegramPrincipalBinding(
        101, str(service.owner_principal_id), ('101',), str(service.principal_binding_id)),))
    service._private_bridge_principal_loader = lambda: 'telegram:101'
    owner = PostgresOwnerInitiatives(engine, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner=service.owner_principal_id,
        binding=service.principal_binding_id, authority_scope=service.store.scope)
    journal = PostgresConversationJournal(engine, service.conversation_scope)
    memory = PostgresOwnerMemorySources(engine, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner=service.owner_principal_id,
        binding=service.principal_binding_id, authority_scope=service.store.scope,
        telegram_bindings=aliases)
    runtime = SimpleNamespace(_delegated_work_owner_service=service,
        _owner_initiatives=lambda: owner, _owner_memory_sources=lambda: memory,
        _journal=lambda _: journal)
    return SimpleNamespace(service=service, turn=turn, clock=clock, owner=owner,
        journal=journal, runtime=runtime, reader=PrivateActivityHistory(lambda:runtime), engine=engine)


def exchange(x, number, *, principal=None, seconds=0):
    source = replace(x.turn, principal_id=principal or x.turn.principal_id,
        audience=MemoryAudience.private_person(principal_id=principal or x.turn.principal_id),
        conversation_id='telegram:101' if principal == 'telegram:101' else x.turn.conversation_id,
        message_id='source:'+str(number), text='Historische vraag '+str(number),
        occurred_at=(NOW+timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    x.journal.admit(source)
    x.journal.commit_reply(source, GroundedReply(source.conversation_id, source.message_id,
        'Bewaard antwoord '+str(number), (), source.occurred_at, 'fixture'))
    doc = ConversationMemoryDocument(tenant_id=source.tenant_id, installation_id=source.installation_id,
        principal_id=source.principal_id, conversation_id=source.conversation_id,
        document_id='activity-doc:'+str(number), content=source.text, occurred_at=source.occurred_at,
        source_ref=source.message_id, evidence_ref='conversation-reply:'+source.message_id,
        privacy_class='internal', trust='direct_user', audience=source.audience)
    PostgresMemoryOutbox(x.engine, tenant_id=source.tenant_id,
        installation_id=source.installation_id).enqueue(doc)
    return source


def event(x, thread, kind, data, seconds=0):
    return PostgresOperationalTimeline(x.engine, x.owner._scope).create_thread(
        thread_id=thread, checkpoint_id=thread+':first', event_type=kind,
        data=data, occurred_at=NOW+timedelta(seconds=seconds))


def read(x, *, source='all', offset=0, day='2026-08-26'):
    turn=replace(x.turn, message_id='read-activity', occurred_at=x.clock[0].strftime('%Y-%m-%dT%H:%M:%SZ'))
    actions=RecentActivityActions(lambda:None, private_reader=x.reader)
    return actions.execute(turn, ConversationActionCall('activity-read','read_recent_activity',
        {'offset':offset,'source':source,'local_date':day})).output


def test_one_tool_reads_conversation_alias_work_initiative_and_learning(database, monkeypatch):
    x=prepared(database,'activity-complete',monkeypatch)
    source=exchange(x,'alias',principal='telegram:101')
    action(SimpleNamespace(server=SimpleNamespace(store=x.service.store),clock=x.clock),source)
    spec=start(x.service,x.turn)
    intent='personal-interest:'+'a'*64
    event(x,'intent:'+intent,'PERSONAL_INTENT_ADOPTED',{'principal_id':str(x.owner._owner),
        'origin':'eva_initiative','intent_id':intent,'title':'Eigen interesse','motivation':'Een idee'})
    event(x,'reflection:own','MEMORY_REFLECTION_COMPLETED',{
        'principal_sha256':hashlib.sha256(str(x.owner._owner).encode()).hexdigest(),
        'disposition':'refreshed','source_fact_count':2,'before_content_sha256':'a'*64,
        'after_content_sha256':'b'*64})
    result=read(x);sources=result['sources']
    assert set(sources)=={'conversations','work','initiative','learning'}
    conversation=sources['conversations']['records'][0]
    assert 'Bewaard antwoord alias' in conversation['exchange_fragment']
    assert conversation['actions']['records'][0]['recorded_status']=='verified'
    assert sources['work']['records'][0]['work_id']==spec.work_id
    assert sources['work']['records'][0]['state']=='admitted'
    assert sources['initiative']['records'][0]['recorded_status']=='PERSONAL_INTENT_ADOPTED'
    reflection=sources['learning']['records'][0]
    assert reflection['changed'] and 'not proof' in reflection['meaning']
    assert x.service.store.load(spec.work_id,**x.service._owner).state=='admitted'
    assert all(row['source_sha256'] for group in sources.values() for row in group['records'])


def test_conversation_paging_retention_and_forgetting_hide_exact_source(database,monkeypatch):
    x=prepared(database,'activity-paging',monkeypatch)
    for i in range(23): exchange(x,i,seconds=i)
    x.clock[0]+=timedelta(seconds=30)
    first=read(x,source='conversations')['sources']['conversations']
    second=read(x,source='conversations',offset=first['next_offset'])['sources']['conversations']
    assert len(first['records'])==20 and len(second['records'])==3 and second['next_offset'] is None
    assert len({r['source_sha256'] for r in first['records']+second['records']})==23
    x.journal.erase_message_content(principal_id=x.turn.principal_id,message_id='source:22',
        operation_sha256='f'*64,erased_at=x.clock[0])
    assert all('Historische vraag 22' not in r['exchange_fragment']
        for r in read(x,source='conversations')['sources']['conversations']['records'])


def test_time_window_is_original_local_day_not_collection_or_current_day(database,monkeypatch):
    x=prepared(database,'activity-original-day',monkeypatch)
    source=exchange(x,'yesterday')
    x.clock[0]+=timedelta(days=1,hours=8)
    result=read(x,source='conversations')
    assert result['sources']['conversations']['records'][0]['occurred_at']==source.occurred_at
    assert result['window_start']=='2026-08-25T22:00:00+00:00'
    assert result['window_end']=='2026-08-26T22:00:00+00:00'
    assert not read(x,source='conversations',day='2026-08-27')['sources']['conversations']['records']


def test_other_person_shared_project_and_revoked_binding_are_not_admitted(database,monkeypatch):
    x=prepared(database,'activity-authority',monkeypatch)
    exchange(x,'other-person',principal='other-person')
    assert not read(x,source='conversations')['sources']['conversations']['records']
    assert not x.reader.available_for(replace(x.turn,project_id='private-project'))
    other=replace(x.turn,principal_id='other-person',audience=MemoryAudience.private_person(principal_id='other-person'))
    assert not x.reader.available_for(other)
    with pytest.raises(Exception):x.reader.read(other,offset=0,source='all')
    with database.begin() as conn:
        conn.execute(principal_bindings.update().where(
            principal_bindings.c.principal_binding_id==x.service.principal_binding_id).values(status='revoked'))
    with pytest.raises(Exception):read(x)


def test_work_pages_do_not_drop_older_work_or_claim_historical_completion(database,monkeypatch):
    x=prepared(database,'activity-work-pages',monkeypatch)
    original=start(x.service,x.turn)
    for i in range(22): start(x.service,replace(x.turn,message_id='work:'+str(i)),key='work:'+str(i))
    first=read(x,source='work')['sources']['work'];second=read(x,source='work',offset=first['next_offset'])['sources']['work']
    assert len(first['records'])==20 and len(second['records'])==3
    assert original.work_id in {row['work_id'] for row in first['records']+second['records']}
    assert all('current_status_checked_at' in r for r in first['records'])


def test_learning_pages_do_not_include_other_person_or_outside_day(database,monkeypatch):
    x=prepared(database,'activity-learning-pages',monkeypatch)
    for i in range(23):event(x,'reflection:'+str(i),'MEMORY_REFLECTION_COMPLETED',{
        'principal_sha256':hashlib.sha256(str(x.owner._owner).encode()).hexdigest(),
        'disposition':'refreshed','source_fact_count':1})
    event(x,'reflection:other','MEMORY_REFLECTION_COMPLETED',{
        'principal_sha256':hashlib.sha256(b'other-person').hexdigest(),'disposition':'OTHER'})
    first=read(x,source='learning')['sources']['learning'];second=read(x,source='learning',offset=first['next_offset'])['sources']['learning']
    assert len(first['records'])==20 and len(second['records'])==3
    assert not read(x,source='learning',day='2026-08-25')['sources']['learning']['records']


def test_initiative_pages_keep_exact_owner_threads_after_adoption_query_projection(database,monkeypatch):
    x=prepared(database,'activity-initiative-owner-pages',monkeypatch)
    expected=set()
    for i in range(23):
        intent='personal-interest:'+hashlib.sha256(('owned:'+str(i)).encode()).hexdigest()
        event(x,'intent:'+intent,'PERSONAL_INTENT_ADOPTED',{
            'principal_id':str(x.owner._owner),'origin':'eva_initiative',
            'intent_id':intent,'title':'Owned initiative '+str(i)},seconds=i)
        expected.add(intent)
    own_prefix=hashlib.sha256(str(x.owner._owner).encode()).hexdigest()[:24]
    event(x,'sensory:initiative-review:'+own_prefix+':1','WILL_DECIDED',
        {'reason':'Own review'},seconds=23)

    # These newer records share the installation but belong to another owner,
    # another origin, or a completely unrelated thread. None may enter the
    # initiative page through an unscoped event-table projection.
    for i in range(23):
        intent='personal-interest:'+hashlib.sha256(('foreign:'+str(i)).encode()).hexdigest()
        event(x,'intent:'+intent,'PERSONAL_INTENT_ADOPTED',{
            'principal_id':'other-person','origin':'eva_initiative',
            'intent_id':intent,'title':'Foreign initiative'},seconds=30+i)
    event(x,'unrelated:private-thread','PRIVATE_NOTE_RECORDED',
        {'summary':'Unrelated private content'},seconds=54)
    event(x,'sensory:initiative-review:other-owner:1','WILL_DECIDED',
        {'reason':'Other owner review'},seconds=55)
    event(x,'intent:wrong-origin','PERSONAL_INTENT_ADOPTED',{
        'principal_id':str(x.owner._owner),'origin':'manual',
        'title':'Not an Eva initiative'},seconds=56)
    other=prepared(database,'activity-initiative-other-installation',monkeypatch)
    event(other,'intent:other-installation','PERSONAL_INTENT_ADOPTED',{
        'principal_id':str(x.owner._owner),'origin':'eva_initiative',
        'title':'Other installation'},seconds=57)
    x.clock[0]+=timedelta(seconds=90)

    first=read(x,source='initiative')['sources']['initiative']
    assert len(first['records'])==20 and first['next_offset']==20
    assert first==read(x,source='initiative')['sources']['initiative']
    second=read(x,source='initiative',offset=first['next_offset'])['sources']['initiative']
    records=first['records']+second['records']
    assert len(second['records'])==4 and second['next_offset'] is None
    assert {r['intent_id'] for r in records if r['intent_id'] is not None}==expected
    assert [r['detail'] for r in records if r['intent_id'] is None]==[{'reason':'Own review'}]
    assert len({r['source_sha256'] for r in records})==24


def test_all_source_paging_must_choose_explicit_source(database,monkeypatch):
    x=prepared(database,'activity-explicit-paging',monkeypatch)
    with pytest.raises(ConversationActionInputRejected): read(x,offset=20)


def test_prior_day_request_with_action_completed_today_is_included(database,monkeypatch):
    x=prepared(database,'activity-later-action',monkeypatch)
    source=exchange(x,'prior-day',seconds=-86400)
    x.clock[0]-=timedelta(days=1)
    action(SimpleNamespace(server=SimpleNamespace(store=x.service.store),clock=x.clock),source,
        completed_at=NOW)
    x.clock[0]=NOW
    rows=read(x,source='conversations')['sources']['conversations']['records']
    assert len(rows)==1 and rows[0]['occurred_at']==source.occurred_at
    assert rows[0]['actions']['records'][0]['activity_at']==NOW.strftime('%Y-%m-%dT%H:%M:%SZ')


def test_completion_stays_in_original_day_despite_later_work_update(database,monkeypatch):
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    from tests.postgres.test_delegated_delivery_store import succeed
    x=prepared(database,'activity-completion-day',monkeypatch)
    spec=start(x.service,x.turn);succeed(x.service.store,spec)
    x.clock[0]+=timedelta(days=1)
    with database.begin() as conn:
        conn.execute(delegated_work_runs.update().where(
            delegated_work_runs.c.work_id==spec.work_id).values(updated_at=x.clock[0]))
    row=read(x,source='work')['sources']['work']['records'][0]
    assert 'completed_at' in row['times_in_window']
    assert 'updated_at' not in row['times_in_window']
    assert row['state']=='succeeded'
    assert 'task_preview' not in row and row['title'].startswith('Taakstatus')


def test_planning_is_not_completed_work_and_keeps_original_event_time(database,monkeypatch):
    from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
    from tests.postgres.test_dashboard_work_context import digital
    x=prepared(database,'activity-planning',monkeypatch)
    digital(x.service,x.turn)
    scheduled=ScheduledOwnerWork(x.service).schedule(x.turn,request_key='later',
        request={'task':'Do this later','run_at':(NOW+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')})
    result=read(x,source='work')['sources']['work']
    assert all(row['state']=='admitted' for row in result['records'])
    assert result['planning_events'][0]['recorded_status']=='WORK_START_SCHEDULED'
    assert result['planning_events'][0]['run_at'].startswith('2026-08-26T13:')
    assert 'Do this later' not in str(result)


def test_past_day_read_does_not_include_future_reply():
    from eva.application.conversation import ConversationTurn
    source=ConversationTurn.private('tenant','installation','person',
        'dashboard:fixture','source:original','Original request',NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    from eva.bootstrap.private_continuity import _exchange_content
    doc=SimpleNamespace(**{k:getattr(source,k) for k in
        ('tenant_id','installation_id','principal_id','conversation_id','occurred_at','audience','project_id')},
        source_ref=source.message_id,content=source.text)
    future=SimpleNamespace(committed_reply=lambda _:SimpleNamespace(
        text='LATER_REPLY',generated_at=(NOW+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')))
    runtime=SimpleNamespace(_journal=lambda _:future)
    assert 'LATER_REPLY' not in _exchange_content(runtime,doc,before=NOW)
    assert 'LATER_REPLY' in _exchange_content(runtime,doc,before=NOW+timedelta(days=1))
    assert 'LATER_REPLY' not in _exchange_content(runtime,doc,before=NOW+timedelta(days=1),before_exclusive=True)
