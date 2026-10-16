"""Activity reads retained public sources; never runs historical tools."""
from uuid import uuid4
import pytest
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_household_public_work import setup
from tests.postgres.test_household_conversation_context import exchange, other
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.bootstrap.household_continuity import HouseholdConversationContext
from eva.bootstrap.recent_activity import RecentActivityActions
from eva.application.conversation import ConversationActionCall


def action(x, turn, *, failed=False):
    scope=external_timeline_scope(turn.tenant_id,turn.installation_id,'fixture')
    timeline=PostgresOperationalTimeline(x.server.store._engine,scope)
    thread=PostgresExternalConversationTools._thread_id(turn,'read_home_assistant_inventory',0)
    state=timeline.create_thread(thread_id=thread,checkpoint_id=thread+':admitted',
        occurred_at=x.clock[0],event_type='TOOL_REQUEST_ADMITTED',data={
            'action_call_id':'fixture','action_name':'read_home_assistant_inventory',
            'action_arguments':{},'receipt_output':{},'source_message_id':turn.message_id,
            'conversation_id':turn.conversation_id,'principal_id':turn.principal_id})
    timeline.transition(thread_id=thread,expected_revision=state.revision,
        checkpoint_id=thread+':done',phase='verified',status='completed',
        event_type='RECOVERY_FAILED' if failed else 'STATE_VERIFIED',
        occurred_at=x.clock[0],data={'proof':{'evidence':{'fixture':True}}})


def test_cross_device_activity_keeps_original_time_and_failed_status(database):
    x=setup(database,'activity-other-device')
    first=exchange(x,x.service,'Lees de inventaris','Gelezen');action(x,first)
    second=exchange(x,x.service,'Lees opnieuw','Niet gelukt');action(x,second,failed=True)
    current=other(x);turn=current.turn(str(uuid4()),'Wat heb je gedaan?')
    provider=RecentActivityActions(lambda:HouseholdConversationContext(x.server))
    result=provider.execute(turn,ConversationActionCall('history','read_recent_activity',{'offset':0})).output
    rows=result['records'];assert len(rows)==2 and result['next_offset'] is None
    assert rows[0]['actions']['records'][0]['recorded_status']=='verified'
    assert rows[1]['actions']['records'][0]['recorded_status']=='failed'
    assert rows[0]['actions']['records'][0]['started_at']==first.occurred_at
    assert rows[0]['actions']['records'][0]['receipt_sha256']


def test_activity_includes_current_conversation_and_pages_without_dropping_old_turns(database):
    x=setup(database,'activity-pagination')
    for i in range(23):
        old=exchange(x,x.service,'Vraag '+str(i),'Antwoord');action(x,old)
        x.service.current('renew')
    turn=x.service.turn(str(uuid4()),'Wat heb je gedaan?');reader=HouseholdConversationContext(x.server)
    first=reader.recent(turn,limit=20,activity=True)
    second=reader.recent(turn,limit=20,activity=True,offset=first['next_offset'])
    assert len(first['records'])==20 and len(second['records'])==3
    assert second['next_offset'] is None
    assert len({r['source_sha256'] for r in first['records']+second['records']})==23


def test_erased_request_excludes_its_tool_receipt(database):
    x=setup(database,'activity-erased');old=exchange(x,x.service,'Verwijderd','Verwijderd');action(x,old)
    PostgresConversationJournal(database,x.server.conversation_scope).erase_message_content(
        principal_id=old.principal_id,message_id=old.message_id,operation_sha256='f'*64,erased_at=x.clock[0])
    turn=other(x).turn(str(uuid4()),'Wat heb je gedaan?')
    assert HouseholdConversationContext(x.server).recent(turn,activity=True)['records']==[]


def test_activity_window_does_not_move_between_pages(database):
    from datetime import timedelta
    x=setup(database,'activity-pinned')
    old=exchange(x,x.service,'Eerdere actie','Antwoord');action(x,old)
    turn=x.service.turn(str(uuid4()),'Wat heb je gedaan?')
    reader=HouseholdConversationContext(x.server)
    first=reader.recent(turn,activity=True)
    x.clock[0]+=timedelta(seconds=2)
    newer=exchange(x,x.service,'Latere actie','Antwoord');action(x,newer)
    second=reader.recent(turn,activity=True)
    assert first==second
