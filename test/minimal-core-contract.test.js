import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-core-contract-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_TEST_SECRET = 'must-not-leak';
process.env.HA_TOKEN = 'test-ha-token-that-must-never-be-persisted';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'bounded';
process.env.OPENAI_API_KEY = 'test-only';
// Deze suite test ook de expliciet ingeschakelde oude autonome bouwcyclus.
// De productie-default wordt apart fail-closed getest.
process.env.EVA_AUTONOMY_CODE_EXECUTION = 'true';

await import('./voicebridge-test-composition.js');
await import('./agent-test-composition.js');

const { __projectRouteTest, handleMessageDetailed } = await import('../src/mind/agent.js');
const { config } = await import('../src/config.js');
const {
  __autonomyScheduleTest,
  hasNightUrgentSignal,
  shouldQueueWorldSignal,
  shouldWakeForWorldSignal,
} = await import('../src/mind/autonomy.js');
const { runExecutionDriver } = await import('../src/brain/llm.js');
const {
  __toolsTest,
  capabilityCatalog,
  executeTool,
  toolsForTurn,
} = await import('../src/mind/tools.js');
const { understandTurn } = await import('../src/mind/understanding.js');
const {
  boundedRuntimeMs,
  canonicalJson,
  executionActionResult,
  normalizeActionResultState,
} = await import('../src/util.js');
const { completedArtifact } = await import('../src/mind/projectexecution.js');
const { bus } = await import('../src/bus.js');
const {
  normalizeVoiceConversationId,
  startVoiceBridge,
  __voiceBridgeTest,
} = await import('../src/mind/voicebridge.js');
const {
  homeEffectClaimFeedback,
  homeReadbackProvesIntent,
} = await import('../src/mind/communication.js');
const { registerSensitiveValues } = await import('../src/security/redaction.js');
const {
  archiveTelegramMessage,
  backupData,
  claimTurnToolExecution,
  completeTurnToolExecution,
  recordTurnToolObservation,
  db,
  deriveVoiceRetryIntent,
  logTimeline,
  latestDailyBackup,
  telegramOutboundByDedupeKey,
} = await import('../src/db.js');
const {
  claimWorkItem,
  createWorkItem,
  listWorkItems,
  recoverWorkLeases,
  releaseWorkLease,
  updateWorkItem,
} = await import('../src/mind/work.js');
const {
  renderCameraNotice,
  renderReminderText,
} = await import('../src/mind/persona.js');

function withoutExecutionTrigger(name, operation) {
  const trigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name);
  assert.ok(trigger?.sql, `missing execution trigger ${name}`);
  db.exec(`DROP TRIGGER main.${name}`);
  try { return operation(); } finally { db.exec(trigger.sql); }
}

function tool({
  name,
  ok = true,
  verified = true,
  changed = false,
  partial = false,
  needsFollowup = false,
  args = {},
  data = null,
} = {}) {
  const resultData = data ?? (name === 'voer_uit' ? { exit_code: 0 } : null);
  return {
    name,
    args,
    ok,
    verified,
    changed,
    partial,
    needs_followup: needsFollowup,
    output: JSON.stringify({
      contract: 'ActionResult/v1',
      ok,
      verified,
      changed,
      data: resultData,
    }),
  };
}

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200, headers: { 'content-type': 'text/event-stream' },
  });
}

function plannedExecution({
  durable = false,
  requiredTools = [],
  turnKind = null,
  actionRequested = false,
} = {}) {
  return {
    durable,
    plannedRequiredTools: requiredTools,
    plannedTurnKind: turnKind,
    plannedActionRequested: actionRequested,
  };
}

function requirementSatisfied(name, tools, text, options = {}) {
  return __projectRouteTest.turnRequirementSatisfied(name, tools, text, options);
}

async function homeControlUnderstanding() {
  const text = 'Zet light.testlamp aan.';
  const result = await understandTurn({
    turnId: 'semantic-stage-test', text, channel: 'telegram', origin: 'user',
  }, {
    capabilityCatalog: capabilityCatalog(),
    clock: () => new Date('2026-08-07T12:00:00.000Z'),
    complete: async () => ({
      toolCalls: [{
        name: 'commit_turn_understanding',
        arguments: JSON.stringify({
          dialogueAct: 'command',
          goal: {
            statement: 'Zet de testlamp aan.',
            outcome: 'De testlamp staat aantoonbaar aan.',
            beneficiary: 'requester',
          },
          conditions: [],
          provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
          confidence: 0.99,
          actions: [{
            capabilityId: 'home.device.control', operationId: 'set', polarity: 'request',
            semanticInput: { target: 'light.testlamp', desiredState: 'on' },
            sourceSpans: [{ start: 0, end: text.length }],
          }],
          requestedEffects: ['home'],
          deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] },
          durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' },
          proofNeeds: ['accepted_result'],
          ambiguities: [],
          firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
        }),
      }],
    }),
  });
  assert.equal(result.ok, true);
  return { result, text };
}

function resignedUnderstanding(understanding, mutate) {
  const changed = structuredClone(understanding);
  mutate(changed);
  const { sha256: _claimed, ...body } = changed;
  changed.sha256 = createHash('sha256').update(canonicalJson(body)).digest('hex');
  return changed;
}

test('semantische foutmeldingen geven de werkelijke oorzaak en beschuldigen de vraag niet', () => {
  const timeout = __projectRouteTest.semanticFailureReply('interpreter_timeout');
  const unavailable = __projectRouteTest.semanticFailureReply('interpreter_unavailable');
  const authority = __projectRouteTest.semanticFailureReply('capability_authority_denied');
  const malformed = __projectRouteTest.semanticFailureReply('interpreter_malformed');
  assert.match(timeout, /oorspronkelijke opdracht nog.*vast.*niks uitgevoerd of gewijzigd/iu);
  assert.match(unavailable, /oorspronkelijke opdracht nog.*niks uitgevoerd of gewijzigd/iu);
  assert.match(authority, /oorspronkelijke opdracht nog.*snap.*niet uitvoeren.*niks gewijzigd/iu);
  assert.match(malformed, /oorspronkelijke opdracht nog.*betrouwbare grip.*niks uitgevoerd of gewijzigd/iu);
  for (const reply of [timeout, unavailable, authority, malformed]) {
    assert.doesNotMatch(
      reply,
      /herhaal|herformuleer|formuleer|korter|zeg.*nog.*keer|taalmodel|modelroute|onderdeel|provider/iu,
    );
  }
});

test('begrip, binding en bevoegdheid houden ieder hun eigen foutstadium', async () => {
  const { result, text } = await homeControlUnderstanding();
  const stateOptions = {
    person: 'Jaap', userText: text, conversationId: 'semantic-stage-test', mode: 'chat',
  };

  const understanding = __projectRouteTest.semanticTurnState({
    ...stateOptions,
    result: { ok: false, failure: { code: 'interpreter_timeout' }, understanding: null },
    messageContext: {},
  });
  assert.deepEqual({
    stage: understanding.failureStage,
    understood: understanding.interpretationSucceeded,
    metadata: __projectRouteTest.semanticFailureResultMetadata(understanding),
  }, {
    stage: 'understanding',
    understood: false,
    metadata: {
      understood: false, interpretationFailed: true, failureStage: 'understanding',
    },
  });

  const invalidBinding = resignedUnderstanding(result.understanding, (changed) => {
    changed.actions[0].semanticInput.target = 42;
  });
  const binding = __projectRouteTest.semanticTurnState({
    ...stateOptions,
    result: { ...result, understanding: invalidBinding },
    messageContext: {},
  });
  assert.equal(binding.failureStage, 'binding');
  assert.equal(binding.interpretationSucceeded, true);
  assert.match(binding.turnContract.outcome, /geldige beurtinterpretatie/u);
  assert.deepEqual(
    __projectRouteTest.semanticFailureResultMetadata(binding),
    {
      understood: true,
      interpretationFailed: false,
      failureStage: 'binding',
      planningError: binding.error,
    },
  );

  const authority = __projectRouteTest.semanticTurnState({
    ...stateOptions, result, messageContext: { syntheticTest: true },
  });
  assert.equal(authority.failureStage, 'authority');
  assert.equal(authority.interpretationSucceeded, true);
  assert.deepEqual(
    __projectRouteTest.semanticFailureResultMetadata(authority),
    {
      understood: true,
      interpretationFailed: false,
      failureStage: 'authority',
      planningError: 'capability_authority_denied',
    },
  );
});

test('semanticTurnState classificeert monitoring niet opnieuw uit afwijkende vrije tekst', async () => {
  const { result } = await homeControlUnderstanding();
  const semantic = __projectRouteTest.semanticTurnState({
    result,
    person: 'Jaap',
    userText: 'Blijf dit twee uur volgen en app me alleen als ik moet bijstellen.',
    conversationId: 'no-late-monitoring-classifier',
    messageContext: {},
    mode: 'chat',
  });

  assert.equal(semantic.ok, true);
  assert.equal(semantic.turnContract.kind, 'action');
  assert.equal(semantic.turnContract.monitoring, undefined);
  assert.equal(semantic.understanding.monitoring, null);
});

test('continue en correct delen één canonieke vervolgact', async () => {
  const { result, text } = await homeControlUnderstanding();
  for (const dialogueAct of ['continue', 'correct']) {
    const understanding = resignedUnderstanding(result.understanding, (changed) => {
      changed.dialogueAct = dialogueAct;
    });
    const semantic = __projectRouteTest.semanticTurnState({
      result: { ...result, understanding }, person: 'Jaap', userText: text,
      conversationId: `canonical-${dialogueAct}`, messageContext: {}, mode: 'chat',
    });
    assert.equal(semantic.continuationRequest, true, dialogueAct);
  }
  const command = __projectRouteTest.semanticTurnState({
    result, person: 'Jaap', userText: text, conversationId: 'canonical-command',
    messageContext: {}, mode: 'chat',
  });
  assert.equal(command.continuationRequest, false);
});

test('een geldige interpretatie en de latere planningsfout krijgen aparte tijdlijnfeiten', async () => {
  const { result, text } = await homeControlUnderstanding();
  const semanticTurn = __projectRouteTest.semanticTurnState({
    result,
    person: 'Jaap',
    userText: text,
    conversationId: 'semantic-timeline-test',
    messageContext: { syntheticTest: true },
    mode: 'chat',
  });
  const timeline = __projectRouteTest.recordSemanticTurnTimeline({
    semanticTurn,
    semanticResult: result,
    turnId: 'semantic-timeline-test',
    conversationId: 'semantic-timeline-test',
  });
  assert.equal(timeline.understanding.kind, 'turn_understanding');
  assert.equal(timeline.understanding.meta.interpretationSucceeded, true);
  assert.equal(timeline.understanding.meta.error, null);
  assert.equal(timeline.understanding.meta.understanding.sourceSha256, result.understanding.sha256);
  assert.equal(timeline.planning.kind, 'turn_planning');
  assert.equal(timeline.planning.meta.failureStage, 'authority');
  assert.equal(timeline.planning.meta.error, 'capability_authority_denied');
});

test('finishTurn bewaart de semantische foutmetadata in plaats van die stil weg te gooien', () => {
  const retained = __projectRouteTest.retainedTurnResultMetadata({
    understood: true,
    interpretationFailed: false,
    failureStage: 'binding',
    planningError: 'capability_binding:invalid_semantic_input',
    preserveAutonomyReply: true,
  });
  assert.deepEqual(retained, {
    understood: true,
    interpretationFailed: false,
    failureStage: 'binding',
    planningError: 'capability_binding:invalid_semantic_input',
  });
});

test('een host-poginglimiet blijft een volledig getypeerde ActionResult', () => {
  const result = JSON.parse(__projectRouteTest.rejectedToolOutput(
    'voer_uit', 'poginglimiet bereikt', 'attempt_limit',
  ));
  assert.deepEqual({
    contract: result.contract,
    tool: result.tool,
    ok: result.ok,
    verified: result.verified,
    partial: result.partial,
    failure_class: result.failure_class,
    retryable: result.retryable,
    effect_uncertain: result.effect_uncertain,
  }, {
    contract: 'ActionResult/v1',
    tool: 'voer_uit',
    ok: false,
    verified: false,
    partial: false,
    failure_class: 'attempt_limit',
    retryable: false,
    effect_uncertain: false,
  });
});

test('de inspectielaag bewaart fouttype, retrybaarheid en effectonzekerheid exact', () => {
  const typedFailure = JSON.stringify({
    contract: 'ActionResult/v1',
    tool: 'beheer_agentmail',
    ok: false,
    changed: false,
    verified: false,
    needs_followup: true,
    partial: false,
    failure_class: 'delivery_unconfirmed',
    retryable: false,
    effect_uncertain: true,
  });
  const { recorded } = __projectRouteTest.inspectToolOutcome(
    'beheer_agentmail', { actie: 'versturen' }, typedFailure,
  );
  assert.deepEqual({
    failure_class: recorded.failure_class,
    retryable: recorded.retryable,
    effect_uncertain: recorded.effect_uncertain,
  }, {
    failure_class: 'delivery_unconfirmed',
    retryable: false,
    effect_uncertain: true,
  });

  const untyped = __projectRouteTest.inspectToolOutcome(
    'zoek_internet', { vraag: 'bron' }, JSON.stringify({ ok: false }),
  ).recorded;
  assert.equal(untyped.retryable, null);
  assert.equal(untyped.effect_uncertain, null);
});


test('alleen een ondubbelzinnige enkelvoudige huisopdracht gebruikt de snelle kernroute', () => {
  const parse = __projectRouteTest.directHomeCommand;
  assert.deepEqual(parse('Zet light.unifi_schuur_led aan.'), {
    doel: 'light.unifi_schuur_led',
    actie: 'aan',
  });
  assert.deepEqual(parse('Zet die schuur-led weer uit.'), {
    doel: 'schuur-led',
    actie: 'uit',
  });
  assert.deepEqual(parse('Sluit het woonkamerrolluik.'), {
    doel: 'woonkamerrolluik',
    actie: 'dicht',
  });
  assert.equal(parse('Zet keuken en woonkamer uit.'), null);
  assert.equal(parse('Zet alle verlichting uit.'), null);
  assert.equal(parse('Zet niet de keukenlamp maar de ganglamp uit.'), null);
  assert.equal(parse('Zet de lamp niet uit.'), null);
  assert.deepEqual(parse('Kun je het licht buiten aanzetten?'), {
    doel: 'licht buiten',
    actie: 'aan',
  });
  assert.deepEqual(parse('Schakel de TV uit in de overkapping.'), {
    doel: 'TV in overkapping',
    actie: 'uit',
  });
});

test('huisdoelen kiezen een exacte slug en handelen nooit op een friendly-name-gelijkspel', () => {
  const resolve = __toolsTest.resolveHomeEntity;
  const states = [
    { entity_id: 'media_player.tv_overkapping_2', state: 'idle', attributes: { friendly_name: 'Tv overkapping' } },
    { entity_id: 'media_player.googletv1069_2', state: 'idle', attributes: { friendly_name: 'Tv overkapping' } },
    { entity_id: 'media_player.tv_overkapping', state: 'off', attributes: { friendly_name: 'Tv overkapping' } },
  ];
  assert.equal(resolve(states, 'tv in de overkapping').state.entity_id, 'media_player.tv_overkapping');
  const tied = resolve(states.slice(0, 2), 'Tv overkapping');
  assert.equal(tied.state, null);
  assert.equal(tied.ambiguous, true);
});

test('een concrete huisopdracht blijft binnen bediening en één gerichte uitleesroute', () => {
  const results = [
    tool({
      name: 'bedien_huis', ok: false, verified: false,
      args: { doel: 'tv in de overkapping', actie: 'uit' },
      data: { entity_id: 'media_player.tv_overkapping' },
    }),
    tool({
      name: 'ha_lees',
      data: { entity_id: 'media_player.tv_overkapping', name: 'Tv overkapping', state: 'off' },
    }),
  ];
  assert.equal(
    __projectRouteTest.verifiedHomeReadback('Schakel de TV uit in de overkapping.', results)?.name,
    'ha_lees',
  );
  const wrongDuplicate = [
    tool({
      name: 'bedien_huis', ok: false, verified: false,
      data: { entity_id: 'switch.heater_overkapping' },
    }),
    tool({
      name: 'ha_lees',
      data: { entity_id: 'switch.heater_overkapping_guest', name: 'Heater overkapping', state: 'off' },
    }),
  ];
  assert.equal(
    __projectRouteTest.verifiedHomeReadback('Zet de heater uit in de overkapping.', wrongDuplicate),
    null,
  );
  assert.equal(
    __projectRouteTest.verifiedHomeReadback('Zet de heater uit in de overkapping.', [
      tool({ name: 'bedien_huis', ok: false, verified: false }),
      wrongDuplicate[1],
    ]),
    null,
  );
});

test('een voorbereide autonome terugmelding bewaart exact tekst, ontvanger en retry-identiteit', () => {
  assert.deepEqual(__projectRouteTest.pendingDeliveryForWork({
    state_json: JSON.stringify({ pending_delivery: {
      text: 'De spots zijn uit; Buiten liet ik bewust met rust.',
      recipient: 'Jaap', preparedAt: '2026-08-06T00:00:00.000Z',
      attempts: 2, dedupeKey: 'episode:7:prepared-delivery',
    } }),
  }), {
    kind: 'legacy',
    text: 'De spots zijn uit; Buiten liet ik bewust met rust.',
    recipient: 'Jaap', preparedAt: '2026-08-06T00:00:00.000Z',
    attempts: 2, dedupeKey: 'episode:7:prepared-delivery',
  });
});

test('een duurzaam zoekresultaat is zonder einduitkomst nog geen bijdrage', () => {
  const searched = tool({ name: 'zoek_internet' });
  assert.equal(
    __projectRouteTest.contributionFromTools(
      [searched],
      'chat',
      'Zoek honderd actuele problemen, groepeer ze en kies de beste.',
      { durable: true },
    ),
    null,
  );
});

test('een mislukte uitvoer voldoet pas na een werkend alternatief', () => {
  const recovery = 'voorbeelden/bestaat-niet.json doet het niet. Zoek uit wat er aan de hand is en zorg dat ik meteen weet welke invoer wél werkt.';
  const missingRun = {
    ...tool({ name: 'voer_uit', changed: true, data: { exit_code: 1, stderr: 'niet gevonden' } }),
    args: { programma: 'node', argumenten: ['kostenchecker.cjs', 'bestaat-niet.json'] },
  };
  const workingRun = {
    ...tool({ name: 'voer_uit', changed: true, data: { exit_code: 0, stdout: '547' } }),
    args: { programma: 'node', argumenten: ['kostenchecker.cjs', 'geldig.json'] },
  };
  assert.equal(__projectRouteTest.turnRequirementSatisfied('voer_uit', [missingRun], recovery), false);
  assert.equal(__projectRouteTest.turnRequirementSatisfied('voer_uit', [missingRun, workingRun], recovery), true);
});

test('gedeeltelijk bewijs met vervolgwerk voldoet niet aan een uitkomstcontract', () => {
  const partial = {
    name: 'zoek_internet', ok: true, verified: true,
    output: JSON.stringify({ ok: true, verified: true, partial: true, needs_followup: true }),
  };
  assert.equal(__projectRouteTest.requiredToolSatisfied('zoek_internet', [partial]), false);
  const waiting = {
    name: 'beheer_agentmail', ok: true, verified: true,
    output: JSON.stringify({ ok: true, verified: true, partial: true, needs_followup: true, waiting_for: 'human_otp' }),
  };
  assert.equal(__projectRouteTest.requiredToolSatisfied('beheer_agentmail', [waiting]), true);
});

test('bron plus test teruglezen vereist beide werkstukken', () => {
  const request = 'Lees bron plus test terug en geef dan de uitkomst.';
  const source = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/kostenchecker.cjs' } };
  const tests = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/kostenchecker.test.cjs' } };
  assert.equal(__projectRouteTest.turnRequirementSatisfied('lees_bestand', [source], request), false);
  assert.equal(__projectRouteTest.turnRequirementSatisfied('lees_bestand', [source, tests], request), true);
});

test('een HA-tijdlijnvraag vereist werkelijk een entiteitslogboek en geen algemene states-dump', () => {
  const userText = 'Zoek via Home Assistant uit of de spots vanochtend eerst uit en later weer aan gingen.';
  const general = {
    ...tool({ name: 'ha_lees' }),
    args: { pad: '/api/states' },
  };
  const logbook = {
    ...tool({ name: 'ha_lees' }),
    args: {
      pad: '/api/logbook/2026-08-06T05:00:00.000Z?entity=light.dimmable_light_1&end_time=2026-08-06T08:00:00.000Z',
    },
  };
  assert.equal(__projectRouteTest.turnRequirementSatisfied('ha_lees', [general], userText), false);
  assert.equal(__projectRouteTest.turnRequirementSatisfied('ha_lees', [general, logbook], userText), true);
});

