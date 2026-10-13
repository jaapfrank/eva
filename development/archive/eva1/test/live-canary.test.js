import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';
import Database from 'better-sqlite3';

const previousDataDir = process.env.EVA_DATA_DIR;
const runtimeDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-live-canary-runtime-'));
process.env.EVA_DATA_DIR = runtimeDataDir;

const {
  REAL_LIFE_SCENARIOS,
  validateScenarioCorpus,
} = await import('../scripts/real-life-scenarios.mjs');
const {
  evaluateObservation,
  evaluateLiveChecks,
  extractTurnObservation,
  parseCliArgs,
  runCanaries,
  scenarioConversationId,
  scenarioEligibility,
  selectScenarios,
  stepShouldRun,
} = await import('../scripts/live-canary.mjs');
const { db: runtimeDb } = await import('../src/db.js');

after(() => {
  runtimeDb.close();
  fs.rmSync(runtimeDataDir, { recursive: true, force: true });
  if (previousDataDir === undefined) delete process.env.EVA_DATA_DIR;
  else process.env.EVA_DATA_DIR = previousDataDir;
});

test('live-canary testharnas opent runtime-imports uitsluitend in een verse tempdatabase', () => {
  assert.equal(fs.realpathSync(runtimeDb.name), path.join(runtimeDataDir, 'eva.db'));
  assert.notEqual(fs.realpathSync(runtimeDb.name), path.join(process.cwd(), 'data', 'eva.db'));
});

test('real-life corpus is uniek, declaratief en dekt alle gevraagde domeinen', () => {
  assert.deepEqual(validateScenarioCorpus(), []);
  const domains = new Set(REAL_LIFE_SCENARIOS.map((scenario) => scenario.domain));
  for (const domain of [
    'house-negation', 'voice-negation', 'voice-execution-continuation',
    'photo-correction', 'location-access-point', 'programming-read-only',
    'earning-read-only', 'ordinary-humour',
  ]) assert.equal(domains.has(domain), true, domain);
  assert.equal(JSON.stringify(REAL_LIFE_SCENARIOS).includes('expectedAnswer'), false);
});

test('effectscenario vereist de expliciete CLI-poort en fotocontext blijft handmatig', () => {
  const voice = REAL_LIFE_SCENARIOS.find((scenario) => scenario.id === 'voice-explicit-and-continuation');
  const photo = REAL_LIFE_SCENARIOS.find((scenario) => scenario.id === 'photo-reply-is-not-a-name');
  assert.match(scenarioEligibility(voice).reason, /--allow-effects/);
  assert.equal(scenarioEligibility(voice, { allowEffects: true }).runnable, true);
  assert.equal(scenarioEligibility(photo, { allowEffects: true }).runnable, false);
  assert.match(scenarioEligibility(photo, { allowEffects: true }).reason, /niet injecteren/);
});

test('CLI selecteert expliciete scenario’s en weigert onbekende ids', () => {
  const options = parseCliArgs(['--scenario', 'ordinary-situational-humour', '--json']);
  assert.equal(options.json, true);
  assert.deepEqual(selectScenarios(options).map((scenario) => scenario.id), ['ordinary-situational-humour']);
  assert.throws(
    () => selectScenarios({ ...options, scenarioIds: ['bestaat-niet'] }),
    /onbekende scenario-id/,
  );
});

test('beoordelaar accepteert gedrag zonder golden antwoord', () => {
  const expected = {
    contract: { kinds: ['conversation'], requiredTools: { exact: [] } },
    offeredTools: { excludes: ['bedien_huis'] },
    calls: { excludes: ['bedien_huis'], max: 1 },
    effects: { mode: 'none', allowedTools: [] },
    reply: {
      minChars: 10,
      mustMatchAny: [{ description: 'lamp blijft staan', patterns: ['niet uit', 'ongemoeid'] }],
      forbiddenPatterns: ['beurtcontract'],
    },
  };
  const result = evaluateObservation(expected, {
    contract: { kind: 'conversation', requiredTools: [], offeredTools: ['huis_status'] },
    actions: [],
    reply: 'Ik laat de lamp ongemoeid.',
  });
  assert.equal(result.ok, true);
});

test('iedere live-beoordeling weigert en- en em-dashes in Eva haar antwoord', () => {
  const expected = {
    contract: {}, calls: {}, effects: { mode: 'none', allowedTools: [] }, reply: {},
  };
  const observation = { contract: {}, actions: [] };
  assert.equal(evaluateObservation(expected, {
    ...observation, reply: 'De keuken is uit, twee lampen zijn offline.',
  }).ok, true);
  for (const reply of [
    'De keuken is uit – twee lampen zijn offline.',
    'De keuken is uit — twee lampen zijn offline.',
  ]) {
    const result = evaluateObservation(expected, { ...observation, reply });
    assert.equal(result.ok, false);
    assert.match(result.failures.join('\n'), /lange gedachtestreep/);
  }
});

function partialKitchenObservation(reply, mutateResult = () => {}) {
  const reachable = Array.from({ length: 8 }, (_, index) => ({
    entity_id: `light.keuken_${index + 1}`,
    name: `Keukenlamp ${index + 1}`,
    state: 'off',
  }));
  const unavailable = [
    { entity_id: 'light.eettafel_links', name: 'Eettafel links', state: 'unavailable' },
    { entity_id: 'light.eettafel_rechts', name: 'Eettafel rechts', state: 'unavailable' },
  ];
  const result = {
    contract: 'ActionResult/v1',
    tool: 'bedien_huis',
    ok: true,
    verified: true,
    changed: true,
    partial: true,
    needs_followup: true,
    effect_uncertain: false,
    data: {
      ok: true,
      verified: true,
      changed: true,
      partial: true,
      needs_followup: true,
      entity_id: 'light.keuken',
      after: 'off',
      evidence: {
        assessment: {
          ok: false,
          entity_id: 'light.keuken',
          state: 'off',
          target_state: 'off',
          reachableVerified: true,
          partial: true,
          expected: 'off',
          isGroup: true,
          members: [...reachable, ...unavailable].map((member) => ({ ...member })),
          reachable,
          unavailable,
          unresolved: unavailable.map((member) => ({ ...member })),
        },
      },
    },
  };
  mutateResult(result);
  return {
    contract: {},
    actions: [{
      name: 'bedien_huis', ok: true, verified: true, changed: true, result,
    }],
    reply,
  };
}

function canonicalTestJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalTestJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalTestJson(value[key])}`).join(',')}}`;
}

function sha256(value) {
  return createHash('sha256').update(String(value)).digest('hex');
}

function verifiedExecutionRow({
  executionKey = 'execution:live-canary:1', executionSeq = 1,
  turnId = 'turn-live-a', tool = 'bedien_huis', args = {}, output = {},
} = {}) {
  const storedArgs = canonicalTestJson(args);
  const storedOutput = typeof output === 'string' ? output : JSON.stringify(output);
  const binding = null;
  const semanticHash = sha256(canonicalTestJson({ turnId, tool, args, binding }));
  const executionContract = 'ExecutionRecord/v2';
  const intentRecordSha256 = sha256(canonicalTestJson({
    executionContract, executionSeq, turnId, tool, args, binding, semanticHash,
  }));
  const row = {
    execution_key: executionKey,
    turn_id: turnId,
    tool,
    args: storedArgs,
    status: 'done',
    started_ts: '2026-08-15T09:00:00.000Z',
    finished_ts: '2026-08-15T09:00:01.000Z',
    output: storedOutput,
    semantic_hash: semanticHash,
    semantic_binding: null,
    intent_record_sha256: intentRecordSha256,
    output_sha256: sha256(storedOutput),
    material_sha256: null,
    completion_record_sha256: null,
    execution_seq: executionSeq,
    execution_contract: executionContract,
    execution_verification: 'verified',
  };
  row.completion_record_sha256 = sha256(canonicalTestJson({
    executionContract,
    executionSeq,
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  }));
  return row;
}

test('keuken-deelsuccesproef bewaakt de echte effectpoort en twee natuurlijke beurten', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => (
    item.id === 'kitchen-light-partial-success-continuation'
  ));
  assert.ok(scenario);
  assert.equal(scenario.risk, 'physical_effect');
  assert.equal(scenario.conversationContext, 'real_person');
  assert.match(scenarioEligibility(scenario).reason, /--allow-effects/);
  assert.equal(scenarioEligibility(scenario, { allowEffects: true }).runnable, true);
  assert.deepEqual(scenario.steps.map((step) => step.text), [
    'Zet het licht uit in de keuken.',
    'En, gelukt?',
  ]);
  assert.deepEqual(scenario.steps[0].expected.calls.exact, ['bedien_huis']);
  assert.equal(scenario.steps[0].expected.calls.counts.bedien_huis, 1);
  assert.deepEqual(scenario.steps[0].expected.actionResult.equals, {
    contract: 'ActionResult/v1',
    tool: 'bedien_huis',
    ok: true,
    verified: true,
    changed: true,
    partial: true,
    needs_followup: true,
    effect_uncertain: false,
    'data.ok': true,
    'data.verified': true,
    'data.changed': true,
    'data.partial': true,
    'data.needs_followup': true,
    'data.after': 'off',
    'data.evidence.assessment.expected': 'off',
    'data.evidence.assessment.reachableVerified': true,
    'data.evidence.assessment.isGroup': true,
  });
  assert.deepEqual(scenario.steps[1].expected.calls.exact, ['ha_lees']);
  assert.equal(scenario.steps[0].expected.homeReadback.entityPath, 'data.entity_id');
  assert.equal(scenario.steps[1].expected.homeReadback.currentActionTool, 'ha_lees');
  assert.equal(scenario.steps[1].expected.homeReadback.source, 'scenario');
  assert.doesNotMatch(JSON.stringify(scenario), /light\.keuken(?:\b|_)/iu);
});

test('keuken-deelsuccesproef wijst de echte kapotte reply af en accepteert parafrases', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => (
    item.id === 'kitchen-light-partial-success-continuation'
  ));
  const expected = scenario.steps[0].expected;
  for (const reply of [
    'Keuken staat uit op de acht bereikbare lampen. Twee lampen zijn offline, dus die kon ik niet controleren.',
    'De bereikbare keukenlampen zijn uit. Eettafel links en Eettafel rechts zijn offline.',
  ]) {
    const result = evaluateObservation(expected, partialKitchenObservation(reply));
    assert.equal(result.ok, true, `${reply}\n${result.failures.join('\n')}`);
    assert.equal(result.exercise?.status, 'exercised');
  }

  const broken = evaluateObservation(expected, partialKitchenObservation(
    'Nog open: Ik kon de relevante gegevens niet volledig uitlezen, dus ik weet het antwoord nog niet zeker.',
  ));
  assert.equal(broken.ok, false);
  assert.equal(broken.exercise?.status, 'exercised');
  assert.match(broken.failures.join('\n'), /bereikbaar|offline|totale mislukking|ticketjargon/iu);

  const falseCoverage = evaluateObservation(expected, partialKitchenObservation(
    'Alles in de keuken is uit. Twee lampen zijn offline.',
  ));
  assert.equal(falseCoverage.ok, false);
  assert.match(falseCoverage.failures.join('\n'), /volledige dekking/iu);

  const negated = evaluateObservation(expected, partialKitchenObservation(
    'De bereikbare keukenlampen zijn niet uit. Twee lampen zijn offline.',
  ));
  assert.equal(negated.ok, false);
  assert.match(negated.failures.join('\n'), /ontken|niet uit|succes/iu);

  const oneOfTwoNames = evaluateObservation(expected, partialKitchenObservation(
    'De bereikbare keukenlampen zijn uit. Alleen Eettafel links is offline.',
  ));
  assert.equal(oneOfTwoNames.ok, false);
  assert.match(oneOfTwoNames.failures.join('\n'), /aantal|alle.*naam|concreet/iu);

  const negatedOffline = evaluateObservation(expected, partialKitchenObservation(
    'De bereikbare keukenlampen zijn uit. Twee lampen zijn niet offline.',
  ));
  assert.equal(negatedOffline.ok, false);
  assert.match(negatedOffline.failures.join('\n'), /offline|onbereikbaar|beperking/iu);

  const wrongSubjectState = evaluateObservation(expected, partialKitchenObservation(
    'De bereikbare keukenlampen zijn aan; de afzuigkap is uit. Twee lampen zijn offline.',
  ));
  assert.equal(wrongSubjectState.ok, false);
  assert.match(wrongSubjectState.failures.join('\n'), /bereikbaar|succes|toestand/iu);
});

