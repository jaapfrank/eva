import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function issuedAspirationCursorScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db } = await import('./src/db.js');
  const {
    dueAttention, settleIssuedAspirationAttention,
  } = await import('./src/mind/curiosity.js');
  const baseline = '2020-01-01T00:00:00.000Z';
  const insert = ({ id, created = baseline, text = 'Exact uitgegeven aspiratie.',
    lastTouched = baseline } = {}) => db.prepare(`INSERT INTO aspirations
      (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
        evidence,next_step,cadence_hours,priority)
      VALUES (?,?,?,'cursor-canary','actief','tool-authored',?,'development',?,
        'het gezin','','Voer één begrensde stap uit.',1,1)`).run(
    id, created, text, lastTouched, `cursor-${id}`,
  );
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();

  insert({ id: 901 });
  const issued = dueAttention();
  assert.equal(Object.isFrozen(issued), true);
  assert.equal(settleIssuedAspirationAttention({ ...issued }), false,
    'een zelfgemaakte shape heeft geen settlement-authority');
  assert.equal(settleIssuedAspirationAttention(structuredClone(issued)), false,
    'een clone heeft geen settlement-authority');
  assert.equal(settleIssuedAspirationAttention(issued), true);
  const firstSettlement = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=901',
  ).get();
  assert.equal(firstSettlement.progress, 'tool-authored');
  assert.notEqual(firstSettlement.last_touched, baseline);
  assert.equal(settleIssuedAspirationAttention(issued), false,
    'het exacte token is na succesvolle settlement one-shot verbruikt');
  assert.deepEqual(db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=901',
  ).get(), firstSettlement);

  db.prepare('DELETE FROM aspirations').run();
  insert({ id: 902 });
  const conflicted = dueAttention();
  db.prepare('UPDATE aspirations SET text=? WHERE id=902').run('Gelijktijdig gewijzigd.');
  assert.equal(settleIssuedAspirationAttention(conflicted), false,
    'inhoudsdrift maakt de CAS nul');
  db.prepare('UPDATE aspirations SET text=? WHERE id=902').run('Exact uitgegeven aspiratie.');
  assert.equal(settleIssuedAspirationAttention(conflicted), false,
    'ook een gecorrigeerde rij kan het verbruikte conflicttoken niet herhalen');
  assert.deepEqual(db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=902',
  ).get(), { progress: 'tool-authored', last_touched: baseline });

  db.prepare('DELETE FROM aspirations').run();
  insert({ id: 903 });
  const replaced = dueAttention();
  db.prepare('DELETE FROM aspirations WHERE id=903').run();
  insert({ id: 903, created: '2021-01-01T00:00:00.000Z' });
  assert.equal(settleIssuedAspirationAttention(replaced), false,
    'delete/reinsert met dezelfde id, tekst en cursor faalt op created_ts');
  assert.equal(db.prepare(
    'SELECT last_touched FROM aspirations WHERE id=903',
  ).get().last_touched, baseline);
  db.close();
}

