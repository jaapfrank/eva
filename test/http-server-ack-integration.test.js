import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after, before } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-server-ack-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.NODE_ENV = 'test';
process.env.EVA_WEB_TOKEN = 'ack-boundary-token';

const { db } = await import('../src/db.js');
const { config } = await import('../src/config.js');
const { canonicalJson } = await import('../src/util.js');
const { createPreparedUnderstandingTurn } = await import('../src/mind/understanding.js');
const { canonicalHttpClientAckPreimage, canonicalHttpWireSha256 } = await import(
  '../src/web/httpack.js'
);
const { beginHttpIngress, releaseHttpIngressAuthority } = await import(
  '../src/web/httpingress.js'
);
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);
const { startIsolatedWebServer } = await import('./web-server-test-composition.js');
const webServerModule = await import('../src/web/server.js');
const projectRoot = path.resolve(new URL('..', import.meta.url).pathname);

let server;
let handled = 0;
const preparedResults = new Map();
const delivered = [];
const voiceIntents = [];

function clientKeyPair() {
  const pair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  return {
    privateKey: pair.privateKey,
    publicKey: {
      contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
      kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
    },
  };
}

const keys = clientKeyPair();
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function resultKey(channel, conversationId, requestId) {
  return `${channel}\0${conversationId}\0${requestId}`;
}

function testDependencies() {
  return {
    handleMessage: async (input) => {
      handled += 1;
      if (input.channel === 'voice') {
        voiceIntents.push(input.messageContext.httpIngressIntent);
        throw new Error('HTTP-voice source-policy is nog niet uitgegeven');
      }
      const result = {
        channel: input.channel,
        conversationId: input.messageContext.conversationId,
        originMessageId: input.messageContext.messageId,
        turnId: `turn:http-server-ack:${handled}`,
        reply: `antwoord:${input.text.trim()}`,
        syntheticTest: false,
      };
      const proof = createPreparedUnderstandingTurn(result);
      seedAuthorizedTurnUnderstanding({
        database: db, channel: result.channel, conversationId: result.conversationId,
        messageId: result.originMessageId, requester: input.sender,
        originalText: input.text.trim(), output: proof,
      });
      preparedResults.set(resultKey(
        result.channel, result.conversationId, result.originMessageId,
      ), result);
      return result;
    },
    verifyHttpResult: (result) => createPreparedUnderstandingTurn(result),
    replayHttpResult: ({ channel, conversationId, messageId }) => (
      preparedResults.get(resultKey(channel, conversationId, messageId)) || null
    ),
    recordDelivery: (result, receipt) => {
      if (delivered.some((entry) => entry.result.turnId === result.turnId)) return false;
      delivered.push({ result, receipt });
      return true;
    },
    verifyDelivery: (result) => delivered.some(
      (entry) => entry.result.turnId === result.turnId,
    ),
  };
}

