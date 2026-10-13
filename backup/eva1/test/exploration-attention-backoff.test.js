import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function issuedExplorationCursorScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db } = await import('./src/db.js');
  const {
    dueAttention,
    explorationById,
    listExplorations,
    nextDeferredExplorationRetryTs,
    noteExplorationAction,
    settleIssuedAspirationAttention,
    settleIssuedExplorationAttention,
    updateExploration,
  } = await import('./src/mind/curiosity.js');
  const { __autonomyScheduleTest } = await import('./src/mind/autonomy.js');
  const baseline = '2020-01-01T00:00:00.000Z';
  const insertAspiration = (id, priority = 1) => db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (?,?,?,'fairness-canary','actief','tool-authored',?,'development',?,
      'het gezin','aspiration-evidence','Neem één eerlijke beurt.',1,?)`).run(
    id, baseline, `Due aspiratie #${id}`, baseline, `fairness-${id}`, priority,
  );
  const insertExploration = ({
    id,
    question = `Exact uitgegeven exploration #${id}`,
    created = baseline,
    updated = baseline,
    lastSelected = baseline,
    deferred = null,
    wake = null,
    status = 'chosen',
    revision = 8,
    aspirationId = null,
    actionCount = 1,
    maxActions = 6,
  }) => db.prepare(`INSERT INTO eva_explorations
    (id,created_ts,updated_ts,source,source_episode_id,aspiration_id,topic_key,
      topic_anchors_json,topic_features_json,question,why_now,hypothesis,
      expected_observation,stop_rule,max_actions,action_count,empty_evidence_streak,
      status,evidence_json,result,decision,next_wake_ts,last_selected_ts,
      scheduler_deferred_ts,revision)
    VALUES (?,?,?,'self',NULL,?,'fairness-topic','["artifact:fairness"]','{}',?,
      'Dit is een duurzame fairnessproef.','De cursor blijft exact.',
      'Een stille beurt wijzigt alleen hostschedulingmetadata.',
      'Stop zodra een andere richting een beurt kreeg.',?,?,0,?,
      '[{"text":"tool-authored evidence"}]','Bestaande inhoudelijke uitkomst.',
      'Bestaand inhoudelijk besluit.',?,?,?,?)`).run(
    id, created, updated, aspirationId, question, maxActions, actionCount,
    status, wake, lastSelected, deferred, revision,
  );
  const clear = () => {
    db.prepare('DELETE FROM eva_explorations').run();
    db.prepare('DELETE FROM aspirations').run();
  };
  const raw = (id) => db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(id);
  const scheduling = (id) => db.prepare(`SELECT status,next_wake_ts,last_selected_ts,
    scheduler_deferred_ts,updated_ts,revision FROM eva_explorations WHERE id=?`).get(id);
  const content = (id) => db.prepare(`SELECT aspiration_id,topic_key,topic_anchors_json,
    topic_features_json,question,why_now,hypothesis,expected_observation,stop_rule,
    max_actions,action_count,empty_evidence_streak,evidence_json,result,decision
    FROM eva_explorations WHERE id=?`).get(id);

  for (const invalidCompletion of [null, undefined, false, 0, 'geen-result-object']) {
    assert.equal(__autonomyScheduleTest.technicalAutonomyFailure(invalidCompletion), true,
      'een null/primitieve completion is een technische fout, geen stille beurt');
    assert.equal(__autonomyScheduleTest.shouldSettleAttention(invalidCompletion), false);
    assert.equal(__autonomyScheduleTest.shouldBackoffExploration(invalidCompletion), false);
  }
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({}), true);
  for (const failure of [
    { error: '503' },
    { interpretationFailed: true },
    { failureStage: 'understanding' },
  ]) {
    assert.equal(__autonomyScheduleTest.technicalAutonomyFailure(failure), true);
    assert.equal(__autonomyScheduleTest.shouldSettleAttention(failure), false);
    assert.equal(__autonomyScheduleTest.shouldBackoffExploration(failure), false);
  }
  assert.equal(__autonomyScheduleTest.technicalAutonomyFailure({}), false);
  assert.equal(__autonomyScheduleTest.shouldSettleAttention({}), true);
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({ noActionReason: 'stil' }), true);
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({ yielded: true }), false);
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({ superseded: true }), false);
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({ contribution: { tool: 'read' } }), false);
  assert.equal(__autonomyScheduleTest.shouldBackoffExploration({
    progress: { made_progress: true },
  }), false);

  clear();
  insertAspiration(9, 1);
  insertAspiration(10, 0.9);
  insertExploration({ id: 701, aspirationId: 9 });
  const linkedBefore = db.prepare('SELECT * FROM aspirations WHERE id=9').get();
  const contentBefore = content(701);
  const issued = dueAttention();
  assert.deepEqual(issued, {
    kind: 'exploration', id: 701, text: 'Exact uitgegeven exploration #701',
  });
  assert.equal(Object.isFrozen(issued), true);
  assert.equal(settleIssuedExplorationAttention({ ...issued }), false,
    'een zelfgemaakte shape heeft geen settlement-authority');
  assert.equal(settleIssuedExplorationAttention(structuredClone(issued)), false,
    'een clone heeft geen settlement-authority');
  assert.equal(settleIssuedExplorationAttention(issued), true);
  const settled = scheduling(701);
  assert.equal(settled.status, 'chosen');
  assert.equal(settled.next_wake_ts, null);
  assert.equal(settled.updated_ts, baseline);
  assert.equal(settled.revision, 9);
  assert.equal(settled.last_selected_ts, settled.scheduler_deferred_ts);
  assert.ok(Date.parse(settled.scheduler_deferred_ts) > Date.parse(baseline));
  assert.deepEqual(content(701), contentBefore,
    'hostdeferral laat alle explorationinhoud bytegelijk');
  assert.deepEqual(db.prepare('SELECT * FROM aspirations WHERE id=9').get(), linkedBefore,
    'hostdeferral raakt de gekoppelde aspiratie niet');
  assert.equal(settleIssuedExplorationAttention(issued), false,
    'het exacte token is one-shot');
  assert.equal(Object.hasOwn(explorationById(701), 'scheduler_deferred_ts'), false);
  assert.equal(Object.hasOwn(listExplorations({ activeOnly: true })[0],
    'scheduler_deferred_ts'), false, 'de hostmarker zit niet in publieke projecties');

  const ninth = dueAttention();
  assert.deepEqual(ninth, { kind: 'aspiration', id: 9, text: 'Due aspiratie #9' },
    'na exact één lege explorationbeurt wint de hoogste due aspiratie');
  assert.equal(settleIssuedAspirationAttention(ninth), true);
  const tenth = dueAttention();
  assert.deepEqual(tenth, { kind: 'aspiration', id: 10, text: 'Due aspiratie #10' },
    'de tweede due aspiratie wint vóór een gemarkeerde exploration');
  assert.equal(settleIssuedAspirationAttention(tenth), true);
  assert.equal(dueAttention(), null,
    'zonder due aspiratie opent de host vóór de retrytijd geen generieke attention');
  assert.ok(Date.parse(nextDeferredExplorationRetryTs()) > Date.now());

  clear();
  insertAspiration(11, 1);
  insertExploration({ id: 702, deferred: baseline });
  assert.deepEqual(dueAttention(), {
    kind: 'aspiration', id: 11, text: 'Due aspiratie #11',
  }, 'zelfs een verstreken marker blijft onder een due aspiratie gerangschikt');

  clear();
  insertExploration({ id: 703, deferred: baseline });
  assert.deepEqual(dueAttention(), {
    kind: 'exploration', id: 703, text: 'Exact uitgegeven exploration #703',
  }, 'zonder due aspiratie mag een verstreken marker begrensd opnieuw proberen');

  clear();
  insertAspiration(12, 1);
  insertExploration({ id: 704, deferred: 'geen-timestamp' });
  assert.deepEqual(dueAttention(), {
    kind: 'exploration', id: 704, text: 'Exact uitgegeven exploration #704',
  }, 'een corrupte marker failt open en verbergt de actieve exploration niet permanent');

  clear();
  insertAspiration(13, 1);
  insertExploration({ id: 711, deferred: '2099' });
  assert.deepEqual(dueAttention(), {
    kind: 'exploration', id: 711, text: 'Exact uitgegeven exploration #711',
  }, 'een parsebare maar niet-canonieke future marker failt eveneens open');

  clear();
  insertExploration({ id: 705 });
  const drifted = dueAttention();
  db.prepare('UPDATE eva_explorations SET question=? WHERE id=705').run(
    'Gelijktijdig inhoudelijk gewijzigd.',
  );
  assert.equal(settleIssuedExplorationAttention(drifted), false,
    'directe question-drift maakt de CAS nul');
  db.prepare('UPDATE eva_explorations SET question=? WHERE id=705').run(
    'Exact uitgegeven exploration #705',
  );
  assert.equal(settleIssuedExplorationAttention(drifted), false,
    'het conflicttoken blijft ook na shape-ABA verbruikt');
  assert.equal(scheduling(705).scheduler_deferred_ts, null);

  clear();
  insertExploration({ id: 706 });
  const toolWins = dueAttention();
  const toolWake = new Date(Date.now() + 86_400_000).toISOString();
  const toolUpdate = updateExploration(706, {
    status: 'waiting', evidence: 'Tool schreef deze wachtreden inhoudelijk.',
    nextWakeTs: toolWake, expectedRevision: 8,
  });
  assert.equal(toolUpdate.ok, true);
  const toolAuthored = raw(706);
  assert.equal(toolAuthored.scheduler_deferred_ts, null);
  assert.equal(toolAuthored.updated_ts, toolAuthored.last_selected_ts,
    'canonieke update gebruikt één timestamp en wist hostdeferral');
  assert.equal(settleIssuedExplorationAttention(toolWins), false,
    'de toolrevisie wint van de host-settlement');
  assert.deepEqual(raw(706), toolAuthored,
    'settlement overschrijft geen tool-authored wake, status, bewijs of revisie');

  clear();
  insertExploration({ id: 707 });
  const actionWins = dueAttention();
  noteExplorationAction(707, {
    tool: 'lees_bestand', ok: true, verified: true, evidence: 'Exact teruggelezen bronfeit.',
  });
  const actionAuthored = raw(707);
  assert.equal(actionAuthored.scheduler_deferred_ts, null);
  assert.equal(settleIssuedExplorationAttention(actionWins), false);
  assert.deepEqual(raw(707), actionAuthored,
    'een read/tool-touch blijft volledig leidend over schedulerdeferral');

  clear();
  insertExploration({ id: 708 });
  const aba = dueAttention();
  db.prepare(`UPDATE eva_explorations SET updated_ts=?,revision=revision+1
    WHERE id=708`).run('2021-01-01T00:00:00.000Z');
  db.prepare(`UPDATE eva_explorations SET updated_ts=?,revision=revision+1
    WHERE id=708`).run(baseline);
  assert.equal(settleIssuedExplorationAttention(aba), false,
    'revisie-ABA kan de issued cursor niet herstellen');
  assert.deepEqual(scheduling(708), {
    status: 'chosen', next_wake_ts: null, last_selected_ts: baseline,
    scheduler_deferred_ts: null, updated_ts: baseline, revision: 10,
  });

  clear();
  insertExploration({ id: 712 });
  const replaced = dueAttention();
  db.prepare('DELETE FROM eva_explorations WHERE id=712').run();
  insertExploration({ id: 712, created: '2021-01-01T00:00:00.000Z' });
  assert.equal(settleIssuedExplorationAttention(replaced), false,
    'delete/reinsert met hetzelfde id faalt op created_ts');
  assert.equal(scheduling(712).scheduler_deferred_ts, null);

  clear();
  const futureWake = new Date(Date.now() + 86_400_000).toISOString();
  insertExploration({ id: 709, status: 'waiting', wake: futureWake });
  insertExploration({ id: 710, status: 'completed' });
  assert.equal(dueAttention(), null,
    'een future sleeper en terminale exploration worden nooit uitgegeven');
  assert.equal(nextDeferredExplorationRetryTs(), null);

  clear();
  insertExploration({ id: 720, updated: '2019-01-01T00:00:00.000Z',
    lastSelected: '2019-01-01T00:00:00.000Z', actionCount: 7, maxActions: 6 });
  insertExploration({ id: 721 });
  assert.deepEqual(dueAttention(), {
    kind: 'exploration', id: 721, text: 'Exact uitgegeven exploration #721',
  }, 'een niet-uitgeefbare oudere rij laat de volgende geldige exploration niet verhongeren');

  clear();
  insertExploration({ id: 722, status: 'running' });
  const noOpBefore = raw(722);
  const noOp = updateExploration(722, { status: 'running', expectedRevision: 8 });
  assert.equal(noOp.ok, true);
  assert.equal(noOp.changed, false,
    'een lege continuation-update normaliseert niet naar revisiechurn');
  assert.deepEqual(raw(722), noOpBefore);
  const invalidWait = updateExploration(722, {
    status: 'waiting', nextWakeTs: 'geen-iso', expectedRevision: 8,
  });
  assert.equal(invalidWait.ok, false);
  assert.equal(invalidWait.changed, false);
  assert.deepEqual(raw(722), noOpBefore,
    'ook een directe ongeldige wachtupdate maakt geen due wachtlus');

  clear();
  insertExploration({ id: 723, status: 'running' });
  db.prepare(`UPDATE eva_explorations SET updated_ts=?,status='evaluating',
    revision=revision+1 WHERE id=723`).run('2020-01-02T00:00:00.000Z');
  const staleWinner = raw(723);
  const stale = updateExploration(723, {
    status: 'completed', result: 'Deze stale kandidaat mag niets overschrijven.',
    expectedRevision: 8,
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.changed, false);
  assert.deepEqual(raw(723), staleWinner,
    'een stale CAS laat de volledige winnende rij bytegelijk');

  clear();
  insertExploration({ id: 724, status: 'running' });
  const originalPrepare = db.prepare;
  const ownPrepare = Object.getOwnPropertyDescriptor(db, 'prepare');
  let interleaved = false;
  Object.defineProperty(db, 'prepare', {
    configurable: true,
    value(sql) {
      const statement = originalPrepare.call(db, sql);
      if (interleaved
        || String(sql).trim() !== 'SELECT * FROM eva_explorations WHERE id=?') {
        return statement;
      }
      return {
        get(...params) {
          const selected = statement.get(...params);
          if (Number(params[0]) === 724) {
            interleaved = true;
            originalPrepare.call(db, `UPDATE eva_explorations
              SET updated_ts=?,status='evaluating',decision=?,revision=revision+1
              WHERE id=724`).run(
              '2020-01-03T00:00:00.000Z', 'De gelijktijdige schrijver won.',
            );
          }
          return selected;
        },
      };
    },
  });
  let raced;
  try {
    raced = updateExploration(724, {
      status: 'completed', result: 'De verliezende writer mag niet afronden.',
      expectedRevision: 8,
    });
  } finally {
    if (ownPrepare) Object.defineProperty(db, 'prepare', ownPrepare);
    else delete db.prepare;
  }
  assert.equal(interleaved, true, 'de test wijzigde de rij exact tussen SELECT en UPDATE');
  assert.equal(raced.ok, false);
  assert.equal(raced.changed, false);
  assert.equal(raw(724).status, 'evaluating');
  assert.equal(raw(724).revision, 9);
  assert.equal(raw(724).decision, 'De gelijktijdige schrijver won.');
  assert.equal(raw(724).result, 'Bestaande inhoudelijke uitkomst.',
    'de SQL-CAS overschrijft geen enkel veld na een echte interleaving');

  clear();
  insertExploration({ id: 725, status: 'running' });
  insertExploration({ id: 726, status: 'running', revision: 9 });
  const otherBefore = raw(726);
  const crossId = updateExploration(725, {
    status: 'completed', result: 'Alleen de exact gekozen rij wordt afgerond.',
    expectedRevision: 8,
  });
  assert.equal(crossId.ok, true);
  assert.equal(crossId.changed, true);
  assert.equal(raw(725).status, 'completed');
  assert.deepEqual(raw(726), otherBefore,
    'de atomaire CAS kan geen andere exploration-id raken');
  db.close();
}

