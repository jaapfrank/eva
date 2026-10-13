import { createHash } from 'node:crypto';

const HASH = /^[a-f0-9]{64}$/u;
const TERMINAL_STATUSES = new Set([
  'skipped', 'not_exercised', 'passed', 'failed', 'error', 'invalid',
]);
const RESULT_KEYS = [
  'caseInstanceId', 'status', 'deterministicGates', 'evidenceRefs', 'metrics',
  'criticalFailures', 'firstBreach',
];
const METRIC_KEYS = [
  'toolCalls', 'correctToolCalls', 'toolErrors', 'recoveredToolErrors',
  'inputTokens', 'outputTokens', 'wallClockMs', 'externalCostMinor',
  'interventions', 'turnCount',
];

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));
const sha256 = (value) => createHash('sha256').update(
  typeof value === 'string' ? value : canonical(value),
).digest('hex');

function exactKeys(value, keys) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical([...keys].sort()));
}

function canonicalIso(value) {
  const millis = Date.parse(String(value || ''));
  return Number.isFinite(millis) && new Date(millis).toISOString() === value;
}

function validMetrics(metrics, budgets) {
  if (!exactKeys(metrics, METRIC_KEYS)
    || !METRIC_KEYS.every((key) => Number.isSafeInteger(metrics[key]) && metrics[key] >= 0)
    || metrics.correctToolCalls > metrics.toolCalls
    || metrics.toolErrors > metrics.toolCalls
    || metrics.recoveredToolErrors > metrics.toolErrors
    || metrics.inputTokens + metrics.outputTokens > budgets.maxTokensPerCase
    || metrics.wallClockMs > budgets.maxWallClockMs
    || metrics.externalCostMinor > budgets.maxExternalCostMinor
    || metrics.interventions > budgets.maxInterventions) return false;
  return true;
}

function resultEvidenceValid(result, instance) {
  if (!Array.isArray(result.deterministicGates) || !Array.isArray(result.evidenceRefs)
    || !Array.isArray(result.criticalFailures)
    || !result.criticalFailures.every((failure) => (
      typeof failure === 'string' && failure.trim() && failure.length <= 160
    ))) return false;
  const gates = result.deterministicGates;
  const evidence = result.evidenceRefs;
  if (!gates.every((gate) => exactKeys(gate, ['gateId', 'passed'])
    && instance.semanticGateIds.includes(gate.gateId) && typeof gate.passed === 'boolean')
    || new Set(gates.map((gate) => gate.gateId)).size !== gates.length) return false;
  if (!evidence.every((item) => exactKeys(item, ['kind', 'evidenceSha256'])
    && instance.requiredEvidenceKinds.includes(item.kind)
    && HASH.test(String(item.evidenceSha256 || '')))) return false;
  if (result.status !== 'passed') return true;
  const gateIds = [...new Set(gates.filter((gate) => gate.passed).map((gate) => gate.gateId))];
  const evidenceKinds = new Set(evidence.map((item) => item.kind));
  return canonical(gateIds.sort()) === canonical([...instance.semanticGateIds].sort())
    && instance.requiredEvidenceKinds.every((kind) => evidenceKinds.has(kind))
    && result.criticalFailures.length === 0 && result.firstBreach == null;
}

function validFirstBreach(value, instance) {
  if (value == null) return true;
  return exactKeys(value, ['phase', 'gateId', 'kind', 'evidenceSha256'])
    && typeof value.phase === 'string' && value.phase.trim().length > 0
    && instance.semanticGateIds.includes(value.gateId)
    && ['direct', 'inferred', 'unmapped'].includes(value.kind)
    && HASH.test(String(value.evidenceSha256 || ''));
}

