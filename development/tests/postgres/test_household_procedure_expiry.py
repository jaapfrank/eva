from datetime import timedelta
import pytest
from eva.application.free_will import FreeWillDecision
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_household_observation_initiative import _shared,_binding,_signal


def prepared(fixture, name):
    auth, old, interests, owner, originals, path, policy=fixture
    owner.stop(old.intent_id,now=NOW);auth.policy();binding=_binding(old.tenant_id)
    store=_shared(owner._engine,old.tenant_id,binding,auth.policy,old.principal_id)
    event=_signal(name);originals.retain(event);store.schedule_household_observation(event,now=NOW)
    claim=store.claim_next(now=NOW)
    store.complete(claim,FreeWillDecision('respond','Reviewed proposal',1,'model','Welcome',
        expression_expires_at_epoch=int(NOW.timestamp())+30,expression_guard_sha256='c'*64),now=NOW)
    return store,binding


def test_expiry_is_durable_before_claim_and_checked_after_claim(costless_native):
    store,binding=prepared(costless_native,'procedure-expiry')
    claim=store.claim_next_household_expression(binding=binding.to_payload(),now=NOW+timedelta(seconds=1))
    assert claim is not None
    assert store._prepared_expression(claim.thread_id)['expression_guard_sha256']=='c'*64
    store.authorize_household_expression(claim,binding=binding.to_payload(),now=NOW+timedelta(seconds=29))
    with pytest.raises(CheckpointConflict):
        store.authorize_household_expression(claim,binding=binding.to_payload(),now=NOW+timedelta(seconds=30))


def test_unclaimed_expired_proposal_is_not_offered(costless_native):
    store,binding=prepared(costless_native,'procedure-expiry-unclaimed')
    assert store.claim_next_household_expression(binding=binding.to_payload(),now=NOW+timedelta(seconds=30)) is None


@pytest.mark.parametrize('fault', [None, 'wrong_owner', 'wrong_origin'])
def test_original_host_lineage_is_durable_only_on_matching_prepared_chain(costless_native, fault):
    from copy import deepcopy
    from eva.application.free_will import HouseholdProcedureLineage
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW); auth.policy(); binding = _binding(old.tenant_id)
    store = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    event = _signal('retained-lineage-' + str(fault))
    originals.retain(event); store.schedule_household_observation(event, now=NOW)
    claim = store.claim_next(now=NOW)
    observation = deepcopy(claim.event.payload['observation'])
    source = {key: observation[key] for key in ('source', 'source_event_id', 'occurred_at')}
    configuration = {'config': {'household_binding': binding.to_payload(),
        'source_principal_id': old.principal_id if fault != 'wrong_owner' else 'another-person',
        'source_sha256': 'a' * 64}}
    if fault == 'wrong_origin': observation['source_event_id'] = 'another-event'
    context = {'observation': observation, 'source_binding_sha256': canonical_sha256(source),
        'source_sha256': 'a' * 64, 'is_execution_permission': False,
        'expires_at_epoch': int(NOW.timestamp()) + 30,
        'binding_sha256': canonical_sha256({'configuration': canonical_sha256(configuration),
                                          'source': canonical_sha256(source)})}
    opaque = HouseholdProcedureLineage(canonical_json_bytes({'contract': 'EvaHouseholdProcedureLineage/v1',
        'context': context, 'source_binding': source, 'configuration': configuration}))
    decision = FreeWillDecision('respond', 'Original host proposal', 1, 'synthetic', 'Welcome',
        expression_expires_at_epoch=context['expires_at_epoch'],
        expression_guard_sha256=context['binding_sha256'], source_context_sha256=canonical_sha256(context),
        household_procedure_lineage=opaque)
    if fault:
        with pytest.raises(ValueError, match='(binding|origin) differs'):
            store.complete(claim, decision, now=NOW)
        return
    store.complete(claim, decision, now=NOW)
    prepared_data = next(e.payload['data'] for e in store._timeline.events(claim.thread_id)
                         if e.event_type == 'PROACTIVE_EXPRESSION_PREPARED')
    assert prepared_data['household_procedure_lineage'] == opaque.payload()
    assert prepared_data['source_context_sha256'] == canonical_sha256(context)
    expression = store.claim_next_household_expression(binding=binding.to_payload(), now=NOW)
    assert expression.source_event_id == claim.event.source_event_id
    # The original exactly retained bytes remain after a restart/reconstructed store.
    reloaded = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    retained_data = next(e.payload['data'] for e in reloaded._timeline.events(expression.thread_id)
                         if e.event_type == 'PROACTIVE_EXPRESSION_PREPARED')
    assert retained_data['household_procedure_lineage'] == opaque.payload()