test('keuken-deelsuccesproef rapporteert een niet geraakte partial branch als not_exercised', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => (
    item.id === 'kitchen-light-partial-success-continuation'
  ));
  const misses = [
    (receipt) => { receipt.partial = false; },
    (receipt) => { receipt.needs_followup = false; },
    (receipt) => { receipt.effect_uncertain = true; },
    (receipt) => { receipt.data.after = 'on'; },
    (receipt) => { receipt.data.evidence.assessment.expected = 'on'; },
    (receipt) => { receipt.data.evidence.assessment.reachableVerified = false; },
    (receipt) => { receipt.data.evidence.assessment.reachable = []; },
    (receipt) => { receipt.data.evidence.assessment.reachable[0].state = 'on'; },
    (receipt) => { receipt.data.evidence.assessment.reachable[0].entity_id = 'Light.Fout'; },
    (receipt) => {
      receipt.data.evidence.assessment.reachable[1].entity_id =
        receipt.data.evidence.assessment.reachable[0].entity_id;
    },
    (receipt) => {
      receipt.data.evidence.assessment.unavailable[0].entity_id =
        receipt.data.evidence.assessment.reachable[0].entity_id;
    },
    (receipt) => { receipt.data.evidence.assessment.unavailable[0].state = 'unknown'; },
    (receipt) => { receipt.data.evidence.assessment.unavailable = []; },
    (receipt) => { receipt.data.evidence.assessment.isGroup = false; },
    (receipt) => { receipt.data.evidence.assessment.members = []; },
    (receipt) => { receipt.data.evidence.assessment.members.pop(); },
    (receipt) => {
      receipt.data.evidence.assessment.members.push({
        entity_id: 'light.verzonnen', name: 'Verzonnen', state: 'off',
      });
    },
    (receipt) => { receipt.data.evidence.assessment.members[0].state = 'on'; },
    (receipt) => { receipt.data.evidence.assessment.unresolved = []; },
    (receipt) => { receipt.data.evidence.assessment.unresolved.pop(); },
    (receipt) => {
      receipt.data.evidence.assessment.unresolved.push({
        entity_id: 'light.keuken_1', name: 'Keukenlamp 1', state: 'unavailable',
      });
    },
    (receipt) => { receipt.data.evidence.assessment.unresolved[0].state = 'unknown'; },
  ];
  for (const mutate of misses) {
    const result = evaluateObservation(
      scenario.steps[0].expected,
      partialKitchenObservation('De keukenlampen zijn uit.', mutate),
    );
    assert.equal(result.ok, false, 'een niet geraakte partial-regressie mag niet groen zijn');
    assert.equal(result.exercise?.status, 'not_exercised');
    assert.match(
      result.exercise?.reason || '',
      /partial|unavailable|reachable|bereikbaar|groep|members|unresolved/iu,
    );
  }
});

test('vervolgvraag leest exact het dynamische keukendoel en antwoordt direct', async () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => (
    item.id === 'kitchen-light-partial-success-continuation'
  ));
  const first = partialKitchenObservation(
    'De bereikbare keukenlampen zijn uit. Twee lampen zijn offline.',
  );
  const current = {
    contract: {},
    actions: [{
      name: 'ha_lees', ok: true, verified: true, changed: false,
      args: { entity_id: 'light.keuken' },
      result: {
        contract: 'ActionResult/v1', tool: 'ha_lees', ok: true, verified: true,
        changed: false, partial: false, needs_followup: false, effect_uncertain: false,
        data: { entity_id: 'light.keuken', name: 'Keuken', state: 'off' },
      },
    }],
    reply: 'Ja, de bereikbare keukenlampen staan nog uit.',
  };
  assert.equal(evaluateObservation(scenario.steps[1].expected, current).ok, true);
  const negatedCurrent = structuredClone(current);
  negatedCurrent.reply = 'De bereikbare keukenlampen staan niet uit.';
  const negatedCurrentResult = evaluateObservation(
    scenario.steps[1].expected, negatedCurrent,
  );
  assert.equal(negatedCurrentResult.ok, false);
  assert.match(negatedCurrentResult.failures.join('\n'), /niet|negatie|uit-status/iu);
  let verifiedEntity = '';
  const live = await evaluateLiveChecks(scenario.steps[1].expected, current, {
    priorObservations: [first],
    homeStateVerifier: async (entityId, expectedState) => {
      verifiedEntity = entityId;
      return {
        ok: false, reachableVerified: true, partial: true,
        entity_id: entityId, state: expectedState,
      };
    },
  });
  assert.equal(live.ok, true, live.failures.join('\n'));
  assert.equal(verifiedEntity, 'light.keuken');

  const wrongEntity = structuredClone(current);
  wrongEntity.actions[0].result.data.entity_id = 'light.woonkamer';
  assert.equal((await evaluateLiveChecks(scenario.steps[1].expected, wrongEntity, {
    priorObservations: [first],
    homeStateVerifier: async (entityId, expectedState) => ({
      ok: true, entity_id: entityId, state: expectedState,
    }),
  })).ok, false);

  for (const args of [
    { entity_id: 'light.keuken', pad: '/api/states' },
    { entity_id: 'light.keuken', integratie: 'hue' },
    { entity_id: 'light.woonkamer' },
    { pad: '/api/states/light.keuken' },
  ]) {
    const broad = structuredClone(current);
    broad.actions[0].args = args;
    const checked = await evaluateLiveChecks(scenario.steps[1].expected, broad, {
      priorObservations: [first],
      homeStateVerifier: async (entityId, expectedState) => ({
        ok: true, entity_id: entityId, state: expectedState,
      }),
    });
    assert.equal(checked.ok, false, JSON.stringify(args));
    assert.match(checked.failures.join('\n'), /argument|entity_id|pad|integratie/iu);
  }
});

test('beoordelaar vindt contract-, effect- en fallbackfouten tegelijk', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => item.id === 'house-negation-explanation');
  const result = evaluateObservation(scenario.steps[0].expected, {
    contract: {
      kind: 'conversation',
      requiredTools: ['bedien_huis'],
      offeredTools: ['bedien_huis'],
    },
    actions: [{ name: 'bedien_huis', ok: true, verified: true, changed: true }],
    reply: 'Nee, dat heb ik nog niet gedaan.',
  });
  assert.equal(result.ok, false);
  assert.match(result.failures.join('\n'), /requiredTools/);
  assert.match(result.failures.join('\n'), /effectgrens/);
  assert.match(result.failures.join('\n'), /verboden patroon/);
});

