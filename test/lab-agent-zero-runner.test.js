import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import { captureAgentZeroResult } from '../scripts/lab/run-agent-zero-core-live.mjs';

const catalog = JSON.parse(fs.readFileSync('benchmark/eva-agent-benchmark-v1.json'));
const matrix = {
  contract: 'EvaCompetitorCapabilityMatrix/v1',
  pinned_participants: { agent_zero: `v2.11@${'a'.repeat(40)}` },
};
const base = {
  catalog,
  matrix,
  caseId: 'C01',
  runId: 'agent-zero-visible-c01',
  sourceCommit: 'a'.repeat(40),
  configSha256: 'a'.repeat(64),
  imageId: `sha256:${'b'.repeat(64)}`,
  wallClockMs: 1234,
  apiResult: { context_id: 'abc123', response: 'Niet als standaard; groepeer op taak.' },
  logItems: [{
    type: 'response', content: 'Niet als standaard; groepeer op taak.', kvps: { finished: true },
  }],
  providerKeys: ['openai/gpt-5.4-mini'],
  usage: { input_tokens: 10, output_tokens: 7, reasoning_tokens: 3, api_calls: 1 },
  reasoningEffort: 'medium',
};

test('captures an exact no-tool Agent Zero development turn', () => {
  const captured = captureAgentZeroResult(base);
  assert.equal(captured.case_id, 'C01');
  assert.deepEqual(captured.tool_names, []);
  assert.equal(captured.competitor_binding, matrix.pinned_participants.agent_zero);
  assert.equal(captured.effectful_tool_calls, 0);
});

test('rejects an effectful tool or a different runtime model', () => {
  assert.throws(() => captureAgentZeroResult({
    ...base,
    logItems: [
      { type: 'tool', kvps: { _tool_name: 'code_execution_tool' } },
      ...base.logItems,
    ],
  }), /actuele binding/u);
  assert.throws(() => captureAgentZeroResult({
    ...base, providerKeys: ['openai/gpt-5.6-sol'],
  }), /actuele binding/u);
});

test('rejects an unsupported case or response not present in the chat log', () => {
  assert.throws(() => captureAgentZeroResult({ ...base, caseId: 'T01' }), /zichtbare E0/u);
  assert.throws(() => captureAgentZeroResult({
    ...base, apiResult: { ...base.apiResult, response: 'ander antwoord' },
  }), /actuele binding/u);
});
