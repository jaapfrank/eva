import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const [phase, dataDir, mutation = ''] = process.argv.slice(2);
if (!['prepare-crash', 'recover', 'recover-mutation'].includes(phase) || !dataDir) {
  throw new Error('native Telegram done-restartfixture mist fase of datamap');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-telegram-restart-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-telegram-restart-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';

const { Bot } = await import('grammy');
const telegram = await import('../src/integrations/telegram.js');
const { canonicalJson } = await import('../src/util.js');
const { db, telegramMessage } = await import('../src/db.js');
const { turnUnderstandingAuthorityRecord } = await import('../src/turnauthority.js');

const chatId = 92001;
const messageId = 8101;
const text = 'Geef na de onderbreking exact hetzelfde korte antwoord.';
const reply = 'Dit antwoord wordt na de herstart precies eenmaal bezorgd.';
const baseUpdate = {
  update_id: 81,
  message: {
    message_id: messageId, date: 1_786_030_100, message_thread_id: 91,
    chat: { id: chatId, type: 'private', first_name: 'Jaap' },
    from: { id: 424242, is_bot: false, first_name: 'Jaap', language_code: 'nl' },
    text,
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
    type: 'function_call', call_id: 'native-telegram-done-restart-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Een exact antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }]);
}

function replyResponse() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: reply }],
  }]);
}

let nativeBot = null;
let sends = 0;
const originalStart = Bot.prototype.start;
const originalSetTimeout = globalThis.setTimeout;
const originalSetInterval = globalThis.setInterval;
Bot.prototype.start = function startWithoutPolling() {
  nativeBot = this;
  this.botInfo = {
    id: 123456789, is_bot: true, first_name: 'Eva Test', username: 'eva_test_bot',
    can_join_groups: true, can_read_all_group_messages: false,
    supports_inline_queries: false,
  };
  this.api.config.use(async (_previous, method, payload) => {
    if (method === 'sendChatAction') return { ok: true, result: true };
    if (method === 'sendMessage') {
      sends += 1;
      return { ok: true, result: {
        message_id: 91_000 + sends, date: 1_786_030_200,
        chat: { id: Number(payload.chat_id), type: 'private', first_name: 'Jaap' },
        from: this.botInfo, text: String(payload.text || ''),
        message_thread_id: payload.message_thread_id,
        reply_to_message: { message_id: payload.reply_parameters?.message_id },
      } };
    }
    throw new Error(`onverwachte Telegram-API-call: ${method}`);
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

const executionRow = () => db.prepare(`SELECT * FROM main.turn_tool_executions
  WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(
  String(messageId),
);
const report = () => {
  const archived = telegramMessage(chatId, messageId);
  const execution = executionRow();
  const generations = execution ? db.prepare(`SELECT source_generation FROM
    main.eva_turn_understanding_authorities_v1 WHERE execution_key=?
    ORDER BY source_generation`).all(execution.execution_key)
    .map((row) => Number(row.source_generation)) : [];
  return {
    phase, mutation: mutation || null,
    status: archived?.processing_status || null,
    attempts: Number(archived?.processing_attempts || 0),
    prepared: Boolean(archived?.meta?.preparedReply),
    executionStatus: execution?.status || null,
    generations,
    sends,
    receipts: Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`)
      .get(String(messageId)).value),
  };
};

if (phase === 'prepare-crash') {
  db.exec(`CREATE TEMP TRIGGER abort_native_telegram_prepared_sidecar
    BEFORE UPDATE OF meta ON main.telegram_messages
    WHEN OLD.chat_id='${chatId}' AND OLD.message_id='${messageId}'
      AND json_extract(OLD.meta,'$.preparedReply') IS NULL
      AND json_type(NEW.meta,'$.preparedReply')='object'
    BEGIN SELECT RAISE(ABORT,'forced crash before prepared Telegram sidecar'); END`);
  let providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    return providerCalls === 1 ? understandingResponse() : replyResponse();
  };
  await assert.rejects(
    nativeBot.handleUpdate(baseUpdate), /forced crash before prepared Telegram sidecar/u,
  );
  const observed = report();
  observed.providerCalls = providerCalls;
  assert.ok(providerCalls >= 1 && providerCalls <= 2);
  assert.equal(observed.executionStatus, 'done');
  assert.deepEqual(observed.generations, [1]);
  assert.equal(observed.attempts, 1);
  assert.equal(observed.prepared, false);
  assert.equal(observed.sends, 0);
  assert.equal(observed.receipts, 0);
  process.stdout.write(`NATIVE_TELEGRAM_DONE_RESTART=${JSON.stringify(observed)}\n`);
  db.close();
  process.exit(0);
}