before(async () => {
  server = await startIsolatedWebServer(testDependencies());
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
  const response = await fetch(`${baseUrl()}${route}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', Authorization: 'Bearer ack-boundary-token',
      ...headers,
    },
    body: JSON.stringify(body),
  });
  const bytes = Buffer.from(await response.arrayBuffer());
  let parsed = null;
  try { parsed = JSON.parse(bytes.toString('utf8')); } catch { /* asserted by caller */ }
  return { response, bytes, body: parsed };
}

function signedAck(result, privateKey = keys.privateKey) {
  const envelope = result.body._httpDelivery;
  const preimage = {
    contract: 'HttpClientAck/v3', requestRef: envelope.requestRef,
    responseRef: envelope.responseRef, responseSha256: envelope.responseSha256,
    deliveryRef: envelope.deliveryRef, challenge: envelope.challenge,
    writeAttemptRef: result.response.headers.get('x-eva-http-write-attempt-ref'),
    writeGeneration: Number(result.response.headers.get('x-eva-http-write-generation')),
    wireSha256: canonicalHttpWireSha256({
      status: result.response.status,
      contentType: result.response.headers.get('content-type'),
      bodyBytes: result.bytes,
    }),
  };
  const signature = sign('sha256', Buffer.from(canonicalHttpClientAckPreimage(preimage)), {
    key: privateKey, dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  return { ...preimage, signature };
}

async function postIntent(route, { text, conversationId, requestId }) {
  return post(route, {
    text, conversation_id: conversationId, request_id: requestId,
    client_ack_public_key: keys.publicKey,
    ...(route === '/api/voice' ? { speak: false } : {}),
  }, { 'Idempotency-Key': requestId });
}

function insertHistoricalAckBoundResponse({ text, conversationId, requestId }) {
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: config.webSender,
    rawText: text, conversationId, syntheticTest: false,
    clientAckPublicKey: keys.publicKey,
  };
  const begun = beginHttpIngress({
    channel: 'web', conversationId, requestId, intent,
  });
  assert.equal(begun.kind, 'started');
  assert.equal(releaseHttpIngressAuthority(begun.preparationAuthority).released, true);
  const row = db.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE request_ref=?`).get(begun.request.requestRef);
  const turnId = 'turn:http-server-ack:historical';
  const publicBody = { ok: true, reply: 'historisch antwoord' };
  const publicBodyJson = canonicalJson(publicBody);
  const provenance = {
    contract: 'HttpResultProvenance/v1', kind: 'turn_result', ref: turnId,
    sha256: sha256('historical-attested-api-regression'),
  };
  const responseBody = {
    contract: 'PreparedHttpResponse/v1', channel: 'web',
    conversationId, intentSha256: row.intent_sha256, provenance, publicBody,
    requestId, state: 'prepared', status: 200, turnId,
  };
  const responseJson = canonicalJson(responseBody);
  const responseSha256 = sha256(responseJson);
  const responseRef = `http-response:${responseSha256}`;
  const preparedAt = new Date().toISOString();
  const terminalBody = {
    contract: 'HttpIngressStateEvent/v1', ordinal: 1,
    requestRef: row.request_ref, responseRef, state: 'prepared',
  };
  const terminalJson = canonicalJson(terminalBody);
  const terminalSha256 = sha256(terminalJson);
  const triggerName = 'eva_http_prepared_responses_valid_reference';
  const triggerSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(triggerName).sql;
  const insertResponse = () => db.prepare(`INSERT INTO main.eva_http_prepared_responses (
    contract,response_ref,response_sha256,request_row_id,request_ref,target_state,
    status,turn_id,public_body_json,public_body_sha256,provenance_kind,
    provenance_ref,provenance_sha256,response_json,prepared_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    responseBody.contract, responseRef, responseSha256, row.id, row.request_ref,
    'prepared', 200, turnId, publicBodyJson, sha256(publicBodyJson),
    provenance.kind, provenance.ref, provenance.sha256, responseJson, preparedAt,
  );
  assert.throws(insertResponse, /invalid current public body/u);
  db.transaction(() => {
    db.exec(`DROP TRIGGER main.${triggerName}`);
    insertResponse();
    db.prepare(`INSERT INTO main.eva_http_ingress_state_events (
      contract,event_ref,event_sha256,request_row_id,request_ref,ordinal,state,
      response_ref,event_json,created_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
      terminalBody.contract, `http-ingress-event:${terminalSha256}`, terminalSha256,
      row.id, row.request_ref, 1, 'prepared', responseRef, terminalJson, preparedAt,
    );
    db.exec(triggerSql);
  }).immediate();
  const classified = beginHttpIngress({
    channel: 'web', conversationId, requestId, intent,
  });
  assert.equal(classified.kind, 'legacy_unattested_public_body');
  assert.equal(classified.reason, 'legacy_unattested_public_body');
  assert.equal(Object.hasOwn(classified, 'responseAuthority'), false);
  return begun.request.requestRef;
}

test('chat en voice weigeren ontbrekende of ongeldige ACK-sleutels vóór uitvoering', async () => {
  const beforeHandled = handled;
  for (const route of ['/api/chat', '/api/voice']) {
    const requestId = `missing-ack:${route.endsWith('voice') ? 'voice' : 'chat'}:0001`;
    const missing = await post(route, { text: 'niet uitvoeren', request_id: requestId }, {
      'Idempotency-Key': requestId,
    });
    assert.equal(missing.response.status, 400, missing.bytes.toString('utf8'));
    assert.equal(missing.body.reason, 'client_ack_key_required');

    const invalidId = `${requestId}:invalid`;
    const invalid = await post(route, {
      text: 'ook niet uitvoeren', request_id: invalidId,
      client_ack_public_key: { contract: 'HttpClientAckPublicKey/v3' },
    }, { 'Idempotency-Key': invalidId });
    assert.equal(invalid.response.status, 400, invalid.bytes.toString('utf8'));
    assert.equal(invalid.body.reason, 'client_ack_key_invalid');
  }
  assert.equal(handled, beforeHandled);
});

