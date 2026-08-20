import assert from 'node:assert/strict';
import { generateKeyPairSync, sign } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after, before } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-web-request-identity-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.OPENAI_API_KEY = 'test-only';
process.env.NODE_ENV = 'test';

const {
  canonicalPublicRequestId,
} = await import('../src/web/requestidentity.js');
const { createPreparedUnderstandingTurn } = await import('../src/mind/understanding.js');
const { db } = await import('../src/db.js');
const { canonicalHttpClientAckPreimage, canonicalHttpWireSha256 } = await import(
  '../src/web/httpack.js'
);
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);
const { startIsolatedWebServer } = await import('./web-server-test-composition.js');
const { startWeb } = await import('../src/web/server.js');

let server;
const handled = [];
const delivered = [];
const preparedResults = new Map();
const clientKeyPair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
const clientJwk = clientKeyPair.publicKey.export({ format: 'jwk' });
const clientAckPublicKey = {
  contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
  kty: 'EC', crv: 'P-256', x: clientJwk.x, y: clientJwk.y,
};

before(async () => {
  server = await startIsolatedWebServer({
    handleMessage: async (input) => {
      handled.push(input);
      if (input.text === 'gelijktijdig') {
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      let result;
      if (input.text === 'duurzaam verder') {
        result = {
          reply: 'Ik heb de veilige vervolgstap bewaard.',
          turnId: 'turn-pending-0001',
          durableHandoff: true,
          executionPending: true,
          workItemId: 101,
          goalEpisodeId: 201,
        };
      } else if (input.text === 'antwoord volgt later') {
        result = {
          reply: '', turnId: 'turn-pending-0002',
          durableHandoff: true, executionPending: true,
          workItemId: 102, goalEpisodeId: 202,
        };
      } else if (input.text === 'ingehaald') {
        result = {
          reply: '', turnId: 'turn-superseded-0001',
          superseded: true, yielded: true,
        };
      } else if (input.text === 'duurzame fout') {
        result = {
          reply: '', turnId: 'turn-error-0001', error: 'interpreter_unavailable',
        };
      } else {
        result = { reply: `antwoord:${input.text}`, turnId: `turn-web-${handled.length}` };
      }
      result = {
        ...result, channel: input.channel, conversationId: input.messageContext.conversationId,
        originMessageId: input.messageContext.messageId,
        syntheticTest: input.messageContext.syntheticTest === true,
      };
      const proof = createPreparedUnderstandingTurn(result);
      seedAuthorizedTurnUnderstanding({
        database: db, channel: result.channel, conversationId: result.conversationId,
        messageId: result.originMessageId, requester: input.sender,
        originalText: input.text.trim(), syntheticTest: result.syntheticTest,
        output: proof,
      });
      preparedResults.set(
        `${input.channel}:${input.messageContext.conversationId}:${input.messageContext.messageId}`,
        result,
      );
      return result;
    },
    recordDelivery: (result, receipt) => delivered.push({ result, receipt }),
    verifyHttpResult: (result) => createPreparedUnderstandingTurn(result),
    replayHttpResult: ({ channel, conversationId, messageId }) => (
      preparedResults.get(`${channel}:${conversationId}:${messageId}`) || null
    ),
    verifyDelivery: (result) => delivered.some((entry) => entry.result === result),
  });
});

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function baseUrl() {
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return `http://127.0.0.1:${address.port}`;
}

async function post(route, body, headers = {}) {
  const requestBody = ['/api/chat', '/api/voice'].includes(route)
    && !Object.hasOwn(body, 'client_ack_public_key')
    ? { ...body, client_ack_public_key: clientAckPublicKey } : body;
  const response = await fetch(`${baseUrl()}${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(requestBody),
  });
  const bodyBytes = Buffer.from(await response.arrayBuffer());
  const responseBody = JSON.parse(bodyBytes.toString('utf8'));
  if (responseBody?._httpDelivery) {
    const envelope = responseBody._httpDelivery;
    const ack = {
      contract: 'HttpClientAck/v3', requestRef: envelope.requestRef,
      responseRef: envelope.responseRef, responseSha256: envelope.responseSha256,
      deliveryRef: envelope.deliveryRef, challenge: envelope.challenge,
      writeAttemptRef: response.headers.get('x-eva-http-write-attempt-ref'),
      writeGeneration: Number(response.headers.get('x-eva-http-write-generation')),
      wireSha256: canonicalHttpWireSha256({
        status: response.status, contentType: response.headers.get('content-type'), bodyBytes,
      }),
    };
    const signature = sign('sha256', Buffer.from(canonicalHttpClientAckPreimage(ack)), {
      key: clientKeyPair.privateKey, dsaEncoding: 'ieee-p1363',
    }).toString('base64url');
    const acknowledged = await fetch(`${baseUrl()}/api/http-delivery/ack`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...ack, signature }),
    });
    assert.equal(acknowledged.status, 200, await acknowledged.text());
  }
  return { response, body: responseBody, bodyBytes };
}

test('request_id-parser accepteert exact body of header en weigert fallbacknormalisatie', () => {
  assert.deepEqual(canonicalPublicRequestId({ bodyRequestId: 'body-key.00000001' }), {
    ok: true, requestId: 'body-key.00000001', synthetic: false,
  });
  assert.deepEqual(canonicalPublicRequestId({ headerRequestId: 'header:key:00000001' }), {
    ok: true, requestId: 'header:key:00000001', synthetic: false,
  });
  assert.equal(canonicalPublicRequestId({}).reason, 'missing');
  assert.equal(canonicalPublicRequestId({ bodyRequestId: ' short-id' }).reason, 'invalid');
  assert.equal(canonicalPublicRequestId({ bodyRequestId: 12345678 }).reason, 'invalid');
  assert.equal(canonicalPublicRequestId({
    bodyRequestId: 'body-key.00000001', headerRequestId: 'body-key.00000002',
  }).reason, 'mismatch');
  assert.equal(canonicalPublicRequestId({ synthetic: true }).reason, 'missing');
  assert.deepEqual(canonicalPublicRequestId({
    bodyRequestId: 'synthetic:00000001', synthetic: true,
  }), {
    ok: true, requestId: 'synthetic:00000001', synthetic: true,
  });
});

test('productie startWeb accepteert geen vrije handler- of verifierinjectie', async () => {
  await assert.rejects(
    () => startWeb({ handleMessage: async () => ({ reply: 'vervalst' }) }),
    /startWeb accepteert geen dependencies/iu,
  );
  await assert.rejects(
    () => startWeb(new Proxy({}, {
      ownKeys: () => [],
      get: (_target, key) => key === 'handleMessage'
        ? async () => ({ reply: 'vervalst' }) : undefined,
    })),
    /startWeb accepteert geen dependencies/iu,
  );
});

test('productievoice zonder HTTP-voice source-policy faalt vóór provider, ledger en delivery', async () => {
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    throw new Error('provider mag vóór geldige voice source-policy niet starten');
  };
  const tables = [
    'turn_tool_executions', 'eva_turn_understanding_authorities_v1',
    'eva_work_items', 'eva_http_delivery_items_v3',
    'eva_http_delivery_attempt_events_v3', 'eva_http_delivery_ack_receipts_v3',
  ];
  const before = Object.fromEntries(tables.map((table) => [
    table, db.prepare(`SELECT count(*) AS count FROM main.${table}`).get().count,
  ]));
  let productionServer;
  try {
    productionServer = await startWeb();
    const address = productionServer.address();
    assert.ok(address && typeof address === 'object');
    const requestId = 'voice-http-policy:00000001';
    const response = await originalFetch(`http://127.0.0.1:${address.port}/api/voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId },
      body: JSON.stringify({
        text: 'start niet zonder voice source-policy', request_id: requestId,
        conversation_id: 'voice:http-policy', speak: false,
        client_ack_public_key: clientAckPublicKey,
      }),
    });
    const body = await response.json();
    assert.equal(response.status, 503, JSON.stringify(body));
    assert.equal(Object.hasOwn(body, '_httpDelivery'), false);
  } finally {
    if (productionServer) await new Promise((resolve) => productionServer.close(resolve));
    globalThis.fetch = originalFetch;
  }
  assert.equal(providerCalls, 0);
  for (const table of tables) {
    assert.equal(db.prepare(`SELECT count(*) AS count FROM main.${table}`).get().count,
      before[table], `${table} wijzigde vóór geldige voice source-policy`);
  }
});

