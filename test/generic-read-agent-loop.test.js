import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-generic-read-loop-');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'generic-read-loop-test-only';
process.env.ALLOW_HOME_CONTROL = 'false';
await import('./agent-test-composition.js');

const { config } = await import('../src/config.js');
const {
  db,
  recentOtherConversationsForPerson,
  relevantOlderConversationForPerson,
  upsertPerson,
} = await import('../src/db.js');
const { handleMessageDetailed } = await import('../src/mind/agent.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completed(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function functionCall(name, arguments_, callId) {
  return completed([{ type: 'function_call', name, call_id: callId,
    arguments: JSON.stringify(arguments_) }]);
}

function textReply(text) {
  return completed([{ type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text }] }]);
}

test('een complete maar inhoudelijk onvoldoende read blijft in dezelfde generieke agentlus', async () => {
  const originalFetch = globalThis.fetch;
  const question = 'Onderzoek de plotselinge temperatuurdaling in de woonkamer.';
  const entityId = 'sensor.woonkamer_temperatuur';
  const logbookPath = `/api/logbook/2026-08-14T08:00:00.000Z?entity=${entityId}`
    + '&end_time=2026-08-14T10:00:00.000Z';
  let modelCalls = 0;
  const modelTools = [];
  const modelInstructions = [];
  let stateReads = 0;
  let logbookReads = 0;
  let preDecisionLogbookReads = 0;

  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      if (url.endsWith('/api/states')) {
        stateReads += 1;
        return new Response(JSON.stringify([{
          entity_id: entityId, state: '18.2',
          attributes: { friendly_name: 'Temperatuur woonkamer', unit_of_measurement: '°C' },
        }]), { status: 200 });
      }
      if (url.includes('/api/logbook/')) {
        logbookReads += 1;
        if (!modelTools.includes('commit_turn_step')) preDecisionLogbookReads += 1;
        return new Response(JSON.stringify([{
          entity_id: entityId, state: '18.2', when: '2026-08-14T09:14:00.000Z',
          name: 'Temperatuur woonkamer',
          message: 'daalde nadat climate.woonkamer door automation.nachtstand naar 18 °C ging',
        }]), { status: 200 });
      }
      return new Response('{}', { status: 404 });
    }

    modelCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    modelTools.push(tool);
    modelInstructions.push(String(body.instructions || ''));
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: 'Onderzoek de plotselinge temperatuurdaling.',
        outcome: 'De aantoonbare oorzaak van de daling is uitgelegd.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{ capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
        semanticInput: { query: question, history: true },
        sourceSpans: [{ start: 0, end: question.length }] }],
      requestedEffects: ['read'], deniedEffects: ['home'],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }, 'understand-temperature-question');
    if (tool === 'commit_turn_step' && modelTools.filter((name) => name === tool).length === 1) {
      return functionCall(tool, {
        kind: 'execute', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
        action: { capabilityId: 'home.api.read', operationId: 'read',
          semanticInput: { path: logbookPath } },
        reasonCode: 'investigate',
      }, 'read-temperature-logbook');
    }
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s2'],
      action: null, reasonCode: 'verify',
    }, 'temperature-cause-complete');
    assert.deepEqual(body.tools || [], []);
    return textReply('De nachtstand zette de woonkamerverwarming om 09:14 terug naar 18 °C; daardoor zakte de temperatuur.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: { syntheticTest: true, syntheticConversationId: 'test:generic-read-loop',
        identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(stateReads, 1, JSON.stringify({ turn, modelTools, modelCalls,
    modelInstructions: modelInstructions.map((value) => value.slice(-500)) }));
  assert.equal(preDecisionLogbookReads, 0,
    'een speciale hostrecovery mag de generieke modelbeslissing niet vóór zijn');
  assert.equal(logbookReads, 1);
  assert.deepEqual(modelTools, [
    'commit_turn_understanding', 'commit_turn_step', 'commit_turn_step', null,
  ]);
  assert.deepEqual(turn.tools.map((item) => item.name), ['huis_status', 'ha_lees']);
  assert.match(turn.reply, /nachtstand.+18\s*°C/iu);
  assert.equal(turn.actionFailed, undefined);
  assert.equal(modelCalls, 4);
});

