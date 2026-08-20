import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

import './voiceauthority-test-composition.js';
import { canonicalJson } from '../src/util.js';
const {
  VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL,
  VOICE_DELIVERY_AUTHORITY_PREDECESSOR_INDEX_SQL,
  VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL,
  VOICE_DELIVERY_AUTHORITY_TABLE,
  VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL,
  initializeVoiceDeliveryAuthorityStore,
  pendingAcknowledgedVoiceDeliveries,
  recordVoiceAckReceipt,
  recordVoiceDeliveredProjection,
  recordVoiceOfferIntent,
  recordVoiceQueryAuthority,
  voiceDeliveryAuthorityRecord,
  voiceDeliveryAuthorityStoreStatus,
  __voiceAuthorityStoreTest,
} = await import('../src/voiceauthority.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

test('de globale store-rijgrens geldt ook voor successor-generaties', () => {
  const successor = VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL
    .eva_voice_delivery_authority_events_v1_valid_successor;
  assert.match(successor, /SELECT CASE WHEN \(\s*SELECT count\(\*\) FROM main\.eva_voice_delivery_authority_events_v1\)>=200000/u);
  assert.doesNotMatch(successor, /NEW\.event_generation=1 AND \(\s*SELECT count\(\*\)/u);
});

function database(t, label) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-voice-store-${label}-`));
  const file = path.join(dir, 'authority.db');
  const candidate = new Database(file);
  candidate.exec(`CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    meta TEXT
  )`);
  t.after(() => {
    candidate.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return candidate;
}

function evidence(requestId = 'voice-store-1') {
  const adapterInstanceSha256 = sha256('voice-store-adapter');
  const frame = Buffer.from(canonicalJson({
    id: 71, type: 'event', event: {
      event_type: 'eva_voice_query',
      data: { id: requestId, vraag: 'Wat is de status?', satellite: 'assist_satellite.keuken' },
    },
  }));
  const sourceBodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256,
    socketGeneration: 7,
    eventType: 'eva_voice_query',
    frameByteLength: frame.length,
    frameSha256: sha256(frame),
    frameBase64: frame.toString('base64'),
    event: JSON.parse(frame.toString('utf8')),
  });
  const sourceSha256 = sha256(sourceBodyJson);
  return {
    requestId,
    conversationId: `voice:${requestId}`,
    requester: 'Jaap',
    satellite: 'assist_satellite.keuken',
    adapterInstanceSha256,
    socketGeneration: 7,
    sourceRef: `home-assistant-websocket:${sourceSha256}`,
    sourceSha256,
    sourceBodyJson,
  };
}

function offer(requestId = 'voice-store-1') {
  const query = evidence(requestId);
  const reply = 'De status is stabiel.';
  const payloadJson = canonicalJson({
    id: requestId, reply, ok: true, vraag: 'Wat is de status?',
    response_time_ms: 10, conversation_id: `voice:${requestId}`,
    satellite: 'assist_satellite.keuken',
  });
  const payloadSha256 = sha256(payloadJson);
  const replySha256 = sha256(reply);
  const operationSha256 = sha256(canonicalJson({
    contract: 'HomeAssistantVoiceReplyOperation/v1',
    requestId,
    satellite: query.satellite,
    adapterInstanceSha256: query.adapterInstanceSha256,
    socketGeneration: query.socketGeneration,
    querySourceRef: query.sourceRef,
    querySourceSha256: query.sourceSha256,
    payloadSha256,
    replySha256,
  }));
  return {
    requestId,
    turnId: `voice:${requestId}:${requestId}`,
    operationRef: `home-assistant-voice-reply:${operationSha256}`,
    operationSha256,
    payloadJson,
    payloadSha256,
    replySha256,
  };
}

function ack(requestId = 'voice-store-1') {
  const query = evidence(requestId);
  const offered = offer(requestId);
  const event = {
    id: 72, type: 'event', event: {
      event_type: 'eva_voice_reply_ack',
      data: {
        id: requestId, accepted: true, satellite: query.satellite,
        delivery_operation_ref: offered.operationRef,
        payload_sha256: offered.payloadSha256,
        reply_sha256: offered.replySha256,
        reply: 'De status is stabiel.', conversation_id: query.conversationId,
      },
    },
  };
  const frame = Buffer.from(canonicalJson(event));
  const sourceBodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256: query.adapterInstanceSha256,
    socketGeneration: query.socketGeneration,
    eventType: 'eva_voice_reply_ack',
    frameByteLength: frame.length,
    frameSha256: sha256(frame),
    frameBase64: frame.toString('base64'),
    event,
  });
  const sourceSha256 = sha256(sourceBodyJson);
  const sourceRef = `home-assistant-websocket:${sourceSha256}`;
  const deliveryJson = canonicalJson({
    accepted: true,
    transport: 'home-assistant-conversation-response',
    acknowledgement: 'eva_voice_reply_ack',
    satellite: query.satellite,
    socketGeneration: query.socketGeneration,
    authorityEvidence: {
      contract: 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2',
      requestId,
      satellite: query.satellite,
      query: {
        sourceRef: query.sourceRef,
        sourceSha256: query.sourceSha256,
        sourceBodyJson: query.sourceBodyJson,
      },
      offer: {
        operationRef: offered.operationRef,
        operationSha256: offered.operationSha256,
        payloadJson: offered.payloadJson,
        payloadSha256: offered.payloadSha256,
        replySha256: offered.replySha256,
      },
      acknowledgement: { sourceRef, sourceSha256, sourceBodyJson },
    },
  });
  return {
    requestId,
    sourceRef,
    sourceSha256,
    sourceBodyJson,
    deliveryJson,
    deliverySha256: sha256(deliveryJson),
  };
}

function delivered(requestId = 'voice-store-1') {
  const accepted = ack(requestId);
  const resultBodyJson = canonicalJson({
    contract: 'DeliverableTurnResult/v1', originChannel: 'voice',
    originConversationId: `voice:${requestId}`, originMessageId: requestId,
    turnId: `voice:${requestId}:${requestId}`, reply: 'De status is stabiel.',
    replySha256: sha256('De status is stabiel.'), sender: 'Jaap',
    goalEpisodeId: null, workItemId: null, finalDeliveryBindingSha256: null,
    syntheticTest: false, replayed: false, durableHandoff: false,
  });
  return {
    requestId,
    resultBodyJson,
    resultSha256: sha256(resultBodyJson),
    deliveryJson: accepted.deliveryJson,
    deliverySha256: accepted.deliverySha256,
  };
}

function authenticatedQuery(requestId = 'voice-store-1') {
  const input = evidence(requestId);
  return {
    requestId,
    conversationId: input.conversationId,
    requester: input.requester,
    satellite: input.satellite,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource(
      input, 'eva_voice_query',
    ),
  };
}

function authenticatedAck(requestId = 'voice-store-1') {
  const input = ack(requestId);
  return {
    requestId,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource(
      input, 'eva_voice_reply_ack',
    ),
    deliveryJson: input.deliveryJson,
    deliverySha256: input.deliverySha256,
  };
}

function insertExactChatOut(db, delivery) {
  db.prepare(`INSERT INTO main.timeline(kind,meta) VALUES ('chat_out',?)`).run(canonicalJson({
    turnId: JSON.parse(delivery.resultBodyJson).turnId,
    deliveredResultSha256: delivery.resultSha256,
    deliveredDeliverySha256: delivery.deliverySha256,
    deliveredAuthorityDeliverySha256: delivery.deliverySha256,
  }));
}

test('voice-store initialiseert atomair en weigert TEMP/lowercase namespace-drift', (t) => {
  const shadowed = database(t, 'shadow');
  shadowed.exec(`CREATE TEMP TABLE ${VOICE_DELIVERY_AUTHORITY_TABLE}(value TEXT)`);
  assert.throws(() => initializeVoiceDeliveryAuthorityStore(shadowed), /TEMP|namespace|shadow/iu);
  assert.equal(shadowed.prepare(`SELECT count(*) AS value FROM main.sqlite_master
    WHERE lower(name)=lower(?)`).get(VOICE_DELIVERY_AUTHORITY_TABLE).value, 0);

  const conflict = database(t, 'conflict');
  conflict.exec(`CREATE TABLE EVA_VOICE_DELIVERY_AUTHORITY_EVENTS_V1(value TEXT)`);
  assert.throws(() => initializeVoiceDeliveryAuthorityStore(conflict), /schema|namespace|inexact/iu);
  assert.equal(voiceDeliveryAuthorityStoreStatus(conflict).available, false);

  const rolledBack = database(t, 'create-rollback');
  rolledBack.exec('CREATE TABLE unrelated(value TEXT)');
  rolledBack.exec(`CREATE INDEX eva_voice_delivery_authority_events_v1_query_source_once
    ON unrelated(value)`);
  assert.throws(() => initializeVoiceDeliveryAuthorityStore(rolledBack), /already exists|index/iu);
  assert.equal(rolledBack.prepare(`SELECT count(*) AS value FROM main.sqlite_master
    WHERE type='table' AND name=?`).get(VOICE_DELIVERY_AUTHORITY_TABLE).value, 0,
  'mislukte init laat geen gedeeltelijke authoritytabel achter');
});

test('voice-store is exact append-only, OR REPLACE-safe en begrensd', (t) => {
  const db = database(t, 'exact');
  assert.equal(initializeVoiceDeliveryAuthorityStore(db).available, true);
  assert.equal(voiceDeliveryAuthorityStoreStatus(db).available, true);
  assert.match(
    VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL
      .eva_voice_delivery_authority_events_v1_valid_successor,
    /count\(\*\).*>=200000/su,
  );
  const forgedQuery = evidence();
  assert.throws(() => recordVoiceQueryAuthority(db, {
    requestId: forgedQuery.requestId,
    conversationId: forgedQuery.conversationId,
    requester: forgedQuery.requester,
    satellite: forgedQuery.satellite,
    authenticatedSource: Object.freeze({
      ...forgedQuery,
      eventType: 'eva_voice_query',
      data: JSON.parse(forgedQuery.sourceBodyJson).event.event.data,
      isCurrent: () => true,
    }),
  }), /private authenticated source-authority/iu);
  recordVoiceQueryAuthority(db, authenticatedQuery());
  recordVoiceOfferIntent(db, offer());
  const forgedAck = ack();
  assert.throws(() => recordVoiceAckReceipt(db, {
    requestId: forgedAck.requestId,
    authenticatedSource: Object.freeze({
      ...forgedAck,
      eventType: 'eva_voice_reply_ack',
      data: JSON.parse(forgedAck.sourceBodyJson).event.event.data,
      isCurrent: () => true,
    }),
    deliveryJson: forgedAck.deliveryJson,
    deliverySha256: forgedAck.deliverySha256,
  }), /private authenticated source-authority/iu);
  recordVoiceAckReceipt(db, authenticatedAck());
  assert.equal(pendingAcknowledgedVoiceDeliveries(db, 10).length, 1);
  const beforeDelivered = voiceDeliveryAuthorityRecord(db, 'voice-store-1');
  assert.equal(beforeDelivered.stage, 'acknowledged');
  assert.throws(() => db.transaction(() => {
    const projection = delivered();
    insertExactChatOut(db, projection);
    recordVoiceDeliveredProjection(db, projection);
    throw new Error('forced projection rollback');
  }).immediate(), /forced projection rollback/u);
  assert.equal(voiceDeliveryAuthorityRecord(db, 'voice-store-1').stage, 'acknowledged');
  const projection = delivered();
  assert.throws(() => recordVoiceDeliveredProjection(db, projection), /atomic chat_out/iu);
  db.transaction(() => {
    insertExactChatOut(db, projection);
    recordVoiceDeliveredProjection(db, projection);
  }).immediate();
  const exact = voiceDeliveryAuthorityRecord(db, 'voice-store-1');
  assert.equal(exact.stage, 'delivered');
  assert.equal(exact.events.length, 4);
  assert.equal(pendingAcknowledgedVoiceDeliveries(db, 10).length, 0);
  assert.throws(() => db.prepare(`UPDATE main.${VOICE_DELIVERY_AUTHORITY_TABLE}
    SET requester='Mallory' WHERE request_id=?`).run('voice-store-1'), /append-only/iu);
  assert.throws(() => db.prepare(`DELETE FROM main.${VOICE_DELIVERY_AUTHORITY_TABLE}
    WHERE request_id=?`).run('voice-store-1'), /append-only/iu);
  const row = db.prepare(`SELECT * FROM main.${VOICE_DELIVERY_AUTHORITY_TABLE}
    WHERE request_id=? AND event_generation=1`).get('voice-store-1');
  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.${VOICE_DELIVERY_AUTHORITY_TABLE} (
    ${Object.keys(row).join(',')}) VALUES (${Object.keys(row).map(() => '?').join(',')})`).run(
    ...Object.values(row),
  ), /replacement|append-only|already exists/iu);
  const oversized = evidence('voice-store-oversized');
  oversized.sourceBodyJson = canonicalJson({ body: 'x'.repeat(70_000) });
  oversized.sourceSha256 = sha256(oversized.sourceBodyJson);
  oversized.sourceRef = `home-assistant-websocket:${oversized.sourceSha256}`;
  assert.throws(() => __voiceAuthorityStoreTest.issueAuthenticatedSource(
    oversized, 'eva_voice_query',
  ), /groot|bytes|source|overschrijdt/iu);
});