test('negatiebeoordelaar accepteert de tegengestelde huisintentie niet', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => item.id === 'house-negation-explanation');
  const observation = {
    contract: { kind: 'conversation', requiredTools: [], offeredTools: [] },
    actions: [],
  };
  assert.equal(evaluateObservation(scenario.steps[0].expected, {
    ...observation,
    reply: 'De lamp moet niet aan blijven.',
  }).ok, false);
  assert.equal(evaluateObservation(scenario.steps[0].expected, {
    ...observation,
    reply: 'Ik begrijp dat de lamp gewoon moet blijven zoals hij is: hij moet gewoon aan blijven.',
  }).ok, true);
});

test('tijdlijnextractie bindt contract en acties aan dezelfde turn-id', () => {
  const meta = (value) => JSON.stringify(value);
  const rows = [
    { id: 10, kind: 'chat_in', content: 'Jaap: testprompt', meta: meta({ messageContext: { syntheticTest: true } }) },
    { id: 11, kind: 'turn_contract', content: 'conversation: antwoord', meta: meta({ turnId: 'turn-a', kind: 'conversation', requiredTools: [], offeredTools: ['huis_status'] }) },
    { id: 12, kind: 'action', content: 'huis_status: gelukt', meta: meta({ turnId: 'turn-a', tool: 'huis_status', ok: true, verified: true, changed: false }) },
    { id: 13, kind: 'action', content: 'bedien_huis: gelukt', meta: meta({ turnId: 'andere-turn', tool: 'bedien_huis', ok: true, verified: true, changed: true }) },
    { id: 14, kind: 'chat_out', content: 'Eva -> Jaap: Actuele uitleg.', meta: meta({ turnId: 'turn-a', syntheticTest: true }) },
  ];
  const observation = extractTurnObservation(rows, 'testprompt');
  assert.equal(observation.turnId, 'turn-a');
  assert.deepEqual(observation.actions.map((action) => action.name), ['huis_status']);
  assert.equal(observation.reply, 'Actuele uitleg.');
});

test('tijdlijnextractie bindt zonder turn_contract via de exacte HTTP-origin aan dezelfde beurt', () => {
  const meta = (value) => JSON.stringify(value);
  const rows = [
    {
      id: 20, kind: 'chat_in', content: 'Jaap: Doe even het licht aan in mijn slaapkamer.',
      meta: meta({ messageContext: { messageId: 'eva-live:request-a' } }),
    },
    {
      id: 21, kind: 'chat_in', content: 'Jaap: Doe even het licht aan in mijn slaapkamer.',
      meta: meta({ messageContext: { messageId: 'eva-live:request-b' } }),
    },
    {
      id: 22, kind: 'turn_understanding', content: 'command: verkeerde beurt',
      meta: meta({ turnId: 'turn-b', understanding: { dialogueAct: 'command' } }),
    },
    {
      id: 23, kind: 'action', content: 'bedien_huis: gelukt',
      meta: meta({ turnId: 'turn-b', tool: 'bedien_huis', ok: true, verified: true, changed: true }),
    },
    {
      id: 24, kind: 'chat_out', content: 'Eva -> Jaap: Verkeerde beurt.',
      meta: meta({ turnId: 'turn-b', originMessageId: 'eva-live:request-b' }),
    },
    {
      id: 25, kind: 'turn_understanding', content: 'command: juiste beurt',
      meta: meta({ turnId: 'turn-a', understanding: { dialogueAct: 'command' } }),
    },
    {
      id: 26, kind: 'action', content: 'bedien_huis: gelukt',
      meta: meta({
        turnId: 'turn-a', tool: 'bedien_huis', ok: true, verified: true, changed: true,
        output: JSON.stringify({ ok: true, verified: true, changed: true,
          data: { entity_id: 'light.dynamisch_doel', after: 'on' } }),
      }),
    },
    {
      id: 27, kind: 'chat_out', content: 'Eva -> Jaap: Ja, staat aan.',
      meta: meta({ turnId: 'turn-a', originMessageId: 'eva-live:request-a' }),
    },
  ];

  const observation = extractTurnObservation(
    rows, 'Doe even het licht aan in mijn slaapkamer.', '', 'eva-live:request-a',
  );
  assert.equal(observation.turnId, 'turn-a');
  assert.deepEqual(observation.actions.map((action) => action.name), ['bedien_huis']);
  assert.equal(observation.actions[0].result.data.entity_id, 'light.dynamisch_doel');
  assert.equal(observation.reply, 'Ja, staat aan.');
});