test('een inspectiebeurt beoordeelt iedere uitgegeven read voordat de volgende read start', async () => {
  const originalFetch = globalThis.fetch;
  const question = 'Lees de woonkamer en de buitentemperatuur en vergelijk ze.';
  const events = [];
  let stepCalls = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      if (url.endsWith('/api/states')) {
        events.push('read-r1');
        return new Response(JSON.stringify([{
          entity_id: 'sensor.woonkamer_temperatuur', state: '19.1',
          attributes: { friendly_name: 'Woonkamer temperatuur' },
        }]), { status: 200 });
      }
      if (url.endsWith('/api/states/sensor.buitentemperatuur')) {
        events.push('read-r2');
        return new Response(JSON.stringify({
          entity_id: 'sensor.buitentemperatuur', state: '14.2',
          attributes: { friendly_name: 'Buitentemperatuur' },
        }), { status: 200 });
      }
      return new Response('{}', { status: 404 });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Vergelijk beide actuele temperaturen.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [
        { capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
          semanticInput: { query: 'woonkamer temperatuur' },
          sourceSpans: [{ start: 0, end: question.length }] },
        { capabilityId: 'home.api.read', operationId: 'read', polarity: 'request',
          semanticInput: { path: '/api/states/sensor.buitentemperatuur' },
          sourceSpans: [{ start: 0, end: question.length }] },
      ],
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1', 'r2'], replyDraft: null },
    }, 'understand-two-reads');
    if (tool === 'commit_turn_step') {
      stepCalls += 1;
      events.push(`step-r${stepCalls}`);
      return functionCall(tool, {
        kind: 'complete', addressesRequirementIds: [`r${stepCalls}`],
        basisReceiptIds: [`s${stepCalls}`], action: null, reasonCode: 'verify',
      }, `complete-read-${stepCalls}`);
    }
    assert.deepEqual(body.tools || [], []);
    return textReply('Binnen is het 19,1 °C en buiten 14,2 °C; binnen is het 4,9 °C warmer.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: { syntheticTest: true, syntheticConversationId: 'test:two-read-order',
        identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(events, ['read-r1', 'step-r1', 'read-r2', 'step-r2']);
  assert.deepEqual(turn.tools.map((item) => item.name), ['huis_status', 'ha_lees']);
  assert.match(turn.reply, /4,9\s*°C warmer/iu);
});

test('een inspectiebeurt voert beurtbreed nooit meer dan vier verschillende reads uit', async () => {
  const originalFetch = globalThis.fetch;
  const question = 'Onderzoek de actuele meetwaarde via veilige bronnen.';
  let reads = 0;
  let stepCalls = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      reads += 1;
      const state = {
        entity_id: `sensor.probe_${reads}`, state: 'unknown', attributes: {},
      };
      return new Response(JSON.stringify(url.endsWith('/api/states') ? [state] : state), {
        status: 200,
      });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Onderbouw de actuele meetwaarde.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: Array.from({ length: 5 }, (_item, index) => ({
        capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
        semanticInput: { query: `meetwaarde bron ${index + 1}` },
        sourceSpans: [{ start: 0, end: question.length }],
      })),
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1', 'r2', 'r3', 'r4', 'r5'],
        replyDraft: null },
    }, 'understand-four-read-cap');
    if (tool === 'commit_turn_step') {
      stepCalls += 1;
      return functionCall(tool, {
        kind: 'complete', addressesRequirementIds: [`r${stepCalls}`],
        basisReceiptIds: [`s${stepCalls}`], action: null, reasonCode: 'verify',
      }, `complete-capped-read-${stepCalls}`);
    }
    assert.deepEqual(body.tools || [], []);
    return textReply('Ik kan de actuele meetwaarde nog niet betrouwbaar vaststellen.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: { syntheticTest: true, syntheticConversationId: 'test:four-read-cap',
        identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(reads, 4, 'initiële en adaptieve reads delen één harde beurtlimiet');
  assert.equal(turn.actionFailed, true);
  assert.match(turn.reply, /informatiebronnen|niet betrouwbaar vaststellen/iu);
  assert.doesNotMatch(turn.reply, /receipt|requirement|round_limit|tool/iu);
});

