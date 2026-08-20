import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-ingress-v3-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_HTTP_INGRESS_CLAIM_LEASE_MS = '100';

const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);
const { canonicalHttpWireSha256 } = await import('../src/web/httpack.js');
const ingress = await import('../src/web/httpingress.js');
const responseAuthority = await import('../src/web/httpresponseauthority.js');
const {
  beginHttpIngress,
  canonicalHttpIntentSha256,
  issueVerifiedHttpResultAuthority,
  prepareHttpResponse,
  settleHttpIngress,
} = ingress;
const {
  consumeHttpResponseAuthority,
  httpResponseAuthorityCurrent,
  httpResponseAuthorityStoreStatus,
  issueHttpResponseAuthority,
} = responseAuthority;

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
let sequence = 0;

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function keyPair() {
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

function insertOrReplaceRow(table, row) {
  const columns = Object.keys(row);
  return db.prepare(`INSERT OR REPLACE INTO main.${table} (
    ${columns.join(',')}
  ) VALUES (${columns.map((column) => `@${column}`).join(',')})`).run(row);
}

function withoutTrigger(name, callback) {
  db.exec('BEGIN IMMEDIATE');
  try {
    db.exec(`DROP TRIGGER main.${name}`);
    callback();
  } finally {
    db.exec('ROLLBACK');
  }
}

function start(label, { keys = keyPair(), withKey = true } = {}) {
  sequence += 1;
  const conversationId = `web:authority:v3:${label}:${sequence}`;
  const requestId = `http-request:v3:${label}:${sequence}`;
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: `bericht ${label}`, conversationId, syntheticTest: false,
    ...(withKey ? { clientAckPublicKey: keys.publicKey } : {}),
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  return { begun, conversationId, requestId, intent, keys };
}

function storeUnderstandingResult(started, overrides = {}) {
  const turnId = overrides.turnId || `turn:http:v3:${sequence}`;
  const runtimeResult = {
    channel: 'web', conversationId: started.conversationId,
    originMessageId: started.requestId, turnId,
    reply: overrides.reply ?? `antwoord ${sequence}`,
    syntheticTest: false,
    ...(overrides.error ? { error: overrides.error } : {}),
    ...(overrides.superseded ? { superseded: true } : {}),
    ...(overrides.durableHandoff ? {
      durableHandoff: true, executionPending: true,
    } : {}),
  };
  const metadata = {};
  for (const key of ['durableHandoff', 'executionPending', 'superseded']) {
    if (typeof runtimeResult[key] === 'boolean') metadata[key] = runtimeResult[key];
  }
  const body = {
    contract: 'PreparedUnderstandingTurn/v1', channel: runtimeResult.channel,
    conversationId: runtimeResult.conversationId,
    originMessageId: runtimeResult.originMessageId, turnId: runtimeResult.turnId,
    reply: runtimeResult.reply, replySha256: sha256(runtimeResult.reply),
    workItemId: null, goalEpisodeId: null, finalDeliveryBindingSha256: null,
    syntheticTest: false, error: runtimeResult.error || null, metadata,
  };
  const prepared = { ...body, sha256: sha256(canonicalJson(body)) };
  seedAuthorizedTurnUnderstanding({
    database: db, channel: 'web', conversationId: started.conversationId,
    messageId: started.requestId, requester: started.intent.sender,
    originalText: started.intent.rawText.trim(), output: prepared,
  });
  return runtimeResult;
}

function durableResponse(label, overrides = {}) {
  const started = start(label, overrides);
  assert.equal(started.begun.kind, 'started');
  const runtimeResult = storeUnderstandingResult(started, {
    turnId: overrides.turnId,
    reply: overrides.reply ?? overrides.body?.reply ?? `antwoord ${label}`,
    error: overrides.error,
    superseded: overrides.superseded,
    durableHandoff: overrides.durableHandoff,
  });
  const resultAuthority = issueVerifiedHttpResultAuthority(
    runtimeResult, started.begun.preparationAuthority,
  );
  const prepared = prepareHttpResponse(started.begun.preparationAuthority, resultAuthority);
  const settled = settleHttpIngress(prepared.settlementAuthority);
  assert.equal(settled.kind, 'settled');
  return {
    response: settled.response, responseAuthority: settled.responseAuthority, ...started,
  };
}

function insertLegacyTerminalResponse(label) {
  sequence += 1;
  const channel = 'web';
  const conversationId = `web:legacy:v2:${label}:${sequence}`;
  const requestId = `http-request:legacy:v2:${label}:${sequence}`;
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: `legacy ${label}`, conversationId, syntheticTest: false,
  };
  const intentJson = canonicalJson(intent);
  const intentSha256 = sha256(intentJson);
  const requestRef = `http-ingress:${sha256(canonicalJson({
    channel, conversationId, requestId,
  }))}`;
  const startedAt = new Date().toISOString();
  const inserted = db.prepare(`INSERT INTO main.eva_http_ingress_requests (
    contract,request_ref,channel,conversation_id,request_id,intent_sha256,
    intent_bytes,started_ts
  ) VALUES (?,?,?,?,?,?,?,?)`).run(
    'HttpIngressRequest/v1', requestRef, channel, conversationId, requestId,
    intentSha256, Buffer.byteLength(intentJson), startedAt,
  );
  const requestRowId = Number(inserted.lastInsertRowid);
  const startedBody = {
    contract: 'HttpIngressStateEvent/v1', ordinal: 0,
    requestRef, responseRef: null, state: 'started',
  };
  const startedJson = canonicalJson(startedBody);
  const startedSha256 = sha256(startedJson);
  db.prepare(`INSERT INTO main.eva_http_ingress_state_events (
    contract,event_ref,event_sha256,request_row_id,request_ref,ordinal,state,
    response_ref,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    startedBody.contract, `http-ingress-event:${startedSha256}`, startedSha256,
    requestRowId, requestRef, 0, 'started', null, startedJson, startedAt,
  );
  const turnId = `turn:http:legacy:${label}:${sequence}`;
  const publicBody = { ok: true, reply: `legacy antwoord ${label}` };
  const publicBodyJson = canonicalJson(publicBody);
  const provenance = {
    contract: 'HttpResultProvenance/v1', kind: 'turn_result', ref: turnId,
    sha256: sha256(`legacy-provenance:${label}:${sequence}`),
  };
  const responseBody = {
    contract: 'PreparedHttpResponse/v1', channel, conversationId, intentSha256,
    provenance, publicBody, requestId, state: 'prepared', status: 200, turnId,
  };
  const responseJson = canonicalJson(responseBody);
  const responseSha256 = sha256(responseJson);
  const responseRef = `http-response:${responseSha256}`;
  const terminalBody = {
    contract: 'HttpIngressStateEvent/v1', ordinal: 1,
    requestRef, responseRef, state: 'prepared',
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
    responseBody.contract, responseRef, responseSha256, requestRowId, requestRef,
    'prepared', 200, turnId, publicBodyJson, sha256(publicBodyJson),
    provenance.kind, provenance.ref, provenance.sha256, responseJson, startedAt,
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
      requestRowId, requestRef, 1, 'prepared', responseRef, terminalJson, startedAt,
    );
    db.exec(triggerSql);
  }).immediate();
  return { channel, conversationId, requestId, intent };
}

function insertLegacyStartedRequest(label) {
  sequence += 1;
  const channel = 'web';
  const conversationId = `web:legacy-started:v2:${label}:${sequence}`;
  const requestId = `http-request:legacy-started:v2:${label}:${sequence}`;
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: `legacy started ${label}`, conversationId, syntheticTest: false,
  };
  const intentJson = canonicalJson(intent);
  const intentSha256 = sha256(intentJson);
  const requestRef = `http-ingress:${sha256(canonicalJson({
    channel, conversationId, requestId,
  }))}`;
  const startedAt = new Date().toISOString();
  const inserted = db.prepare(`INSERT INTO main.eva_http_ingress_requests (
    contract,request_ref,channel,conversation_id,request_id,intent_sha256,
    intent_bytes,started_ts
  ) VALUES (?,?,?,?,?,?,?,?)`).run(
    'HttpIngressRequest/v1', requestRef, channel, conversationId, requestId,
    intentSha256, Buffer.byteLength(intentJson), startedAt,
  );
  const startedBody = {
    contract: 'HttpIngressStateEvent/v1', ordinal: 0,
    requestRef, responseRef: null, state: 'started',
  };
  const eventJson = canonicalJson(startedBody);
  const eventSha256 = sha256(eventJson);
  db.prepare(`INSERT INTO main.eva_http_ingress_state_events (
    contract,event_ref,event_sha256,request_row_id,request_ref,ordinal,state,
    response_ref,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    startedBody.contract, `http-ingress-event:${eventSha256}`, eventSha256,
    Number(inserted.lastInsertRowid), requestRef, 0, 'started', null,
    eventJson, startedAt,
  );
  return { channel, conversationId, requestId, intent };
}

test('ingress en responseauthority hebben gescheiden publieke grenzen', () => {
  for (const name of [
    'beginHttpIngress', 'canonicalHttpIntentSha256', 'httpIngressStoreStatus',
    'issueVerifiedHttpResultAuthority', 'prepareHttpResponse', 'settleHttpIngress',
  ]) assert.equal(typeof ingress[name], 'function', name);
  for (const name of [
    'registerHttpResultVerifier', 'issueHttpResponseAuthority',
    'consumeHttpResponseAuthority',
  ]) assert.equal(typeof ingress[name], 'undefined', name);
  assert.deepEqual(httpResponseAuthorityStoreStatus(), {
    contract: 'HttpResponseAuthorityStoreStatus/v3', available: true,
  });
  assert.deepEqual(db.prepare(`SELECT name FROM pragma_function_list
    WHERE name GLOB 'eva_http_response*' OR name GLOB 'eva_http_delivery*'`).all(), []);
});

test('de P-256 sleutel en challenge zijn vóór uitvoering exact en append-only duurzaam', () => {
  const started = start('before-execution');
  assert.equal(started.begun.kind, 'started');
  assert.ok(Date.parse(started.begun.claim.leaseUntil) - Date.now() >= 29_000);
  const request = db.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE conversation_id=? AND request_id=?`).get(started.conversationId, started.requestId);
  const binding = db.prepare(`SELECT * FROM main.eva_http_client_ack_bindings_v3
    WHERE request_row_id=?`).get(request.id);
  assert.equal(binding.request_ref, request.request_ref);
  assert.equal(binding.intent_sha256, canonicalHttpIntentSha256(started.intent));
  assert.equal(JSON.parse(binding.intent_json).clientAckPublicKey.x,
    started.keys.publicKey.x);
  assert.equal(sha256(binding.ack_challenge), binding.ack_challenge_sha256);
  assert.throws(() => db.prepare(`UPDATE main.eva_http_client_ack_bindings_v3
    SET ack_challenge=ack_challenge WHERE id=?`).run(binding.id), /append-only/u);
  assert.throws(() => db.prepare(`DELETE FROM main.eva_http_client_ack_bindings_v3
    WHERE id=?`).run(binding.id), /append-only/u);
  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.eva_http_client_ack_bindings_v3
    SELECT * FROM main.eva_http_client_ack_bindings_v3 WHERE id=?`).run(binding.id),
  /collision|append-only/u);
});

