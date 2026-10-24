"""Exercise enrolled and legacy composition through isolated existing runtime fixtures."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.household_provisions import procedure_provision_id
from eva.application.free_will import ProactiveExpressionClaim
from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.bootstrap.household_initiative_delivery import HouseholdInitiativeDelivery
from eva.contracts.canonical_json import canonical_sha256
from tests.bootstrap import test_household_initiative_loop as existing
from tests.bootstrap import test_initiative_quiet_delivery as quiet

ROOT = Path(__file__).resolve().parents[3]
from eva.bootstrap import household_initiative as composition
from eva.bootstrap import household_provision_host as host


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # Guard the modules actually used by these tests, without freezing legitimate
    # future implementation changes to a historical candidate digest.
    paths = [ROOT / relative for relative in (
        'src/eva/adapters/postgres/household_provisions.py',
        'src/eva/providers/home_assistant_procedure.py',
        'src/eva/application/household_procedure.py',
        'src/eva/bootstrap/household_initiative.py',
        'src/eva/bootstrap/household_provision_host.py',
    )]
    pins = {path: sha(path) for path in paths}
    for path, digest in pins.items(): assert sha(path) == digest
    def forbidden(*args, **kwargs):
        raise AssertionError('No real HTTP, database or model call permitted')
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    import httpx2
    monkeypatch.setattr(httpx2, 'Client', forbidden)
    monkeypatch.setattr(existing, 'composition', composition)
    monkeypatch.setattr(quiet, 'composition', composition)
    monkeypatch.delenv('EVA_HOUSEHOLD_PROCEDURE_FILE', raising=False)
    yield
    for path, digest in pins.items(): assert sha(path) == digest


@pytest.fixture
def wiring(tmp_path, monkeypatch):
    # Reuse the actual existing isolated composition fixture, not a copied loop.
    loop, allow, calls, workers, stores = existing._compose_without_network(monkeypatch)
    engine = sa.create_engine('postgresql+psycopg://invalid.invalid/not_used')
    loop.engine = engine
    policy = loop.voice.policy
    selected = loop._selected()
    binding = HouseholdInitiativeBinding(policy.tenant_id, policy.installation_id,
        policy.household_id, 'household-device:' + selected['device_id'],
        selected['satellite_id'], selected['device_id'], policy.consent_operation_id,
        canonical_sha256(policy.model_dump(mode='json'))).to_payload()
    config = {'contract': 'EvaHouseholdProcedureBinding/v1', 'enabled': True,
        'household_binding': binding, 'source_file': 'fixture.py', 'source_sha256': 'a' * 64,
        'reference_id': 'procedure-reference:isolated', 'source_principal_id': 'owner',
        'review_sha256': 'b' * 64}
    path = tmp_path / 'fixture-config.json'
    path.write_text(json.dumps(config))
    enrollment = {'contract': 'EvaHouseholdProvisionEnrollment/v1',
        'provision_id': procedure_provision_id(config), 'owner_principal_id': 'owner',
        'binding_sha256': canonical_sha256(config)}
    managed = {'household_provision_enrollment': enrollment}
    captured = []
    def callbacks_for(*args):
        captured.append(('callbacks', args))
        return {'authorize': lambda conn, now: None, 'verify_current': lambda conn, evidence: None,
                'verify_realization': lambda conn, evidence: None,
                'effect_status': lambda conn, evidence: 'clear'}
    factory = host.HouseholdProvisionHostFactory(engine,
        load_enrollment=lambda: managed['household_provision_enrollment'], callbacks_for=callbacks_for)
    loop.provision_factory = factory
    monkeypatch.setenv('EVA_HOUSEHOLD_PROCEDURE_FILE', str(path))
    import eva.providers.home_assistant_procedure as provider
    class Proposal:
        def __init__(self, *args, **kwargs):
            self.authorize = kwargs['authorize']
            self.provisions = kwargs.get('provisions')
            captured.append(('proposal', self))
        def validate_expression(self, event, digest):
            captured.append(('validate', event, digest)); return False
        def admit_expression(self, event, digest, claim):
            captured.append(('admit', event, digest, claim)); return admission
        def settle_expression(self, value, **kwargs):
            captured.append(('settle', value, kwargs))
    admission = {'admitted_now': True, 'receipt': {'isolated': 'admission'}}
    monkeypatch.setattr(provider, 'HomeAssistantProcedure', Proposal)
    monkeypatch.setattr(composition, 'HouseholdInitiativeDelivery', HouseholdInitiativeDelivery)
    return SimpleNamespace(loop=loop, binding=binding, config=config, path=path,
        managed=managed, factory=factory, captured=captured, admission=admission, allow=allow)


def test_explicit_none_is_legacy_without_enrollment_or_database(wiring):
    wiring.managed['household_provision_enrollment'] = None
    assert wiring.factory(str(wiring.path), household_binding=wiring.binding) is None
    _, _, delivery, _ = wiring.loop._components_for(wiring.loop._selected())
    proposal = next(value for kind, value in wiring.captured if kind == 'proposal')
    assert proposal.provisions is None
    assert delivery.admit_expression is None and delivery.settle_expression is None
    assert not any(row[0] == 'callbacks' for row in wiring.captured)


def test_required_managed_key_missing_fails_closed_instead_of_legacy(wiring):
    del wiring.managed['household_provision_enrollment']
    with pytest.raises(KeyError): wiring.factory.cache_key(str(wiring.path))
    with pytest.raises(KeyError): wiring.loop._components_for(wiring.loop._selected())
    assert not wiring.captured


@pytest.mark.parametrize('field', ['tenant_id', 'installation_id', 'household_id', 'satellite_id', 'source_owner'])
def test_wrong_scope_or_source_owner_is_rejected_before_adapter_use(wiring, field):
    binding = deepcopy(wiring.binding)
    if field == 'source_owner':
        wiring.managed['household_provision_enrollment']['owner_principal_id'] = 'different-owner'
    else:
        binding[field] = 'different-scope'
    with pytest.raises(ValueError): wiring.factory(str(wiring.path), household_binding=binding)
    assert not wiring.captured


def test_enrollment_current_config_and_path_changes_invalidate_cached_composition(wiring, monkeypatch):
    first = wiring.loop._components_for(wiring.loop._selected())
    assert wiring.loop._components_for(wiring.loop._selected()) is first
    proposal = next(row[1] for row in wiring.captured if row[0] == 'proposal')
    original = deepcopy(wiring.managed['household_provision_enrollment'])
    key = wiring.factory.cache_key(str(wiring.path))
    wiring.managed['household_provision_enrollment'] = None
    assert wiring.factory.cache_key(str(wiring.path)) != key
    assert first[3](existing.NOW) is False
    with pytest.raises(ValueError, match='configuration changed'): proposal.authorize()
    wiring.managed['household_provision_enrollment'] = original
    wiring.config['review_sha256'] = 'c' * 64
    wiring.path.write_text(json.dumps(wiring.config))
    with pytest.raises(ValueError): wiring.factory.cache_key(str(wiring.path))
    original['binding_sha256'] = canonical_sha256(wiring.config)
    assert wiring.factory.cache_key(str(wiring.path)) != key
    assert first[3](existing.NOW) is False
    second = wiring.loop._components_for(wiring.loop._selected())
    assert second is not first
    other_path = wiring.path.with_name('changed-path.json')
    other_path.write_text(json.dumps(wiring.config))
    monkeypatch.setenv('EVA_HOUSEHOLD_PROCEDURE_FILE', str(other_path))
    assert second[3](existing.NOW) is False
    assert wiring.loop._components_for(wiring.loop._selected()) is not second


def test_final_hooks_forward_same_store_event_guard_claim_and_settlement(wiring):
    store, _, delivery, _ = wiring.loop._components_for(wiring.loop._selected())
    claim = ProactiveExpressionClaim('thread', 'operation', 1, 0, 'source', 'Fixture', 1)
    event, reads = object(), []
    def prepared(thread):
        reads.append(('prepared', thread)); return {'expression_guard_sha256': 'd' * 64}
    def source(thread):
        reads.append(('event', thread)); return event
    store._prepared_expression, store._event_from_thread = prepared, source
    assert delivery.validate_expression(claim) is False
    assert delivery.admit_expression(claim) is wiring.admission
    delivery.settle_expression(wiring.admission, outcome='not_dispatched', proof_sha256='e' * 64)
    assert reads == [('prepared', 'thread'), ('event', 'thread')] * 2
    assert ('validate', event, 'd' * 64) in wiring.captured
    admit = next(row for row in wiring.captured if row[0] == 'admit')
    assert admit[1] is event and admit[2] == 'd' * 64 and admit[3] is claim
    settled = next(row for row in wiring.captured if row[0] == 'settle')
    assert settled[1] is wiring.admission
    assert settled[2] == {'outcome': 'not_dispatched', 'proof_sha256': 'e' * 64}
    proposal = next(row[1] for row in wiring.captured if row[0] == 'proposal')
    assert proposal.provisions.verify_realization(None, {}) is None


def test_legacy_effect_and_late_attention_contract_remain_original(monkeypatch):
    # Run existing behavioral assertions through the private class and actual
    # original delivery, with their already isolated HTTP/model/store doubles.
    for transition in ['none', 'final_provenance', 'quiet_hours_begin']:
        quiet.test_pending_message_respects_late_attention_changes(monkeypatch, transition)


@pytest.mark.parametrize('callback_name,result', [('verify_realization', 'd' * 64), ('effect_status', 'clear')])
def test_trusted_callback_mutation_does_not_return_stale_proof_or_effect(wiring, callback_name, result):
    def mutate(connection, evidence):
        wiring.config['review_sha256'] = 'f' * 64
        wiring.path.write_text(json.dumps(wiring.config))
        wiring.managed['household_provision_enrollment']['binding_sha256'] = canonical_sha256(wiring.config)
        return result
    def callbacks_for(*args):
        callbacks = {'authorize': lambda conn, now: None,
                     'verify_current': lambda conn, evidence: None,
                     'verify_realization': lambda conn, evidence: None,
                     'effect_status': lambda conn, evidence: 'clear'}
        callbacks[callback_name] = mutate
        return callbacks
    wiring.factory.callbacks_for = callbacks_for
    adapter = wiring.factory(str(wiring.path), household_binding=wiring.binding)
    with pytest.raises(ValueError, match='managed configuration changed'):
        getattr(adapter, callback_name)(None, {})
