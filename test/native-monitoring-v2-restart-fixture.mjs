import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';

const [phase, dataDir, clockIso = null] = process.argv.slice(2);
const tamperKind = String(phase || '').startsWith('tamper:')
  ? String(phase).slice('tamper:'.length) : null;
if (!['prepare-pending', 'prepare-waiting', 'prepare-discovery-waiting',
  'prepare-hinted-pending', 'prepare-outcome-retry-pending',
  'prepare-scheduler-retry', 'prepare-scheduler-no-window',
  'scheduler-crossing-recover', 'scheduler-crossing-error-recover', 'scheduler-recover',
  'scheduler-guard-recover', 'scheduler-overrule-recover',
  'startup-inspect',
  'recover', 'recover-fail', 'recover-race-state',
  'recover-multichunk-race', 'receipt-closure-race', 'receipt-wrong-route-proof',
  'accept-transport-crash', 'recover-accepted'].includes(phase)
  && !tamperKind
  || !dataDir) throw new Error('native monitoring-v2 fixture mist fase of datamap');

const NativeDate = Date;
let clockMs = clockIso == null ? NativeDate.now() : NativeDate.parse(clockIso);
if (clockIso != null) {
  if (!Number.isFinite(clockMs) || new NativeDate(clockMs).toISOString() !== clockIso) {
    throw new Error('native monitoring-v2 fixture mist een canonieke klok');
  }
  globalThis.Date = class TestDate extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [clockMs])); }
    static now() { return clockMs; }
  };
}
const setClock = (value) => {
  const next = Number(value);
  if (!Number.isFinite(next)) throw new Error('ongeldige native testklok');
  clockMs = next;
};

process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.ALLOW_HOME_CONTROL = 'true';
process.env.OPENAI_API_KEY = 'native-monitoring-v2-test-only';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-monitoring-v2-test';
process.env.HA_URL = 'http://native-monitoring-ha.invalid';
process.env.HA_TOKEN = 'native-monitoring-ha-token';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
if (['scheduler-crossing-recover', 'scheduler-crossing-error-recover'].includes(phase)) {
  process.env.EVA_AUTONOMY_TURN_MINUTES = '15';
}
if (phase === 'recover-multichunk-race') {
  process.env.EVA_TEST_TELEGRAM_SAFE_TEXT_LIMIT = '40';
}

await import('./telegram-test-composition.js');
await import('./homeassistant-test-composition.js');
await import('./agent-test-composition.js');

const { bus } = await import('../src/bus.js');
const {
  addMessage, db, kvGet, kvSet, upsertPerson, validateExecutionRecord,
} = await import('../src/db.js');
const { handleMessageDetailed, runAutonomousTurn } = await import('../src/mind/agent.js');
const {
  __autonomyScheduleTest, autonomyStatus, startAutonomy,
} = await import('../src/mind/autonomy.js');
const {
  buildMonitoringMeasurementFact,
  issueMonitoringContract,
  issueMonitoringLifecycleBinding,
  issueMonitoringOutcomeReceipt,
  issueMonitoringPendingDelivery,
  monitoringContractReadBindings,
  normalizeMonitoringPendingDelivery,
  normalizeMonitoringOutcomeDecision,
} = await import('../src/mind/monitoringcontract.js');
const {
  bindEpisodeToWork,
  createGoalEpisode,
  goalEpisodeById,
  recordEpisodeDelivery,
  selectPortfolioCommitment,
  transitionGoalEpisode,
  verifyMonitoringLifecycleAuthority,
} = await import('../src/mind/portfolio.js');
const { createWorkItem } = await import('../src/mind/work.js');
const { registerMessenger } = await import('../src/mind/tools.js');
const telegram = await import('../src/integrations/telegram.js');
const { prepareAuthenticatedTelegramTurnForTest, setBotForTest }
  = telegram.__telegramTransportTest;

const chatId = '424242';
const messageId = '7358';
const conversationId = `telegram:${chatId}`;
const requester = 'Jaap';
const discoveryStart = phase === 'prepare-discovery-waiting';
const hintedStart = phase === 'prepare-hinted-pending';
const entityId = discoveryStart
  ? 'weather.native_buitentemperatuur' : 'sensor.native_temperatuur';
const messageThreadId = 17;
const hostileEntityName = 'API key: sk-proj-abcdefghijklmnopqrstuvwxyz – Zone—\u0007';
const userText = discoveryStart
  ? 'Waarschuw me wanneer de buitentemperatuur lager is dan 19 graden.'
  : 'Waarschuw me wanneer de temperatuur van sensor.native_temperatuur lager is dan 19 graden.';
const conditionText = discoveryStart
  ? 'De buitentemperatuur is lager dan 19 graden.'
  : 'De temperatuur is lager dan 19 graden.';
const conditionStart = userText.indexOf('wanneer');
const temperatureStart = userText.indexOf('temperatuur');
const now = new Date().toISOString();

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function functionResponse(name, value, callId) {
  return completedModelResponse([{
    type: 'function_call', call_id: callId, name, arguments: JSON.stringify(value),
  }]);
}

function textResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant', content: [{ type: 'output_text', text }],
  }]);
}

function startUnderstanding() {
  const readActions = discoveryStart ? [{
    capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
    semanticInput: { query: 'actuele buitentemperatuur met exacte entity-id' },
    sourceSpans: [{ start: temperatureStart, end: userText.length - 1 }],
  }, {
    capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
    semanticInput: { entityId: '<buitentemperatuur_resultaat>' },
    sourceSpans: [{ start: temperatureStart, end: userText.length - 1 }],
  }] : [{
    capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
    semanticInput: { entityId },
    sourceSpans: [{ start: temperatureStart, end: userText.length - 1 }],
  }];
  return {
    dialogueAct: 'command',
    goal: {
      statement: 'Bewaak de actuele temperatuur.',
      outcome: 'Jaap krijgt exact eenmaal bericht zodra de temperatuur laag genoeg is.',
      beneficiary: 'requester',
    },
    conditions: [{
      statement: conditionText, role: 'guard', state: 'required',
      sourceSpans: [{ start: conditionStart, end: userText.length }],
      requirementIds: ['r1'], afterRequirementIds: [],
    }],
    provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
    confidence: 0.99,
    actions: [...readActions, {
      capabilityId: 'work.episode.manage', operationId: 'start', polarity: 'request',
      semanticInput: {
        titel: 'Temperatuur onder grens', doel: 'Bewaak de geverifieerde temperatuur.',
        volgende_stap: 'Lees de gebonden temperatuur opnieuw en beoordeel de grens.',
        definitie_klaar: 'De eerste geverifieerde grensmeting is eenmaal aan Jaap bezorgd.',
        fase: 'Wacht op de volgende temperatuurmeting.',
        hervatten_na: new Date(Date.now() + 60_000).toISOString(),
        afleveren_aan: requester,
        toestand_json: hintedStart
          ? JSON.stringify({ focus_hint: 'Controleer uitsluitend de afgesproken grens.' })
          : '{}',
      },
      sourceSpans: [{ start: 0, end: userText.length }],
    }],
    requestedEffects: ['read', 'persistent'], deniedEffects: [],
    temporal: { mode: 'now', startSourceSpans: [] },
    durability: 'episode', delivery: { mode: 'later', target: 'requester' },
    proofNeeds: [], ambiguities: [],
    firstDecision: {
      kind: 'schedule', requirementIds: discoveryStart ? ['r1', 'r2', 'r3'] : ['r1', 'r2'],
      replyDraft: 'Ik houd de temperatuur in de gaten.',
    },
  };
}