test('een Home Assistant-logboekpad bewaart een ISO-tijdzone in de query', () => {
  assert.equal(
    __toolsTest.normalizeHaReadPath('/api/logbook/2026-08-01T00:00:00+02:00?entity=media_player.xbox&end_time=2026-08-06T17:00:00+02:00'),
    '/api/logbook/2026-08-01T00:00:00+02:00?entity=media_player.xbox&end_time=2026-08-06T17:00:00%2B02:00',
  );
});

test('een uitzoekopdracht eindigt niet met alleen een aangekondigde volgende route', () => {
  const feedback = __projectRouteTest.incompleteRequestedOutcomeFeedback(
    'Ik pak de Home Assistant-logboekroute zelf; dit bronbestand was de verkeerde lade.',
    'Zoek via Home Assistant uit wat er met de spots gebeurde.',
  );
  assert.match(feedback, /alleen een aankondiging/i);
  assert.equal(
    __projectRouteTest.incompleteRequestedOutcomeFeedback(
      'De spots gingen om 07:36 uit. De bron laat niet zien waarom ze later weer aan gingen.',
      'Zoek via Home Assistant uit wat er met de spots gebeurde.',
    ),
    '',
  );
});

test('dubbel verpakte toolresultaten worden gewone mensentaal', () => {
  const inner = JSON.stringify({
    ok: true,
    verified: true,
    message: 'Drie concrete problemen zijn gecontroleerd; nummer twee heeft de duidelijkste koper.',
    sources: ['https://example.test/bron'],
  });
  const outer = JSON.stringify({
    contract: 'ActionResult/v1',
    tool: 'zoek_internet',
    ok: true,
    verified: true,
    human_message: '',
    evidence: { summary: inner },
    data: { truncated: true, text: inner },
  });
  assert.equal(
    __projectRouteTest.humanResultText(outer, { preferDetailed: true }),
    'Drie concrete problemen zijn gecontroleerd; nummer twee heeft de duidelijkste koper.',
  );
  const fallback = __projectRouteTest.evidenceFallback([{
    name: 'zoek_internet',
    ok: true,
    verified: true,
    output: outer,
  }]);
  assert.equal(fallback.startsWith('{'), false);
  assert.equal(fallback.includes('Mijn afrondende antwoord viel weg'), false);
  const requiredFallback = __projectRouteTest.evidenceFallback([
    { name: 'zoek_internet', ok: true, verified: true, output: outer },
    tool({ name: 'voer_uit', changed: true, data: { stdout: 'RUWE INTERNE UITVOER' } }),
  ], { requiredTools: ['zoek_internet'], userText: 'Hoe kunnen we geld verdienen?' });
  assert.match(requiredFallback, /Drie concrete problemen/);
  assert.doesNotMatch(requiredFallback, /RUWE INTERNE UITVOER/);
  const readOutput = JSON.stringify({
    contract: 'ActionResult/v1', tool: 'lees_bestand', ok: true, verified: true,
    data: '1: rauwe bronregel\n2: nog een rauwe bronregel',
  });
  assert.equal(__projectRouteTest.evidenceFallback([{
    name: 'lees_bestand', ok: true, verified: true, output: readOutput,
  }], { requiredTools: ['lees_bestand'], userText: 'Zoek dit uit.' }), '');
  assert.equal(__projectRouteTest.evidenceFallback([{
    name: 'lijst_bestanden', ok: true, verified: true, output: readOutput,
  }], { requiredTools: ['lijst_bestanden'], userText: 'Zoek dit uit.' }), '');
});

test('afgekorte audituitvoer blijft geldige compacte JSON', () => {
  const parsed = {
    contract: 'ActionResult/v1', tool: 'lees_bestand', ok: true, verified: true,
    partial: false, needs_followup: false, evidence: { summary: 'bewijs '.repeat(500) },
  };
  const compact = __projectRouteTest.auditToolOutput(JSON.stringify({ ...parsed, data: 'x'.repeat(8_000) }), parsed);
  const audited = JSON.parse(compact);
  assert.equal(audited.audit_truncated, true);
  assert.equal(audited.tool, 'lees_bestand');
  assert.ok(compact.length < 1_800);
  const failed = {
    contract: 'ActionResult/v1', tool: 'voer_uit', ok: false, verified: false,
    error: 'programma ontbreekt', failure_class: 'missing_entrypoint',
    data: { exit_code: 2, expected_exit_codes: [0, 1], stderr: 'bestand niet gevonden' },
    evidence: { summary: 'x'.repeat(3_000) },
  };
  const compactFailure = JSON.parse(__projectRouteTest.auditToolOutput(
    JSON.stringify({ ...failed, padding: 'x'.repeat(8_000) }), failed,
  ));
  assert.equal(compactFailure.error, 'programma ontbreekt');
  assert.equal(compactFailure.failure_class, 'missing_entrypoint');
  assert.equal(compactFailure.data.exit_code, 2);
  assert.equal(compactFailure.data.stderr, 'bestand niet gevonden');

  const monitoringWork = {
    contract: 'ActionResult/v1', tool: 'werk_bijhouden', ok: true, changed: true,
    verified: true, partial: false, needs_followup: false,
    data: {
      ok: true, changed: true, proofAccepted: false,
      item: {
        id: 110, status: 'open', owner: 'Jaap', title: 'Temperatuur bewaken',
        next_step: 'Lees de twee gebonden bronnen opnieuw.',
        definition_of_done: 'De eerste match is eenmaal afgeleverd.',
        next_wake_ts: '2026-08-15T09:22:38.455Z',
        state_json: JSON.stringify({
          nonAuthoritativeState: 'x'.repeat(9_000),
          monitoring: {
            contract: 'MonitoringContract/v1',
            sourceUnderstandingSha256: 'a'.repeat(64),
            cadenceMs: 300_000,
            endsAt: null,
            readBindings: [
              { entityId: 'weather.forecast_thuis', requirementId: 'r2', name: 'Weer thuis' },
              { entityId: 'climate.home', requirementId: 'r3', name: 'Home' },
            ],
          },
        }),
      },
    },
    evidence: { summary: 'werkbewijs '.repeat(500) },
  };
  const compactWork = JSON.parse(__projectRouteTest.auditToolOutput(
    JSON.stringify({ ...monitoringWork, padding: 'x'.repeat(8_000) }), monitoringWork,
  ));
  assert.deepEqual({
    ok: compactWork.data.ok,
    changed: compactWork.data.changed,
    id: compactWork.data.item.id,
    status: compactWork.data.item.status,
    nextWake: compactWork.data.item.next_wake_ts,
  }, {
    ok: true, changed: true, id: 110, status: 'open',
    nextWake: '2026-08-15T09:22:38.455Z',
  });
  assert.match(compactWork.data.item.state_json, /MonitoringContract\/v1/u);
  assert.equal(compactWork.audit_compacted, true);
  assert.equal(compactWork.data.compacted, true);
  assert.doesNotMatch(compactWork.data.item.state_json, /nonAuthoritativeState/u);

  const normalizedWork = normalizeActionResultState({
    raw: JSON.stringify(monitoringWork.data),
    changesByDefault: true,
    verification: 'ok',
    effect: 'persistent',
    tool: 'werk_bijhouden',
  });
  assert.equal(normalizedWork.ok, true);
  assert.equal(normalizedWork.verified, true);
  assert.equal(normalizedWork.partial, false);
  assert.equal(normalizedWork.needsFollowup, false);
  assert.equal(normalizedWork.failure, null);
  assert.equal(normalizedWork.data.compacted, true);
  assert.equal(normalizedWork.data.item.id, 110);
  assert.equal(normalizedWork.data.item.next_step, 'Lees de twee gebonden bronnen opnieuw.');
  assert.match(normalizedWork.data.item.state_json, /MonitoringContract\/v1/u);
});

test('een gevraagd projectpad is gewone bronvermelding maar ruwe uitvoer blijft intern', () => {
  assert.equal(__projectRouteTest.looksLikeInternalOutput('Het staat in `docs/eva-runtime-map.md`.'), true);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Het staat in `docs/eva-runtime-map.md`.', { allowProjectPaths: true },
  ), false);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    '`docs/eva-runtime-map.md` zegt dit. stdout: rauw', { allowProjectPaths: true },
  ), true);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'De foutproef slaagde met exitcode 1; de melding is kort en er staat geen stacktrace.',
  ), false);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Gebruik `voorbeelden/geldig.json`; die gaf 547.', { allowProjectPaths: true },
  ), false);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Lees data/workspace/geheim.txt', { allowProjectPaths: true },
  ), true);
});

test('een inhoudelijke correctie wordt geen voortaan-beleidsverklaring', () => {
  const feedback = __projectRouteTest.styleCorrectionPolicyFeedback;
  assert.match(
    feedback(
      'Voortaan neem ik de deurcontext eerst mee.',
      'Je had kunnen weten dat ik het was omdat de voordeur net open was gegaan.',
    ),
    /geen beleid/,
  );
  assert.equal(
    feedback(
      'De net geopende voordeur maakte jou inderdaad de logische persoon.',
      'Je had kunnen weten dat ik het was omdat de voordeur net open was gegaan.',
    ),
    '',
  );
});

test('actuele internetclaims blijven zonder directe bronlink onvolledig', () => {
  const webProof = tool({ name: 'zoek_internet', data: { sources: ['https://voorbeeld.nl/vraag'] } });
  assert.match(
    __projectRouteTest.missingSourceLinkFeedback(
      'Er is nu aantoonbare vraag en honderd euro is haalbaar.', ['zoek_internet'], [webProof],
    ),
    /bron-URL/,
  );
  assert.equal(
    __projectRouteTest.missingSourceLinkFeedback(
      'De actuele bron staat op https://voorbeeld.nl/vraag.', ['zoek_internet'], [webProof],
    ),
    '',
  );
});

test('een echte vervolgcontrole bestaat pas na een duurzame wake én terugmelding', () => {
  const request = 'Blijf jij het nu bekijken. Geef me tips als ik iets moet doen.';
  const monitoringOptions = {
    turnKind: 'monitoring',
    ...__projectRouteTest.monitoringRequestSpec({
      text: request,
      recentThreadRows: [
        { content: 'Hoe warm is de BBQ nu?' },
        { content: 'De BBQ is nu 78 graden.' },
      ],
      requester: 'Jaap',
      now: Date.now(),
    }),
    sourceReadBound: true,
  };

  const unscheduled = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: {
      item: {
        status: 'open',
        next_wake_ts: new Date(Date.now() + 5 * 60_000).toISOString(),
        next_step: 'Ga hier later mee verder.',
      },
    },
  });
  const scheduled = tool({
    name: 'werk_bijhouden',
    changed: true,
    args: { bewerking: 'update', id: 41 },
    data: {
      item: {
        id: 41,
        status: 'open',
        next_wake_ts: new Date(Date.now() + 5 * 60_000).toISOString(),
        next_step: 'Lees de actuele BBQ- en kerntemperatuur en stuur Jaap alleen een relevante tip terug.',
      },
    },
  });
  const promise = 'Ja, ik houd de BBQ in de gaten en meld me als je iets moet bijstellen.';
  assert.equal(requirementSatisfied(
    'werk_bijhouden', [unscheduled], request, monitoringOptions,
  ), false);
  assert.equal(requirementSatisfied(
    'werk_bijhouden', [scheduled], request, monitoringOptions,
  ), true);
  assert.equal(
    requirementSatisfied(
      'werk_bijhouden', [scheduled], request, { ...monitoringOptions, workId: 41 },
    ),
    true,
  );
  assert.equal(
    requirementSatisfied(
      'werk_bijhouden', [scheduled], request, { ...monitoringOptions, workId: 42 },
    ),
    false,
  );
  assert.equal(
    requirementSatisfied(
      'werk_bijhouden', [scheduled], request, {
        ...monitoringOptions, workId: Number.NaN, requireBound: true,
      },
    ),
    false,
  );
  const tooLate = tool({
    ...scheduled,
    name: 'werk_bijhouden',
    changed: true,
    args: { bewerking: 'update', id: 41 },
    data: {
      item: {
        ...JSON.parse(scheduled.output).data.item,
        next_wake_ts: new Date(Date.now() + 25 * 60 * 60_000).toISOString(),
      },
    },
  });
  assert.equal(
    requirementSatisfied(
      'werk_bijhouden', [tooLate], request, { ...monitoringOptions, workId: 41 },
    ),
    false,
  );
  assert.match(__projectRouteTest.monitoringPromiseFeedback(promise, true, []), /niet duurzaam gepland/i);
  assert.match(__projectRouteTest.monitoringPromiseFeedback('Komt goed.', true, []), /niet duurzaam gepland/i);
  assert.equal(
    __projectRouteTest.monitoringPromiseFeedback(promise, true, [scheduled], {
      workId: 41, sourceReadBound: true,
    }),
    '',
  );
  assert.match(
    __projectRouteTest.monitoringPromiseFeedback(promise, true, [scheduled], {
      workId: 42, sourceReadBound: true,
    }),
    /niet duurzaam gepland/i,
  );
});

test('een bestaande huisstatus wordt niet als Eva haar eigen wijziging verteld', () => {
  const request = 'Zet de tweede tv in de overkapping uit.';
  const intent = { doel: 'tweede tv in de overkapping', actie: 'uit' };
  const unchanged = tool({
    name: 'bedien_huis',
    changed: false,
    args: { doel: 'tweede tv in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'media_player.tv_overkapping_2', name: 'TV overkapping 2',
      before: 'off', after: 'off',
    },
  });
  const changed = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede tv in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'media_player.tv_overkapping_2', name: 'TV overkapping 2',
      before: 'on', after: 'off',
    },
  });
  const changedWithGenericFriendlyName = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede tv in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'media_player.tv_overkapping_2', name: 'Televisie',
      before: 'on', after: 'off',
    },
  });
  const wrongLamp = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede lamp in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'light.lamp_overkapping_2', name: 'Lamp overkapping 2',
      before: 'on', after: 'off',
    },
  });
  const spoofedTarget = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede tv in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'light.lamp_overkapping_2', name: 'Lamp overkapping 2',
      before: 'on', after: 'off',
    },
  });
  const wrongArgsForResolvedTv = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede lamp in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'media_player.tv_overkapping_2', name: 'TV overkapping 2',
      before: 'on', after: 'off',
    },
  });
  const inconsistentResolvedTarget = tool({
    name: 'bedien_huis',
    changed: true,
    args: { doel: 'tweede tv in de overkapping', actie: 'uit' },
    data: {
      entity_id: 'light.lamp_overkapping_2', name: 'TV overkapping 2',
      before: 'on', after: 'off',
    },
  });
  const tvReadback = tool({
    name: 'ha_lees',
    data: {
      entity_id: 'media_player.tv_overkapping_2', name: 'TV overkapping 2', state: 'off',
    },
  });
  const lampReadback = tool({
    name: 'ha_lees',
    data: {
      entity_id: 'light.lamp_overkapping_2', name: 'Lamp overkapping 2', state: 'off',
    },
  });
  assert.match(
    __projectRouteTest.homeEffectAttributionFeedback('Ik heb hem uitgezet.', request, [unchanged]),
    /niet aantoonbaar zelf veranderd/i,
  );
  assert.equal(
    __projectRouteTest.homeEffectAttributionFeedback('De tweede tv stond al uit.', request, [unchanged]),
    '',
  );
  assert.equal(
    __projectRouteTest.homeEffectAttributionFeedback(
      'Ik heb gecontroleerd dat hij al uit stond; ik heb hem niet uitgezet.', request, [unchanged],
    ),
    '',
  );
  for (const answer of ['Uitgezet, klaar.', 'Gedaan, hij is nu uit.']) {
    assert.match(
      __projectRouteTest.homeEffectAttributionFeedback(answer, request, [unchanged]),
      /niet aantoonbaar zelf veranderd/i,
    );
  }
  assert.match(
    __projectRouteTest.homeEffectAttributionFeedback('Ik heb hem uitgezet.', request, [wrongLamp]),
    /niet aantoonbaar zelf veranderd/i,
  );
  for (const receipt of [spoofedTarget, wrongArgsForResolvedTv, inconsistentResolvedTarget]) {
    assert.match(
      __projectRouteTest.homeEffectAttributionFeedback('Ik heb hem uitgezet.', request, [receipt]),
      /niet aantoonbaar zelf veranderd/i,
    );
  }
  for (const answer of ['Ik zette hem uit.', "Heb 'm even uit gedaan.", 'Klaar.']) {
    assert.match(
      __projectRouteTest.homeEffectAttributionFeedback(answer, request, [unchanged]),
      /niet aantoonbaar zelf veranderd/i,
      answer,
    );
    assert.equal(
      __projectRouteTest.homeEffectAttributionFeedback(answer, request, [changed]),
      '',
      answer,
    );
  }
  assert.equal(
    __projectRouteTest.homeEffectAttributionFeedback('Tv uit.', request, [unchanged]),
    '',
  );
  assert.match(
    __projectRouteTest.homeEffectAttributionFeedback('Tv uit.', request, [wrongLamp]),
    /niet voor precies het gevraagde apparaat bevestigd/i,
  );
  assert.match(
    __projectRouteTest.homeEffectAttributionFeedback('Klaar.', request, [wrongLamp]),
    /niet aantoonbaar zelf veranderd/i,
  );
  for (const denial of [
    'Ik zette hem niet uit.',
    "Ik heb 'm niet uit gedaan.",
    'Ik heb hem niet uitgezet.',
  ]) {
    assert.equal(
      __projectRouteTest.homeEffectAttributionFeedback(denial, request, [wrongLamp]),
      '',
      denial,
    );
  }
  assert.equal(homeReadbackProvesIntent(intent, [unchanged, tvReadback]), true);
  assert.equal(homeReadbackProvesIntent(intent, [wrongLamp, lampReadback]), false);
  assert.equal(homeReadbackProvesIntent(intent, [spoofedTarget, lampReadback]), false);
  assert.equal(homeReadbackProvesIntent(intent, [unchanged, lampReadback]), false);
  assert.match(
    homeEffectClaimFeedback('De tweede tv staat uit.', intent, [wrongLamp, lampReadback], {
      readbackProven: true,
    }),
    /niet voor precies het gevraagde apparaat bevestigd/i,
  );
  assert.equal(
    __projectRouteTest.homeEffectAttributionFeedback('Ik heb hem uitgezet.', request, [changed]),
    '',
  );
  assert.equal(
    __projectRouteTest.homeEffectAttributionFeedback(
      'Ik heb hem uitgezet.', request, [changedWithGenericFriendlyName],
    ),
    '',
  );
});

test('zelfverbetering gebruikt relatiefeedback zonder een tweede geheugenregel', () => {
  const request = 'Pas je eigen communicatie aan.';
  const relevantHistory = tool({
    name: 'zoek_gesprek',
    data: {
      count: 2,
      messages: [
        { sender: 'Jaap', content: 'Je communicatie klinkt nog steeds te zakelijk.' },
        { sender: 'Eva', content: 'Ik zal mijn communicatiestijl aanpassen.' },
      ],
    },
  });
  const unrelatedHistory = tool({
    name: 'zoek_gesprek',
    data: { count: 1, messages: [{ content: 'De vuilnisbak is vol.' }] },
  });
  assert.equal(
    requirementSatisfied('zoek_gesprek', [unrelatedHistory], request, {
      turnKind: 'self_improvement',
    }),
    false,
  );
  assert.equal(
    requirementSatisfied('zoek_gesprek', [relevantHistory], request, {
      turnKind: 'self_improvement',
    }),
    true,
  );
  assert.equal(__projectRouteTest.shouldAutoBindDurableTool({
    mode: 'chat', syntheticTest: false, turnKind: 'self_improvement',
    hasEpisode: false, name: 'zoek_gesprek', recorded: {
      ok: true, verified: true, changed: false,
    },
  }), false);
});

test('technisch zelfherstel is brongebonden diagnose en geen geheugen- of succesclaim', () => {
  const request = 'Repareer en test je eigen wakeword-router.';
  const contract = { kind: 'self_repair' };
  const proofOptions = { turnKind: contract.kind };
  assert.equal(
    requirementSatisfied('zoek_gesprek', [tool({
      name: 'zoek_gesprek', data: { count: 1, messages: [{ content: 'De vuilnisbak is vol.' }] },
    })], request, proofOptions),
    false,
  );
  const relevantHistory = tool({
    name: 'zoek_gesprek',
    data: { count: 1, messages: [{ content: 'Hey Eva activeerde terwijl niemand het wakeword zei.' }] },
  });
  assert.equal(
    requirementSatisfied('zoek_gesprek', [relevantHistory], request, proofOptions),
    true,
  );
  const listed = tool({ name: 'lijst_bestanden', data: { entries: ['src/voice/wakeword.js'] } });
  const read = tool({
    name: 'lees_bestand', args: { pad: 'src/voice/wakeword.js', zoek: 'wakeword' },
    data: { content: 'wakeword detector routing' },
  });
  assert.equal(
    requirementSatisfied('lees_bestand', [read, listed], request, proofOptions),
    false,
  );
  assert.equal(
    requirementSatisfied('lees_bestand', [listed, read], request, proofOptions),
    true,
  );
  assert.match(
    __projectRouteTest.finalReplyFeedback({
      reply: 'Ik heb de wakeword-router gerepareerd.',
      mode: 'chat', userText: request, toolResults: [relevantHistory, listed, read],
      turnContract: contract, turnPlan: { actionIntent: 'execute' },
    }),
    /niet (?:gewijzigd|door de root-maintainer toegepast)/i,
  );
});

