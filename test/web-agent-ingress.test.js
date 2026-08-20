import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-web-agent-ingress-');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.NODE_ENV = 'test';
process.env.EVA_RUNTIME_MODE = 'normal';

const { claimTurnToolExecution, db } = await import('../src/db.js');
const { config } = await import('../src/config.js');
const { canonicalJson, normalizeUserFacingText } = await import('../src/util.js');
const { canonicalHttpClientAckPreimage, canonicalHttpWireSha256 } = await import(
  '../src/web/httpack.js'
);
const {
  handleMessageDetailed,
  preparedUnderstandingIngressReplay,
  verifiedPreparedUnderstandingTurnForResult,
} = await import('../src/mind/agent.js');
const {
  createDurableWork,
  episodeHasFinalDelivery,
  goalEpisodeById,
  recordEpisodeDelivery,
} = await import('../src/mind/portfolio.js');
const { startWeb } = await import('../src/web/server.js');

const httpAckKeyPair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
const httpAckJwk = httpAckKeyPair.publicKey.export({ format: 'jwk' });
const httpClientAckPublicKey = Object.freeze({
  contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
  kty: 'EC', crv: 'P-256', x: httpAckJwk.x, y: httpAckJwk.y,
});

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function replyUnderstanding(text, replyDraft) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'web-ingress-understanding',
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Geef een gewoon antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
    }),
  }]);
}

function textResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text }],
  }]);
}

function appendTestHttpExecutionTakeover(requestRef) {
  const request = db.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE request_ref=?`).get(requestRef);
  const current = db.prepare(`SELECT * FROM main.eva_http_ingress_claim_events
    WHERE request_row_id=? AND phase='execution'
    ORDER BY generation DESC,sequence DESC LIMIT 1`).get(request.id);
  assert.equal(current.action, 'released');
  const createdTs = new Date(Math.max(
    Date.now(), Date.parse(current.created_ts),
  )).toISOString();
  const leaseUntil = new Date(Date.parse(createdTs) + 120_000).toISOString();
  const generation = Number(current.generation) + 1;
  const tokenSha256 = createHash('sha256').update(
    `test-only-http-takeover:${requestRef}:${generation}`,
  ).digest('hex');
  const body = {
    action: 'acquired', contract: 'HttpIngressClaimEvent/v1', generation,
    leaseUntil, phase: 'execution', requestRef, sequence: 0, tokenSha256,
  };
  const eventJson = canonicalJson(body);
  const eventSha256 = createHash('sha256').update(eventJson).digest('hex');
  db.prepare(`INSERT INTO main.eva_http_ingress_claim_events (
    contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,phase,
    generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, `http-claim-event:${eventSha256}`, eventSha256,
    request.id, requestRef, body.phase, generation, body.sequence, body.action,
    tokenSha256, leaseUntil, eventJson, createdTs,
  );
  return { generation, claimEventRef: `http-claim-event:${eventSha256}` };
}

