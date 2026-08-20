import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-executor-'));
process.env.EVA_DATA_DIR = dataDir;
await import('./homeassistant-test-composition.js');

const { db, upsertPerson } = await import('../src/db.js');
const { setPersonHouseholdRole } = await import('../src/mind/household.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const attention = await import('../src/mind/perceptionattention.js');
const { runPerceptionIncidentOnce } = await import('../src/mind/perceptionexecutor.js');

const personId = upsertPerson('Jaap Deurbeltest', { telegram_id: '424242', notes: 'bewoner' });
setPersonHouseholdRole(personId, 'household_member', { source: 'test' });

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

let sequence = 0;
function record({ frame = false, camera = null } = {}) {
  sequence += 1;
  const cameraEntity = camera || `camera.executor_doorbell_${sequence}`;
  attention.recordDoorbellEvidence(__cameraTriggerTest.issue({
    type: 'doorbell', camera: cameraEntity,
    sensor: `binary_sensor.executor_doorbell_${sequence}_doorbell`,
    correlationId: `executor-${sequence}`, triggerTs: new Date().toISOString(),
    frames: frame ? [{ buffer: Buffer.from(`image-${sequence}`), contentType: 'image/jpeg' }] : [],
  }));
  return cameraEntity;
}

test('één eigen tekstbesluit veroorzaakt exact één household-transportcall', async () => {
  record();
  let calls = 0;
  let seen;
  const result = await runPerceptionIncidentOnce({
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
      recipient: 'Jaap Deurbeltest', text: 'Hoi! Ik kom eraan — één telletje.',
    }),
    sendText: async (text, recipient, options) => {
      calls += 1;
      seen = { text, recipient, options };
      return { ok: true, accepted: true, sent: [{ chatId: '424242', messageId: 1 }] };
    },
  });
  assert.equal(result.status, 'delivered');
  assert.equal(calls, 1);
  assert.equal(seen.text, 'Hoi! Ik kom eraan — één telletje.');
  assert.equal(seen.recipient, 'Jaap Deurbeltest');
  assert.deepEqual(seen.options.expectedTargetIds, ['424242']);
  assert.equal(seen.options.householdOnly, true);
  assert.equal(seen.options.checkCurrentBeforeEachTransport, true);
});

test('providerfout doet geen effect en wordt nooit als stilte geboekt', async () => {
  record({ frame: true });
  let effects = 0;
  const result = await runPerceptionIncidentOnce({
    decide: async () => null,
    sendText: async () => { effects += 1; },
    sendPhoto: async () => { effects += 1; },
    announce: async () => { effects += 1; },
  });
  assert.equal(result.status, 'decision_failed');
  assert.equal(effects, 0);
  const latest = db.prepare('SELECT terminal_reason FROM eva_doorbell_incidents ORDER BY id DESC LIMIT 1').get();
  assert.equal(latest.terminal_reason, 'decision_failed');
  const released = db.prepare(`SELECT frame_released_ts FROM eva_doorbell_incidents
    ORDER BY id DESC LIMIT 1`).get();
  assert.ok(Number.isFinite(Date.parse(released.frame_released_ts)));
});

test('stilte blijft een eigen keuze en roept geen transport aan', async () => {
  record();
  let effects = 0;
  const result = await runPerceptionIncidentOnce({
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'silent',
    }),
    sendText: async () => { effects += 1; },
    sendPhoto: async () => { effects += 1; },
    announce: async () => { effects += 1; },
  });
  assert.equal(result.status, 'silent');
  assert.equal(effects, 0);
});

test('een fotokeuze gebruikt exact de private bytes, caption en duurzame householdbinding', async () => {
  record({ frame: true });
  let seen;
  const result = await runPerceptionIncidentOnce({
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'household_photo',
      recipient: 'Jaap Deurbeltest', text: 'Kijk, onze bezoeker heeft gevoel voor timing.',
    }),
    sendPhoto: async (buffer, mime, text, recipient, options) => {
      seen = { buffer, mime, text, recipient, options };
      return { ok: true, accepted: true, sent: [{ chatId: '424242', messageId: 2 }] };
    },
  });
  assert.equal(result.status, 'delivered');
  assert.equal(seen.buffer.toString(), `image-${sequence}`);
  assert.equal(seen.mime, 'image/jpeg');
  assert.equal(seen.text, 'Kijk, onze bezoeker heeft gevoel voor timing.');
  assert.equal(seen.recipient, 'Jaap Deurbeltest');
  assert.deepEqual(seen.options.expectedTargetIds, ['424242']);
});

