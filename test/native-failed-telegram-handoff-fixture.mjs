import assert from 'node:assert/strict';

const dataDir = process.argv[2];
if (!dataDir) throw new Error('native failed Telegram-fixture mist datamap');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-failed-telegram-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-failed-telegram-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';

const { Bot } = await import('grammy');
const {
  archiveTelegramMessage, claimTelegramMessage, db, failTelegramMessage, telegramMessage,
} = await import('../src/db.js');
const portfolio = await import('../src/mind/portfolio.js');
const telegram = await import('../src/integrations/telegram.js');
const { runAutonomousTurn } = await import('../src/mind/agent.js');

const chatId = '85001';
const messageId = '61001';
const threadId = 19;
const requester = 'Jaap';
archiveTelegramMessage({
  chatId, messageId, ts: 1_786_300_000, sender: requester, role: 'user', kind: 'text',
  content: 'Rond deze mislukte opdracht later veilig af.',
  meta: { inbound: true, fromTelegramId: '424242', messageThreadId: String(threadId) },
});
assert.equal(claimTelegramMessage(chatId, messageId).claimed, true);
assert.equal(failTelegramMessage(chatId, messageId, 'native providercrash'), true);

let nativeBot = null;
let sends = 0;
let providerCalls = 0;
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
    if (method !== 'sendMessage') throw new Error(`onverwachte Telegram-call ${method}`);
    sends += 1;
    return { ok: true, result: {
      message_id: 62000 + sends, date: 1_786_300_100 + sends,
      chat: { id: Number(payload.chat_id), type: 'private', first_name: requester },
      from: this.botInfo, text: String(payload.text || ''),
      message_thread_id: payload.message_thread_id,
      reply_to_message: { message_id: payload.reply_parameters?.message_id },
    } };
  });
  return Promise.resolve();
};
globalThis.setTimeout = () => Object.freeze({ unref() {} });
globalThis.setInterval = () => Object.freeze({ unref() {} });
try {
  await telegram.startTelegram();
} finally {
  Bot.prototype.start = originalStart;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.setInterval = originalSetInterval;
}
assert.ok(nativeBot);

const bound = db.prepare(`SELECT o.episode_id,e.work_item_id,w.visible_to_family
  FROM eva_episode_origins o
  JOIN eva_goal_episodes e ON e.id=o.episode_id
  JOIN eva_work_items w ON w.id=e.work_item_id
  WHERE o.origin_channel='telegram' AND o.origin_conversation_id=?
    AND o.origin_message_id=?`).get(`telegram:${chatId}`, messageId);
assert.ok(bound);
assert.equal(bound.visible_to_family, 0);
assert.equal(telegramMessage(chatId, messageId).processing_status, 'failed');
const workCountBeforeRedelivery = Number(db.prepare('SELECT count(*) n FROM eva_work_items').get().n);
const episodeCountBeforeRedelivery = Number(db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n);
const originCountBeforeRedelivery = Number(db.prepare('SELECT count(*) n FROM eva_episode_origins').get().n);
globalThis.fetch = async () => {
  providerCalls += 1;
  throw new Error('native redelivery van een bestaande handoff mag geen provider bereiken');
};
const originalUpdate = {
  update_id: 91_001,
  message: {
    message_id: Number(messageId), date: 1_786_300_000,
    message_thread_id: threadId,
    chat: { id: Number(chatId), type: 'private', first_name: requester },
    from: { id: 424242, is_bot: false, first_name: requester },
    text: 'Rond deze mislukte opdracht later veilig af.',
  },
};
await nativeBot.handleUpdate(originalUpdate);
await nativeBot.handleUpdate({ ...originalUpdate, update_id: 91_002 });
assert.equal(providerCalls, 0);
assert.equal(sends, 0);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_work_items').get().n),
  workCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n),
  episodeCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_episode_origins').get().n),
  originCountBeforeRedelivery);
const reply = 'Ik heb je bericht weer opgepakt.';
const completedModelResponse = (output) => new Response(`data: ${JSON.stringify({
  type: 'response.completed', response: { output },
})}\n\n`, {
  status: 200, headers: { 'content-type': 'text/event-stream' },
});
globalThis.fetch = async () => {
  providerCalls += 1;
  return completedModelResponse([{
    type: 'function_call', call_id: 'native-failed-retry-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'continue',
      goal: {
        statement: 'Rond deze mislukte opdracht later veilig af.',
        outcome: 'Reageer op dezelfde draad.', beneficiary: 'requester',
      },
      conditions: [], provenance: { sourceSpans: [{ start: 0, end: 44 }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }]);
};
const prepared = await runAutonomousTurn({
  reason: 'bereid het mislukte Telegramantwoord exact voor',
  workId: bound.work_item_id, goalEpisodeId: bound.episode_id,
});
assert.equal(prepared.reply, '');
assert.equal(providerCalls, 1);
assert.equal(sends, 0);
const pendingWork = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(bound.work_item_id);
const pendingEpisode = portfolio.goalEpisodeById(bound.episode_id);
const pendingState = JSON.parse(pendingWork.state_json);
assert.equal(pendingEpisode.state, 'delivery_pending', JSON.stringify({
  prepared, lastError: pendingWork.last_error, state: pendingState,
}));
assert.equal(pendingState.interpretation_retry, undefined);
assert.equal(pendingState.pending_delivery.kind, 'interpretation_reply');
assert.equal(pendingState.pending_delivery.text, reply);
assert.equal(portfolio.verifyInterpretationRetryPendingDelivery(
  pendingWork, pendingEpisode, pendingState.pending_delivery,
), true);
assert.equal(telegramMessage(chatId, messageId).processing_status, 'failed');
const executionCountBeforePendingReplay = Number(db.prepare(
  'SELECT count(*) n FROM turn_tool_executions',
).get().n);
globalThis.fetch = async () => {
  providerCalls += 1;
  throw new Error('prepared redelivery mag de provider niet bereiken');
};
await nativeBot.handleUpdate({ ...originalUpdate, update_id: 91_003 });
await nativeBot.handleUpdate({ ...originalUpdate, update_id: 91_004 });
assert.equal(providerCalls, 1);
assert.equal(sends, 0);
assert.equal(Number(db.prepare('SELECT count(*) n FROM turn_tool_executions').get().n),
  executionCountBeforePendingReplay);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_work_items').get().n),
  workCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n),
  episodeCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_episode_origins').get().n),
  originCountBeforeRedelivery);

