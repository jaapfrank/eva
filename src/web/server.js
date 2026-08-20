import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { config } from '../config.js';
import { runtimeVersion } from '../runtimeversion.js';
import { bus } from '../bus.js';
import db, {
  allPeople,
  archiveMemoryHygieneCandidates,
  emotionHistory,
  getTimeline,
  listArchivedMemories,
  memoryHygienePreview,
  memoryPage,
  openReminders,
  reviewMemoryAction,
  restoreMemoryHygieneArchive,
} from '../db.js';
import { canonicalJson, errMsg, normalizeUserFacingText } from '../util.js';
import {
  agentStatus,
  deliveredReplyRecorded,
  handleMessageDetailed,
  preparedUnderstandingIngressReplay,
  recordDeliveredReply,
  verifiedPreparedUnderstandingTurnForResult,
} from '../mind/agent.js';
import { verifyPreparedUnderstandingTurn } from '../mind/understanding.js';
import { getMood, moodSummary } from '../mind/emotions.js';
import { llmRuntimeStatus } from '../brain/llm.js';
import { authStatus, startLoginFlow } from '../brain/oauth.js';
import { cachedStates, haConnected } from '../integrations/homeassistant.js';
import { getAppearance } from '../mind/appearance.js';
import { autonomyStatus } from '../mind/autonomy.js';
import { listCapabilities } from '../mind/capabilities.js';
import { behaviorLearningStatus } from '../mind/learning.js';
import { perceptionAttentionStatus } from '../mind/perceptionattention.js';
import { normalizeVoiceConversationId } from '../mind/voicebridge.js';
import {
  assertSafeWebBinding,
  familyTimelineEntry,
  normalizeWebConversationId,
  requestAuthorized,
} from './access.js';
import {
  canonicalPublicRequestId,
  PUBLIC_REQUEST_REPLAY_SCOPE,
} from './requestidentity.js';
import {
  beginHttpIngress,
  httpIngressStoreStatus,
  issueVerifiedHttpResultAuthority,
  prepareHttpResponse,
  releaseHttpIngressAuthority,
  renewHttpIngressAuthority,
  settleHttpIngress,
} from './httpingress.js';
import {
  acknowledgeHttpDelivery,
  abandonHttpDeliveryWrite,
  abandonHttpDeliveryTransmission,
  beginHttpDelivery,
  claimHttpDeliveryTransmission,
  claimHttpDeliveryWrite,
  finishHttpDeliveryTransmission,
  finishHttpDeliveryWrite,
  httpDeliveryStoreStatus,
  httpDeliveryTransmissionAuthorityCurrent,
  httpDeliveryWriteAuthorityCurrent,
} from './httpdelivery.js';
import {
  httpResponseAuthorityStoreStatus,
  issueHttpResponseAuthority,
} from './httpresponseauthority.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUS_EVENTS = ['timeline', 'mood', 'activity', 'chat', 'auth', 'house'];
const HEALTH_ATTENTION_KINDS = new Set(['aspiration', 'exploration', 'goal']);

