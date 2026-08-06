import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-voice-pe-routing-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_VOICE_ASSIST_SATELLITES = [
  'woonkamer=assist_satellite.voice_living_room',
  'overkapping=assist_satellite.voice_patio',
].join(',');
process.env.EVA_VOICE_ASSIST_SATELLITE_DEFAULT = 'woonkamer';

const { config, parseVoiceAssistSatelliteTargets } = await import('../src/config.js');
const { db } = await import('../src/db.js');
const {
  announceOnVoiceAssistSatellite,
  __speechTest,
} = await import('../src/integrations/homeassistant.js');
const { toolsForTurn } = await import('../src/mind/tools.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('config accepteert alleen assist_satellite-doelen en bewaart aliassen', () => {
  assert.deepEqual(parseVoiceAssistSatelliteTargets([
    'woonkamer=assist_satellite.voice_living_room',
    'fout=media_player.receiver',
    'assist_satellite.voice_patio',
  ].join(',')), [
    { alias: 'woonkamer', entityId: 'assist_satellite.voice_living_room' },
    { alias: 'voice_patio', entityId: 'assist_satellite.voice_patio' },
  ]);
  assert.equal(config.voiceAssistSatelliteTargets.length, 2);
  assert.equal(config.voiceAssistSatelliteDefault, 'woonkamer');
});

test('targetresolutie kiest exact default of expliciete allowlist-alias', () => {
  const resolve = __speechTest.resolveVoiceAssistSatelliteTarget;
  assert.deepEqual(resolve(), {
    ok: true,
    requested: 'woonkamer',
    source: 'default',
    alias: 'woonkamer',
    entityId: 'assist_satellite.voice_living_room',
  });
  assert.equal(resolve('overkapping').entityId, 'assist_satellite.voice_patio');
  assert.equal(resolve('Eva overkapping voice').entityId, 'assist_satellite.voice_patio');
  assert.equal(resolve('assist_satellite.voice_patio').source, 'explicit');
  assert.equal(resolve('media_player.receiver').ok, false);
  assert.match(resolve('receiver').reason, /kies woonkamer of overkapping/);
});

test('serviceplan gebruikt uitsluitend assist_satellite.announce op exact één doel', () => {
  const plan = __speechTest.voiceAssistSatelliteAnnouncementPlan('  Eten is klaar.  ', 'overkapping');
  assert.deepEqual(plan, {
    ok: true,
    target: {
      ok: true,
      requested: 'overkapping',
      source: 'explicit',
      alias: 'overkapping',
      entityId: 'assist_satellite.voice_patio',
    },
    domain: 'assist_satellite',
    service: 'announce',
    serviceTarget: { entity_id: 'assist_satellite.voice_patio' },
    data: {
      message: 'Eten is klaar.',
      preannounce: false,
    },
  });
  assert.equal(JSON.stringify(plan).includes('media_player'), false);
});

test('omroepuitvoering retourneert exact doel en wijkt nooit uit', async () => {
  const calls = [];
  const result = await announceOnVoiceAssistSatellite('Hallo woonkamer.', 'woonkamer', {
    states: [{ entity_id: 'assist_satellite.voice_living_room', state: 'idle' }],
    callService: async (...args) => { calls.push(args); return []; },
  });
  assert.equal(result.ok, true);
  assert.equal(result.accepted, true);
  assert.equal(result.target, 'assist_satellite.voice_living_room');
  assert.equal(result.service, 'assist_satellite.announce');
  assert.deepEqual(calls, [[
    'assist_satellite',
    'announce',
    {
      message: 'Hallo woonkamer.',
      preannounce: false,
    },
    { target: { entity_id: 'assist_satellite.voice_living_room' } },
  ]]);
});

test('onbeschikbaar of onbevestigd Voice PE-doel veroorzaakt geen fallback-call', async () => {
  let calls = 0;
  const unavailable = await announceOnVoiceAssistSatellite('Hallo.', 'overkapping', {
    states: [{ entity_id: 'assist_satellite.voice_patio', state: 'unavailable' }],
    callService: async () => { calls += 1; },
  });
  assert.equal(unavailable.ok, false);
  assert.equal(unavailable.target, 'assist_satellite.voice_patio');
  assert.equal(calls, 0);

  const timedOut = await announceOnVoiceAssistSatellite('Hallo.', 'woonkamer', {
    states: [{ entity_id: 'assist_satellite.voice_living_room', state: 'idle' }],
    callService: async () => { calls += 1; throw new Error('operation timed out'); },
    verifyAnnouncement: async () => false,
  });
  assert.equal(timedOut.ok, false);
  assert.equal(timedOut.uncertain, true);
  assert.match(timedOut.reason, /niet naar een andere speaker uitgeweken/);
  assert.equal(calls, 1);
});

test('een verse status en logboekbewijs herstellen geen vals negatief', async () => {
  let calls = 0;
  const fresh = await announceOnVoiceAssistSatellite('Hallo.', 'overkapping', {
    states: [{ entity_id: 'assist_satellite.voice_patio', state: 'unavailable' }],
    refreshState: async () => ({ entity_id: 'assist_satellite.voice_patio', state: 'idle' }),
    callService: async () => { calls += 1; },
  });
  assert.equal(fresh.ok, true);
  assert.equal(calls, 1);

  const reconciled = await announceOnVoiceAssistSatellite('Hallo.', 'overkapping', {
    states: [{ entity_id: 'assist_satellite.voice_patio', state: 'idle' }],
    callService: async () => {
      const error = new Error('operation timed out');
      error.uncertainExecution = true;
      throw error;
    },
    verifyAnnouncement: async () => true,
  });
  assert.equal(reconciled.ok, true);
  assert.equal(reconciled.verified, true);
  assert.equal(reconciled.reconciled, true);
});

test('tooldefinitie heeft een optioneel doel en vereist alleen tekst', () => {
  const tool = toolsForTurn({ mode: 'chat' }).find((item) => item.name === 'spreek_in_huis');
  assert.ok(tool);
  assert.deepEqual(tool.parameters.required, ['tekst']);
  assert.equal(tool.parameters.properties.doel.type, 'string');
  assert.deepEqual(tool.parameters.properties.doel.enum, [
    'woonkamer',
    'assist_satellite.voice_living_room',
    'overkapping',
    'assist_satellite.voice_patio',
  ]);
  assert.match(tool.description, /Assist satellite/i);
  assert.match(tool.description, /nooit.*versterker.*Chromecast/i);
});