test('iteratieve uitvoering heeft ruimte voor herstel plus eindbewijs en eert autonome aandachtstijd', () => {
  const chat = __projectRouteTest.iterativeExecutionBudget('chat');
  const autonomy = __projectRouteTest.iterativeExecutionBudget('autonomy');
  assert.ok(chat.maxRounds > 12);
  assert.equal(chat.baseRounds, 12);
  assert.equal(chat.progressExtensionRounds, 3);
  assert.equal(chat.totalTimeoutMs, 10 * 60_000);
  assert.ok(autonomy.maxRounds > chat.maxRounds);
  assert.equal(autonomy.totalTimeoutMs, config.autonomyTurnMinutes * 60_000);
});

test('expliciet onvoltooid bewijs wordt niet als een valse klaarclaim behandeld', () => {
  assert.equal(__projectRouteTest.explicitlyIncompleteAction(
    'De tests zijn uitgevoerd, maar de laatste teruglezing ontbreekt nog. Dit is nog niet afgerond.',
  ), true);
  assert.equal(__projectRouteTest.explicitlyIncompleteAction(
    'Alles is klaar en volledig gecontroleerd.',
  ), false);
});

test('een capabilitydemonstratie valt terug op controleerbare gewone taal', () => {
  const used = tool({
    name: 'gebruik_capability',
    changed: false,
    data: {
      demonstrated: true,
      receipt_id: 12,
      capability: { kind: 'skill', name: 'antwoord-afmaakpoort', version: 2 },
    },
  });
  const answer = __projectRouteTest.evidenceFallback([used], {
    requiredTools: ['gebruik_capability'],
    userText: 'Demonstreer een skill.',
  });
  assert.match(answer, /antwoord-afmaakpoort v2/);
  assert.match(answer, /echt (?:geladen en )?gebruikt/);
  assert.doesNotMatch(answer, /receipt|hash|sandbox/i);
  assert.doesNotMatch(answer, /\{"/);
});

test('nieuw bewijs binnen hetzelfde project is geen dubbele autonome melding', () => {
  const first = 'Ik heb een eerste lokale Home Assistant-configuratie-audit gebouwd en getest. Het rapport vond 6 problemen.';
  const progress = 'De regressietest van de Home Assistant-configuratie-audit is nu apart gecontroleerd en slaagt met de bedoelde exitcode.';
  const repeat = 'Ik heb de lokale Home Assistant-configuratie-audit gebouwd en getest; het rapport vond 6 problemen.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(progress, first), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(progress, first), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(repeat, first), false);
  const liveFirst = 'De ontbrekende regressietest voor de Home Assistant-audit is nu gecontroleerd: alle 4 varianten met lege trigger- of actieblokken worden correct afgekeurd. Dit dicht een echt gat in de audit; het is nog geen verdiend geld.';
  const liveRepeat = 'De ontbrekende regressietest staat nu weer op de juiste plek en is opnieuw uitgevoerd: alle vier lege trigger-/actievarianten worden correct afgekeurd. Daarmee is die concrete blokkade uit het werkboek weg; dit is nog geen verdiend geld.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(liveRepeat, liveFirst), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(liveRepeat, liveFirst), false);

  const doorFirst = 'De achterdeur staat open terwijl het buiten koeler is.';
  const doorRepeat = 'Omdat de koelte helpt, kan die deur nog open blijven.';
  const doorChanged = 'De achterdeur staat nog open, maar het regent nu naar binnen.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(doorRepeat, doorFirst), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(doorRepeat, doorFirst), false);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(doorChanged, doorFirst), true);
});

test('synthetische livegesprekken kunnen productieplanning niet besmetten', () => {
  const allowed = __projectRouteTest.syntheticToolAllowed;
  for (const name of [
    'werk_bijhouden', 'verkenning_bijhouden', 'onthoud', 'zet_herinnering',
    'bedien_huis', 'gebruik_robot', 'stuur_bericht', 'beheer_agentmail',
    'maak_vaardigheid', 'beheer_addon', 'toon_werk', 'zoek_geheugen',
    'zoek_gesprek', 'signalen_afhandelen',
  ]) assert.equal(allowed(name), false, `${name} mocht muteren`);
  for (const name of ['huis_status', 'ha_lees', 'lees_bestand', 'schrijf_bestand', 'voer_uit']) {
    assert.equal(allowed(name), true, `${name} had als geïsoleerde proef beschikbaar moeten blijven`);
  }
  assert.equal(
    __projectRouteTest.syntheticReplyNeedsPersistentState('Morgen om half tien pak ik dit weer op.'),
    true,
  );
  assert.equal(
    __projectRouteTest.syntheticReplyNeedsPersistentState('Ik heb de bron nu gecontroleerd.'),
    false,
  );
});

test('een terugblik maakt toevallig toolgebruik niet tot een bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution(
      [tool({ name: 'voer_uit' })],
      'chat',
      'Wat heb je vandaag gedaan?',
      plannedExecution(),
    ),
    null,
  );
  assert.equal(
    contribution(
      [tool({ name: 'voer_uit' })],
      'chat',
      'Voer het prijsprogramma uit.',
      plannedExecution({ requiredTools: ['voer_uit'], actionRequested: true }),
    )?.name,
    'voer_uit',
  );
});

test('autonomie telt geen losse gedachte of ongecontroleerde write als bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(contribution([tool({ name: 'onthoud', changed: true })], 'autonomy'), null);
  assert.equal(contribution([tool({ name: 'schrijf_bestand', changed: true })], 'autonomy'), null);

  const checkedArtifact = [
    { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/notitie.md' } }), args: { pad: 'data/workspace/notitie.md' } },
    { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/notitie.md' } },
  ];
  assert.equal(contribution(checkedArtifact, 'autonomy'), null);

  const groundedMessage = [
    tool({ name: 'zoek_internet' }),
    tool({ name: 'stuur_bericht', changed: true }),
  ];
  assert.equal(contribution(groundedMessage, 'autonomy')?.name, 'stuur_bericht');
});

test('bij meerdere bestanden blijft uitvoerbare broncode het te controleren werkstuk', () => {
  const program = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/audit.py' } }),
    args: { pad: 'data/workspace/audit.py' },
  };
  const fixture = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/fout.yaml' } }),
    args: { pad: 'data/workspace/fout.yaml' },
  };
  const run = {
    ...tool({ name: 'voer_uit' }),
    args: {
      programma: 'python3',
      argumenten: ['audit.py', 'fout.yaml'],
      werkmap: 'data/workspace',
    },
  };
  assert.equal(
    __projectRouteTest.incompleteAutonomousWrite([program, fixture, run])?.args?.pad,
    'data/workspace/audit.py',
  );
  const read = {
    ...tool({ name: 'lees_bestand' }),
    args: { pad: 'data/workspace/audit.py' },
  };
  assert.equal(
    __projectRouteTest.completedAutonomousArtifact([program, fixture, run, read])?.args?.pad,
    'data/workspace/audit.py',
  );
});

test('bestaand open werk mag in stille uren doorgaan en krijgt snel vervolg', () => {
  const open = { open_commitments: [{ id: 8 }] };
  const idle = { open_commitments: [] };
  assert.equal(__autonomyScheduleTest.mayWorkAtNight(open, []), true);
  assert.equal(__autonomyScheduleTest.mayWorkAtNight(idle, []), false);
  assert.equal(
    __autonomyScheduleTest.nextDelay({ contribution: null }, open),
    30 * 60_000,
  );
  assert.equal(
    __autonomyScheduleTest.nextDelay({ progress: { made_progress: true } }, open),
    5 * 60_000,
  );
  const now = Date.parse('2026-08-03T00:00:00.000Z');
  assert.equal(
    __autonomyScheduleTest.schedulerDelay('2026-08-03T00:01:00.000Z', now),
    60_000,
  );
  assert.equal(
    __autonomyScheduleTest.schedulerDelay('2026-08-02T23:59:00.000Z', now),
    1_000,
  );
});

test('een nieuwe aspiratie verdringt geen uitvoerbaar bestaand commitment', () => {
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([], null), true);
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([], { item: { id: 41 } }), false);
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([{ id: 'deur' }], null), false);
});

test('autonoom werk vraagt rapportage en daarna het meerdaagse checkpoint', () => {
  const started = tool({ name: 'werk_bijhouden', changed: true, data: { item: { id: 7, status: 'open' } } });
  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/resultaat.md' } }),
    args: { pad: 'data/workspace/resultaat.md' },
  };
  const read = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/resultaat.md' } };
  const sent = tool({ name: 'stuur_bericht', changed: true });
  assert.equal(__projectRouteTest.autonomyArtifactReportPending([written, read]), true);
  assert.equal(__projectRouteTest.autonomyArtifactReportPending([written, read, sent]), false);
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([started, written, read, sent], [7]),
    true,
  );
  const done = tool({ name: 'werk_bijhouden', changed: true, data: { item: { id: 7, status: 'done' } } });
  const executed = {
    ...tool({ name: 'voer_uit' }),
    args: {
      programma: 'node',
      argumenten: ['controle.mjs'],
      werkmap: 'data/workspace',
    },
  };
  const sourceRead = {
    ...tool({ name: 'lees_bestand' }),
    args: { pad: 'data/workspace/controle.mjs' },
  };
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([executed, sourceRead, done, sent], [7]),
    true,
  );

  const startedHere = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: { item: { id: 8, status: 'open' } },
  });
  const checkpointHere = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: { item: { id: 8, status: 'open' } },
  });
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([written, read, startedHere, sent]),
    true,
  );
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending(
      [written, read, startedHere, sent, checkpointHere],
    ),
    false,
  );
});

test('autonoom werk kan niet vóór het gezinsbericht als afgerond worden geboekt', async () => {
  const item = createWorkItem({
    source: 'test',
    title: 'Rapporteer vóór afronden',
    goal: 'Voer controle.mjs uit en meld het bewezen resultaat.',
    nextStep: 'Voer de controle uit.',
  });
  const result = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden',
    id: item.id,
    resultaat: 'klaar',
  }, {
    mode: 'autonomy',
    priorToolResults: [tool({ name: 'voer_uit' })],
  }));

  assert.equal(result.ok, false);
  assert.match(result.evidence.summary, /pas afgerond nadat/i);
});

test('vrije autonomie kan niet blijven zoeken of oude gesprekken als takenbak gebruiken', () => {
  const offered = toolsForTurn({ mode: 'autonomy' }).map((item) => item.name);
  assert.equal(offered.includes('zoek_gesprek'), false);
  assert.equal(offered.includes('toon_werk'), false);

  const reads = [
    tool({ name: 'huis_status' }),
    tool({ name: 'ha_lees' }),
    tool({ name: 'wie_is_thuis' }),
    tool({ name: 'zoek_geheugen' }),
  ];
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', reads), false);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('stuur_bericht', reads), true);

  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/proef.md' } }),
    args: { pad: 'data/workspace/proef.md' },
  };
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('lees_bestand', [...reads, written]), true);
  assert.equal(__projectRouteTest.autonomyLaneAllows('lees_bestand', 'research', reads), true);
  assert.equal(__projectRouteTest.autonomyLaneAllows('lees_bestand', 'research', [...reads, written]), true);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', [
    tool({ name: 'huis_status', ok: false, verified: false }),
  ]), true);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', [
    tool({ name: 'ha_lees', ok: false, verified: false }),
    tool({ name: 'ha_lees', ok: false, verified: false }),
  ]), false);

  assert.equal(
    __projectRouteTest.autonomyNoActionReason('to=functions.ha_lees ...', reads),
    'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.',
  );
  assert.equal(
    __projectRouteTest.autonomyNoActionReason(
      'Klaar: `data/workspace/inkomstenkans-start-nu-uitwerken.md` is gecontroleerd en bevat precies drie concrete verkoopteksten.',
      [],
    ),
    'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.',
  );
  assert.equal(
    __toolsTest.emptyAutonomousWebFinding('Voor morgen zijn geen aangekondigde afsluitingen of andere verkeershinder gevonden.'),
    true,
  );
  assert.equal(
    __toolsTest.emptyAutonomousWebFinding('De N304 is morgen van 09:00 tot 16:00 afgesloten.'),
    false,
  );
  assert.equal(
    __toolsTest.autonomousMessagesOverlap(
      'De ramen boven kunnen vannacht openblijven; KNMI verwacht een droge nacht.',
      'Als de ramen boven nog dicht zijn, zet ze nu even open om af te koelen.',
    ),
    true,
  );
  assert.equal(
    __toolsTest.autonomousMessagesOverlap(
      'Er is rook bij de schuur; ga direct naar buiten.',
      'De schuurdeur staat nog open.',
    ),
    false,
  );
});

test('een open autonoom werkpunt blijft uitvoerbaar en echte voortgang is apart zichtbaar', () => {
  const open = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: {
      proofAccepted: true,
      item: {
        id: 8,
        status: 'open',
        title: 'Maak de audit af',
        next_step: 'Herstel de lege action-controle en voer de regressietest opnieuw uit.',
      },
    },
  });
  assert.equal(__projectRouteTest.autonomyProgressFromTools([open])?.made_progress, true);

  const unchanged = { ...open, changed: false };
  assert.equal(__projectRouteTest.autonomyProgressFromTools([unchanged]), null);
  assert.equal(__projectRouteTest.autonomyLaneForTool('huis_status'), null);
  assert.equal(__projectRouteTest.autonomyLaneForWork({
    title: 'Home Assistant YAML-audit',
    next_step: 'Pas ha_config_audit.py aan en voer de test uit.',
  }), 'workspace');
  const paidProject = {
    title: 'Een eerste betaalde softwareklus kiezen',
    goal: 'Vind een koper voor een Home Assistant-configuratie-audit.',
    next_step: 'Controleer de Reddit-thread en de betaalbereidheid.',
  };
  assert.equal(__projectRouteTest.autonomyProofRelevantToWork(
    paidProject,
    tool({ name: 'huis_status' }),
  ), false);
  assert.equal(__projectRouteTest.autonomyProofRelevantToWork(
    paidProject,
    tool({ name: 'zoek_internet' }),
  ), true);
  assert.equal(__toolsTest.proofToolFitsSubject(
    `${paidProject.title} ${paidProject.goal} ${paidProject.next_step}`,
    'huis_status',
  ), false);
});

test('gedeeltelijk bereikbare huisgroepen gelden als uitgevoerd met eerlijke opvolging', () => {
  assert.deepEqual(__toolsTest.homeOperationOutcome('on', { state: 'on' }, {
    ok: false,
    reachableVerified: true,
    unavailable: [{ entity_id: 'light.buiten_2' }],
  }), {
    ok: true,
    verified: true,
    partial: true,
    needsFollowup: true,
  });
});

test('identiek werk herschrijven en een onbekende werkbewerking tellen niet als voortgang', async () => {
  const file = path.join(dataDir, 'workspace', 'zelfde.txt');
  const first = JSON.parse(await executeTool('schrijf_bestand', {
    pad: file,
    inhoud: 'zelfde inhoud',
  }, { mode: 'autonomy' }));
  const second = JSON.parse(await executeTool('schrijf_bestand', {
    pad: file,
    inhoud: 'zelfde inhoud',
  }, { mode: 'autonomy' }));
  assert.equal(first.changed, true);
  assert.equal(second.changed, false);

  const invalid = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'doe-maar-iets',
  }, { mode: 'autonomy' }));
  assert.equal(invalid.ok, false);
  assert.equal(invalid.changed, false);
  assert.match(invalid.error, /onbekende werkbewerking/i);
});

test('de autonome event-inbox bewaart openingen en echte temperatuurstappen, geen sluitruis', () => {
  const temperature = (previous, current) => ({
    domain: 'sensor',
    oldState: String(previous),
    newState: String(current),
    attributes: { deviceClass: 'temperature', unit_of_measurement: '°C' },
  });
  const contact = (state) => ({
    domain: 'binary_sensor',
    newState: state,
    attributes: { deviceClass: 'door' },
  });

  assert.equal(shouldQueueWorldSignal(temperature(20, 20.6)), false);
  assert.equal(shouldQueueWorldSignal(temperature(20, 21)), true);
  assert.equal(shouldQueueWorldSignal(contact('off')), false);
  assert.equal(shouldQueueWorldSignal(contact('on')), true);
  assert.equal(shouldWakeForWorldSignal(contact('on')), true);
  assert.equal(shouldWakeForWorldSignal(temperature(20, 21)), false);
  assert.equal(hasNightUrgentSignal([{
    key: 'binary_sensor.achterdeur',
    type: 'entity_state',
    text: 'Achterdeur: off -> on',
  }]), true);
  assert.equal(hasNightUrgentSignal([{
    key: 'sensor.temperatuur',
    type: 'entity_state',
    text: 'Temperatuur: 20 -> 21',
  }]), false);
});

test('een gewone herinnering of gezichtsnaam wordt geen verzonnen gezinsbijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution([tool({ name: 'onthoud', changed: true })], 'chat', 'Dat klopt', plannedExecution()),
    null,
  );
  assert.equal(
    contribution(
      [tool({ name: 'leer_gezicht_van_foto', changed: true })],
      'chat',
      'Dit is Lola',
      plannedExecution({ requiredTools: ['leer_gezicht_van_foto'], actionRequested: true }),
    ),
    null,
  );
  assert.equal(
    contribution(
      [tool({ name: 'onthoud', changed: true })],
      'chat',
      'Onthoud dat Lola van thee houdt',
      plannedExecution({ requiredTools: ['onthoud'], actionRequested: true }),
    )?.name,
    'onthoud',
  );
});

test('alleen een terminale auto-bound éénstapsvoorkeur mag op transportaflevering wachten', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  const memory = {
    ...tool({ name: 'onthoud', changed: true }),
    semanticBinding: {
      capabilityId: 'memory.fact.write', operationId: 'remember',
      requirementId: 'r1', role: 'primary',
    },
  };
  const primaryPlan = {
    requirementId: 'r1', toolName: 'onthoud',
    capabilityId: 'memory.fact.write', operationId: 'remember',
    supportsDeferred: false,
  };
  const validUnderstanding = {
    durability: 'turn', temporal: { mode: 'now' },
    source: { origin: 'user' }, monitoring: null,
  };
  const autoBoundOneShot = {
    autoBoundThisTurn: true,
    turnSucceeded: true,
    continuation: false,
    resumed: false,
    understanding: validUnderstanding,
    plans: [primaryPlan],
  };
  const options = (overrides = {}) => ({
    durable: true,
    plannedRequiredTools: ['onthoud'],
    plannedTurnKind: 'action',
    plannedActionRequested: true,
    autoBoundOneShot: { ...autoBoundOneShot, ...overrides },
  });

  assert.equal(contribution(
    [memory], 'chat',
    'Ik heb liever één afgemaakt resultaat dan vijf ideeën—onthoud die voorkeur.',
    options(),
  )?.name, 'onthoud');
  assert.equal(contribution(
    [memory], 'chat', 'Onthoud dit.', options(),
  )?.name, 'onthoud', 'na het effect resteert bij composeruitval alleen aflevering');

  const incomplete = [
    { ...memory, partial: true },
    { ...memory, needs_followup: true },
    { ...memory, effect_uncertain: true },
    { ...memory, ok: false },
    { ...memory, verified: false },
    { ...memory, changed: false },
  ];
  for (const receipt of incomplete) {
    assert.equal(contribution([receipt], 'chat', 'Onthoud dit.', options()), null);
  }
  for (const boundary of [
    { turnKind: 'monitoring' },
    { turnKind: 'continuity' },
    { oneShot: { plans: [{ ...primaryPlan, supportsDeferred: true }] } },
    { oneShot: { understanding: { ...validUnderstanding, durability: 'episode' } } },
    { oneShot: { understanding: { ...validUnderstanding, temporal: { mode: 'later' } } } },
    { oneShot: { understanding: { ...validUnderstanding, source: { origin: 'resume' } } } },
    { oneShot: { understanding: { ...validUnderstanding, source: { origin: 'self' } } } },
    { oneShot: { understanding: { ...validUnderstanding, source: undefined } } },
    { oneShot: { resumed: true } },
    { oneShot: { continuation: true } },
    { oneShot: { plans: [primaryPlan, { ...primaryPlan, requirementId: 'r2' }] } },
    { oneShot: { turnSucceeded: false } },
    { oneShot: { autoBoundThisTurn: false } },
  ]) {
    assert.equal(contribution([memory], 'chat', 'Onthoud dit.', {
      ...options(boundary.oneShot),
      ...(boundary.turnKind ? { plannedTurnKind: boundary.turnKind } : {}),
    }), null, JSON.stringify(boundary));
  }
  assert.equal(contribution([memory], 'chat', 'Onthoud dit.', {
    ...options(), plannedActionRequested: false,
  }), null, 'een niet-geselecteerde actie krijgt geen lifecycle-effect');
  assert.equal(contribution([memory], 'chat', 'Onthoud dit en maak daarna een bestand.', {
    ...options({ plans: [primaryPlan, {
      requirementId: 'r2', toolName: 'schrijf_bestand',
      capabilityId: 'workspace.file.write', operationId: 'write',
    }] }),
    plannedRequiredTools: ['onthoud', 'schrijf_bestand'],
  }), null, 'incidenteel geheugen in groter werk mag het werk niet afronden');
  for (const semanticBinding of [
    { ...memory.semanticBinding, capabilityId: 'memory.fact.read' },
    { ...memory.semanticBinding, operationId: 'search' },
    { ...memory.semanticBinding, role: 'recovery' },
  ]) {
    assert.equal(contribution([{ ...memory, semanticBinding }], 'chat', 'Onthoud dit.', options()),
      null, JSON.stringify(semanticBinding));
  }
});

