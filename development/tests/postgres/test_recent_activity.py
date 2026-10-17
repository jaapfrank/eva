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


def action(x, turn, *, failed=False, completed_at=None, observation=None, receipt_output=None):
    scope=external_timeline_scope(turn.tenant_id,turn.installation_id,'fixture')
    timeline=PostgresOperationalTimeline(x.server.store._engine,scope)
    thread=PostgresExternalConversationTools._thread_id(turn,'read_home_assistant_inventory',0)
    state=timeline.create_thread(thread_id=thread,checkpoint_id=thread+':admitted',
        occurred_at=x.clock[0],event_type='TOOL_REQUEST_ADMITTED',data={
            'action_call_id':'fixture','action_name':'read_home_assistant_inventory',
            'action_arguments':{},'receipt_output':receipt_output or {},'source_message_id':turn.message_id,
            'conversation_id':turn.conversation_id,'principal_id':turn.principal_id,
            **({'observation_output_key':'inventory'} if observation is not None else {})})
    timeline.transition(thread_id=thread,expected_revision=state.revision,
        checkpoint_id=thread+':done',phase='verified',status='completed',
        event_type='RECOVERY_FAILED' if failed else 'STATE_VERIFIED',
        occurred_at=completed_at or x.clock[0],data={'proof':{'evidence':{'fixture':True,
            **({'observation':observation} if observation is not None else {})}}})


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


@pytest.mark.parametrize('local_date', [None, '2026-08-26'])
def test_activity_includes_current_conversation_and_pages_without_dropping_old_turns(database, local_date, monkeypatch):
    from datetime import timedelta
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'UTC')
    x=setup(database,'activity-pagination-'+str(local_date))
    for i in range(23):
        old=exchange(x,x.service,'Vraag '+str(i),'Antwoord');action(x,old)
        x.service.current('renew')
    turn=x.service.turn(str(uuid4()),'Wat heb je gedaan?');reader=HouseholdConversationContext(x.server)
    first=reader.recent(turn,limit=20,activity=True,local_date=local_date)
    x.clock[0]+=timedelta(seconds=2)
    newer=exchange(x,x.service,'Nieuw na vraag','Antwoord');action(x,newer)
    second=reader.recent(turn,limit=20,activity=True,offset=first['next_offset'],local_date=local_date)
    assert len(first['records'])==20 and len(second['records'])==3
    assert second['next_offset'] is None
    assert len({r['source_sha256'] for r in first['records']+second['records']})==23
    assert first['window_end']==second['window_end']==turn.occurred_at


@pytest.mark.parametrize('local_date', [None, '2026-08-26'])
def test_erased_request_excludes_its_tool_receipt(database, local_date):
    x=setup(database,'activity-erased-'+str(local_date));old=exchange(x,x.service,'Verwijderd','Verwijderd');action(x,old)
    PostgresConversationJournal(database,x.server.conversation_scope).erase_message_content(
        principal_id=old.principal_id,message_id=old.message_id,operation_sha256='f'*64,erased_at=x.clock[0])
    turn=other(x).turn(str(uuid4()),'Wat heb je gedaan?')
    assert HouseholdConversationContext(x.server).recent(turn,activity=True,local_date=local_date)['records']==[]


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


@pytest.mark.parametrize(('day', 'start', 'end', 'hours'), [
    ('2026-03-29', '2026-03-28T23:00:00+00:00', '2026-03-29T22:00:00+00:00', 23),
    ('2026-10-25', '2026-10-24T22:00:00+00:00', '2026-10-25T23:00:00+00:00', 25),
])
def test_activity_calendar_window_uses_local_midnights(monkeypatch, day, start, end, hours):
    from datetime import datetime, timedelta
    from eva.bootstrap.recent_activity import activity_window
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'Europe/Amsterdam')
    now=datetime.fromisoformat(end)+timedelta(hours=22)
    lower, upper, calendar_end, zone=activity_window(now, day)
    assert lower.isoformat()==start and upper.isoformat()==end
    assert calendar_end==upper and (upper-lower).total_seconds()==hours*3600
    assert zone=='Europe/Amsterdam'


