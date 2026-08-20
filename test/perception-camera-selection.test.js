import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-camera-selection-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';

const { db, kvGet, kvSet } = await import('../src/db.js');
const { config } = await import('../src/config.js');
const {
  replacePendingCameraCandidate,
  selectCameraAttentionCandidate,
} = await import('../src/mind/perceptionattention.js');
const {
  cameraAttentionAudit,
  cameraFamilyCaption,
  cameraReportHistory,
  cleanupTemporaryCameraFrames,
  selectCameraEventForAttention,
  shouldReportCameraEvent,
} = await import('../src/mind/perception.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function cameraFixture(camera, now, frame, ageMs = 10_000) {
  return {
    type: 'person_detected',
    camera,
    waar: camera.replace('camera.', '').replaceAll('_', ' '),
    triggerTs: new Date(now - ageMs).toISOString(),
    frames: [{ buffer: Buffer.from(frame), contentType: 'image/jpeg' }],
  };
}

function emptySelectionState(overrides = {}) {
  return {
    byCamera: {}, personByCamera: {}, unknownByCamera: {}, attentionSelections: [],
    ...overrides,
  };
}

test('drie gelijkwaardige niet-deurbelcamera-events roteren met een gewone reden', () => {
  const now = Date.parse('2026-08-14T08:00:00.000Z');
  kvSet('camera_report_state_v2', emptySelectionState());
  const cameras = ['camera.g4_doorbell', 'camera.tuin', 'camera.overkapping'];
  const chosen = [];

  for (let round = 0; round < 6; round += 1) {
    const choice = selectCameraEventForAttention(cameras.map((camera) => (
      cameraFixture(camera, now + round * 1_000, `${camera}-beeld-${round}`)
    )), { now: now + round * 1_000 });
    assert.ok(choice);
    assert.equal(choice.event.type, 'person_detected');
    assert.equal(typeof choice.reason, 'string');
    assert.match(choice.reason, /camera|waarneming|incident/iu);
    assert.doesNotMatch(choice.reason, /score|weging|\b0\.\d+/iu);
    chosen.push(choice.cameraKey);
  }

  assert.deepEqual(new Set(chosen), new Set(cameras));
  const counts = cameras.map((camera) => chosen.filter((value) => value === camera).length);
  assert.ok(Math.max(...counts) - Math.min(...counts) <= 1, JSON.stringify({ chosen, counts }));
  const persisted = kvGet('camera_report_state_v2');
  assert.deepEqual(persisted.attentionSelections.slice(0, 3).map((row) => row.cameraKey),
    chosen.slice(-3).reverse());
});

test('een bijna verlopen camera-event mag duurzame fairness terecht overrulen', () => {
  const now = Date.parse('2026-08-14T09:00:00.000Z');
  kvSet('camera_report_state_v2', emptySelectionState({
    attentionSelections: [{ cameraKey: 'camera.g4_doorbell', fingerprint: 'eerder',
      selectedAt: new Date(now - 1_000).toISOString() }],
  }));
  const choice = selectCameraEventForAttention([
    cameraFixture('camera.g4_doorbell', now, 'urgent', 115_000),
    cameraFixture('camera.tuin', now, 'tuin-nieuw'),
    cameraFixture('camera.overkapping', now, 'overkapping-nieuw'),
  ], { now });

  assert.equal(choice.cameraKey, 'camera.g4_doorbell');
  assert.match(choice.reason, /bijna te oud|voorrang/iu);
});

test('exact herhaald beeld wordt gedempt en KV-herladen bewaart selectiecontinuïteit', () => {
  const now = Date.parse('2026-08-14T10:00:00.000Z');
  kvSet('camera_report_state_v2', emptySelectionState());
  const original = selectCameraEventForAttention([
    cameraFixture('camera.garage', now, 'hetzelfde-beeld'),
  ], { now });
  assert.equal(original.cameraKey, 'camera.garage');

  // Selectiecontinuïteit is serialiseerbaar en leeft uitsluitend in de reeds
  // bestaande KV-store, dus niet in vluchtig modulegeheugen.
  const persisted = JSON.parse(JSON.stringify(kvGet('camera_report_state_v2')));
  kvSet('camera_report_state_v2', persisted);
  const next = selectCameraEventForAttention([
    cameraFixture('camera.overkapping', now + 1_000, 'hetzelfde-beeld'),
    cameraFixture('camera.tuin', now + 1_000, 'een-nieuwe-waarneming'),
  ], { now: now + 1_000 });

  assert.equal(next.cameraKey, 'camera.tuin');
  assert.equal(next.factors.duplicate, false);
});

test('onbekend-gezichtsrust van de tuin domineert een andere camera niet', () => {
  const now = Date.parse('2026-08-14T11:00:00.000Z');
  const state = emptySelectionState({
    unknownAt: now,
    unknownByCamera: { 'camera.tuin': now },
  });
  const garden = cameraReportHistory(state, 'camera.tuin');
  const canopy = cameraReportHistory(state, 'camera.overkapping');

  assert.equal(garden.unknownAt, now);
  assert.equal(canopy.unknownAt, 0);
  assert.equal(shouldReportCameraEvent({ type: 'person_detected',
    detail: { recognized: false, learnable: true }, now,
    previous: garden.previous, unknownAt: garden.unknownAt }), false);
  assert.equal(shouldReportCameraEvent({ type: 'person_detected',
    detail: { recognized: false, learnable: true }, now,
    previous: canopy.previous, unknownAt: canopy.unknownAt }), true);
});