test('chat en voice weigeren ontbrekende, ongeldige of ongelijke identiteit vóór de handler', async () => {
  const beforeCalls = handled.length;
  for (const route of ['/api/chat', '/api/voice']) {
    for (const request of [
      [{ text: 'mag niet starten' }, {}],
      [{ text: 'mag niet starten', request_id: 'kort' }, {}],
      [{ text: 'mag niet starten', request_id: ' exact-key.00000001' }, {}],
      [{ text: 'mag niet starten', request_id: 12345678 }, {}],
      [{ text: 'mag niet starten', request_id: 'body-key.00000003' },
        { 'Idempotency-Key': 'header-key.00000003' }],
    ]) {
      const result = await post(route, request[0], request[1]);
      assert.equal(result.response.status, 400, `${route}: ${JSON.stringify(result.body)}`);
      assert.match(result.body.error, /request_id|Idempotency-Key/iu);
    }
  }
  assert.equal(handled.length, beforeCalls);
});

test('geaccepteerde chatidentiteit wordt exact geëchood en doorgegeven', async () => {
  const chatId = 'web-chat:00000001';
  const chat = await post('/api/chat', { text: 'hallo web', request_id: chatId });
  assert.equal(chat.response.status, 200);
  assert.equal(chat.body.request_id, chatId);
  assert.equal(chat.body.request_replay_scope, 'durable_http_ingress');
  assert.equal(handled.at(-1).channel, 'web');
  assert.equal(handled.at(-1).messageContext.messageId, chatId);
});