async function legacyExplorationMigrationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const path = (await import('node:path')).default;
  const Database = (await import('better-sqlite3')).default;
  const databasePath = path.join(process.env.EVA_DATA_DIR, 'eva.db');
  const legacy = new Database(databasePath);
  legacy.exec(`CREATE TABLE eva_explorations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts TEXT NOT NULL, updated_ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'self', source_episode_id INTEGER, aspiration_id INTEGER,
    topic_key TEXT, topic_anchors_json TEXT NOT NULL DEFAULT '[]',
    topic_features_json TEXT NOT NULL DEFAULT '{}', question TEXT NOT NULL,
    why_now TEXT NOT NULL, hypothesis TEXT NOT NULL, expected_observation TEXT NOT NULL,
    stop_rule TEXT NOT NULL, max_actions INTEGER NOT NULL DEFAULT 6,
    action_count INTEGER NOT NULL DEFAULT 0, empty_evidence_streak INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'chosen', evidence_json TEXT NOT NULL DEFAULT '[]',
    result TEXT, decision TEXT, next_wake_ts TEXT, last_selected_ts TEXT,
    revision INTEGER NOT NULL DEFAULT 1
  );
  INSERT INTO eva_explorations
    (id,created_ts,updated_ts,source,question,why_now,hypothesis,expected_observation,
      stop_rule,status,evidence_json,result,decision,next_wake_ts,last_selected_ts,revision)
    VALUES (122,'2020-01-01T00:00:00.000Z','2020-01-02T00:00:00.000Z','self',
      'Bestaande slapende exploration #122','Bestaande reden','Bestaande hypothese',
      'Bestaande observatie','Bestaande stopregel','waiting','[{"text":"bewijs"}]',
      'Bestaand resultaat','Bestaand besluit','2099-01-01T00:00:00.000Z',
      '2020-01-02T00:00:00.000Z',7);`);
  legacy.prepare('UPDATE eva_explorations SET topic_features_json=? WHERE id=122').run(
    JSON.stringify({
      version: 9, subjects: [], focus_subjects: [], focus_subject: null,
      dimensions: [], focus_dimensions: [], contexts: [], components: [], effects: [],
      read_only: false, strong_ids: [],
    }),
  );
  const before = legacy.prepare('SELECT * FROM eva_explorations WHERE id=122').get();
  legacy.close();
  const { db } = await import('./src/db.js');
  await import('./src/mind/curiosity.js');
  const columns = db.prepare('PRAGMA table_info(eva_explorations)').all();
  assert.deepEqual(columns.filter((column) => column.name === 'scheduler_deferred_ts'), [{
    cid: columns.find((column) => column.name === 'scheduler_deferred_ts').cid,
    name: 'scheduler_deferred_ts', type: 'TEXT', notnull: 0, dflt_value: null, pk: 0,
  }]);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_explorations
    WHERE scheduler_deferred_ts IS NOT NULL`).get().count, 0);
  const after = db.prepare('SELECT * FROM eva_explorations WHERE id=122').get();
  assert.equal(after.scheduler_deferred_ts, null,
    'eerste start voegt uitsluitend een nullable hostmarker toe');
  delete after.scheduler_deferred_ts;
  assert.deepEqual(after, before,
    'de migratie laat alle bestaande #122 lifecycle- en inhoudsvelden bytegelijk');
  db.close();
}

async function publicSchedulerBackoffScenario() {
  const assert = (await import('node:assert/strict')).default;
  const fs = (await import('node:fs')).default;
  const path = (await import('node:path')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { llmRuntimeStatus } = await import('./src/brain/llm.js');
  const { agentStatus } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const KEY = 'eva_autonomy_scheduler_v1';
  const baseline = '2020-01-01T00:00:00.000Z';
  const realSetTimeout = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 500) return realSetTimeout(callback, 0, ...args);
    const handle = { callback, delay, args, cancelled: false, fired: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage: {
      input_tokens: 1_000, output_tokens: 100, total_tokens: 1_100,
    } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
  const action = (capabilityId, operationId, semanticInput, span) => ({
    capabilityId, operationId, polarity: 'request', semanticInput, sourceSpans: [span],
  });
  const understanding = (payload, actions = [], {
    decision = actions.length ? 'act' : 'silent', effects = null,
  } = {}) => {
    const start = payload.turn.text.indexOf(sentence);
    assert.ok(start >= 0, payload.turn.text);
    const span = { start, end: start + sentence.length };
    const projected = typeof actions === 'function' ? actions(span) : actions;
    return {
      dialogueAct: projected.length ? 'command' : 'inform',
      goal: { statement: projected.length ? 'Voer één begrensde stap uit.' : 'Sla deze stap bewust over.',
        outcome: projected.length ? 'De stap levert werkelijk bewijs.' : 'Geen onbewezen effect.',
        beneficiary: 'self' },
      conditions: [], provenance: { sourceSpans: [span], contextHashes: [] }, confidence: 0.99,
      actions: projected, requestedEffects: effects || (projected.length ? ['read'] : []),
      deniedEffects: [], temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: decision, requirementIds: projected.length ? ['r1'] : [],
        replyDraft: null },
    };
  };
  const insertAspiration = (id, priority) => db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (?,?,?,'public-fairness','actief','',?,'development',?,'het gezin','',
      'Neem één zichtbare beurt.',1,?)`).run(
    id, baseline, `Publieke aspiratie #${id}`, baseline, `public-${id}`, priority,
  );
  const insertExploration = (id, status = 'chosen') => db.prepare(`INSERT INTO eva_explorations
    (id,created_ts,updated_ts,source,topic_key,topic_anchors_json,topic_features_json,
      question,why_now,hypothesis,expected_observation,stop_rule,max_actions,
      action_count,empty_evidence_streak,status,evidence_json,result,decision,
      next_wake_ts,last_selected_ts,scheduler_deferred_ts,revision)
    VALUES (?,?,?,'self','public-fairness','["artifact:public"]','{}',?,
      'De publieke scheduler moet eerlijk blijven.','Een exacte beurt volstaat.',
      'De provideruitkomst bepaalt alleen hostdeferral.',
      'Stop na het vastgelegde schedulerbewijs.',6,0,0,?,'[{"text":"bestaand bewijs"}]',
      'Bestaand resultaat','Bestaand besluit',NULL,?,NULL,8)`).run(
    id, baseline, baseline, `Publieke exploration #${id}`, status, baseline,
  );
  const row = (id) => db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(id);
  const durableCounts = () => ({
    actions: db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count,
    contributions: db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='contribution'").get().count,
    executions: db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    cognitive: db.prepare(`SELECT count(*) AS count FROM sqlite_master
      WHERE type='table' AND name='cognitive_episodes'`).get().count
      ? db.prepare('SELECT count(*) AS count FROM cognitive_episodes').get().count : 0,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
  });
  const clearAttention = () => {
    db.prepare('DELETE FROM eva_explorations').run();
    db.prepare('DELETE FROM aspirations').run();
    db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  };
  const forceDue = () => {
    const current = autonomyStatus();
    kvSet(KEY, { ...current, next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
      daily_preempted_ms: 0 });
  };
  const assertTechnicalRetry = () => {
    const current = autonomyStatus();
    const delay = Date.parse(current.next_run_ts) - Date.parse(current.last_run_ts);
    assert.equal(current.last_result.technical_retry, true);
    assert.ok(delay >= 15_000 && delay <= 60_000,
      `een technische fout hoort binnen 15-60s te herstarten; kreeg ${delay}ms`);
  };
  let previousTimer = null;
  const nextTimer = () => timers.find((item) => (
    item !== previousTimer && !item.cancelled && !item.fired
  ));
  const fireRun = async () => {
    const beforeRun = autonomyStatus().last_run_ts;
    const timer = nextTimer();
    assert.ok(timer, 'de publieke scheduler moet opnieuw bewapend zijn');
    timer.fired = true;
    previousTimer = timer;
    timer.callback(...timer.args);
    const deadline = Date.now() + 12_000;
    while ((autonomyStatus().last_run_ts === beforeRun || autonomyStatus().running)
      && Date.now() < deadline) {
      await new Promise((resolve) => realSetTimeout(resolve, 10));
    }
    await new Promise((resolve) => realSetTimeout(resolve, 0));
    assert.notEqual(autonomyStatus().last_run_ts, beforeRun);
    return agentStatus().last_autonomy_turn;
  };
  const fireEarlyDefer = async () => {
    const beforeRun = autonomyStatus().last_run_ts;
    const timerCount = timers.length;
    const timer = nextTimer();
    assert.ok(timer);
    timer.fired = true;
    previousTimer = timer;
    timer.callback(...timer.args);
    const deadline = Date.now() + 3_000;
    while ((timers.length === timerCount
      || !autonomyStatus().last_result?.deferred_for_exploration_retry)
      && Date.now() < deadline) {
      await new Promise((resolve) => realSetTimeout(resolve, 10));
    }
    assert.equal(autonomyStatus().last_run_ts, beforeRun,
      'een vroege hostdefer publiceert geen verzonnen providerbeurt');
    assert.equal(autonomyStatus().last_result.deferred_for_exploration_retry, true);
  };

  let phase = 'silent';
  let providerCalls = 0;
  const observed = [];
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
    providerCalls += 1;
    if (phase === '503') return new Response('tijdelijk niet beschikbaar', { status: 503 });
    const request = JSON.parse(String(options.body || '{}'));
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    const payload = JSON.parse(request.input[0].content[0].text);
    observed.push({ phase, attention: structuredClone(payload.context.selfAttention) });
    if (phase === 'malformed') {
      return completed([{ type: 'function_call', call_id: 'malformed-understanding',
        name: 'commit_turn_understanding', arguments: '{}' }]);
    }
    if (phase === 'validation') {
      return completed([{ type: 'function_call', call_id: 'invalid-exploration-write',
        name: 'commit_turn_understanding', arguments: JSON.stringify(understanding(payload,
          (span) => [action('workspace.file.write', 'write', {
            pad: 'resultaat/onveilig.mjs',
          }, span)], { effects: ['workspace'] })) }]);
    }
    if (phase === 'read') {
      return completed([{ type: 'function_call', call_id: 'exploration-read',
        name: 'commit_turn_understanding', arguments: JSON.stringify(understanding(payload,
          (span) => [action('workspace.file.read', 'read', {
            pad: 'data/workspace/bron.md',
          }, span)])) }]);
    }
    return completed([{ type: 'function_call', call_id: `${phase}-silent`,
      name: 'commit_turn_understanding',
      arguments: JSON.stringify(understanding(payload)) }]);
  };

  clearAttention();
  insertExploration(801);
  kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  startAutonomy();
  const silentBefore = row(801);
  const silentCounts = durableCounts();
  const silentQuietRuns = Number(autonomyStatus().quiet_runs || 0);
  const silentTurn = await fireRun();
  assert.deepEqual(silentTurn.tools, []);
  const silentAfter = row(801);
  assert.equal(silentAfter.status, silentBefore.status);
  assert.equal(silentAfter.updated_ts, silentBefore.updated_ts);
  assert.equal(silentAfter.next_wake_ts, silentBefore.next_wake_ts);
  assert.equal(silentAfter.result, silentBefore.result);
  assert.equal(silentAfter.decision, silentBefore.decision);
  assert.equal(silentAfter.evidence_json, silentBefore.evidence_json);
  assert.equal(silentAfter.revision, silentBefore.revision + 1);
  assert.ok(silentAfter.scheduler_deferred_ts);
  assert.equal(silentAfter.scheduler_deferred_ts, silentAfter.last_selected_ts);
  const afterSilentCounts = durableCounts();
  assert.equal(afterSilentCounts.actions, silentCounts.actions);
  assert.equal(afterSilentCounts.executions, silentCounts.executions);
  assert.equal(afterSilentCounts.contributions, silentCounts.contributions);
  assert.equal(afterSilentCounts.work, silentCounts.work);
  assert.equal(autonomyStatus().quiet_runs, silentQuietRuns + 1,
    'een geldige stille semantische beurt blijft een gewone quiet run');

  const callsBeforeEarly = providerCalls;
  const attemptsBeforeEarly = llmRuntimeStatus().provider_request_attempts;
  const countsBeforeEarly = durableCounts();
  const beforeEarly = row(801);
  forceDue();
  await fireEarlyDefer();
  assert.equal(providerCalls, callsBeforeEarly,
    'vóór de retrytijd gebeurt nul provider- of tool-I/O');
  assert.equal(llmRuntimeStatus().provider_request_attempts, attemptsBeforeEarly);
  assert.deepEqual(durableCounts(), countsBeforeEarly,
    'vroege defer maakt ook nul ledger-, contribution-, cognitive- of workdelta');
  assert.deepEqual(row(801), beforeEarly, 'de vroege defer laat de rij exact ongemoeid');
  assert.equal(autonomyStatus().current_selection, null);
  assert.equal(Date.parse(autonomyStatus().next_run_ts),
    Date.parse(beforeEarly.scheduler_deferred_ts) + 15 * 60_000);

  insertAspiration(9, 1);
  insertAspiration(10, 0.9);
  forceDue();
  await fireRun();
  forceDue();
  await fireRun();
  assert.deepEqual(observed.filter((item) => item.phase === 'silent').map((item) => ({
    kind: item.attention.kind, id: item.attention.id,
  })), [
    { kind: 'exploration', id: 801 },
    { kind: 'aspiration', id: 9 },
    { kind: 'aspiration', id: 10 },
  ], 'publieke ticks geven #9 en #10 beide een kans vóór explorationretry');
  assert.notEqual(db.prepare('SELECT last_touched FROM aspirations WHERE id=9').get().last_touched,
    baseline);
  assert.notEqual(db.prepare('SELECT last_touched FROM aspirations WHERE id=10').get().last_touched,
    baseline);

  clearAttention();
  insertAspiration(18, 1);
  phase = 'malformed';
  forceDue();
  const aspirationBefore = db.prepare('SELECT * FROM aspirations WHERE id=18').get();
  const aspirationQuietRuns = Number(autonomyStatus().quiet_runs || 0);
  const malformedAspiration = await fireRun();
  assert.ok(malformedAspiration.error);
  assert.deepEqual(db.prepare('SELECT * FROM aspirations WHERE id=18').get(), aspirationBefore,
    'een technische interpretatiefout verbruikt de aspiration-cadans niet');
  assert.equal(autonomyStatus().quiet_runs, aspirationQuietRuns,
    'een technische interpretatiefout is geen quiet run');
  assertTechnicalRetry();

  clearAttention();
  insertExploration(802, 'evaluating');
  phase = 'validation';
  forceDue();
  const validationBefore = row(802);
  const validationCounts = durableCounts();
  const validationCalls = providerCalls;
  const validationQuietRuns = Number(autonomyStatus().quiet_runs || 0);
  const rejected = await fireRun();
  assert.deepEqual(rejected.tools, []);
  assert.ok(rejected.error);
  assert.equal(rejected.planningError, undefined,
    'een uitgeputte exploration verwijdert workspace al vóór planning/authority');
  assert.deepEqual(row(802), validationBefore,
    'een afgewezen interpretatie wijzigt geen exploration-settlement of backoff');
  assert.equal(autonomyStatus().quiet_runs, validationQuietRuns);
  assertTechnicalRetry();
  assert.equal(providerCalls - validationCalls, 1);
  assert.equal(durableCounts().actions, validationCounts.actions);
  assert.equal(durableCounts().executions, validationCounts.executions);
  assert.equal(durableCounts().contributions, validationCounts.contributions);
  assert.equal(durableCounts().work, validationCounts.work);

  clearAttention();
  insertExploration(803, 'running');
  phase = '503';
  forceDue();
  const unavailableBefore = row(803);
  const unavailableCounts = durableCounts();
  const unavailableCalls = providerCalls;
  const unavailableAttempts = llmRuntimeStatus().provider_request_attempts;
  const unavailableQuietRuns = Number(autonomyStatus().quiet_runs || 0);
  const unavailable = await fireRun();
  assert.deepEqual(unavailable?.tools || [], []);
  assert.deepEqual(row(803), unavailableBefore,
    'een providerfout wijzigt geen exploration-settlement of backoff');
  assert.equal(autonomyStatus().quiet_runs, unavailableQuietRuns);
  assertTechnicalRetry();
  assert.equal(providerCalls - unavailableCalls, 2);
  assert.equal(llmRuntimeStatus().provider_request_attempts - unavailableAttempts, 2);
  assert.equal(durableCounts().actions, unavailableCounts.actions);
  assert.equal(durableCounts().executions, unavailableCounts.executions);
  assert.equal(durableCounts().contributions, unavailableCounts.contributions);
  assert.equal(durableCounts().work, unavailableCounts.work);

  clearAttention();
  insertExploration(805, 'running');
  phase = 'silent';
  let throwSchedulerPublication = false;
  db.function('throw_autonomy_scheduler_once', () => {
    if (!throwSchedulerPublication) return 0;
    throwSchedulerPublication = false;
    throw new Error('injected one-shot scheduler publication failure');
  });
  db.exec(`CREATE TEMP TRIGGER throw_autonomy_scheduler_once
    BEFORE UPDATE OF value ON kv
    WHEN OLD.key='eva_autonomy_scheduler_v1'
    BEGIN
      SELECT throw_autonomy_scheduler_once();
    END`);
  forceDue();
  const thrownBefore = row(805);
  const thrownQuietRuns = Number(autonomyStatus().quiet_runs || 0);
  throwSchedulerPublication = true;
  await fireRun();
  assert.equal(throwSchedulerPublication, false,
    'de test moet exact de finale schedulerpublicatie hebben laten werpen');
  assert.deepEqual(row(805), thrownBefore,
    'een geworpen schedulerfout verbruikt exploration-attention niet');
  assert.equal(autonomyStatus().quiet_runs, thrownQuietRuns,
    'een geworpen schedulerfout is geen quiet run');
  assert.match(autonomyStatus().last_result?.error || '',
    /injected one-shot scheduler publication failure/u);
  assertTechnicalRetry();
  db.exec('DROP TRIGGER throw_autonomy_scheduler_once');

  clearAttention();
  insertExploration(804, 'running');
  fs.mkdirSync(path.join(process.env.EVA_DATA_DIR, 'workspace'), { recursive: true });
  fs.writeFileSync(path.join(process.env.EVA_DATA_DIR, 'workspace', 'bron.md'),
    '# Bron\nExact terugleesbaar lokaal bewijs.\n');
  phase = 'read';
  forceDue();
  const readCounts = durableCounts();
  const readTurn = await fireRun();
  assert.deepEqual(readTurn.tools.map((tool) => tool.name), ['lees_bestand']);
  assert.equal(readTurn.tools[0].ok, true);
  const readAfter = row(804);
  assert.equal(readAfter.scheduler_deferred_ts, null,
    'een echte tool-touch wint de settlement-CAS');
  assert.equal(readAfter.status, 'running');
  assert.equal(readAfter.revision, 9);
  assert.match(readAfter.evidence_json, /lees_bestand/u);
  assert.equal(durableCounts().actions, readCounts.actions + 1);
  assert.equal(durableCounts().executions, readCounts.executions,
    'read-only uitvoering maakt geen idempotente mutatie-executionledger');
  db.close();
}

