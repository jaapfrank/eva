import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-doorbell-ingress-priority-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
await import('./homeassistant-test-composition.js');

const { bus } = await import('../src/bus.js');
const { db, upsertPerson } = await import('../src/db.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const { startPerception } = await import('../src/mind/perception.js');
const { registerPhotoSender } = await import('../src/mind/tools.js');

upsertPerson('Ingress regressiebewoner', { telegram_id: '4242901' });

after(() => {
  registerPhotoSender(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(read, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let value = read();
  while (!value && Date.now() < deadline) {
    await delay(5);
    value = read();
  }
  return value;
}

test('een hangende gewone persoonsfoto blokkeert authentieke deurbelingress niet', async () => {
  const originalFetch = globalThis.fetch;
  let releasePhoto;
  let photoSenderReturned = false;
  let markPhotoSenderEntered;
  let markPhotoSenderReturned;
  const photoGate = new Promise((resolve) => { releasePhoto = resolve; });
  const photoSenderEntered = new Promise((resolve) => { markPhotoSenderEntered = resolve; });
  const photoSenderFinished = new Promise((resolve) => { markPhotoSenderReturned = resolve; });
  const doorbellSensor = 'binary_sensor.p1_ingress_doorbell';

  globalThis.fetch = async () => new Response(JSON.stringify({
    gezichten: [{
      embedding: Array(512).fill(0),
      det_score: 0.99,
      oppervlak: 6_400,
      bbox: [10, 10, 90, 90],
    }],
  }), { status: 200, headers: { 'content-type': 'application/json' } });

  registerPhotoSender(async () => {
    markPhotoSenderEntered();
    await photoGate;
    photoSenderReturned = true;
    markPhotoSenderReturned();
    return { ok: false, accepted: false, info: 'gerichte regressiezender vrijgegeven' };
  });

  startPerception();

  try {
    await __cameraTriggerTest.captureAndIssue({
      type: 'person_detected',
      source: 'home_assistant',
      camera: 'camera.p1_ingress_person',
      sensor: 'binary_sensor.p1_ingress_person_detected',
      correlationId: 'p1-ingress-person',
      triggerTs: new Date().toISOString(),
      frames: [{ buffer: Buffer.from('p1-person-frame'), contentType: 'image/jpeg' }],
    }, null);

    assert.equal(await Promise.race([
      photoSenderEntered.then(() => true),
      delay(1_000).then(() => false),
    ]), true, 'de gewone person_detected-route moet aantoonbaar in de fotozender hangen');

    await __cameraTriggerTest.captureAndIssue({
      type: 'doorbell',
      source: 'home_assistant',
      camera: 'camera.p1_ingress_doorbell',
      sensor: doorbellSensor,
      correlationId: 'p1-ingress-doorbell',
      triggerTs: new Date().toISOString(),
      frames: [],
    }, null);

    const incidentWhilePhotoHangs = await waitFor(() => db.prepare(`
      SELECT status,sensor,visual_status
      FROM eva_doorbell_incidents
      WHERE sensor=?
    `).get(doorbellSensor), 250);

    assert.deepEqual({
      incident: incidentWhilePhotoHangs,
      personPhotoStillPending: !photoSenderReturned,
    }, {
      incident: {
        status: 'ready',
        sensor: doorbellSensor,
        visual_status: 'unavailable',
      },
      personPhotoStillPending: true,
    }, 'de authentieke deurbel moet direct duurzaam ready zijn terwijl de gewone fotozender hangt');
  } finally {
    releasePhoto();
    await Promise.race([photoSenderFinished, delay(1_000)]);
    await waitFor(() => db.prepare(`SELECT id FROM eva_doorbell_incidents WHERE sensor=?`)
      .get(doorbellSensor), 1_000);
    registerPhotoSender(null);
    globalThis.fetch = originalFetch;
    bus.removeAllListeners('house');
  }
});
