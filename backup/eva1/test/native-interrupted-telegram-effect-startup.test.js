import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function runFixture(dataDir, mode, scenario) {
  return spawnSync(process.execPath, [
    'test/native-interrupted-telegram-effect-startup-fixture.mjs', dataDir, mode, scenario,
  ], {
    cwd: projectRoot,
    env: { ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp' },
    encoding: 'utf8', timeout: 30_000,
  });
}

function recoveryResult(child) {
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const match = child.stdout.match(/NATIVE_INTERRUPTED_TELEGRAM_EFFECT=(\{.*\})/u);
  assert.ok(match, child.stdout);
  return JSON.parse(match[1]);
}

const scenarios = Object.freeze([
  ['done_read', 'done', 'ha_lees'],
  ['started_effect', 'started', 'ha_zet'],
  ['started_transport', 'transport_sending', '@transport/telegram.sendMessage'],
  ['done_mutation', 'done', 'ha_zet'],
  ['malformed', 'started', 'ha_zet'],
]);

for (const [scenario, childStatus, childTool] of scenarios) test(
  `startup herinterpreteert ${scenario} nooit als een generieke lege beurt`, () => {
  const dataDir = fs.mkdtempSync(path.join(
    '/tmp', 'bottie-native-interrupted-telegram-effect-',
  ));
  try {
    const crashed = runFixture(dataDir, 'crash', scenario);
    assert.equal(crashed.status, 73, crashed.stderr || crashed.stdout);
    if (scenario !== 'done_read') {
      const seeded = runFixture(dataDir, 'seed', scenario);
      assert.equal(seeded.status, 0, seeded.stderr || seeded.stdout);
    }
    const recovered = recoveryResult(runFixture(dataDir, 'recover', scenario));
    assert.equal(recovered.before.parentStatus, 'started');
    assert.equal(recovered.before.children.length, 1);
    assert.equal(recovered.before.children[0].tool, childTool);
    assert.equal(recovered.before.children[0].status, childStatus);
    assert.equal(recovered.before.origins, 0);
    assert.equal(recovered.before.handoffs, 0);
    assert.equal(recovered.before.inboundStatus, 'processing');

    assert.equal(recovered.after.parentStatus, 'done');
    assert.equal(recovered.after.parentError, 'effect_reconciliation_required');
    assert.equal(recovered.after.durableHandoff, false);
    assert.equal(recovered.after.children.length, 1);
    assert.equal(recovered.after.origins, 0);
    assert.equal(recovered.after.handoffs, 0);
    assert.equal(recovered.after.inboundStatus, 'failed');
    assert.equal(recovered.after.inboundError, 'effect_reconciliation_required');
    assert.deepEqual({
      providerCalls: recovered.providerCalls,
      homeReads: recovered.homeReads,
      sends: recovered.sends,
    }, { providerCalls: 0, homeReads: 0, sends: 0 });
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

const existingEvidenceScenarios = Object.freeze([
  ['existing_reply', 'already_replied', 'completed', null, 0, 0],
  ['existing_origin', 'existing_origin', 'failed', 'existing_origin', 1, 1],
  ['newer_message', 'superseded', 'failed', 'superseded', 0, 0],
]);

for (const [scenario, disposition, inboundStatus, inboundError, origins, handoffs]
  of existingEvidenceScenarios) test(
  `startup legt ${scenario} als evidence-bound disposition vast`, () => {
    const dataDir = fs.mkdtempSync(path.join(
      '/tmp', 'bottie-native-interrupted-telegram-existing-',
    ));
    try {
      const crashed = runFixture(dataDir, 'crash', scenario);
      assert.equal(crashed.status, 73, crashed.stderr || crashed.stdout);
      const seeded = runFixture(dataDir, 'seed', scenario);
      assert.equal(seeded.status, 0, seeded.stderr || seeded.stdout);
      const recovered = recoveryResult(runFixture(dataDir, 'recover', scenario));
      assert.equal(recovered.before.parentStatus, 'started');
      assert.equal(recovered.before.children.length, 0);
      assert.equal(recovered.after.parentStatus, 'done');
      assert.equal(recovered.after.noPlanDecision, `recovery:${disposition}`);
      assert.equal(recovered.after.durableHandoff, false);
      assert.equal(recovered.after.origins, origins);
      assert.equal(recovered.after.handoffs, handoffs);
      assert.equal(recovered.after.inboundStatus, inboundStatus);
      assert.equal(recovered.after.inboundError, inboundError);
      assert.deepEqual({
        providerCalls: recovered.providerCalls,
        homeReads: recovered.homeReads,
        sends: recovered.sends,
      }, { providerCalls: 0, homeReads: 0, sends: 0 });
    } finally {
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  },
);

const invalidReplyEvidenceScenarios = Object.freeze([
  'legacy_reply',
  'foreign_reply',
  'stale_reply',
  'reply_hash_drift',
]);

for (const scenario of invalidReplyEvidenceScenarios) test(
  `startup weigert ${scenario} als afleverauthority`, () => {
    const dataDir = fs.mkdtempSync(path.join(
      '/tmp', 'bottie-native-interrupted-telegram-invalid-reply-',
    ));
    try {
      const crashed = runFixture(dataDir, 'crash', scenario);
      assert.equal(crashed.status, 73, crashed.stderr || crashed.stdout);
      const seeded = runFixture(dataDir, 'seed', scenario);
      assert.equal(seeded.status, 0, seeded.stderr || seeded.stdout);
      const recovered = recoveryResult(runFixture(dataDir, 'recover', scenario));
      assert.equal(recovered.before.parentStatus, 'started');
      assert.equal(recovered.before.children.length, 0);
      assert.equal(recovered.after.parentStatus, 'done');
      assert.equal(
        recovered.after.noPlanDecision,
        'recovery:effect_reconciliation_required',
      );
      assert.equal(recovered.after.parentError, 'effect_reconciliation_required');
      assert.equal(recovered.after.durableHandoff, false);
      assert.equal(recovered.after.origins, 0);
      assert.equal(recovered.after.handoffs, 0);
      assert.equal(recovered.after.inboundStatus, 'failed');
      assert.equal(recovered.after.inboundError, 'effect_reconciliation_required');
      assert.deepEqual({
        providerCalls: recovered.providerCalls,
        homeReads: recovered.homeReads,
        sends: recovered.sends,
      }, { providerCalls: 0, homeReads: 0, sends: 0 });
    } finally {
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  },
);

const combinedEvidenceScenarios = Object.freeze([
  ['existing_reply_started_effect', 'started', 'ha_zet', 0, 0],
  ['existing_origin_done_mutation', 'done', 'ha_zet', 1, 1],
  ['newer_message_started_transport', 'transport_sending',
    '@transport/telegram.sendMessage', 0, 0],
  ['newer_message_malformed', 'started', 'ha_zet', 0, 0],
]);

for (const [scenario, childStatus, childTool, origins, handoffs]
  of combinedEvidenceScenarios) test(
  `childbewijs domineert ${scenario}`, () => {
    const dataDir = fs.mkdtempSync(path.join(
      '/tmp', 'bottie-native-interrupted-telegram-combined-',
    ));
    try {
      const crashed = runFixture(dataDir, 'crash', scenario);
      assert.equal(crashed.status, 73, crashed.stderr || crashed.stdout);
      const seeded = runFixture(dataDir, 'seed', scenario);
      assert.equal(seeded.status, 0, seeded.stderr || seeded.stdout);
      const recovered = recoveryResult(runFixture(dataDir, 'recover', scenario));
      assert.equal(recovered.before.parentStatus, 'started');
      assert.equal(recovered.before.children.length, 1);
      assert.equal(recovered.before.children[0].status, childStatus);
      assert.equal(recovered.before.children[0].tool, childTool);
      assert.equal(recovered.after.parentStatus, 'done');
      assert.equal(recovered.after.parentError, 'effect_reconciliation_required');
      assert.equal(
        recovered.after.noPlanDecision,
        'recovery:effect_reconciliation_required',
      );
      assert.equal(recovered.after.durableHandoff, false);
      assert.equal(recovered.after.origins, origins);
      assert.equal(recovered.after.handoffs, handoffs);
      assert.equal(recovered.after.inboundStatus, 'failed');
      assert.equal(recovered.after.inboundError, 'effect_reconciliation_required');
      assert.deepEqual({
        providerCalls: recovered.providerCalls,
        homeReads: recovered.homeReads,
        sends: recovered.sends,
      }, { providerCalls: 0, homeReads: 0, sends: 0 });
    } finally {
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  },
);