test('kritiek of een losse herhaling wordt door toevallig toolgebruik geen bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution(
      [tool({ name: 'schrijf_bestand', changed: true })],
      'chat',
      'Daar heb ik niks aan',
      plannedExecution(),
    ),
    null,
  );
  assert.equal(
    contribution(
      [tool({ name: 'voer_uit', changed: true })],
      'chat',
      'Alweer',
      plannedExecution(),
    ),
    null,
  );
});

test('een half gebouwd programma telt niet als afgeronde bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  const request = 'Bouw een werkend programma in een bestand';
  assert.equal(
    contribution(
      [tool({ name: 'schrijf_bestand', changed: true })],
      'chat',
      request,
      plannedExecution({
        requiredTools: ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
        turnKind: 'programming_build',
        actionRequested: true,
      }),
    ),
    null,
  );
  assert.equal(
    contribution([
      { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/rekenaar.mjs' } }), args: { pad: 'data/workspace/rekenaar.mjs' } },
      { ...tool({ name: 'voer_uit', changed: true }), args: { programma: 'node', argumenten: ['rekenaar.mjs'], werkmap: 'data/workspace' } },
    ], 'chat', request, plannedExecution({
      requiredTools: ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
      turnKind: 'programming_build',
      actionRequested: true,
      durable: true,
    })),
    null,
  );
  assert.equal(
    contribution([
      { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/rekenaar.mjs' } }), args: { pad: 'data/workspace/rekenaar.mjs' } },
      { ...tool({ name: 'voer_uit', changed: true }), args: { programma: 'node', argumenten: ['rekenaar.mjs'], werkmap: 'data/workspace' } },
      { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/rekenaar.mjs' } },
    ], 'chat', request, plannedExecution({
      requiredTools: ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
      turnKind: 'programming_build',
      actionRequested: true,
    }))?.name,
    'voer_uit',
  );
});

test('een gecontroleerd document valt terug op de inhoud en nooit op het interne pad', () => {
  const reply = __projectRouteTest.evidenceFallback([
    { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/bieding.md' } }), args: { pad: 'data/workspace/bieding.md' } },
    {
      ...tool({ name: 'lees_bestand', data: { text: 'Hello, I propose a small first milestone with three concrete deliverables.' } }),
      args: { pad: 'data/workspace/bieding.md' },
    },
  ], { userText: 'Doe dit.' });
  assert.match(reply, /small first milestone/);
  assert.doesNotMatch(reply, /data\/workspace/);
});

test('interne autonome voortgang blijft buiten het gezinskanaal', () => {
  assert.equal(__toolsTest.autonomousMessageIsInternalProgress(
    'De regressietest van de Home Assistant-configuratie-audit slaagt met exitcode 0.',
  ), true);
  assert.equal(__toolsTest.autonomousMessageIsInternalProgress(
    'De achterdeur staat nog open en het gaat regenen; zal ik jullie over tien minuten nog eens waarschuwen?',
  ), false);
});

test('commercieel onderzoek degradeert een open-claim zonder toekomstige sluitingsdatum', () => {
  const bounded = __toolsTest.commercialResearchEvidenceBoundary(
    'Vind een betaalde freelance opdracht',
    'Deze opdracht staat open en reageren kan. Peildatum 2026-08-04.',
    new Date('2026-08-04T10:00:00Z'),
  );
  assert.match(bounded, /actuele open status.*niet bewezen/i);
  const proven = __toolsTest.commercialResearchEvidenceBoundary(
    'Vind een betaalde freelance opdracht',
    'De directe bron meldt open; sluitingsdatum 2026-08-09.',
    new Date('2026-08-04T10:00:00Z'),
  );
  assert.doesNotMatch(proven, /niet bewezen/i);
});

test('ruwe uitvoer en gescrapete html zijn nooit een gezinsantwoord', () => {
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    '<p _ngcontent-webapp>Scope of Work</p> stdout exitcode 0',
  ), true);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Ik heb een biedtekst gemaakt met één concrete mijlpaal en drie vragen.',
  ), false);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Hij staat klaar als `freelancer-first-milestone-scope.md`.',
  ), true);
});

test('een autonome vervolgsessie bewaart de concrete uitvoerfout', () => {
  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/audit.py' } }),
    args: { pad: 'data/workspace/audit.py' },
  };
  const failed = {
    ...tool({
    name: 'voer_uit',
    ok: false,
    verified: false,
    data: {
      exit_code: 2,
      stderr: "No such file or directory: 'foute_automations.yaml'",
    },
    }),
    args: { programma: 'python3', argumenten: ['audit.py'], werkmap: 'data/workspace' },
  };
  const step = __projectRouteTest.autonomyContinuationStep(
    'data/workspace/audit.py',
    false,
    [written, failed],
  );
  assert.match(step, /foute_automations\.yaml/);
  assert.match(step, /exitcode 2/);

  const stale = __projectRouteTest.autonomyContinuationStep(
    'data/workspace/audit.py',
    false,
    [failed, written],
  );
  assert.doesNotMatch(stale, /foute_automations\.yaml/);
});

test('een bedoelde niet-nul exitcode telt als gecontroleerde proef', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  fs.writeFileSync(path.join(workspace, 'verwachte-exit.mjs'), 'process.exit(1);\n', 'utf8');
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['verwachte-exit.mjs'],
    werkmap: workspace,
    verwachte_exitcodes: [1],
  }, { mode: 'autonomy' }));
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.exit_code, 1);
  assert.deepEqual(result.data.expected_exit_codes, [1]);
  assert.equal(result.data.artifact_receipts.before.length, 1);
  assert.equal(result.data.artifact_receipts.before[0].sha256,
    createHash('sha256').update(fs.readFileSync(path.join(workspace, 'verwachte-exit.mjs'))).digest('hex'));
  assert.deepEqual(result.data.artifact_receipts.after, result.data.artifact_receipts.before);
});

test('programmareceipts binden bron en test aan writes, runs en finale readback', async () => {
  const directory = 'receipt-chain';
  const sourceArgs = { pad: `${directory}/reken.cjs`, inhoud: 'module.exports = 41;\n' };
  const testArgs = {
    pad: `${directory}/reken.test.cjs`,
    inhoud: "const assert = require('node:assert/strict'); assert.equal(require('./reken.cjs'), 41);\n",
  };
  const recorded = (name, args, output) => {
    const result = JSON.parse(output);
    return {
      name, args, output, ok: result.ok, verified: result.verified, changed: result.changed,
      partial: result.partial, needs_followup: result.needs_followup,
      semanticBinding: {
        understandingSha256: 'a'.repeat(64), requirementId: 'receipt-chain',
      },
    };
  };
  const sourceWrite = recorded('schrijf_bestand', sourceArgs,
    await executeTool('schrijf_bestand', sourceArgs, { mode: 'chat' }));
  const testWrite = recorded('schrijf_bestand', testArgs,
    await executeTool('schrijf_bestand', testArgs, { mode: 'chat' }));
  const priorToolResults = [sourceWrite, testWrite];
  const testRunArgs = {
    programma: 'node', argumenten: ['reken.test.cjs'], werkmap: `data/workspace/${directory}`,
  };
  const testRun = recorded('voer_uit', testRunArgs, await executeTool(
    'voer_uit', testRunArgs, { mode: 'chat', priorToolResults, timeoutMs: 30_000 },
  ));
  const sourceRunArgs = {
    programma: 'node', argumenten: ['reken.cjs'], werkmap: `data/workspace/${directory}`,
  };
  const sourceRun = recorded('voer_uit', sourceRunArgs, await executeTool(
    'voer_uit', sourceRunArgs, { mode: 'chat', priorToolResults, timeoutMs: 30_000 },
  ));
  const sourceReadArgs = { pad: `${directory}/reken.cjs` };
  const sourceRead = recorded('lees_bestand', sourceReadArgs,
    await executeTool('lees_bestand', sourceReadArgs, { mode: 'chat' }));
  const testReadArgs = { pad: `${directory}/reken.test.cjs` };
  const testRead = recorded('lees_bestand', testReadArgs,
    await executeTool('lees_bestand', testReadArgs, { mode: 'chat' }));

  const runReceipts = JSON.parse(testRun.output).data.artifact_receipts;
  assert.deepEqual(new Set(runReceipts.before.map((item) => item.path)), new Set([
    'data/workspace/receipt-chain/reken.cjs',
    'data/workspace/receipt-chain/reken.test.cjs',
  ]));
  assert.deepEqual(runReceipts.after, runReceipts.before);
  const plan = { requirementId: 'receipt-chain', args: { pad: `${directory}/reken.cjs` } };
  const missingFinalTestRead = __projectRouteTest.iterativeArtifactStatus(
    [sourceWrite, testWrite, testRun, sourceRun, sourceRead], plan,
  );
  assert.equal(missingFinalTestRead.complete, false);
  assert.equal(missingFinalTestRead.next, 'readback');
  assert.equal(missingFinalTestRead.readTarget, 'data/workspace/receipt-chain/reken.test.cjs');
  const complete = __projectRouteTest.iterativeArtifactStatus(
    [sourceWrite, testWrite, testRun, sourceRun, sourceRead, testRead], plan,
  );
  assert.equal(complete.complete, true);

  const repeatedSourceWrite = recorded('schrijf_bestand', sourceArgs,
    await executeTool('schrijf_bestand', sourceArgs, { mode: 'chat' }));
  const repeatedTestWrite = recorded('schrijf_bestand', testArgs,
    await executeTool('schrijf_bestand', testArgs, { mode: 'chat' }));
  assert.equal(repeatedSourceWrite.changed, false);
  assert.equal(repeatedTestWrite.changed, false);
  const noOpWritesDoNotInvalidateProof = __projectRouteTest.iterativeArtifactStatus(
    [
      sourceWrite, testWrite, testRun, sourceRun, sourceRead, testRead,
      repeatedSourceWrite, repeatedTestWrite,
    ],
    plan,
  );
  assert.equal(noOpWritesDoNotInvalidateProof.complete, true);

  const tamperedResult = JSON.parse(sourceRun.output);
  tamperedResult.data.artifact_receipts.before
    .find((item) => item.path.endsWith('/reken.cjs')).sha256 = '0'.repeat(64);
  const tamperedRun = { ...sourceRun, output: JSON.stringify(tamperedResult) };
  const rejected = __projectRouteTest.iterativeArtifactStatus(
    [sourceWrite, testWrite, testRun, tamperedRun, sourceRead, testRead], plan,
  );
  assert.equal(rejected.complete, false);
  assert.equal(rejected.next, 'write');
});

test('documentafronding bindt write aan een volledige hashgelijke readback', async () => {
  const args = {
    pad: 'document-receipt/resultaat.md',
    inhoud: '# Gecontroleerd resultaat\n\nAlleen aantoonbare feiten.\n',
  };
  const recorded = (name, toolArgs, output) => {
    const result = JSON.parse(output);
    return {
      name, args: toolArgs, output, ok: result.ok, verified: result.verified,
      changed: result.changed, partial: result.partial,
      needs_followup: result.needs_followup,
      semanticBinding: {
        understandingSha256: 'b'.repeat(64), requirementId: 'document-receipt',
      },
    };
  };
  const write = recorded('schrijf_bestand', args,
    await executeTool('schrijf_bestand', args, { mode: 'chat' }));
  const readArgs = { pad: args.pad };
  const read = recorded('lees_bestand', readArgs,
    await executeTool('lees_bestand', readArgs, { mode: 'chat' }));
  const plan = { requirementId: 'document-receipt', args: { pad: args.pad } };
  assert.equal(__projectRouteTest.iterativeArtifactStatus([write, read], plan).complete, true);
  assert.equal(completedArtifact([write, read], {
    requireExecution: false, path: 'data/workspace/document-receipt/resultaat.md',
  })?.path, 'data/workspace/document-receipt/resultaat.md');

  const tamperedPayload = JSON.parse(read.output);
  tamperedPayload.artifact_receipt.sha256 = '0'.repeat(64);
  const tampered = { ...read, output: JSON.stringify(tamperedPayload) };
  assert.equal(__projectRouteTest.iterativeArtifactStatus([write, tampered], plan).complete, false);
  assert.equal(completedArtifact([write, tampered], { requireExecution: false }), null);

  const mismatchedBinding = {
    ...read,
    semanticBinding: {
      understandingSha256: 'c'.repeat(64), requirementId: 'document-receipt',
    },
  };
  assert.equal(completedArtifact([write, mismatchedBinding], { requireExecution: false }), null);

  const partialArgs = { pad: args.pad, vanaf_regel: 1, aantal_regels: 1 };
  const partialRead = recorded('lees_bestand', partialArgs,
    await executeTool('lees_bestand', partialArgs, { mode: 'chat' }));
  assert.equal(JSON.parse(partialRead.output).artifact_receipt.complete_read, false);
  assert.equal(completedArtifact([write, partialRead], { requireExecution: false }), null);
});

test('childprogrammatijd blijft binnen de resterende driverdeadline', () => {
  assert.equal(boundedRuntimeMs(undefined), 120_000);
  assert.equal(boundedRuntimeMs(10_000), 9_500);
  assert.equal(boundedRuntimeMs(200), 1);
  assert.equal(boundedRuntimeMs(999_999), 120_000);
});

test('afgekorte programma-uitvoer bewaart exitbewijs en levert getypeerd vervolgwerk', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const scriptPath = path.join(workspace, 'lange-verwachte-exit.mjs');
  const write = JSON.parse(await executeTool('schrijf_bestand', {
    pad: scriptPath,
    inhoud: "process.stdout.write('x'.repeat(9001)); process.exit(1);\n",
  }, { mode: 'chat' }));
  assert.equal(write.ok, true);
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['lange-verwachte-exit.mjs'],
    werkmap: workspace,
    verwachte_exitcodes: [1],
  }, { mode: 'chat' }));
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(result.failure_class, 'output_truncated');
  assert.equal(result.retryable, false);
  assert.equal(result.effect_uncertain, false);
  assert.equal(result.data.exit_code, 1);
  assert.deepEqual(result.data.expected_exit_codes, [1]);
});

test('een verwachte exitcode maakt een gemeld ontbrekend geheim niet succesvol', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  fs.writeFileSync(
    path.join(workspace, 'ontbrekend-token.mjs'),
    "process.stderr.write('HA_TOKEN ontbreekt in de uitvoeromgeving\\n'); process.exit(2);\n",
    'utf8',
  );
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['ontbrekend-token.mjs'],
    werkmap: workspace,
    verwachte_exitcodes: [0, 1, 2],
  }, { mode: 'autonomy' }));
  assert.equal(result.ok, false);
  assert.equal(result.verified, false);
  assert.equal(result.changed, false);
  assert.match(result.error, /uitvoerfout/);
});

test('een toegestane exitcode maakt een ontbrekend programma-entrypoint niet succesvol', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'python3',
    argumenten: ['bestaat-zeker-niet.py'],
    werkmap: workspace,
    verwachte_exitcodes: [0, 2],
  }, { mode: 'autonomy' }));
  assert.equal(result.ok, false);
  assert.equal(result.verified, false);
  assert.equal(result.changed, false);
  assert.match(result.error, /entrypoint bestaat niet/);
});

test('vrij gekozen code na een document blijft open tot die code echt is uitgevoerd en gelezen', () => {
  const documentWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/route.md' } }),
    args: { pad: 'data/workspace/route.md' },
  };
  const documentRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/route.md' } };
  const scriptWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/demo.py' } }),
    args: { pad: 'data/workspace/demo.py' },
  };
  const base = [documentWrite, documentRead, scriptWrite];
  assert.equal(__projectRouteTest.buildMissionComplete(base, false), false);
  const run = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0 } }),
    args: { programma: 'python3', argumenten: ['demo.py'], werkmap: 'data/workspace' },
  };
  const scriptRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/demo.py' } };
  assert.equal(__projectRouteTest.buildMissionComplete([...base, run, scriptRead], false), true);
});

test('gevraagde tests en een echte CLI-run zijn afzonderlijke acceptatiebewijzen', () => {
  const prompt = 'Schrijf gerichte tests, voer ze uit en controleer ook één echte CLI-run.';
  const testRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stderr: 'Ran 3 tests in 0.01s\nOK' } }),
    args: { programma: 'python3', argumenten: ['test_cli.py'], werkmap: 'data/workspace' },
  };
  const helpRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: 'usage: cli.py' } }),
    args: { programma: 'python3', argumenten: ['cli.py', '--help'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, helpRun], prompt).complete, false);
  const cliRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '[{"naam":"Melk","aantal":3}]' } }),
    args: { programma: 'python3', argumenten: ['cli.py', 'een.json', 'twee.json'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, cliRun], prompt).complete, true);
  const wrapperRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '[...]', stderr: 'CLI-CONTROLE: OK' } }),
    args: { programma: 'python3', argumenten: ['voer_cli_demo.py'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, wrapperRun], prompt).complete, true);
});

test('duurzaam werk kan niet zonder bewijs als klaar worden geboekt', () => {
  const item = createWorkItem({
    title: 'Controleer een proef',
    goal: 'Een teruggelezen en gecontroleerd resultaat',
    nextStep: 'Voer de proef uit',
  });
  assert.throws(
    () => updateWorkItem(item.id, { status: 'done', result: 'klaar' }),
    /pas klaar/,
  );
  const done = updateWorkItem(item.id, {
    status: 'done',
    evidence: 'Uitvoer opnieuw gelezen: 42',
    result: 'De proef geeft 42',
    proofAccepted: true,
  });
  assert.equal(done.status, 'done');
});

test('een lege tussenstap wist eerder werkbewijs niet', () => {
  const item = createWorkItem({
    title: 'Bewijs vasthouden',
    goal: 'Bewaar het laatste echte testresultaat',
    nextStep: 'Voer de eerste proef uit',
  });
  updateWorkItem(item.id, { evidence: 'Regressietest geslaagd: 6 controles.' });
  const updated = updateWorkItem(item.id, {
    nextStep: 'Voer de volgende proef uit',
    evidence: '',
  });
  assert.equal(updated.evidence, 'Regressietest geslaagd: 6 controles.');
});

test('duurzaam werk gebruikt revisies, leases en herstelbare checkpoints', () => {
  const item = createWorkItem({
    title: 'Hervatbare flow',
    goal: 'Lever drie gecontroleerde stappen',
    nextStep: 'Voer stap één uit',
    definitionOfDone: 'Drie stappen zijn getest en teruggelezen',
    priority: 80,
  });
  const claimed = claimWorkItem(item.id, { owner: 'worker-a', leaseMs: 60_000 });
  assert.equal(claimed.lease_owner, 'worker-a');
  assert.equal(claimed.attempt_count, 1);
  assert.equal(claimWorkItem(item.id, { owner: 'worker-b', leaseMs: 60_000 }), null);
  assert.throws(() => updateWorkItem(item.id, {
    expectedRevision: claimed.revision - 1,
    currentStep: 'botsende update',
  }), /veranderde intussen/);
  const checkpoint = updateWorkItem(item.id, {
    expectedRevision: claimed.revision,
    currentStep: 'Voer stap twee uit',
    state: { stap_1: 'geslaagd' },
  });
  assert.equal(checkpoint.current_step, 'Voer stap twee uit');
  assert.deepEqual(JSON.parse(checkpoint.state_json), { stap_1: 'geslaagd' });
  releaseWorkLease(item.id, 'worker-a');
  assert.ok(claimWorkItem(item.id, { owner: 'worker-b', leaseMs: 60_000 }));
  assert.equal(recoverWorkLeases() >= 1, true);
  const recovered = listWorkItems({ statuses: ['open'], limit: 200 })
    .find((candidate) => candidate.id === item.id);
  assert.equal(recovered.lease_owner, null);
  assert.match(recovered.last_error, /procesherstart/i);
});

test('autonome programmas krijgen geen Eva-secrets mee', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const program = path.join(workspace, 'environment-check.mjs');
  fs.writeFileSync(
    program,
    "console.log(process.env.EVA_TEST_SECRET || 'afgeschermd');\n",
    'utf8',
  );
  const raw = await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['environment-check.mjs'],
    werkmap: workspace,
  }, { mode: 'chat' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.doesNotMatch(JSON.stringify(result.data), /must-not-leak/);
});

