// Home Assistant voice is alleen een adapter. De betekenis en uitvoering lopen
// door exact dezelfde agent als Telegram en het dashboard.
import { createHash } from 'node:crypto';
import { db, logTimeline } from '../db.js';
import { canonicalJson, errMsg } from '../util.js';
import {
  consumeVoiceStoreEventSource,
  pendingAcknowledgedVoiceDeliveryPage,
  pendingAcknowledgedVoiceDeliveries,
  recordVoiceAckReceipt,
  recordVoiceOfferIntent,
  recordVoiceQueryAuthority,
  verifiedVoiceStoreEventSource,
} from '../voiceauthority.js';
import {
  restCall,
} from '../integrations/homeassistant.js';
import { recoverInterruptedUnderstandingIngresses } from './portfolio.js';

let started = false;
const active = new Set();
// Home Assistant wacht 35 seconden. Eva sluit haar eigen delivery drie seconden
// eerder, zodat eventtransport en ACK niet precies op HA's timeoutgrens racen.
const VOICE_DELIVERY_WINDOW_MS = 32_000;
let configuredDeliveryWindowMs = VOICE_DELIVERY_WINDOW_MS;
let _startupVoiceRecovery = Promise.resolve(0);
let voiceDeliveryDrainPromise = null;
let voiceDeliveryDrainTimer = null;
let voiceDeliveryDrainCursor = null;
let voiceDeliveryDrainRequestGeneration = 0;
const scheduledVoiceAckRetries = new WeakMap();
const VOICE_ACK_RETRY_DELAYS_MS = Object.freeze([50, 200, 800]);
const VOICE_DELIVERY_DRAIN_DELAYS_MS = Object.freeze([0, 100, 500, 2_000, 10_000, 30_000]);
const VOICE_DELIVERY_DRAIN_PAGE_SIZE = 20;
const VOICE_DELIVERY_DRAIN_MAX_VISITS = 100;
const voiceDeliveryRetryState = new Map();
const VOICE_LANE_PREFIX = 'voice-lane:';
const UNRESOLVED_VOICE_LANE = `${VOICE_LANE_PREFIX}unresolved`;
const VOICE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const VOICE_REPLY_OPERATION_REF = /^home-assistant-voice-reply:[a-f0-9]{64}$/u;
const issuedVoiceUnderstandingSources = new WeakMap();
const issuedVoiceDeliveryAuthorities = new WeakMap();

function normalizedSatellite(value = '') {
  const satellite = String(value || '').trim().toLowerCase();
  return /^assist_satellite\.[a-z0-9_]+$/.test(satellite) ? satellite : '';
}

export function voiceLaneKey(value = '') {
  const satellite = normalizedSatellite(value);
  return satellite ? `${VOICE_LANE_PREFIX}${satellite}` : UNRESOLVED_VOICE_LANE;
}

function normalizedVoiceLane(value = '') {
  const lane = String(value || '').trim().toLowerCase();
  return voiceLaneKey(lane.startsWith(VOICE_LANE_PREFIX)
    ? lane.slice(VOICE_LANE_PREFIX.length) : lane);
}

export function voiceRequestProvenance(data = {}) {
  const claimedSender = String(data.sender || '').trim();
  const speakerId = String(data.speaker_id || '').trim();
  const identityVerified = [true, 'true', 1, '1'].includes(data.speaker_identity_verified)
    && Boolean(claimedSender && speakerId);
  return {
    sender: identityVerified ? claimedSender : 'Huisgenoot',
    identityTrusted: identityVerified,
    satellite: normalizedSatellite(data.satellite),
  };
}