def test_yesterday_activity_remains_visible_when_asked_late_next_day(database, monkeypatch):
    from datetime import timedelta
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'Europe/Amsterdam')
    x=setup(database,'activity-yesterday')
    old=exchange(x,x.service,'Actie gisterenmiddag','Antwoord');action(x,old)
    x.service.current('close')
    x.clock[0]+=timedelta(days=1,hours=8)
    turn=other(x).turn(str(uuid4()),'Wat deed je gisteren?')
    reader=HouseholdConversationContext(x.server)
    assert reader.recent(turn,activity=True)['records']==[]
    result=reader.recent(turn,activity=True,local_date='2026-08-26')
    assert len(result['records'])==1
    assert result['records'][0]['occurred_at']==old.occurred_at
    assert result['window_start']=='2026-08-25T22:00:00Z'
    assert result['window_end']=='2026-08-26T22:00:00Z'
    assert result['window_end_exclusive'] is True


@pytest.mark.parametrize('value', ['2026-8-26', '2026-02-30', '2026-08-27',
                                  '2026-07-25', '', 20260826, True])
def test_invalid_or_outside_calendar_date_rejected(value, monkeypatch):
    from datetime import UTC, datetime
    from eva.bootstrap.recent_activity import activity_window
    from eva.application.conversation import ConversationActionInputRejected
    monkeypatch.delenv('EVA_CONVERSATION_TIMEZONE', raising=False)
    with pytest.raises(ConversationActionInputRejected):
        activity_window(datetime(2026,8,26,12,tzinfo=UTC), value)


def test_calendar_lookback_is_bounded_and_default_timezone_is_utc(monkeypatch):
    from datetime import UTC, datetime
    from eva.bootstrap.recent_activity import activity_window
    monkeypatch.delenv('EVA_CONVERSATION_TIMEZONE', raising=False)
    start, end, _, zone=activity_window(datetime(2026,8,26,12,tzinfo=UTC),'2026-07-26')
    assert zone=='UTC' and start==datetime(2026,7,26,tzinfo=UTC)
    assert end==datetime(2026,7,27,tzinfo=UTC)


def test_calendar_midnight_boundaries_apply_to_ingress_and_receipts(database, monkeypatch):
    from datetime import UTC, datetime, timedelta
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'Europe/Amsterdam')
    x=setup(database,'activity-midnight')
    x.clock[0]=datetime(2026,8,26,21,59,58,tzinfo=UTC)
    source=other(x)
    before=exchange(x,source,'Net voor de dag','Antwoord');action(x,before)
    first=exchange(x,source,'Precies begin dag','Antwoord');action(x,first)
    x.clock[0]=datetime(2026,8,27,21,59,58,tzinfo=UTC)
    source=other(x)
    last=exchange(x,source,'Laatste seconde','Antwoord')
    action(x,last,completed_at=x.clock[0]+timedelta(seconds=1))
    after=exchange(x,source,'Precies volgende dag','Antwoord');action(x,after)
    x.clock[0]=datetime(2026,8,28,20,tzinfo=UTC)
    turn=other(x).turn(str(uuid4()),'Wat deed je gisteren?')
    provider=RecentActivityActions(lambda:HouseholdConversationContext(x.server))
    result=provider.execute(turn,ConversationActionCall('day','read_recent_activity',
        {'offset':0,'local_date':'2026-08-27'})).output
    assert [row['occurred_at'] for row in result['records']]==[first.occurred_at,last.occurred_at]
    assert result['records'][0]['actions']['records'][0]['recorded_status']=='verified'
    assert result['records'][1]['actions']['records'][0]['recorded_status']=='admitted_only'
    assert result['records'][1]['actions']['records'][0]['receipt_sha256'] is None


def test_empty_calendar_result_keeps_window_and_coverage_metadata(database, monkeypatch):
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'UTC')
    x=setup(database,'activity-empty-day')
    result=HouseholdConversationContext(x.server).recent(x.turn,activity=True,local_date='2026-08-25')
    assert result['records']==[] and result['next_offset'] is None
    assert result['window_start']=='2026-08-25T00:00:00Z'
    assert result['window_end']=='2026-08-26T00:00:00Z'
    assert result['window_end_exclusive'] is True
    assert result['session_candidate_limit']==65
    assert result['session_limit_reached'] is False and result['page_limit_reached'] is False
    assert result['coverage'] and result['pagination_consistency']


