import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = mkdtempSync(path.join(tmpdir(), 'bottie-telegram-transport-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.EVA_ATTENTION_BATCH_MS = '10';

const {
  archiveTelegramMessage,
  db,
  newerTelegramUserMessage,
  telegramOutboundByDedupeKey,
} = await import('../src/db.js');
const {
  __telegramMediaTest,
  __telegramTransportTest,
  telegramPollingOptions,
} = await import('../src/integrations/telegram.js');
const {
  directTurnIdentity,
  resolveTelegramTargets,
  telegramTextSendOptions,
} = __telegramTransportTest;

after(() => {
  db.close();
  rmSync(dataDir, { recursive: true, force: true });
});

const people = [
  { name: 'Jaap', telegram_id: 12345 },
  { name: 'J Frank', telegram_id: '12345' },
  { name: 'Lola', telegram_id: '67890' },
  { name: 'Sanne', telegram_id: 24680 },
  { name: 'Niet bereikbaar', telegram_id: null },
];

test('onbekende niet-lege ontvanger faalt gesloten, ook met broadcast-optie', () => {
  for (const options of [{}, { broadcast: true }]) {
    const result = resolveTelegramTargets(people, 'Japp', options);
    assert.equal(result.ok, false);
    assert.equal(result.code, 'TELEGRAM_RECIPIENT_UNKNOWN');
    assert.deepEqual(result.targets, []);
    assert.match(result.info, /niets verstuurd/i);
  }
});

test('ontbrekende ontvanger vereist expliciete gezinsbroadcast', () => {
  const missing = resolveTelegramTargets(people, null);
  assert.equal(missing.ok, false);
  assert.equal(missing.code, 'TELEGRAM_RECIPIENT_REQUIRED');

  const optionBroadcast = resolveTelegramTargets(people, null, { broadcast: true });
  assert.equal(optionBroadcast.ok, true);
  assert.equal(optionBroadcast.broadcast, true);

  const namedBroadcast = resolveTelegramTargets(people, 'het gezin');
  assert.equal(namedBroadcast.ok, true);
  assert.equal(namedBroadcast.broadcast, true);
});

test('numerieke en string-id van dezelfde persoon worden gededupliceerd', () => {
  const broadcast = resolveTelegramTargets(people, 'gezin');
  assert.equal(broadcast.ok, true);
  assert.deepEqual(
    broadcast.targets.map((person) => String(person.telegram_id)).sort(),
    ['12345', '24680', '67890'],
  );

  const alias = resolveTelegramTargets(people, 'J Frank');
  assert.equal(alias.ok, true);
  assert.equal(alias.targets.length, 1);
  assert.equal(String(alias.targets[0].telegram_id), '12345');

  const byId = resolveTelegramTargets(people, 12345);
  assert.equal(byId.ok, true);
  assert.equal(byId.targets.length, 1);
});

test('een onvolledige naam die meerdere chat-ids raakt is getypeerd ambigu', () => {
  const result = resolveTelegramTargets([
    { name: 'Sanne', telegram_id: 1 },
    { name: 'Sandra', telegram_id: 2 },
  ], 'san');
  assert.equal(result.ok, false);
  assert.equal(result.code, 'TELEGRAM_RECIPIENT_AMBIGUOUS');
  assert.deepEqual(result.targets, []);
});

test('direct antwoord krijgt stabiele turn-, signal- en dedupe-identiteit', () => {
  const identity = directTurnIdentity({
    chat: { id: 9001 },
    message: { message_id: 77 },
  });
  assert.deepEqual(identity, {
    chatId: '9001',
    messageId: '77',
    signalRef: 'turn:telegram:9001:77',
    dedupeKey: 'user-reply:turn:telegram:9001:77',
  });
});

test('reply-threading wordt alleen op het eerste Telegram-fragment gezet', () => {
  assert.deepEqual(telegramTextSendOptions({
    replyToMessageId: '77',
    messageThreadId: 12,
  }, 0), {
    message_thread_id: 12,
    reply_parameters: {
      message_id: 77,
      allow_sending_without_reply: true,
    },
  });
  assert.deepEqual(telegramTextSendOptions({
    replyToMessageId: '77',
    messageThreadId: 12,
  }, 1), {
    message_thread_id: 12,
  });
});

test('verwijzingen naar mijn of jouw laatste foto kiezen de juiste afzender', () => {
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Laatste foto die je liet zien'),
    'assistant',
  );
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Wat stond er op de foto die ik stuurde?'),
    'user',
  );
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Wat zie je op de laatste foto?'),
    null,
  );
});