test('real-person extractie vervangt compacte tijdlijnoutput door het volledige geverifieerde executionrecord', () => {
  const requestId = 'canary:strict-live:a';
  const conversationId = 'web:household-live:a';
  const turnId = 'turn-live-a';
  const fullResult = partialKitchenObservation('bewijs').actions[0].result;
  const compactResult = {
    contract: 'ActionResult/v1', tool: 'bedien_huis', ok: true,
    verified: true, changed: true, partial: true,
    data: { entity_id: 'light.keuken', after: 'off' },
  };
  const meta = (value) => JSON.stringify(value);
  const rows = [
    {
      id: 30, kind: 'chat_in', content: 'Jaap: Zet het licht uit in de keuken.',
      meta: meta({ conversationId, messageContext: { messageId: requestId } }),
    },
    {
      id: 31, kind: 'turn_contract', content: 'action: zet uit',
      meta: meta({ turnId, kind: 'action', requiredTools: ['bedien_huis'] }),
    },
    {
      id: 32, kind: 'action', content: 'bedien_huis: gelukt',
      meta: meta({
        turnId, tool: 'bedien_huis', args: { doel: 'keuken', actie: 'uit' },
        ok: true, verified: true, changed: true, output: JSON.stringify(compactResult),
      }),
    },
    {
      id: 33, kind: 'chat_out', content: 'Eva -> Jaap: Begrensd gelukt.',
      meta: meta({ turnId, conversationId, originMessageId: requestId }),
    },
  ];
  const execution = verifiedExecutionRow({
    turnId,
    args: { doel: 'keuken', actie: 'uit' },
    output: fullResult,
  });
  const observation = extractTurnObservation(
    rows,
    'Zet het licht uit in de keuken.',
    'Begrensd gelukt.',
    requestId,
    {
      strictRealPerson: true,
      conversationId,
      apiBody: {
        request_id: requestId, conversation_id: conversationId, turn_id: turnId,
      },
      executionRows: [execution],
    },
  );
  assert.deepEqual(observation.integrityFailures, []);
  assert.equal(observation.turnId, turnId);
  assert.equal(observation.actions[0].executionKey, execution.execution_key);
  assert.equal(observation.actions[0].executionValidated, true);
  assert.equal(observation.actions[0].result.partial, true);
  assert.equal(
    observation.actions[0].result.data.evidence.assessment.reachableVerified,
    true,
    'de compacte timelineprojectie mag het volledige partial-bewijs niet weggooien',
  );

  const tampered = { ...execution, output_sha256: '0'.repeat(64) };
  const rejected = extractTurnObservation(
    rows, 'Zet het licht uit in de keuken.', 'Begrensd gelukt.', requestId,
    {
      strictRealPerson: true, conversationId,
      apiBody: {
        request_id: requestId, conversation_id: conversationId, turn_id: turnId,
      },
      executionRows: [tampered],
    },
  );
  assert.equal(rejected.actions[0].result, null);
  assert.match(rejected.integrityFailures.join('\n'), /execution|ledger|hash/iu);

  const extraResult = {
    contract: 'ActionResult/v1', tool: 'ha_lees', ok: true, verified: true,
    changed: false, partial: false, needs_followup: false, effect_uncertain: false,
    data: { entity_id: 'light.keuken', state: 'off' },
  };
  const hiddenExtra = verifiedExecutionRow({
    executionKey: 'execution:live-canary:hidden', executionSeq: 2,
    turnId, tool: 'ha_lees', args: { entity_id: 'light.keuken' }, output: extraResult,
  });
  const invalidExtra = {
    ...verifiedExecutionRow({
      executionKey: 'execution:live-canary:invalid', executionSeq: 3,
      turnId, tool: 'ha_lees', args: { entity_id: 'light.keuken' }, output: extraResult,
    }),
    output_sha256: '0'.repeat(64),
  };
  const nonterminalExtra = {
    ...verifiedExecutionRow({
      executionKey: 'execution:live-canary:running', executionSeq: 4,
      turnId, tool: 'ha_lees', args: { entity_id: 'light.keuken' }, output: extraResult,
    }),
    status: 'started',
    finished_ts: null,
  };
  const namespaceLookalikeExtra = verifiedExecutionRow({
    executionKey: 'execution:live-canary:turn-lookalike', executionSeq: 5,
    turnId, tool: '@turnevil', args: {},
    output: { ...extraResult, tool: '@turnevil' },
  });
  for (const [label, extra] of [
    ['verborgen extra execution', hiddenExtra],
    ['ongeldig extra execution', invalidExtra],
    ['niet-terminaal extra execution', nonterminalExtra],
    ['niet-gereserveerde namespace-lookalike', namespaceLookalikeExtra],
  ]) {
    const extraObservation = extractTurnObservation(
      rows, 'Zet het licht uit in de keuken.', 'Begrensd gelukt.', requestId,
      {
        strictRealPerson: true, conversationId,
        apiBody: {
          request_id: requestId, conversation_id: conversationId, turn_id: turnId,
        },
        executionRows: [execution, extra],
      },
    );
    assert.notDeepEqual(extraObservation.integrityFailures, [], label);
    assert.match(extraObservation.integrityFailures.join('\n'), /execution|ledger/iu, label);
  }

  const ignoredTurnBookkeeping = {
    execution_key: 'execution:live-canary:turn-bookkeeping',
    execution_seq: 6,
    execution_contract: 'kapot-mag-genegeerd-worden',
    turn_id: turnId,
    tool: '@turn/understanding',
  };
  const bookkeepingObservation = extractTurnObservation(
    rows, 'Zet het licht uit in de keuken.', 'Begrensd gelukt.', requestId,
    {
      strictRealPerson: true, conversationId,
      apiBody: {
        request_id: requestId, conversation_id: conversationId, turn_id: turnId,
      },
      executionRows: [execution, ignoredTurnBookkeeping],
    },
  );
  assert.deepEqual(bookkeepingObservation.integrityFailures, []);
});

test('real-person extractie faalt gesloten op interleaving, verkeerde origin en legacy-only correlatie', () => {
  const requestId = 'canary:strict-live:owned';
  const conversationId = 'web:household-live:strict';
  const meta = (value) => JSON.stringify(value);
  const baseInbound = {
    id: 40, kind: 'chat_in', content: 'Jaap: En, gelukt?',
    meta: meta({ conversationId, messageContext: { messageId: requestId } }),
  };
  const cases = [
    {
      label: 'verkeerde origin',
      rows: [
        baseInbound,
        { id: 41, kind: 'chat_in', content: 'Jaap: ander verzoek', meta: meta({
          conversationId, messageContext: { messageId: 'canary:strict-live:other' },
        }) },
        { id: 42, kind: 'turn_contract', content: 'conversation: legacy', meta: meta({
          turnId: 'turn-other', kind: 'conversation',
        }) },
        { id: 43, kind: 'chat_out', content: 'Eva -> Jaap: Verkeerd.', meta: meta({
          turnId: 'turn-other', conversationId,
          originMessageId: 'canary:strict-live:other',
        }) },
      ],
      apiBody: {},
    },
    {
      label: 'legacy-only',
      rows: [
        baseInbound,
        { id: 44, kind: 'turn_contract', content: 'conversation: legacy', meta: meta({
          turnId: 'turn-legacy', kind: 'conversation',
        }) },
        { id: 45, kind: 'chat_out', content: 'Eva -> Jaap: Legacy.', meta: meta({
          turnId: 'turn-legacy', conversationId,
        }) },
      ],
      apiBody: {},
    },
    {
      label: 'verkeerde API-binding',
      rows: [
        baseInbound,
        { id: 46, kind: 'turn_contract', content: 'conversation: exact', meta: meta({
          turnId: 'turn-exact', kind: 'conversation',
        }) },
        { id: 47, kind: 'chat_out', content: 'Eva -> Jaap: Exact.', meta: meta({
          turnId: 'turn-exact', conversationId, originMessageId: requestId,
        }) },
      ],
      apiBody: {
        request_id: 'canary:strict-live:other',
        conversation_id: conversationId,
        turn_id: 'turn-exact',
      },
    },
  ];
  for (const fixture of cases) {
    const observation = extractTurnObservation(
      fixture.rows, 'En, gelukt?', 'antwoord', requestId,
      {
        strictRealPerson: true, conversationId,
        apiBody: fixture.apiBody, executionRows: [],
      },
    );
    assert.notDeepEqual(observation.integrityFailures, [], fixture.label);
    assert.match(
      observation.integrityFailures.join('\n'),
      /chat_out|origin|request|turn|correlatie/iu,
      fixture.label,
    );
  }
});