test('een gewone vraag gebruikt een oude relevante gespreksherinnering al bij het begrijpen', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Mila';
  const question = 'Hoe noemden we de blauwe sleutel voor de logeerkamer ook alweer?';
  const remembered = 'Afgesproken: de blauwe sleutel voor de logeerkamer heet voortaan de maansleutel.';
  const oldTs = new Date(Date.now() - 90 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:mila:oude-sleutel', person, 'user',
    'Voor de logeerkamer wil ik de blauwe sleutel een herkenbare naam geven.');
  insert.run(oldTs, 'telegram', 'telegram:mila:oude-sleutel', 'Eva', 'assistant', remembered);
  insert.run(oldTs, 'telegram', 'telegram:mila:onverwant', person, 'user',
    'De keukentafel mag zaterdag naar de kringloop.');
  insert.run(oldTs, 'telegram', 'telegram:mila:onverwant', 'Eva', 'assistant',
    'Prima, dan houden we zaterdag ruimte vrij voor de keukentafel.');

  const requests = [];
  globalThis.fetch = async (_input, options = {}) => {
    const body = JSON.parse(String(options.body || '{}'));
    requests.push(body);
    if (body.tools?.[0]?.name === 'commit_turn_understanding') {
      return functionCall('commit_turn_understanding', {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Noem de eerder afgesproken sleutelnaam.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'reply', requirementIds: [],
          replyDraft: 'Dat was de maansleutel.' },
      }, 'remember-old-key-name');
    }
    assert.deepEqual(body.tools || [], []);
    return textReply('Dat was de maansleutel.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:mila:nieuwe-vraag', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  const planningBody = JSON.stringify(requests[0]);
  assert.match(planningBody, /maansleutel/iu,
    'de relevante oude gespreksepisode moet vóór de eerste modelbeslissing beschikbaar zijn');
  assert.doesNotMatch(planningBody, /keukentafel/iu,
    'een ongerelateerde oude episode hoort niet als algemene gesprekshistorie mee te reizen');
  assert.equal(requests.length, 2, 'een gewone herinneringsvraag krijgt geen onnodige agentrondes');
  assert.deepEqual(turn.tools, []);
  assert.equal(turn.reply, 'Dat was de maansleutel.');
  assert.doesNotMatch(turn.reply, /nog open|controle|bewijs/iu);
});

test('geen voortgang in een readlus eindigt met een concrete huisgenootblokkade', async () => {
  const originalFetch = globalThis.fetch;
  const question = 'Waarom is de werkkamer ineens zo koud?';
  let modelCalls = 0;
  let haReads = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      haReads += 1;
      return new Response('Home Assistant is tijdelijk niet bereikbaar', { status: 503 });
    }
    modelCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Leg de aantoonbare oorzaak van de kou uit.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{ capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
        semanticInput: { query: question, history: true },
        sourceSpans: [{ start: 0, end: question.length }] }],
      requestedEffects: ['read'], deniedEffects: ['home'],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }, 'understand-cold-room');
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'execute', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
      action: { capabilityId: 'home.context.read', operationId: 'read',
        semanticInput: { query: question, history: true } },
      reasonCode: 'retry',
    }, 'repeat-failed-read');
    throw new Error(`onverwachte modelcall ${tool}`);
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: { syntheticTest: true,
        syntheticConversationId: 'test:generic-read-no-progress', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(haReads, 1, 'dezelfde mislukte bron wordt niet blind opnieuw aangeroepen');
  assert.equal(modelCalls, 2);
  assert.equal(turn.actionFailed, true);
  assert.match(turn.reply, /(?:bron|gegevens|informatie).+(?:niet bereikbaar|geen nieuwe|niet verder)/iu);
  assert.doesNotMatch(turn.reply,
    /no_progress|round_limit|deadline|receipt|requirement|capability|authority|tool/iu);
});

