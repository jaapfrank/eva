import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import './voicebridge-test-composition.js';
import './voiceauthority-test-composition.js';

const [scenario, dataDir] = process.argv.slice(2);
if (!['startup-page', 'poison-page', 'last-timer'].includes(scenario) || !dataDir) {
  throw new Error('native Voice-drainfixture mist scenario of datamap');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-voice-drain-test-only';
process.env.HA_URL = 'http://127.0.0.1:8123';

const { canonicalJson } = await import('../src/util.js');
const { db } = await import('../src/db.js');
const agent = await import('../src/mind/agent.js');
const voiceBridge = await import('../src/mind/voicebridge.js');
const {
  pendingAcknowledgedVoiceDeliveries,
  recordVoiceAckReceipt,
  recordVoiceOfferIntent,
  recordVoiceQueryAuthority,
  voiceDeliveryAuthorityRecord,
  __voiceAuthorityStoreTest,
} = await import('../src/voiceauthority.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const satellite = 'assist_satellite.keuken';
const requester = 'Jaap';
const reply = 'Deze lokale Voice-receipt blijft exact gebonden.';
let providerCalls = 0;
let recoveryExternalCalls = 0;

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(text) {
  return completedModelResponse([{
    type: 'function_call', call_id: `voice-drain-understanding-${sha256(text).slice(0, 12)}`,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: reply, beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }]);
}

function replyResponse() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: reply }],
  }]);
}

function boundOffer(requestId, conversationId, result, source, sourceBody) {
  const payload = {
    id: requestId, reply: result.reply, ok: true, vraag: `Bewaar ${requestId}.`,
    response_time_ms: 1, conversation_id: conversationId, satellite,
  };
  const payloadJson = canonicalJson(payload);
  const payloadSha256 = sha256(payloadJson);
  const replySha256 = sha256(result.reply);
  const operationSha256 = sha256(canonicalJson({
    contract: 'HomeAssistantVoiceReplyOperation/v1', requestId, satellite,
    adapterInstanceSha256: sourceBody.adapterInstanceSha256,
    socketGeneration: sourceBody.socketGeneration,
    querySourceRef: source.ref, querySourceSha256: source.sha256,
    payloadSha256, replySha256,
  }));
  return {
    requestId, turnId: result.turnId,
    operationRef: `home-assistant-voice-reply:${operationSha256}`,
    operationSha256, payloadJson, payloadSha256, replySha256, reply: result.reply,
  };
}

function boundAcknowledgement(requestId, conversationId, source, sourceBody, offer) {
  const event = {
    id: 2, type: 'event', event: {
      event_type: 'eva_voice_reply_ack',
      data: {
        id: requestId, accepted: true, satellite,
        delivery_operation_ref: offer.operationRef,
        payload_sha256: offer.payloadSha256,
        reply_sha256: offer.replySha256,
        reply: offer.reply, conversation_id: conversationId,
      },
    },
  };
  const frame = Buffer.from(canonicalJson(event));
  const sourceBodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256: sourceBody.adapterInstanceSha256,
    socketGeneration: sourceBody.socketGeneration,
    eventType: 'eva_voice_reply_ack', frameByteLength: frame.length,
    frameSha256: sha256(frame), frameBase64: frame.toString('base64'), event,
  });
  const sourceSha256 = sha256(sourceBodyJson);
  const sourceRef = `home-assistant-websocket:${sha256(`drain-ack:${sourceSha256}`)}`;
  const delivery = {
    accepted: true, transport: 'home-assistant-conversation-response',
    acknowledgement: 'eva_voice_reply_ack', satellite,
    socketGeneration: sourceBody.socketGeneration,
    authorityEvidence: {
      contract: 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2', requestId, satellite,
      adapterInstanceSha256: sourceBody.adapterInstanceSha256,
      socketGeneration: sourceBody.socketGeneration,
      query: { sourceRef: source.ref, sourceSha256: source.sha256, sourceBodyJson: source.bodyJson },
      offer: {
        operationRef: offer.operationRef, operationSha256: offer.operationSha256,
        payloadJson: offer.payloadJson, payloadSha256: offer.payloadSha256,
        replySha256: offer.replySha256,
      },
      acknowledgement: { sourceRef, sourceSha256, sourceBodyJson },
    },
  };
  const deliveryJson = canonicalJson(delivery);
  return {
    requestId,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource({
      sourceRef, sourceSha256, sourceBodyJson,
    }, 'eva_voice_reply_ack'),
    deliveryJson, deliverySha256: sha256(deliveryJson),
  };
}

async function prepare(requestId, { acknowledge = true } = {}) {
  const conversationId = `voice:${requestId}`;
  const text = `Bewaar ${requestId}.`;
  const messageContext = voiceBridge.__voiceBridgeTest.issueSyntheticUnderstandingContext({
    messageId: requestId, satellite, conversationId, requester, text,
  });
  const source = voiceBridge.verifiedVoiceUnderstandingSource(messageContext, {
    channel: 'voice', conversationId, messageId: requestId, requester, originalText: text,
  });
  assert.ok(source);
  const sourceBody = JSON.parse(source.bodyJson);
  recordVoiceQueryAuthority(db, {
    requestId, conversationId, requester, satellite,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource({
      sourceRef: source.ref, sourceSha256: source.sha256, sourceBodyJson: source.bodyJson,
    }, 'eva_voice_query'),
  });
  let turnCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    turnCalls += 1;
    return turnCalls === 1 ? understandingResponse(text) : replyResponse();
  };
  const result = await agent.handleMessageDetailed({
    channel: 'voice', sender: requester, text, messageContext,
  });
  const offer = boundOffer(requestId, conversationId, result, source, sourceBody);
  recordVoiceOfferIntent(db, offer);
  const acknowledgement = boundAcknowledgement(
    requestId, conversationId, source, sourceBody, offer,
  );
  if (acknowledge) recordVoiceAckReceipt(db, acknowledgement);
  await new Promise((resolve) => setTimeout(resolve, 2));
  return acknowledgement;
}

