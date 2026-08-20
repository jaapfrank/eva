import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = mkdtempSync(path.join(tmpdir(), 'bottie-perception-policy-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';

const { db } = await import('../src/db.js');
const {
  cameraEventIsFresh,
  cameraNoticeRecognition,
  cleanupTemporaryCameraFrames,
  shouldReportCameraEvent,
} = await import('../src/mind/perception.js');

after(() => {
  db.close();
  rmSync(dataDir, { recursive: true, force: true });
});

test('een gewone straatdetectie zonder leerbaar gezicht blijft intern', () => {
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: { recognized: false, learnable: false },
  }), false);
});

test('alleen een vers en expliciet getimestamped camera-event is bruikbaar', () => {
  const now = Date.parse('2026-08-12T18:00:00.000Z');
  assert.equal(cameraEventIsFresh({ triggerTs: new Date(now - 30_000).toISOString() }, now), true);
  assert.equal(cameraEventIsFresh({}, now), false);
  assert.equal(cameraEventIsFresh({ triggerTs: 'geen tijd' }, now), false);
  assert.equal(cameraEventIsFresh({ triggerTs: new Date(now - 121_000).toISOString() }, now), false);
  assert.equal(cameraEventIsFresh({ triggerTs: new Date(now + 31_000).toISOString() }, now), false);
});

test('een leerbaar onbekend gezicht mag pas na twee uur opnieuw worden gemeld', () => {
  const now = Date.now();
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: { recognized: false, learnable: true },
    previous: now - 121 * 60_000,
    unknownAt: now - 121 * 60_000,
    now,
  }), true);
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: { recognized: false, learnable: true },
    unknownAt: now - 5 * 60_000,
    now,
  }), false);
});

test('bekende gezichten respecteren de camerarusttijd en deurbel gaat altijd door', () => {
  const now = Date.now();
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: { recognized: true, learnable: true },
    previous: now - 60_000,
    now,
    quietMs: 10 * 60_000,
  }), false);
  assert.equal(shouldReportCameraEvent({
    type: 'doorbell',
    detail: { recognized: false, learnable: false },
    now,
  }), true);
});

test('een automatische naam op een te klein of zwak gezicht blijft intern', () => {
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: {
      recognized: true,
      person: 'Jasmin',
      gelijkenis: 0.45,
      largestArea: 837,
      detScore: 0.595,
      learnable: false,
    },
  }), false);
});

test('een zwakke deurbelmatch meldt de bel maar lekt geen naam of naamvraag', () => {
  const safe = cameraNoticeRecognition('doorbell', {
    recognized: true,
    person: 'Jasmin',
    faceCount: 1,
    learnable: false,
  });
  assert.equal(safe.recognized, false);
  assert.equal(safe.person, null);
  assert.equal(safe.faceCount, 0);
});

test('een goed gezicht buiten het afgesproken cameragebied blijft intern', () => {
  const detail = { recognized: true, learnable: true, bbox: [300, 30, 340, 70] };
  const frameSize = { width: 640, height: 360 };
  const region = { minY: 0.36 };
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected', detail, frameSize, region,
  }), false);
  assert.equal(shouldReportCameraEvent({
    type: 'person_detected',
    detail: { ...detail, bbox: [500, 190, 560, 260] },
    frameSize,
    region,
  }), true);
});

test('tijdelijke triggerframes verdwijnen na beoordeling zonder replyfoto te raken', async () => {
  const triggerDir = path.join(dataDir, 'private', 'camera_trigger_frames');
  mkdirSync(triggerDir, { recursive: true });
  const temporary = path.join(triggerDir, 'tijdelijk.jpg');
  const durable = path.join(triggerDir, 'duurzaam.jpg');
  writeFileSync(temporary, 'temporary');
  writeFileSync(durable, 'durable');

  const removed = await cleanupTemporaryCameraFrames({
    frames: [
      { path: temporary, temporary: true },
      { path: durable, temporary: false },
    ],
  });

  assert.equal(removed, 1);
  assert.equal(existsSync(temporary), false);
  assert.equal(existsSync(durable), true);
});
