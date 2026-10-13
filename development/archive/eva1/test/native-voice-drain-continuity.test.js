import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const fixture = path.join(import.meta.dirname, 'native-voice-drain-continuity-fixture.mjs');

function runScenario(t, scenario) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-voice-drain-${scenario}-`));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const child = spawnSync(process.execPath, [fixture, scenario, dataDir], {
    cwd: path.resolve(import.meta.dirname, '..'), env: { ...process.env },
    encoding: 'utf8', timeout: 30_000,
  });
  const readyLine = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_VOICE_DRAIN_READY='));
  assert.ok(readyLine, `${child.stdout}\n${child.stderr}`);
  const ready = JSON.parse(readyLine.slice('NATIVE_VOICE_DRAIN_READY='.length));
  assert.equal(ready.scenario, scenario);
  assert.equal(ready.acknowledged, scenario === 'last-timer' ? 20 : 21);
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_VOICE_DRAIN_CONTINUITY='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('NATIVE_VOICE_DRAIN_CONTINUITY='.length));
}

test('startup vervolgt na de eerste 20 geldige Voice gen3-rijen', (t) => {
  const report = runScenario(t, 'startup-page');
  assert.equal(report.preparationProviderCalls >= 21, true);
  assert.equal(report.recoveryExternalCalls, 0);
  assert.equal(report.delivered, 21);
  assert.equal(report.pending, 0);
});

test('twintig oudste poisonrijen blokkeren een jongere geldige Voice-rij niet', (t) => {
  const report = runScenario(t, 'poison-page');
  assert.equal(report.recoveryExternalCalls, 0);
  assert.equal(report.goodStage, 'delivered');
  assert.equal(report.goodReceipts, 1);
  assert.equal(report.poisonedDelivered, 0);
  assert.equal(report.poisonLogs, 20, JSON.stringify(report));
});

test('een ACK die met de laatste timer coalescet krijgt nog een volgende pagina', (t) => {
  const report = runScenario(t, 'last-timer');
  assert.equal(report.recoveryExternalCalls, 0);
  assert.equal(report.goodStage, 'delivered');
  assert.equal(report.goodReceipts, 1);
  assert.equal(report.poisonLogs, 20, JSON.stringify(report));
});
