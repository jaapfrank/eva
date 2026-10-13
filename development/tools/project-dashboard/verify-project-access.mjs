import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';

const folder = await mkdtemp(join(tmpdir(), 'eva-dashboard-access-'));
const routeFile = join(folder, 'route.conf');
process.env.EVA_PROJECT_ROUTE_FILE = routeFile;
const { projectServer } = await import('./project-server.mjs');
let handler;
projectServer().configurePreviewServer({ middlewares: { use: (_path, callback) => { handler = callback; } } });
const server = createServer(handler);
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const nativeFetch = globalThis.fetch;
const requests = [];
let ready = true, accessCode = 401, challenge = 'Basic realm="Eva cockpit", charset="UTF-8"', offline = false;
globalThis.fetch = async (url, options) => {
  requests.push(url);
  assert.equal(options.headers, undefined, 'projection must never forward credentials');
  if (offline) throw new Error('offline');
  return url.endsWith('/readyz') ? new Response(JSON.stringify({ ready }), {status: ready ? 200 : 503})
    : new Response('{}', {status: accessCode, headers: {'www-authenticate': challenge}});
};
const read = async () => (await nativeFetch(`http://127.0.0.1:${server.address().port}/project-state`)).json();
try {
  await writeFile(routeFile, '[Service]\nEnvironment=EVA2_CANARY_URL=http://127.0.0.1:44711\n');
  let data = await read();
  assert.equal(data.runtime.port, 44711);
  assert.equal(data.runtime.state, 'ready');
  assert.equal(data.browserAccess.state, 'login_required');
  assert.equal(data.browserAccess.url, 'http://localhost:44711/internal/v1/dashboard');
  assert.notEqual(data.project.practiceTests.find(item => item.id === 'BR-01').status, 'ready');
  await writeFile(routeFile, '[Service]\nEnvironment=EVA2_CANARY_URL=http://127.0.0.1:44710\n');
  assert.equal((await read()).runtime.port, 44710, 'route changes are read without restarting');
  ready = false;
  data = await read();
  assert.equal(data.runtime.state, 'not_ready');
  assert.equal(data.browserAccess.url, null);
  ready = true;
  accessCode = 200;
  assert.equal((await read()).browserAccess.state, 'unavailable', 'unguarded page does not prove owner access');
  accessCode = 401; challenge = 'Basic realm="Other app"';
  assert.equal((await read()).browserAccess.state, 'unavailable');
  offline = true;
  assert.equal((await read()).runtime.state, 'unknown');
  await writeFile(routeFile, 'no valid route');
  const before = requests.length;
  data = await read();
  assert.equal(data.runtime.port, undefined);
  assert.equal(requests.length, before, 'invalid route never falls back to a historical port');
  console.log(JSON.stringify({status: 'passed', checks: ['route changes', 'login boundary', 'not ready', 'unguarded route', 'wrong app', 'offline', 'invalid route'], credentials: 'none', liveMutations: 0}));
} finally {
  globalThis.fetch = nativeFetch;
  await new Promise(resolve => server.close(resolve));
  await rm(folder, {recursive: true, force: true});
}
