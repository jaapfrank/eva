import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { createHoloStageLanProxy } from '../../../deploy/dashboard/holostage-lan.mjs';

function listenEphemeral(server, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, host, () => resolve(server.address().port));
  });
}

function closeServer(server) {
  return new Promise(resolve => {
    server.closeAllConnections?.();
    server.close(() => resolve());
  });
}

function rawHttpJson({ port, method = 'POST', path, headers = {} }) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { hostname: '127.0.0.1', port, method, path, headers: { connection: 'close', ...headers } },
      res => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString('utf8');
          let json = null;
          try { json = JSON.parse(raw); } catch { /* text response */ }
          resolve({ status: res.statusCode, raw, json });
        });
      }
    );
    req.on('error', reject);
    req.end();
  });
}

test('holostage-lan voice proxy forwards Core 403 detail and surfaces 503 when Core check fails', async () => {
  let grantStatus = 403;
  let grantBody = { detail: 'Privéschild is actief: live eigenaarsspraak is tijdelijk afgeschermd' };
  let abortMidBody = false;
  let voiceHits = 0;

  const fakeCore = http.createServer((req, res) => {
    if (req.url === '/internal/v1/dashboard/holostage/voice-grant-check') {
      if (abortMidBody) {
        res.writeHead(403, { 'content-type': 'application/json; charset=utf-8', 'content-length': '120' });
        res.write('{"detail":"afgebroken');
        setImmediate(() => req.socket.destroy(new Error('simulated upstream TCP reset')));
        return;
      }
      res.writeHead(grantStatus, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(grantBody));
      return;
    }
    res.writeHead(404).end();
  });

  const fakeVoice = http.createServer((req, res) => {
    voiceHits += 1;
    res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ url: 'ws://127.0.0.1:7880', room: 'eva-voice-test', token: 'tok' }));
  });

  const corePort = await listenEphemeral(fakeCore);
  const voicePort = await listenEphemeral(fakeVoice);
  const stageCookie = 'eva_holostage_session=stg_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop12';
  const voiceToken = 'v'.repeat(40);

  const proxy = createHoloStageLanProxy({
    bindHost: '127.0.0.1',
    lanHost: '127.0.0.1',
    port: 0,
    upstreamPort: corePort,
    voiceUpstreamPort: voicePort,
    voiceToken,
    publicHost: 'jaap.diplodocus-goldeye.ts.net',
  });
  const proxyPort = await listenEphemeral(proxy);

  const funnelHeaders = {
    host: 'jaap.diplodocus-goldeye.ts.net',
    origin: 'https://jaap.diplodocus-goldeye.ts.net',
    'x-forwarded-proto': 'https',
    cookie: stageCookie,
  };

  try {
    // 1. Exact 403 detail from Core is preserved instead of generic message
    const res403 = await rawHttpJson({
      port: proxyPort,
      path: '/internal/v1/dashboard/holostage/voice/session',
      headers: funnelHeaders,
    });
    assert.equal(res403.status, 403);
    assert.equal(res403.json?.detail, 'Privéschild is actief: live eigenaarsspraak is tijdelijk afgeschermd');
    assert.equal(voiceHits, 0);

    // 2. Allowed 200 rewrites LiveKit URL to wss://<publicHost>
    grantStatus = 200;
    grantBody = { voice_allowed: true };
    const res200 = await rawHttpJson({
      port: proxyPort,
      path: '/internal/v1/dashboard/holostage/voice/session',
      headers: funnelHeaders,
    });
    assert.equal(res200.status, 200);
    assert.equal(res200.json?.url, 'wss://jaap.diplodocus-goldeye.ts.net');
    assert.equal(voiceHits, 1);

    // 3. Upstream Core mid-body TCP reset after 403 headers resolves cleanly to 503 without hanging
    abortMidBody = true;
    const resMidReset = await rawHttpJson({
      port: proxyPort,
      path: '/internal/v1/dashboard/holostage/voice/session',
      headers: funnelHeaders,
    });
    assert.equal(resMidReset.status, 503);
    assert.match(resMidReset.json?.detail || '', /tijdelijk niet bereikbaar/);
    abortMidBody = false;

    // 4. When Core upstream is down, returns 503 instead of misleading 403
    await closeServer(fakeCore);
    const res503 = await rawHttpJson({
      port: proxyPort,
      path: '/internal/v1/dashboard/holostage/voice/session',
      headers: funnelHeaders,
    });
    assert.equal(res503.status, 503);
    assert.match(res503.json?.detail || '', /tijdelijk niet bereikbaar/);
  } finally {
    await closeServer(proxy);
    await closeServer(fakeVoice);
  }
});