function publicErrorText(error) {
  return normalizeUserFacingText(errMsg(error)).trim() || 'onbekende fout';
}
const FAMILY_AUTONOMY_DISPLAY_KEYS = Object.freeze([
  'choice_label', 'reason_label', 'result_expires_ts', 'result_label', 'result_ts',
  'selection_expires_ts', 'selection_ts',
].sort());
const FAMILY_AUTONOMY_DISPLAY_TEXT = /^[\p{L}\p{M}\p{N}][\p{L}\p{M}\p{N}\s.,;:!?'"()°%/+\-\u2013\u2014]+$/u;
const FAMILY_AUTONOMY_INTERNAL_REF = /(?:^|\s)(?:aspiration|exploration|goal|work|worker|episode|turn|signal|request|receipt|tool)(?::|#)[a-z0-9._:-]+(?:$|\s|[.,;!?])/iu;
const FAMILY_AUTONOMY_UUID = /(?:^|[^a-f0-9])[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}(?:$|[^a-f0-9])/iu;
const FAMILY_AUTONOMY_HASH = /(?:^|[^a-f0-9])(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})(?:$|[^a-f0-9])/iu;
const ACTIVE_HTTP_REQUESTS = new Map();
const DELIVERED_HTTP_REQUESTS = new Set();
const ACK_BODY_LIMIT_BYTES = 2_048;
const ACK_ATTEMPT_LIMIT = 32;
const ACK_ATTEMPT_WINDOW_MS = 60_000;
const ACK_RATE_CLIENT_LIMIT = 1_024;
const WRITE_ATTEMPT_REF = /^http-write-attempt-v3:[a-f0-9]{64}$/u;

export function publicBusEventData(name, data) {
  if (name !== 'house') return data;
  if (!data || typeof data !== 'object') return data;
  const type = String(data.type || '').toLowerCase();
  const containsCameraMaterial = Buffer.isBuffer(data?.frame?.buffer)
    || (Array.isArray(data.frames) && data.frames.some((frame) => Buffer.isBuffer(frame?.buffer)));
  if (['doorbell', 'person_detected'].includes(type) || containsCameraMaterial) return null;
  return data;
}

export { familyTimelineEntry } from './access.js';

function positiveInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function nonNegativeInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

function optionalBoolean(value) {
  return typeof value === 'boolean' ? value : null;
}

function safeTimestamp(value) {
  const text = String(value || '');
  const milliseconds = Date.parse(text);
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
}

function safeRuntimeIdentifier(value, pattern) {
  const text = String(value || '');
  return pattern.test(text) ? text : null;
}

function healthSchedulerSelection(selection) {
  if (!selection || typeof selection !== 'object') return null;
  const kind = String(selection.attention?.kind || '');
  const attentionId = positiveInteger(selection.attention?.id);
  const attention = HEALTH_ATTENTION_KINDS.has(kind) && attentionId
    ? { kind, id: attentionId } : null;
  return {
    attention,
    work_id: positiveInteger(selection.work_id),
    goal_episode_id: positiveInteger(selection.goal_episode_id),
    signal_batch_id: safeRuntimeIdentifier(
      selection.signal_batch_id,
      /^signals:[a-f0-9]{24}$/u,
    ),
    signal_count: nonNegativeInteger(selection.signal_count),
  };
}

function healthAgentProjection(status) {
  return {
    active_turns: nonNegativeInteger(status?.active_turns),
    interactive_pending: nonNegativeInteger(status?.interactive_pending),
    interaction_epoch: nonNegativeInteger(status?.interaction_epoch),
  };
}

function healthLastAutonomyResult(result) {
  if (!result || typeof result !== 'object') return null;
  return {
    turn_id: safeRuntimeIdentifier(result.turn_id, /^[a-z0-9:._-]{1,160}$/iu),
    productive: optionalBoolean(result.productive),
    progressed: optionalBoolean(result.progressed),
    yielded: optionalBoolean(result.yielded),
    tool: safeRuntimeIdentifier(result.tool, /^[a-z0-9_]{1,80}$/iu),
    signals_handled: optionalBoolean(result.signals_handled),
    signals_acknowledged: nonNegativeInteger(result.signals_acknowledged),
    signals_deferred: nonNegativeInteger(result.signals_deferred),
  };
}

function healthAutonomyProjection(status) {
  return {
    enabled: status?.enabled === true,
    scheduler_started: status?.scheduler_started === true,
    running: status?.running === true,
    current_selection: healthSchedulerSelection(status?.current_selection),
    last_run_ts: safeTimestamp(status?.last_run_ts),
    next_run_ts: safeTimestamp(status?.next_run_ts),
    last_result: healthLastAutonomyResult(status?.last_result),
    pending_signal_count: Array.isArray(status?.pending_signals)
      ? status.pending_signals.length : 0,
    quarantined_signal_count: Array.isArray(status?.quarantined_signals)
      ? status.quarantined_signals.length : 0,
  };
}

function healthRuntimeProjection() {
  return {
    mode: config.runtimeMode,
    recovery: config.recoveryMode,
    external_effects_enabled: config.externalEffectsEnabled,
  };
}

function familyAutonomyDisplayText(value, limit) {
  if (typeof value !== 'string') return null;
  const text = value.normalize('NFKC').replace(/\s+/gu, ' ').trim();
  if (text !== value || text.length < 12 || text.length > limit
    || !FAMILY_AUTONOMY_DISPLAY_TEXT.test(text) || /^\d+$/u.test(text)
    || /^worker$/iu.test(text) || FAMILY_AUTONOMY_INTERNAL_REF.test(text)
    || FAMILY_AUTONOMY_UUID.test(text) || FAMILY_AUTONOMY_HASH.test(text)) return null;
  return (text.match(/\p{L}{2,}/gu) || []).length >= 3 ? text : null;
}

function familyAutonomyDisplayProjection(display, { allowSelection = true } = {}) {
  const exact = display && typeof display === 'object' && !Array.isArray(display)
    && JSON.stringify(Object.keys(display).sort()) === JSON.stringify(FAMILY_AUTONOMY_DISPLAY_KEYS)
    ? display : null;
  const choice = allowSelection ? familyAutonomyDisplayText(exact?.choice_label, 360) : null;
  const reason = allowSelection ? familyAutonomyDisplayText(exact?.reason_label, 360) : null;
  const selectionTs = choice && reason ? safeTimestamp(exact?.selection_ts) : null;
  const selectionExpiresTs = selectionTs ? safeTimestamp(exact?.selection_expires_ts) : null;
  const selectionWindow = selectionTs && selectionExpiresTs
    && Date.parse(selectionExpiresTs) >= Date.parse(selectionTs);
  const result = familyAutonomyDisplayText(exact?.result_label, 500);
  const resultTs = result ? safeTimestamp(exact?.result_ts) : null;
  const resultExpiresTs = resultTs ? safeTimestamp(exact?.result_expires_ts) : null;
  const resultWindow = resultTs && resultExpiresTs
    && Date.parse(resultExpiresTs) >= Date.parse(resultTs);
  return {
    choice_label: selectionWindow ? choice : null,
    reason_label: selectionWindow ? reason : null,
    selection_ts: selectionWindow ? selectionTs : null,
    selection_expires_ts: selectionWindow ? selectionExpiresTs : null,
    result_label: resultWindow ? result : null,
    result_ts: resultWindow ? resultTs : null,
    result_expires_ts: resultWindow ? resultExpiresTs : null,
  };
}

function familyAutonomyProjection(status) {
  const base = healthAutonomyProjection(status);
  const workId = positiveInteger(base.current_selection?.work_id);
  const selectedWork = /** @type {any} */ (db.prepare(
    'SELECT visible_to_family FROM eva_work_items WHERE id=?',
  ).get(workId));
  const selectedWorkVisible = !workId || selectedWork?.visible_to_family === 1;
  const signalSelection = Number(base.current_selection?.signal_count || 0) > 0;
  return {
    ...base,
    current_selection: selectedWorkVisible ? base.current_selection : null,
    display: familyAutonomyDisplayProjection(status?.display, {
      allowSelection: selectedWorkVisible && !signalSelection,
    }),
  };
}

function countsSinceMidnight() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return {
    memories: (/** @type {{c: number}} */ (
      db.prepare('SELECT COUNT(*) AS c FROM memories WHERE COALESCE(archived,0)=0').get()
    )).c,
    memoriesArchived: (/** @type {{c: number}} */ (
      db.prepare('SELECT COUNT(*) AS c FROM memories WHERE COALESCE(archived,0)=1').get()
    )).c,
    messagesToday: (/** @type {{c: number}} */ (
      db.prepare('SELECT COUNT(*) AS c FROM main.messages WHERE ts >= ?')
        .get(start.toISOString())
    )).c,
    remindersOpen: (/** @type {{c: number}} */ (
      db.prepare("SELECT COUNT(*) AS c FROM reminders WHERE status = 'open'").get()
    )).c,
  };
}

function requestIdentityError(reason) {
  if (reason === 'mismatch') {
    return 'request_id en Idempotency-Key moeten bytegelijk zijn.';
  }
  if (reason === 'invalid') {
    return 'request_id of Idempotency-Key heeft geen geldig canoniek formaat.';
  }
  return 'Geef request_id of Idempotency-Key mee.';
}

function issueServerHttpResultAuthority(result, preparationAuthority) {
  return issueVerifiedHttpResultAuthority(result, preparationAuthority);
}

function resultProof({ result, identity, intent, sender, verifyResult }) {
  const proof = verifyResult(result, {
    channel: identity.channel, conversationId: identity.conversationId,
    messageId: identity.requestId, rawText: intent.rawText, sender,
  });
  return verifyPreparedUnderstandingTurn(proof, {
    channel: identity.channel, conversationId: identity.conversationId,
    messageId: identity.requestId,
  });
}

