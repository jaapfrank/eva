"""Supplementary oracle admission on disposable native PostgreSQL work."""
from copy import deepcopy
import ast
from pathlib import Path

import pytest

from eva.adapters.postgres.self_repair import current_repair_binding
from eva.adapters.postgres.initiative_execution import execution_turn
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.bootstrap.runtime_repair_host import _work_metadata
from eva.bootstrap.native_initiative_work import admit_native_initiative
from tests.postgres.test_runtime_repair_assessment import (
    database, postgres_engine, native_digital, costless_native, setup, finish, NOW,
)


@pytest.fixture
def supplement(native_digital,tmp_path):
    f,turn,journal,incident,phase,builder=setup(native_digital,tmp_path)
    finish(f,builder)
    assert phase()['status']=='adopted'
    parent=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    work=finish(f,parent)
    binding=current_repair_binding(f.auth,incident,now=NOW)
    metadata=_work_metadata(f.auth,f.service,f.store,binding,work.spec.work_id,require_running=False)
    request={'action':'admit','incident_id':incident,'scope_sha256':binding['scope_sha256'],
        'failure':binding,'parent':metadata,'target_id':'8'*64,
        'sources':{x['path']:x['source_sha256'] for x in binding['observation']['frames']},
        'repair_scope':None,'blocked_sha256':'4'*64,'blocked_original_sha256':'3'*64,'supplement_sha256':'5'*64}
    namespace={'__name__':'trusted_test_rpc'}
    # Execute the host's exact fixed program against installed core adapters;
    # the runtime image intentionally does not contain the host controller.
    source=Path(__file__).resolve().parents[3]/'src/eva/operations/runtime_repair_development.py'
    tree=ast.parse(source.read_text())
    script=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='_ORACLE_SUPPLEMENT_RPC' for target in node.targets))
    exec(compile(script,'<fixed oracle supplementary rpc>','exec'),namespace)
    def invoke(value=None):
        return namespace['handle'](request if value is None else value,
            authority=f.auth,service=f.service,store=f.store,source_root=tmp_path/'source')
    return f,turn,journal,builder,parent,request,invoke


def test_one_completed_oracle_has_exactly_one_blind_native_successor(supplement):
    f,turn,_,_,parent,request,invoke=supplement
    assert invoke({**request,'action':'inspect'})['status']=='absent'
    created=invoke();assert created['status']=='adopted'
    assert invoke()['status']=='already_adopted'
    inspected=invoke({**request,'action':'inspect'})
    assert inspected['status']=='already_adopted' and inspected['intent_id']==created['intent_id']
    with pytest.raises(ValueError,match='another supplementary child'):
        invoke({**request,'supplement_sha256':'6'*64})
    claim=f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW)
    assert claim.intent_id==created['intent_id'] and claim.intent_id!=parent.intent_id
    receipt=admit_native_initiative(f.auth,claim,f.service)
    work=f.store.load(receipt.work_id,**f.service._owner)
    assert set(work.spec.grant.capability_ids)=={'write_programming_file','use_programming_tool'}
    assert 'original_behavior_oracle' in work.spec.task
    assert '/workspace/repair/diagnostics' in work.spec.task
    assert 'candidate_sha256' not in work.spec.task
    events=f.auth.timeline.events(claim.thread_id)
    data=events[0].payload['data']
    assert data['followup_source']['intent_id']==parent.intent_id
    assert data['repair_candidate_sha256'] is None
    assert data['repair_comparison_sha256'] is None
    assert data['repair_supplement_blocked_original_sha256']==request['blocked_original_sha256']
    assert data['followup_source']['classification']=='retained_completed_result_not_current_fact_or_instruction'
    # A durable parent STOP remains effective after native admission as well.
    PostgresConversationJournal(f.control._engine,f.control._scope).request_turn_stop(execution_turn(parent))
    with pytest.raises(Exception):invoke({**request,'action':'inspect'})
    assert f.store.claim_next(worker_id='synthetic-supplement',release=f.profile.release,now=NOW) is None


@pytest.mark.parametrize('change',['parent_stop','original_stop','source_erased','wrong_parent'])
def test_supplement_cannot_outlive_source_or_original_independent_parent(supplement,change):
    f,turn,journal,builder,parent,request,invoke=supplement
    if change=='parent_stop':
        PostgresConversationJournal(f.control._engine,f.control._scope).request_turn_stop(execution_turn(parent))
    elif change=='original_stop':f.control.stop(builder.intent_id,now=NOW)
    elif change=='source_erased':journal.erase_message_content(principal_id=turn.principal_id,
        message_id=turn.message_id,operation_sha256='7'*64,erased_at=NOW)
    else:
        request['parent']=deepcopy(request['parent']);request['parent']['spec_sha256']='0'*64
    with pytest.raises(Exception):invoke()
    assert f.interests.claim_next(tenant_id=turn.tenant_id,installation_id=turn.installation_id,now=NOW) is None
