import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-attention-'));
process.env.EVA_DATA_DIR = dataDir;
await import('./homeassistant-test-composition.js');

const { db } = await import('../src/db.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const attention = await import('../src/mind/perceptionattention.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function issuedDoorbell({
  correlation = `doorbell-${Date.now()}-${Math.random()}`,
  frame = true,
  offsetMs = 0,
} = {}) {
  return __cameraTriggerTest.issue({
    type: 'doorbell', source: 'home_assistant',
    camera: 'camera.g4_doorbell_high_resolution',
    sensor: 'binary_sensor.g4_doorbell_doorbell',
    correlationId: correlation,
    triggerTs: new Date(Date.now() + offsetMs).toISOString(),
    frames: frame ? [{ buffer: Buffer.from(`jpeg-${correlation}`), contentType: 'image/jpeg' }] : [],
  });
}

const recipients = [{ label: 'gezin', chatIds: ['100', '200'] },
  { label: 'Jaap', chatIds: ['100'] }];

test('een plain clone en replay geven geen incident- of fileauthority', () => {
  const issued = issuedDoorbell({ offsetMs: -100_000 });
  assert.throws(() => attention.recordDoorbellEvidence({ ...issued }), /exact uitgegeven/u);
  const recorded = attention.recordDoorbellEvidence(issued);
  assert.equal(recorded.ok, true);
  assert.throws(() => attention.recordDoorbellEvidence(issued), /exact uitgegeven/u);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_doorbell_incidents').get().count, 1);
  const files = fs.readdirSync(path.join(dataDir, 'private', 'perception_incidents'));
  const frameFiles = files.filter((name) => /^[a-f0-9-]{36}\.(?:jpg|png|webp)$/u.test(name));
  assert.equal(frameFiles.length, 1, JSON.stringify(files));
  const framePath = path.join(dataDir, 'private', 'perception_incidents', frameFiles[0]);
  const frameStat = fs.lstatSync(framePath);
  assert.equal(frameStat.isFile(), true, JSON.stringify({ files, mode: frameStat.mode & 0o777 }));
  const mode = frameStat.mode & 0o777;
  assert.equal(mode, 0o600, JSON.stringify({ dataDir, framePath, mode: mode.toString(8),
    umask: process.umask().toString(8) }));
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  assert.equal(attention.perceptionIncidentContext(claim).incident.id, recorded.incidentId);
  attention.failPerceptionIncidentClaim(claim, 'test afgerond');
  assert.equal(attention.cleanupPerceptionFrames(), 1);
  assert.equal(fs.existsSync(framePath), false);
  const released = db.prepare(`SELECT frame_released_ts FROM eva_doorbell_incidents
    WHERE id=?`).get(recorded.incidentId);
  assert.ok(Number.isFinite(Date.parse(released.frame_released_ts)));
  assert.equal(attention.cleanupPerceptionFrames(), 0);
});

test('Eva kan exact stil kiezen zonder outward effect', () => {
  const source = issuedDoorbell({ correlation: `silent-${Date.now()}`, offsetMs: -75_000 });
  const recorded = attention.recordDoorbellEvidence(source);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  const context = attention.perceptionIncidentContext(claim);
  assert.equal(context.incident.id, recorded.incidentId);
  assert.ok(context.choices.actions.includes('silent'));
  const decision = attention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'silent',
  });
  const plan = attention.issuePerceptionIncidentPlan(claim, decision);
  assert.equal(attention.beginPerceptionIncidentEffect(plan).action, 'silent');
  assert.deepEqual(attention.settlePerceptionIncidentPlan(plan, { result: 'silent' }), {
    ok: true, status: 'silent',
  });
});

