from copy import deepcopy
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.household_provisions import procedure_provision_id
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.application.household_procedure import HouseholdProcedureModel, HouseholdProvisionInactive
from eva.application.free_will import FreeWillDecision
from eva.contracts.canonical_json import canonical_sha256
from tests.providers.test_home_assistant_procedure import configured


def enrolled(tmp_path, monkeypatch):
    import json
    p, event, now, calls, output, path = configured(tmp_path, monkeypatch)
    config = json.loads(path.read_text())
    state = {'provision_id': procedure_provision_id(config), 'revision': 2,
        'owner_principal_id': 'owner', 'household_binding_sha256': canonical_sha256(config['household_binding']),
        'status': 'active', 'stopped': False, 'effect_status': 'clear',
        'adoption': {'owner_principal_id': 'owner'}, 'verification_sha256': 'd' * 64,
        'evidence': {key: config[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}}
    state['evidence']['binding_sha256'] = canonical_sha256(config)
    p.provisions = SimpleNamespace(inspect=lambda *a, **kw: deepcopy(state))
    return p, event, now, calls, output, path, state


@pytest.mark.parametrize('status', ['proposed', 'paused', 'rejected', 'retired'])
def test_enrolled_inactive_provision_never_reaches_executor(tmp_path, monkeypatch, status):
    p, event, now, calls, _, _, state = enrolled(tmp_path, monkeypatch)
    state['status'] = status
    assert p(event, now=now) is None
    assert calls == []


def test_enrolled_missing_state_is_not_legacy_and_bad_source_is_not_hidden(tmp_path, monkeypatch):
    p, event, now, calls, _, path, state = enrolled(tmp_path, monkeypatch)
    p.provisions.inspect = lambda *a, **kw: None
    with pytest.raises(LookupError, match='state is missing'):
        p(event, now=now)
    p.provisions.inspect = lambda *a, **kw: state
    state['status'] = 'paused'
    (path.parent / 'extension.py').write_text('changed source')
    with pytest.raises(ValueError, match='source changed'):
        p(event, now=now)
    assert not calls


def test_pause_during_binding_is_rechecked_before_executor(tmp_path, monkeypatch):
    p, event, now, calls, _, _, state = enrolled(tmp_path, monkeypatch)
    original = p._binding
    def pause(observation):
        binding = original(observation)
        state['status'] = 'paused'; state['revision'] += 1
        return binding
    p._binding = pause
    assert p(event, now=now) is None and not calls


def test_late_guard_denies_pause_and_changed_version_before_effect(tmp_path, monkeypatch):
    p, event, now, calls, _, _, state = enrolled(tmp_path, monkeypatch)
    context = p(event, now=now)
    assert len(calls) == 1
    state['status'] = 'paused'; state['revision'] += 1
    assert p.validate_expression(event, context['binding_sha256']) is False
    state['status'] = 'active'
    with pytest.raises(ValueError, match='configuration changed|source or configuration'):
        p.validate_expression(event, context['binding_sha256'])
    assert len(calls) == 1


def test_final_hook_passes_exact_claim_to_transactional_admission(tmp_path, monkeypatch):
    p, event, now, calls, _, _, state = enrolled(tmp_path, monkeypatch)
    context = p(event, now=now)
    seen = []
    claim = SimpleNamespace(thread_id='thread', operation_id='operation', revision=3, source_event_id='source')
    receipt = {'provision_id': state['provision_id'], 'admission_id': 'a' * 64}
    def admit(*args, **kw):
        seen.append((args, kw))
        return {'admitted_now': True, 'receipt': receipt}
    p.provisions.admit_effect = admit
    admission = p.admit_expression(event, context['binding_sha256'], claim)
    assert admission['admitted_now'] and seen[0][0][1] == vars(claim)
    assert seen[0][1]['expected_revision'] == 2
    p.provisions.admit_effect = lambda *a, **kw: (_ for _ in ()).throw(CheckpointConflict('pause won'))
    assert p.admit_expression(event, context['binding_sha256'], claim)['admitted_now'] is False
    p.provisions.settle_effect = lambda *a, **kw: seen.append((a, kw))
    p.settle_expression(admission, outcome='unknown', proof_sha256='f' * 64)
    assert seen[-1][1]['outcome'] == 'unknown'


def test_pause_and_resume_during_final_hook_cannot_renew_old_expression(tmp_path, monkeypatch):
    p, event, now, _, _, _, state = enrolled(tmp_path, monkeypatch)
    context = p(event, now=now)
    reads = []
    def inspect(*args, **kwargs):
        reads.append(True)
        if len(reads) == 2:
            state['revision'] += 2  # Paused and resumed while the old guard was read.
        return deepcopy(state)
    p.provisions.inspect = inspect
    p.provisions.admit_effect = lambda *a, **kw: pytest.fail('stale expression reached admission')
    claim = SimpleNamespace(thread_id='thread', operation_id='operation', revision=3, source_event_id='source')
    with pytest.raises(ValueError, match='source or configuration changed'):
        p.admit_expression(event, context['binding_sha256'], claim)


def test_pause_after_model_preserves_usage_without_claiming_uncertain_effect():
    result = FreeWillDecision('respond', 'fixture', 1, 'fixture', 'Hello')
    class Proposal:
        def __call__(self, event, *, now):
            return {'expires_at_epoch': 9999999999, 'binding_sha256': 'a' * 64}
        def validate_current(self, context):
            raise HouseholdProvisionInactive('paused')
    model = SimpleNamespace(evaluate_sensory_event=lambda *a, **kw: result)
    wrapped = HouseholdProcedureModel(model, Proposal(), authorize=lambda: None)
    answer = wrapped.evaluate_sensory_event(object())
    assert answer.disposition == 'silence' and answer.usage == result.usage