def test_calendar_activity_excludes_confirmed_private_shadow(database, monkeypatch):
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE', 'UTC')
    x=setup(database,'activity-private-day')
    public=exchange(x,x.service,'Openbare actie','Antwoord');action(x,public)
    timeline=PostgresOperationalTimeline(database,x.server.store.scope)
    timeline.create_thread(thread_id='voice-identity:'+x.service.binding['session_id'],
        checkpoint_id='fixture-confirmed',occurred_at=x.clock[0],event_type='VOICE_IDENTITY_CONFIRMED',
        phase='confirmed',status='completed',data={'speaker_confirmed_at':public.occurred_at})
    private=exchange(x,x.service,'PRIVATESHADOW','PRIVATERESULT');action(x,private)
    turn=other(x).turn(str(uuid4()),'Wat deed je vandaag?')
    result=HouseholdConversationContext(x.server).recent(turn,activity=True,local_date='2026-08-26')
    assert len(result['records'])==1
    assert result['records'][0]['occurred_at']==public.occurred_at
    assert 'PRIVATE' not in str(result)


def test_cross_midnight_completion_contains_verified_result(database, monkeypatch):
    from datetime import UTC, datetime, timedelta
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE','UTC')
    x=setup(database,'activity-actual-day')
    x.clock[0]=datetime(2026,8,26,23,59,58,tzinfo=UTC)
    source=other(x)
    old=exchange(x,source,'Lees de apparaten','Antwoord')
    observation={'total':2,'entities':[{'entity_id':'light.study','name':'Bureaulamp'},
                                      {'entity_id':'light.hall','name':'Ganglamp'}]}
    action(x,old,completed_at=x.clock[0]+timedelta(seconds=2),observation=observation)
    x.clock[0]+=timedelta(seconds=4)
    turn=source.turn(str(uuid4()),'Wat deed je vandaag?')
    result=HouseholdConversationContext(x.server).recent(turn,activity=True,local_date='2026-08-27')
    assert len(result['records'])==1
    record=result['records'][0]['actions']['records'][0]
    assert record['lifecycle']=='finished' and record['recorded_status']=='verified'
    assert record['activity_at']=='2026-08-27T00:00:01Z'
    assert 'Bureaulamp' in record['result']['fragment']
    assert record['result']['truncated'] is False


@pytest.mark.parametrize(('last_event','lifecycle','recorded'), [
    ('TOOL_REQUEST_ADMITTED','admitted','admitted_only'),
    ('TOOL_PREPARED','running','admitted_only'),
    ('TOOL_EXECUTED','executed_unconfirmed','admitted_only'),
    ('STATE_VERIFIED','finished','verified'),
    ('STATE_VERIFICATION_TIMED_OUT','finished','timed_out'),
])
def test_activity_lifecycle_comes_from_recorded_events(database, last_event, lifecycle, recorded):
    x=setup(database,'activity-lifecycle-'+last_event)
    old=exchange(x,x.service,'Doe een actie','Antwoord')
    timeline=PostgresOperationalTimeline(database,external_timeline_scope(old.tenant_id,old.installation_id,'fixture'))
    thread=PostgresExternalConversationTools._thread_id(old,'read_home_assistant_inventory',0)
    state=timeline.create_thread(thread_id=thread,checkpoint_id=thread+':admitted',
        occurred_at=x.clock[0],event_type='TOOL_REQUEST_ADMITTED',data={
            'action_call_id':'fixture','action_name':'read_home_assistant_inventory',
            'action_arguments':{},'receipt_output':{},'source_message_id':old.message_id,
            'conversation_id':old.conversation_id,'principal_id':old.principal_id})
    if last_event!='TOOL_REQUEST_ADMITTED':
        timeline.transition(thread_id=thread,expected_revision=state.revision,checkpoint_id=thread+':next',
            phase='fixture',status='waiting_for_proof',event_type=last_event,occurred_at=x.clock[0],
            data={'dispatch':{'accepted':True}} if last_event=='TOOL_EXECUTED' else {})
    turn=other(x).turn(str(uuid4()),'Wat deed je?')
    row=HouseholdConversationContext(x.server).recent(turn,activity=True)['records'][0]['actions']['records'][0]
    assert row['lifecycle']==lifecycle and row['recorded_status']==recorded
    assert row['result'] is None


