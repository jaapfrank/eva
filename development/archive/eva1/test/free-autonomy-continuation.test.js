import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function atomicExplorationRollbackScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { createHash } = await import('node:crypto');
  const { bus } = await import('./src/bus.js');
  const { db } = await import('./src/db.js');
  const { beginCognitiveEpisode } = await import('./src/mind/cognitive.js');
  const {
    issueFreeAutonomyExplorationAtomicStart,
    startIssuedFreeAutonomyExplorationAtomic,
  } = await import('./src/mind/curiosity.js');
  const { canonicalJson } = await import('./src/util.js');
  db.prepare('DELETE FROM eva_explorations').run();
  const turnId = 'atomic-rollback-turn';
  const episode = beginCognitiveEpisode({
    correlationId: turnId, kind: 'autonomy', source: 'self',
    focusKind: 'free_attention', goalKey: 'free_attention',
  });
  const basisArgs = Object.freeze({ query: 'een materiële actuele basiswaarneming' });
  const basisPlan = Object.freeze({
    requirementId: 'r1', capabilityId: 'home.context.read', operationId: 'read',
    toolName: 'huis_status', effect: 'read', supportsDeferred: false,
    execution: Object.freeze({ mode: 'direct' }), args: basisArgs,
  });
  const semanticBinding = Object.freeze({
    understandingSha256: 'a'.repeat(64), requirementId: 'r1',
    addressesRequirementIds: Object.freeze(['r1']), basisReceiptIds: Object.freeze([]),
    capabilityId: 'home.context.read', operationId: 'read', role: 'primary',
  });
  const basisReceipt = Object.freeze({
    name: 'huis_status', args: basisArgs,
    output: JSON.stringify({ contract: 'ActionResult/v1', tool: 'huis_status',
      ok: true, verified: true, changed: false }),
    ok: true, verified: true, changed: false, partial: false,
    needs_followup: false, effect_uncertain: false,
    evidence: Object.freeze({ summary: 'Een exact geverifieerde materiële basiswaarneming.' }),
    semanticBinding,
  });
  const receiptSha256 = createHash('sha256').update(canonicalJson({
    name: basisReceipt.name, args: basisReceipt.args,
    output: basisReceipt.output, semanticBinding: basisReceipt.semanticBinding,
  })).digest('hex');
  const semanticInput = Object.freeze({
    aanleiding: 'Deze waarneming verdient precies één kleine broncheck.',
    hypothese: 'De tweede waarneming bevestigt dezelfde concrete voorwaarde.',
    max_acties: 2,
    onderwerp_ankers: Object.freeze(['entity:test-basis', 'question:test-check']),
    onderwerp_sleutel: 'atomic_rollback_check',
    stopregel: 'Stop direct na precies één aanvullende waarneming.',
    verwachte_waarneming: 'De aanvullende waarneming bevestigt of weerlegt de voorwaarde.',
    vraag: 'Bevestigt één aanvullende waarneming deze concrete voorwaarde?',
  });
  const action = Object.freeze({
    requirementId: 'continuation_exploration_start',
    capabilityId: 'exploration.manage', operationId: 'start', semanticInput,
    sourceSpans: Object.freeze([]), polarity: 'request', authorization: 'allowed',
  });
  const source = {
    contract: 'FreeAutonomyContinuation/v1', kind: 'model_continuation', turnId,
    understandingSha256: 'a'.repeat(64), basisReceiptSha256: receiptSha256,
    basisReceiptId: 's1', choice: 'start_exploration',
    remainingRequirementIds: Object.freeze(['r2']), action,
  };
  const decision = Object.freeze({ ...source,
    sha256: createHash('sha256').update(canonicalJson(source)).digest('hex') });
  const startArgs = Object.freeze({ bewerking: 'start', ...semanticInput });
  let emitted = 0;
  bus.on('exploration_changed', () => { emitted += 1; });
  const capability = issueFreeAutonomyExplorationAtomicStart({
    decision, startArgs, basisPlan, basisReceipt, basisReceiptId: 's1',
    basisEvidence: basisReceipt.evidence.summary,
    sourceEpisodeId: episode.id, turnId,
    afterInsert: () => { throw new Error('geïnjecteerde processtop tussen insert en charge'); },
  });
  assert.ok(capability);
  const failed = startIssuedFreeAutonomyExplorationAtomic(capability, startArgs);
  assert.equal(failed.ok, false);
  assert.equal(failed.changed, false);
  assert.equal(failed.effect_uncertain, false);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_explorations').get().n, 0,
    'de insert en basisboeking rollen samen terug');
  assert.equal(emitted, 0, 'een rollback mag geen post-commit domeinevent uitsturen');
  const replay = startIssuedFreeAutonomyExplorationAtomic(capability, startArgs);
  assert.equal(replay.ok, false, 'de mislukte one-shot capability is niet replaybaar');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_explorations').get().n, 0);
  db.close();
}

