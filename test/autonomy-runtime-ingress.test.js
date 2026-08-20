import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-ingress-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';
process.env.SLEEP_START = '23:59';
process.env.WAKE_TIME = '00:00';
process.env.WEB_PORT = '0';

// Importing this production ingress performs the one allowed private
// registration. The test deliberately never imports or exports its issuer.
const { autonomyStatus, startAutonomy } = await import('../src/mind/autonomy.js');
const {
  agentStatus, handleMessageDetailed, runAutonomousTurn,
} = await import('../src/mind/agent.js');
const { db, kvGet, kvSet } = await import('../src/db.js');
const { registerRuntimePrincipalValidator } = await import('../src/mind/runtimecontroller.js');
const { createSignalBatch } = await import('../src/mind/signals.js');
const { llmRuntimeStatus } = await import('../src/brain/llm.js');
const { startWeb } = await import('../src/web/server.js');

const SCHEDULER_KEY = 'eva_autonomy_scheduler_v1';
const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const scheduledTimers = [];

after(() => {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
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

function runtimeUnderstanding() {
  return completed([{
    type: 'function_call', call_id: 'understand-runtime', name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: {
        statement: 'Controleer de actuele runtime-status.',
        outcome: 'Eva kent haar actuele runtime-status.',
        beneficiary: 'self',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: 5_000 }], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'runtime.status.read', operationId: 'status', polarity: 'request',
        semanticInput: {},
        sourceSpans: [{ start: 0, end: 5_000 }],
      }],
      requestedEffects: ['read'],
      deniedEffects: [],
      temporal: { mode: 'now' },
      durability: 'turn',
      delivery: { mode: 'none', target: 'none' },
      proofNeeds: ['accepted_result'],
      ambiguities: [],
      firstDecision: {
        kind: 'inspect', requirementIds: ['r1'],
        replyDraft: 'Ik controleer mijn runtime-status.',
      },
    }),
  }]);
}

