"""Real PostgreSQL ingress, atomic slots and ordinary verified action route."""
from dataclasses import replace
import json
import pytest
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine


def test_two_tasks_no_plan_swap_replay_stop_and_exhaustion(project,private_tmp_path):
    p=project
    pool=private_tmp_path/'pool.json'
    pool.write_text(json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'first','tool_configuration_sha256':'a'*64},
        {'slot_id':'second','tool_configuration_sha256':'c'*64}]}));pool.chmod(0o600)
    settings=replace(p['settings'],pool_file=str(pool))
    def route():
        bundle=DocumentGoalActionsProviderFactory.build(settings,p['timeline_engine'])
        return PostgresConversationActions(p['timeline_engine'],external_tools=bundle.external_tools)
    def call(message,name,args):
        return route().execute(p['turn'](message),ConversationActionCall(message,name,args)).output
    args={'title':'Gezinsdag','first_document':'Beste familie, welkom!','second_document':'Zaterdag eten?'}
    first=call('task-one','start_document_project',args)
    assert first['status']=='verified'  and first['result']['project_status']=='waiting', first
    assert call('task-one','start_document_project',args)==first
    second=call('task-two','start_document_project',{**args,'title':'Ander feest'})
    assert second['result']['goal_id']!=first['result']['goal_id']
    claims=[p['store'].document_slot(s) for s in ('first','second')]
    assert claims[0]['plan']['first_path']!=claims[1]['plan']['first_path']
    assert all(c['plan']['second_content']=='Zaterdag eten?' for c in claims)
    assert all(c['plan']['contract']=='EvaDocumentGoalPlan/v3' for c in claims)
    # New route object/DB reads recover immutable status without a new host plan.
    assert call('status','get_document_project',{'goal_id':first['result']['goal_id']})['result']['project_status']=='waiting', first
    assert call('stop','stop_document_project',{'goal_id':first['result']['goal_id']})['result']['project_status']=='cancelled'
    blocked=call('task-three','start_document_project',args)
    assert blocked['result']['project_status']=='blocked'
    assert blocked['result']['reason']=='geen vrij projectslot'
    assert p['store'].document_slot('first')==claims[0]  # cancelled slot never recycled


def test_slot_claim_race_replay_and_input_conflict(project):
    from concurrent.futures import ThreadPoolExecutor
    from eva.providers.document_task_input import DocumentTaskInput,DocumentTaskPool,DocumentTaskSlot
    p=project
    pool=DocumentTaskPool((DocumentTaskSlot('race-a','a'*64),DocumentTaskSlot('race-b','b'*64)))
    task=DocumentTaskInput.from_arguments({'title':'T','first_document':'A','second_document':'B'})
    turn=p['turn']('race')
    kwargs=dict(store=p['store'],task=task,turn=turn,call_id='same',authority_sha256=p['authority'].configuration_sha256,
        tenant_id=str(p['authority'].scope.tenant_id),installation_id=str(p['authority'].scope.installation_id))
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts=list(executor.map(lambda _:pool.claim(**kwargs),range(2)))
    assert receipts[0]==receipts[1]
    assert p['store'].document_slot('race-b') is None
    with pytest.raises(ConversationConflict,match='replay'):
        pool.claim(**{**kwargs,'task':replace(task,second_document='changed')})


def test_owner_and_current_mandate_block_new_task(project,private_tmp_path):
    p=project
    pool=private_tmp_path/'limited-pool.json'
    pool.write_text(json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'limited','tool_configuration_sha256':'d'*64}]}));pool.chmod(0o600)
    bundle=DocumentGoalActionsProviderFactory.build(replace(p['settings'],pool_file=str(pool)),p['timeline_engine'])
    port=bundle.external_tools[0]
    args={'title':'T','first_document':'A','second_document':'B'}
    with pytest.raises(ConversationConflict):
        port.execute(p['turn']('other',principal_id='other'),ConversationActionCall('other','start_document_project',args))
    p['mandate_path'].write_text(json.dumps({**p['mandate'],'enabled':False}))
    with pytest.raises(ConversationConflict):
        port.execute(p['turn']('revoked'),ConversationActionCall('revoked','start_document_project',args))
    assert p['store'].document_slot('limited') is None