function currentBinding() {
  return db.prepare(`SELECT w.id AS work_id,w.status AS work_status,w.state_json,
      w.next_wake_ts AS work_next_wake_ts,w.wait_json,
      e.id AS episode_id,e.state AS episode_state,e.wake_ts AS episode_wake_ts
    FROM eva_work_items w JOIN eva_goal_episodes e ON e.work_item_id=w.id
    ORDER BY CASE WHEN json_extract(w.state_json, '$.monitoring.contract')
      = 'MonitoringContract/v2' THEN 0 ELSE 1 END,w.id DESC LIMIT 1`).get();
}

function receiptCount(episodeId) {
  return Number(db.prepare(`SELECT count(*) AS value FROM eva_delivery_receipts
    WHERE episode_id=?`).get(Number(episodeId)).value);
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

function monitoringReceiptBinding(pending, lifecycle) {
  return {
    contract: 'MonitoringDeliveryReceiptBinding/v1',
    monitoringContractSha256: pending.monitoringContractSha256,
    pendingSha256: pending.pendingSha256,
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    originBindingSha256: lifecycle.originBindingSha256,
    outcomeReceiptSha256: pending.outcomeRef.outcomeReceiptSha256,
    outcomeSha256: pending.outcomeRef.outcomeSha256,
    factSetSha256: pending.factSetSha256,
    textSha256: pending.textSha256,
    rendererVersion: pending.rendererVersion,
    dedupeKey: pending.dedupeKey,
  };
}

function withoutTableTriggers(table, mutation) {
  const triggers = db.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(table);
  db.transaction(() => {
    for (const trigger of triggers) {
      db.exec(`DROP TRIGGER main.${String(trigger.name).replaceAll('"', '""')}`);
    }
    mutation();
    for (const trigger of triggers) db.exec(String(trigger.sql));
  })();
}

function rewriteExecutionSemanticBinding(executionKey, mutate) {
  const row = db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key=?')
    .get(executionKey);
  assert.ok(row);
  const args = JSON.parse(row.args);
  const originalBinding = JSON.parse(row.semantic_binding);
  const semanticBinding = mutate(structuredClone(originalBinding));
  const storedBinding = canonical(semanticBinding);
  const semanticHash = digest({
    turnId: row.turn_id, tool: row.tool, args, binding: semanticBinding,
  });
  const intentBody = {
    turnId: row.turn_id, tool: row.tool, args,
    binding: semanticBinding, semanticHash,
  };
  const intentRecordSha256 = digest(row.execution_contract === 'ExecutionRecord/v2'
    ? {
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...intentBody,
    } : intentBody);
  const completionBody = {
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash,
    semanticBinding: storedBinding,
    intentRecordSha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  };
  const completionRecordSha256 = digest(row.execution_contract === 'ExecutionRecord/v2'
    ? {
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...completionBody,
    } : completionBody);
  withoutTableTriggers('turn_tool_executions', () => {
    db.prepare(`UPDATE turn_tool_executions
      SET semantic_binding=?,semantic_hash=?,intent_record_sha256=?,completion_record_sha256=?
      WHERE execution_key=?`).run(
      storedBinding, semanticHash, intentRecordSha256, completionRecordSha256, executionKey,
    );
  });
  return { completionRecordSha256, semanticBinding };
}

function rewriteSelfConsistentExecution(executionKey, mutate) {
  const row = db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key=?')
    .get(executionKey);
  assert.ok(row);
  const draft = {
    executionKey: row.execution_key,
    turnId: row.turn_id,
    args: JSON.parse(row.args),
    semanticBinding: JSON.parse(row.semantic_binding),
    output: JSON.parse(row.output),
  };
  const changed = mutate(structuredClone(draft));
  const next = changed && typeof changed === 'object' ? changed : draft;
  const storedArgs = canonical(next.args);
  const storedBinding = canonical(next.semanticBinding);
  const storedOutput = canonical(next.output);
  const semanticHash = digest({
    turnId: next.turnId, tool: row.tool,
    args: next.args, binding: next.semanticBinding,
  });
  const intentBody = {
    turnId: next.turnId, tool: row.tool, args: next.args,
    binding: next.semanticBinding, semanticHash,
  };
  const intentRecordSha256 = digest(row.execution_contract === 'ExecutionRecord/v2'
    ? {
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...intentBody,
    } : intentBody);
  const outputSha256 = digest(storedOutput);
  const completionBody = {
    executionKey: next.executionKey,
    turnId: next.turnId,
    tool: row.tool,
    args: storedArgs,
    semanticHash,
    semanticBinding: storedBinding,
    intentRecordSha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256,
    materialSha256: null,
  };
  const completionRecordSha256 = digest(row.execution_contract === 'ExecutionRecord/v2'
    ? {
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...completionBody,
    } : completionBody);
  withoutTableTriggers('turn_tool_executions', () => {
    db.prepare(`UPDATE turn_tool_executions SET
      execution_key=?,turn_id=?,args=?,semantic_binding=?,semantic_hash=?,
      intent_record_sha256=?,output=?,output_sha256=?,material_sha256=NULL,
      completion_record_sha256=? WHERE execution_key=?`).run(
      next.executionKey, next.turnId, storedArgs, storedBinding, semanticHash,
      intentRecordSha256, storedOutput, outputSha256, completionRecordSha256,
      executionKey,
    );
  });
  return {
    ...row,
    execution_key: next.executionKey,
    turn_id: next.turnId,
    args: storedArgs,
    semantic_binding: storedBinding,
    semantic_hash: semanticHash,
    intent_record_sha256: intentRecordSha256,
    output: storedOutput,
    output_sha256: outputSha256,
    material_sha256: null,
    completion_record_sha256: completionRecordSha256,
  };
}

function reissueMonitoringState(state, replacementBinding = null, contractOverrides = {}) {
  const contract = state.monitoring;
  const lifecycle = state.monitoring_lifecycle;
  const fields = contract.readBindings.fields;
  const bindings = contract.readBindings.entries.map((entry) => (
    Object.fromEntries(fields.map((field, index) => [field, entry[index]]))
  ));
  if (replacementBinding) {
    const index = bindings.findIndex((binding) => (
      binding.requirementId === replacementBinding.requirementId
    ));
    assert.notEqual(index, -1);
    const body = { ...bindings[index], ...replacementBinding };
    delete body.bindingSha256;
    bindings[index] = { ...body, bindingSha256: digest(body) };
  }
  const forgedContract = issueMonitoringContract({
    sourceUnderstandingSha256: contract.sourceUnderstandingSha256,
    conditions: contract.conditions,
    temporalCondition: contract.temporalCondition,
    definitionOfDone: contract.definitionOfDone,
    cadenceMs: contract.cadenceMs,
    endsAt: contract.endsAt,
    discoveryQuery: contract.discoveryQuery,
    readBindings: {
      ...contract.readBindings,
      entries: bindings.map((binding) => fields.map((field) => binding[field])),
    },
    createdAt: contract.createdAt,
    requesterLabel: contract.presentation.requesterLabel,
    sourceTextSha256: contract.presentation.sourceTextSha256,
    deliveryRequired: contract.delivery.deliveryRequired,
    originBindingSha256: contract.delivery.originBindingSha256,
    ...contractOverrides,
  });
  assert.ok(forgedContract);
  const authority = lifecycle.originAuthority;
  const forgedLifecycle = issueMonitoringLifecycleBinding({
    monitoring: forgedContract,
    episodeId: lifecycle.episodeId,
    episodeKey: lifecycle.episodeKey,
    issuedEpisodeRevision: lifecycle.issuedEpisodeRevision,
    workId: lifecycle.workId,
    originRowId: lifecycle.originRowId,
    originKey: lifecycle.originKey,
    startAuthority: Object.fromEntries([
      'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
      'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
      'sourceSha256', 'sourceGeneration',
    ].map((field) => [field, authority[field]])),
    deliveryOrigin: authority,
    generation: lifecycle.generation,
  });
  assert.ok(forgedLifecycle);
  state.monitoring = forgedContract;
  state.monitoring_lifecycle = forgedLifecycle;
}

function monitoringFactFromExecution(binding, row, contract) {
  const output = JSON.parse(row.output);
  const args = JSON.parse(row.args);
  const semanticBinding = JSON.parse(row.semantic_binding);
  const receipt = {
    name: 'ha_lees', args, output: row.output,
    ok: output.ok, verified: output.verified, changed: output.changed,
    partial: output.partial === true, needs_followup: output.needs_followup === true,
    effect_uncertain: output.effect_uncertain === true,
    semanticBinding, at: row.finished_ts,
  };
  return buildMonitoringMeasurementFact(binding, receipt, {
    now: Date.parse(row.finished_ts),
    sourceUnderstandingSha256: contract.sourceUnderstandingSha256,
    verifyLedgerReceipt: (candidate) => candidate === receipt ? {
      executionKey: row.execution_key,
      intentRecordSha256: row.intent_record_sha256,
      outputSha256: row.output_sha256,
      completionRecordSha256: row.completion_record_sha256,
      finishedTs: row.finished_ts,
    } : null,
  });
}

function reissuePendingWithLineageMutation(state, {
  mutateObservation = null,
  mutateOutcome = (draft) => draft,
} = {}) {
  const contract = state.monitoring;
  const lifecycle = state.monitoring_lifecycle;
  const pending = state.pending_delivery;
  assert.equal(pending.contract, 'MonitoringPendingDelivery/v2');
  const factFields = pending.factRefs.fields;
  assert.equal(pending.factRefs.entries.length, 1);
  const factReference = Object.fromEntries(factFields.map((field, index) => (
    [field, pending.factRefs.entries[0][index]]
  )));
  const binding = monitoringContractReadBindings(contract)[0];
  const observationRow = typeof mutateObservation === 'function'
    ? rewriteSelfConsistentExecution(factReference.executionKey, mutateObservation)
    : validateExecutionRecord(db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key=?')
      .get(factReference.executionKey));
  const fact = monitoringFactFromExecution(binding, observationRow, contract);
  assert.ok(fact);
  const outcomeRow = rewriteSelfConsistentExecution(
    pending.outcomeRef.executionKey,
    (draft) => mutateOutcome({
      ...draft,
      args: { ...draft.args, factIds: [fact.factId] },
      output: {
        ...draft.output,
        outcome: { ...draft.output.outcome, factIds: [fact.factId] },
      },
    }),
  );
  const outcomeOutput = JSON.parse(outcomeRow.output);
  const outcome = normalizeMonitoringOutcomeDecision(outcomeOutput.outcome, [fact]);
  assert.ok(outcome);
  const outcomeReceipt = issueMonitoringOutcomeReceipt({
    outcome,
    executionKey: outcomeRow.execution_key,
    intentRecordSha256: outcomeRow.intent_record_sha256,
    outputSha256: outcomeRow.output_sha256,
    completionRecordSha256: outcomeRow.completion_record_sha256,
    finishedAt: outcomeRow.finished_ts,
  });
  assert.ok(outcomeReceipt);
  const forgedPending = issueMonitoringPendingDelivery({
    monitoring: contract, lifecycle, outcome, outcomeReceipt,
    facts: [fact], text: pending.text,
    episodeId: pending.episodeId, workId: pending.workId,
    dedupeKey: pending.dedupeKey, preparedAt: pending.preparedAt,
    attempts: pending.attempts,
  });
  assert.ok(forgedPending);
  const validatedOutcomeRow = validateExecutionRecord(db.prepare(`SELECT *
    FROM turn_tool_executions WHERE execution_key=?`).get(outcomeRow.execution_key));
  assert.equal(validatedOutcomeRow.completion_record_sha256,
    forgedPending.outcomeRef.completionRecordSha256);
  assert.equal(forgedPending.outcomeRef.outcomeReceiptSha256,
    outcomeReceipt.outcomeReceiptSha256);
  assert.ok(normalizeMonitoringPendingDelivery(forgedPending, contract, {
    episodeId: forgedPending.episodeId,
    workId: forgedPending.workId,
    dedupeKey: forgedPending.dedupeKey,
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    now: new Date().toISOString(), facts: [fact], outcome, outcomeReceipt,
  }), 'de self-consistente tamper moet vóór store-authority structureel geldig zijn');
  state.pending_delivery = forgedPending;
}

function executionToolCounts() {
  const rows = db.prepare(`SELECT tool,count(*) AS value FROM main.turn_tool_executions
    GROUP BY tool`).all();
  return Object.fromEntries(rows.map((row) => [String(row.tool), Number(row.value)]));
}

function countDelta(after, before, tool) {
  return Number(after[tool] || 0) - Number(before[tool] || 0);
}

let acceptedSends = 0;
let messengerCalls = 0;
let providerCalls = 0;
let judgeCalls = 0;
let homeReads = 0;
let telegramSequence = 80_000;
let closureError = null;
let deletedMessages = 0;
let schedulerUrgencyBefore = null;
let schedulerStartupStateSha256 = null;
let schedulerGuard = null;
let schedulerCompetitor = null;
let schedulerCrossing = null;
setBotForTest({ api: { sendMessage: async (targetChatId, text, options = {}) => {
  acceptedSends += 1;
  if (phase === 'recover-multichunk-race' && acceptedSends === 1) {
    const current = currentBinding();
    const state = JSON.parse(current.state_json);
    state.pending_delivery.lifecycleGeneration += 1;
    state.pending_delivery = rehashPending(state.pending_delivery);
    db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
      .run(JSON.stringify(state), current.work_id);
  }
  return {
    chat: { id: Number(targetChatId) }, message_id: ++telegramSequence,
    date: 1_786_500_000 + telegramSequence, text,
    from: { id: 123456789, is_bot: true, first_name: 'Eva Test' },
    ...(options.reply_parameters?.message_id ? {
      reply_to_message: { message_id: options.reply_parameters.message_id },
    } : {}),
    ...(options.message_thread_id ? { message_thread_id: options.message_thread_id } : {}),
  };
}, deleteMessage: async () => {
  deletedMessages += 1;
  return true;
} } });

const toolCountsBefore = executionToolCounts();
const telegramArchiveBefore = Number(db.prepare(`SELECT count(*) AS value
  FROM telegram_messages WHERE role='assistant'`).get().value);

if (['prepare-pending', 'prepare-waiting', 'prepare-discovery-waiting',
  'prepare-hinted-pending', 'prepare-outcome-retry-pending',
  'prepare-scheduler-retry', 'prepare-scheduler-no-window',
].includes(phase)) {
  fs.mkdirSync(dataDir, { recursive: true });
  upsertPerson(requester, { telegram_id: chatId });
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(process.env.HA_URL)) {
      homeReads += 1;
      if (String(url).endsWith('/api/states')) {
        return new Response(JSON.stringify([{
          entity_id: entityId, state: 'sunny', last_updated: now,
          attributes: {
            friendly_name: hostileEntityName, temperature: 18,
            temperature_unit: '°C',
          },
        }]), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        entity_id: entityId, state: discoveryStart ? 'sunny' : '18.0', last_updated: now,
        attributes: discoveryStart ? {
          friendly_name: hostileEntityName, temperature: 18, temperature_unit: '°C',
        } : { friendly_name: hostileEntityName, unit_of_measurement: '°C' },
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    providerCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const toolNames = (body.tools || []).map((tool) => tool?.name);
    if (toolNames.includes('commit_turn_understanding')) {
      return functionResponse('commit_turn_understanding', startUnderstanding(), 'native-monitor-start');
    }
    if (toolNames.includes('commit_monitoring_outcome')) {
      judgeCalls += 1;
      const payload = JSON.parse(String(body.input?.[0]?.content?.[0]?.text || '{}'));
      if (phase === 'prepare-outcome-retry-pending' && judgeCalls === 1) {
        return functionResponse('commit_monitoring_outcome', {
          verdict: 'notify', terminal: true, factIds: [],
        }, 'native-monitor-outcome-invalid-first');
      }
      return functionResponse('commit_monitoring_outcome', {
        verdict: 'notify', terminal: true,
        factIds: payload.facts.map((fact) => fact.factId),
      }, 'native-monitor-outcome');
    }
    return textResponse('Ik houd dit in de gaten.');
  };
  registerMessenger(async () => {
    messengerCalls += 1;
    if (phase === 'prepare-scheduler-no-window') {
      const current = currentBinding();
      const state = JSON.parse(current.state_json);
      setClock(Date.parse(state.pending_delivery.expiresAt) - 119_000);
    }
    return {
      ok: false, accepted: false, changed: false, verified: false,
      needs_followup: true, failure_class: 'transient', retryable: true,
      effect_uncertain: false,
    };
  });
  const started = await prepareAuthenticatedTelegramTurnForTest({
    ctx: {
      chat: { id: Number(chatId) }, from: { id: Number(chatId), first_name: requester },
      message: {
        message_id: Number(messageId), message_thread_id: messageThreadId,
        date: 1_786_500_000, text: userText,
      },
    },
    sender: requester, text: userText,
  });
  assert.ok(started.workItemId && started.goalEpisodeId, JSON.stringify({
    started,
    timeline: db.prepare(`SELECT kind,content,meta FROM main.timeline
      ORDER BY id DESC LIMIT 8`).all(),
  }));
  if (['prepare-pending', 'prepare-hinted-pending',
    'prepare-outcome-retry-pending'].includes(phase)) {
    await runAutonomousTurn({
      reason: 'native monitoring pending', workId: started.workItemId,
      goalEpisodeId: started.goalEpisodeId,
    });
  } else if (['prepare-scheduler-retry', 'prepare-scheduler-no-window'].includes(phase)) {
    const initial = currentBinding();
    const wake = db.prepare('SELECT wake_ts FROM eva_goal_episodes WHERE id=?')
      .get(initial.episode_id)?.wake_ts;
    setClock(Math.max(Date.parse(wake) + 1, Date.now() + 31 * 60_000));
    const scheduler = kvGet('eva_autonomy_scheduler_v1', {});
    kvSet('eva_autonomy_scheduler_v1', {
      ...scheduler,
      next_run_ts: new Date().toISOString(),
      pending_signals: [], quarantined_signals: [],
    });
    await __autonomyScheduleTest.tick();
  }
} else if (tamperKind) {
  const binding = currentBinding();
  assert.ok(binding);
  const state = JSON.parse(binding.state_json);
  const pending = state.pending_delivery;
  const lifecycle = state.monitoring_lifecycle;
  const factField = (name) => pending.factRefs.fields.indexOf(name);
  const persistState = () => db.prepare(
    'UPDATE eva_work_items SET state_json=? WHERE id=?',
  ).run(JSON.stringify(state), binding.work_id);
  if (tamperKind === 'missing-start-execution') {
    withoutTableTriggers('turn_tool_executions', () => {
      db.prepare('DELETE FROM turn_tool_executions WHERE execution_key=?')
        .run(lifecycle.originAuthority.executionKey);
    });
  } else if (tamperKind === 'corrupt-start-execution') {
    withoutTableTriggers('turn_tool_executions', () => {
      db.prepare(`UPDATE turn_tool_executions SET output=? WHERE execution_key=?`)
        .run('{"contract":"PreparedUnderstandingTurn/v1","tampered":true}',
          lifecycle.originAuthority.executionKey);
    });
  } else if (tamperKind === 'missing-start-authority') {
    withoutTableTriggers('eva_turn_understanding_authorities_v1', () => {
      db.prepare(`DELETE FROM eva_turn_understanding_authorities_v1
        WHERE execution_key=? AND source_generation=?`).run(
        lifecycle.originAuthority.executionKey,
        lifecycle.originAuthority.sourceGeneration,
      );
    });
  } else if (tamperKind === 'missing-understanding') {
    db.prepare(`DELETE FROM timeline WHERE kind='turn_understanding'`).run();
  } else if (tamperKind === 'missing-fact') {
    const executionKey = pending.factRefs.entries[0][factField('executionKey')];
    withoutTableTriggers('turn_tool_executions', () => {
      db.prepare('DELETE FROM turn_tool_executions WHERE execution_key=?').run(executionKey);
    });
  } else if (tamperKind === 'missing-outcome') {
    withoutTableTriggers('turn_tool_executions', () => {
      db.prepare('DELETE FROM turn_tool_executions WHERE execution_key=?')
        .run(pending.outcomeRef.executionKey);
    });
  } else if (tamperKind === 'legacy-contract') {
    state.monitoring = { contract: 'MonitoringContract/v1', endsAt: null };
    persistState();
  } else if (tamperKind === 'legacy-pending') {
    state.pending_delivery = {
      text: pending.text, facts: 'vrije legacytekst', recipient: requester,
      preparedAt: pending.preparedAt, attempts: 0, dedupeKey: pending.dedupeKey,
    };
    persistState();
  } else if (tamperKind === 'expired') {
    pending.expiresAt = pending.preparedAt;
    state.pending_delivery = rehashPending(pending);
    persistState();
  } else if (tamperKind === 'attempts-100') {
    pending.attempts = 100;
    state.pending_delivery = rehashPending(pending);
    persistState();
  } else if (tamperKind === 'attempts-99') {
    pending.attempts = 99;
    state.pending_delivery = rehashPending(pending);
    persistState();
    const pinnedWake = String(binding.episode_wake_ts || '');
    assert.ok(pinnedWake);
    db.prepare(`UPDATE eva_work_items SET next_wake_ts=?,wait_json=? WHERE id=?`).run(
      pinnedWake,
      JSON.stringify({ reason: 'delivery_retry', resumeAfter: pinnedWake, attempts: 99 }),
      binding.work_id,
    );
  } else if (['missing-pending-future', 'legacy-pending-future',
    'late-pending-wake'].includes(tamperKind)) {
    if (tamperKind === 'missing-pending-future') delete state.pending_delivery;
    if (tamperKind === 'legacy-pending-future') {
      state.pending_delivery = {
        text: pending.text, preparedAt: pending.preparedAt,
        attempts: pending.attempts, dedupeKey: pending.dedupeKey,
      };
    }
    persistState();
    if (tamperKind === 'late-pending-wake') {
      const lateWake = new Date(Date.parse(pending.expiresAt) + 60_000).toISOString();
      db.prepare('UPDATE eva_goal_episodes SET wake_ts=? WHERE id=?')
        .run(lateWake, binding.episode_id);
      db.prepare('UPDATE eva_work_items SET next_wake_ts=?,wait_json=? WHERE id=?').run(
        lateWake,
        JSON.stringify({
          reason: 'delivery_retry', resumeAfter: lateWake, attempts: pending.attempts,
        }),
        binding.work_id,
      );
    }
  } else if (['observation-extra-args', 'observation-wrong-addresses',
    'observation-nonempty-basis', 'observation-execution-type',
    'outcome-wrong-turn', 'outcome-wrong-key', 'outcome-extra-args',
    'outcome-wrong-attempt', 'outcome-wrong-lineage',
    'outcome-fake-request-sha', 'outcome-missing-response-sha',
    'outcome-fake-response-sha', 'outcome-accepted-failure',
    'outcome-missing-failure', 'outcome-extra-output',
    'outcome-attempt-overflow'].includes(tamperKind)) {
    const mutateObservation = (draft) => {
      if (tamperKind === 'observation-extra-args') draft.args.extra = 'forged';
      if (tamperKind === 'observation-wrong-addresses') {
        draft.semanticBinding.addressesRequirementIds = ['r1', 'r2'];
      }
      if (tamperKind === 'observation-nonempty-basis') {
        draft.semanticBinding.basisReceiptIds = ['s1'];
      }
      if (tamperKind === 'observation-execution-type') {
        draft.semanticBinding.executionType = 'forged';
      }
      return draft;
    };
    const mutateOutcome = (draft) => {
      if (tamperKind === 'outcome-wrong-turn') draft.turnId = 'forged-monitoring-turn';
      if (tamperKind === 'outcome-wrong-key') {
        draft.executionKey = `monitoring-outcome:${'f'.repeat(64)}`;
      }
      if (tamperKind === 'outcome-extra-args') draft.args.extra = 'forged';
      if (tamperKind === 'outcome-wrong-attempt') draft.args.attempt = 2;
      if (tamperKind === 'outcome-attempt-overflow') {
        draft.args.attempt = 3;
        draft.executionKey = `monitoring-outcome:${digest({
          turnId: draft.turnId,
          monitoringId: draft.args.monitoringId,
          factIds: draft.args.factIds,
          attempt: draft.args.attempt,
        })}`;
      }
      if (tamperKind === 'outcome-wrong-lineage') {
        draft.semanticBinding = {
          ...draft.semanticBinding,
          addressesRequirementIds: ['r1', 'r2'],
          basisReceiptIds: ['s1'],
          role: 'primary',
          executionType: null,
        };
      }
      if (tamperKind === 'outcome-fake-request-sha') {
        draft.args.requestSha256 = 'f'.repeat(64);
      }
      if (tamperKind === 'outcome-missing-response-sha') {
        delete draft.output.responseSha256;
      }
      if (tamperKind === 'outcome-fake-response-sha') {
        draft.output.responseSha256 = 'f'.repeat(64);
      }
      if (tamperKind === 'outcome-accepted-failure') {
        draft.output.failure = 'forged failure';
      }
      if (tamperKind === 'outcome-missing-failure') delete draft.output.failure;
      if (tamperKind === 'outcome-extra-output') draft.output.extra = 'forged';
      return draft;
    };
    reissuePendingWithLineageMutation(state, {
      mutateObservation: tamperKind.startsWith('observation-') ? mutateObservation : null,
      mutateOutcome,
    });
    persistState();
  } else if (['start-extra-monitoring-runtime', 'start-extra-pending-delivery',
    'start-extra-monitoring-lifecycle', 'start-extra-start-authority',
    'start-extra-delivery-origin'].includes(tamperKind)) {
    const startRow = db.prepare(`SELECT execution_key FROM turn_tool_executions
      WHERE tool='werk_bijhouden' AND status='done'
      ORDER BY execution_seq ASC LIMIT 1`).get();
    assert.ok(startRow);
    const key = {
      'start-extra-monitoring-runtime': 'monitoring_runtime',
      'start-extra-pending-delivery': 'pending_delivery',
      'start-extra-monitoring-lifecycle': 'monitoring_lifecycle',
      'start-extra-start-authority': 'monitoring_start_authority',
      'start-extra-delivery-origin': 'monitoring_start_delivery_origin',
    }[tamperKind];
    rewriteSelfConsistentExecution(startRow.execution_key, (draft) => {
      const issuedState = JSON.parse(String(draft.args.toestand_json || 'null'));
      draft.args.toestand_json = JSON.stringify({ ...issuedState, [key]: { forged: true } });
      return draft;
    });
  } else if (tamperKind === 'duplicate-fact-refs') {
    pending.factRefs.entries.push([...pending.factRefs.entries[0]]);
    state.pending_delivery = rehashPending(pending);
    persistState();
  } else if (tamperKind === 'wrong-lifecycle-generation') {
    pending.lifecycleGeneration += 1;
    state.pending_delivery = rehashPending(pending);
    persistState();
  } else if (['issuance-wrong-role', 'issuance-basis-missing',
    'issuance-basis-extra', 'issuance-basis-wrong'].includes(tamperKind)) {
    assert.equal(pending, undefined);
    const contract = state.monitoring;
    const fields = contract.readBindings.fields;
    const issuedBinding = Object.fromEntries(fields.map((field, index) => (
      [field, contract.readBindings.entries[0][index]]
    )));
    const rewritten = rewriteExecutionSemanticBinding(
      issuedBinding.issuedExecutionKey,
      (semantic) => {
        if (tamperKind === 'issuance-wrong-role') semantic.role = 'primary';
        if (tamperKind === 'issuance-basis-missing') semantic.basisReceiptIds = [];
        if (tamperKind === 'issuance-basis-extra') semantic.basisReceiptIds = ['s1', 's2'];
        if (tamperKind === 'issuance-basis-wrong') semantic.basisReceiptIds = ['s2'];
        return semantic;
      },
    );
    reissueMonitoringState(state, {
      requirementId: issuedBinding.requirementId,
      issuedCompletionRecordSha256: rewritten.completionRecordSha256,
    });
    persistState();
  } else if (tamperKind === 'discovery-wrong-requirement') {
    assert.equal(pending, undefined);
    const row = db.prepare(`SELECT execution_key FROM turn_tool_executions
      WHERE tool='huis_status' ORDER BY execution_seq ASC LIMIT 1`).get();
    assert.ok(row);
    rewriteExecutionSemanticBinding(row.execution_key, (semantic) => ({
      ...semantic, requirementId: 'r2', addressesRequirementIds: ['r2'],
    }));
  } else if (tamperKind === 'self-rehashed-contract-semantics') {
    assert.equal(pending, undefined);
    const contract = state.monitoring;
    const issuedMaxAges = [...contract.readBindings.entries].map((entry) => (
      entry[contract.readBindings.fields.indexOf('maxAgeMs')]
    ));
    reissueMonitoringState(state, null, {
      definitionOfDone: `${contract.definitionOfDone} Gewijzigde einddefinitie.`,
      temporalCondition: 'Alleen na een zelfgehashte, niet-uitgegeven tijdsvoorwaarde.',
      cadenceMs: contract.cadenceMs + 1_000,
      endsAt: new Date(Date.parse(contract.createdAt) + 4 * 60 * 60_000).toISOString(),
      createdAt: new Date(Date.parse(contract.createdAt) + 1).toISOString(),
    });
    assert.notEqual(state.monitoring.contractSha256, contract.contractSha256);
    assert.deepEqual([...state.monitoring.readBindings.entries].map((entry) => (
      entry[state.monitoring.readBindings.fields.indexOf('maxAgeMs')]
    )), issuedMaxAges, 'cadencewijziging blijft bewust binnen hetzelfde maxAge-plateau');
    persistState();
  } else if (tamperKind === 'self-rehashed-binding-redirect') {
    assert.equal(pending, undefined);
    const contract = state.monitoring;
    const fields = contract.readBindings.fields;
    const original = Object.fromEntries(fields.map((field, index) => [
      field, contract.readBindings.entries[0][index],
    ]));
    const forgedBody = {
      ...original,
      entityId: 'climate.redirected_temperatuur',
      sourceField: 'current_temperature',
      valueType: 'number',
      quantityKind: 'temperature',
      unitField: 'temperature_unit',
      expectedUnit: '°C',
    };
    delete forgedBody.bindingSha256;
    const forgedBinding = { ...forgedBody, bindingSha256: digest(forgedBody) };
    const forgedDictionary = {
      ...contract.readBindings,
      entries: [fields.map((field) => forgedBinding[field])],
    };
    const forgedContract = issueMonitoringContract({
      sourceUnderstandingSha256: contract.sourceUnderstandingSha256,
      conditions: contract.conditions,
      temporalCondition: contract.temporalCondition,
      definitionOfDone: contract.definitionOfDone,
      cadenceMs: contract.cadenceMs,
      endsAt: contract.endsAt,
      discoveryQuery: contract.discoveryQuery,
      readBindings: forgedDictionary,
      createdAt: contract.createdAt,
      requesterLabel: contract.presentation.requesterLabel,
      sourceTextSha256: contract.presentation.sourceTextSha256,
      deliveryRequired: contract.delivery.deliveryRequired,
      originBindingSha256: contract.delivery.originBindingSha256,
    });
    assert.ok(forgedContract);
    const authority = lifecycle.originAuthority;
    const forgedLifecycle = issueMonitoringLifecycleBinding({
      monitoring: forgedContract,
      episodeId: lifecycle.episodeId,
      episodeKey: lifecycle.episodeKey,
      issuedEpisodeRevision: lifecycle.issuedEpisodeRevision,
      workId: lifecycle.workId,
      originRowId: lifecycle.originRowId,
      originKey: lifecycle.originKey,
      startAuthority: Object.fromEntries([
        'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
        'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
        'sourceSha256', 'sourceGeneration',
      ].map((field) => [field, authority[field]])),
      deliveryOrigin: authority,
      generation: lifecycle.generation,
    });
    assert.ok(forgedLifecycle);
    assert.notEqual(forgedContract.contractSha256, contract.contractSha256);
    assert.notEqual(forgedLifecycle.lifecycleSha256, lifecycle.lifecycleSha256);
    state.monitoring = forgedContract;
    state.monitoring_lifecycle = forgedLifecycle;
    persistState();
  } else {
    throw new Error(`onbekende monitoringtamper: ${tamperKind}`);
  }
} else if (phase === 'receipt-wrong-route-proof') {
  const binding = currentBinding();
  assert.ok(binding);
  const state = JSON.parse(binding.state_json);
  const pending = state.pending_delivery;
  const lifecycle = state.monitoring_lifecycle;
  const origin = lifecycle.originAuthority;
  const expectedDedupeKey = `prepared:${pending.dedupeKey}`;
  const sent = await telegram.sendToFamily(pending.text, requester, {
    dedupeKey: 'prepared:wrong-monitoring-delivery',
    broadcast: false,
    directReply: false,
    replyToMessageId: origin.messageId,
    messageThreadId: origin.messageThreadId || undefined,
    directTurnSignalRef: `turn:telegram:${chatId}:${origin.messageId}`,
    originPrincipal: origin.principal,
  });
  try {
    recordEpisodeDelivery(binding.episode_id, {
      deliveredChannel: 'telegram', deliveryTarget: requester,
      originConversationId: conversationId, originMessageId: messageId,
      deliveredConversationId: conversationId,
      resultText: pending.text, proofSummary: 'geverifieerde native monitoringmeting',
      idempotencyKey: expectedDedupeKey,
      transport: {
        accepted: true, transport: 'telegram', kind: 'text',
        fanoutOperationKey: sent.fanoutOperationKey, sent: sent.sent || [],
      },
      monitoringBinding: monitoringReceiptBinding(pending, lifecycle),
    });
  } catch (error) {
    closureError = String(error?.message || error);
  }
} else if (phase === 'receipt-closure-race') {
  const binding = currentBinding();
  assert.ok(binding);
  const state = JSON.parse(binding.state_json);
  const pending = state.pending_delivery;
  const lifecycle = state.monitoring_lifecycle;
  const origin = lifecycle.originAuthority;
  const transportDedupeKey = `prepared:${pending.dedupeKey}`;
  const sent = await telegram.sendToFamily(pending.text, null, {
    dedupeKey: transportDedupeKey,
    broadcast: false,
    directReply: true,
    directReplyChatId: chatId,
    replyToMessageId: origin.messageId,
    messageThreadId: origin.messageThreadId || undefined,
    directTurnSignalRef: `turn:telegram:${chatId}:${origin.messageId}`,
    originPrincipal: origin.principal,
  });
  state.pending_delivery.lifecycleGeneration += 1;
  state.pending_delivery = rehashPending(state.pending_delivery);
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
    .run(JSON.stringify(state), binding.work_id);
  try {
    recordEpisodeDelivery(binding.episode_id, {
      deliveredChannel: 'telegram', deliveryTarget: requester,
      originConversationId: conversationId, originMessageId: messageId,
      deliveredConversationId: conversationId,
      resultText: pending.text, proofSummary: 'geverifieerde native monitoringmeting',
      idempotencyKey: transportDedupeKey,
      transport: {
        accepted: true, transport: 'telegram', kind: 'text',
        fanoutOperationKey: sent.fanoutOperationKey, sent: sent.sent || [],
      },
      monitoringBinding: monitoringReceiptBinding(pending, lifecycle),
    });
  } catch (error) {
    closureError = String(error?.message || error);
  }
} else if (phase === 'accept-transport-crash') {
  const binding = currentBinding();
  assert.ok(binding);
  const state = JSON.parse(binding.state_json);
  const pending = state.pending_delivery;
  const origin = state.monitoring_lifecycle.originAuthority;
  const sent = await telegram.sendToFamily(pending.text, null, {
    dedupeKey: `prepared:${pending.dedupeKey}`,
    broadcast: false,
    directReply: true,
    directReplyChatId: chatId,
    replyToMessageId: origin.messageId,
    messageThreadId: origin.messageThreadId || undefined,
    directTurnSignalRef: `turn:telegram:${chatId}:${origin.messageId}`,
    originPrincipal: origin.principal,
  });
  assert.equal(sent.accepted, true, JSON.stringify(sent));
  assert.equal(sent.sent.length, 1);
} else if (['scheduler-crossing-recover',
  'scheduler-crossing-error-recover'].includes(phase)) {
  const monitoringBefore = currentBinding();
  assert.ok(monitoringBefore);
  const monitoringWake = String(monitoringBefore.episode_wake_ts || '');
  assert.ok(monitoringWake);
  db.prepare('UPDATE eva_work_items SET owner=NULL WHERE id=?')
    .run(monitoringBefore.work_id);
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(process.env.HA_URL)) {
      homeReads += 1;
      throw new Error('crossing-wake mag Home Assistant niet bereiken');
    }
    providerCalls += 1;
    db.prepare('UPDATE eva_work_items SET owner=? WHERE id=?')
      .run(requester, monitoringBefore.work_id);
    const body = JSON.parse(String(options.body || '{}'));
    if (!(body.tools || []).some((tool) => tool?.name === 'commit_turn_understanding')) {
      throw new Error('crossing-wake verwacht uitsluitend een TurnUnderstanding-call');
    }
    if (phase === 'scheduler-crossing-error-recover') {
      throw new Error('crossing-wake exceptionproef mag de provider niet bereiken');
    }
    setClock(Date.parse(monitoringWake) + 1);
    const payload = JSON.parse(String(body.input?.[0]?.content?.[0]?.text || '{}'));
    const sourceText = String(payload.turn?.text || '');
    return functionResponse('commit_turn_understanding', {
      dialogueAct: 'inform',
      goal: {
        statement: 'Rond deze andere beurt zonder nieuwe voortgang af.',
        outcome: 'Er wordt geen onbewezen effect uitgevoerd.', beneficiary: 'self',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: sourceText.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
    }, 'scheduler-crossing-no-progress');
  };
  registerMessenger(async (text, recipient, options) => {
    messengerCalls += 1;
    return telegram.sendToFamily(text, recipient, options);
  });
  const competingWork = createWorkItem({
    source: 'self', owner: null, title: 'Lopende andere autonomiebeurt',
    goal: 'Beoordeel deze andere richting zonder onbewezen effect.',
    nextStep: 'Beoordeel of er aantoonbare voortgang mogelijk is.',
    definitionOfDone: 'De richting is aantoonbaar beoordeeld.', priority: 100,
  });
  const competingEpisode = createGoalEpisode({
    title: 'Lopende andere autonomiebeurt',
    outcome: 'De andere richting wordt aantoonbaar beoordeeld.',
    definitionOfDone: 'De richting is aantoonbaar beoordeeld.',
    originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone',
    nextAction: 'Beoordeel de richting zonder onbewezen effect.',
  });
  bindEpisodeToWork(competingEpisode.id, competingWork.id);
  kvSet('eva_autonomy_scheduler_v1', {
    ...kvGet('eva_autonomy_scheduler_v1', {}),
    next_run_ts: new Date().toISOString(), pending_signals: [], quarantined_signals: [],
  });
  const nativeMathRandom = Math.random;
  if (phase === 'scheduler-crossing-error-recover') {
    let injected = false;
    Math.random = () => {
      if (!injected) {
        injected = true;
        setClock(Date.parse(monitoringWake) + 1);
        throw new Error('scheduler-crossing-test-exception');
      }
      return nativeMathRandom();
    };
  }
  try {
    await __autonomyScheduleTest.tick();
  } finally {
    Math.random = nativeMathRandom;
  }
  const completionAt = new Date().toISOString();
  const completionScheduler = kvGet('eva_autonomy_scheduler_v1', {});
  const competingLease = db.prepare(`SELECT lease_owner,lease_until
    FROM eva_work_items WHERE id=?`).get(competingWork.id);
  schedulerCrossing = {
    completionAt,
    completionNextRunTs: completionScheduler.next_run_ts || null,
    completionLastResult: completionScheduler.last_result || null,
    competingWorkId: competingWork.id,
    competingLeaseOwner: competingLease?.lease_owner ?? null,
    competingLeaseUntil: competingLease?.lease_until ?? null,
  };
  setClock(Date.parse(completionAt) + 15_001);
  await __autonomyScheduleTest.tick();
} else if (['scheduler-recover', 'scheduler-guard-recover',
  'scheduler-overrule-recover'].includes(phase)) {
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(process.env.HA_URL)) homeReads += 1;
    else {
      providerCalls += 1;
      const body = JSON.parse(String(options.body || '{}'));
      if ((body.tools || []).some((tool) => tool?.name === 'commit_monitoring_outcome')) {
        judgeCalls += 1;
      }
    }
    throw new Error('sealed schedulerdelivery mag geen provider of Home Assistant bereiken');
  };
  registerMessenger(async (text, recipient, options) => {
    messengerCalls += 1;
    return telegram.sendToFamily(text, recipient, options);
  });
  if (phase === 'scheduler-overrule-recover') {
    const futureWake = new Date(Date.now() + 60_000).toISOString();
    const competingWork = createWorkItem({
      source: 'telegram', owner: 'Andere bewoner',
      title: 'Nabije andere bewonersopdracht',
      goal: 'Voer de andere bewonersopdracht later uit.',
      nextStep: 'Wacht op het nabije afgesproken moment.',
      definitionOfDone: 'De andere opdracht is later uitgevoerd.', priority: 100,
    });
    const competingEpisode = createGoalEpisode({
      title: 'Nabije andere bewonersopdracht',
      outcome: 'De andere bewonersopdracht wordt later uitgevoerd.',
      definitionOfDone: 'De andere opdracht is later uitgevoerd.',
      originKind: 'user', originChannel: 'telegram',
      originConversationId: 'telegram:999999', originMessageId: '991',
      requester: 'Andere bewoner', deliveryPolicy: 'required',
      deliveryTarget: 'Andere bewoner', nextAction: 'Wacht op het afgesproken moment.',
    });
    const boundCompeting = bindEpisodeToWork(competingEpisode.id, competingWork.id);
    transitionGoalEpisode(boundCompeting.id, {
      state: 'waiting', wakeTs: futureWake,
      work: {
        nextWakeTs: futureWake,
        wait: { reason: 'scheduled_user_work', resumeAfter: futureWake },
      },
    });
    schedulerCompetitor = { workId: competingWork.id, wakeTs: futureWake };
  }
  if (phase === 'scheduler-recover') {
    addMessage('telegram', 'Andere bewoner', 'user',
      'Een recent gesprek mag een due monitoringdelivery niet uitstellen.', 'telegram:999999');
  }
  const scheduler = kvGet('eva_autonomy_scheduler_v1', {});
  kvSet('eva_autonomy_scheduler_v1', {
    ...scheduler,
    next_run_ts: new Date().toISOString(),
    pending_signals: [], quarantined_signals: [],
  });
  const urgencyWork = db.prepare(`SELECT * FROM eva_work_items
    WHERE json_extract(state_json, '$.monitoring.contract')='MonitoringContract/v2'
    ORDER BY id DESC LIMIT 1`).get();
  schedulerStartupStateSha256 = urgencyWork
    ? digest(String(urgencyWork.state_json || '')) : null;
  let urgencyState = null;
  try { urgencyState = JSON.parse(String(urgencyWork?.state_json || 'null')); }
  catch { urgencyState = null; }
  const urgencyChoice = urgencyWork ? selectPortfolioCommitment([urgencyWork], new Date()) : null;
  const urgencyPendingSha256 = String(
    urgencyState?.pending_delivery?.pendingSha256 || '',
  );
  const urgencyAuthority = urgencyChoice?.episode && urgencyPendingSha256
    ? verifyMonitoringLifecycleAuthority({
      workId: Number(urgencyWork.id), episodeId: Number(urgencyChoice.episode.id),
      expectedPendingSha256: urgencyPendingSha256,
    }) : null;
  schedulerUrgencyBefore = {
    workPresent: Boolean(urgencyWork),
    workStatus: urgencyWork?.status || null,
    workWakeTs: urgencyWork?.next_wake_ts || null,
    wakeFinite: Number.isFinite(Date.parse(urgencyWork?.next_wake_ts || '')),
    wakeDue: Number.isFinite(Date.parse(urgencyWork?.next_wake_ts || ''))
      && Date.parse(urgencyWork.next_wake_ts) <= Date.now(),
    pendingSha256: urgencyPendingSha256 || null,
    choicePresent: Boolean(urgencyChoice),
    choiceEpisodeState: urgencyChoice?.episode?.state || null,
    authorityPresent: Boolean(urgencyAuthority),
    authorityPendingPresent: Boolean(urgencyAuthority?.pending),
  };
  if (phase === 'scheduler-guard-recover') {
    const timerDelays = [];
    const nativeSetTimeout = globalThis.setTimeout;
    const nativeClearTimeout = globalThis.clearTimeout;
    globalThis.setTimeout = (callback, delay) => {
      timerDelays.push(Number(delay));
      return { callback, delay };
    };
    globalThis.clearTimeout = () => {};
    kvSet('eva_autonomy_scheduler_v1', {
      ...kvGet('eva_autonomy_scheduler_v1', {}),
      next_run_ts: new Date(Date.now() + 60 * 60_000).toISOString(),
    });
    startAutonomy();
    const cold = kvGet('eva_autonomy_scheduler_v1', {});
    const coldTimerDelay = timerDelays.at(-1) ?? null;
    kvSet('eva_autonomy_scheduler_v1', {
      ...cold, next_run_ts: new Date(Date.now() + 60 * 60_000).toISOString(),
    });
    bus.emit('work_changed', { status: 'open' });
    const workChanged = kvGet('eva_autonomy_scheduler_v1', {});
    const workChangedTimerDelay = timerDelays.at(-1) ?? null;
    kvSet('eva_autonomy_scheduler_v1', {
      ...workChanged, next_run_ts: new Date().toISOString(),
    });
    globalThis.fetch = () => new Promise(() => {});
    void handleMessageDetailed({
      channel: 'web', sender: 'Bezoeker', text: 'Houd deze beurt actief.',
      messageContext: { messageId: 'scheduler-busy-web' },
    }).catch(() => {});
    await Promise.resolve();
    await __autonomyScheduleTest.tick();
    const busy = kvGet('eva_autonomy_scheduler_v1', {});
    schedulerGuard = {
      coldNextRunTs: cold.next_run_ts || null,
      coldTimerDelay,
      workChangedNextRunTs: workChanged.next_run_ts || null,
      workChangedTimerDelay,
      busyNextRunTs: busy.next_run_ts || null,
      busyCurrentSelection: autonomyStatus().current_selection,
    };
    globalThis.setTimeout = nativeSetTimeout;
    globalThis.clearTimeout = nativeClearTimeout;
  } else {
    await __autonomyScheduleTest.tick();
  }
  if (schedulerCompetitor) {
    const competing = db.prepare(`SELECT status,attempt_count,next_wake_ts
      FROM eva_work_items WHERE id=?`).get(schedulerCompetitor.workId);
    schedulerCompetitor = {
      ...schedulerCompetitor,
      status: competing?.status || null,
      attemptCount: Number(competing?.attempt_count || 0),
      storedWakeTs: competing?.next_wake_ts || null,
    };
  }
} else if (phase === 'startup-inspect') {
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('startupquarantaine mag geen provider of Home Assistant bereiken');
  };
  registerMessenger(async () => {
    messengerCalls += 1;
    throw new Error('startupquarantaine mag geen messenger bereiken');
  });
} else {
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(process.env.HA_URL)) homeReads += 1;
    else {
      providerCalls += 1;
      const body = JSON.parse(String(options.body || '{}'));
      if ((body.tools || []).some((tool) => tool?.name === 'commit_monitoring_outcome')) {
        judgeCalls += 1;
      }
    }
    throw new Error('sealed monitoringrestart mag geen provider of Home Assistant bereiken');
  };
  registerMessenger(phase === 'recover-fail' ? async () => {
    messengerCalls += 1;
    return {
      ok: false, accepted: false, changed: false, verified: false,
      needs_followup: true, failure_class: 'transient', retryable: true,
      effect_uncertain: false,
    };
  } : async (text, recipient, options) => {
    messengerCalls += 1;
    const sent = await telegram.sendToFamily(text, recipient, options);
    if (phase === 'recover-race-state') {
      const current = currentBinding();
      const state = JSON.parse(current.state_json);
      state.pending_delivery.lifecycleGeneration += 1;
      state.pending_delivery = rehashPending(state.pending_delivery);
      db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
        .run(JSON.stringify(state), current.work_id);
    }
    return sent;
  });
  const binding = currentBinding();
  if (binding?.episode_state !== 'closed') {
    await runAutonomousTurn({
      reason: 'native sealed monitoringrestart', workId: binding.work_id,
      goalEpisodeId: binding.episode_id,
    });
  }
}

