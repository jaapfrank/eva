"""Host completion protocol on disposable PostgreSQL; no image installation."""
import pytest

from eva.adapters.postgres.self_repair import current_repair_binding
from eva.bootstrap.native_initiative_work import admit_native_initiative
from eva.bootstrap.runtime_repair_host import handle_host_repair_request
from tests.postgres.test_runtime_repair_assessment import database, postgres_engine, native_digital, costless_native, setup, finish, NOW
from tests.postgres.test_native_initiative_digital_work import started,result


def rpc(f,incident,action,**arguments):
    scope=current_repair_binding(f.auth,incident,now=NOW)['scope_sha256']
    request={'action':action,'incident_id':incident,'scope_sha256':scope,**arguments}
    return handle_host_repair_request(request,authority=f.auth,service=f.service,store=f.store)


def test_host_metadata_matches_actual_native_work_and_programming_namespace(native_digital,tmp_path):
    from eva.adapters.postgres.programming_actions import ProgrammingActions
    from types import SimpleNamespace
    f,_,_,incident,_,builder=setup(native_digital,tmp_path)
    receipt=admit_native_initiative(f.auth,builder,f.service)
    work=f.store.load(receipt.work_id,**f.service._owner)
    with pytest.raises(ValueError,match='running repair work'):
        rpc(f,incident,'work_metadata',task_id=work.spec.work_id,require_running=True)
    call=started(f,work)
    meta=rpc(f,incident,'work_metadata',task_id=work.spec.work_id,require_running=True)
    guard,source=f.auth.bind_work(work.spec)
    programming=ProgrammingActions(f.store._engine,work.spec,source,lambda _:None,
        lambda _:None,lambda:NOW,client=SimpleNamespace())
    assert meta['workspace_key']==programming.workspace_key and meta['session_key']==programming.session_key
    assert meta['role'] is None and meta['spec_sha256']==work.spec.sha256
    assert meta['work_succeeded'] is False and meta['usage_measured'] is False
    f.store.record_success(call,result(call),now=NOW)
    completed=rpc(f,incident,'work_metadata',task_id=work.spec.work_id,require_running=False)
    assert completed['work_succeeded'] and completed['usage_measured']
    assert 'output' not in completed and 'task' not in completed


def test_host_history_replay_does_not_reopen_a_verified_completion(native_digital,tmp_path):
    f,turn,journal,incident,phase,builder=setup(native_digital,tmp_path)
    finish(f,builder)
    phase('independent_review',candidate_sha256='7'*64,comparison_sha256='8'*64)
    review=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    # This is a synthetic host proof to exercise the protocol, NOT a claim
    # that the production host installed or independently accepted a repair.
    proof={'target_id':'8'*64,'candidate_sha256':'7'*64,
        'comparison_sha256':'8'*64,'independent_review_sha256':'6'*64,'application_receipt_sha256':'5'*64,
        'installed_behavior_sha256':'4'*64,'review_task_id':'work:eva-initiative:'+review.intent_id.removeprefix('personal-interest:')}
    admitted=admit_native_initiative(f.auth,review,f.service)
    review_work=f.store.load(admitted.work_id,**f.service._owner)
    with pytest.raises(ValueError,match='exact separate repair review'):
        rpc(f,incident,'close_verified',proof=proof)
    call=started(f,review_work);f.store.record_success(call,result(call),now=NOW)
    scope=current_repair_binding(f.auth,incident,now=NOW)['scope_sha256']
    assert rpc(f,incident,'close_verified',proof=proof)['state']=='repaired'
    journal.erase_message_content(principal_id=turn.principal_id,message_id=turn.message_id,
        operation_sha256='3'*64,erased_at=NOW)
    request={'action':'close_verified','incident_id':incident,'scope_sha256':scope,'proof':proof}
    replay=handle_host_repair_request(request,authority=f.auth,service=f.service,store=f.store)
    assert replay['replayed'] is True
    card=next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id']==builder.intent_id)
    assert card['status']=='done' and card['can_stop'] is False
    with pytest.raises(ValueError,match='replay differs'):
        handle_host_repair_request({**request,'proof':{**proof,'candidate_sha256':'2'*64}},
            authority=f.auth,service=f.service,store=f.store)


def test_completion_rechecks_review_stop_inside_closing_transaction(native_digital,tmp_path,monkeypatch):
    import eva.bootstrap.runtime_repair_host as host
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    f,_,_,incident,phase,builder=setup(native_digital,tmp_path)
    finish(f,builder)
    phase('independent_review',candidate_sha256='7'*64,comparison_sha256='8'*64)
    review=f.interests.claim_next(tenant_id=f.auth.owner._tenant_id,
        installation_id=f.auth.owner._installation_id,now=NOW)
    admitted=admit_native_initiative(f.auth,review,f.service)
    work=f.store.load(admitted.work_id,**f.service._owner)
    call=started(f,work);f.store.record_success(call,result(call),now=NOW)
    proof={'target_id':'8'*64,'candidate_sha256':'7'*64,'comparison_sha256':'8'*64,
        'independent_review_sha256':'6'*64,'application_receipt_sha256':'5'*64,
        'installed_behavior_sha256':'4'*64,'review_task_id':work.spec.work_id}
    original=host._work_metadata
    def stop_after_snapshot(*args,**kwargs):
        snapshot=original(*args,**kwargs)
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.adapters.postgres.initiative_execution import execution_turn
        turn=execution_turn(PostgresPersonalInterestStore._claim_from_events(
            f.auth.owner._tenant_id,f.auth.owner._installation_id,
            f.auth.timeline.events(work.spec.origin.thread_id),work.spec.origin.claim_revision))
        PostgresConversationJournal(f.auth.owner._engine,f.auth.owner._scope).request_turn_stop(turn)
        return snapshot
    monkeypatch.setattr(host,'_work_metadata',stop_after_snapshot)
    with pytest.raises(ValueError,match='review stopped before completion'):
        rpc(f,incident,'close_verified',proof=proof)
    assert rpc(f,incident,'status')['state']=='repair_investigating'
