import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { registerHooks } from 'node:module';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { Bot } from 'grammy';

const dataDir = mkdtempSync(path.join(tmpdir(), 'eva-concurrent-ingress-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '123:test-only';
process.env.TELEGRAM_ALLOWED_IDS = '7,8';
process.env.EVA2_CANARY_URL = 'http://fixture.invalid';
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';
const controller = new AbortController();
globalThis.__ingressSignal = controller.signal;
const bot = new Bot('123:test-only', { botInfo: {
  id: 123, is_bot: true, first_name: 'Test', username: 'TestBot',
  can_join_groups: true, can_read_all_group_messages: false,
  supports_inline_queries: false,
} });
globalThis.__ingressBot = bot;
const moduleUrl = new URL('../src/integrations/telegram.js', import.meta.url).href;
registerHooks({ load(url, context, nextLoad) {
  const loaded = nextLoad(url, context);
  if (url !== moduleUrl) return loaded;
  let source = String(loaded.source);
  for (const [before, after] of [
    ['bot = new Bot(config.telegramToken);', 'bot = globalThis.__ingressBot;'],
    ['  startPreparedTelegramOutbox();', '  // Background outbox timer excluded in this isolated ingress test.'],
    ['pollTelegramConcurrently(bot, {', 'globalThis.__ingressPolling = pollTelegramConcurrently(bot, { signal: globalThis.__ingressSignal, pollIntervalMs: 1,'],
  ]) {
    assert.ok(source.includes(before), `Missing test composition seam: ${before}`);
    source = source.replace(before, after);
  }
  return { ...loaded, source };
} });

test('actual polling and archived /eva2 ingress deliver a later turn before the first finishes',
  { timeout: 10000 }, async () => {
    let releaseFirst;
    const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
    const bodies = [];
    const sends = [];
    const offsets = [];
    let firstReleased = false;
    let laterUpdatesArrived = false;
    const updates = [1, 2, 3, 4].map((id) => ({ update_id: id, message: {
      message_id: id, date: 1787486400 + id,
      from: { id: id === 3 ? 8 : id === 4 ? 99 : 7, is_bot: false, first_name: 'Fixture' },
      chat: { id: id === 3 ? 8 : id === 4 ? 99 : 7, type: 'private' },
      text: id === 1 ? '/eva2 Research this carefully' : '/eva2 What is happening?',
    } }));
    bot.api.config.use(async (_previous, method, payload) => {
      if (method === 'getUpdates') {
        await delay(1);
        offsets.push(payload.offset);
        return { ok: true, result: updates.filter((u) => u.update_id >= payload.offset && (laterUpdatesArrived || u.update_id === 1)) };
      }
      if (method === 'sendMessage') {
        sends.push(payload);
        return { ok: true, result: { message_id: 100 + sends.length,
          chat: { id: payload.chat_id, type: 'private' }, date: 1787486500, text: payload.text, reply_to_message: payload.reply_parameters ? { message_id: payload.reply_parameters.message_id } : undefined } };
      }
      if (['deleteWebhook', 'sendChatAction', 'sendMessageDraft'].includes(method)) {
        return { ok: true, result: true };
      }
      throw new Error(`Unexpected Telegram API method: ${method}`);
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      assert.match(url, /\/internal\/v1\/telegram\/canary\/respond$/u);
      const body = JSON.parse(options.body);
      bodies.push(body);
      assert.deepEqual(body.recent_messages, []);
      if (body.message_id === 1) await firstGate;
      return new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: `telegram:${body.chat_id}`,
        message_id: `telegram:${body.chat_id}:${body.message_id}`,
        text: `Fixture response ${body.message_id}`,
      }), { headers: { 'content-type': 'application/json' } });
    };
    const { startTelegram } = await import('../src/integrations/telegram.js');
    const { db, telegramMessage } = await import('../src/db.js');
    try {
      await startTelegram();
      const until = async (predicate) => {
        const deadline = Date.now() + 4000;
        while (!predicate()) {
          assert.ok(Date.now() < deadline, JSON.stringify({bodies, sends, offsets: offsets.slice(0, 5), timeline: db.prepare('SELECT * FROM timeline ORDER BY id DESC LIMIT 5').all()}));
          await delay(5);
        }
      };
      await until(() => bodies.length === 1);
      laterUpdatesArrived = true; // A later polling batch arrives while the first core call remains blocked.
      await until(() => sends.some((s) => s.text === 'Eva2: Fixture response 2')
        && sends.some((s) => s.text === 'Eva2: Fixture response 3'));
      assert.equal(firstReleased, false);
      assert.equal(sends.some((s) => s.text === 'Eva2: Fixture response 1'), false);
      assert.deepEqual(bodies.map((b) => [b.chat_id, b.telegram_user_id, b.message_id]),
        [['7', 7, 1], ['7', 7, 2], ['8', 8, 3]]);
      assert.equal(telegramMessage(7, 1).processing_status, 'processing');
      assert.equal(telegramMessage(7, 2).processing_status, 'completed');
      assert.ok(offsets.every((offset) => offset <= 1), 'unfinished turn must remain unacknowledged');
      await delay(20); // Multiple re-polls must not redispatch in-flight or completed updates.
      assert.equal(bodies.length, 3);
      firstReleased = true;
      releaseFirst();
      await until(() => offsets.some((offset) => offset === 5));
      assert.equal(telegramMessage(7, 1).processing_status, 'completed');
      await bot.handleUpdate(updates[0]);
      await bot.handleUpdate(updates[1]);
      assert.equal(bodies.length, 3, 'durable completed replay must not call core again');
      for (const id of [1, 2, 3]) {
        const delivered = sends.filter((s) => s.text === `Eva2: Fixture response ${id}`);
        assert.equal(delivered.length, 1);
        assert.equal(Number(delivered[0].chat_id), id === 3 ? 8 : 7);
        assert.equal(delivered[0].reply_parameters?.message_id, id);
      }
    } finally {
      releaseFirst();
      controller.abort();
      await globalThis.__ingressPolling;
      globalThis.fetch = originalFetch;
      db.close();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
