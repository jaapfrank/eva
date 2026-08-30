import assert from 'node:assert/strict';
import test from 'node:test';

import {
  askEva2Canary,
  createEva2DraftSink,
  eva2CanaryContinuation,
  eva2CanaryPrompt,
} from '../src/integrations/eva2canary.js';

test('Telegramconcept gebruikt één draft-id, begrenst updates en faalt zacht', async () => {
  let now = 1_000;
  const calls = [];
  const sink = createEva2DraftSink({
    chatId: 7, draftId: 9201, clock: () => now,
    sendDraft: async (payload) => { calls.push(payload); return true; },
  });
  assert.ok(sink);
  assert.equal(await sink('Hoi'), true);
  now += 100;
  assert.equal(await sink('Hoi Jaap'), false);
  now += 250;
  assert.equal(await sink('Hoi Jaap, leuk je te zien.'), true);
  assert.deepEqual(calls.map((row) => row.draft_id), [9201, 9201]);
  assert.equal(createEva2DraftSink({ chatId: -7, draftId: 1, sendDraft() {} }), null);
});

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

  assert.equal(
    captured.url,
    'http://127.0.0.1:44674/internal/v1/telegram/canary/respond',
  );
  assert.equal(captured.body.text, 'Vertel eens eerlijk wat je denkt');
  assert.equal(captured.body.chat_id, '7');
  assert.equal(captured.body.telegram_user_id, 7);
  assert.equal(captured.body.principal_id, undefined);
  assert.equal(captured.body.audience, undefined);
  assert.equal(captured.body.recent_messages[0].text, 'We hadden het over een koersvlag.');
  assert.equal(result.text, 'Eerlijk? Ik denk dat je vooral duidelijkheid zoekt.');
});

test('B-canary herhaalt één identieke duurzame beurt na een tijdelijke 503', async () => {
  const bodies = [];
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674', chatId: 7, messageId: 9010, fromId: 7,
    text: '/eva2 Hoi na de herstart', telegramDate: 1787486400,
    fetchImpl: async (_url, options) => {
      bodies.push(options.body);
      if (bodies.length === 1) {
        return new Response('{"detail":"tijdelijk niet beschikbaar"}', { status: 503 });
      }
      return new Response(JSON.stringify({
        contract: 'EvaInternalGroundedReply/v1', conversation_id: 'telegram:7',
        message_id: 'telegram:7:9010', text: 'Ik ben er weer.',
      }), { headers: { 'content-type': 'application/json' }, status: 200 });
    },
  });

  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
  assert.equal(result.text, 'Ik ben er weer.');
});

test('B-canary stopt gesloten na twee tijdelijke 503-antwoorden', async () => {
  let calls = 0;
  await assert.rejects(askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674', chatId: 7, messageId: 9011, fromId: 7,
    text: '/eva2 Hoi', telegramDate: 1787486400,
    fetchImpl: async () => {
      calls += 1;
      return new Response('', { status: 503 });
    },
  }), /HTTP 503/u);
  assert.equal(calls, 2);
});

test('een privébeurt verwerkt tekstchunks als één oplopend Telegramconcept', async () => {
  const drafts = [];
  let accept;
  const rows = [
    { contract: 'EvaTextDelta/v1', text: 'Hoi ' },
    { contract: 'EvaTextDelta/v1', text: 'Jaap.' },
    {
      contract: 'EvaInternalGroundedReply/v1',
      conversation_id: 'telegram:7',
      message_id: 'telegram:7:9201',
      text: 'Hoi Jaap.',
    },
  ];
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674', chatId: 7, messageId: 9201, fromId: 7,
    text: '/eva2 Hoi', telegramDate: 1787486400,
    onDraft: async (text) => drafts.push(text),
    fetchImpl: async (_url, options) => {
      accept = options.headers.Accept;
      return new Response(`${rows.map((row) => JSON.stringify(row)).join('\n')}\n`, {
        headers: { 'content-type': 'application/x-ndjson' }, status: 200,
      });
    },
  });
  assert.equal(accept, 'application/x-ndjson');
  assert.deepEqual(drafts, ['Hoi ', 'Hoi Jaap.']);
  assert.equal(result.text, 'Hoi Jaap.');
});

test('een oudere Eva2 JSON-route blijft werken wanneer drafts al aanstaan', async () => {
  const drafts = [];
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674', chatId: 7, messageId: 9202, fromId: 7,
    text: '/eva2 Hoi', telegramDate: 1787486400,
    onDraft: async (text) => drafts.push(text),
    fetchImpl: async () => new Response(JSON.stringify({
      contract: 'EvaInternalGroundedReply/v1', conversation_id: 'telegram:7',
      message_id: 'telegram:7:9202', text: 'Gewoon antwoord.',
    }), { headers: { 'content-type': 'application/json' }, status: 200 }),
  });
  assert.deepEqual(drafts, []);
  assert.equal(result.text, 'Gewoon antwoord.');
});

test('B-canary laat Eva2 de groepsconsent server-side bepalen', async () => {
  let captured;
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674',
    chatId: -42,
    messageId: 9003,
    fromId: 7,
    text: '/eva2 Dit hoort bij onze groep',
    telegramDate: 1787486400,
    fetchImpl: async (_url, options) => {
      captured = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          contract: 'EvaInternalGroundedReply/v1',
          conversation_id: 'telegram:-42',
          message_id: 'telegram:-42:9003',
          text: 'Stuur mij privé /eva2 /share -42.',
        }),
      };
    },
  });
  assert.equal(captured.chat_id, '-42');
  assert.equal(captured.telegram_user_id, 7);
  assert.equal(result.text, 'Stuur mij privé /eva2 /share -42.');
});

test('ieder privéaccount wordt afzonderlijk aan Eva2 aangeboden', async () => {
  const identities = [];
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
        identities.push([body.chat_id, body.telegram_user_id]);
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
  assert.deepEqual(identities, [['7', 7], ['8', 8]]);
});