test('een expliciete reply-keten blijft aan exact dezelfde foto gekoppeld', () => {
  const chatId = 'reply-chain';
  archiveTelegramMessage({
    chatId, messageId: '100', ts: '2026-08-01T10:00:00Z',
    sender: 'Eva', role: 'assistant', kind: 'photo', content: 'Wie is dit?', mediaRef: 'foto-100',
  });
  archiveTelegramMessage({
    chatId, messageId: '101', ts: '2026-08-01T10:00:10Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Dat is Lola', replyToMessageId: '100',
  });
  archiveTelegramMessage({
    chatId, messageId: '102', ts: '2026-08-01T10:00:20Z',
    sender: 'Eva', role: 'assistant', kind: 'text', content: 'Dank je, dat is Lola.', replyToMessageId: '101',
  });
  archiveTelegramMessage({
    chatId, messageId: '103', ts: '2026-08-01T10:00:25Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Nee Jaap.', replyToMessageId: '102',
  });

  assert.equal(__telegramMediaTest.telegramReplyChainPhoto(chatId, '102')?.message_id, '100');
  assert.equal(
    __telegramMediaTest.inheritedPhotoForShortFollowup({
      chat: { id: chatId },
      message: { message_id: '103', text: 'Nee Jaap.' },
    })?.message_id,
    '100',
  );
});

test('korte bevestiging pakt de direct voorafgaande foto en een oudere beurt wordt superseded', () => {
  const chatId = 'short-confirmation';
  archiveTelegramMessage({
    chatId, messageId: '200', ts: '2026-08-01T11:00:00Z',
    sender: 'Eva', role: 'assistant', kind: 'photo', content: 'Jaap is bij de voordeur.', mediaRef: 'foto-200',
  });
  archiveTelegramMessage({
    chatId, messageId: '201', ts: '2026-08-01T11:00:05Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Dat klopt',
  });
  archiveTelegramMessage({
    chatId, messageId: '202', ts: '2026-08-01T11:00:06Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'En dit is een nieuwe vraag',
  });

  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Dat klopt'), true);
  assert.equal(
    __telegramMediaTest.inheritedPhotoForShortFollowup({
      chat: { id: chatId },
      message: { message_id: '201', text: 'Dat klopt' },
    })?.message_id,
    '200',
  );
  assert.equal(newerTelegramUserMessage(chatId, '201')?.message_id, '202');
});

test('herstart bewaart Telegram-updates die tijdens downtime zijn binnengekomen', () => {
  assert.deepEqual(telegramPollingOptions(), {
    drop_pending_updates: false,
  });
});

test('een later gearchiveerd oud reply-doel geldt niet als nieuwer Telegram-bericht', () => {
  const chatId = 'out-of-order-reply';
  archiveTelegramMessage({
    chatId, messageId: '300', sender: 'Jaap', role: 'user', kind: 'text', content: 'Actuele vraag',
  });
  archiveTelegramMessage({
    chatId, messageId: '250', sender: 'Jaap', role: 'user', kind: 'text', content: 'Oud reply-doel',
  });
  assert.equal(newerTelegramUserMessage(chatId, '300'), null);
});

test('Telegram-deduplicatie blijft binnen de bedoelde chat', () => {
  archiveTelegramMessage({
    chatId: 'chat-a', messageId: '10', sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'voor A', meta: { dedupeKey: 'zelfde-aflevering' },
  });
  archiveTelegramMessage({
    chatId: 'chat-b', messageId: '11', sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'voor B', meta: { dedupeKey: 'zelfde-aflevering' },
  });
  assert.equal(telegramOutboundByDedupeKey('zelfde-aflevering', 'chat-a')?.content, 'voor A');
  assert.equal(telegramOutboundByDedupeKey('zelfde-aflevering', 'chat-b')?.content, 'voor B');
});
