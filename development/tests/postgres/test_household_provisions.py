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


@pytest.fixture
def version_control(provision):
    store, control, build = provision
    active(provision)
    target = {**EVIDENCE, 'binding_sha256': '1' * 64, 'source_sha256': '2' * 64,
              'review_sha256': '3' * 64, 'reference_id': 'procedure-reference:next',
              'realization_receipt_sha256': None}
    installed = [EVIDENCE['binding_sha256']]
    calls = []
    def verify_current(conn, evidence):
        calls.append(('current', evidence['binding_sha256']))
        if evidence['binding_sha256'] != installed[0]:
            raise ValueError('actual installed source differs')
    def verify_version(conn, evidence):
        calls.append(('reviewed_version', evidence['binding_sha256']))
        if evidence['binding_sha256'] not in {EVIDENCE['binding_sha256'], target['binding_sha256']}:
            raise ValueError('source/review resolver refuses unknown target')
    store.verify_current, store.verify_version = verify_current, verify_version
    return store, control, target, installed, calls


def _stage_version(fixture):
    store, control, target, installed, calls = fixture
    result = store.stage_version('fixture-provision', target, expected_revision=2,
        binding_sha256='a' * 64, request_id='stage-next', now=NOW)
    return result, store._version_id(target)


def test_source_version_change_and_undo_use_one_identity_and_real_target_check(version_control):
    store, control, target, installed, calls = version_control
    staged, version = _stage_version(version_control)
    assert staged['status'] == 'active' and staged['evidence'] == EVIDENCE
    assert len(staged['versions']) == 2 and staged['current_version'] != version
    assert store.stage_version('fixture-provision', target, expected_revision=2,
        binding_sha256='a' * 64, request_id='stage-next', now=NOW) == staged
    pending = store.begin_change('fixture-provision', version, expected_revision=staged['revision'],
        binding_sha256='a' * 64, request_id='begin-change', now=NOW)
    assert pending['status'] == 'paused' and pending['pending_change']['to_version'] == version
    with pytest.raises(CheckpointConflict):
        admit(store, pending['revision'])
    with pytest.raises(CheckpointConflict, match='change is incomplete'):
        command(store, 'resume', pending['revision'], 'no-premature-resume')
    with pytest.raises(ValueError, match='actual installed'):
        store.finish_change('fixture-provision', expected_revision=pending['revision'],
            binding_sha256='a' * 64, request_id='confirm-change', now=NOW)
    installed[0] = target['binding_sha256']  # Host file-apply is separate from this store test.
    changed = store.finish_change('fixture-provision', expected_revision=pending['revision'],
        binding_sha256='a' * 64, request_id='confirm-change', now=NOW)
    assert changed['status'] == 'active' and changed['current_version'] == version
    assert changed['adoption'] == staged['adoption'] and changed['provision_id'] == staged['provision_id']
    assert store.finish_change('fixture-provision', expected_revision=pending['revision'],
        binding_sha256='a' * 64, request_id='confirm-change', now=NOW) == changed
    undo = store.begin_change('fixture-provision', undo=True, expected_revision=changed['revision'],
        binding_sha256=target['binding_sha256'], request_id='undo-change', now=NOW)
    installed[0] = EVIDENCE['binding_sha256']
    restored = store.finish_change('fixture-provision', expected_revision=undo['revision'],
        binding_sha256=target['binding_sha256'], request_id='confirm-undo', now=NOW)
    assert restored['status'] == 'active' and restored['evidence'] == EVIDENCE
    assert restored['current_version'] == staged['current_version']
    history = store.version_history('fixture-provision', now=NOW, limit=4)
    assert len(history['entries']) == 4 and history['earlier_entries'] > 0
    assert history['entries'][-1]['version_id'] == staged['current_version']
    assert all('source' not in item and 'proof' not in item for item in history['entries'])