/** @param {any} [input] */
export function voiceReplyPayload(input = {}) {
  const { reply, question, responseTimeMs, conversationId, satellite } = input;
  return {
    reply: String(reply || ''),
    ok: true,
    vraag: String(question || ''),
    response_time_ms: Math.max(0, Math.round(Number(responseTimeMs) || 0)),
    conversation_id: String(conversationId || ''),
    ...(normalizedSatellite(satellite) ? { satellite: normalizedSatellite(satellite) } : {}),
  };
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function exactVoiceReplyOffer(id, payload, querySource) {
  const eventPayload = Object.freeze({ id: String(id), ...payload });
  const payloadJson = canonicalJson(eventPayload);
  const payloadSha256 = sha256(payloadJson);
  const replySha256 = sha256(String(eventPayload.reply || ''));
  const operationSha256 = sha256(canonicalJson({
    contract: 'HomeAssistantVoiceReplyOperation/v1',
    requestId: String(id),
    satellite: normalizedSatellite(eventPayload.satellite),
    adapterInstanceSha256: String(querySource?.adapterInstanceSha256 || ''),
    socketGeneration: Number(querySource?.socketGeneration),
    querySourceRef: String(querySource?.sourceRef || ''),
    querySourceSha256: String(querySource?.sourceSha256 || ''),
    payloadSha256,
    replySha256,
  }));
  const operationRef = `home-assistant-voice-reply:${operationSha256}`;
  if (!VOICE_REPLY_OPERATION_REF.test(operationRef)
    || !SHA256.test(payloadSha256) || !SHA256.test(replySha256)) return null;
  return Object.freeze({
    operationRef, operationSha256, payloadJson, payloadSha256, replySha256,
    reply: eventPayload.reply,
    eventData: Object.freeze({
      ...eventPayload,
      delivery_operation_ref: operationRef,
      payload_sha256: payloadSha256,
      reply_sha256: replySha256,
    }),
  });
}

function createVoiceTurnGate({ clearTimer = clearTimeout, now = Date.now } = {}) {
  const latestByLane = new Map();
  const laneById = new Map();
  const deadlineById = new Map();
  const pendingDelivery = new Map();
  const controllerById = new Map();

  const takePending = (id) => {
    const pending = pendingDelivery.get(id);
    if (!pending) return null;
    pendingDelivery.delete(id);
    clearTimer(pending.timer);
    return pending;
  };

  // Sluit alleen het antwoordtransport. De inhoudelijke beurt blijft actueel
  // zolang geen nieuw commando op dezelfde fysieke satellite binnenkomt.
  const expire = (id) => {
    const pending = takePending(id);
    deadlineById.delete(id);
    const controller = controllerById.get(id);
    if (controller && !controller.signal.aborted) controller.abort();
    controllerById.delete(id);
    return pending;
  };

  const revoke = (id) => {
    const pending = expire(id);
    const laneKey = laneById.get(id);
    if (laneKey && latestByLane.get(laneKey) === id) latestByLane.delete(laneKey);
    laneById.delete(id);
    return pending;
  };

  const leaseIsCurrent = (laneKey, id) => (
    latestByLane.get(laneKey) === id && laneById.get(id) === laneKey
  );

  const deadlineFor = (id, supplied = null) => (
    supplied != null && supplied !== '' && Number.isFinite(Number(supplied))
      ? Number(supplied)
      : deadlineById.get(id) ?? null
  );

  return {
    begin(lane, id, { deadlineAt = null } = {}) {
      const laneKey = normalizedVoiceLane(lane);
      const turnId = String(id || '').trim();
      if (laneKey === UNRESOLVED_VOICE_LANE) throw new Error('voice_lane_unresolved');
      if (!turnId) throw new Error('voice_turn_id_missing');
      const previousId = latestByLane.get(laneKey);
      if (previousId && previousId !== turnId) revoke(previousId);
      if (laneById.has(turnId) || controllerById.has(turnId)) revoke(turnId);
      latestByLane.set(laneKey, turnId);
      laneById.set(turnId, laneKey);
      controllerById.set(turnId, new AbortController());
      if (deadlineAt != null && deadlineAt !== '' && Number.isFinite(Number(deadlineAt))) {
        deadlineById.set(turnId, Number(deadlineAt));
      }
      // Bewust zonder deadline: alleen echte supersession beëindigt de opdracht.
      return () => leaseIsCurrent(laneKey, turnId);
    },
    track(id, lane, result, timer, satellite = '', deadlineAt = null, transport = null) {
      const laneKey = normalizedVoiceLane(lane);
      const satelliteId = normalizedSatellite(satellite);
      const deadline = deadlineFor(id, deadlineAt);
      if (!satelliteId || voiceLaneKey(satelliteId) !== laneKey
        || !leaseIsCurrent(laneKey, id) || !controllerById.has(id)
        || (deadline != null && now() >= deadline)) {
        clearTimer(timer);
        expire(id);
        return false;
      }
      takePending(id);
      pendingDelivery.set(id, {
        laneKey, result, timer, deadlineAt: deadline,
        satellite: satelliteId,
        transport: transport ? Object.freeze({
          adapterInstanceSha256: String(transport.adapterInstanceSha256 || ''),
          socketGeneration: Number(transport.socketGeneration),
          sourceRef: String(transport.sourceRef || ''),
          sourceSha256: String(transport.sourceSha256 || ''),
          sourceBodyJson: String(transport.sourceBodyJson || ''),
          offerOperationRef: String(transport.offer?.operationRef || ''),
          offerOperationSha256: String(transport.offer?.operationSha256 || ''),
          offerPayloadJson: String(transport.offer?.payloadJson || ''),
          offerPayloadSha256: String(transport.offer?.payloadSha256 || ''),
          offerReplySha256: String(transport.offer?.replySha256 || ''),
          offerReply: String(transport.offer?.reply || ''),
        }) : null,
      });
      return true;
    },
    acknowledge(id, satellite = '', transport = null) {
      const pending = pendingDelivery.get(id);
      if (!pending) return null;
      const satelliteId = normalizedSatellite(satellite);
      if (!pending.satellite || !satelliteId) return null;
      if (!leaseIsCurrent(pending.laneKey, id)
        || !controllerById.has(id)
        || (pending.deadlineAt != null && now() >= pending.deadlineAt)) {
        expire(id);
        return null;
      }
      if (pending.satellite !== satelliteId) return null;
      if (pending.transport) {
        if (pending.transport.adapterInstanceSha256
            !== String(transport?.adapterInstanceSha256 || '')
          || pending.transport.socketGeneration !== Number(transport?.socketGeneration)) {
          return null;
        }
        let offeredPayload;
        try {
          offeredPayload = JSON.parse(pending.transport.offerPayloadJson);
        } catch { return null; }
        const acknowledgement = transport?.data || {};
        if (!VOICE_REPLY_OPERATION_REF.test(pending.transport.offerOperationRef)
          || !SHA256.test(pending.transport.offerOperationSha256)
          || !SHA256.test(pending.transport.offerPayloadSha256)
          || !SHA256.test(pending.transport.offerReplySha256)
          || canonicalJson(offeredPayload) !== pending.transport.offerPayloadJson
          || sha256(pending.transport.offerPayloadJson) !== pending.transport.offerPayloadSha256
          || sha256(pending.transport.offerReply) !== pending.transport.offerReplySha256
          || String(offeredPayload.id || '') !== String(id)
          || String(offeredPayload.reply ?? '') !== pending.transport.offerReply
          || String(offeredPayload.satellite || '') !== pending.satellite
          || String(acknowledgement.delivery_operation_ref || '')
            !== pending.transport.offerOperationRef
          || String(acknowledgement.payload_sha256 || '')
            !== pending.transport.offerPayloadSha256
          || String(acknowledgement.reply_sha256 || '')
            !== pending.transport.offerReplySha256
          || String(acknowledgement.reply ?? '') !== pending.transport.offerReply
          || String(acknowledgement.conversation_id || '')
            !== String(offeredPayload.conversation_id || '')) return null;
      }
      return pending;
    },
    finalizeAcknowledgement(id, expectedPending) {
      const pending = pendingDelivery.get(id);
      if (!pending || pending !== expectedPending) return false;
      const accepted = takePending(id);
      deadlineById.delete(id);
      controllerById.delete(id);
      return accepted === expectedPending;
    },
    async deliver(id, isCurrent, send) {
      const controller = controllerById.get(id);
      const deadline = deadlineFor(id);
      if (!controller || !isCurrent() || (deadline != null && now() >= deadline)) {
        expire(id);
        return false;
      }
      let onAbort;
      const aborted = new Promise((resolve) => {
        onAbort = () => resolve(false);
        controller.signal.addEventListener('abort', onAbort, { once: true });
      });
      try {
        const offered = Promise.resolve()
          .then(() => send(controller.signal))
          .then((accepted) => accepted !== false);
        const completed = await Promise.race([offered, aborted]);
        if (!completed || !isCurrent()) {
          expire(id);
          return false;
        }
        if (deadline != null && now() >= deadline) {
          expire(id);
          return false;
        }
        return true;
      } finally {
        controller.signal.removeEventListener('abort', onAbort);
      }
    },
    finish(lane, id) {
      const laneKey = normalizedVoiceLane(lane);
      if (laneById.get(id) === laneKey) revoke(id);
    },
    expire,
  };
}

const voiceTurnGate = createVoiceTurnGate();

export function normalizeVoiceConversationId(value, sender) {
  const stripPrefix = (candidate) => String(candidate || '')
    .trim()
    .toLowerCase()
    .replace(/^(?:voice\s*:\s*)+/, '')
    .trim();
  const identity = stripPrefix(value) || stripPrefix(sender) || 'huisgenoot';
  return `voice:${identity}`;
}

export function voiceRequestInput(data = {}, id = '') {
  const rawInput = String(data.vraag || data.text || data.command || '');
  const rawText = rawInput.trim();
  const markedSynthetic = /^__eva_test__\s+/i.test(rawText);
  const syntheticTest = markedSynthetic || [true, 'true', 1, '1'].includes(
    data.synthetic_test ?? data.syntheticTest,
  );
  return {
    rawInput,
    text: markedSynthetic ? rawText.replace(/^__eva_test__\s+/i, '').trim() : rawText,
    syntheticTest,
    syntheticConversationId: syntheticTest ? `test:${id}` : '',
  };
}

function issueVoiceUnderstandingSource({
  messageId = '', satellite = '', laneKey = '', conversationId = '',
  requester = '', rawInput = '', originalText = '', isCurrent = null,
  ingressScope = '', ingressToken = '', authenticatedSource = null,
} = {}) {
  const id = typeof messageId === 'string' && VOICE_REQUEST_ID.test(messageId)
    ? messageId : '';
  const endpoint = normalizedSatellite(satellite);
  const lane = normalizedVoiceLane(laneKey);
  const conversation = String(conversationId || '');
  if (!id || !endpoint || lane !== voiceLaneKey(endpoint)
    || ingressScope !== `voice:${endpoint}` || ingressToken !== id
    || !conversation || typeof requester !== 'string'
    || typeof rawInput !== 'string' || typeof originalText !== 'string'
    || typeof isCurrent !== 'function' || isCurrent() !== true
    || authenticatedSource?.eventType !== 'eva_voice_query'
    || typeof authenticatedSource?.sourceRef !== 'string'
    || !authenticatedSource.sourceRef.startsWith('home-assistant-websocket:')
    || !/^[a-f0-9]{64}$/u.test(String(authenticatedSource?.sourceSha256 || ''))
    || typeof authenticatedSource?.sourceBodyJson !== 'string'
    || typeof authenticatedSource?.isCurrent !== 'function'
    || authenticatedSource.isCurrent() !== true) return null;
  const capability = Object.freeze({});
  issuedVoiceUnderstandingSources.set(capability, Object.freeze({
    messageId: id, satellite: endpoint, laneKey: lane, conversationId: conversation,
    requester, rawInput, originalText, isCurrent,
    ingressScope, ingressToken,
    sourceRef: authenticatedSource.sourceRef,
    sourceSha256: authenticatedSource.sourceSha256,
    sourceBodyJson: authenticatedSource.sourceBodyJson,
  }));
  return capability;
}

/** Read-only; de enige issuer blijft in de fysieke voice_query-handler. */
export function verifiedVoiceUnderstandingSource(messageContext = null, expected = {}) {
  const capability = messageContext?.voiceUnderstandingAuthority;
  const issued = capability && typeof capability === 'object'
    ? issuedVoiceUnderstandingSources.get(capability) : null;
  if (!issued
    || expected?.channel !== 'voice'
    || String(expected?.conversationId || '') !== issued.conversationId
    || String(expected?.messageId || '') !== issued.messageId
    || String(expected?.requester || '') !== issued.requester
    || String(expected?.originalText ?? '') !== issued.originalText
    || String(messageContext?.messageId || '') !== issued.messageId
    || String(messageContext?.conversationId || '') !== issued.conversationId
    || normalizedSatellite(messageContext?.voiceSatellite) !== issued.satellite
    || normalizedVoiceLane(messageContext?.voiceLaneKey) !== issued.laneKey
    || String(messageContext?.voiceRawInput ?? '') !== issued.rawInput
    || String(messageContext?.ingressScope || '') !== issued.ingressScope
    || String(messageContext?.ingressToken || '') !== issued.ingressToken
    || messageContext?.isCurrent !== issued.isCurrent
    || issued.isCurrent() !== true) return null;
  return Object.freeze({
    kind: 'voice_ingress', ref: issued.sourceRef,
    sha256: issued.sourceSha256, bodyJson: issued.sourceBodyJson, generation: 1,
    ingressScope: issued.ingressScope, ingressToken: issued.ingressToken,
  });
}

function issueVoiceDeliveryAuthority(result, delivery) {
  if (!result || typeof result !== 'object' || delivery?.accepted !== true
    || delivery?.transport !== 'home-assistant-conversation-response') return null;
  const capability = Object.freeze({});
  issuedVoiceDeliveryAuthorities.set(capability, Object.freeze({
    result,
    deliverySha256: createHash('sha256').update(canonicalJson(delivery)).digest('hex'),
  }));
  return capability;
}

/** Verifier-only: alleen een exact geauthenticeerde HA-ACK kan deze cap uitgeven. */
export function verifiedVoiceDeliveryAuthority(capability, result, delivery) {
  const issued = capability && typeof capability === 'object'
    ? issuedVoiceDeliveryAuthorities.get(capability) : null;
  if (!issued || issued.result !== result || delivery?.accepted !== true
    || delivery?.transport !== 'home-assistant-conversation-response'
    || createHash('sha256').update(canonicalJson(delivery)).digest('hex')
      !== issued.deliverySha256) return null;
  return Object.freeze({
    contract: 'HomeAssistantVoiceDeliveredReplyAuthority/v1',
    deliverySha256: issued.deliverySha256,
  });
}

export function consumeVoiceDeliveryAuthority(capability, result, delivery) {
  if (!verifiedVoiceDeliveryAuthority(capability, result, delivery)) return false;
  issuedVoiceDeliveryAuthorities.delete(capability);
  return true;
}

async function returnVoiceEvent(id, payload, isCurrent = () => true) {
  // Deze controle staat expres direct naast de netwerkgrens. Een eerdere check
  // na het denken laat nog een race toe met een nieuw binnenkomend commando.
  return voiceTurnGate.deliver(
    id,
    isCurrent,
    (signal) => {
      if (signal.aborted) return false;
      // De lease kan een coöperatieve transportcall stoppen vóór acceptatie.
      // Een reeds door Home Assistant geaccepteerd event is niet terug te halen.
      return restCall('POST', '/api/events/eva_voice_reply', { id, ...payload }, { signal });
    },
  );
}

export function startVoiceBridge({ deliveryWindowMs = VOICE_DELIVERY_WINDOW_MS } = {}) {
  if (started) return;
  started = true;
  configuredDeliveryWindowMs = Number.isFinite(Number(deliveryWindowMs))
    ? Math.max(1, Math.min(VOICE_DELIVERY_WINDOW_MS, Number(deliveryWindowMs)))
    : VOICE_DELIVERY_WINDOW_MS;
  _startupVoiceRecovery = recoverAcknowledgedVoiceDeliveries().catch((error) => {
    logTimeline('system', `Duurzaam Voice-ACK-herstel liep vast: ${errMsg(error)}`, {
      internalOnly: true,
    });
    return 0;
  }).finally(() => {
    if (pendingAcknowledgedVoiceDeliveries(db, 1).length) {
      scheduleAcknowledgedVoiceDeliveryDrain(1);
    }
  });
}

function voiceDeliveryRetryDelay(attempt) {
  return VOICE_DELIVERY_DRAIN_DELAYS_MS[Math.min(
    Math.max(0, Number(attempt) || 0), VOICE_DELIVERY_DRAIN_DELAYS_MS.length - 1,
  )];
}

function acknowledgeVoiceDeliveryRetry(authority, now = Date.now()) {
  const previous = voiceDeliveryRetryState.get(authority.requestId);
  const attempt = previous?.eventSha256 === authority.eventSha256
    ? Math.min(previous.attempt + 1, VOICE_DELIVERY_DRAIN_DELAYS_MS.length - 1) : 1;
  voiceDeliveryRetryState.set(authority.requestId, Object.freeze({
    eventSha256: authority.eventSha256,
    attempt,
    retryAt: now + voiceDeliveryRetryDelay(attempt),
  }));
}

function voiceDeliveryRetryEligible(authority, now = Date.now()) {
  const state = voiceDeliveryRetryState.get(authority.requestId);
  if (!state || state.eventSha256 !== authority.eventSha256) return true;
  return state.retryAt <= now;
}

async function projectAcknowledgedVoiceDelivery(authority) {
  const {
    deliveredReplyRecorded,
    preparedUnderstandingIngressReplay,
    recordDeliveredReply,
    verifiedPreparedUnderstandingTurnForResult,
    verifiedVoiceFinalDeliveryRecoveryForResult,
  } = await import('./agent.js');
  const result = preparedUnderstandingIngressReplay({
    channel: 'voice',
    conversationId: authority.conversationId,
    messageId: authority.requestId,
    sender: authority.requester,
  });
  let delivery;
  try { delivery = JSON.parse(authority.acknowledgement.deliveryJson); } catch { return false; }
  if (!result || canonicalJson(delivery) !== authority.acknowledgement.deliveryJson
    || !verifiedPreparedUnderstandingTurnForResult(result, {
      channel: 'voice',
      conversationId: authority.conversationId,
      messageId: authority.requestId,
      sender: authority.requester,
      sourceRef: authority.query.sourceRef,
      sourceSha256: authority.query.sourceSha256,
      sourceBodyJson: authority.query.sourceBodyJson,
      sourceGeneration: 1,
    })) return false;
  if (result.finalDeliveryBindingSha256
    && !await verifiedVoiceFinalDeliveryRecoveryForResult(result, authority, delivery)) return false;
  const deliveryAuthority = issueVoiceDeliveryAuthority(result, delivery);
  if (!deliveryAuthority) return false;
  if (recordDeliveredReply(result, delivery, deliveryAuthority)) return true;
  if (!deliveredReplyRecorded(result, delivery)) {
    throw new Error(`duurzaam Voice-ACK ${authority.requestId} kon niet worden geprojecteerd`);
  }
  return true;
}

async function drainAcknowledgedVoiceDeliveries({
  limit = VOICE_DELIVERY_DRAIN_PAGE_SIZE,
  maxVisits = VOICE_DELIVERY_DRAIN_MAX_VISITS,
} = {}) {
  const pageSize = Math.max(1, Math.min(100, Number(limit) || VOICE_DELIVERY_DRAIN_PAGE_SIZE));
  const visitLimit = Math.max(pageSize,
    Math.min(1_000, Number(maxVisits) || VOICE_DELIVERY_DRAIN_MAX_VISITS));
  let recovered = 0;
  let visited = 0;
  let cursor = voiceDeliveryDrainCursor;
  let more = false;
  while (visited < visitLimit) {
    const page = pendingAcknowledgedVoiceDeliveryPage(db, {
      limit: Math.min(pageSize, visitLimit - visited), after: cursor,
    });
    more = page.hasMore;
    if (!page.records.length) {
      cursor = null;
      more = false;
      break;
    }
    const now = Date.now();
    for (const authority of page.records) {
      visited += 1;
      if (!voiceDeliveryRetryEligible(authority, now)) continue;
      try {
        if (await projectAcknowledgedVoiceDelivery(authority)) {
          voiceDeliveryRetryState.delete(authority.requestId);
          recovered += 1;
        } else {
          acknowledgeVoiceDeliveryRetry(authority, now);
        }
      } catch (error) {
        acknowledgeVoiceDeliveryRetry(authority, now);
        logTimeline('system', `Voice-ACK ${authority.requestId} blijft herstelbaar: ${errMsg(error)}`, {
          internalOnly: true, requestId: authority.requestId,
        });
      }
    }
    cursor = page.nextCursor;
    if (!page.hasMore) {
      cursor = null;
      more = false;
      break;
    }
  }
  voiceDeliveryDrainCursor = more ? cursor : null;
  return recovered;
}

async function recoverAcknowledgedVoiceDeliveries(options = {}) {
  if (voiceDeliveryDrainPromise) return voiceDeliveryDrainPromise;
  voiceDeliveryDrainPromise = drainAcknowledgedVoiceDeliveries(options).finally(() => {
    voiceDeliveryDrainPromise = null;
  });
  return voiceDeliveryDrainPromise;
}

function scheduleAcknowledgedVoiceDeliveryDrain(attempt = 0) {
  voiceDeliveryDrainRequestGeneration += 1;
  if (voiceDeliveryDrainTimer) return;
  const scheduledGeneration = voiceDeliveryDrainRequestGeneration;
  voiceDeliveryDrainTimer = setTimeout(async () => {
    voiceDeliveryDrainTimer = null;
    await recoverAcknowledgedVoiceDeliveries();
    const requestedWhileRunning = voiceDeliveryDrainRequestGeneration > scheduledGeneration;
    if (voiceDeliveryDrainCursor || requestedWhileRunning) {
      scheduleAcknowledgedVoiceDeliveryDrain(0);
    } else if (pendingAcknowledgedVoiceDeliveries(db, 1).length) {
      scheduleAcknowledgedVoiceDeliveryDrain(attempt + 1);
    }
  }, voiceDeliveryRetryDelay(attempt));
  voiceDeliveryDrainTimer.unref?.();
}

function scheduleAuthenticatedVoiceAckRetry(capability, attempt) {
  if (!capability || typeof capability !== 'object'
    || scheduledVoiceAckRetries.has(capability)
    || attempt >= VOICE_ACK_RETRY_DELAYS_MS.length) return;
  const timer = setTimeout(() => {
    scheduledVoiceAckRetries.delete(capability);
    processAuthenticatedHaVoiceReplyAck(capability, attempt + 1).catch(() => {});
  }, VOICE_ACK_RETRY_DELAYS_MS[attempt]);
  timer.unref?.();
  scheduledVoiceAckRetries.set(capability, timer);
}

export async function acceptAuthenticatedHaVoiceReplyAck(capability) {
  return processAuthenticatedHaVoiceReplyAck(capability, 0);
}

async function processAuthenticatedHaVoiceReplyAck(capability, attempt) {
  if (!started) return false;
  const authenticatedSource = await verifiedVoiceStoreEventSource(
    capability, 'eva_voice_reply_ack',
  );
  if (!authenticatedSource) return false;
  const data = authenticatedSource.data;
  const id = String(data.id || '').trim();
  if (!id || ![true, 'true', 1, '1'].includes(data.accepted)) return false;
  const pending = voiceTurnGate.acknowledge(id, data.satellite, authenticatedSource);
  if (!pending) return false;
  const query = pending.transport;
  let queryBody;
  let offerPayload;
  try { queryBody = JSON.parse(String(query?.sourceBodyJson || '')); } catch { return false; }
  try { offerPayload = JSON.parse(String(query?.offerPayloadJson || '')); } catch { return false; }
  const rebuiltOffer = exactVoiceReplyOffer(id, offerPayload, query);
  if (!query || !/^home-assistant-websocket:[a-f0-9]{64}$/u.test(query.sourceRef)
    || !/^[a-f0-9]{64}$/u.test(query.sourceSha256)
    || createHash('sha256').update(query.sourceBodyJson).digest('hex') !== query.sourceSha256
    || canonicalJson(queryBody) !== query.sourceBodyJson
    || queryBody.eventType !== 'eva_voice_query'
    || String(queryBody.event?.event?.data?.id || '') !== id
    || String(queryBody.event?.event?.data?.satellite || '') !== String(data.satellite || '')
    || queryBody.adapterInstanceSha256 !== query.adapterInstanceSha256
    || Number(queryBody.socketGeneration) !== query.socketGeneration
    || canonicalJson(offerPayload) !== query.offerPayloadJson
    || !rebuiltOffer
    || rebuiltOffer.operationRef !== query.offerOperationRef
    || rebuiltOffer.operationSha256 !== query.offerOperationSha256
    || rebuiltOffer.payloadSha256 !== query.offerPayloadSha256
    || rebuiltOffer.replySha256 !== query.offerReplySha256
    || rebuiltOffer.reply !== query.offerReply) {
    return false;
  }
  let acknowledgementCommitted = false;
  try {
    const delivery = {
      accepted: true,
      transport: 'home-assistant-conversation-response',
      acknowledgement: 'eva_voice_reply_ack',
      satellite: pending.satellite || null,
      socketGeneration: authenticatedSource.socketGeneration,
      authorityEvidence: Object.freeze({
        contract: 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2',
        requestId: id,
        satellite: pending.satellite,
        adapterInstanceSha256: authenticatedSource.adapterInstanceSha256,
        socketGeneration: authenticatedSource.socketGeneration,
        query: Object.freeze({
          sourceRef: query.sourceRef,
          sourceSha256: query.sourceSha256,
          sourceBodyJson: query.sourceBodyJson,
        }),
        offer: Object.freeze({
          operationRef: query.offerOperationRef,
          operationSha256: query.offerOperationSha256,
          payloadJson: query.offerPayloadJson,
          payloadSha256: query.offerPayloadSha256,
          replySha256: query.offerReplySha256,
        }),
        acknowledgement: Object.freeze({
          sourceRef: authenticatedSource.sourceRef,
          sourceSha256: authenticatedSource.sourceSha256,
          sourceBodyJson: authenticatedSource.sourceBodyJson,
        }),
      }),
    };
    const deliveryJson = canonicalJson(delivery);
    recordVoiceAckReceipt(db, {
      requestId: id,
      authenticatedSource,
      deliveryJson,
      deliverySha256: sha256(deliveryJson),
    });
    acknowledgementCommitted = true;
    if (!await consumeVoiceStoreEventSource(
      capability, 'eva_voice_reply_ack', authenticatedSource,
    )) throw new Error('duurzaam Voice-ACK verloor haar postcommit source-consumptie');
    if (!voiceTurnGate.finalizeAcknowledgement(id, pending)) {
      throw new Error('duurzaam Voice-ACK verloor haar postcommit pending-finalisatie');
    }
    scheduleAcknowledgedVoiceDeliveryDrain(0);
  } catch (error) {
    logTimeline('system', `Voice-ACK kon niet worden vastgelegd: ${errMsg(error)}`, {
      internalOnly: true,
    });
    if (acknowledgementCommitted) scheduleAcknowledgedVoiceDeliveryDrain(0);
    else scheduleAuthenticatedVoiceAckRetry(capability, attempt);
    return false;
  }
  return true;
}

export async function acceptAuthenticatedHaVoiceQuery(capability) {
  if (!started) return false;
  const authenticatedSource = await verifiedVoiceStoreEventSource(
    capability, 'eva_voice_query',
  );
  if (!authenticatedSource) return false;
  const data = authenticatedSource.data;
  const id = typeof data.id === 'string' && VOICE_REQUEST_ID.test(data.id)
      ? data.id : '';
  if (!id) {
    logTimeline('voice_rejected', 'Voice-beurt geweigerd: duurzaam request-id ontbreekt.', {
      internalOnly: true,
      id: null,
      reason: 'missing_request_id',
    });
    return false;
  }
  const request = voiceRequestInput(data, id);
  if (!request.text) return false;
  const provenance = voiceRequestProvenance(data);
  if (!provenance.satellite) {
    logTimeline('voice_rejected', `Voice-beurt ${id} geweigerd: fysieke Assist-satellite ontbreekt.`, {
      internalOnly: true,
      id,
      reason: 'unresolved_satellite',
    });
    return false;
  }
  const ingressDeadlineAt = Date.now() + configuredDeliveryWindowMs;
  if (active.has(id)) return false;
  const { rawInput, text, syntheticTest } = request;
  const { sender } = provenance;
  const conversationId = normalizeVoiceConversationId(
    data.conversation_id || data.session_id,
    request.syntheticConversationId || provenance.satellite || sender,
  );
  let durableQuery;
  try {
    durableQuery = recordVoiceQueryAuthority(db, {
      requestId: id,
      conversationId,
      requester: sender,
      satellite: provenance.satellite,
      authenticatedSource,
    });
    if (!await consumeVoiceStoreEventSource(
      capability, 'eva_voice_query', authenticatedSource,
    )) throw new Error('duurzame Voice-query verloor haar postcommit source-consumptie');
  } catch (error) {
    logTimeline('voice_rejected', `Voice-beurt ${id} botst met duurzaam querybewijs.`, {
      internalOnly: true, id, reason: errMsg(error),
    });
    return false;
  }
  if (durableQuery?.stage !== 'query') return false;
  active.add(id);
  let isCurrent = () => true;
  const laneKey = voiceLaneKey(provenance.satellite);

  (async () => {
      const laneIsCurrent = voiceTurnGate.begin(laneKey, id, {
        deadlineAt: ingressDeadlineAt,
      });
      isCurrent = () => laneIsCurrent() && authenticatedSource.isCurrent() === true;
      const startedAt = performance.now();
      const ingressScope = `voice:${provenance.satellite}`;
      const ingressToken = id;
      const voiceUnderstandingAuthority = issueVoiceUnderstandingSource({
        messageId: id, satellite: provenance.satellite, laneKey, conversationId,
        requester: sender, rawInput, originalText: text, isCurrent,
        ingressScope, ingressToken,
        authenticatedSource,
      });
      if (!voiceUnderstandingAuthority) {
        throw new Error('voice_ingress_authority_unavailable');
      }
      const { handleMessageDetailed } = await import('./agent.js');
      const result = await handleMessageDetailed({
        channel: 'voice',
        sender,
        text,
        messageContext: {
          identityTrusted: provenance.identityTrusted,
          conversationId,
          messageId: id,
          voiceSatellite: provenance.satellite || null,
          voiceLaneKey: laneKey,
          voiceRawInput: rawInput,
          voiceUnderstandingAuthority,
          ingressScope,
          ingressToken,
          // Gespreksgeheugen blijft per persoon/sessie, maar een trage oude
          // voicebeurt mag een nieuwe uitspraak niet achter dezelfde queue zetten.
          queueKey: `${conversationId}:${id}`,
          isCurrent,
          syntheticTest,
        },
      });
      if (!isCurrent() || result.yielded || result.superseded) {
        recoverInterruptedUnderstandingIngresses({
          channel: 'voice', conversationId, messageId: id, createHandoff: false,
        });
        logTimeline('voice_superseded', `Voice-beurt ${id} is ingehaald; late tekst is niet afgeleverd.`, {
          internalOnly: true,
          id,
          conversationId,
          laneKey,
          turnId: result.turnId || null,
        });
        return;
      }
      const remainingDeliveryMs = ingressDeadlineAt - Date.now();
      if (remainingDeliveryMs <= 0) {
        voiceTurnGate.expire(id);
        logTimeline('voice_delivery_expired', `Voice-beurt ${id} rondde af na de antwoordtermijn; late tekst is niet afgeleverd.`, {
          internalOnly: true,
          id,
          conversationId,
          laneKey,
          turnId: result.turnId || null,
        });
        throw new Error('voice_delivery_expired');
      }
      if (!String(result.reply || '').trim()) throw new Error('voice_reply_empty');
      const replyPayload = voiceReplyPayload({
        reply: result.reply,
        question: text,
        responseTimeMs: performance.now() - startedAt,
        conversationId,
        satellite: provenance.satellite,
      });
      const offer = exactVoiceReplyOffer(id, replyPayload, authenticatedSource);
      if (!offer) throw new Error('voice_reply_offer_binding_unavailable');
      const timer = setTimeout(() => {
        const pending = voiceTurnGate.expire(id);
        if (!pending) return;
        recoverInterruptedUnderstandingIngresses({
          channel: 'voice', conversationId, messageId: id,
        });
        logTimeline('voice_delivery_unconfirmed', `Home Assistant bevestigde voiceantwoord ${id} niet binnen de luistertermijn.`, {
          internalOnly: true,
          id,
          conversationId,
          laneKey,
          turnId: result.turnId || null,
        });
      }, remainingDeliveryMs);
      if (!voiceTurnGate.track(
        id, laneKey, result, timer, provenance.satellite, ingressDeadlineAt,
        { ...authenticatedSource, offer },
      )) {
        throw new Error('voice_delivery_track_rejected');
      }
      recordVoiceOfferIntent(db, {
        requestId: id,
        turnId: result.turnId,
        operationRef: offer.operationRef,
        operationSha256: offer.operationSha256,
        payloadJson: offer.payloadJson,
        payloadSha256: offer.payloadSha256,
        replySha256: offer.replySha256,
      });
      const offered = await returnVoiceEvent(id, offer.eventData, isCurrent);
      if (!offered) throw new Error('voice_delivery_not_offered');
      logTimeline('voice_reply_offered', `Voiceantwoord ${id} is exact aan HA aangeboden.`, {
        internalOnly: true,
        id,
        conversationId,
        satellite: provenance.satellite,
        turnId: result.turnId || null,
        deliveryOperationRef: offer.operationRef,
        deliveryOperationSha256: offer.operationSha256,
        payloadSha256: offer.payloadSha256,
        replySha256: offer.replySha256,
      });
  })().catch((error) => {
      logTimeline('system', `Voice-beurt liep vast: ${errMsg(error)}`, { internalOnly: true });
      recoverInterruptedUnderstandingIngresses({
        channel: 'voice', conversationId, messageId: id, createHandoff: isCurrent(),
      });
      voiceTurnGate.expire(id);
  }).finally(() => {
      const cleanupTimer = setTimeout(() => {
        active.delete(id);
        if (laneKey) voiceTurnGate.finish(laneKey, id);
      }, 60_000);
      cleanupTimer.unref?.();
  });
  return true;
}
