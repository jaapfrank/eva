"""Learning projection uses owned receipts, without running an improvement."""
from datetime import timedelta
from types import SimpleNamespace
import hashlib
import json
import pytest
import sqlalchemy as sa
from tests.postgres.test_dashboard_work_context import context_engine, runtime, digital
from tests.postgres.test_private_work_return import setup, NOW
from tests.postgres.test_delegated_work_store import principal_bindings
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.bootstrap.dashboard_learning import initiative_history
from eva.contracts.canonical_json import canonical_sha256


def prepared(engine, label, monkeypatch):
    import eva.bootstrap.dashboard_learning as module
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW + timedelta(seconds=20)
    monkeypatch.setattr(module, 'datetime', Clock)
    service, turn, _ = setup(engine, label, voice=False)
    r = runtime(service, turn)
    owner = PostgresOwnerInitiatives(engine, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner=service.owner_principal_id,
        binding=service.principal_binding_id, authority_scope=service.store.scope)
    r._owner_initiatives = lambda: owner
    r._memory = SimpleNamespace(_bank_id=lambda *args: 'existing-bank',
        _recall_style_mirror=lambda bank, person: 'Owned derived style')
    return r, service, turn, owner


def event(owner, thread, name, data, seconds=0):
    PostgresOperationalTimeline(owner._engine, owner._scope).create_thread(
        thread_id=thread, checkpoint_id='checkpoint:'+thread, event_type=name,
        data=data, occurred_at=NOW + timedelta(seconds=seconds))


def test_reflection_and_later_context_are_same_owner_and_exact_digest(context_engine, monkeypatch):
    r, service, turn, owner = prepared(context_engine, 'learning-reflection', monkeypatch)
    principal = str(service.owner_principal_id)
    event(owner, 'own-reflection', 'MEMORY_REFLECTION_COMPLETED', {
        'principal_sha256':hashlib.sha256(principal.encode()).hexdigest(),
        'after_content_sha256':'a'*64, 'before_content_sha256':'b'*64, 'source_fact_count':3})
    event(owner, 'foreign-reflection', 'MEMORY_REFLECTION_COMPLETED', {
        'principal_sha256':hashlib.sha256(b'other-person').hexdigest(), 'after_content_sha256':'f'*64})
    event(owner, 'wrong-person-context', 'CONVERSATION_MODEL_MEMORY_CONTEXT', {
        'principal_id':'other-person', 'style_mirror_sha256':'a'*64}, 1)
    assert initiative_history(r)['learning']['reflections'][0]['later_context_at'] is None
    event(owner, 'own-context', 'CONVERSATION_MODEL_MEMORY_CONTEXT', {
        'principal_id':principal, 'style_mirror_sha256':'a'*64}, 2)
    result = initiative_history(r)['learning']['reflections']
    assert len(result)==1 and result[0]['changed'] and result[0]['later_context_at']
    assert 'geen bewezen gedragsverbetering' in result[0]['meaning']
    with context_engine.begin() as conn:
        conn.execute(principal_bindings.update().where(
            principal_bindings.c.principal_binding_id==service.principal_binding_id).values(status='revoked'))
    with pytest.raises(Exception): initiative_history(r)


def test_component_request_is_not_application_and_no_raw_patch_is_exposed(context_engine, monkeypatch):
    r, service, turn, owner = prepared(context_engine, 'learning-component', monkeypatch)
    wid, _ = digital(service, turn)
    spec = service.store.load(wid, **service._owner).spec
    for step, (name, result) in enumerate([
        ('compare_own_component_improvements', {'state':'baseline_retained'}),
        ('apply_reviewed_component_improvement', {'state':'completed','installed':True}),
        ('get_reviewed_component_application', {'state':'completed','installed':True}),
    ]):
        thread='component-draft:'+canonical_sha256({'execution':spec.execution_id,
            'source':spec.source.model_dump(mode='json'), 'step':step})
        event(owner, thread, 'COMPONENT_ACTION_RECORDED', {'name':name,
            'arguments':{'patch':'PRIVATE RAW PATCH'},
            'output':{'status':'verified','operation_result':result}}, step)
    result=initiative_history(r)
    items=result['learning']['components']
    assert len(items)==3
    assert next(x for x in items if x['stage']=='selection')['reference_retained']
    assert not next(x for x in items if x['stage']=='application_request')['applied']
    assert next(x for x in items if x['stage']=='application_status')['applied']
    assert 'PRIVATE RAW PATCH' not in json.dumps(result)
    other, _, _, _=prepared(context_engine, 'learning-component-other', monkeypatch)
    assert initiative_history(other)['learning']['components']==[]