async function freeAutonomyContinuationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const scenario = String(process.env.EVA_CONTINUATION_SCENARIO || 'start');
  const { db, kvSet } = await import('./src/db.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
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

  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const current = db.prepare('SELECT value FROM kv WHERE key=?').get(KEY);
  const scheduler = current ? JSON.parse(current.value) : {};
  kvSet(KEY, { ...scheduler,
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: Number(scheduler.daily_used_ms || 0),
    daily_productive_ms: Number(scheduler.daily_productive_ms || 0),
    daily_preempted_ms: Number(scheduler.daily_preempted_ms || 0),
  });

  const completed = (output, usage = { input_tokens: 700, output_tokens: 100,
    total_tokens: 800 }) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const standardSentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
  const firstQuery = 'Welke actuele primaire bron toont vandaag een kleine lokale besparingskans?';
  const secondQuery = 'Welke onafhankelijke actuele bron toetst dezelfde lokale besparingskans?';
  const selectedHomePath = '/api/states/sensor.veilige_lokale_broncheck';
  const explorationInput = {
    aanleiding: 'De eerste actuele bron maakt een kleine toetsbare vervolgvraag relevant.',
    hypothese: 'Een onafhankelijke tweede bron bevestigt dezelfde concrete lokale kans.',
    max_acties: 2,
    onderwerp_ankers: ['artifact:eerste-bronwaarneming', 'question:lokale-besparingskans'],
    onderwerp_sleutel: 'lokale_besparingskans_broncheck',
    stopregel: 'Stop na de ene onafhankelijke broncheck, ook als de bronnen verschillen.',
    verwachte_waarneming: 'De tweede bron bevestigt of weerlegt de concrete voorwaarde.',
    vraag: 'Bevestigt een onafhankelijke actuele bron deze lokale besparingskans?',
  };
  const action = (capabilityId, operationId, semanticInput, span) => ({
    capabilityId, operationId, polarity: 'request', semanticInput, sourceSpans: [span],
  });
  const freeUnderstanding = (payload) => {
    const start = payload.turn.text.indexOf(standardSentence);
    assert.ok(start >= 0, payload.turn.text);
    const span = { start, end: start + standardSentence.length };
    return {
      dialogueAct: 'command',
      goal: { statement: 'Kies zelf welke actuele context een kleine proef verdient.',
        outcome: 'Een zelfgekozen waarneming is begrensd getoetst.', beneficiary: 'self' },
      conditions: [], provenance: { sourceSpans: [span], contextHashes: [] }, confidence: 0.99,
      actions: [
        action('work.episode.read', 'list', {}, span),
        action('internet.research', 'search', { vraag: firstQuery }, span),
        action('goal.portfolio.read', 'list', {}, span),
        action('internet.research', 'search', { vraag: secondQuery }, span),
        action('home.api.read', 'read', { path: selectedHomePath }, span),
      ],
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1', 'r2', 'r3', 'r4', 'r5'],
        replyDraft: null },
    };
  };
  const userUnderstanding = (payload) => {
    const span = { start: 0, end: payload.turn.text.length };
    return {
      dialogueAct: 'inform',
      goal: { statement: 'Stop de vrije testbeurt.', outcome: 'De beurt stopt.',
        beneficiary: 'requester' },
      conditions: [], provenance: { sourceSpans: [span], contextHashes: [] }, confidence: 0.99,
      actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Ik stop deze beurt.' },
    };
  };
  const continuation = (choice = 'start_exploration', ids = ['r5']) => ({
    choice, remainingRequirementIds: choice === 'start_exploration' ? ids : [],
    action: choice === 'start_exploration' ? {
      requirementId: 'continuation_exploration_start',
      capabilityId: 'exploration.manage', operationId: 'start',
      semanticInput: explorationInput,
    } : null,
  });

  let continuationCalls = 0;
  let webCalls = 0;
  let homeCalls = 0;
  let preemptPromise = null;
  const continuationBodies = [];
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    if (target.includes('/api/states')) {
      homeCalls += 1;
      if (scenario === 'selected-failure') {
        return new Response('gebonden lokale read faalt', { status: 503 });
      }
      if (scenario === 'cas-race') {
        const exploration = db.prepare(`SELECT id FROM eva_explorations
          WHERE status='running' ORDER BY id DESC LIMIT 1`).get();
        assert.ok(exploration);
        db.prepare(`UPDATE eva_explorations SET revision=revision+1,updated_ts=?
          WHERE id=?`).run(new Date().toISOString(), exploration.id);
      }
      return new Response(JSON.stringify({
        entity_id: 'sensor.veilige_lokale_broncheck', state: 'stabiel',
        last_updated: '2026-08-12T08:00:00.000Z',
        attributes: { friendly_name: 'Veilige lokale broncheck' },
      }), { status: 200,
        headers: { 'content-type': 'application/json' } });
    }
    assert.equal(target, 'https://chatgpt.com/backend-api/codex/responses');
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name === 'commit_turn_understanding') {
      const payload = JSON.parse(request.input[0].content[0].text);
      const value = payload.turn.origin === 'user'
        ? userUnderstanding(payload) : freeUnderstanding(payload);
      return completed([{ type: 'function_call', call_id: `understanding-${scenario}`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(value) }]);
    }
    if (request.tools?.[0]?.name === 'commit_free_autonomy_continuation') {
      continuationCalls += 1;
      continuationBodies.push(request);
      if (scenario === 'provider-error') {
        return new Response('tijdelijk niet beschikbaar', { status: 503 });
      }
      if (scenario === 'stale') {
        preemptPromise = handleMessageDetailed({
          channel: 'web', sender: 'bewoner', text: 'stop deze vrije beurt',
          messageContext: { syntheticTest: true, queueKey: 'autonomy' },
        });
      }
      const value = scenario === 'keep' ? continuation('keep_observation')
        : scenario === 'silent' ? continuation('silent')
            : scenario === 'unknown-id' ? continuation('start_exploration', ['r999'])
            : continuation();
      return completed([{ type: 'function_call', call_id: `continuation-${scenario}`,
        name: 'commit_free_autonomy_continuation', arguments: JSON.stringify(value) }]);
    }
    if (request.tools?.some((tool) => tool?.type === 'web_search')) {
      webCalls += 1;
      const query = request.input?.[0]?.content?.[0]?.text;
      assert.ok([firstQuery, secondQuery].includes(query), query);
      const urlValue = webCalls === 1
        ? 'https://example.org/eerste-primaire-bron'
        : 'https://example.net/onafhankelijke-tweede-bron';
      return completed([
        { type: 'web_search_call', id: `search-${webCalls}`, status: 'completed',
          action: { type: 'search', query } },
        { type: 'message', content: [{ type: 'output_text',
          text: `De actuele bron geeft een concrete controleerbare voorwaarde en een materiële lokale waarneming. ${urlValue}` }] },
      ]);
    }
    assert.equal(scenario, 'stale');
    return completed([{ type: 'message', content: [{ type: 'output_text',
      text: 'Ik stop deze beurt.' }] }]);
  };

  startAutonomy();
  const timer = timers.find((item) => item.scheduler && !item.cancelled && !item.fired);
  assert.ok(timer, 'de publieke scheduler heeft een tick gepland');
  const previousRun = autonomyStatus().last_run_ts;
  timer.fired = true;
  timer.callback(...timer.args);
  const deadline = Date.now() + 12_000;
  while ((!autonomyStatus().last_run_ts || autonomyStatus().last_run_ts === previousRun)
    && Date.now() < deadline) await new Promise((resolve) => nativeWait(resolve, 10));
  await new Promise((resolve) => nativeWait(resolve, 0));
  assert.notEqual(autonomyStatus().last_run_ts, previousRun);
  if (preemptPromise) await preemptPromise;
  const result = agentStatus().last_autonomy_turn;

  assert.equal(continuationCalls, 1, 'de semantische continuation krijgt exact één providercall');
  assert.equal(continuationBodies.length, 1);
  const body = continuationBodies[0];
  assert.equal(body.model, 'gpt-5.6-terra');
  assert.equal(body.tool_choice, 'required');
  assert.equal(body.tools.length, 1);
  assert.equal(body.tools[0].name, 'commit_free_autonomy_continuation');
  assert.deepEqual(body.tools[0].parameters.properties.choice.enum,
    ['start_exploration', 'keep_observation', 'silent']);
  assert.equal(body.parallel_tool_calls, false);
  assert.equal(body.store, false);
  assert.equal(body.stream, true);
  assert.equal(body.prompt_cache_key, 'eva-free-autonomy-continuation');
  assert.equal(Object.hasOwn(body, 'max_output_tokens'), false,
    'OAuth verwijdert uitsluitend de incompatibele transportproperty');
  const continuationPayload = JSON.parse(body.input[0].content[0].text);
  assert.equal(continuationPayload.contract, 'FreeAutonomyContinuation/v1');
  assert.equal(continuationPayload.evidence.receiptId, 's2',
    'een administratieve eerste receipt verandert het echte inhoudelijke anker niet');
  assert.equal(continuationPayload.evidence.requirementId, 'r2');
  assert.deepEqual(continuationPayload.remaining.map((item) => item.requirementId), ['r5'],
    'administratieve en LLM-provider-backed resterende plannen worden niet aangeboden');
  assert.equal(JSON.stringify(continuationPayload).includes(secondQuery), false,
    'internet.research mag niet via deze continuation worden aangeboden');

  const explorations = db.prepare('SELECT * FROM eva_explorations ORDER BY id').all();
  if (scenario === 'start') {
    assert.deepEqual(result.tools.map((tool) => tool.name),
      ['toon_werk', 'zoek_internet', 'verkenning_bijhouden', 'ha_lees'],
    JSON.stringify({ result, explorations }));
    assert.equal(webCalls, 1, 'de enige research-providercall is de oorspronkelijke basisread');
    assert.equal(homeCalls, 1);
    assert.equal(explorations.length, 1);
    const exploration = explorations[0];
    assert.equal(exploration.source, 'self');
    assert.equal(exploration.question, explorationInput.vraag);
    assert.equal(exploration.topic_key, explorationInput.onderwerp_sleutel);
    assert.equal(exploration.max_actions, 2);
    assert.equal(exploration.action_count, 2);
    assert.equal(exploration.status, 'evaluating');
    assert.equal(JSON.parse(exploration.evidence_json).length, 2);
    const planningRow = db.prepare(`SELECT meta FROM timeline WHERE kind='turn_planning'
      AND json_extract(meta,'$.contract')='FreeAutonomyContinuation/v1'
      ORDER BY id DESC LIMIT 1`).get();
    assert.ok(planningRow);
    const planning = JSON.parse(planningRow.meta);
    assert.equal(planning.choice, 'start_exploration');
    assert.equal(planning.basisReceiptId, 's2');
    assert.deepEqual(planning.remainingRequirementIds, ['r5']);
    assert.match(planning.continuationSha256, /^[a-f0-9]{64}$/u);
    const actionRows = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
      AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(result.turnId)
      .map((row) => JSON.parse(row.meta));
    assert.deepEqual(actionRows.map((row) => row.tool),
      ['toon_werk', 'zoek_internet', 'verkenning_bijhouden', 'ha_lees']);
    const startBinding = actionRows[2].semanticBinding;
    assert.equal(startBinding.understandingSha256, planning.continuationSha256);
    assert.equal(startBinding.role, 'free_autonomy_continuation');
    assert.deepEqual(startBinding.basisReceiptIds, ['s2']);
    const selectedBinding = actionRows[3].semanticBinding;
    assert.notEqual(selectedBinding.understandingSha256, planning.continuationSha256,
      'de geselecteerde oorspronkelijke read houdt haar oorspronkelijke actieautoriteit');
    assert.deepEqual(actionRows[3].continuation, {
      contract: 'FreeAutonomyContinuation/v1',
      decisionSha256: planning.continuationSha256,
      basisReceiptSha256: planning.basisReceiptSha256,
      basisReceiptId: 's2', explorationId: exploration.id,
      selectedRequirementIds: ['r5'],
    });
    const usage = db.prepare(`SELECT meta FROM timeline WHERE kind='llm_usage'
      AND json_extract(meta,'$.cacheKey')='eva-free-autonomy-continuation'
      ORDER BY id DESC LIMIT 1`).get();
    const usageMeta = JSON.parse(usage.meta);
    assert.deepEqual({
      fallbackUsed: usageMeta.fallbackUsed, requestAttempts: usageMeta.requestAttempts,
      authenticationRetries: usageMeta.authenticationRetries,
      boundedIdenticalTransportRetries: usageMeta.boundedIdenticalTransportRetries,
      rateLimitRetries: usageMeta.rateLimitRetries,
      transientRetries: usageMeta.transientRetries,
    }, { fallbackUsed: false, requestAttempts: 1, authenticationRetries: 0,
      boundedIdenticalTransportRetries: 0, rateLimitRetries: 0, transientRetries: 0 });
  } else if (scenario === 'cas-race') {
    assert.equal(webCalls, 1);
    assert.equal(homeCalls, 1);
    assert.equal(explorations.length, 1);
    const raced = explorations[0];
    assert.equal(raced.status, 'running');
    assert.equal(raced.action_count, 1,
      'de stale geselecteerde receipt mag niet buiten haar expected-revision worden geboekt');
    assert.equal(JSON.parse(raced.evidence_json).length, 1);
    assert.equal(result.actionFailed, true);
    assert.equal(result.progress, null);
    const retained = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
      AND json_extract(meta,'$.turnId')=? AND json_extract(meta,'$.tool')='ha_lees'
      ORDER BY id DESC LIMIT 1`).get(result.turnId);
    assert.ok(retained, 'de werkelijk verkregen readreceipt blijft forensisch bewaard');
    assert.equal(JSON.parse(retained.meta).verified, true);
  } else if (scenario === 'selected-failure') {
    assert.equal(webCalls, 1);
    assert.equal(homeCalls, 1);
    assert.equal(explorations.length, 1);
    const failed = explorations[0];
    assert.equal(failed.status, 'evaluating');
    assert.equal(failed.action_count, 2,
      'basis en de ene mislukte geselecteerde poging verbruiken exact het budget');
    assert.equal(JSON.parse(failed.evidence_json).length, 2);
    assert.deepEqual(result.tools.map((tool) => [tool.name, tool.ok]), [
      ['toon_werk', true], ['zoek_internet', true],
      ['verkenning_bijhouden', true], ['ha_lees', false],
    ]);
    assert.equal(result.progress, null);
    assert.equal(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='turn_planning'
      AND json_extract(meta,'$.turnId')=?
      AND json_extract(meta,'$.contract')='TurnStepDecision/v1'`).get(result.turnId).n, 0,
    'de generieke recoveryplanner mag na de ene continuationpoging niet lopen');
  } else {
    assert.equal(explorations.length, 0, `${scenario} mag geen exploration maken`);
    assert.equal(webCalls, 1, `${scenario} mag geen resterende providerread uitvoeren`);
    assert.equal(homeCalls, 0, `${scenario} mag de veilige resterende read niet uitvoeren`);
    if (scenario === 'stale') {
      assert.equal(result.superseded, true);
      assert.equal(result.yielded, true);
      assert.deepEqual(result.tools.map((tool) => tool.name), ['toon_werk', 'zoek_internet']);
    } else {
      assert.deepEqual(result.tools.map((tool) => tool.name), ['toon_werk', 'zoek_internet']);
    }
  }
  db.close();
}

function runScenario(scenario) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-free-continuation-${scenario}-`));
  fs.writeFileSync(path.join(dataDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${freeAutonomyContinuationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 25_000,
    env: { ...process.env, EVA_DATA_DIR: dataDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      EVA_MODEL_VOICE: 'gpt-5.6-terra', OPENAI_API_KEY: 'test-only-openai-key',
      EVA_CONTINUATION_SCENARIO: scenario, SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(dataDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${scenario}\n${result.stdout}\n${result.stderr}`);
}

test('vrije multi-read krijgt één modelgekozen exploration-start en alleen de gekozen reads', () => {
  runScenario('start');
});

test('exploration-insert en basisboeking rollen bij een processtop volledig samen terug', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-free-continuation-rollback-'));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${atomicExplorationRollbackScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 15_000,
    env: { ...process.env, EVA_DATA_DIR: dataDir },
  });
  fs.rmSync(dataDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

for (const scenario of [
  'keep', 'silent', 'unknown-id', 'provider-error', 'stale',
  'cas-race', 'selected-failure',
]) {
  test(`vrije continuation faalt effectloos of eindigt begrensd: ${scenario}`, () => {
    runScenario(scenario);
  });
}