function renewalLoop(authority, initialLeaseUntil) {
  let active = true;
  let leaseUntil = Date.parse(String(initialLeaseUntil || ''));
  let timer = null;
  const schedule = () => {
    if (!active) return;
    const remaining = Number.isFinite(leaseUntil) ? leaseUntil - Date.now() : 3_000;
    const delay = Math.max(50, Math.min(5_000, Math.floor(remaining / 3)));
    timer = setTimeout(() => {
      if (!active) return;
      try {
        const receipt = renewHttpIngressAuthority(authority);
        if (!receipt) { active = false; return; }
        leaseUntil = Date.parse(receipt.leaseUntil);
        schedule();
      } catch { active = false; }
    }, delay);
    timer.unref?.();
  };
  schedule();
  return () => {
    active = false;
    if (timer) clearTimeout(timer);
  };
}

function transientHttpOutcome(identity, reason = 'request_in_progress') {
  return {
    status: 503, replayed: false,
    body: {
      error: 'Dezelfde request wordt elders verwerkt; probeer exact deze request_id opnieuw.',
      ok: false,
      execution_pending: true, reason,
      conversation_id: identity.conversationId, turn_id: null,
      request_id: identity.requestId,
      request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      runtime_code_sha256: runtimeVersion().loaded_code_sha256,
    },
  };
}

function deliveryFailureOutcome(identity, error) {
  return {
    status: 503, body: {
      error: `Duurzame HTTP-aflevering is nog niet bevestigd: ${publicErrorText(error)}`,
      ok: false, request_id: identity.requestId,
      request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
    },
  };
}

function currentAttestedPublicApiResponse(response) {
  const body = response?.publicBody;
  if (!body || typeof body !== 'object'
    || body.conversation_id !== response.conversationId
    || body.turn_id !== response.turnId
    || body.request_id !== response.requestId
    || body.request_replay_scope !== PUBLIC_REQUEST_REPLAY_SCOPE
    || !/^[a-f0-9]{64}$/u.test(String(body.runtime_code_sha256 || ''))
    || [
      'conversationId', 'turnId', 'requestId', 'requestReplayScope',
      'runtimeCodeSha256', 'conversationActive', 'continueListening',
      'listenTimeoutMs', 'executionPending', 'durableHandoff',
    ].some((key) => Object.hasOwn(body, key))) return false;
  if (response.channel !== 'voice') {
    return !['conversation_active', 'continue_listening', 'listen_timeout_ms']
      .some((key) => Object.hasOwn(body, key));
  }
  return typeof body.conversation_active === 'boolean'
    && typeof body.continue_listening === 'boolean'
    && Number.isSafeInteger(body.listen_timeout_ms)
    && body.listen_timeout_ms >= 0 && body.listen_timeout_ms <= 120_000;
}

function legacyPublicBodyOutcome(identity) {
  return {
    status: 409, body: {
      error: 'Deze oudere response attesteert niet de huidige publieke API-vorm en kan niet veilig replayen.',
      ok: false, reason: 'legacy_unattested_public_body',
      request_id: identity.requestId,
      request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
    },
  };
}

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function acknowledgedWireIdentity(acknowledged) {
  let body;
  try { body = JSON.parse(acknowledged.wire.bodyBytes.toString('utf8')); } catch {
    throw new Error('bevestigde HTTP-wire bevat geen exact JSON-object');
  }
  const requestIdentity = canonicalPublicRequestId({ bodyRequestId: body?.request_id });
  const conversationId = typeof body?.conversation_id === 'string'
    ? body.conversation_id : '';
  if (!requestIdentity.ok || !conversationId || conversationId !== conversationId.trim()) {
    throw new Error('bevestigde HTTP-wire mist haar geattesteerde API-identiteit');
  }
  const matches = ['web', 'voice'].filter((channel) => (
    `http-ingress:${sha256(canonicalJson({
      channel, conversationId, requestId: requestIdentity.requestId,
    }))}` === acknowledged.delivery.requestRef
  ));
  if (matches.length !== 1) {
    throw new Error('bevestigde HTTP-wire mist een unieke ingressidentiteit');
  }
  const publicBody = { ...body };
  delete publicBody._httpDelivery;
  return {
    body, publicBody, channel: matches[0], conversationId,
    requestId: requestIdentity.requestId,
  };
}

async function reconcileAcknowledgedHttpDelivery({ acknowledged, replayResult, recordDelivery,
  verifyResult, verifyDelivery, sender }) {
  if (!acknowledged?.delivered || acknowledged.kind !== 'acknowledged') {
    throw new Error('HTTP-aflevering mist een geldige client-ACK');
  }
  const identity = acknowledgedWireIdentity(acknowledged);
  if (Object.hasOwn(identity.publicBody, 'error')) return true;
  if (!['turn_result', 'durable_handoff'].includes(
    acknowledged.delivery.provenance.kind,
  )) return true;
  const knownResult = replayResult({
    channel: identity.channel, conversationId: identity.conversationId,
    messageId: identity.requestId, sender,
  });
  if (knownResult?.syntheticTest === true) return true;
  const requestRef = acknowledged.delivery.requestRef;
  if (knownResult && verifyDelivery(knownResult)) {
    DELIVERED_HTTP_REQUESTS.add(requestRef);
    return true;
  }
  if (DELIVERED_HTTP_REQUESTS.has(requestRef)) return true;
  const replayedResult = knownResult;
  if (!replayedResult
    || String(replayedResult.turnId || '') !== String(acknowledged.delivery.turnId || '')) {
    throw new Error('duurzaam HTTP-antwoord mist herstelbaar exact agentresultaat');
  }
  const recoveredProof = verifyResult(replayedResult, {
    channel: identity.channel, conversationId: identity.conversationId,
    messageId: identity.requestId, sender,
  });
  if (!recoveredProof) {
    throw new Error('duurzaam HTTP-antwoord mist exact heruitgegeven agentresultaat');
  }
  const result = deliveryResult({
    publicBody: identity.publicBody,
    state: acknowledged.delivery.provenance.kind === 'durable_handoff'
      ? 'handoff' : 'prepared',
  }, replayedResult);
  if (!result) throw new Error('duurzaam HTTP-antwoord mist exacte afleverbinding');
  const recorded = recordDelivery(result, {
    accepted: true, transport: identity.channel === 'voice' ? 'http-voice' : 'http',
    durableHttpRequestRef: requestRef,
    clientAckReceiptRef: acknowledged.ack.ackReceiptRef,
    httpWireSha256: acknowledged.delivery.wireSha256,
  });
  if (!recorded && !verifyDelivery(result)) {
    throw new Error('duurzaam HTTP-antwoord mist exacte afleverreceipt');
  }
  DELIVERED_HTTP_REQUESTS.add(requestRef);
  return true;
}