async function escapedExplorationPublicationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const KEY = 'eva_autonomy_scheduler_v1';
  const baseline = '2020-01-01T00:00:00.000Z';
  const wait = globalThis.setTimeout.bind(globalThis);
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
  db.prepare(`INSERT INTO eva_explorations
    (id,created_ts,updated_ts,source,topic_key,topic_anchors_json,topic_features_json,
      question,why_now,hypothesis,expected_observation,stop_rule,max_actions,
      action_count,empty_evidence_streak,status,evidence_json,result,decision,
      next_wake_ts,last_selected_ts,scheduler_deferred_ts,revision)
    VALUES (829,?,?,'self','rollback','["artifact:rollback"]','{}',
      'Blijf na schedulerrollback exact due.','Bewijs de atomaire publicatie.',
      'Een mislukte KV-write rolt de hostmarker terug.','De restart kiest dezelfde rij.',
      'Stop na restartbewijs.',6,0,0,'evaluating','[{"text":"duurzaam bewijs"}]',
      'Bestaand resultaat','Bestaand besluit',NULL,?,NULL,8)`).run(
    baseline, baseline, baseline,
  );
  const before = db.prepare('SELECT * FROM eva_explorations WHERE id=829').get();
  kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    const payload = JSON.parse(request.input[0].content[0].text);
    const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = payload.turn.text.indexOf(sentence);
    return completed([{ type: 'function_call', call_id: 'rollback-silent',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'inform', goal: { statement: 'Sla deze poging veilig over.',
          outcome: 'Geen onbewezen effect.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans: [{ start,
          end: start + sentence.length }], contextHashes: [] }, confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
      }) }]);
  };
  db.exec(`CREATE TEMP TRIGGER abort_exploration_scheduler_publication
    BEFORE UPDATE OF value ON kv
    WHEN OLD.key='eva_autonomy_scheduler_v1'
    BEGIN
      SELECT RAISE(ABORT, 'injected exploration scheduler publication failure');
    END`);
  startAutonomy();
  const timer = timers.find((item) => !item.cancelled);
  assert.ok(timer);
  timer.callback(...timer.args);
  const deadline = Date.now() + 10_000;
  let escapedAudit = null;
  while (Date.now() < deadline) {
    escapedAudit = db.prepare(`SELECT content FROM timeline WHERE kind='system'
      AND content LIKE '%Zelfstandige planner liep vast:%'
      ORDER BY id DESC LIMIT 1`).get();
    if (escapedAudit && !autonomyStatus().running && timers.length >= 2) break;
    await new Promise((resolve) => wait(resolve, 10));
  }
  assert.match(escapedAudit?.content || '',
    /injected exploration scheduler publication failure/u);
  assert.equal(autonomyStatus().last_result, null,
    'ook de catch-publicatie faalt; er staat geen vals schedulerresultaat');
  assert.deepEqual(db.prepare('SELECT * FROM eva_explorations WHERE id=829').get(), before,
    'de marker/revisie rolt atomair met de finale KV-publicatie terug');
  db.close();
  process.exit(23);
}