test('een verstreken readdeadline eindigt direct en natuurlijk met wat er ontbreekt', async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const question = 'Waardoor veranderde de luchtkwaliteit in de studeerkamer?';
  let clockOffset = 0;
  let modelCalls = 0;
  const modelTools = [];
  let haReads = 0;
  Date.now = () => originalNow() + clockOffset;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      haReads += 1;
      clockOffset = 60_000;
      return new Response(JSON.stringify([{
        entity_id: 'sensor.studeerkamer_luchtkwaliteit', state: '72',
        attributes: { friendly_name: 'Luchtkwaliteit studeerkamer' },
      }]), { status: 200 });
    }
    modelCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    modelTools.push(tool);
    if (tool !== 'commit_turn_understanding') throw new Error(`onverwachte modelcall ${tool}`);
    return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Leg de aantoonbare oorzaak van de verandering uit.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{ capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
        semanticInput: { query: question, history: true },
        sourceSpans: [{ start: 0, end: question.length }] }],
      requestedEffects: ['read'], deniedEffects: ['home'],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }, 'understand-air-quality');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: { syntheticTest: true,
        syntheticConversationId: 'test:generic-read-deadline', identityTrusted: true },
    });
  } finally {
    Date.now = originalNow;
    globalThis.fetch = originalFetch;
  }

  assert.equal(haReads, 1);
  assert.equal(modelCalls, 1, `na de verstreken beurtdeadline wordt geen nieuwe modelcall gestart: ${JSON.stringify({ modelTools, reply: turn.reply })}`);
  assert.equal(turn.actionFailed, true);
  assert.match(turn.reply, /(?:gegevens|informatie|bron).+(?:niet op tijd|tijd)/iu);
  assert.doesNotMatch(turn.reply,
    /no_progress|round_limit|deadline|receipt|requirement|capability|authority|tool/iu);
});

test('actuele status gebruikt oude gesprekstekst alleen als onbevestigde aanwijzing en leest live', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Noor';
  const question = 'Wat is nu de status van de lamp in de overkapping?';
  const oldTs = new Date(Date.now() - 90 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:noor:oude-lampstatus', person, 'user',
    'Hoe staat de lamp in de overkapping erbij?');
  insert.run(oldTs, 'telegram', 'telegram:noor:oude-lampstatus', 'Eva', 'assistant',
    'De lamp in de overkapping staat aan.');

  const requests = [];
  let understandingCalls = 0;
  let stateReads = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      assert.ok(url.endsWith('/api/states'));
      stateReads += 1;
      return new Response(JSON.stringify([{
        entity_id: 'light.overkapping', state: 'off',
        attributes: { friendly_name: 'Lamp overkapping' },
      }]), { status: 200 });
    }
    const body = JSON.parse(String(options.body || '{}'));
    requests.push(body);
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') {
      understandingCalls += 1;
      if (understandingCalls === 1) return functionCall(tool, {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Geef de actuele lampstatus.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'reply', requirementIds: [],
          replyDraft: 'De lamp in de overkapping staat aan.' },
      }, 'stale-status-reply');
      return functionCall(tool, {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Geef de actuele lampstatus.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{ capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
          semanticInput: { query: question }, sourceSpans: [{ start: 0, end: question.length }] }],
        requestedEffects: ['read'], deniedEffects: ['home'],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }, 'live-status-inspection');
    }
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
      action: null, reasonCode: 'verify',
    }, 'live-status-complete');
    assert.deepEqual(body.tools || [], []);
    return textReply('De lamp in de overkapping is nu uit.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:noor:actuele-lampstatus', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  const firstPlanningBody = JSON.stringify(requests[0]);
  assert.match(firstPlanningBody, /lamp in de overkapping staat aan/iu,
    'de oude episode mag als herinneringsaanwijzing beschikbaar blijven');
  assert.match(firstPlanningBody, /historical_unconfirmed/iu,
    'oude gesprekstekst heeft een structurele historische/onbevestigde markering');
  assert.equal(understandingCalls, 2,
    'een statusantwoord zonder verse read wordt afgewezen en eenmaal opnieuw geïnterpreteerd');
  assert.equal(stateReads, 1);
  assert.deepEqual(turn.tools.map((item) => item.name), ['huis_status']);
  assert.equal(turn.reply, 'De lamp in de overkapping is nu uit.');
  assert.doesNotMatch(turn.reply, /staat aan/iu);
});