test('alleen de private productiescheduler kan runtime- en SignalBatchbevoegdheid geven', async () => {
  assert.throws(
    () => registerRuntimePrincipalValidator('autonomy', () => 'system:eva-autonomy'),
    /alleen eenmaal/,
    'de productie-ingress moet de enige autonomy-validator al bezitten',
  );

  const cloneSignal = { id: 'plain-clone-door', ts: new Date().toISOString(),
    type: 'entity_state', key: 'binary_sensor.achterdeur_sensor_contact_2',
    text: 'Achterdeur sensor Contact: off -> on' };
  const cloneBatch = createSignalBatch([cloneSignal]);
  const cloneState = { next_run_ts: new Date(Date.now() + 60_000).toISOString(),
    pending_signals: [cloneSignal], daily_used_ms: 0, daily_productive_ms: 0,
    daily_preempted_ms: 0 };
  kvSet(SCHEDULER_KEY, cloneState);
  const ledgerRowsBeforeForgery = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  let cloneProviderCalls = 0, cloneHomeCalls = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/states/')) cloneHomeCalls += 1;
    else cloneProviderCalls += 1;
    throw new Error('plain clone mocht geen I/O bereiken');
  };
  const cloned = await runAutonomousTurn({
    reason: 'Vervalste schedulerclone met geldige batch.', pendingSignals: [cloneSignal],
    schedulerContext: Object.freeze({ pendingSignals: Object.freeze([cloneSignal]),
      signalBatch: cloneBatch, attention: null, workId: null, goalEpisodeId: null }),
  });
  assert.equal(cloned.interpretationFailed, true);
  assert.equal(cloned.error, 'untrusted_autonomy_scheduler_context');
  assert.deepEqual(cloned.tools, []);
  assert.equal(cloneProviderCalls, 0);
  assert.equal(cloneHomeCalls, 0);
  assert.deepEqual(kvGet(SCHEDULER_KEY), cloneState, 'de clone mag schedulerstate niet wijzigen');

  let accessorReads = 0;
  const statefulClone = new Proxy({}, {
    get(_target, key) {
      accessorReads += 1;
      return key === 'signalBatch' && accessorReads > 1 ? cloneBatch : undefined;
    },
  });
  const accessorRejected = await runAutonomousTurn({
    reason: 'Stateful accessor mag de attestatie niet passeren.', schedulerContext: statefulClone,
  });
  assert.equal(accessorRejected.error, 'untrusted_autonomy_scheduler_context');
  assert.deepEqual(accessorRejected.tools, []);
  assert.equal(accessorReads, 0, 'de principalcheck moet vóór iedere context-propertyread gebeuren');
  const unprivilegedAttention = await runAutonomousTurn({
    reason: 'Losse aandacht zonder schedulerattest.',
    attention: { kind: 'goal', id: 42, text: 'Vervalste aandacht.' },
  });
  assert.equal(unprivilegedAttention.error, 'untrusted_autonomy_scheduler_context');
  assert.deepEqual(unprivilegedAttention.tools, []);
  const plainAttention = await runAutonomousTurn({ reason: 'Plain aandacht mag niet door.',
    schedulerContext: Object.freeze({ attention: { kind: 'forged' }, workId: 42 }) });
  assert.equal(plainAttention.error, 'untrusted_autonomy_scheduler_context');
  assert.deepEqual(plainAttention.tools, []);
  assert.equal(cloneProviderCalls, 0);
  assert.equal(cloneHomeCalls, 0);
  assert.deepEqual(kvGet(SCHEDULER_KEY), cloneState);

  let forgedCalls = 0;
  globalThis.fetch = async () => {
    forgedCalls += 1;
    return runtimeUnderstanding();
  };

  const forged = await runAutonomousTurn({
    reason: 'Controleer je runtime-status.',
    schedulerContext: Object.freeze({}),
  });
  assert.equal(forged.error, 'untrusted_autonomy_scheduler_context');
  assert.deepEqual(forged.tools, []);
  assert.equal(forgedCalls, 0, 'een plain schedulercontext faalt vóór provider- en tool-I/O');
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    ledgerRowsBeforeForgery, 'een vervalste schedulercontext claimt ook geen ledger-I/O');

  let signalUnderstandingCalls = 0, signalHomeCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).includes('/api/states/')) {
      signalHomeCalls += 1;
      return new Response(JSON.stringify({ entity_id: cloneSignal.key, state: 'on', attributes: {},
        last_updated: new Date().toISOString() }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      return completed([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'De actuele deurstatus is beoordeeld.' }] }]);
    }
    signalUnderstandingCalls += 1;
    const payload = JSON.parse(request.input[0].content[0].text);
    const end = payload.turn.text.length;
    return completed([{ type: 'function_call', call_id: 'understand-timer-signal',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Beoordeel het actuele deursignaal.',
          outcome: 'De actuele deurstatus is exact gecontroleerd.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
        confidence: 0.99, actions: [{ capabilityId: 'home.entity.read', operationId: 'read',
          polarity: 'request', semanticInput: { entityId: cloneSignal.key },
          sourceSpans: [{ start: 0, end }] }],
        signalAssessment: { disposition: 'observed',
          assessment: 'De actuele deurstatus is exact uitgelezen.' },
        requestedEffects: ['read', 'persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
        delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'schedule', requirementIds: ['r1', 'r2'], replyDraft: null },
      }) }]);
  };

  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [cloneSignal],
    daily_used_ms: 0,
    daily_productive_ms: 0,
    daily_preempted_ms: 0,
  });

  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false };
    scheduledTimers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle && typeof handle === 'object') handle.cancelled = true;
  };

  startAutonomy();
  const first = scheduledTimers.find((item) => !item.cancelled);
  assert.ok(first, 'startAutonomy moet de echte armTimer-route plannen');
  first.callback(...first.args);

  const deadline = Date.now() + 10_000;
  while (!autonomyStatus().last_run_ts && Date.now() < deadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  // Geef de promise-finally van tick nog één microtask om de her-arm in onze
  // nep-timer op te vangen, zodat deze test nooit een echte achtergrondtimer lekt.
  await new Promise((resolve) => originalSetTimeout(resolve, 0));

  assert.equal(signalUnderstandingCalls, 1,
    'de productiegetrouwe episode-zonder-werk mag geen interpretatieretry veroorzaken');
  assert.equal(signalHomeCalls, 1);
  assert.deepEqual(agentStatus().last_autonomy_turn?.tools?.map((tool) => tool.name),
    ['ha_lees', 'signalen_afhandelen']);
  const signalState = kvGet(SCHEDULER_KEY);
  assert.deepEqual(signalState.pending_signals, []);
  assert.equal(signalState.last_result.signals_acknowledged, 1);
  assert.ok(scheduledTimers.length >= 2, 'de echte scheduler moet zich na de beurt opnieuw bewapenen');

  let scheduledCalls = 0;
  globalThis.fetch = async () => {
    scheduledCalls += 1;
    return runtimeUnderstanding();
  };
  kvSet(SCHEDULER_KEY, { ...signalState,
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [] });
  const priorRun = autonomyStatus().last_run_ts;
  const runtimeTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
  assert.ok(runtimeTimer, 'de echte scheduler moet voor de runtimebeurt opnieuw bewapend zijn');
  runtimeTimer.callback(...runtimeTimer.args);
  const runtimeDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === priorRun && Date.now() < runtimeDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));

  const status = agentStatus();
  const scheduledRuntime = status.last_autonomy_turn?.tools
    ?.find((item) => item.name === 'beheer_runtime');
  assert.ok(scheduledRuntime, 'de private schedulerroute moet beheer_runtime werkelijk uitvoeren');
  assert.doesNotMatch(scheduledRuntime.output, /niet geautoriseerd/,
    'de private schedulerreferentie moet de runtime-authoritygrens passeren');
  if (scheduledRuntime.ok) assert.equal(scheduledRuntime.verified, true);
  else assert.match(scheduledRuntime.output, /ENOENT|no such file/i,
    'vóór indexsanering mag alleen de fail-closed tracked-filecontrole nog blokkeren');
  assert.ok(autonomyStatus().last_run_ts);
  assert.equal(scheduledCalls, 1, 'ook de echte scheduler moet exact het gebonden plan uitvoeren');
});

