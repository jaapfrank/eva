import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-disabled-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.NODE_ENV = 'test';
process.env.EVA_AUTONOMY_LEVEL = 'full';
delete process.env.EVA_AUTONOMY_ENABLED;

const originalSetInterval = globalThis.setInterval;
const originalSetTimeout = globalThis.setTimeout;
const originalFetch = globalThis.fetch;
let intervalCalls = 0;
let timeoutCalls = 0;
let fetchCalls = 0;

globalThis.setInterval = () => {
  intervalCalls += 1;
  return Object.freeze({ unref() {} });
};
globalThis.setTimeout = () => {
  timeoutCalls += 1;
  return Object.freeze({ unref() {} });
};
globalThis.fetch = async () => {
  fetchCalls += 1;
  throw new Error('disabled autonomy mag geen provider of connector bereiken');
};

const { config } = await import('../src/config.js');
const { db, kvGet } = await import('../src/db.js');
const { bus } = await import('../src/bus.js');
const {
  __autonomyScheduleTest, autonomyStatus, startAutonomy,
} = await import('../src/mind/autonomy.js');
const { runAutonomousTurn } = await import('../src/mind/agent.js');

after(() => {
  globalThis.setInterval = originalSetInterval;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.fetch = originalFetch;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('autonomie blijft zonder expliciete opt-in volledig uit', async () => {
  assert.equal(config.autonomyEnabled, false);
  assert.equal(kvGet('eva_autonomy_scheduler_v1', null), null);
  const listenersBefore = Object.fromEntries([
    'house', 'world_signal', 'perception_ready', 'work_changed',
  ].map((name) => [name, bus.listenerCount(name)]));

  const started = startAutonomy();
  const ticked = await __autonomyScheduleTest.tick();
  const direct = await runAutonomousTurn({ reason: 'probe' });

  assert.equal(started, false);
  assert.equal(ticked, false);
  assert.equal(kvGet('eva_autonomy_scheduler_v1', null), null,
    'disabled startup mag geen schedulerstate initialiseren');
  assert.equal(intervalCalls, 0, 'disabled startup mag geen interval plannen');
  assert.equal(timeoutCalls, 0, 'disabled startup mag geen retry of wake plannen');
  assert.deepEqual(Object.fromEntries([
    'house', 'world_signal', 'perception_ready', 'work_changed',
  ].map((name) => [name, bus.listenerCount(name)])), listenersBefore,
  'disabled startup mag geen eventlisteners registreren');
  assert.equal(fetchCalls, 0);
  assert.equal(direct.disabled, true);
  assert.equal(direct.yielded, true);
  assert.deepEqual(direct.tools, []);
  assert.deepEqual(autonomyStatus(), {
    enabled: false,
    scheduler_started: false,
    running: false,
    level: 'disabled',
  });
});
