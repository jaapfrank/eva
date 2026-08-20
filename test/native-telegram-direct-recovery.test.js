import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const fixture = path.join(import.meta.dirname, 'native-telegram-direct-recovery-fixture.mjs');

function runPhase(dataDir, phase, variant, originMode = 'exact') {
  const child = spawnSync(process.execPath, [fixture, phase, dataDir, variant, originMode], {
    cwd: path.resolve(import.meta.dirname, '..'), env: { ...process.env },
    encoding: 'utf8', timeout: 30_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_TELEGRAM_DIRECT_RECOVERY='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('NATIVE_TELEGRAM_DIRECT_RECOVERY='.length));
}

for (const originMode of ['zero', 'multiple', 'mismatch']) {
  test(`done gen1 weigert ${originMode} finale Telegram-oorsprong zonder claim of send`, (t) => {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-telegram-origin-${originMode}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    const crashed = runPhase(dataDir, 'prepare-crash', 'reply');
    const rejected = runPhase(dataDir, 'recover', 'reply', originMode);
    assert.equal(crashed.executionStatus, 'done', JSON.stringify(crashed));
    assert.deepEqual({
      attempts: rejected.attempts,
      generations: rejected.generations,
      sends: rejected.sends,
      receipts: rejected.receipts,
      providerCalls: rejected.providerCalls,
      visionCalls: rejected.visionCalls,
      getFileCalls: rejected.getFileCalls,
      downloadFetches: rejected.downloadFetches,
      chatActions: rejected.chatActions,
      exactOriginCount: rejected.exactOriginCount,
    }, {
      attempts: 1,
      generations: [1],
      sends: 0,
      receipts: 0,
      providerCalls: 0,
      visionCalls: 0,
      getFileCalls: 0,
      downloadFetches: 0,
      chatActions: 0,
      exactOriginCount: originMode === 'multiple' ? 2 : 0,
    });
  });
}

for (const variant of ['reply', 'photo', 'video', 'document']) {
  test(`done gen1 ${variant} herstelt direct zonder handler of externe heranalyse`, (t) => {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-telegram-direct-${variant}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    const crashed = runPhase(dataDir, 'prepare-crash', variant);
    assert.equal(crashed.executionStatus, 'done', JSON.stringify(crashed));
    assert.equal(crashed.status, 'failed', JSON.stringify(crashed));
    assert.equal(crashed.attempts, 1, JSON.stringify(crashed));
    assert.deepEqual(crashed.generations, [1], JSON.stringify(crashed));
    assert.equal(crashed.prepared, false, JSON.stringify(crashed));
    assert.equal(crashed.sends, 0, JSON.stringify(crashed));
    assert.equal(crashed.receipts, 0, JSON.stringify(crashed));
    assert.equal(crashed.episodeState, 'delivery_pending', JSON.stringify(crashed));
    assert.equal(crashed.workStatus, 'open', JSON.stringify(crashed));
    assert.match(crashed.finalDeliveryBindingSha256, /^[a-f0-9]{64}$/u);
    assert.equal(crashed.preparedOutput.goalEpisodeId, crashed.issuedEpisodeId,
      JSON.stringify(crashed));
    assert.equal(crashed.preparedOutput.workItemId, crashed.issuedWorkItemId,
      JSON.stringify(crashed));
    assert.equal(crashed.preparedOutput.finalDeliveryBindingSha256,
      crashed.finalDeliveryBindingSha256, JSON.stringify(crashed));
    assert.equal(crashed.authoritySource.kind,
      variant === 'reply' ? 'text' : variant, JSON.stringify(crashed));
    assert.equal(crashed.authoritySource.content,
      `Lees data/workspace/telegram-direct-${variant}-proof.txt en rond het Telegram-werk af.`,
      JSON.stringify(crashed));
    assert.equal(crashed.authoritySource.sourceBodySha256,
      crashed.authoritySource.sourceSha256, JSON.stringify(crashed));
    if (['photo', 'video', 'document'].includes(variant)) {
      const index = ['reply', 'photo', 'video', 'document'].indexOf(variant) + 1;
      assert.equal(crashed.authoritySource.fileId, `${variant}-file-${index}`,
        JSON.stringify(crashed));
      assert.equal(crashed.authoritySource.fileUniqueId, `${variant}-unique-${index}`,
        JSON.stringify(crashed));
    }

    const recovered = runPhase(dataDir, 'recover', variant);
    assert.equal(recovered.recoveryError, null, JSON.stringify(recovered));
    assert.equal(recovered.deliveryFailure, null, JSON.stringify(recovered));
    assert.deepEqual({
      status: recovered.status, attempts: recovered.attempts,
      prepared: recovered.prepared, generations: recovered.generations,
      sends: recovered.sends, receipts: recovered.receipts,
      providerCalls: recovered.providerCalls, visionCalls: recovered.visionCalls,
      getFileCalls: recovered.getFileCalls, downloadFetches: recovered.downloadFetches,
      chatActions: recovered.chatActions,
      episodeState: recovered.episodeState, workStatus: recovered.workStatus,
      finalDeliveryBindingSha256: recovered.finalDeliveryBindingSha256,
      authorityGeneration: recovered.authorityGeneration,
    }, {
      status: 'completed', attempts: 1, prepared: true, generations: [1],
      sends: 1, receipts: 1, providerCalls: 0, visionCalls: 0,
      getFileCalls: 0, downloadFetches: 0, chatActions: 0,
      episodeState: 'closed', workStatus: 'done',
      finalDeliveryBindingSha256: crashed.finalDeliveryBindingSha256,
      authorityGeneration: 1,
    });
  });
}