test('alleen de lege exacte predecessor migreert', (t) => {
  const empty = database(t, 'predecessor-empty');
  empty.exec(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_INDEX_SQL)) empty.exec(sql);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL)) empty.exec(sql);
  assert.equal(initializeVoiceDeliveryAuthorityStore(empty).available, true);

  const populated = database(t, 'predecessor-populated');
  populated.exec(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_INDEX_SQL)) populated.exec(sql);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL)) populated.exec(sql);
  const source = evidence('voice-store-predecessor');
  const historical = {
    contract: 'VoiceDeliveryAuthorityEvent/v1',
    request_id: source.requestId,
    event_generation: 1,
    event_kind: 'query',
    conversation_id: source.conversationId,
    requester: source.requester,
    satellite: source.satellite,
    adapter_instance_sha256: source.adapterInstanceSha256,
    socket_generation: source.socketGeneration,
    query_source_ref: source.sourceRef,
    query_source_sha256: source.sourceSha256,
    query_source_body_json: source.sourceBodyJson,
    turn_id: null,
    offer_operation_ref: null,
    offer_operation_sha256: null,
    offer_payload_json: null,
    offer_payload_sha256: null,
    offer_reply_sha256: null,
    ack_source_ref: null,
    ack_source_sha256: null,
    ack_source_body_json: null,
    result_body_json: null,
    result_sha256: null,
    predecessor_sha256: null,
    event_sha256: '0'.repeat(64),
    issued_ts: '2026-08-13T12:00:00.000Z',
  };
  populated.prepare(`INSERT INTO main.${VOICE_DELIVERY_AUTHORITY_TABLE} (
    ${Object.keys(historical).join(',')}) VALUES (${Object.keys(historical).map(() => '?').join(',')})`)
    .run(...Object.values(historical));
  assert.throws(() => initializeVoiceDeliveryAuthorityStore(populated), /predecessor|rijen/iu);
});