async function postAckBoundHttp({
  nativeFetch, address, route, requestId, body, expectedStatus = null,
}) {
  const response = await nativeFetch(`http://127.0.0.1:${address.port}${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId },
    body: JSON.stringify({ ...body, client_ack_public_key: httpClientAckPublicKey }),
  });
  const bodyBytes = Buffer.from(await response.arrayBuffer());
  const contentType = response.headers.get('content-type');
  assert.equal(contentType, 'application/json; charset=utf-8');
  assert.equal(Number(response.headers.get('content-length')), bodyBytes.length);
  const parsedBody = JSON.parse(bodyBytes.toString('utf8'));
  if (expectedStatus != null) {
    assert.equal(
      response.status, expectedStatus,
      `${route} mist een gediscrimineerde voice-over-http source-authority: ${
        bodyBytes.toString('utf8')}`,
    );
  }
  assert.equal(bodyBytes.toString('utf8'), canonicalJson(parsedBody),
    'de HTTP-wire moet exact canonieke JSON bevatten');
  const envelope = parsedBody?._httpDelivery;
  assert.deepEqual(Object.keys(envelope || {}).sort(), [
    'challenge', 'contract', 'deliveryRef', 'keyFingerprint',
    'requestRef', 'responseRef', 'responseSha256',
  ].sort());
  assert.equal(envelope.contract, 'HttpClientAckChallenge/v3');
  assert.match(envelope.requestRef, /^http-ingress:[a-f0-9]{64}$/u);
  assert.match(envelope.responseRef, /^http-response:[a-f0-9]{64}$/u);
  assert.equal(envelope.responseRef, `http-response:${envelope.responseSha256}`);
  assert.match(envelope.deliveryRef, /^http-delivery-v3:[a-f0-9]{64}$/u);
  assert.match(envelope.challenge, /^[a-f0-9]{64}$/u);
  assert.match(envelope.keyFingerprint, /^p256-spki-sha256:[a-f0-9]{64}$/u);
  const writeAttemptRef = response.headers.get('x-eva-http-write-attempt-ref');
  const writeGeneration = Number(response.headers.get('x-eva-http-write-generation'));
  assert.match(writeAttemptRef || '', /^http-write-attempt-v3:[a-f0-9]{64}$/u);
  assert.equal(Number.isSafeInteger(writeGeneration) && writeGeneration >= 1, true);
  const wireSha256 = canonicalHttpWireSha256({
    status: response.status, contentType, bodyBytes,
  });
  return {
    response, body: parsedBody, bodyBytes, contentType, envelope,
    writeAttemptRef, writeGeneration, wireSha256,
  };
}

function signedAckBoundHttp(exchange) {
  const ack = {
    contract: 'HttpClientAck/v3',
    requestRef: exchange.envelope.requestRef,
    responseRef: exchange.envelope.responseRef,
    responseSha256: exchange.envelope.responseSha256,
    deliveryRef: exchange.envelope.deliveryRef,
    challenge: exchange.envelope.challenge,
    wireSha256: exchange.wireSha256,
    writeAttemptRef: exchange.writeAttemptRef,
    writeGeneration: exchange.writeGeneration,
  };
  const signature = sign(
    'sha256', Buffer.from(canonicalHttpClientAckPreimage(ack), 'utf8'),
    { key: httpAckKeyPair.privateKey, dsaEncoding: 'ieee-p1363' },
  ).toString('base64url');
  return { ...ack, signature };
}

async function acknowledgeAckBoundHttp({ nativeFetch, address, exchange }) {
  const ack = signedAckBoundHttp(exchange);
  const response = await nativeFetch(
    `http://127.0.0.1:${address.port}/api/http-delivery/ack`,
    {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ack),
    },
  );
  const bodyBytes = Buffer.from(await response.arrayBuffer());
  const body = JSON.parse(bodyBytes.toString('utf8'));
  assert.equal(response.status, 200, bodyBytes.toString('utf8'));
  assert.equal(body.ok, true);
  assert.equal(body.delivered, true);
  assert.equal(body.request_ref, ack.requestRef);
  assert.equal(body.delivery_ref, ack.deliveryRef);
  assert.match(body.ack_receipt_ref, /^http-delivery-ack-v3:[a-f0-9]{64}$/u);
  return { response, body, bodyBytes, ack };
}

function omittedMaterialUnderstanding(text) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'web-material-omission',
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: { statement: text, outcome: 'Een controleerbaar intern bestand.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: {
        kind: 'reply', requirementIds: [], replyDraft: 'Ik heb het bestand gecorrigeerd.',
      },
    }),
  }]);
}

