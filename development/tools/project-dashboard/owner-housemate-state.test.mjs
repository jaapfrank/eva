import test from 'node:test';
import assert from 'node:assert/strict';
import { validHousemateState } from './src/owner-housemate-state.mjs';
const state = () => ({ contract: 'EvaOwnerHousemateState/v1', checked_at: '2026-10-24T10:00:00Z',
  current_learning: [{ source_id: '11111111-1111-4111-8111-111111111111', kind: 'person_hypothesis',
    text: 'A provisional interpretation', uncertainty_reason: 'One explicit response', outcome: 'open',
    recorded_at: '2026-10-23T10:00:00Z', review_at: null, revised: false, retention_state: 'retained', is_execution_permission: false }],
  learning_limit: 8, learning_coverage: 'recent_current_authorized_heads', personality_deltas: [],
  personality_deltas_state: 'no_current_typed_sources', inner_state: null, development_state: 'not_configured', development_attention: false, shared_trace: null,
  shared_trace_state: 'no_current_authorized_shared_source', curiosity_source: 'current_open_project_questions' });
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

const delta = () => ({ ...state().current_learning[0], kind: 'personality_delta', text: 'Answer with a short recommendation first',
  contract: 'EvaPersonalityDeltaView/v1', version: 1, audience: 'own_private_context', epistemic_status: 'model_inference_unconfirmed', active: true, processing_state: 'awaiting_dream' });
test('accepts real typed deltas without reclassifying hypotheses or claiming they are processed', () => {
  const value=state();value.current_learning=[];value.personality_deltas=[delta()];value.personality_deltas_state='current_typed_sources';
  assert.equal(validHousemateState(value),true);
  for (const change of [{kind:'person_hypothesis'},{processing_state:'processed'},{audience:'household'},
    {epistemic_status:'verified'},{version:0},{active:false},{text:'x'.repeat(401)}]) {
    const bad=structuredClone(value);Object.assign(bad.personality_deltas[0],change);assert.equal(validHousemateState(bad),false);
  }
});
test('accepts actual processed state with source-bound project status and rejects stale STOP state', () => {
  const value=state();value.development_state='available';value.curiosity_source='processed_current_development';
  value.inner_state={current_curiosity:'A real project question',open_threads:[{intent_id:'project:one',status:'working',phase:'exploring',updated_at:value.checked_at,source_sha256:'a'.repeat(64)}],
    recent_tone:'continuing_own_project',last_updated:value.checked_at,evidence_count:1};
  assert.equal(validHousemateState(value),true);
  value.inner_state.open_threads[0].status='stopped';assert.equal(validHousemateState(value),false);
  value.inner_state.open_threads=[];value.inner_state.recent_tone='an invented emotion';assert.equal(validHousemateState(value),false);
});
