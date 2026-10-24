from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions
from eva.adapters.postgres.operational_timeline import CheckpointConflict


NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
EVIDENCE = {'binding_sha256': 'a' * 64, 'source_sha256': 'b' * 64,
            'review_sha256': 'c' * 64, 'reference_id': 'procedure-reference:fixture',
            'realization_receipt_sha256': 'd' * 64}
CLAIM = {'thread_id': 'sensory:fixture', 'operation_id': 'expression:fixture',
         'revision': 3, 'source_event_id': 'source:fixture'}


@pytest.fixture
def provision(postgres_engine, request):
    control = {'allowed': True, 'current': True, 'verified': None, 'effect': 'clear'}
    def authorize(conn, now):
        if not control['allowed']:
            raise PermissionError('fixture owner authority revoked')
    def verify(conn, evidence):
        if not control['current']:
            raise ValueError('fixture current source changed')
    def build(**kw):
        return PostgresHouseholdProvisions(postgres_engine,
            tenant_id='e112-' + request.node.name, installation_id='home',
            owner_principal_id=kw.get('owner', 'owner'),
            household_binding=kw.get('household', {'household_id': 'family'}),
            authorize=authorize, verify_current=verify,
            verify_realization=lambda conn, evidence: control['verified'],
            effect_status=lambda conn, evidence: control['effect'])
    store = build()
    store.register('fixture-provision', EVIDENCE, request_id='register', now=NOW)
    return store, control, build


def command(store, action, revision, request_id=None):
    return store.command('fixture-provision', action, expected_revision=revision,
                         binding_sha256='a' * 64, request_id=request_id or action, now=NOW)


def active(provision):
    store, control, _ = provision
    control['verified'] = 'e' * 64
    return command(store, 'adopt', 1)


def admit(store, revision, request_id='admit'):
    return store.admit_effect('fixture-provision', CLAIM, expected_revision=revision,
                             binding_sha256='a' * 64, request_id=request_id, now=NOW)


def test_registration_adoption_and_verified_resume_are_distinct(provision):
    store, control, build = provision
    assert store.inspect('fixture-provision', now=NOW)['status'] == 'proposed'
    accepted = command(store, 'adopt', 1)
    assert accepted['adoption'] and accepted['status'] == 'proposed'
    assert accepted['verification_sha256'] is None
    assert command(store, 'adopt', 1) == accepted  # Same original receipt, not another append.
    command(store, 'pause', 2)
    with pytest.raises(CheckpointConflict, match='verified realization'):
        command(store, 'resume', 3)
    control['verified'] = 'e' * 64
    resumed = command(build(), 'resume', 3)
    assert resumed['status'] == 'active' and resumed['revision'] == 4
    assert build().inspect('fixture-provision', now=NOW)['revision'] == 4


def test_absent_realization_is_proposed_and_cannot_resume_without_real_proof(provision):
    store, _, _ = provision
    evidence = {**EVIDENCE, 'realization_receipt_sha256': None}
    registered = store.register('unverified-provision', evidence, request_id='register-none', now=NOW)
    assert registered['status'] == 'proposed' and registered['verification_sha256'] is None
    assert registered['evidence']['realization_receipt_sha256'] is None
    adopted = store.command('unverified-provision', 'adopt', expected_revision=1,
        binding_sha256='a' * 64, request_id='adopt-none', now=NOW)
    assert adopted['status'] == 'proposed' and adopted['adoption']
    assert adopted['verification_sha256'] is None
    store.command('unverified-provision', 'pause', expected_revision=2,
        binding_sha256='a' * 64, request_id='pause-none', now=NOW)
    with pytest.raises(CheckpointConflict, match='verified realization'):
        store.command('unverified-provision', 'resume', expected_revision=3,
            binding_sha256='a' * 64, request_id='resume-none', now=NOW)
    assert store.inspect('unverified-provision', now=NOW)['status'] == 'paused'
    for key in ('binding_sha256', 'source_sha256', 'review_sha256'):
        with pytest.raises(ValueError, match='digest'):
            store.register('invalid-' + key, {**evidence, key: None},
                           request_id='bad-' + key, now=NOW)


def test_replay_changed_request_revision_and_source_change_fail_closed(provision):
    store, control, _ = provision
    active(provision)
    with pytest.raises(ValueError, match='request changed'):
        command(store, 'pause', 2, request_id='adopt')
    with pytest.raises(CheckpointConflict, match='revision'):
        command(store, 'pause', 1)
    command(store, 'pause', 2)
    control['current'] = False
    with pytest.raises(ValueError, match='source changed'):
        command(store, 'resume', 3)
    assert store.inspect('fixture-provision', now=NOW)['status'] == 'paused'


