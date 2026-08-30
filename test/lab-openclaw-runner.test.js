import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  captureOpenClawResult,
  inspectOpenClawTrace,
} from '../scripts/lab/run-openclaw-core-live.mjs';

const catalog = JSON.parse(fs.readFileSync('benchmark/eva-agent-benchmark-v1.json'));
const matrix = {
  contract: 'EvaCompetitorCapabilityMatrix/v1',
  pinned_participants: { openclaw: `v2026.7.1-2@${'a'.repeat(40)}` },
};
const trace = {
  reply: 'Niet als standaard; groepeer op taak.',
  toolCalls: 0,
  usage: { input: 10, output: 7, total: 17 },
};
const base = {
  catalog,
  matrix,
  caseId: 'C01',
  runId: 'openclaw-visible-c01',
  sourceCommit: 'a'.repeat(40),
  configSha256: 'b'.repeat(64),
  participantVersion: 'OpenClaw 2026.7.1-2 (aaaaaaa)',
  codexPluginVersion: '2026.7.1-1',
  wallClockMs: 1234,
  trace,
  result: {
    payloads: [{ text: trace.reply }],
    meta: {
      aborted: false,
      stopReason: 'stop',
      agentMeta: { provider: 'openai', model: 'gpt-5.4-mini', agentHarnessId: 'codex' },
      requestShaping: { thinking: 'medium' },
      executionTrace: {
        attempts: [{ provider: 'openai', model: 'gpt-5.4-mini', result: 'success' }],
        fallbackUsed: false,
      },
    },
  },
};

test('captures an exact current no-tool OpenClaw development turn', () => {
  const captured = captureOpenClawResult(base);
  assert.equal(captured.case_id, 'C01');
  assert.equal(captured.tool_calls, 0);
  assert.equal(captured.competitor_binding, matrix.pinned_participants.openclaw);
  assert.equal(captured.semantic_gates, 'not_judged');
});

test('rejects a turn with tools, fallback, or a different model', () => {
  assert.throws(() => captureOpenClawResult({ ...base, trace: { ...trace, toolCalls: 1 } }), /actuele binding/u);
  assert.throws(() => captureOpenClawResult({
    ...base,
    result: {
      ...base.result,
      meta: {
        ...base.result.meta,
        executionTrace: { ...base.result.meta.executionTrace, fallbackUsed: true },
      },
    },
  }), /actuele binding/u);
  assert.throws(() => captureOpenClawResult({
    ...base,
    result: {
      ...base.result,
      meta: {
        ...base.result.meta,
        agentMeta: { ...base.result.meta.agentMeta, model: 'gpt-5.6-sol' },
      },
    },
  }), /actuele binding/u);
});

test('extracts one completed user/assistant turn and detects a tool call', () => {
  const lines = [
    { type: 'model.completed', sessionId: 'run-1', data: {
      timedOut: false, aborted: false, promptError: null,
      messagesSnapshot: [{ role: 'user', content: 'vraag' }, { role: 'assistant', content: [{ type: 'text', text: trace.reply }] }],
      assistantTexts: [trace.reply], usage: trace.usage,
    } },
    { type: 'session.ended', sessionId: 'run-1', data: { status: 'success' } },
  ];
  assert.deepEqual(inspectOpenClawTrace(lines, 'run-1'), trace);
  lines[0].data.messagesSnapshot[1].content.push({ type: 'toolCall', name: 'exec' });
  assert.equal(inspectOpenClawTrace(lines, 'run-1').toolCalls, 1);
});

test('rejects unsupported or mutating development cases', () => {
  assert.throws(() => captureOpenClawResult({ ...base, caseId: 'T01' }), /zichtbare E0/u);
});
