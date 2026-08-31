import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-dialogue-route-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.TELEGRAM_ALLOWED_IDS = '42';
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';
await import('./telegram-test-composition.js');
await import('./agent-test-composition.js');

const {
  archiveTelegramMessage,
  db,
  recentTelegramMessages,
  searchTelegramConversationOpportunity,
  searchTelegramMessages,
  telegramConversationSearchSpec,
  telegramMessage,
} = await import('../src/db.js');
const {
  archivedTelegramDialogueRoute,
  eva2TelegramPrompt,
} = await import('../src/telegramdialogue.js');
const { __telegramTransportTest } = await import('../src/integrations/telegram.js');
const { __projectRouteTest } = await import('../src/mind/agent.js');

after(() => {
  __telegramTransportTest.setBotForTest(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('dezelfde strikte commandogrens bepaalt routering en historische provenance', () => {
  assert.equal(eva2TelegramPrompt('/eva2 test'), 'test');
  assert.equal(eva2TelegramPrompt('/eva2@EvaBot test'), 'test');
  assert.equal(eva2TelegramPrompt('/eva20 test'), null);
  assert.equal(eva2TelegramPrompt('vervolg zonder commando'), null);

  assert.equal(archivedTelegramDialogueRoute({
    role: 'user', content: '/eva2 oud bericht', meta: {},
  }), 'eva2');
  assert.equal(archivedTelegramDialogueRoute({
    role: 'assistant', content: 'oude fout',
    meta: { deliveryKey: 'eva2-canary-error:turn:1' },
  }), 'eva2');
  assert.equal(archivedTelegramDialogueRoute({
    role: 'assistant', content: 'Eva2: zichtbaar antwoord', meta: {},
  }), 'eva2');
  assert.equal(archivedTelegramDialogueRoute({
    role: 'user', content: 'gewoon Eva1-bericht', meta: {},
  }), 'eva1');
});

test('de echte Telegram-envelop krijgt vóór verwerking een duurzame dialoogroute', () => {
  const base = {
    chat: { id: 42 },
    from: { id: 42, first_name: 'Jaap' },
  };
  const eva2 = __telegramTransportTest.archiveTelegramEnvelope({
    ...base,
    message: { message_id: 100, date: 1_786_000_000, text: '/eva2 twee' },
  });
  const eva1 = __telegramTransportTest.archiveTelegramEnvelope({
    ...base,
    message: { message_id: 101, date: 1_786_000_001, text: 'één' },
  });
  assert.equal(eva2.meta.dialogueRoute, 'eva2');
  assert.equal(eva1.meta.dialogueRoute, 'eva1');
});

test('Eva2-uitvoer blijft ook na duurzaam transport als Eva2 gemarkeerd', async () => {
  __telegramTransportTest.setBotForTest({
    api: {
      sendMessage: async (chatId, text, options) => ({
        chat: { id: chatId }, message_id: 102, date: 1_786_000_002, text,
        ...(options?.reply_parameters?.message_id ? {
          reply_to_message: { message_id: options.reply_parameters.message_id },
        } : {}),
      }),
    },
  });
  await __telegramTransportTest.sendTelegramText('42', 'Eva2: twee', {
    dedupeKey: 'eva2-canary:test-route',
  });
  assert.equal(telegramMessage('42', '102').meta.dialogueRoute, 'eva2');
});

test('Eva1-context en beide gesprekzoekroutes sluiten nieuwe én oude Eva2-regels uit', () => {
  archiveTelegramMessage({
    chatId: 42, messageId: 103, sender: 'Jaap', role: 'user', kind: 'text',
    content: '/eva2 LEGACY_EVA2_GEHEIM',
  });
  archiveTelegramMessage({
    chatId: 42, messageId: 104, sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'oude onbenoemde uitvoer LEGACY_EVA2_ANTWOORD',
    meta: { deliveryKey: 'eva2-canary:legacy' },
  });
  archiveTelegramMessage({
    chatId: 42, messageId: 105, sender: 'Jaap', role: 'user', kind: 'text',
    content: 'EVA1_ZICHTBAAR gesprek', meta: { dialogueRoute: 'eva1' },
  });
  archiveTelegramMessage({
    chatId: 42, messageId: 106, sender: 'Jaap', role: 'user', kind: 'text',
    content: 'OUD_GEWOON_VERVOLG_DAT_EVA2_KREEG',
  });
  archiveTelegramMessage({
    chatId: 42, messageId: 107, sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'oude Eva2-fout zonder zichtbaar label',
    meta: { deliveryKey: 'eva2-canary-error:turn:telegram:42:106' },
  });

  const recent = recentTelegramMessages({ chatId: 42, limit: 20, dialogueRoute: 'eva1' });
  assert.equal(recent.some((row) => row.content.includes('EVA2')), false);
  assert.equal(recent.some((row) => row.content.includes('OUD_GEWOON_VERVOLG')), false);
  assert.equal(recent.some((row) => row.content.includes('EVA1_ZICHTBAAR')), true);

  const modelContext = __projectRouteTest.telegramConversationRows({
    chatId: '42', messageId: '105',
  }, 20);
  assert.equal(modelContext.some((row) => row.content.includes('EVA2')), false);
  assert.equal(modelContext.some((row) => row.content.includes('EVA1_ZICHTBAAR')), true);

  const lexical = searchTelegramMessages('gesprek geheim antwoord', {
    chatId: 42, limit: 20, dialogueRoute: 'eva1',
  });
  assert.equal(lexical.some((row) => row.content.includes('EVA2')), false);
  assert.equal(lexical.some((row) => row.content.includes('EVA1_ZICHTBAAR')), true);

  const spec = telegramConversationSearchSpec('gesprek geheim antwoord', {
    chatId: 42, limit: 6,
  });
  const opportunity = searchTelegramConversationOpportunity(spec, {
    now: Date.parse('2026-08-31T00:00:00Z'),
  });
  assert.equal(opportunity.messages.some((row) => row.content.includes('EVA2')), false);
});
