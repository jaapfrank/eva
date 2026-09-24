import assert from 'node:assert/strict';
import test from 'node:test';

import {
  askEva2Canary,
  createEva2DraftSink,
  eva2CanaryPrompt,
  labelEva1TelegramReply,
  labelEva2TelegramFailure,
  labelEva2TelegramReply,
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

test('een gewone vervolgzin gaat ook na een /eva2-beurt nooit naar B', async () => {
  let calls = 0;
  const result = await askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674',
    chatId: 7,
    messageId: 9002,
    fromId: 7,
    text: 'Doe maar',
    // Een oude caller mag de verwijderde continuationgrens niet heropenen.
    continuation: true,
    telegramDate: 1787486410,
    fetchImpl: async () => { calls += 1; throw new Error('mag niet worden aangeroepen'); },
  });
  assert.equal(result, null);
  assert.equal(calls, 0);
});

test('ieder zichtbaar canary-antwoord en iedere fout hebben een ondubbelzinnig label', () => {
  assert.equal(labelEva1TelegramReply('Hoi Jaap.'), 'Eva1: Hoi Jaap.');
  assert.equal(labelEva1TelegramReply('Eva1: Hoi Jaap.'), 'Eva1: Hoi Jaap.');
  assert.equal(labelEva2TelegramReply('Hoi Jaap.'), 'Eva2: Hoi Jaap.');
  assert.equal(labelEva2TelegramReply('Eva2: Hoi Jaap.'), 'Eva2: Hoi Jaap.');
  assert.equal(
    labelEva2TelegramFailure('tijdelijk niet beschikbaar'),
    'Eva2-fout: tijdelijk niet beschikbaar. Eva1 heeft dit bericht niet verwerkt.',
  );
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
  assert.deepEqual(captured.body.recent_messages, []);
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

test('een server-side streamfout wordt herkenbaar als Eva2-fout gemeld', async () => {
  await assert.rejects(askEva2Canary({
    baseUrl: 'http://127.0.0.1:44674', chatId: 7, messageId: 9203, fromId: 7,
    text: '/eva2 Zet iets aan', telegramDate: 1787486400,
    onDraft: async () => {},
    fetchImpl: async () => new Response(
      `${JSON.stringify({ contract: 'EvaTextStreamError/v1' })}\n`,
      { headers: { 'content-type': 'application/x-ndjson' }, status: 200 },
    ),
  }), /Eva2 kon haar antwoord niet veilig afronden/u);
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


test('receipt precedes blocked answer and does not become its draft prefix', async () => {
  const drafts = []; let controller; let receiptSeen;
  const seen = new Promise(resolve => { receiptSeen = resolve; });
  const sink = createEva2DraftSink({ chatId: 7, draftId: 9301,
    sendDraft: async value => { drafts.push(value); return true; } });
  const pending = askEva2Canary({
    baseUrl: 'http://local', chatId: 7, fromId: 7, messageId: 9301,
    text: '/eva2 Hallo', telegramDate: 1787486400,
    onDraft: sink, onReceipt: async text => { await sink.receipt(text); receiptSeen(); },
    fetchImpl: async () => new Response(new ReadableStream({ start(value) { controller = value; } }),
      { headers: { 'content-type': 'application/x-ndjson', 'x-eva-input-receipt': 'Een momentje.' } }),
  });
  await seen;
  assert.deepEqual(drafts, [{ chat_id: 7, draft_id: 9301, text: 'Een momentje.' }]);
  const rows = [{ contract: 'EvaTextDelta/v1', text: 'Hallo!' },
    { contract: 'EvaInternalGroundedReply/v1', conversation_id: 'telegram:7',
      message_id: 'telegram:7:9301', text: 'Hallo!' }];
  controller.enqueue(new TextEncoder().encode(rows.map(JSON.stringify).join('\n') + '\n'));
  controller.close();
  assert.equal((await pending).text, 'Hallo!');
  assert.equal(drafts[1].text, 'Hallo!');
});

for (const mode of ['denied', 'missing', 'oversized', 'failed']) {
  test(`optional receipt ${mode} preserves answer or HTTP failure`, async () => {
    let receipts = 0;
    const headers = { 'content-type': 'application/x-ndjson' };
    if (mode !== 'missing') headers['x-eva-input-receipt'] = mode === 'oversized' ? 'x'.repeat(121) : 'Een momentje.';
    const pending = askEva2Canary({
      baseUrl: 'http://local', chatId: 7, fromId: 7, messageId: 9302,
      text: '/eva2 Hallo', telegramDate: 1787486400,
      onDraft: async () => {}, onReceipt: async () => { receipts++; throw new Error('draft unavailable'); },
      fetchImpl: async () => new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: 'telegram:7', message_id: 'telegram:7:9302', text: 'Hallo!' }) + '\n',
        { status: mode === 'denied' ? 403 : 200, headers }),
    });
    if (mode === 'denied') await assert.rejects(pending, /HTTP 403/u);
    else assert.equal((await pending).text, 'Hallo!');
    assert.equal(receipts, mode === 'failed' ? 1 : 0);
  });
}


test('receipt draft aborts slow transport without disabling real drafts', async () => {
  let calls = 0;
  const sink = createEva2DraftSink({ chatId: 7, draftId: 9303,
    sendDraft: async (_payload, signal) => {
      calls++;
      if (!signal) return true;
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      });
    } });
  assert.equal(await sink.receipt('Een momentje.'), false);
  assert.equal(await sink('Hallo!'), true);
  assert.equal(calls, 2);
});
