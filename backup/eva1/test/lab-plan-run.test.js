import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { planLabRun, validateLabCatalog } from '../scripts/lab/plan-run.mjs';

const catalog = JSON.parse(fs.readFileSync(
  new URL('../benchmark/eva-agent-benchmark-v1.json', import.meta.url), 'utf8',
));
const HASHES = Object.freeze({
  sourceDigest: '1'.repeat(64),
  adapterDigest: '2'.repeat(64),
  configDigest: '3'.repeat(64),
});

function plan(overrides = {}) {
  return planLabRun({
    catalog,
    runId: 'lab-2026-08-16-eva-controlled-sim',
    participant: { id: 'eva', ...HASHES },
    lane: 'controlled',
    track: 'sim',
    sealedSetCommitment: '4'.repeat(64),
    repetitions: 2,
    ...overrides,
  });
}

test('LAB-planner legt alle 36 cases en iedere vooraf gekozen herhaling als no_run vast', () => {
  const result = plan();
  assert.equal(result.contract, 'LabRunPlan/v1');
  assert.equal(result.instances.length, 72);
  assert.equal(new Set(result.instances.map((item) => item.caseInstanceId)).size, 72);
  assert.deepEqual(new Set(result.instances.map((item) => item.status)), new Set(['no_run']));
  assert.equal(result.instances.some((item) => item.eligible === false), true);
  assert.equal(result.instances.every((item) => item.eligible
    ? item.eligibilityReason === null
    : item.eligibilityReason === 'lane_or_track_not_eligible'), true);
  assert.equal(Object.hasOwn(result, 'score'), false);
  assert.match(result.planSha256, /^[a-f0-9]{64}$/u);
});

test('LAB-planner is byte-deterministisch en bindt participant, lane en sealed set', () => {
  const first = plan();
  const second = plan();
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.notEqual(plan({ participant: { id: 'hermes', ...HASHES } }).planSha256,
    first.planSha256);
  assert.notEqual(plan({ lane: 'native' }).planSha256, first.planSha256);
  assert.notEqual(plan({ sealedSetCommitment: '5'.repeat(64) }).planSha256,
    first.planSha256);
});

test('LAB-planner faalt gesloten op incomplete catalogus, ongebonden digests en ruim budget', () => {
  assert.throws(() => validateLabCatalog({ ...catalog, case_count: 35 }), /identiteit/u);
  assert.throws(() => plan({ participant: { id: 'eva', ...HASHES, sourceDigest: 'x' } }),
    /digests/u);
  assert.throws(() => plan({ budgets: {
    maxTokensPerCase: 100_000,
    maxWallClockMs: 30 * 60_000,
    maxExternalCostMinor: 10_000,
    maxInterventions: 101,
  } }), /interventiebudget/u);
});
