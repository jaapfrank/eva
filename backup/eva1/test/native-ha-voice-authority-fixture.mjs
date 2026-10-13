import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { WebSocketServer } from 'ws';

import './voicebridge-test-composition.js';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-native-ha-voice-'));
const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
await new Promise((resolve, reject) => {
  server.once('listening', resolve);
  server.once('error', reject);
});
const port = server.address().port;
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-ha-voice-test-only';
process.env.HA_URL = `http://127.0.0.1:${port}`;
process.env.HA_TOKEN = 'native-ha-websocket-token';

const scenarios = Object.freeze({
  preauth: Object.freeze({
    id: 'native-ha-preauth-1',
    text: 'Deze pre-auth voicebeurt mag nooit starten.',
    reply: 'Dit pre-auth antwoord mag nooit bestaan.',
  }),
  publicBus: Object.freeze({
    id: 'native-ha-public-bus-1',
    text: 'Deze publieke busbeurt mag nooit starten.',
    reply: 'Dit publieke busantwoord mag nooit bestaan.',
  }),
  wrongSubscription: Object.freeze({
    id: 'native-ha-wrong-subscription-1',
    text: 'Een event op het verkeerde subscription-id mag nooit starten.',
    reply: 'Dit antwoord mag nooit bestaan.',
  }),
  missingAckBinding: Object.freeze({
    id: 'native-ha-missing-ack-binding-1',
    text: 'Bereid een antwoord voor maar accepteer geen ongebonden ACK.',
    reply: 'Dit antwoord blijft onbevestigd zonder exacte ACK-binding.',
  }),
  tamperedAck: Object.freeze({
    id: 'native-ha-tampered-ack-1',
    text: 'Bereid een antwoord voor maar accepteer geen gewijzigde ACK.',
    reply: 'Dit antwoord blijft onbevestigd bij gewijzigde bytes.',
  }),
  crossGeneration: Object.freeze({
    id: 'native-ha-cross-generation-1',
    text: 'Bereid dit antwoord op de huidige socket voor.',
    reply: 'Een ACK van een nieuwe socketgeneratie mag dit niet bevestigen.',
  }),
  authentic: Object.freeze({
    id: 'native-ha-authentic-1',
    text: 'Beantwoord precies deze authentieke HA-voicebeurt.',
    reply: 'Dit antwoord hoort uitsluitend bij de authentieke HA-socket.',
  }),
  rollback: Object.freeze({
    id: 'native-ha-rollback-1',
    text: 'Bezorg dit antwoord één keer ondanks een lokale transactierollback.',
    reply: 'Dit Voice-antwoord krijgt exact één duurzame ontvangstregistratie.',
  }),
  ackRollback: Object.freeze({
    id: 'native-ha-ack-rollback-1',
    text: 'Bewaar dezelfde fysieke ACK na een lokale gen3-rollback.',
    reply: 'Deze ene ACK wordt na rollback zonder tweede offer duurzaam verwerkt.',
  }),
  drainBlocked: Object.freeze({
    id: 'native-ha-drain-blocked-1',
    text: 'Laat deze lokale projectie tijdelijk falen.',
    reply: 'Deze ACK blijft duurzaam klaarstaan voor lokale retry.',
  }),
  drainGood: Object.freeze({
    id: 'native-ha-drain-good-1',
    text: 'Bezorg deze ACK ondanks een oudere geblokkeerde rij.',
    reply: 'Deze jongere ACK passeert de geblokkeerde rij zonder netwerk.',
  }),
  stale: Object.freeze({
    id: 'native-ha-stale-socket-1',
    text: 'Deze beurt wordt tijdens het begrijpen stale.',
    reply: 'Een stale socket mag dit antwoord nooit aanbieden.',
  }),
});
const satellite = 'assist_satellite.keuken';

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(text, reply, callId) {
  return completedModelResponse([{
    type: 'function_call', call_id: callId,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Een exact antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }]);
}

function replyResponse(reply) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: reply }],
  }]);
}

function voiceEvent(scenario, eventType = 'eva_voice_query', extra = {}, subscriptionId = 902) {
  return JSON.stringify({
    id: subscriptionId,
    type: 'event',
    event: {
      event_type: eventType,
      time_fired: '2026-08-13T12:00:00.000Z',
      origin: 'LOCAL',
      data: eventType === 'eva_voice_query' ? {
        id: scenario.id,
        vraag: scenario.text,
        sender: 'Jaap',
        speaker_id: 'jaap',
        speaker_identity_verified: true,
        satellite,
        conversation_id: scenario.id,
        ...extra,
      } : {
        id: scenario.id,
        accepted: true,
        satellite,
        ...extra,
      },
    },
  });
}

