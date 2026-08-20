import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function freeNoActionRuntimeContinuityScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const { createWorkItem } = await import('./src/mind/work.js');
  const { ensureEpisodeForWorkItem } = await import('./src/mind/portfolio.js');
  const {
    beginCognitiveEpisode, closeCognitiveEpisode,
    recordCognitiveAction, recordCognitiveOutcome,
  } = await import('./src/mind/cognitive.js');
  const KEY = 'eva_autonomy_scheduler_v1';
  const wait = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = {
      callback, delay, args, cancelled: false, fired: false,
      scheduler: /\btick\(\)/u.test(String(callback)),
    };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };

  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed',
    response: { output, usage: { input_tokens: 800, output_tokens: 120, total_tokens: 920 } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const standardSentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
  const privateQuery = 'unieke privé-huisread voor anti-herhaling 7392';
  const privateEntity = 'sensor.prive_anti_herhaling_7392';
  const action = (span, query) => ({
    capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
    semanticInput: { query }, sourceSpans: [span],
  });
  const freeReadUnderstanding = (payload, query) => {
    const start = payload.turn.text.indexOf(standardSentence);
    assert.ok(start >= 0, payload.turn.text);
    const span = { start, end: start + standardSentence.length };
    return {
      dialogueAct: 'command',
      goal: {
        statement: 'Lees één actuele huiscontext zonder vervolgactie.',
        outcome: 'De actuele huiscontext is gecontroleerd.',
        beneficiary: 'self',
      },
      conditions: [{
        statement: standardSentence, role: 'guard', state: 'required',
        sourceSpans: [span], requirementIds: [], afterRequirementIds: [],
      }],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: [action(span, query)],
      requestedEffects: ['read'],
      deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] },
      durability: 'turn',
      delivery: { mode: 'silent', target: 'none' },
      proofNeeds: [],
      ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    };
  };
  const silentUnderstanding = (payload) => {
    const span = { start: 0, end: payload.turn.text.length };
    return {
      dialogueAct: 'inform',
      goal: {
        statement: 'Laat deze niet-vrije context zonder nieuwe actie.',
        outcome: 'Geen nieuwe actie.',
        beneficiary: 'self',
      },
      conditions: [],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: [],
      requestedEffects: [],
      deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] },
      durability: 'turn',
      delivery: { mode: 'silent', target: 'none' },
      proofNeeds: [],
      ambiguities: [],
      firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
    };
  };
  const chatReplyUnderstanding = (payload) => {
    const span = { start: 0, end: payload.turn.text.length };
    return {
      dialogueAct: 'inform',
      goal: { statement: 'Beantwoord de testchat.', outcome: 'Testchat beantwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: [],
      requestedEffects: [],
      deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] },
      durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' },
      proofNeeds: [],
      ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'De testchat is ontvangen.' },
    };
  };

  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const forceDue = () => {
    const row = db.prepare('SELECT value FROM kv WHERE key=?').get(KEY);
    const state = row ? JSON.parse(row.value) : {};
    kvSet(KEY, {
      ...state,
      next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [],
      daily_used_ms: Number(state.daily_used_ms || 0),
      daily_productive_ms: Number(state.daily_productive_ms || 0),
      daily_preempted_ms: Number(state.daily_preempted_ms || 0),
    });
  };
  forceDue();

  let phase = 'first-free-read';
  let stateReads = 0;
  const interpretationRequests = [];
  const selections = [];
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    if (target.includes('/api/states')) {
      assert.equal(phase.includes('free-'), true, phase);
      stateReads += 1;
      return new Response(JSON.stringify([{
        entity_id: privateEntity,
        state: 'stable',
        last_updated: '2026-08-12T08:00:00.000Z',
        attributes: { friendly_name: 'Privé anti-herhaling teststatus' },
      }]), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    assert.equal(target, 'https://chatgpt.com/backend-api/codex/responses');
    const request = JSON.parse(String(options.body || '{}'));
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    const payload = JSON.parse(request.input[0].content[0].text);
    interpretationRequests.push({ phase, instructions: request.instructions, payload });
    selections.push({ phase, selection: structuredClone(autonomyStatus().current_selection) });
    if (phase === 'synthetic-chat') {
      assert.equal(payload.turn.origin, 'user');
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
      return completed([{
        type: 'function_call', call_id: 'synthetic-chat-reply',
        name: 'commit_turn_understanding', arguments: JSON.stringify(chatReplyUnderstanding(payload)),
      }]);
    }
    if (phase === 'first-free-read') {
      assert.equal(Object.hasOwn(payload.context, 'selfAttention'), false,
        'deze route moet exact ongebonden vrije aandacht blijven');
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
      return completed([{
        type: 'function_call', call_id: 'first-free-home-read',
        name: 'commit_turn_understanding',
        arguments: JSON.stringify(freeReadUnderstanding(payload, privateQuery)),
      }]);
    }
    if (phase === 'second-free-read') {
      assert.equal(Object.hasOwn(payload.context, 'selfAttention'), false);
      assert.match(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
      assert.match(request.instructions, /home\.context\.read\/read/u);
      assert.match(request.instructions, /work\.episode\.read\/list/u);
      assert.doesNotMatch(request.instructions, /conversation\.history\.read\/search/u);
      assert.doesNotMatch(request.instructions, /goal\.portfolio\.read\/list/u);
      assert.match(request.instructions, /geen bijdrage of voortgang/u);
      assert.match(request.instructions, /geen opdracht of bevoegdheid/u);
      assert.equal(request.instructions.includes(privateQuery), false);
      assert.equal(request.instructions.includes(privateEntity), false);
      return completed([{
        type: 'function_call', call_id: 'second-free-home-read',
        name: 'commit_turn_understanding',
        arguments: JSON.stringify(freeReadUnderstanding(payload, 'een andere actuele huisread 7393')),
      }]);
    }
    if (phase.startsWith('free-restore-')) {
      assert.equal(Object.hasOwn(payload.context, 'selfAttention'), false);
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u,
        'een direct nieuwere niet-vrije episode wist de oudere grens uit');
      return completed([{
        type: 'function_call', call_id: `${phase}-home-read`,
        name: 'commit_turn_understanding',
        arguments: JSON.stringify(freeReadUnderstanding(payload, `${phase} actuele huisread`)),
      }]);
    }
    if (phase === 'attention') {
      assert.equal(payload.context.selfAttention?.kind, 'aspiration');
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
    } else if (phase === 'signal') {
      assert.equal(payload.context.signalBatch?.signals?.length, 1);
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
    } else if (phase === 'work') {
      assert.equal(Number(payload.context.workId) > 0, true);
      assert.doesNotMatch(request.instructions, /Recente vrije aandacht \(negatieve grens\)/u);
    } else {
      assert.fail(`onbekende interpretatiefase ${phase}`);
    }
    return completed([{
      type: 'function_call', call_id: `${phase}-silent`,
      name: 'commit_turn_understanding', arguments: JSON.stringify(silentUnderstanding(payload)),
    }]);
  };

  let priorTimer = null;
  const trigger = async (previousRun = null) => {
    const timer = timers.find((item) => item.scheduler && !item.cancelled && !item.fired);
    assert.ok(timer, 'de publieke scheduler moet een verse tick hebben');
    assert.notEqual(timer, priorTimer);
    timer.fired = true;
    priorTimer = timer;
    timer.callback(...timer.args);
    const deadline = Date.now() + 10_000;
    while ((!autonomyStatus().last_run_ts || autonomyStatus().last_run_ts === previousRun)
      && Date.now() < deadline) {
      await new Promise((resolve) => wait(resolve, 10));
    }
    await new Promise((resolve) => wait(resolve, 0));
    assert.notEqual(autonomyStatus().last_run_ts, previousRun);
    return agentStatus().last_autonomy_turn;
  };

  startAutonomy();
  let previousRun = autonomyStatus().last_run_ts;
  const first = await trigger(previousRun);
  assert.deepEqual(selections[0].selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });
  assert.deepEqual(first.tools.map((tool) => [tool.name, tool.ok, tool.verified, tool.changed]), [
    ['huis_status', true, true, false],
  ], JSON.stringify(first));
  assert.ok(first.noActionReason);

  const firstEpisode = db.prepare(`SELECT episode.*, trigger.payload_json AS trigger_payload_json
    FROM cognitive_episodes AS episode
    JOIN cognitive_events AS trigger ON trigger.id = episode.trigger_event_id
    WHERE episode.correlation_id = ?`).get(first.turnId);
  assert.equal(firstEpisode.status, 'completed');
  assert.equal(firstEpisode.goal_key, 'free_attention');
  assert.equal(firstEpisode.result_class, 'no_action');
  assert.deepEqual(JSON.parse(firstEpisode.trigger_payload_json), {
    channel: 'autonomy', mode: 'autonomy', sender: 'Eva',
    contractKind: 'autonomy', authenticFree: true, syntheticTest: false,
  });
  const cognitiveActions = db.prepare(`SELECT source, payload_json FROM cognitive_events
    WHERE episode_id=? AND kind='action' ORDER BY id`).all(firstEpisode.id);
  assert.equal(cognitiveActions.length, 1);
  assert.equal(cognitiveActions[0].source, 'tool');
  const actionPayload = JSON.parse(cognitiveActions[0].payload_json);
  assert.equal(actionPayload.capabilityId, 'home.context.read');
  assert.equal(actionPayload.operationId, 'read');

  const partial = beginCognitiveEpisode({
    correlationId: 'production-shaped-free-partial', kind: 'autonomous_attention',
    modality: 'internal', source: 'autonomy_clock', sourceRef: 'scheduler',
    subjectKey: 'eva', focusKind: 'self_chosen_or_continuing_work',
    goalKey: 'free_attention', whyNow: 'vrije administratieve inspectie',
    expectedChange: 'geen effect',
    payload: { authenticFree: true, syntheticTest: false },
  });
  const partialPair = (tool, capabilityId, operationId, incomplete = false) => {
    recordCognitiveAction(partial.id, {
      tool, capabilityId, operationId, ok: true, verified: true, changed: false,
      executionStarted: true, effect: 'read', partial: incomplete,
      needsFollowup: incomplete, effectUncertain: false,
    });
    recordCognitiveOutcome(partial.id, {
      resultClass: tool, ok: true, verified: true, changed: false,
      executionStarted: true, effectType: 'read', partial: incomplete,
      needsFollowup: incomplete, effectUncertain: false,
    });
  };
  partialPair('zoek_gesprek', 'conversation.history.read', 'search', true);
  partialPair('toon_werk', 'work.episode.read', 'list');
  const partialTools = [
    { name: 'zoek_gesprek', execution_started: true, ok: true, verified: true,
      changed: false, partial: true, needs_followup: true,
      failure_class: 'output_truncated', effect_uncertain: false },
    { name: 'toon_werk', execution_started: true, ok: true, verified: true,
      changed: false, partial: false, needs_followup: false, effect_uncertain: false },
    { name: 'doel_bijhouden', execution_started: false, ok: false, verified: false,
      changed: false, partial: false, needs_followup: true,
      failure_class: 'policy', retryable: false, effect_uncertain: false },
  ];
  closeCognitiveEpisode(partial.id, {
    status: 'completed', resultClass: 'partial_execution',
    evidence: { contribution: null, tools: partialTools },
    effect: { replied: false, madeProgress: false, continuationId: null,
      readOnly: true, hadChanged: false, hadEffectUncertainty: false,
      hadDurableEffect: false },
  });
  assert.deepEqual(partialTools.map((tool) => tool.execution_started), [true, true, false]);
  assert.equal(db.prepare(`SELECT count(*) n FROM cognitive_events
    WHERE episode_id=? AND kind='action'`).get(partial.id).n, 2);
  assert.equal(db.prepare(`SELECT count(*) n FROM cognitive_events
    WHERE episode_id=? AND kind='outcome'`).get(partial.id).n, 2);
  const partialRow = db.prepare('SELECT status,result_class FROM cognitive_episodes WHERE id=?')
    .get(partial.id);
  assert.deepEqual(partialRow, { status: 'completed', result_class: 'partial_execution' });

  phase = 'second-free-read';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const second = await trigger(previousRun);
  assert.deepEqual(selections[1].selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });
  assert.deepEqual(second.tools.map((tool) => [tool.name, tool.ok, tool.verified, tool.changed]), [
    ['huis_status', true, true, false],
  ], JSON.stringify(second));
  assert.ok(second.noActionReason);
  assert.equal(stateReads, 2);
  assert.equal(interpretationRequests.length, 2);

  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (993,'2020-01-01T00:00:00.000Z','Test de gebonden aandacht.',
      'continuity-gate','actief','','2020-01-01T00:00:00.000Z','development',
      'continuity-gate','het gezin','','Kies hoogstens één veilige stap.',1,1)`).run();
  phase = 'attention';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: { kind: 'aspiration', id: 993 }, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });
  db.prepare('DELETE FROM aspirations WHERE id=993').run();

  phase = 'free-restore-after-attention';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });

  const signal = {
    id: 'free-no-action-signal-7392', ts: new Date().toISOString(),
    type: 'entity_state', key: 'binary_sensor.test_deur', text: 'Testdeur: off -> on',
  };
  const signalState = JSON.parse(db.prepare('SELECT value FROM kv WHERE key=?').get(KEY).value);
  kvSet(KEY, {
    ...signalState,
    next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [signal],
  });
  phase = 'signal';
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: selections.at(-1).selection.signal_batch_id, signal_count: 1,
  });
  assert.match(String(selections.at(-1).selection.signal_batch_id || ''), /^signals:/u);

  phase = 'free-restore-after-signal';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });

  const work = createWorkItem({
    source: 'self', title: 'Continuïteitsgrens werkcontext',
    goal: 'Controleer dat gebonden werk geen vrije context krijgt.',
    nextStep: 'Beoordeel de exacte werkcontext.', visibleToFamily: false,
  });
  const episode = ensureEpisodeForWorkItem(work, {
    originKind: 'self', originChannel: 'autonomy',
  });
  phase = 'work';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: null, work_id: work.id, goal_episode_id: episode.id,
    signal_batch_id: null, signal_count: 0,
  });
  db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE id=?")
    .run(work.id);

  phase = 'free-restore-after-work';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.deepEqual(selections.at(-1).selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });

  phase = 'synthetic-chat';
  const synthetic = await handleMessageDetailed({
    channel: 'web', sender: 'bewoner', text: 'synthetische continuïteitsgrenscontrole',
    messageContext: { syntheticTest: true, conversationId: 'web:free-no-action-synthetic' },
  });
  assert.equal(synthetic.reply, 'De testchat is ontvangen.');
  assert.equal(interpretationRequests.at(-1).phase, 'synthetic-chat');
  db.close();
}

test('echte vrije no-action wordt privacy-arm in de volgende vrije schedulerbeurt meegenomen', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-free-no-action-runtime-'));
  fs.writeFileSync(path.join(dataDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${freeNoActionRuntimeContinuityScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env,
      EVA_DATA_DIR: dataDir,
      EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only-openai-key',
      SLEEP_START: '00:00',
      WAKE_TIME: '00:00',
      TMPDIR: '/tmp',
    },
  });
  fs.rmSync(dataDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