test('webrequest krijgt vóór antwoord een stabiele duurzame agent-ingress en replayt zonder provider', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Hoe gaat het vandaag met je?';
  const reply = 'Goed. Ik ben aandachtig en rustig aanwezig.';
  const messageId = 'web-request:durable-agent:0001';
  const conversationId = 'web:durable-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  let first;
  let replay;
  try {
    first = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    });
    const callsAfterFirst = providerCalls;
    replay = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    });
    assert.equal(providerCalls, callsAfterFirst,
      'dezelfde request mag provider of tools niet opnieuw bereiken');
    await assert.rejects(() => handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: `${text} andere inhoud`,
      messageContext: { conversationId, messageId, identityTrusted: true },
    }), /andere handeling gebonden/iu);
    assert.equal(providerCalls, callsAfterFirst,
      'dezelfde request-id met andere inhoud faalt vóór de provider');
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(replay.replayed, true);
  assert.equal(replay.reply, first.reply);
  assert.equal(replay.turnId, first.turnId);
  assert.equal(replay.originMessageId, messageId);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='user'`).get(conversationId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='chat_in' AND json_extract(meta,'$.conversationId')=?`).get(conversationId).count, 1);
  const ingress = db.prepare(`SELECT status,output FROM turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='web'
      AND json_extract(args,'$.conversationId')=?
      AND json_extract(args,'$.messageId')=?`).get(conversationId, messageId);
  assert.equal(ingress.status, 'done');
  const prepared = JSON.parse(ingress.output);
  assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(prepared.turnId, first.turnId);
  assert.equal(prepared.reply, first.reply);
  assert.match(prepared.sha256, /^[a-f0-9]{64}$/u);
  assert.equal(verifiedPreparedUnderstandingTurnForResult(first, {
    channel: 'web', conversationId, messageId,
  })?.sha256, prepared.sha256);
  assert.equal(verifiedPreparedUnderstandingTurnForResult({
    ...first, reply: `${first.reply} vervalst`,
  }, { channel: 'web', conversationId, messageId }), null);
  assert.equal(preparedUnderstandingIngressReplay({
    channel: 'web', conversationId, messageId, sender: 'Jaap',
  })?.reply, first.reply);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    let providerCalls=0;
    globalThis.fetch=async()=>{providerCalls+=1;throw new Error('provider mag niet starten')};
    const {handleMessageDetailed}=await import('./src/mind/agent.js');
    const replay=await handleMessageDetailed(${JSON.stringify({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    })});
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({providerCalls,replay}));
    db.close();
    process.exit(0);
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.providerCalls, 0);
  assert.equal(restartedOutcome.replay.replayed, true);
  assert.equal(restartedOutcome.replay.turnId, first.turnId);
  assert.equal(restartedOutcome.replay.reply, first.reply);
});

test('een ingehaalde webbeurt bewaart een exact Prepared supersession-resultaat', async () => {
  const originalFetch = globalThis.fetch;
  const conversationId = 'web:prepared-supersession';
  const firstText = 'Eerste vraag die wordt ingehaald.';
  const secondText = 'Tweede en actuele vraag.';
  let releaseFirst;
  let firstProviderStarted;
  const firstStarted = new Promise((resolve) => { firstProviderStarted = resolve; });
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  let calls = 0;
  globalThis.fetch = async (_input, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    calls += 1;
    if (calls === 1) {
      firstProviderStarted();
      await firstGate;
      return replyUnderstanding(firstText, 'Oud antwoord.');
    }
    if (request.tools?.some((tool) => tool.name === 'commit_turn_understanding')) {
      return replyUnderstanding(secondText, 'Actueel antwoord.');
    }
    return textResponse('Actueel antwoord.');
  };
  let first;
  let second;
  try {
    const firstPromise = handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: firstText,
      messageContext: {
        conversationId, messageId: 'web-superseded:0001', identityTrusted: true,
      },
    });
    await firstStarted;
    const secondPromise = handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: secondText,
      messageContext: {
        conversationId, messageId: 'web-superseded:0002', identityTrusted: true,
      },
    });
    releaseFirst();
    [first, second] = await Promise.all([firstPromise, secondPromise]);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(first.superseded, true);
  assert.equal(first.reply, '');
  assert.equal(second.reply, 'Actueel antwoord.');
  const prepared = JSON.parse(db.prepare(`SELECT output FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`)
    .get('web-superseded:0001').output);
  assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(prepared.metadata.superseded, true);
  assert.equal(prepared.metadata.yielded, true);
  assert.equal(prepared.reply, '');
});

