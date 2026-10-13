import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

async function perceptionSchedulerScenario(kind) {
  const assert = (await import('node:assert/strict')).default;
  await import('./test/homeassistant-test-composition.js');
  const { bus } = await import('./src/bus.js');
  const { db, kvGet, kvSet } = await import('./src/db.js');
  const { __cameraTriggerTest } = await import('./src/integrations/homeassistant.js');
  const {
    agentStatus,
    handleMessageDetailed,
  } = await import('./src/mind/agent.js');
  const {
    autonomyStatus,
    startAutonomy,
  } = await import('./src/mind/autonomy.js');
  const attention = await import('./src/mind/perceptionattention.js');
  const { createDurableWork } = await import('./src/mind/portfolio.js');

  const schedulerKey = 'eva_autonomy_scheduler_v1';
  const realSetTimeout = globalThis.setTimeout.bind(globalThis);
  const realClearTimeout = globalThis.clearTimeout.bind(globalThis);
  const schedulerTimers = [];

  globalThis.setTimeout = (callback, delay, ...args) => {
    if (!/\btick\(\)/u.test(String(callback))) {
      return realSetTimeout(callback, delay, ...args);
    }
    const handle = {
      callback, delay, args, cancelled: false, invoked: false, scheduler: true,
    };
    schedulerTimers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle?.scheduler === true) handle.cancelled = true;
    else realClearTimeout(handle);
  };

  const waitFor = async (predicate, message, timeoutMs = 8_000) => {
    const deadline = Date.now() + timeoutMs;
    while (!predicate() && Date.now() < deadline) {
      await new Promise((resolve) => realSetTimeout(resolve, 10));
    }
    assert.equal(predicate(), true, message);
  };
  const fireScheduler = () => {
    const handle = [...schedulerTimers].reverse().find((candidate) => (
      candidate.scheduler && !candidate.cancelled && !candidate.invoked
    ));
    assert.ok(handle, 'de publieke autonomiescheduler moet een tick hebben bewapend');
    handle.invoked = true;
    handle.callback(...handle.args);
    return handle;
  };
  const failedResponse = () => new Response(JSON.stringify({ error: {
    code: 'test_provider_reject', param: 'none', message: 'gerichte livenessproef',
  } }), { status: 400, headers: { 'content-type': 'application/json' } });
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  let incidentSequence = 0;
  const recordIncident = (suffix) => {
    incidentSequence += 1;
    assert.ok(__cameraTriggerTest, 'de authentieke camera-eventpoort is alleen in testcontext open');
    const event = __cameraTriggerTest.issue({
      type: 'doorbell', source: 'home_assistant',
      camera: `camera.scheduler_doorbell_${suffix}_${incidentSequence}`,
      sensor: `binary_sensor.scheduler_doorbell_${suffix}_${incidentSequence}_doorbell`,
      correlationId: `scheduler-${suffix}-${incidentSequence}-${Date.now()}`,
      triggerTs: new Date().toISOString(), frames: [],
    });
    const recorded = attention.recordDoorbellEvidence(event);
    assert.equal(recorded.ok, true);
    return recorded;
  };
  const createWork = (suffix) => createDurableWork({
    source: 'autonomy', owner: 'Eva', title: `Schedulerwerk ${suffix}`,
    goal: `Maak aantoonbare voortgang voor livenessproef ${suffix}.`,
    nextStep: `Voer precies een kleine bewijsstap uit voor ${suffix}.`,
    definitionOfDone: `De bewijsstap voor ${suffix} is gecontroleerd.`, priority: 100,
  }, { originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone' });

  try {
    if (kind === 'ready_during_turn') {
      const { workItem } = createWork(kind);
      kvSet(schedulerKey, { pending_signals: [],
        next_run_ts: new Date(Date.now() - 1_000).toISOString() });
      let releaseProvider;
      const gate = new Promise((resolve) => { releaseProvider = resolve; });
      let providerCalls = 0;
      globalThis.fetch = async () => {
        providerCalls += 1;
        await gate;
        return failedResponse();
      };
      startAutonomy();
      fireScheduler();
      await waitFor(() => providerCalls > 0 && autonomyStatus().running,
        'de gewone schedulerbeurt moet aantoonbaar in haar providerfase lopen');

      const recorded = recordIncident(kind);
      const emittedAt = Date.now();
      bus.emit('perception_ready', {
        contract: 'PerceptionReadyNotice/v1', incidentId: recorded.incidentId,
      });
      const immediateWake = Date.parse(kvGet(schedulerKey).next_run_ts || '');
      assert.ok(Number.isFinite(immediateWake) && immediateWake <= emittedAt + 2_500,
        `perception_ready moet onmiddellijk wekken; kreeg ${immediateWake - emittedAt}ms`);

      releaseProvider();
      await waitFor(() => agentStatus().active_turns === 0 && !autonomyStatus().running,
        'de ingehaalde gewone beurt moet eindigen');
      const finalWake = Date.parse(kvGet(schedulerKey).next_run_ts || '');
      assert.ok(finalWake <= emittedAt + 2_500,
        `beurteinde mag de perception-wake niet overschrijven; kreeg ${finalWake - emittedAt}ms`);
      assert.equal(attention.perceptionAttentionStatus().counts.ready, 1);
      assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?')
        .get(workItem.id).status, 'open');
      return;
    }

    if (kind === 'active_interactive_retry') {
      recordIncident(kind);
      kvSet(schedulerKey, { pending_signals: [],
        next_run_ts: new Date(Date.now() - 1_000).toISOString() });
      let releaseProvider;
      const gate = new Promise((resolve) => { releaseProvider = resolve; });
      let providerCalls = 0;
      const chatText = 'Blijf heel even bij deze beurt.';
      globalThis.fetch = async (_url, options = {}) => {
        providerCalls += 1;
        const request = JSON.parse(String(options.body || '{}'));
        if (request.tools?.[0]?.name === 'commit_turn_understanding') {
          return completed([{ type: 'function_call', call_id: 'active-chat-understanding',
            name: 'commit_turn_understanding', arguments: JSON.stringify({
              dialogueAct: 'continue',
              goal: { statement: chatText, outcome: 'Reageer op dezelfde draad.',
                beneficiary: 'requester' },
              conditions: [], provenance: { sourceSpans: [{ start: 0, end: chatText.length }],
                contextHashes: [] }, confidence: 0.99, actions: [], requestedEffects: [],
              deniedEffects: [], temporal: { mode: 'none', startSourceSpans: [] },
              durability: 'turn', delivery: { mode: 'reply', target: 'origin' },
              proofNeeds: [], ambiguities: [], firstDecision: { kind: 'reply',
                requirementIds: [], replyDraft: 'Ik ben er nog.' },
            }) }]);
        }
        await gate;
        return completed([{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Ik ben er nog.' }] }]);
      };
      startAutonomy();
      const previousAutonomyTurn = agentStatus().last_autonomy_turn;
      const chat = handleMessageDetailed({
        channel: 'web', sender: 'Schedulerbewoner', text: chatText,
        messageContext: { conversationId: 'web:perception-liveness', identityTrusted: true },
      });
      await waitFor(() => providerCalls > 1 && agentStatus().active_turns > 0
        && agentStatus().interactive_pending > 0,
      'de interactieve beurt moet tegelijk actief en pending zichtbaar zijn');
      const firedAt = Date.now();
      fireScheduler();
      await waitFor(() => Date.parse(kvGet(schedulerKey).next_run_ts || '') > firedAt,
        'de geblokkeerde scheduler moet een korte retry bewaren');
      const retryAt = Date.parse(kvGet(schedulerKey).next_run_ts || '');
      assert.ok(retryAt >= firedAt + 10_000 && retryAt <= firedAt + 20_000,
        `een private incidentretry hoort circa 15s te zijn; kreeg ${retryAt - firedAt}ms`);
      assert.equal(agentStatus().last_autonomy_turn, previousAutonomyTurn,
        'de geblokkeerde tick mag geen generieke autonome beurt starten');
      releaseProvider();
      await chat;
      return;
    }

    if (kind === 'decision_failed_is_terminal_lane') {
      const recorded = recordIncident(kind);
      kvSet(schedulerKey, { pending_signals: [],
        next_run_ts: new Date(Date.now() - 1_000).toISOString() });
      let providerCalls = 0;
      globalThis.fetch = async () => {
        providerCalls += 1;
        return completed([{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'geen getypeerde keuze' }] }]);
      };
      const previousAutonomyTurn = agentStatus().last_autonomy_turn;
      startAutonomy();
      fireScheduler();
      await waitFor(() => autonomyStatus().last_result?.perception === true,
        'de private lane moet haar fail-closed resultaat opslaan');
      assert.equal(providerCalls, 1,
        'een ongeldig deurbelbesluit mag niet gevolgd worden door een generieke providercall');
      assert.equal(agentStatus().last_autonomy_turn, previousAutonomyTurn,
        'decision_failed is geen aanleiding voor een generieke autonome beurt');
      assert.equal(autonomyStatus().last_result.summary, 'Deurbelincident: decision_failed');
      const incident = db.prepare(`SELECT status,terminal_reason FROM eva_doorbell_incidents
        WHERE id=?`).get(recorded.incidentId);
      assert.deepEqual(incident, { status: 'expired', terminal_reason: 'decision_failed' });
      return;
    }

    if (kind === 'private_lane_precedes_signals_and_work') {
      const recorded = recordIncident(kind);
      const { workItem } = createWork(kind);
      const pendingSignal = { id: 'perception-priority-signal',
        ts: new Date().toISOString(), type: 'entity_state',
        key: 'binary_sensor.perception_priority_door', text: 'Deur: off -> on' };
      kvSet(schedulerKey, { pending_signals: [pendingSignal],
        next_run_ts: new Date(Date.now() - 1_000).toISOString() });
      let providerCalls = 0;
      globalThis.fetch = async (_url, options = {}) => {
        providerCalls += 1;
        const request = JSON.parse(String(options.body || '{}'));
        assert.deepEqual(request.tools?.map((tool) => tool.name), ['kies_deurbelreactie']);
        return completed([{ type: 'function_call', call_id: 'scheduler-doorbell-silent',
          name: 'kies_deurbelreactie', arguments: JSON.stringify({
            contract: 'DoorbellAttentionDecision/v1', action: 'silent',
          }) }]);
      };
      const previousAutonomyTurn = agentStatus().last_autonomy_turn;
      startAutonomy();
      fireScheduler();
      await waitFor(() => autonomyStatus().last_result?.perception === true,
        'de private incidentlane moet als eerste afronden');
      assert.equal(providerCalls, 1);
      assert.equal(agentStatus().last_autonomy_turn, previousAutonomyTurn,
        'dezelfde tick mag geen tweede, generieke beurt starten');
      assert.deepEqual(kvGet(schedulerKey).pending_signals, [pendingSignal],
        'de signaalbatch blijft exact pending voor haar eigen latere beurt');
      assert.deepEqual(db.prepare('SELECT status,lease_owner FROM eva_work_items WHERE id=?')
        .get(workItem.id), { status: 'open', lease_owner: null });
      assert.deepEqual(db.prepare(`SELECT status,terminal_reason FROM eva_doorbell_incidents
        WHERE id=?`).get(recorded.incidentId), { status: 'settled', terminal_reason: 'silent' });
      return;
    }

    throw new Error(`onbekend scenario ${kind}`);
  } finally {
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
    db.close();
  }
}

function runIsolatedScenario(kind) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-perception-scheduler-${kind}-`));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e',
      `(${perceptionSchedulerScenario.toString()})(${JSON.stringify(kind)})
        .then(() => process.exit(0))
        .catch((error) => { console.error(error); process.exit(1); });`], {
      cwd: projectRoot,
      env: {
        ...process.env,
        EVA_DATA_DIR: dataDir,
        EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
        OPENAI_API_KEY: 'test-only',
        NODE_TEST_CONTEXT: process.env.NODE_TEST_CONTEXT || 'child-v8',
        SLEEP_START: '00:00',
        WAKE_TIME: '00:00',
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('perception_ready tijdens een lopende beurt bewaart de onmiddellijke volgende run', () => {
  runIsolatedScenario('ready_during_turn');
});

test('een actief interactief gesprek geeft een private incidentlane een korte retry', () => {
  runIsolatedScenario('active_interactive_retry');
});

test('decision_failed sluit alleen de private lane en start geen generieke beurt', () => {
  runIsolatedScenario('decision_failed_is_terminal_lane');
});

test('een privé deurbelincident gaat in dezelfde tick voor signalen en werk', () => {
  runIsolatedScenario('private_lane_precedes_signals_and_work');
});
