import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const fixture = path.join(import.meta.dirname, 'native-voice-restart-fixture.mjs');

function runPhase(dataDir, phase, detail = null) {
  const child = spawnSync(process.execPath, [
    fixture, phase, dataDir, ...(detail == null ? [] : [detail]),
  ], {
    cwd: path.resolve(import.meta.dirname, '..'),
    env: { ...process.env },
    encoding: 'utf8',
    timeout: 20_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_VOICE_RESTART_REPORT='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('NATIVE_VOICE_RESTART_REPORT='.length));
}

test('procesherstart na offer herhaalt provider, REST of speak nooit', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-voice-offer-restart-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-offer');
  assert.ok(prepared.providerCalls >= 1 && prepared.providerCalls <= 2);
  assert.deepEqual({
    physicalOffers: prepared.physicalOffers,
    stage: prepared.stage,
    deliveryCount: prepared.deliveryCount,
  }, { physicalOffers: 1, stage: 'offered', deliveryCount: 0 });
  assert.deepEqual(runPhase(dataDir, 'recover'), {
    phase: 'recover', recovered: 0, networkCalls: 0,
    physicalOffers: 0, stage: 'offered', deliveryCount: 0,
  });
});

test('procesherstart na ACK projecteert lokaal één receipt en herhaalt nooit offer/speak', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-voice-ack-restart-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-ack');
  assert.ok(prepared.providerCalls >= 1 && prepared.providerCalls <= 2);
  assert.deepEqual({
    physicalOffers: prepared.physicalOffers,
    stage: prepared.stage,
    deliveryCount: prepared.deliveryCount,
  }, { physicalOffers: 1, stage: 'acknowledged', deliveryCount: 0 });
  assert.deepEqual(runPhase(dataDir, 'recover'), {
    phase: 'recover', recovered: 1, networkCalls: 0,
    physicalOffers: 0, stage: 'delivered', deliveryCount: 1,
  });
  assert.deepEqual(runPhase(dataDir, 'recover'), {
    phase: 'recover', recovered: 0, networkCalls: 0,
    physicalOffers: 0, stage: 'delivered', deliveryCount: 1,
  });
});

test('procesherstart na ACK sluit één echt verzegeld Voice-werk zonder extern effect', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-voice-final-restart-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-final-ack');
  assert.equal(prepared.stage, 'acknowledged');
  assert.equal(prepared.episodeState, 'delivery_pending', JSON.stringify(prepared));
  assert.equal(prepared.workStatus, 'open');
  assert.match(prepared.finalDeliveryBindingSha256, /^[a-f0-9]{64}$/u);
  assert.deepEqual(runPhase(dataDir, 'recover'), {
    phase: 'recover', recovered: 1, networkCalls: 0,
    physicalOffers: 0, stage: 'delivered', deliveryCount: 1,
    episodeState: 'closed', workStatus: 'done',
    finalDeliveryBindingSha256: prepared.finalDeliveryBindingSha256,
  });
  assert.deepEqual(runPhase(dataDir, 'recover'), {
    phase: 'recover', recovered: 0, networkCalls: 0,
    physicalOffers: 0, stage: 'delivered', deliveryCount: 1,
    episodeState: 'closed', workStatus: 'done',
    finalDeliveryBindingSha256: prepared.finalDeliveryBindingSha256,
  });
});

test('Voice finale restartproof faalt gesloten op iedere canonieke bindingsmutatie', (t) => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-voice-final-mutation-base-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const prepared = runPhase(base, 'prepare-final-ack');
  assert.equal(prepared.episodeState, 'delivery_pending', JSON.stringify(prepared));
  const mutations = [
    'query', 'source', 'offer', 'rawAck', 'delivery', 'reply', 'turn', 'episode',
    'work', 'conversation', 'message', 'requester', 'finalBinding',
  ];
  for (const mutation of mutations) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-voice-${mutation}-`));
    fs.cpSync(base, dataDir, { recursive: true, force: false });
    const observed = runPhase(dataDir, 'recover-mutation', mutation);
    assert.deepEqual(observed, {
      phase: 'recover-mutation', mutation, verified: false, networkCalls: 0,
      stage: 'acknowledged', deliveryCount: 0,
      episodeState: 'delivery_pending', workStatus: 'open',
    });
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