test('de echte HTTP-route verzegelt uitsluitend het duurzame agentantwoord en replayt exact', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Vertel me één rustige zin.';
  const reply = 'Ik ben hier en houd mijn aandacht bij wat werkelijk gebeurt.';
  const requestId = 'web-request:http-agent:0001';
  const conversationId = 'web:http-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = () => postAckBoundHttp({
    nativeFetch: originalFetch, address, route: '/api/chat', requestId,
    body: { text, conversation_id: conversationId, request_id: requestId },
  });
  let firstBody;
  let firstWireBase64;
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.response.status, 200);
    assert.equal(replay.response.status, 200);
    assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
    assert.deepEqual(replay.body, first.body);
    assert.equal(first.body.reply, reply);
    assert.equal(first.body.conversation_id, conversationId);
    assert.equal(first.body.request_id, requestId);
    assert.equal(first.body.request_replay_scope, 'durable_http_ingress');
    assert.deepEqual(replay.bodyBytes, first.bodyBytes);
    assert.equal(providerCalls, callsAfterFirst);
    await acknowledgeAckBoundHttp({ nativeFetch: originalFetch, address, exchange: first });
    firstBody = first.body;
    firstWireBase64 = first.bodyBytes.toString('base64');
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1;
      throw new Error('provider mag bij HTTP-replay niet starten');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb();
    const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)},client_ack_public_key:${JSON.stringify(httpClientAckPublicKey)}}),
    });
    const bodyBytes=Buffer.from(await response.arrayBuffer());
    const body=JSON.parse(bodyBytes.toString('utf8'));
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({status:response.status,replayed:response.headers.get('Idempotency-Replayed'),body,bodyBase64:bodyBytes.toString('base64'),providerCalls}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.status, 200);
  assert.equal(restartedOutcome.replayed, 'true');
  assert.equal(restartedOutcome.providerCalls, 0);
  assert.deepEqual(restartedOutcome.body, firstBody);
  assert.equal(restartedOutcome.bodyBase64, firstWireBase64);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_http_ingress_requests
    WHERE channel='web' AND conversation_id=? AND request_id=?`)
    .get(conversationId, requestId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_http_prepared_responses
    WHERE request_ref=(SELECT request_ref FROM eva_http_ingress_requests
      WHERE channel='web' AND conversation_id=? AND request_id=?)`)
    .get(conversationId, requestId).count, 1);
  const storedPublic = JSON.parse(db.prepare(`SELECT public_body_json
    FROM eva_http_prepared_responses WHERE request_ref=(SELECT request_ref
      FROM eva_http_ingress_requests WHERE channel='web' AND conversation_id=?
        AND request_id=?)`).get(conversationId, requestId).public_body_json);
  assert.equal(storedPublic.runtime_code_sha256, firstBody.runtime_code_sha256);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='user'`).get(conversationId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='assistant'`)
    .get(conversationId).count, 1);
});

test('web case 4: HTTP-voice bindt een eigen HTTP-source-authority en echte client-ACK', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Geef een rustig antwoord zonder spraakuitvoer.';
  const reply = 'Eerste regel.\nTweede  regel blijft bytegelijk.';
  const canonicalReply = normalizeUserFacingText(reply);
  const requestId = 'voice-request:http-agent:0001';
  const conversationId = 'voice:http-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = () => postAckBoundHttp({
    nativeFetch: originalFetch, address, route: '/api/voice', requestId,
    expectedStatus: 200,
    body: {
      text, speak: false, conversation_id: conversationId, request_id: requestId,
    },
  });
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.body.reply, canonicalReply);
    assert.equal(replay.response.status, 200);
    assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
    assert.deepEqual(replay.body, first.body);
    assert.deepEqual(replay.bodyBytes, first.bodyBytes);
    assert.equal(providerCalls, callsAfterFirst);
    await acknowledgeAckBoundHttp({ nativeFetch: originalFetch, address, exchange: first });
    const row = db.prepare(`SELECT output FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='voice'
        AND json_extract(args,'$.conversationId')=?
        AND json_extract(args,'$.messageId')=?`).get(conversationId, requestId);
    const prepared = JSON.parse(row.output);
    assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
    assert.equal(prepared.channel, 'voice');
    assert.equal(prepared.reply, canonicalReply);
    assert.equal(prepared.syntheticTest, false);
    const source = db.prepare(`SELECT authority.source_kind,authority.source_ref,
        authority.source_sha256,request.request_ref,request.intent_sha256
      FROM main.eva_turn_understanding_authorities_v1 authority
      JOIN main.eva_http_ingress_requests request
        ON request.channel=authority.channel
        AND request.conversation_id=authority.conversation_id
        AND request.request_id=authority.message_id
      WHERE authority.channel='voice' AND authority.conversation_id=?
        AND authority.message_id=?`).get(conversationId, requestId);
    assert.equal(source.source_kind, 'http_voice_ingress');
    assert.equal(source.source_ref, source.request_ref);
    assert.equal(source.source_sha256, source.intent_sha256);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
});

