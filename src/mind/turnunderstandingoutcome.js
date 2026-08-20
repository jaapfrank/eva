import { createHash } from 'node:crypto';

import { canonicalJson } from '../util.js';
import { createPreparedUnderstandingTurn } from './understanding.js';

const KINDS = new Set([
  'prepared', 'superseded', 'durable_handoff', 'synthetic_interrupted',
  'recovery_disposition',
]);
const RECOVERY_DISPOSITIONS = new Set([
  'effect_reconciliation_required', 'existing_origin', 'already_replied', 'superseded',
]);
const ARG_KEYS = Object.freeze([
  'channel', 'conversationId', 'messageId', 'originalText', 'requester',
  'sessionRef', 'supersessionScope', 'supersessionToken', 'syntheticTest',
]);
const PREPARED_KEYS = new Set([
  'channel', 'conversationId', 'originMessageId', 'turnId', 'reply',
  'workItemId', 'goalEpisodeId', 'finalDeliveryBindingSha256', 'syntheticTest',
  'error', 'understood', 'interpretationFailed', 'noPlan', 'recovered',
  'durableHandoff', 'executionPending', 'actionFailed', 'durableProofMissing',
  'superseded', 'yielded', 'failureStage', 'planningError', 'noPlanDecision',
]);
function plainObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  return Object.values(Object.getOwnPropertyDescriptors(value)).every((descriptor) => (
    Object.hasOwn(descriptor, 'value') && descriptor.get == null && descriptor.set == null
  ));
}

function exactKeys(value, required, optional = []) {
  if (!plainObject(value)) return false;
  const ownKeys = Reflect.ownKeys(value);
  if (ownKeys.some((key) => typeof key !== 'string')) return false;
  const keys = ownKeys.sort();
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => Object.hasOwn(value, key))
    && keys.every((key) => allowed.has(key));
}

function exactArgs(value) {
  return exactKeys(value, ARG_KEYS)
    && ['telegram', 'voice', 'web'].includes(value.channel)
    && typeof value.conversationId === 'string'
    && value.conversationId.length >= 1 && value.conversationId.length <= 240
    && typeof value.messageId === 'string'
    && value.messageId.length >= 1 && value.messageId.length <= 160
    && typeof value.originalText === 'string'
    && typeof value.requester === 'string'
    && typeof value.sessionRef === 'string'
    && typeof value.syntheticTest === 'boolean'
    && (value.supersessionScope === null
      || typeof value.supersessionScope === 'string')
    && (value.supersessionToken === null
      || typeof value.supersessionToken === 'string');
}

function boundedString(value, limit, { optional = false } = {}) {
  return typeof value === 'string' && value.length <= limit
    && (optional || value.length >= 1);
}

function boundedId(value) {
  return Number.isSafeInteger(value) && value >= 1;
}

function preparedOutcome(value, args) {
  if (!plainObject(value)
    || Reflect.ownKeys(value).some((key) => (
      typeof key !== 'string' || !PREPARED_KEYS.has(key)
    ))
    || value.channel !== args.channel
    || value.conversationId !== args.conversationId
    || String(value.originMessageId || '') !== args.messageId
    || value.syntheticTest !== args.syntheticTest) {
    throw new Error('prepared understanding-outcome heeft geen exacte ingressbinding');
  }
  return createPreparedUnderstandingTurn(value);
}

function supersededOutcome(value, args) {
  if (!exactKeys(value, ['turnId'], ['goalEpisodeId', 'workItemId', 'durationMs'])
    || !boundedString(value.turnId, 180)
    || value.goalEpisodeId != null && !boundedId(value.goalEpisodeId)
    || value.workItemId != null && !boundedId(value.workItemId)
    || value.durationMs != null && (!Number.isSafeInteger(value.durationMs)
      || value.durationMs < 0 || value.durationMs > 86_400_000)) {
    throw new Error('superseded understanding-outcome is niet exact begrensd');
  }
  return createPreparedUnderstandingTurn({
    reply: '', turnId: value.turnId, channel: args.channel,
    conversationId: args.conversationId, originMessageId: args.messageId,
    sender: args.requester, tools: [], goalEpisodeId: value.goalEpisodeId || null,
    workItemId: value.workItemId || null, yielded: true, superseded: true,
    durationMs: Number(value.durationMs || 0), syntheticTest: args.syntheticTest,
  });
}

