import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-enabled-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.NODE_ENV = 'test';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';

const originalSetTimeout = globalThis.setTimeout;
let timeoutCalls = 0;
globalThis.setTimeout = () => {
  timeoutCalls += 1;
  return Object.freeze({ unref() {} });
};

const { config } = await import('../src/config.js');
const { db, kvGet } = await import('../src/db.js');
const { bus } = await import('../src/bus.js');
const { autonomyStatus, startAutonomy } = await import('../src/mind/autonomy.js');

after(() => {
  globalThis.setTimeout = originalSetTimeout;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('alleen expliciete true start de autonomiescheduler en start is idempotent', () => {
  assert.equal(config.autonomyEnabled, true);
  assert.equal(config.autonomyLevel, 'full');
  assert.equal(kvGet('eva_autonomy_scheduler_v1', null), null);
  const houseListeners = bus.listenerCount('house');
  timeoutCalls = 0;

  assert.equal(startAutonomy(), true);
  assert.notEqual(kvGet('eva_autonomy_scheduler_v1', null), null);
  assert.equal(timeoutCalls, 1);
  assert.equal(bus.listenerCount('house'), houseListeners + 1);

  assert.equal(startAutonomy(), false);
  assert.equal(timeoutCalls, 1, 'een tweede start mag geen tweede timer maken');
  assert.equal(bus.listenerCount('house'), houseListeners + 1,
    'een tweede start mag geen listener dupliceren');
  assert.equal(autonomyStatus().enabled, true);
  assert.equal(autonomyStatus().scheduler_started, true);
  assert.equal(autonomyStatus().running, false);
});
