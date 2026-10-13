import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { planLabRun } from '../scripts/lab/plan-run.mjs';
import {
  appendLabCaseResult,
  createLabResultLedger,
  summarizeLabResultLedger,
  verifyLabResultLedger,
} from '../scripts/lab/result-ledger.mjs';

const catalog = JSON.parse(fs.readFileSync(
  new URL('../benchmark/eva-agent-benchmark-v1.json', import.meta.url), 'utf8',
));

function plan() {
  return planLabRun({
    catalog,
    runId: 'lab-result-ledger-test',
    participant: {
      id: 'eva', sourceDigest: '1'.repeat(64), adapterDigest: '2'.repeat(64),
      configDigest: '3'.repeat(64),
    },
    lane: 'controlled',
    track: 'sim',
    sealedSetCommitment: '4'.repeat(64),
  });
}

function metrics(overrides = {}) {
  return {
    toolCalls: 3,
    correctToolCalls: 3,
    toolErrors: 1,
    recoveredToolErrors: 1,
    inputTokens: 1200,
    outputTokens: 300,
    wallClockMs: 2500,
    externalCostMinor: 4,
    interventions: 0,
    turnCount: 4,
    ...overrides,
  };
}

test('resultledger begint met iedere geplande case als no_run en is hashketengebonden', () => {
  const runPlan = plan();
  const ledger = createLabResultLedger(runPlan, '2026-08-16T10:00:00.000Z');
  assert.equal(ledger.entries.length, 36);
  assert.equal(ledger.entries.every((entry) => entry.result.status === 'no_run'), true);
  assert.equal(verifyLabResultLedger(ledger, runPlan), true);
  const forged = structuredClone(ledger);
  forged.entries[4].result.status = 'passed';
  assert.equal(verifyLabResultLedger(forged, runPlan), false);
});

test('passed vereist alle gates en evidencesoorten en bewaart vergelijkbare metrieken', () => {
  const runPlan = plan();
  const initial = createLabResultLedger(runPlan, '2026-08-16T10:00:00.000Z');
  const instance = runPlan.instances.find((item) => item.eligible);
  const result = {
    caseInstanceId: instance.caseInstanceId,
    status: 'passed',
    deterministicGates: instance.semanticGateIds.map((gateId) => ({ gateId, passed: true })),
    evidenceRefs: instance.requiredEvidenceKinds.map((kind, index) => ({
      kind, evidenceSha256: String(index + 5).repeat(64).slice(0, 64),
    })),
    metrics: metrics(),
    criticalFailures: [],
    firstBreach: null,
  };
  const ledger = appendLabCaseResult(initial, result, '2026-08-16T10:01:00.000Z');
  assert.equal(verifyLabResultLedger(ledger, runPlan), true);
  const summary = summarizeLabResultLedger(ledger, runPlan);
  assert.equal(summary.statusCounts.passed, 1);
  assert.equal(summary.metrics.toolCallAccuracy, 1);
  assert.equal(summary.metrics.recoveryRate, 1);
  assert.equal(summary.metrics.totalTokens, 1500);
  assert.equal(Object.hasOwn(summary, 'marketingScore'), false);
});

test('ledger faalt gesloten op bewijsloos groen, duplicate finalisatie en onmogelijke tellingen', () => {
  const runPlan = plan();
  const initial = createLabResultLedger(runPlan, '2026-08-16T10:00:00.000Z');
  const instance = runPlan.instances.find((item) => item.eligible);
  const base = {
    caseInstanceId: instance.caseInstanceId,
    status: 'passed',
    deterministicGates: instance.semanticGateIds.map((gateId) => ({ gateId, passed: true })),
    evidenceRefs: [],
    metrics: metrics(),
    criticalFailures: [],
    firstBreach: null,
  };
  assert.throws(() => appendLabCaseResult(initial, base, '2026-08-16T10:01:00.000Z'),
    /bewijs/u);
  assert.throws(() => appendLabCaseResult(initial, {
    ...base,
    status: 'failed',
    metrics: metrics({ correctToolCalls: 4 }),
    criticalFailures: ['effect_boundary'],
  }, '2026-08-16T10:01:00.000Z'), /metrieken/u);
  const skipped = appendLabCaseResult(initial, {
    ...base,
    status: 'skipped',
    deterministicGates: [],
    metrics: metrics({ toolCalls: 0, correctToolCalls: 0, toolErrors: 0, recoveredToolErrors: 0 }),
    criticalFailures: [],
  }, '2026-08-16T10:01:00.000Z');
  assert.throws(() => appendLabCaseResult(skipped, {
    ...base, status: 'failed', criticalFailures: ['late_result'],
  }, '2026-08-16T10:02:00.000Z'), /definitief/u);
});
