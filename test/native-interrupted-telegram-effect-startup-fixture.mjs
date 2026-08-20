import assert from 'node:assert/strict';

const [dataDir, mode, scenario = 'done_read'] = process.argv.slice(2);
const scenarios = new Set([
  'done_read', 'started_effect', 'started_transport', 'done_mutation', 'malformed',
  'existing_reply', 'existing_origin', 'newer_message',
  'legacy_reply', 'foreign_reply', 'stale_reply', 'reply_hash_drift',
  'existing_reply_started_effect', 'existing_origin_done_mutation',
  'newer_message_started_transport', 'newer_message_malformed',
]);
if (!dataDir || !['crash', 'seed', 'recover'].includes(mode) || !scenarios.has(scenario)) {
  throw new Error('native interrupted Telegram-effectfixture mist datamap of mode');
}

process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-interrupted-telegram-effect-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-interrupted-telegram-effect-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.HA_URL = 'http://native-ha.invalid';
process.env.HA_TOKEN = 'native-interrupted-telegram-effect-ha-token';

const { Bot } = await import('grammy');

const chatId = '86101';
const messageId = '62101';
const requester = 'Jaap';
const entityId = 'sensor.startup_effect_probe';
const userText = `Lees ${entityId} en vertel wat je werkelijk ziet.`;
const update = {
  update_id: mode === 'crash' ? 93_001 : 93_002,
  message: {
    message_id: Number(messageId), date: 1_786_400_200,
    chat: { id: Number(chatId), type: 'private', first_name: requester },
    from: { id: 424242, is_bot: false, first_name: requester },
    text: userText,
  },
};

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse() {
  return completedModelResponse([{
    type: 'function_call', call_id: 'native-interrupted-effect-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'question',
      goal: {
        statement: userText,
        outcome: 'De actuele sensorwaarde is gelezen en nuchter teruggegeven.',
        beneficiary: 'requester',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
        semanticInput: { entityId },
        sourceSpans: [{ start: 0, end: userText.length }],
      }],
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['source_read'],
      ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: 'Ik kijk even.' },
    }),
  }]);
}

let nativeBot = null;
let sends = 0;
let providerCalls = 0;
let homeReads = 0;
const originalStart = Bot.prototype.start;
const originalSetTimeout = globalThis.setTimeout;
const originalSetInterval = globalThis.setInterval;
Bot.prototype.start = function startWithoutPolling() {
  nativeBot = this;
  this.botInfo = {
    id: 123456789, is_bot: true, first_name: 'Eva Test', username: 'eva_test_bot',
    can_join_groups: true, can_read_all_group_messages: false, supports_inline_queries: false,
  };
  this.api.config.use(async (_previous, method, payload) => {
    if (method === 'sendChatAction') return { ok: true, result: true };
    if (method !== 'sendMessage') throw new Error(`onverwachte Telegram-call ${method}`);
    sends += 1;
    return { ok: true, result: {
      message_id: 64_000 + sends, date: 1_786_400_300 + sends,
      chat: { id: Number(payload.chat_id), type: 'private', first_name: requester },
      from: this.botInfo, text: String(payload.text || ''),
    } };
  });
  return Promise.resolve();
};
globalThis.setTimeout = () => Object.freeze({ unref() {} });
globalThis.setInterval = () => Object.freeze({ unref() {} });