test('duurzame ACK vóór receipt sluit via fresh-process HTTP-voice replay', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Bewaar deze ACK en herstel daarna zonder heruitvoering.';
  const reply = 'De duurzame HTTP-voice aflevering is hersteld.';
  const requestId = 'voice-request:http-ack-before-receipt:0001';
  const conversationId = 'voice:http-ack-before-receipt';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply) : textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  let ack;
  try {
    const exchange = await postAckBoundHttp({
      nativeFetch: originalFetch, address, route: '/api/voice', requestId,
      expectedStatus: 200,
      body: {
        text, speak: false, conversation_id: conversationId, request_id: requestId,
      },
    });
    assert.equal(providerCalls, 2);
    ack = signedAckBoundHttp(exchange);
    db.exec(`CREATE TRIGGER main.fail_http_voice_receipt_after_ack
      BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
      BEGIN SELECT RAISE(ABORT,'test crash after durable HTTP ACK'); END`);
    const failed = await originalFetch(
      `http://127.0.0.1:${address.port}/api/http-delivery/ack`,
      {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ack),
      },
    );
    const failedBody = await failed.json();
    assert.equal(failed.status, 503, JSON.stringify(failedBody));
    assert.equal(failedBody.acknowledged, true, JSON.stringify(failedBody));
    assert.equal(failedBody.delivered, false);
    assert.equal(db.prepare(`SELECT count(*) AS count FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.conversationId')=?`)
      .get(conversationId).count, 0);
  } finally {
    db.exec('DROP TRIGGER IF EXISTS main.fail_http_voice_receipt_after_ack');
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1; throw new Error('fresh replay mag provider niet starten');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb(); const address=server.address();
    const request=()=>nativeFetch('http://127.0.0.1:'+address.port+'/api/voice',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},speak:false,
        conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)},
        client_ack_public_key:${JSON.stringify(httpClientAckPublicKey)}})
    });
    const first=await request(); const firstBody=await first.json();
    const second=await request(); const secondBody=await second.json();
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({firstStatus:first.status,secondStatus:second.status,
      firstBody,secondBody,providerCalls,
      chatOut:db.prepare("SELECT count(*) count FROM timeline WHERE kind='chat_out' AND json_extract(meta,'$.conversationId')=?").get(${JSON.stringify(conversationId)}).count,
      assistant:db.prepare("SELECT count(*) count FROM messages WHERE role='assistant' AND channel='voice' AND conversation_id=?").get(${JSON.stringify(conversationId)}).count}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const outcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(outcome.firstStatus, 200, JSON.stringify(outcome.firstBody));
  assert.equal(outcome.secondStatus, 200, JSON.stringify(outcome.secondBody));
  assert.equal(outcome.providerCalls, 0);
  assert.equal(outcome.chatOut, 1);
  assert.equal(outcome.assistant, 1);
});

