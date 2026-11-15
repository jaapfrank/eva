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
    card=next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id']==claim.intent_id)
    assert card['status']=='working' and card['can_stop'] is True
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


def test_superseded_queued_and_investigating_repairs_close_cleanly_without_preempting_submitted_handoff(native_digital, tmp_path):
    import hashlib
    from tests.postgres.test_delegated_work_store import _usage
    from tests.postgres.test_native_initiative_digital_work import started, result
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline

    f, _, turn, journal, _ = prepare(native_digital, stop_previous=False)
    f.profile = replace(f.profile, capability_ids=('write_programming_file', 'use_programming_tool',
        'use_connector_tool', 'draft_own_component_improvement', 'prepare_own_component_release'))
    f.service.digital_profile = f.profile
    f.policy['digital_work'] = {'worker_profile_release_sha256': f.profile.release.worker_profile_release_sha256,
        'capability_ids': list(f.profile.capability_ids)}
    f.path.write_text(json.dumps(f.policy))
    pkg = tmp_path / 'eva'
    (pkg / 'application').mkdir(parents=True)
    target_file = pkg / 'application' / 'example.py'
    v1 = b'def example():\n    return 1\n'
    target_file.write_bytes(v1)
    obs1 = RepairObservation('1' * 64, (('application/example.py', 'example:1', hashlib.sha256(v1).hexdigest()),))
    repair = PostgresSelfRepair(f.auth, f.service, clock=lambda: NOW, source_root=pkg)

    # 1. Queued repair whose source is upgraded before adoption closes as repair_superseded.
    queued = repair.observe(turn, obs1)
    assert queued['created'] and not queued['admitted']
    v2 = b'def example():\n    return 2\n'
    target_file.write_bytes(v2)
    f.control.stop(f.claim.intent_id, now=NOW)
    assert repair.run_once() == 'superseded'
    assert repair.timeline.load(queued['incident_id']).phase == 'repair_superseded'

    # 2. Adopted repair whose builder failed (initiative_blocked) reconciles to repair_superseded
    #    once a newer release upgrades the observed source, and owner card turns 'done'.
    turn2 = replace(turn, message_id='failed-builder-source')
    journal.admit(turn2)
    obs2 = RepairObservation('2' * 64, (('application/example.py', 'example:1', hashlib.sha256(v2).hexdigest()),))
    adopted = repair.observe(turn2, obs2)
    assert adopted['admitted']
    claim2 = f.interests.claim_next(tenant_id=turn.tenant_id, installation_id=turn.installation_id, now=NOW)
    admitted2 = admit_native_initiative(f.auth, claim2, f.service)
    call2 = started(f, f.store.load(admitted2.work_id, **f.service._owner))
    f.store.record_failure(call2, error_code='incomplete_work', error_fingerprint='3' * 64,
        ambiguous=False, usage=_usage(), now=NOW)
    card_before = next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id'] == claim2.intent_id)
    assert card_before['status'] == 'attention'
    v3 = b'def example():\n    return 3\n'
    target_file.write_bytes(v3)
    assert repair.run_once() == 'superseded'
    assert repair.timeline.load(adopted['incident_id']).phase == 'repair_superseded'
    card_after = next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id'] == claim2.intent_id)
    assert card_after['status'] == 'done' and card_after['can_stop'] is False

    # 3. Completed builder WITHOUT submit handoff reconciles cleanly to repair_superseded
    #    even alongside earlier owner initiatives, turning its owner card from 'working' to 'done'.
    turn3a = replace(turn, message_id='completed-nosubmit-source')
    journal.admit(turn3a)
    obs3a = RepairObservation('3' * 64, (('application/example.py', 'example:1', hashlib.sha256(v3).hexdigest()),))
    nosubmit = repair.observe(turn3a, obs3a)
    assert nosubmit['admitted']
    claim3a = f.interests.claim_next(tenant_id=turn.tenant_id, installation_id=turn.installation_id, now=NOW)
    admitted3a = admit_native_initiative(f.auth, claim3a, f.service)
    call3a = started(f, f.store.load(admitted3a.work_id, **f.service._owner))
    f.store.record_success(call3a, result(call3a), now=NOW)
    assert next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id'] == claim3a.intent_id)['status'] == 'working'
    v4 = b'def example():\n    return 4\n'
    target_file.write_bytes(v4)
    assert repair.run_once() == 'superseded'
    assert repair.timeline.load(nosubmit['incident_id']).phase == 'repair_superseded'
    card3a = next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id'] == claim3a.intent_id)
    assert card3a['status'] == 'done' and card3a['can_stop'] is False

    # 4. Completed builder WITH an admitted submit handoff in owner._scope stays
    #    repair_investigating so host close_verified is never preempted; once a child
    #    oracle fails and host records SELF_REPAIR_REMAINS_OPEN, a subsequent release
    #    closes the incident as repair_superseded and marks BOTH parent and child cards 'done'.
    from eva.adapters.postgres.self_repair import current_repair_binding
    from eva.bootstrap.runtime_repair_host import handle_host_repair_request
    from eva.bootstrap.runtime_repair_work import admit_repair_assessment
    turn3 = replace(turn, message_id='submitted-builder-source')
    journal.admit(turn3)
    obs3 = RepairObservation('4' * 64, (('application/example.py', 'example:1', hashlib.sha256(v4).hexdigest()),))
    submitted = repair.observe(turn3, obs3)
    assert submitted['admitted']
    claim3 = f.interests.claim_next(tenant_id=turn.tenant_id, installation_id=turn.installation_id, now=NOW)
    admitted3 = admit_native_initiative(f.auth, claim3, f.service)
    work3 = f.store.load(admitted3.work_id, **f.service._owner)
    call3 = started(f, work3)
    PostgresOperationalTimeline(f.control._engine, f.control._scope).create_thread(
        thread_id='component-draft:' + '5' * 64, checkpoint_id='component-draft:' + '5' * 64 + ':request',
        occurred_at=NOW, phase='repair_handoff_unconfirmed', status='active',
        event_type='RUNTIME_REPAIR_REQUEST_ADMITTED',
        data={'execution_id': work3.spec.execution_id, 'spec_sha256': work3.spec.sha256,
              'source_ingress_sha256': work3.spec.source_ingress_sha256,
              'request': {'action': 'submit', 'incident_id': submitted['incident_id']}})
    f.store.record_success(call3, result(call3), now=NOW)
    v5 = b'def example():\n    return 5\n'
    target_file.write_bytes(v5)
    assert repair.run_once() == 'idle'
    assert repair.timeline.load(submitted['incident_id']).phase == 'repair_investigating'

    # Admit and fail a child original_behavior_oracle, record SELF_REPAIR_REMAINS_OPEN,
    # then upgrade to v6 and verify both parent ('attention') and child ('attention') cards turn 'done'.
    target_file.write_bytes(v4)
    oracle = admit_repair_assessment(f.auth, f.service, incident_id=submitted['incident_id'],
        role='original_behavior_oracle', target_id='6' * 64,
        sources={'application/example.py': hashlib.sha256(v4).hexdigest()},
        source_root=pkg, candidate_sha256=None, comparison_sha256=None)
    oracle_claim = f.interests.claim_next(tenant_id=turn.tenant_id, installation_id=turn.installation_id, now=NOW)
    oracle_admitted = admit_native_initiative(f.auth, oracle_claim, f.service)
    oracle_call = started(f, f.store.load(oracle_admitted.work_id, **f.service._owner))
    f.store.record_failure(oracle_call, error_code='incomplete_work', error_fingerprint='7' * 64,
        ambiguous=False, usage=_usage(), now=NOW)
    scope_sha = current_repair_binding(f.auth, submitted['incident_id'], now=NOW)['scope_sha256']
    handle_host_repair_request({'action': 'record_waiting', 'incident_id': submitted['incident_id'],
        'scope_sha256': scope_sha, 'outcome': 'blocked', 'reason_code': 'oracle_failed',
        'handoff_sha256': '8' * 64}, authority=f.auth, service=f.service, store=f.store)
    cards_waiting = {c['intent_id']: c for c in f.control.page(now=NOW)['cards']}
    assert cards_waiting[claim3.intent_id]['status'] == 'attention'
    assert cards_waiting[oracle['intent_id']]['status'] == 'attention'
    target_file.write_bytes(b'def example():\n    return 6\n')
    assert repair.run_once() == 'superseded'
    assert repair.timeline.load(submitted['incident_id']).phase == 'repair_superseded'
    cards_done = {c['intent_id']: c for c in f.control.page(now=NOW)['cards']}
    assert cards_done[claim3.intent_id]['status'] == 'done' and cards_done[claim3.intent_id]['can_stop'] is False
    assert cards_done[oracle['intent_id']]['status'] == 'done' and cards_done[oracle['intent_id']]['can_stop'] is False
