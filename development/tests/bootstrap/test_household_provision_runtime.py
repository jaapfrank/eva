"""Actual runtime/provider handoff; all external/database effects isolated."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.household_provisions import procedure_provision_id
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.bootstrap.household_provision_runtime import configured_household_provision_runtime
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.contracts import ProviderServiceContext
from tests.bootstrap.test_household_initiative_loop import Policy, SAT, DEVICE


@pytest.fixture(autouse=True)
def no_external_database(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('No actual SQL in runtime composition tests')
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)


@pytest.fixture
def host(tmp_path, monkeypatch):
    import eva.bootstrap.household_provision_runtime as module
    import eva.bootstrap.household_provision_host as factory_module
    engine = sa.create_engine('postgresql+psycopg://invalid.invalid/unused')
    policy = Policy()
    binding = HouseholdInitiativeBinding(policy.tenant_id, policy.installation_id,
        policy.household_id, 'household-device:' + DEVICE, SAT, DEVICE,
        policy.consent_operation_id, canonical_sha256(policy.model_dump(mode='json'))).to_payload()
    source = 'def propose(event, binding, *, now): return {}\n'
    (tmp_path / 'source.py').write_text(source)
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    review = tmp_path / 'adoption-review.json'
    review.write_text(json.dumps({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
                                 'source': {'sha256': source_sha}}))
    config = {'contract': 'EvaHouseholdProcedureBinding/v1', 'enabled': True,
        'household_binding': binding, 'source_file': 'source.py', 'source_sha256': source_sha,
        'reference_id': 'procedure-reference:fixture', 'source_principal_id': 'owner',
        'review_sha256': hashlib.sha256(review.read_bytes()).hexdigest()}
    source_path = tmp_path / 'current.json'; source_path.write_text(json.dumps(config))
    enrollment_path = tmp_path / 'enrollment.json'
    enrollment_path.write_text(json.dumps({'household_provision_enrollment': None}))
    enrollment_path.chmod(0o600)
    allowed = [True]
    calls, records, working_proof = [], {}, [None]
    def authorize(connection, now):
        calls.append(('owner_authorize',))
        if not allowed[0]: raise PermissionError('current owner revoked')
    owner = SimpleNamespace(_engine=engine, _owner='owner', _tenant_id=policy.tenant_id,
        _installation_id=policy.installation_id,
        _scope=external_timeline_scope(policy.tenant_id, policy.installation_id, 'fixture'),
        _authorize=authorize)
    controller = configured_household_provision_runtime(engine, owner_loader=lambda: owner,
        environ={'EVA_HOUSEHOLD_PROVISION_ENROLLMENT_FILE': str(enrollment_path),
                 'EVA_HOUSEHOLD_PROCEDURE_FILE': str(source_path)})
    context = SimpleNamespace(shared_output=SimpleNamespace(policy=policy, validate_current=lambda: None),
        work_server=SimpleNamespace(authority=SimpleNamespace(validate_current=lambda: None)),
        current_authority=lambda: None)
    controller.factory_for(SimpleNamespace(), context)
    monkeypatch.setattr(controller, '_working_proof', lambda config: working_proof[0])
    monkeypatch.setattr(controller, '_reference', lambda *args, **kwargs: None)
    class Legacy:
        def __init__(self, path, *, engine, household, authorize, provisions, **kwargs):
            self.path, self.engine, self.household = path, engine, household
            self.authorize, self.provisions = authorize, provisions
        def _current(self):
            self.authorize()
            current = json.loads(source_path.read_text())
            actual = (tmp_path / current['source_file']).read_text()
            if hashlib.sha256(actual.encode()).hexdigest() != current['source_sha256']:
                raise ValueError('source changed')
            return actual, 'a' * 64
    monkeypatch.setattr(module, 'HomeAssistantProcedure', Legacy)
    class Store:
        on_register = None
        def __init__(self, engine, **kwargs): self.options = kwargs
        def inspect(self, provision_id, *, now):
            self.options['authorize'](None, now)
            state = deepcopy(records.get(provision_id))
            if state is not None:
                state['effect_status'] = self.options['effect_status'](None, state['evidence'])
            return state
        def register(self, provision_id, evidence, *, request_id, now):
            self.options['authorize'](None, now)
            self.options['verify_current'](None, evidence)
            calls.append(('register', provision_id, deepcopy(evidence), request_id))
            state = {'provision_id': provision_id, 'status': 'proposed', 'revision': 1,
                'evidence': deepcopy(evidence), 'adoption': None, 'verification_sha256': None,
                'owner_principal_id': self.options['owner_principal_id'],
                'household_binding_sha256': canonical_sha256(self.options['household_binding']),
                'stopped': False, 'pending_effects': {}}
            records[provision_id] = state
            if Store.on_register is not None: Store.on_register()
            return deepcopy(state)
        def command(self, provision_id, action, **kwargs):
            self.options['authorize'](None, kwargs['now'])
            calls.append(('command', provision_id, action, kwargs))
            return {'provision_id': provision_id, 'action': action}
    monkeypatch.setattr(module, 'PostgresHouseholdProvisions', Store)
    monkeypatch.setattr(factory_module, 'PostgresHouseholdProvisions', Store)
    return SimpleNamespace(controller=controller, engine=engine, binding=binding,
        policy=policy, source_path=source_path, config=config, enrollment_path=enrollment_path,
        records=records, calls=calls, owner=owner, allowed=allowed, store_class=Store,
        working_proof=working_proof)


def test_absent_setting_preserves_legacy_but_explicit_invalid_setting_does_not(host):
    assert configured_household_provision_runtime(host.engine, owner_loader=lambda: host.owner,
                                                  environ={}) is None
    with pytest.raises(ValueError):
        configured_household_provision_runtime(host.engine, owner_loader=lambda: host.owner,
            environ={'EVA_HOUSEHOLD_PROVISION_ENROLLMENT_FILE': ''})
    assert host.controller.factory(str(host.source_path), household_binding=host.binding) is None
    assert host.records == {} and host.calls == []


@pytest.mark.parametrize('failure', ['missing_file', 'missing_key', 'invalid_pointer'])
def test_configured_missing_or_invalid_enrollment_fails_closed(host, failure):
    if failure == 'missing_file': host.enrollment_path.unlink()
    elif failure == 'missing_key': host.enrollment_path.write_text('{}')
    else: host.enrollment_path.write_text(json.dumps({'household_provision_enrollment': False}))
    with pytest.raises((OSError, ValueError)):
        host.controller.overview()
    assert not host.records


def test_explicit_registration_uses_host_provenance_and_enrolls_only_proposed(host):
    result = host.controller.register('owner-register')
    pointer = json.loads(host.enrollment_path.read_text())['household_provision_enrollment']
    assert pointer is result['enrollment'] is None  # No proof: preserve authorized legacy operation.
    assert result['receipt']['evidence']['binding_sha256'] == canonical_sha256(host.config)
    assert result['receipt']['status'] == 'proposed'
    evidence = result['receipt']['evidence']
    assert evidence['realization_receipt_sha256'] is None
    assert evidence['source_sha256'] == host.config['source_sha256']
    overview = host.controller.overview()
    assert overview['provision']['effect_status'] == 'unknown'
    assert overview['actual_working_proof_available'] is False
    assert result['route_handoff'] == 'proposed_saved_awaiting_working_proof'


def test_changed_source_handoff_retains_record_without_pointer_activation(host):
    def change():
        host.config['review_sha256'] = 'f' * 64
        host.source_path.write_text(json.dumps(host.config))
    host.store_class.on_register = change
    with pytest.raises(ValueError, match='handoff changed'):
        host.controller.register('owner-register-changing-source')
    assert list(host.records.values())[0]['status'] == 'proposed'
    assert json.loads(host.enrollment_path.read_text())['household_provision_enrollment'] is None


def test_current_owner_and_household_are_required_for_registration(host):
    host.allowed[0] = False
    with pytest.raises(PermissionError): host.controller.register('revoked-owner')
    assert not host.records
    host.allowed[0] = True
    host.policy.household_id = 'different-household'
    with pytest.raises(ValueError, match='household authority'):
        host.controller.register('wrong-household')
    assert not host.records


def test_commands_are_bound_to_one_host_source_and_forward_revision_without_proof(host):
    host.working_proof[0] = 'd' * 64
    host.controller.register('owner-register')
    provision_id = procedure_provision_id(host.config)
    result = host.controller.command(provision_id, 'pause', 1, canonical_sha256(host.config), 'owner-pause')
    assert result == {'provision_id': provision_id, 'action': 'pause'}
    command = next(row for row in host.calls if row[0] == 'command')
    assert command[3]['expected_revision'] == 1 and command[3]['request_id'] == 'owner-pause'
    assert 'proof' not in command[3] and 'evidence' not in command[3]
    with pytest.raises(LookupError):
        host.controller.command('foreign-provision', 'resume', 1, 'a' * 64, 'foreign-request')


def test_actual_runtime_dashboard_methods_use_the_same_bound_controller():
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    with pytest.raises(RuntimeError): runtime.dashboard_household_provisions()
    seen = []
    runtime._household_provision_runtime = SimpleNamespace(
        overview=lambda: {'from': 'same-controller'}, register=lambda request: seen.append(('register', request)),
        command=lambda *args: seen.append(('command', args)))
    assert runtime.dashboard_household_provisions() == {'from': 'same-controller'}
    runtime.dashboard_register_household_provision('request')
    runtime.dashboard_household_provision_command('provision', 'pause', 3, 'a' * 64, 'request2')
    assert seen == [('register', 'request'), ('command', ('provision', 'pause', 3, 'a' * 64, 'request2'))]


def _partial_registration(host):
    host.working_proof[0] = 'd' * 64
    result = host.controller.register('first-owner-registration')
    host.enrollment_path.write_text(json.dumps({'household_provision_enrollment': None}))
    return result['receipt']['provision_id']


def _fake_recovery_transaction(host, monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    # Only this fixture's connection is isolated; other real Engine calls stay forbidden.
    monkeypatch.setattr(host.engine, 'begin', lambda: Connection())


def test_partial_registration_commands_cannot_mutate_unenrolled_record(host):
    provision_id = _partial_registration(host)
    before = deepcopy(host.records)
    with pytest.raises(ValueError, match='enrollment is incomplete'):
        host.controller.command(provision_id, 'pause', 1, canonical_sha256(host.config), 'pause-partial')
    assert host.records == before
    assert not any(call[0] == 'command' for call in host.calls)
    assert json.loads(host.enrollment_path.read_text())['household_provision_enrollment'] is None


def test_partial_registration_can_finish_after_reload_with_new_request_without_new_record(host, monkeypatch):
    provision_id = _partial_registration(host)
    before = deepcopy(host.records)
    _fake_recovery_transaction(host, monkeypatch)
    recovered = host.controller.register('new-owner-request-after-reload')
    assert recovered['recovered_registration'] is True
    assert recovered['request_id'] == 'new-owner-request-after-reload'
    assert host.records == before and len(host.records) == 1
    assert sum(call[0] == 'register' for call in host.calls) == 1
    assert recovered['receipt']['status'] == 'proposed'
    assert recovered['receipt']['adoption'] is None
    assert recovered['actual_working_proof_available'] is True
    pointer = json.loads(host.enrollment_path.read_text())['household_provision_enrollment']
    assert pointer['provision_id'] == provision_id
    assert pointer['binding_sha256'] == canonical_sha256(host.config)


@pytest.mark.parametrize('drift', ['source', 'owner'])
def test_partial_registration_recovery_rechecks_current_source_and_owner(host, monkeypatch, drift):
    _partial_registration(host)
    before = deepcopy(host.records)
    _fake_recovery_transaction(host, monkeypatch)
    if drift == 'source':
        (host.source_path.parent / 'source.py').write_text('changed since original registration')
    else:
        host.allowed[0] = False
    with pytest.raises((ValueError, PermissionError)):
        host.controller.register('recovery-with-drift')
    assert host.records == before
    assert json.loads(host.enrollment_path.read_text())['household_provision_enrollment'] is None


@pytest.mark.parametrize('enabled', [False, True])
def test_actual_provider_services_inject_optional_factory_from_frozen_context(monkeypatch, enabled):
    from eva.providers.home_assistant import HomeAssistantProviderFactory
    import eva.bootstrap.household_initiative as composition
    import eva.bootstrap.native_work_delivery as native
    seen = []
    marker = object()
    monkeypatch.setattr(native, 'NativeWorkDelivery', lambda *a: SimpleNamespace(run_once=lambda: None))
    def loop(**kwargs):
        seen.append(kwargs); return SimpleNamespace(run_once=lambda: None)
    monkeypatch.setattr(composition, 'HouseholdInitiativeLoop', loop)
    def builder(settings, context):
        seen.append((settings, context)); return marker
    context = ProviderServiceContext(engine=object(), work_server=object(), shared_output=object(),
        model=object(), memory=object(), identity=object(), budget=object(), principal_id='owner',
        current_authority=lambda: None, source_store=object(), foreground_active=lambda: False)
    if enabled: context = replace(context, household_provision_factory=builder)
    settings = object()
    result = HomeAssistantProviderFactory.build_services(settings, context)
    assert result.owns_sensory_decisions is True and len(result.tasks) == 2
    assert seen[-1]['provision_factory'] is (marker if enabled else None)
    if enabled: assert seen[0] == (settings, context)


def test_proposed_without_proof_stays_legacy_until_explicit_proven_handoff(host, monkeypatch):
    first = host.controller.register('save-proposal')
    assert first['enrollment'] is None and first['actual_working_proof_available'] is False
    assert host.controller.factory(str(host.source_path), household_binding=host.binding) is None
    _fake_recovery_transaction(host, monkeypatch)
    second = host.controller.register('still-no-proof')
    assert second['enrollment'] is None and len(host.records) == 1
    host.working_proof[0] = 'd' * 64
    assert host.controller.overview()['actual_working_proof_available'] is True
    # A GET and receipt alone never enroll or activate.
    assert json.loads(host.enrollment_path.read_text())['household_provision_enrollment'] is None
    last = host.controller.register('explicitly-finish')
    assert last['enrollment'] is not None and last['recovered_registration'] is True
    assert len(host.records) == 1 and sum(c[0] == 'register' for c in host.calls) == 1
    assert last['receipt']['status'] == 'proposed' and last['receipt']['adoption'] is None


def test_working_proof_change_during_managed_handoff_never_writes_pointer(host, monkeypatch):
    def proof(config):
        host.config['review_sha256'] = 'f' * 64
        host.source_path.write_text(json.dumps(host.config))
        return 'd' * 64
    monkeypatch.setattr(host.controller, '_working_proof', proof)
    with pytest.raises(ValueError, match='handoff changed'):
        host.controller.register('proof-read-source-drift')
    assert json.loads(host.enrollment_path.read_text())['household_provision_enrollment'] is None
    assert list(host.records.values())[0]['adoption'] is None