let update = structuredClone(baseUpdate);
let baselineProtected = false;
let mutationAttested = false;
if (phase === 'recover-mutation') {
  const execution = executionRow();
  const authority = turnUnderstandingAuthorityRecord(db, execution);
  assert.ok(execution && authority);
  switch (mutation) {
    case 'archive':
      update.message.message_thread_id += 1;
      mutationAttested = update.message.message_thread_id !== baseUpdate.message.message_thread_id;
      break;
    case 'requester': {
      const args = JSON.parse(execution.args);
      args.requester = 'Andere naam';
      assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET args=?
        WHERE execution_key=?`).run(canonicalJson(args), execution.execution_key),
      /intent is immutable/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_intent_immutable'
          AND tbl_name='turn_tool_executions'`).get());
      db.exec('DROP TRIGGER main.turn_tool_executions_intent_immutable');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_intent_immutable'`).get(), undefined);
      db.prepare(`UPDATE main.turn_tool_executions SET args=? WHERE execution_key=?`)
        .run(canonicalJson(args), execution.execution_key);
      mutationAttested = db.prepare(`SELECT json_extract(args,'$.requester') AS value
        FROM main.turn_tool_executions WHERE execution_key=?`)
        .get(execution.execution_key).value === 'Andere naam';
      break;
    }
    case 'text':
      update.message.text = `${text} gewijzigd`;
      mutationAttested = update.message.text !== baseUpdate.message.text;
      break;
    case 'source': {
      assert.throws(() => db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
        SET source_sha256=? WHERE execution_key=? AND source_generation=1`)
        .run('0'.repeat(64), execution.execution_key), /append-only/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='eva_turn_understanding_authorities_v1_no_update'
          AND tbl_name='eva_turn_understanding_authorities_v1'`).get());
      db.exec('DROP TRIGGER main.eva_turn_understanding_authorities_v1_no_update');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='eva_turn_understanding_authorities_v1_no_update'`).get(),
      undefined);
      db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
        SET source_sha256=? WHERE execution_key=? AND source_generation=1`)
        .run('0'.repeat(64), execution.execution_key);
      mutationAttested = db.prepare(`SELECT source_sha256 AS value FROM
        main.eva_turn_understanding_authorities_v1
        WHERE execution_key=? AND source_generation=1`).get(execution.execution_key).value
        === '0'.repeat(64);
      break;
    }
    case 'authority': {
      assert.throws(() => db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
        SET authority_sha256=? WHERE execution_key=? AND source_generation=1`)
        .run('0'.repeat(64), execution.execution_key), /append-only/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='eva_turn_understanding_authorities_v1_no_update'
          AND tbl_name='eva_turn_understanding_authorities_v1'`).get());
      db.exec('DROP TRIGGER main.eva_turn_understanding_authorities_v1_no_update');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='eva_turn_understanding_authorities_v1_no_update'`).get(),
      undefined);
      db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
        SET authority_sha256=? WHERE execution_key=? AND source_generation=1`)
        .run('0'.repeat(64), execution.execution_key);
      mutationAttested = db.prepare(`SELECT authority_sha256 AS value FROM
        main.eva_turn_understanding_authorities_v1
        WHERE execution_key=? AND source_generation=1`).get(execution.execution_key).value
        === '0'.repeat(64);
      break;
    }
    case 'output': {
      assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET output_sha256=?
        WHERE execution_key=?`).run('0'.repeat(64), execution.execution_key),
      /terminal record is immutable/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'
          AND tbl_name='turn_tool_executions'`).get());
      db.exec('DROP TRIGGER main.turn_tool_executions_terminal_immutable');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'`).get(), undefined);
      db.prepare(`UPDATE main.turn_tool_executions SET output_sha256=?
        WHERE execution_key=?`).run('0'.repeat(64), execution.execution_key);
      mutationAttested = db.prepare(`SELECT output_sha256 AS value
        FROM main.turn_tool_executions WHERE execution_key=?`)
        .get(execution.execution_key).value === '0'.repeat(64);
      break;
    }
    case 'turn': {
      const output = JSON.parse(execution.output);
      output.turnId = `${output.turnId}:changed`;
      assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET output=?
        WHERE execution_key=?`).run(canonicalJson(output), execution.execution_key),
      /terminal record is immutable/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'
          AND tbl_name='turn_tool_executions'`).get());
      db.exec('DROP TRIGGER main.turn_tool_executions_terminal_immutable');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'`).get(), undefined);
      db.prepare(`UPDATE main.turn_tool_executions SET output=? WHERE execution_key=?`)
        .run(canonicalJson(output), execution.execution_key);
      mutationAttested = JSON.parse(db.prepare(`SELECT output FROM main.turn_tool_executions
        WHERE execution_key=?`).get(execution.execution_key).output).turnId === output.turnId;
      break;
    }
    case 'finalBinding': {
      const output = JSON.parse(execution.output);
      output.finalDeliveryBindingSha256 = '0'.repeat(64);
      assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET output=?
        WHERE execution_key=?`).run(canonicalJson(output), execution.execution_key),
      /terminal record is immutable/u);
      baselineProtected = true;
      assert.ok(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'
          AND tbl_name='turn_tool_executions'`).get());
      db.exec('DROP TRIGGER main.turn_tool_executions_terminal_immutable');
      assert.equal(db.prepare(`SELECT 1 FROM main.sqlite_master
        WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'`).get(), undefined);
      db.prepare(`UPDATE main.turn_tool_executions SET output=? WHERE execution_key=?`)
        .run(canonicalJson(output), execution.execution_key);
      mutationAttested = JSON.parse(db.prepare(`SELECT output FROM main.turn_tool_executions
        WHERE execution_key=?`).get(execution.execution_key).output)
        .finalDeliveryBindingSha256 === '0'.repeat(64);
      break;
    }
    default: throw new Error(`onbekende Telegram done-restartmutatie: ${mutation}`);
  }
  assert.equal(mutationAttested, true, `mutatie ${mutation} staat niet exact in de fixture`);
}

let providerCalls = 0;
globalThis.fetch = async () => {
  providerCalls += 1;
  throw new Error('Telegram done-restart mag provider niet bereiken');
};
let rejected = null;
try {
  await nativeBot.handleUpdate(update);
} catch (error) {
  rejected = String(error?.message || error);
}
if (phase === 'recover') {
  await nativeBot.handleUpdate(baseUpdate);
}
const observed = report();
observed.providerCalls = providerCalls;
observed.rejected = rejected;
if (phase === 'recover-mutation') {
  observed.baselineProtected = baselineProtected;
  observed.mutationAttested = mutationAttested;
}
process.stdout.write(`NATIVE_TELEGRAM_DONE_RESTART=${JSON.stringify(observed)}\n`);
db.close();
