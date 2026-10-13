import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-telegram-social-failure-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.NODE_ENV = 'test';
process.env.OPENAI_API_KEY = 'social-failure-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:social-failure-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.EVA_AUTONOMY_ENABLED = 'false';

await import('./telegram-test-composition.js');
const { db } = await import('../src/db.js');
const { directSocialReply } = await import('../src/mind/persona.js');
const telegram = await import('../src/integrations/telegram.js');
const { prepareAuthenticatedTelegramTurnForTest } = telegram.__telegramTransportTest;

const originalFetch = globalThis.fetch;
let providerCalls = 0;

after(() => {
  globalThis.fetch = originalFetch;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function telegramContext(messageId, text) {
  const chat = { id: 424242, type: 'private', first_name: 'Jaap' };
  const from = { id: 424242, is_bot: false, first_name: 'Jaap' };
  return {
    chat,
    from,
    message: {
      message_id: messageId,
      date: 1_786_500_000 + messageId,
      chat,
      from,
      text,
    },
  };
}

test('een begroeting wordt bij providerlimiet geen opdracht of spookwerk', async () => {
  assert.equal(directSocialReply('Hallo, zet straks de lamp uit.', { person: 'Jaap' }), null,
    'een begroeting met een opdracht mag nooit de sociale korte route nemen');
  globalThis.fetch = async () => {
    providerCalls += 1;
    return new Response(JSON.stringify({ error: { code: 'rate_limit_exceeded' } }), {
      status: 429,
      headers: { 'content-type': 'application/json' },
    });
  };
  const cases = [
    { messageId: 92001, text: 'hallo', reply: /^(?:hoi|hallo)\b/iu },
    { messageId: 92002, text: 'hallo eva', reply: /^(?:hoi|hallo)\b/iu },
    { messageId: 92003, text: 'eva?', reply: /^ja\b/iu },
  ];
  for (const scenario of cases) {
    const workBefore = db.prepare('SELECT count(*) AS n FROM eva_work_items').get().n;
    const episodesBefore = db.prepare('SELECT count(*) AS n FROM eva_goal_episodes').get().n;
    const result = await prepareAuthenticatedTelegramTurnForTest({
      ctx: telegramContext(scenario.messageId, scenario.text),
      sender: 'Jaap',
      text: scenario.text,
    });

    assert.equal(result.error, 'interpreter_unavailable');
    assert.notEqual(result.durableHandoff, true);
    assert.equal(db.prepare('SELECT count(*) AS n FROM eva_work_items').get().n, workBefore);
    assert.equal(db.prepare('SELECT count(*) AS n FROM eva_goal_episodes').get().n, episodesBefore);
    assert.match(result.reply, scenario.reply);
    assert.doesNotMatch(result.reply, /opdracht|bewaard|uitgevoerd|gewijzigd|provider|model/iu);
  }
  assert.equal(providerCalls, cases.length,
    'een simpele sociale beurt krijgt geen tweede modelpoging voor tekst');
});