test('HTTP-voice ACK sluit een finale werkepisode exact eenmaal en overleeft restart', async () => {
  const originalFetch = globalThis.fetch;
  const requestId = 'voice-request:http-final-delivery:0001';
  const conversationId = 'voice:http-final-delivery';
  const { workItem, episode } = createDurableWork({
    source: 'voice', owner: config.webSender, title: 'HTTP-voice eindaflevering',
    goal: 'Lees het bewijs en rond het werk controleerbaar af.',
  }, {
    originKind: 'user', originChannel: 'voice', originConversationId: conversationId,
    originMessageId: requestId, requester: config.webSender,
    deliveryPolicy: 'required', deliveryTarget: config.webSender,
  });
  fs.mkdirSync(config.workspaceDir, { recursive: true });
  const proofPath = path.join(config.workspaceDir, 'http-voice-final-bewijs.txt');
  fs.writeFileSync(proofPath, 'Het HTTP-voice eindresultaat is gecontroleerd.\n');
  const readText = `Lees ${proofPath}`;
  const closeText = `rond werk #${workItem.id} af`;
  const text = `${readText} en ${closeText}.`;
  const reply = 'Het HTTP-voice eindresultaat is gecontroleerd en afgerond.';
  let providerCalls = 0;
  globalThis.fetch = async (input, init = {}) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    const body = JSON.parse(String(init.body || '{}'));
    if (body.tools?.[0]?.name === 'commit_turn_understanding') {
      return completedModelResponse([{
        type: 'function_call', call_id: 'http-voice-final-understanding',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'command',
          goal: {
            statement: 'Rond het gecontroleerde HTTP-voice werk af.',
            outcome: 'Het gecontroleerde resultaat is aantoonbaar afgerond.',
            beneficiary: 'requester',
          },
          conditions: [],
          provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
          confidence: 0.99,
          actions: [{
            capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
            semanticInput: { pad: proofPath },
            sourceSpans: [{ start: text.indexOf(readText),
              end: text.indexOf(readText) + readText.length }],
          }, {
            capabilityId: 'work.episode.manage', operationId: 'afronden',
            polarity: 'request', semanticInput: {
              id: workItem.id, resultaat: 'Het HTTP-voice eindresultaat is gecontroleerd.',
            },
            sourceSpans: [{ start: text.indexOf(closeText),
              end: text.indexOf(closeText) + closeText.length }],
          }],
          requestedEffects: ['read', 'persistent'], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
        }),
      }]);
    }
    return textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  let exchange;
  let ack;
  let prepared;
  try {
    exchange = await postAckBoundHttp({
      nativeFetch: originalFetch, address, route: '/api/voice', requestId,
      expectedStatus: 200,
      body: {
        text, speak: false, conversation_id: conversationId, request_id: requestId,
      },
    });
    assert.equal(exchange.body.reply, reply);
    assert.equal(providerCalls, 2);
    prepared = JSON.parse(db.prepare(`SELECT output FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='voice'
        AND json_extract(args,'$.conversationId')=?
        AND json_extract(args,'$.messageId')=?`).get(conversationId, requestId).output);
    assert.equal(prepared.workItemId, workItem.id);
    assert.equal(prepared.goalEpisodeId, episode.id);
    assert.match(prepared.finalDeliveryBindingSha256, /^[a-f0-9]{64}$/u);
    assert.throws(() => recordEpisodeDelivery(episode.id, {
      deliveredChannel: 'voice', deliveryTarget: config.webSender,
      originConversationId: conversationId, originMessageId: requestId,
      deliveredConversationId: conversationId, resultText: reply,
      idempotencyKey: `turn:${prepared.turnId}:episode:${episode.id}`,
      transport: {
        accepted: true, transport: 'http-voice',
        durableHttpRequestRef: exchange.envelope.requestRef,
        clientAckReceiptRef: `http-delivery-ack-v3:${'0'.repeat(64)}`,
        httpWireSha256: exchange.wireSha256,
      },
      deliveryTurnId: prepared.turnId,
      finalDeliveryBinding: {
        bindingSha256: prepared.finalDeliveryBindingSha256,
        recovery: 'http_voice_ack',
        httpVoiceAckProof: Object.freeze({
          contract: 'AcknowledgedHttpAgentDeliveryProof/v1',
        }),
      },
    }), /exact duurzaam ACK-herstelbewijs/iu);
    ack = signedAckBoundHttp(exchange);
    db.exec(`CREATE TRIGGER main.fail_http_voice_final_receipt_after_ack
      BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
      BEGIN SELECT RAISE(ABORT,'test crash after final HTTP voice ACK'); END`);
    const failed = await originalFetch(
      `http://127.0.0.1:${address.port}/api/http-delivery/ack`,
      {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ack),
      },
    );
    const failedBody = await failed.json();
    assert.equal(failed.status, 503, JSON.stringify(failedBody));
    assert.equal(failedBody.acknowledged, true, JSON.stringify(failedBody));
    assert.equal(failedBody.delivered, false);
    assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
    assert.equal(episodeHasFinalDelivery(episode.id), false);
    assert.equal(db.prepare(`SELECT status FROM main.eva_work_items WHERE id=?`)
      .get(workItem.id).status, 'open');
    assert.equal(db.prepare(`SELECT count(*) AS count FROM main.eva_delivery_receipts
      WHERE episode_id=?`).get(episode.id).count, 0);
    assert.equal(db.prepare(`SELECT count(*) AS count FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?`)
      .get(exchange.body.turn_id).count, 0);
    assert.equal(providerCalls, 2);
  } finally {
    db.exec('DROP TRIGGER IF EXISTS main.fail_http_voice_final_receipt_after_ack');
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }

  const takeover = appendTestHttpExecutionTakeover(exchange.envelope.requestRef);
  assert.equal(takeover.generation, 2);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1; throw new Error('ACK-restart mag niet heruitvoeren');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb(); const address=server.address();
    const request=()=>nativeFetch('http://127.0.0.1:'+address.port+'/api/voice',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},speak:false,
        conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)},
        client_ack_public_key:${JSON.stringify(httpClientAckPublicKey)}})
    });
    const {db}=await import('./src/db.js');
    const snapshot=()=>({providerCalls,
      episode:db.prepare('SELECT state FROM eva_goal_episodes WHERE id=?').get(${episode.id}).state,
      work:db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(${workItem.id}).status,
      receipts:db.prepare('SELECT count(*) count FROM eva_delivery_receipts WHERE episode_id=?').get(${episode.id}).count,
      chatOut:db.prepare("SELECT count(*) count FROM timeline WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?").get(${JSON.stringify(exchange?.body?.turn_id)}).count,
      assistant:db.prepare("SELECT count(*) count FROM messages WHERE role='assistant' AND channel='voice' AND conversation_id=?").get(${JSON.stringify(conversationId)}).count});
    const first=await request(); const firstBytes=Buffer.from(await first.arrayBuffer());
    const afterFirst=snapshot();
    const second=await request(); const secondBytes=Buffer.from(await second.arrayBuffer());
    const ackResponse=await nativeFetch('http://127.0.0.1:'+address.port+'/api/http-delivery/ack',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(${JSON.stringify(ack)})
    });
    const ackBody=await ackResponse.json(); await new Promise(resolve=>server.close(resolve));
    console.log(JSON.stringify({firstStatus:first.status,secondStatus:second.status,
      firstReplay:first.headers.get('Idempotency-Replayed'),
      firstBytes:firstBytes.toString('base64'),secondBytes:secondBytes.toString('base64'),
      ackStatus:ackResponse.status,ackBody,afterFirst,afterRepeated:snapshot()}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.firstStatus, 200,
    Buffer.from(restartedOutcome.firstBytes, 'base64').toString('utf8'));
  assert.equal(restartedOutcome.secondStatus, 200);
  assert.equal(restartedOutcome.firstReplay, 'true');
  assert.equal(restartedOutcome.firstBytes, exchange.bodyBytes.toString('base64'));
  assert.equal(restartedOutcome.secondBytes, exchange.bodyBytes.toString('base64'));
  assert.deepEqual(restartedOutcome.afterFirst, {
    providerCalls: 0, episode: 'closed', work: 'done',
    receipts: 1, chatOut: 1, assistant: 1,
  });
  assert.equal(restartedOutcome.ackStatus, 200);
  assert.equal(restartedOutcome.ackBody.delivered, true);
  assert.deepEqual(restartedOutcome.afterRepeated, restartedOutcome.afterFirst);
});