test('authority bezit stabiele wire-bytes met de clientchallenge en is one-shot', () => {
  const durable = durableResponse('canonical', { reply: 'één' });
  assert.throws(() => issueHttpResponseAuthority(durable.response), /opaque|authority/u);
  assert.throws(() => issueHttpResponseAuthority({
    ...durable.response, publicBody: { ok: true, reply: 'vervalst' },
  }), /opaque|authority/u);
  const issued = issueHttpResponseAuthority(durable.responseAuthority);
  assert.equal(issued.contract, 'IssuedHttpResponseAuthority/v3');
  assert.equal(httpResponseAuthorityCurrent(issued.authority), true);
  assert.equal(Object.hasOwn(issued.response, 'clientPublicKey'), false);
  const consumed = consumeHttpResponseAuthority(issued.authority);
  assert.equal(httpResponseAuthorityCurrent(issued.authority), false);
  const body = JSON.parse(consumed.wire.bodyBytes.toString('utf8'));
  assert.deepEqual({ ok: body.ok, reply: body.reply }, { ok: true, reply: 'één' });
  assert.equal(body._httpDelivery.contract, 'HttpClientAckChallenge/v3');
  assert.equal(body._httpDelivery.requestRef, consumed.response.requestRef);
  assert.equal(body._httpDelivery.responseRef, consumed.response.responseRef);
  assert.equal(body._httpDelivery.deliveryRef, consumed.response.deliveryRef);
  assert.equal(consumed.wire.wireSha256, canonicalHttpWireSha256({
    status: consumed.wire.status, contentType: consumed.wire.contentType,
    bodyBytes: consumed.wire.bodyBytes,
  }));
  assert.notEqual(consumed.wire.wireSha256, canonicalHttpWireSha256({
    status: consumed.wire.status === 599 ? 598 : 599,
    contentType: consumed.wire.contentType, bodyBytes: consumed.wire.bodyBytes,
  }));
  assert.throws(() => consumeHttpResponseAuthority(issued.authority), /one-shot/u);

  const replayIngress = beginHttpIngress({
    channel: 'web', conversationId: durable.conversationId,
    requestId: durable.requestId, intent: durable.intent,
  });
  assert.equal(replayIngress.kind, 'replay');
  const replay = issueHttpResponseAuthority(replayIngress.responseAuthority);
  assert.equal(replay.response.authoritySha256, issued.response.authoritySha256);
  assert.equal(replay.response.deliveryRef, issued.response.deliveryRef);
});