test('een subjectgebonden toestandsvraag accepteert geen historische geheugentool als verse read', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Lina';
  const question = 'Staat de lamp in de hal aan?';
  const oldTs = new Date(Date.now() - 75 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:lina:oude-halstatus', person, 'user',
    'Hoe staat de lamp in de hal erbij?');
  insert.run(oldTs, 'telegram', 'telegram:lina:oude-halstatus', 'Eva', 'assistant',
    'De lamp in de hal staat aan.');

  let understandingCalls = 0;
  let stateReads = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      assert.ok(url.endsWith('/api/states'));
      stateReads += 1;
      return new Response(JSON.stringify([{
        entity_id: 'light.hal', state: 'off', attributes: { friendly_name: 'Lamp hal' },
      }]), { status: 200 });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') {
      understandingCalls += 1;
      const live = understandingCalls > 1;
      return functionCall(tool, {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Geef de actuele toestand van de hallamp.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{ capabilityId: live ? 'home.context.read' : 'memory.fact.read',
          operationId: live ? 'read' : 'search', polarity: 'request',
          semanticInput: live ? { query: question } : { vraag: question },
          sourceSpans: [{ start: 0, end: question.length }] }],
        requestedEffects: ['read'], deniedEffects: ['home'],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }, live ? 'fresh-hal-state' : 'historical-memory-is-not-live');
    }
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
      action: null, reasonCode: 'verify',
    }, 'hal-state-complete');
    assert.deepEqual(body.tools || [], []);
    return textReply(stateReads
      ? 'De lamp in de hal is nu uit.' : 'Volgens mijn oude herinnering staat de lamp aan.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:lina:actuele-halstatus', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(understandingCalls, 2,
    'memory.fact.read is historisch en voldoet niet als verse toestandsinspectie');
  assert.equal(stateReads, 1);
  assert.deepEqual(turn.tools.map((item) => item.name), ['huis_status']);
  assert.equal(turn.reply, 'De lamp in de hal is nu uit.');
});

test('recall uit een ander kanaal van 47 uur oud blijft onbevestigde historie voor lampstatus', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Nora';
  const question = 'Staat de lamp op zolder aan?';
  const recentTs = new Date(Date.now() - 47 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(recentTs, 'telegram', 'telegram:nora:zolderlamp', person, 'user',
    'Kun je de lamp op zolder controleren?');
  insert.run(recentTs, 'telegram', 'telegram:nora:zolderlamp', 'Eva', 'assistant',
    'De lamp op zolder staat aan.');

  const planningBodies = [];
  let understandingCalls = 0;
  let stateReads = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      stateReads += 1;
      return new Response(JSON.stringify([{
        entity_id: 'light.zolder', state: 'off', attributes: { friendly_name: 'Lamp zolder' },
      }]), { status: 200 });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') {
      planningBodies.push(JSON.stringify(body));
      understandingCalls += 1;
      const live = understandingCalls > 1;
      return functionCall(tool, {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Geef de actuele zolderlampstatus.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99,
        actions: live ? [{ capabilityId: 'home.context.read', operationId: 'read',
          polarity: 'request', semanticInput: { query: question },
          sourceSpans: [{ start: 0, end: question.length }] }] : [],
        requestedEffects: live ? ['read'] : [], deniedEffects: live ? ['home'] : [],
        temporal: { mode: live ? 'now' : 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' },
        proofNeeds: live ? ['source_read'] : [], ambiguities: [],
        firstDecision: live
          ? { kind: 'inspect', requirementIds: ['r1'], replyDraft: null }
          : { kind: 'reply', requirementIds: [], replyDraft: 'De lamp staat aan.' },
      }, live ? 'inspect-recent-cross-channel-state' : 'trust-recent-cross-channel-state');
    }
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
      action: null, reasonCode: 'verify',
    }, 'complete-live-zolder-state');
    assert.deepEqual(body.tools || [], []);
    return textReply(stateReads ? 'De lamp op zolder is nu uit.' : 'De lamp staat aan.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:nora:zolderstatus', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.match(planningBodies[0], /historical_unconfirmed/iu,
    'ook recente cross-channel recall is geen actuele gesprekstoestand');
  assert.equal(understandingCalls, 2);
  assert.equal(stateReads, 1);
  assert.equal(turn.reply, 'De lamp op zolder is nu uit.');
});