const sockets = [];
let readySocket = null;
let readySubscriptionIds = null;
let readyResolve;
const ready = new Promise((resolve) => { readyResolve = resolve; });
server.on('connection', (socket) => {
  sockets.push(socket);
  // Dit frame komt bewust vóór auth_required/auth_ok en vóór subscriptions.
  socket.send(voiceEvent(scenarios.preauth));
  socket.send(JSON.stringify({ type: 'auth_required', ha_version: '2026.8.0' }));
  const subscriptions = new Map();
  let statesRead = false;
  socket.on('message', (raw) => {
    const message = JSON.parse(raw.toString());
    if (message.type === 'auth') {
      socket.send(JSON.stringify({ type: 'auth_ok', ha_version: '2026.8.0' }));
      return;
    }
    if (message.type === 'subscribe_events') {
      subscriptions.set(message.event_type, message.id);
      socket.send(JSON.stringify({ id: message.id, type: 'result', success: true, result: null }));
    } else if (message.type === 'get_states') {
      statesRead = true;
      socket.send(JSON.stringify({ id: message.id, type: 'result', success: true, result: [] }));
    }
    if (statesRead && ['state_changed', 'eva_voice_query', 'eva_voice_reply_ack']
      .every((eventType) => subscriptions.has(eventType))) {
      readySocket = socket;
      readySubscriptionIds = Object.freeze(Object.fromEntries(subscriptions));
      readyResolve(socket);
    }
  });
});

const { bus } = await import('../src/bus.js');
const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const { connectHA, haConnected } = await import('../src/integrations/homeassistant.js');
const {
  __voiceBridgeTest,
  startVoiceBridge,
} = await import('../src/mind/voicebridge.js');
const { voiceDeliveryAuthorityRecord } = await import('../src/voiceauthority.js');

const providerCalls = new Map(Object.keys(scenarios).map((key) => [key, 0]));
const providerConnectionStates = new Map(Object.keys(scenarios).map((key) => [key, []]));
const offers = [];
const ackFramesSent = new Map();
const offerBodies = new Map();
let staleUnderstandingResolve;
const staleUnderstanding = new Promise((resolve) => { staleUnderstandingResolve = resolve; });
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  if (String(url).includes('/api/events/eva_voice_reply')) {
    const body = JSON.parse(String(options.body || '{}'));
    offers.push(String(body.id || ''));
    offerBodies.set(String(body.id || ''), body);
    return new Response('{}', { status: 200 });
  }
  const body = String(options.body || '');
  const entry = Object.entries(scenarios).find(([, scenario]) => body.includes(scenario.text));
  assert.ok(entry, `onbekende providercall: ${body.slice(0, 200)}`);
  const [key, scenario] = entry;
  const count = (providerCalls.get(key) || 0) + 1;
  providerCalls.set(key, count);
  providerConnectionStates.get(key).push(haConnected());
  if (key === 'stale' && count === 1) return staleUnderstanding;
  return count === 1
    ? understandingResponse(scenario.text, scenario.reply, `native-ha-${key}-understanding`)
    : replyResponse(scenario.reply);
};