test('zestien gelijkwaardige camera’s verhongeren niet en alle cameramaps blijven begrensd', () => {
  const now = Date.parse('2026-08-14T12:00:00.000Z');
  kvSet('camera_report_state_v2', emptySelectionState());
  const cameras = Array.from({ length: 16 }, (_, index) => (
    `camera.fairness_${String(index + 1).padStart(2, '0')}`
  ));
  const chosen = [];
  for (let round = 0; round < cameras.length * 2; round += 1) {
    const at = now + round * 1_000;
    const choice = selectCameraEventForAttention(cameras.map((camera) => (
      cameraFixture(camera, at, `${camera}-beeld-${round}`)
    )), { now: at });
    chosen.push(choice.cameraKey);
  }
  assert.deepEqual(new Set(chosen.slice(0, cameras.length)), new Set(cameras),
    `iedere camera hoort binnen één eerlijke ronde aan bod te komen: ${JSON.stringify(chosen)}`);

  const oversized = emptySelectionState({
    attentionSelections: Array.from({ length: 96 }, (_, index) => ({
      cameraKey: `camera.history_${index}`,
      fingerprint: `fingerprint-${index}`,
      selectedAt: new Date(now - index * 1_000).toISOString(),
    })),
    byCamera: Object.fromEntries(Array.from({ length: 96 }, (_, index) => (
      [`camera.state_${index}`, now - index * 1_000]
    ))),
    personByCamera: Object.fromEntries(Array.from({ length: 96 }, (_, index) => (
      [`camera.state_${index}`, `Bewoner ${index}`]
    ))),
    unknownByCamera: Object.fromEntries(Array.from({ length: 96 }, (_, index) => (
      [`camera.state_${index}`, now - index * 1_000]
    ))),
  });
  kvSet('camera_report_state_v2', JSON.parse(JSON.stringify(oversized)));
  selectCameraEventForAttention([
    cameraFixture('camera.restart', now + 60_000, 'restart-beeld'),
  ], { now: now + 60_000 });
  const bounded = kvGet('camera_report_state_v2');
  assert.ok(bounded.attentionSelections.length <= 64);
  assert.ok(Object.keys(bounded.byCamera).length <= 64);
  assert.ok(Object.keys(bounded.personByCamera).length <= 64);
  assert.ok(Object.keys(bounded.unknownByCamera).length <= 64);
  assert.equal(Object.hasOwn(bounded.byCamera, 'camera.state_0'), true);
  assert.equal(Object.hasOwn(bounded.byCamera, 'camera.state_95'), false);
});

test('same-camera burst ruimt het verdrongen frame exact eenmaal op en dempt de duplicaatkeuze', async () => {
  const frameDir = path.join(config.dataDir, 'private', 'camera_trigger_frames');
  fs.mkdirSync(frameDir, { recursive: true });
  const firstPath = path.join(frameDir, 'burst-eerste.jpg');
  const secondPath = path.join(frameDir, 'burst-tweede.jpg');
  fs.writeFileSync(firstPath, 'zelfde-burstbeeld');
  fs.writeFileSync(secondPath, 'zelfde-burstbeeld');
  const now = Date.parse('2026-08-14T13:00:00.000Z');
  const event = (file) => ({
    ...cameraFixture('camera.tuin', now, 'zelfde-burstbeeld'),
    frames: [{ path: file, temporary: true, buffer: Buffer.from('zelfde-burstbeeld'),
      contentType: 'image/jpeg' }],
  });
  const queue = new Map();
  let cleanupCalls = 0;
  const cleanup = async (candidate) => {
    cleanupCalls += 1;
    return cleanupTemporaryCameraFrames(candidate);
  };
  const first = await replacePendingCameraCandidate(queue, event(firstPath), { cleanup });
  assert.equal(first.displaced, null);
  const replacement = await replacePendingCameraCandidate(queue, event(secondPath), { cleanup });

  assert.equal(cleanupCalls, 1);
  assert.equal(fs.existsSync(firstPath), false);
  assert.equal(fs.existsSync(secondPath), true);
  assert.equal(replacement.duplicate, true);
  const other = cameraFixture('camera.overkapping', now, 'werkelijk-nieuw-beeld');
  const selected = selectCameraAttentionCandidate([
    queue.get('camera.tuin'), other,
  ], { history: [], now, duplicateFingerprints: [replacement.fingerprint] });
  assert.equal(selected.cameraKey, 'camera.overkapping');
  assert.equal(selected.factors.duplicate, false);
});

test('de gezinscaption houdt selectiebeleid intern maar bewaart het in de vision-audit', () => {
  const now = Date.parse('2026-08-14T14:00:00.000Z');
  kvSet('camera_report_state_v2', emptySelectionState());
  const choice = selectCameraEventForAttention([
    cameraFixture('camera.overkapping', now, 'caption-beeld'),
  ], { now });
  const caption = cameraFamilyCaption({
    place: 'de overkapping', camera: 'camera.overkapping',
    recognition: { recognized: false, learnable: true, faceCount: 0 },
    reason: choice.reason,
  });
  const audit = cameraAttentionAudit(choice);

  assert.match(caption, /overkapping/iu);
  assert.match(caption, /camera\.overkapping/u);
  assert.match(caption,
    /(?:geen gezicht[^.]*betrouwbaar[^.]*herkennen|niet betrouwbaar[^.]*herkennen)/iu);
  assert.doesNotMatch(caption,
    /waarom|juist dit beeld|fairness|select(?:ie|or)|score|weging|voorrang|minder aan bod|actuele optie/iu);
  assert.equal(caption.includes(choice.reason), false);
  assert.equal(audit.attentionReason, choice.reason);
  assert.deepEqual(audit.attentionFactors, choice.factors);
});
