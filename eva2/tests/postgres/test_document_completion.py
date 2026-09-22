"""Document projections reuse real SQL delivery fences; transports are local fakes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver
from eva.adapters.postgres.operational_timeline import operational_checkpoints
from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.application.conversation import ConversationActionCall
from eva.application.durable_goals import GoalArtifact, GoalEvidence
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker
from eva.providers.document_completion import DocumentCompletionSource
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from eva.providers.document_task_input import DocumentTaskInput
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id, principal_bindings
from tests.postgres.test_people_memory_overview import NOW


@pytest.fixture
def completion(project, private_tmp_path, monkeypatch):
    p = project
    label = 'doc-return-' + uuid4().hex
    engine = p['timeline_engine']
    scope, _, binding = _seed_authority(engine, label)
    owner = _id(label, 'principal')
    bindings = [TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(owner), ('42',), str(binding)),))]
    at = [NOW]
    resolver = PostgresPrivateOwnerResolver(engine, tenant_id=p['settings'].tenant_id,
        installation_id=p['settings'].installation_id, owner=owner, binding=binding,
        authority_scope=scope, bindings_loader=lambda: bindings[0], clock=lambda: at[0])
    monkeypatch.setattr('eva.providers.private_owner_authority.configured_private_owner_resolver', lambda *a, **kw: resolver)
    mandate = private_tmp_path / 'completion-mandate.json'
    mandate.write_text(json.dumps({**p['mandate'], 'owner_principal_id': str(owner)})); mandate.chmod(0o600)
    pool = private_tmp_path / 'completion-pool.json'
    pool.write_text(json.dumps({'contract': 'EvaDocumentTaskPool/v2', 'slots': [{'slot_id': 'legacy', 'tool_configuration_sha256': 'a'*64}], 'dynamic_tool_configuration_sha256': 'b'*64})); pool.chmod(0o600)
    settings = replace(p['settings'], mandate_file=str(mandate), pool_file=str(pool), plan_file='', definition_file='')
    adapter = DocumentGoalActionsProviderFactory.build(settings, engine).external_tools[0]._provider
    source = DocumentCompletionSource(store=adapter.store, authority=adapter.authority,
        read_pool=adapter.read_pool, load_source_turn=adapter.load_source_turn)
    work = PostgresDelegatedWorkStore(engine, scope)
    service = DelegatedWorkOwnerService(work, owner_principal_id=owner, principal_binding_id=binding,
        conversation_scope=adapter.authority.scope, clock=lambda: at[0])
    service.document_completion_source = source
    delivery = PostgresDelegatedWorkDeliveryStore(work, bindings_loader=lambda: bindings[0],
        document_source=source, clock=lambda: at[0])
    def start(channel='telegram', *, key=None, historical=False, content='Second private document'):
        key = key or uuid4().hex
        conversation = {'telegram': 'telegram:42', 'dashboard': 'dashboard:'+str(binding), 'voice': 'voice:fixed-session'}[channel]
        turn = p['turn'](key, principal_id='telegram:42' if channel=='telegram' else str(owner),
                         conversation_id=conversation, occurred_at=at[0].isoformat().replace('+00:00','Z'))
        if historical:
            item = adapter.pool.claim(adapter.store, task=DocumentTaskInput('Historical', 'First', content),
                turn=turn, call_id=key, authority_sha256=adapter.authority.configuration_sha256,
                tenant_id=str(adapter.authority.scope.tenant_id), installation_id=str(adapter.authority.scope.installation_id), owner_principal_id=str(owner))
            from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
            plan = DocumentGoalPlan.from_json(json.dumps(item['plan']))
            goal = PreparedDocumentGoal.from_json(json.dumps(item['definition']), plan).goal
            adapter.store.admit(goal); adapter.store.bind_document_plan(goal, plan.sha256, adapter.authority.configuration_sha256)
            adapter.store.schedule_wake(goal)
        else:
            bundle = DocumentGoalActionsProviderFactory.build(settings, engine)
            router = PostgresConversationActions(engine, external_tools=bundle.external_tools)
            args = {'title':'Private documents', 'first_document':'First private document', 'second_document':content}
            result = router.execute(turn, ConversationActionCall(key, 'start_document_project', args)).output['result']
            item = adapter.pool.find(adapter.store, result['goal_id'].removeprefix('document-task:'))
        return turn, item
    def finish(item, *, terminal=True, invalid=False):
        from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
        plan = DocumentGoalPlan.from_json(json.dumps(item['plan']))
        goal = PreparedDocumentGoal.from_json(json.dumps(item['definition']), plan).goal
        first = GoalArtifact('document:'+plan.step(1).operation_id, plan.first_content, 'text/plain', at[0])
        final = GoalArtifact('document:'+plan.step(2, first).operation_id, 'incorrect artifact' if invalid else plan.final_content(first), 'text/plain', at[0])
        store = adapter.store
        store.record_wake(goal, GoalEvidence(goal.wake.provider, goal.wake.resource, {goal.wake.field:goal.wake.expected_value}, at[0], 'projection-only'))
        store.prepare_effect(goal, goal.primary_instruction); store.record_effect(goal, first)
        store.prepare_followup(goal); store.record_followup(goal, final)
        if terminal:
            store.prepare_delivery(goal, final); store.record_delivery(goal, final, 'accepted', None)
        return goal, final
    return locals()


def test_new_binding_replay_and_historical_claim_never_backfills(completion):
    c = completion
    turn, old = c['start'](historical=True)
    c['finish'](old)
    assert c['source'].load(old['plan']['goal_id']) is None
    task = DocumentTaskInput(**old['input'])
    replay = c['adapter'].pool.claim(c['adapter'].store, task=task, turn=turn, call_id=turn.message_id,
        authority_sha256=c['adapter'].authority.configuration_sha256,
        tenant_id=str(c['adapter'].authority.scope.tenant_id), installation_id=str(c['adapter'].authority.scope.installation_id),
        owner_principal_id=str(c['owner']), return_binding_factory=lambda: pytest.fail('historical binding must not be recomputed'))
    assert replay == old and 'return_binding' not in replay
    _, item = c['start']()
    assert item['return_binding']['source'] == item['source']
    assert c['source'].load(item['plan']['goal_id']) is None
    c['finish'](item, terminal=False)
    assert c['source'].load(item['plan']['goal_id']) is None
    assert c['delivery'].claim_next(now=c['at'][0]) is None


def test_telegram_documents_claim_once_restart_ack_and_no_fake_research(completion):
    c = completion
    _, item = c['start'](); c['finish'](item)
    goal_id = item['plan']['goal_id']
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: c['delivery'].claim_next(now=c['at'][0]), range(2)))
    claim, = [value for value in claims if value is not None]
    result = c['delivery'].authorize_delivery(goal_id, **c['service']._owner)
    assert 'First private document' in result.output and 'Second private document' in result.output
    assert claim.operation_id == result.operation_id
    restarted = PostgresDelegatedWorkDeliveryStore(c['work'], bindings_loader=lambda:c['bindings'][0], document_source=c['source'], clock=lambda:c['at'][0])
    assert restarted.acknowledge(claim, '42:7', now=c['at'][0]) == 'acknowledged'
    assert restarted.claim_next(now=c['at'][0]) is None
    with c['engine'].begin() as connection:
        c['work']._scope(connection)
        assert connection.scalar(sa.select(sa.func.count()).select_from(delegated_work_runs).where(delegated_work_runs.c.tenant_id==c['scope'].tenant_id)) == 0
    assert c['adapter'].store.state(goal_id,str(c['owner'])).delivery_status == 'accepted'


@pytest.mark.parametrize('change', ['pairing', 'binding', 'stop', 'mandate'])
def test_current_authority_changes_block_before_claim_and_between_parts(completion, change):
    c = completion
    turn, item = c['start'](content='x'*7000); c['finish'](item)
    sent = []
    def revoke():
        if change == 'pairing':
            c['bindings'][0] = TelegramPrincipalBindings((TelegramPrincipalBinding(99, str(c['owner']), ('99',), str(c['binding'])),))
            assert c['bindings'][0].resolve(99, '99') == str(c['owner'])
        elif change == 'binding':
            with c['engine'].begin() as connection:
                connection.execute(sa.update(principal_bindings).where(principal_bindings.c.principal_binding_id==c['binding']).values(status='revoked'))
        elif change == 'stop':
            c['p']['journal'].request_turn_stop(turn)
        else:
            c['mandate'].write_text(json.dumps({**c['p']['mandate'], 'owner_principal_id':str(c['owner']), 'enabled':False}))
    class Transport:
        def deliver_text(self, message, *, expected_chat_id):
            assert expected_chat_id == '42'
            sent.append(message); revoke(); return '42:1'
    worker = DelegatedWorkDeliveryWorker(c['delivery'], Transport(), clock=lambda:c['at'][0])
    assert worker.run_once() == 'ambiguous'
    assert len(sent)==1 and sent[0].startswith('Documentproject afgerond')
    assert c['delivery'].claim_next(now=c['at'][0]+timedelta(minutes=5)) is None


def test_crash_claim_expires_ambiguous_without_resend(completion):
    c = completion
    _, item = c['start'](); c['finish'](item)
    claim = c['delivery'].claim_next(now=c['at'][0],lease_seconds=1)
    assert claim is not None
    assert c['delivery'].claim_next(now=c['at'][0]+timedelta(seconds=2)) is None
    with c['engine'].begin() as connection:
        c['work']._scope(connection)
        assert connection.scalar(sa.select(operational_checkpoints.c.phase).where(
            operational_checkpoints.c.tenant_id==c['scope'].tenant_id,
            operational_checkpoints.c.thread_id==c['delivery']._delivery_thread(claim.work_id))) == 'delivery_ambiguous'


@pytest.mark.parametrize('channel', ['dashboard','voice'])
def test_original_session_only_document_return_and_ack(completion, channel):
    c = completion
    turn,item=c['start'](channel)
    if channel=='voice':
        c['service'].private_return(turn,'open')
    c['finish'](item)
    assert c['delivery'].claim_next(now=c['at'][0]) is None
    result=c['service'].private_return(turn,'poll')['result']
    assert result['work_id']==item['plan']['goal_id']
    identity={k:result[k] for k in ['work_id','operation_id']}
    assert c['service'].private_return(turn,'authorize',**identity)['output_sha256']==result['output_sha256']
    assert c['service'].private_return(turn,'ack',**identity)['status']=='acknowledged'
    assert c['service'].private_return(turn,'poll')['result'] is None
    other=replace(turn,conversation_id='voice:other-session')
    c['service'].private_return(other,'open')
    assert c['service'].private_return(other,'poll')['result'] is None
    with pytest.raises(ValueError):c['service'].private_return(other,'authorize',**identity)


@pytest.mark.parametrize('ending', ['close', 'expiry', 'crash'])
def test_document_voice_session_loss_never_replays(completion, ending):
    c = completion
    turn, item = c['start']('voice')
    c['service'].private_return(turn, 'open'); c['finish'](item)
    if ending == 'crash':
        result = c['service'].private_return(turn, 'poll')['result']
        assert result
        assert c['service'].private_return(turn, 'poll')['result'] is None
    elif ending == 'close':
        c['service'].private_return(turn, 'close')
        with pytest.raises(ValueError): c['service'].private_return(turn, 'poll')
    else:
        c['at'][0] += timedelta(seconds=20)
        with pytest.raises(ValueError): c['service'].private_return(turn, 'poll')
    replacement = replace(turn, conversation_id='voice:replacement')
    c['service'].private_return(replacement, 'open')
    assert c['service'].private_return(replacement, 'poll')['result'] is None
    assert c['source'].load(item['plan']['goal_id']) is not None


def test_revoked_original_ingress_blocks_initial_claim_and_unknown_owner(completion):
    c = completion
    turn,item = c['start'](); c['finish'](item)
    c['p']['journal'].request_turn_stop(turn)
    assert c['delivery'].claim_next(now=c['at'][0]) is None
    from eva.application.memory_audience import MemoryAudience
    unknown=replace(turn, principal_id='unknown', audience=MemoryAudience.private_person(principal_id='unknown'),conversation_id='voice:unknown')
    with pytest.raises((ValueError,RuntimeError)):
        c['service'].private_return(unknown,'open')


def test_transport_uncertainty_preserves_local_result_without_retry(completion):
    c=completion
    _,item=c['start']();c['finish'](item)
    class Uncertain:
        calls=0
        def deliver_text(self,*args,**kwargs):
            self.calls+=1
            raise TimeoutError('synthetic transport uncertainty')
    transport=Uncertain()
    worker=DelegatedWorkDeliveryWorker(c['delivery'],transport,clock=lambda:c['at'][0])
    assert worker.run_once()=='ambiguous'
    assert worker.run_once()=='idle' and transport.calls==1
    assert c['source'].load(item['plan']['goal_id']) is not None


def test_terminal_state_without_exact_artifact_proof_is_not_delivered(completion):
    c=completion
    _,item=c['start']();c['finish'](item,invalid=True)
    with pytest.raises(ValueError,match='artifact binding'):
        c['source'].load(item['plan']['goal_id'])
    assert c['delivery'].claim_next(now=c['at'][0]) is None