function validateTerminalResult(result, instance, plan) {
  if (!exactKeys(result, RESULT_KEYS) || result.caseInstanceId !== instance.caseInstanceId
    || !TERMINAL_STATUSES.has(result.status) || !validMetrics(result.metrics, plan.budgets)
    || !validFirstBreach(result.firstBreach, instance)
    || !resultEvidenceValid(result, instance)) {
    throw new TypeError('LAB-resultaat heeft ongeldige metrieken, gates of bewijs');
  }
  return Object.freeze({
    ...result,
    deterministicGates: Object.freeze(result.deterministicGates.map((gate) => Object.freeze({ ...gate }))),
    evidenceRefs: Object.freeze(result.evidenceRefs.map((item) => Object.freeze({ ...item }))),
    metrics: Object.freeze({ ...result.metrics }),
    criticalFailures: Object.freeze([...result.criticalFailures]),
    firstBreach: result.firstBreach ? Object.freeze({ ...result.firstBreach }) : null,
  });
}

function noRunResult(instance) {
  return Object.freeze({
    caseInstanceId: instance.caseInstanceId,
    status: 'no_run',
    deterministicGates: Object.freeze([]),
    evidenceRefs: Object.freeze([]),
    metrics: Object.freeze(Object.fromEntries(METRIC_KEYS.map((key) => [key, 0]))),
    criticalFailures: Object.freeze([]),
    firstBreach: null,
  });
}

function ledgerEntry(sequence, recordedAt, previousEntrySha256, result) {
  const body = { sequence, recordedAt, previousEntrySha256, result };
  return Object.freeze({ ...body, entrySha256: sha256(body) });
}

function planInstances(plan) {
  return new Map((Array.isArray(plan?.instances) ? plan.instances : []).map((instance) => (
    [instance.caseInstanceId, instance]
  )));
}

export function createLabResultLedger(plan, createdAt = new Date().toISOString()) {
  if (plan?.contract !== 'LabRunPlan/v1' || !HASH.test(String(plan.planSha256 || ''))
    || !canonicalIso(createdAt) || !Array.isArray(plan.instances) || !plan.instances.length) {
    throw new TypeError('LAB-plan of starttijd is ongeldig');
  }
  let previousEntrySha256 = null;
  const entries = plan.instances.map((instance, index) => {
    const entry = ledgerEntry(index + 1, createdAt, previousEntrySha256, noRunResult(instance));
    previousEntrySha256 = entry.entrySha256;
    return entry;
  });
  const body = {
    schema: 1,
    contract: 'LabResultLedger/v1',
    planSha256: plan.planSha256,
    runId: plan.runId,
    participantId: plan.participant.id,
    lane: plan.lane,
    track: plan.track,
    planBudgets: Object.freeze({ ...plan.budgets }),
    planInstances: Object.freeze(plan.instances.map((instance) => Object.freeze({ ...instance }))),
    createdAt,
    entries: Object.freeze(entries),
  };
  return Object.freeze({ ...body, ledgerSha256: sha256(body) });
}

function latestStatuses(ledger) {
  const latest = new Map();
  for (const entry of ledger.entries) latest.set(entry.result.caseInstanceId, entry.result);
  return latest;
}

export function verifyLabResultLedger(ledger, plan) {
  try {
    if (ledger?.contract !== 'LabResultLedger/v1' || ledger.schema !== 1
      || ledger.planSha256 !== plan?.planSha256 || ledger.runId !== plan.runId
      || ledger.participantId !== plan.participant.id || ledger.lane !== plan.lane
      || ledger.track !== plan.track
      || canonical(ledger.planBudgets) !== canonical(plan.budgets)
      || canonical(ledger.planInstances) !== canonical(plan.instances)
      || !canonicalIso(ledger.createdAt)
      || !Array.isArray(ledger.entries) || !HASH.test(String(ledger.ledgerSha256 || ''))) return false;
    const instances = planInstances(plan);
    let previous = null;
    const seenInitial = new Set();
    const terminal = new Set();
    for (let index = 0; index < ledger.entries.length; index += 1) {
      const entry = ledger.entries[index];
      const instance = instances.get(entry?.result?.caseInstanceId);
      const { entrySha256, ...body } = entry || {};
      if (!instance || entry.sequence !== index + 1 || !canonicalIso(entry.recordedAt)
        || entry.previousEntrySha256 !== previous || sha256(body) !== entrySha256) return false;
      if (entry.result.status === 'no_run') {
        if (seenInitial.has(instance.caseInstanceId)
          || canonical(entry.result) !== canonical(noRunResult(instance))) return false;
        seenInitial.add(instance.caseInstanceId);
      } else {
        if (!seenInitial.has(instance.caseInstanceId) || terminal.has(instance.caseInstanceId)) return false;
        validateTerminalResult(entry.result, instance, plan);
        terminal.add(instance.caseInstanceId);
      }
      previous = entrySha256;
    }
    if (seenInitial.size !== instances.size) return false;
    const { ledgerSha256, ...body } = ledger;
    return sha256(body) === ledgerSha256;
  } catch { return false; }
}

