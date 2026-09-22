"""One existing background worker and real PostgreSQL; provider is an isolated fixture."""
import asyncio
from datetime import UTC, datetime, timedelta
import threading
from uuid import UUID

import pytest
from pydantic import ValidationError

from eva.adapters.postgres.delegated_work import DelegatedWorkConflict, PostgresDelegatedWorkStore
from eva.application.conversation import ConversationModelUsage, ConversationTurn
from eva.application.research import ResearchArtifact, ResearchSource
from eva.bootstrap.delegated_research import DelegatedResearchProfile, DelegatedResearchRunner
from eva.bootstrap.delegated_work_provider import MODEL_PROFILE_ID
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.delegated_work import DelegatedWorkSpec, DelegatedWorkGrant, load_delegated_work_spec
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from tests.postgres.test_delegated_work_store import delegated_engine, _seed_authority, _id, _admit

NOW = datetime(2026,8,26,12,tzinfo=UTC)
PUBLIC = dict(contract="PublicResearchRequest/v1", schema_version=1,
    question="Welke grenzen beschrijft de HTTP specificatie?", title="HTTP grenzen",
    options=["HTTP"], research_mode="topic", public_review="public_unlinked")

class Research:
    def __init__(self):
        self.calls = 0
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
    def research_configuration_sha256(self): return "a" * 64
    def research_request_payload(self, **kwargs): return kwargs | {"options":list(kwargs["options"])}
    def research_current_options(self, **kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(10)
        return ResearchArtifact(kwargs['title'], kwargs['question'], 'A bounded fixture finding.',
            (ResearchSource('HTTP specification', 'https://www.rfc-editor.org/rfc/rfc9110'),),
            kwargs['researched_at'], 'fixture-research',
            ConversationModelUsage.measured(tool_calls=1,input_tokens=500,output_tokens=100))


def setup(engine,label):
    scope,episode,binding = _seed_authority(engine,label)
    store = PostgresDelegatedWorkStore(engine,scope)
    research = Research()
    profile = DelegatedResearchProfile(MODEL_PROFILE_ID,research.research_configuration_sha256())
    spec = profile.build_spec(research=PUBLIC,work_id='work:'+label, episode_id=str(episode),
        generation=1,parent_task_id='task:'+label,owner_principal_id=str(_id(label,'principal')),
        principal_binding_id=str(binding),task=PUBLIC['question'],created_at=NOW,deadline=NOW+timedelta(minutes=5))
    _admit(store,spec)
    runner = DelegatedResearchRunner(store,research,profile,clock=lambda:NOW)
    worker = DelegatedWorkWorker(store,runner,worker_id='worker:'+label,release=profile.release,clock=lambda:NOW)
    owner = dict(owner_principal_id=UUID(spec.owner_principal_id),principal_binding_id=binding)
    return store,research,profile,spec,worker,owner


def test_public_research_is_versioned_and_v1_stays_tool_free(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-contract')
    assert load_delegated_work_spec(spec.to_payload()) == spec
    with pytest.raises(ValidationError): DelegatedWorkSpec.model_validate(spec.to_payload())
    with pytest.raises(ValidationError): DelegatedWorkGrant.model_validate(spec.grant.to_payload())
    changed=spec.to_payload(); changed['grant']['context_refs']=['memory:other']
    with pytest.raises(ValidationError): load_delegated_work_spec(changed)
    changed=spec.to_payload(); changed['grant']['capability_ids']=['workspace.write']
    with pytest.raises(ValidationError): load_delegated_work_spec(changed)
    with pytest.raises(Exception):
        profile.build_spec(research=PUBLIC | {'question':'Mijn password: secret'},
            work_id='unused')
    assert research.calls==0


def test_background_research_keeps_foreground_available_and_persists_sources(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-finish')
    research.release.clear()
    async def run():
        pending=asyncio.create_task(worker.run_once())
        assert await asyncio.to_thread(research.started.wait,5)
        # The worker yields; another foreground operation can inspect and continue.
        assert store.list_owned(**owner)[0].state=='call_started'
        await asyncio.sleep(0)
        research.release.set()
        assert await pending=='succeeded'
    asyncio.run(run())
    saved=store.load(spec.work_id,**owner)
    assert 'https://www.rfc-editor.org/rfc/rfc9110' in saved.result.output
    assert saved.result.usage.tool_calls==1
    restarted=PostgresDelegatedWorkStore(store._engine,store.scope)
    assert restarted.load(spec.work_id,**owner).result==saved.result
    assert asyncio.run(worker.run_once())=='idle'
    assert research.calls==1
    wrong=owner | {'owner_principal_id':_id('other','principal')}
    with pytest.raises(DelegatedWorkConflict): restarted.load(spec.work_id,**wrong)


def test_stop_before_dispatch_and_during_research_do_not_start_another_step(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-stop')
    research.release.clear()
    async def run():
        pending=asyncio.create_task(worker.run_once())
        assert await asyncio.to_thread(research.started.wait,5)
        store.request_stop(spec.work_id,**owner,now=NOW)
        research.release.set()
        assert await pending=='stopped'
    asyncio.run(run())
    assert store.load(spec.work_id,**owner).result is None
    assert asyncio.run(worker.run_once())=='idle'
    assert research.calls==1
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-prestop')
    store.request_stop(spec.work_id,**owner,now=NOW)
    assert asyncio.run(worker.run_once())=='idle'
    assert research.calls==0


def test_admission_is_explicit_owner_scoped_and_idempotent(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-admit')
    service=DelegatedWorkOwnerService(store,**owner,research_profile=profile,clock=lambda:NOW)
    turn=ConversationTurn.private('tenant:research-admit','installation:research-admit',
        str(owner['owner_principal_id']),'conversation','message',PUBLIC['question'],NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    first=service.execute('research',turn,research=PUBLIC,request_key='admission')
    second=service.execute('research',turn,research=PUBLIC,request_key='admission')
    assert first['work']['work_id']==second['work']['work_id'] and second['created'] is False
    unavailable=DelegatedWorkOwnerService(store,**owner,clock=lambda:NOW)
    with pytest.raises(DelegatedWorkConflict): unavailable.execute('research',turn,research=PUBLIC,request_key='no')
    wrong=ConversationTurn.private(turn.tenant_id,turn.installation_id,'other',turn.conversation_id,
        turn.message_id,turn.text,turn.occurred_at)
    with pytest.raises(DelegatedWorkConflict): service.execute('research',wrong,research=PUBLIC,request_key='wrong')
    assert research.calls==0


def test_crash_after_call_fence_is_not_replayed(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-crash')
    assert asyncio.run(worker.run_once(fault_after="after_call_started"))=='crashed_after_call'
    assert asyncio.run(worker.run_once())=='idle'
    assert research.calls==0
    assert store.load(spec.work_id,**owner).state=='call_started'


def test_revoked_binding_after_claim_prevents_tool_dispatch(delegated_engine):
    import sqlalchemy as sa
    from tests.postgres.test_delegated_work_store import principal_bindings
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-revoke')
    original=store.start_call
    def revoke(claim,**kwargs):
        call=original(claim,**kwargs)
        with store._engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE eva_core.principal_bindings DISABLE TRIGGER ALL')
            try:
                connection.execute(sa.update(principal_bindings).where(
                    principal_bindings.c.principal_binding_id==owner['principal_binding_id']
                ).values(status='revoked'))
            finally:
                connection.exec_driver_sql('ALTER TABLE eva_core.principal_bindings ENABLE TRIGGER ALL')
        return call
    store.start_call=revoke
    assert asyncio.run(worker.run_once())=='ambiguous'
    assert research.calls==0


def test_research_action_only_admits_work_and_existing_resume_preserves_v2(delegated_engine):
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    from eva.application.conversation import ConversationActionCall
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-action')
    service=DelegatedWorkOwnerService(store,**owner,research_profile=profile,clock=lambda:NOW)
    actions=PostgresDelegatedWorkActions(store._engine,service)
    turn=ConversationTurn.private('tenant:research-action','installation:research-action',
        str(owner['owner_principal_id']),'foreground','message',PUBLIC['question'],NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    call=ConversationActionCall('research-action','start_background_research',{'research':PUBLIC})
    result=actions.execute(turn,call)
    assert actions.execute(turn,call)==result
    work_id=result.output['operation_result']['work']['work_id']
    assert result.output['operation_result']['work']['state']=='admitted'
    assert research.calls==0
    service.execute('stop',turn,work_id=work_id,request_key='stop')
    resumed=service.execute('resume',turn,work_id=work_id,request_key='resume')
    current=store.load(resumed['work']['work_id'],**owner)
    assert current.spec.contract=='DelegatedResearchWork/v2' and current.spec.research.to_payload()==PUBLIC
    assert resumed['work']['generation']==2
    assert service.execute('resume',turn,work_id=work_id,request_key='resume')['created'] is False
    assert research.calls==0


def test_inaccurate_source_payload_never_becomes_a_success(delegated_engine):
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-invalid')
    original=research.research_current_options
    def wrong(**kwargs): return original(**(kwargs | {'question':'Another task'}))
    research.research_current_options=wrong
    assert asyncio.run(worker.run_once())=='ambiguous'
    assert store.load(spec.work_id,**owner).result is None
    assert research.calls==1
    assert asyncio.run(worker.run_once())=='idle'


def test_failed_research_preserves_measured_over_budget_usage(delegated_engine):
    from eva.adapters.model.contracts import ConversationModelError
    store,research,profile,spec,worker,owner=setup(delegated_engine,'research-budget')
    def over_budget(**kwargs):
        research.calls+=1
        raise ConversationModelError('provider exceeded output budget', model_usage=
            ConversationModelUsage.measured(tool_calls=2,input_tokens=9000,output_tokens=1300))
    research.research_current_options=over_budget
    assert asyncio.run(worker.run_once())=='ambiguous'
    saved=store.load(spec.work_id,**owner)
    assert saved.result is None and saved.usage.output_tokens==1300 and saved.usage.tool_calls==2
    assert asyncio.run(worker.run_once())=='idle' and research.calls==1
