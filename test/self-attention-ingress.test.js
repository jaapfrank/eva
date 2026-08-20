import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-self-attention-ingress-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';
process.env.SLEEP_START = '23:59';
process.env.WAKE_TIME = '00:00';
fs.writeFileSync(path.join(dataDir, 'auth.json'), JSON.stringify({
  access_token: 'test-oauth-token', account_id: 'test-account',
  expires_at: Date.now() + 60 * 60_000,
}));
await import('./agent-test-composition.js');

const { autonomyStatus, startAutonomy } = await import('../src/mind/autonomy.js');
const {
  __projectRouteTest, agentStatus, handleMessageDetailed,
} = await import('../src/mind/agent.js');
const { db, kvSet } = await import('../src/db.js');
const { manageGoal } = await import('../src/mind/portfolio.js');
const { redactSensitiveText } = await import('../src/security/redaction.js');

const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const SCHEDULER_KEY = 'eva_autonomy_scheduler_v1';

after(() => {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completed(output, usage = {
  input_tokens: 1_000, output_tokens: 100, total_tokens: 1_100,
}) {
  const event = { type: 'response.completed', response: { output, usage } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200, headers: { 'content-type': 'text/event-stream' },
  });
}

function promptInputSchema(payload, reference) {
  const schema = payload.inputSchemas[reference];
  assert.ok(schema && typeof schema === 'object' && !Array.isArray(schema));
  const fields = payload.fieldMaps[schema.f];
  assert.ok(fields && typeof fields === 'object' && !Array.isArray(fields));
  return { ...schema, f: fields };
}

test('de private scheduler draagt exacte geredigeerde self-attention tot veilige projectie', async () => {
  assert.equal(agentStatus().open_commitments.length, 0);
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const secret = 'aspiration-secret-123456789';
  const attentionText = `Vind één meetbare gezinsbijdrage; api_key=${secret}`;
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (909,'2020-01-01T00:00:00.000Z',?,'productievorm','actief','',
      '2020-01-01T00:00:00.000Z','development','self-attention-test','het gezin','',
      'Kies één veilige huidige stap.',1,1)`).run(attentionText);
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  const groundedReason = `periodieke vrije aandacht. Er is een eigen aandachtsrichting beschikbaar (aspiration #909): ${attentionText}. Dit is jouw optionele eigen aandachtsrichting: bepaal zelf of je haar nu volgt of bewust iets anders belangrijker vindt.`;
  const expectedText = redactSensitiveText(`Aanleiding voor deze zelfstandige beurt: ${groundedReason}.\n\nKijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.`);
  let understandingCalls = 0;
  let capturedRequest = null;
  const providerRequests = [];
  const providerUrls = [];
  globalThis.fetch = async (url, options = {}) => {
    providerUrls.push(String(url));
    const request = JSON.parse(String(options.body || '{}'));
    providerRequests.push(request);
    if (request.tools?.[0]?.name === 'commit_turn_step') {
      return completed([{ type: 'function_call', call_id: 'blocked-self-attention-read',
        name: 'commit_turn_step', arguments: JSON.stringify({
          kind: 'blocked', addressesRequirementIds: [], basisReceiptIds: ['s1'],
          action: null, reasonCode: 'external_block',
        }) }]);
    }
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      return completed([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'Ik heb de veilige actuele route bekeken.' }] }]);
    }
    understandingCalls += 1;
    capturedRequest = request;
    const payload = JSON.parse(request.input[0].content[0].text);
    const positive = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const candidate = 'Dit is jouw optionele eigen aandachtsrichting: bepaal zelf of je haar nu volgt of bewust iets anders belangrijker vindt.';
    const positiveStart = payload.turn.text.indexOf(positive);
    const candidateStart = payload.turn.text.indexOf(candidate);
    return completed([{ type: 'function_call', call_id: 'understand-self-attention',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: 'Kies één veilige actuele bijdrage.',
          outcome: 'Eén concrete route is actueel gecontroleerd.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans: [{ start: positiveStart,
          end: positiveStart + positive.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{ capabilityId: 'workspace.file.read', operationId: 'read',
          polarity: 'request', semanticInput: {
            pad: 'data/workspace/ontbrekend-self-attention.md' },
          sourceSpans: [{ start: positiveStart, end: positiveStart + positive.length }] },
        { capabilityId: 'workspace.file.write', operationId: 'write', polarity: 'deny',
          semanticInput: {}, sourceSpans: [{ start: candidateStart,
            end: candidateStart + candidate.length }] }],
        requestedEffects: ['read'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1', 'r2'], replyDraft: null },
      }) }]);
  };

  const scheduledTimers = [];
  let boundedRetryPauseHook = null;
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 500) {
      if (boundedRetryPauseHook) return boundedRetryPauseHook(callback, args);
      return originalSetTimeout(callback, 0, ...args);
    }
    const handle = { callback, delay, args, cancelled: false };
    scheduledTimers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle && typeof handle === 'object') handle.cancelled = true;
  };
  startAutonomy();
  const first = scheduledTimers.find((item) => !item.cancelled);
  assert.ok(first, 'startAutonomy moet de publieke schedulertimer bewapenen');
  first.callback(...first.args);
  const deadline = Date.now() + 10_000;
  while (!autonomyStatus().last_run_ts && Date.now() < deadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  const turn = agentStatus().last_autonomy_turn;
  assert.equal(understandingCalls, 1, 'veilige projectie veroorzaakt geen interpretatieretry');
  assert.equal(Object.hasOwn(capturedRequest, 'max_output_tokens'), false,
    'de OAuth-wirevorm gebruikt usagebewijs in plaats van het onondersteunde veld');
  assert.ok(providerUrls.every((url) => (
    url === 'https://chatgpt.com/backend-api/codex/responses'
  )));
  const payload = JSON.parse(capturedRequest.input[0].content[0].text);
  assert.equal(providerRequests.some((request) => JSON.stringify(request).includes(secret)), false,
    'raw aandachttekst mag receipt, antwoord- of recoveryprompt niet omzeilen');
  assert.equal(providerRequests.length, 1,
    'een echte read-failure mag na de ene interpretatie geen providerrecovery openen');
  assert.equal(providerRequests.some((request) => request.tools?.[0]?.name === 'commit_turn_step'), false);
  assert.equal(payload.turn.text, expectedText);
  assert.deepEqual(Object.keys(payload.context.selfAttention).sort(), [
    'contract', 'id', 'kind',
  ]);
  assert.equal(payload.context.selfAttention.kind, 'aspiration');
  assert.equal(payload.context.selfAttention.id, 909);
  assert.ok(turn?.turnId);
  assert.deepEqual(turn.tools.map((tool) => tool.name), ['lees_bestand'], JSON.stringify(turn));
  assert.equal(turn.tools[0].ok, false);
  const row = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(turn.turnId);
  const understanding = JSON.parse(row.meta).understanding;
  assert.deepEqual(understanding.decisionProjection.acceptedRequirementIds, ['r1']);
  assert.deepEqual(understanding.decisionProjection.rejectedRequirementIds, ['r2']);
  assert.deepEqual(understanding.firstDecision.requirementIds, ['r1']);

  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (910,'2020-01-01T00:00:00.000Z','Controleer één tijdelijke providerfout.',
      'transport-canary','actief','','2020-01-01T00:00:00.000Z','development',
      'self-attention-transport-test','het gezin','','Stop veilig bij provideruitval.',1,1)`).run();
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  let transportRequests = 0;
  const transportBodies = [];
  let transportUrl = '';
  const transportActionRowsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count;
  const transportExecutionRowsBefore = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  globalThis.fetch = async (url, options = {}) => {
    transportRequests += 1;
    transportUrl = String(url);
    const rawBody = String(options.body || '{}');
    transportBodies.push(rawBody);
    if (transportRequests === 1) {
      return new Response('tijdelijk niet beschikbaar', { status: 503 });
    }
    assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'")
      .get().count, transportActionRowsBefore,
    'vóór de succesvolle interpretatieresponse mag geen action zijn gestart');
    assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions')
      .get().count, transportExecutionRowsBefore,
    'vóór de succesvolle interpretatieresponse mag geen tool zijn gestart');
    const request = JSON.parse(rawBody);
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    const positive = turnPayload.turn.text.split('\n\n').at(-1);
    const start = turnPayload.turn.text.indexOf(positive);
    const sourceSpans = [{ start, end: start + positive.length }];
    return completed([{ type: 'function_call', call_id: 'transport-recovered-read',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Controleer één actuele bijdrage.',
          outcome: 'Eén actuele bron is veilig gelezen.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
        actions: [{ capabilityId: 'workspace.file.read', operationId: 'read',
          polarity: 'request', semanticInput: {
            pad: 'data/workspace/ontbrekend-transport-herstel.md' }, sourceSpans }],
        requestedEffects: ['read'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }) }]);
  };
  const previousRun = autonomyStatus().last_run_ts;
  const next = [...scheduledTimers].reverse().find((item) => item !== first && !item.cancelled);
  assert.ok(next, 'de publieke scheduler moet na de eerste beurt opnieuw bewapend zijn');
  next.callback(...next.args);
  const retryDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === previousRun && Date.now() < retryDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  assert.equal(transportRequests, 2,
    'een geattesteerde self-attention interpretatie herhaalt exact één bewezen HTTP 503');
  assert.equal(transportUrl, 'https://chatgpt.com/backend-api/codex/responses');
  assert.equal(transportBodies[0], transportBodies[1],
    'de bounded transportretry moet byte-identiek dezelfde requestbody gebruiken');
  assert.equal(Object.hasOwn(JSON.parse(transportBodies[0]), 'max_output_tokens'), false);
  const recoveredTransportTurn = agentStatus().last_autonomy_turn;
  assert.deepEqual(recoveredTransportTurn.tools.map((item) => item.name), ['lees_bestand']);
  const recoveredUnderstandingRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
    recoveredTransportTurn.turnId,
  );
  const recoveredUnderstanding = JSON.parse(recoveredUnderstandingRow.meta);
  assert.equal(recoveredUnderstanding.attempts, 1,
    'twee transportattempts blijven exact één semantische interpretatie');
  const recoveredUsage = JSON.parse(db.prepare(
    "SELECT meta FROM timeline WHERE kind='llm_usage' ORDER BY id DESC LIMIT 1",
  ).get().meta);
  assert.deepEqual({
    requestAttempts: recoveredUsage.requestAttempts,
    boundedRetry: recoveredUsage.boundedIdenticalTransportRetries,
    transientRetries: recoveredUsage.transientRetries,
    fallbackUsed: recoveredUsage.fallbackUsed,
    sameModel: recoveredUsage.model === recoveredUsage.requestedModel,
  }, { requestAttempts: 2, boundedRetry: 1, transientRetries: 0,
    fallbackUsed: false, sameModel: true });
  assert.equal(autonomyStatus().current_selection, null,
    'ook een herstelde providerfout laat geen stale authentieke schedulerselectie achter');
  const transportAspiration = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=910',
  ).get();
  assert.equal(transportAspiration.progress, '',
    'een transportfailure mag geen inhoudelijke aspiratievoortgang verzinnen');
  assert.notEqual(transportAspiration.last_touched, '2020-01-01T00:00:00.000Z',
    'de als resultaat teruggekeerde 503-failure roteert wel de due aspiratie');

  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (912,'2020-01-01T00:00:00.000Z','Controleer twee tijdelijke providerfouten.',
      'transport-failclosed-canary','actief','','2020-01-01T00:00:00.000Z','development',
      'self-attention-transport-failclosed-test','het gezin','',
      'Stop na exact één identieke transportretry.',1,1)`).run();
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  let failedTransportRequests = 0;
  const failureUsageRowsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='llm_usage'",
  ).get().count;
  globalThis.fetch = async () => {
    failedTransportRequests += 1;
    return new Response('tijdelijk niet beschikbaar', { status: 503 });
  };
  const beforeFailedTransportRun = autonomyStatus().last_run_ts;
  const failedRetryTimer = [...scheduledTimers].reverse().find((item) => (
    item !== first && item !== next && !item.cancelled
  ));
  assert.ok(failedRetryTimer);
  failedRetryTimer.callback(...failedRetryTimer.args);
  const failedRetryDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === beforeFailedTransportRun
    && Date.now() < failedRetryDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  const failedTransportTurn = agentStatus().last_autonomy_turn;
  assert.equal(failedTransportRequests, 2);
  assert.deepEqual(failedTransportTurn.tools, []);
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='llm_usage'")
    .get().count, failureUsageRowsBefore,
  'twee HTTP 503 responses leveren geen verzonnen usagebewijs op');
  const failedUnderstandingRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
    failedTransportTurn.turnId,
  );
  const failedUnderstanding = JSON.parse(failedUnderstandingRow.meta);
  assert.equal(failedUnderstanding.attempts, 1);
  assert.equal(failedUnderstanding.error, 'interpreter_unavailable');
  assert.equal(failedUnderstanding.attemptFailures[0].code, 'interpreter_unavailable');
  assert.equal(failedUnderstanding.attemptFailures[0].providerStatus, 503);
  assert.equal(failedUnderstanding.attemptFailures[0].providerAttempts, 2);
  const failedTransportAspiration = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=912',
  ).get();
  assert.equal(failedTransportAspiration.progress, '');
  assert.equal(failedTransportAspiration.last_touched, '2020-01-01T00:00:00.000Z',
    'technische provideruitval verbruikt de due aspiratie niet');
  assert.equal(autonomyStatus().last_result.technical_retry, true,
    'de scheduler bewaart de mislukte interpretatie als technische retry');

  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (911,'2020-01-01T00:00:00.000Z','Controleer de publieke self-attention effectgrens.',
      'authority-canary','actief','','2020-01-01T00:00:00.000Z','development',
      'self-attention-authority-test','het gezin','','Kies één veilige huidige stap.',1,1)`).run();
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  const actionRowsBefore = db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count;
  const executionRowsBefore = db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count;
  const effectUrls = [];
  let effectRequests = 0;
  globalThis.fetch = async (url, options = {}) => {
    effectUrls.push(String(url));
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses',
      'de afgewezen action mag geen huis- of andere netwerk-I/O starten');
    const request = JSON.parse(String(options.body || '{}'));
    effectRequests += 1;
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    assert.equal(turnPayload.context.selfAttention.id, 911);
    const positive = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = turnPayload.turn.text.indexOf(positive);
    const sourceSpans = [{ start, end: start + positive.length }];
    return completed([{ type: 'function_call', call_id: 'reject-high-effect-self-attention',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: 'Controleer één actuele bijdrage.',
          outcome: 'Eén route is veilig beoordeeld.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
        actions: [{ capabilityId: 'workspace.file.list', operationId: 'list',
          polarity: 'request', semanticInput: {}, sourceSpans },
        { capabilityId: 'home.device.control', operationId: 'set',
          polarity: 'request', semanticInput: {
            target: 'light.authority_canary', desiredState: 'on' }, sourceSpans }],
        requestedEffects: ['read', 'home'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }) }]);
  };
  const beforeEffectRun = autonomyStatus().last_run_ts;
  const third = [...scheduledTimers].reverse().find((item) => (
    item !== first && item !== next && item !== failedRetryTimer && !item.cancelled
  ));
  assert.ok(third, 'de publieke scheduler moet voor de authority-canary opnieuw bewapend zijn');
  third.callback(...third.args);
  const effectDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === beforeEffectRun && Date.now() < effectDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  const rejectedTurn = agentStatus().last_autonomy_turn;
  assert.equal(effectRequests, 1, 'de effectgrens gebruikt exact één interpretatiecall');
  assert.deepEqual(effectUrls, ['https://chatgpt.com/backend-api/codex/responses']);
  assert.deepEqual(rejectedTurn.tools, []);
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count,
    actionRowsBefore, 'geen action mag de uitvoerledger bereiken');
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    executionRowsBefore, 'geen tool execution mag worden gestart');
  const rejectedRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(rejectedTurn.turnId);
  const rejectedMeta = JSON.parse(rejectedRow.meta);
  assert.equal(rejectedMeta.attempts, 1);
  assert.equal(rejectedMeta.attemptFailures[0].validation,
    'self_attention_effect_not_allowed');
  assert.deepEqual(rejectedMeta.attemptFailures[0].actionAudit.map((item) => ({
    capabilityId: item.capabilityId, effect: item.effect,
    bindingMatched: item.bindingMatched, selected: item.selected,
  })), [{ capabilityId: 'workspace.file.list', effect: 'read',
    bindingMatched: true, selected: true },
  { capabilityId: 'home.device.control', effect: 'home',
    bindingMatched: false, selected: false }]);
  assert.equal(autonomyStatus().current_selection, null,
    'de afgewezen authority-canary laat geen stale schedulerselectie achter');

  for (const route of ['workspace-followup', 'bound-manage']) {
    db.prepare('DELETE FROM eva_explorations').run();
    db.prepare('DELETE FROM aspirations').run();
    db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
    const listSecret = `sk-proj-goal-list-secret-${route}`;
    const listGoal = manageGoal({
      operation: 'create', title: `Verborgen ${listSecret} doellijst controleren`,
      intent: 'De private doelreview mag geen generieke portefeuillelijst openen.',
      why: 'Ruwe sleutels mogen geen iteratieve providerstap bereiken.',
      beneficiary: 'Eva en het gezin', horizon: 'deze beurt',
      successMetrics: ['geen tool- of vervolgprovidercall'], source: 'self',
      nextReviewTs: new Date(Date.now() - 60_000).toISOString(),
    }).goal;
    kvSet(SCHEDULER_KEY, {
      next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
      daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
    });
    const listActionRowsBefore = db.prepare(
      "SELECT count(*) AS count FROM timeline WHERE kind='action'",
    ).get().count;
    const listExecutionRowsBefore = db.prepare(
      'SELECT count(*) AS count FROM turn_tool_executions',
    ).get().count;
    const listProviderBodies = [];
    globalThis.fetch = async (url, options = {}) => {
      assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
      const request = JSON.parse(String(options.body || '{}'));
      listProviderBodies.push(request);
      assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding',
        'een afgewezen doellijst mag geen execution-driver openen');
      const turnPayload = JSON.parse(request.input[0].content[0].text);
      assert.equal(turnPayload.context.selfAttention.kind, 'goal');
      assert.equal(turnPayload.context.selfAttention.id, listGoal.id);
      assert.equal(JSON.stringify(request).includes(listGoal.goal_key), false);
      assert.equal(JSON.stringify(request).includes(listSecret), false);
      const positive = turnPayload.turn.text.split('\n\n').at(-1);
      const start = turnPayload.turn.text.indexOf(positive);
      const sourceSpans = [{ start, end: start + positive.length }];
      const actions = [{ capabilityId: 'goal.portfolio.read', operationId: 'list',
        polarity: 'request', semanticInput: {}, sourceSpans }];
      const effects = ['read'];
      if (route === 'workspace-followup') {
        actions.push({ capabilityId: 'workspace.file.write', operationId: 'write',
          polarity: 'request', semanticInput: {
            pad: 'goal-list-resultaat.md', inhoud: 'Gebruik hiervoor eerst de doellijst.' },
          sourceSpans });
        effects.push('workspace');
      } else {
        const capability = turnPayload.capabilities.find((item) => (
          item[0] === 'goal.portfolio.manage'
        ));
        const pause = capability[1].find((item) => item[0] === 'pause');
        const schema = promptInputSchema(turnPayload, pause[3]);
        actions.push({ capabilityId: 'goal.portfolio.manage', operationId: 'pause',
          polarity: 'request', semanticInput: {
            doel_ref: turnPayload.fieldTypes[schema.f.doel_ref].e[0],
            verwachte_revisie: turnPayload.fieldTypes[schema.f.verwachte_revisie].e[0],
          }, sourceSpans });
        effects.push('persistent');
      }
      return completed([{ type: 'function_call',
        call_id: `reject-goal-list-${route}`, name: 'commit_turn_understanding',
        arguments: JSON.stringify({
          dialogueAct: 'command', goal: { statement: 'Herbekijk exact deze richting.',
            outcome: 'De generieke doellijst blijft gesloten.', beneficiary: 'self' },
          conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
          actions, requestedEffects: effects, deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'act', requirementIds: ['r2'], replyDraft: null },
        }),
      }]);
    };
    const beforeListRun = autonomyStatus().last_run_ts;
    const listTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
    assert.ok(listTimer, `${route}: scheduler moet opnieuw bewapend zijn`);
    listTimer.callback(...listTimer.args);
    const listDeadline = Date.now() + 10_000;
    while (autonomyStatus().last_run_ts === beforeListRun && Date.now() < listDeadline) {
      await new Promise((resolve) => originalSetTimeout(resolve, 10));
    }
    await new Promise((resolve) => originalSetTimeout(resolve, 0));
    const listTurn = agentStatus().last_autonomy_turn;
    assert.equal(listProviderBodies.length, 1, route);
    assert.deepEqual(listTurn.tools, [], route);
    assert.equal(db.prepare(
      "SELECT count(*) AS count FROM timeline WHERE kind='action'",
    ).get().count, listActionRowsBefore, route);
    assert.equal(db.prepare(
      'SELECT count(*) AS count FROM turn_tool_executions',
    ).get().count, listExecutionRowsBefore, route);
    const listUnderstandingRow = db.prepare(`SELECT meta FROM timeline
      WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`)
      .get(listTurn.turnId);
    const listFailure = JSON.parse(listUnderstandingRow.meta);
    assert.equal(listFailure.attempts, 1, route);
    assert.equal(listFailure.attemptFailures[0].validation,
      'self_attention_effect_not_allowed', route);
    assert.equal(listFailure.attemptFailures[0].actionAudit[0].bindingMatched, false, route);
    assert.equal(listFailure.attemptFailures[0].actionAudit[0].selected, false, route);
    assert.equal(JSON.stringify(listFailure).includes(listGoal.goal_key), false, route);
    assert.equal(JSON.stringify(agentStatus()).includes(listGoal.goal_key), false, route);
    assert.equal(autonomyStatus().current_selection, null, route);
  }

  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const goalTitleSecret = 'sk-proj-goal-title-secret-938475';
  const dueGoal = manageGoal({
    operation: 'create', title: `Exact geattesteerde ${goalTitleSecret} doelreview uitvoeren`,
    intent: 'Eén zelfgekozen richting op haar eigen reviewmoment botsingsvrij bijstellen.',
    why: 'Een duurzame eigen richting moet exact aan haar geselecteerde doel gebonden blijven.',
    beneficiary: 'Eva en het gezin', horizon: 'de komende week',
    successMetrics: ['een exact gebonden toekomstig reviewmoment'],
    source: 'self', nextReviewTs: new Date(Date.now() - 60_000).toISOString(),
  }).goal;
  const siblingGoal = manageGoal({
    operation: 'create', title: 'Onaangeraakte siblingrichting',
    intent: 'Een tweede richting bewijst dat de reviewbinding niet kan overspringen.',
    why: 'Een identificeerbare sibling maakt verkeerde doelmutatie zichtbaar.',
    beneficiary: 'Eva en het gezin', horizon: 'de komende week',
    successMetrics: ['geen wijziging door de andere doelreview'],
    source: 'self', nextReviewTs: '2099-01-01T00:00:00.000Z',
  }).goal;
  const siblingRevision = siblingGoal.revision;
  const goalNextReview = new Date(Date.now() + 2 * 60 * 60_000).toISOString();
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  let goalRequests = 0;
  let modelGoalRevision = null;
  let modelGoalRef = null;
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
    const request = JSON.parse(String(options.body || '{}'));
    goalRequests += 1;
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    assert.equal(request.instructions.includes(dueGoal.goal_key), false);
    assert.equal(JSON.stringify(request.tools).includes(dueGoal.goal_key), false);
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    assert.deepEqual(Object.keys(turnPayload.context.selfAttention).sort(), [
      'contract', 'id', 'kind',
    ]);
    assert.equal(turnPayload.context.selfAttention.kind, 'goal');
    assert.equal(turnPayload.context.selfAttention.id, dueGoal.id);
    assert.equal(JSON.stringify(turnPayload.context).includes(dueGoal.goal_key), false);
    const serializedRequest = JSON.stringify(request);
    assert.equal(serializedRequest.includes(dueGoal.goal_key), false,
      'ook een legacy titelafgeleide raw key verlaat de host niet');
    assert.equal(serializedRequest.includes('sk_proj_goal_title_secret'), false);
    assert.equal(serializedRequest.includes(goalTitleSecret), false);
    assert.deepEqual(autonomyStatus().current_selection?.attention, {
      kind: 'goal', id: dueGoal.id,
    });
    const capability = turnPayload.capabilities.find((item) => (
      item[0] === 'goal.portfolio.manage'
    ));
    const update = capability[1].find((item) => item[0] === 'update');
    const schema = promptInputSchema(turnPayload, update[3]);
    assert.equal(Object.hasOwn(schema.f, 'doel_sleutel'), false);
    const goalRef = turnPayload.fieldTypes[schema.f.doel_ref].e[0];
    const goalRevision = turnPayload.fieldTypes[schema.f.verwachte_revisie].e[0];
    assert.match(goalRef, /^goal-ref:[a-f0-9]{32}$/u);
    assert.equal(serializedRequest.split(goalRef).length - 1, 1,
      'de inhoudsloze requestreferentie staat exact eenmaal in de providerbody');
    assert.equal(goalRevision, Number(dueGoal.revision) + 1);
    modelGoalRef = goalRef;
    modelGoalRevision = goalRevision;
    const positive = turnPayload.turn.text.split('\n\n').at(-1);
    assert.match(positive, /^Herbekijk nu deze ene eigen richting/u);
    assert.match(positive, /bijstellen.*pauzeren.*afronden.*verlaten/u);
    const start = turnPayload.turn.text.indexOf(positive);
    const sourceSpans = [{ start, end: start + positive.length }];
    return completed([{ type: 'function_call', call_id: 'exact-goal-review-update',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: 'Herbekijk de geselecteerde eigen richting.',
          outcome: 'Het exacte doel heeft een nieuw toekomstig reviewmoment.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
        actions: [{ capabilityId: 'goal.portfolio.manage', operationId: 'update',
          polarity: 'request', semanticInput: { doel_ref: goalRef,
            verwachte_revisie: goalRevision, volgende_review: goalNextReview,
            titel: 'Veilig bijgestelde eigen doelrichting', prioriteit: 0.73 }, sourceSpans }],
        requestedEffects: ['persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
      }) }]);
  };
  const beforeGoalRun = autonomyStatus().last_run_ts;
  const goalTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
  assert.ok(goalTimer, 'de publieke scheduler moet voor de doelreview opnieuw bewapend zijn');
  goalTimer.callback(...goalTimer.args);
  const goalDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === beforeGoalRun && Date.now() < goalDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  const goalTurn = agentStatus().last_autonomy_turn;
  assert.equal(goalRequests, 1, 'de goal-binding gebruikt één interpretatiecall');
  assert.match(modelGoalRef, /^goal-ref:/u);
  assert.deepEqual(goalTurn.tools.map((tool) => tool.name), ['doel_bijhouden'],
    JSON.stringify(goalTurn));
  assert.ok(goalTurn.tools[0].ok && goalTurn.tools[0].verified, JSON.stringify(goalTurn));
  assert.equal(goalTurn.tools[0].output, '');
  assert.equal(JSON.stringify(agentStatus()).includes(dueGoal.goal_key), false,
    'de geautoriseerde statusprojectie bevat geen raw key uit het toolresultaat');
  assert.equal(JSON.stringify(agentStatus()).includes(goalTitleSecret), false);
  const updatedGoal = db.prepare('SELECT * FROM eva_goals WHERE goal_key=?').get(dueGoal.goal_key);
  const untouchedSibling = db.prepare('SELECT * FROM eva_goals WHERE goal_key=?').get(
    siblingGoal.goal_key,
  );
  assert.equal(updatedGoal.next_review_ts, goalNextReview);
  assert.equal(updatedGoal.revision, modelGoalRevision + 1);
  assert.equal(updatedGoal.title, 'Veilig bijgestelde eigen doelrichting');
  assert.equal(updatedGoal.priority, 0.73);
  assert.equal(untouchedSibling.revision, siblingRevision);
  const goalUnderstandingRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(goalTurn.turnId);
  const goalUnderstanding = JSON.parse(goalUnderstandingRow.meta).understanding;
  assert.equal(Object.hasOwn(goalUnderstanding.actions[0], 'semanticInput'), false,
    'het privacyrecord bewaart geen semantische invoer');
  const goalActionRow = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? AND json_extract(meta,'$.tool')='doel_bijhouden'
    ORDER BY id DESC LIMIT 1`).get(goalTurn.turnId);
  const goalAction = JSON.parse(goalActionRow.meta);
  assert.deepEqual(goalAction.args, {
    bewerking: 'update',
    verwachte_revisie: modelGoalRevision,
    volgende_review: goalNextReview,
    titel: 'Veilig bijgestelde eigen doelrichting',
    prioriteit: 0.73,
    doel_sleutel: dueGoal.goal_key,
    verwacht_doel_id: dueGoal.id,
  }, 'de interne ledger onderscheidt modelvelden van de exact geattesteerde targetresolutie');
  assert.equal(goalAction.semanticBinding.capabilityId, 'goal.portfolio.manage');
  assert.equal(goalAction.semanticBinding.operationId, 'update');
  assert.equal(goalAction.semanticBinding.requirementId, 'r1');
  assert.deepEqual(goalAction.semanticBinding.targetResolution, {
    kind: 'self_attention_goal_ref', source: 'host_receipt',
    modelField: 'doel_ref', resolved: true,
  });
  const outwardGoalTools = __projectRouteTest.privacySafeTurnTools([{
    name: 'doel_bijhouden', args: goalAction.args,
    output: JSON.stringify({ ok: true, changed: true, goal: updatedGoal }),
    ok: true, changed: true, verified: true, needs_followup: false, partial: false,
    failure_class: null, retryable: null, effect_uncertain: false,
    evidence: { summary: JSON.stringify(updatedGoal) }, at: new Date().toISOString(),
    semanticBinding: goalAction.semanticBinding,
  }]);
  assert.equal(JSON.stringify(outwardGoalTools).includes(dueGoal.goal_key), false);
  assert.equal(JSON.stringify(outwardGoalTools).includes(updatedGoal.title), false);
  assert.equal(outwardGoalTools[0].output, '');
  assert.equal(Object.hasOwn(outwardGoalTools[0].args, 'doel_sleutel'), false);
  assert.equal(autonomyStatus().current_selection, null);

  const driftGoal = manageGoal({
    operation: 'create', title: 'Private CAS drift veilig afwijzen',
    intent: 'Een bron- of revisiewijziging tijdens de review mag niet worden overschreven.',
    why: 'De private targetbinding blijft een CAS en geen hostbevel.',
    beneficiary: 'Eva en het gezin', horizon: 'deze week',
    successMetrics: ['drift geeft changed false zonder sleutel in failureaudit'], source: 'self',
    nextReviewTs: new Date(Date.now() - 60_000).toISOString(),
  }).goal;
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  let driftRequests = 0;
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    driftRequests += 1;
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    const capability = turnPayload.capabilities.find((item) => (
      item[0] === 'goal.portfolio.manage'
    ));
    const update = capability[1].find((item) => item[0] === 'update');
    const schema = promptInputSchema(turnPayload, update[3]);
    const goalRef = turnPayload.fieldTypes[schema.f.doel_ref].e[0];
    const revision = turnPayload.fieldTypes[schema.f.verwachte_revisie].e[0];
    const positive = turnPayload.turn.text.split('\n\n').at(-1);
    const start = turnPayload.turn.text.indexOf(positive);
    const sourceSpans = [{ start, end: start + positive.length }];
    db.prepare("UPDATE eva_goals SET source='conversation' WHERE id=?").run(driftGoal.id);
    return completed([{ type: 'function_call', call_id: 'drifted-goal-update',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Review het exact geselecteerde doel.',
          outcome: 'De CAS bepaalt of het doel nog wijzigbaar is.', beneficiary: 'self' },
        conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
        actions: [{ capabilityId: 'goal.portfolio.manage', operationId: 'update',
          polarity: 'request', semanticInput: { doel_ref: goalRef,
            verwachte_revisie: revision,
            volgende_review: new Date(Date.now() + 3 * 60 * 60_000).toISOString() },
          sourceSpans }], requestedEffects: ['persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
      }) }]);
  };
  const beforeDriftRun = autonomyStatus().last_run_ts;
  const driftTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
  assert.ok(driftTimer);
  driftTimer.callback(...driftTimer.args);
  const driftDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === beforeDriftRun && Date.now() < driftDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  assert.equal(driftRequests, 1);
  const driftTurn = agentStatus().last_autonomy_turn;
  assert.deepEqual(driftTurn.tools.map((item) => item.name), ['doel_bijhouden']);
  assert.equal(driftTurn.tools[0].ok, false);
  const driftActionRow = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? AND json_extract(meta,'$.tool')='doel_bijhouden'
    ORDER BY id DESC LIMIT 1`).get(driftTurn.turnId);
  const driftAction = JSON.parse(driftActionRow.meta);
  assert.equal(driftAction.changed, false);
  assert.equal(driftAction.args.doel_sleutel, undefined);
  assert.equal(driftAction.args.verwacht_doel_id, driftGoal.id);
  assert.deepEqual(driftAction.args.target_resolution,
    driftAction.semanticBinding.targetResolution);
  assert.equal(JSON.stringify(driftAction).includes(driftGoal.goal_key), false);
  assert.equal(JSON.stringify(agentStatus()).includes(driftGoal.goal_key), false);
  const driftedRow = db.prepare('SELECT * FROM eva_goals WHERE id=?').get(driftGoal.id);
  assert.equal(driftedRow.source, 'conversation');
  assert.equal(driftedRow.revision, Number(driftGoal.revision) + 1,
    'de schedulerclaim is de enige revisiewijziging');
  db.prepare("UPDATE eva_goals SET source='self', status='abandoned' WHERE id=?").run(driftGoal.id);

  const preemptGoal = manageGoal({
    operation: 'create', title: 'Preemptie tijdens private doelreview bewaken',
    intent: 'Een bewonersbeurt moet een lopende eigen doelreview vóór ieder effect stoppen.',
    why: 'Eigen aandacht wijkt altijd voor nieuwe interactieve ingress.',
    beneficiary: 'Eva en het gezin', horizon: 'deze week',
    successMetrics: ['na preemptie blijft alleen de schedulerclaim zichtbaar'], source: 'self',
    nextReviewTs: new Date(Date.now() - 60_000).toISOString(),
  }).goal;
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(), pending_signals: [],
    daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0,
  });
  const actionRowsBeforePreemption = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count;
  const executionRowsBeforePreemption = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  let releaseBoundedRetryPause;
  let signalBoundedRetryPause;
  const boundedRetryPauseStarted = new Promise((resolve) => {
    signalBoundedRetryPause = resolve;
  });
  boundedRetryPauseHook = (callback, args) => {
    const handle = { callback, args, cancelled: false };
    releaseBoundedRetryPause = () => {
      if (!handle.cancelled) originalSetTimeout(callback, 0, ...args);
    };
    signalBoundedRetryPause();
    return handle;
  };
  let preemptedGoalProviderCalls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      return completed([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'De bewonersbeurt is voorgegaan.' }] }]);
    }
    const turnPayload = JSON.parse(request.input[0].content[0].text);
    if (turnPayload.context.selfAttention?.kind !== 'goal') {
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
            replyDraft: 'Ik ben er; jouw beurt gaat nu voor.' },
        }) }]);
    }
    preemptedGoalProviderCalls += 1;
    return new Response('tijdelijk niet beschikbaar', { status: 503 });
  };

  const beforePreemptRun = autonomyStatus().last_run_ts;
  const preemptTimer = [...scheduledTimers].reverse().find((item) => !item.cancelled);
  assert.ok(preemptTimer);
  preemptTimer.callback(...preemptTimer.args);
  await boundedRetryPauseStarted;
  const epochBeforeUser = agentStatus().interaction_epoch;
  await handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text: 'Ben je er nu even?',
    messageContext: { conversationId: 'web:self-attention-preemption', syntheticTest: true },
  });
  assert.equal(agentStatus().interactive_pending, 0);
  assert.equal(agentStatus().interaction_epoch, epochBeforeUser + 1);
  releaseBoundedRetryPause();
  boundedRetryPauseHook = null;
  const preemptDeadline = Date.now() + 10_000;
  while (autonomyStatus().last_run_ts === beforePreemptRun && Date.now() < preemptDeadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  assert.equal(preemptedGoalProviderCalls, 1);
  assert.deepEqual(agentStatus().last_autonomy_turn.tools, []);
  assert.equal(agentStatus().last_autonomy_turn.superseded, true);
  assert.equal(autonomyStatus().last_result.yielded, true);
  assert.equal(Object.hasOwn(autonomyStatus().last_result, 'error'), false);
  const preemptedGoal = db.prepare('SELECT * FROM eva_goals WHERE id=?').get(preemptGoal.id);
  assert.equal(preemptedGoal.revision, Number(preemptGoal.revision) + 1,
    'alleen de pre-provider schedulerclaim mag zichtbaar zijn');
  assert.equal(preemptedGoal.status, 'active');
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count,
    actionRowsBeforePreemption);
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    executionRowsBeforePreemption);
  assert.equal(autonomyStatus().current_selection, null);

});