function durableHandoffOutcome(value, args) {
  if (!exactKeys(value, ['goalEpisodeId', 'turnId', 'workItemId'])
    || !boundedId(value.workItemId) || !boundedId(value.goalEpisodeId)
    || !boundedString(value.turnId, 180)) {
    throw new Error('understanding-handoff mist exacte duurzame ids');
  }
  return createPreparedUnderstandingTurn({
    channel: args.channel, conversationId: args.conversationId,
    originMessageId: args.messageId, turnId: value.turnId,
    // Recovery sealt alleen de handoff. De oorspronkelijke transporthandler
    // behandelt dit als een stille replay en verzendt geen tweede crashantwoord.
    reply: '',
    workItemId: value.workItemId, goalEpisodeId: value.goalEpisodeId,
    durableHandoff: true, executionPending: true,
    actionFailed: true, error: 'runtime_interrupted',
    syntheticTest: args.syntheticTest,
  });
}

function syntheticInterruptedOutcome(value, args) {
  if (value !== null || args.syntheticTest !== true) {
    throw new Error('synthetic_interrupted vereist een synthetische ingress');
  }
  return createPreparedUnderstandingTurn({
    channel: args.channel, conversationId: args.conversationId,
    originMessageId: args.messageId,
    turnId: `recovered:${createHash('sha256').update(canonicalJson({
      channel: args.channel, conversationId: args.conversationId,
      messageId: args.messageId, syntheticTest: true,
    })).digest('hex')}`,
    reply: 'De synthetische controle is onderbroken en niet opnieuw uitgevoerd.',
    actionFailed: true, error: 'runtime_interrupted', syntheticTest: true,
  });
}

function recoveryDispositionOutcome(value, args) {
  if (!exactKeys(value, ['disposition', 'evidenceSha256'])
    || !RECOVERY_DISPOSITIONS.has(value.disposition)
    || !/^[a-f0-9]{64}$/u.test(String(value.evidenceSha256 || ''))) {
    throw new Error('recovery-disposition mist exact duurzaam bewijs');
  }
  const failClosed = ['effect_reconciliation_required', 'existing_origin'].includes(
    value.disposition,
  );
  return createPreparedUnderstandingTurn({
    channel: args.channel,
    conversationId: args.conversationId,
    originMessageId: args.messageId,
    turnId: `recovery-disposition:${value.evidenceSha256}`,
    reply: '',
    recovered: true,
    noPlan: true,
    noPlanDecision: `recovery:${value.disposition}`,
    planningError: `recovery-evidence:${value.evidenceSha256}`,
    actionFailed: failClosed,
    durableProofMissing: failClosed,
    superseded: value.disposition === 'superseded',
    yielded: value.disposition === 'superseded',
    ...(failClosed ? { error: 'effect_reconciliation_required' } : {}),
    syntheticTest: args.syntheticTest,
  });
}

/**
 * Projecteert uitsluitend data. Dit resultaat verleent op zichzelf nooit
 * mutation- of delivery-authority; die blijft privé in de agentroute.
 */
export function projectTurnUnderstandingTerminalOutcome(input) {
  if (!exactKeys(input, ['args', 'kind', 'value'])
    || !KINDS.has(input.kind) || !exactArgs(input.args)) {
    throw new Error('understanding terminal-outcome envelope is ongeldig');
  }
  if (input.kind === 'prepared') return preparedOutcome(input.value, input.args);
  if (input.kind === 'superseded') return supersededOutcome(input.value, input.args);
  if (input.kind === 'durable_handoff') return durableHandoffOutcome(input.value, input.args);
  if (input.kind === 'synthetic_interrupted') {
    return syntheticInterruptedOutcome(input.value, input.args);
  }
  return recoveryDispositionOutcome(input.value, input.args);
}
