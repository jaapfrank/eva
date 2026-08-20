import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-doorbell-intercom-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.HA_URL = 'http://127.0.0.1:8123';
await import('./homeassistant-test-composition.js');

const { db } = await import('../src/db.js');
const {
  __speechTest,
  currentDoorbellIntercomTarget,
  resolveDoorbellIntercomTarget,
  speak,
} = await import('../src/integrations/homeassistant.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const doorbellCamera = {
  entity_id: 'camera.g4_doorbell_high_resolution',
  state: 'streaming',
  attributes: { friendly_name: 'G4 Doorbell' },
};

const availableDoorbellSpeaker = {
  entity_id: 'media_player.g4_doorbell',
  state: 'idle',
  attributes: { friendly_name: 'G4 Doorbell' },
};

test('alleen de exact aanwezige deurbelcamera en beschikbare deurbelspeaker worden gekoppeld', () => {
  const target = resolveDoorbellIntercomTarget(
    doorbellCamera.entity_id,
    [doorbellCamera, availableDoorbellSpeaker],
  );

  assert.deepEqual(target, {
    contract: 'DoorbellIntercomTarget/v1',
    cameraEntity: doorbellCamera.entity_id,
    speakerEntity: availableDoorbellSpeaker.entity_id,
  });
  assert.equal(Object.isFrozen(target), true);
});

test('een niet-deurbelcamera krijgt ook met een deurbelspeaker geen intercomtarget', () => {
  const garageCamera = {
    entity_id: 'camera.g5_garage_high_resolution',
    state: 'streaming',
    attributes: { friendly_name: 'G5 Garage' },
  };

  assert.equal(resolveDoorbellIntercomTarget(
    garageCamera.entity_id,
    [garageCamera, availableDoorbellSpeaker],
  ), null);
});

test('een mutable friendly name kan een willekeurige camera geen intercomauthority geven', () => {
  const renamedCamera = {
    entity_id: 'camera.garage',
    state: 'streaming',
    attributes: { friendly_name: 'Doorbell' },
  };
  assert.equal(resolveDoorbellIntercomTarget(
    renamedCamera.entity_id,
    [renamedCamera, availableDoorbellSpeaker],
  ), null);
});

test('een unavailable of unknown deurbelspeaker wordt geweigerd', () => {
  for (const state of ['unavailable', 'unknown']) {
    assert.equal(resolveDoorbellIntercomTarget(
      doorbellCamera.entity_id,
      [doorbellCamera, { ...availableDoorbellSpeaker, state }],
    ), null);
  }
});

test('meerdere mogelijke deurbelintercoms zijn ambigu en worden geweigerd', () => {
  assert.equal(resolveDoorbellIntercomTarget(
    doorbellCamera.entity_id,
    [doorbellCamera, availableDoorbellSpeaker, {
      entity_id: 'media_player.voordeur_deurbel',
      state: 'idle',
      attributes: { friendly_name: 'Voordeur deurbel' },
    }],
  ), null);
});

test('een willekeurige speaker of ontbrekend target levert nooit een default op', () => {
  const arbitrarySpeaker = {
    entity_id: 'media_player.woonkamer',
    state: 'idle',
    attributes: { friendly_name: 'Doorbell fallback' },
  };

  assert.equal(resolveDoorbellIntercomTarget(
    doorbellCamera.entity_id,
    [doorbellCamera, arbitrarySpeaker],
  ), null);
  assert.equal(resolveDoorbellIntercomTarget(null, [doorbellCamera, availableDoorbellSpeaker]), null);
  assert.equal(resolveDoorbellIntercomTarget('media_player.g4_doorbell', [
    doorbellCamera,
    availableDoorbellSpeaker,
  ]), null);
});

test('camera-identiteit wordt exact vergeleken zonder fuzzy alias of fallback', () => {
  const rows = [doorbellCamera, availableDoorbellSpeaker];

  assert.equal(resolveDoorbellIntercomTarget('doorbell', rows), null);
  assert.equal(resolveDoorbellIntercomTarget('camera.g4_doorbell', rows), null);
  assert.equal(resolveDoorbellIntercomTarget('camera.g4_doorbell_high_resolution_extra', rows), null);
  assert.equal(resolveDoorbellIntercomTarget('', rows), null);
});

test('een lege adaptercache levert geen door hogere lagen verzonnen intercomtarget', () => {
  assert.equal(currentDoorbellIntercomTarget(doorbellCamera.entity_id), null);
});

test('een vastgelopen TTS-audiopreflight eindigt begrensd vóór enige speakeropdracht', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (url, options = {}) => {
    calls += 1;
    if (String(url).includes('/api/tts_get_url')) {
      return new Response(JSON.stringify({ path: '/api/tts_proxy/doorbell.mp3' }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal.reason), { once: true });
    });
  };
  try {
    const started = Date.now();
    const result = await __speechTest.playableTtsUrl([{
      entity_id: 'tts.piper', state: 'idle', attributes: {},
    }], 'Ik laat binnen weten dat je er bent.', {
      isCurrent: () => true,
      timeoutMs: 25,
    });
    assert.equal(result.url, undefined);
    assert.match(result.error, /timeout|afgebroken/u);
    assert.equal(calls, 2);
    assert.ok(Date.now() - started < 1_000);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('elke TTS-route hoort uitsluitend genormaliseerde bewonersuitvoer', async () => {
  const originalFetch = globalThis.fetch;
  const generatedMessages = [];
  const serviceMessages = [];
  let calls = 0;
  globalThis.fetch = async (url, options = {}) => {
    calls += 1;
    const pathname = new URL(String(url)).pathname;
    if (pathname === '/api/states') {
      return new Response(JSON.stringify([
        availableDoorbellSpeaker,
        { entity_id: 'tts.piper', state: 'idle', attributes: {} },
      ]), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    if (pathname === '/api/tts_get_url') {
      generatedMessages.push(JSON.parse(String(options.body || '{}')).message);
      return new Response(JSON.stringify({ path: '/api/tts_proxy/eva.mp3' }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    if (pathname === '/api/tts_proxy/eva.mp3') {
      return new Response(Buffer.alloc(600), {
        status: 200, headers: { 'content-type': 'audio/mpeg' },
      });
    }
    if (pathname === '/api/services/media_player/play_media') {
      serviceMessages.push(JSON.parse(String(options.body || '{}')));
      return new Response('[]', {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    throw new Error(`onverwachte TTS-route: ${options.method || 'GET'} ${pathname}`);
  };
  try {
    const minusSign = String.fromCodePoint(0x2212);
    const result = await speak(
      `Buiten is het ${minusSign}5 °C — ik houd de deur nog even dicht.`,
      availableDoorbellSpeaker.entity_id,
    );
    assert.equal(result.ok, true);
    assert.deepEqual(generatedMessages, [
      'Buiten is het -5 °C, ik houd de deur nog even dicht.',
    ]);
    assert.equal(serviceMessages.length, 1);

    calls = 0;
    const empty = await speak(' — ', availableDoorbellSpeaker.entity_id);
    assert.equal(empty.ok, false);
    assert.match(empty.error, /tekst|spraak/u);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