test('autonome programmas zitten in een echte mount- en procesisolatie', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const extensions = path.join(workspace, 'extensions');
  fs.mkdirSync(extensions, { recursive: true });
  fs.writeFileSync(path.join(extensions, 'integrity.txt'), 'vast\n', 'utf8');
  const program = path.join(workspace, 'sandbox-check.mjs');
  const privateSource = path.resolve('src/mind/agent.js');
  const privateEnv = path.resolve('.env');
  fs.writeFileSync(program, `
import fs from 'node:fs';
import path from 'node:path';
const probe = (target, mode) => {
  try {
    if (mode === 'write') fs.writeFileSync(target, 'escape');
    else fs.readFileSync(target, 'utf8');
    return 'accessible';
  } catch (error) { return error.code || 'blocked'; }
};
console.log(JSON.stringify({
  source: probe(${JSON.stringify(privateSource)}, 'read'),
  env: probe(${JSON.stringify(privateEnv)}, 'read'),
  docker: probe('/var/run/docker.sock', 'read'),
  outside: probe(path.resolve(process.cwd(), '..', 'escape.txt'), 'write'),
  extensions: probe(path.join(process.cwd(), 'extensions', 'integrity.txt'), 'write'),
  interfaces: fs.readdirSync('/sys/class/net').sort(),
}));
`, 'utf8');
  const raw = await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['sandbox-check.mjs'],
    werkmap: workspace,
  }, { mode: 'autonomy' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, true);
  const probe = JSON.parse(result.data.stdout);
  for (const name of ['source', 'env', 'docker']) {
    assert.notEqual(probe[name], 'accessible', `${name} ontsnapte uit de sandbox`);
  }
  assert.notEqual(probe.extensions, 'accessible', 'een programma kon een geactiveerde extensie vervangen');
  assert.equal(fs.readFileSync(path.join(extensions, 'integrity.txt'), 'utf8'), 'vast\n');
  assert.deepEqual(probe.interfaces, ['lo']);
  // De private /tmp mag beschrijfbaar zijn, maar die write mag niet op de host verschijnen.
  assert.equal(fs.existsSync(path.join(dataDir, 'escape.txt')), false);
});

test('lees- en schrijftools volgen geen symlink naar afgeschermde data', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const privateDir = path.join(dataDir, 'private');
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(privateDir, { recursive: true });
  const secretFile = path.join(privateDir, 'do-not-touch.txt');
  fs.writeFileSync(secretFile, 'blijft-ongewijzigd\n', { mode: 0o600 });
  const escape = path.join(workspace, 'escape-private');
  fs.symlinkSync(privateDir, escape, 'dir');

  const read = JSON.parse(await executeTool('lees_bestand', {
    pad: path.join(escape, 'do-not-touch.txt'),
  }));
  assert.equal(read.ok, false);
  assert.equal(read.verified, false);

  const write = JSON.parse(await executeTool('schrijf_bestand', {
    pad: path.join(escape, 'do-not-touch.txt'),
    inhoud: 'overschreven\n',
  }, { mode: 'autonomy' }));
  assert.equal(write.ok, false);
  assert.equal(write.verified, false);
  assert.equal(fs.readFileSync(secretFile, 'utf8'), 'blijft-ongewijzigd\n');
});

test('afgeschermde bestandssoorten zijn ook direct uit de werkruimte onleesbaar', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const files = ['secrets.yaml', 'herstel.db', '.env.backup'];
  for (const name of files) {
    const file = path.join(workspace, name);
    fs.writeFileSync(file, 'gevoelig\n', { mode: 0o600 });
    const result = JSON.parse(await executeTool('lees_bestand', { pad: file }));
    assert.equal(result.ok, false, name);
    assert.equal(result.verified, false, name);
  }
});

test('persistente tijdlijn- en toolreceipts redigeren actuele geheimwaarden centraal', () => {
  const secret = process.env.HA_TOKEN;
  const vaultSecret = 'vault-canary-must-not-persist';
  registerSensitiveValues([['VAULT_TEST', vaultSecret]]);
  const event = logTimeline('system', `canary ${secret}`, { nested: { secret } });
  const vaultEvent = logTimeline('system', `canary ${vaultSecret}`, { nested: { vaultSecret } });
  claimTurnToolExecution({ executionKey: 'redaction-canary', turnId: 'redaction', tool: 'probe', args: { secret } });
  completeTurnToolExecution('redaction-canary', `uitvoer ${secret}`);
  const timeline = db.prepare('SELECT content,meta FROM timeline WHERE id=?').get(event.id);
  const vaultTimeline = db.prepare('SELECT content,meta FROM timeline WHERE id=?').get(vaultEvent.id);
  const execution = db.prepare('SELECT args,output FROM turn_tool_executions WHERE execution_key=?').get('redaction-canary');
  assert.equal(JSON.stringify({ timeline, execution }).includes(secret), false);
  assert.equal(JSON.stringify(vaultTimeline).includes(vaultSecret), false);
  assert.match(timeline.content, /AFGESCHERMD:HA_TOKEN/);
  assert.match(vaultTimeline.content, /AFGESCHERMD:VAULT_TEST/);
});

test('dagbackup wordt pas na checksum en SQLite-integriteit atomair zichtbaar', async () => {
  const directory = path.join(dataDir, 'backup-contract');
  const destination = await backupData(directory);
  assert.equal(fs.existsSync(destination), true);
  assert.equal(fs.existsSync(`${destination}.sha256`), true);
  assert.equal(fs.statSync(destination).mode & 0o777, 0o600);
  assert.equal(latestDailyBackup(directory)?.path, destination);

  for (let index = 0; index < 11; index += 1) {
    const corrupt = path.join(directory, `eva-2099-01-${String(index + 1).padStart(2, '0')}-00-00-00.db`);
    fs.writeFileSync(corrupt, 'geen sqlite', { mode: 0o600 });
    fs.writeFileSync(`${corrupt}.sha256`, `${'0'.repeat(64)}  ${path.basename(corrupt)}\n`, { mode: 0o600 });
  }
  assert.equal(latestDailyBackup(directory)?.path, destination);
  await backupData(directory);
  assert.equal(fs.existsSync(destination), true, 'corrupte nieuwere namen mogen geldig herstelpunt niet evicten');
  assert.deepEqual(
    fs.readdirSync(directory).filter((name) => name.includes('.partial')),
    [],
  );
});

test('Telegram-uitvoer is terug te vinden op de duurzame deduplicatiesleutel', () => {
  archiveTelegramMessage({
    chatId: '42',
    messageId: '99',
    role: 'assistant',
    content: 'Een antwoord',
    meta: { dedupeKey: 'user-reply:turn:telegram:42:12' },
  });
  const found = telegramOutboundByDedupeKey('user-reply:turn:telegram:42:12');
  assert.equal(found?.message_id, '99');
});

test('een duurzame toolontvangst voorkomt blinde dubbele uitvoering na herstel', () => {
  const first = claimTurnToolExecution({
    executionKey: 'receipt-test', turnId: 'telegram:42:99', tool: 'bedien_huis', args: { doel: 'lamp', actie: 'uit' },
  });
  assert.equal(first.claimed, true);
  completeTurnToolExecution('receipt-test', '{"ok":true,"verified":true}');
  const replay = claimTurnToolExecution({
    executionKey: 'receipt-test', turnId: 'telegram:42:99', tool: 'bedien_huis', args: { doel: 'lamp', actie: 'uit' },
  });
  assert.equal(replay.claimed, false);
  assert.equal(replay.row.status, 'done');
  assert.equal(replay.row.output, '{"ok":true,"verified":true}');
});

test('een duurzame uitvoeringssleutel is exact aan haar semantische handeling gebonden', () => {
  const executionKey = 'semantic-receipt-test';
  const first = claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:semantic',
    tool: 'bedien_huis',
    args: { actie: 'uit', doel: 'lamp' },
  });
  assert.equal(first.claimed, true);

  const reorderedReplay = claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:semantic',
    tool: 'bedien_huis',
    args: { doel: 'lamp', actie: 'uit' },
  });
  assert.equal(reorderedReplay.claimed, false, 'objectvolgorde verandert de betekenis niet');

  assert.throws(() => claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:semantic',
    tool: 'bedien_huis',
    args: { doel: 'lamp', actie: 'aan' },
  }), /andere handeling gebonden/);
  assert.throws(() => claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:other',
    tool: 'bedien_huis',
    args: { doel: 'lamp', actie: 'uit' },
  }), /andere handeling gebonden/);
  assert.throws(() => claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:semantic',
    tool: 'spreek_in_huis',
    args: { doel: 'lamp', actie: 'uit' },
  }), /andere handeling gebonden/);
});

test('de duurzame ledger weigert achteraf gewijzigde argumenten ook vóór voltooiing', () => {
  const executionKey = 'semantic-args-tamper-test';
  const intent = {
    executionKey,
    turnId: 'telegram:42:tamper',
    tool: 'bedien_huis',
    args: { doel: 'overkapping', actie: 'uit' },
  };
  claimTurnToolExecution(intent);
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET args=? WHERE execution_key=?')
      .run(canonicalJson({ doel: 'woonkamer', actie: 'aan' }), executionKey);
  });
  assert.throws(() => claimTurnToolExecution(intent), /andere handeling|semantische hash/i);
  assert.throws(
    () => completeTurnToolExecution(executionKey, '{"ok":true}'),
    /intentierecordhash/i,
  );
});

test('redactie maakt twee verschillende ruwe handelingen niet tot dezelfde receipt', () => {
  const executionKey = 'semantic-redaction-collision-test';
  const common = {
    executionKey, turnId: 'telegram:42:redaction-collision', tool: 'probe',
  };
  const first = claimTurnToolExecution({
    ...common, args: { credential: 'password=eerste-geheim-123' },
  });
  assert.match(first.row.args, /AFGESCHERMD:SECRET/);
  assert.doesNotMatch(first.row.args, /eerste-geheim/);
  assert.throws(() => claimTurnToolExecution({
    ...common, args: { credential: 'password=tweede-geheim-456' },
  }), /andere handeling/i);
});

test('een oude niet-canonieke ledgerregel blijft expliciet onverifieerbaar', () => {
  const executionKey = 'legacy-noncanonical-args-test';
  db.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,execution_seq)
    SELECT ?,?,?,?,?,?,coalesce(max(execution_seq),0)+1 FROM main.turn_tool_executions`).run(
    executionKey,
    'telegram:42:legacy-order',
    'probe',
    JSON.stringify({ b: 2, a: 1 }),
    'started',
    new Date().toISOString(),
  );
  assert.throws(() => claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:legacy-order',
    tool: 'probe',
    args: { a: 1, b: 2 },
  }), /legacy.*onverifieerbaar/u);
  const stored = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  assert.equal(stored.args, JSON.stringify({ b: 2, a: 1 }));
  assert.equal(stored.intent_record_sha256, null);
  assert.equal(stored.execution_verification, 'legacy_unverifiable');
  assert.throws(() => claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:legacy-order',
    tool: 'probe',
    args: { a: 1, b: 3 },
  }), /andere handeling/i);
});

test('een gepland effect bindt ook understanding, requirement en capability aan zijn receipt', () => {
  const executionKey = 'semantic-plan-receipt-test';
  const binding = {
    understandingSha256: 'a'.repeat(64),
    requirementId: 'r2',
    addressesRequirementIds: ['r2'],
    basisReceiptIds: ['s1'],
    capabilityId: 'home.entity.control',
    operationId: 'set',
    role: 'primary',
    executionType: 'direct',
  };
  const first = claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:bound-plan',
    tool: 'bedien_huis',
    args: { doel: 'lamp', actie: 'uit' },
    semanticBinding: binding,
  });
  assert.equal(first.claimed, true);
  assert.deepEqual(JSON.parse(first.row.semantic_binding), binding);
  assert.equal(claimTurnToolExecution({
    executionKey,
    turnId: 'telegram:42:bound-plan',
    tool: 'bedien_huis',
    args: { actie: 'uit', doel: 'lamp' },
    semanticBinding: { ...binding },
  }).claimed, false);

  for (const changed of [
    { requirementId: 'r3' },
    { capabilityId: 'home.entity.read' },
    { operationId: 'read' },
    { understandingSha256: 'b'.repeat(64) },
    { addressesRequirementIds: ['r3'] },
    { basisReceiptIds: ['s2'] },
    { role: 'recovery' },
    { executionType: 'retry' },
  ]) {
    assert.throws(() => claimTurnToolExecution({
      executionKey,
      turnId: 'telegram:42:bound-plan',
      tool: 'bedien_huis',
      args: { doel: 'lamp', actie: 'uit' },
      semanticBinding: { ...binding, ...changed },
    }), /andere handeling gebonden|ongeldige semantische uitvoeringsbinding/);
  }
  assert.throws(() => claimTurnToolExecution({
    executionKey: 'invalid-semantic-plan-receipt',
    turnId: 'telegram:42:bound-plan',
    tool: 'bedien_huis',
    args: {},
    semanticBinding: { ...binding, understandingSha256: 'niet-een-hash' },
  }), /ongeldige semantische uitvoeringsbinding/);
  for (const [index, invalidBinding] of [
    { ...binding, addressesRequirementIds: ['r3'] },
    { ...binding, role: '' },
    { ...binding, extraAuthority: true },
    { ...binding, basisReceiptIds: ['s1', 's1'] },
  ].entries()) {
    assert.throws(() => claimTurnToolExecution({
      executionKey: `invalid-semantic-binding:${index}`,
      turnId: 'telegram:42:bound-plan',
      tool: 'bedien_huis',
      args: {},
      semanticBinding: invalidBinding,
    }), /ongeldige semantische uitvoeringsbinding/);
  }
});

test('een duurzame uitvoeringsreceipt kan niet stil naar ander bewijs worden herschreven', () => {
  const executionKey = 'completion-receipt-test';
  claimTurnToolExecution({ executionKey, turnId: 'turn:completion', tool: 'probe', args: {} });
  assert.equal(completeTurnToolExecution(executionKey, '{"ok":true}').completed, true);
  assert.equal(
    db.prepare('SELECT output_sha256 FROM turn_tool_executions WHERE execution_key=?')
      .get(executionKey).output_sha256,
    createHash('sha256').update('{"ok":true}').digest('hex'),
  );
  assert.equal(completeTurnToolExecution(executionKey, '{"ok":true}').completed, false);
  assert.throws(
    () => completeTurnToolExecution(executionKey, '{"ok":false}'),
    /conflicterende eindstatus/,
  );
  assert.throws(
    () => completeTurnToolExecution('missing-completion-receipt', '{}'),
    /onbekende duurzame tooluitvoering/,
  );
});

test('uitvoer- en materiaalhashes weigeren DB-mutatie en NULL blijft fail-closed na herstart', () => {
  const outputKey = 'completion-output-tamper-test';
  const outputIntent = {
    executionKey: outputKey, turnId: 'turn:output-tamper', tool: 'probe', args: { soort: 'output' },
  };
  claimTurnToolExecution(outputIntent);
  completeTurnToolExecution(outputKey, '{"ok":true}');
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?')
      .run('{"ok":false}', outputKey);
  });
  assert.throws(() => claimTurnToolExecution(outputIntent), /outputhash/i);

  const materialKey = 'completion-material-tamper-test';
  const materialIntent = {
    executionKey: materialKey, turnId: 'turn:material-tamper', tool: 'probe', args: { soort: 'materiaal' },
  };
  claimTurnToolExecution(materialIntent);
  completeTurnToolExecution(materialKey, JSON.stringify({
    ok: true, artifact_receipt: { sha256: 'a'.repeat(64) },
  }));
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET material_sha256=? WHERE execution_key=?')
      .run('b'.repeat(64), materialKey);
  });
  assert.throws(() => claimTurnToolExecution(materialIntent), /materiaalhash/i);

  const restartKey = 'completion-null-restart-test';
  const restartIntent = {
    executionKey: restartKey, turnId: 'turn:null-restart', tool: 'probe', args: { soort: 'restart' },
  };
  claimTurnToolExecution(restartIntent);
  completeTurnToolExecution(restartKey, '{"ok":true}');
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output_sha256=NULL WHERE execution_key=?')
      .run(restartKey);
  });
  const probe = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const { claimTurnToolExecution, db } = await import('./src/db.js');
    try {
      claimTurnToolExecution(${JSON.stringify(restartIntent)});
      db.close();
      process.exit(3);
    } catch (error) {
      db.close();
      process.exit(/outputhash/i.test(String(error?.message || '')) ? 0 : 4);
    }
  `], {
    cwd: path.resolve('.'),
    env: { ...process.env, EVA_DATA_DIR: dataDir },
    encoding: 'utf8',
  });
  assert.equal(probe.status, 0, probe.stderr || probe.stdout);
});

test('verse waarnemingen zijn append-only auditbewijs en nooit replaycache', () => {
  const binding = {
    understandingSha256: 'c'.repeat(64), requirementId: 'r1',
    addressesRequirementIds: ['r1'], basisReceiptIds: [],
    capabilityId: 'workspace.file.read', operationId: 'read',
    role: 'primary', executionType: null,
  };
  const first = recordTurnToolObservation({
    turnId: 'telegram:42:fresh-read', tool: 'lees_bestand',
    args: { pad: 'notitie.txt' }, semanticBinding: binding,
    output: '{"ok":true,"verified":true,"artifact_receipt":{"sha256":"' + 'd'.repeat(64) + '"}}',
  });
  const second = recordTurnToolObservation({
    turnId: 'telegram:42:fresh-read', tool: 'lees_bestand',
    args: { pad: 'notitie.txt' }, semanticBinding: binding,
    output: '{"ok":true,"verified":true,"artifact_receipt":{"sha256":"' + 'e'.repeat(64) + '"}}',
  });
  assert.notEqual(first.execution_key, second.execution_key);
  const rows = db.prepare(`SELECT material_sha256 FROM turn_tool_executions
    WHERE turn_id='telegram:42:fresh-read' ORDER BY rowid`).all();
  assert.deepEqual(rows.map((row) => row.material_sha256), ['d'.repeat(64), 'e'.repeat(64)]);
});

function seedVoiceRetryLedger({ conversationId, turnId, args, result }) {
  const digest = (value) => createHash('sha256').update(String(value)).digest('hex');
  const recordBody = {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256: digest(`source:${turnId}`),
  };
  logTimeline('turn_understanding', 'voicehandeling begrepen', {
    internalOnly: true, conversationId, turnId, interpretationSucceeded: true,
    understanding: {
      ...recordBody, sha256: digest(canonicalJson(recordBody)),
    },
  });
  const executionKey = `voice-retry-test:${digest(`${turnId}:${canonicalJson(args)}`)}`;
  claimTurnToolExecution({ executionKey, turnId, tool: 'spreek_in_huis', args });
  completeTurnToolExecution(executionKey, JSON.stringify(result));
  return db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key=?').get(executionKey);
}

const voiceActionResult = (overrides = {}) => ({
  contract: 'ActionResult/v1', tool: 'spreek_in_huis',
  ok: true, verified: true, changed: true, ...overrides,
});

test('VoiceRetry leidt een hashgebonden voltooid effect uit exact de nieuwste conversatiebeurt af', () => {
  const conversationId = 'voice:retry-success';
  const row = seedVoiceRetryLedger({
    conversationId, turnId: 'voice-retry-success-turn',
    args: { tekst: 'Eten is klaar.', doel: 'overkapping' }, result: voiceActionResult(),
  });
  const intent = deriveVoiceRetryIntent({
    conversationId, now: new Date(Date.parse(row.finished_ts) + 1_000),
  });
  assert.equal(intent.contract, 'VoiceRetryIntent/v1');
  assert.equal(intent.disposition, 'already_completed');
  assert.deepEqual(intent.action, { tekst: 'Eten is klaar.', doel: 'overkapping' });
  assert.match(intent.sourceSha256, /^[a-f0-9]{64}$/u);
  assert.match(intent.effectSha256, /^[a-f0-9]{64}$/u);
  const { sha256, ...body } = intent;
  assert.equal(sha256, createHash('sha256').update(canonicalJson(body)).digest('hex'));
  assert.equal(Object.isFrozen(intent), true);
  assert.equal(Object.isFrozen(intent.action), true);
  assert.equal(deriveVoiceRetryIntent({ conversationId: `${conversationId}-ander` }), null);
});

test('VoiceRetry laat alleen een expliciet veilig pre-effect netwerkfalen opnieuw proberen', () => {
  const conversationId = 'voice:retry-transient';
  const row = seedVoiceRetryLedger({
    conversationId, turnId: 'voice-retry-transient-turn', args: { tekst: 'De deur is open.' },
    result: voiceActionResult({
      ok: false, verified: false, changed: false, failure_class: 'transient',
      retryable: true, effect_uncertain: false,
    }),
  });
  const intent = deriveVoiceRetryIntent({
    conversationId, now: new Date(Date.parse(row.finished_ts) + 2_000),
  });
  assert.equal(intent.disposition, 'retryable_pre_effect');
  assert.deepEqual(intent.action, { tekst: 'De deur is open.' });
});

