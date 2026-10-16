"""Current PostgreSQL work/source association, using a disposable migrated database."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path
import hashlib
import pytest
import sqlalchemy as sa
import os
from sqlalchemy.engine import make_url
from alembic.config import Config
from tests.postgres.test_people_memory_overview import _upgrade

@pytest.fixture(scope="module")
def context_engine():
    dsn=os.environ["EVA2_TEST_POSTGRES_DSN"]; url=make_url(dsn)
    assert url.database == "eva2_4b_disposable_e23_20261016"
    admin=sa.create_engine(url.set(database="postgres"),isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM pg_database WHERE datname=:name"),{"name":url.database})==0
        conn.exec_driver_sql('CREATE DATABASE "eva2_4b_disposable_e23_20261016"')
    engine=sa.create_engine(dsn)
    config=Config(str(Path(__file__).resolve().parents[3]/'deploy/alembic.ini'))
    config.set_main_option('sqlalchemy.url',dsn.replace('%','%%'));_upgrade(engine,config)
    try:yield engine
    finally:
        engine.dispose()
        # Current work cannot be downgraded to the old episode schema; remove
        # only this explicitly named disposable test database instead.
        with admin.connect() as conn:conn.exec_driver_sql('DROP DATABASE "eva2_4b_disposable_e23_20261016"')
        admin.dispose()
from tests.postgres.test_private_work_return import setup, start, NOW
from eva.bootstrap.dashboard_work_context import conversation_jobs, work_context
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationConflict, GroundedReply
from eva.application.delegated_work_delivery import PrivateWorkDestination
from tests.postgres.test_delegated_work_store import principal_bindings


def runtime(service, turn):
    return SimpleNamespace(_delegated_work_owner_service=service,
        _runtime_tenant_id=turn.tenant_id, _runtime_installation_id=turn.installation_id,
        _dashboard_owner_principal_id=service.owner_principal_id,
        _dashboard_principal_binding_id=service.principal_binding_id)


def digital(service, turn):
    file=Path(__file__).resolve().parents[3]/'src/eva/bootstrap/delegated_digital_runner.py'
    service.digital_profile=DelegatedDigitalProfile('model:controlled','d'*64,'e'*64,
        ('home_assistant_request',), (('bootstrap/delegated_digital_runner.py',hashlib.sha256(file.read_bytes()).hexdigest()),))
    journal=PostgresConversationJournal(service.store._engine,service.conversation_scope);journal.admit(turn)
    reply=GroundedReply(conversation_id=turn.conversation_id,message_id=turn.message_id,
        text='Ik werk je vraag uit.',generated_at=turn.occurred_at,provider='controlled',evidence_memory_ids=())
    journal.commit_reply(turn,reply)
    work=service.execute('digital',turn,task='Controleer beschikbare gegevens',request_key='digital')['work']
    return work['work_id'],journal


def test_current_conversation_selection_before_card_limit_and_private_owner_scope(context_engine):
    service,turn,clock=setup(context_engine,'context-filter',voice=False);r=runtime(service,turn)
    own=start(service,turn)
    other=replace(turn,conversation_id='voice:unrelated')
    for i in range(22):start(service,other,key='other-'+str(i))
    view=conversation_jobs(r)
    assert [w['work_id'] for w in view['jobs']]==[own.work_id]
    assert view['conversation_id']==turn.conversation_id and not view['truncated']
    another,otherturn,_=setup(context_engine,'context-other-owner',voice=False)
    with pytest.raises(Exception):work_context(runtime(another,otherturn),own.work_id)


def test_digital_source_reply_resume_and_erasure_are_exact(context_engine):
    service,turn,clock=setup(context_engine,'context-digital',voice=False);r=runtime(service,turn)
    wid,journal=digital(service,turn)
    view=work_context(r,wid);assert view['origin']['input']==turn.text
    assert view['origin']['message_id']==turn.message_id and view['origin']['reply']=='Ik werk je vraag uit.'
    assert conversation_jobs(r)['jobs'][0]['origin_message_id']==turn.message_id
    service.execute('stop',turn,work_id=wid,request_key='stop')
    clock[0]+=timedelta(seconds=1)
    resumed=service.execute('resume',turn,work_id=wid,request_key='resume')['work']['work_id']
    assert resumed!=wid and work_context(r,resumed)['origin']['root_work_id']==wid
    assert work_context(r,resumed)['origin']['message_id']==turn.message_id
    journal.erase_message_content(principal_id=turn.principal_id,message_id=turn.message_id,operation_sha256="a"*64,erased_at=clock[0])
    assert work_context(r,resumed)['origin']['state']=='unavailable'
    assert service.store.load(resumed,**service._owner).state=='admitted'


def test_legacy_telegram_sidecar_is_exact_and_website_origin_is_unknown(context_engine):
    service,turn,clock=setup(context_engine,'context-legacy',voice=False);r=runtime(service,turn)
    wid=start(service,turn).work_id
    assert work_context(r,wid)['origin']['state']=='unavailable'  # episode is not a turn
    turn=replace(turn,conversation_id='telegram:12345',message_id='telegram:12345:7')
    journal=PostgresConversationJournal(context_engine,service.conversation_scope);journal.admit(turn)
    destination=PrivateWorkDestination(service.owner_principal_id,service.principal_binding_id,12345,'12345',
        turn.tenant_id,turn.installation_id,turn.principal_id,turn.conversation_id,turn.message_id)
    service._delivery_destination_loader=lambda _:destination
    wid=start(service,turn,'telegram').work_id
    view=work_context(r,wid);assert view['origin']['channel']=='Telegram'
    assert view['origin']['message_id']=='telegram:12345:7' and view['origin']['input']==turn.text
    assert wid not in [w['work_id'] for w in conversation_jobs(r)['jobs']]


def test_origin_and_context_fail_after_current_owner_binding_revoked(context_engine):
    service,turn,_=setup(context_engine,'context-revoke',voice=False);r=runtime(service,turn)
    wid,_=digital(service,turn)
    with context_engine.begin() as conn:
        conn.execute(principal_bindings.update().where(principal_bindings.c.principal_binding_id==service.principal_binding_id).values(status='revoked'))
    with pytest.raises(Exception):work_context(r,wid)
    with pytest.raises(Exception):conversation_jobs(r)
