import assert from 'node:assert/strict';

const [dataDir, mode] = process.argv.slice(2);
if (!dataDir || !['crash', 'recover'].includes(mode)) {
  throw new Error('native interrupted Telegram-fixture mist datamap of mode');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-interrupted-telegram-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-interrupted-telegram-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';

const { Bot } = await import('grammy');

const chatId = '86001';
const messageId = '62001';
const requester = 'Jaap';
const userText = 'Pak mijn gecrashte Telegramvraag veilig weer op.';
const update = {
  update_id: mode === 'crash' ? 92_001 : 92_002,
  message: {
    message_id: Number(messageId), date: 1_786_400_000,
    chat: { id: Number(chatId), type: 'private', first_name: requester },
    from: { id: 424242, is_bot: false, first_name: requester },
    text: userText,
  },
};

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
    if (method === 'sendChatAction') return { ok: true, result: true };
    if (method !== 'sendMessage') throw new Error(`onverwachte Telegram-call ${method}`);
    sends += 1;
    return { ok: true, result: {
      message_id: 63_000 + sends, date: 1_786_400_100 + sends,
      chat: { id: Number(payload.chat_id), type: 'private', first_name: requester },
      from: this.botInfo, text: String(payload.text || ''),
    } };
  });
  return Promise.resolve();
};
globalThis.setTimeout = () => Object.freeze({ unref() {} });
globalThis.setInterval = () => Object.freeze({ unref() {} });
if (mode === 'recover') {
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('startup-adoptie mag geen provider bereiken');
  };
}

const telegram = await import('../src/integrations/telegram.js');
await telegram.startTelegram();
Bot.prototype.start = originalStart;
globalThis.setTimeout = originalSetTimeout;
globalThis.setInterval = originalSetInterval;
assert.ok(nativeBot);

if (mode === 'crash') {
  globalThis.fetch = async () => {
    providerCalls += 1;
    process.exit(73);
  };
  await nativeBot.handleUpdate(update);
  throw new Error('native crashfixture had vóór providercrash moeten stoppen');
}

const { db, telegramMessage } = await import('../src/db.js');
const execution = db.prepare(`SELECT * FROM turn_tool_executions
  WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(messageId);
const generations = db.prepare(`SELECT source_generation
  FROM eva_turn_understanding_authorities_v1 WHERE execution_key=?
  ORDER BY source_generation`).all(execution?.execution_key)
  .map(({ source_generation: generation }) => Number(generation));
const handoff = db.prepare(`SELECT h.work_item_id,h.episode_id,w.visible_to_family
  FROM eva_interpretation_retry_handoffs h
  JOIN eva_work_items w ON w.id=h.work_item_id
  WHERE h.origin_channel='telegram' AND h.origin_conversation_id=?
    AND h.origin_message_id=?`).get(`telegram:${chatId}`, messageId);
assert.ok(execution && handoff);
await nativeBot.handleUpdate(update);
const result = {
  executionStatus: execution.status,
  durableHandoff: JSON.parse(execution.output).metadata.durableHandoff,
  generations,
  inboundStatus: telegramMessage(chatId, messageId).processing_status,
  workItemId: Number(handoff.work_item_id), episodeId: Number(handoff.episode_id),
  visibleToFamily: Number(handoff.visible_to_family),
  handoffs: Number(db.prepare('SELECT count(*) n FROM eva_interpretation_retry_handoffs').get().n),
  workItems: Number(db.prepare('SELECT count(*) n FROM eva_work_items').get().n),
  episodes: Number(db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n),
  origins: Number(db.prepare('SELECT count(*) n FROM eva_episode_origins').get().n),
  providerCalls, sends,
};
process.stdout.write(`NATIVE_INTERRUPTED_TELEGRAM=${JSON.stringify(result)}\n`);
db.close();
process.exit(0);