async function escapedSchedulerFailureScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const schedulerKey = 'eva_autonomy_scheduler_v1';
  const baseline = '2020-01-01T00:00:00.000Z';
  const realSetTimeout = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage: {
      input_tokens: 1_000, output_tokens: 100, total_tokens: 1_100,
    } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (929,?,'Blijf na een ontsnapte schedulerfout due.','restart-canary','actief',
      'tool-authored-before-crash',?,'development','restart-canary','het gezin','',
      'Probeer na restart opnieuw.',1,1)`).run(baseline, baseline);
  kvSet(schedulerKey, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    const payload = JSON.parse(request.input[0].content[0].text);
    const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = payload.turn.text.indexOf(sentence);
    return completed([{ type: 'function_call', call_id: 'restart-valid-silent',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'inform', goal: { statement: 'Sla deze richting nu veilig over.',
          outcome: 'De aandacht heeft geen onbewezen effect.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans: [{ start,
          end: start + sentence.length }], contextHashes: [] }, confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
      }) }]);
  };
  db.exec(`CREATE TEMP TRIGGER abort_all_scheduler_publication
    BEFORE UPDATE OF value ON kv
    WHEN OLD.key='eva_autonomy_scheduler_v1'
    BEGIN
      SELECT RAISE(ABORT, 'escaped scheduler publication failure');
    END`);
  startAutonomy();
  const first = timers.find((item) => !item.cancelled);
  assert.ok(first);
  first.callback(...first.args);
  const deadline = Date.now() + 8_000;
  let escapedRow = null;
  while (Date.now() < deadline) {
    escapedRow = db.prepare(`SELECT * FROM timeline WHERE kind='system'
      AND content LIKE '%Zelfstandige planner liep vast:%'
      ORDER BY id DESC LIMIT 1`).get();
    if (escapedRow && timers.length >= 2 && autonomyStatus().running === false) break;
    await new Promise((resolve) => realSetTimeout(resolve, 10));
  }
  assert.match(escapedRow?.content || '', /escaped scheduler publication failure/u);
  assert.deepEqual(db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=929',
  ).get(), { progress: 'tool-authored-before-crash', last_touched: baseline });
  db.close();
  process.exit(23);
}

async function restartedAspirationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db } = await import('./src/db.js');
  const {
    dueAttention, settleIssuedAspirationAttention,
  } = await import('./src/mind/curiosity.js');
  const baseline = '2020-01-01T00:00:00.000Z';
  assert.deepEqual(db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=929',
  ).get(), { progress: 'tool-authored-before-crash', last_touched: baseline });
  const freshlyIssued = dueAttention();
  assert.deepEqual(freshlyIssued, {
    kind: 'aspiration', id: 929, text: 'Blijf na een ontsnapte schedulerfout due.',
  });
  assert.equal(settleIssuedAspirationAttention(freshlyIssued), true,
    'na restart krijgt dezelfde due rij een vers exact settlementtoken');
  assert.equal(db.prepare('SELECT progress FROM aspirations WHERE id=929').get().progress,
    'tool-authored-before-crash');
  db.close();
}

async function yieldedAspirationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const schedulerKey = 'eva_autonomy_scheduler_v1';
  const baseline = '2020-01-01T00:00:00.000Z';
  const realSetTimeout = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage: {
      input_tokens: 1_000, output_tokens: 100, total_tokens: 1_100,
    } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });

  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (919,?,'Laat bewonerspreemptie deze aspiratiecursor ongemoeid laten.',
      'preemption-canary','actief','Eerdere tool-authored voortgang.',?,'development',
      'aspiration-preemption-canary','het gezin','','Probeer later opnieuw.',1,1)`).run(
    baseline, baseline,
  );
  kvSet(schedulerKey, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  const actionRowsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count;
  const executionsBefore = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  let releaseAspirationProvider;
  let signalAspirationProvider;
  const aspirationProviderStarted = new Promise((resolve) => {
    signalAspirationProvider = resolve;
  });
  let aspirationCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
    const request = JSON.parse(String(options.body || '{}'));
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    if (turnPayload.context.selfAttention?.kind !== 'aspiration') {
      const end = turnPayload.turn.text.length;
      return completed([{ type: 'function_call', call_id: 'interactive-preemption-reply',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'chat', goal: { statement: turnPayload.turn.text,
            outcome: 'De bewoner krijgt direct antwoord.', beneficiary: 'requester' },
          conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
          confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'reply', requirementIds: [],
            replyDraft: 'Ik ben er; deze bewonersbeurt gaat voor.' },
        }) }]);
    }
    aspirationCalls += 1;
    assert.deepEqual(autonomyStatus().current_selection?.attention, {
      kind: 'aspiration', id: 919,
    });
    const positive = turnPayload.turn.text.split('\n\n').at(-1);
    const start = turnPayload.turn.text.indexOf(positive);
    const providerResult = completed([{ type: 'function_call',
      call_id: 'preempted-aspiration-result', name: 'commit_turn_understanding',
      arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Bekijk deze aspiratie.',
          outcome: 'De richting is veilig beoordeeld.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans: [{ start,
          end: start + positive.length }], contextHashes: [] }, confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
      }),
    }]);
    signalAspirationProvider();
    return new Promise((resolve) => {
      releaseAspirationProvider = () => resolve(providerResult);
    });
  };

  startAutonomy();
  const timer = timers.find((item) => !item.cancelled);
  assert.ok(timer, 'de publieke scheduler moet de aspiration-beurt bewapenen');
  timer.callback(...timer.args);
  await aspirationProviderStarted;
  const epochBeforeUser = agentStatus().interaction_epoch;
  await handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text: 'Kun je nu luisteren?',
    messageContext: { conversationId: 'web:aspiration-preemption', syntheticTest: true },
  });
  assert.equal(agentStatus().interactive_pending, 0);
  assert.equal(agentStatus().interaction_epoch, epochBeforeUser + 1);
  releaseAspirationProvider();
  const deadline = Date.now() + 10_000;
  while (!autonomyStatus().last_run_ts && Date.now() < deadline) {
    await new Promise((resolve) => realSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => realSetTimeout(resolve, 0));

  assert.equal(aspirationCalls, 1);
  assert.equal(agentStatus().last_autonomy_turn.yielded, true);
  assert.equal(agentStatus().last_autonomy_turn.superseded, true);
  assert.deepEqual(agentStatus().last_autonomy_turn.tools, []);
  assert.equal(autonomyStatus().last_result.yielded, true);
  assert.deepEqual(db.prepare(
    'SELECT status,progress,last_touched FROM aspirations WHERE id=919',
  ).get(), {
    status: 'actief', progress: 'Eerdere tool-authored voortgang.', last_touched: baseline,
  }, 'een werkelijk yielded aspiratiebeurt verbruikt haar due-cursor niet');
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count,
    actionRowsBefore);
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    executionsBefore);
  assert.equal(autonomyStatus().current_selection, null);
  db.close();
}

test('bewonerspreemptie laat aspiration-attention werkelijk due', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-aspiration-preempt-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${yieldedAspirationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test('aspiration-attention cursor is exact, one-shot en ABA-bestendig', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-aspiration-cursor-'));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${issuedAspirationCursorScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    env: { ...process.env, EVA_DATA_DIR: childDir },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test('ontsnapte schedulerfout laat de aspiratie over een echte restart due', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-aspiration-restart-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const crashed = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${escapedSchedulerFailureScenario.toString()})().catch((error) => { console.error(error); process.exit(24); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  assert.equal(crashed.status, 23, `${crashed.stdout}\n${crashed.stderr}`);
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${restartedAspirationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    env: { ...process.env, EVA_DATA_DIR: childDir },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(restarted.status, 0, `${restarted.stdout}\n${restarted.stderr}`);
});