def test_activity_result_fragments_are_bounded_and_exclude_raw_receipt_metadata(database):
    from eva.contracts.canonical_json import canonical_sha256
    x=setup(database,'activity-preview-budget')
    observation={'summary':'Bevestigd resultaat '+('x'*4000)}
    for index in range(6):
        old=exchange(x,x.service,'Lees '+str(index),'Antwoord')
        action(x,old,observation=observation,receipt_output={'private_configuration':'SECRET_METADATA'})
    turn=other(x).turn(str(uuid4()),'Wat deed je?')
    result=HouseholdConversationContext(x.server).recent(turn,activity=True)
    previews=[r['actions']['records'][0]['result'] for r in result['records']]
    assert sum(len(p['fragment']) for p in previews)==6000
    assert all(len(p['fragment'])<=1200 and p['truncated'] and p['untrusted'] for p in previews)
    assert all(p['source_sha256']==canonical_sha256(observation) for p in previews)
    assert 'SECRET_METADATA' not in str(result)


def test_failed_receipt_does_not_expose_unverified_observation(database):
    x=setup(database,'activity-preview-failed')
    old=exchange(x,x.service,'Lees','Antwoord')
    action(x,old,failed=True,observation={'unconfirmed':'MUST_NOT_APPEAR'})
    turn=other(x).turn(str(uuid4()),'Wat deed je?')
    result=HouseholdConversationContext(x.server).recent(turn,activity=True)
    assert result['records'][0]['actions']['records'][0]['result'] is None
    assert 'MUST_NOT_APPEAR' not in str(result)


def test_cross_midnight_action_after_private_confirmation_is_excluded(database, monkeypatch):
    from datetime import UTC, datetime, timedelta
    monkeypatch.setenv('EVA_CONVERSATION_TIMEZONE','UTC')
    x=setup(database,'activity-day-confirmation')
    x.clock[0]=datetime(2026,8,26,23,59,58,tzinfo=UTC)
    source=other(x)
    old=exchange(x,source,'Oorspronkelijk openbaar','Antwoord')
    action(x,old,completed_at=x.clock[0]+timedelta(seconds=2),observation={'private':'HIDDEN'})
    timeline=PostgresOperationalTimeline(database,x.server.store.scope)
    confirmed=x.clock[0]+timedelta(seconds=1)
    timeline.create_thread(thread_id='voice-identity:'+source.binding['session_id'],
        checkpoint_id='fixture-confirmed',occurred_at=confirmed,event_type='VOICE_IDENTITY_CONFIRMED',
        phase='confirmed',status='completed',data={'speaker_confirmed_at':confirmed.strftime('%Y-%m-%dT%H:%M:%SZ')})
    x.clock[0]+=timedelta(seconds=4)
    turn=other(x).turn(str(uuid4()),'Wat deed je vandaag?')
    result=HouseholdConversationContext(x.server).recent(turn,activity=True,local_date='2026-08-27')
    assert result['records']==[]


def test_activity_receipt_tampering_fails_before_content_is_returned(database):
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import operational_events, OperationalTimelineError
    x=setup(database,'activity-tampered-proof')
    old=exchange(x,x.service,'Lees','Antwoord');action(x,old,observation={'summary':'Original'})
    thread=PostgresExternalConversationTools._thread_id(old,'read_home_assistant_inventory',0)
    with database.begin() as connection:
        payload=connection.execute(sa.select(operational_events.c.payload).where(
            operational_events.c.thread_id==thread, operational_events.c.event_type=='STATE_VERIFIED')).scalar_one()
        payload['data']['proof']['evidence']['observation']={'summary':'FORGED'}
        # Corruption fixture in the disposable test database only. Ordinary
        # writers cannot mutate this append-only table at all.
        connection.exec_driver_sql('ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER')
        connection.execute(sa.update(operational_events).where(operational_events.c.thread_id==thread,
            operational_events.c.event_type=='STATE_VERIFIED').values(payload=payload))
        connection.exec_driver_sql('ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER')
    turn=other(x).turn(str(uuid4()),'Wat deed je?')
    with pytest.raises(OperationalTimelineError):
        HouseholdConversationContext(x.server).recent(turn,activity=True)
