import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import { captureHermesResult } from '../scripts/lab/run-hermes-core-live.mjs';

const catalog = JSON.parse(fs.readFileSync('benchmark/eva-agent-benchmark-v1.json'));
const matrix = {
  contract: 'EvaCompetitorCapabilityMatrix/v1',
  pinned_participants: { hermes: `v2026.8.27@${'a'.repeat(40)}` },
};
const base = {
  catalog,
  matrix,
  caseId: 'C01',
  runId: 'hermes-visible-c01',
  sourceCommit: 'a'.repeat(40),
  sourceVersion: '0.20.6',
  configSha256: 'b'.repeat(64),
  version: 'Hermes Agent v0.20.6 (2026.8.27)\nInstall directory: test',
  wallClockMs: 1234,
  session: {
    model: 'gpt-5.4-mini',
    billing_provider: 'openai-codex',
    reasoning_effort: 'medium',
    input_tokens: 10,
    output_tokens: 7,
    reasoning_tokens: 2,
    api_call_count: 1,
    tool_call_count: 0,
    messages: [
      { role: 'user', content: 'Ik wil voor elk apparaat een apart dashboard. Goed idee toch?' },
      { role: 'assistant', content: 'Niet als standaard; groepeer op taak.' },
    ],
  },
};

test('captures an exact Hermes development turn', () => {
  const captured = captureHermesResult(base);
  assert.equal(captured.case_id, 'C01');
  assert.equal(captured.tool_calls, 0);
  assert.equal(captured.semantic_gates, 'not_judged');
});

test('rejects even a read-only tool in the shared no-tool case', () => {
  assert.throws(() => captureHermesResult({
    ...base,
    session: {
      ...base.session,
      tool_call_count: 1,
      messages: [
        base.session.messages[0],
        { role: 'assistant', content: '', tool_calls: JSON.stringify([
          { function: { name: 'skill_view', arguments: '{"name":"openhue"}' } },
        ]) },
        { role: 'tool', content: '{}' },
        base.session.messages[1],
      ],
    },
  }), /actuele binding/u);
});

test('rejects effectful tools, a different model or a mutating case', () => {
  assert.throws(() => captureHermesResult({
    ...base,
    session: {
      ...base.session,
      tool_call_count: 1,
      messages: [base.session.messages[0], {
        role: 'assistant', content: 'done', tool_calls: JSON.stringify([
          { function: { name: 'terminal', arguments: '{}' } },
        ]),
      }],
    },
  }), /actuele binding/u);
  assert.throws(() => captureHermesResult({
    ...base, session: { ...base.session, model: 'gpt-5.6-sol' },
  }), /actuele binding/u);
  assert.throws(() => captureHermesResult({ ...base, caseId: 'T01' }), /zichtbare E0/u);
});