test('VoiceRetry weigert onzekerheid, policy, inner-ledgerargs en redactiemarkers', () => {
  const cases = [
    ['uncertain', { tekst: 'Onzeker.' }, voiceActionResult({
      ok: false, verified: false, changed: false, failure_class: 'transient',
      retryable: true, effect_uncertain: true,
    })],
    ['policy', { tekst: 'Verboden.' }, voiceActionResult({
      ok: false, verified: false, changed: false, failure_class: 'policy',
      retryable: true, effect_uncertain: false,
    })],
    ['inner', { target: 'assist_satellite.voice_patio', text: 'Inner.' }, voiceActionResult()],
    ['redacted', { tekst: 'Token [AFGESCHERMD:HA_TOKEN]' }, voiceActionResult()],
  ];
  for (const [slug, args, result] of cases) {
    const conversationId = `voice:retry-${slug}`;
    const row = seedVoiceRetryLedger({
      conversationId, turnId: `voice-retry-${slug}-turn`, args, result,
    });
    assert.equal(deriveVoiceRetryIntent({
      conversationId, now: new Date(Date.parse(row.finished_ts) + 1_000),
    }), null, slug);
  }

  const corruptConversation = 'voice:retry-corrupt-understanding';
  seedVoiceRetryLedger({
    conversationId: corruptConversation, turnId: 'voice-retry-corrupt-turn',
    args: { tekst: 'Niet uit corrupte bron.' }, result: voiceActionResult(),
  });
  const timeline = db.prepare(`SELECT id,meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.conversationId')=?
    ORDER BY id DESC LIMIT 1`).get(corruptConversation);
  const corruptMeta = JSON.parse(timeline.meta);
  corruptMeta.understanding.sourceSha256 = 'e'.repeat(64);
  db.prepare('UPDATE timeline SET meta=? WHERE id=?').run(JSON.stringify(corruptMeta), timeline.id);
  assert.equal(deriveVoiceRetryIntent({ conversationId: corruptConversation }), null);
});

test('VoiceRetry vertrouwt geen achteraf gewijzigde args of beurtbinding uit de ledger', () => {
  const argsConversation = 'voice:retry-args-tamper';
  const argsRow = seedVoiceRetryLedger({
    conversationId: argsConversation, turnId: 'voice-retry-args-origin',
    args: { tekst: 'Originele zin.', doel: 'overkapping' }, result: voiceActionResult(),
  });
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET args=? WHERE execution_key=?').run(
      canonicalJson({ tekst: 'Gewijzigde zin.', doel: 'woonkamer' }),
      argsRow.execution_key,
    );
  });
  assert.equal(deriveVoiceRetryIntent({
    conversationId: argsConversation,
    now: new Date(Date.parse(argsRow.finished_ts) + 1_000),
  }), null);

  const outputConversation = 'voice:retry-output-tamper';
  const outputRow = seedVoiceRetryLedger({
    conversationId: outputConversation, turnId: 'voice-retry-output-origin',
    args: { tekst: 'Niet uit gewijzigde uitvoer.' }, result: voiceActionResult(),
  });
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?')
      .run(JSON.stringify(voiceActionResult({ changed: false })), outputRow.execution_key);
  });
  assert.equal(deriveVoiceRetryIntent({
    conversationId: outputConversation,
    now: new Date(Date.parse(outputRow.finished_ts) + 1_000),
  }), null);

  const timeConversation = 'voice:retry-time-tamper';
  const timeRow = seedVoiceRetryLedger({
    conversationId: timeConversation, turnId: 'voice-retry-time-origin',
    args: { tekst: 'Deze tijd hoort vast te staan.' }, result: voiceActionResult({
      ok: false, verified: false, changed: false, failure_class: 'transient',
      retryable: true, effect_uncertain: false,
    }),
  });
  const forgedFinishedTs = new Date(Date.parse(timeRow.finished_ts) + 60_000).toISOString();
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET finished_ts=? WHERE execution_key=?')
      .run(forgedFinishedTs, timeRow.execution_key);
  });
  assert.equal(deriveVoiceRetryIntent({
    conversationId: timeConversation,
    now: new Date(Date.parse(forgedFinishedTs) + 1_000),
  }), null);

  const originConversation = 'voice:retry-turn-origin';
  const movedRow = seedVoiceRetryLedger({
    conversationId: originConversation, turnId: 'voice-retry-turn-origin-id',
    args: { tekst: 'Hoort alleen bij de oorsprong.' }, result: voiceActionResult(),
  });
  const victimConversation = 'voice:retry-turn-victim';
  const victimTurnId = 'voice-retry-turn-victim-id';
  const digest = (value) => createHash('sha256').update(String(value)).digest('hex');
  const recordBody = {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256: digest(`source:${victimTurnId}`),
  };
  logTimeline('turn_understanding', 'slachtofferbeurt begrepen', {
    internalOnly: true, conversationId: victimConversation, turnId: victimTurnId,
    interpretationSucceeded: true,
    understanding: { ...recordBody, sha256: digest(canonicalJson(recordBody)) },
  });
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET turn_id=? WHERE execution_key=?')
      .run(victimTurnId, movedRow.execution_key);
  });
  assert.equal(deriveVoiceRetryIntent({
    conversationId: victimConversation,
    now: new Date(Date.parse(movedRow.finished_ts) + 1_000),
  }), null);
});

test('VoiceRetry vervalt na vijf minuten en graaft niet voorbij een interveniërende understanding', () => {
  const expiredConversation = 'voice:retry-expired';
  const expired = seedVoiceRetryLedger({
    conversationId: expiredConversation, turnId: 'voice-retry-expired-turn',
    args: { tekst: 'Te oud.' }, result: voiceActionResult(),
  });
  assert.equal(deriveVoiceRetryIntent({
    conversationId: expiredConversation,
    now: new Date(Date.parse(expired.finished_ts) + 5 * 60_000 + 1),
  }), null);

  const interveningConversation = 'voice:retry-intervening';
  seedVoiceRetryLedger({
    conversationId: interveningConversation, turnId: 'voice-retry-original-turn',
    args: { tekst: 'Niet opgraven.' }, result: voiceActionResult(),
  });
  logTimeline('turn_understanding', 'nieuwe gesprekshandeling', {
    internalOnly: true, conversationId: interveningConversation,
    turnId: 'voice-retry-newer-turn', interpretationSucceeded: true,
    understanding: {
      contract: 'TurnUnderstandingRecord/v1',
      sha256: 'c'.repeat(64), sourceSha256: 'd'.repeat(64),
    },
  });
  assert.equal(deriveVoiceRetryIntent({ conversationId: interveningConversation }), null);

  const telegramConversation = 'telegram:42';
  seedVoiceRetryLedger({
    conversationId: telegramConversation, turnId: 'telegram:420:1',
    args: { tekst: 'Verkeerde prefix.' }, result: voiceActionResult(),
  });
  assert.equal(deriveVoiceRetryIntent({ conversationId: telegramConversation }), null);
});

test('camera en herinnering gebruiken de centrale Eva-woordkeuze', () => {
  assert.match(
    renderCameraNotice({
      place: 'de tuin',
      camera: 'G5 tuin',
      recognition: { recognized: true, person: 'Frank' },
    }),
    /^Frank is bij de tuin\./,
  );
  assert.equal(
    renderReminderText('Laat Jaap weten: het raam kan dicht'),
    'het raam kan dicht',
  );
});

test('een direct vervalste signaalbeoordeling krijgt geen batchauthority', async () => {
  const batch = __autonomyScheduleTest.signalBatch([{
    id: 'signal-123', ts: '2026-08-09T10:00:00.000Z', type: 'entity_state',
    key: 'sensor.achterdeur_temperature', text: 'Achterdeur Temperature: 26.4 -> 25.8',
  }]);
  const context = {
    mode: 'autonomy',
    messageContext: { pendingSignals: batch.signals, signalBatch: batch },
  };
  const empty = JSON.parse(await executeTool('signalen_afhandelen', {
    ids: ['signal-123'],
    oordeel: '',
  }, context));
  assert.equal(empty.ok, false);
  assert.equal(empty.verified, false);

  const unrelated = JSON.parse(await executeTool('signalen_afhandelen', {
    ids: ['signal-123'],
    oordeel: 'Aardappels zijn lekker.',
  }, context));
  assert.equal(unrelated.ok, false);
  assert.equal(unrelated.verified, false);

  const forged = JSON.parse(await executeTool('signalen_afhandelen', {
    dispositie: 'no_action', oordeel: 'Geen actie nodig na de actuele beoordeling.',
  }, context));
  assert.equal(forged.ok, false);
  assert.equal(forged.verified, false);
});

test('de autonomiescheduler vertrouwt alleen een receipt van exact haar eigen signaalbatch', () => {
  const batch = __autonomyScheduleTest.signalBatch([
    { id: 'signal-1', ts: '2026-08-09T02:16:15.981Z', key: 'binary_sensor.achterdeur',
      type: 'entity_state', text: 'Achterdeur: off -> on' },
    { id: 'signal-2', ts: '2026-08-09T02:16:16.981Z', key: 'sensor.regen',
      type: 'entity_state', text: 'Regen: droog -> nat' },
  ]);
  assert.equal(Object.isFrozen(batch), true);
  assert.equal(Object.isFrozen(batch.signals), true);
  assert.match(batch.sha256, /^[a-f0-9]{64}$/u);
  const output = JSON.stringify({
    contract: 'ActionResult/v1', ok: true, verified: true,
    data: { contract: 'SignalAssessmentReceipt/v1', batchId: batch.batchId,
      batchSha256: batch.sha256, acknowledgedIds: ['signal-1'] },
  });
  assert.deepEqual([...__autonomyScheduleTest.acknowledgedSignalIds([
    { name: 'signalen_afhandelen', args: { ids: ['signal-2'] }, ok: true, verified: true, output },
  ], batch)], [], 'een gedeeltelijke acknowledgement mag de batch niet verkleinen');
  const completeOutput = JSON.stringify({
    contract: 'ActionResult/v1', ok: true, verified: true,
    data: { contract: 'SignalAssessmentReceipt/v1', batchId: batch.batchId,
      batchSha256: batch.sha256, acknowledgedIds: ['signal-1', 'signal-2'] },
  });
  assert.deepEqual(__autonomyScheduleTest.acknowledgedSignalIds([
    { name: 'signalen_afhandelen', ok: true, verified: true, output: completeOutput },
  ], batch), ['signal-1', 'signal-2']);
  assert.deepEqual([...__autonomyScheduleTest.acknowledgedSignalIds([
    { name: 'signalen_afhandelen', ok: true, verified: true,
      output: output.replace(batch.sha256, '0'.repeat(64)) },
  ], batch)], [], 'een stale of vervalste batchhash mag niets verwijderen');
  assert.deepEqual([...__autonomyScheduleTest.acknowledgedSignalIds([
    { name: 'signalen_afhandelen', ok: false, verified: false, output },
  ], batch)], [], 'een mislukte beoordeling mag niets verwijderen');
  const current = [
    { ...batch.signals[0] },
    { ...batch.signals[1], text: 'Regen: nat -> hard' },
    { id: 'signal-3', ts: '2026-08-09T02:17:00.000Z', key: 'sensor.wind',
      type: 'entity_state', text: 'Wind: stil -> stevig' },
  ];
  assert.deepEqual(__autonomyScheduleTest.removeAcknowledgedSignalSnapshots(
    current, batch, ['signal-1', 'signal-2'],
  ).map((signal) => signal.id), ['signal-2', 'signal-3'],
  'alleen het exact oorspronkelijke snapshot verdwijnt; wijziging en nieuw signaal blijven');
});

test('voice-sessies hebben altijd precies een bruikbare voice-prefix', () => {
  assert.equal(normalizeVoiceConversationId('voice:', 'Lola'), 'voice:lola');
  assert.equal(normalizeVoiceConversationId('voice:voice:Jaap', 'Lola'), 'voice:jaap');
  assert.equal(normalizeVoiceConversationId('VOICE : VOICE : Jaap', 'Lola'), 'voice:jaap');
  assert.equal(normalizeVoiceConversationId('', ''), 'voice:huisgenoot');
});

test('voiceproeven dragen expliciete provenance en voeren niet de markering uit', () => {
  const parse = __voiceBridgeTest.voiceRequestInput;
  assert.deepEqual(parse({ vraag: '__eva_test__ Schakel de tv uit.' }, 'run-7'), {
    rawInput: '__eva_test__ Schakel de tv uit.',
    text: 'Schakel de tv uit.',
    syntheticTest: true,
    syntheticConversationId: 'test:run-7',
  });
  assert.deepEqual(parse({ vraag: 'Hoe gaat het?' }, 'run-8'), {
    rawInput: 'Hoe gaat het?',
    text: 'Hoe gaat het?',
    syntheticTest: false,
    syntheticConversationId: '',
  });
});

test('voicebridge verzint geen bewoner of satellite en levert geen ongebruikt luistercontract', () => {
  assert.deepEqual(__voiceBridgeTest.voiceRequestProvenance({
    sender: 'Jaap',
    satellite: 'overkapping',
  }), {
    sender: 'Huisgenoot',
    identityTrusted: false,
    satellite: '',
  });
  assert.deepEqual(__voiceBridgeTest.voiceRequestProvenance({
    sender: 'Lola',
    speaker_id: 'voiceprint-lola',
    speaker_identity_verified: true,
    satellite: 'assist_satellite.voice_patio',
  }), {
    sender: 'Lola',
    identityTrusted: true,
    satellite: 'assist_satellite.voice_patio',
  });

  const payload = __voiceBridgeTest.voiceReplyPayload({
    reply: 'Hoi.',
    question: 'Ben je daar?',
    responseTimeMs: 12.6,
    conversationId: 'voice:patio',
    satellite: 'assist_satellite.voice_patio',
  });
  assert.deepEqual(payload, {
    reply: 'Hoi.',
    ok: true,
    vraag: 'Ben je daar?',
    response_time_ms: 13,
    conversation_id: 'voice:patio',
    satellite: 'assist_satellite.voice_patio',
  });
  assert.equal('continue_listening' in payload, false);
  assert.equal('listen_timeout_ms' in payload, false);
  assert.equal('conversation_active' in payload, false);

  const automation = fs.readFileSync(
    path.join(process.cwd(), 'deploy/eva-voice-bridge-automation.yaml'),
    'utf8',
  );
  assert.match(automation, /sender:\s+Huisgenoot/);
  assert.match(automation, /\{% else %\}\s+\{\{ '' \}\}/u);
  assert.match(automation, /satellite:\s+"\{\{ resolved_satellite \| trim \}\}"/u);
  assert.doesNotMatch(automation, /continue_listening|listen_timeout_ms/);
  assert.match(automation, /trigger\.satellite_id/);
  assert.match(
    automation,
    /id:\s+superseded[\s\S]*?event_type:\s+eva_voice_query[\s\S]*?satellite:\s+"\{\{ resolved_satellite \| trim \}\}"/u,
  );
  assert.match(automation, /wait\.trigger\.id == 'reply'/u);
  assert.match(automation, /value_template:\s+"\{\{ wait\.trigger is none \}\}"/u);
  assert.match(
    automation,
    /value_template:\s+"\{\{ wait\.trigger is none \}\}"[\s\S]*?else:[\s\S]*?set_conversation_response:\s+""/u,
    'een superseded conversation-trigger moet expliciet leeg eindigen, anders zegt HA "Done"',
  );
  assert.match(automation, /timeout:\s+"00:00:35"/u);
  assert.equal(__voiceBridgeTest.voiceDeliveryWindowMs, 32_000);
  assert.ok(
    automation.indexOf('trigger.satellite_id') < automation.indexOf('trigger.device_id'),
    'de exacte Assist-satellite moet vóór de device-fallback worden gebruikt',
  );
});

test('voice-lanes gebruiken exact de satellite en nooit een conversation-id als fysieke fallback', () => {
  const lane = __voiceBridgeTest.voiceLaneKey;
  assert.equal(
    lane(' ASSIST_SATELLITE.VOICE_PATIO '),
    'voice-lane:assist_satellite.voice_patio',
  );
  assert.equal(lane('voice:wake-session-1'), 'voice-lane:unresolved');
  assert.equal(lane('voice:wake-session-2'), 'voice-lane:unresolved');
  assert.equal(lane(''), 'voice-lane:unresolved');
});

test('de publieke bus bereikt ook voor een onopgeloste lane geen voice-authority', async () => {
  const rejected = [];
  const onTimeline = (row) => {
    if (row.kind === 'voice_rejected' && row.meta?.id === 'unresolved-live-route') rejected.push(row);
  };
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error('een onopgeloste voice-query mag geen netwerkwerk starten');
  };
  bus.on('timeline', onTimeline);
  try {
    startVoiceBridge();
    bus.emit('voice_query', {
      id: 'unresolved-live-route',
      vraag: 'Zet de tv uit.',
      satellite: 'overkapping',
    });
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    bus.off('timeline', onTimeline);
    globalThis.fetch = originalFetch;
  }
  assert.equal(fetchCalls, 0);
  assert.equal(rejected.length, 0);
});

test('de publieke bus kan zonder request-id geen fysiek event of rejection minten', async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  const rejectedBefore = db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='voice_rejected'`).get().value;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error('een voice-event zonder id mag geen netwerkwerk starten');
  };
  try {
    startVoiceBridge();
    bus.emit('voice_query', {
      vraag: 'Zet de tv uit.',
      satellite: 'assist_satellite.voice_living_room',
    });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(fetchCalls, 0);
  const rejectedAfter = db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='voice_rejected'`).get().value;
  assert.equal(rejectedAfter, rejectedBefore);
});

test('de gate accepteert geen unresolved of lane-satellite-kruising', () => {
  const gate = __voiceBridgeTest.createVoiceTurnGate({ clearTimer: () => {} });
  assert.throws(
    () => gate.begin(__voiceBridgeTest.voiceLaneKey(''), 'geen-lane'),
    /voice_lane_unresolved/,
  );
  const patio = 'assist_satellite.voice_patio';
  const living = 'assist_satellite.voice_living_room';
  const laneKey = __voiceBridgeTest.voiceLaneKey(patio);
  const isCurrent = gate.begin(laneKey, 'gekruist');
  assert.equal(gate.track(
    'gekruist', laneKey, { reply: 'Nooit afleveren.' }, 'timer', living,
  ), false);
  assert.equal(isCurrent(), true, 'een deliveryfout trekt de inhoudelijke opdracht niet in');
  assert.equal(gate.acknowledge('gekruist', living), null);
  gate.finish(laneKey, 'gekruist');
  assert.equal(isCurrent(), false);
});

test('een voice-ACK hoort exact bij de resolved satellite', () => {
  const gate = __voiceBridgeTest.createVoiceTurnGate({ clearTimer: () => {} });
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  gate.begin(laneKey, 'antwoord-1');
  assert.equal(gate.track(
    'antwoord-1', laneKey, { reply: 'Hoi.' }, 'timer-1', satellite,
  ), true);
  assert.equal(gate.acknowledge('antwoord-1', 'assist_satellite.voice_living_room'), null);
  assert.equal(
    gate.acknowledge('antwoord-1', satellite).satellite,
    satellite,
  );
});

test('dezelfde satellite supersedet een oude beurt ondanks verschillende conversation-ids', async () => {
  const cleared = [];
  const sent = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    clearTimer: (timer) => cleared.push(timer),
  });
  const satellite = 'assist_satellite.voice_patio';
  const first = {
    conversationId: 'voice:wake-session-1',
    laneKey: __voiceBridgeTest.voiceLaneKey(satellite),
  };
  const second = {
    conversationId: 'voice:wake-session-2',
    laneKey: __voiceBridgeTest.voiceLaneKey(satellite),
  };
  assert.notEqual(first.conversationId, second.conversationId);
  assert.equal(first.laneKey, second.laneKey);

  const tvIsCurrent = gate.begin(first.laneKey, 'tv-uit');
  assert.equal(gate.track(
    'tv-uit', first.laneKey, { reply: 'Ik heb de tv uitgezet.' }, 'tv-timer', satellite,
  ), true);
  const heaterIsCurrent = gate.begin(second.laneKey, 'heater-aan');

  assert.equal(tvIsCurrent(), false);
  assert.equal(heaterIsCurrent(), true);
  assert.equal(await gate.deliver('tv-uit', tvIsCurrent, () => sent.push('tv')), false);
  assert.equal(gate.acknowledge('tv-uit', satellite), null);
  assert.deepEqual(cleared, ['tv-timer']);
  assert.equal(gate.track(
    'heater-aan',
    second.laneKey,
    { reply: 'De heater staat aan.' },
    'heater-timer',
    satellite,
  ), true);
  assert.equal(await gate.deliver(
    'heater-aan', heaterIsCurrent, () => sent.push('heater'),
  ), true);
  assert.deepEqual(sent, ['heater']);
  assert.equal(
    gate.acknowledge('heater-aan', satellite).result.reply,
    'De heater staat aan.',
  );
});

