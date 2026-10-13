import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

async function contributionOutcomeUpgradeScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db, logContributionOutcome } = await import('./src/db.js');
  const columns = db.prepare('PRAGMA table_info(timeline)').all();
  const outcomeColumn = columns.find((row) => row.name === 'outcome_key');
  assert.ok(outcomeColumn);
  assert.equal(Number(outcomeColumn.notnull), 0);
  const index = db.prepare("PRAGMA index_list('timeline')").all()
    .find((row) => row.name === 'idx_timeline_contribution_outcome_key');
  assert.equal(Number(index?.unique), 1);
  assert.equal(Number(index?.partial), 1);
  assert.deepEqual(db.prepare("PRAGMA index_info('idx_timeline_contribution_outcome_key')")
    .all().map((row) => row.name), ['outcome_key']);
  assert.deepEqual(db.prepare(`SELECT id,ts,kind,content,meta,outcome_key
    FROM timeline WHERE id=1`).get(), {
    id: 1, ts: '2026-01-01T00:00:00.000Z', kind: 'contribution',
    content: 'bestaande rij', meta: '{"legacy":true}', outcome_key: null,
  });
  const key = 'a'.repeat(64);
  const meta = { lifecycleContract: 'ObservationContribution/v1', evidence: 'Legacy upgrade.' };
  const first = logContributionOutcome(key, 'Zelfstandige onderzoeksnotitie: legacy upgrade.', meta);
  const replay = logContributionOutcome(key, 'Zelfstandige onderzoeksnotitie: legacy upgrade.', meta);
  assert.equal(first.created, true);
  assert.equal(replay.created, false);
  assert.equal(db.prepare('SELECT count(*) n FROM timeline WHERE outcome_key=?').get(key).n, 1);
  assert.throws(() => logContributionOutcome(
    key, 'Zelfstandige onderzoeksnotitie: conflicterende replay.', meta,
  ), /conflicterende contribution-outcome/u);
  assert.equal(db.prepare('SELECT count(*) n FROM timeline WHERE outcome_key=?').get(key).n, 1);
  db.close();
}

