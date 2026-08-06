import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

process.env.EVA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-vision-pending-'));
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';

const {
  latestPendingNameablePhoto,
  latestPendingNameablePhotoForTelegramChat,
  nameLastSeen,
  nameablePhotoContext,
  rememberNameablePhoto,
  updateNameablePhoto,
} = await import('../src/mind/vision.js');

test('een losse gezichtsnaam kiest nooit stilletjes uit twee open foto’s', () => {
  const first = rememberNameablePhoto({
    buffer: Buffer.from('eerste-foto'),
    pending: true,
    camera: 'Voordeur',
  });
  const second = rememberNameablePhoto({
    buffer: Buffer.from('tweede-foto'),
    pending: true,
    camera: 'Voordeur',
  });

  assert.equal(latestPendingNameablePhoto()?.ref, second);
  assert.equal(latestPendingNameablePhoto({ requireUnique: true }), null);

  updateNameablePhoto(second, { pending: false });
  assert.equal(latestPendingNameablePhoto({ requireUnique: true })?.ref, first);
});

test('een naamreply corrigeert de eerdere fotokandidaat in plaats van een los label te maken', async () => {
  const ref = rememberNameablePhoto({
    buffer: Buffer.from('testfoto-zonder-echt-gezicht'),
    caption: 'Jasmin is bij G5 tuin.\nCamera: G5 tuin',
    camera: 'G5 tuin',
    suggestedPerson: 'Jasmin',
  });
  const result = await nameLastSeen('Mark', { ref });
  assert.equal(result.ok, true);
  assert.equal(result.correctedFrom, 'Jasmin');
  assert.match(result.message, /koppeling met Jasmin.+vervangen/i);
  assert.equal(nameablePhotoContext(ref)?.namedPerson, 'Mark');
});

test('een open gezichtsvraag wordt per Telegram-chat opgelost', () => {
  const jaap = rememberNameablePhoto({
    buffer: Buffer.from('foto-voor-jaap'),
    pending: true,
    telegramMessages: [{ chatId: 'jaap-chat', messageId: '10' }],
  });
  const lola = rememberNameablePhoto({
    buffer: Buffer.from('foto-voor-lola'),
    pending: true,
    telegramMessages: [{ chatId: 'lola-chat', messageId: '10' }],
  });

  assert.equal(
    latestPendingNameablePhotoForTelegramChat({
      chatId: 'jaap-chat',
      requireUnique: true,
    })?.ref,
    jaap,
  );
  assert.equal(
    latestPendingNameablePhotoForTelegramChat({
      chatId: 'lola-chat',
      requireUnique: true,
    })?.ref,
    lola,
  );
});
