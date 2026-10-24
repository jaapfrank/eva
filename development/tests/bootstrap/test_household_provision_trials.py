"""Focused exact-trial proof and real source/PG/provider handoffs; no live family effects."""
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.postgres.conftest import postgres_engine, postgres_gate
from tests.bootstrap.test_household_provision_versions import version_host, stage
from tests.providers.test_home_assistant_procedure import configured
from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.adapters.postgres.operational_timeline import CheckpointState, _checkpoint_payload, external_timeline_scope
from eva.bootstrap.household_provision_runtime import TrustedHouseholdProvisionCallbacks
from eva.bootstrap.household_provision_trials import HouseholdProvisionTrialEvidence
from eva.contracts.canonical_json import canonical_sha256


@pytest.fixture
def retained_trial_lineage(tmp_path, monkeypatch):
    legacy, wrapped, now, calls, _, path = configured(tmp_path, monkeypatch)
    config = json.loads(path.read_text())
    review_path = tmp_path / 'isolated-review.json'
    review_path.write_text(json.dumps({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
        'source': {'sha256': config['source_sha256']}}))
    config['review_sha256'] = hashlib.sha256(review_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(config))
    binding = legacy.household.to_payload()
    scope = external_timeline_scope(binding['tenant_id'], binding['installation_id'], 'fixture')
    evidence = {key: config[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}
    evidence.update(binding_sha256=canonical_sha256(config), realization_receipt_sha256=None)
    source, guard = legacy._current()
    observation = wrapped.payload['observation']
    source_binding = legacy._binding(observation)  # Existing isolated registry/health doubles.
    expression_guard = legacy._proposal_guard(guard, source_binding, {})
    context = {'proposal_text': 'Synthetic fixture only', 'expires_at_epoch': int(now.timestamp()) + 30,
        'binding_sha256': expression_guard, 'source_sha256': config['source_sha256'],
        'source_binding_sha256': canonical_sha256(source_binding), 'observation': observation,
        'is_execution_permission': False}
    thread = 'sensory:' + wrapped.source_event_id
    lineage = {'thread_id': thread, 'source_event_id': wrapped.source_event_id,
               'context': context, 'source_binding': source_binding}
    reference = {'thread_id': thread, 'source_event_id': wrapped.source_event_id,
                 'source_context_sha256': canonical_sha256(context)}
    auth = {'allowed': True, 'reads': []}
    def authorize(connection, at):
        auth['reads'].append('owner_authorize')
        if not auth['allowed']: raise PermissionError('isolated owner revoked')
    owner = SimpleNamespace(_engine=legacy.engine, _scope=scope,
        _tenant_id=binding['tenant_id'], _installation_id=binding['installation_id'],
        _owner='owner', _authorize=authorize)
    data = {'source': wrapped.source, 'source_event_id': wrapped.source_event_id,
            'event_type': wrapped.event_type, 'payload': wrapped.payload}
    prepared = {'household_binding': binding, 'source_event_id': wrapped.source_event_id,
        'expression': 'Synthetic isolated message', 'urgency': 1,
        'expression_expires_at_epoch': int(now.timestamp()) + 30,
        'expression_guard_sha256': expression_guard, 'source_context_sha256': canonical_sha256(context)}
    rows = []
    def append(kind, phase, payload, status='completed', deadline=None):
        seq = len(rows) + 1
        state = CheckpointState(thread, thread + ':' + str(seq), wrapped.source_event_id,
            phase, status, 0, None, deadline, seq, now)
        wire = _checkpoint_payload(state, payload)
        rows.append({'tenant_id': scope.tenant_id, 'installation_id': scope.installation_id,
            'event_id': uuid4(), 'thread_id': thread, 'sequence': seq, 'checkpoint_id': state.checkpoint_id,
            'operation_id': state.operation_id, 'event_type': kind, 'occurred_at': now,
            'payload': wire, 'payload_sha256': bytes.fromhex(canonical_sha256(wire))})
        return {**asdict(state), 'tenant_id': scope.tenant_id, 'installation_id': scope.installation_id}
    append('SENSORY_EVENT_OBSERVED', 'sensory_observed', data)
    append('PROACTIVE_EXPRESSION_PREPARED', 'expression_pending', prepared)
    append('PROACTIVE_EXPRESSION_CLAIMED', 'expression_claimed',
           {'source_event_id': wrapped.source_event_id, 'urgency': 1}, 'active', now + timedelta(seconds=300))
    offered_base = {'binding': binding, 'operation_id': wrapped.source_event_id,
        'thread_id': thread, 'revision': 3, 'source_event_id': wrapped.source_event_id,
        'text_sha256': canonical_sha256(prepared['expression']), 'http_status': 204}
    terminal = {'channel': 'home_assistant', 'disposition': 'offered', 'household_binding': binding,
                'source_event_id': wrapped.source_event_id,
                'provider_message_id': 'ha-offered:' + canonical_sha256(offered_base)}
    checkpoint = append('PROACTIVE_EXPRESSION_OFFERED', 'expression_offered', terminal)
    state = {'checkpoint': checkpoint, 'rows': rows, 'lineage': lineage}
    def load_lineage(connection, selected, metadata):
        assert selected == reference and metadata == evidence
        auth['reads'].append('lineage')
        return deepcopy(state['lineage'])
    def read_chain(connection, selected_scope, selected_thread):
        assert selected_scope == scope and selected_thread == thread
        auth['reads'].append('chain')
        return deepcopy(state['checkpoint']), deepcopy(state['rows'])
    def helper(proof_reference=reference):
        return TrustedHouseholdProvisionCallbacks(owner, legacy,
            household_binding=binding, procedure_path=path, review_path=review_path,
            proof_reference=proof_reference, load_lineage=load_lineage,
            clock=lambda: now, read_chain=read_chain)
    return SimpleNamespace(helper=helper, legacy=legacy, owner=owner, auth=auth,
        evidence=evidence, state=state, now=now, path=path, config=config,
        reference=reference, append=append, source=source)


def exact_trial(retained):
    cb=retained.helper()
    configuration,guard=cb._current(None,retained.evidence)
    claimed=retained.state['rows'][2]
    final=retained.state['rows'][-1]['payload']['data']
    claim={'thread_id':claimed['thread_id'],'source_event_id':retained.reference['source_event_id'],
           'operation_id':claimed['operation_id'],'revision':claimed['sequence']}
    trial={'trial_id':'a'*64,'version_id':PostgresHouseholdProvisions._version_id(retained.evidence),
        'binding_sha256':retained.evidence['binding_sha256'],'owner_principal_id':'owner',
        'household_binding_sha256':canonical_sha256(cb.binding),'execution_guard_sha256':guard,
        'authorized_at':retained.now.isoformat(),'expires_at_epoch':int(retained.now.timestamp())+120,
        'claims':{canonical_sha256(claim):{'claim':claim,'outcome':'completed',
            'proof_sha256':final['provider_message_id'].removeprefix('ha-offered:')}}}
    return cb,trial


def test_exact_post_grant_claim_offer_is_working_evidence(retained_trial_lineage):
    r=retained_trial_lineage;cb,trial=exact_trial(r)
    helper=HouseholdProvisionTrialEvidence(cb,lambda:None)
    proof=helper.verify_trial_realization(None,r.evidence,trial)
    assert isinstance(proof,str) and len(proof)==64
    assert helper.verify_empty_current(None,r.evidence) is None  # V1 absence is never new-binding clear.


@pytest.mark.parametrize('fault',['no_postgrant_claim','pregrant_claim','wrong_receipt','unknown_effect','execution_guard'])
def test_old_or_unconfirmed_offer_cannot_certify_a_new_trial(retained_trial_lineage,fault):
    r=retained_trial_lineage;cb,trial=exact_trial(r)
    if fault=='no_postgrant_claim':trial['claims']={}
    elif fault=='pregrant_claim':trial['authorized_at']=(r.now+timedelta(seconds=1)).isoformat()
    elif fault=='wrong_receipt':next(iter(trial['claims'].values()))['proof_sha256']='0'*64
    elif fault=='unknown_effect':next(iter(trial['claims'].values()))['outcome']='unknown'
    else:trial['execution_guard_sha256']='0'*64
    assert HouseholdProvisionTrialEvidence(cb,lambda:None).verify_trial_realization(None,r.evidence,trial) is None


@pytest.mark.parametrize('fault',['source','owner'])
def test_exact_trial_rechecks_source_and_owner_after_blocking_chain_read(retained_trial_lineage,fault):
    r=retained_trial_lineage;cb,trial=exact_trial(r);read=cb.read_chain
    def changed(*args):
        result=read(*args)
        if fault=='source':(r.path.parent/'extension.py').write_text('changed source')
        else:r.auth['allowed']=False
        return result
    cb.read_chain=changed
    with pytest.raises((ValueError,PermissionError)):
        HouseholdProvisionTrialEvidence(cb,lambda:None).verify_trial_realization(None,r.evidence,trial)


def test_checked_new_source_handoff_with_no_effect_is_clear_but_not_working(version_host):
    f=version_host; result=stage(f); f.controls['verified']=None
    changed=f.host.change(f.pid,result['selected_version_id'],result['receipt']['revision'],
        f.pointer['binding_sha256'],'new-version-before-first-use')
    assert changed['receipt']['status']=='proposed' and changed['receipt']['adoption']
    config=json.loads(f.path.read_text()); owner=f.controller.owner_loader()
    owner._engine=f.controller.engine
    options=f.controller._callbacks()
    def current(connection,evidence):
        owner._authorize(connection,datetime.now(UTC).replace(microsecond=0))
        options['verify_current'](connection,evidence)
        return json.loads(f.path.read_text()),'9'*64
    cb=SimpleNamespace(_current=current,owner=owner,binding=config['household_binding'],scope=owner._scope,
        authorize=owner._authorize,verify_current=options['verify_current'],
        verify_realization=options['verify_realization'],effect_status=lambda conn,evidence:'unknown')
    helper=HouseholdProvisionTrialEvidence(cb,f.controller._load_enrollment)
    evidence=f.host._evidence(config)
    with f.controller.engine.begin() as connection:
        proof=helper.verify_empty_current(connection,evidence)
        assert isinstance(proof,str) and len(proof)==64
    store=PostgresHouseholdProvisions(f.controller.engine,
        tenant_id=config['household_binding']['tenant_id'],installation_id='home',owner_principal_id='owner',
        household_binding=config['household_binding'],authorize=owner._authorize,
        verify_current=options['verify_current'],verify_realization=lambda conn,evidence:None,
        effect_status=lambda conn,evidence:'unknown',verify_empty_current=helper.verify_empty_current,
        current_guard=helper.current_guard,verify_trial_realization=lambda conn,evidence,trial:None)
    now=datetime.now(UTC).replace(microsecond=0)
    granted=store.authorize_trial(f.pid,expires_at_epoch=int(now.timestamp())+300,max_effects=1,
        expected_revision=changed['receipt']['revision'],binding_sha256=changed['enrollment']['binding_sha256'],
        request_id='owner-allows-new-version-trial',now=now)
    assert granted['status']=='proposed' and granted['adoption']==changed['receipt']['adoption']
    assert granted['verification_sha256'] is None
    # Existing acceptance is historical, not evidence that this new source works.
    from eva.adapters.postgres.operational_timeline import CheckpointConflict
    with pytest.raises(CheckpointConflict,match='exact completed trial working proof'):
        store.evaluate_trial(f.pid,working=True,useful=True,feedback='Kan nog niet bevestigd zijn',
            expected_revision=granted['revision'],binding_sha256=changed['enrollment']['binding_sha256'],
            request_id='cannot-adopt-before-new-use',now=now)


def test_real_provider_preparation_and_final_admission_share_same_bounded_trial(postgres_engine,tmp_path,monkeypatch):
    from copy import copy
    from eva.application.free_will import ProactiveExpressionClaim
    p,event,now,inputs,out,path=configured(tmp_path,monkeypatch)
    config=json.loads(path.read_text());config['reference_id']='procedure-reference:trial-'+tmp_path.name
    path.write_text(json.dumps(config));pid=procedure_provision_id(config)
    legacy=copy(p);legacy.provisions=None
    controls={'allowed':True}
    def authorize(connection,at):
        if not controls['allowed']:raise PermissionError('owner revoked')
    evidence={**{k:config[k] for k in ('source_sha256','review_sha256','reference_id')},
        'binding_sha256':canonical_sha256(config),'realization_receipt_sha256':None}
    store=PostgresHouseholdProvisions(postgres_engine,tenant_id=p.household.tenant_id,
        installation_id=p.household.installation_id,owner_principal_id=config['source_principal_id'],
        household_binding=p.household.to_payload(),authorize=authorize,
        verify_current=lambda conn,ev:legacy._current(),verify_realization=lambda conn,ev:None,
        effect_status=lambda conn,ev:'clear',current_guard=lambda conn,ev:legacy._current()[1],
        verify_trial_realization=lambda conn,ev,trial:None)
    store.register(pid,evidence,request_id='proposed-before-trial',now=now);p.provisions=store
    assert p(event,now=now) is None and inputs==[]
    granted=store.authorize_trial(pid,expires_at_epoch=int(now.timestamp())+300,max_effects=1,
        expected_revision=1,binding_sha256=evidence['binding_sha256'],request_id='explicit-owner-trial',now=now)
    context=p(event,now=now)
    assert context is not None and len(inputs)==1
    claim=ProactiveExpressionClaim('sensory:'+event.source_event_id,event.source_event_id,3,0,
                                  event.source_event_id,'Synthetic trial response',1)
    admitted=p.admit_expression(event,context['binding_sha256'],claim)
    assert admitted['admitted_now'] is True
    assert store.inspect(pid,now=now)['trial']['effect_count']==1
    assert p(event,now=now) is None  # No second source execution while effect is pending/budget spent.
    assert len(inputs)==1


@pytest.mark.parametrize('version_host',[{'full_household':True}],indirect=True)
@pytest.mark.parametrize('other_effect',[None,'same_household','other_household'])
def test_new_adopted_artifact_version_requires_real_new_trial_offer_feedback_and_notice(version_host,monkeypatch,other_effect):
    """No constant working proof: None until original new-version PG offer exists."""
    from eva.application.free_will import FreeWillDecision,HouseholdProcedureLineage
    from eva.application.household_initiative import HouseholdInitiativeBinding
    from eva.application.sensory import SensoryEvent
    from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
    from eva.bootstrap.household_provision_runtime import HouseholdProvisionRuntime
    from eva.bootstrap.household_provision_trials import HouseholdProvisionTrials
    from eva.contracts.canonical_json import canonical_json_bytes
    f=version_host; staged=stage(f); f.controls['verified']=None
    changed=f.host.change(f.pid,staged['selected_version_id'],staged['receipt']['revision'],
                         f.pointer['binding_sha256'],'install-unverified-version-for-trial')
    assert changed['receipt']['status']=='proposed' and changed['receipt']['adoption']
    assert changed['receipt']['verification_sha256'] is None
    owner=f.controller.owner_loader();owner._engine=f.controller.engine
    config=json.loads(f.path.read_text());binding=HouseholdInitiativeBinding(**config['household_binding'])
    owner._tenant_id,owner._installation_id=binding.tenant_id,binding.installation_id
    original_options=f.controller._callbacks()
    class Legacy:
        path,engine,household,provisions=f.path,f.controller.engine,binding,None
        def _current(self):
            current=json.loads(f.path.read_text())
            expected=f.host._evidence(current)
            with f.controller.engine.begin() as connection:
                owner._authorize(connection,datetime.now(UTC).replace(microsecond=0))
                original_options['verify_current'](connection,expected)
            if f.host._source(current['reference_id'],current)['baseline_content'] != (
                    f.path.parent/current['source_file']).read_text():
                raise ValueError('current private source reference changed')
            configuration={'config':current,'execution':{'fixture_no_network':True},'wrapper_sha256':'c'*64}
            return (f.path.parent/current['source_file']).read_text(),canonical_sha256(configuration)
    legacy=Legacy()
    f.controller._reference=lambda connection,bound,evidence,**kw:HouseholdProvisionRuntime._reference(
        f.controller,connection,bound,evidence,**kw)
    f.controller._lineage=lambda connection,reference,evidence:HouseholdProvisionRuntime._lineage(
        f.controller,connection,reference,evidence)
    def callbacks(bound,path,current,enrollment):
        return TrustedHouseholdProvisionCallbacks(owner,legacy,household_binding=bound,
            procedure_path=f.path,review_path=f.review_path,
            select_reference=lambda conn,evidence,**kw:f.controller._reference(conn,bound,evidence,**kw),
            load_lineage=f.controller._lineage,load_enrollment=f.controller._load_enrollment).callbacks()
    f.controller._callbacks=callbacks
    f.controller._version_controls=lambda:f.host
    trials=HouseholdProvisionTrials(f.controller)
    evidence=f.host._evidence(config);options=callbacks(binding.to_payload(),str(f.path),config,None)
    with f.controller.engine.begin() as connection:
        assert options['verify_realization'](connection,evidence) is None
    now=datetime.now(UTC).replace(microsecond=0)
    grant=trials.authorize(f.pid,int(now.timestamp())+300,1,changed['receipt']['revision'],
                          evidence['binding_sha256'],'owner-grants-specific-new-version-trial')
    assert grant['status']=='proposed' and grant['adoption']==changed['receipt']['adoption']
    # Known-empty new source permits preparation, never an invented admission claim.
    from eva.adapters.postgres.operational_timeline import CheckpointConflict
    with pytest.raises(CheckpointConflict,match='not dispatchable'):
        f.host._store(config).admit_effect(f.pid,{'thread_id':'sensory:missing-trial-source',
            'source_event_id':'missing-trial-source','operation_id':'missing-trial-operation','revision':3},
            expected_revision=grant['revision'],binding_sha256=evidence['binding_sha256'],
            request_id='cannot-admit-missing-source-claim',now=now)
    source=PostgresSensoryEventStore(f.controller.engine,tenant_id=binding.tenant_id,installation_id='home')
    shared=PostgresSensoryEventStore(f.controller.engine,tenant_id=binding.tenant_id,installation_id='home',
        initiative_principal_id='owner',initiative_authorizer=lambda:None,
        context_reviews_only=True,household_binding=binding)
    native=SensoryEvent('home_assistant','home-assistant:trial-'+hashlib.sha256(f.pid.encode()).hexdigest(),
        'state_changed',now,{'entity_id':'binary_sensor.fixture','old_state':'off','new_state':'on',
                              'friendly_name':'Synthetic shared trial observation'})
    source.retain(native);assert shared.schedule_household_observation(native,now=now)=='scheduled'
    claim=shared.claim_next(now=now);observation=claim.event.payload['observation']
    original_binding={k:observation[k] for k in ('source','source_event_id','occurred_at')}
    configuration={'config':config,'execution':{'fixture_no_network':True},'wrapper_sha256':'c'*64,
                   'provision':{'id':f.pid,'revision':grant['revision']}}
    context={'proposal_text':'Synthetic new source result','expires_at_epoch':int(now.timestamp())+30,
        'source_sha256':config['source_sha256'],'source_binding_sha256':canonical_sha256(original_binding),
        'observation':observation,'is_execution_permission':False,
        'binding_sha256':canonical_sha256({'configuration':canonical_sha256(configuration),
                                          'source':canonical_sha256(original_binding)})}
    lineage=HouseholdProcedureLineage(canonical_json_bytes({'contract':'EvaHouseholdProcedureLineage/v1',
        'context':context,'configuration':configuration,'source_binding':original_binding}))
    shared.complete(claim,FreeWillDecision('respond','Synthetic ordinary choice',1,'fixture','Welkom',
        source_context_sha256=canonical_sha256(context),expression_guard_sha256=context['binding_sha256'],
        expression_expires_at_epoch=context['expires_at_epoch'],household_procedure_lineage=lineage),now=now)
    expression=shared.claim_next_household_expression(binding=binding.to_payload(),now=now)
    bound_claim={k:getattr(expression,k) for k in ('thread_id','operation_id','revision','source_event_id')}
    provision=f.host._store(config)
    if other_effect is not None:
        from dataclasses import replace
        from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
        other_binding=binding if other_effect=='same_household' else replace(binding,household_id='other-family')
        other_native=replace(native,source_event_id=native.source_event_id+'-other')
        wrapped=other_binding.observation_event(other_native)
        thread='sensory:'+wrapped.source_event_id
        other_observation=wrapped.payload['observation']
        origin_binding={k:other_observation[k] for k in ('source','source_event_id','occurred_at')}
        other_config=deepcopy(config);other_config['household_binding']=other_binding.to_payload()
        other_configuration={**configuration,'config':other_config}
        other_context={**context,'observation':other_observation,
            'source_binding_sha256':canonical_sha256(origin_binding),
            'binding_sha256':canonical_sha256({'configuration':canonical_sha256(other_configuration),
                                              'source':canonical_sha256(origin_binding)})}
        timeline=PostgresOperationalTimeline(f.controller.engine,owner._scope)
        timeline.create_thread(thread_id=thread,checkpoint_id=thread+':1',operation_id=wrapped.source_event_id,
            phase='sensory_observed',status='completed',event_type='SENSORY_EVENT_OBSERVED',occurred_at=now,
            data={'source':wrapped.source,'source_event_id':wrapped.source_event_id,
                  'event_type':wrapped.event_type,'payload':wrapped.payload})
        timeline.transition(thread_id=thread,expected_revision=1,checkpoint_id=thread+':2',
            operation_id=wrapped.source_event_id,phase='expression_pending',status='completed',
            event_type='PROACTIVE_EXPRESSION_PREPARED',occurred_at=now,
            data={'expression':'Other synthetic pending output','urgency':1,
                'household_binding':other_binding.to_payload(),'source_event_id':wrapped.source_event_id,
                'source_context_sha256':canonical_sha256(other_context),
                'expression_guard_sha256':other_context['binding_sha256'],
                'expression_expires_at_epoch':other_context['expires_at_epoch'],
                'household_procedure_lineage':{'contract':'EvaHouseholdProcedureLineage/v1',
                    'context':other_context,'configuration':other_configuration,'source_binding':origin_binding}})
        timeline.transition(thread_id=thread,expected_revision=2,checkpoint_id=thread+':3',
            operation_id=wrapped.source_event_id,phase='expression_claimed',status='active',
            event_type='PROACTIVE_EXPRESSION_CLAIMED',occurred_at=now,deadline=now+timedelta(seconds=300),
            data={'source_event_id':wrapped.source_event_id,'urgency':1})
        if other_effect=='same_household':
            from eva.adapters.postgres.operational_timeline import CheckpointConflict
            with pytest.raises(CheckpointConflict,match='not dispatchable'):
                provision.admit_effect(f.pid,bound_claim,expected_revision=grant['revision'],
                    binding_sha256=evidence['binding_sha256'],request_id='other-effect-blocks',now=now)
            assert provision.inspect(f.pid,now=now)['effect_status']=='in_flight'
            return
    admitted=provision.admit_effect(f.pid,bound_claim,expected_revision=grant['revision'],
        binding_sha256=evidence['binding_sha256'],request_id='admit-new-trial-effect',now=now)
    replay=provision.admit_effect(f.pid,bound_claim,expected_revision=grant['revision'],
        binding_sha256=evidence['binding_sha256'],request_id='never-replay-own-admitted-claim',now=now)
    assert replay['admitted_now'] is False
    offer='ha-offered:'+canonical_sha256({'binding':binding.to_payload(),
        'operation_id':expression.operation_id,'thread_id':expression.thread_id,'revision':expression.revision,
        'source_event_id':expression.source_event_id,'text_sha256':canonical_sha256(expression.text),'http_status':204})
    provision.settle_effect(f.pid,admission_id=admitted['receipt']['admission_id'],outcome='completed',
        proof_sha256=offer.removeprefix('ha-offered:'),request_id='settle-original-new-trial-offer',now=now)
    shared.acknowledge_household_expression(expression,binding=binding.to_payload(),disposition='offered',
        provider_message_id=offer,now=now)
    current=provision.inspect(f.pid,now=datetime.now(UTC).replace(microsecond=0))
    assert current['status']=='proposed' and current['verification_sha256'] is None
    with f.controller.engine.begin() as connection:
        assert options['verify_realization'](connection,evidence) is not None
        assert options['verify_trial_realization'](connection,evidence,current['trial']) is not None
    evaluated=trials.evaluate(f.pid,True,True,'De nieuwe versie werkt en helpt daadwerkelijk.',
        current['revision'],evidence['binding_sha256'],'real-new-version-owner-feedback')
    assert evaluated['status']=='proposed' and evaluated['adoption']==changed['receipt']['adoption']
    notice=evaluated['trial']['notice'];assert notice and notice['acknowledged_at'] is None
    final=trials.acknowledge_notice(f.pid,notice['notice_id'],notice['nonce'],evaluated['revision'],
        evidence['binding_sha256'],'owner-render-acknowledges-new-version-notice')
    assert final['status']=='active' and final['adoption']['method']=='prior_authorized_trial'
    assert final['adoption']['trial_id']==grant['trial']['trial_id']
    assert final['verification_sha256'] is not None


def test_legacy_claim_without_lifecycle_admission_guard_remains_unknown(retained_trial_lineage):
    from eva.adapters.postgres.operational_timeline import _state_from_event,_event_from_row
    r=retained_trial_lineage;cb,trial=exact_trial(r)
    r.state['rows']=r.state['rows'][:3]
    last=r.state['rows'][-1]
    projected=_state_from_event(_event_from_row(SimpleNamespace(**last)))
    r.state['checkpoint']={**asdict(projected),'tenant_id':cb.scope.tenant_id,'installation_id':cb.scope.installation_id}
    cb.select_reference=lambda *args,**kwargs:None
    claim=next(iter(trial['claims'].values()))['claim']
    pointer={'contract':'EvaHouseholdProvisionEnrollment/v1','provision_id':procedure_provision_id(r.config),
        'owner_principal_id':'owner','binding_sha256':r.evidence['binding_sha256']}
    helper=HouseholdProvisionTrialEvidence(cb,lambda:pointer)
    assert helper.effect_status_for_admission(None,r.evidence,claim,1)=='unknown'
    assert 'chain' in r.auth['reads']  # Valid scoped legacy chain was actually examined.
