import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

import './voicebridge-test-composition.js';
import './voiceauthority-test-composition.js';

const prior = Object.fromEntries(['EVA_DATA_DIR', 'OPENAI_API_KEY', 'HA_URL']
  .map((name) => [name, process.env[name]]));
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-native-voice-txn-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-voice-transaction-test-only';
process.env.HA_URL = 'http://127.0.0.1:8123';

const { canonicalJson } = await import('../src/util.js');
const { db } = await import('../src/db.js');
const agent = await import('../src/mind/agent.js');
const voiceBridge = await import('../src/mind/voicebridge.js');
const {
  recordVoiceAckReceipt,
  recordVoiceOfferIntent,
  recordVoiceQueryAuthority,
  voiceDeliveryAuthorityRecord,
  __voiceAuthorityStoreTest,
} = await import('../src/voiceauthority.js');

after(() => {
  db.close();
  for (const [name, value] of Object.entries(prior)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(text, reply) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'voice-transaction-understanding',
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

test('Voice-cap en issued-result worden pas na geslaagde chat_out+projectie geconsumeerd', async () => {
  const requestId = 'voice-cap-rollback-1';
  const conversationId = 'voice:cap-rollback';
  const requester = 'Jaap';
  const satellite = 'assist_satellite.keuken';
  const text = 'Leg deze Voice-aflevering exact één keer vast.';
  const intendedReply = 'Deze Voice-aflevering is exact één keer ontvangen.';
  const messageContext = voiceBridge.__voiceBridgeTest.issueSyntheticUnderstandingContext({
    messageId: requestId,
    satellite,
    conversationId,
    requester,
    text,
  });
  const source = voiceBridge.verifiedVoiceUnderstandingSource(messageContext, {
    channel: 'voice', conversationId, messageId: requestId,
    requester, originalText: text,
  });
  assert.ok(source);
  const sourceBody = JSON.parse(source.bodyJson);
  const queryStoreSource = __voiceAuthorityStoreTest.issueAuthenticatedSource({
    sourceRef: source.ref,
    sourceSha256: source.sha256,
    sourceBodyJson: source.bodyJson,
  }, 'eva_voice_query');
  recordVoiceQueryAuthority(db, {
    requestId, conversationId, requester, satellite,
    authenticatedSource: queryStoreSource,
  });

  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async (url) => {
    assert.match(String(url), /api\.openai\.com/u);
    providerCalls += 1;
    return providerCalls === 1
      ? understandingResponse(text, intendedReply)
      : replyResponse(intendedReply);
  };
  let result;
  try {
    result = await agent.handleMessageDetailed({
      channel: 'voice', sender: requester, text, messageContext,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.ok(providerCalls >= 1 && providerCalls <= 2);
  assert.ok(String(result.reply || '').trim().length > 0);

  const payload = {
    id: requestId,
    reply: result.reply,
    ok: true,
    vraag: text,
    response_time_ms: 1,
    conversation_id: conversationId,
    satellite,
  };
  const payloadJson = canonicalJson(payload);
  const payloadSha256 = sha256(payloadJson);
  const replySha256 = sha256(result.reply);
  const operationSha256 = sha256(canonicalJson({
    contract: 'HomeAssistantVoiceReplyOperation/v1',
    requestId,
    satellite,
    adapterInstanceSha256: sourceBody.adapterInstanceSha256,
    socketGeneration: sourceBody.socketGeneration,
    querySourceRef: source.ref,
    querySourceSha256: source.sha256,
    payloadSha256,
    replySha256,
  }));
  const operationRef = `home-assistant-voice-reply:${operationSha256}`;
  recordVoiceOfferIntent(db, {
    requestId, turnId: result.turnId, operationRef, operationSha256,
    payloadJson, payloadSha256, replySha256,
  });

  const ackEvent = {
    id: 2, type: 'event', event: {
      event_type: 'eva_voice_reply_ack',
      data: {
        id: requestId, accepted: true, satellite,
        delivery_operation_ref: operationRef,
        payload_sha256: payloadSha256,
        reply_sha256: replySha256,
        reply: result.reply,
        conversation_id: conversationId,
      },
    },
  };
  const ackFrame = Buffer.from(canonicalJson(ackEvent));
  const ackBodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256: sourceBody.adapterInstanceSha256,
    socketGeneration: sourceBody.socketGeneration,
    eventType: 'eva_voice_reply_ack',
    frameByteLength: ackFrame.length,
    frameSha256: sha256(ackFrame),
    frameBase64: ackFrame.toString('base64'),
    event: ackEvent,
  });
  const ackSha256 = sha256(ackBodyJson);
  const ackRef = `home-assistant-websocket:${sha256(`ack:${ackSha256}`)}`;
  const delivery = {
    accepted: true,
    transport: 'home-assistant-conversation-response',
    acknowledgement: 'eva_voice_reply_ack',
    satellite,
    socketGeneration: sourceBody.socketGeneration,
    authorityEvidence: {
      contract: 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2',
      requestId,
      satellite,
      adapterInstanceSha256: sourceBody.adapterInstanceSha256,
      socketGeneration: sourceBody.socketGeneration,
      query: {
        sourceRef: source.ref,
        sourceSha256: source.sha256,
        sourceBodyJson: source.bodyJson,
      },
      offer: {
        operationRef, operationSha256, payloadJson, payloadSha256, replySha256,
      },
      acknowledgement: {
        sourceRef: ackRef,
        sourceSha256: ackSha256,
        sourceBodyJson: ackBodyJson,
      },
    },
  };
  const deliveryJson = canonicalJson(delivery);
  recordVoiceAckReceipt(db, {
    requestId,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource({
      sourceRef: ackRef,
      sourceSha256: ackSha256,
      sourceBodyJson: ackBodyJson,
    }, 'eva_voice_reply_ack'),
    deliveryJson,
    deliverySha256: sha256(deliveryJson),
  });
  const capability = voiceBridge.__voiceBridgeTest.issueVoiceDeliveryAuthority(result, delivery);
  assert.ok(capability);

  const physicalAcceptances = 1;
  db.exec(`CREATE TEMP TRIGGER native_voice_cap_chat_out_rollback
    BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
    BEGIN SELECT RAISE(ABORT,'forced voice cap chat_out rollback'); END`);
  try {
    assert.throws(
      () => agent.recordDeliveredReply(result, delivery, capability),
      /forced voice cap chat_out rollback/u,
    );
  } finally {
    db.exec('DROP TRIGGER temp.native_voice_cap_chat_out_rollback');
  }
  assert.ok(voiceBridge.__voiceBridgeTest.verifiedVoiceDeliveryAuthority(
    capability, result, delivery,
  ));
  assert.equal(voiceDeliveryAuthorityRecord(db, requestId).stage, 'acknowledged');
  assert.equal(agent.recordDeliveredReply(result, delivery, capability), true);
  assert.equal(agent.deliveredReplyRecorded(result, delivery), true);
  assert.equal(agent.deliveredReplyRecorded({ ...result, reply: 'Andere bytes.' }, delivery), false);
  assert.equal(agent.deliveredReplyRecorded({ ...result, channel: 'web' }, delivery), false);
  assert.equal(agent.deliveredReplyRecorded(result, { ...delivery, transport: 'telegram' }), false);
  assert.equal(agent.deliveredReplyRecorded(result, {
    ...delivery,
    authorityEvidence: { ...delivery.authorityEvidence, requestId: 'voice-other' },
  }), false);
  assert.equal(physicalAcceptances, 1);
  assert.equal(agent.recordDeliveredReply(result, delivery, capability), false);
  assert.equal(voiceDeliveryAuthorityRecord(db, requestId).stage, 'delivered');
  assert.equal(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?`).get(result.turnId).value, 1);
  assert.equal(physicalAcceptances, 1);
});