function deliveryResult(response, result) {
  const publicReply = String(response?.publicBody?.reply || '').trim();
  const resultReply = String(result?.reply || '').trim();
  if (!publicReply) return null;
  if (publicReply === resultReply) return result;
  if (response?.state !== 'handoff' || result?.finalDeliveryBindingSha256) return null;
  return Object.freeze({
    ...result, reply: publicReply, durableHandoff: true, executionPending: true,
  });
}

async function durableHttpOutcome({ response, responseAuthority, replayed, replayResult,
  recordDelivery, verifyResult, verifyDelivery, sender, identity }) {
  if (!currentAttestedPublicApiResponse(response)) {
    return legacyPublicBodyOutcome(identity);
  }
  const issued = issueHttpResponseAuthority(responseAuthority);
  if (issued.kind !== 'issued' || !issued.authority) {
    throw new Error(`HTTP-responseauthority weigerde: ${issued.kind || 'unknown'}`);
  }
  const begun = beginHttpDelivery(issued.authority);
  if (begun.kind === 'acknowledged') {
    await reconcileAcknowledgedHttpDelivery({
      acknowledged: begun, replayResult, recordDelivery, verifyResult, verifyDelivery, sender,
    });
    const transmission = claimHttpDeliveryTransmission(begun.delivery);
    if (transmission.kind !== 'write' || !transmission.transmissionAuthority
      || !transmission.wire) {
      throw new Error(`HTTP-wire-retrieval is nog niet schrijfbaar: ${
        transmission.kind || 'unknown'}`);
    }
    return {
      status: transmission.wire.status, wire: transmission.wire,
      transmissionAuthority: transmission.transmissionAuthority,
      writeAttemptRef: transmission.writeAttemptRef,
      writeGeneration: transmission.writeGeneration, replayed,
    };
  }
  if (begun.kind !== 'prepared' || !begun.delivery) {
    throw new Error(`HTTP-deliveryvoorbereiding weigerde: ${begun.kind || 'unknown'}`);
  }
  const claimed = claimHttpDeliveryWrite(begun.delivery);
  if (claimed.kind === 'write' && claimed.writeAuthority && claimed.wire) {
    return {
      status: claimed.wire.status, wire: claimed.wire,
      writeAuthority: claimed.writeAuthority,
      writeAttemptRef: claimed.writeAttemptRef,
      writeGeneration: claimed.writeGeneration, replayed,
    };
  }
  if (claimed.kind !== 'awaiting_client_ack') {
    throw new Error(`HTTP-wire is nog niet schrijfbaar: ${claimed.kind || 'unknown'}`);
  }
  const transmission = claimHttpDeliveryTransmission(begun.delivery);
  if (transmission.kind !== 'write' || !transmission.transmissionAuthority
    || !transmission.wire) {
    throw new Error(`HTTP-wire-retrieval is nog niet schrijfbaar: ${
      transmission.kind || 'unknown'}`);
  }
  return {
    status: transmission.wire.status, wire: transmission.wire,
    transmissionAuthority: transmission.transmissionAuthority,
    writeAttemptRef: transmission.writeAttemptRef,
    writeGeneration: transmission.writeGeneration, replayed,
  };
}

