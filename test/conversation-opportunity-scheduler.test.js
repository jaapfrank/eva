import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-conversation-scheduler-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'conversation-scheduler-test-only';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
process.env.NODE_ENV = 'test';

// Autonomy wordt als eerste eigenaar van de private runtimevalidator geladen.
const { autonomyStatus, startAutonomy } = await import('../src/mind/autonomy.js');
const { agentStatus } = await import('../src/mind/agent.js');
const { archiveTelegramMessage, db, kvSet } = await import('../src/db.js');
const { createDurableWork, updateDurableWork } = await import('../src/mind/portfolio.js');

const SCHEDULER_KEY = 'eva_autonomy_scheduler_v1';
const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const timers = [];

after(() => {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completed(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: {
      output, usage: { input_tokens: 300, output_tokens: 80, total_tokens: 380 },
    },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function searchUnderstanding(payload) {
  const span = { start: 0, end: payload.turn.text.length };
  return completed([{
    type: 'function_call', call_id: `search-${Date.now()}`,
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: {
        statement: 'Lees de nog open bewonerslus in de exact gebonden Telegramchat.',
        outcome: 'De open bewonerslus is met echte gesprekscontext beoordeeld.',
        beneficiary: 'household',
      },
      conditions: [],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'conversation.history.read', operationId: 'search',
        polarity: 'request', semanticInput: { vraag: 'lampvraag antwoord' },
        sourceSpans: [span],
      }],
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
      delivery: { mode: 'silent', target: 'none' },
      proofNeeds: ['source_read'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }),
  }]);
}

function forceDue() {
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
    daily_preempted_ms: 0,
  });
}

function latestDurableSearchOutput() {
  const row = db.prepare(`SELECT output FROM turn_tool_executions
    WHERE tool='zoek_gesprek' AND status='done'
    ORDER BY execution_seq DESC LIMIT 1`).get();
  assert.ok(row?.output, 'zoek_gesprek moet via receipt:conversation duurzaam zijn vastgelegd');
  return String(row.output);
}

async function triggerScheduler(previousRun = null) {
  const timer = timers.find((candidate) => !candidate.cancelled && !candidate.fired);
  assert.ok(timer, 'de echte scheduler moet een tick hebben uitgegeven');
  timer.fired = true;
  timer.callback(...timer.args);
  const deadline = Date.now() + 10_000;
  while ((!autonomyStatus().last_run_ts
      || autonomyStatus().last_run_ts === previousRun
      || autonomyStatus().running) && Date.now() < deadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  assert.notEqual(autonomyStatus().last_run_ts, previousRun);
  return agentStatus().last_autonomy_turn;
}

test('productiescheduler bindt episodezoeking aan één chat en faalt zonder episode gesloten', async () => {
  const query = 'lampvraag antwoord';
  archiveTelegramMessage({
    chatId: 'scheduler-private-a', messageId: '10', sender: 'Jaap', role: 'user',
    content: 'Mijn lampvraag kreeg geen antwoord hoewel het licht wel uitging.',
    processingStatus: 'failed',
    meta: { inbound: true, fromTelegramId: 1001 },
  });
  archiveTelegramMessage({
    chatId: 'scheduler-private-b', messageId: '99', sender: 'Mila', role: 'user',
    content: `PRIVATE_SCHEDULER_LEAK ${query}`,
    processingStatus: 'failed',
    meta: { inbound: true, fromTelegramId: 2002 },
  });
  const { workItem, episode } = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: `Herstel ${query}`,
    goal: `Lees ${query} en herstel de nog open bewonerslus.`,
    nextStep: `Zoek ${query} alleen in de bronchat.`,
    definitionOfDone: 'Jaap krijgt een inhoudelijk herstelantwoord in dezelfde Telegramchat.',
    visibleToFamily: true, priority: 100,
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:scheduler-private-a', originMessageId: '10',
    requester: 'Jaap', deliveryPolicy: 'required', deliveryTarget: 'Jaap',
  });
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();

  globalThis.setTimeout = (callback, delay, ...args) => {
    const scheduler = /\btick\(\)/u.test(String(callback));
    if (!scheduler) return originalSetTimeout(callback, delay, ...args);
    const handle = { callback, delay, args, cancelled: false, fired: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle && typeof handle === 'object' && Object.hasOwn(handle, 'fired')) {
      handle.cancelled = true;
    } else originalClearTimeout(handle);
  };
  globalThis.fetch = async (_input, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    const payload = JSON.parse(request.input[0].content[0].text);
    return searchUnderstanding(payload);
  };

  forceDue();
  startAutonomy();
  const boundTurn = await triggerScheduler();
  const boundSearch = boundTurn.tools.find((tool) => tool.name === 'zoek_gesprek');
  assert.ok(boundSearch, JSON.stringify(boundTurn));
  const boundOutput = latestDurableSearchOutput();
  const boundResult = JSON.parse(boundOutput);
  assert.equal(boundResult.ok, true, boundOutput);
  assert.equal(boundResult.data.source_binding.chat_id, 'scheduler-private-a');
  assert.equal(boundResult.data.source_binding.work_id, workItem.id);
  assert.equal(boundResult.data.source_binding.goal_episode_id, episode.id);
  assert.equal(boundResult.data.messages[0].message_id, '10');
  assert.doesNotMatch(boundOutput, /PRIVATE_SCHEDULER_LEAK/u);

  updateDurableWork(workItem.id, {
    status: 'blocked', evidence: 'Scheduler-scopetest tijdelijk geparkeerd.',
    nextWakeTs: '2099-01-01T00:00:00.000Z',
    wait: { type: 'time', until: '2099-01-01T00:00:00.000Z' },
  });
  forceDue();
  const priorRun = autonomyStatus().last_run_ts;
  const freeTurn = await triggerScheduler(priorRun);
  const freeSearch = freeTurn.tools.find((tool) => tool.name === 'zoek_gesprek');
  assert.ok(freeSearch, JSON.stringify(freeTurn));
  const freeOutput = latestDurableSearchOutput();
  const freeResult = JSON.parse(freeOutput);
  assert.equal(freeResult.ok, false, freeOutput);
  assert.equal(freeResult.failure_class, 'conversation_scope_unbound');
  assert.doesNotMatch(freeOutput, /PRIVATE_SCHEDULER_LEAK|Mijn lampvraag/u);
});