test('een actuele temperatuurconstructie gebruikt oude context alleen als aanwijzing en leest live', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Tess';
  const question = 'Hoe warm is het nu in de woonkamer?';
  const oldTs = new Date(Date.now() - 65 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:tess:oude-woonkamertemperatuur', person, 'user',
    'Hoe warm is de woonkamer?');
  insert.run(oldTs, 'telegram', 'telegram:tess:oude-woonkamertemperatuur', 'Eva', 'assistant',
    'De woonkamer is warm: 23 graden.');

  let understandingCalls = 0;
  let stateReads = 0;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      stateReads += 1;
      return new Response(JSON.stringify([{
        entity_id: 'sensor.woonkamer_temperatuur', state: '18.5',
        attributes: { friendly_name: 'Woonkamer temperatuur', unit_of_measurement: '°C' },
      }]), { status: 200 });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') {
      understandingCalls += 1;
      const live = understandingCalls > 1;
      return functionCall(tool, {
        dialogueAct: 'question',
        goal: { statement: question, outcome: 'Geef de actuele woonkamertemperatuur.',
          beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
        confidence: 0.99,
        actions: live ? [{ capabilityId: 'home.context.read', operationId: 'read',
          polarity: 'request', semanticInput: { query: question },
          sourceSpans: [{ start: 0, end: question.length }] }] : [],
        requestedEffects: live ? ['read'] : [], deniedEffects: live ? ['home'] : [],
        temporal: { mode: live ? 'now' : 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' },
        proofNeeds: live ? ['source_read'] : [], ambiguities: [],
        firstDecision: live
          ? { kind: 'inspect', requirementIds: ['r1'], replyDraft: null }
          : { kind: 'reply', requirementIds: [], replyDraft: 'Het is 23 graden.' },
      }, live ? 'inspect-live-temperature' : 'trust-old-temperature');
    }
    if (tool === 'commit_turn_step') return functionCall(tool, {
      kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
      action: null, reasonCode: 'verify',
    }, 'complete-live-temperature');
    assert.deepEqual(body.tools || [], []);
    return textReply(stateReads ? 'Het is nu 18,5 °C in de woonkamer.' : 'Het is 23 graden.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:tess:temperatuur-nu', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(understandingCalls, 2,
    'de onderwerpgebonden temperatuurconstructie vereist actuele waarneming');
  assert.equal(stateReads, 1);
  assert.equal(turn.reply, 'Het is nu 18,5 °C in de woonkamer.');
});

test('een live inspectplan mag tijdens herstel niet terugvallen naar historische geheugenread', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Fenna';
  const question = 'Staat de lamp in de keuken aan?';
  const oldTs = new Date(Date.now() - 70 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:fenna:oude-keukenlamp', person, 'user',
    'De keukenlamp stond toen aan.');
  insert.run(oldTs, 'telegram', 'telegram:fenna:oude-keukenlamp', 'Eva', 'assistant',
    'Ik zag toen dat de keukenlamp aan stond.');

  let firstStep = true;
  let liveReads = 0;
  let downgradeRetrySeen = false;
  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      liveReads += 1;
      const entityId = liveReads === 1 ? 'light.keuken' : 'light.keuken_verificatie';
      const state = { entity_id: entityId, state: 'off', attributes: {} };
      return new Response(JSON.stringify(url.endsWith('/api/states') ? [state] : state), {
        status: 200,
      });
    }
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Geef de actuele keukenlampstatus.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{ capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
        semanticInput: { query: question }, sourceSpans: [{ start: 0, end: question.length }] }],
      requestedEffects: ['read'], deniedEffects: ['home'],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }, 'understand-live-no-memory-downgrade');
    if (tool === 'commit_turn_step') {
      const retry = String(body.instructions || '').includes('turn_step_freshness_downgrade');
      if (firstStep) {
        firstStep = false;
        return functionCall(tool, {
          kind: 'execute', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
          action: { capabilityId: 'memory.fact.read', operationId: 'search',
            semanticInput: { vraag: 'keukenlamp status' } }, reasonCode: 'investigate',
        }, 'attempt-memory-downgrade');
      }
      if (retry) {
        downgradeRetrySeen = true;
        return functionCall(tool, {
          kind: 'execute', addressesRequirementIds: ['r1'], basisReceiptIds: ['s1'],
          action: { capabilityId: 'home.api.read', operationId: 'read',
            semanticInput: { path: '/api/states/light.keuken_verificatie' } },
          reasonCode: 'investigate',
        }, 'repair-with-live-read');
      }
      if (liveReads > 1) return functionCall(tool, {
        kind: 'complete', addressesRequirementIds: ['r1'], basisReceiptIds: ['s2'],
        action: null, reasonCode: 'verify',
      }, 'complete-after-live-read');
      return functionCall(tool, {
        kind: 'blocked', addressesRequirementIds: [], basisReceiptIds: ['s1'],
        action: null, reasonCode: 'external_block',
      }, 'blocked-after-memory-downgrade');
    }
    assert.deepEqual(body.tools || [], []);
    return textReply('De keukenlamp is nu uit.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:fenna:keukenstatus', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(downgradeRetrySeen, true);
  assert.equal(liveReads, 2);
  assert.doesNotMatch(turn.tools.map((item) => item.name).join(' '), /zoek_geheugen/iu);
  assert.equal(turn.reply, 'De keukenlamp is nu uit.');
});

