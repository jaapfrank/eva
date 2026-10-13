import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-voice-pe-routing-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_VOICE_ASSIST_SATELLITES = [
  'woonkamer=assist_satellite.voice_living_room',
  'overkapping=assist_satellite.voice_patio',
].join(',');
process.env.EVA_VOICE_ASSIST_SATELLITE_DEFAULT = 'woonkamer';
process.env.OPENAI_API_KEY = 'voice-routing-test-key';

await import('./voicebridge-test-composition.js');
await import('./homeassistant-test-composition.js');
await import('./agent-test-composition.js');

const { config, parseVoiceAssistSatelliteTargets } = await import('../src/config.js');
const { bus } = await import('../src/bus.js');
const { db } = await import('../src/db.js');
const {
  announceOnVoiceAssistSatellite,
  restCall,
  setPersonTrackers,
  __speechTest,
} = await import('../src/integrations/homeassistant.js');
const { __voiceBridgeTest, startVoiceBridge } = await import('../src/mind/voicebridge.js');
const { __projectRouteTest, handleMessageDetailed } = await import('../src/mind/agent.js');
const { controlRobot, exploreWithRobot } = await import('../src/integrations/robot.js');
const { __toolsTest, capabilityCatalog, toolsForTurn } = await import('../src/mind/tools.js');
const { understandTurn } = await import('../src/mind/understanding.js');

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