test('dezelfde key en intent replayt process-lokaal zonder tweede uitvoering', async () => {
  const id = 'web-replay:00000001';
  const beforeCalls = handled.length;
  const beforeDeliveries = delivered.length;
  const first = await post('/api/chat', { text: 'een keer', request_id: id });
  const second = await post('/api/chat', { text: 'een keer', request_id: id });
  assert.equal(first.response.status, 200);
  assert.equal(second.response.status, 200);
  assert.deepEqual(second.body, first.body);
  assert.equal(second.response.headers.get('Idempotency-Replayed'), 'true');
  assert.equal(handled.length, beforeCalls + 1);
  assert.equal(delivered.length, beforeDeliveries + 1);
});

test('gelijktijdige identieke requests delen één uitvoering en één antwoord', async () => {
  const id = 'web-concurrent:00000001';
  const beforeCalls = handled.length;
  const beforeDeliveries = delivered.length;
  const concurrent = await Promise.all([
    post('/api/chat', { text: 'gelijktijdig', request_id: id }),
    post('/api/chat', { text: 'gelijktijdig', request_id: id }),
  ]);
  const [first] = concurrent.filter((item) => item.response.status === 200);
  const [waiting] = concurrent.filter((item) => item.response.status === 503);
  assert.ok(first);
  assert.ok(waiting);
  assert.equal(Object.hasOwn(waiting.body, '_httpDelivery'), false);
  assert.equal(waiting.response.headers.get('x-eva-http-write-attempt-ref'), null);
  const replay = await post('/api/chat', { text: 'gelijktijdig', request_id: id });
  assert.equal(replay.response.status, 200);
  assert.deepEqual(replay.body, first.body);
  assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
  assert.equal(handled.length, beforeCalls + 1);
  assert.equal(delivered.length, beforeDeliveries + 1);
});

test('dezelfde key met andere gebonden HTTP-intent faalt gesloten met 409', async () => {
  const id = 'web-conflict:00000001';
  const beforeCalls = handled.length;
  const first = await post('/api/chat', { text: 'intentie A', request_id: id });
  assert.equal(first.response.status, 200);
  const changedText = await post('/api/chat', { text: 'intentie B', request_id: id });
  assert.equal(changedText.response.status, 409);
  assert.equal(changedText.body.request_id, id);
  assert.equal(changedText.body.reason, 'request_intent_conflict');
  assert.equal(handled.length, beforeCalls + 1);
});

test('202 blijft aan dezelfde key gebonden en wordt niet stil opnieuw uitgevoerd', async () => {
  const id = 'web-pending:00000001';
  const beforeCalls = handled.length;
  const first = await post('/api/chat', { text: 'duurzaam verder', request_id: id });
  const replay = await post('/api/chat', { text: 'duurzaam verder', request_id: id });
  assert.equal(first.response.status, 202);
  assert.equal(first.body.request_id, id);
  assert.deepEqual(replay.body, first.body);
  assert.equal(replay.response.status, 202);
  assert.equal(handled.length, beforeCalls + 1,
    'zonder web-write-ahead mag een 202-retry geen tweede providerbeurt starten');
});

test('een lege interne vervolgstatus wordt één keer als vaste duurzame 202-aflevering bezorgd', async () => {
  const id = 'web-pending:00000002';
  const beforeCalls = handled.length;
  const beforeDeliveries = delivered.length;
  const first = await post('/api/chat', { text: 'antwoord volgt later', request_id: id });
  const replay = await post('/api/chat', { text: 'antwoord volgt later', request_id: id });
  assert.equal(first.response.status, 202);
  assert.equal(first.body.reply, 'De opdracht is veilig bewaard en wordt vervolgd.');
  assert.deepEqual(replay.body, first.body);
  assert.equal(handled.length, beforeCalls + 1);
  assert.equal(delivered.length, beforeDeliveries + 1);
  assert.equal(delivered.at(-1).result.reply, first.body.reply);
});

