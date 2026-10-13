import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';

import './voicebridge-test-composition.js';
import './voiceauthority-test-composition.js';

const [phase, dataDir, mutation] = process.argv.slice(2);
if (![
  'prepare-offer', 'prepare-ack', 'prepare-final-ack', 'recover', 'recover-mutation',
].includes(phase) || !dataDir) {
  throw new Error('native voice restart fixture mist fase of datamap');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-voice-restart-test-only';
process.env.HA_URL = 'http://127.0.0.1:8123';

const { canonicalJson } = await import('../src/util.js');
const { db } = await import('../src/db.js');
const agent = await import('../src/mind/agent.js');
const voiceBridge = await import('../src/mind/voicebridge.js');
const portfolio = await import('../src/mind/portfolio.js');
const {
  recordVoiceAckReceipt,
  recordVoiceOfferIntent,
  recordVoiceQueryAuthority,
  pendingAcknowledgedVoiceDeliveries,
  voiceDeliveryAuthorityRecord,
  __voiceAuthorityStoreTest,
} = await import('../src/voiceauthority.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const requestId = 'voice-process-restart-1';
const conversationId = 'voice:process-restart';
const requester = 'Jaap';
const satellite = 'assist_satellite.keuken';
const proofPath = 'data/workspace/voice-final-proof.txt';
const text = phase === 'prepare-final-ack'
  ? `Lees ${proofPath} en rond het Voice-werk af.`
  : 'Bewaar dit Voice-antwoord over een procesherstart.';
const intendedReply = phase === 'prepare-final-ack'
  ? 'Het Voice-werk is gecontroleerd en gereed.'
  : 'Dit antwoord blijft exact aan dezelfde Voice-beurt gebonden.';

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(finalWork = null) {
  if (finalWork) {
    const readText = `Lees ${proofPath}`;
    const closeText = 'rond het Voice-werk af';
    return completedModelResponse([{
      type: 'function_call', call_id: 'voice-restart-final-understanding',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: 'Rond het Voice-werk af.', outcome: intendedReply, beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{
          capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
          semanticInput: { pad: proofPath },
          sourceSpans: [{ start: text.indexOf(readText), end: text.indexOf(readText) + readText.length }],
        }, {
          capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request',
          semanticInput: { id: finalWork.workItem.id, resultaat: intendedReply },
          sourceSpans: [{ start: text.indexOf(closeText), end: text.indexOf(closeText) + closeText.length }],
        }],
        requestedEffects: ['read', 'persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
      }),
    }]);
  }
  return completedModelResponse([{
    type: 'function_call', call_id: 'voice-restart-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Een exact antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: intendedReply },
    }),
  }]);
}

function replyResponse() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: intendedReply }],
  }]);
}

function boundOffer(result, source, sourceBody) {
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
  return {
    turnId: result.turnId,
    operationRef: `home-assistant-voice-reply:${operationSha256}`,
    operationSha256,
    payloadJson,
    payloadSha256,
    replySha256,
    reply: result.reply,
  };
}

function boundAcknowledgement(source, sourceBody, offer) {
  const event = {
    id: 2, type: 'event', event: {
      event_type: 'eva_voice_reply_ack',
      data: {
        id: requestId, accepted: true, satellite,
        delivery_operation_ref: offer.operationRef,
        payload_sha256: offer.payloadSha256,
        reply_sha256: offer.replySha256,
        reply: offer.reply,
        conversation_id: conversationId,
      },
    },
  };
  const frame = Buffer.from(canonicalJson(event));
  const bodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256: sourceBody.adapterInstanceSha256,
    socketGeneration: sourceBody.socketGeneration,
    eventType: 'eva_voice_reply_ack',
    frameByteLength: frame.length,
    frameSha256: sha256(frame),
    frameBase64: frame.toString('base64'),
    event,
  });
  const sourceSha256 = sha256(bodyJson);
  const sourceRef = `home-assistant-websocket:${sha256(`restart-ack:${sourceSha256}`)}`;
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
        operationRef: offer.operationRef,
        operationSha256: offer.operationSha256,
        payloadJson: offer.payloadJson,
        payloadSha256: offer.payloadSha256,
        replySha256: offer.replySha256,
      },
      acknowledgement: { sourceRef, sourceSha256, sourceBodyJson: bodyJson },
    },
  };
  const deliveryJson = canonicalJson(delivery);
  return {
    requestId,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource({
      sourceRef,
      sourceSha256,
      sourceBodyJson: bodyJson,
    }, 'eva_voice_reply_ack'),
    deliveryJson,
    deliverySha256: sha256(deliveryJson),
  };
}

