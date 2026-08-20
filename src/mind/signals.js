import { createHash } from 'node:crypto';
import { canonicalJson, verifiedCompleteReceipt } from '../util.js';

export const SIGNAL_BATCH_CONTRACT = 'SignalBatch/v1';
export const SIGNAL_ASSESSMENT_CONTRACT = 'SignalAssessmentReceipt/v1';
export const SIGNAL_QUARANTINE_CONTRACT = 'SignalQuarantineReceipt/v1';
export const SIGNAL_QUARANTINE_LIMIT = 30;
// Dezelfde grens geldt in bounded en full: de strengste autonome discoverypoort
// laat vier inhoudelijke reads toe. Zo blijft iedere geldige batch ook buiten de
// full-config volledig uitvoerbaar vóór de terminale hostassessment.
export const SIGNAL_BATCH_MAX_ENTITY_KEYS = 4;
export const HOME_ENTITY_SIGNAL_SCOPES = Object.freeze([
  'entity_state:binary_sensor.*',
  'entity_state:sensor.*',
  'entity_state:climate.*',
  'sun:sun.sun',
  'arrival:person.*',
  'departure:person.*',
]);

const SIGNAL_FIELDS = ['id', 'ts', 'key', 'type', 'text'];
const SIGNAL_ENTITY_KEY = /^[a-z0-9_]+\.[a-z0-9_]+$/u;

function signalEntry(value = {}) {
  return Object.freeze(Object.fromEntries(SIGNAL_FIELDS.map((field) => [
    field,
    String(value?.[field] || '').slice(0, field === 'text' ? 500 : 240),
  ])));
}

export function signalMatchesScopeSelector(signal, selector) {
  const normalized = signalEntry(signal);
  const separator = String(selector || '').indexOf(':');
  if (separator < 1 || !SIGNAL_ENTITY_KEY.test(normalized.key)) return false;
  const type = selector.slice(0, separator), key = selector.slice(separator + 1);
  return normalized.type === type
    && (key.endsWith('.*') ? normalized.key.startsWith(key.slice(0, -1)) : normalized.key === key);
}

export function isRoutableSignalSnapshot(signal) {
  const normalized = signalEntry(signal);
  return Boolean(normalized.id)
    && HOME_ENTITY_SIGNAL_SCOPES.some((selector) => signalMatchesScopeSelector(normalized, selector));
}

export function partitionSignalSnapshots(signals = []) {
  const routable = [], unsupported = [];
  for (const signal of (Array.isArray(signals) ? signals : []).slice(0, 30).map(signalEntry)) {
    (isRoutableSignalSnapshot(signal) ? routable : unsupported).push(signal);
  }
  return { routable, unsupported };
}

export function selectRoutableSignalBatchSnapshots(
  signals = [], maxEntityKeys = SIGNAL_BATCH_MAX_ENTITY_KEYS,
) {
  const limit = Math.max(1, Math.min(
    SIGNAL_BATCH_MAX_ENTITY_KEYS, Math.trunc(Number(maxEntityKeys) || 0),
  ));
  const selectedKeys = new Set();
  return (Array.isArray(signals) ? signals : []).filter((signal) => {
    const normalized = signalEntry(signal);
    if (selectedKeys.has(normalized.key)) return true;
    if (selectedKeys.size >= limit) return false;
    selectedKeys.add(normalized.key); return true;
  });
}

function signalSnapshotSha256(signal) {
  return createHash('sha256').update(canonicalJson(signalEntry(signal))).digest('hex');
}

function quarantineFingerprint(signal) {
  const normalized = signalEntry(signal);
  return createHash('sha256').update(canonicalJson({
    type: normalized.type, key: normalized.key,
  })).digest('hex');
}

function quarantineEntry(signal, fingerprint, quarantinedAt) {
  const snapshot = signalEntry(signal);
  return {
    contract: 'QuarantinedSignal/v1', fingerprint, reason: 'unsupported_signal_scope',
    first_seen_ts: snapshot.ts, last_seen_ts: snapshot.ts,
    first_quarantined_ts: quarantinedAt, last_quarantined_ts: quarantinedAt,
    occurrences: 1, latest_snapshot: snapshot, last_receipt_id: null,
  };
}

function updateQuarantineEntry(entry, signal, quarantinedAt) {
  const snapshot = signalEntry(signal);
  return {
    ...entry,
    last_seen_ts: snapshot.ts,
    last_quarantined_ts: quarantinedAt,
    occurrences: Math.max(1, Number(entry.occurrences || 0)) + 1,
    latest_snapshot: snapshot,
  };
}

/**
 * Verplaats uitsluitend snapshots die byte-canoniek nog overeenkomen met de
 * eerder geobserveerde kandidaten. Een gelijk id met nieuwere inhoud blijft
 * daardoor staan voor een volgende schedulerbeurt.
 */
