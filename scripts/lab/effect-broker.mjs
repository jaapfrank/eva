import { createHash } from 'node:crypto';

const HASH = /^[a-f0-9]{64}$/u;
const ID = /^[a-z0-9][a-z0-9._:-]{0,159}$/u;
const EFFECT_CLASSES = new Set(['E0', 'E1', 'E2', 'E3', 'E4', 'E5']);
const AUTHORIZATION_KEYS = [
  'contract', 'authorizationId', 'runId', 'caseInstanceId', 'effectClass',
  'operations', 'targets', 'sinks', 'maxCalls', 'expiresAt', 'reversible',
  'rollbackDriver', 'approvalSha256',
];
const REQUEST_KEYS = ['operation', 'target', 'sink', 'payloadSha256'];

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));
const sha256 = (value) => createHash('sha256').update(canonical(value)).digest('hex');

function exactKeys(value, keys) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical([...keys].sort()));
}

function canonicalIso(value) {
  const millis = Date.parse(String(value || ''));
  return Number.isFinite(millis) && new Date(millis).toISOString() === value;
}

function exactStringSet(values, { nullable = false } = {}) {
  if (!Array.isArray(values) || !values.length) return null;
  const normalized = values.map((value) => nullable && value == null ? null : String(value || ''));
  if (normalized.some((value) => value == null ? !nullable : !ID.test(value))
    || new Set(normalized).size !== normalized.length) return null;
  return Object.freeze(normalized);
}

function validateAuthorization(value, verifyApproval) {
  if (!exactKeys(value, AUTHORIZATION_KEYS)
    || value.contract !== 'EffectAuthorization/v1'
    || !ID.test(String(value.authorizationId || ''))
    || !ID.test(String(value.runId || ''))
    || !/^lab:[a-f0-9]{64}$/u.test(String(value.caseInstanceId || ''))
    || !EFFECT_CLASSES.has(value.effectClass)
    || !Number.isSafeInteger(value.maxCalls) || value.maxCalls < 1 || value.maxCalls > 10_000
    || !canonicalIso(value.expiresAt) || typeof value.reversible !== 'boolean'
    || (value.reversible && !ID.test(String(value.rollbackDriver || '')))
    || (!value.reversible && value.rollbackDriver != null)
    || !HASH.test(String(value.approvalSha256 || ''))
    || verifyApproval(value) !== true) {
    throw new TypeError('EffectAuthorization/v1 is ongeldig of niet goedgekeurd');
  }
  const operations = exactStringSet(value.operations);
  const targets = exactStringSet(value.targets);
  const sinks = exactStringSet(value.sinks);
  if (!operations || !targets || !sinks) {
    throw new TypeError('EffectAuthorization/v1 heeft ongeldige grenzen');
  }
  return Object.freeze({ ...value, operations, targets, sinks });
}

function validateRequest(request, authorization) {
  if (!exactKeys(request, REQUEST_KEYS) || !authorization.operations.includes(request.operation)
    || !authorization.targets.includes(request.target)
    || (request.sink != null && !authorization.sinks.includes(request.sink))
    || !HASH.test(String(request.payloadSha256 || ''))) {
    throw new TypeError('LAB-effect is niet geautoriseerd voor operatie, target of sink');
  }
  return Object.freeze({ ...request, sink: request.sink == null ? null : request.sink });
}

function executionReceipt({ authorization, request, sequence, executedAt, outcome }) {
  if (!outcome || typeof outcome !== 'object' || outcome.accepted !== true
    || !HASH.test(String(outcome.effectReceiptSha256 || ''))) {
    throw new TypeError('LAB-effectexecutor gaf geen accepted effectbewijs');
  }
  const body = {
    contract: 'LabEffectReceipt/v1',
    authorizationId: authorization.authorizationId,
    runId: authorization.runId,
    caseInstanceId: authorization.caseInstanceId,
    sequence,
    executedAt,
    request,
    upstreamEffectReceiptSha256: outcome.effectReceiptSha256,
  };
  return Object.freeze({ ...body, receiptSha256: sha256(body) });
}

export function createLabEffectBroker(authorization, {
  verifyApproval,
  execute,
  rollback = null,
} = {}) {
  if (typeof verifyApproval !== 'function' || typeof execute !== 'function'
    || (rollback != null && typeof rollback !== 'function')) {
    throw new TypeError('LAB-effectbroker mist hostverifiers of executor');
  }
  const exactAuthorization = validateAuthorization(authorization, verifyApproval);
  const issuedReceipts = new WeakSet();
  const rolledBackReceipts = new WeakSet();
  const receipts = [];
  let calls = 0;
  let stopped = null;

  async function executeEffect(request, now = new Date().toISOString()) {
    if (stopped) throw new Error(`LAB-effectbroker is gestopt: ${stopped.reason}`);
    if (!canonicalIso(now) || Date.parse(now) >= Date.parse(exactAuthorization.expiresAt)) {
      throw new Error('LAB-effectauthorization is verlopen');
    }
    if (calls >= exactAuthorization.maxCalls) throw new Error('LAB-effectcallbudget is uitgeput');
    const exactRequest = validateRequest(request, exactAuthorization);
    calls += 1;
    const outcome = await execute(exactRequest, Object.freeze({
      authorizationId: exactAuthorization.authorizationId,
      sequence: calls,
    }));
    const receipt = executionReceipt({
      authorization: exactAuthorization,
      request: exactRequest,
      sequence: calls,
      executedAt: now,
      outcome,
    });
    issuedReceipts.add(receipt);
    receipts.push(receipt);
    return receipt;
  }

  async function rollbackEffect(receipt, now = new Date().toISOString()) {
    if (!canonicalIso(now) || !exactAuthorization.reversible || typeof rollback !== 'function'
      || !issuedReceipts.has(receipt)) {
      throw new Error('LAB-effectreceipt heeft geen geldige rollbackauthority');
    }
    if (rolledBackReceipts.has(receipt)) throw new Error('LAB-effectrollback is al uitgevoerd');
    rolledBackReceipts.add(receipt);
    await rollback(receipt, exactAuthorization.rollbackDriver);
    const body = {
      contract: 'LabRollbackReceipt/v1',
      authorizationId: exactAuthorization.authorizationId,
      effectReceiptSha256: receipt.receiptSha256,
      rollbackDriver: exactAuthorization.rollbackDriver,
      rolledBackAt: now,
    };
    return Object.freeze({ ...body, rollbackReceiptSha256: sha256(body) });
  }

  function stop(reason, now = new Date().toISOString()) {
    if (!canonicalIso(now) || typeof reason !== 'string' || !reason.trim() || reason.length > 160) {
      throw new TypeError('LAB-stopbewijs is ongeldig');
    }
    if (!stopped) stopped = Object.freeze({ reason: reason.trim(), stoppedAt: now });
    return stopped;
  }

  function snapshot() {
    return Object.freeze({
      contract: 'LabEffectBrokerSnapshot/v1',
      authorizationId: exactAuthorization.authorizationId,
      calls,
      maxCalls: exactAuthorization.maxCalls,
      stopped,
      receiptSha256s: Object.freeze(receipts.map((receipt) => receipt.receiptSha256)),
    });
  }

  return Object.freeze({ execute: executeEffect, rollback: rollbackEffect, stop, snapshot });
}