def test_pool_health_is_readonly_and_accepts_empty_slots(project,private_tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    p=project
    source=Path(__file__).resolve().parents[3]/'deploy/living-core/document-worker-health.py'
    spec=importlib.util.spec_from_file_location('task_health',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'check_process',lambda:None)  # process liveness separately proven
    pool=private_tmp_path/'health-pool'
    pool.write_text(json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'health-slot','tool_configuration_sha256':'e'*64}]}));pool.chmod(0o600)
    dsn=private_tmp_path/'health-dsn'
    dsn.write_text(p['timeline_engine'].url.render_as_string(hide_password=False));dsn.chmod(0o600)
    for key,value in {'EVA_DOCUMENT_TASK_POOL_FILE':str(pool),'EVA_DATABASE_DSN_FILE':str(dsn),
        'EVA_RUNTIME_TENANT_ID':p['mandate']['tenant_id'],'EVA_RUNTIME_INSTALLATION_ID':p['mandate']['installation_id']}.items():
        monkeypatch.setenv(key,value)
    module.check()
    assert p['store'].document_slot('health-slot') is None
    monkeypatch.setenv('EVA_DOCUMENT_GOAL_PLAN_FILE',p['settings'].plan_file)
    module.check()  # Both explicit legacy plan and pool stay in the readiness check.
    bad=private_tmp_path/'bad-legacy-plan';bad.write_text('{}');bad.chmod(0o600)
    monkeypatch.setenv('EVA_DOCUMENT_GOAL_PLAN_FILE',str(bad))
    with pytest.raises(RuntimeError):module.check()
    monkeypatch.setenv('EVA_DOCUMENT_GOAL_PLAN_FILE',p['settings'].plan_file)
    pool.write_text('{}')
    with pytest.raises(ConversationConflict):module.check()


def test_bootstrap_recovers_slot_claim_before_goal_admission(project,private_tmp_path,monkeypatch):
    import eva.bootstrap.document_goal_worker as bootstrap
    from eva.providers.document_task_input import DocumentTaskInput,DocumentTaskPool,DocumentTaskSlot
    from eva.providers.document_goal_plan import DocumentGoalPlan
    p=project
    raw=json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'unadmitted','tool_configuration_sha256':'f'*64}]})
    pool_file=private_tmp_path/'recovery-pool'
    pool_file.write_text(raw);pool_file.chmod(0o600)
    pool=DocumentTaskPool.from_json(raw)
    turn=p['turn']('claim-before-admission')
    task=DocumentTaskInput.from_arguments({'title':'T','first_document':'A','second_document':'B'})
    claim=pool.claim(p['store'],task=task,turn=turn,call_id='claim',
        authority_sha256=p['authority'].configuration_sha256,
        tenant_id=str(p['authority'].scope.tenant_id),installation_id=str(p['authority'].scope.installation_id))
    goal_id=claim['definition']['goal']['goal_id']
    assert p['store'].state(goal_id,'owner') is None
    monkeypatch.setenv('EVA_DOCUMENT_TASK_POOL_FILE',str(pool_file))
    monkeypatch.setenv('EVA_MCP_AUTONOMY_MANDATE_FILE',str(p['mandate_path']))
    monkeypatch.delenv('EVA_MCP_MANAGED_ENDPOINTS_FILE',raising=False)
    callbacks={}
    def capture(**kwargs):callbacks.update(kwargs);return 0
    monkeypatch.setattr(bootstrap,'run_loop',capture)
    plans=[]
    def capture_ports(**kwargs):
        # The real bootstrap has already reloaded the owner ingress and repaired SQL.
        plans.append(DocumentGoalPlan.from_json(kwargs['read_bound_plan']()))
        return object()  # No provider dispatch is necessary to prove admission recovery.
    monkeypatch.setattr(bootstrap,'build_document_goal_ports',capture_ports)
    assert bootstrap.main()==0
    selected=callbacks['select_goals'](p['store'],p['goal'].created_at)
    assert len(selected)==1 and selected[0].goal_id==goal_id
    callbacks['worker_factory'](p['timeline_engine'],p['store'],selected[0],p['mandate']['tenant_id'],p['mandate']['installation_id'])
    state=p['store'].state(goal_id,'owner')
    assert state is not None and state.status=='waiting'
    assert state.spec==selected[0] and plans[0].goal_sha256==state.spec.sha256
    assert p['store'].document_plan_binding(state.spec)==(plans[0].sha256,p['authority'].configuration_sha256)
    assert p['store'].document_slot('unadmitted')==claim
    assert not any(e.event_type=='GOAL_EFFECT_PREPARED' for e in p['store']._events(state.spec))
    callbacks['worker_factory'](p['timeline_engine'],p['store'],selected[0],p['mandate']['tenant_id'],p['mandate']['installation_id'])
    assert sum(e.event_type=='GOAL_ADMITTED' for e in p['store']._events(state.spec))==1