test('real-person extractie vereist alle drie API-correlatievelden aanwezig en exact', () => {
  const requestId = 'canary:strict-live:api-fields';
  const conversationId = 'web:household-live:api-fields';
  const turnId = 'turn-api-fields';
  const meta = (value) => JSON.stringify(value);
  const rows = [
    {
      id: 50, kind: 'chat_in', content: 'Jaap: En, gelukt?',
      meta: meta({ conversationId, messageContext: { messageId: requestId } }),
    },
    {
      id: 51, kind: 'turn_contract', content: 'conversation: exact',
      meta: meta({ turnId, kind: 'conversation' }),
    },
    {
      id: 52, kind: 'chat_out', content: 'Eva -> Jaap: Ja.',
      meta: meta({ turnId, conversationId, originMessageId: requestId }),
    },
  ];
  const completeBody = {
    request_id: requestId, conversation_id: conversationId, turn_id: turnId,
  };
  for (const field of ['request_id', 'conversation_id', 'turn_id']) {
    const apiBody = { ...completeBody };
    delete apiBody[field];
    const observation = extractTurnObservation(
      rows, 'En, gelukt?', 'Ja.', requestId,
      {
        strictRealPerson: true, conversationId, apiBody, executionRows: [],
      },
    );
    assert.match(
      observation.integrityFailures.join('\n'),
      new RegExp(`API-${field}.*ontbreekt`, 'iu'),
      field,
    );
  }
});

test('echte persoonscontext gebruikt een normale gesprek-id en laat de standaardcanary ongemoeid', () => {
  const runId = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
  assert.equal(
    scenarioConversationId({}, runId, 2),
    `web:canary:${runId}:2`,
  );
  const liveId = scenarioConversationId({ conversationContext: 'real_person' }, runId, 2);
  assert.equal(liveId, `web:household-live:${runId}:2`);
  assert.doesNotMatch(liveId, /(?:^|[:/_-])(?:test|synthetic|blackbox|canary|persona-eval)(?:$|[:/_-])/iu);
});

test('actie-uitkomstcriteria bewijzen de requesterbinding zonder vaste entity-id', () => {
  const expected = {
    contract: {},
    calls: { includes: ['bedien_huis'], verified: ['bedien_huis'] },
    effects: { mode: 'allowlist', allowedTools: ['bedien_huis'] },
    actionResult: {
      tool: 'bedien_huis',
      equals: {
        'data.target_binding.contract': 'HomeRequesterTargetBinding/v1',
        'data.target_binding.requested_target': 'mijn slaapkamer',
        'data.target_binding.method': 'person_memory',
        'data.after': 'on',
      },
      equalsObservation: [{ path: 'data.target_binding.requester', observationPath: 'requester' }],
      samePaths: [['data.target_binding.resolved_entity_id', 'data.entity_id']],
      nonEmpty: ['data.target_binding.source_memory_ids', 'data.target_binding.alias_evidence'],
      matches: { 'data.target_binding.binding_sha256': '^[a-f0-9]{64}$' },
    },
  };
  const result = {
    ok: true, verified: true, changed: true,
    data: {
      entity_id: 'light.dynamisch_doel', after: 'on',
      target_binding: {
        contract: 'HomeRequesterTargetBinding/v1', requester: 'Jaap',
        requested_target: 'mijn slaapkamer', method: 'person_memory',
        resolved_entity_id: 'light.dynamisch_doel', source_memory_ids: [3803],
        alias_evidence: ['elements'], binding_sha256: 'a'.repeat(64),
      },
    },
  };
  const observation = {
    requester: 'Jaap', contract: { kind: '', requiredTools: [], offeredTools: [] },
    actions: [{ name: 'bedien_huis', ok: true, verified: true, changed: true, result }],
    reply: 'Ja, staat aan.',
  };
  assert.equal(evaluateObservation(expected, observation).ok, true);

  const wrong = structuredClone(observation);
  wrong.actions[0].result.data.target_binding.resolved_entity_id = 'light.andere_kamer';
  const rejected = evaluateObservation(expected, wrong);
  assert.equal(rejected.ok, false);
  assert.match(rejected.failures.join('\n'), /resolved_entity_id.*data\.entity_id/iu);
});

test('verse huisreadback gebruikt de dynamische action-entiteit en eist volledige on-status', async () => {
  const observation = {
    actions: [{
      name: 'bedien_huis',
      result: { data: { entity_id: 'light.dynamisch_doel' } },
    }],
  };
  let readEntity = '';
  const passed = await evaluateLiveChecks({
    homeReadback: {
      actionTool: 'bedien_huis', entityPath: 'data.entity_id',
      expectedState: 'on', source: 'current', requireComplete: true,
    },
  }, observation, {
    homeStateVerifier: async (entityId, expectedState) => {
      readEntity = entityId;
      return { ok: true, entity_id: entityId, state: expectedState };
    },
  });
  assert.equal(passed.ok, true);
  assert.equal(readEntity, 'light.dynamisch_doel');

  const partial = await evaluateLiveChecks({
    homeReadback: {
      actionTool: 'bedien_huis', entityPath: 'data.entity_id',
      expectedState: 'on', source: 'current', requireComplete: true,
    },
  }, observation, {
    homeStateVerifier: async (entityId) => ({
      ok: false, reachableVerified: true, entity_id: entityId, state: 'on',
    }),
  });
  assert.equal(partial.ok, false);
  assert.match(partial.failures.join('\n'), /niet volledig/iu);

  const exactContinuation = await evaluateLiveChecks({
    homeReadback: {
      actionTool: 'bedien_huis', entityPath: 'data.entity_id',
      currentActionTool: 'ha_lees', currentEntityPath: 'data.entity_id',
      expectedState: 'on', source: 'scenario', requireComplete: true,
    },
  }, {
    actions: [{ name: 'ha_lees', result: { data: { entity_id: 'light.dynamisch_doel' } } }],
  }, {
    priorObservations: [observation],
    homeStateVerifier: async (entityId) => ({ ok: true, entity_id: entityId, state: 'on' }),
  });
  assert.equal(exactContinuation.ok, true);
  const wrongContinuation = await evaluateLiveChecks({
    homeReadback: {
      actionTool: 'bedien_huis', entityPath: 'data.entity_id',
      currentActionTool: 'ha_lees', currentEntityPath: 'data.entity_id',
      expectedState: 'on', source: 'scenario', requireComplete: true,
    },
  }, {
    actions: [{ name: 'ha_lees', result: { data: { entity_id: 'light.andere_kamer' } } }],
  }, {
    priorObservations: [observation],
    homeStateVerifier: async (entityId) => ({ ok: true, entity_id: entityId, state: 'on' }),
  });
  assert.equal(wrongContinuation.ok, false);
  assert.match(wrongContinuation.failures.join('\n'), /eerder gekozen entity-id/iu);
});