function poisonTrigger() {
  db.exec(`CREATE TEMP TRIGGER native_voice_drain_poison
    BEFORE INSERT ON main.timeline
    WHEN NEW.kind='chat_out'
      AND json_extract(NEW.meta,'$.originMessageId') LIKE 'voice-drain-poison-%'
    BEGIN SELECT RAISE(ABORT,'forced poison Voice projection'); END`);
}

function receiptCount(requestId) {
  return Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`).get(requestId).value);
}

function poisonLogs() {
  return Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='system' AND content LIKE 'Voice-ACK voice-drain-poison-% blijft herstelbaar:%'`)
    .get().value);
}

async function waitFor(check, label, timeoutMs = 4_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timeout: ${label}`);
}

let report;
if (scenario === 'startup-page') {
  const ids = [];
  for (let index = 1; index <= 21; index += 1) {
    const id = `voice-drain-startup-${String(index).padStart(2, '0')}`;
    ids.push(id);
    await prepare(id);
  }
  const preparationProviderCalls = providerCalls;
  globalThis.fetch = async () => {
    recoveryExternalCalls += 1;
    throw new Error('Voice-drainherstel mag geen externe call doen');
  };
  process.stdout.write(`NATIVE_VOICE_DRAIN_READY=${JSON.stringify({
    scenario, acknowledged: pendingAcknowledgedVoiceDeliveries(db, 100).length,
  })}\n`);
  voiceBridge.startVoiceBridge();
  await voiceBridge.__voiceBridgeTest.waitForStartupRecovery();
  await waitFor(() => ids.every((id) => receiptCount(id) === 1),
    'startup page continuation').catch(() => {});
  report = {
    scenario, preparationProviderCalls, recoveryExternalCalls,
    delivered: ids.filter((id) => receiptCount(id) === 1).length,
    pending: pendingAcknowledgedVoiceDeliveries(db, 100).length,
  };
} else if (scenario === 'poison-page') {
  const poisonIds = [];
  for (let index = 1; index <= 20; index += 1) {
    const id = `voice-drain-poison-${String(index).padStart(2, '0')}`;
    poisonIds.push(id);
    await prepare(id);
  }
  const goodId = 'voice-drain-younger-good';
  await prepare(goodId);
  poisonTrigger();
  const preparationProviderCalls = providerCalls;
  globalThis.fetch = async () => {
    recoveryExternalCalls += 1;
    throw new Error('Voice-drainherstel mag geen externe call doen');
  };
  process.stdout.write(`NATIVE_VOICE_DRAIN_READY=${JSON.stringify({
    scenario, acknowledged: pendingAcknowledgedVoiceDeliveries(db, 100).length,
  })}\n`);
  voiceBridge.startVoiceBridge();
  await voiceBridge.__voiceBridgeTest.waitForStartupRecovery();
  await waitFor(() => receiptCount(goodId) === 1, 'younger good Voice row').catch(() => {});
  report = {
    scenario, preparationProviderCalls, recoveryExternalCalls,
    goodStage: voiceDeliveryAuthorityRecord(db, goodId)?.stage || null,
    goodReceipts: receiptCount(goodId),
    poisonedDelivered: poisonIds.filter((id) => receiptCount(id) > 0).length,
    poisonLogs: poisonLogs(),
  };
} else {
  for (let index = 1; index <= 20; index += 1) {
    await prepare(`voice-drain-poison-${String(index).padStart(2, '0')}`);
  }
  const goodId = 'voice-drain-last-timer-good';
  const pendingAck = await prepare(goodId, { acknowledge: false });
  poisonTrigger();
  const preparationProviderCalls = providerCalls;
  globalThis.fetch = async () => {
    recoveryExternalCalls += 1;
    throw new Error('Voice-drainherstel mag geen externe call doen');
  };
  process.stdout.write(`NATIVE_VOICE_DRAIN_READY=${JSON.stringify({
    scenario, acknowledged: pendingAcknowledgedVoiceDeliveries(db, 100).length,
  })}\n`);
  voiceBridge.__voiceBridgeTest.scheduleAcknowledgedVoiceDeliveryDrain(3);
  await new Promise((resolve) => setTimeout(resolve, 50));
  recordVoiceAckReceipt(db, pendingAck);
  voiceBridge.__voiceBridgeTest.scheduleAcknowledgedVoiceDeliveryDrain(0);
  await waitFor(() => receiptCount(goodId) === 1,
    'coalesced last-timer Voice row', 5_000).catch(() => {});
  report = {
    scenario, preparationProviderCalls, recoveryExternalCalls,
    goodStage: voiceDeliveryAuthorityRecord(db, goodId)?.stage || null,
    goodReceipts: receiptCount(goodId), poisonLogs: poisonLogs(),
  };
}

process.stdout.write(`NATIVE_VOICE_DRAIN_CONTINUITY=${JSON.stringify(report)}\n`);
db.close();
process.exit(0);
