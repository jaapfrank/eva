import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import test, { after } from 'node:test';
import {
  issueMonitoringContract,
  monitoringContractReadBindings,
  normalizeMonitoringPendingDeliveryReference,
} from '../src/mind/monitoringcontract.js';

const dataDir = fs.mkdtempSync('/tmp/bottie-telegram-temperature-monitoring-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.ALLOW_HOME_CONTROL = 'true';
process.env.OPENAI_API_KEY = 'test-only';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
await import('./telegram-test-composition.js');
await import('./homeassistant-test-composition.js');
await import('./agent-test-composition.js');

const { config } = await import('../src/config.js');
const { db, logTimeline, upsertPerson } = await import('../src/db.js');
const { __projectRouteTest, runAutonomousTurn } = await import('../src/mind/agent.js');
const { bindHomeContextEntityReferences } = await import('../src/mind/home.js');
const { goalEpisodeById } = await import('../src/mind/portfolio.js');
const { executeTool, registerMessenger } = await import('../src/mind/tools.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const telegramModule = await import('../src/integrations/telegram.js');
const { sendToFamily } = telegramModule;
const {
  prepareAuthenticatedTelegramTurnForTest,
  setBotForTest,
} = telegramModule.__telegramTransportTest;

upsertPerson('Jaap', { telegram_id: '424242' });
let telegramSequence = 90_000;

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function functionResponse(name, argumentsValue, callId) {
  return completedModelResponse([{
    type: 'function_call', call_id: callId, name,
    arguments: JSON.stringify(argumentsValue),
  }]);
}

function textResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text }],
  }]);
}

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonical(value[key])}`
  )).join(',')}}`;
}

function digest(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : canonical(value))
    .digest('hex');
}

function rehashPending(value) {
  const { pendingSha256: _pendingSha256, ...body } = value;
  body.factSetSha256 = digest(body.factRefs);
  return { ...body, pendingSha256: digest(body) };
}

function pendingWithoutRetryMutation(value) {
  const copy = JSON.parse(JSON.stringify(value));
  delete copy.attempts;
  delete copy.pendingSha256;
  return copy;
}