@pytest.mark.parametrize('owner_action', ['pause', 'stop'])
def test_version_handoff_preserves_owner_pause_or_stop_during_change(version_control, owner_action):
    store, control, target, installed, calls = version_control
    staged, version = _stage_version(version_control)
    pending = store.begin_change('fixture-provision', version, expected_revision=staged['revision'],
        binding_sha256='a' * 64, request_id='begin-for-owner-action', now=NOW)
    owner_receipt = command(store, owner_action, pending['revision'], 'owner-during-change')
    installed[0] = target['binding_sha256']
    result = store.finish_change('fixture-provision', expected_revision=owner_receipt['revision'],
        binding_sha256='a' * 64, request_id='finish-preserving-owner-action', now=NOW)
    assert result['status'] == 'paused' and result['adoption'] == staged['adoption']
    assert result['stopped'] is (owner_action == 'stop')
    assert result['pending_change'] is None


@pytest.mark.parametrize('reason', ['unknown_effect', 'in_flight', 'unreviewed', 'forged_proof'])
def test_version_cannot_bypass_uncertainty_review_or_host_proof(version_control, reason):
    store, control, target, installed, calls = version_control
    if reason == 'unreviewed':
        store.verify_version = None
        with pytest.raises(RuntimeError, match='resolver is unavailable'):
            _stage_version(version_control)
    elif reason == 'forged_proof':
        target['realization_receipt_sha256'] = 'f' * 64
        with pytest.raises(ValueError, match='cannot supply realization proof'):
            _stage_version(version_control)
    else:
        staged, version = _stage_version(version_control)
        control['effect'] = 'unknown' if reason == 'unknown_effect' else 'in_flight'
        with pytest.raises(CheckpointConflict, match='not confirmed clear'):
            store.begin_change('fixture-provision', version, expected_revision=staged['revision'],
                binding_sha256='a' * 64, request_id='cannot-change-with-effect', now=NOW)
        assert store.inspect('fixture-provision', now=NOW)['pending_change'] is None
    assert installed[0] == EVIDENCE['binding_sha256']


def test_new_version_without_real_working_proof_does_not_become_active(version_control):
    store, control, target, installed, calls = version_control
    staged, version = _stage_version(version_control)
    pending = store.begin_change('fixture-provision', version, expected_revision=staged['revision'],
        binding_sha256='a' * 64, request_id='begin-unverified-target', now=NOW)
    installed[0] = target['binding_sha256']; control['verified'] = None
    result = store.finish_change('fixture-provision', expected_revision=pending['revision'],
        binding_sha256='a' * 64, request_id='finish-unverified-target', now=NOW)
    assert result['status'] == 'proposed' and result['verification_sha256'] is None
    assert result['adoption'] == staged['adoption']


@pytest.fixture
def trial_control(provision):
    store, control, build=provision
    control.update(guard='9'*64,trial_proof=None)
    store.current_guard=lambda conn,evidence:control['guard']
    store.verify_trial_realization=lambda conn,evidence,trial:(control['trial_proof'] if any(
        row['outcome']=='completed' for row in trial['claims'].values()) else None)
    return store,control


def _authorize_trial(fixture):
    store,control=fixture
    return store.authorize_trial('fixture-provision',expires_at_epoch=int(NOW.timestamp())+3600,
        max_effects=1,expected_revision=1,binding_sha256='a'*64,request_id='owner-start-trial',now=NOW)


def _evaluated_trial(fixture):
    store,control=fixture; trial=_authorize_trial(fixture)
    claim=admit(store,trial['revision'],'trial-effect')
    settled=store.settle_effect('fixture-provision',admission_id=claim['receipt']['admission_id'],
        outcome='completed',proof_sha256='4'*64,request_id='real-offer',now=NOW)
    control['trial_proof']='5'*64
    return store.evaluate_trial('fixture-provision',working=True,useful=True,
        feedback='Deze bestaande voorziening werkt en helpt ons.',expected_revision=settled['revision'],
        binding_sha256='a'*64,request_id='real-owner-feedback',now=NOW)


