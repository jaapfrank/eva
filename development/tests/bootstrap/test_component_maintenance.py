from types import SimpleNamespace
from datetime import UTC,datetime
import pytest
from eva.bootstrap.component_maintenance import maintenance_policy,observe_installed_maintenance,current_maintenance_origin

POLICY=dict(enabled=True,authorization_source='Current owner E4.19 authorization',packages=['psycopg','psycopg-binary'],target_version='3.3.6')

def test_requires_current_explicit_pair_policy():
    authority=SimpleNamespace(policy=lambda:({'component_maintenance':POLICY},'a'*64))
    assert maintenance_policy(authority)==(POLICY,'a'*64)
    authority.policy=lambda:({},'a'*64)
    with pytest.raises(ValueError,match='explicit'):maintenance_policy(authority)

def test_no_extra_package_or_implied_maintenance_authority():
    authority=SimpleNamespace(policy=lambda:({'component_maintenance':{**POLICY,'packages':['psycopg','other']}},'a'*64))
    with pytest.raises(ValueError):maintenance_policy(authority)

def test_real_observation_enters_existing_sensory_store_without_fabricated_task():
    calls=[];retained=[];owner=SimpleNamespace()
    authority=SimpleNamespace(policy=lambda:({'component_maintenance':POLICY},'a'*64),owner=owner,
        timeline=SimpleNamespace(create_thread=lambda **kw:calls.append(kw)))
    observation=dict(source_sha256='a'*64,inventory_sha256='b'*64,official_releases={'psycopg':'c'*64,'psycopg-binary':'d'*64},versions={'core':{'psycopg':'3.3.4','psycopg-binary':'3.3.4'}})
    store=SimpleNamespace(retain=lambda event:retained.append(event) or 'retained')
    result=observe_installed_maintenance(authority,observation,sensory_store=store,now=datetime.now(UTC))
    assert result['state']=='retained' and len(retained)==1
    assert retained[0].source=='component_maintenance'
    assert calls[0]['event_type']=='COMPONENT_MAINTENANCE_OBSERVED'
    assert 'repair_incident_id' not in str(calls) and 'PERSONAL_INTENT_ADOPTED' not in str(calls)

def test_no_forged_origin_or_untyped_admission():
    with pytest.raises(ValueError,match='typed'):current_maintenance_origin(None,{'contract':'SELF_REPAIR'})


# Real disposable PG18/schema0012 integration. No provider or production calls.
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_autonomous_self_repair import postgres_engine
from tests.postgres.test_owner_initiatives import costless_native,NOW
from tests.postgres.test_native_initiative_digital_work import native_digital,started,result