def test_completed_legacy_result_remains_available_with_pool(project,private_tmp_path):
    from tests.postgres.test_document_goal_actions import test_result_requires_complete_exact_artifacts_and_survives_factory_restart
    p=project
    # Existing stored result projection fixture, not a new physical execution claim.
    test_result_requires_complete_exact_artifacts_and_survives_factory_restart(p,p['timeline_engine'])
    pool=private_tmp_path/'coexist-pool'
    pool.write_text(json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'coexist','tool_configuration_sha256':'f'*64}]}));pool.chmod(0o600)
    bundle=DocumentGoalActionsProviderFactory.build(replace(p['settings'],pool_file=str(pool)),p['timeline_engine'])
    names={tool.name for tool in bundle.conversation_tools}
    assert {'start_document_project','get_prepared_document_project','stop_prepared_document_project'}<=names
    assert len(names)==7
    route=PostgresConversationActions(p['timeline_engine'],external_tools=bundle.external_tools)
    result=route.execute(p['turn']('legacy-result-with-pool'),ConversationActionCall('lookup','get_prepared_document_project',{})).output['result']
    assert result['project_status']=='completed'
    assert result['final_document']['content']=='Final: Draft\n'
    assert p['store'].document_slot('coexist') is None


def test_bootstrap_selects_legacy_and_pool_once_with_correct_plan_routes(project,private_tmp_path,monkeypatch):
    import eva.bootstrap.document_goal_worker as bootstrap
    from tests.postgres.test_document_goal_actions import call
    from eva.providers.document_task_input import DocumentTaskInput,DocumentTaskPool
    p=project
    call(p,'start','legacy-start')
    raw=json.dumps({'contract':'EvaDocumentTaskPool/v1','slots':[
        {'slot_id':'alongside','tool_configuration_sha256':'f'*64}]})
    poolfile=private_tmp_path/'alongside-pool';poolfile.write_text(raw);poolfile.chmod(0o600)
    pool=DocumentTaskPool.from_json(raw)
    turn=p['turn']('new-task')
    claim=pool.claim(p['store'],task=DocumentTaskInput.from_arguments({'title':'T','first_document':'A','second_document':'B'}),
        turn=turn,call_id='new',authority_sha256=p['authority'].configuration_sha256,
        tenant_id=str(p['authority'].scope.tenant_id),installation_id=str(p['authority'].scope.installation_id))
    monkeypatch.setenv('EVA_DOCUMENT_TASK_POOL_FILE',str(poolfile))
    monkeypatch.setenv('EVA_DOCUMENT_GOAL_PLAN_FILE',p['settings'].plan_file)
    monkeypatch.setenv('EVA_MCP_AUTONOMY_MANDATE_FILE',str(p['mandate_path']))
    monkeypatch.delenv('EVA_MCP_MANAGED_ENDPOINTS_FILE',raising=False)
    callbacks={}
    def capture(**kwargs):callbacks.update(kwargs);return 0
    monkeypatch.setattr(bootstrap,'run_loop',capture)
    routes=[]
    def capture_ports(**kwargs):
        routes.append((kwargs['goal'].goal_id,kwargs['read_bound_plan'] is not None,kwargs['plan_file']))
        if kwargs['read_bound_plan'] is not None:kwargs['read_bound_plan']()
        return object()  # No native dispatch; factory route and real SQL are under test.
    monkeypatch.setattr(bootstrap,'build_document_goal_ports',capture_ports)
    bootstrap.main()
    selected=callbacks['select_goals'](p['store'],p['goal'].created_at)
    assert len(selected)==2 and len({g.goal_id for g in selected})==2
    for goal in selected:
        callbacks['worker_factory'](p['timeline_engine'],p['store'],goal,p['mandate']['tenant_id'],p['mandate']['installation_id'])
    assert (p['goal'].goal_id,False,p['settings'].plan_file) in routes
    assert (claim['plan']['goal_id'],True,p['settings'].plan_file) in routes
    assert len(callbacks['select_goals'](p['store'],p['goal'].created_at))==2
    # An explicit host plan accidentally selecting the same pool goal fails closed.
    duplicate=private_tmp_path/'duplicate-plan';duplicate.write_text(json.dumps(claim['plan']));duplicate.chmod(0o600)
    monkeypatch.setenv('EVA_DOCUMENT_GOAL_PLAN_FILE',str(duplicate))
    bootstrap.main()
    with pytest.raises(RuntimeError,match='multiple plan bindings'):
        callbacks['select_goals'](p['store'],p['goal'].created_at)
