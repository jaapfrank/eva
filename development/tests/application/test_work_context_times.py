from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from eva.application.work_context import work_context_metadata
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions


def test_original_task_times_survive_metadata_and_conversation_projection():
    now=datetime(2026,10,16,14,tzinfo=timezone.utc)
    work=SimpleNamespace(spec=SimpleNamespace(work_id='work:one',created_at='2026-10-15T12:00:00Z'),
        title='Onderzoek',state='verified',stop_requested_at=None,
        updated_at=now-timedelta(hours=1),
        result=SimpleNamespace(completed_at='2026-10-16T10:00:00Z'))
    listing=work_context_metadata([work],now)
    service=SimpleNamespace(available_for=lambda turn:True,active_work_metadata=lambda turn:listing)
    actions=PostgresDelegatedWorkActions.__new__(PostgresDelegatedWorkActions)
    actions._service=service
    text='\n'.join(actions.active_context(SimpleNamespace(text='Wat heb je gedaan?',message_id='m')))
    for value in ['2026-10-15T12:00:00Z','2026-10-16T10:00:00Z','2026-10-16T13:00:00+00:00']:
        assert value in text
    assert 'list_delegated_work' in text and 'get_delegated_work' in text
    assert 'not proof of completion' in text


def test_failed_task_update_is_not_completion_and_old_work_not_recent():
    now=datetime(2026,10,16,14,tzinfo=timezone.utc)
    work=SimpleNamespace(spec=SimpleNamespace(work_id='work:failed',created_at='2026-10-14T12:00:00Z'),
        title='Failed',state='failed',stop_requested_at=None,updated_at=now-timedelta(days=2),result=None)
    value=work_context_metadata([work],now)['jobs'][0]
    assert value['completed_at'] is None
    assert not value['recent_outcome'] and not value['result_available']


def test_household_listing_retains_dates_without_result_content(monkeypatch):
    from contextlib import nullcontext
    from eva.bootstrap.household_work import HouseholdWorkService
    from eva.adapters.postgres import household_work_destination as destination
    monkeypatch.setattr(destination,'read_destination',lambda *args:None)
    monkeypatch.setattr(destination,'delivery_status',lambda *args:None)
    service=HouseholdWorkService.__new__(HouseholdWorkService)
    import eva.bootstrap.household_work as module
    monkeypatch.setattr(module,"PostgresConversationJournal",lambda *args:SimpleNamespace(
        admit_household_ingress=lambda turn,**kwargs:turn))
    service.server=SimpleNamespace(conversation_scope=None)
    service._delivery_only=False
    service.current=lambda *args:None
    service.validate_authority=lambda turn:None
    service.origin=lambda turn:None
    service.store=SimpleNamespace(_engine=SimpleNamespace(begin=lambda:nullcontext(None)),
        _scope=lambda connection:None,scope=None)
    work=SimpleNamespace(spec=SimpleNamespace(work_id='work:one',task='Question',generation=1,
        parent_task_id='conversation:one',created_at='2026-10-15T12:00:00Z'),
        title='Title',state='verified',error_code=None,stop_requested_at=None,usage=None,
        updated_at=datetime(2026,10,16,13,tzinfo=timezone.utc),
        result=SimpleNamespace(completed_at='2026-10-16T10:00:00Z',output='PRIVATE RESULT CONTENT',output_sha256='a'*64))
    service._native_work=lambda *args,**kwargs:[work]
    row=service.execute('list',None,request_key='test')['jobs'][0]
    assert row['created_at']=='2026-10-15T12:00:00Z'
    assert row['completed_at']=='2026-10-16T10:00:00Z'
    assert row['updated_at']=='2026-10-16T13:00:00+00:00'
    assert 'PRIVATE RESULT CONTENT' not in str(row) and 'result' not in row
