import assert from 'node:assert/strict';
import test from 'node:test';

import { createLabEffectBroker } from '../scripts/lab/effect-broker.mjs';

function authorization(overrides = {}) {
  return {
    contract: 'EffectAuthorization/v1',
    authorizationId: 'lab-auth-1',
    runId: 'lab-run-1',
    caseInstanceId: `lab:${'1'.repeat(64)}`,
    effectClass: 'E3',
    operations: ['home.read', 'home.set'],
    targets: ['fixture:light.kitchen'],
    sinks: ['fixture:telegram.requester'],
    maxCalls: 2,
    expiresAt: '2026-08-16T10:15:00.000Z',
    reversible: true,
    rollbackDriver: 'fixture:home.restore',
    approvalSha256: '2'.repeat(64),
    ...overrides,
  };
}

test('effectbroker laat alleen exact geautoriseerde operatie, target en sink door', async () => {
  const calls = [];
  const broker = createLabEffectBroker(authorization(), {
    verifyApproval: (value) => value.approvalSha256 === '2'.repeat(64),
    execute: async (request) => {
      calls.push(request);
      return { accepted: true, effectReceiptSha256: '3'.repeat(64) };
    },
  });
  const receipt = await broker.execute({
    operation: 'home.set', target: 'fixture:light.kitchen',
    sink: 'fixture:telegram.requester', payloadSha256: '4'.repeat(64),
  }, '2026-08-16T10:00:00.000Z');
  assert.equal(receipt.contract, 'LabEffectReceipt/v1');
  assert.equal(receipt.sequence, 1);
  assert.equal(calls.length, 1);
  await assert.rejects(() => broker.execute({
    operation: 'home.set', target: 'fixture:light.bedroom',
    sink: 'fixture:telegram.requester', payloadSha256: '4'.repeat(64),
  }, '2026-08-16T10:01:00.000Z'), /niet geautoriseerd/u);
  assert.equal(calls.length, 1);
});

test('effectbroker handhaaft callbudget, expiry en menselijke stop vóór uitvoering', async () => {
  let calls = 0;
  const broker = createLabEffectBroker(authorization({ maxCalls: 1 }), {
    verifyApproval: () => true,
    execute: async () => {
      calls += 1;
      return { accepted: true, effectReceiptSha256: '5'.repeat(64) };
    },
  });
  const request = {
    operation: 'home.read', target: 'fixture:light.kitchen', sink: null,
    payloadSha256: '6'.repeat(64),
  };
  await broker.execute(request, '2026-08-16T10:00:00.000Z');
  await assert.rejects(() => broker.execute(request, '2026-08-16T10:01:00.000Z'), /budget/u);
  assert.equal(calls, 1);

  const expired = createLabEffectBroker(authorization(), {
    verifyApproval: () => true,
    execute: async () => { calls += 1; },
  });
  await assert.rejects(() => expired.execute(request, '2026-08-16T10:15:00.001Z'), /verlopen/u);
  expired.stop('human_stop', '2026-08-16T10:02:00.000Z');
  await assert.rejects(() => expired.execute(request, '2026-08-16T10:03:00.000Z'), /gestopt/u);
  assert.equal(calls, 1);
});

test('alleen echte brokerreceipt kan rollback activeren en clones minten geen bevoegdheid', async () => {
  const rolledBack = [];
  const broker = createLabEffectBroker(authorization({ maxCalls: 1 }), {
    verifyApproval: () => true,
    execute: async () => ({ accepted: true, effectReceiptSha256: '7'.repeat(64) }),
    rollback: async (receipt) => { rolledBack.push(receipt.receiptSha256); },
  });
  const receipt = await broker.execute({
    operation: 'home.set', target: 'fixture:light.kitchen', sink: null,
    payloadSha256: '8'.repeat(64),
  }, '2026-08-16T10:00:00.000Z');
  await assert.rejects(() => broker.rollback(structuredClone(receipt),
    '2026-08-16T10:02:00.000Z'), /receipt/u);
  const rollbackReceipt = await broker.rollback(receipt, '2026-08-16T10:02:00.000Z');
  assert.equal(rollbackReceipt.contract, 'LabRollbackReceipt/v1');
  assert.deepEqual(rolledBack, [receipt.receiptSha256]);
  await assert.rejects(() => broker.rollback(receipt, '2026-08-16T10:03:00.000Z'), /rollback/u);
});