after(() => {
  registerMessenger(null);
  setBotForTest(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('Telegram-vraag ontdekt twee temperaturen, bewaakt stil en bezorgt de eerste koudere-buitenmeting exact eenmaal', async () => {
  const chatId = '424242';
  const messageId = '4358';
  const conversationId = `telegram:${chatId}`;
  const requestText = 'geef aan wanneer ik de ramen meot openen als het buiten kouder is dan binnen.';
  const terminalMessage = 'De afgesproken voorwaarde is bereikt. Weer thuis is 18 °C en Woonkamer is 19 °C.';
  const conditionStart = requestText.indexOf('wanneer');
  const observationStart = requestText.indexOf('het buiten');
  const firstWake = new Date(Date.now() + 5 * 60_000).toISOString();
  let homeStates = [{
    entity_id: 'weather.forecast_thuis', state: 'sunny',
    last_updated: new Date().toISOString(),
    attributes: {
      friendly_name: 'Weer thuis', temperature: 20, temperature_unit: '°C',
    },
  }, {
    entity_id: 'climate.woonkamer', state: 'heat',
    last_updated: new Date().toISOString(),
    attributes: {
      friendly_name: 'Woonkamer', current_temperature: 19, temperature_unit: '°C',
    },
  }];
  let understandingCalls = 0;
  let stepCalls = 0;
  let outcomeCalls = 0;
  let sends = 0;
  let composerCalls = 0;
  let homeEntityRestCalls = 0;
  let failNextSend = false;
  let delivered = null;
  let monitoringInstructionSeen = false;

  const mappedGuards = __projectRouteTest.monitoringFactBindingSpecs({
    monitoring: {
      sourceEntityReadRequirementIds: ['r1', 'r2'],
      sourceReferenceTexts: ['woonkamer', 'buiten'],
    },
    conditions: [{
      statement: 'De woonkamer is warmer dan 21 graden.', role: 'guard',
      requirementIds: ['r1'],
    }, {
      statement: 'Buiten is kouder dan 18 graden.', role: 'guard',
      requirementIds: ['r2'],
    }],
    actions: [{
      requirementId: 'r1', capabilityId: 'home.entity.read', operationId: 'read',
      sourceSpans: [{ start: 0, end: 10 }],
    }, {
      requirementId: 'r2', capabilityId: 'home.entity.read', operationId: 'read',
      sourceSpans: [{ start: 11, end: 17 }],
    }],
  }, 'woonkamer buiten');
  assert.deepEqual(mappedGuards?.map((item) => ({
    requirementId: item.requirementId, condition: item.condition, operand: item.operand,
  })), [{
    requirementId: 'r1', condition: 'De woonkamer is warmer dan 21 graden.',
    operand: 'subject',
  }, {
    requirementId: 'r2', condition: 'Buiten is kouder dan 18 graden.',
    operand: 'subject',
  }], 'iedere readbinding erft uitsluitend haar eigen guardcondition');

  __cameraTriggerTest.setCachedStates(homeStates.map((item) => ({
    ...item,
    attributes: {
      ...item.attributes,
      ...(item.entity_id.startsWith('weather.') ? { temperature: 77 }
        : { current_temperature: 88 }),
    },
  })));
  __cameraTriggerTest.setCacheConnection(true);

  assert.equal(bindHomeContextEntityReferences([{
    requirementId: 'r2', sourceText: 'buiten',
  }], [{ entity_id: 'weather.noord', name: 'Weer noord' },
    { entity_id: 'weather.zuid', name: 'Weer zuid' }]), null,
  'twee gelijkwaardige HA-kandidaten worden niet op volgorde gekozen');
  assert.equal(bindHomeContextEntityReferences([{
    requirementId: 'r2', sourceText: 'buiten',
  }, { requirementId: 'r3', sourceText: 'buiten' }], [{
    entity_id: 'weather.forecast_thuis', name: 'Weer thuis',
  }]), null, 'twee meetrollen mogen niet stilzwijgend dezelfde entity delen');
  assert.deepEqual(bindHomeContextEntityReferences([{
    requirementId: 'r2', sourceText: 'het buiten',
  }, {
    requirementId: 'r3', sourceText: 'binnen',
  }], [{
    entity_id: 'weather.forecast_thuis', name: 'Forecast Thuis', state: 'rainy',
  }, {
    entity_id: 'climate.home', name: 'Home', state: 'heat',
  }, {
    entity_id: 'climate.woonkamer', name: 'Woonkamer', state: 'heat',
  }, {
    entity_id: 'climate.badkamer', name: 'Badkamer', state: 'off',
  }]), [{ requirementId: 'r2', entityId: 'weather.forecast_thuis' },
    { requirementId: 'r3', entityId: 'climate.home' }],
  'een hele-woningthermostaat is de unieke algemene binnenbron; kamers blijven alternatieven');

  setBotForTest({ api: { sendMessage: async (targetChatId, text, options = {}) => ({
    chat: { id: targetChatId }, message_id: ++telegramSequence,
    date: 1_786_020_000 + telegramSequence, text,
    ...(options.reply_parameters?.message_id ? {
      reply_to_message: { message_id: options.reply_parameters.message_id },
    } : {}),
  }) } });
  registerMessenger(async (text, recipient, options) => {
    if (failNextSend) {
      failNextSend = false;
      return { ok: false, accepted: false, needs_followup: true,
        failure_class: 'transient', retryable: true };
    }
    sends += 1;
    delivered = { text, recipient, options };
    return sendToFamily(text, recipient, options);
  });

  const startUnderstanding = {
    dialogueAct: 'command',
    goal: {
      statement: 'Bewaak de verhouding tussen buiten- en binnentemperatuur.',
      outcome: 'Jaap krijgt bericht zodra het buiten kouder is dan binnen.',
      beneficiary: 'requester',
    },
    conditions: [{
      statement: 'Buiten is kouder dan binnen.', role: 'guard', state: 'required',
      sourceSpans: [{ start: conditionStart, end: requestText.length - 1 }],
      requirementIds: ['r2', 'r3'], afterRequirementIds: [],
    }],
    provenance: { sourceSpans: [{ start: 0, end: requestText.length }], contextHashes: [] },
    confidence: 0.99,
    actions: [{
      capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
      semanticInput: { query: 'actuele buiten- en binnentemperatuur met exacte entity-id' },
      sourceSpans: [{ start: observationStart, end: requestText.length - 1 }],
    }, {
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: '<buitentemperatuur_resultaat>' },
      sourceSpans: [{ start: observationStart, end: requestText.indexOf(' kouder') }],
    }, {
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: '<binnentemperatuur_resultaat>' },
      sourceSpans: [{ start: requestText.indexOf('binnen'), end: requestText.length - 1 }],
    }, {
      capabilityId: 'work.episode.manage', operationId: 'start', polarity: 'request',
      semanticInput: {
        titel: 'Buiten kouder dan binnen',
        doel: 'Vergelijk verse buiten- en binnentemperaturen en bericht Jaap zodra buiten kouder is.',
        volgende_stap: 'Lees buiten- en binnentemperatuur opnieuw en meld Jaap zodra buiten kouder is dan binnen.',
        definitie_klaar: 'De eerste geverifieerde koudere-buitenmeting is eenmaal aan Jaap bezorgd.',
        fase: 'Wacht op de volgende temperatuurmeting.',
        hervatten_na: firstWake,
        afleveren_aan: 'Jaap',
        toestand_json: '{}',
      },
      sourceSpans: [{ start: 0, end: requestText.length }],
    }],
    requestedEffects: ['read', 'persistent'], deniedEffects: [],
    temporal: { mode: 'now', startSourceSpans: [] },
    durability: 'episode', delivery: { mode: 'later', target: 'requester' },
    proofNeeds: [], ambiguities: [],
    firstDecision: {
      kind: 'schedule', requirementIds: ['r1', 'r2', 'r3', 'r4'],
      replyDraft: 'Ik vergelijk ze en geef het door zodra het buiten kouder is dan binnen.',
    },
  };
  const resumeUnderstanding = {
    dialogueAct: 'continue',
    goal: {
      statement: 'Hervat de opgeslagen temperatuurmonitoring.',
      outcome: 'Beoordeel de twee verse temperaturen.', beneficiary: 'requester',
    },
    conditions: [], provenance: { sourceSpans: [], contextHashes: [] }, confidence: 0.99,
    actions: [], requestedEffects: [], deniedEffects: [],
    temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
    delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
    firstDecision: {
      kind: 'reply', requirementIds: [], replyDraft: 'Ik beoordeel de opgeslagen temperatuurmonitoring.',
    },
  };

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    if (target.startsWith(config.haUrl)) {
      if (target.endsWith('/api/states')) {
        return new Response(JSON.stringify(homeStates), {
          status: 200, headers: { 'content-type': 'application/json' },
        });
      }
      homeEntityRestCalls += 1;
      const entityId = decodeURIComponent(target.split('/api/states/')[1] || '');
      const state = homeStates.find((item) => item.entity_id === entityId);
      return new Response(JSON.stringify(state || {}), {
        status: state ? 200 : 404, headers: { 'content-type': 'application/json' },
      });
    }

    const body = JSON.parse(String(options.body || '{}'));
    const structuredTool = body.tools?.[0]?.name;
    if (structuredTool === 'commit_turn_understanding') {
      understandingCalls += 1;
      monitoringInstructionSeen ||= /Dit is monitoring/u.test(String(body.instructions || ''));
      return functionResponse('commit_turn_understanding',
        understandingCalls === 1 ? startUnderstanding : resumeUnderstanding,
        `temperature-understanding-${understandingCalls}`);
    }
    if (structuredTool === 'commit_turn_step') {
      stepCalls += 1;
      const payload = JSON.parse(body.input[0].content[0].text);
      const requirementId = payload.original.openRequirementIds[0];
      const entityId = requirementId === 'r2'
        ? 'weather.forecast_thuis' : 'climate.woonkamer';
      const basis = payload.evidence.find((item) => item.ok && item.verified
        && item.candidates.some((candidate) => candidate.value === entityId));
      assert.ok(basis, JSON.stringify(payload.evidence));
      return functionResponse('commit_turn_step', {
        kind: 'execute', addressesRequirementIds: [requirementId],
        basisReceiptIds: [basis.id], reasonCode: 'investigate',
        action: {
          capabilityId: 'home.entity.read', operationId: 'read',
          semanticInput: { entityId },
        },
      }, `temperature-step-${stepCalls}`);
    }
    if (structuredTool === 'commit_monitoring_outcome') {
      outcomeCalls += 1;
      const outcomePayload = JSON.parse(String(body.input?.[0]?.content?.[0]?.text || '{}'));
      const evidence = JSON.stringify(outcomePayload);
      assert.equal(outcomePayload.monitoring.definitionOfDone, requestText);
      assert.ok(outcomePayload.monitoring.conditions.includes('Buiten is kouder dan binnen.'));
      assert.equal(Object.hasOwn(outcomePayload, 'householdContext'), false);
      assert.doesNotMatch(String(body.instructions || ''), /includeAllMemberDetails|huishoudroster/iu);
      assert.match(evidence, /weather\.forecast_thuis/u);
      assert.match(evidence, /climate\.woonkamer/u);
      assert.deepEqual(Object.keys(JSON.parse(body.tools[0].parameters
        ? JSON.stringify(body.tools[0].parameters.properties) : '{}')).sort(),
      ['factIds', 'terminal', 'verdict']);
      const readings = new Map(outcomePayload.facts.map((fact) => [
        fact.entityId, fact,
      ]));
      assert.equal(readings.get('climate.woonkamer')?.sourceField, 'current_temperature');
      assert.equal(readings.get('climate.woonkamer')?.value, 19);
      assert.equal(readings.get('climate.woonkamer')?.unit, '°C');
      const factIds = outcomePayload.facts.map((fact) => fact.factId);
      if (readings.get('weather.forecast_thuis')?.value === 20) {
        return functionResponse('commit_monitoring_outcome', {
          verdict: 'silent', terminal: false, factIds,
        }, 'temperature-silent');
      }
      assert.equal(readings.get('weather.forecast_thuis')?.value, 18);
      return functionResponse('commit_monitoring_outcome', {
        verdict: 'notify', terminal: true, factIds,
      }, 'temperature-notify');
    }
    assert.deepEqual(body.tools || [], []);
    composerCalls += 1;
    return textResponse(outcomeCalls >= 2
      ? terminalMessage
      : 'Ik vergelijk ze en geef het door zodra het buiten kouder is dan binnen.');
  };

  try {
    const cachedProbe = JSON.parse(await executeTool('ha_lees', {
      entity_id: 'weather.forecast_thuis',
    }, { mode: 'chat', sender: 'Jaap' }));
    assert.equal(cachedProbe.data.temperature, 77,
      'een gewone niet-monitoringread mag de verbonden adaptercache behouden');
    assert.equal(homeEntityRestCalls, 0,
      'de gewone cache-hit opent geen REST entity-read');
    const startTurn = await prepareAuthenticatedTelegramTurnForTest({
      ctx: {
        chat: { id: Number(chatId) },
        from: { id: Number(chatId), first_name: 'Jaap' },
        message: {
          message_id: Number(messageId), date: 1_786_000_000, text: requestText,
        },
      },
      sender: 'Jaap', text: requestText,
    });
    assert.equal(monitoringInstructionSeen, true,
      JSON.stringify({ reply: startTurn.reply, tools: startTurn.tools,
        failureStage: startTurn.failureStage, error: startTurn.error }));
    const discoveryReads = startTurn.tools.filter((item) => item.name === 'huis_status');
    assert.equal(discoveryReads.length > 0, true, JSON.stringify(startTurn));
    assert.equal(Object.hasOwn(discoveryReads[0].args, 'historie'), false,
      'een conditionele monitoringdiscovery is een actuele snapshot, geen logboekvraag');
    assert.equal(discoveryReads.length, 1,
      JSON.stringify(startTurn));
    assert.equal(startTurn.tools.filter((item) => item.name === 'ha_lees').length, 2,
      JSON.stringify(startTurn));
    assert.equal(stepCalls, 0,
      'unieke geverifieerde HA-kandidaten worden zonder extra modelrondes gebonden');
    assert.equal(startTurn.reply,
      'Ik houd dit nu in de gaten en geef het je door zodra de afgesproken voorwaarde is bereikt.',
    `de startbeurt bevestigt alleen de duurzame monitor en loopt niet vooruit op haar terminale oordeel: ${JSON.stringify(startTurn)}`);

    const bound = db.prepare(`SELECT w.*,e.id episode_id,e.state episode_state,
      e.origin_kind,e.origin_conversation_id,e.origin_message_id,e.requester,
      e.delivery_policy,e.delivery_target
      FROM eva_work_items w JOIN eva_goal_episodes e ON e.work_item_id=w.id
      WHERE e.origin_conversation_id=? AND e.origin_message_id=?`).get(conversationId, messageId);
    assert.ok(bound, JSON.stringify(startTurn));
    const contract = JSON.parse(bound.state_json).monitoring;
    assert.equal(contract.contract, 'MonitoringContract/v2');
    assert.match(contract.contractSha256, /^[a-f0-9]{64}$/u);
    assert.equal(contract.presentation.contract, 'MonitoringPresentationContext/v1');
    assert.equal(contract.presentation.audience, 'requester');
    assert.equal(contract.delivery.contract, 'MonitoringDeliveryBinding/v1');
    assert.equal(contract.delivery.route, 'direct_telegram');
    assert.equal(contract.delivery.broadcast, false);
    assert.equal(contract.delivery.recipientResolution, 'none');
    assert.match(contract.delivery.originBindingSha256, /^[a-f0-9]{64}$/u);
    assert.equal(contract.definitionOfDone, requestText);
    assert.equal(contract.discoveryQuery,
      'actuele buiten- en binnentemperatuur met exacte entity-id');
    const contractBindings = monitoringContractReadBindings(contract);
    assert.deepEqual(contractBindings.map((item) => item.entityId).sort(), [
      'climate.woonkamer', 'weather.forecast_thuis',
    ]);
    assert.equal(new Set(contractBindings.map((item) => item.requirementId)).size, 2);
    assert.ok(contractBindings.every((item) => (
      item.contract === 'MonitoringFactBinding/v1'
      && /^[a-f0-9]{64}$/u.test(item.bindingSha256)
    )));
    assert.equal(bound.origin_kind, 'user');
    assert.equal(bound.requester, 'Jaap');
    assert.equal(bound.delivery_policy, 'required');
    assert.equal(bound.delivery_target, 'Jaap');
    assert.equal(bound.episode_state, 'waiting');
    const lifecycle = JSON.parse(bound.state_json).monitoring_lifecycle;
    assert.equal(lifecycle.contract, 'MonitoringLifecycleBinding/v1');
    assert.equal(lifecycle.episodeId, bound.episode_id);
    assert.equal(lifecycle.workId, bound.id);
    assert.equal(lifecycle.originAuthority.executionKey.startsWith('turn-understanding:'), true);
    assert.match(lifecycle.originAuthority.authoritySha256, /^[a-f0-9]{64}$/u);

    const silent = await runAutonomousTurn({
      reason: 'temperatuurmeetmoment', workId: bound.id, goalEpisodeId: bound.episode_id,
    });
    assert.equal(silent.tools.filter((item) => item.name === 'ha_lees').length, 2,
      JSON.stringify(silent));
    assert.equal(silent.tools.some((item) => item.name === 'stuur_bericht'), false);
    assert.equal(sends, 0);
    assert.equal(goalEpisodeById(bound.episode_id).state, 'waiting');

    const preparedKey = `episode:${bound.episode_id}:monitoring-final`;
    const probePinnedDeliveryBoundary = async ({
      label, text = terminalMessage, dedupeKey = preparedKey,
      activeWorkId = bound.id, episodeState = 'delivery_pending',
    }) => {
      db.exec('SAVEPOINT monitoring_delivery_boundary');
      const sendsBefore = sends;
      try {
        const work = db.prepare('SELECT state_json FROM eva_work_items WHERE id=?').get(bound.id);
        const state = JSON.parse(work.state_json);
        state.pending_delivery = {
          text: terminalMessage,
          facts: 'Twee verse temperatuurmetingen voldoen aan de opgeslagen voorwaarde.',
          recipient: null,
          preparedAt: new Date().toISOString(),
          attempts: 0,
          dedupeKey: preparedKey,
        };
        db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
          .run(JSON.stringify(state), bound.id);
        db.prepare('UPDATE eva_goal_episodes SET state=? WHERE id=?')
          .run(episodeState, bound.episode_id);
        logTimeline('chat_out', `Eva: ${text}`, {
          sender: 'Eva', autonomous: true, familyVisible: true,
        });
        const rejected = JSON.parse(await executeTool('stuur_bericht', { tekst: text }, {
          mode: 'autonomy', priorToolResults: [],
          activeWorkId, activeGoalEpisodeId: bound.episode_id,
          preparedDeliveryDedupeKey: dedupeKey,
        }));
        assert.equal(rejected.ok, false, label);
        assert.equal(sends, sendsBefore, `${label}: transport mag niet beginnen`);
      } finally {
        db.exec('ROLLBACK TO monitoring_delivery_boundary');
        db.exec('RELEASE monitoring_delivery_boundary');
      }
    };
    await probePinnedDeliveryBoundary({
      label: 'een verse suffix is niet de gepinde delivery-key',
      dedupeKey: `${preparedKey}:forged`,
    });
    await probePinnedDeliveryBoundary({
      label: 'andere tekst hoort niet bij de gepinde delivery',
      text: 'Een andere waarschuwing over dezelfde temperatuurmeting.',
    });
    await probePinnedDeliveryBoundary({
      label: 'een ander actief werk-id draagt de gepinde delivery niet',
      activeWorkId: Number(bound.id) + 100_000,
    });
    await probePinnedDeliveryBoundary({
      label: 'een niet-pending episode draagt de gepinde delivery niet',
      episodeState: 'waiting',
    });

    logTimeline('chat_out', `Eva: ${terminalMessage}`, {
      sender: 'Eva', autonomous: true, familyVisible: true,
    });
    homeStates = homeStates.map((item) => item.entity_id === 'weather.forecast_thuis'
      ? {
          ...item,
          attributes: { ...item.attributes, temperature: 18 },
          last_updated: new Date().toISOString(),
        }
      : item);
    failNextSend = true;
    const restReadsBeforeNotify = homeEntityRestCalls;
    const notify = await runAutonomousTurn({
      reason: 'temperatuurmeetmoment', workId: bound.id, goalEpisodeId: bound.episode_id,
    });
    assert.equal(notify.tools.filter((item) => item.name === 'ha_lees').length, 2,
      JSON.stringify(notify));
    assert.equal(homeEntityRestCalls - restReadsBeforeNotify, 2,
      'de gebonden monitorcycle omzeilt de cache voor iedere exacte bron eenmaal');
    assert.equal(notify.tools.filter((item) => item.name === 'stuur_bericht').length, 1,
      JSON.stringify({ notify, outcomeCalls, sends,
        stored: db.prepare('SELECT state_json,last_error FROM eva_work_items WHERE id=?')
          .get(bound.id) }));
    assert.equal(sends, 0, 'een geweigerde transportpoging blijft als sealed pending staan');
    assert.equal(goalEpisodeById(bound.episode_id).state, 'delivery_pending');
    const pendingState = JSON.parse(db.prepare(
      'SELECT state_json FROM eva_work_items WHERE id=?',
    ).get(bound.id).state_json);
    const pending = pendingState.pending_delivery;
    assert.equal(pending.contract, 'MonitoringPendingDelivery/v2');
    assert.equal(pending.monitoringContractSha256, contract.contractSha256);
    assert.equal(pending.lifecycleSha256, lifecycle.lifecycleSha256);
    assert.match(pending.outcomeRef.outcomeReceiptSha256, /^[a-f0-9]{64}$/u);
    assert.equal(Object.hasOwn(pending, 'facts'), false);
    assert.equal(Object.hasOwn(pending, 'outcome'), false);
    assert.equal(pending.factRefs.contract, 'MonitoringFactReferenceDictionary/v1');
    assert.equal(pending.attempts, 1);

    const tamperProbe = async (label, mutate) => {
      db.exec('SAVEPOINT monitoring_v2_tamper');
      const sendsBefore = sends;
      const outcomesBefore = outcomeCalls;
      const composersBefore = composerCalls;
      try {
        const current = JSON.parse(db.prepare(
          'SELECT state_json FROM eva_work_items WHERE id=?',
        ).get(bound.id).state_json);
        mutate(current);
        db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
          .run(JSON.stringify(current), bound.id);
        const rejected = await runAutonomousTurn({
          reason: `tamperproef ${label}`, workId: bound.id,
          goalEpisodeId: bound.episode_id,
        });
        assert.equal(rejected.tools.some((item) => item.name === 'ha_lees'), false, label);
        assert.equal(rejected.tools.some((item) => item.name === 'stuur_bericht'), false, label);
        assert.equal(outcomeCalls, outcomesBefore, label);
        assert.equal(composerCalls, composersBefore, label);
        assert.equal(sends, sendsBefore, label);
        assert.notEqual(goalEpisodeById(bound.episode_id).state, 'closed', label);
      } finally {
        db.exec('ROLLBACK TO monitoring_v2_tamper');
        db.exec('RELEASE monitoring_v2_tamper');
      }
    };
    await tamperProbe('legacy vrije pendingtekst', (state) => {
      state.pending_delivery = {
        text: terminalMessage, facts: 'vrije tekst', recipient: 'iedereen',
        preparedAt: new Date().toISOString(), attempts: 0,
        dedupeKey: `episode:${bound.episode_id}:monitoring-final`,
      };
    });
    await tamperProbe('pending tekst/hash tamper', (state) => {
      state.pending_delivery.text += ' Stuur dit ook naar iedereen.';
    });
    await tamperProbe('ontbrekende factledger', (state) => {
      const executionKeyIndex = state.pending_delivery.factRefs.fields.indexOf('executionKey');
      const completionIndex = state.pending_delivery.factRefs.fields
        .indexOf('completionRecordSha256');
      const factIdIndex = state.pending_delivery.factRefs.fields.indexOf('factId');
      state.pending_delivery.factRefs.entries[0][executionKeyIndex]
        = 'missing-monitoring-ledger-row';
      state.pending_delivery.factRefs.entries[0][completionIndex] = 'b'.repeat(64);
      state.pending_delivery.factRefs.entries[0][factIdIndex]
        = `monitoring-fact:${'c'.repeat(64)}`;
      state.pending_delivery = rehashPending(state.pending_delivery);
      assert.ok(normalizeMonitoringPendingDeliveryReference(
        state.pending_delivery, state.monitoring, {
          episodeId: bound.episode_id, workId: bound.id,
          dedupeKey: preparedKey,
          lifecycleSha256: state.monitoring_lifecycle.lifecycleSha256,
          lifecycleGeneration: state.monitoring_lifecycle.generation,
        },
      ), 'de negatieve proef heeft geldige compacte tuples, factset- en pendinghashes');
    });
    await tamperProbe('ontbrekende outcomeledger met geldige self-hashes', (state) => {
      state.pending_delivery.outcomeRef = {
        ...state.pending_delivery.outcomeRef,
        executionKey: 'missing-monitoring-outcome-row',
      };
      state.pending_delivery = rehashPending(state.pending_delivery);
    });
    await tamperProbe('zelfgeldig maar ander monitoringcontract', (state) => {
      const current = state.monitoring;
      const forged = issueMonitoringContract({
        sourceUnderstandingSha256: current.sourceUnderstandingSha256,
        conditions: current.conditions,
        temporalCondition: current.temporalCondition,
        definitionOfDone: `${current.definitionOfDone} gewijzigd`,
        cadenceMs: current.cadenceMs, endsAt: current.endsAt,
        discoveryQuery: current.discoveryQuery, readBindings: current.readBindings,
        createdAt: current.createdAt,
        requesterLabel: current.presentation.requesterLabel,
        sourceTextSha256: current.presentation.sourceTextSha256,
        deliveryRequired: true,
        originBindingSha256: current.delivery.originBindingSha256,
      });
      assert.ok(forged);
      state.monitoring = forged;
    });
    await tamperProbe('legacy monitoringcontract', (state) => {
      state.monitoring = { contract: 'MonitoringContract/v1', endsAt: null };
    });

    failNextSend = true;
    const failedRetry = await runAutonomousTurn({
      reason: 'herstart sealed monitoringaflevering met tijdelijke transportfout',
      workId: bound.id, goalEpisodeId: bound.episode_id,
    });
    assert.equal(failedRetry.tools.some((item) => item.name === 'ha_lees'), false,
      'een pending retry leest Home Assistant niet opnieuw');
    assert.equal(failedRetry.tools.filter((item) => item.name === 'stuur_bericht').length, 1);
    const retriedPending = JSON.parse(db.prepare(
      'SELECT state_json FROM eva_work_items WHERE id=?',
    ).get(bound.id).state_json).pending_delivery;
    assert.equal(retriedPending.attempts, pending.attempts + 1);
    assert.notEqual(retriedPending.pendingSha256, pending.pendingSha256);
    assert.deepEqual(pendingWithoutRetryMutation(retriedPending),
      pendingWithoutRetryMutation(pending),
      'retry bewaart preparedAt, expiresAt, dedupe, tekst, renderer, refs en authority exact');

    const resumedDelivery = await runAutonomousTurn({
      reason: 'herstart sealed monitoringaflevering',
      workId: bound.id, goalEpisodeId: bound.episode_id,
    });
    assert.equal(resumedDelivery.tools.some((item) => item.name === 'ha_lees'), false,
      'sealed restart leest Home Assistant niet opnieuw');
    assert.equal(resumedDelivery.tools.filter((item) => item.name === 'stuur_bericht').length, 1);
    assert.equal(sends, 1);
    assert.equal(delivered.recipient, null,
      'directe monitoring gebruikt geen naamlookup als routeauthority');
    assert.equal(delivered.options.dedupeKey,
      `prepared:episode:${bound.episode_id}:monitoring-final`);
    assert.equal(delivered.options.directReply, true);
    assert.equal(delivered.options.directReplyChatId, chatId);
    assert.equal(delivered.options.originPrincipal, 'telegram:424242');
    assert.equal(delivered.options.broadcast, false);
    assert.equal(composerCalls, 0,
      'monitoring gebruikt geen vrije presentatie- of composer-call');
    assert.equal(goalEpisodeById(bound.episode_id).state, 'closed');
    const deliveryReceipt = db.prepare(`SELECT idempotency_key FROM eva_delivery_receipts
      WHERE episode_id=?`).get(bound.episode_id);
    assert.equal(deliveryReceipt.idempotency_key,
      `prepared:episode:${bound.episode_id}:monitoring-final`);
    assert.equal(db.prepare('SELECT COUNT(*) count FROM eva_delivery_receipts WHERE episode_id=?')
      .get(bound.episode_id).count, 1);

    await runAutonomousTurn({
      reason: 'dezelfde temperatuurhervatting opnieuw aangeboden',
      workId: bound.id, goalEpisodeId: bound.episode_id,
    });
    assert.equal(sends, 1, 'replay mag geen tweede bericht bezorgen');
    assert.equal(db.prepare('SELECT COUNT(*) count FROM eva_delivery_receipts WHERE episode_id=?')
      .get(bound.episode_id).count, 1);
    assert.equal(goalEpisodeById(bound.episode_id).state, 'closed');
  } finally {
    __cameraTriggerTest.setCacheConnection(false);
    __cameraTriggerTest.setCachedStates([]);
    globalThis.fetch = originalFetch;
  }
});
