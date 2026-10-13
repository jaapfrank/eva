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
    if (['src/integrations/telegram.js', 'src/telegramdialogue.js',
      'src/integrations/eva2canary.js', 'src/integrations/eva2media.js'].includes(relative)) {
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
  source = source.replace('complete: chatComplete, imageMessage: imageMsg,',
    'complete: globalThis.__mediaComplete, imageMessage: imageMsg,');
  source = source.replace('      const turnAuthority = beginTelegramUnderstanding(ctx, naam);',
    "      if (ctx.message.text?.startsWith('/eva1')) { await sendTelegramText(ctx.chat.id, 'Eva1: legacy fixture', { replyToMessageId: ctx.message.message_id, dedupeKey: inboundReplyDedupeKey(ctx, 'legacy-fixture') }); return; }\n      const turnAuthority = beginTelegramUnderstanding(ctx, naam);");
  return { ...loaded, source };
} });


test('actual handler keeps text/photo with Eva2, explicit eva1 separate and no legacy fallback',
  { timeout: 15000 }, async () => {
    const sends = [], requests = [], mediaRequests = [];
    const make = (id, content) => ({ update_id: id, message: {
      message_id: id, date: 1787486400 + id,
      from: { id: 7, is_bot: false, first_name: 'Fixture' },
      chat: { id: 7, type: 'private' }, ...content,
    } });
    const updates = [
      make(1, { text: 'Hallo' }),
      make(2, { photo: [{ file_id: 'photo-2' }], caption: 'Tuya screenshot' }),
      make(3, { text: '/eva1 Hallo' }),
      make(4, { text: '/eva2 Hallo' }),
      make(5, { text: 'Core failure' }),
      make(6, { document: { file_id: 'document-6', mime_type: 'image/png' } }),
      make(7, { video: { file_id: 'video-7', mime_type: 'video/mp4' } }),
    ];
    bot.api.config.use(async (_previous, method, payload) => {
      if (method === 'getUpdates') { await delay(5); return { ok: true, result: [] }; }
      if (method === 'sendMessage') {
        sends.push(payload);
        return { ok: true, result: { message_id: 100 + sends.length,
          chat: { id: payload.chat_id, type: 'private' }, date: 1787486500,
          text: payload.text, reply_to_message: { message_id: payload.reply_parameters.message_id } } };
      }
      if (method === 'getFile') return { ok: true, result: { file_path: payload.file_id } };
      if (['deleteWebhook', 'sendChatAction', 'sendMessageDraft'].includes(method))
        return { ok: true, result: true };
      throw new Error('Unexpected API: ' + method);
    });
    globalThis.__mediaComplete = async request => {
      mediaRequests.push(request);
      assert.equal(request.model, 'gpt-6-astra');
      assert.equal(request.effort, 'medium');
      return { text: 'Visible Tuya notification.' };
    };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      if (String(url).startsWith('https://api.telegram.org/file/')) return new Response('fixture-image');
      const body = JSON.parse(options.body); requests.push(body);
      assert.deepEqual(body.recent_messages, []);
      if (body.message_id === 5) return new Response('{}', { status: 503 });
      return new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: 'telegram:7', message_id: 'telegram:7:' + body.message_id,
        text: 'Reply ' + body.message_id }));
    };
    const { startTelegram } = await import(moduleUrl);
    const { db, telegramMessage } = await import(pathToFileURL(path.join(hostRoot, 'src/db.js')).href);
    try {
      await startTelegram();
      for (const update of updates) await bot.handleUpdate(update);
      assert.deepEqual(requests.map(r => r.message_id), [1, 2, 4, 5, 6]);
      assert.equal(mediaRequests.length, 2);
      for (const id of [1, 2, 4, 6]) {
        assert.equal(sends.filter(s => s.text === 'Eva2: Reply ' + id).length, 1);
        assert.equal(telegramMessage(7, id).meta.dialogueRoute, 'eva2');
      }
      assert.equal(sends.filter(s => s.text === 'Eva1: legacy fixture').length, 1);
      assert.equal(telegramMessage(7, 3).meta.dialogueRoute, 'eva1');
      assert.match(sends.find(s => s.reply_parameters.message_id === 5).text, /^Eva2-fout:/);
      assert.match(sends.find(s => s.reply_parameters.message_id === 7).text, /^Eva2:/);
      assert.equal(requests.find(r => r.message_id === 2).text, 'Tuya screenshot');
      assert.equal(requests.find(r => r.message_id === 2).media_context.source_ref, 'telegram:7:2');
      assert.equal(requests.find(r => r.message_id === 6).media_context.media_type, 'image/png');
      for (const update of updates) await bot.handleUpdate(update);
      assert.equal(requests.length, 5);
      assert.equal(mediaRequests.length, 2);
      assert.equal(sends.length, 7);
    } finally {
      controller.abort(); await globalThis.__ingressPolling;
      globalThis.fetch = originalFetch; db.close();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
