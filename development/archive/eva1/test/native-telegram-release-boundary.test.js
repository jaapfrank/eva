import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-native-telegram-release-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-release-test';
process.env.TELEGRAM_ALLOWED_IDS = '';

const { Bot } = await import('grammy');
const telegram = await import('../src/integrations/telegram.js');
const voiceBridge = await import('../src/mind/voicebridge.js');
const { db } = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('de production Telegram-module exporteert geen mutable transport-testcap', () => {
  assert.equal('__telegramTransportTest' in telegram, false);
  assert.equal('__telegramMediaTest' in telegram, false);
  assert.equal('__voiceBridgeTest' in voiceBridge, false);
});

test('een token zonder exact niet-lege principal-allowlist start geen Telegram-authoritymiddleware', async () => {
  const originalStart = Bot.prototype.start;
  const originalSetTimeout = globalThis.setTimeout;
  const originalSetInterval = globalThis.setInterval;
  let starts = 0;
  Bot.prototype.start = function forbiddenEmptyAllowlistStart() {
    starts += 1;
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
  assert.equal(starts, 0);
  assert.equal(db.prepare(`SELECT count(*) AS value FROM main.turn_tool_executions
    WHERE tool='@turn/understanding'`).get().value, 0);
});

test('video-preprocessing passeert archived authority vóór vision-providerwerk', () => {
  const source = fs.readFileSync(new URL('../src/integrations/telegram.js', import.meta.url), 'utf8');
  const start = source.indexOf("bot.on(['message:video', 'message:document']");
  const end = source.indexOf("bot.catch(", start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  const authorityAt = handler.indexOf('beginTelegramUnderstanding(ctx, naam)');
  const providerAt = handler.indexOf('describeVideoBuffer(');
  const canonicalInputAt = handler.indexOf('telegramVideoUnderstandingText(');
  const agentAt = handler.indexOf('handleMessageDetailed(');
  assert.ok(authorityAt >= 0 && authorityAt < providerAt
    && providerAt < canonicalInputAt && canonicalInputAt < agentAt);
  assert.match(handler, /caption: ctx\.message\.caption/u);
  assert.match(handler, /replyToMessageId:/u);
  assert.match(handler, /kind: telegramMessageKind\(ctx\.message\)/u);
  assert.match(handler, /fileId: video\.file_id/u);
  assert.match(handler, /fileUniqueId: video\.file_unique_id/u);
  const helperStart = source.indexOf('function telegramVideoUnderstandingText(');
  assert.ok(helperStart >= 0 && helperStart < start);
  const helper = source.slice(helperStart, source.indexOf('\n}\n', helperStart) + 3);
  assert.match(helper, /geen nieuwe tekst van/u);
  assert.match(helper, /TelegramDerivedVideoContext\/v1/u);
  assert.match(helper, /\.slice\(0, 16_000\)/u);
  assert.match(helper, /fileIdSha256: sha256/u);
  assert.match(helper, /fileUniqueIdSha256: sha256/u);
});