def test_real_timeline_lineage_offer_enrollment_adoption_pause_resume_and_stop(costless_native, tmp_path, monkeypatch):
    """Synthetic content, real PG and real proof reader; no live person/model/speaker."""
    import hashlib, json
    from datetime import datetime
    from types import SimpleNamespace
    from eva.application.free_will import HouseholdProcedureLineage
    from eva.bootstrap.household_provision_runtime import configured_household_provision_runtime
    import eva.bootstrap.household_provision_runtime as runtime
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW); auth.policy(); binding = _binding(old.tenant_id)
    store = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    source = b'def propose(event, binding, *, now): return {}\n'
    (tmp_path / 'provision.py').write_bytes(source)
    review_path = tmp_path / 'adoption-review.json'
    review_path.write_text(json.dumps({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
        'source': {'sha256': hashlib.sha256(source).hexdigest()}}))
    config = {'contract': 'EvaHouseholdProcedureBinding/v1', 'enabled': True,
        'household_binding': binding.to_payload(), 'source_file': 'provision.py',
        'source_sha256': hashlib.sha256(source).hexdigest(), 'source_principal_id': old.principal_id,
        'review_sha256': hashlib.sha256(review_path.read_bytes()).hexdigest(),
        'reference_id': 'procedure-reference:lineage-proof-test'}
    config_path = tmp_path / 'current.json'; config_path.write_text(json.dumps(config))
    pointer = tmp_path / 'enrollment.json'; pointer.write_text('{"household_provision_enrollment":null}')
    pointer.chmod(0o600)
    configuration = {'config': config, 'execution': {'fixture': 'no network'}, 'wrapper_sha256': 'b' * 64}
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(runtime, 'datetime', FixedTime)
    class Legacy:
        def __init__(self, path, *, engine, household, authorize, provisions, **kwargs):
            self.path, self.engine, self.household = path, engine, household
            self.authorize, self.provisions = authorize, provisions
        def _current(self):
            self.authorize()
            if (tmp_path / 'provision.py').read_bytes() != source:
                raise ValueError('current source changed')
            return source.decode(), canonical_sha256(configuration)
        from eva.providers.home_assistant_procedure import HomeAssistantProcedure as _Original
        _proposal_guard = staticmethod(_Original._proposal_guard)
    monkeypatch.setattr(runtime, 'HomeAssistantProcedure', Legacy)
    controller = configured_household_provision_runtime(owner._engine, owner_loader=lambda: owner,
        environ={'EVA_HOUSEHOLD_PROVISION_ENROLLMENT_FILE': str(pointer),
                 'EVA_HOUSEHOLD_PROCEDURE_FILE': str(config_path)})
    controller.settings = SimpleNamespace()
    monkeypatch.setattr(controller, '_authorize_household', lambda bound: auth.policy())
    first = controller.register('first-before-any-proof')
    assert first['enrollment'] is None and first['receipt']['status'] == 'proposed'
    assert controller.overview()['actual_working_proof_available'] is False
    event = _signal('full-lineage-offer'); originals.retain(event)
    store.schedule_household_observation(event, now=NOW); claim = store.claim_next(now=NOW)
    observation = claim.event.payload['observation']
    source_binding = {key: observation[key] for key in ('source', 'source_event_id', 'occurred_at')}
    context = {'observation': observation, 'proposal_text': 'Synthetic test only',
        'source_binding_sha256': canonical_sha256(source_binding), 'source_sha256': config['source_sha256'],
        'is_execution_permission': False, 'expires_at_epoch': int(NOW.timestamp()) + 30,
        'binding_sha256': canonical_sha256({'configuration': canonical_sha256(configuration),
                                          'source': canonical_sha256(source_binding)})}
    lineage = HouseholdProcedureLineage(canonical_json_bytes({'contract': 'EvaHouseholdProcedureLineage/v1',
        'context': context, 'configuration': configuration, 'source_binding': source_binding}))
    store.complete(claim, FreeWillDecision('respond', 'Synthetic household decision', 1, 'fixture', 'Welcome',
        source_context_sha256=canonical_sha256(context), expression_guard_sha256=context['binding_sha256'],
        expression_expires_at_epoch=context['expires_at_epoch'], household_procedure_lineage=lineage), now=NOW)
    assert controller.overview()['provision']['effect_status'] == 'clear'
    assert controller.overview()['actual_working_proof_available'] is False
    expression = store.claim_next_household_expression(binding=binding.to_payload(), now=NOW)
    assert controller.overview()['provision']['effect_status'] == 'in_flight'
    offer = 'ha-offered:' + canonical_sha256({'binding': binding.to_payload(),
        'operation_id': expression.operation_id, 'thread_id': expression.thread_id,
        'revision': expression.revision, 'source_event_id': expression.source_event_id,
        'text_sha256': canonical_sha256(expression.text), 'http_status': 204})
    store.acknowledge_household_expression(expression, binding=binding.to_payload(),
        disposition='offered', provider_message_id=offer, now=NOW)
    assert controller.overview()['actual_working_proof_available'] is True
    assert json.loads(pointer.read_text())['household_provision_enrollment'] is None
    enrolled = controller.register('explicit-handoff-after-offer')
    assert enrolled['recovered_registration'] is True and enrolled['enrollment'] is not None
    assert enrolled['receipt']['status'] == 'proposed' and enrolled['receipt']['adoption'] is None
    pid = enrolled['receipt']['provision_id']; digest = canonical_sha256(config)
    adopted = controller.command(pid, 'adopt', enrolled['receipt']['revision'], digest, 'owner-adopt')
    assert adopted['status'] == 'active' and adopted['verification_sha256']
    paused = controller.command(pid, 'pause', adopted['revision'], digest, 'owner-pause')
    resumed = controller.command(pid, 'resume', paused['revision'], digest, 'owner-resume')
    assert resumed['status'] == 'active' and resumed['adoption'] == adopted['adoption']
    stopped = controller.command(pid, 'stop', resumed['revision'], digest, 'owner-stop')
    assert stopped['stopped'] is True and stopped['status'] == 'paused'
    with pytest.raises(CheckpointConflict):
        controller.command(pid, 'resume', stopped['revision'], digest, 'cannot-reset-stop')
    (tmp_path / 'provision.py').write_bytes(b'changed source')
    with pytest.raises(ValueError, match='source changed'):
        controller.overview()