test('een normale materiaalhandoff wordt als Prepared 202 verzegeld en niet generiek voltooid', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Corrigeer data/workspace/http-handoff.md en sla het controleerbaar op.';
  const requestId = 'web-request:http-handoff:0001';
  const conversationId = 'web:http-handoff';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return omittedMaterialUnderstanding(text);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = () => postAckBoundHttp({
    nativeFetch: originalFetch, address, route: '/api/chat', requestId,
    body: { text, conversation_id: conversationId, request_id: requestId },
  });
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.response.status, 202, JSON.stringify(first.body));
    assert.equal(first.body.durable_handoff, true);
    assert.equal(first.body.execution_pending, true);
    assert.deepEqual(replay.body, first.body);
    assert.deepEqual(replay.bodyBytes, first.bodyBytes);
    assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
    assert.equal(providerCalls, callsAfterFirst);
    await acknowledgeAckBoundHttp({ nativeFetch: originalFetch, address, exchange: first });
    const row = db.prepare(`SELECT output FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='web'
        AND json_extract(args,'$.conversationId')=?
        AND json_extract(args,'$.messageId')=?`).get(conversationId, requestId);
    const prepared = JSON.parse(row.output);
    assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
    assert.equal(prepared.metadata.durableHandoff, true);
    assert.equal(prepared.metadata.executionPending, true);
    assert.ok(prepared.workItemId);
    assert.ok(prepared.goalEpisodeId);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
});