async function executeDurableHttpRequest({
  identity, intent, sender, execute, verifyResult, replayResult, recordDelivery,
  verifyDelivery,
  afterSettled = null,
}) {
  const input = {
    channel: identity.channel, conversationId: identity.conversationId,
    requestId: identity.requestId, intent,
  };
  let begun;
  try { begun = beginHttpIngress(input); } catch (error) {
    if (error instanceof TypeError) {
      return {
        status: 400, body: {
          error: 'client_ack_public_key is geen canonieke P-256 verificatiesleutel.',
          ok: false, reason: 'client_ack_key_invalid', request_id: identity.requestId,
          request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
        },
      };
    }
    return {
      status: 503, body: {
        error: `Duurzame HTTP-ingress is niet beschikbaar: ${publicErrorText(error)}`,
        ok: false, request_id: identity.requestId,
        request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      },
    };
  }
  if (begun.kind === 'rejected') {
    return {
      status: 400, body: {
        error: 'Een duurzame client-ACK-sleutel is verplicht voor deze request.',
        ok: false, reason: begun.reason, request_id: identity.requestId,
        request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      },
    };
  }
  if (begun.kind === 'legacy_unacknowledged') {
    return {
      status: 409, body: {
        error: 'Deze oudere request heeft geen client-ACK-binding en kan niet veilig replayen.',
        ok: false, reason: begun.reason, request_id: identity.requestId,
        request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      },
    };
  }
  if (begun.kind === 'legacy_unattested_public_body') {
    return legacyPublicBodyOutcome(identity);
  }
  if (begun.kind === 'conflict') {
    return {
      status: 409, body: {
        error: 'Deze request_id is al aan andere HTTP-inhoud gebonden.',
        ok: false, reason: begun.reason, request_id: identity.requestId,
        request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      },
    };
  }
  if (begun.kind === 'replay') {
    try {
      return await durableHttpOutcome({
        response: begun.response, responseAuthority: begun.responseAuthority,
        replayed: true, replayResult, recordDelivery, verifyResult, verifyDelivery,
        sender, identity,
      });
    } catch (error) { return deliveryFailureOutcome(identity, error); }
  }
  if (begun.kind === 'settlement_required') {
    const settled = settleHttpIngress(begun.settlementAuthority);
    if (settled.kind === 'legacy_unattested_public_body') {
      return legacyPublicBodyOutcome(identity);
    }
    if (settled.kind !== 'settled' && settled.kind !== 'replay') {
      return transientHttpOutcome(identity, 'settlement_in_progress');
    }
    try {
      return await durableHttpOutcome({
        response: settled.response, responseAuthority: settled.responseAuthority,
        replayed: true, replayResult, recordDelivery, verifyResult, verifyDelivery,
        sender, identity,
      });
    } catch (error) { return deliveryFailureOutcome(identity, error); }
  }
  if (!['started', 'resume', 'in_progress', 'settlement_in_progress', 'lane_in_progress']
    .includes(begun.kind)) {
    return transientHttpOutcome(identity, 'unexpected_http_ingress_state');
  }
  const active = ACTIVE_HTTP_REQUESTS.get(begun.request.requestRef);
  if (active) {
    const outcome = await active;
    return outcome.wire
      ? transientHttpOutcome(identity, 'wire_write_in_progress')
      : { ...outcome, replayed: true };
  }
  if (['in_progress', 'settlement_in_progress', 'lane_in_progress'].includes(begun.kind)) {
    return transientHttpOutcome(identity, begun.kind);
  }
  if (!begun.preparationAuthority) return transientHttpOutcome(identity);

  const run = (async () => {
    const stopRenewal = renewalLoop(
      begun.preparationAuthority, begun.claim?.leaseUntil,
    );
    let result;
    try {
      result = await execute(begun.preparationAuthority);
      const proof = resultProof({ result, identity, intent, sender, verifyResult });
      if (!proof) throw new Error('agentresultaat mist exact duurzaam beurtbewijs');
      const resultAuthority = issueServerHttpResultAuthority(
        result, begun.preparationAuthority,
      );
      if (!resultAuthority) throw new Error('agentresultaat mist exact duurzaam beurtbewijs');
      const prepared = prepareHttpResponse(begun.preparationAuthority, resultAuthority);
      if (!['response_prepared', 'response_replay'].includes(prepared.kind)
        || !prepared.settlementAuthority) {
        throw new Error(`HTTP-resultaat kon niet worden voorbereid: ${prepared.reason || prepared.kind}`);
      }
      const settled = settleHttpIngress(prepared.settlementAuthority);
      if (!['settled', 'replay'].includes(settled.kind)) {
        throw new Error(`HTTP-resultaat kon niet worden vastgezet: ${settled.reason || settled.kind}`);
      }
      if (settled.kind === 'settled' && typeof afterSettled === 'function') {
        await afterSettled(result);
      }
      return await durableHttpOutcome({
        response: settled.response, responseAuthority: settled.responseAuthority,
        replayed: false, replayResult, recordDelivery, verifyResult, verifyDelivery,
        sender, identity,
      });
    } catch (error) {
      try { releaseHttpIngressAuthority(begun.preparationAuthority); } catch { /* fenced */ }
      return {
        status: 503, body: {
          error: `Duurzaam HTTP-resultaat kon niet worden bewezen: ${publicErrorText(error)}`,
          ok: false, request_id: identity.requestId,
          request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
        },
      };
    } finally {
      stopRenewal();
    }
  })();
  ACTIVE_HTTP_REQUESTS.set(begun.request.requestRef, run);
  try { return await run; } finally {
    if (ACTIVE_HTTP_REQUESTS.get(begun.request.requestRef) === run) {
      ACTIVE_HTTP_REQUESTS.delete(begun.request.requestRef);
    }
  }
}

function sendHttpOutcome(res, outcome) {
  if (outcome.replayed) res.set('Idempotency-Replayed', 'true');
  if (!outcome.wire) return res.status(outcome.status).json(outcome.body);
  let writeAuthority = outcome.writeAuthority || null;
  let transmissionAuthority = outcome.transmissionAuthority || null;
  if (Boolean(writeAuthority) === Boolean(transmissionAuthority)
    || !WRITE_ATTEMPT_REF.test(String(outcome.writeAttemptRef || ''))
    || !Number.isSafeInteger(outcome.writeGeneration)
    || outcome.writeGeneration < 1 || outcome.writeGeneration > 64
    || (writeAuthority && !httpDeliveryWriteAuthorityCurrent(writeAuthority))
    || (transmissionAuthority
      && !httpDeliveryTransmissionAuthorityCurrent(transmissionAuthority))) {
    if (writeAuthority) {
      try { abandonHttpDeliveryWrite(writeAuthority); } catch { /* capability is fenced */ }
      writeAuthority = null;
    }
    if (transmissionAuthority) {
      try { abandonHttpDeliveryTransmission(transmissionAuthority); } catch {
        /* capability is fenced */
      }
      transmissionAuthority = null;
    }
    return res.status(503).json({
      error: 'HTTP-wire mist een actuele duurzame netwerkwrite-authority.',
      ok: false,
    });
  }
  const abandon = () => {
    if (writeAuthority) {
      const authority = writeAuthority;
      writeAuthority = null;
      try { abandonHttpDeliveryWrite(authority); } catch { /* capability is fenced */ }
    }
    if (transmissionAuthority) {
      const authority = transmissionAuthority;
      transmissionAuthority = null;
      try { abandonHttpDeliveryTransmission(authority); } catch { /* capability is fenced */ }
    }
  };
  res.once('finish', () => {
    if (writeAuthority) {
      const authority = writeAuthority;
      writeAuthority = null;
      try { finishHttpDeliveryWrite(authority); } catch {
        // De browser vraagt na ACK-weigering dezelfde duurzame wire opnieuw op.
      }
    }
    if (transmissionAuthority) {
      const authority = transmissionAuthority;
      transmissionAuthority = null;
      try { finishHttpDeliveryTransmission(authority); } catch {
        // Retrieval verandert delivery-truth niet en blijft fail-closed getraceerd.
      }
    }
  });
  res.once('error', abandon);
  res.once('close', () => {
    if (!res.writableFinished) abandon();
  });
  res.status(outcome.wire.status);
  res.setHeader('Content-Type', outcome.wire.contentType);
  res.setHeader('Content-Length', String(outcome.wire.wireLength));
  res.setHeader('X-Eva-Http-Write-Attempt-Ref', outcome.writeAttemptRef);
  res.setHeader('X-Eva-Http-Write-Generation', String(outcome.writeGeneration));
  try { res.end(outcome.wire.bodyBytes); } catch (error) {
    abandon();
    throw error;
  }
  return res;
}