def test_prior_trial_admission_evaluation_notice_and_ack_are_separate(trial_control):
    store,control=trial_control
    evaluated=_evaluated_trial(trial_control)
    assert evaluated['status']=='proposed' and evaluated['adoption'] is None
    assert evaluated['trial']['phase']=='evaluated_awaiting_notice'
    assert evaluated['trial']['effect_count']==1
    notice=evaluated['trial']['notice']
    assert notice['owner_principal_id']=='owner' and notice['acknowledged_at'] is None
    with pytest.raises(ValueError,match='server notice'):
        store.acknowledge_trial_notice('fixture-provision',notice_id=notice['notice_id'],nonce='f'*64,
            expected_revision=evaluated['revision'],binding_sha256='a'*64,request_id='forged-ack',now=NOW)
    adopted=store.acknowledge_trial_notice('fixture-provision',notice_id=notice['notice_id'],nonce=notice['nonce'],
        expected_revision=evaluated['revision'],binding_sha256='a'*64,request_id='actual-owner-render-ack',now=NOW)
    assert adopted['status']=='active' and adopted['adoption']['method']=='prior_authorized_trial'
    assert adopted['adoption']['trial_id']==evaluated['trial']['trial_id']
    assert store.acknowledge_trial_notice('fixture-provision',notice_id=notice['notice_id'],nonce=notice['nonce'],
        expected_revision=evaluated['revision'],binding_sha256='a'*64,request_id='actual-owner-render-ack',now=NOW)==adopted


def test_trial_cannot_count_replayed_or_more_than_authorized_effect(trial_control):
    store,control=trial_control; trial=_authorize_trial(trial_control)
    first=admit(store,trial['revision'],'first-trial-admission')
    assert first['admitted_now'] is True
    assert admit(store,trial['revision'],'first-trial-admission')['admitted_now'] is False
    settled=store.settle_effect('fixture-provision',admission_id=first['receipt']['admission_id'],
        outcome='not_dispatched',proof_sha256='4'*64,request_id='no-effect-after-admission',now=NOW)
    assert store.inspect('fixture-provision',now=NOW)['trial']['effect_count']==1
    with pytest.raises(CheckpointConflict,match='not dispatchable'):
        store.admit_effect('fixture-provision',{**CLAIM,'source_event_id':'another-source'},
            expected_revision=settled['revision'],binding_sha256='a'*64,request_id='beyond-grant',now=NOW)


def test_old_global_proof_and_owner_opinion_are_not_post_grant_working_evidence(trial_control):
    store,control=trial_control; control['verified']='e'*64;trial=_authorize_trial(trial_control)
    with pytest.raises(CheckpointConflict,match='exact completed trial'):
        store.evaluate_trial('fixture-provision',working=True,useful=True,feedback='Lijkt goed',
            expected_revision=trial['revision'],binding_sha256='a'*64,request_id='not-proof',now=NOW)
    state=store.inspect('fixture-provision',now=NOW)
    assert state['status']=='proposed' and state['trial']['notice'] is None and state['adoption'] is None


def test_negative_real_owner_feedback_is_retained_without_implicit_adoption(trial_control):
    store,control=trial_control;trial=_authorize_trial(trial_control)
    result=store.evaluate_trial('fixture-provision',working=True,useful=False,
        feedback='Werkt maar helpt ons niet.',expected_revision=trial['revision'],binding_sha256='a'*64,
        request_id='owner-negative-feedback',now=NOW)
    assert result['status']=='paused' and result['adoption'] is None
    assert result['trial']['evaluation']['feedback']=='Werkt maar helpt ons niet.'
    assert result['trial']['notice'] is None


@pytest.mark.parametrize('fault',['guard','proof','unknown','pause','stop','expiry','owner'])
def test_trial_notice_never_activates_after_authority_uncertainty_or_owner_pause(trial_control,fault):
    from datetime import timedelta
    store,control=trial_control; evaluated=_evaluated_trial(trial_control)
    notice=evaluated['trial']['notice'];revision=evaluated['revision'];now=NOW
    if fault=='guard':control['guard']='6'*64
    elif fault=='proof':control['trial_proof']=None
    elif fault=='unknown':control['effect']='unknown'
    elif fault in {'pause','stop'}:revision=command(store,fault,revision,'owner-after-evaluation')['revision']
    elif fault=='expiry':now=NOW+timedelta(seconds=3601)
    else:control['allowed']=False
    with pytest.raises((CheckpointConflict,PermissionError)):
        store.acknowledge_trial_notice('fixture-provision',notice_id=notice['notice_id'],nonce=notice['nonce'],
            expected_revision=revision,binding_sha256='a'*64,request_id='stale-notice',now=now)
    control['allowed']=True
    state=store.inspect('fixture-provision',now=NOW)
    assert state['status']!='active' and state['adoption'] is None