test('voice-supersession bereikt via restCall het echte fetch-AbortSignal', async () => {
  const originalFetch = globalThis.fetch;
  let requestStarted;
  const started = new Promise((resolve) => { requestStarted = resolve; });
  let fetchSignal = null;
  globalThis.fetch = async (_url, options = {}) => {
    fetchSignal = options.signal;
    requestStarted();
    return new Promise((_resolve, reject) => {
      const rejectAborted = () => reject(fetchSignal.reason || new DOMException('aborted', 'AbortError'));
      if (fetchSignal.aborted) rejectAborted();
      else fetchSignal.addEventListener('abort', rejectAborted, { once: true });
    });
  };

  const gate = __voiceBridgeTest.createVoiceTurnGate();
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  const oldIsCurrent = gate.begin(laneKey, 'oude-http-post', {
    deadlineAt: Date.now() + 5_000,
  });
  assert.equal(gate.track(
    'oude-http-post', laneKey, { reply: 'Oud.' }, null, satellite, Date.now() + 5_000,
  ), true);

  try {
    const delivery = gate.deliver('oude-http-post', oldIsCurrent, (signal) => restCall(
      'POST',
      '/api/events/eva_voice_reply',
      { id: 'oude-http-post', reply: 'Oud.' },
      { timeoutMs: 5_000, signal },
    ));
    await started;
    const newIsCurrent = gate.begin(laneKey, 'nieuwe-query', {
      deadlineAt: Date.now() + 5_000,
    });
    assert.equal(oldIsCurrent(), false);
    assert.equal(newIsCurrent(), true);
    assert.equal(fetchSignal.aborted, true, 'de echte fetch ziet de lane-revocation');
    assert.equal(await delivery, false);
    gate.finish(laneKey, 'nieuwe-query');
  } finally {
    globalThis.fetch = originalFetch;
  }
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
  assert.equal(
    __speechTest.voiceAssistSatelliteAnnouncementPlan(
      'Buiten is het koeler — zet gerust een raam open.', 'overkapping',
    ).data.message,
    'Buiten is het koeler, zet gerust een raam open.',
  );
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

test('een stale unavailable-status probeert exact het gekozen Voice PE-doel en wijkt nooit uit', async () => {
  let calls = 0;
  const unavailable = await announceOnVoiceAssistSatellite('Hallo.', 'overkapping', {
    states: [{ entity_id: 'assist_satellite.voice_patio', state: 'unavailable' }],
    callService: async () => { calls += 1; },
  });
  assert.equal(unavailable.ok, true);
  assert.equal(unavailable.verified, true);
  assert.equal(unavailable.accepted, true);
  assert.equal(unavailable.target, 'assist_satellite.voice_patio');
  assert.equal(unavailable.stale_target_state, 'unavailable');
  assert.equal(calls, 1);

  const timedOut = await announceOnVoiceAssistSatellite('Hallo.', 'woonkamer', {
    states: [{ entity_id: 'assist_satellite.voice_living_room', state: 'idle' }],
    callService: async () => { calls += 1; throw new Error('operation timed out'); },
    verifyAnnouncement: async () => false,
  });
  assert.equal(timedOut.ok, false);
  assert.equal(timedOut.uncertain, true);
  assert.equal(timedOut.effect_uncertain, true);
  assert.equal(timedOut.retryable, false);
  assert.match(timedOut.reason, /niet naar een andere speaker uitgeweken/);
  assert.equal(calls, 2);

  const unavailableBeforeEffect = await announceOnVoiceAssistSatellite('Hallo.', 'woonkamer', {
    states: [{ entity_id: 'assist_satellite.voice_living_room', state: 'idle' }],
    callService: async () => {
      calls += 1;
      throw new Error('HTTP 503 unavailable');
    },
  });
  assert.equal(unavailableBeforeEffect.ok, false);
  assert.equal(unavailableBeforeEffect.effect_uncertain, false);
  assert.equal(unavailableBeforeEffect.failure_class, 'unavailable');
  assert.equal(unavailableBeforeEffect.retryable, true);
  assert.equal(calls, 3);
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

test('dezelfde geaccepteerde omroep heeft per turn precies één fysiek effect', async () => {
  const calls = [];
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const announce = async (message, target) => {
    calls.push({ message, target });
    await pending;
    return {
      ok: true,
      verified: true,
      changed: true,
      accepted: true,
      target,
      message: 'Home Assistant heeft de omroep geaccepteerd.',
    };
  };

  const first = __toolsTest.announceOncePerTurn({
    turnId: 'turn-vincent-1',
    text: '  Jaap is onderweg. ',
    requestedTarget: 'overkapping',
    announce,
  });
  const duplicate = __toolsTest.announceOncePerTurn({
    turnId: 'turn-vincent-1',
    text: 'JAAP IS ONDERWEG.',
    requestedTarget: 'assist_satellite.voice_patio',
    announce,
  });
  release();
  const [firstResult, duplicateResult] = await Promise.all([first, duplicate]);

  assert.equal(firstResult.changed, true);
  assert.equal(duplicateResult.changed, false);
  assert.equal(duplicateResult.duplicate_prevented, true);
  assert.equal(duplicateResult.receipt_reused, true);
  assert.deepEqual(calls, [{
    message: 'Jaap is onderweg.',
    target: 'assist_satellite.voice_patio',
  }]);

  const replay = await __toolsTest.announceOncePerTurn({
    turnId: 'turn-vincent-1',
    text: 'Jaap is onderweg.',
    requestedTarget: 'overkapping',
    announce,
  });
  assert.equal(replay.receipt_reused, true);
  assert.equal(calls.length, 1);

  await __toolsTest.announceOncePerTurn({
    turnId: 'turn-vincent-2',
    text: 'Jaap is onderweg.',
    requestedTarget: 'overkapping',
    announce: async (message, target) => {
      calls.push({ message, target });
      return { ok: true, verified: true, changed: true, accepted: true, target };
    },
  });
  assert.equal(calls.length, 2);
});

test('een crashreceipt maakt een mogelijke eerdere omroep onzeker zonder haar te herhalen', async () => {
  let calls = 0;
  await assert.rejects(__toolsTest.announceOncePerTurn({
    turnId: 'turn-crash-na-acceptatie',
    text: 'Vincent, Jaap is onderweg.',
    requestedTarget: 'overkapping',
    announce: async () => {
      calls += 1;
      throw new Error('proces viel weg na mogelijk geaccepteerde servicecall');
    },
  }));

  const replay = await __toolsTest.announceOncePerTurn({
    turnId: 'turn-crash-na-acceptatie',
    text: 'Vincent, Jaap is onderweg.',
    requestedTarget: 'assist_satellite.voice_patio',
    announce: async () => {
      calls += 1;
      return { ok: true, verified: true, changed: true, accepted: true };
    },
  });
  assert.equal(calls, 1);
  assert.equal(replay.ok, false);
  assert.equal(replay.effect_uncertain, true);
  assert.equal(replay.retryable, false);
  assert.match(replay.reason, /herhaal.*niet|niet.*herhaal/iu);
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

test('een robotstatus-await die de voicebeurt inhaalt start geen vacuumservice', async () => {
  let current = true;
  const mutations = [];
  const result = await controlRobot('start', {
    isCurrent: () => current,
    getState: async () => {
      current = false;
      return {
        entity_id: config.robotEntityId,
        state: 'docked',
        attributes: { battery_level: 90 },
      };
    },
    callService: async (...args) => { mutations.push(args); },
    delay: async () => {},
  });

  assert.equal(mutations.length, 0);
  assert.deepEqual({
    ok: result.ok,
    changed: result.changed,
    stale: result.stale,
    failure_class: result.failure_class,
    retryable: result.retryable,
    effect_uncertain: result.effect_uncertain,
  }, {
    ok: false, changed: false, stale: true,
    failure_class: 'conflict', retryable: false, effect_uncertain: false,
  });
});

test('robotverkenning controleert actualiteit vóór enable en vóór de beweegpuls', async (t) => {
  const status = {
    ok: true,
    robot: { state: 'docked', battery: 90 },
    obstacle_avoidance: 'on',
    active_events: [],
    high_resolution_manual_control: { enabled: false },
  };
  const frame = { buffer: Buffer.alloc(2_100), contentType: 'image/jpeg', path: '/tmp/frame.jpg', bytes: 2_100 };

  await t.test('ingehaald tijdens cameralezing: nul mutaties', async () => {
    let current = true;
    const mutations = [];
    const result = await exploreWithRobot({}, {
      isCurrent: () => current,
      robotStatus: async () => status,
      captureRobotFrame: async () => { current = false; return frame; },
      valetudo: async (...args) => { mutations.push(args); },
      delay: async () => {},
      controlRobot: async () => { throw new Error('geen cleanup nodig'); },
    });
    assert.equal(mutations.length, 0);
    assert.equal(result.failure_class, 'conflict');
    assert.equal(result.effect_uncertain, false);
  });

  await t.test('ingehaald na enable: alleen veiligheidscleanup, geen draaipuls', async () => {
    let current = true;
    const mutations = [];
    const result = await exploreWithRobot({}, {
      isCurrent: () => current,
      robotStatus: async () => status,
      captureRobotFrame: async () => frame,
      valetudo: async (_method, _route, body) => {
        mutations.push(body);
        if (body.action === 'enable') current = false;
      },
      delay: async () => {},
      controlRobot: async () => { throw new Error('geen vacuumcleanup nodig'); },
    });
    assert.equal(mutations.some((body) => (
      body.action === 'move' && Number(body.vector?.angle) !== 0
    )), false);
    assert.deepEqual(mutations.map((body) => body.action), ['enable', 'move', 'disable']);
    assert.equal(result.failure_class, 'conflict');
    assert.equal(result.effect_uncertain, false);
  });
});

test('aanwezigheidsconfig muteert niet als de voicebeurt tijdens config-read vervalt', async () => {
  let current = true;
  const mutations = [];
  const people = {
    storage: [{
      id: 'jaap', name: 'Jaap', editable: true,
      device_trackers: ['device_tracker.telefoon_oud'],
    }],
    config: [],
  };
  const result = await setPersonTrackers('Jaap', ['device_tracker.telefoon_nieuw'], {
    isCurrent: () => current,
    stateStore: new Map([
      ['device_tracker.telefoon_nieuw', { entity_id: 'device_tracker.telefoon_nieuw' }],
      ['person.jaap', { entity_id: 'person.jaap', state: 'home', attributes: {} }],
    ]),
    listPersonConfigurations: async () => {
      current = false;
      return people;
    },
    wsCommand: async (payload) => { mutations.push(payload); },
  });

  assert.equal(mutations.length, 0);
  assert.equal(result.stale, true);
  assert.equal(result.failure_class, 'conflict');
  assert.equal(result.effect_uncertain, false);
});

test('corrupte voicewoorden worden alleen via twee actuele entityankers gegrond', async () => {
  const text = 'Zet de Hitler aan In de overkant in';
  const command = __projectRouteTest.directHomeCommand(text);
  assert.deepEqual(command, { doel: 'Hitler in overkant in', actie: 'aan' });
  const states = [{
    entity_id: 'switch.heater_overkapping', state: 'off',
    attributes: { friendly_name: 'Heater overkapping' },
  }, {
    entity_id: 'light.spots_overkapping', state: 'off',
    attributes: { friendly_name: 'Spotjes overkapping' },
  }, {
    entity_id: 'switch.heater_schuur', state: 'off',
    attributes: { friendly_name: 'Heater schuur' },
  }];
  const grounding = __toolsTest.resolveContextualHomeCommand(states, command);
  assert.deepEqual({
    status: grounding.status, resolution: grounding.resolution,
    entityId: grounding.entityId, action: grounding.action,
  }, {
    status: 'resolved', resolution: 'contextual_phonetic',
    entityId: 'switch.heater_overkapping', action: 'aan',
  });

  let modelCalls = 0;
  const result = await understandTurn({
    turnId: 'voice-corrupt-heater', channel: 'voice', origin: 'user', text,
  }, {
    capabilityCatalog: capabilityCatalog(), voiceHomeGrounding: grounding,
    complete: async () => { modelCalls += 1; throw new Error('niet nodig'); },
  });
  assert.equal(result.ok, true);
  assert.equal(result.source, 'voice_home_context');
  assert.equal(modelCalls, 0);
  assert.deepEqual(result.understanding.actions.map((action) => ({
    capability: action.capabilityId, operation: action.operationId,
    input: action.semanticInput, authorization: action.authorization,
  })), [{
    capability: 'home.device.control', operation: 'set',
    input: { target: 'switch.heater_overkapping', desiredState: 'on' },
    authorization: 'allowed',
  }]);
  const turnState = __projectRouteTest.semanticTurnState({
    result, person: 'Huisgenoot', userText: text, conversationId: 'voice:test',
    messageContext: { syntheticTest: false }, mode: 'chat',
  });
  assert.deepEqual(turnState.plans.map((plan) => ({ tool: plan.toolName, args: plan.args })), [{
    tool: 'bedien_huis', args: { doel: 'switch.heater_overkapping', actie: 'aan' },
  }]);
});

test('de gegronde heaterroute bereikt in een effectstub exact één service en verifieert terug', async () => {
  const calls = [];
  const before = {
    entity_id: 'switch.heater_overkapping', state: 'off',
    attributes: { friendly_name: 'Heater overkapping' },
  };
  const result = await __toolsTest.operateHome({
    target: before.entity_id, action: 'aan', isCurrent: () => true,
  }, {
    getState: async () => before,
    callService: async (...args) => { calls.push(args); return []; },
    verifyEntityState: async () => ({
      ok: true, reachableVerified: true, state: 'on', reachable: [before.entity_id], unavailable: [],
    }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.entity_id, before.entity_id);
  assert.deepEqual(calls, [['switch', 'turn_on', { entity_id: before.entity_id }]]);
});

test('contextuele voicegronding is generiek en geen Hitler-stringhack', () => {
  const states = [{
    entity_id: 'light.keuken_lamp', state: 'off',
    attributes: { friendly_name: 'Keuken lamp' },
  }, {
    entity_id: 'light.gang_lamp', state: 'off',
    attributes: { friendly_name: 'Gang lamp' },
  }];
  const command = __projectRouteTest.directHomeCommand('Zet de keukel lamp aan.');
  const result = __toolsTest.resolveContextualHomeCommand(states, command);
  assert.equal(result.status, 'resolved');
  assert.equal(result.entityId, 'light.keuken_lamp');
  assert.equal(__projectRouteTest.directHomeCommand('Vertel eens iets over Hitler.'), null);
});

test('een entitygelijkspel stelt één vraag en maakt geen uitvoeractie', async () => {
  const text = 'Zet de Hitler aan in de overkant in';
  const command = __projectRouteTest.directHomeCommand(text);
  const states = [{
    entity_id: 'switch.heater_overkapping_links', state: 'off',
    attributes: { friendly_name: 'Heater overkapping links' },
  }, {
    entity_id: 'switch.heater_overkapping_rechts', state: 'off',
    attributes: { friendly_name: 'Heater overkapping rechts' },
  }];
  const grounding = __toolsTest.resolveContextualHomeCommand(states, command);
  assert.equal(grounding.status, 'ambiguous');
  assert.equal((grounding.question.match(/\?/gu) || []).length, 1);

  let modelCalls = 0;
  const result = await understandTurn({
    turnId: 'voice-ambiguous-heater', channel: 'voice', origin: 'user', text,
  }, {
    capabilityCatalog: capabilityCatalog(), voiceHomeGrounding: grounding,
    complete: async () => { modelCalls += 1; throw new Error('geen effectroute'); },
  });
  assert.equal(modelCalls, 0);
  assert.equal(result.source, 'voice_home_ambiguity');
  assert.equal(result.understanding.firstDecision.kind, 'clarify');
  assert.equal(result.understanding.actions.length, 0);
  assert.deepEqual(result.understanding.deniedEffects, ['home']);
  assert.equal((result.understanding.firstDecision.replyDraft.match(/\?/gu) || []).length, 1);
});

test('geen contextmatch of mislukte HA-read blijft effectloos en vraagt één ding', async () => {
  const unknown = __toolsTest.resolveContextualHomeCommand([{
    entity_id: 'light.gang', state: 'off', attributes: { friendly_name: 'Gang' },
  }], { doel: 'koffiemolen in de serre', actie: 'aan' });
  assert.equal(unknown.status, 'ambiguous');
  assert.equal(unknown.candidates.length, 0);
  assert.equal(unknown.question, 'Welk apparaat bedoel je precies?');

  const unavailable = await __toolsTest.groundContextualHomeCommand(
    { doel: 'heater overkapping', actie: 'aan' },
    { loadStates: async () => { throw new Error('HA offline'); } },
  );
  const result = await understandTurn({
    turnId: 'voice-home-context-unavailable', channel: 'voice', origin: 'user',
    text: 'Zet de heater in de overkapping aan',
  }, {
    capabilityCatalog: capabilityCatalog(), voiceHomeGrounding: unavailable,
    complete: async () => { throw new Error('geen model- of effectroute'); },
  });
  assert.equal(result.source, 'voice_home_ambiguity');
  assert.equal(result.understanding.actions.length, 0);
  assert.equal((result.understanding.firstDecision.replyDraft.match(/\?/gu) || []).length, 1);
});

test('de productie-ingang weigert een vormgelijk voicebericht zonder private authority', async () => {
  await assert.rejects(Promise.resolve().then(() => handleMessageDetailed({
    channel: 'voice', sender: 'Huisgenoot', text: 'Zet de heater aan.',
    messageContext: {
      conversationId: 'test:voice-production-boundary',
      messageId: 'voice-production-boundary-1', syntheticTest: true,
    },
  })), (error) => error?.code === 'EVA_TURN_AUTHORITY_INGRESS_UNAVAILABLE');
});

test('de vrije gesprekspipeline levert bij gelijkspel alleen de natuurlijke controlevraag', async () => {
  const text = 'Zet de hitler aan in de overkant in';
  const authorityContext = __voiceBridgeTest.issueSyntheticUnderstandingContext({
    messageId: 'voice-grounding-ambiguity-1',
    satellite: 'assist_satellite.voice_patio',
    conversationId: 'test:voice-grounding-ambiguity',
    requester: 'Huisgenoot', text,
  });
  const result = await handleMessageDetailed({
    channel: 'voice', sender: 'Huisgenoot', text,
    messageContext: {
      ...authorityContext,
      voiceHomeStates: [{
        entity_id: 'switch.heater_overkapping_links', state: 'off',
        attributes: { friendly_name: 'Heater overkapping links' },
      }, {
        entity_id: 'switch.heater_overkapping_rechts', state: 'off',
        attributes: { friendly_name: 'Heater overkapping rechts' },
      }],
    },
  });
  assert.equal(result.noPlanDecision, 'clarify');
  assert.equal(result.tools.length, 0);
  assert.equal((result.reply.match(/\?/gu) || []).length, 1);
  assert.match(result.reply, /^Bedoel je /u);
});

test('supersession tijdens de HA-contextread levert geen plan en geen antwoord', async () => {
  const command = { doel: 'Hitler in overkant in', actie: 'aan' };
  let current = true;
  const grounding = await __toolsTest.groundContextualHomeCommand(command, {
    isCurrent: () => current,
    loadStates: async () => {
      current = false;
      return [{ entity_id: 'switch.heater_overkapping', state: 'off',
        attributes: { friendly_name: 'Heater overkapping' } }];
    },
  });
  assert.equal(grounding.status, 'stale');
  const result = await understandTurn({
    turnId: 'voice-stale-grounding', channel: 'voice', origin: 'user',
    text: 'Zet de Hitler aan in de overkant in',
  }, {
    capabilityCatalog: capabilityCatalog(), voiceHomeGrounding: grounding,
    complete: async () => { throw new Error('stale beurt mag niet naar model'); },
  });
  assert.equal(result.source, 'voice_home_stale');
  assert.equal(result.understanding.firstDecision.kind, 'silent');
  assert.equal(result.understanding.actions.length, 0);
  assert.equal(result.understanding.delivery.mode, 'silent');
});

const PROMPT_FRESHNESS = Object.freeze(['static', 'live', 'historical']);

function compactPromptFieldType(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '?';
  const bounds = (lower, upper) => {
    const hasLower = Object.hasOwn(value, lower) && Number.isFinite(value[lower]);
    const hasUpper = Object.hasOwn(value, upper) && Number.isFinite(value[upper]);
    return hasLower || hasUpper
      ? [hasLower ? value[lower] : null, hasUpper ? value[upper] : null] : null;
  };
  if (value.type === 'array') {
    const item = { a: compactPromptFieldType(value.items) };
    const limit = bounds('minItems', 'maxItems');
    return limit ? { ...item, b: limit } : item;
  }
  if (value.type === 'object' || value.properties) {
    const fields = Object.keys(value.properties || {}).sort();
    const required = [...new Set((value.required || []).map(String))].sort();
    return {
      r: fields.length > 0 && fields.length === required.length ? 'all' : required,
      f: Object.fromEntries(fields.map((field) => [
        field, compactPromptFieldType(value.properties[field]),
      ])),
    };
  }
  const result = { k: structuredClone(value.type ?? '?') };
  if (Array.isArray(value.enum)) result.e = structuredClone(value.enum);
  if (Object.hasOwn(value, 'pattern')) result.p = value.pattern;
  const limit = ['number', 'integer'].includes(value.type)
    ? bounds('minimum', 'maximum') : bounds('minLength', 'maxLength');
  if (limit) result.b = limit;
  return Object.keys(result).length === 1 ? result.k : result;
}

function compactPromptSchema(value = {}) {
  const fields = Object.keys(value.properties || {}).sort();
  const required = [...new Set((value.required || []).map(String))].sort();
  return {
    r: fields.length > 0 && fields.length === required.length ? 'all' : required,
    f: Object.fromEntries(fields.map((field) => [
      field, compactPromptFieldType(value.properties[field]),
    ])),
  };
}

function dereferencePromptSchema(payload, reference, used) {
  const schema = payload.inputSchemas[reference];
  assert.deepEqual(Object.keys(schema || {}).sort(), ['f', 'r']);
  const fieldMap = payload.fieldMaps[schema.f];
  assert.ok(fieldMap && typeof fieldMap === 'object' && !Array.isArray(fieldMap));
  used.schemas.add(reference);
  used.fieldMaps.add(schema.f);
  return {
    r: schema.r,
    f: Object.fromEntries(Object.entries(fieldMap).map(([field, typeReference]) => {
      assert.ok(Object.hasOwn(payload.fieldTypes, typeReference));
      used.fieldTypes.add(typeReference);
      return [field, payload.fieldTypes[typeReference]];
    })),
  };
}

test('voice-interpretatie gebruikt één compacte volledige registryprojectie en één poging', async () => {
  const catalog = capabilityCatalog();
  let request = null;
  let calls = 0;
  const result = await understandTurn({
    turnId: 'voice-catalog-canary', channel: 'voice', origin: 'user',
    text: 'Maak een klein programma en controleer het echt.',
  }, {
    capabilityCatalog: catalog,
    identityInstructions: 'E'.repeat(2_700),
    maxAttempts: 1,
    timeoutMs: 8_000,
    complete: async (value) => {
      calls += 1;
      request = value;
      throw new Error('request timeout');
    },
  });

  assert.equal(calls, 1);
  assert.equal(request.timeoutMs, 8_000);
  assert.equal(result.failure.code, 'interpreter_timeout');
  assert.equal(result.attempts, 1);
  const payload = JSON.parse(request.input[0].content[0].text);
  assert.equal(catalog.length, 49);
  assert.equal(payload.capabilities.length, 49);
  assert.equal(payload.capabilities.length, catalog.length);
  assert.equal(payload.capabilities.reduce((count, item) => count + item[1].length, 0),
    catalog.reduce((count, item) => count + item.operations.length, 0));
  assert.equal(payload.catalogFormat.contract, 'CapabilityCatalogPrompt/v2');
  assert.deepEqual(payload.catalogFormat.capability, [
    'id', 'operations', 'freshness(0=static|1=live|2=historical|[]=per-operation)',
  ]);
  assert.deepEqual(payload.catalogFormat.schema, ['r(required|all)', 'f(fieldMapRef)']);
  assert.equal(payload.catalogFormat.fieldMap, 'field:typeRef');
  const operations = payload.capabilities.flatMap(([id, rows, freshness]) => {
    assert.ok(Number.isInteger(freshness) || Array.isArray(freshness));
    if (Array.isArray(freshness)) assert.equal(freshness.length, rows.length);
    return rows.map((operation, index) => ({
      capabilityId: id,
      freshness: PROMPT_FRESHNESS[Array.isArray(freshness) ? freshness[index] : freshness],
      operation,
    }));
  });
  assert.equal(operations.length, 93);
  const expectedOperations = new Map(catalog.flatMap((capability) => (
    capability.operations.map((operation) => [
      `${capability.id}\u0000${operation.id}`, { capabilityId: capability.id, operation },
    ])
  )));
  const used = {
    schemas: new Set(), fieldMaps: new Set(), fieldTypes: new Set(), proofs: new Set(),
  };
  const actualOperations = new Map();
  for (const { capabilityId, freshness, operation } of operations) {
    assert.ok([9, 10].includes(operation.length));
    assert.ok(operation[6] === 0 || typeof operation[6] === 'string');
    assert.equal(typeof operation[7], 'string');
    assert.ok(Array.isArray(operation[8]));
    if (operation.length === 10) assert.ok(Array.isArray(operation[9]));
    const key = `${capabilityId}\u0000${operation[0]}`;
    assert.equal(actualOperations.has(key), false, `dubbele promptoperation ${key}`);
    const expected = expectedOperations.get(key)?.operation;
    assert.ok(expected, `onbekende promptoperation ${key}`);
    used.proofs.add(operation[7]);
    actualOperations.set(key, {
      effect: operation[1],
      supportsDeferred: operation[2] === 1,
      input: dereferencePromptSchema(payload, operation[3], used),
      execution: {
        mode: operation[4] === 1 ? 'iterative' : 'direct',
        materializable: operation[5], targetField: operation[6] || null,
      },
      proofNeeds: payload.proofSets[operation[7]],
      rebindableFields: operation[8],
      sourceBoundFields: operation[9] || [],
      freshnessClass: freshness,
    });
    assert.deepEqual(actualOperations.get(key), {
      effect: expected.effect,
      supportsDeferred: expected.supportsDeferred,
      input: compactPromptSchema(expected.input),
      execution: {
        mode: expected.execution.mode,
        materializable: expected.execution.materializable,
        targetField: expected.execution.target?.field || null,
      },
      proofNeeds: [...new Set(expected.proofNeeds)].sort(),
      rebindableFields: expected.rebindableFields,
      sourceBoundFields: expected.sourceBoundFields,
      freshnessClass: expected.freshnessClass,
    }, key);
  }
  assert.equal(actualOperations.size, expectedOperations.size);
  assert.deepEqual([...used.schemas].sort(), Object.keys(payload.inputSchemas).sort());
  assert.deepEqual([...used.fieldMaps].sort(), Object.keys(payload.fieldMaps).sort());
  assert.deepEqual([...used.fieldTypes].sort(), Object.keys(payload.fieldTypes).sort());
  assert.deepEqual([...used.proofs].sort(), Object.keys(payload.proofSets).sort());
  assert.deepEqual(operations.filter(({ operation }) => operation[6] !== 0)
    .map(({ capabilityId, operation }) => [capabilityId, operation[0], operation[6]]),
  [['workspace.file.write', 'write', 'pad']]);
  assert.equal(payload.catalogFormat.operation[6], 'target(0=none|field)');
  assert.ok(Buffer.byteLength(request.input[0].content[0].text) < 16_000,
    'de semantische payload hoort ruim onder de oude 90 KB te blijven');
  assert.ok(Buffer.byteLength(JSON.stringify(request)) < 30_000,
    'de volledige voice-request hoort met een realistische identiteit onder 30 KB te blijven');
});

test('publieke bus kan geen late, lege of niet-aangenomen voicebeurt starten', async () => {
  const completed = (text, kind = 'reply') => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output: [{
      type: 'function_call', call_id: `voice-${kind}`, name: 'commit_turn_understanding',
      arguments: JSON.stringify({
        dialogueAct: 'continue', goal: { statement: text, outcome: text, beneficiary: 'requester' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: kind === 'silent' ? 'silent' : 'reply', target: 'origin' },
        proofNeeds: [], ambiguities: [],
        firstDecision: { kind, requirementIds: [], replyDraft: kind === 'silent' ? '' : 'Ik hoor je.' },
      }),
    }] },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const textReply = () => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output: [{ type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: 'Ik hoor je.' }] }] },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const originalFetch = globalThis.fetch;
  const originalDateNow = Date.now;
  const timelineListenersBefore = bus.listenerCount('timeline');
  let clockOffsetMs = 0;
  let scenario = 'late';
  let providerCalls = 0;
  let replyOffers = 0;
  Date.now = () => originalDateNow() + clockOffsetMs;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/events/eva_voice_reply')) {
      replyOffers += 1;
      return new Response(scenario === 'false' ? 'false' : '{}', { status: 200 });
    }
    providerCalls += 1;
    if (scenario === 'late' && providerCalls === 1) clockOffsetMs += 2_000;
    return providerCalls % 2 === 1
      ? completed(`Voice ${scenario}`, scenario === 'empty' ? 'silent' : 'reply') : textReply();
  };
  try {
    startVoiceBridge({ deliveryWindowMs: 1_000 });
    for (const name of ['late', 'false', 'empty']) {
      scenario = name; providerCalls = 0; replyOffers = 0;
      const id = `voice-delivery-${name}`;
      bus.emit('voice_query', {
        id, vraag: `Voice ${name}`, sender: 'Jaap', speaker_id: 'jaap',
        speaker_identity_verified: true, satellite: 'assist_satellite.voice_patio',
        conversation_id: `delivery-${name}`,
      });
      await new Promise((resolve) => setTimeout(resolve, 20));
      const row = db.prepare("SELECT * FROM turn_tool_executions WHERE turn_id=? AND tool='@turn/understanding'")
        .get(`ingress:voice:voice:delivery-${name}:${id}`);
      assert.equal(row, undefined);
      assert.equal(providerCalls, 0);
      assert.equal(replyOffers, 0);
    }
    assert.equal(bus.listenerCount('timeline'), timelineListenersBefore);
    assert.deepEqual(db.prepare(`SELECT json_extract(meta, '$.id') AS id FROM timeline
      WHERE kind='voice_delivery_unconfirmed' AND json_extract(meta, '$.id') LIKE 'voice-delivery-%'
      ORDER BY id`).all(), []);
  } finally {
    Date.now = originalDateNow;
    globalThis.fetch = originalFetch;
  }
});