test('publieke duurzame fouttekst bevat geen lange dash en bewaart een negatief getal', () => {
  const minusSign = String.fromCodePoint(0x2212);
  const durable = durableResponse('public-error-normalization', {
    reply: '',
    error: `Sensorwaarde ${minusSign}5 °C — resultaat nog niet bevestigd.`,
  });
  const issued = issueHttpResponseAuthority(durable.responseAuthority);
  const consumed = consumeHttpResponseAuthority(issued.authority);
  const body = JSON.parse(consumed.wire.bodyBytes.toString('utf8'));
  assert.equal(body.ok, false);
  assert.equal(body.error, 'Sensorwaarde -5 °C, resultaat nog niet bevestigd.');
  assert.equal(body.error.includes(String.fromCodePoint(0x2013)), false);
  assert.equal(body.error.includes(String.fromCodePoint(0x2014)), false);

  const dashOnly = durableResponse('public-error-empty-after-normalization', {
    reply: '',
    error: ' — ',
  });
  const dashOnlyIssued = issueHttpResponseAuthority(dashOnly.responseAuthority);
  const dashOnlyConsumed = consumeHttpResponseAuthority(dashOnlyIssued.authority);
  const dashOnlyBody = JSON.parse(dashOnlyConsumed.wire.bodyBytes.toString('utf8'));
  assert.equal(dashOnlyBody.ok, false);
  assert.equal(dashOnlyBody.error, 'onbekende fout');
});