const {
  archiveTelegramMessage, claimTurnToolExecution, completeTurnToolExecution, db,
  telegramMessage,
} = await import('../src/db.js');
await import('./telegram-test-composition.js');
const executionTurnId = `telegram:${chatId}:${messageId}`;
const parentRow = () => db.prepare(`SELECT * FROM main.turn_tool_executions
  WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(messageId);
const childRows = () => db.prepare(`SELECT execution_key,turn_id,tool,status,
    intent_record_sha256,completion_record_sha256
  FROM main.turn_tool_executions WHERE turn_id=? AND tool<>'@turn/understanding'
  ORDER BY execution_seq`).all(executionTurnId);
const originCount = () => Number(db.prepare(`SELECT count(*) AS value
  FROM main.eva_episode_origins WHERE origin_channel='telegram'
    AND origin_conversation_id=? AND origin_message_id=?`).get(
  `telegram:${chatId}`, messageId,
).value);
const handoffCount = () => Number(db.prepare(`SELECT count(*) AS value
  FROM main.eva_interpretation_retry_handoffs WHERE origin_channel='telegram'
    AND origin_conversation_id=? AND origin_message_id=?`).get(
  `telegram:${chatId}`, messageId,
).value);

async function seedAcceptedReply() {
  const { __telegramTransportTest } = await import('../src/integrations/telegram.js');
  const signalRef = `turn:telegram:${chatId}:${messageId}`;
  const deliveryKey = `user-reply:${signalRef}`;
  __telegramTransportTest.setBotForTest({
    api: {
      async sendMessage(targetChatId, text, options = {}) {
        return {
          chat: { id: Number(targetChatId) },
          message_id: 64_101,
          date: 1_786_400_250,
          from: { id: 123456789, is_bot: true, first_name: 'Eva Test' },
          text,
          ...(options.reply_parameters?.message_id ? {
            reply_to_message: { message_id: options.reply_parameters.message_id },
          } : {}),
        };
      },
    },
  });
  try {
    const sent = await __telegramTransportTest.sendTelegramText(
      chatId,
      'Dit antwoord is aantoonbaar door Telegram geaccepteerd.',
      {
        dedupeKey: deliveryKey,
        replyToMessageId: messageId,
        directTurnSignalRef: signalRef,
        originPrincipal: 'telegram:424242',
      },
    );
    assert.equal(sent.length, 1);
  } finally {
    __telegramTransportTest.setBotForTest(null);
  }
  const reply = telegramMessage(chatId, '64101');
  assert.equal(reply?.sender, 'Eva');
  assert.equal(reply?.meta?.transportAccepted, true);
  assert.match(String(reply?.meta?.operationKey || ''), /^telegram-text:/u);
  assert.match(String(reply?.meta?.receiptSha256 || ''), /^[a-f0-9]{64}$/u);
  assert.equal(db.prepare(`SELECT status FROM main.turn_tool_executions
    WHERE execution_key=?`).get(reply.meta.operationKey)?.status, 'transport_accepted');
  return reply;
}

if (mode === 'seed') {
  assert.ok(parentRow());
  const executionKey = `native-startup-effect:${scenario}:${messageId}`;
  if (scenario === 'started_effect') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: 'ha_zet',
      args: { entityId: 'light.startup_effect_probe', state: 'off' },
    });
  } else if (scenario === 'started_transport') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: '@transport/telegram.sendMessage',
      args: { chatId, messageId, payloadSha256: 'a'.repeat(64) },
      initialStatus: 'transport_sending',
    });
  } else if (scenario === 'done_mutation') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: 'ha_zet',
      args: { entityId: 'light.startup_effect_probe', state: 'off' },
    });
    completeTurnToolExecution(executionKey, JSON.stringify({
      contract: 'ActionResult/v1', tool: 'ha_zet', ok: true, changed: true,
      verified: true, needs_followup: false, partial: false,
      failure_class: null, retryable: false, effect_uncertain: false,
      execution_started: true,
    }));
  } else if (scenario === 'malformed') {
    const sequence = Number(db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
      FROM main.turn_tool_executions`).get().value);
    db.prepare(`INSERT INTO main.turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,execution_seq,
      execution_contract,execution_verification
    ) VALUES (?,?,?,?,?,?,?,?,?)`).run(
      executionKey, executionTurnId, 'ha_zet',
      JSON.stringify({ entityId: 'light.startup_effect_probe', state: 'off' }),
      'started', new Date().toISOString(), sequence,
      'ExecutionRecord/v1', 'legacy_unverifiable',
    );
  } else if (scenario === 'existing_reply') {
    await seedAcceptedReply();
  } else if (scenario === 'legacy_reply') {
    archiveTelegramMessage({
      chatId, messageId: '64101', sender: 'Eva', role: 'assistant',
      kind: 'text', content: 'Lokaal reeds afgeleverd antwoord.',
      replyToMessageId: messageId, processingStatus: 'completed',
    });
  } else if (scenario === 'foreign_reply') {
    await seedAcceptedReply();
    archiveTelegramMessage({
      chatId, messageId: '64101', sender: 'Andere bot', role: 'assistant',
    });
  } else if (scenario === 'stale_reply') {
    const reply = await seedAcceptedReply();
    archiveTelegramMessage({
      chatId, messageId: '64101', role: 'assistant',
      meta: { ...reply.meta, retracted: true },
    });
  } else if (scenario === 'reply_hash_drift') {
    const reply = await seedAcceptedReply();
    archiveTelegramMessage({
      chatId, messageId: '64101', role: 'assistant',
      meta: { ...reply.meta, receiptSha256: 'f'.repeat(64) },
    });
  } else if (scenario === 'existing_origin') {
    const parentArgs = JSON.parse(parentRow().args);
    const { createInterpretationRetryHandoff } = await import('../src/mind/portfolio.js');
    createInterpretationRetryHandoff({
      channel: 'telegram', conversationId: `telegram:${chatId}`, messageId,
      requester, originalText: userText, failureClass: 'seeded_existing_origin',
      sessionRef: parentArgs.sessionRef,
      supersessionScope: parentArgs.supersessionScope,
      supersessionToken: parentArgs.supersessionToken,
      visibleToFamily: false,
    });
  } else if (scenario === 'newer_message') {
    archiveTelegramMessage({
      chatId, messageId: String(Number(messageId) + 1), sender: requester, role: 'user',
      kind: 'text', content: 'Dit nieuwere bericht supersedeert de oude vraag.',
      meta: { inbound: true, fromTelegramId: '424242' },
    });
  } else if (scenario === 'existing_reply_started_effect') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: 'ha_zet',
      args: { entityId: 'light.startup_effect_probe', state: 'off' },
    });
    await seedAcceptedReply();
  } else if (scenario === 'existing_origin_done_mutation') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: 'ha_zet',
      args: { entityId: 'light.startup_effect_probe', state: 'off' },
    });
    completeTurnToolExecution(executionKey, JSON.stringify({
      contract: 'ActionResult/v1', tool: 'ha_zet', ok: true, changed: true,
      verified: true, needs_followup: false, partial: false,
      failure_class: null, retryable: false, effect_uncertain: false,
      execution_started: true,
    }));
    const parentArgs = JSON.parse(parentRow().args);
    const { createInterpretationRetryHandoff } = await import('../src/mind/portfolio.js');
    createInterpretationRetryHandoff({
      channel: 'telegram', conversationId: `telegram:${chatId}`, messageId,
      requester, originalText: userText, failureClass: 'seeded_existing_origin',
      sessionRef: parentArgs.sessionRef,
      supersessionScope: parentArgs.supersessionScope,
      supersessionToken: parentArgs.supersessionToken,
      visibleToFamily: false,
    });
  } else if (scenario === 'newer_message_started_transport') {
    claimTurnToolExecution({
      executionKey, turnId: executionTurnId, tool: '@transport/telegram.sendMessage',
      args: { chatId, messageId, payloadSha256: 'a'.repeat(64) },
      initialStatus: 'transport_sending',
    });
    archiveTelegramMessage({
      chatId, messageId: String(Number(messageId) + 1), sender: requester, role: 'user',
      kind: 'text', content: 'Dit nieuwere bericht supersedeert de oude vraag.',
      meta: { inbound: true, fromTelegramId: '424242' },
    });
  } else if (scenario === 'newer_message_malformed') {
    const sequence = Number(db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
      FROM main.turn_tool_executions`).get().value);
    db.prepare(`INSERT INTO main.turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,execution_seq,
      execution_contract,execution_verification
    ) VALUES (?,?,?,?,?,?,?,?,?)`).run(
      executionKey, executionTurnId, 'ha_zet',
      JSON.stringify({ entityId: 'light.startup_effect_probe', state: 'off' }),
      'started', new Date().toISOString(), sequence,
      'ExecutionRecord/v1', 'legacy_unverifiable',
    );
    archiveTelegramMessage({
      chatId, messageId: String(Number(messageId) + 1), sender: requester, role: 'user',
      kind: 'text', content: 'Dit nieuwere bericht supersedeert de oude vraag.',
      meta: { inbound: true, fromTelegramId: '424242' },
    });
  } else {
    throw new Error(`scenario ${scenario} heeft geen seed nodig`);
  }
  if (['started_effect', 'started_transport', 'done_mutation', 'malformed',
    'existing_reply_started_effect', 'existing_origin_done_mutation',
    'newer_message_started_transport', 'newer_message_malformed'].includes(scenario)) {
    assert.equal(childRows().length, 1);
  } else {
    assert.equal(childRows().length, 0);
  }
  db.close();
  process.exit(0);
}

if (mode === 'recover') {
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('effectreconciliatie tijdens startup mag geen provider of tool-I/O bereiken');
  };
}

const before = mode === 'recover' ? {
  parentStatus: parentRow()?.status || null,
  children: childRows(),
  origins: originCount(),
  handoffs: handoffCount(),
  inboundStatus: telegramMessage(chatId, messageId)?.processing_status || null,
} : null;

const telegram = await import('../src/integrations/telegram.js');
await telegram.startTelegram();
Bot.prototype.start = originalStart;
globalThis.setTimeout = originalSetTimeout;
globalThis.setInterval = originalSetInterval;
assert.ok(nativeBot);

if (mode === 'crash') {
  globalThis.fetch = async (url) => {
    if (scenario !== 'done_read') {
      providerCalls += 1;
      process.exit(73);
    }
    const target = String(url);
    if (target === `${process.env.HA_URL}/api/states/${entityId}`) {
      homeReads += 1;
      return new Response(JSON.stringify({
        entity_id: entityId,
        state: 'ready',
        attributes: { friendly_name: 'Startup effect probe' },
        last_updated: '2026-08-15T10:00:00.000Z',
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    providerCalls += 1;
    if (providerCalls === 1) return understandingResponse();
    const parent = parentRow();
    const children = childRows();
    assert.equal(parent?.status, 'started');
    assert.equal(originCount(), 0);
    assert.equal(handoffCount(), 0);
    assert.equal(homeReads, 1);
    assert.equal(children.length, 1);
    assert.equal(children[0].tool, 'ha_lees');
    assert.equal(children[0].status, 'done');
    assert.match(String(children[0].completion_record_sha256 || ''), /^[a-f0-9]{64}$/u);
    process.exit(73);
  };
  await nativeBot.handleUpdate(update);
  throw new Error('native effectcrashfixture had na de duurzame childreceipt moeten stoppen');
}

const parent = parentRow();
const output = parent?.output ? JSON.parse(parent.output) : null;
const result = {
  before,
  after: {
    parentStatus: parent?.status || null,
    parentError: output?.error || null,
    durableHandoff: output?.metadata?.durableHandoff === true,
    noPlanDecision: output?.metadata?.noPlanDecision || null,
    children: childRows(),
    origins: originCount(),
    handoffs: handoffCount(),
    inboundStatus: telegramMessage(chatId, messageId)?.processing_status || null,
    inboundError: telegramMessage(chatId, messageId)?.processing_error || null,
  },
  providerCalls,
  homeReads,
  sends,
};
process.stdout.write(`NATIVE_INTERRUPTED_TELEGRAM_EFFECT=${JSON.stringify(result)}\n`);
db.close();
process.exit(0);