const binding = currentBinding();
const state = binding ? JSON.parse(binding.state_json) : {};
const transport = db.prepare(`SELECT status FROM main.turn_tool_executions
  WHERE tool='@transport/telegram.sendMessage' ORDER BY execution_seq DESC LIMIT 1`).get();
const transportRow = db.prepare(`SELECT args,output,status,completion_record_sha256
  FROM main.turn_tool_executions WHERE tool='@transport/telegram.sendMessage'
  ORDER BY execution_seq DESC LIMIT 1`).get();
const deliveryReceipt = binding ? db.prepare(`SELECT * FROM eva_delivery_receipts
  WHERE episode_id=? ORDER BY id DESC LIMIT 1`).get(binding.episode_id) : null;
const assistantArchive = db.prepare(`SELECT chat_id,message_id,content,reply_to_message_id,meta
  FROM telegram_messages WHERE role='assistant' ORDER BY id DESC LIMIT 1`).get();
const toolCountsAfter = executionToolCounts();
const telegramArchiveAfter = Number(db.prepare(`SELECT count(*) AS value
  FROM telegram_messages WHERE role='assistant'`).get().value);
const report = {
  phase, providerCalls, judgeCalls, homeReads, messengerCalls, acceptedSends,
  workId: binding?.work_id || null, episodeId: binding?.episode_id || null,
  workStatus: binding?.work_status || null,
  episodeState: binding?.episode_state || null,
  pendingContract: state.pending_delivery?.contract || null,
  pendingAttempts: state.pending_delivery?.attempts ?? null,
  pendingSha256: state.pending_delivery?.pendingSha256 || null,
  stateJsonSha256: binding ? digest(String(binding.state_json || '')) : null,
  receipts: binding ? receiptCount(binding.episode_id) : 0,
  transportStatus: transport?.status || null,
  newHuisStatus: countDelta(toolCountsAfter, toolCountsBefore, 'huis_status'),
  newHaLees: countDelta(toolCountsAfter, toolCountsBefore, 'ha_lees'),
  newWerkBijhouden: countDelta(toolCountsAfter, toolCountsBefore, 'werk_bijhouden'),
  newStuurBericht: countDelta(toolCountsAfter, toolCountsBefore, 'stuur_bericht'),
  newTransportRows: countDelta(toolCountsAfter, toolCountsBefore,
    '@transport/telegram.sendMessage'),
  newTelegramArchiveRows: telegramArchiveAfter - telegramArchiveBefore,
  wakeTs: binding ? db.prepare('SELECT wake_ts FROM eva_goal_episodes WHERE id=?')
    .get(binding.episode_id)?.wake_ts || null : null,
  workNextWakeTs: binding?.work_next_wake_ts || null,
  wait: binding?.wait_json ? JSON.parse(binding.wait_json) : null,
  pendingExpiresAt: state.pending_delivery?.expiresAt || null,
  clockNow: new Date().toISOString(),
  scheduler: kvGet('eva_autonomy_scheduler_v1', null),
  schedulerUrgencyBefore,
  schedulerStartupStateSha256,
  schedulerGuard,
  schedulerCompetitor,
  schedulerCrossing,
  lastError: binding ? db.prepare('SELECT last_error FROM eva_work_items WHERE id=?')
    .get(binding.work_id)?.last_error || null : null,
  closureError,
  deletedMessages,
  pendingText: state.pending_delivery?.text || null,
  workStateHint: state.focus_hint || null,
  pendingTextSha256: state.pending_delivery?.textSha256 || null,
  transportIntent: transportRow?.args ? JSON.parse(transportRow.args) : null,
  transportReceipt: transportRow?.output ? JSON.parse(transportRow.output) : null,
  transportCompletionSha256: transportRow?.completion_record_sha256 || null,
  deliveryReceipt: deliveryReceipt ? {
    episodeId: Number(deliveryReceipt.episode_id),
    originConversationId: deliveryReceipt.origin_conversation_id,
    originMessageId: deliveryReceipt.origin_message_id,
    deliveredConversationId: deliveryReceipt.delivered_conversation_id,
    deliveredChannel: deliveryReceipt.delivered_channel,
    deliveryTarget: deliveryReceipt.delivery_target,
    resultText: deliveryReceipt.result_text,
    idempotencyKey: deliveryReceipt.idempotency_key,
    transport: JSON.parse(deliveryReceipt.transport_json),
  } : null,
  assistantArchive: assistantArchive ? {
    chatId: assistantArchive.chat_id, messageId: assistantArchive.message_id,
    content: assistantArchive.content,
    replyToMessageId: assistantArchive.reply_to_message_id,
    meta: JSON.parse(assistantArchive.meta),
  } : null,
};
registerMessenger(null);
setBotForTest(null);
db.close();
process.stdout.write(`NATIVE_MONITORING_V2_RESTART=${JSON.stringify(report)}\n`);
