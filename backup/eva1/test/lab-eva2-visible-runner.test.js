import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { runVisibleEva2Case } from '../scripts/lab/run-eva2-visible-core-live.mjs';

const catalog = JSON.parse(fs.readFileSync('benchmark/eva-agent-benchmark-v1.json'));
const events = [
  { sequence: 1, event_type: 'INGRESS_COMMITTED', payload_sha256: '1'.repeat(64) },
  { sequence: 2, event_type: 'REPLY_COMMITTED', payload_sha256: '2'.repeat(64) },
  { sequence: 3, event_type: 'MEMORY_RETAIN_QUEUED', payload_sha256: '3'.repeat(64) },
];

test('zichtbare C01-run bewaart transcript en claimt geen semantische score', async () => {
  const result = await runVisibleEva2Case({
    catalog, caseId: 'C01', runId: 'development-c01', telegramDate: 1_787_504_000,
    ask: async () => ({
      text: 'Ik vind één logisch hoofddashboard rustiger.',
      message_id: 'dashboard:11111111-1111-4111-8111-111111111111',
      evidence_memory_ids: [],
    }),
    readEvents: async () => events,
  });
  assert.equal(result.status, 'development_evidence_captured_not_scored');
  assert.equal(result.semantic_gates, 'not_judged');
  assert.equal(result.deterministic_observations.unexpected_conversation_event_count, 0);
  assert.deepEqual(result.critical_failures, []);
});

test('T02 faalt zichtbaar bij een mutatie of extra effectevent', async () => {
  let reads = 0;
  const result = await runVisibleEva2Case({
    catalog, caseId: 'T02', runId: 'development-t02', telegramDate: 1_787_504_001,
    ask: async () => ({
      text: 'Ik zet hem niet uit.',
      message_id: 'dashboard:22222222-2222-4222-8222-222222222222',
      evidence_memory_ids: ['memory:1'],
    }),
    readEvents: async () => [...events, {
      sequence: 4, event_type: 'TOOL_PREPARED', payload_sha256: '4'.repeat(64),
    }],
    readState: async () => ({
      entity_id: 'light.kantoor_links', state: reads++ ? 'off' : 'on',
      last_changed: reads === 1 ? '2026-08-23T17:00:00Z' : '2026-08-23T17:00:01Z',
    }),
  });
  assert.deepEqual(result.critical_failures, ['unexpected_effect_or_state_change']);
  assert.equal(result.deterministic_observations.external_state_unchanged, false);
  assert.equal(result.deterministic_observations.unexpected_conversation_event_count, 1);
});

test('runner weigert niet-E0 en niet-ondersteunde cases', async () => {
  await assert.rejects(() => runVisibleEva2Case({
    catalog, caseId: 'T01', runId: 'development-t01', telegramDate: 1_787_504_001,
    ask: async () => ({}), readEvents: async () => [],
  }), /ongeldig/u);
});