test('een verse intent zonder sleutel faalt vóór request en execution-claim', () => {
  const beforeRequests = db.prepare('SELECT count(*) AS count FROM eva_http_ingress_requests')
    .get().count;
  const beforeClaims = db.prepare('SELECT count(*) AS count FROM eva_http_ingress_claim_events')
    .get().count;
  const started = start('legacy-no-key', { withKey: false });
  assert.deepEqual(started.begun, {
    kind: 'rejected', state: 'rejected', reason: 'client_ack_key_required',
  });
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_http_ingress_requests')
    .get().count, beforeRequests);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_http_ingress_claim_events')
    .get().count, beforeClaims);
});

test('huidige response-writeschema accepteert uitsluitend geattesteerde snake_case API-identiteit', () => {
  const sql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='eva_http_prepared_responses_valid_reference'`).get().sql;
  const publicBodyRule = sql.slice(sql.indexOf(
    '(SELECT count(*) FROM json_each(NEW.public_body_json))',
  ));
  for (const key of [
    'conversation_id', 'turn_id', 'request_id', 'request_replay_scope',
    'runtime_code_sha256',
  ]) assert.match(publicBodyRule, new RegExp(`'${key}'`, 'u'));
  for (const legacyKey of [
    'conversationId', 'turnId', 'requestId', 'requestReplayScope',
    'runtimeCodeSha256', 'conversationActive', 'continueListening',
    'listenTimeoutMs', 'executionPending', 'durableHandoff',
  ]) assert.doesNotMatch(publicBodyRule, new RegExp(`'${legacyKey}'`, 'u'));
  assert.match(publicBodyRule, /json_type\(NEW\.public_body_json,'\$\.conversation_id'\) IS NOT 'text'/u);
  assert.match(publicBodyRule, /json_extract\(NEW\.public_body_json,'\$\.request_id'\)/u);
  assert.match(publicBodyRule, /json_type\(NEW\.public_body_json,'\$\.runtime_code_sha256'\) IS NOT 'text'/u);
  db.exec('BEGIN IMMEDIATE');
  try {
    db.exec('CREATE TABLE eva_http_ingress_unexpected_namespace (id INTEGER)');
    const drifted = ingress.httpIngressStoreStatus();
    assert.equal(drifted.available, false);
    assert.equal(drifted.reason, 'http_ingress_schema_namespace_invalid');
  } finally { db.exec('ROLLBACK'); }
  assert.equal(ingress.httpIngressStoreStatus().available, true);
});

test('een exacte pre-v3 terminale no-key rij replayt alleen legacy_unacknowledged', () => {
  const legacy = insertLegacyTerminalResponse('terminal');
  const replay = beginHttpIngress(legacy);
  assert.equal(replay.kind, 'legacy_unacknowledged');
  assert.equal(replay.state, 'prepared');
  assert.equal(replay.delivered, false);
  assert.equal(replay.reason, 'legacy_missing_client_ack_key');
  assert.equal(Object.hasOwn(replay, 'responseAuthority'), false);
  assert.throws(() => issueHttpResponseAuthority(replay.response), /opaque|authority/u);
  assert.equal(db.prepare(`SELECT count(*) AS count
    FROM main.eva_http_response_authorities_v3 WHERE request_ref=?`)
    .get(replay.request.requestRef).count, 0);
});

test('een pre-v3 started no-key rij krijgt nooit alsnog een execution-claim', () => {
  const legacy = insertLegacyStartedRequest('open');
  const claimsBefore = db.prepare(`SELECT count(*) AS count
    FROM main.eva_http_ingress_claim_events`).get().count;
  const replay = beginHttpIngress(legacy);
  assert.equal(replay.kind, 'legacy_unacknowledged');
  assert.equal(replay.state, 'started');
  assert.equal(replay.delivered, false);
  assert.equal(Object.hasOwn(replay, 'preparationAuthority'), false);
  assert.equal(Object.hasOwn(replay, 'settlementAuthority'), false);
  assert.equal(db.prepare(`SELECT count(*) AS count
    FROM main.eva_http_ingress_claim_events`).get().count, claimsBefore);

  const keys = keyPair();
  const requestId = `${legacy.requestId}:v3`;
  const intent = {
    ...legacy.intent, rawText: 'nieuwe client-bound request',
    clientAckPublicKey: keys.publicKey,
  };
  const next = beginHttpIngress({
    channel: 'web', conversationId: legacy.conversationId, requestId, intent,
  });
  assert.equal(next.kind, 'started');
  assert.equal(Object.hasOwn(next, 'preparationAuthority'), true);
});

test('ongeldige of uitgebreide clientsleutels falen vóór een requestrij ontstaat', () => {
  const good = keyPair().publicKey;
  const before = db.prepare('SELECT count(*) AS count FROM eva_http_ingress_requests')
    .get().count;
  for (const [index, publicKey] of [
    { ...good, x: 'A'.repeat(43) },
    { ...good, algorithm: 'EdDSA' },
    { ...good, privateMaterial: 'verboden' },
    { ...good, crv: 'P-384' },
  ].entries()) {
    sequence += 1;
    const conversationId = `web:bad-key:${sequence}`;
    const requestId = `http-request:bad-key:${sequence}:${index}`;
    const intent = {
      contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
      rawText: 'ongeldig', conversationId, syntheticTest: false,
      clientAckPublicKey: publicKey,
    };
    assert.throws(() => beginHttpIngress({
      channel: 'web', conversationId, requestId, intent,
    }), /public key|P-256|canonical/u);
  }
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_http_ingress_requests')
    .get().count, before);
});

test('null-turn supersession is client-bound; turn-provenance met null blijft verboden', () => {
  const nullDurable = durableResponse('null', { superseded: true });
  const valid = issueHttpResponseAuthority(nullDurable.responseAuthority);
  const consumed = consumeHttpResponseAuthority(valid.authority);
  assert.equal(consumed.response.turnId, null);
  assert.equal(consumed.response.provenance.kind, 'request_supersession');

  const other = durableResponse('null-other', { superseded: true });
  assert.throws(() => issueHttpResponseAuthority(other.response), /opaque|authority/u);
});

test('v3 authority-schema is main/TEMP/autoindex/OR REPLACE-safe', () => {
  const guarded = durableResponse('guards');
  const issued = issueHttpResponseAuthority(guarded.responseAuthority);
  const row = db.prepare(`SELECT * FROM main.eva_http_response_authorities_v3
    WHERE authority_ref=?`).get(issued.response.authorityRef);
  assert.throws(() => db.prepare(`UPDATE main.eva_http_response_authorities_v3
    SET status=201 WHERE id=?`).run(row.id), /append-only/u);
  assert.throws(() => db.prepare(`DELETE FROM main.eva_http_response_authorities_v3
    WHERE id=?`).run(row.id), /append-only/u);
  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.eva_http_response_authorities_v3
    SELECT * FROM main.eva_http_response_authorities_v3 WHERE id=?`).run(row.id),
  /collision|append-only/u);
  db.exec('CREATE TEMP VIEW eva_http_response_authorities_v3 AS SELECT 1 AS id');
  assert.equal(httpResponseAuthorityStoreStatus().available, false);
  db.exec('DROP VIEW temp.eva_http_response_authorities_v3');
  db.exec(`CREATE TEMP TRIGGER arbitrary_temp_authority_blocker
    BEFORE INSERT ON main.eva_http_response_authorities_v3 BEGIN SELECT RAISE(IGNORE); END`);
  assert.equal(httpResponseAuthorityStoreStatus().available, false);
  db.exec('DROP TRIGGER temp.arbitrary_temp_authority_blocker');
  db.exec(`CREATE INDEX unexpected_http_response_authority_v3
    ON eva_http_response_authorities_v3(status)`);
  assert.equal(httpResponseAuthorityStoreStatus().available, false);
  db.exec('DROP INDEX unexpected_http_response_authority_v3');
  assert.equal(httpResponseAuthorityStoreStatus().available, true);
});

