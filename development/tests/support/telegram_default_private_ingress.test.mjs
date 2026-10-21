import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { registerHooks, createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
const hostRoot = process.env.EVA_TELEGRAM_HOST_ROOT;
assert.ok(hostRoot, 'EVA_TELEGRAM_HOST_ROOT must name the existing host checkout');
const hostRequire = createRequire(path.join(hostRoot, 'package.json'));
const { Bot } = await import(pathToFileURL(hostRequire.resolve('grammy')).href);

const dataDir = mkdtempSync(path.join(tmpdir(), 'eva-concurrent-ingress-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '123:test-only';
process.env.TELEGRAM_ALLOWED_IDS = '7,8';
process.env.EVA2_CANARY_URL = 'http://fixture.invalid';
process.env.EVA2_TELEGRAM_DEFAULT_PRIVATE_IDS = '7';
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
const moduleUrl = pathToFileURL(path.join(hostRoot, 'src/integrations/telegram.js')).href;
registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === './eva2media.js' && process.env.EVA_TELEGRAM_CANDIDATE_ROOT) {
    return { url: pathToFileURL(path.join(process.env.EVA_TELEGRAM_CANDIDATE_ROOT,
      'src/integrations/eva2media.js')).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}, load(url, context, nextLoad) {
  const loaded = nextLoad(url, context);
  let source = String(loaded.source);
  if (process.env.EVA_TELEGRAM_CANDIDATE_ROOT && url.startsWith('file:')) {
    const relative = path.relative(hostRoot, fileURLToPath(url));
    if (['src/config.js', 'src/integrations/telegram.js',
      'src/telegramdialogue.js', 'src/integrations/eva2canary.js',
      'src/integrations/telegrampolling.js'].includes(relative)) {
      source = readFileSync(path.join(process.env.EVA_TELEGRAM_CANDIDATE_ROOT, relative), 'utf8');
    }
  }
  if (url !== moduleUrl) return { ...loaded, source };
  for (const [before, after] of [
    ['bot = new Bot(config.telegramToken);', 'bot = globalThis.__ingressBot;'],
    ['  startPreparedTelegramOutbox();', '  // Background outbox timer excluded in this isolated ingress test.'],
    ['pollTelegramConcurrently(bot, {', 'globalThis.__ingressPolling = pollTelegramConcurrently(bot, { signal: globalThis.__ingressSignal, pollIntervalMs: 750,'],
  ]) {
    assert.ok(source.includes(before), `Missing test composition seam: ${before}`);
    source = source.replace(before, after);
  }
  return { ...loaded, source };
} });

test('actual default private ingress preserves overlapping replies, historical routes and original input',
  { timeout: 15000 }, async () => {
    let releaseFirst;
    const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
    const bodies = [];
    const sends = [];
    const drafts = [];
    const offsets = [];
    let firstReleased = false;
    let laterUpdatesArrived = false;
    const updates = [1, 2, 3, 4, 5].map((id) => ({ update_id: id, message: {
      message_id: id, date: 1787486400 + id,
      from: { id: id === 3 ? 8 : id === 4 ? 99 : 7, is_bot: false, first_name: 'Fixture' },
      chat: { id: id === 3 ? 8 : id === 4 ? 99 : 7, type: 'private' },
      text: id === 1 ? 'Research this carefully' : id === 5 ? 'Negative final identity' : id === 3 || id === 4 ? '/eva2 How are you?' : 'How are you?',
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
      if (method === 'sendMessageDraft') {
        drafts.push(payload);
        return { ok: true, result: true };
      }
      if (['deleteWebhook', 'sendChatAction'].includes(method)) {
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
      assert.match(options.headers.Accept, /application\/x-ndjson/);
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        async start(output) {
          const send = item => output.enqueue(encoder.encode(JSON.stringify(item)+'\n'));
          if (body.message_id !== 5) send({contract:'EvaTextDelta/v1', text:`Fixture response ${body.message_id}`});
          if (body.message_id === 1) await firstGate;
          send({contract:'EvaInternalGroundedReply/v1',
            conversation_id:`telegram:${body.chat_id}`,
            message_id:`telegram:${body.chat_id}:${body.message_id === 5 ? 999 : body.message_id}`,
            text:body.message_id === 5 ? 'WRONG_FINAL_MUST_NOT_BE_DELIVERED' : `Fixture response ${body.message_id}`});
          output.close();
        },
      });
      return new Response(stream, {headers:{'content-type':'application/x-ndjson'}});

    };
    const { startTelegram } = await import(moduleUrl);
    const { db, telegramMessage, archiveTelegramMessage } = await import(pathToFileURL(path.join(hostRoot, 'src/db.js')).href);
    const oldUpdate = { update_id: 6, message: { message_id: 6, date: 1787486390,
      from: { id: 7, is_bot: false, first_name: 'Fixture' },
      chat: { id: 7, type: 'private' }, text: 'Historical Eva1 text' } };
    archiveTelegramMessage({ chatId: 7, messageId: 6, role: 'user',
      kind: 'text', content: oldUpdate.message.text, processingStatus: 'completed',
      meta: { inbound: true, fromTelegramId: '7', dialogueRoute: 'eva1' } });
    try {
      await startTelegram();
      const until = async (predicate) => {
        const deadline = Date.now() + 4000;
        while (!predicate()) {
          assert.ok(Date.now() < deadline, JSON.stringify({bodies, sends, offsets: offsets.slice(0, 5), timeline: db.prepare('SELECT * FROM timeline ORDER BY id DESC LIMIT 5').all()}));
          await delay(5);
        }
      };
      await until(() => bodies.length === 1 && drafts.some(d => d.draft_id === 1));
      laterUpdatesArrived = true; // A later polling batch arrives while the first core call remains blocked.
      await until(() => sends.some((s) => s.text === 'Fixture response 2')
        && sends.some((s) => s.text === 'Fixture response 3')
        && sends.some((s) => s.reply_parameters?.message_id === 5));
      assert.equal(firstReleased, false);
      assert.equal(sends.some((s) => s.text === 'Fixture response 1'), false);
      assert.deepEqual(bodies.map((b) => [b.chat_id, b.telegram_user_id, b.message_id]),
        [['7', 7, 1], ['7', 7, 2], ['8', 8, 3], ['7', 7, 5]]);
      assert.equal(telegramMessage(7, 1).processing_status, 'processing');
      assert.equal(telegramMessage(7, 2).processing_status, 'completed');
      assert.ok(offsets.every((offset) => offset <= 1), 'unfinished turn must remain unacknowledged');
      await delay(20); // Multiple re-polls must not redispatch in-flight or completed updates.
      assert.equal(bodies.length, 4);
      firstReleased = true;
      releaseFirst();
      await until(() => offsets.some((offset) => offset === 6));
      assert.equal(telegramMessage(7, 1).processing_status, 'completed');
      await bot.handleUpdate(updates[0]);
      await bot.handleUpdate(updates[1]);
      await bot.handleUpdate(updates[4]);
      assert.equal(bodies.length, 4, 'durable completed replay must not call core again');
      for (const id of [1, 2, 3]) {
        const delivered = sends.filter((s) => s.text === `Fixture response ${id}`);
        assert.equal(delivered.length, 1);
        assert.equal(Number(delivered[0].chat_id), id === 3 ? 8 : 7);
        assert.equal(delivered[0].reply_parameters?.message_id, id);
        const archived = telegramMessage(id === 3 ? 8 : 7, 100 + sends.indexOf(delivered[0]) + 1);
        assert.equal(Number(archived.reply_to_message_id), id);
        assert.equal(archived.content, delivered[0].text);
        const ownDrafts = drafts.filter(d => d.draft_id === id);
        assert.ok(ownDrafts.length > 0);
        assert.ok(ownDrafts.every(d => Number(d.chat_id) === (id === 3 ? 8 : 7)
          && d.text === `Fixture response ${id}`));
      }
      assert.ok(sends.findIndex(s => s.reply_parameters?.message_id === 2)
        < sends.findIndex(s => s.reply_parameters?.message_id === 1));
      const failures = sends.filter(s => s.reply_parameters?.message_id === 5);
      assert.equal(failures.length, 1);
      assert.match(failures[0].text, /^Je bericht is niet afgehandeld:/);
      assert.ok(sends.every(s => !s.text.includes('WRONG_FINAL_MUST_NOT_BE_DELIVERED')));
      assert.ok(drafts.every(d => !d.text.includes('WRONG_FINAL_MUST_NOT_BE_DELIVERED')));
      assert.equal(telegramMessage(7, 5).processing_status, 'completed');
      for (const id of [1, 2, 3, 5]) {
        const input = telegramMessage(id === 3 ? 8 : 7, id);
        assert.equal(input.meta.dialogueRoute, 'eva2');
        assert.equal(input.content, updates[id - 1].message.text);
      }
      await bot.handleUpdate(oldUpdate);
      assert.equal(telegramMessage(7, 6).meta.dialogueRoute, 'eva1');
      assert.equal(telegramMessage(7, 6).content, oldUpdate.message.text);
      assert.equal(bodies.length, 4);
      assert.equal(bodies[0].text, 'Research this carefully');
      assert.equal(bodies[1].text, 'How are you?');
    } finally {
      releaseFirst();
      controller.abort();
      await globalThis.__ingressPolling;
      globalThis.fetch = originalFetch;
      db.close();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