async function restartedExplorationPublicationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvGet, kvSet } = await import('./src/db.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const KEY = 'eva_autonomy_scheduler_v1';
  const wait = globalThis.setTimeout.bind(globalThis);
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
  const before = db.prepare('SELECT * FROM eva_explorations WHERE id=829').get();
  assert.equal(before.scheduler_deferred_ts, null);
  assert.equal(before.revision, 8);
  const state = kvGet(KEY, {});
  kvSet(KEY, { ...state, next_run_ts: new Date(Date.now() - 1_000).toISOString() });
  let providerCalls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    providerCalls += 1;
    const request = JSON.parse(String(options.body || '{}'));
    const payload = JSON.parse(request.input[0].content[0].text);
    assert.equal(payload.context.selfAttention.kind, 'exploration');
    assert.equal(payload.context.selfAttention.id, 829);
    assert.deepEqual(payload.context.selfAttention.exploration, {
      contract: 'ExplorationContinuationContext/v1', id: 829, revision: 8,
      question: 'Blijf na schedulerrollback exact due.', status: 'evaluating',
      actionCount: 0, maxActions: 6,
      hypothesis: 'Een mislukte KV-write rolt de hostmarker terug.',
      expectedObservation: 'De restart kiest dezelfde rij.',
      stopRule: 'Stop na restartbewijs.',
      lastEvidence: { summary: 'duurzaam bewijs', verified: false },
      decision: 'Bestaand besluit',
    });
    assert.deepEqual(autonomyStatus().current_selection, {
      attention: { kind: 'exploration', id: 829 }, work_id: null,
      goal_episode_id: null, signal_batch_id: null, signal_count: 0,
    });
    assert.equal(JSON.stringify(autonomyStatus().current_selection).includes(
      'duurzaam bewijs',
    ), false, 'publieke schedulerstatus projecteert geen private context');
    const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = payload.turn.text.indexOf(sentence);
    return completed([{ type: 'function_call', call_id: 'restart-silent',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'inform', goal: { statement: 'Probeer na restart exact opnieuw.',
          outcome: 'De verse cursor wordt publiek verwerkt.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans: [{ start,
          end: start + sentence.length }], contextHashes: [] }, confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
      }) }]);
  };
  const previousRun = autonomyStatus().last_run_ts;
  startAutonomy();
  const timer = timers.find((item) => !item.cancelled);
  assert.ok(timer);
  timer.callback(...timer.args);
  const deadline = Date.now() + 10_000;
  while ((autonomyStatus().last_run_ts === previousRun || autonomyStatus().running)
    && Date.now() < deadline) {
    await new Promise((resolve) => wait(resolve, 10));
  }
  await new Promise((resolve) => wait(resolve, 0));
  assert.notEqual(autonomyStatus().last_run_ts, previousRun);
  assert.equal(providerCalls, 1);
  const after = db.prepare('SELECT * FROM eva_explorations WHERE id=829').get();
  assert.ok(after.scheduler_deferred_ts,
    'een echte restart geeft dezelfde due exploration een verse private cursor');
  assert.equal(after.revision, 9);
  assert.equal(after.status, before.status);
  assert.equal(after.updated_ts, before.updated_ts);
  assert.equal(after.result, before.result);
  assert.equal(after.decision, before.decision);
  assert.equal(after.evidence_json, before.evidence_json);
  db.close();
}