test('een intercomkeuze gebruikt uitsluitend de vooraf exact gebonden deurbelspeaker', async () => {
  const camera = 'camera.executor_doorbell_intercom';
  __cameraTriggerTest.setCachedStates([{
    entity_id: camera, state: 'streaming', attributes: { friendly_name: 'Executor Doorbell' },
  }, {
    entity_id: 'media_player.executor_doorbell', state: 'idle',
    attributes: { friendly_name: 'Executor Doorbell' },
  }]);
  record({ camera });
  let seen;
  const result = await runPerceptionIncidentOnce({
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'intercom',
      text: 'Hoi daar — ik laat binnen even weten dat je er bent.',
    }),
    announce: async (text, cameraEntity, options) => {
      seen = { text, cameraEntity, options };
      return { ok: true, accepted: true, target: options.expectedSpeakerEntity };
    },
  });
  __cameraTriggerTest.setCachedStates([]);
  assert.equal(result.status, 'delivered');
  assert.equal(seen.cameraEntity, camera);
  assert.equal(seen.options.expectedSpeakerEntity, 'media_player.executor_doorbell');
  assert.equal(seen.text, 'Hoi daar — ik laat binnen even weten dat je er bent.');
});

test('een bewonersbeurt die tijdens modelkeuze binnenkomt stopt vóór effect', async () => {
  record();
  let current = true;
  let effects = 0;
  const result = await runPerceptionIncidentOnce({
    isCurrent: () => current,
    decide: async (claim) => {
      const decision = attention.normalizePerceptionDecision(claim, {
        contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
        recipient: 'Jaap Deurbeltest', text: 'Dit mag niet meer worden verstuurd.',
      });
      current = false;
      return decision;
    },
    sendText: async () => { effects += 1; },
  });
  assert.equal(result.status, 'superseded_pre_effect');
  assert.equal(effects, 0);
});

test('een onzeker transportresultaat sluit terminal en wordt niet gereplayed', async () => {
  record();
  let calls = 0;
  const result = await runPerceptionIncidentOnce({
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
      recipient: 'Jaap Deurbeltest', text: 'Even geduld, ik kijk wie er thuis is.',
    }),
    sendText: async () => {
      calls += 1;
      return { ok: false, accepted: false, uncertain: true, info: 'timeout na send' };
    },
  });
  assert.equal(result.status, 'effect_uncertain');
  await runPerceptionIncidentOnce({ sendText: async () => { calls += 1; } });
  assert.equal(calls, 1);
});

test('de incidentdeadline blijft tot vlak vóór iedere transportmutatie gelden', async () => {
  record();
  let clock = Date.now();
  let outwardEffects = 0;
  const result = await runPerceptionIncidentOnce({
    now: () => clock,
    decide: async (claim) => attention.normalizePerceptionDecision(claim, {
      contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
      recipient: 'Jaap Deurbeltest', text: 'Deze reactie mag niet na de incidentdeadline landen.',
    }),
    sendText: async (_text, _recipient, options) => {
      clock += 10 * 60_000;
      if (!options.isCurrent()) {
        return { ok: false, accepted: false, stale: true, uncertain: false };
      }
      outwardEffects += 1;
      return { ok: true, accepted: true };
    },
  });
  assert.equal(result.status, 'failed');
  assert.equal(outwardEffects, 0);
});

test('een vóór I/O duurzaam plan wordt na procesachtige capability-loss exact eenmaal hervat', async () => {
  record();
  const claim = attention.claimReadyPerceptionIncident({
    householdRecipients: [{ label: 'Jaap Deurbeltest', chatIds: ['424242'] }],
  });
  const decision = attention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
    recipient: 'Jaap Deurbeltest', text: 'Ik had dit al besloten; nu voer ik het één keer uit.',
  });
  attention.issuePerceptionIncidentPlan(claim, decision);
  let calls = 0;
  const result = await runPerceptionIncidentOnce({
    sendText: async () => {
      calls += 1;
      return { ok: true, accepted: true, sent: [{ chatId: '424242', messageId: 3 }] };
    },
  });
  assert.equal(result.status, 'delivered');
  await runPerceptionIncidentOnce({ sendText: async () => { calls += 1; } });
  assert.equal(calls, 1);
});
