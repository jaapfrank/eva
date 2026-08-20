import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function runFixture(dataDir, mode) {
  return spawnSync(process.execPath, [
    'test/native-interrupted-telegram-startup-fixture.mjs', dataDir, mode,
  ], {
    cwd: projectRoot,
    env: { ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp' },
    encoding: 'utf8', timeout: 30_000,
  });
}

function recoveryResult(child) {
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const match = child.stdout.match(/NATIVE_INTERRUPTED_TELEGRAM=(\{.*\})/u);
  assert.ok(match, child.stdout);
  return JSON.parse(match[1]);
}

test('native startup adopteert exact één authentiek gecrashte Telegram-understanding', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-native-interrupted-telegram-'));
  try {
    const crashed = runFixture(dataDir, 'crash');
    assert.equal(crashed.status, 73, crashed.stderr || crashed.stdout);
    const expected = {
      executionStatus: 'done', durableHandoff: true, generations: [1, 2],
      inboundStatus: 'failed', workItemId: 1, episodeId: 1, visibleToFamily: 0,
      handoffs: 1, workItems: 1, episodes: 1, origins: 1, providerCalls: 0, sends: 0,
    };
    assert.deepEqual(recoveryResult(runFixture(dataDir, 'recover')), expected);
    assert.deepEqual(recoveryResult(runFixture(dataDir, 'recover')), expected);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