test('healthprojectie ziet interactieve ingress al tijdens de verborgen understandingwacht', async () => {
  const before = agentStatus();
  const heldUnderstandings = [];
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    if (heldUnderstandings.length < 3
      && request.tools?.[0]?.name === 'commit_turn_understanding') {
      return new Promise((resolve) => {
        heldUnderstandings.push(resolve);
      });
    }
    if (request.tools?.[0]?.name === 'commit_turn_understanding') {
      return runtimeUnderstanding();
    }
    return completed([{ type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: 'De actuele runtime-status is gecontroleerd.' }] }]);
  };

  const turnPromises = [
    handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: 'Controleer de actuele runtime-status.',
      messageContext: { conversationId: 'web:observability-race', identityTrusted: true },
    }),
    handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: 'Tweede beurt staat aantoonbaar in dezelfde wachtrij.',
      messageContext: { conversationId: 'web:observability-race', identityTrusted: true },
    }),
    handleMessageDetailed({
      channel: 'telegram', sender: 'Jaap',
      text: 'health-user-canary-secret-telegram-7429',
      messageContext: { conversationId: 'telegram:observability-race',
        chatId: 74, messageId: 29, identityTrusted: true },
    }),
    handleMessageDetailed({
      channel: 'voice', sender: 'Jaap', text: 'Controleer ook via spraak de runtime-status.',
      messageContext: { conversationId: 'voice:observability-race',
        messageId: 'voice-observability-1', identityTrusted: true },
    }),
  ];
  const deadline = Date.now() + 2_000;
  while (heldUnderstandings.length < 3 && Date.now() < deadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 5));
  }
  assert.equal(heldUnderstandings.length, 3,
    'web, Telegram en voice moeten tegelijk in hun understandingrequest staan');
  const during = agentStatus();
  assert.equal(during.active_turns, 0,
    'de provider-understanding loopt vóór de oudere active-turnprojectie');
  assert.equal(during.interactive_pending, before.interactive_pending + 4,
    'de tweede webbeurt telt ook terwijl hij achter dezelfde conversationqueue wacht');
  assert.equal(during.interaction_epoch, before.interaction_epoch + 4);

  const schedulerBefore = autonomyStatus();
  const providerAttemptsBeforeScheduler = llmRuntimeStatus().provider_request_attempts;
  kvSet(SCHEDULER_KEY, {
    ...kvGet(SCHEDULER_KEY), next_run_ts: new Date(Date.now() - 1_000).toISOString(),
  });
  const schedulerTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
  assert.ok(schedulerTimer);
  schedulerTimer.callback(...schedulerTimer.args);
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  assert.equal(autonomyStatus().running, false);
  assert.equal(autonomyStatus().current_selection, null);
  assert.equal(autonomyStatus().last_run_ts, schedulerBefore.last_run_ts,
    'pending useringress mag geen schedulerbeurt als run boeken');
  assert.equal(llmRuntimeStatus().provider_request_attempts, providerAttemptsBeforeScheduler,
    'de startgate blokkeert vóór iedere nieuwe providerfetch');

  heldUnderstandings[0](runtimeUnderstanding());
  heldUnderstandings[1](new Response(JSON.stringify({ error: {
    code: 'observability_reject', param: 'none', message: 'niet projecteren',
  } }), { status: 400, headers: { 'content-type': 'application/json' } }));
  heldUnderstandings[2](runtimeUnderstanding());
  await Promise.all(turnPromises);
  const afterStatus = agentStatus();
  assert.equal(afterStatus.interactive_pending, before.interactive_pending);
  assert.equal(afterStatus.interaction_epoch, during.interaction_epoch,
    'het monotone ingress-epoch daalt na afronding niet terug');
});

