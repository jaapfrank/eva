import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const fixture = path.join(import.meta.dirname, 'native-telegram-done-restart-fixture.mjs');

function runPhase(dataDir, phase, mutation = null) {
  const child = spawnSync(process.execPath, [
    fixture, phase, dataDir, ...(mutation == null ? [] : [mutation]),
  ], {
    cwd: path.resolve(import.meta.dirname, '..'), env: { ...process.env },
    encoding: 'utf8', timeout: 25_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_TELEGRAM_DONE_RESTART='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('NATIVE_TELEGRAM_DONE_RESTART='.length));
}

test('done gen1 zonder prepared sidecar herstelt dezelfde update zonder tweede poging', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-telegram-done-restart-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const crashed = runPhase(dataDir, 'prepare-crash');
  assert.equal(crashed.executionStatus, 'done');
  assert.deepEqual(crashed.generations, [1]);
  assert.equal(crashed.attempts, 1);
  assert.equal(crashed.prepared, false);
  assert.equal(crashed.sends, 0);
  assert.equal(crashed.receipts, 0);
  const recovered = runPhase(dataDir, 'recover');
  assert.deepEqual(recovered, {
    phase: 'recover', mutation: null, status: 'completed', attempts: 1,
    prepared: true, executionStatus: 'done', generations: [1], sends: 1,
    receipts: 1, providerCalls: 0, rejected: null,
  });
});

test('done zonder sidecar faalt vóór poging op iedere bindingsmutatie', (t) => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-telegram-done-mutation-base-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  runPhase(base, 'prepare-crash');
  for (const mutation of [
    'archive', 'source', 'authority', 'output', 'requester', 'text', 'turn', 'finalBinding',
  ]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-telegram-${mutation}-`));
    fs.cpSync(base, dataDir, { recursive: true, force: false });
    const observed = runPhase(dataDir, 'recover-mutation', mutation);
    assert.equal(observed.attempts, 1, JSON.stringify(observed));
    assert.equal(observed.prepared, false, JSON.stringify(observed));
    assert.deepEqual(observed.generations, [1], JSON.stringify(observed));
    assert.equal(observed.providerCalls, 0, JSON.stringify(observed));
    assert.equal(observed.sends, 0, JSON.stringify(observed));
    assert.equal(observed.receipts, 0, JSON.stringify(observed));
    assert.equal(observed.mutationAttested, true, JSON.stringify(observed));
    assert.equal(observed.baselineProtected,
      ['source', 'authority', 'output', 'requester', 'turn', 'finalBinding']
        .includes(mutation), JSON.stringify(observed));
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