test('slaapkamer-liveproef is eindig en stuurt correcties alleen na de vorige mislukking', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => item.id === 'bedroom-light-natural-recovery');
  assert.ok(scenario);
  assert.equal(scenario.conversationContext, 'real_person');
  assert.deepEqual(scenario.steps.map((step) => step.text), [
    'Doe even het licht aan in mijn slaapkamer.',
    'Dat vroeg ik toch niet?',
    'Je hebt het nog steeds niet gedaan.',
    'En, gelukt?',
  ]);
  assert.equal(stepShouldRun(scenario.steps[1], { ok: true }), false);
  assert.equal(stepShouldRun(scenario.steps[1], { ok: false }), true);
  assert.equal(stepShouldRun(scenario.steps[3], { ok: true }), true);
  assert.doesNotMatch(JSON.stringify(scenario), /light\.mb|light\.slaapkamer_lola/iu);

  const alreadyOn = evaluateObservation(scenario.steps[0].expected, {
    requester: 'Jaap',
    contract: { kind: '', requiredTools: [], offeredTools: [] },
    actions: [{
      name: 'bedien_huis', ok: true, verified: true, changed: false,
      result: {
        ok: true, verified: true, changed: false,
        data: {
          verified: true, entity_id: 'light.dynamisch_doel', after: 'on',
          target_binding: {
            contract: 'HomeRequesterTargetBinding/v1', requester: 'Jaap',
            requested_target: 'mijn slaapkamer', method: 'person_memory',
            resolved_entity_id: 'light.dynamisch_doel', source_memory_ids: [3856],
            alias_evidence: ['elements'], binding_sha256: 'a'.repeat(64),
          },
        },
      },
    }],
    reply: 'Je slaapkamerlicht stond al aan.',
  });
  assert.deepEqual(alreadyOn, { ok: true, failures: [] });
  assert.equal(evaluateObservation(scenario.steps[3].expected, {
    requester: 'Jaap',
    contract: { kind: '', requiredTools: [], offeredTools: [] },
    actions: [],
    reply: 'Het slaapkamerlicht stond al aan; daar hoefde ik niets meer aan te doen.',
  }).ok, false, 'een positieve tekst zonder de exacte live read is geen acceptatiebewijs');
});