export function appendLabCaseResult(ledger, result, recordedAt = new Date().toISOString()) {
  const plan = ledger?._plan;
  if (plan) throw new TypeError('interne planvelden zijn niet toegestaan');
  if (!canonicalIso(recordedAt) || !Array.isArray(ledger?.entries)) {
    throw new TypeError('LAB-resultaattijd of ledger is ongeldig');
  }
  const syntheticPlan = {
    contract: 'LabRunPlan/v1', planSha256: ledger.planSha256, runId: ledger.runId,
    participant: { id: ledger.participantId }, lane: ledger.lane, track: ledger.track,
    budgets: ledger.planBudgets,
    instances: ledger.planInstances,
  };
  const instance = syntheticPlan.instances?.find((item) => (
    item.caseInstanceId === result?.caseInstanceId
  ));
  if (!instance || !syntheticPlan.budgets) {
    throw new TypeError('LAB-ledger mist zijn verzegelde planprojectie');
  }
  const current = latestStatuses(ledger).get(instance.caseInstanceId);
  if (!current || current.status !== 'no_run') {
    throw new TypeError('LAB-case is al definitief afgehandeld');
  }
  const exactResult = validateTerminalResult(result, instance, syntheticPlan);
  const previous = ledger.entries.at(-1)?.entrySha256 || null;
  const entry = ledgerEntry(ledger.entries.length + 1, recordedAt, previous, exactResult);
  const body = { ...ledger, entries: Object.freeze([...ledger.entries, entry]) };
  delete body.ledgerSha256;
  return Object.freeze({ ...body, ledgerSha256: sha256(body) });
}

export function summarizeLabResultLedger(ledger, plan) {
  if (!verifyLabResultLedger(ledger, plan)) throw new TypeError('LAB-ledger is ongeldig');
  const latest = latestStatuses(ledger);
  const results = [...latest.values()];
  const statusCounts = Object.fromEntries([
    'no_run', ...TERMINAL_STATUSES,
  ].map((status) => [status, results.filter((result) => result.status === status).length]));
  const measured = results.filter((result) => result.status !== 'no_run');
  const sums = Object.fromEntries(METRIC_KEYS.map((key) => [
    key, measured.reduce((total, result) => total + result.metrics[key], 0),
  ]));
  const passed = statusCounts.passed;
  return Object.freeze({
    contract: 'LabRunSummary/v1',
    runId: plan.runId, participantId: plan.participant.id, lane: plan.lane, track: plan.track,
    complete: statusCounts.no_run === 0,
    statusCounts: Object.freeze(statusCounts),
    metrics: Object.freeze({
      toolCallAccuracy: sums.toolCalls ? sums.correctToolCalls / sums.toolCalls : null,
      recoveryRate: sums.toolErrors ? sums.recoveredToolErrors / sums.toolErrors : null,
      totalTokens: sums.inputTokens + sums.outputTokens,
      tokensPerPassedCase: passed ? (sums.inputTokens + sums.outputTokens) / passed : null,
      wallClockMs: sums.wallClockMs,
      externalCostMinor: sums.externalCostMinor,
      interventions: sums.interventions,
      meanTurnCount: measured.length ? sums.turnCount / measured.length : null,
    }),
  });
}
