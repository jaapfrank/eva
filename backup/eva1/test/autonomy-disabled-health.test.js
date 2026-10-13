import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-health-off-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.NODE_ENV = 'test';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.OPENAI_API_KEY = 'test-only';
process.env.EVA_RUNTIME_MODE = 'recovery';
process.env.EVA_AUTONOMY_LEVEL = 'full';
delete process.env.EVA_AUTONOMY_ENABLED;

const { db } = await import('../src/db.js');
const { startWeb } = await import('../src/web/server.js');

let server = null;
after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('health toont ondubbelzinnig dat de scheduler uit staat', async () => {
  server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const response = await fetch(`http://127.0.0.1:${address.port}/api/health`);
  assert.equal(response.status, 200);
  const health = await response.json();

  assert.deepEqual(health.runtime, {
    mode: 'recovery',
    recovery: true,
    external_effects_enabled: false,
  });
  assert.equal(health.autonomie.enabled, false);
  assert.equal(health.autonomie.scheduler_started, false);
  assert.equal(health.autonomie.running, false);
  assert.equal(health.autonomie.next_run_ts, null);
});
