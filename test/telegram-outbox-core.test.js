import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-outbox-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';

const { telegramOutboundByDedupeKey } = await import('../src/db.js');
const { __telegramTransportTest } = await import('../src/integrations/telegram.js');
const {
  preparedTelegramResult,
  persistPreparedTelegramReply,
  sendTelegramText,
  setBotForTest,
} = __telegramTransportTest;

test('een gedeeltelijk Telegram-antwoord hervat alleen ontbrekende tekstblokken', async () => {
  let sequence = 0;
  setBotForTest({
    api: {
      async sendMessage(chatId) {
        sequence += 1;
        if (sequence >= 2) throw new Error('tijdelijke Telegram-storing');
        return { chat: { id: chatId }, message_id: 100 + sequence, date: Date.now() / 1000 };
      },
    },
  });

  const text = `${'a'.repeat(3_800)}${'b'.repeat(3_800)}${'c'.repeat(300)}`;
  const options = { dedupeKey: 'turn:42', replyToMessageId: 7 };
  await assert.rejects(() => sendTelegramText('99', text, options));
  assert.ok(telegramOutboundByDedupeKey('turn:42:chunk:1'));
  assert.equal(telegramOutboundByDedupeKey('turn:42:chunk:2'), null);

  const retried = [];
  setBotForTest({
    api: {
      async sendMessage(chatId, content) {
        retried.push(content);
        return { chat: { id: chatId }, message_id: 200 + retried.length, date: Date.now() / 1000 };
      },
    },
  });
  const sent = await sendTelegramText('99', text, options);
  assert.equal(retried.length, 2);
  assert.equal(sent.length, 3);
  assert.equal(sent[0]._evaRecovered, true);
  assert.ok(telegramOutboundByDedupeKey('turn:42:chunk:3'));
});

test('een lang voorbereid antwoord blijft volledig en hervat zonder eerste deel opnieuw te sturen', async () => {
  const ctx = { chat: { id: '123' }, message: { message_id: '456' }, from: { first_name: 'Jaap' } };
  const text = `${'a'.repeat(4_000)}${'b'.repeat(4_000)}${'c'.repeat(4_000)}${'d'.repeat(4_000)}${'e'.repeat(4_000)}`;
  persistPreparedTelegramReply(ctx, 'Jaap', {
    turnId: 'lange-beurt',
    reply: text,
    conversationId: 'telegram:123',
    goalEpisodeId: 77,
    workItemId: 88,
    originMessageId: '456',
    tools: [],
  });
  const prepared = preparedTelegramResult(ctx);
  assert.equal(prepared.reply, text);
  assert.equal(prepared.goalEpisodeId, 77);
  assert.equal(prepared.workItemId, 88);
  assert.equal(prepared.originMessageId, '456');

  let sequence = 0;
  const delivered = [];
  setBotForTest({
    api: {
      async sendMessage(chatId, content) {
        sequence += 1;
        if (sequence >= 2) throw new Error('tijdelijke Telegram-storing');
        delivered.push(content);
        return { chat: { id: chatId }, message_id: 300 + sequence, date: Date.now() / 1000 };
      },
    },
  });
  const options = { dedupeKey: 'turn:lang', replyToMessageId: 456 };
  await assert.rejects(() => sendTelegramText('123', prepared.reply, options));

  const recovered = [];
  setBotForTest({
    api: {
      async sendMessage(chatId, content) {
        recovered.push(content);
        return { chat: { id: chatId }, message_id: 400 + recovered.length, date: Date.now() / 1000 };
      },
    },
  });
  await sendTelegramText('123', prepared.reply, options);
  assert.equal(recovered.length, 5);
  assert.equal([...delivered, ...recovered].join(''), text);
});
