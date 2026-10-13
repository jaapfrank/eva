import assert from 'node:assert/strict';
import test from 'node:test';
import {
  archivedTelegramDialogueRoute, eva2IncomingTelegramPrompt, eva2TelegramPrompt,
} from '../../deploy/telegram-bridge/src/telegramdialogue.js';
import { askEva2Canary } from '../../deploy/telegram-bridge/src/integrations/eva2canary.js';

test('default route applies only to selected private text, with explicit commands retained', () => {
  const options = { chatId: 7, fromId: 7, defaultPrivateIds: '7,8' };
  options.defaultRoute = 'legacy';
  assert.equal(eva2IncomingTelegramPrompt(' Hello ', options), 'Hello');
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, chatId: -7 }), null);
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, fromId: 8 }), null);
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, defaultPrivateIds: '8' }), null);
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, defaultPrivateIds: '' }), null);
  assert.equal(eva2IncomingTelegramPrompt('/start', options), null);
  assert.equal(eva2IncomingTelegramPrompt('/eva2@EvaBot Hello', options), 'Hello');
  assert.equal(eva2IncomingTelegramPrompt('/eva2 Hello', { ...options, chatId: -7 }), 'Hello');
  assert.throws(() => eva2IncomingTelegramPrompt('Hello', { ...options, defaultPrivateIds: '7,bad' }), /invalid/);
});

test('new messages use Eva2; only the exact eva1 command selects the old conversation', () => {
  assert.equal(eva2IncomingTelegramPrompt('Hello'), 'Hello');
  assert.equal(eva2IncomingTelegramPrompt('Hello', { chatId: 8, fromId: 8 }), 'Hello');
  assert.equal(eva2IncomingTelegramPrompt('/eva1 Hello'), null);
  assert.equal(eva2IncomingTelegramPrompt('/eva1@EvaBot Hello'), null);
  assert.equal(eva2IncomingTelegramPrompt('/eva10 Hello'), '/eva10 Hello');
  assert.equal(eva2IncomingTelegramPrompt('/share -7'), '/share -7');
});

test('historical inference and archived route remain fixed across configuration changes', () => {
  const row = { role: 'user', content: 'Hello', meta: { dialogueRoute: 'eva1' } };
  const options = { chatId: 7, fromId: 7, defaultPrivateIds: '7', archivedMessage: row };
  assert.equal(eva2TelegramPrompt('Hello'), null);
  assert.equal(archivedTelegramDialogueRoute(row), 'eva1');
  assert.equal(eva2IncomingTelegramPrompt('Hello', options), null);
  const current = { ...row, meta: { dialogueRoute: 'eva2' } };
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, defaultPrivateIds: '', archivedMessage: current }), 'Hello');
  assert.equal(eva2IncomingTelegramPrompt('Hello', { ...options, archivedMessage: { role: 'user', content: 'Hello' } }), null);
  assert.throws(() => eva2IncomingTelegramPrompt('Changed text', options), /archived input/);
});

test('plain private input keeps original identity and text, never forwards Eva1 history', async () => {
  const calls = [];
  const response = await askEva2Canary({
    baseUrl: 'http://fixture', chatId: 7, fromId: 7, messageId: 12,
    text: 'Continue our topic', defaultPrivateIds: '7', telegramDate: 1787486400,
    recentMessages: [{ role: 'user', text: 'LEGACY_MUST_NOT_ENTER_CONTEXT' }],
    fetchImpl: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      return new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: 'telegram:7', message_id: 'telegram:7:12', text: 'Current reply' }));
    },
  });
  assert.equal(response.text, 'Current reply');
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body.recent_messages, []);
  assert.equal(calls[0].body.text, 'Continue our topic');
  assert.equal(calls[0].body.telegram_user_id, 7);
  assert.equal(calls[0].body.message_id, 12);
});

test('default input failure dispatches once and never turns into an Eva1 request', async () => {
  let calls = 0;
  await assert.rejects(askEva2Canary({
    baseUrl: 'http://fixture', chatId: 7, fromId: 7, messageId: 12,
    text: 'Continue our topic', defaultPrivateIds: '7', telegramDate: 1787486400,
    fetchImpl: async () => { calls++; return new Response('{}', { status: 503 }); },
  }), /503/);
  assert.equal(calls, 1);
});