@pytest.fixture
def maintenance_native(native_digital,monkeypatch,request):
    import json
    from dataclasses import replace
    import eva.bootstrap.component_maintenance as maintenance
    f=native_digital
    caps=('write_programming_file','use_programming_tool','manage_own_component_update','use_connector_tool')
    if not getattr(request,'param',False):caps+=('apply_reviewed_component_improvement',)
    f.profile=replace(f.profile,capability_ids=caps)
    f.service.digital_profile=f.profile
    f.policy['digital_work']={'worker_profile_release_sha256':f.profile.release.worker_profile_release_sha256,'capability_ids':list(f.profile.capability_ids)}
    f.policy['component_maintenance']=POLICY
    f.path.write_text(json.dumps(f.policy))
    class FixedTime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(maintenance,'datetime',FixedTime)
    observation=dict(source_sha256='a'*64,inventory_sha256='b'*64,official_releases={'psycopg':'c'*64,'psycopg-binary':'d'*64},versions={'core':{'psycopg':'3.3.4','psycopg-binary':'3.3.4'}})
    maintenance.observe_installed_maintenance(f.auth,observation,sensory_store=f.sensory,now=NOW)
    from eva.bootstrap.native_initiative_work import admit_native_initiative
    admission=admit_native_initiative(f.auth,f.claim,f.service)
    work=f.store.load(admission.work_id,**f.service._owner);call=started(f,work)
    origin,_=maintenance.origin_from_current_work(f.auth,f.service,f.store,work.spec.work_id,'a'*64)
    if getattr(request,'param',False):
        # Real current native action/store path; only the HTTP transport is simulated.
        import eva.adapters.postgres.component_improvement_actions as actions
        from eva.application.conversation import ConversationActionCall
        observed=[]
        def describe(action,**arguments):
            observed.append(arguments)
            assert arguments['scope_sha256']==origin['scope_sha256']
            return {'state':'described','target_id':'1'*64,'accepted':False,'installed':False}
        evaluator=SimpleNamespace(reviewed_releases=(),runtime_repair_enabled=False,component_maintenance_enabled=True,maintenance=describe)
        monkeypatch.setattr(actions,'configured_component_evaluator',lambda:evaluator)
        guard,turn=f.auth.bind_work(work.spec)
        adapter=actions.ComponentImprovementActions(f.store._engine,work.spec,turn,lambda _:None,lambda _:None,lambda:NOW,repair_guard=guard)
        adapter.execute(turn,ConversationActionCall(call_id='maintenance-describe',name='manage_own_component_update',arguments={'action':'describe'}),step_index=0)
        assert len(observed)==1 and observed[0]['task_id']==work.spec.work_id
    f.store.record_success(call,result(call,'Only the builder proposal is ready'),now=NOW)
    return f,origin,work

def _rpc(f,origin,action,**arguments):
    from eva.bootstrap.component_maintenance import handle_host_maintenance_request
    return handle_host_maintenance_request({'action':action,'origin':origin,'scope_sha256':origin['scope_sha256'],**arguments},authority=f.auth,service=f.service,store=f.store)

def _finish_stage(f,origin,role,**arguments):
    from eva.bootstrap.component_maintenance import admit_maintenance_assessment
    from eva.bootstrap.native_initiative_work import admit_native_initiative
    value=admit_maintenance_assessment(f.auth,f.service,f.store,origin=origin,role=role,target_id='1'*64,**arguments)
    assert value['status']=='adopted'
    claim=f.interests.claim_next(tenant_id=f.control._tenant_id,installation_id=f.control._installation_id,now=NOW)
    admitted=admit_native_initiative(f.auth,claim,f.service)
    work=f.store.load(admitted.work_id,**f.service._owner)
    assert 'use_connector_tool' not in work.spec.grant.capability_ids
    assert 'manage_own_component_update' in work.spec.grant.capability_ids
    call=started(f,work);f.store.record_success(call,result(call,'Separate controlled host stage'),now=NOW)
    return work

def test_pg18_real_native_stages_and_same_intent_result(maintenance_native):
    f,origin,original=maintenance_native
    assert _rpc(f,origin,'record_submitted',proof={'target_id':'1'*64})['installed'] is False
    card=next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id']==origin['original_intent_id'])
    assert card['status']=='working' and card['can_stop'] is True
    _finish_stage(f,origin,'original_behavior_oracle')
    review=_finish_stage(f,origin,'independent_review',candidate_sha256='2'*64,comparison_sha256='3'*64)
    proof=dict(target_id='1'*64,candidate_sha256='2'*64,comparison_sha256='3'*64,review_sha256='4'*64,application_receipt_sha256='5'*64,installed_behavior_sha256='6'*64,image_recovery_sha256='7'*64,review_task_id=review.spec.work_id)
    # This controlled host attestation tests lifecycle only, not real installed gain.
    with pytest.raises(ValueError,match='complete installed maintenance proof'):
        _rpc(f,origin,'close_verified',proof={k:v for k,v in proof.items() if k!='image_recovery_sha256'})
    assert _rpc(f,origin,'close_verified',proof=proof)['installed'] is True
    assert _rpc(f,origin,'close_verified',proof=proof)['replayed'] is True
    card=next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id']==origin['original_intent_id'])
    assert card['status']=='done' and card['can_stop'] is False
    assert 'bijgewerkt' in f.control.artifact(origin['original_intent_id'],now=NOW)['content']
    # Original model result remains the actual builder output.
    assert f.store.load(original.spec.work_id,**f.service._owner).result.output=='Only the builder proposal is ready'