test('een oud resultaat dat pas na de nieuwe beurt gereed is passeert de publishgrens niet', async () => {
  const cleared = [];
  const sent = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    clearTimer: (timer) => cleared.push(timer),
  });
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  const oldIsCurrent = gate.begin(laneKey, 'oude-internetvraag');
  const newIsCurrent = gate.begin(laneKey, 'nieuw-tv-commando');

  assert.equal(gate.track(
    'nieuw-tv-commando', laneKey, { reply: 'De tv staat uit.' }, 'new-timer', satellite,
  ), true);
  assert.equal(await gate.deliver(
    'nieuw-tv-commando', newIsCurrent, () => sent.push('nieuw'),
  ), true);
  assert.equal(gate.track(
    'oude-internetvraag', laneKey, { reply: 'Oud resultaat.' }, 'old-timer', satellite,
  ), false);
  assert.equal(await gate.deliver(
    'oude-internetvraag', oldIsCurrent, () => sent.push('oud'),
  ), false);
  assert.deepEqual(sent, ['nieuw']);
  assert.equal(gate.acknowledge('oude-internetvraag', satellite), null);
  assert.equal(gate.acknowledge('nieuw-tv-commando', satellite).result.reply, 'De tv staat uit.');
  assert.deepEqual(cleared, ['old-timer', 'new-timer']);
});

test('verschillende satellites blijven onafhankelijk bij dezelfde conversation-id', async () => {
  const cleared = [];
  const sent = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    clearTimer: (timer) => cleared.push(timer),
  });
  const conversationId = 'voice:jaap';
  const patio = 'assist_satellite.voice_patio';
  const living = 'assist_satellite.voice_living_room';
  const patioLane = __voiceBridgeTest.voiceLaneKey(patio);
  const livingLane = __voiceBridgeTest.voiceLaneKey(living);
  const patioIsCurrent = gate.begin(patioLane, 'patio');
  const livingIsCurrent = gate.begin(livingLane, 'living');
  assert.equal(conversationId, 'voice:jaap');
  assert.equal(patioIsCurrent(), true);
  assert.equal(livingIsCurrent(), true);
  assert.equal(gate.track(
    'patio', patioLane, { reply: 'Buiten klaar.' }, 'patio-timer', patio,
  ), true);
  assert.equal(gate.track(
    'living', livingLane, { reply: 'Binnen klaar.' }, 'living-timer', living,
  ), true);
  assert.equal(await gate.deliver('living', livingIsCurrent, () => sent.push('living')), true);
  assert.equal(await gate.deliver('patio', patioIsCurrent, () => sent.push('patio')), true);
  assert.deepEqual(sent, ['living', 'patio']);
  assert.equal(gate.acknowledge('living', living).result.reply, 'Binnen klaar.');
  assert.equal(gate.acknowledge('patio', patio).result.reply, 'Buiten klaar.');
  assert.deepEqual(cleared, ['living-timer', 'patio-timer']);
});

test('supersession breekt een coöperatieve send af en weigert de late ACK', async () => {
  const cleared = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    clearTimer: (timer) => cleared.push(timer),
  });
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  const firstIsCurrent = gate.begin(laneKey, 'eerste');
  assert.equal(gate.track(
    'eerste', laneKey, { reply: 'Het eerste antwoord.' }, 'eerste-timer', satellite,
  ), true);
  let sendStarted;
  const started = new Promise((resolve) => { sendStarted = resolve; });
  let observedSignal;
  const offered = gate.deliver('eerste', firstIsCurrent, (signal) => new Promise((resolve) => {
    observedSignal = signal;
    sendStarted();
    signal.addEventListener('abort', () => resolve(), { once: true });
  }));
  await started;

  const secondIsCurrent = gate.begin(laneKey, 'tweede');
  assert.equal(firstIsCurrent(), false);
  assert.equal(secondIsCurrent(), true);
  assert.equal(observedSignal.aborted, true);
  assert.equal(await offered, false);
  assert.equal(gate.acknowledge('eerste', satellite), null);
  assert.deepEqual(cleared, ['eerste-timer']);
});

test('delivery-expiry breekt de send af maar supersedet de inhoudelijke beurt niet', async () => {
  let clock = 1_000;
  const cleared = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    now: () => clock,
    clearTimer: (timer) => cleared.push(timer),
  });
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  const isCurrent = gate.begin(laneKey, 'lang-werk', { deadlineAt: 2_000 });
  const result = { reply: 'Het werk is af.' };
  assert.equal(gate.track(
    'lang-werk', laneKey, result, 'deadline-timer', satellite, 2_000,
  ), true);

  let sendStarted;
  const started = new Promise((resolve) => { sendStarted = resolve; });
  let observedSignal;
  const delivery = gate.deliver('lang-werk', isCurrent, (signal) => new Promise((resolve) => {
    observedSignal = signal;
    sendStarted();
    signal.addEventListener('abort', () => resolve(false), { once: true });
  }));
  await started;
  clock = 2_000;
  assert.equal(gate.expire('lang-werk').result, result);
  assert.equal(observedSignal.aborted, true);
  assert.equal(await delivery, false);
  assert.equal(isCurrent(), true, 'de opdrachtlease staat los van de gesloten voicepipeline');
  assert.equal(gate.acknowledge('lang-werk', satellite), null);
  assert.deepEqual(cleared, ['deadline-timer']);
  gate.finish(laneKey, 'lang-werk');
  assert.equal(isCurrent(), false);
});

test('een vóór supersession bevestigde delivery blijft historisch bewijs', async () => {
  const cleared = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    clearTimer: (timer) => cleared.push(timer),
  });
  const satellite = 'assist_satellite.voice_patio';
  const laneKey = __voiceBridgeTest.voiceLaneKey(satellite);
  const firstIsCurrent = gate.begin(laneKey, 'eerste');
  const result = { turnId: 'turn-eerste', reply: 'Het eerste antwoord.' };
  assert.equal(gate.track('eerste', laneKey, result, 'eerste-timer', satellite), true);
  assert.equal(await gate.deliver('eerste', firstIsCurrent, async () => {}), true);
  const acceptedBeforeSupersession = gate.acknowledge('eerste', satellite);
  assert.equal(acceptedBeforeSupersession.result, result);
  assert.deepEqual(cleared, ['eerste-timer']);

  const secondIsCurrent = gate.begin(laneKey, 'tweede');
  assert.equal(secondIsCurrent(), true);
  assert.equal(gate.acknowledge('eerste', satellite), null);
  assert.equal(acceptedBeforeSupersession.result.reply, 'Het eerste antwoord.');
});

test('de voiceantwoordtermijn begint bij ingress en late tekst is niet verzend- of ACK-baar', async () => {
  let clock = 1_000;
  const cleared = [];
  const sent = [];
  const gate = __voiceBridgeTest.createVoiceTurnGate({
    now: () => clock,
    clearTimer: (timer) => cleared.push(timer),
  });
  const laneKey = __voiceBridgeTest.voiceLaneKey('assist_satellite.voice_patio');
  const satellite = 'assist_satellite.voice_patio';
  const lateIsCurrent = gate.begin(laneKey, 'late', { deadlineAt: 1_035 });
  clock = 1_036;
  assert.equal(lateIsCurrent(), true, 'deadline sluit delivery, niet de inhoudelijke lease');
  assert.equal(await gate.deliver('late', lateIsCurrent, () => sent.push('late')), false);
  assert.equal(gate.track(
    'late', laneKey, { reply: 'Te laat.' }, 'late-timer', satellite, 1_035,
  ), false);
  assert.deepEqual(sent, []);
  assert.deepEqual(cleared, ['late-timer']);

  clock = 2_000;
  gate.begin(laneKey, 'pending', { deadlineAt: 2_035 });
  assert.equal(gate.track(
    'pending', laneKey, { reply: 'Nog op tijd.' }, 'pending-timer', satellite, 2_035,
  ), true);
  clock = 2_035;
  assert.equal(gate.acknowledge('pending', satellite), null);
  assert.deepEqual(cleared, ['late-timer', 'pending-timer']);
});

test('de execution-driver toont alleen opaque aliassen en laat verplichting en voltooiing bij de host', async () => {
  const implementationSpec = 'Maak de parser, voer de randgevallen uit en lees de bron terug.';
  const understandingSummary = {
    contract: 'TurnUnderstanding/v1',
    intent: 'implement',
    constraints: { internet: false },
  };
  const materialize = {
    alias: 'materialiseer', description: 'Maak het afgesproken artefact.',
    parameters: { type: 'object', properties: { inhoud: { type: 'string' } }, required: ['inhoud'] },
    globalToolName: 'schrijf_bestand',
  };
  const verify = {
    alias: 'controleer', description: 'Controleer het gemaakte artefact.',
    parameters: { type: 'object', properties: { scenario: { type: 'string' } }, required: ['scenario'] },
    globalToolName: 'voer_uit',
  };
  const modelRounds = [];
  const executions = [];
  const result = await runExecutionDriver({
    implementationSpec,
    understandingSummary,
    offeredHandles: [materialize, verify],
    maxRounds: 6,
    roundTimeoutMs: 2_222,
    totalTimeoutMs: 10_000,
    rateLimitRetries: 1,
    budgetClass: 'foreground',
    obligations: ({ receipts }) => (receipts.length ? ['controleer'] : ['materialiseer']),
    done: ({ receipts }) => receipts.length === 2,
    complete: async (request) => {
      modelRounds.push(request);
      const alias = request.tools[0].name;
      return {
        output: [{ type: 'function_call', call_id: `driver-${modelRounds.length}`, name: alias, arguments: '{"inhoud":"bron"}' }],
        text: 'dit mag geen eindantwoord worden',
        toolCalls: [{ call_id: `driver-${modelRounds.length}`, name: alias, arguments: '{"inhoud":"bron"}' }],
      };
    },
    execute: async (handle, args, context) => {
      executions.push({ handle, args, context });
      assert.equal(context.contract, 'ExecutionContext/v1');
      assert.equal(context.implementationSpec, implementationSpec);
      assert.equal(Object.isFrozen(context.understandingSummary), true);
      assert.equal(Object.isFrozen(context.understandingSummary.constraints), true);
      assert.ok(context.deadlineTs > Date.now());
      assert.ok(context.timeoutMs > 0);
      return {
        contract: 'ActionResult/v1', ok: true, verified: true, changed: true,
        data: { alias: handle.alias },
      };
    },
  });

  assert.equal(result.contract, 'ExecutionTrace/v1');
  assert.equal(result.status, 'completed');
  assert.equal(result.rounds, 2);
  assert.equal(result.receipts.length, 2);
  assert.equal('text' in result, false);
  assert.deepEqual(executions.map(({ handle }) => handle), [materialize, verify]);
  assert.deepEqual(modelRounds.map(({ tools }) => tools.map(({ name }) => name)), [
    ['materialiseer'], ['controleer'],
  ]);
  assert.deepEqual(Object.keys(modelRounds[0].tools[0]).sort(), ['description', 'name', 'parameters']);
  assert.doesNotMatch(JSON.stringify(modelRounds[0].tools), /schrijf_bestand|voer_uit/);
  assert.match(modelRounds[0].instructions, /Maak de parser, voer de randgevallen uit/);
  assert.match(modelRounds[0].instructions, /"intent":"implement"/);
  assert.equal(modelRounds[0].toolChoice, 'required');
  assert.equal(modelRounds[0].rateLimitRetries, 1);
  assert.equal(modelRounds[0].budgetClass, 'foreground');
  assert.ok(modelRounds.every(({ timeoutMs }) => timeoutMs > 0 && timeoutMs <= 2_222));
});

