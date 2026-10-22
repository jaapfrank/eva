"""Automatic faults adopt the existing native work lane without becoming user intent."""
from dataclasses import replace
import json
import pytest
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.self_repair import PostgresSelfRepair
from eva.application.conversation import ConversationTurn
from eva.application.self_repair import RepairObservation
from eva.bootstrap.native_initiative_work import admit_native_initiative
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_native_initiative_digital_work import native_digital


@pytest.fixture
def postgres_engine(database):
    # Dedicated disposable schema on the installed PostgreSQL major version.
    # This is not the separate frozen 4B PostgreSQL16 migration gate.
    return database


def prepare(fixture, *, stop_previous=True):
    f=fixture
    if stop_previous:f.control.stop(f.claim.intent_id,now=NOW)
    f.policy['self_repair']={'enabled':True,'authorization_source':'Synthetic standing owner repair authorization'}
    f.path.write_text(json.dumps(f.policy))
    service=f.service
    turn=ConversationTurn.private(f.control._tenant_id,f.control._installation_id,
        str(f.control._owner),'synthetic-private','synthetic-failed-request',
        'PRIVATE_USER_TEXT_NOT_REPAIR_INSTRUCTIONS',NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    journal=PostgresConversationJournal(f.control._engine,f.control._scope);journal.admit(turn)
    repair=PostgresSelfRepair(f.auth,service,clock=lambda:NOW)
    observation=RepairObservation('a'*64,(('application/example.py','example:1','b'*64),))
    return f,repair,turn,journal,observation


def test_one_durable_goal_across_repeated_failures_uses_real_native_claim(native_digital):
    f,repair,turn,journal,observation=prepare(native_digital)
    first=repair.observe(turn,observation)
    assert first['created'] and first['admitted']
    assert repair.observe(turn,observation)==dict(first,created=False,admitted=False)
    another=replace(turn,message_id='another-failed-request');journal.admit(another)
    repair.observe(another,observation)
    events=repair.timeline.events(first['incident_id'])
    assert len(events)==3 and events[-1].event_type=='SELF_REPAIR_EVIDENCE_ADDED'
    assert turn.text not in str(events)
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    assert claim.execution_mode=='autonomous_task'
    native=admit_native_initiative(f.auth,claim,f.service)
    work=f.store.load(native.work_id,**f.service._owner)
    assert work.spec.origin.kind=='eva_initiative'
    assert not work.spec.source.message_id.startswith('synthetic-')
    assert repair.timeline.load(first['incident_id']).phase=='repair_investigating'


def test_busy_lane_keeps_repair_queued_and_later_admits_once(native_digital):
    f,repair,turn,_,observation=prepare(native_digital,stop_previous=False)
    result=repair.observe(turn,observation)
    assert result['created'] and not result['admitted']
    assert repair.run_once()=='waiting'
    f.control.stop(f.claim.intent_id,now=NOW)
    assert repair.run_once()=='admitted'
    assert repair.run_once()=='idle'


def test_actual_runtime_failure_wrapper_commits_one_truthful_notice(native_digital):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.contracts.timestamps import parse_utc_timestamp
    f,repair,turn,journal,_=prepare(native_digital)
    def fail(*args):
        # Exercise a genuine packaged Eva error frame, without changing runtime
        # code, calling a model, or replaying an external action.
        parse_utc_timestamp('PRIVATE_INVALID_TIMESTAMP')
    runtime=SimpleNamespace(_self_repair=repair,_journal=lambda _:journal,
        _respond_admitted=fail,_cold_path=SimpleNamespace(foreground=nullcontext))
    reply=PostgresHindsightMemoryRuntime.respond(runtime,turn)
    assert reply.provider=='runtime:self-repair-admitted'
    assert 'probleempje' in reply.text and 'herstellen' in reply.text
    assert journal.committed_reply(turn)==reply
    assert PostgresHindsightMemoryRuntime.respond(runtime,turn)==reply
    assert len(f.control.page(now=NOW)['cards'])==2  # old stopped + one new repair
    assert 'PRIVATE_INVALID_TIMESTAMP' not in str(f.control.page(now=NOW))
    another=replace(turn,message_id='repeat-real-runtime-failure');journal.admit(another)
    with pytest.raises(ValueError):PostgresHindsightMemoryRuntime.respond(runtime,another)
    assert journal.committed_reply(another) is None


def test_stop_revocation_and_source_erasure_do_not_create_a_new_repair(native_digital):
    f,repair,turn,journal,observation=prepare(native_digital)
    journal.erase_message_content(principal_id=turn.principal_id,message_id=turn.message_id,
        operation_sha256='c'*64,erased_at=NOW)
    assert repair.observe(turn,observation) is None
    f.policy['self_repair']['enabled']=False
    f.path.write_text(json.dumps(f.policy))
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    with pytest.raises((ValueError,InitiativeMandateDenied)):repair.observe(turn,observation)


@pytest.mark.parametrize('event', ['stop', 'erase'])
def test_queued_source_stop_or_erasure_blocks_later_admission(native_digital,event):
    f,repair,turn,journal,observation=prepare(native_digital,stop_previous=False)
    result=repair.observe(turn,observation)
    assert not result['admitted']
    if event=='stop':journal.request_turn_stop(turn)
    else:journal.erase_message_content(principal_id=turn.principal_id,message_id=turn.message_id,
        operation_sha256='d'*64,erased_at=NOW)
    f.control.stop(f.claim.intent_id,now=NOW)
    assert repair.run_once()=='source_unavailable'
    assert repair.timeline.load(result['incident_id']).phase=='repair_source_unavailable'


def test_already_stopped_source_does_not_create_repair(native_digital):
    _,repair,turn,journal,observation=prepare(native_digital)
    journal.request_turn_stop(turn)
    assert repair.observe(turn,observation) is None


@pytest.mark.parametrize('event', ['stop', 'disable'])
def test_adopted_repair_rechecks_permission_and_source_before_native_work(native_digital,event):
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    f,repair,turn,journal,observation=prepare(native_digital)
    assert repair.observe(turn,observation)['admitted']
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    if event=='stop':journal.request_turn_stop(turn)
    else:
        f.policy['self_repair']['enabled']=False
        f.path.write_text(json.dumps(f.policy))
    with pytest.raises(InitiativeMandateDenied):admit_native_initiative(f.auth,claim,f.service)


def test_repair_never_inherits_connector_effect_permission(native_digital):
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    f,repair,turn,_,observation=prepare(native_digital)
    assert repair.observe(turn,observation)['admitted']
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    guard,_=f.auth.bind(claim)
    assert guard.action_allowed('use_connector_tool') is False
    # Even a direct connector dispatch cannot bypass the action filter.
    with pytest.raises(InitiativeMandateDenied,match='cannot repeat or dispatch'):
        guard.authorize_request(object(),phase='execute')


@pytest.mark.parametrize('event',['stop','erase','disable'])
def test_assessment_binding_requires_original_source_and_permission(native_digital,event):
    from eva.adapters.postgres.self_repair import current_repair_binding
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    f,repair,turn,journal,observation=prepare(native_digital)
    incident=repair.observe(turn,observation)['incident_id']
    binding=current_repair_binding(f.auth,incident,now=NOW)
    assert binding['observation']==observation.to_payload()
    assert turn.text not in str(binding)
    if event=='stop':f.control.stop(binding['intent_id'],now=NOW)
    elif event=='erase':journal.erase_message_content(principal_id=turn.principal_id,
        message_id=turn.message_id,operation_sha256='9'*64,erased_at=NOW)
    else:
        f.policy['self_repair']['enabled']=False;f.path.write_text(json.dumps(f.policy))
    with pytest.raises((ValueError,InitiativeMandateDenied)):
        current_repair_binding(f.auth,incident,now=NOW)


def test_completed_development_can_still_stop_pending_repair_without_install_capability(native_digital):
    from tests.postgres.test_native_initiative_digital_work import started,result
    from eva.adapters.postgres.self_repair import current_repair_binding
    f,repair,turn,_,observation=prepare(native_digital)
    incident=repair.observe(turn,observation)['incident_id']
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    admitted=admit_native_initiative(f.auth,claim,f.service)
    work=f.store.load(admitted.work_id,**f.service._owner)
    call=started(f,work)
    saved=f.store.record_success(call,result(call),now=NOW)
    before=f.auth.timeline.events(claim.thread_id)
    assert current_repair_binding(f.auth,incident,now=NOW)['intent_id']==claim.intent_id
    assert f.control.stop(claim.intent_id,now=NOW)['status']=='stopped'
    assert f.store.load(admitted.work_id,**f.service._owner)==saved
    assert f.auth.timeline.events(claim.thread_id)==before
    with pytest.raises(ValueError,match='development source was stopped'):
        current_repair_binding(f.auth,incident,now=NOW)


def test_mandate_change_while_waiting_for_initiative_lock_rolls_back_intake(native_digital):
    import sqlalchemy as sa
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    f,repair,turn,_,observation=prepare(native_digital)
    def change(conn,cursor,statement,parameters,context,executemany):
        if 'pg_advisory_xact_lock' in statement and any(
                isinstance(value,str) and value.startswith('eva-initiative:')
                for value in (parameters.values() if isinstance(parameters,dict) else parameters)):
            f.policy['self_repair']['enabled']=False
            f.path.write_text(json.dumps(f.policy))
    sa.event.listen(f.control._engine,'after_cursor_execute',change)
    try:
        with pytest.raises((ValueError,InitiativeMandateDenied)):repair.observe(turn,observation)
    finally:sa.event.remove(f.control._engine,'after_cursor_execute',change)


def test_earlier_mandate_does_not_starve_new_authorized_repair(native_digital):
    from eva.contracts.canonical_json import canonical_sha256
    f,repair,turn,journal,observation=prepare(native_digital,stop_previous=False)
    first=repair.observe(turn,observation)
    _,old=f.auth.policy()
    f.policy['self_repair']['authorization_source']+=' revised'
    f.path.write_text(json.dumps(f.policy))
    f.auth.admit_revision(old,canonical_sha256({k:v for k,v in f.policy.items() if k!='enabled'}))
    newer=replace(turn,message_id='new-authorized-fault');journal.admit(newer)
    second=repair.observe(newer,replace(observation,error_type_sha256='f'*64))
    assert second['created'] and not second['admitted']
    assert repair.run_once()=='mandate_changed'
    assert repair.timeline.load(first['incident_id']).phase=='repair_authority_changed'
    f.control.stop(f.claim.intent_id,now=NOW)
    assert repair.run_once()=='admitted'
    assert repair.timeline.load(second['incident_id']).phase=='repair_investigating'


def test_erasure_waits_for_admission_source_lock_and_blocks_next_native_step(native_digital,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from time import monotonic, sleep
    import sqlalchemy as sa
    from eva.adapters.postgres import self_repair as module
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    f,repair,turn,journal,observation=prepare(native_digital,stop_previous=False)
    repair.observe(turn,observation)
    f.control.stop(f.claim.intent_id,now=NOW)
    updating=Event();pid=[];pending=[];original=module.adopt_private_initiative
    def before_update(conn,cursor,statement,parameters,context,executemany):
        if (current_thread().name.startswith('repair-eraser') and 'erase_conversation_event_content' in statement):
            pid.append(conn.connection.driver_connection.info.backend_pid);updating.set()
    def erase():
        return journal.erase_message_content(principal_id=turn.principal_id,message_id=turn.message_id,
            operation_sha256='e'*64,erased_at=NOW)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='repair-eraser') as pool:
        def adopted(connection,*args,**kwargs):
            pending.append(pool.submit(erase));assert updating.wait(3)
            blocked=False;end=monotonic()+3
            while monotonic()<end:
                with f.control._engine.connect() as check:
                    blocked=bool(check.execute(sa.text('select cardinality(pg_blocking_pids(:pid))'),{'pid':pid[0]}).scalar_one())
                if blocked:break
                sleep(.01)
            assert blocked and not pending[0].done()
            return original(connection,*args,**kwargs)
        monkeypatch.setattr(module,'adopt_private_initiative',adopted)
        sa.event.listen(f.control._engine,'before_cursor_execute',before_update)
        try:
            assert repair.run_once()=='admitted'
            pending[0].result(timeout=3)
        finally:sa.event.remove(f.control._engine,'before_cursor_execute',before_update)
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    with pytest.raises(InitiativeMandateDenied):admit_native_initiative(f.auth,claim,f.service)


def test_source_lock_has_exact_scope_type_digest_and_no_mutation_privilege(native_digital):
    import sqlalchemy as sa
    from uuid import uuid4
    from eva.adapters.postgres.operational_timeline import operational_events as events
    from eva.adapters.postgres.scope import set_local_scope
    f,_,turn,journal,_=prepare(native_digital)
    with f.control._engine.begin() as connection:
        journal._scope(connection)
        rows=connection.execute(sa.select(events.c.event_id,events.c.event_type,
            events.c.payload_sha256).where(events.c.tenant_id==f.control._scope.tenant_id,
            events.c.installation_id==f.control._scope.installation_id)).all()
        ingress=next(row for row in rows if row.event_type=='INGRESS_COMMITTED')
        other=next(row for row in rows if row.event_type!='INGRESS_COMMITTED')
        def lock(event,digest):
            return connection.scalar(sa.text('SELECT eva_core.lock_conversation_source(:id,:digest)'),
                {'id':event,'digest':digest})
        assert lock(ingress.event_id,bytes(ingress.payload_sha256)) is True
        assert lock(ingress.event_id,b'0'*32) is False
        assert lock(other.event_id,bytes(other.payload_sha256)) is False
        assert lock(uuid4(),bytes(ingress.payload_sha256)) is False
        assert connection.scalar(sa.text("SELECT has_table_privilege(current_user,'eva_core.operational_events','UPDATE')")) is False
    with f.control._engine.begin() as connection:
        connection.exec_driver_sql('SET LOCAL ROLE eva_runtime')
        set_local_scope(connection,tenant_id=str(uuid4()),installation_id=str(uuid4()))
        assert connection.scalar(sa.text('SELECT eva_core.lock_conversation_source(:id,:digest)'),
            {'id':ingress.event_id,'digest':bytes(ingress.payload_sha256)}) is False
    for event,digest,scope in ((None,b'0'*32,True),(ingress.event_id,None,True),
                              (ingress.event_id,b'0',True),(ingress.event_id,b'0'*32,False)):
        with pytest.raises(sa.exc.DBAPIError) as rejected:
            with f.control._engine.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE eva_runtime')
                if scope:set_local_scope(connection,tenant_id=str(f.control._scope.tenant_id),
                                         installation_id=str(f.control._scope.installation_id))
                connection.execute(sa.text('SELECT eva_core.lock_conversation_source(:id,:digest)'),
                    {'id':event,'digest':digest})
        assert rejected.value.orig.sqlstate=='42501'
    with pytest.raises(sa.exc.DBAPIError) as rejected:
        with f.control._engine.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE eva_backup')
            connection.execute(sa.text('SELECT eva_core.lock_conversation_source(:id,:digest)'),
                {'id':ingress.event_id,'digest':bytes(ingress.payload_sha256)})
    assert rejected.value.orig.sqlstate=='42501'
    with f.control._engine.connect() as connection:
        # PUBLIC is represented by grantee=0, not a login role.
        assert connection.scalar(sa.text("SELECT count(*) FROM pg_proc p, LATERAL aclexplode(p.proacl) a WHERE p.oid='eva_core.lock_conversation_source(uuid,bytea)'::regprocedure AND a.grantee=0"))==0