let report;
if (phase.startsWith('prepare-')) {
  let finalWork = null;
  if (phase === 'prepare-final-ack') {
    finalWork = portfolio.createDurableWork({
      source: 'voice', owner: requester, title: 'Voice eindaflevering',
      goal: intendedReply,
    }, {
      originKind: 'user', originChannel: 'voice', originConversationId: conversationId,
      originMessageId: requestId, requester, deliveryPolicy: 'required',
      deliveryTarget: requester,
    });
    fs.mkdirSync(`${dataDir}/workspace`, { recursive: true });
    fs.writeFileSync(`${dataDir}/workspace/voice-final-proof.txt`,
      'Het Voice-werk is gecontroleerd en gereed.\n');
  }
  const issuedMessageContext = voiceBridge.__voiceBridgeTest.issueSyntheticUnderstandingContext({
    messageId: requestId,
    satellite,
    conversationId,
    requester,
    text,
    syntheticTest: phase !== 'prepare-final-ack',
  });
  const messageContext = finalWork ? {
    ...issuedMessageContext,
    workId: finalWork.workItem.id,
    goalEpisodeId: finalWork.episode.id,
  } : issuedMessageContext;
  const source = voiceBridge.verifiedVoiceUnderstandingSource(messageContext, {
    channel: 'voice', conversationId, messageId: requestId,
    requester, originalText: text,
  });
  assert.ok(source);
  const sourceBody = JSON.parse(source.bodyJson);
  recordVoiceQueryAuthority(db, {
    requestId, conversationId, requester, satellite,
    authenticatedSource: __voiceAuthorityStoreTest.issueAuthenticatedSource({
      sourceRef: source.ref,
      sourceSha256: source.sha256,
      sourceBodyJson: source.bodyJson,
    }, 'eva_voice_query'),
  });
  let providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    return providerCalls === 1 ? understandingResponse(finalWork) : replyResponse();
  };
  const result = await agent.handleMessageDetailed({
    channel: 'voice', sender: requester, text, messageContext,
  });
  const offer = boundOffer(result, source, sourceBody);
  recordVoiceOfferIntent(db, { requestId, ...offer });
  if (phase === 'prepare-ack' || phase === 'prepare-final-ack') {
    recordVoiceAckReceipt(db, boundAcknowledgement(source, sourceBody, offer));
  }
  report = {
    phase,
    providerCalls,
    physicalOffers: 1,
    stage: voiceDeliveryAuthorityRecord(db, requestId)?.stage || null,
    deliveryCount: Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`)
      .get(requestId).value),
    episodeState: finalWork ? portfolio.goalEpisodeById(finalWork.episode.id)?.state : null,
    workStatus: finalWork ? db.prepare('SELECT status FROM main.eva_work_items WHERE id=?')
      .get(finalWork.workItem.id)?.status : null,
    finalDeliveryBindingSha256: result.finalDeliveryBindingSha256 || null,
    tools: result.tools?.map((tool) => ({
      name: tool.name, ok: tool.ok, verified: tool.verified, changed: tool.changed,
      output: String(tool.output || '').slice(0, 500),
    })) || [],
  };
} else if (phase === 'recover') {
  let networkCalls = 0;
  globalThis.fetch = async () => {
    networkCalls += 1;
    throw new Error('Voice-herstart mag provider noch REST bereiken');
  };
  voiceBridge.startVoiceBridge({ deliveryWindowMs: 5_000 });
  const recovered = await voiceBridge.__voiceBridgeTest.waitForStartupRecovery();
  report = {
    phase,
    recovered,
    networkCalls,
    physicalOffers: 0,
    stage: voiceDeliveryAuthorityRecord(db, requestId)?.stage || null,
    deliveryCount: Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`)
      .get(requestId).value),
  };
  const prepared = agent.preparedUnderstandingIngressReplay({
    channel: 'voice', conversationId, messageId: requestId, sender: requester,
  });
  if (prepared?.goalEpisodeId) {
    report.episodeState = portfolio.goalEpisodeById(prepared.goalEpisodeId)?.state || null;
    report.workStatus = db.prepare('SELECT status FROM main.eva_work_items WHERE id=?')
      .get(prepared.workItemId)?.status || null;
    report.finalDeliveryBindingSha256 = prepared.finalDeliveryBindingSha256 || null;
  }
} else {
  let networkCalls = 0;
  globalThis.fetch = async () => {
    networkCalls += 1;
    throw new Error('Voice-mutatieproef mag provider noch REST bereiken');
  };
  const authority = pendingAcknowledgedVoiceDeliveries(db, 1)[0];
  const result = agent.preparedUnderstandingIngressReplay({
    channel: 'voice', conversationId, messageId: requestId, sender: requester,
  });
  const originalEpisodeId = result?.goalEpisodeId;
  const originalWorkItemId = result?.workItemId;
  let delivery;
  try { delivery = JSON.parse(authority?.acknowledgement?.deliveryJson || 'null'); } catch {}
  assert.ok(authority && result && delivery);
  assert.ok(agent.verifiedPreparedUnderstandingTurnForResult(result, {
    channel: 'voice', conversationId, messageId: requestId, sender: requester,
    sourceRef: authority.query.sourceRef,
    sourceSha256: authority.query.sourceSha256,
    sourceBodyJson: authority.query.sourceBodyJson,
    sourceGeneration: 1,
  }));
  let candidateAuthority = authority;
  let candidateDelivery = delivery;
  switch (mutation) {
    case 'query':
      candidateAuthority = { ...authority, requestId: `${requestId}-changed` };
      break;
    case 'source':
      candidateAuthority = { ...authority, query: {
        ...authority.query, sourceBodyJson: `${authority.query.sourceBodyJson} `,
      } };
      break;
    case 'offer':
      candidateAuthority = { ...authority, offer: {
        ...authority.offer, operationSha256: '0'.repeat(64),
      } };
      break;
    case 'rawAck':
      candidateAuthority = { ...authority, acknowledgement: {
        ...authority.acknowledgement,
        sourceBodyJson: `${authority.acknowledgement.sourceBodyJson} `,
      } };
      break;
    case 'delivery':
      candidateDelivery = { ...delivery, acknowledgement: 'changed' };
      break;
    case 'reply': result.reply = `${result.reply} gewijzigd`; break;
    case 'turn': result.turnId = `${result.turnId}:changed`; break;
    case 'episode': result.goalEpisodeId += 1; break;
    case 'work': result.workItemId += 1; break;
    case 'conversation': result.conversationId = `${result.conversationId}:changed`; break;
    case 'message': result.originMessageId = `${result.originMessageId}-changed`; break;
    case 'requester': result.sender = `${result.sender} veranderd`; break;
    case 'finalBinding': result.finalDeliveryBindingSha256 = '0'.repeat(64); break;
    default: throw new Error(`onbekende Voice-herstelmutatie: ${mutation}`);
  }
  const verified = await agent.verifiedVoiceFinalDeliveryRecoveryForResult(
    result, candidateAuthority, candidateDelivery,
  );
  report = {
    phase, mutation, verified: Boolean(verified), networkCalls,
    stage: voiceDeliveryAuthorityRecord(db, requestId)?.stage || null,
    deliveryCount: Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
      WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`)
      .get(requestId).value),
    episodeState: portfolio.goalEpisodeById(originalEpisodeId)?.state || null,
    workStatus: db.prepare('SELECT status FROM main.eva_work_items WHERE id=?')
      .get(originalWorkItemId)?.status || null,
  };
}

console.log(`NATIVE_VOICE_RESTART_REPORT=${JSON.stringify(report)}`);
db.close();