test('een execution-modeltimeout gebruikt exact één lichtere fallback vóór één uitvoering', async () => {
  const handle = { alias: 'maak', description: 'Maak het artefact.', parameters: { type: 'object' } };
  const requests = [];
  let executions = 0;
  const result = await runExecutionDriver({
    implementationSpec: 'Maak één aantoonbaar artefact.',
    understandingSummary: { goal: 'één artefact' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', effort: 'high',
    fallbackModel: 'gpt-5.6-terra', fallbackEffort: 'medium',
    roundTimeoutMs: 120_000, totalTimeoutMs: 300_000,
    obligations: () => ['maak'], done: ({ receipts }) => receipts.length === 1,
    complete: async (request) => {
      requests.push(request);
      if (requests.length === 1) throw new Error('The operation was aborted due to timeout');
      return {
        toolCalls: [{ call_id: 'fallback-maak', name: 'maak', arguments: '{}' }],
      };
    },
    execute: async () => {
      executions += 1;
      return { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
    },
  });

  assert.equal(result.status, 'completed');
  assert.equal(result.rounds, 1);
  assert.equal(executions, 1, 'de fallback kiest alleen; de hosthandeling gebeurt exact één keer');
  assert.deepEqual(requests.map(({ model, effort }) => [model, effort]), [
    ['gpt-5.6-sol', 'high'], ['gpt-5.6-terra', 'medium'],
  ]);
  assert.ok(requests.every(({ transientRetries }) => transientRetries === 0));
  assert.equal(result.trace.some(({ type }) => type === 'model_timeout_fallback'), true);
  assert.equal(result.trace.some(({ type }) => type === 'model_fallback_recovered'), true);
});

test('twee execution-modeltimeouts stoppen getypeerd en effectvrij vóór de eerste handeling', async () => {
  const handle = { alias: 'maak', description: 'Maak het artefact.', parameters: { type: 'object' } };
  const models = [];
  let executions = 0;
  const result = await runExecutionDriver({
    implementationSpec: 'Maak één aantoonbaar artefact.',
    understandingSummary: { goal: 'één artefact' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', fallbackModel: 'gpt-5.6-terra',
    obligations: () => ['maak'], done: () => false,
    complete: async ({ model }) => {
      models.push(model);
      throw Object.assign(new Error('request timeout'), { code: 'EVA_LLM_TIMEOUT' });
    },
    execute: async () => { executions += 1; },
  });

  assert.deepEqual(models, ['gpt-5.6-sol', 'gpt-5.6-terra']);
  assert.equal(executions, 0);
  assert.equal(result.status, 'blocked');
  assert.equal(result.code, 'EVA_EXECUTION_MODEL_TIMEOUT');
  assert.equal(result.reason, 'model_timeout_routes_exhausted');
  assert.equal(result.phase, 'before_first_action');
  assert.equal(result.modelAttempts, 2);
  assert.equal(result.fallbackAttempted, true);
  assert.equal(result.fallbackFailureKind, 'timeout');
  assert.deepEqual(result.receipts, []);
  assert.match(__projectRouteTest.executionModelFailureText(result),
    /primaire en alternatieve uitvoerroute.*vóór de eerste handeling.*niets uitgevoerd of gewijzigd/iu);
});

test('yield en een modelprotocolfout openen nooit de execution-fallback', async () => {
  const handle = { alias: 'maak', description: 'Maak het artefact.', parameters: { type: 'object' } };
  let yielding = false;
  let yieldCalls = 0;
  const yielded = await runExecutionDriver({
    implementationSpec: 'Maak één aantoonbaar artefact.',
    understandingSummary: { goal: 'één artefact' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', fallbackModel: 'gpt-5.6-terra',
    shouldYield: () => yielding, obligations: () => ['maak'], done: () => false,
    complete: async () => {
      yieldCalls += 1;
      yielding = true;
      throw new Error('The operation was aborted due to timeout');
    },
    execute: async () => assert.fail('yield stopt vóór een hosthandeling'),
  });
  assert.equal(yielded.status, 'yielded');
  assert.equal(yieldCalls, 1);
  assert.equal(yielded.trace.some(({ type }) => type === 'model_timeout_fallback'), false);

  let protocolCalls = 0;
  const protocol = await runExecutionDriver({
    implementationSpec: 'Maak één aantoonbaar artefact.',
    understandingSummary: { goal: 'één artefact' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', fallbackModel: 'gpt-5.6-terra',
    obligations: () => ['maak'], done: () => false,
    complete: async () => { protocolCalls += 1; return { toolCalls: [] }; },
    execute: async () => assert.fail('een protocolfout bereikt geen hosthandeling'),
  });
  assert.equal(protocol.status, 'protocol_error');
  assert.equal(protocolCalls, 1);
});

test('de totale execution-deadline opent geen tweede modelaanroep en een toolfout geen modelretry', async () => {
  const handle = { alias: 'maak', description: 'Maak het artefact.', parameters: { type: 'object' } };
  let deadlineCalls = 0;
  const deadline = await runExecutionDriver({
    implementationSpec: 'Maak één aantoonbaar artefact.',
    understandingSummary: { goal: 'één artefact' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', fallbackModel: 'gpt-5.6-terra',
    totalTimeoutMs: 50, roundTimeoutMs: 100,
    obligations: () => ['maak'], done: () => false,
    complete: async () => {
      deadlineCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 75));
      throw new Error('The operation was aborted due to timeout');
    },
    execute: async () => assert.fail('de deadline ligt vóór de eerste hosthandeling'),
  });
  assert.equal(deadlineCalls, 1);
  assert.equal(deadline.code, 'EVA_EXECUTION_MODEL_TIMEOUT');
  assert.equal(deadline.fallbackAttempted, false);
  assert.equal(deadline.fallbackFailureKind, 'deadline');

  let modelCalls = 0;
  let toolCalls = 0;
  await assert.rejects(runExecutionDriver({
    implementationSpec: 'Voer één hosthandeling uit.',
    understandingSummary: { goal: 'één handeling' }, offeredHandles: [handle],
    model: 'gpt-5.6-sol', fallbackModel: 'gpt-5.6-terra',
    obligations: () => ['maak'], done: () => false,
    complete: async () => {
      modelCalls += 1;
      return { toolCalls: [{ call_id: 'tool-timeout', name: 'maak', arguments: '{}' }] };
    },
    execute: async () => {
      toolCalls += 1;
      throw new Error('tool timeout');
    },
  }), /tool timeout/iu);
  assert.equal(modelCalls, 1);
  assert.equal(toolCalls, 1);
});

test('de execution-driver verlengt alleen bij nieuw bewijs en stopt bij stagnatie', async () => {
  const handle = {
    alias: 'werk', description: 'Leg één controleerbare stap vast.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: { stap: { type: 'integer' } }, required: ['stap'],
    },
  };
  let calls = 0;
  const progressing = await runExecutionDriver({
    implementationSpec: 'Maak vijf aantoonbaar verschillende stappen af.',
    understandingSummary: { goal: 'vijf stappen' }, offeredHandles: [handle],
    baseRounds: 2, maxRounds: 8, progressExtensionRounds: 2,
    obligations: () => ['werk'], done: ({ receipts }) => receipts.length === 5,
    complete: async () => {
      calls += 1;
      const args = JSON.stringify({ stap: calls });
      return { toolCalls: [{ call_id: `p-${calls}`, name: 'werk', arguments: args }] };
    },
    execute: async (_offered, args) => ({
      contract: 'ActionResult/v1', ok: true, verified: true, changed: true,
      needs_followup: false, partial: false, data: { stap: args.stap },
    }),
  });
  assert.equal(progressing.status, 'completed');
  assert.equal(progressing.rounds, 5);
  assert.ok(progressing.trace.some((event) => event.type === 'lease_extended'));

  const stagnant = await runExecutionDriver({
    implementationSpec: 'Herhaal niet zonder nieuw bewijs.',
    understandingSummary: { goal: 'één nieuw feit' }, offeredHandles: [handle],
    baseRounds: 2, maxRounds: 8, progressExtensionRounds: 2,
    obligations: () => ['werk'], done: () => false,
    complete: async () => ({
      toolCalls: [{ call_id: 'zelfde', name: 'werk', arguments: '{"stap":1}' }],
    }),
    execute: async () => ({
      contract: 'ActionResult/v1', ok: true, verified: true, changed: false,
      needs_followup: false, partial: false, data: { stap: 1 },
    }),
  });
  assert.equal(stagnant.status, 'exhausted');
  assert.equal(stagnant.reason, 'progress_lease_expired');
  assert.equal(stagnant.rounds, 3);
});

test('de execution-driver bindt siblingdoelen exact en laat alleen declaratieve materiaalinhoud vrij', async () => {
  const write = {
    alias: 'materialiseer', description: 'Schrijf het exact uitgegeven bestand.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: {
        pad: { type: 'string', minLength: 1 },
        inhoud: { type: 'string', minLength: 1 },
        toelichting: { type: 'string' },
      },
      required: ['pad', 'inhoud'],
    },
  };
  let offeredSchema = null;
  let executions = 0;
  const exact = await runExecutionDriver({
    implementationSpec: 'Schrijf de gerichte proef.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [write], maxRounds: 1,
    obligations: () => [{
      alias: write.alias,
      expectedInput: { pad: 'demo/proef.cjs' },
      materializableFields: ['inhoud'],
    }],
    done: ({ receipts }) => receipts.length === 1,
    complete: async ({ tools }) => {
      offeredSchema = tools[0].parameters;
      return {
        output: [], text: '',
        toolCalls: [{
          call_id: 'exact-write', name: write.alias,
          arguments: JSON.stringify({ pad: 'demo/proef.cjs', inhoud: 'assert.ok(true);' }),
        }],
      };
    },
    execute: async () => {
      executions += 1;
      return { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
    },
  });
  assert.equal(exact.status, 'completed');
  assert.equal(executions, 1);
  assert.deepEqual(Object.keys(offeredSchema.properties).sort(), ['inhoud', 'pad']);
  assert.deepEqual(offeredSchema.properties.pad.enum, ['demo/proef.cjs']);
  assert.equal(Object.hasOwn(offeredSchema.properties.inhoud, 'enum'), false);
  assert.deepEqual(new Set(offeredSchema.required), new Set(['pad', 'inhoud']));

  let protocolAttempts = 0;
  const corrected = await runExecutionDriver({
    implementationSpec: 'Schrijf de gerichte proef.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [write], maxRounds: 2,
    obligations: () => [{
      alias: write.alias,
      expectedInput: { pad: 'demo/proef.cjs' },
      materializableFields: ['inhoud'],
    }],
    done: ({ receipts }) => receipts.length === 1,
    complete: async () => {
      protocolAttempts += 1;
      return {
        output: [], text: '',
        toolCalls: [{
          call_id: `corrected-write-${protocolAttempts}`, name: write.alias,
          arguments: JSON.stringify({
            pad: protocolAttempts === 1 ? 'demo/andere-fixture.cjs' : 'demo/proef.cjs',
            inhoud: protocolAttempts === 1 ? 'nee' : 'assert.ok(true);',
          }),
        }],
      };
    },
    execute: async () => {
      executions += 1;
      return { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
    },
  });
  assert.equal(corrected.status, 'completed');
  assert.equal(corrected.rounds, 2);
  assert.equal(corrected.receipts.length, 1);
  assert.equal(executions, 2, 'een afwijkende fixture bereikt de effectcallback niet');
  assert.equal(corrected.recovery, null, 'protocolfeedback wordt geen inspect/repair-herstelstaat');
  assert.equal(corrected.trace.some((item) => (
    item.type === 'protocol_feedback' && item.reason === 'obligation_arguments_mismatch'
  )), true);

  let rejectedCalls = 0;
  const rejected = await runExecutionDriver({
    implementationSpec: 'Schrijf de gerichte proef.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [write], maxRounds: 3,
    obligations: () => [{
      alias: write.alias,
      expectedInput: { pad: 'demo/proef.cjs' },
      materializableFields: ['inhoud'],
    }],
    done: () => false,
    complete: async () => {
      rejectedCalls += 1;
      return {
        output: [], text: '',
        toolCalls: [{
          call_id: `rejected-write-${rejectedCalls}`, name: write.alias,
          arguments: JSON.stringify({ pad: 'demo/nog-steeds-verkeerd.cjs', inhoud: 'nee' }),
        }],
      };
    },
    execute: async () => {
      executions += 1;
      return { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
    },
  });
  assert.equal(rejected.status, 'protocol_error');
  assert.equal(rejected.reason, 'obligation_arguments_mismatch');
  assert.equal(rejected.rounds, 2, 'één correctiekans, daarna stopt dezelfde protocolfout');
  assert.equal(rejected.receipts.length, 0);
  assert.equal(executions, 2);
  assert.equal(rejected.recovery, null);
});

test('samengestelde execution-binding blijft uit provider-enum en wordt exact door de host bewaakt', async () => {
  const run = {
    alias: 'execution_proof_run', description: 'Voer het exact gebonden programma uit.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: {
        programma: { type: 'string', enum: ['node', 'python3'] },
        argumenten: { type: 'array', items: { type: 'string' } },
        werkmap: { type: 'string', minLength: 1 },
      },
      required: ['programma', 'argumenten', 'werkmap'],
    },
  };
  const expectedInput = {
    programma: 'node', argumenten: ['demo.mjs', '--controle'], werkmap: 'crash',
  };
  let attempts = 0;
  let executions = 0;
  const result = await runExecutionDriver({
    implementationSpec: 'Voer de reeds geschreven demo met de gebonden argumenten uit.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [run], maxRounds: 2,
    obligations: () => [{ alias: run.alias, expectedInput, materializableFields: [] }],
    done: ({ receipts }) => receipts.length === 1,
    complete: async ({ tools, input }) => {
      attempts += 1;
      const schema = tools[0].parameters;
      assert.deepEqual(schema.properties.programma.enum, ['node']);
      assert.equal(Object.hasOwn(schema.properties.argumenten, 'enum'), false,
        'een array als enumlid maakt het provider-schema ongeldig');
      assert.deepEqual(schema.properties.argumenten.items.enum, ['demo.mjs', '--controle']);
      assert.equal(schema.properties.argumenten.minItems, 2);
      assert.equal(schema.properties.argumenten.maxItems, 2);
      assert.match(schema.properties.argumenten.description,
        /\["demo\.mjs","--controle"\]/u,
        'het model krijgt de samengestelde waarde als instructie, niet als ongeldige enum');
      if (attempts === 2) {
        const feedback = input.find((item) => item.type === 'function_call_output');
        assert.deepEqual(JSON.parse(feedback.output).expectedInput, expectedInput,
          'de ene no-effect correctieronde noemt de exacte hostbinding');
      }
      return {
        output: [], text: '',
        toolCalls: [{
          call_id: `composite-${attempts}`, name: run.alias,
          arguments: JSON.stringify({
            ...expectedInput,
            argumenten: attempts === 1 ? ['ander.mjs'] : expectedInput.argumenten,
          }),
        }],
      };
    },
    execute: async (_handle, args) => {
      executions += 1;
      assert.deepEqual(args, expectedInput);
      return { contract: 'ActionResult/v1', ok: true, verified: true, changed: false };
    },
  });
  assert.equal(result.status, 'completed');
  assert.equal(result.rounds, 2);
  assert.equal(executions, 1, 'de afwijkende array bereikt de effectcallback niet');
  assert.equal(result.trace.some((item) => (
    item.type === 'protocol_feedback' && item.reason === 'obligation_arguments_mismatch'
  )), true);
});

test('de execution-driver voert nooit meer dan één modeltoolcall per ronde uit', async () => {
  let executions = 0;
  const handle = { alias: 'stap', description: 'Eén stap.', parameters: { type: 'object' } };
  const result = await runExecutionDriver({
    implementationSpec: 'Doe één stap.',
    understandingSummary: { intent: 'execute' },
    offeredHandles: [handle],
    obligations: () => ['stap'],
    done: () => false,
    execute: async () => {
      executions += 1;
      return { contract: 'ActionResult/v1', ok: true, verified: true };
    },
    complete: async () => ({
      output: [], text: '',
      toolCalls: [
        { call_id: 'a', name: 'stap', arguments: '{}' },
        { call_id: 'b', name: 'stap', arguments: '{}' },
      ],
    }),
  });
  assert.equal(result.status, 'protocol_error');
  assert.equal(result.reason, 'exactly_one_tool_call_required');
  assert.equal(executions, 0);
  assert.equal(result.receipts.length, 0);
});

test('alleen getypeerde ActionResult-velden sturen herstel en onzeker effect blijft zichtbaar', async () => {
  const mutate = { alias: 'muteer', description: 'Voer de mutatie uit.', parameters: { type: 'object' } };
  const readback = { alias: 'lees_terug', description: 'Lees de actuele toestand terug.', parameters: { type: 'object' } };
  const recoveryContexts = [];
  const modelOffers = [];
  const result = await runExecutionDriver({
    implementationSpec: 'Pas de toestand één keer aan.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [mutate, readback],
    maxRounds: 2,
    obligations: (context) => {
      recoveryContexts.push(context.recovery);
      return context.recovery?.effect_uncertain ? ['lees_terug'] : ['muteer'];
    },
    done: ({ receipts }) => receipts.length === 2,
    complete: async ({ tools }) => {
      modelOffers.push(tools.map(({ name }) => name));
      const alias = tools[0].name;
      return {
        output: [{ type: 'function_call', call_id: alias, name: alias, arguments: '{}' }],
        text: '', toolCalls: [{ call_id: alias, name: alias, arguments: '{}' }],
      };
    },
    execute: async (handle) => handle === mutate
      ? {
        contract: 'ActionResult/v1', ok: false, verified: false,
        failure_class: 'declared_route_failure', retryable: false, effect_uncertain: true,
        error: 'timeout 429 permission denied not found',
      }
      : { contract: 'ActionResult/v1', ok: true, verified: true, changed: false },
  });
  const declaredRecovery = {
    contract: 'ExecutionRecovery/v1', receipt: 0, alias: 'muteer',
    failure_class: 'declared_route_failure', retryable: false, effect_uncertain: true,
  };
  assert.deepEqual(recoveryContexts, [null, declaredRecovery]);
  assert.deepEqual(modelOffers, [['muteer'], ['lees_terug']]);
  assert.equal(result.status, 'completed');
  assert.equal(result.receipts.length, 2);
  assert.equal(result.receipts[0].recoveryReceipt, null);
  assert.equal(result.receipts[1].recoveryReceipt, 0);
  assert.deepEqual(result.recovery, declaredRecovery);
  assert.equal('error' in recoveryContexts[1], false);
});

test('executionadapter vult ontbrekende typed failurevelden uitsluitend uit het hostreceipt', () => {
  const raw = {
    contract: 'ActionResult/v1', ok: true, verified: true, changed: false,
    partial: true, needs_followup: true, data: { truncated: true },
  };
  const recorded = {
    output: JSON.stringify(raw), failure_class: 'output_truncated',
    retryable: false, effect_uncertain: false,
  };
  assert.deepEqual(executionActionResult(recorded), {
    ...raw, failure_class: 'output_truncated', retryable: false, effect_uncertain: false,
  });
  assert.deepEqual(executionActionResult({
    ...recorded,
    output: JSON.stringify({ ...raw, retryable: true }),
  }), {
    contract: 'InvalidActionResult/v1', ok: false, verified: false,
    error: 'conflicting_action_result_metadata:retryable',
  });
  const untyped = executionActionResult({ output: JSON.stringify(raw) });
  assert.equal(Object.hasOwn(untyped, 'failure_class'), false,
    'zonder getypeerd hostbewijs blijft de driverreceipt bewust ongeldig');
});

test('geslaagd herstel sluit alleen de gebonden failure en keert daarna terug naar proof-readback', async () => {
  const aliases = [
    'execution_proof_run', 'execution_recovery_inspect', 'execution_recovery_repair',
    'execution_recovery_retest', 'execution_proof_readback',
  ];
  const handles = aliases.map((alias) => ({
    alias, description: alias, parameters: { type: 'object', additionalProperties: false },
  }));
  const selected = [];
  const recoveries = [];
  const result = await runExecutionDriver({
    implementationSpec: 'Herstel de mislukte proef en lees daarna het eindartefact terug.',
    understandingSummary: { authority: 'already_bound' }, offeredHandles: handles,
    maxRounds: 8, recoveryResolved: __projectRouteTest.iterativeRecoveryResolved,
    obligations: (context) => {
      recoveries.push(context.recovery);
      const retested = context.receipts.some((receipt) => (
        receipt.alias === 'execution_recovery_retest' && receipt.result?.verified === true
      ));
      const obligation = __projectRouteTest.iterativeExecutionObligation(
        context, { next: retested ? 'readback' : 'run' },
      );
      return [`execution_${obligation.lane}_${obligation.type}`];
    },
    done: ({ receipts }) => receipts.at(-1)?.alias === 'execution_proof_readback',
    complete: async ({ tools }) => {
      const alias = tools[0].name;
      selected.push(alias);
      return { toolCalls: [{ call_id: `${alias}-${selected.length}`, name: alias, arguments: '{}' }] };
    },
    execute: async (handle) => handle.alias === 'execution_proof_run'
      ? {
        contract: 'ActionResult/v1', ok: false, verified: false,
        needs_followup: true, partial: false, failure_class: 'test_failure',
        retryable: false, effect_uncertain: true,
      }
      : {
        contract: 'ActionResult/v1', ok: true, verified: true,
        changed: handle.alias === 'execution_recovery_repair',
      },
  });

  assert.equal(result.status, 'completed');
  assert.deepEqual(selected, aliases);
  assert.deepEqual(result.receipts.map(({ recoveryReceipt }) => recoveryReceipt), [null, 0, 0, 0, null]);
  assert.deepEqual(recoveries.map((recovery) => recovery?.alias || null), [
    null, 'execution_proof_run', 'execution_proof_run', 'execution_proof_run', null,
  ]);
  assert.equal(result.recovery, null);
  assert.equal(result.trace.some(({ type }) => type === 'recovery_resolved'), true);
  assert.equal(__projectRouteTest.iterativeRecoveryResolved({
    recovery: { receipt: 0, alias: 'execution_proof_run', effect_uncertain: true },
    receipt: result.receipts[1],
  }), false, 'alleen inspecteren verdoezelt een mislukte run niet');
  assert.equal(__projectRouteTest.iterativeRecoveryResolved({
    recovery: { receipt: 0, alias: 'execution_proof_run', effect_uncertain: true },
    receipt: result.receipts[3],
  }), true, 'een gebonden geslaagde retest sluit de run-failure wel');
});

test('veel verschillende iteratieve reads verbruiken niet de globale toolnaamlimiet', () => {
  let globalAttempts = 0;
  const signatures = new Map();
  for (let index = 0; index < 20; index += 1) {
    const signature = `lees_bestand:{"pad":"artefact-${index}.js"}`;
    signatures.set(signature, Number(signatures.get(signature) || 0) + 1);
    const state = __projectRouteTest.globalToolNameAttempt(globalAttempts, true, 8);
    globalAttempts = state.attempts;
    assert.equal(state.exceeded, false);
  }
  assert.equal(globalAttempts, 0);
  assert.ok([...signatures.values()].every((attempts) => attempts === 1),
    'de bestaande per-signaturegrens blijft iedere concrete read afzonderlijk begrenzen');
  const ninthOrdinaryRead = Array.from({ length: 9 }).reduce(
    (state) => __projectRouteTest.globalToolNameAttempt(state.attempts, false, 8),
    { attempts: 0, exceeded: false },
  );
  assert.equal(ninthOrdinaryRead.exceeded, true, 'buiten de driver blijft de globale grens actief');
});

test('een andere route heropent een non-retryable handle alleen voor dezelfde herstelreceipt', async () => {
  const repair = {
    alias: 'repareer', description: 'Herstel het artefact.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  };
  const inspect = {
    alias: 'inspecteer', description: 'Inspecteer de actuele toestand.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  };
  const aliases = [];
  let repairAttempts = 0;
  const result = await runExecutionDriver({
    implementationSpec: 'Herstel pas opnieuw nadat dezelfde mislukte poging gericht is geïnspecteerd.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [repair, inspect],
    maxRounds: 5,
    obligations: ({ receipts }) => receipts.length === 0
      ? [repair.alias]
      : receipts.length === 1 ? [inspect.alias] : [repair.alias],
    done: ({ receipts }) => receipts.length === 3,
    complete: async ({ tools }) => {
      const alias = tools[0].name;
      aliases.push(alias);
      return {
        output: [{ type: 'function_call', call_id: `${alias}-${aliases.length}`, name: alias, arguments: '{}' }],
        text: '',
        toolCalls: [{ call_id: `${alias}-${aliases.length}`, name: alias, arguments: '{}' }],
      };
    },
    execute: async (handle) => {
      if (handle === inspect) {
        return { contract: 'ActionResult/v1', ok: true, verified: true, changed: false };
      }
      repairAttempts += 1;
      return repairAttempts === 1
        ? {
          contract: 'ActionResult/v1', ok: false, verified: false,
          needs_followup: true, partial: false,
          failure_class: 'attempt_limit', retryable: false, effect_uncertain: false,
        }
        : { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
    },
  });

  assert.equal(result.status, 'completed');
  assert.deepEqual(aliases, ['repareer', 'inspecteer', 'repareer']);
  assert.deepEqual(result.receipts.map((receipt) => receipt.recoveryReceipt), [null, 0, 0]);
});

test('een non-retryable executionreceipt kan niet dezelfde handle blijven aanbieden', async () => {
  const repair = {
    alias: 'execution_recovery_repair',
    description: 'Herstel het artefact.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  };
  let modelCalls = 0;
  let executions = 0;
  const result = await runExecutionDriver({
    implementationSpec: 'Herstel het artefact gecontroleerd.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [repair],
    maxRounds: 8,
    obligations: () => ['execution_recovery_repair'],
    done: () => false,
    complete: async () => {
      modelCalls += 1;
      return {
        output: [{
          type: 'function_call', call_id: `repair-${modelCalls}`,
          name: repair.alias, arguments: '{}',
        }],
        text: '',
        toolCalls: [{ call_id: `repair-${modelCalls}`, name: repair.alias, arguments: '{}' }],
      };
    },
    execute: async () => {
      executions += 1;
      return {
        contract: 'ActionResult/v1', ok: false, verified: false,
        needs_followup: true, partial: false,
        failure_class: 'attempt_limit', retryable: false, effect_uncertain: false,
        error: 'De poginglimiet is bereikt.',
      };
    },
  });

  assert.equal(result.status, 'blocked');
  assert.equal(result.reason, 'non_retryable_obligation');
  assert.equal(result.receipts.length, 1);
  assert.equal(modelCalls, 1);
  assert.equal(executions, 1);
  assert.equal(result.trace.some((item) => (
    item.type === 'route_rejected' && item.alias === repair.alias
  )), true);
});

test('ongetypeerde fouttekst wordt geen herstelclassificatie en yield stopt vóór modelwerk', async () => {
  const handle = { alias: 'probeer', description: 'Probeer.', parameters: { type: 'object' } };
  const base = {
    implementationSpec: 'Probeer gecontroleerd.',
    understandingSummary: { intent: 'execute' },
    offeredHandles: [handle],
    obligations: () => ['probeer'],
    done: () => false,
  };
  let modelCalls = 0;
  const yielded = await runExecutionDriver({
    ...base,
    shouldYield: () => true,
    execute: async () => ({ contract: 'ActionResult/v1', ok: true, verified: true }),
    complete: async () => { modelCalls += 1; return { output: [], text: '', toolCalls: [] }; },
  });
  assert.equal(yielded.status, 'yielded');
  assert.equal(modelCalls, 0);

  const invalid = await runExecutionDriver({
    ...base,
    execute: async () => 'Fout bij probeer: timeout 429',
    complete: async () => ({
      output: [{ type: 'function_call', call_id: 'invalid', name: 'probeer', arguments: '{}' }],
      text: '', toolCalls: [{ call_id: 'invalid', name: 'probeer', arguments: '{}' }],
    }),
  });
  assert.equal(invalid.status, 'protocol_error');
  assert.equal(invalid.reason, 'invalid_action_result');
  assert.equal(invalid.recovery, null);
  assert.equal(invalid.receipts.length, 0);
});

test('een onbekende execution-verplichting faalt vóór de modelcall', async () => {
  let modelCalls = 0;
  await assert.rejects(runExecutionDriver({
    implementationSpec: 'Voer alleen een uitgegeven handle uit.',
    understandingSummary: { authority: 'already_bound' },
    offeredHandles: [{ alias: 'bekend', description: 'Bekende stap.' }],
    obligations: () => ['onbekend'],
    done: () => false,
    execute: async () => ({ contract: 'ActionResult/v1', ok: true, verified: true }),
    complete: async () => { modelCalls += 1; return { output: [], toolCalls: [] }; },
  }), /Niet-aangeboden execution-handle verplicht gesteld: onbekend/);
  assert.equal(modelCalls, 0);
});

test('de echte antwoordgrens weigert ruwe modeltaal en houdt haar gegronde terugval', async () => {
  const userText = 'Kijk hoeveel bestanden er nu in je werkruimte staan.';
  fs.mkdirSync(path.join(dataDir, 'workspace'), { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'workspace', 'grounded-proef.txt'), 'bewijs\n');
  const originalFetch = globalThis.fetch;
  let modelCalls = 0;
  globalThis.fetch = async () => {
    modelCalls += 1;
    if (modelCalls >= 2) return completedModelResponse([{
      type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: '{"stdout":"interne uitvoer"}' }],
    }]);
    return completedModelResponse([{
      type: 'function_call', call_id: 'understand-grounded-fallback',
      name: 'commit_turn_understanding',
      arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: {
          statement: 'Bekijk de huidige werkruimte.',
          outcome: 'Het actuele aantal bestanden is gecontroleerd.',
          beneficiary: 'requester',
        },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{
          capabilityId: 'workspace.file.list', operationId: 'list', polarity: 'request',
          semanticInput: { pad: 'data/workspace' },
          sourceSpans: [{ start: 0, end: userText.length }],
        }],
        requestedEffects: ['read'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' },
        proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }),
    }]);
  };
  try {
    const turn = await handleMessageDetailed({
      channel: 'test', sender: 'Jaap', text: userText,
      messageContext: { identityTrusted: true, conversationId: 'test:grounded-fallback' },
    });
    assert.equal(modelCalls, 3,
      'na de eerste afgekeurde formulering volgt exact één bewijsgebonden herstelpoging');
    assert.equal(turn.reply, turn.tools[0].evidence.summary);
    assert.match(turn.reply, /^\d+ \S+ gelezen\.$/);
    assert.doesNotMatch(turn.reply, /stdout|interne uitvoer|\{/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