test('runner correleert health, echte idempotente API-call en SQLite-tijdlijn end-to-end', async (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-canary-runner-'));
  const dbPath = path.join(directory, 'eva.db');
  const db = new Database(dbPath);
  db.exec(`CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT
  );
  CREATE TABLE turn_tool_executions (
    execution_key TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL,
    started_ts TEXT NOT NULL,
    finished_ts TEXT,
    output TEXT,
    semantic_hash TEXT,
    semantic_binding TEXT,
    intent_record_sha256 TEXT,
    output_sha256 TEXT,
    material_sha256 TEXT,
    completion_record_sha256 TEXT,
    execution_seq INTEGER NOT NULL,
    execution_contract TEXT NOT NULL,
    execution_verification TEXT NOT NULL
  )`);
  const insert = db.prepare('INSERT INTO timeline (ts,kind,content,meta) VALUES (?,?,?,?)');
  const insertExecution = db.prepare(`INSERT INTO turn_tool_executions (
    execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
    semantic_hash,semantic_binding,intent_record_sha256,output_sha256,
    material_sha256,completion_record_sha256,execution_seq,execution_contract,
    execution_verification
  ) VALUES (
    @execution_key,@turn_id,@tool,@args,@status,@started_ts,@finished_ts,@output,
    @semantic_hash,@semantic_binding,@intent_record_sha256,@output_sha256,
    @material_sha256,@completion_record_sha256,@execution_seq,@execution_contract,
    @execution_verification
  )`);
  const scenario = {
    id: 'mock-read-only-canary',
    domain: 'mock',
    description: 'mock',
    risk: 'read_only',
    transport: 'api_chat',
    steps: [{
      text: 'Geef een veilige uitleg.',
      expected: {
        contract: { kinds: ['conversation'], requiredTools: { exact: [] } },
        offeredTools: { excludes: ['bedien_huis'] },
        calls: { max: 0 },
        effects: { mode: 'none', allowedTools: [] },
        reply: { minChars: 8, mustMatch: [{ description: 'geeft uitleg', pattern: 'uitleg' }] },
      },
    }],
  };
  const server = http.createServer((request, response) => {
    if (request.url === '/api/health') {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        gezond: true,
        runtimeVersion: { code_matches_disk: true, pid: 123 },
      }));
      return;
    }
    if (request.url === '/api/http-delivery/ack' && request.method === 'POST') {
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => {
        const body = JSON.parse(raw);
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({
          delivered: true,
          request_ref: body.requestRef,
          delivery_ref: body.deliveryRef,
        }));
      });
      return;
    }
    if (request.url !== '/api/chat' || request.method !== 'POST') {
      response.writeHead(404).end();
      return;
    }
    let raw = '';
    request.on('data', (chunk) => { raw += chunk; });
    request.on('end', () => {
      const body = JSON.parse(raw);
      assert.equal(body.synthetic, undefined);
      assert.match(body.request_id, /^canary:[A-Za-z0-9._:-]+$/u);
      assert.equal(request.headers['idempotency-key'], body.request_id);
      const now = new Date().toISOString();
      const strictLedgerRead = body.text === 'Lees de mockentiteit exact uit.';
      const turnId = strictLedgerRead ? 'mock-turn-strict-ledger' : 'mock-turn';
      const reply = strictLedgerRead
        ? 'De bereikbare mocklamp staat uit.'
        : 'Hier is de veilige uitleg.';
      insert.run(now, 'chat_in', `Jaap: ${body.text}`, JSON.stringify({
        conversationId: body.conversation_id,
        messageContext: { syntheticTest: false, messageId: body.request_id },
      }));
      insert.run(now, 'turn_contract', 'conversation: direct antwoord', JSON.stringify({
        turnId, kind: 'conversation',
        requiredTools: strictLedgerRead ? ['ha_lees'] : [],
        offeredTools: strictLedgerRead ? ['ha_lees'] : ['huis_status'],
      }));
      if (strictLedgerRead) {
        const args = { entity_id: 'light.mock_dynamisch' };
        const full = {
          contract: 'ActionResult/v1', tool: 'ha_lees', ok: true, verified: true,
          changed: false, partial: false, needs_followup: false, effect_uncertain: false,
          data: { entity_id: 'light.mock_dynamisch', state: 'off' },
        };
        insertExecution.run(verifiedExecutionRow({
          executionKey: 'execution:mock:strict-ledger', executionSeq: 1,
          turnId, tool: 'ha_lees', args, output: full,
        }));
        insert.run(now, 'action', 'ha_lees: gelukt', JSON.stringify({
          turnId, tool: 'ha_lees', args, ok: true, verified: true, changed: false,
          output: JSON.stringify({
            contract: 'ActionResult/v1', tool: 'ha_lees', ok: true,
            verified: true, changed: false,
            data: { entity_id: 'light.mock_dynamisch' },
          }),
        }));
      }
      insert.run(now, 'chat_out', `Eva -> Jaap: ${reply}`, JSON.stringify({
        turnId, conversationId: body.conversation_id,
        syntheticTest: false, originMessageId: body.request_id,
      }));
      response.writeHead(200, {
        'content-type': 'application/json; charset=utf-8',
        'x-eva-http-write-attempt-ref': `http-write-attempt-v3:${'e'.repeat(64)}`,
        'x-eva-http-write-generation': '1',
      });
      response.end(JSON.stringify({
        reply,
        request_id: body.request_id,
        conversation_id: body.conversation_id,
        turn_id: turnId,
        _httpDelivery: {
          requestRef: `http-ingress:${'b'.repeat(64)}`,
          responseRef: `http-response:${'a'.repeat(64)}`,
          responseSha256: 'a'.repeat(64),
          deliveryRef: `http-delivery-v3:${'c'.repeat(64)}`,
          challenge: 'd'.repeat(64),
        },
      }));
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  context.after(() => {
    server.close();
    db.close();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const address = server.address();
  const options = parseCliArgs([
    '--base-url', `http://127.0.0.1:${address.port}`,
    '--db', dbPath,
    '--scenario', scenario.id,
  ]);
  const report = await runCanaries(options, [scenario]);
  assert.deepEqual(report.summary, {
    passed: 1, failed: 0, errors: 0, skipped: 0, not_exercised: 0,
  });
  assert.equal(report.results[0].steps[0].observation.turnId, 'mock-turn');
  assert.equal(report.results[0].steps[0].synthetic, false);

  const notExercisedScenario = {
    ...scenario,
    id: 'mock-partial-branch-not-exercised',
    steps: [{
      text: 'Geef nog een veilige uitleg.',
      expected: {
        ...scenario.steps[0].expected,
        partialExercise: {
          actionTool: 'bedien_huis', assessmentPath: 'data.evidence.assessment',
        },
      },
    }],
  };
  const notExercisedOptions = parseCliArgs([
    '--base-url', `http://127.0.0.1:${address.port}`,
    '--db', dbPath,
    '--scenario', notExercisedScenario.id,
  ]);
  const notExercisedReport = await runCanaries(
    notExercisedOptions, [notExercisedScenario],
  );
  assert.equal(notExercisedReport.results[0].status, 'not_exercised');
  assert.equal(notExercisedReport.results[0].steps[0].status, 'not_exercised');
  assert.match(notExercisedReport.results[0].reason, /partial branch niet geraakt/iu);
  assert.deepEqual(notExercisedReport.summary, {
    passed: 0, failed: 0, errors: 0, skipped: 0, not_exercised: 1,
  });

  const strictLedgerScenario = {
    id: 'mock-strict-ledger-canary',
    domain: 'mock',
    description: 'mock strict full ledger',
    risk: 'read_only',
    transport: 'api_chat',
    conversationContext: 'real_person',
    steps: [{
      text: 'Lees de mockentiteit exact uit.',
      expected: {
        contract: {},
        calls: { exact: ['ha_lees'], verified: ['ha_lees'], max: 1 },
        effects: { mode: 'none', allowedTools: [] },
        actionResult: {
          tool: 'ha_lees',
          equals: {
            ok: true, verified: true, changed: false, effect_uncertain: false,
            'data.entity_id': 'light.mock_dynamisch', 'data.state': 'off',
          },
        },
        reply: { mustMatch: [{ description: 'direct resultaat', pattern: 'bereikbare.+uit' }] },
      },
    }],
  };
  const strictLedgerOptions = parseCliArgs([
    '--base-url', `http://127.0.0.1:${address.port}`,
    '--db', dbPath,
    '--scenario', strictLedgerScenario.id,
  ]);
  const strictLedgerReport = await runCanaries(strictLedgerOptions, [strictLedgerScenario]);
  assert.deepEqual(strictLedgerReport.summary, {
    passed: 1, failed: 0, errors: 0, skipped: 0, not_exercised: 0,
  });
  const strictAction = strictLedgerReport.results[0].steps[0].observation.actions[0];
  assert.equal(strictAction.executionKey, 'execution:mock:strict-ledger');
  assert.equal(strictAction.executionValidated, true);
  assert.equal(strictAction.result.data.state, 'off');
});
