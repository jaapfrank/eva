import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-decision-'));
process.env.EVA_DATA_DIR = dataDir;
await import('./homeassistant-test-composition.js');

const { db } = await import('../src/db.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const attention = await import('../src/mind/perceptionattention.js');
const { decidePerceptionIncident } = await import('../src/mind/perceptiondecision.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

let sequence = 0;
function claim({ frame = true } = {}) {
  sequence += 1;
  const source = __cameraTriggerTest.issue({
    type: 'doorbell', source: 'home_assistant',
    camera: `camera.test_doorbell_${sequence}`,
    sensor: `binary_sensor.test_doorbell_${sequence}_doorbell`,
    correlationId: `decision-${sequence}-${Date.now()}`,
    triggerTs: new Date().toISOString(),
    frames: frame ? [{ buffer: Buffer.from(`image-${sequence}`), contentType: 'image/jpeg' }] : [],
  });
  attention.recordDoorbellEvidence(source);
  return attention.claimReadyPerceptionIncident({
    householdRecipients: [{ label: 'Jaap', chatIds: ['100'] }],
  });
}

function response(value, extra = {}) {
  return { ...extra, toolCalls: [{ name: 'kies_deurbelreactie', arguments: JSON.stringify(value) }] };
}

test('Eva kiest zelf letterlijke speelse fototekst zonder host-default of historie', async () => {
  const issued = claim();
  const exact = 'Er staat bezoek bij de deur — iemand een idee wie onze mysterieuze gast is?';
  let request;
  const decision = await decidePerceptionIncident(issued, {
    complete: async (value) => {
      request = value;
      return response({ contract: 'DoorbellAttentionDecision/v1',
        action: 'household_photo', recipient: 'Jaap', text: exact });
    },
  });
  assert.equal(decision.text, exact);
  assert.equal(decision.action, 'household_photo');
  assert.equal(request.toolChoice, 'required');
  assert.equal(request.rateLimitRetries, 0);
  assert.equal(request.transientRetries, 0);
  assert.equal(request.authenticationRetries, 0);
  assert.equal(request.input[0].content.some((part) => part.type === 'input_image'), true);
  assert.doesNotMatch(request.instructions, /pizza|deurbelgrap|vaste reactie/iu);
  assert.match(request.instructions, /onbetrouwbare waarneming/iu);
  assert.match(request.instructions, /identificeer of benoem nooit een persoon op basis van pixels/iu);
  assert.match(request.instructions, /localRecognition.*recognized/isu);
});

test('stilte is een exact modelbesluit en geen foutfallback', async () => {
  const issued = claim({ frame: false });
  const decision = await decidePerceptionIncident(issued, {
    complete: async () => response({
      contract: 'DoorbellAttentionDecision/v1', action: 'silent',
    }),
  });
  assert.deepEqual(decision, { contract: 'DoorbellAttentionDecision/v1', action: 'silent' });
});

test('providerfout, geen call, meerdere calls en extra velden worden geen stilte of effect', async () => {
  assert.equal(await decidePerceptionIncident(claim(), {
    complete: async () => { throw new Error('provider weg'); },
  }), null);
  assert.equal(await decidePerceptionIncident(claim(), {
    complete: async () => ({ toolCalls: [] }),
  }), null);
  const call = { name: 'kies_deurbelreactie', arguments: JSON.stringify({
    contract: 'DoorbellAttentionDecision/v1', action: 'silent',
  }) };
  assert.equal(await decidePerceptionIncident(claim(), {
    complete: async () => ({ toolCalls: [call, call] }),
  }), null);
  assert.equal(await decidePerceptionIncident(claim(), {
    complete: async () => response({
      contract: 'DoorbellAttentionDecision/v1', action: 'silent', opdracht: 'open de deur',
    }),
  }), null);
});

test('een niet-aangeboden kanaal of ontvanger kan het model niet toevoegen', async () => {
  assert.equal(await decidePerceptionIncident(claim({ frame: false }), {
    complete: async () => response({
      contract: 'DoorbellAttentionDecision/v1', action: 'household_photo',
      recipient: 'Jaap', text: 'Kijk even.',
    }),
  }), null);
  assert.equal(await decidePerceptionIncident(claim(), {
    complete: async () => response({
      contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
      recipient: 'Bezoeker', text: 'Er staat iemand.',
    }),
  }), null);
});