async function yieldedExplorationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, kvSet } = await import('./src/db.js');
  const { llmRuntimeStatus } = await import('./src/brain/llm.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const KEY = 'eva_autonomy_scheduler_v1';
  const baseline = '2020-01-01T00:00:00.000Z';
  const wait = globalThis.setTimeout.bind(globalThis);
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
  db.prepare(`INSERT INTO eva_explorations
    (id,created_ts,updated_ts,source,topic_key,topic_anchors_json,topic_features_json,
      question,why_now,hypothesis,expected_observation,stop_rule,max_actions,
      action_count,empty_evidence_streak,status,evidence_json,result,decision,
      next_wake_ts,last_selected_ts,scheduler_deferred_ts,revision)
    VALUES (819,?,?,'self','yield','["artifact:yield"]','{}',
      'Laat bewonerspreemptie deze exploration ongemoeid.','De bewoner gaat voor.',
      'Een superseded beurt settlelt niets.','Alle velden blijven exact gelijk.',
      'Stop na bewonersbewijs.',6,0,0,'running','[{"text":"bestaand bewijs"}]',
      'Bestaand resultaat','Bestaand besluit',NULL,?,NULL,8)`).run(
    baseline, baseline, baseline,
  );
  const before = db.prepare('SELECT * FROM eva_explorations WHERE id=819').get();
  const actionRowsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count;
  const contributionRowsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='contribution'",
  ).get().count;
  const executionsBefore = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  const attemptsBefore = llmRuntimeStatus().provider_request_attempts;
  kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  let releaseProvider;
  let signalProvider;
  const providerStarted = new Promise((resolve) => { signalProvider = resolve; });
  let explorationCalls = 0;
  let fetchCalls = 0;
  const providerStages = [];
  globalThis.fetch = async (_url, options = {}) => {
    fetchCalls += 1;
    const request = JSON.parse(String(options.body || '{}'));
    const payload = JSON.parse(request.input[0].content[0].text);
    const attention = payload.context.selfAttention;
    providerStages.push({
      attention: attention?.kind || null,
      tool: request.tools?.[0]?.name || null,
    });
    if (attention?.kind !== 'exploration') {
      const end = payload.turn.text.length;
      return completed([{ type: 'function_call', call_id: 'resident-reply',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'chat', goal: { statement: payload.turn.text,
            outcome: 'De bewoner krijgt direct antwoord.', beneficiary: 'requester' },
          conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
          confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'reply', requirementIds: [],
            replyDraft: 'Ik ben er; jouw beurt gaat voor.' },
        }) }]);
    }
    explorationCalls += 1;
    assert.equal(attention.id, 819);
    const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = payload.turn.text.indexOf(sentence);
    const response = completed([{ type: 'function_call', call_id: 'yielded-exploration',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'inform', goal: { statement: 'Bekijk deze exploration.',
          outcome: 'De richting wordt veilig beoordeeld.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans: [{ start,
          end: start + sentence.length }], contextHashes: [] }, confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
      }) }]);
    signalProvider();
    return new Promise((resolve) => { releaseProvider = () => resolve(response); });
  };
  startAutonomy();
  const timer = timers.find((item) => !item.cancelled);
  assert.ok(timer);
  timer.callback(...timer.args);
  await providerStarted;
  const epochBefore = agentStatus().interaction_epoch;
  await handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text: 'Kun je nu luisteren?',
    messageContext: { conversationId: 'web:exploration-yield', syntheticTest: true },
  });
  assert.equal(agentStatus().interaction_epoch, epochBefore + 1);
  releaseProvider();
  const deadline = Date.now() + 10_000;
  while (!autonomyStatus().last_run_ts && Date.now() < deadline) {
    await new Promise((resolve) => wait(resolve, 10));
  }
  await new Promise((resolve) => wait(resolve, 0));
  assert.equal(explorationCalls, 1);
  assert.equal(agentStatus().last_autonomy_turn.yielded, true);
  assert.equal(agentStatus().last_autonomy_turn.superseded, true);
  assert.deepEqual(agentStatus().last_autonomy_turn.tools, []);
  assert.equal(autonomyStatus().last_result.yielded, true);
  assert.deepEqual(db.prepare('SELECT * FROM eva_explorations WHERE id=819').get(), before,
    'een echte bewonersyield verbruikt noch markeert de explorationcursor');
  assert.equal(fetchCalls, 3,
    'exact één explorationrequest en twee bewonersproviderstappen worden geprobeerd');
  assert.deepEqual(providerStages, [
    { attention: 'exploration', tool: 'commit_turn_understanding' },
    { attention: null, tool: 'commit_turn_understanding' },
  ], 'exact de twee interpretationrequests dragen een geattesteerde turnpayload');
  assert.equal(llmRuntimeStatus().provider_request_attempts - attemptsBefore, 3);
  assert.equal(db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count, actionRowsBefore);
  assert.equal(db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='contribution'",
  ).get().count, contributionRowsBefore);
  assert.equal(db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count, executionsBefore);
  assert.equal(autonomyStatus().current_selection, null);
  db.close();
}