test('authority OR REPLACE bewaart de oude rij op elke challenge-UNIQUE-identiteit', () => {
  const guarded = durableResponse('authority-unique-guards');
  const issued = issueHttpResponseAuthority(guarded.responseAuthority);
  const table = 'eva_http_response_authorities_v3';
  const row = db.prepare(`SELECT * FROM main.${table} WHERE authority_ref=?`)
    .get(issued.response.authorityRef);
  const collisionSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='eva_http_response_authorities_v3_no_replace'`)
    .get().sql;
  assert.match(collisionSql, /ack_challenge=NEW\.ack_challenge/u);
  assert.match(collisionSql, /ack_challenge_sha256=NEW\.ack_challenge_sha256/u);
  const changed = sha256('changed-authority-unique-identities');
  const base = {
    ...row, id: Number(row.id) + 1_000_000,
    authority_ref: `http-response-authority-v3:${changed}`,
    authority_sha256: changed, request_ref: `http-ingress:${changed}`,
    response_ref: `http-response:${changed}`, response_sha256: changed,
    delivery_ref: `http-delivery-v3:${changed}`, wire_sha256: changed,
    response_json: '{"changed":true}',
  };
  withoutTrigger('eva_http_response_authorities_v3_valid_ingress_response', () => {
    for (const candidate of [
      { ...base, ack_challenge: row.ack_challenge, ack_challenge_sha256: changed },
      { ...base, ack_challenge: changed, ack_challenge_sha256: row.ack_challenge_sha256 },
    ]) {
      assert.throws(() => insertOrReplaceRow(table, candidate),
        /HTTP response authority v3 identity collision/u);
      assert.deepEqual(db.prepare(`SELECT * FROM main.${table} WHERE id=?`).get(row.id), row);
    }
  });
  assert.deepEqual(db.prepare(`SELECT * FROM main.${table} WHERE id=?`).get(row.id), row);
});

test('opaque response-authority faalt gesloten bij ingress dependency-drift', () => {
  const durable = durableResponse('dependency-drift');
  db.exec(`CREATE INDEX unexpected_http_ingress_dependency_v3
    ON eva_http_ingress_requests(started_ts)`);
  assert.equal(ingress.httpIngressStoreStatus().available, false);
  assert.throws(() => issueHttpResponseAuthority(durable.responseAuthority),
    /ingress.unavailable|index_signatures_invalid/u);
  db.exec('DROP INDEX unexpected_http_ingress_dependency_v3');
  assert.equal(ingress.httpIngressStoreStatus().available, true);
  assert.equal(issueHttpResponseAuthority(durable.responseAuthority).kind, 'issued');

  db.exec(`CREATE TRIGGER unexpected_http_execution_dependency
    BEFORE INSERT ON main.turn_tool_executions BEGIN SELECT 1; END`);
  const executionDrift = ingress.httpIngressStoreStatus();
  assert.equal(executionDrift.available, false);
  assert.match(executionDrift.reason, /^http_ingress_dependency_invalid:execution_ledger:/u);
  db.exec('DROP TRIGGER unexpected_http_execution_dependency');
  assert.equal(ingress.httpIngressStoreStatus().available, true);

  const replay = beginHttpIngress({
    channel: 'web', conversationId: durable.conversationId,
    requestId: durable.requestId, intent: durable.intent,
  });
  assert.equal(replay.kind, 'replay');
  db.exec(`CREATE TEMP TRIGGER arbitrary_ingress_dependency_blocker
    BEFORE INSERT ON main.eva_http_ingress_requests BEGIN SELECT RAISE(IGNORE); END`);
  assert.equal(ingress.httpIngressStoreStatus().available, false);
  assert.throws(() => issueHttpResponseAuthority(replay.responseAuthority),
    /ingress store unavailable|temp_shadow/u);
  db.exec('DROP TRIGGER temp.arbitrary_ingress_dependency_blocker');
  const finalReplay = beginHttpIngress({
    channel: 'web', conversationId: durable.conversationId,
    requestId: durable.requestId, intent: durable.intent,
  });
  assert.equal(issueHttpResponseAuthority(finalReplay.responseAuthority).kind, 'issued');
});