def test_pg18_stop_original_prevents_new_assessment(maintenance_native):
    f,origin,_=maintenance_native
    _rpc(f,origin,'record_submitted',proof={'target_id':'1'*64})
    assert f.control.stop(origin['original_intent_id'],now=NOW)['status']=='stopped'
    from eva.bootstrap.component_maintenance import admit_maintenance_assessment
    with pytest.raises((ValueError,RuntimeError),match='stopped|STOP'):
        admit_maintenance_assessment(f.auth,f.service,f.store,origin=origin,role='original_behavior_oracle',target_id='1'*64)

def test_pg18_changed_source_refuses_stage(maintenance_native):
    from eva.bootstrap.component_maintenance import admit_maintenance_assessment,observe_installed_maintenance
    f,origin,_=maintenance_native
    # A new real retained observation is not the original source approval.
    new_source=dict(source_sha256='e'*64,inventory_sha256='f'*64,
        official_releases={'psycopg':'c'*64,'psycopg-binary':'d'*64},versions={'core':{'psycopg':'3.3.4','psycopg-binary':'3.3.4'}})
    observe_installed_maintenance(f.auth,new_source,sensory_store=f.sensory,now=NOW)
    changed={**origin,'observation_sha256':new_source['source_sha256']}
    with pytest.raises(ValueError,match='observation changed'):
        admit_maintenance_assessment(f.auth,f.service,f.store,origin=changed,role='original_behavior_oracle',target_id='1'*64)


def test_optional_maintenance_without_owner_context_never_calls_authority():
    from eva.bootstrap.component_maintenance import compose_component_maintenance
    runtime=SimpleNamespace(_initiative_authority=lambda:pytest.fail('optional absent context must not resolve authority'))
    assert compose_component_maintenance(runtime,attention_store=None) is None
    assert compose_component_maintenance(runtime,attention_store=SimpleNamespace()) is None

def _composable(authority):
    return SimpleNamespace(_delegated_work_owner_service=object(),_runtime_tenant_id='t',_runtime_installation_id='i',
        _dashboard_owner_principal_id='p',_dashboard_principal_binding_id='b',_dashboard_authority_scope=object(),_initiative_authority=lambda:authority)

def test_revoked_optional_maintenance_disabled_and_programming_errors_visible():
    from eva.bootstrap.component_maintenance import compose_component_maintenance
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    def revoked():raise InitiativeMandateDenied('durably revoked')
    runtime=_composable(SimpleNamespace(policy=revoked))
    store=SimpleNamespace(_attention_source_resolver=None)
    assert compose_component_maintenance(runtime,attention_store=store) is None
    def broken():raise TypeError('programming error')
    runtime._initiative_authority=lambda:SimpleNamespace(policy=broken)
    with pytest.raises(TypeError,match='programming error'):compose_component_maintenance(runtime,attention_store=store)

def test_valid_maintenance_adds_only_own_coverage_and_preserves_other_source():
    from eva.bootstrap.component_maintenance import compose_component_maintenance
    from eva.application.sensory import SensoryEvent
    store=SimpleNamespace(_attention_source_resolver=lambda event:'existing provider')
    runtime=_composable(SimpleNamespace(policy=lambda:({'component_maintenance':POLICY},'a'*64)))
    resolver=compose_component_maintenance(runtime,attention_store=store)
    assert resolver(SensoryEvent('existing','e','changed',NOW,{}))=='existing provider'
    coverage=resolver(SensoryEvent('component_maintenance','m','component_update_available',NOW,{'component_set':'psycopg'}))
    assert coverage.source=='component_maintenance' and store._attention_source_resolver is resolver