@pytest.mark.parametrize('phase,hazard_offset,offer_offset,foreign', [
    ('live', 0, 1, None), ('live', 0, 0, None), ('live', 1, 0, None),
    ('ambiguous', 0, 1, None), ('expired', 0, 1, None),
    ('live', 0, 1, 'household'), ('live', 0, 1, 'owner'), ('pending', 0, 1, None),
])
def test_unresolved_effect_selection_is_not_hidden_by_offer_or_second_tie(
        costless_native, monkeypatch, phase, hazard_offset, offer_offset, foreign):
    """Original valid synthetic chains in real PG; independent of live dispatch."""
    from copy import deepcopy
    from dataclasses import replace
    from datetime import datetime
    from eva.adapters.postgres.operational_timeline import (
        insert_initial_timeline_thread, transition_timeline_thread)
    from eva.bootstrap.household_provision_runtime import (
        HouseholdProvisionRuntime, TrustedHouseholdProvisionCallbacks, read_exact_chain)
    import eva.bootstrap.household_provision_runtime as runtime
    from eva.contracts.canonical_json import canonical_sha256
    auth, old, interests, owner, originals, path, policy = costless_native
    binding = _binding(old.tenant_id)
    config = {'household_binding': binding.to_payload(), 'source_principal_id': old.principal_id,
              'source_sha256': 'a' * 64, 'review_sha256': 'b' * 64, 'reference_id': 'source:selection'}
    evidence = {**{k: config[k] for k in ('source_sha256', 'review_sha256', 'reference_id')},
                'binding_sha256': canonical_sha256(config), 'realization_receipt_sha256': None}
    now = NOW + timedelta(seconds=301 if phase == 'expired' else 10)
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(runtime, 'datetime', FixedTime)
    controller = object.__new__(HouseholdProvisionRuntime)
    controller.owner_loader = lambda: owner
    controller._source = lambda: deepcopy(config)
    def seed(label, at, mode, *, foreign=None):
        bound = replace(binding, household_id='another-household') if foreign == 'household' else binding
        selected_config = {**config, 'household_binding': bound.to_payload()}
        if foreign == 'owner': selected_config['source_principal_id'] = 'another-owner'
        event = bound.observation_event(_signal(label, at=at))
        thread = 'sensory:' + event.source_event_id
        source_binding = {k: event.payload['observation'][k] for k in ('source', 'source_event_id', 'occurred_at')}
        context = {'observation': event.payload['observation'], 'source_sha256': 'a' * 64,
            'source_binding_sha256': canonical_sha256(source_binding), 'is_execution_permission': False,
            'binding_sha256': 'd' * 64, 'expires_at_epoch': int(at.timestamp()) + 30}
        prepared = {'household_binding': bound.to_payload(), 'source_event_id': event.source_event_id,
            'expression': 'Synthetic selection fixture', 'urgency': 1,
            'source_context_sha256': canonical_sha256(context), 'expression_guard_sha256': 'd' * 64,
            'expression_expires_at_epoch': int(at.timestamp()) + 30,
            'household_procedure_lineage': {'contract': 'EvaHouseholdProcedureLineage/v1',
                'context': context, 'source_binding': source_binding,
                'configuration': {'config': selected_config}}}
        with owner._engine.begin() as connection:
            owner._authorize(connection, NOW)
            insert_initial_timeline_thread(connection, owner._scope, thread_id=thread,
                checkpoint_id=thread+':1', operation_id=event.source_event_id, phase='sensory_observed',
                status='completed', event_type='SENSORY_EVENT_OBSERVED', occurred_at=at,
                data={'source': event.source, 'source_event_id': event.source_event_id,
                      'event_type': event.event_type, 'payload': event.payload})
            def transition(seq, phase, status, event_type, data, deadline=None):
                transition_timeline_thread(connection, owner._scope, thread_id=thread,
                    expected_revision=seq-1, checkpoint_id=thread+':'+str(seq),
                    operation_id=event.source_event_id, phase=phase, status=status,
                    event_type=event_type, occurred_at=at, deadline=deadline, data=data)
            transition(2, 'expression_pending', 'completed', 'PROACTIVE_EXPRESSION_PREPARED', prepared)
            if mode != 'pending':
                transition(3, 'expression_claimed', 'active', 'PROACTIVE_EXPRESSION_CLAIMED',
                    {'source_event_id': event.source_event_id}, at+timedelta(seconds=300))
            if mode == 'offered':
                offer = 'ha-offered:' + canonical_sha256({'binding': bound.to_payload(),
                    'operation_id': event.source_event_id, 'thread_id': thread, 'revision': 3,
                    'source_event_id': event.source_event_id,
                    'text_sha256': canonical_sha256(prepared['expression']), 'http_status': 204})
                transition(4, 'expression_offered', 'completed', 'PROACTIVE_EXPRESSION_OFFERED',
                    {'channel': 'home_assistant', 'disposition': 'offered',
                     'household_binding': bound.to_payload(), 'source_event_id': event.source_event_id,
                     'provider_message_id': offer})
            elif mode == 'ambiguous':
                transition(4, 'expression_ambiguous', 'needs_help', 'PROACTIVE_EXPRESSION_AMBIGUOUS',
                    {'reason': 'synthetic uncertain outcome'})
        return thread, event.source_event_id
    hazard_thread, hazard_id = seed('hazard-'+phase+str(foreign)+str(hazard_offset)+str(offer_offset),
        NOW+timedelta(seconds=hazard_offset), phase, foreign=foreign)
    offered_thread, offered_id = seed('settled-'+phase+str(foreign)+str(hazard_offset)+str(offer_offset),
        NOW+timedelta(seconds=offer_offset), 'offered')
    with owner._engine.begin() as connection:
        owner._authorize(connection, NOW)
        reference = controller._reference(connection, binding.to_payload(), evidence, for_proof=False)
        expected_hazard = foreign is None and phase != 'pending'
        assert reference['thread_id'] == (hazard_thread if expected_hazard else offered_thread)
        assert reference['source_event_id'] == (hazard_id if expected_hazard else offered_id)
        checkpoint, rows = read_exact_chain(connection, owner._scope, reference['thread_id'])
        prepared = next(row for row in rows if row['event_type'] == 'PROACTIVE_EXPRESSION_PREPARED')
        cb = object.__new__(TrustedHouseholdProvisionCallbacks)
        cb.clock, cb.binding, cb.scope = lambda: now, binding.to_payload(), owner._scope
        assert cb._legacy_effect(({'thread_id': reference['thread_id'],
                                 'source_event_id': reference['source_event_id']},
                                 checkpoint, rows, prepared, None), evidence, 'unused') == (
            'unknown' if expected_hazard and phase in {'expired', 'ambiguous'} else
            'in_flight' if expected_hazard else 'clear')
        proof_reference = controller._reference(connection, binding.to_payload(), evidence, for_proof=True)
        assert proof_reference['thread_id'] == offered_thread