export function quarantineUnsupportedSignalSnapshots({
  pendingSignals = [], candidates = [], quarantinedSignals = [], quarantinedAt = new Date().toISOString(),
} = {}) {
  const candidateSnapshots = new Set((Array.isArray(candidates) ? candidates : [])
    .map((signal) => canonicalJson(signalEntry(signal))));
  const moved = [];
  const pending = (Array.isArray(pendingSignals) ? pendingSignals : []).filter((signal) => {
    const normalized = signalEntry(signal);
    if (!candidateSnapshots.has(canonicalJson(normalized))) return true;
    moved.push(normalized); return false;
  });
  if (!moved.length) return { pendingSignals: pending, quarantinedSignals, receipt: null,
    newFingerprints: 0 };

  const entries = (Array.isArray(quarantinedSignals) ? quarantinedSignals : [])
    .filter((entry) => entry?.contract === 'QuarantinedSignal/v1' && entry.fingerprint)
    .map((entry) => structuredClone(entry));
  const byFingerprint = new Map(entries.map((entry, index) => [entry.fingerprint, index]));
  const touched = new Set(), created = new Set();
  for (const signal of moved) {
    const fingerprint = quarantineFingerprint(signal), index = byFingerprint.get(fingerprint);
    if (index == null) {
      byFingerprint.set(fingerprint, entries.length);
      entries.push(quarantineEntry(signal, fingerprint, quarantinedAt));
      created.add(fingerprint);
    } else entries[index] = updateQuarantineEntry(entries[index], signal, quarantinedAt);
    touched.add(fingerprint);
  }
  const receiptBody = {
    contract: SIGNAL_QUARANTINE_CONTRACT,
    quarantinedAt,
    reason: 'unsupported_signal_scope',
    moved: moved.map((signal) => ({
      id: signal.id,
      snapshotSha256: signalSnapshotSha256(signal),
      fingerprint: quarantineFingerprint(signal),
    })),
  };
  const sha256 = createHash('sha256').update(canonicalJson(receiptBody)).digest('hex');
  const receipt = Object.freeze({ ...receiptBody,
    receiptId: `signal-quarantine:${sha256.slice(0, 24)}`, sha256,
    newFingerprints: created.size,
  });
  const updated = entries.map((entry) => touched.has(entry.fingerprint)
    ? { ...entry, last_receipt_id: receipt.receiptId } : entry);
  const retained = [
    ...updated.filter((entry) => !touched.has(entry.fingerprint)),
    ...updated.filter((entry) => touched.has(entry.fingerprint)),
  ].slice(-SIGNAL_QUARANTINE_LIMIT);
  return { pendingSignals: pending, quarantinedSignals: retained, receipt,
    newFingerprints: created.size };
}

export function createSignalBatch(signals = []) {
  const entries = (Array.isArray(signals) ? signals : []).slice(0, 30)
    .map(signalEntry).filter((signal) => signal.id);
  const body = { contract: SIGNAL_BATCH_CONTRACT, signals: entries };
  const sha256 = createHash('sha256').update(canonicalJson(body)).digest('hex');
  return Object.freeze({ ...body, batchId: `signals:${sha256.slice(0, 24)}`, sha256,
    signals: Object.freeze(entries) });
}

export function validSignalBatch(value) {
  if (!value || value.contract !== SIGNAL_BATCH_CONTRACT || !Array.isArray(value.signals)) return false;
  const canonical = createSignalBatch(value.signals);
  const ids = canonical.signals.map((signal) => signal.id);
  const keys = canonical.signals.map((signal) => signal.key);
  return value.batchId === canonical.batchId && value.sha256 === canonical.sha256
    && canonicalJson(value.signals) === canonicalJson(canonical.signals)
    && new Set(ids).size === ids.length
    && new Set(keys).size <= SIGNAL_BATCH_MAX_ENTITY_KEYS;
}

function actionResult(output) {
  try {
    return typeof output === 'string' ? JSON.parse(output) : output;
  } catch {
    return null;
  }
}

export function acknowledgedSignalIds(tools = [], batch = createSignalBatch()) {
  if (!validSignalBatch(batch)) return [];
  const assessments = (Array.isArray(tools) ? tools : []).filter((tool) => (
    tool?.name === 'signalen_afhandelen'
  ));
  if (assessments.length !== 1 || tools.at(-1) !== assessments[0]) return [];
  const tool = assessments[0];
  const result = actionResult(tool.output);
  const receipt = result?.data;
  const expected = batch.signals.map((signal) => signal.id);
  const acknowledged = Array.isArray(receipt?.acknowledgedIds)
    ? receipt.acknowledgedIds.map(String) : [];
  if (tool.ok !== true || tool.verified !== true || !verifiedCompleteReceipt(result)
    || receipt?.contract !== SIGNAL_ASSESSMENT_CONTRACT
    || receipt.batchId !== batch.batchId || receipt.batchSha256 !== batch.sha256
    || canonicalJson(acknowledged) !== canonicalJson(expected)
    || new Set(acknowledged).size !== acknowledged.length) return [];
  return expected;
}

export function removeAcknowledgedSignalSnapshots(current = [], batch, acknowledgedIds = []) {
  if (!validSignalBatch(batch)) return [...(Array.isArray(current) ? current : [])];
  const acknowledged = new Set(acknowledgedIds.map(String));
  const snapshots = new Map(batch.signals.map((signal) => [signal.id, canonicalJson(signal)]));
  return (Array.isArray(current) ? current : []).filter((signal) => {
    const normalized = signalEntry(signal);
    return !acknowledged.has(normalized.id)
      || snapshots.get(normalized.id) !== canonicalJson(normalized);
  });
}