test('productieserver exporteert geen ambient testdependency-issuer', () => {
  assert.equal(Object.hasOwn(webServerModule, 'issueWebTestDependencies'), false);
});

test('API-authenticatie gebeurt vóór JSON-parsing', async () => {
  const response = await fetch(`${baseUrl()}/api/http-delivery/ack`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{',
  });
  assert.equal(response.status, 401);
});

test('ACK-endpoint heeft een eigen kleine bodygrens', async () => {
  const response = await fetch(`${baseUrl()}/api/http-delivery/ack`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', Authorization: 'Bearer ack-boundary-token',
    },
    body: JSON.stringify({ padding: 'x'.repeat(8 * 1024) }),
  });
  assert.equal(response.status, 413);
});

test('exacte chatwire wordt pas na geldige ACK als geleverd vastgelegd en replayt bytegelijk', async () => {
  const request = {
    text: 'één exact antwoord', conversationId: 'web:http-server-ack',
    requestId: 'http-server-ack:chat:0001',
  };
  const first = await postIntent('/api/chat', request);
  assert.equal(first.response.status, 200, first.bytes.toString('utf8'));
  assert.equal(first.response.headers.get('content-type'), 'application/json; charset=utf-8');
  assert.equal(first.bytes.toString('utf8'), canonicalJson(first.body));
  assert.equal(first.body.reply, `antwoord:${request.text}`);
  assert.equal(first.body.conversation_id, request.conversationId);
  assert.equal(first.body.request_id, request.requestId);
  assert.equal(first.body.request_replay_scope, 'durable_http_ingress');
  assert.match(first.body.runtime_code_sha256, /^[a-f0-9]{64}$/u);
  assert.equal(first.body.turn_id, 'turn:http-server-ack:1');
  assert.equal(first.body._httpDelivery.contract, 'HttpClientAckChallenge/v3');
  const storedBody = JSON.parse(db.prepare(`SELECT public_body_json
    FROM main.eva_http_prepared_responses WHERE request_ref=?`)
    .get(first.body._httpDelivery.requestRef).public_body_json);
  const attestedWireBody = { ...first.body };
  delete attestedWireBody._httpDelivery;
  assert.deepEqual(storedBody, attestedWireBody,
    'de huidige publieke API-vorm is vóór responseauthority en wirehash duurzaam gebonden');
  assert.equal(delivered.length, 0, 'een geslaagde socketwrite is nog geen delivery');

  const handledAfterPrimaryWrite = handled;
  const beforeBrowserPersist = await postIntent('/api/chat', request);
  assert.equal(beforeBrowserPersist.response.status, 200,
    beforeBrowserPersist.bytes.toString('utf8'));
  assert.equal(beforeBrowserPersist.response.headers.get('Idempotency-Replayed'), 'true');
  assert.deepEqual(beforeBrowserPersist.bytes, first.bytes,
    'post-finish/pre-IDB retry haalt exact de duurzame wire opnieuw op');
  assert.equal(beforeBrowserPersist.response.headers.get('x-eva-http-write-attempt-ref'),
    first.response.headers.get('x-eva-http-write-attempt-ref'));
  assert.equal(beforeBrowserPersist.response.headers.get('x-eva-http-write-generation'),
    first.response.headers.get('x-eva-http-write-generation'));
  assert.equal(handled, handledAfterPrimaryWrite, 'retrieval voert geen tweede providerturn uit');
  assert.equal(delivered.length, 0, 'retrieval vóór browser-ACK maakt nog geen delivery');

  const forged = signedAck(first, clientKeyPair().privateKey);
  const refused = await post('/api/http-delivery/ack', forged);
  assert.equal(refused.response.status, 400, refused.bytes.toString('utf8'));
  assert.equal(delivered.length, 0);

  const ack = signedAck(first);
  const accepted = await post('/api/http-delivery/ack', ack);
  assert.equal(accepted.response.status, 200, accepted.bytes.toString('utf8'));
  assert.deepEqual(Object.keys(accepted.body).sort(), [
    'ack_receipt', 'ack_receipt_ref', 'delivered', 'delivery_ref', 'ok', 'request_ref',
  ].sort());
  assert.equal(accepted.body.delivered, true);
  assert.equal(accepted.body.request_ref, ack.requestRef);
  assert.equal(accepted.body.delivery_ref, ack.deliveryRef);
  assert.match(accepted.body.ack_receipt_ref, /^http-delivery-ack-v3:[a-f0-9]{64}$/u);
  assert.equal(accepted.body.ack_receipt_ref,
    `http-delivery-ack-v3:${sha256(canonicalJson(accepted.body.ack_receipt))}`);
  assert.equal(accepted.body.ack_receipt.writeAttemptRef, ack.writeAttemptRef);
  assert.equal(accepted.body.ack_receipt.writeGeneration, ack.writeGeneration);
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].receipt.clientAckReceiptRef.startsWith(
    'http-delivery-ack-v3:',
  ), true);

  const duplicate = await post('/api/http-delivery/ack', ack);
  assert.equal(duplicate.response.status, 200, duplicate.bytes.toString('utf8'));
  assert.equal(duplicate.body.delivered, true);
  assert.equal(delivered.length, 1, 'ACK-replay mint geen tweede agentdelivery');

  const handledAfterFirst = handled;
  const replay = await postIntent('/api/chat', request);
  assert.equal(replay.response.status, 200, replay.bytes.toString('utf8'));
  assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
  assert.deepEqual(replay.bytes, first.bytes);
  assert.equal(handled, handledAfterFirst);
  assert.equal(delivered.length, 1);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const {startIsolatedWebServer}=await import('./test/web-server-test-composition.js');
    const result=${JSON.stringify(preparedResults.get(resultKey(
    'web', request.conversationId, request.requestId,
  )))};
    let executions=0;
    const server=await startIsolatedWebServer({
      handleMessage:async()=>{executions+=1;throw new Error('restart mag niet uitvoeren')},
      verifyHttpResult:()=>({contract:'PreparedUnderstandingTurn/v1'}),replayHttpResult:()=>result,
      recordDelivery:()=>true,verifyDelivery:()=>false,
    });
    const address=server.address();
    const response=await fetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(
    request.requestId,
  )},Authorization:'Bearer ack-boundary-token'},body:JSON.stringify(${JSON.stringify({
    text: request.text, conversation_id: request.conversationId,
    request_id: request.requestId, client_ack_public_key: keys.publicKey,
  })})});
    const bytes=Buffer.from(await response.arrayBuffer());
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');db.close();
    console.log(JSON.stringify({status:response.status,replayed:response.headers.get('Idempotency-Replayed'),bytes:bytes.toString('base64'),executions}));
  `], {
    cwd: projectRoot, encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, EVA_DATA_DIR: dataDir, WEB_PORT: '0', NODE_TEST_CONTEXT: 'child-v8' },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.status, 200);
  assert.equal(restartedOutcome.replayed, 'true');
  assert.equal(restartedOutcome.executions, 0);
  assert.deepEqual(Buffer.from(restartedOutcome.bytes, 'base64'), first.bytes);
});

test('ACK-gebonden historische body zonder huidige API-identiteit replayt fail-closed', async () => {
  const request = {
    text: 'oude duurzame response', conversationId: 'web:http-server-ack:historical',
    requestId: 'http-server-ack:historical:0001',
  };
  const requestRef = insertHistoricalAckBoundResponse(request);
  const beforeHandled = handled;
  const beforeDelivered = delivered.length;
  const response = await postIntent('/api/chat', request);
  assert.equal(response.response.status, 409, response.bytes.toString('utf8'));
  assert.equal(response.body.reason, 'legacy_unattested_public_body');
  assert.equal(Object.hasOwn(response.body, '_httpDelivery'), false);
  assert.equal(handled, beforeHandled);
  assert.equal(delivered.length, beforeDelivered);
  assert.equal(db.prepare(`SELECT count(*) AS count
    FROM main.eva_http_delivery_items_v3 WHERE request_ref=?`).get(requestRef).count, 0);
});

test('voice bindt identiteit en clientsleutel vóór de nog ontbrekende source-policy', async () => {
  const beforeDelivered = delivered.length;
  const request = {
    text: 'luister verder', conversationId: 'voice:http-server-ack',
    requestId: 'http-server-ack:voice:0001',
  };
  const response = await postIntent('/api/voice', request);
  assert.equal(response.response.status, 503, response.bytes.toString('utf8'));
  assert.equal(Object.hasOwn(response.body, '_httpDelivery'), false);
  assert.equal(delivered.length, beforeDelivered);
  assert.deepEqual(voiceIntents.at(-1).clientAckPublicKey, keys.publicKey);
  const binding = db.prepare(`SELECT binding.intent_json
    FROM main.eva_http_client_ack_bindings_v3 binding
    JOIN main.eva_http_ingress_requests request ON request.id=binding.request_row_id
    WHERE request.channel='voice' AND request.conversation_id=? AND request.request_id=?`)
    .get(request.conversationId, request.requestId);
  assert.deepEqual(JSON.parse(binding.intent_json).clientAckPublicKey, keys.publicKey);
});

test('restart migreert alleen exacte vorige publicBody-DDL naar huidige namespace', () => {
  const name = 'eva_http_prepared_responses_valid_reference';
  const guardName = 'eva_http_ingress_requests_no_update';
  const stored = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name).sql;
  const guardStored = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(guardName).sql;
  const ingressSource = fs.readFileSync(new URL('../src/web/httpingress.js', import.meta.url), 'utf8');
  const currentRuleSource = ingressSource.match(
    /const CURRENT_PUBLIC_BODY_TRIGGER_RULE = `([\s\S]*?)`;/u,
  )?.[1];
  const previousRule = ingressSource.match(
    /const PREVIOUS_PUBLIC_BODY_TRIGGER_RULE = `([\s\S]*?)`;/u,
  )?.[1];
  const currentRule = currentRuleSource?.replace(
    '${PUBLIC_REQUEST_REPLAY_SCOPE}', 'durable_http_ingress',
  );
  assert.ok(currentRule && previousRule);
  const previous = stored.replace(currentRule, previousRule);
  const oldest = previous.replace(
    /'runtimeCodeSha256','conversation_id','turn_id','request_id',\s*'request_replay_scope','runtime_code_sha256','conversation_active',\s*'continue_listening','listen_timeout_ms','execution_pending','durable_handoff'/u,
    "'runtimeCodeSha256'",
  );
  assert.notEqual(previous, stored);
  assert.notEqual(oldest, previous);
  const rowsBefore = db.prepare('SELECT count(*) AS count FROM main.eva_http_prepared_responses')
    .get().count;
  const restartStatus = () => {
    const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
      const {httpIngressStoreStatus}=await import('./src/web/httpingress.js');
      const {db}=await import('./src/db.js');
      console.log(JSON.stringify(httpIngressStoreStatus()));db.close();
    `], {
      cwd: projectRoot, encoding: 'utf8', timeout: 30_000,
      env: { ...process.env, EVA_DATA_DIR: dataDir },
    });
    assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
    return JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  };

  db.exec(`DROP TRIGGER main.${name};\n${previous};\nDROP TRIGGER main.${guardName};`);
  assert.equal(restartStatus().available, false);
  assert.equal(db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name).sql, previous,
  'een targetmigratie mag overige DDL-drift niet gedeeltelijk repareren');
  db.exec(`${guardStored};\nDROP TRIGGER main.${name};\n${stored}`);

  db.exec(`DROP TRIGGER main.${name};\n${previous};
    CREATE TABLE main.eva_http_ingress_unexpected_namespace (id INTEGER);`);
  assert.equal(restartStatus().available, false);
  assert.equal(db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name).sql, previous,
  'een onverwachte namespace mag de target-DDL niet muteren');
  db.exec(`DROP TABLE main.eva_http_ingress_unexpected_namespace;
    DROP TRIGGER main.${name};\n${stored}`);

  for (const prior of [previous, oldest]) {
    db.exec(`DROP TRIGGER main.${name};\n${prior}`);
    const status = restartStatus();
    assert.equal(status.available, true, JSON.stringify(status));
    assert.equal(db.prepare('SELECT count(*) AS count FROM main.eva_http_prepared_responses')
      .get().count, rowsBefore);
    assert.equal(db.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='trigger' AND name=?`).get(name).sql, stored);
  }
});

test('ACK-verificatiepogingen zijn per client begrensd', async () => {
  const statuses = [];
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const response = await post('/api/http-delivery/ack', {
      contract: 'HttpClientAck/v3', attempt,
    });
    statuses.push(response.response.status);
    if (response.response.status === 429) break;
  }
  assert.ok(statuses.includes(429), `onbegrensde ACK-pogingen: ${statuses.join(',')}`);
});
