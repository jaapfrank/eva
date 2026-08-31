import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-telegram-archive-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';

const {
  archiveTelegramMessage,
  claimTelegramMessage,
  completeTelegramMessage,
  db,
  markTelegramMessageDeleted,
  recentTelegramMessages,
  recoverInterruptedTelegramMessages,
  searchTelegramMessages,
  telegramMessage,
  upsertPerson,
} = await import('../src/db.js');
const { telegramPollingOptions } = await import('../src/integrations/telegram.js');
const { toolsForTurn, makeExecutor } = await import('../src/mind/tools.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('Telegram-envelop blijft idempotent en kan later met media worden verrijkt', () => {
  archiveTelegramMessage({
    chatId: 42,
    messageId: 100,
    ts: 1_785_000_000,
    sender: 'Jaap',
    role: 'user',
    kind: 'photo',
    content: 'Ken jij deze persoon?',
    replyToMessageId: 90,
    fileId: 'telegram-file',
    meta: { inbound: true },
  });
  archiveTelegramMessage({
    chatId: 42,
    messageId: 100,
    mediaRef: 'foto-ref-1',
    meta: { locallyStored: true },
  });

  const row = telegramMessage(42, 100);
  assert.equal(row.sender, 'Jaap');
  assert.equal(row.role, 'user');
  assert.equal(row.kind, 'photo');
  assert.equal(row.content, 'Ken jij deze persoon?');
  assert.equal(row.reply_to_message_id, '90');
  assert.equal(row.file_id, 'telegram-file');
  assert.equal(row.media_ref, 'foto-ref-1');
  assert.deepEqual(row.meta, { inbound: true, locallyStored: true });
  assert.equal(db.prepare('SELECT count(*) AS n FROM telegram_messages').get().n, 1);
});

test('teruglezen is per chat begrensd en verwijderde berichten blijven alleen als audit bestaan', () => {
  archiveTelegramMessage({
    chatId: 42,
    messageId: 101,
    sender: 'Eva',
    role: 'assistant',
    content: 'Ik heb de foto nog lokaal.',
  });
  archiveTelegramMessage({
    chatId: 99,
    messageId: 1,
    sender: 'Lola',
    role: 'user',
    content: 'Dit hoort bij een andere privéchat.',
  });

  assert.deepEqual(
    recentTelegramMessages({ chatId: 42, limit: 10 }).map((row) => row.message_id),
    ['100', '101'],
  );
  assert.deepEqual(
    recentTelegramMessages({ chatId: 42, beforeMessageId: 101, limit: 10 })
      .map((row) => row.message_id),
    ['100'],
  );
  assert.equal(searchTelegramMessages('foto', { chatId: 42 })[0].message_id, '101');

  assert.equal(markTelegramMessageDeleted(42, 101), true);
  assert.deepEqual(
    recentTelegramMessages({ chatId: 42, limit: 10 }).map((row) => row.message_id),
    ['100'],
  );
  assert.equal(telegramMessage(42, 101).deleted, true);
});

test('herstart gooit wachtende Telegram-updates niet meer weg', () => {
  assert.deepEqual(telegramPollingOptions(), { drop_pending_updates: false });
});

test('een al verwerkte Telegram-update wordt niet opnieuw uitgevoerd', () => {
  archiveTelegramMessage({
    chatId: 'dedupe-chat',
    messageId: '300',
    sender: 'Jaap',
    role: 'user',
    content: 'Zet het licht buiten aan.',
  });
  assert.deepEqual(claimTelegramMessage('dedupe-chat', '300'), {
    claimed: true,
    reason: 'claimed',
    attempts: 1,
  });
  assert.equal(claimTelegramMessage('dedupe-chat', '300').reason, 'processing');
  assert.equal(completeTelegramMessage('dedupe-chat', '300'), true);

  // Telegram kan na een procescrash hetzelfde envelope opnieuw aanbieden.
  // Opnieuw archiveren mag completed daarom niet terugzetten naar received.
  archiveTelegramMessage({
    chatId: 'dedupe-chat',
    messageId: '300',
    sender: 'Jaap',
    role: 'user',
    content: 'Zet het licht buiten aan.',
    processingStatus: 'received',
  });
  assert.equal(claimTelegramMessage('dedupe-chat', '300').reason, 'completed');
});

test('onderbroken verwerking wordt bij herstart opnieuw claimbaar', () => {
  archiveTelegramMessage({
    chatId: 'recovery-chat',
    messageId: '400',
    sender: 'Jaap',
    role: 'user',
    content: 'Zoek dit helemaal uit.',
  });
  assert.equal(claimTelegramMessage('recovery-chat', '400').claimed, true);
  assert.equal(recoverInterruptedTelegramMessages(), 1);
  assert.equal(claimTelegramMessage('recovery-chat', '400').claimed, true);
});

test('Eva kan haar lokale Telegram-gesprek als kernvaardigheid teruglezen', async () => {
  upsertPerson('Jaap', { telegram_id: 42 });
  archiveTelegramMessage({
    chatId: 42,
    messageId: 102,
    sender: 'Jaap',
    role: 'user',
    content: '/eva2 EVA2_FOTO_MAG_NIET_LEKKEN',
    meta: { dialogueRoute: 'eva2' },
  });
  const available = toolsForTurn({ mode: 'chat' });
  assert.equal(available.some((tool) => tool.name === 'zoek_gesprek'), true);

  const execute = makeExecutor({
    mode: 'chat',
    asker: 'Jaap',
    channel: 'telegram',
    sourceText: 'Wat zei ik eerder over die foto?',
    messageContext: { chatId: '42' },
  });
  const result = await execute('zoek_gesprek', {
    vraag: 'foto',
    limiet: 5,
  });
  assert.match(result, /Telegram|oude opslag/i);
  assert.match(result, /Ken jij deze persoon/i);
  assert.doesNotMatch(result, /EVA2_FOTO_MAG_NIET_LEKKEN/u);
});
