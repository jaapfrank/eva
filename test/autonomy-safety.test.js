import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-safety-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'bounded';
process.env.OPENAI_API_KEY = 'test-only';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
delete process.env.EVA_AUTONOMY_CODE_EXECUTION;

const { db } = await import('../src/db.js');
const { executeTool } = await import('../src/mind/tools.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('de toolgrens blokkeert autonome code ook als een model de tool toch aanroept', async () => {
  const raw = await executeTool('schrijf_bestand', {
    pad: 'data/workspace/mag-niet.txt',
    inhoud: 'niet schrijven',
  }, { mode: 'autonomy' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, false);
  assert.match(result.evidence.summary, /standaard uitgeschakeld/);
  assert.equal(fs.existsSync(path.join(dataDir, 'workspace', 'mag-niet.txt')), false);
});

async function schedulerIsolationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { config } = await import('./src/config.js');
  const { db, kvGet, kvSet, claimTurnToolExecution } = await import('./src/db.js');
  const { agentStatus } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const { createDurableWork, goalEpisodeById } = await import('./src/mind/portfolio.js');
  const entityId = 'sensor.scheduler_resume_probe';
  const goal = `Lees ${entityId} actueel en sluit dit werk met bewijs.`;
  const pending = [{ id: 'pending-door', ts: new Date().toISOString(),
    key: 'binary_sensor.scheduler_door', type: 'entity_state', text: 'Deur veranderde' },
  { id: 'pending-temperature', ts: new Date().toISOString(),
    key: 'sensor.up_sense_temperature', type: 'entity_state', text: 'Temperatuur veranderde' }];
  for (const [name, seq] of [['eva_work_items', 91], ['eva_goal_episodes', 58]]) {
    db.prepare('DELETE FROM sqlite_sequence WHERE name=?').run(name);
    db.prepare('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)').run(name, seq);
  }
  const { workItem, episode } = createDurableWork({ source: 'autonomy', owner: 'Eva',
    title: `Controleer ${entityId}`, goal, nextStep: `Lees ${entityId} exact.`,
    definitionOfDone: 'De actuele sensorstate is vastgelegd.', priority: 100,
  }, { originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone' });
  assert.deepEqual([workItem.id, episode.id], [92, 59]);
  kvSet('eva_autonomy_scheduler_v1', { pending_signals: pending,
    next_run_ts: new Date(Date.now() - 1_000).toISOString() });

  const timers = [], wait = globalThis.setTimeout.bind(globalThis);
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  let providerCalls = 0, understandingCalls = 0, sawWorkContext = false;
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(config.haUrl)) {
      const requestedEntityId = decodeURIComponent(String(url).split('/api/states/')[1] || entityId);
      return new Response(JSON.stringify({
      entity_id: requestedEntityId, state: 'ready', attributes: { friendly_name: 'Scheduler resume probe' },
      last_updated: new Date().toISOString(),
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    providerCalls += 1;
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') return completed([{
      type: 'message', role: 'assistant', content: [{ type: 'output_text',
        text: 'De sensorread is vastgelegd en het werk is afgerond.' }],
    }]);
    understandingCalls += 1;
    const payload = JSON.parse(request.input[0].content[0].text);
    if (payload.context.signalBatch) {
      assert.deepEqual(autonomyStatus().current_selection, {
        attention: null, work_id: null, goal_episode_id: null,
        signal_batch_id: payload.context.signalBatch.batchId,
        signal_count: pending.length,
      });
      assert.deepEqual(payload.context.signalBatch.signals.map((signal) => signal.key),
        pending.map((signal) => signal.key));
      const actions = pending.map((signal) => ({
        capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
        semanticInput: { entityId: signal.key },
        sourceSpans: [{ start: 0, end: payload.turn.text.length }],
      }));
      const raw = { dialogueAct: 'command', goal: { statement: 'Beoordeel beide huissignalen.',
        outcome: 'Beide actuele entiteiten zijn uitgelezen en beoordeeld.', beneficiary: 'household' },
      conditions: [], provenance: { sourceSpans: [{ start: 0, end: payload.turn.text.length }],
        contextHashes: [] }, confidence: 0.99, actions,
      signalAssessment: { disposition: 'acted',
        assessment: 'Ik heb beide fysieke toestanden aangepast.' },
      requestedEffects: ['read', 'persistent'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'none', target: 'none' }, proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'act', requirementIds: ['r1', 'r2', 'r3'], replyDraft: null } };
      return completed([{ type: 'function_call', call_id: 'assess-current-signal-batch',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw) }]);
    }
    assert.equal(payload.context.signalBatch, undefined);
    assert.deepEqual(autonomyStatus().current_selection, {
      attention: null, work_id: workItem.id, goal_episode_id: episode.id,
      signal_batch_id: null, signal_count: 0,
    });
    assert.deepEqual(payload.context.pendingSignals || [], []);
    assert.doesNotMatch(payload.turn.text, /up_sense_temperature|pending-temperature/u);
    sawWorkContext = payload.turn.text.includes(entityId);
    const readText = `Lees ${entityId} actueel`;
    const closeText = 'sluit dit werk met bewijs';
    const readStart = payload.turn.text.indexOf(readText);
    const closeStart = payload.turn.text.indexOf(closeText);
    assert.ok(readStart >= 0 && closeStart >= 0, payload.turn.text);
    const raw = { dialogueAct: 'command', goal: { statement: `Controleer ${entityId}.`,
      outcome: 'De actuele sensorstate is vastgelegd en het werk is gesloten.', beneficiary: 'household' },
    conditions: [], provenance: { sourceSpans: [{ start: readStart,
      end: closeStart + closeText.length }], contextHashes: [] }, confidence: 0.99,
    actions: [{ capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId }, sourceSpans: [{ start: readStart, end: readStart + readText.length }] },
    { capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request',
      semanticInput: { id: episode.id, resultaat: 'De actuele sensorstate is vastgelegd.' },
      sourceSpans: [{ start: closeStart, end: closeStart + closeText.length }] }],
    requestedEffects: ['read', 'persistent'], deniedEffects: ['home', 'external', 'runtime', 'voice', 'workspace'],
    temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
    delivery: { mode: 'none', target: 'none' }, proofNeeds: ['source_read'], ambiguities: [],
    firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null } };
    return completed([{ type: 'function_call', call_id: 'resume-without-signals',
      name: 'commit_turn_understanding', arguments: JSON.stringify(raw) }]);
  };

  startAutonomy();
  timers.find((item) => !item.cancelled).callback();
  let deadline = Date.now() + 8_000;
  while (db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(workItem.id).status !== 'done'
    && Date.now() < deadline) await new Promise((resolve) => wait(resolve, 10));
  await new Promise((resolve) => wait(resolve, 0));
  const resumed = agentStatus().last_autonomy_turn;
  assert.equal(sawWorkContext, true);
  assert.deepEqual(resumed.tools.map((tool) => tool.name), ['ha_lees', 'werk_bijhouden'],
    JSON.stringify(resumed));
  const workAction = JSON.parse(db.prepare(`SELECT meta FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.tool')='werk_bijhouden'
    ORDER BY id DESC LIMIT 1`).get().meta);
  assert.equal(workAction.args.id, 92, 'het hostgebonden werk-id vervangt het model-episode-id');
  assert.equal(understandingCalls, 1, 'de typed projectie voorkomt een interpretatieretry');
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
  assert.deepEqual(kvGet('eva_autonomy_scheduler_v1').pending_signals, pending);
  assert.equal(autonomyStatus().last_result.signals_handled, false);
  assert.equal(autonomyStatus().current_selection, null,
    'een afgeronde schedulerbeurt laat geen oude selectie als actueel staan');

  const callsBeforeSignalTurn = providerCalls;
  const understandingsBeforeSignalTurn = understandingCalls;
  const ledgerRowsBeforeSignalTurn = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  const state = kvGet('eva_autonomy_scheduler_v1');
  state.next_run_ts = new Date(Date.now() - 1_000).toISOString();
  kvSet('eva_autonomy_scheduler_v1', state);
  const second = timers.filter((item) => !item.cancelled).at(-1);
  const firstRun = autonomyStatus().last_run_ts;
  second.callback(...second.args);
  deadline = Date.now() + 8_000;
  while (autonomyStatus().last_run_ts === firstRun && Date.now() < deadline) {
    await new Promise((resolve) => wait(resolve, 10));
  }
  assert.ok(providerCalls > callsBeforeSignalTurn,
    'de actuele deur- en temperatuurbatch bereikt de interpreter');
  const signalTurn = agentStatus().last_autonomy_turn;
  assert.deepEqual(signalTurn.tools.map((tool) => tool.name),
    ['ha_lees', 'ha_lees', 'signalen_afhandelen'], JSON.stringify(signalTurn));
  const hostAssessment = 'De actuele toestand is uitsluitend via de geselecteerde uitlezingen beoordeeld; er is in deze beurt geen fysieke handeling uitgevoerd.';
  const signalLedger = db.prepare(`SELECT execution_key,turn_id,tool,args,semantic_binding,status,
      semantic_hash,intent_record_sha256,output_sha256,completion_record_sha256,output
    FROM turn_tool_executions
    WHERE turn_id=? ORDER BY rowid`).all(signalTurn.turnId);
  assert.deepEqual(signalLedger.map(({ tool, status }) => ({ tool, status })), [
    { tool: 'ha_lees', status: 'done' },
    { tool: 'ha_lees', status: 'done' },
    { tool: 'signalen_afhandelen', status: 'done' },
  ]);
  for (const row of signalLedger) for (const field of [
    'semantic_hash', 'intent_record_sha256', 'output_sha256', 'completion_record_sha256',
  ]) assert.match(row[field], /^[a-f0-9]{64}$/u, `${row.tool}:${field}`);
  const assessment = JSON.parse(signalLedger.at(-1).output);
  assert.equal(assessment.ok, true);
  assert.equal(assessment.verified, true);
  assert.equal(assessment.data.contract, 'SignalAssessmentReceipt/v1');
  assert.equal(assessment.data.disposition, 'observed');
  assert.equal(assessment.data.assessment, hostAssessment);
  assert.deepEqual(JSON.parse(signalLedger.at(-1).args),
    { dispositie: 'observed', oordeel: hostAssessment });
  const assessmentAction = JSON.parse(db.prepare(`SELECT meta FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?
      AND json_extract(meta,'$.tool')='signalen_afhandelen'
    ORDER BY id DESC LIMIT 1`).get(signalTurn.turnId).meta);
  assert.deepEqual(assessmentAction.args,
    { dispositie: 'observed', oordeel: hostAssessment });
  const terminal = signalLedger.at(-1);
  const replay = claimTurnToolExecution({
    executionKey: terminal.execution_key, turnId: terminal.turn_id, tool: terminal.tool,
    args: JSON.parse(terminal.args), semanticBinding: JSON.parse(terminal.semantic_binding),
  });
  assert.equal(replay.claimed, false);
  assert.equal(replay.row.output, terminal.output);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM turn_tool_executions
    WHERE turn_id=? AND tool='signalen_afhandelen'`).get(signalTurn.turnId).count, 1);
  assert.equal(understandingCalls - understandingsBeforeSignalTurn, 1,
    'de live batch gebruikt exact één interpretatie');
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count
    - ledgerRowsBeforeSignalTurn, 3, 'twee reads en één assessment leveren exact drie nieuwe rows');
  assert.deepEqual(kvGet('eva_autonomy_scheduler_v1').pending_signals, []);
  assert.equal(autonomyStatus().last_result.signals_acknowledged, 2);
  assert.equal(autonomyStatus().current_selection, null);
  db.close();
}

async function schedulerReleaseFailureScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { createDurableWork } = await import('./src/mind/portfolio.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const { workItem, episode } = createDurableWork({
    source: 'autonomy', owner: 'Eva', title: 'Lease-cleanupproef',
    goal: 'Bewaar schedulerobservability ook als leasevrijgave faalt.',
    nextStep: 'Start één gecontroleerde hervatbeurt.', priority: 100,
  }, { originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone' });
  kvSet('eva_autonomy_scheduler_v1', {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
  });

  const wait = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  let releaseFailureArmed = false;
  globalThis.fetch = async () => {
    if (!releaseFailureArmed) {
      assert.deepEqual(autonomyStatus().current_selection, {
        attention: null, work_id: workItem.id, goal_episode_id: episode.id,
        signal_batch_id: null, signal_count: 0,
      });
      db.exec(`CREATE TRIGGER force_scheduler_release_failure
        BEFORE UPDATE OF lease_owner ON eva_work_items
        WHEN OLD.id=${Number(workItem.id)}
          AND OLD.lease_owner IS NOT NULL AND NEW.lease_owner IS NULL
        BEGIN SELECT RAISE(ABORT, 'forced_release_failure'); END`);
      releaseFailureArmed = true;
    }
    return new Response(JSON.stringify({ error: {
      code: 'forced_provider_reject', param: 'none', message: 'test',
    } }), { status: 400, headers: { 'content-type': 'application/json' } });
  };

  startAutonomy();
  timers.find((item) => !item.cancelled).callback();
  const deadline = Date.now() + 5_000;
  while ((!releaseFailureArmed || autonomyStatus().running) && Date.now() < deadline) {
    await new Promise((resolve) => wait(resolve, 10));
  }
  await new Promise((resolve) => wait(resolve, 0));
  assert.equal(releaseFailureArmed, true);
  assert.equal(autonomyStatus().running, false,
    'running wordt ook na een exception uit leasevrijgave onvoorwaardelijk gewist');
  assert.equal(autonomyStatus().current_selection, null,
    'de authentieke selectie mag na een release-exception nooit stale blijven');
  assert.ok(db.prepare('SELECT lease_owner FROM eva_work_items WHERE id=?')
    .get(workItem.id).lease_owner,
  'de nog aanwezige lease bewijst dat de geforceerde release werkelijk gooide');
  db.close();
}

test('de scheduler hervat gekozen werk en handelt daarna de actuele deur- en temperatuurbatch volledig af', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-scheduler-signals-'));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${schedulerIsolationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 35_000,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test('schedulerselectie wordt ook bij falende leasevrijgave onvoorwaardelijk gewist', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-scheduler-release-failure-'));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${schedulerReleaseFailureScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 15_000,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