test('/api/health ontsluit uitsluitend de begrensde observabilityprojecties', async () => {
  const stateCanary = 'health-state-canary-secret-9184';
  const schedulerState = kvGet(SCHEDULER_KEY);
  kvSet(SCHEDULER_KEY, {
    ...schedulerState,
    pending_signals: [{ id: 'health-canary-signal', ts: new Date().toISOString(),
      key: 'sensor.health_canary', type: 'entity_state', text: stateCanary }],
    quarantined_signals: [{ contract: 'QuarantinedSignal/v1',
      latest_snapshot: { text: stateCanary } }],
    last_result: {
      turn_id: 'health-safe-turn', productive: false, progressed: true,
      yielded: false, tool: 'lees_bestand', signals_handled: false,
      signals_acknowledged: 1, signals_deferred: 2,
      summary: stateCanary, error: stateCanary,
    },
  });
  const server = await startWeb();
  try {
    const address = server.address();
    assert.ok(address && typeof address === 'object');
    const response = await originalFetch(`http://127.0.0.1:${address.port}/api/health`);
    assert.equal(response.status, 200);
    const health = await response.json();
    assert.deepEqual(Object.keys(health.agent).sort(), [
      'active_turns', 'interaction_epoch', 'interactive_pending',
    ]);
    assert.deepEqual(health.agent, {
      active_turns: agentStatus().active_turns,
      interactive_pending: agentStatus().interactive_pending,
      interaction_epoch: agentStatus().interaction_epoch,
    });
    assert.deepEqual(Object.keys(health.autonomie).sort(), [
      'current_selection', 'last_result', 'last_run_ts', 'next_run_ts',
      'pending_signal_count', 'quarantined_signal_count', 'running',
    ]);
    assert.equal(health.autonomie.current_selection, null);
    assert.equal(health.autonomie.pending_signal_count, 1);
    assert.equal(health.autonomie.quarantined_signal_count, 1);
    assert.deepEqual(Object.keys(health.autonomie.last_result).sort(), [
      'productive', 'progressed', 'signals_acknowledged', 'signals_deferred',
      'signals_handled', 'tool', 'turn_id', 'yielded',
    ]);
    assert.ok(Number.isSafeInteger(health.llm.provider_request_attempts));
    assert.deepEqual(Object.keys(health.llm), ['provider_request_attempts']);
    assert.ok(Number.isSafeInteger(health.runtimeVersion.pid));
    assert.ok(Number.isFinite(Date.parse(health.runtimeVersion.started_at)),
      'de process-lokale tellers zijn aan pid plus runtime-start te correleren');
    const serialized = JSON.stringify(health);
    for (const canary of [stateCanary, 'health-user-canary-secret-telegram-7429',
      'niet projecteren']) assert.equal(serialized.includes(canary), false, canary);
    for (const forbidden of ['last_turn', 'last_autonomy_turn', 'open_commitments',
      'pending_signals', 'quarantined_signals', 'summary', 'error']) {
      assert.equal(Object.hasOwn(health.agent, forbidden)
        || Object.hasOwn(health.autonomie, forbidden)
        || Object.hasOwn(health.autonomie.last_result, forbidden), false, forbidden);
    }
  } finally {
    await new Promise((resolve, reject) => server.close((error) => (
      error ? reject(error) : resolve()
    )));
  }
});