export async function startWeb() {
  if (arguments.length !== 0) throw new TypeError('startWeb accepteert geen dependencies');
  assertSafeWebBinding({ host: config.webHost, token: config.webToken });
  const ingressStatus = httpIngressStoreStatus();
  if (!ingressStatus.available) {
    throw new Error(`Duurzame HTTP-ingress is niet beschikbaar: ${ingressStatus.reason}`);
  }
  const responseAuthorityStatus = httpResponseAuthorityStoreStatus();
  if (!responseAuthorityStatus.available) {
    throw new Error(`Duurzame HTTP-responseauthority is niet beschikbaar: ${responseAuthorityStatus.reason}`);
  }
  const deliveryStatus = httpDeliveryStoreStatus();
  if (!deliveryStatus.available) {
    throw new Error(`Duurzame HTTP-delivery is niet beschikbaar: ${deliveryStatus.reason}`);
  }
  const webMessageHandler = handleMessageDetailed;
  const webDeliveryRecorder = recordDeliveredReply;
  const webResultVerifier = verifiedPreparedUnderstandingTurnForResult;
  const webResultReplayer = preparedUnderstandingIngressReplay;
  const webDeliveryVerifier = deliveredReplyRecorded;
  const app = express();
  const ackAttempts = new Map();
  app.disable('x-powered-by');
  app.use('/api', (req, res, next) => {
    if (requestAuthorized(req, config.webToken)) return next();
    return res.status(401).json({ error: 'Geen geldige toegang tot Eva.' });
  });
  app.use('/api/http-delivery/ack', (req, res, next) => {
    const now = Date.now();
    const client = String(req.socket?.remoteAddress || 'unknown');
    let attempts = ackAttempts.get(client);
    if (!attempts || attempts.expiresAt <= now) {
      if (!attempts && ackAttempts.size >= ACK_RATE_CLIENT_LIMIT) {
        for (const [key, value] of ackAttempts) {
          if (value.expiresAt <= now) ackAttempts.delete(key);
        }
      }
      if (!attempts && ackAttempts.size >= ACK_RATE_CLIENT_LIMIT) {
        return res.status(429).set('Retry-After', '60').json({
          error: 'Te veel gelijktijdige ACK-clients; probeer later opnieuw.',
          ok: false, acknowledged: false, delivered: false,
        });
      }
      attempts = { count: 0, expiresAt: now + ACK_ATTEMPT_WINDOW_MS };
      ackAttempts.set(client, attempts);
    }
    attempts.count += 1;
    if (attempts.count > ACK_ATTEMPT_LIMIT) {
      const retryAfter = Math.max(1, Math.ceil((attempts.expiresAt - now) / 1_000));
      return res.status(429).set('Retry-After', String(retryAfter)).json({
        error: 'Te veel ACK-pogingen; probeer na de begrensde periode opnieuw.',
        ok: false, acknowledged: false, delivered: false,
      });
    }
    return next();
  });
  app.use('/api/http-delivery/ack', express.json({
    limit: ACK_BODY_LIMIT_BYTES, strict: true, type: 'application/json',
  }));
  app.use('/api', express.json({ limit: '2mb', strict: true, type: 'application/json' }));
  app.use('/api', (error, _req, res, next) => {
    if (error?.type === 'entity.too.large') {
      return res.status(413).json({ error: 'De JSON-body overschrijdt de toegestane grens.' });
    }
    if (error instanceof SyntaxError) {
      return res.status(400).json({ error: 'De JSON-body is ongeldig.' });
    }
    return next(error);
  });
  app.use('/vendor/three', express.static(path.join(__dirname, '../../node_modules/three')));
  app.use(express.static(path.join(__dirname, 'public')));

  app.get('/api/overview', (_req, res) => {
    try {
      const status = agentStatus({ audience: 'family' });
      res.json({
        name: 'Eva',
        awake: true,
        activity: status.activity,
        mood: getMood(),
        moodSummary: moodSummary(),
        auth: authStatus(),
        haConnected: haConnected(),
        counts: countsSinceMidnight(),
      });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/health', (_req, res) => {
    try {
      const auth = authStatus();
      const agent = agentStatus({ audience: 'family' });
      const autonomy = autonomyStatus();
      let behaviorLearning;
      try { behaviorLearning = behaviorLearningStatus(); } catch {
        behaviorLearning = { enabled: false, reason: 'runtime_unavailable' };
      }
      let perceptionAttention;
      try { perceptionAttention = perceptionAttentionStatus(); } catch {
        perceptionAttention = { enabled: false, reason: 'runtime_unavailable', counts: {} };
      }
      const checks = [
        { naam: 'database', ok: true },
        { naam: 'ChatGPT', ok: auth.status === 'ok' || !!config.openaiApiKey, zacht: false },
        {
          naam: 'Home Assistant',
          ok: haConnected(),
          zacht: true,
          cached_states: cachedStates().length,
        },
        {
          naam: 'Gedragsleren', ok: behaviorLearning.enabled === true,
          zacht: true, reden: behaviorLearning.reason,
        },
        {
          naam: 'Deurbelkeuze', ok: perceptionAttention.enabled === true,
          zacht: true, reden: perceptionAttention.reason,
        },
      ];
      res.json({
        gezond: checks.every((check) => check.ok || check.zacht),
        checks,
        runtime: healthRuntimeProjection(),
        runtimeVersion: runtimeVersion(),
        llm: llmRuntimeStatus(),
        agent: healthAgentProjection(agent),
        autonomie: healthAutonomyProjection(autonomy),
        gedragsleren: behaviorLearning,
        deurbelkeuze: perceptionAttention,
      });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/workstatus', (_req, res) => {
    try {
      res.json({
        ...agentStatus({ audience: 'family' }),
        runtime: healthRuntimeProjection(),
        autonomy: familyAutonomyProjection(autonomyStatus()),
      });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/capabilities', (_req, res) => {
    try {
      res.json({ capabilities: listCapabilities({ limit: 100 }) });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/timeline', (req, res) => {
    try {
      const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 200);
      const kinds = req.query.kinds
        ? String(req.query.kinds).split(',').map((item) => item.trim()).filter(Boolean)
        : null;
      const before = Number(req.query.before) || null;
      const rows = getTimeline({ limit: 800, kinds, before })
        .filter(familyTimelineEntry)
        .slice(0, limit);
      res.json(rows);
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/memories', (req, res) => {
    try {
      const query = String(req.query.q || '').trim();
      const scope = String(req.query.scope || 'active').trim().toLowerCase();
      if (scope === 'archive') return res.json(listArchivedMemories(100, query));
      if (scope === 'review') {
        return res.json(memoryPage({ scope: 'review', query, limit: 100 }).items);
      }
      if (scope !== 'active') return res.status(400).json({ error: 'Onbekende geheugenweergave.' });
      const preview = memoryHygienePreview({ query, limit: 100 });
      return res.json(preview.items);
    } catch (error) {
      return res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/memories/page', (req, res) => {
    try {
      const query = String(req.query.q || '').trim();
      const scope = String(req.query.scope || 'active').trim().toLowerCase();
      if (!['active', 'review', 'archive'].includes(scope)) {
        return res.status(400).json({ error: 'Onbekende geheugenweergave.' });
      }
      const requestedLimit = req.query.limit == null ? 100 : Number(req.query.limit);
      if (!Number.isSafeInteger(requestedLimit) || requestedLimit <= 0) {
        return res.status(400).json({ error: 'Ongeldige geheugenpaginalimiet.' });
      }
      const requestedBefore = req.query.before == null ? null : Number(req.query.before);
      if (requestedBefore != null
        && (!Number.isSafeInteger(requestedBefore) || requestedBefore <= 0)) {
        return res.status(400).json({ error: 'Ongeldige geheugencursor.' });
      }
      return res.json(memoryPage({
        scope, query, limit: Math.min(requestedLimit, 100), before: requestedBefore,
      }));
    } catch (error) {
      return res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/memories/overview', (_req, res) => {
    try {
      const preview = memoryHygienePreview({ limit: 1 });
      const archived = /** @type {{count?: unknown}|undefined} */ (
        db.prepare('SELECT COUNT(*) AS count FROM memories WHERE COALESCE(archived,0)=1').get()
      );
      res.json({
        contract: 'MemoryHygieneOverview/v1',
        active: preview.totalActive,
        archived: Number(archived?.count || 0),
        review: Number(preview.counts?.REVIEW || 0),
        archiveCandidates: Number(preview.counts?.ARCHIVE_CANDIDATE || 0),
        protected: Number(preview.counts?.PROTECT || 0),
        kept: Number(preview.counts?.KEEP || 0),
      });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.post('/api/memories/archive', (req, res) => {
    try {
      const keys = Object.keys(req.body || {});
      const ids = req.body?.ids;
      if (keys.length !== 1 || keys[0] !== 'ids' || !Array.isArray(ids)
        || ids.length !== 1 || positiveInteger(ids[0]) == null) {
        return res.status(400).json({ error: 'Kies exact één geheugenregel.' });
      }
      res.json(archiveMemoryHygieneCandidates({ ids: req.body?.ids }));
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.post('/api/memories/restore', (req, res) => {
    try {
      const keys = Object.keys(req.body || {});
      const ids = req.body?.ids;
      if (keys.length !== 1 || keys[0] !== 'ids' || !Array.isArray(ids)
        || ids.length !== 1 || positiveInteger(ids[0]) == null) {
        return res.status(400).json({ error: 'Kies exact één geheugenregel.' });
      }
      res.json(restoreMemoryHygieneArchive({ ids: req.body?.ids }));
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.post('/api/memories/review', (req, res) => {
    try {
      const keys = Object.keys(req.body || {}).sort();
      const expected = ['action', 'expectedFingerprint', 'id', 'operationKey', 'revision'];
      if (keys.length !== expected.length
        || keys.some((key, index) => key !== expected[index])) {
        return res.status(400).json({ error: 'Geef exact één geheugenbeoordeling door.' });
      }
      return res.json(reviewMemoryAction(req.body));
    } catch (error) {
      if (error?.code === 'EVA_MEMORY_REVIEW_CONFLICT') {
        const status = error.reasonCode === 'not_found' ? 404 : 409;
        return res.status(status).json({ error: publicErrorText(error), reasonCode: error.reasonCode });
      }
      if (error instanceof TypeError) return res.status(400).json({ error: publicErrorText(error) });
      return res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/people', (_req, res) => {
    try {
      res.json(allPeople());
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/reminders', (_req, res) => {
    try {
      res.json(openReminders());
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/proposals', (_req, res) => res.json([]));

  app.get('/api/emotions/history', (req, res) => {
    try {
      const hours = Math.min(Math.max(Number(req.query.hours) || 24, 1), 24 * 14);
      res.json(emotionHistory(hours));
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.post('/api/http-delivery/ack', async (req, res) => {
    let acknowledged = null;
    try {
      acknowledged = acknowledgeHttpDelivery(req.body);
      await reconcileAcknowledgedHttpDelivery({
        acknowledged, replayResult: webResultReplayer,
        recordDelivery: webDeliveryRecorder, verifyResult: webResultVerifier,
        verifyDelivery: webDeliveryVerifier,
        sender: config.webSender,
      });
      return res.json({
        ok: true, delivered: true,
        delivery_ref: acknowledged.delivery.deliveryRef,
        request_ref: acknowledged.delivery.requestRef,
        ack_receipt_ref: acknowledged.ack.ackReceiptRef,
        ack_receipt: acknowledged.ack.receipt,
      });
    } catch (error) {
      if (acknowledged) {
        return res.status(503).json({
          error: `De client-ACK is duurzaam bewaard, maar deliveryregistratie wacht: ${publicErrorText(error)}`,
          ok: false, acknowledged: true, delivered: false,
          delivery_ref: acknowledged.delivery.deliveryRef,
        });
      }
      if (error?.code === 'EVA_HTTP_DELIVERY_WRITE_NOT_FINISHED') {
        return res.status(409).json({
          ok: false, acknowledged: false, delivered: false, retry_wire: true,
          request_ref: error.requestRef, delivery_ref: error.deliveryRef,
          write_attempt_ref: error.writeAttemptRef,
          write_generation: error.writeGeneration,
        });
      }
      const unavailable = /store unavailable|schema|database is locked/iu
        .test(String(error?.message || error));
      return res.status(unavailable ? 503 : 400).json({
        error: `De client-ACK is geweigerd: ${publicErrorText(error)}`,
        ok: false, acknowledged: false, delivered: false,
      });
    }
  });

  app.post('/api/chat', async (req, res) => {
    if (Object.hasOwn(req.body || {}, 'synthetic')) {
      return res.status(400).json({ error: 'synthetic is geen publieke requestoptie.' });
    }
    const identity = canonicalPublicRequestId({
      bodyRequestId: req.body?.request_id,
      headerRequestId: req.headers['idempotency-key'],
    });
    if (!identity.ok) {
      return res.status(400).json({ error: requestIdentityError(identity.reason) });
    }
    const sender = config.webSender;
    const rawText = String(req.body?.text || '');
    const text = rawText.trim();
    if (!text) return res.status(400).json({ error: 'Geen tekst meegegeven.' });
    const conversationId = normalizeWebConversationId(
      req.body?.conversation_id || req.body?.session_id,
      sender,
    );
    const intent = {
      contract: 'PublicHttpIntent/v1', route: '/api/chat', sender,
      rawText, conversationId, syntheticTest: identity.synthetic,
      ...(Object.hasOwn(req.body || {}, 'client_ack_public_key')
        ? { clientAckPublicKey: req.body.client_ack_public_key } : {}),
    };
    const outcome = await executeDurableHttpRequest({
      identity: {
        ...identity, channel: 'web', conversationId,
      },
      intent, sender, verifyResult: webResultVerifier,
      replayResult: webResultReplayer, recordDelivery: webDeliveryRecorder,
      verifyDelivery: webDeliveryVerifier,
      execute: (httpIngressAuthority) => webMessageHandler({
        channel: 'web', sender, text: rawText,
        messageContext: {
          conversationId, identityTrusted: true,
          syntheticTest: identity.synthetic, messageId: identity.requestId,
          httpIngressAuthority, httpIngressIntent: intent,
        },
      }),
    });
    return sendHttpOutcome(res, outcome);
  });

  // Het oude dashboard blijft bruikbaar, maar toont geen verzonnen parallelle
  // "binnenwerelden" meer. Gesprek, geheugen en aantoonbaar werk zijn leidend.
  app.get('/api/innerlife', (_req, res) => {
    const status = agentStatus({ audience: 'family' });
    res.json({
      drives: {},
      working: status.current_focus ? [status.current_focus] : [],
      zelfbeeld: 'Ik ben Eva, een digitale huisgenoot. Wie ik word blijkt uit wat ik onthoud, doe en afmaak.',
      gedachtegang: status.current_focus || '',
      verwonderingen: [],
      voornemens: [],
      karakter: { afspraken: [] },
      leeftijdDagen: 0,
      runtime: healthRuntimeProjection(),
      autonomie: familyAutonomyProjection(autonomyStatus()),
      werkstatus: status,
      vaardigheden: listCapabilities({ status: 'active', limit: 50 }).map((item) => ({
        soort: item.kind,
        naam: item.name,
        versie: item.version,
        beschrijving: item.description,
        laatstBijgewerkt: item.updated_ts,
      })),
    });
  });

  app.post('/api/agreement/remove', (_req, res) => res.json({ ok: false, afspraken: [] }));

  app.get('/api/appearance', (_req, res) => {
    try {
      res.json(getAppearance());
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.post('/api/voice', async (req, res) => {
    if (Object.hasOwn(req.body || {}, 'synthetic')) {
      return res.status(400).json({ error: 'synthetic is geen publieke requestoptie.' });
    }
    const identity = canonicalPublicRequestId({
      bodyRequestId: req.body?.request_id,
      headerRequestId: req.headers['idempotency-key'],
    });
    if (!identity.ok) {
      return res.status(400).json({ error: requestIdentityError(identity.reason) });
    }
    const sender = config.webSender;
    const rawText = String(req.body?.text || '');
    const text = rawText.trim();
    if (!text) return res.status(400).json({ error: 'Geen tekst meegegeven.' });
    const conversationId = normalizeVoiceConversationId(
      req.body?.conversation_id || req.body?.session_id,
      sender,
    );
    const shouldSpeak = Boolean(req.body?.speak);
    if (shouldSpeak) {
      return res.status(503).json({
        error: 'Duurzame spraakaflevering is nog niet beschikbaar; dezelfde request_id is niet uitgevoerd.',
        ok: false, request_id: identity.requestId,
        request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
      });
    }
    const speaker = req.body?.speaker == null ? null : String(req.body.speaker);
    const intent = {
      contract: 'PublicHttpIntent/v1', route: '/api/voice', sender,
      rawText, conversationId, shouldSpeak, speaker, syntheticTest: identity.synthetic,
      ...(Object.hasOwn(req.body || {}, 'client_ack_public_key')
        ? { clientAckPublicKey: req.body.client_ack_public_key } : {}),
    };
    const outcome = await executeDurableHttpRequest({
      identity: {
        ...identity, channel: 'voice', conversationId,
      },
      intent, sender, verifyResult: webResultVerifier,
      replayResult: webResultReplayer, recordDelivery: webDeliveryRecorder,
      verifyDelivery: webDeliveryVerifier,
      execute: (httpIngressAuthority) => webMessageHandler({
        channel: 'voice', sender, text: rawText,
        messageContext: {
          conversationId, identityTrusted: true,
          syntheticTest: identity.synthetic, messageId: identity.requestId,
          httpIngressAuthority, httpIngressIntent: intent,
        },
      }),
      afterSettled: null,
    });
    return sendHttpOutcome(res, outcome);
  });

  app.post('/api/auth/login', async (_req, res) => {
    try {
      const { authUrl } = await startLoginFlow();
      res.json({ authUrl });
    } catch (error) {
      res.status(500).json({ error: publicErrorText(error) });
    }
  });

  app.get('/api/events', (req, res) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write(': hallo van Eva\n\n');
    const handlers = new Map();
    for (const name of BUS_EVENTS) {
      const handler = (data) => {
        try {
          if (name === 'timeline' && !familyTimelineEntry(data)) return;
          const publicData = publicBusEventData(name, data);
          if (publicData == null) return;
          res.write(`event: ${name}\ndata: ${JSON.stringify(publicData)}\n\n`);
        } catch {
          // De browserverbinding is al gesloten.
        }
      };
      handlers.set(name, handler);
      bus.on(name, handler);
    }
    const ping = setInterval(() => {
      try { res.write(': ping\n\n'); } catch { /* verbinding gesloten */ }
    }, 25_000);
    req.on('close', () => {
      clearInterval(ping);
      for (const [name, handler] of handlers) bus.off(name, handler);
      res.end();
    });
  });

  return new Promise((resolve, reject) => {
    const server = app.listen(config.webPort, config.webHost, () => {
      console.log(`[web] dashboard op http://${config.webHost}:${config.webPort}`);
      resolve(server);
    });
    server.on('error', reject);
  });
}

export default startWeb;