test('superseded en foutresultaten zijn op de eerste call exact dezelfde terminale response als replay', async () => {
  for (const [text, expectedStatus] of [['ingehaald', 409], ['duurzame fout', 500]]) {
    const id = `web-terminal:${expectedStatus}:0001`;
    const beforeDeliveries = delivered.length;
    const first = await post('/api/chat', { text, request_id: id });
    const replay = await post('/api/chat', { text, request_id: id });
    assert.equal(first.response.status, expectedStatus,
      `${JSON.stringify(first.body)} replay=${JSON.stringify(replay.body)}`);
    assert.equal(replay.response.status, expectedStatus);
    assert.deepEqual(replay.body, first.body);
    assert.equal(delivered.length, beforeDeliveries);
  }
});

test('publieke callers kunnen geen synthetic-authority minten', async () => {
  const beforeCalls = handled.length;
  const requestId = 'synthetic:canary:00000001';
  const result = await post('/api/chat', {
    text: 'canary', synthetic: true, request_id: requestId,
  }, { 'Idempotency-Key': requestId });
  assert.equal(result.response.status, 400);
  assert.match(result.body.error, /synthetic.*geen publieke/iu);
  assert.equal(handled.length, beforeCalls);
});

test('spraakverzoek faalt vóór uitvoering zolang duurzame speech-outbox ontbreekt', async () => {
  const beforeCalls = handled.length;
  const requestId = 'voice-speech:00000001';
  const result = await post('/api/voice', {
    text: 'zeg dit hardop', speak: true, request_id: requestId,
  }, { 'Idempotency-Key': requestId });
  assert.equal(result.response.status, 503);
  assert.equal(result.body.request_id, requestId);
  assert.match(result.body.error, /spraakaflevering.*niet beschikbaar/iu);
  assert.equal(handled.length, beforeCalls);
});

test('dashboard bewaart request, sleutel en ACK tot serverbevestiging', () => {
  const script = fs.readFileSync(new URL('../src/web/public/app.js', import.meta.url), 'utf8');
  assert.match(script, /crypto\.randomUUID\(\)/u);
  assert.doesNotMatch(script, /sessionStorage/u);
  assert.match(script, /storePendingHttpRequest/u);
  assert.match(script, /storePendingHttpDelivery/u);
  assert.match(script, /loadPendingHttpDelivery/u);
  assert.match(script, /bodyBytes:\s*response\.bodyBytes/u);
  assert.match(script, /request_id:\s*pending\.requestId/u);
  assert.match(script, /'Idempotency-Key':\s*pending\.requestId/u);
  assert.match(script, /JSON\.parse\(rawBody\)/u);
  assert.match(script, /response\.body\.request_id\s*!==\s*pending\.requestId/u);
  assert.match(script, /client_ack_public_key:\s*pending\.clientAckPublicKey/u);
  assert.match(script, /\/api\/http-delivery\/ack/u);
  assert.match(script, /ensurePendingChatBubble/u);
  assert.match(script, /accepted\.body\.delivered\s*!==\s*true[\s\S]+clearPendingHttpDelivery[\s\S]+const result/u);
  assert.match(script, /pending\.ack\s*&&\s*error\.status\s*===\s*400[\s\S]+dezelfde duurzame ACK opnieuw/u);
  assert.match(script, /!pending\.ack\s*&&\s*error\.status[\s\S]+error\.status\s*>=\s*400/u);
  assert.match(script, /else if \(pending\.lastResponse[\s\S]+?volatilePendingChat\s*=\s*pending/u);
});

test('dashboard valideert ACK-response-identiteiten vóór tonen en wissen', () => {
  const script = fs.readFileSync(new URL('../src/web/public/app.js', import.meta.url), 'utf8');
  const handler = script.slice(
    script.indexOf('async function submitChat()'),
    script.indexOf("$('#chat-form').addEventListener"),
  );
  assert.match(handler, /accepted\.body\.request_ref\s*!==\s*pending\.ack\.requestRef/u);
  assert.match(handler, /accepted\.body\.delivery_ref\s*!==\s*pending\.ack\.deliveryRef/u);
  assert.match(script, /const ACK_RECEIPT_PATTERN = \/\^http-delivery-ack-v3:/u);
  const identityCheck = handler.indexOf('accepted.body.request_ref !== pending.ack.requestRef');
  const display = handler.indexOf("addChat('assistant', result.reply)");
  const clear = handler.indexOf('clearPendingHttpDeliveryOrReload({');
  assert.ok(identityCheck >= 0 && identityCheck < display && identityCheck < clear);
});