const validPendingWaitJson = pendingWork.wait_json;
const damagedPendingWait = JSON.parse(validPendingWaitJson);
damagedPendingWait.attempts = Number(damagedPendingWait.attempts) + 1;
db.prepare('UPDATE eva_work_items SET wait_json=? WHERE id=?').run(
  JSON.stringify(damagedPendingWait), bound.work_item_id,
);
const integrityLogsBeforeCorruptReplay = Number(db.prepare(`SELECT count(*) n
  FROM timeline WHERE kind='system'
    AND json_extract(meta,'$.reason')='registered_interpretation_handoff_integrity_failure'`)
  .get().n);
await nativeBot.handleUpdate({ ...originalUpdate, update_id: 91_005 });
assert.equal(providerCalls, 1);
assert.equal(sends, 0);
assert.equal(Number(db.prepare('SELECT count(*) n FROM turn_tool_executions').get().n),
  executionCountBeforePendingReplay);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_work_items').get().n),
  workCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n),
  episodeCountBeforeRedelivery);
assert.equal(Number(db.prepare('SELECT count(*) n FROM eva_episode_origins').get().n),
  originCountBeforeRedelivery);
const integrityLog = db.prepare(`SELECT content,meta FROM timeline WHERE kind='system'
  AND json_extract(meta,'$.reason')='registered_interpretation_handoff_integrity_failure'
  ORDER BY id DESC LIMIT 1`).get();
assert.ok(integrityLog);
assert.equal(Number(db.prepare(`SELECT count(*) n FROM timeline WHERE kind='system'
  AND json_extract(meta,'$.reason')='registered_interpretation_handoff_integrity_failure'`)
  .get().n), integrityLogsBeforeCorruptReplay + 1);
assert.deepEqual(JSON.parse(integrityLog.meta), {
  internalOnly: true,
  telegramArchive: true,
  duplicateUpdate: true,
  failedIngressRecovery: true,
  reason: 'registered_interpretation_handoff_integrity_failure',
});
assert.equal([
  integrityLog.content, integrityLog.meta,
].some((value) => String(value).includes(chatId)
  || String(value).includes(messageId)
  || String(value).includes(reply)), false);
db.prepare('UPDATE eva_work_items SET wait_json=? WHERE id=?').run(
  validPendingWaitJson, bound.work_item_id,
);

const delivered = await runAutonomousTurn({
  reason: 'bezorg het exact voorbereide Telegramantwoord',
  workId: bound.work_item_id, goalEpisodeId: bound.episode_id,
});
assert.equal(delivered.reply, reply);
assert.equal(providerCalls, 1);
assert.equal(sends, 1);
const inbound = telegramMessage(chatId, messageId);
const outbound = db.prepare(`SELECT * FROM telegram_messages
  WHERE chat_id=? AND role='assistant' AND deleted=0 AND reply_to_message_id=?
  ORDER BY id DESC LIMIT 1`).get(chatId, messageId);
const receipt = db.prepare(`SELECT * FROM eva_delivery_receipts
  WHERE episode_id=? ORDER BY id DESC LIMIT 1`).get(bound.episode_id);
assert.ok(outbound && receipt);
const executionCountAfterDelivery = Number(db.prepare(
  'SELECT count(*) n FROM turn_tool_executions',
).get().n);
await nativeBot.handleUpdate({ ...originalUpdate, update_id: 91_006 });
assert.equal(providerCalls, 1);
assert.equal(sends, 1);
assert.equal(Number(db.prepare('SELECT count(*) n FROM turn_tool_executions').get().n),
  executionCountAfterDelivery);
assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
  .get(bound.episode_id).n, 1);
const result = {
  workItemId: Number(bound.work_item_id), episodeId: Number(bound.episode_id),
  workStatus: db.prepare('SELECT status FROM eva_work_items WHERE id=?')
    .get(bound.work_item_id).status,
  episodeState: portfolio.goalEpisodeById(bound.episode_id).state,
  inboundStatus: inbound.processing_status, sends,
  providerCalls,
  outboundChatId: outbound.chat_id,
  outboundReplyTo: outbound.reply_to_message_id,
  outboundThread: JSON.parse(outbound.meta).messageThreadId,
  receiptOriginConversationId: receipt.origin_conversation_id,
  receiptOriginMessageId: receipt.origin_message_id,
};
process.stdout.write(`NATIVE_FAILED_TELEGRAM=${JSON.stringify(result)}\n`);
db.close();
process.exit(0);
