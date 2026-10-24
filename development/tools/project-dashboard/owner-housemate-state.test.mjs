import test from 'node:test';
import assert from 'node:assert/strict';
import { validHousemateState } from './src/owner-housemate-state.mjs';
const state = () => ({ contract: 'EvaOwnerHousemateState/v1', checked_at: '2026-10-24T10:00:00Z',
  current_learning: [{ source_id: '11111111-1111-4111-8111-111111111111', kind: 'person_hypothesis',
    text: 'A provisional interpretation', uncertainty_reason: 'One explicit response', outcome: 'open',
    recorded_at: '2026-10-23T10:00:00Z', review_at: null, revised: false, retention_state: 'retained', is_execution_permission: false }],
  learning_limit: 8, learning_coverage: 'recent_current_authorized_heads', personality_deltas: [],
  personality_deltas_state: 'no_separate_typed_delta_route', shared_trace: null,
  shared_trace_state: 'no_separate_registered_trace_route', curiosity_source: 'current_open_project_questions' });
test('keeps actual typed learning distinct from unavailable optional personality and trace routes', () => {
  assert.equal(validHousemateState(state()), true);
  const empty = state(); empty.current_learning = []; assert.equal(validHousemateState(empty), true);
  const fake = state(); fake.personality_deltas = [{ text: 'invented personality' }]; assert.equal(validHousemateState(fake), false);
  const trace = state(); trace.shared_trace = { text: 'unbound private content' }; assert.equal(validHousemateState(trace), false);
});
test('refuses invalid source navigation, implicit authority and malformed content', () => {
  for (const change of [{ source_id: '../other' }, { kind: 'proven_personality' }, { text: { private: 'bad' } },
    { uncertainty_reason: '' }, { outcome: 'success' }, { recorded_at: 'bad' }, { is_execution_permission: true }]) {
    const value = state(); Object.assign(value.current_learning[0], change); assert.equal(validHousemateState(value), false);
  }
});
test('bounds the selection and rejects unversioned data', () => {
  const many = state(); many.current_learning = Array.from({ length: 9 }, () => state().current_learning[0]);
  assert.equal(validHousemateState(many), false);
  assert.equal(validHousemateState({ ...state(), contract: 'other' }), false);
});