function runScenario(name, scenario, { timeout = 30_000, env = {}, oauth = false } = {}) {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-${name}-`));
  if (oauth) fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${scenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout,
    env: { ...process.env, EVA_DATA_DIR: childDir, ...env },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

test('exploration-attention cursor is exact, one-shot, ABA-bestendig en cross-kind eerlijk', () => {
  runScenario('exploration-cursor', issuedExplorationCursorScenario);
});

test('eerste start migreert alleen de nullable hostdeferralmarker en laat #122 inert', () => {
  runScenario('exploration-migration', legacyExplorationMigrationScenario);
});

test('publieke scheduler deferreert vroeg zonder I/O en geeft daarna #9 en #10 echte kansen', () => {
  runScenario('exploration-public-scheduler', publicSchedulerBackoffScenario, {
    timeout: 40_000,
    oauth: true,
    env: { EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full', EVA_AUTONOMY_INTERVAL_MINUTES: '15',
      EVA_AUTONOMY_DAILY_WORK_MINUTES: '120', OPENAI_API_KEY: 'test-only',
      SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
});

test('bewonerspreemptie laat exploration-attention exact ongemoeid', () => {
  runScenario('exploration-yield', yieldedExplorationScenario, {
    timeout: 25_000,
    oauth: true,
    env: { EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full', EVA_AUTONOMY_INTERVAL_MINUTES: '15',
      EVA_AUTONOMY_DAILY_WORK_MINUTES: '120', OPENAI_API_KEY: 'test-only',
      SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
});

test('mislukte KV-publicatie rolt explorationdeferral terug en restart geeft een verse cursor', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-exploration-restart-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const env = { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
    EVA_AUTONOMY_INTERVAL_MINUTES: '15', EVA_AUTONOMY_DAILY_WORK_MINUTES: '120',
    OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' };
  const escaped = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${escapedExplorationPublicationScenario.toString()})().catch((error) => { console.error(error); process.exit(24); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000, env,
  });
  assert.equal(escaped.status, 23, `${escaped.stdout}\n${escaped.stderr}`);
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${restartedExplorationPublicationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000, env,
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(restarted.status, 0, `${restarted.stdout}\n${restarted.stderr}`);
});