test('modeltekst en duurzame household ids blijven exact aan één foto-effect gebonden', () => {
  const source = issuedDoorbell({ correlation: `photo-${Date.now()}`, offsetMs: -50_000 });
  const recorded = attention.recordDoorbellEvidence(source);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  assert.equal(attention.perceptionIncidentContext(claim).incident.id, recorded.incidentId);
  const exactText = 'Ik zie iemand staan — willen jullie even meekijken?';
  const decision = attention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'household_photo',
    recipient: 'Jaap', text: exactText,
  });
  const capability = attention.issuePerceptionIncidentPlan(claim, decision);
  const plan = attention.beginPerceptionIncidentEffect(capability);
  assert.equal(plan.text, exactText);
  assert.deepEqual(plan.targetIds, ['100']);
  assert.ok(Buffer.isBuffer(plan.frame.buffer));
  assert.equal(plan.frame.sha256, createHash('sha256').update(plan.frame.buffer).digest('hex'));
  attention.settlePerceptionIncidentPlan(capability, { result: 'delivered', receipt: 'transport:1' });
});

test('een crash na effectclaim wordt onzeker en nooit opnieuw uitvoerbaar', () => {
  const source = issuedDoorbell({ correlation: `crash-${Date.now()}`, frame: false, offsetMs: -25_000 });
  const recorded = attention.recordDoorbellEvidence(source);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  assert.equal(attention.perceptionIncidentContext(claim).incident.id, recorded.incidentId);
  const decision = attention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
    recipient: 'gezin', text: 'Er staat iemand bij de deur.',
  });
  const capability = attention.issuePerceptionIncidentPlan(claim, decision);
  assert.equal(attention.beginPerceptionIncidentEffect(capability).action, 'household_text');
  assert.equal(attention.recoverPerceptionIncidents().uncertain, 1);
  assert.equal(attention.beginPerceptionIncidentEffect(capability), null);
  assert.equal(attention.claimPlannedPerceptionIncident(), null);
});

test('dezelfde fysieke belbron wordt ook na planning duurzaam samengevoegd', () => {
  const observed = new Date().toISOString();
  const first = __cameraTriggerTest.issue({
    type: 'doorbell', camera: 'camera.g4_doorbell_high_resolution',
    sensor: 'event.g4_doorbell', correlationId: `source-a-${Date.now()}`,
    triggerTs: observed, frames: [],
  });
  const original = attention.recordDoorbellEvidence(first);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  const decision = attention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'silent',
  });
  const capability = attention.issuePerceptionIncidentPlan(claim, decision);
  const alias = __cameraTriggerTest.issue({
    type: 'doorbell', camera: 'camera.g4_doorbell_high_resolution',
    sensor: 'binary_sensor.g4_doorbell_doorbell', correlationId: `source-b-${Date.now()}`,
    triggerTs: observed, frames: [],
  });
  const coalesced = attention.recordDoorbellEvidence(alias);
  assert.equal(coalesced.coalesced, true);
  assert.equal(coalesced.incidentId, original.incidentId);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_doorbell_incidents').get().count >= 1, true);
  assert.equal(attention.beginPerceptionIncidentEffect(capability).action, 'silent');
  attention.settlePerceptionIncidentPlan(capability, { result: 'silent' });
});