const waitFor = async (predicate, label, timeoutMs = 4_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timeout waiting for ${label}`);
};
const rowFor = (id) => db.prepare(`SELECT * FROM main.turn_tool_executions
  WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(id) || null;
const deliveryCount = (id) => Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
  WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`).get(id).value);
const rawVoiceStage = (id) => ['missing', 'query', 'offered', 'acknowledged', 'delivered'][
  Number(db.prepare(`SELECT coalesce(max(event_generation),0) AS value
    FROM main.eva_voice_delivery_authority_events_v1 WHERE request_id=?`).get(id).value)
];
const voiceGenerationCount = (id, generation) => Number(db.prepare(`SELECT count(*) AS value
  FROM main.eva_voice_delivery_authority_events_v1
  WHERE request_id=? AND event_generation=?`).get(id, generation).value);
const deliveryAuthority = (id) => {
  const row = db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?
    ORDER BY id DESC LIMIT 1`).get(id);
  return row ? JSON.parse(row.meta)?.delivery?.authorityEvidence || null : null;
};
const durableOffer = (id) => {
  const row = db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='voice_reply_offered' AND json_extract(meta,'$.id')=?
    ORDER BY id DESC LIMIT 1`).get(id);
  return row ? JSON.parse(row.meta) : null;
};
const subscribedEvent = (scenario, eventType = 'eva_voice_query', extra = {}) => voiceEvent(
  scenario, eventType, extra, readySubscriptionIds[eventType],
);
const exactAckEcho = (offer) => ({
  delivery_operation_ref: offer.delivery_operation_ref,
  payload_sha256: offer.payload_sha256,
  reply_sha256: offer.reply_sha256,
  reply: offer.reply,
  conversation_id: offer.conversation_id,
});

let report;
try {
  startVoiceBridge({ deliveryWindowMs: 5_000 });
  const startupRecovered = await __voiceBridgeTest.waitForStartupRecovery();
  await connectHA({ runMaintenance: false });
  await ready;
  await waitFor(() => haConnected(), 'HA subscribed readiness');
  await new Promise((resolve) => setTimeout(resolve, 100));

  bus.emit('voice_query', {
    id: scenarios.publicBus.id,
    vraag: scenarios.publicBus.text,
    sender: 'Jaap', speaker_id: 'jaap', speaker_identity_verified: true,
    satellite, conversation_id: scenarios.publicBus.id,
  });
  await new Promise((resolve) => setTimeout(resolve, 150));

  readySocket.send(voiceEvent(
    scenarios.wrongSubscription, 'eva_voice_query', {},
    readySubscriptionIds.eva_voice_query + 10_000,
  ));
  await new Promise((resolve) => setTimeout(resolve, 150));

  readySocket.send(subscribedEvent(scenarios.missingAckBinding));
  await waitFor(() => offers.includes(scenarios.missingAckBinding.id),
    'missing-binding voice reply offer');
  readySocket.send(subscribedEvent(scenarios.missingAckBinding, 'eva_voice_reply_ack'));
  await new Promise((resolve) => setTimeout(resolve, 100));

  readySocket.send(subscribedEvent(scenarios.tamperedAck));
  await waitFor(() => offers.includes(scenarios.tamperedAck.id),
    'tampered-ACK voice reply offer');
  const tamperedOffer = offerBodies.get(scenarios.tamperedAck.id);
  readySocket.send(subscribedEvent(scenarios.tamperedAck, 'eva_voice_reply_ack', {
    ...exactAckEcho(tamperedOffer),
    reply: `${tamperedOffer.reply} gewijzigd`,
  }));
  await new Promise((resolve) => setTimeout(resolve, 100));

  readySocket.send(subscribedEvent(scenarios.authentic));
  await waitFor(() => offers.includes(scenarios.authentic.id), 'authentic voice reply offer');
  bus.emit('voice_reply_ack', {
    id: scenarios.authentic.id, accepted: true, satellite,
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const publicAckDeliveryCount = deliveryCount(scenarios.authentic.id);
  const authenticOffer = offerBodies.get(scenarios.authentic.id);
  readySocket.send(subscribedEvent(scenarios.authentic, 'eva_voice_reply_ack',
    exactAckEcho(authenticOffer)));
  await waitFor(() => deliveryCount(scenarios.authentic.id) === 1, 'authentic voice ACK');
  // Dezelfde request-id mag binnen deze adapterboot geen tweede querycap,
  // gewijzigde rebind of tweede ACK-cap minten.
  readySocket.send(subscribedEvent(scenarios.authentic));
  readySocket.send(subscribedEvent(scenarios.authentic, 'eva_voice_query', {
    vraag: 'Gewijzigde replay met hetzelfde request-id.',
  }));
  readySocket.send(subscribedEvent(scenarios.authentic, 'eva_voice_reply_ack',
    exactAckEcho(authenticOffer)));
  await new Promise((resolve) => setTimeout(resolve, 150));
  const authenticReplay = {
    providerCalls: providerCalls.get('authentic'),
    offerCount: offers.filter((id) => id === scenarios.authentic.id).length,
    deliveryCount: deliveryCount(scenarios.authentic.id),
  };

  readySocket.send(subscribedEvent(scenarios.rollback));
  await waitFor(() => offers.includes(scenarios.rollback.id), 'rollback voice reply offer');
  const rollbackOffer = offerBodies.get(scenarios.rollback.id);
  db.exec(`CREATE TEMP TRIGGER native_voice_chat_out_rollback
    BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
    BEGIN SELECT RAISE(ABORT,'forced native voice chat_out rollback'); END`);
  readySocket.send(subscribedEvent(scenarios.rollback, 'eva_voice_reply_ack',
    exactAckEcho(rollbackOffer)));
  await waitFor(() => voiceDeliveryAuthorityRecord(db, scenarios.rollback.id)?.stage
    === 'acknowledged', 'durable rollback ACK');
  await new Promise((resolve) => setTimeout(resolve, 100));
  const rollbackBeforeRecovery = {
    offerCount: offers.filter((id) => id === scenarios.rollback.id).length,
    deliveryCount: deliveryCount(scenarios.rollback.id),
    stage: voiceDeliveryAuthorityRecord(db, scenarios.rollback.id)?.stage || null,
  };
  db.exec('DROP TRIGGER temp.native_voice_chat_out_rollback');
  await waitFor(() => deliveryCount(scenarios.rollback.id) === 1,
    'scheduled rollback voice delivery projection');
  const rollbackExplicitRecovery = await __voiceBridgeTest.recoverAcknowledgedVoiceDeliveries();
  const rollbackSecondExplicitRecovery = await __voiceBridgeTest
    .recoverAcknowledgedVoiceDeliveries();
  const rollbackAfterRecovery = {
    offerCount: offers.filter((id) => id === scenarios.rollback.id).length,
    deliveryCount: deliveryCount(scenarios.rollback.id),
    stage: voiceDeliveryAuthorityRecord(db, scenarios.rollback.id)?.stage || null,
    ackEventCount: voiceGenerationCount(scenarios.rollback.id, 3),
    deliveredEventCount: voiceGenerationCount(scenarios.rollback.id, 4),
    scheduledRecovered: true,
    explicitRecovery: rollbackExplicitRecovery,
    secondExplicitRecovery: rollbackSecondExplicitRecovery,
  };

  readySocket.send(subscribedEvent(scenarios.ackRollback));
  await waitFor(() => offers.includes(scenarios.ackRollback.id),
    'ACK-rollback voice reply offer');
  const ackRollbackOffer = offerBodies.get(scenarios.ackRollback.id);
  db.exec(`CREATE TEMP TRIGGER native_voice_ack_generation_rollback
    BEFORE INSERT ON main.eva_voice_delivery_authority_events_v1
    WHEN NEW.event_generation=3
    BEGIN SELECT RAISE(ABORT,'forced native voice ACK generation rollback'); END`);
  ackFramesSent.set(scenarios.ackRollback.id, 1);
  readySocket.send(subscribedEvent(scenarios.ackRollback, 'eva_voice_reply_ack',
    exactAckEcho(ackRollbackOffer)));
  await new Promise((resolve) => setTimeout(resolve, 100));
  const ackRollbackBeforeRetry = {
    offerCount: offers.filter((id) => id === scenarios.ackRollback.id).length,
    ackEventCount: ackFramesSent.get(scenarios.ackRollback.id),
    deliveryCount: deliveryCount(scenarios.ackRollback.id),
    stage: rawVoiceStage(scenarios.ackRollback.id),
  };
  db.exec('DROP TRIGGER temp.native_voice_ack_generation_rollback');
  await waitFor(() => voiceDeliveryAuthorityRecord(db, scenarios.ackRollback.id)?.stage
    === 'delivered', 'same in-memory ACK retry');
  const ackRollbackAfterRetry = {
    offerCount: offers.filter((id) => id === scenarios.ackRollback.id).length,
    ackEventCount: ackFramesSent.get(scenarios.ackRollback.id),
    deliveryCount: deliveryCount(scenarios.ackRollback.id),
    stage: voiceDeliveryAuthorityRecord(db, scenarios.ackRollback.id)?.stage || null,
  };

  readySocket.send(subscribedEvent(scenarios.drainBlocked));
  await waitFor(() => offers.includes(scenarios.drainBlocked.id),
    'blocked drain voice reply offer');
  const blockedOffer = offerBodies.get(scenarios.drainBlocked.id);
  db.exec(`CREATE TEMP TRIGGER native_voice_stage4_row_rollback
    BEFORE INSERT ON main.timeline
    WHEN NEW.kind='chat_out'
      AND json_extract(NEW.meta,'$.originMessageId')='${scenarios.drainBlocked.id}'
    BEGIN SELECT RAISE(ABORT,'forced native voice stage4 row rollback'); END`);
  ackFramesSent.set(scenarios.drainBlocked.id, 1);
  readySocket.send(subscribedEvent(scenarios.drainBlocked, 'eva_voice_reply_ack',
    exactAckEcho(blockedOffer)));
  await waitFor(() => rawVoiceStage(scenarios.drainBlocked.id) === 'acknowledged',
    'blocked durable stage3');

  readySocket.send(subscribedEvent(scenarios.drainGood));
  await waitFor(() => offers.includes(scenarios.drainGood.id),
    'good drain voice reply offer');
  const goodOffer = offerBodies.get(scenarios.drainGood.id);
  ackFramesSent.set(scenarios.drainGood.id, 1);
  readySocket.send(subscribedEvent(scenarios.drainGood, 'eva_voice_reply_ack',
    exactAckEcho(goodOffer)));
  await waitFor(() => deliveryCount(scenarios.drainGood.id) === 1,
    'younger row passes blocked row');
  const drainBeforeLift = {
    blockedStage: rawVoiceStage(scenarios.drainBlocked.id),
    blockedDeliveryCount: deliveryCount(scenarios.drainBlocked.id),
    goodStage: rawVoiceStage(scenarios.drainGood.id),
    goodDeliveryCount: deliveryCount(scenarios.drainGood.id),
    blockedOfferCount: offers.filter((id) => id === scenarios.drainBlocked.id).length,
    goodOfferCount: offers.filter((id) => id === scenarios.drainGood.id).length,
    blockedAckEventCount: ackFramesSent.get(scenarios.drainBlocked.id),
    goodAckEventCount: ackFramesSent.get(scenarios.drainGood.id),
  };
  db.exec('DROP TRIGGER temp.native_voice_stage4_row_rollback');
  await waitFor(() => deliveryCount(scenarios.drainBlocked.id) === 1,
    'scheduled blocked row recovery', 5_000);
  const drainAfterLift = {
    blockedStage: rawVoiceStage(scenarios.drainBlocked.id),
    blockedDeliveryCount: deliveryCount(scenarios.drainBlocked.id),
    goodStage: rawVoiceStage(scenarios.drainGood.id),
    goodDeliveryCount: deliveryCount(scenarios.drainGood.id),
    blockedOfferCount: offers.filter((id) => id === scenarios.drainBlocked.id).length,
    goodOfferCount: offers.filter((id) => id === scenarios.drainGood.id).length,
    blockedAckEventCount: ackFramesSent.get(scenarios.drainBlocked.id),
    goodAckEventCount: ackFramesSent.get(scenarios.drainGood.id),
  };

  readySocket.send(subscribedEvent(scenarios.crossGeneration));
  await waitFor(() => offers.includes(scenarios.crossGeneration.id),
    'cross-generation voice reply offer');
  const crossGenerationOffer = offerBodies.get(scenarios.crossGeneration.id);
  const oldReadySocket = readySocket;
  oldReadySocket.terminate();
  await waitFor(() => !haConnected(), 'cross-generation socket disconnect');
  await waitFor(() => sockets.length >= 2 && readySocket !== oldReadySocket && haConnected(),
    'cross-generation replacement socket', 8_000);
  readySocket.send(subscribedEvent(scenarios.crossGeneration, 'eva_voice_reply_ack',
    exactAckEcho(crossGenerationOffer)));
  await new Promise((resolve) => setTimeout(resolve, 150));

  readySocket.send(subscribedEvent(scenarios.stale));
  await waitFor(() => providerCalls.get('stale') === 1, 'stale provider entry');
  readySocket.terminate();
  await waitFor(() => !haConnected(), 'stale socket disconnect');
  staleUnderstandingResolve(understandingResponse(
    scenarios.stale.text, scenarios.stale.reply, 'native-ha-stale-understanding',
  ));
  await new Promise((resolve) => setTimeout(resolve, 300));

  report = {
    startupRecovery: { completedBeforeScenarios: true, recovered: startupRecovered },
    providerCalls: Object.fromEntries(providerCalls),
    providerConnectionStates: Object.fromEntries(providerConnectionStates),
    offers,
    rows: Object.fromEntries(Object.entries(scenarios).map(([key, scenario]) => {
      const row = rowFor(scenario.id);
      return [key, row ? { status: row.status } : null];
    })),
    publicAckDeliveryCount,
    missingAckDeliveryCount: deliveryCount(scenarios.missingAckBinding.id),
    tamperedAckDeliveryCount: deliveryCount(scenarios.tamperedAck.id),
    crossGenerationDeliveryCount: deliveryCount(scenarios.crossGeneration.id),
    authenticReplay,
    rollbackBeforeRecovery,
    rollbackAfterRecovery,
    ackRollbackBeforeRetry,
    ackRollbackAfterRetry,
    drainBeforeLift,
    drainAfterLift,
    authenticDeliveryCount: deliveryCount(scenarios.authentic.id),
    staleDeliveryCount: deliveryCount(scenarios.stale.id),
    authenticOffer: (() => {
      const offer = offerBodies.get(scenarios.authentic.id) || {};
      const payload = {
        id: offer.id,
        reply: offer.reply,
        ok: offer.ok,
        vraag: offer.vraag,
        response_time_ms: offer.response_time_ms,
        conversation_id: offer.conversation_id,
        satellite: offer.satellite,
      };
      return {
        deliveryOperationRef: offer.delivery_operation_ref || null,
        payloadSha256: offer.payload_sha256 || null,
        expectedPayloadSha256: createHash('sha256')
          .update(canonicalJson(payload)).digest('hex'),
        replySha256: offer.reply_sha256 || null,
        expectedReplySha256: createHash('sha256').update(String(offer.reply || '')).digest('hex'),
      };
    })(),
    authenticDurableOffer: durableOffer(scenarios.authentic.id),
    authenticAckEvidence: (() => {
      const evidence = deliveryAuthority(scenarios.authentic.id);
      if (!evidence) return null;
      const queryBody = JSON.parse(evidence.query.sourceBodyJson);
      const ackBody = JSON.parse(evidence.acknowledgement.sourceBodyJson);
      const queryFrame = Buffer.from(queryBody.frameBase64, 'base64');
      const ackFrame = Buffer.from(ackBody.frameBase64, 'base64');
      return {
        contract: evidence.contract,
        requestId: evidence.requestId,
        satellite: evidence.satellite,
        queryEventType: queryBody.eventType,
        ackEventType: ackBody.eventType,
        queryCanonical: JSON.stringify(queryBody) === evidence.query.sourceBodyJson,
        ackCanonical: JSON.stringify(ackBody) === evidence.acknowledgement.sourceBodyJson,
        queryBodySha256: createHash('sha256')
          .update(evidence.query.sourceBodyJson).digest('hex'),
        querySourceSha256: evidence.query.sourceSha256,
        ackBodySha256: createHash('sha256')
          .update(evidence.acknowledgement.sourceBodyJson).digest('hex'),
        ackSourceSha256: evidence.acknowledgement.sourceSha256,
        queryFrameRoundTrip: queryFrame.toString('base64') === queryBody.frameBase64,
        ackFrameRoundTrip: ackFrame.toString('base64') === ackBody.frameBase64,
        queryFrameSha256: createHash('sha256').update(queryFrame).digest('hex'),
        expectedQueryFrameSha256: queryBody.frameSha256,
        ackFrameSha256: createHash('sha256').update(ackFrame).digest('hex'),
        expectedAckFrameSha256: ackBody.frameSha256,
        sameAdapter: queryBody.adapterInstanceSha256 === ackBody.adapterInstanceSha256
          && ackBody.adapterInstanceSha256 === evidence.adapterInstanceSha256,
        sameSocketGeneration: queryBody.socketGeneration === ackBody.socketGeneration
          && ackBody.socketGeneration === evidence.socketGeneration,
      };
    })(),
    authenticSourceEvidence: (() => {
      const row = rowFor(scenarios.authentic.id);
      const source = row && db.prepare(`SELECT source_sha256,source_body_json
        FROM main.eva_turn_understanding_authorities_v1 WHERE execution_key=?`).get(
        row.execution_key,
      );
      if (!source) return null;
      const body = JSON.parse(source.source_body_json);
      return {
        canonical: JSON.stringify(body) === source.source_body_json,
        bodySha256: createHash('sha256')
          .update(source.source_body_json).digest('hex'),
        sourceSha256: source.source_sha256,
        frameRoundTrip: Buffer.from(body.frameBase64, 'base64').toString('base64')
          === body.frameBase64,
        socketGeneration: body.socketGeneration,
      };
    })(),
  };
} finally {
  globalThis.fetch = originalFetch;
  for (const socket of sockets) socket.terminate();
  await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
}

process.stdout.write(`NATIVE_HA_VOICE_REPORT=${JSON.stringify(report)}\n`);
process.exit(0);