test('een herinneringsvraag met alleen temporeel bijwoord nu behoudt de gewone fast path', async () => {
  const originalFetch = globalThis.fetch;
  const person = 'Sara';
  const question = 'Hoe noemden we de sleutel die ik nu nodig heb?';
  const oldTs = new Date(Date.now() - 85 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:sara:sleutelnaam', person, 'user',
    'We noemden de sleutel voor de berging voortaan maansleutel.');
  insert.run(oldTs, 'telegram', 'telegram:sara:sleutelnaam', 'Eva', 'assistant',
    'De sleutelnaam maansleutel is genoteerd.');

  let modelCalls = 0;
  globalThis.fetch = async (_input, options = {}) => {
    modelCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    if (tool === 'commit_turn_understanding') return functionCall(tool, {
      dialogueAct: 'question',
      goal: { statement: question, outcome: 'Noem de afgesproken sleutelnaam.',
        beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: question.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Dat is de maansleutel.' },
    }, 'remember-key-needed-now');
    assert.deepEqual(body.tools || [], []);
    return textReply('Dat is de maansleutel.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: person, text: question,
      messageContext: { conversationId: 'web:sara:sleutel-nu', identityTrusted: true },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(modelCalls, 2, 'een los “nu” maakt een herinneringsvraag geen toestandsinspectie');
  assert.deepEqual(turn.tools, []);
  assert.equal(turn.reply, 'Dat is de maansleutel.');
});

test('één generiek recallwoord is onvoldoende maar een aantoonbaar zeldzame term blijft werken', () => {
  const person = 'Ravi';
  const oldTs = new Date(Date.now() - 100 * 24 * 3600_000).toISOString();
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(oldTs, 'telegram', 'telegram:ravi:oude-lamp', person, 'user',
    'De lamp was gisteren aan.');
  insert.run(oldTs, 'telegram', 'telegram:ravi:oude-lamp', 'Eva', 'assistant',
    'Ik onthoud dat de lamp toen aan stond.');
  insert.run(oldTs, 'telegram', 'telegram:ravi:zeldzame-naam', person, 'user',
    'De reservesleutel heet maansleutel.');
  insert.run(oldTs, 'telegram', 'telegram:ravi:zeldzame-naam', 'Eva', 'assistant',
    'Maansleutel is genoteerd als de naam van de reservesleutel.');

  assert.deepEqual(relevantOlderConversationForPerson(
    person, 'web:ravi:status', 'Wat is nu de status van de lamp?', 3,
  ), [], 'één generiek betekeniswoord mag geen oude status als antwoordcontext ophalen');
  assert.deepEqual(relevantOlderConversationForPerson(
    person, 'web:ravi:twee-termen', 'lamp zolder', 3,
  ), [], 'bij meerdere betekeniswoorden is één generieke overlap onvoldoende');
  const rare = relevantOlderConversationForPerson(
    person, 'web:ravi:naam', 'Maansleutel?', 3,
  );
  assert.ok(rare.some((row) => /maansleutel/iu.test(row.content)),
    'een lange term die aantoonbaar maar in één oude episode voorkomt blijft herinnerbaar');
});

test('episodische relevance beoordeelt bij een groot archief hoogstens de harde kandidaatcap', () => {
  const person = 'Archief Tester';
  const sparsePerson = 'Spaarzame Archieflezer';
  const oldBase = Date.now() - 80 * 24 * 3600_000;
  upsertPerson(sparsePerson);
  const sparseInsert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  sparseInsert.run(new Date(oldBase - 10_000).toISOString(), 'telegram',
    'telegram:spaarzame-archieflezer:verre-episode', sparsePerson, 'user',
    'Een verreterm met ankerterm staat alleen diep in het archief.');
  sparseInsert.run(new Date(oldBase - 9_000).toISOString(), 'telegram',
    'telegram:spaarzame-archieflezer:verre-episode', 'Eva', 'assistant',
    'Ik onthoud de verreterm en ankerterm uit deze oude episode.');
  upsertPerson(person);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  db.transaction(() => {
    for (let index = 0; index < 900; index += 1) {
      const ts = new Date(oldBase + index * 1_000).toISOString();
      const conversationId = `telegram:archief-tester:${index}`;
      insert.run(ts, 'telegram', conversationId, person, 'user',
        `Archiefnotitie ${index} met zoekterm en ankerterm.`);
      insert.run(ts, 'telegram', conversationId, 'Eva', 'assistant',
        `Antwoord ${index} over zoekterm en ankerterm.`);
    }
  }).immediate();

  const relevance = (content, tokenText) => {
    const words = new Set(String(content || '').toLocaleLowerCase('nl-NL')
      .match(/[\p{L}\p{N}]{3,}/gu) || []);
    return String(tokenText || '').split(' ').filter(Boolean)
      .reduce((score, token) => score + Number(words.has(token)), 0);
  };
  let calls = 0;
  db.function('episodic_conversation_relevance', { deterministic: true }, (content, tokenText) => {
    calls += 1;
    return relevance(content, tokenText);
  });
  try {
    const outsideTail = relevantOlderConversationForPerson(
      sparsePerson, 'web:spaarzame-archieflezer:nieuw', 'verreterm ankerterm', 3,
    );
    assert.deepEqual(outsideTail, [],
      'recall mist bewust een sparse afzender buiten de harde nieuwste-id-projectie');
    const rows = relevantOlderConversationForPerson(
      person, 'web:archief-tester:nieuw', 'zoekterm ankerterm', 3,
    );
    assert.ok(rows.length > 0);
    assert.ok(calls > 0, 'de teller bewijst dat relevance werkelijk is uitgevoerd');
    assert.ok(calls <= 320,
      `relevance hoort alleen de vooraf begrensde kandidaten te zien: ${calls}`);
  } finally {
    db.function('episodic_conversation_relevance', { deterministic: true }, relevance);
  }
});

test('recente persoonsrecall gebruikt een harde newest-id-tail en sluit gemengde threads uit', () => {
  const sparsePerson = 'Recente Spaarzame Lezer';
  const mixedPerson = 'Recente Gemengde Lezer';
  const now = new Date().toISOString();
  upsertPerson(sparsePerson);
  upsertPerson(mixedPerson);
  const insert = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`);
  insert.run(now, 'telegram', 'telegram:recente-spaarzame:diep', sparsePerson, 'user',
    'Diepe recente persoonsregel buiten de tail.');
  insert.run(now, 'telegram', 'telegram:recente-spaarzame:diep', 'Eva', 'assistant',
    'Dit antwoord hoort eveneens buiten de harde tail te vallen.');
  db.transaction(() => {
    for (let index = 0; index < 700; index += 1) insert.run(
      now, 'web', `web:recall-noise:${index}`, `Ruis ${index}`, 'user', `Ruisregel ${index}`,
    );
  }).immediate();

  assert.deepEqual(recentOtherConversationsForPerson(
    sparsePerson, 'web:recente-spaarzame:nieuw', 12,
  ), [], 'een sparse afzender buiten de newest-id-tail wordt bewust gemist');

  insert.run(now, 'telegram', 'telegram:recente-gemengde:thread', mixedPerson, 'user',
    'gemengdemarker van de bedoelde persoon');
  insert.run(now, 'telegram', 'telegram:recente-gemengde:thread', 'Andere Bewoner', 'user',
    'gemengdemarker van een andere bewoner');
  insert.run(now, 'telegram', 'telegram:recente-gemengde:thread', 'Eva', 'assistant',
    'gemengdemarker antwoord');
  insert.run(now, 'telegram', 'telegram:recente-gemengde:veilig', mixedPerson, 'user',
    'veiligemarker van één persoon');
  insert.run(now, 'telegram', 'telegram:recente-gemengde:veilig', 'Eva', 'assistant',
    'veiligemarker antwoord');

  const rows = recentOtherConversationsForPerson(
    mixedPerson, 'web:recente-gemengde:nieuw', 12,
  );
  assert.ok(rows.some((row) => /veiligemarker/iu.test(row.content)),
    'een kleine één-op-éénthread binnen de tail blijft beschikbaar');
  assert.ok(rows.every((row) => !/gemengdemarker/iu.test(row.content)),
    'een thread met een tweede userparticipant levert geen persoonsrecall');
});