test('event- en binary-sensoralias zonder camerabinding blijven één fysieke beldruk', () => {
  const suffix = `nocamera_${Date.now()}`;
  const observed = new Date().toISOString();
  const first = attention.recordDoorbellEvidence(__cameraTriggerTest.issue({
    type: 'doorbell', camera: null, sensor: `event.${suffix}_doorbell`,
    correlationId: `event-${suffix}`, triggerTs: observed, frames: [],
  }));
  const alias = attention.recordDoorbellEvidence(__cameraTriggerTest.issue({
    type: 'doorbell', camera: null, sensor: `binary_sensor.${suffix}_doorbell_doorbell`,
    correlationId: `binary-${suffix}`, triggerTs: observed, frames: [],
  }));
  assert.equal(alias.coalesced, true);
  assert.equal(alias.incidentId, first.incidentId);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_doorbell_sources
    WHERE incident_id=?`).get(first.incidentId).count, 2);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  assert.equal(attention.perceptionIncidentContext(claim).incident.id, first.incidentId);
  attention.failPerceptionIncidentClaim(claim, 'alias-test afgerond');
});

test('een cameracache die tussen twee sensoraliasen verschijnt splitst de beldruk niet', () => {
  const suffix = `cache_race_${Date.now()}`;
  const observed = new Date().toISOString();
  const first = attention.recordDoorbellEvidence(__cameraTriggerTest.issue({
    type: 'doorbell', camera: null, sensor: `event.${suffix}_doorbell`,
    correlationId: `event-${suffix}`, triggerTs: observed, frames: [],
  }));
  const alias = attention.recordDoorbellEvidence(__cameraTriggerTest.issue({
    type: 'doorbell', camera: `camera.${suffix}_doorbell`,
    sensor: `binary_sensor.${suffix}_doorbell_doorbell`,
    correlationId: `binary-${suffix}`, triggerTs: observed, frames: [],
  }));
  assert.equal(alias.coalesced, true);
  assert.equal(alias.incidentId, first.incidentId);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  assert.equal(attention.perceptionIncidentContext(claim).incident.id, first.incidentId);
  attention.failPerceptionIncidentClaim(claim, 'cache-race-test afgerond');
});

test('een vastgelopen snapshot laat de authentieke deurbel zonder beeld tijdig doorgaan', async () => {
  const triggerTs = new Date().toISOString();
  let aborted = false;
  const issued = await __cameraTriggerTest.captureAndIssue({
    type: 'doorbell', source: 'home_assistant',
    camera: 'camera.timeout_doorbell', sensor: 'event.timeout_doorbell',
    correlationId: `snapshot-timeout-${Date.now()}`, triggerTs,
  }, 'camera.timeout_doorbell', {
    timeoutMs: 20,
    snapshot: (_camera, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        aborted = true;
        reject(signal.reason);
      }, { once: true });
    }),
  });
  assert.equal(aborted, true);
  assert.equal(issued.triggerTs, triggerTs);
  assert.equal(issued.frame, null);
  assert.deepEqual(issued.frames, []);

  const recorded = attention.recordDoorbellEvidence(issued);
  const claim = attention.claimReadyPerceptionIncident({ householdRecipients: recipients });
  const context = attention.perceptionIncidentContext(claim);
  assert.equal(context.incident.id, recorded.incidentId);
  assert.equal(context.observation.visual, 'unavailable');
  assert.equal(context.choices.actions.includes('household_photo'), false);
  assert.equal(context.choices.actions.includes('silent'), true);
  assert.equal(context.choices.actions.includes('household_text'), true);
  attention.failPerceptionIncidentClaim(claim, 'timeouttest afgerond');
});

test('cameraresponses worden op MIME en werkelijke bytegrootte begrensd', async () => {
  await assert.rejects(
    () => __cameraTriggerTest.readBoundedImageResponse(new Response('geen beeld', {
      headers: { 'content-type': 'text/plain' },
    }), 32),
    /beeldtype/u,
  );
  await assert.rejects(
    () => __cameraTriggerTest.readBoundedImageResponse(new Response(Buffer.alloc(9), {
      headers: { 'content-type': 'image/jpeg' },
    }), 8),
    /groottelimiet|te groot/u,
  );
  const image = await __cameraTriggerTest.readBoundedImageResponse(new Response(
    Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    { headers: { 'content-type': 'image/jpeg; charset=binary' } },
  ), 8);
  assert.deepEqual(image, Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
});

test('legacy privacymigratie volgt geen symlinks uit de schrijfbare workspace', () => {
  const legacy = path.join(dataDir, 'workspace', 'camera_trigger_frames');
  const outside = path.join(dataDir, 'outside.jpg');
  fs.mkdirSync(legacy, { recursive: true });
  fs.writeFileSync(outside, 'buiten de migratie');
  fs.symlinkSync(outside, path.join(legacy, 'forged.jpg'));
  assert.equal(attention.migrateLegacyDoorbellFrameDirectory(), 0);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'buiten de migratie');
  assert.equal(fs.lstatSync(path.join(legacy, 'forged.jpg')).isSymbolicLink(), true);
});
