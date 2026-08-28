import assert from 'node:assert/strict';
import test from 'node:test';

import {
  askEva2Canary,
  eva2CanaryContinuation,
  eva2CanaryPrompt,
} from '../src/integrations/eva2canary.js';

test('alleen expliciete /eva2 berichten gaan naar de B-canary', () => {
  assert.equal(eva2CanaryPrompt('hoi Eva'), null);
  assert.equal(eva2CanaryPrompt('/eva2 Hoe gaat het?'), 'Hoe gaat het?');
  assert.equal(eva2CanaryPrompt('/eva2@EvaBot   Denk eens mee'), 'Denk eens mee');
});

test('een expliciete /eva2 beurt houdt korte vervolgen restartvast op B', () => {
  const priorMessages = [{
    content: '/eva2 Het is koud in de overkapping',
    meta: { fromTelegramId: '7' },
    role: 'user',
    ts: '2026-08-23T17:47:04.000Z',
  }];
  assert.equal(eva2CanaryContinuation({
    text: 'Doe maar', telegramDate: 1787507230, fromId: 7, priorMessages,
  }), true);
  assert.equal(eva2CanaryContinuation({
    text: 'Doe maar', telegramDate: 1787507230, fromId: 8, priorMessages,
  }), false);
  assert.equal(eva2CanaryContinuation({
    text: '/start', telegramDate: 1787507230, fromId: 7, priorMessages,
  }), false);
  assert.equal(eva2CanaryContinuation({
    text: 'Doe maar', telegramDate: 1787510830, fromId: 7, priorMessages,
  }), false);
});

test('een gewone vervolgzin wordt binnen een actieve sessie exact naar B gestuurd', async () => {
  let captured;
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674',
    chatId: 7,
    messageId: 9002,
    fromId: 7,
    text: 'Doe maar',
    continuation: true,
    telegramDate: 1787486410,
    fetchImpl: async (_url, options) => {
      captured = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          contract: 'EvaInternalGroundedReply/v1',
          conversation_id: 'telegram:7',
          message_id: 'telegram:7:9002',
          text: 'Ik pak de bedoelde verwarming uit ons gesprek.',
        }),
      };
    },
  });
  assert.equal(captured.text, 'Doe maar');
  assert.equal(result.text, 'Ik pak de bedoelde verwarming uit ons gesprek.');
});

test('B-canary bindt de echte Telegram-beurt en valideert het antwoord', async () => {
  let captured;
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674',
    chatId: 7,
    messageId: 9001,
    fromId: 7,
    text: '/eva2 Vertel eens eerlijk wat je denkt',
    telegramDate: 1787486400,
    recentMessages: [{
      occurred_at: '2026-08-23T12:00:00Z',
      role: 'Jaap',
      text: 'We hadden het over een koersvlag.',
    }],
    fetchImpl: async (url, options) => {
      captured = { url, body: JSON.parse(options.body) };
      return {
        ok: true,
        json: async () => ({
          contract: 'EvaInternalGroundedReply/v1',
          conversation_id: 'telegram:7',
          message_id: 'telegram:7:9001',
          text: 'Eerlijk? Ik denk dat je vooral duidelijkheid zoekt.',
        }),
      };
    },
  });

  assert.equal(captured.url, 'http://127.0.0.1:44674/internal/v1/turns/respond');
  assert.equal(captured.body.text, 'Vertel eens eerlijk wat je denkt');
  assert.equal(captured.body.conversation_id, 'telegram:7');
  assert.deepEqual(captured.body.audience, {
    audience_id: 'person:telegram:7',
    kind: 'private_person',
    principal_id: 'telegram:7',
  });
  assert.equal(captured.body.principal_id, 'telegram:7');
  assert.equal(captured.body.recent_messages[0].text, 'We hadden het over een koersvlag.');
  assert.equal(result.text, 'Eerlijk? Ik denk dat je vooral duidelijkheid zoekt.');
});

test('B-canary weigert groepen tot groepsconsent persoonsgebonden is', async () => {
  await assert.rejects(
    askEva2Canary({
      baseUrl: 'http://127.0.0.1:44674',
      chatId: -42,
      messageId: 9003,
      fromId: 7,
      text: '/eva2 Dit hoort bij onze groep',
      telegramDate: 1787486400,
      fetchImpl: async () => assert.fail('groep mag Eva2 niet bereiken'),
    }),
    /alleen een gekoppeld privégesprek/u,
  );
});

test('ieder privéaccount krijgt een eigen Eva2-principal', async () => {
  const principals = [];
  for (const fromId of [7, 8]) {
    await askEva2Canary({
      baseUrl: 'http://127.0.0.1:44674',
      chatId: fromId,
      messageId: 9100 + fromId,
      fromId,
      text: '/eva2 Hallo',
      telegramDate: 1787486400 + fromId,
      fetchImpl: async (_url, options) => {
        const body = JSON.parse(options.body);
        principals.push(body.principal_id);
        return {
          ok: true,
          json: async () => ({
            contract: 'EvaInternalGroundedReply/v1',
            conversation_id: `telegram:${fromId}`,
            message_id: `telegram:${fromId}:${9100 + fromId}`,
            text: 'Hoi!',
          }),
        };
      },
    });
  }
  assert.deepEqual(principals, ['telegram:7', 'telegram:8']);
});