async function freeResearchContinuityScenario() {
  const assert = (await import('node:assert/strict')).default;
  const fs = (await import('node:fs')).default;
  const path = (await import('node:path')).default;
  const {
    db, getTimeline, kvSet, logContributionOutcome, logTimeline,
  } = await import('./src/db.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const { selfContinuityContextText } = await import('./src/mind/social.js');
  const {
    dueAttention, startExploration, updateExploration,
  } = await import('./src/mind/curiosity.js');

  const KEY = 'eva_autonomy_scheduler_v1';
  const nativeWait = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false, fired: false,
      scheduler: /\btick\(\)/u.test(String(callback)) };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };

  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage: {
      input_tokens: 800, output_tokens: 120, total_tokens: 920,
    } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const standardSentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
  const query = 'Welke actuele primaire bron beschrijft een kleine, legale lokale besparingskans voor een Nederlands huishouden?';
  const secondQuery = 'Welke tweede actuele bron beschrijft dezelfde lokale besparingskans?';
  const sourceUrl = 'https://example.org/actuele-primaire-bron';
  const thinSourceUrl = 'https://x.co';
  // De raw live providertekst/bronlijst van de bewezen productiebeurt is niet
  // bewaard. Deze inhoud is representatief; de persisted understandingvelden,
  // echte executeTool-wrapper en lifecyclebinding hieronder zijn exact de vorm.
  const researchText = `De actuele primaire bron beschrijft een concrete lokale besparingsroute met controleerbare voorwaarden. Voor dit huishouden blijft nog een lokale toepasbaarheidscheck nodig. ${sourceUrl}`;

  const action = (capabilityId, operationId, semanticInput, span) => ({
    capabilityId, operationId, polarity: 'request', semanticInput, sourceSpans: [span],
  });
  const understanding = (payload, actions, {
    decision = 'inspect', requirementIds = null, conditions = null,
  } = {}) => {
    const start = payload.turn.text.indexOf(standardSentence);
    assert.ok(start >= 0, payload.turn.text);
    const span = { start, end: start + standardSentence.length };
    const projected = actions(span);
    return {
      dialogueAct: projected.length ? 'command' : 'inform',
      goal: {
        statement: projected.length ? 'Onderzoek één actuele bron.' : 'Blijf nu stil.',
        outcome: projected.length ? 'Eén actuele bronwaarneming is gecontroleerd.' : 'Geen nieuwe actie.',
        beneficiary: projected.length ? 'household' : 'self',
      },
      conditions: typeof conditions === 'function' ? conditions(span)
        : conditions == null && projected.length === 1 ? [{
            statement: standardSentence,
            state: 'required', sourceSpans: [span], role: 'guard',
            requirementIds: [], afterRequirementIds: [],
          }] : conditions || [],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: projected,
      requestedEffects: projected.length ? ['read'] : [],
      deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] },
      durability: 'turn',
      delivery: { mode: 'silent', target: 'none' },
      proofNeeds: [], ambiguities: [],
      firstDecision: {
        kind: projected.length ? decision : 'silent',
        requirementIds: requirementIds || projected.map((_, index) => `r${index + 1}`),
        replyDraft: null,
      },
    };
  };
  const userReplyUnderstanding = (payload) => {
    const span = { start: 0, end: payload.turn.text.length };
    return {
      dialogueAct: 'inform',
      goal: { statement: 'Onderbreek de vrije beurt.', outcome: 'De onderbreking is verwerkt.', beneficiary: 'requester' },
      conditions: [], provenance: { sourceSpans: [span], contextHashes: [] }, confidence: 0.99,
      actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Ik stop de zelfstandige beurt.' },
    };
  };

  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const secret = 'sleeping-test-secret-value-12345';
  const futureWake = new Date(Date.now() + 24 * 60 * 60_000).toISOString();
  const started = startExploration({
    question: `Welke lokale forecast hoort pas morgen bekeken te worden? api-key=${secret} /root/private/forecast.json D:\\Eva\\private\\forecast.json`,
    whyNow: 'De forecast is pas bij het afgesproken kijkvenster beslissend.',
    hypothesis: 'Wachten voorkomt een parallelle cyclus met verouderde voorspellingen.',
    expectedObservation: 'Na de wekdatum is een verse forecast beschikbaar.',
    stopRule: 'Start voor de wekdatum niets via ../private/.env of workspace/private.json.',
    maxActions: 3, source: 'self', topicKey: 'sleeping_forecast_continuity',
    topicAnchors: ['event:future-forecast-window', 'constraint:no-parallel-cycle'],
  });
  assert.equal(started.ok, true, JSON.stringify(started));
  const waiting = updateExploration(started.exploration.id, {
    status: 'waiting', nextWakeTs: futureWake,
    expectedRevision: started.exploration.revision,
  });
  assert.equal(waiting.ok, true, JSON.stringify(waiting));
  assert.equal(dueAttention(), null, 'future-wake exploration blijft voor de wekdatum onselecteerbaar');
  const sleepingBefore = db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(waiting.exploration.id);

  const forceDue = () => {
    const current = db.prepare('SELECT value FROM kv WHERE key=?').get(KEY);
    const state = current ? JSON.parse(current.value) : {};
    kvSet(KEY, { ...state, next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [], daily_used_ms: Number(state.daily_used_ms || 0),
      daily_productive_ms: Number(state.daily_productive_ms || 0),
      daily_preempted_ms: Number(state.daily_preempted_ms || 0) });
  };
  forceDue();

  let phase = 'success';
  let providerSearchCalls = 0;
  let preemptPromise = null;
  const selections = [];
  const interpretationRequests = [];
  const providerQueries = [];
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
    const request = JSON.parse(String(options.body || '{}'));
    const understandingCall = request.tools?.[0]?.name === 'commit_turn_understanding';
    if (understandingCall) {
      const payload = JSON.parse(request.input[0].content[0].text);
      interpretationRequests.push({ phase, instructions: request.instructions, payload });
      if (payload.turn.origin === 'user') {
        assert.doesNotMatch(request.instructions, /Continuïteitsgrens voor slapende eigen vragen/u);
        return completed([{ type: 'function_call', call_id: 'preempt-user-understanding',
          name: 'commit_turn_understanding', arguments: JSON.stringify(userReplyUnderstanding(payload)) }]);
      }
      if (phase === 'after-wake') {
        assert.equal(payload.context.selfAttention.kind, 'exploration');
        assert.doesNotMatch(request.instructions, /Continuïteitsgrens voor slapende eigen vragen/u);
        return completed([{ type: 'function_call', call_id: 'awake-silent',
          name: 'commit_turn_understanding', arguments: JSON.stringify(
            understanding(payload, () => [], { conditions: [] }),
          ) }]);
      }
      assert.equal(Object.hasOwn(payload.context, 'selfAttention'), false);
      if (phase === 'preempt') {
        assert.doesNotMatch(request.instructions, /Continuïteitsgrens voor slapende eigen vragen/u);
      } else {
        assert.match(request.instructions, /Continuïteitsgrens voor slapende eigen vragen/u);
        assert.match(request.instructions, /Welke lokale forecast hoort pas morgen bekeken te worden/iu);
        assert.match(request.instructions, new RegExp(futureWake.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'u'));
        for (const forbidden of [secret, '/root/private', '../private', 'workspace/private', 'D:\\Eva']) {
          assert.equal(request.instructions.includes(forbidden), false, forbidden);
        }
        assert.equal(request.instructions.includes(`#${waiting.exploration.id}`), false);
      }
      const plans = phase === 'multi'
        ? (span) => [
            action('internet.research', 'search', { vraag: query }, span),
            action('internet.research', 'search', { vraag: secondQuery }, span),
          ]
        : (span) => [action('internet.research', 'search', { vraag: query }, span)];
      const conditionOverride = phase === 'action-condition'
        ? (conditionSpan) => [{
            statement: standardSentence,
            state: 'required', sourceSpans: [conditionSpan], role: 'action',
            requirementIds: ['r1'], afterRequirementIds: [],
          }]
        : null;
      return completed([{ type: 'function_call', call_id: `free-research-${phase}`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(understanding(
          payload, plans, phase === 'multi'
            ? { requirementIds: ['r1', 'r2'], conditions: [] }
            : conditionOverride ? { conditions: conditionOverride } : {},
        )) }]);
    }
    const webSearch = request.tools?.some((tool) => tool?.type === 'web_search');
    if (!webSearch) {
      assert.equal(phase, 'preempt');
      return completed([{ type: 'message', content: [{ type: 'output_text',
        text: 'De zelfstandige beurt is gestopt.' }] }]);
    }
    providerSearchCalls += 1;
    const asked = request.input?.[0]?.content?.[0]?.text;
    providerQueries.push({ phase, asked });
    if (phase === 'multi') assert.ok([query, secondQuery].includes(asked));
    else assert.equal(asked, query);
    if (phase === 'preempt') {
      preemptPromise = handleMessageDetailed({
        channel: 'web', sender: 'bewoner', text: 'stop heel even',
        messageContext: { syntheticTest: true, queueKey: 'autonomy' },
      });
    }
    const text = phase === 'no-source'
      ? 'De zoekactie vond een inhoudelijke beschrijving, maar zonder een rechtstreeks controleerbare bronlink blijft dit geen materieel bruikbare waarneming.'
      : phase === 'thin'
        ? `Zelfde zelfde zelfde zelfde zelfde zelfde zelfde zelfde zelfde zelfde. ${thinSourceUrl}`
        : researchText;
    return completed([
      { type: 'web_search_call', id: `search-${phase}`, status: 'completed',
        action: { type: 'search', query: asked } },
      { type: 'message', content: [{ type: 'output_text', text }] },
    ]);
  };

  let lastTimer = null;
  const trigger = async (previousRun = null) => {
    const timer = timers.find((item) => item.scheduler && !item.cancelled && !item.fired);
    assert.ok(timer, 'publieke scheduler heeft een verse tick');
    assert.notEqual(timer, lastTimer);
    timer.fired = true;
    lastTimer = timer;
    timer.callback(...timer.args);
    selections.push({ phase, value: structuredClone(autonomyStatus().current_selection) });
    const deadline = Date.now() + 12_000;
    while ((!autonomyStatus().last_run_ts || autonomyStatus().last_run_ts === previousRun)
      && Date.now() < deadline) await new Promise((resolve) => nativeWait(resolve, 10));
    await new Promise((resolve) => nativeWait(resolve, 0));
    assert.notEqual(autonomyStatus().last_run_ts, previousRun);
    return agentStatus().last_autonomy_turn;
  };
  const workspaceTree = () => {
    const root = path.join(process.env.EVA_DATA_DIR, 'workspace');
    const rows = [];
    const walk = (directory, prefix = '') => {
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const relative = path.join(prefix, entry.name);
        rows.push(`${entry.isDirectory() ? 'd' : 'f'}:${relative}`);
        if (entry.isDirectory()) walk(path.join(directory, entry.name), relative);
      }
    };
    walk(root);
    return rows.sort();
  };
  const durableCounts = () => ({
    work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
    episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
    explorations: db.prepare('SELECT count(*) n FROM eva_explorations').get().n,
    aspirations: db.prepare('SELECT count(*) n FROM aspirations').get().n,
    reminders: db.prepare('SELECT count(*) n FROM reminders').get().n,
    messages: db.prepare('SELECT count(*) n FROM messages').get().n,
    telegram: db.prepare('SELECT count(*) n FROM telegram_messages').get().n,
    deliveries: db.prepare("SELECT count(*) n FROM timeline WHERE kind='chat_out'").get().n,
  });

  const beforeCounts = durableCounts();
  const beforeFiles = workspaceTree();
  startAutonomy();
  let previousRun = autonomyStatus().last_run_ts;
  const successTurn = await trigger(previousRun);
  assert.deepEqual(selections.at(-1).value, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  });
  assert.deepEqual(successTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['zoek_internet', true, true]], JSON.stringify(successTurn));
  assert.match(successTurn.reply, /actuele primaire bron/iu);
  assert.equal(successTurn.progress, null);
  assert.equal(successTurn.noActionReason, null);
  assert.equal(autonomyStatus().last_result.productive, true);
  assert.equal(autonomyStatus().last_result.progressed, false);
  assert.equal(autonomyStatus().last_result.tool, 'zoek_internet');
  assert.match(autonomyStatus().last_result.summary, /actuele primaire bron/iu);
  assert.deepEqual(durableCounts(), beforeCounts);
  assert.deepEqual(workspaceTree(), beforeFiles);
  assert.deepEqual(db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(waiting.exploration.id),
    sleepingBefore, 'vrije research muteert de slapende exploration niet');
  const outcomes = db.prepare(`SELECT * FROM timeline WHERE kind='contribution'
    AND json_extract(meta,'$.lifecycleContract')='ObservationContribution/v1'`).all();
  assert.equal(outcomes.length, 1);
  assert.equal(outcomes[0].outcome_key?.length, 64);
  const outcomeMeta = JSON.parse(outcomes[0].meta);
  assert.equal(outcomeMeta.outcomeKey, outcomes[0].outcome_key);
  assert.equal(outcomeMeta.observationKind, 'internet_research');
  assert.equal(outcomeMeta.source, 'bijdrage');
  assert.deepEqual(outcomeMeta.sources, [sourceUrl]);
  const understandingRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?
    ORDER BY id DESC LIMIT 1`).get(successTurn.turnId);
  const understandingRecord = JSON.parse(understandingRow.meta).understanding;
  assert.equal(outcomeMeta.understandingSha256, understandingRecord.sourceSha256,
    'lifecycle bindt de originele in-memory understanding-SHA, niet de record-SHA');
  assert.equal(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=?`).get(successTurn.turnId).n, 1);
  assert.equal(db.prepare(`SELECT json_extract(meta,'$.tool') tool FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=?`).get(successTurn.turnId).tool, 'zoek_internet');
  const actionBinding = JSON.parse(db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=?`).get(successTurn.turnId).meta).semanticBinding;
  assert.equal(actionBinding.understandingSha256, understandingRecord.sourceSha256);
  assert.equal(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? AND json_extract(meta,'$.tool')!='zoek_internet'`)
    .get(successTurn.turnId).n, 0);
  const today = agentStatus().today_real_steps.filter((item) => item.last_action === 'zoek_internet');
  assert.equal(today.length, 1);
  assert.match(today[0].topic, /^Zelfstandige onderzoeksnotitie:/u);
  assert.match(today[0].evidence, /actuele primaire bron/iu);
  assert.equal(JSON.stringify(agentStatus()).includes(secret), false);
  assert.equal(JSON.stringify(getTimeline({ limit: 500 })).includes(secret), false);
  assert.deepEqual(providerQueries, [{ phase: 'success', asked: query }]);

  const replayOne = logContributionOutcome(outcomes[0].outcome_key, outcomes[0].content, outcomeMeta);
  const replayTwo = logContributionOutcome(outcomes[0].outcome_key, outcomes[0].content, outcomeMeta);
  assert.equal(replayOne.created, false);
  assert.equal(replayTwo.created, false);
  assert.equal(db.prepare('SELECT count(*) n FROM timeline WHERE outcome_key=?')
    .get(outcomes[0].outcome_key).n, 1, 'durable outcome-helper replay blijft exact één rij');
  assert.throws(() => logContributionOutcome(
    outcomes[0].outcome_key, `${outcomes[0].content} conflict`, outcomeMeta,
  ), /conflicterende contribution-outcome/u);

  phase = 'no-source';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const noSourceTurn = await trigger(previousRun);
  assert.ok(noSourceTurn.noActionReason);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='contribution'").get().n, 1);

  phase = 'multi';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const multiTurn = await trigger(previousRun);
  assert.ok(multiTurn.noActionReason || multiTurn.actionFailed === true);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='contribution'").get().n, 1);
  assert.ok(providerQueries.filter((item) => item.phase === 'multi').length >= 1,
    'multi-plan mag uitvoeren, maar krijgt nooit observation-contribution');

  phase = 'action-condition';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const conditionedTurn = await trigger(previousRun);
  assert.deepEqual(conditionedTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['zoek_internet', true, true]], 'action-linked research mag wel uitvoeren');
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='contribution'").get().n, 1,
    'een action-linked condition is geen vrije observation-precommit');

  phase = 'thin';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const thinTurn = await trigger(previousRun);
  assert.deepEqual(thinTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['zoek_internet', true, true]], 'de dunne notitie heeft wel een complete zoekreceipt');
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='contribution'").get().n, 1,
    'een bronlink zonder materieel bericht is geen observation-contribution');

  const pastWake = new Date(Date.now() - 1_000).toISOString();
  db.prepare(`UPDATE eva_explorations SET next_wake_ts=?,updated_ts=?,revision=revision+1
    WHERE id=? AND status='waiting'`).run(pastWake, pastWake, waiting.exploration.id);
  phase = 'after-wake';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const wakeTurn = await trigger(previousRun);
  assert.deepEqual(selections.at(-1).value?.attention, {
    kind: 'exploration', id: waiting.exploration.id,
  }, 'na de wekdatum selecteert de bestaande private exploration normaal');
  assert.equal(wakeTurn.noActionReason == null || typeof wakeTurn.noActionReason === 'string', true);
  db.prepare(`UPDATE eva_explorations SET status='completed',next_wake_ts=NULL,
    result='Wekselectie in regressie aantoonbaar bereikt.',decision='Geen verdere testactie.',
    updated_ts=?,revision=revision+1 WHERE id=?`).run(
    new Date().toISOString(), waiting.exploration.id,
  );

  phase = 'preempt';
  forceDue();
  previousRun = autonomyStatus().last_run_ts;
  const preemptTurn = await trigger(previousRun);
  assert.ok(preemptPromise, 'de providercall moet de interactieve preemption hebben ingepland');
  await preemptPromise;
  assert.equal(preemptTurn.progress, null);
  assert.equal(preemptTurn.noActionReason, null);
  assert.equal(preemptTurn.superseded, true);
  assert.equal(preemptTurn.yielded, true);
  assert.deepEqual(preemptTurn.tools, []);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);
  assert.equal(autonomyStatus().last_result.yielded, true);
  assert.equal(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='contribution'
    AND json_extract(meta,'$.turnId')=?`).get(preemptTurn.turnId).n, 0);
  assert.equal(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=?`).get(preemptTurn.turnId).n, 0);
  assert.equal(agentStatus().last_autonomy_turn.tools.length, 0);
  assert.equal(providerSearchCalls, providerQueries.length);
  assert.ok(providerSearchCalls >= 6,
    'success, no-source, multi, condition, thin en preempt bereikten echte providerresearch');
  assert.deepEqual(workspaceTree(), beforeFiles);
  assert.deepEqual(durableCounts(), beforeCounts,
    'research, tegenvoorbeelden, wake-selectie en preempt maken geen durable effect');
  logTimeline('contribution', 'Opdrachtfilter mag niet in eigen continuïteit komen.', {
    source: 'opdracht', lastAction: 'bedien_huis',
    evidence: 'UNIEKE_OPDRACHTFILTER_INHOUD',
  });
  logTimeline('contribution', 'Synthetische bijdrage mag niet in eigen continuïteit komen.', {
    source: 'bijdrage', syntheticTest: true, lastAction: 'bedien_huis',
    evidence: 'UNIEKE_SYNTHETISCHE_INHOUD',
  });
  const selfContext = selfContinuityContextText({ mode: 'autonomy' });
  assert.match(selfContext, /onderzoek 1/u,
    'de observation-contribution bereikt compact de researchlane');
  assert.match(selfContext, /actuele primaire bron/iu,
    'de nieuwe bijdrage bereikt de compacte laatste zelfgekozen uitkomst');
  assert.doesNotMatch(selfContext, /UNIEKE_OPDRACHTFILTER_INHOUD/u);
  assert.doesNotMatch(selfContext, /UNIEKE_SYNTHETISCHE_INHOUD/u);
  db.close();
}

test('vrije authentieke research wordt exact eenmaal een stille observation-contribution', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-free-research-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${freeResearchContinuityScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 40_000,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only-openai-key', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test('bestaande timeline krijgt outcome-kolom vóór de partial unique index', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-outcome-upgrade-'));
  const legacy = new Database(path.join(childDir, 'eva.db'));
  legacy.exec(`CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT
  );
  INSERT INTO timeline (ts,kind,content,meta)
    VALUES ('2026-01-01T00:00:00.000Z','contribution','bestaande rij','{"legacy":true}');`);
  legacy.close();
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${contributionOutcomeUpgradeScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    env: { ...process.env, EVA_DATA_DIR: childDir },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