@pytest.mark.parametrize('maintenance_native',[True],indirect=True)
def test_pg18_minimal_maintenance_grant_stops_and_card_shows_stopped(maintenance_native):
    f,origin,work=maintenance_native
    assert 'apply_reviewed_component_improvement' not in work.spec.grant.capability_ids
    _rpc(f,origin,'record_submitted',proof={'target_id':'1'*64})
    assert f.control.stop(origin['original_intent_id'],now=NOW)['status']=='stopped'
    card=next(c for c in f.control.page(now=NOW)['cards'] if c['intent_id']==origin['original_intent_id'])
    assert card['status']=='stopped' and card['can_stop'] is False
    from eva.bootstrap.component_maintenance import admit_maintenance_assessment
    with pytest.raises((ValueError,RuntimeError),match='stopped|STOP'):
        admit_maintenance_assessment(f.auth,f.service,f.store,origin=origin,role='original_behavior_oracle',target_id='1'*64)



@pytest.mark.parametrize('authorization_source', [POLICY['authorization_source'], 'Bewuste toestemming — '*30])
def test_pg18_ha_owned_source_reaches_private_decision_without_forced_model_choice(native_digital, monkeypatch, tmp_path, authorization_source):
    """One real-PG handoff case. No model response, update choice, or success is fabricated.

    An explicitly synthetic historical next-attention fixture tests scheduling;
    execution is intercepted at the existing model INPUT before any response.
    """
    import json
    from dataclasses import replace
    from datetime import timedelta
    from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
    from eva.adapters.postgres.initiative_attention import pending_attention, bind_attention_source
    from eva.application.resident_attention import NextAttention
    from eva.bootstrap.component_maintenance import compose_component_maintenance
    from tests.postgres.test_next_attention import finish, worker
    f = native_digital
    f.control.stop(f.claim.intent_id, now=NOW)
    f.profile = replace(f.profile, capability_ids=f.profile.capability_ids + ("manage_own_component_update",))
    f.policy["digital_work"]["capability_ids"] = list(f.profile.capability_ids)
    f.policy["component_maintenance"] = {**POLICY, "authorization_source":authorization_source}
    f.path.write_text(json.dumps(f.policy)); f.auth.policy()
    store = PostgresSensoryEventStore(f.control._engine, tenant_id=f.claim.tenant_id,
        installation_id=f.claim.installation_id, initiative_principal_id=f.claim.principal_id,
        initiative_authorizer=f.auth.policy, context_reviews_only=True)
    observation = dict(source_sha256='a'*64, inventory_sha256='b'*64,
        official_releases={'psycopg':'c'*64, 'psycopg-binary':'d'*64},
        versions={'core':{'psycopg':'3.3.4', 'psycopg-binary':'3.3.4'}})
    observe_installed_maintenance(f.auth, observation, sensory_store=f.sensory, now=NOW)
    # Actual HA ownership disables the general raw sensory consumer. Merely
    # retaining the source does not offer it on the private review (V1 defect).
    assert store.schedule_context_review(now=NOW) == 'scheduled'
    before = store._event_from_thread(store._decision_prefix + str(int(NOW.timestamp()) // 3600))
    assert not any(v['source']=='component_maintenance'
                   for v in before.payload['life_context']['observations'])
    historical = finish(store, choice=NextAttention(NOW+timedelta(hours=2),
        'Synthetic historical scheduling fixture, not an Eva choice'))
    with f.control._engine.connect() as connection:
        store._timeline._scope(connection)
        original_attention = pending_attention(connection, store, now=NOW)
    # Full existing bounded history must not erase the new software observation.
    from eva.application.sensory import SensoryEvent
    import hashlib
    for index in range(8):
        source_id = 'home-assistant:'+hashlib.sha256(str(index).encode()).hexdigest()
        f.sensory.retain(SensoryEvent('home_assistant', source_id, 'state_changed', NOW,
            {'entity_id':'sensor.context_'+str(index), 'new_state':'x'*80,
             'friendly_name':'n'*80, 'device_class':'d'*40}))
    store._private_work_context_reader = lambda now:{
        'available':True, 'source':'current_private_owner_work_store', 'as_of':now.isoformat(),
        'jobs':[{'work_id':'work:context-'+str(index), 'spec_sha256':'1'*64,
                 'source_ingress_sha256':'2'*64, 'source_conversation_kind':'telegram',
                 'summary':'bounded test metadata '*80} for index in range(3)]}
    runtime = _composable(f.auth)
    compose_component_maintenance(runtime, attention_store=store)
    at = NOW + timedelta(seconds=1)
    # A real new-source occasion uses the SAME private model lane while keeping
    # the old future appointment and all existing budget/STOP/native guards.
    assert store.schedule_context_review(now=at) == 'scheduled'
    assert store.schedule_context_review(now=at) == 'idle'
    claim = store.claim_next(now=at)
    assert claim is not None and claim.event.source == 'initiative-review'
    assert claim.event.payload['review_cause'] == 'component_source_changed'
    offered = claim.event.payload['life_context']['observations'][0]
    assert offered['source_event_id'] == 'maintenance-'+'a'*64
    assert offered['observation_sha256'] == 'a'*64
    assert offered['source_sha256'] != offered['observation_sha256']
    assert offered['source_attention']['available'] is True
    assert offered['installed_versions'] == observation['versions']
    declared = offered['declared_maintenance_mandate']
    assert declared['enabled'] is True
    assert declared['authorization_source'] == authorization_source.encode()[:256].decode('utf-8', errors='ignore')
    assert declared['authorization_source_sha256'] == hashlib.sha256(authorization_source.encode()).hexdigest()
    assert declared['authorization_source_truncated'] is (len(authorization_source.encode()) > 256)
    assert declared['classification'] == 'declared_current_owner_policy_not_execution_permission'
    assert declared['policy_sha256'] == offered['maintenance_configuration_sha256']
    assert offered['is_execution_permission'] is False
    assert 'new_cost_eur_cents' not in declared  # No invented zero price or extra grant.
    assert offered['target_version'] == '3.3.6' and offered['is_current_proof'] is False
    assert claim.event.payload['life_context']['coverage']['byte_budget_truncated'] is True
    assert 'entity_id' not in offered
    assert claim.event.payload['pending_attention'] == original_attention
    assert 'attention_origin' not in claim.event.payload
    with f.control._engine.connect() as connection:
        store._timeline._scope(connection)
        assert pending_attention(connection, store, now=at) == original_attention
        watch = bind_attention_source(connection, store,
            NextAttention(at+timedelta(hours=1), 'Test only: verify offered source binding',
                          offered['source_event_id']), claim.event, now=at)
        assert watch['source_sha256'] == offered['source_sha256']
        with pytest.raises(ValueError, match='exact private owner'):
            store._private_maintenance_context_reader(connection, store._timeline._scope_value,
                'another-person', at)
    # Use the actual existing worker input, then STOP at its model boundary.
    # No fake model response is returned or committed as a successful choice.
    class InputObserved(BaseException):
        pass
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from tests.model.test_conversation_model import _memory
    model = PydanticConversationModel(auth_file=tmp_path/'not-used-no-provider-call')
    captured = []
    def input_only(body, *args, **kwargs):
        captured.append(body)
        raise InputObserved()
    monkeypatch.setattr(model, '_request_using', input_only)
    # Local test admission only; the request interception cannot contact a provider.
    monkeypatch.setattr(model, 'credential_status', lambda:SimpleNamespace(ready=True))
    with pytest.raises(InputObserved):
        worker(store, f.auth, model, memory=SimpleNamespace(
            recall_for_presence=lambda query:_memory()))._decide_claim(claim, now=at, blocker=None)
    assert len(captured) == 1
    wire = json.loads(captured[0]['input'][0]['content'])
    assert wire['event']['payload']['life_context']['observations'][0] == offered
    assert 'manage_own_component_update' in str(wire['available_native_actions'])
    assert 'source_event_id' in str(captured[0]['tools'])
    assert not any(e.event_type=='WILL_DECIDED' for e in store._timeline.events(claim.thread_id))
    assert store.schedule_context_review(now=at+timedelta(seconds=1)) == 'idle'
    # A changed retained source invalidates the old undispatched model input;
    # its cancellation does not consume the unrelated future attention.
    changed = {**observation, 'source_sha256':'e'*64, 'inventory_sha256':'f'*64}
    observe_installed_maintenance(f.auth, changed, sensory_store=f.sensory,
        now=at+timedelta(seconds=2))
    started_claim = replace(claim, revision=store._timeline.load(claim.thread_id).revision)
    assert not store.context_review_claim_is_current(started_claim, now=at+timedelta(seconds=2))
    assert store.cancel_superseded_attention(started_claim, now=at+timedelta(seconds=2))
    assert store.schedule_context_review(now=at+timedelta(seconds=3)) == 'scheduled'
    with f.control._engine.connect() as connection:
        store._timeline._scope(connection)
        assert pending_attention(connection, store, now=at+timedelta(seconds=3)) == original_attention
    # Optional source revocation does not grant another audience or native tool.
    f.policy['component_maintenance']['enabled'] = False
    f.path.write_text(json.dumps(f.policy))
    with f.control._engine.connect() as connection:
        store._timeline._scope(connection)
        assert store._private_maintenance_context_reader(connection, store._timeline._scope_value,
            f.claim.principal_id, at) == ()
    assert store.schedule_context_review(now=at+timedelta(seconds=2)) == 'mandate_unavailable'



def test_default_observation_clock_satisfies_actual_timeline_second_contract(monkeypatch):
    import eva.bootstrap.component_maintenance as maintenance
    from eva.adapters.postgres.operational_timeline import _require_utc_second
    instants=[]
    class FractionalClock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,24,9,39,20,522907,tzinfo=UTC)
    monkeypatch.setattr(maintenance,'datetime',FractionalClock)
    def create(**kwargs):
        _require_utc_second(kwargs['occurred_at'],'occurred_at')
        instants.append(kwargs['occurred_at'])
    def retain(event):
        _require_utc_second(event.occurred_at,'occurred_at')
        instants.append(event.occurred_at)
        return 'retained'
    authority=SimpleNamespace(policy=lambda:({'component_maintenance':POLICY},'a'*64),
        owner=SimpleNamespace(),timeline=SimpleNamespace(create_thread=create))
    observation=dict(source_sha256='a'*64,inventory_sha256='b'*64,
        official_releases={'psycopg':'c'*64,'psycopg-binary':'d'*64},
        versions={'core':{'psycopg':'3.3.4','psycopg-binary':'3.3.4'}})
    assert maintenance.observe_installed_maintenance(authority,observation,
        sensory_store=SimpleNamespace(retain=retain))['state']=='retained'
    assert instants==[datetime(2026,10,24,9,39,20,tzinfo=UTC)]*2


def test_pg18_default_observation_clock_reaches_real_retained_source(costless_native,monkeypatch):
    import json
    import eva.bootstrap.component_maintenance as maintenance
    auth,old,interests,owner,sensory,path,policy=costless_native
    policy['component_maintenance']=dict(POLICY);path.write_text(json.dumps(policy))
    class FractionalClock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.replace(microsecond=522907)
    monkeypatch.setattr(maintenance,'datetime',FractionalClock)
    observation=dict(source_sha256='a'*64,inventory_sha256='b'*64,
        official_releases={'psycopg':'c'*64,'psycopg-binary':'d'*64},
        versions={'core':{'psycopg':'3.3.4','psycopg-binary':'3.3.4'}})
    assert maintenance.observe_installed_maintenance(auth,observation,sensory_store=sensory)['state']=='retained'
    retained=auth.timeline.events('component-maintenance-source:'+'a'*64)
    assert len(retained)==1 and retained[0].occurred_at==NOW
    source=sensory._event_from_thread('sensory:maintenance-'+'a'*64)
    assert source.occurred_at==NOW and source.source=='component_maintenance'