test('een crash vóór agentantwoord wordt na restart één handoff en nooit een tweede providerbeurt', () => {
  const text = 'Werk dit veilig later voor me af.';
  const requestId = 'web-request:http-crash:0001';
  const conversationId = 'web:http-crash';
  const durableTurn = `web:${createHash('sha256').update(canonicalJson({
    conversationId, messageId: requestId,
  })).digest('hex')}`;
  const executionKey = `turn-understanding:${createHash('sha256').update(canonicalJson({
    channel: 'web', conversationId, messageId: requestId,
  })).digest('hex')}`;
  const seeded = claimTurnToolExecution({
    executionKey,
    turnId: `ingress:web:${conversationId}:${requestId}`,
    tool: '@turn/understanding',
    args: {
      channel: 'web', conversationId, messageId: requestId,
      requester: 'Jaap', originalText: text, sessionRef: durableTurn,
      syntheticTest: false,
      supersessionScope: `web:${conversationId}`, supersessionToken: requestId,
    },
  });
  assert.equal(seeded.claimed, true);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1;
      throw new Error('provider mag na crash niet starten');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb();
    const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)}}),
    });
    const body=await response.json();
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({status:response.status,body,providerCalls,
      origins:db.prepare("SELECT count(*) count FROM eva_episode_origins WHERE origin_channel='web' AND origin_conversation_id=? AND origin_message_id=?").get(${JSON.stringify(conversationId)},${JSON.stringify(requestId)}).count}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const outcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(outcome.status, 202, JSON.stringify(outcome));
  assert.equal(outcome.providerCalls, 0);
  assert.equal(outcome.body.reply, 'De opdracht is veilig bewaard en wordt vervolgd.');
  assert.equal(outcome.origins, 1);
  assert.equal(db.prepare(`SELECT count(*) count FROM eva_http_prepared_responses
    WHERE target_state='handoff' AND request_ref=(SELECT request_ref
      FROM eva_http_ingress_requests WHERE conversation_id=? AND request_id=?)`)
    .get(conversationId, requestId).count, 1);
  const replay = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1; throw new Error('provider mag handoff niet herhalen');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb(); const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)}}),
    });
    const body=await response.json(); await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js'); console.log(JSON.stringify({status:response.status,body,providerCalls,
      origins:db.prepare("SELECT count(*) count FROM eva_episode_origins WHERE origin_channel='web' AND origin_conversation_id=? AND origin_message_id=?").get(${JSON.stringify(conversationId)},${JSON.stringify(requestId)}).count}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(replay.status, 0, replay.stderr || replay.stdout);
  const replayOutcome = JSON.parse(replay.stdout.trim().split('\n').at(-1));
  assert.equal(replayOutcome.status, 202);
  assert.equal(replayOutcome.providerCalls, 0);
  assert.equal(replayOutcome.origins, 1);
  assert.deepEqual(replayOutcome.body, outcome.body);
});