@pytest.mark.parametrize('action', ['stop', 'retire'])
def test_stop_retirement_and_rejection_cannot_resume(provision, action):
    store, _, _ = provision
    active(provision)
    receipt = command(store, action, 2)
    with pytest.raises(CheckpointConflict):
        command(store, 'resume', receipt['revision'])
    with pytest.raises(CheckpointConflict):
        admit(store, receipt['revision'])


def test_reject_is_only_for_unadopted_proposals_and_retirement_keeps_history(provision):
    store, _, build = provision
    store.register('unadopted-provision', EVIDENCE, request_id='register-rejected', now=NOW)
    rejected = store.command('unadopted-provision', 'reject', expected_revision=1,
        binding_sha256='a' * 64, request_id='reject-proposal', now=NOW)
    assert rejected['status'] == 'rejected' and rejected['adoption'] is None
    adopted = active(provision)
    with pytest.raises(CheckpointConflict, match='unadopted proposal'):
        command(store, 'reject', 2)
    command(store, 'pause', 2)
    with pytest.raises(CheckpointConflict, match='unadopted proposal'):
        command(store, 'reject', 3)
    retired = command(store, 'retire', 3)
    assert retired['status'] == 'retired' and retired['adoption'] == adopted['adoption']
    assert build().inspect('fixture-provision', now=NOW)['adoption'] == adopted['adoption']
    history = store.timeline.events(store._thread('fixture-provision'))
    assert history[1].event_type == 'HOUSEHOLD_PROVISION_ADOPT'
    assert history[-1].event_type == 'HOUSEHOLD_PROVISION_RETIRE'


def test_owner_household_and_current_authority_remain_separate(provision):
    store, control, build = provision
    from eva.adapters.postgres.operational_timeline import OperationalTimelineError
    with pytest.raises(OperationalTimelineError, match='owner or state'):
        build(owner='daughter').inspect('fixture-provision', now=NOW)
    with pytest.raises(OperationalTimelineError, match='owner or state'):
        build(household={'household_id': 'another'}).inspect('fixture-provision', now=NOW)
    control['allowed'] = False
    with pytest.raises(PermissionError):
        store.register('fixture-provision', EVIDENCE, request_id='register', now=NOW)
    with pytest.raises(PermissionError):
        command(store, 'pause', 1)


def test_final_admission_serializes_pause_and_never_replays_an_effect(provision):
    store, _, _ = provision
    active(provision)
    first = admit(store, 2)
    assert first['admitted_now'] is True
    assert admit(store, 2) == {'admitted_now': False, 'receipt': first['receipt']}
    assert admit(store, 3, request_id='different-request')['admitted_now'] is False
    paused = command(store, 'pause', 3)
    assert paused['effect_status'] == 'in_flight' and paused['in_flight_effect_may_complete']
    admission_id = first['receipt']['admission_id']
    settled = store.settle_effect('fixture-provision', admission_id=admission_id,
        outcome='unknown', proof_sha256='f' * 64, request_id='uncertain', now=NOW)
    with pytest.raises(CheckpointConflict, match='not confirmed clear'):
        command(store, 'resume', settled['revision'])
    completed = store.settle_effect('fixture-provision', admission_id=admission_id,
        outcome='completed', proof_sha256='1' * 64, request_id='readback', now=NOW)
    resumed = command(store, 'resume', completed['revision'])
    assert resumed['status'] == 'active'
    assert admit(store, resumed['revision'], request_id='still-same-claim')['admitted_now'] is False


def test_pause_before_admission_prevents_dispatch(provision):
    store, _, _ = provision
    active(provision)
    command(store, 'pause', 2)
    with pytest.raises(CheckpointConflict):
        admit(store, 2)
    with pytest.raises(CheckpointConflict, match='not dispatchable'):
        admit(store, 3)


def test_concurrent_pause_waits_for_an_already_committing_admission(provision):
    store, control, build = provision
    active(provision)
    inside, release = Event(), Event()
    original = store.verify_current
    def blocked(conn, evidence):
        inside.set()
        assert release.wait(5)
        original(conn, evidence)
    store.verify_current = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitting = pool.submit(admit, store, 2)
        assert inside.wait(5)
        paused = pool.submit(command, build(), 'pause', 3)
        release.set()
        assert admitting.result(timeout=5)['admitted_now'] is True
        receipt = paused.result(timeout=5)
    assert receipt['status'] == 'paused' and receipt['effect_status'] == 'in_flight'
