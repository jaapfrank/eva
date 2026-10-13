import { createHash } from 'node:crypto';

const SHA256 = /^[a-f0-9]{64}$/u;
const REQUIREMENT_ID = /^r(?:[1-9]|1[0-2])$/u;
const ENTITY_ID = /^[a-z0-9_]+\.[a-z0-9_]+$/iu;
const FACT_ID = /^monitoring-fact:[a-f0-9]{64}$/u;
const CONDITION_ID = /^monitoring-condition:[a-f0-9]{64}$/u;
const CONDITION_ROLES = new Set(['action', 'guard', 'recovery', 'completion']);
const OPERANDS = new Set(['subject', 'left', 'right', 'reference', 'threshold']);
const TEMPERATURE_INTENT = /\b(?:temperatuur[\p{L}]*|koud(?:er|ste)?|warm(?:er|ste)?|graden?)\b|[°]\s*[cf]\b/iu;
const BINDING_FIELDS = Object.freeze([
  'contract', 'requirementId', 'entityId', 'conditionId', 'operand',
  'sourceField', 'valueType', 'quantityKind', 'unitField', 'expectedUnit',
  'maxAgeMs', 'issuedExecutionKey', 'issuedCompletionRecordSha256', 'bindingSha256',
]);
const FACT_REFERENCE_FIELDS = Object.freeze([
  'bindingSha256', 'requirementId', 'executionKey', 'completionRecordSha256', 'factId',
]);

function plainObject(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonical(value[key])}`
  )).join(',')}}`;
}

function exactKeys(value, keys) {
  return plainObject(value)
    && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}

function sha256(value) {
  return createHash('sha256').update(
    typeof value === 'string' ? value : canonical(value),
  ).digest('hex');
}

function freeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function text(value, max = 500) {
  const result = String(value ?? '').trim();
  return result && result.length <= max ? result : '';
}

function scalarText(value, max = 80) {
  return typeof value === 'string' && value.trim() && value.trim().length <= max
    ? value.trim() : null;
}

function canonicalMonitoringText(value, max = 4_000) {
  return String(value ?? '')
    .normalize('NFKC')
    // Een expliciete C0/C1-filter is de transportcanonicalisatie, niet invoervalidatie.
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f-\u009f]+/gu, ' ')
    .replace(/[\u2013\u2014]/gu, '-')
    .replace(/-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/giu,
      '[AFGESCHERMD:PRIVATE_KEY]')
    .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/-]{12,}/giu, '$1[AFGESCHERMD:TOKEN]')
    .replace(/((?:api[-_ ]?key|access[-_ ]?token|password|wachtwoord|passphrase|secret)\s*(?:is|was|=|:)\s*["'`]?)\S{8,}/giu,
      '$1[AFGESCHERMD:SECRET]')
    .replace(/\b(?:sk-(?:proj-)?|ghp_|github_pat_)[A-Za-z0-9_-]{12,}\b/gu,
      '[AFGESCHERMD:TOKEN]')
    .replace(/\s+/gu, ' ')
    .trim()
    .slice(0, max)
    .trimEnd();
}

function finiteNumber(value, allowString = false) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (!allowString || typeof value !== 'string'
    || !/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/u.test(value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function parsedReceipt(receipt) {
  if (!plainObject(receipt) || receipt.name !== 'ha_lees'
    || receipt.ok !== true || receipt.verified !== true || receipt.changed !== false
    || receipt.partial === true || receipt.needs_followup === true
    || receipt.effect_uncertain === true) return null;
  let payload;
  try { payload = JSON.parse(String(receipt.output || '')); } catch { return null; }
  if (!plainObject(payload) || payload.contract !== 'ActionResult/v1'
    || payload.tool !== 'ha_lees' || payload.ok !== true || payload.verified !== true
    || payload.changed !== false || payload.partial === true
    || payload.needs_followup === true || payload.effect_uncertain === true
    || !plainObject(payload.data)) return null;
  return payload;
}

function issuedField(entityId, data, sourceText) {
  const domain = entityId.split('.')[0].toLowerCase();
  const temperature = TEMPERATURE_INTENT.test(sourceText);
  if (temperature && ['climate', 'weather'].includes(domain)) {
    const sourceField = domain === 'climate' ? 'current_temperature' : 'temperature';
    const value = finiteNumber(data[sourceField]);
    const unit = scalarText(data.temperature_unit, 24);
    return value == null || !unit ? null : {
      sourceField, valueType: 'number', quantityKind: 'temperature',
      unitSource: { kind: 'field', field: 'temperature_unit' }, expectedUnit: unit,
    };
  }
  if (domain === 'sensor') {
    const numeric = finiteNumber(data.state, true);
    if (numeric != null) {
      const unit = scalarText(data.unit, 24);
      return unit ? {
        sourceField: 'state', valueType: 'number', quantityKind: 'measurement',
        unitSource: { kind: 'field', field: 'unit' }, expectedUnit: unit,
      } : null;
    }
  }
  const state = scalarText(data.state, 80);
  if (!state) return null;
  return {
    sourceField: 'state', valueType: 'string', quantityKind: 'state',
    unitSource: { kind: 'none', field: null }, expectedUnit: null,
  };
}

function bindingBody(value) {
  return {
    contract: 'MonitoringFactBinding/v1',
    requirementId: value.requirementId,
    entityId: value.entityId,
    conditionId: value.conditionId,
    operand: value.operand,
    sourceField: value.sourceField,
    valueType: value.valueType,
    quantityKind: value.quantityKind,
    unitField: value.unitField,
    expectedUnit: value.expectedUnit,
    maxAgeMs: value.maxAgeMs,
    issuedExecutionKey: value.issuedExecutionKey,
    issuedCompletionRecordSha256: value.issuedCompletionRecordSha256,
  };
}

function monitoringCondition(statement, role = 'guard') {
  const normalizedStatement = text(statement, 500);
  const normalizedRole = String(role || '');
  if (!normalizedStatement || !CONDITION_ROLES.has(normalizedRole)) return null;
  const body = { statement: normalizedStatement, role: normalizedRole };
  return freeze({
    conditionId: `monitoring-condition:${sha256(body)}`,
    ...body,
  });
}

export function monitoringConditionId(statement, role = 'guard') {
  return monitoringCondition(statement, role)?.conditionId || null;
}

function monitoringConditionReference(value) {
  const conditionId = typeof value === 'string' ? value : value?.conditionId;
  if ((plainObject(value) && !exactKeys(value, ['conditionId']))
    || !CONDITION_ID.test(String(conditionId || ''))) return null;
  return String(conditionId);
}

function bindingDictionary(bindings) {
  return freeze({
    contract: 'MonitoringFactBindingDictionary/v1',
    fields: [...BINDING_FIELDS],
    entries: bindings.map((binding) => BINDING_FIELDS.map((field) => binding[field])),
  });
}

function unpackBindingDictionary(value) {
  if (!exactKeys(value, ['contract', 'fields', 'entries'])
    || value.contract !== 'MonitoringFactBindingDictionary/v1'
    || !Array.isArray(value.fields) || canonical(value.fields) !== canonical(BINDING_FIELDS)
    || !Array.isArray(value.entries) || !value.entries.length || value.entries.length > 8) {
    return null;
  }
  const bindings = value.entries.map((entry) => (
    Array.isArray(entry) && entry.length === BINDING_FIELDS.length
      ? normalizeMonitoringFactBinding(Object.fromEntries(
        BINDING_FIELDS.map((field, index) => [field, entry[index]]),
      )) : null
  ));
  return bindings.some((binding) => !binding) ? null : bindings;
}

function normalizedBindingList(value) {
  if (Array.isArray(value)) {
    if (!value.length || value.length > 8) return null;
    const bindings = value.map((item) => normalizeMonitoringFactBinding(item?.factBinding || item));
    return bindings.some((binding) => !binding) ? null : bindings;
  }
  return unpackBindingDictionary(value);
}

export function monitoringContractReadBindings(contractValue) {
  const bindings = unpackBindingDictionary(contractValue?.readBindings);
  return bindings ? freeze([...bindings]) : null;
}

export function normalizeMonitoringFactBinding(value) {
  if (!exactKeys(value, [
    'contract', 'requirementId', 'entityId', 'conditionId', 'operand',
    'sourceField', 'valueType', 'quantityKind', 'unitField', 'expectedUnit',
    'maxAgeMs', 'issuedExecutionKey', 'issuedCompletionRecordSha256', 'bindingSha256',
  ]) || value.contract !== 'MonitoringFactBinding/v1'
    || !REQUIREMENT_ID.test(String(value.requirementId || ''))
    || !ENTITY_ID.test(String(value.entityId || ''))
    || !CONDITION_ID.test(String(value.conditionId || ''))
    || !OPERANDS.has(String(value.operand || ''))
    || !['state', 'temperature', 'current_temperature'].includes(value.sourceField)
    || !['number', 'string'].includes(value.valueType)
    || !['temperature', 'measurement', 'state'].includes(value.quantityKind)
    || !Number.isSafeInteger(value.maxAgeMs)
    || value.maxAgeMs < 15_000 || value.maxAgeMs > 24 * 60 * 60_000
    || !text(value.issuedExecutionKey, 300)
    || !SHA256.test(String(value.issuedCompletionRecordSha256 || ''))
    || !SHA256.test(String(value.bindingSha256 || ''))) return null;
  const unitField = value.unitField == null ? null : scalarText(value.unitField, 40);
  const expectedUnit = value.expectedUnit == null ? null : scalarText(value.expectedUnit, 24);
  if ((value.valueType === 'number') !== (value.quantityKind !== 'state')
    || (unitField == null ? expectedUnit !== null : !expectedUnit)
    || (value.quantityKind === 'temperature'
      && !['temperature', 'current_temperature'].includes(value.sourceField))
    || (value.quantityKind !== 'temperature'
      && value.sourceField !== 'state')) return null;
  const normalized = bindingBody({
    ...value,
    requirementId: String(value.requirementId), entityId: String(value.entityId),
    conditionId: String(value.conditionId), operand: String(value.operand),
    sourceField: String(value.sourceField), valueType: String(value.valueType),
    quantityKind: String(value.quantityKind),
    unitField, expectedUnit, maxAgeMs: Number(value.maxAgeMs),
    issuedExecutionKey: String(value.issuedExecutionKey),
    issuedCompletionRecordSha256: String(value.issuedCompletionRecordSha256),
  });
  const bindingSha256 = sha256(normalized);
  return bindingSha256 === value.bindingSha256
    ? freeze({ ...normalized, bindingSha256 }) : null;
}

export function issueMonitoringFactBinding({
  requirementId, entityId, name, condition, conditionRole = 'guard',
  operand = 'subject', sourceText, sourceUnderstandingSha256,
  freshness = {}, receipt, verifyInitialReceipt = null,
} = /** @type {any} */ ({})) {
  const payload = parsedReceipt(receipt);
  const exactEntityId = text(entityId, 255);
  const binding = receipt?.semanticBinding;
  const data = payload?.data;
  const sourceUpdatedAt = Date.parse(data?.updated || '');
  const maxAgeMs = Math.max(15_000, Math.min(
    24 * 60 * 60_000, Math.trunc(Number(freshness.maxAgeMs) || 15 * 60_000),
  ));
  const issuedLedger = exactLedger(receipt, verifyInitialReceipt);
  if (!issuedLedger || !payload || !ENTITY_ID.test(exactEntityId)
    || receipt.args?.entity_id !== exactEntityId || data.entity_id !== exactEntityId
    || !REQUIREMENT_ID.test(String(requirementId || ''))
    || binding?.requirementId !== requirementId
    || binding?.capabilityId !== 'home.entity.read' || binding?.operationId !== 'read'
    || binding?.understandingSha256 !== sourceUnderstandingSha256
    || !SHA256.test(String(sourceUnderstandingSha256 || ''))
    || !Number.isFinite(sourceUpdatedAt)
    || sourceUpdatedAt > Date.parse(issuedLedger.finishedTs) + 30_000) return null;
  const source = text(sourceText, 500);
  const selected = issuedField(exactEntityId, data, `${source} ${condition || ''}`);
  const issuedCondition = monitoringCondition(condition, conditionRole);
  if (!source || !text(name || data.name || exactEntityId, 200)
    || !selected || !issuedCondition) return null;
  const body = bindingBody({
    contract: 'MonitoringFactBinding/v1',
    requirementId: String(requirementId), entityId: exactEntityId,
    conditionId: issuedCondition.conditionId,
    operand: String(operand), sourceField: selected.sourceField,
    valueType: selected.valueType, quantityKind: selected.quantityKind,
    unitField: selected.unitSource.field, expectedUnit: selected.expectedUnit,
    maxAgeMs,
    issuedExecutionKey: issuedLedger.executionKey,
    issuedCompletionRecordSha256: issuedLedger.completionRecordSha256,
  });
  const candidate = { ...body, bindingSha256: sha256(body) };
  return normalizeMonitoringFactBinding(candidate);
}

function exactLedger(receipt, verifyLedgerReceipt) {
  if (typeof verifyLedgerReceipt !== 'function') return null;
  let ledger;
  try { ledger = verifyLedgerReceipt(receipt); } catch { return null; }
  const output = String(receipt?.output || '');
  if (!plainObject(ledger) || !text(ledger.executionKey, 300)
    || !SHA256.test(String(ledger.intentRecordSha256 || ''))
    || !SHA256.test(String(ledger.outputSha256 || ''))
    || ledger.outputSha256 !== sha256(output)
    || !SHA256.test(String(ledger.completionRecordSha256 || ''))
    || !Number.isFinite(Date.parse(ledger.finishedTs || ''))) return null;
  return {
    executionKey: String(ledger.executionKey),
    intentRecordSha256: String(ledger.intentRecordSha256),
    outputSha256: String(ledger.outputSha256),
    completionRecordSha256: String(ledger.completionRecordSha256),
    finishedTs: new Date(ledger.finishedTs).toISOString(),
  };
}

export function buildMonitoringMeasurementFact(bindingValue, receipt, {
  now = Date.now(), verifyLedgerReceipt = null, sourceUnderstandingSha256 = null,
} = {}) {
  const binding = normalizeMonitoringFactBinding(bindingValue);
  const payload = parsedReceipt(receipt);
  const ledger = exactLedger(receipt, verifyLedgerReceipt);
  if (!binding || !payload || !ledger
    || receipt.args?.entity_id !== binding.entityId
    || payload.data.entity_id !== binding.entityId
    || !SHA256.test(String(sourceUnderstandingSha256 || ''))
    || receipt.semanticBinding?.understandingSha256 !== sourceUnderstandingSha256
    || receipt.semanticBinding?.requirementId !== binding.requirementId
    || receipt.semanticBinding?.capabilityId !== 'home.entity.read'
    || receipt.semanticBinding?.operationId !== 'read') return null;
  const sourceUpdatedMs = Date.parse(payload.data.updated || '');
  const readMs = Date.parse(ledger.finishedTs);
  const currentMs = Number(now);
  if (!Number.isFinite(sourceUpdatedMs) || !Number.isFinite(readMs)
    || !Number.isFinite(currentMs) || sourceUpdatedMs > readMs + 30_000
    || readMs > currentMs + 30_000
    || currentMs - readMs > binding.maxAgeMs) return null;
  let value;
  if (binding.valueType === 'number') {
    value = finiteNumber(payload.data[binding.sourceField], binding.sourceField === 'state');
    if (value == null) return null;
  } else {
    value = scalarText(payload.data[binding.sourceField], 80);
    if (!value) return null;
  }
  const unit = binding.unitField == null
    ? null : scalarText(payload.data[binding.unitField], 24);
  if (unit !== binding.expectedUnit) return null;
  const body = {
    contract: 'MonitoringMeasurementFact/v1',
    bindingSha256: binding.bindingSha256,
    requirementId: binding.requirementId,
    entityId: binding.entityId,
    name: text(payload.data.name || binding.entityId, 200),
    conditionId: binding.conditionId,
    operand: binding.operand,
    sourceField: binding.sourceField,
    valueType: binding.valueType,
    quantityKind: binding.quantityKind,
    value,
    unit,
    observedAt: ledger.finishedTs,
    sourceUpdatedAt: new Date(sourceUpdatedMs).toISOString(),
    readAt: ledger.finishedTs,
    executionKey: ledger.executionKey,
    intentRecordSha256: ledger.intentRecordSha256,
    outputSha256: ledger.outputSha256,
    completionRecordSha256: ledger.completionRecordSha256,
    evidenceSha256: sha256(payload.evidence ?? null),
  };
  const factSha256 = sha256(body);
  return freeze({ ...body, factSha256, factId: `monitoring-fact:${factSha256}` });
}

const FACT_FIELDS = Object.freeze([
  'contract', 'bindingSha256', 'requirementId', 'entityId', 'name', 'conditionId',
  'operand', 'sourceField', 'valueType', 'quantityKind', 'value',
  'unit', 'observedAt', 'sourceUpdatedAt', 'readAt', 'executionKey', 'intentRecordSha256',
  'outputSha256', 'completionRecordSha256', 'evidenceSha256', 'factSha256', 'factId',
]);

function monitoringFactShapeValid(value) {
  return exactKeys(value, FACT_FIELDS) && value.contract === 'MonitoringMeasurementFact/v1'
    && SHA256.test(String(value.bindingSha256 || ''))
    && REQUIREMENT_ID.test(String(value.requirementId || ''))
    && ENTITY_ID.test(String(value.entityId || '')) && Boolean(text(value.name, 200))
    && CONDITION_ID.test(String(value.conditionId || ''))
    && OPERANDS.has(value.operand)
    && ['state', 'temperature', 'current_temperature'].includes(value.sourceField)
    && ['number', 'string'].includes(value.valueType)
    && ['temperature', 'measurement', 'state'].includes(value.quantityKind)
    && Boolean(text(value.executionKey, 300))
    && SHA256.test(String(value.intentRecordSha256 || ''))
    && SHA256.test(String(value.outputSha256 || ''))
    && SHA256.test(String(value.completionRecordSha256 || ''))
    && SHA256.test(String(value.evidenceSha256 || ''))
    && SHA256.test(String(value.factSha256 || ''))
    && value.factId === `monitoring-fact:${value.factSha256}`;
}

function monitoringFactTimesValid(value) {
  const observedAt = Date.parse(value.observedAt || '');
  const sourceUpdatedAt = Date.parse(value.sourceUpdatedAt || '');
  const readAt = Date.parse(value.readAt || '');
  return Number.isFinite(observedAt) && Number.isFinite(sourceUpdatedAt)
    && Number.isFinite(readAt) && observedAt === readAt
    && sourceUpdatedAt <= readAt + 30_000;
}

export function normalizeMonitoringMeasurementFact(value) {
  if (!monitoringFactShapeValid(value) || !monitoringFactTimesValid(value)) return null;
  const numberValue = value.valueType === 'number' ? finiteNumber(value.value) : null;
  const stringValue = value.valueType === 'string' ? scalarText(value.value, 80) : null;
  const unit = value.unit == null ? null : scalarText(value.unit, 24);
  if ((value.valueType === 'number' && numberValue == null)
    || (value.valueType === 'string' && !stringValue)
    || (value.quantityKind === 'state' ? unit !== null : unit === null)
    || (value.quantityKind === 'temperature'
      && !['temperature', 'current_temperature'].includes(value.sourceField))
    || (value.quantityKind !== 'temperature' && value.sourceField !== 'state')) return null;
  const body = {
    ...Object.fromEntries(FACT_FIELDS.slice(0, -2).map((key) => [key, value[key]])),
    value: value.valueType === 'number' ? numberValue : stringValue,
    unit,
    observedAt: new Date(value.observedAt).toISOString(),
    sourceUpdatedAt: new Date(value.sourceUpdatedAt).toISOString(),
    readAt: new Date(value.readAt).toISOString(),
  };
  const factSha256 = sha256(body);
  if (factSha256 !== value.factSha256) return null;
  return freeze({
    ...body,
    factSha256,
    factId: `monitoring-fact:${factSha256}`,
  });
}

export function normalizeMonitoringOutcomeDecision(raw, facts = []) {
  if (!plainObject(raw) || Object.keys(raw).sort().join(',') !== 'factIds,terminal,verdict'
    || !['notify', 'silent'].includes(raw.verdict) || typeof raw.terminal !== 'boolean'
    || (raw.verdict === 'notify') !== raw.terminal || !Array.isArray(raw.factIds)) return null;
  if (!Array.isArray(facts) || !facts.length || facts.length > 8) return null;
  const normalizedFacts = facts.map(normalizeMonitoringMeasurementFact);
  if (normalizedFacts.some((fact) => !fact)) return null;
  const expected = normalizedFacts.map((fact) => String(fact.factId));
  if (!expected.length || expected.some((id) => !FACT_ID.test(id))
    || raw.factIds.length !== expected.length
    || raw.factIds.some((id, index) => id !== expected[index])) return null;
  return { verdict: raw.verdict, terminal: raw.terminal, factIds: [...expected] };
}

export function monitoringOutcomeRequestRecord({
  monitoring, conditionContext, facts, attempt,
} = /** @type {any} */ ({})) {
  const contract = normalizeMonitoringContract(monitoring);
  const normalizedFacts = Array.isArray(facts)
    ? facts.map(normalizeMonitoringMeasurementFact) : [];
  const conditions = Array.isArray(conditionContext) ? conditionContext.map((condition) => {
    if (!exactKeys(condition, ['statement', 'role'])) return null;
    const statement = text(condition.statement, 500);
    const role = text(condition.role, 40);
    const conditionId = monitoringConditionId(statement, role);
    return statement && role && conditionId ? { conditionId, statement, role } : null;
  }) : [];
  if (!contract || !Number.isSafeInteger(attempt) || attempt < 1 || attempt > 2
    || !conditions.length || conditions.some((condition) => !condition)
    || canonical(conditions.map((condition) => condition.conditionId))
      !== canonical(contract.conditions)
    || normalizedFacts.some((fact) => !fact)
    || !factsMatchMonitoringContract(contract, normalizedFacts)) return null;
  const body = {
    contract: 'MonitoringOutcomeRequest/v1',
    monitoringId: contract.monitoringId,
    monitoringContractSha256: contract.contractSha256,
    sourceUnderstandingSha256: contract.sourceUnderstandingSha256,
    conditions,
    temporalCondition: contract.temporalCondition,
    definitionOfDone: contract.definitionOfDone,
    facts: normalizedFacts,
    attempt,
  };
  return freeze({ ...body, requestSha256: sha256(body) });
}

export function monitoringOutcomeResponseRecord({
  requestSha256, accepted, outcome, facts, failure,
} = /** @type {any} */ ({})) {
  if (!SHA256.test(String(requestSha256 || '')) || typeof accepted !== 'boolean') return null;
  const decision = accepted ? normalizeMonitoringOutcomeDecision(outcome, facts) : null;
  const normalizedFailure = failure == null ? null : text(failure, 300) || null;
  if ((accepted && (!decision || failure !== null))
    || (!accepted && (outcome !== null || (failure != null && !normalizedFailure)))) return null;
  const body = {
    contract: 'MonitoringOutcomeResponse/v1',
    requestSha256: String(requestSha256),
    accepted,
    outcome: decision,
    failure: normalizedFailure,
  };
  return freeze({ ...body, responseSha256: sha256(body) });
}

function renderedValue(fact) {
  const value = typeof fact?.value === 'number' && Number.isFinite(fact.value)
    ? String(fact.value) : canonicalMonitoringText(fact?.value, 80);
  if (!value) return null;
  const unit = fact?.unit == null ? null : canonicalMonitoringText(fact.unit, 24);
  return `${value}${unit ? ` ${unit}` : ''}`;
}

export function renderMonitoringTerminalText(facts = []) {
  if (!Array.isArray(facts) || !facts.length || facts.length > 8
    || Buffer.byteLength(canonical(facts), 'utf8') > 32_000) return '';
  const normalized = facts.map(normalizeMonitoringMeasurementFact);
  if (normalized.some((fact) => !fact)
    || new Set(normalized.map((fact) => fact.factId)).size !== normalized.length) return '';
  const readings = normalized.map((fact) => {
    const name = canonicalMonitoringText(fact?.name, 200);
    const value = renderedValue(fact);
    return name && value ? `${name} is ${value}` : null;
  });
  if (!readings.length || readings.some((item) => !item)) return '';
  const joined = readings.length === 1 ? readings[0]
    : readings.length === 2 ? `${readings[0]} en ${readings[1]}`
      : `${readings.slice(0, -1).join(', ')} en ${readings.at(-1)}`;
  return `De afgesproken voorwaarde is bereikt. ${joined}.`;
}

function presentationBody(value) {
  return {
    contract: 'MonitoringPresentationContext/v1',
    monitoringId: value.monitoringId,
    audience: 'requester',
    requesterLabel: value.requesterLabel,
    locale: value.locale,
    sourceTextSha256: value.sourceTextSha256,
  };
}

export function issueMonitoringPresentationContext({
  monitoringId, requesterLabel, sourceTextSha256, locale = 'nl-NL',
} = /** @type {any} */ ({})) {
  const body = presentationBody({
    monitoringId: text(monitoringId, 100),
    requesterLabel: text(requesterLabel, 120),
    locale: text(locale, 20),
    sourceTextSha256: String(sourceTextSha256 || ''),
  });
  if (!/^monitoring:[a-f0-9]{64}$/u.test(body.monitoringId)
    || !body.requesterLabel || body.locale !== 'nl-NL'
    || !SHA256.test(body.sourceTextSha256)) return null;
  const presentationSha256 = sha256(body);
  return freeze({ ...body, presentationSha256 });
}

export function normalizeMonitoringPresentationContext(value) {
  if (!exactKeys(value, [
    'contract', 'monitoringId', 'audience', 'requesterLabel', 'locale',
    'sourceTextSha256', 'presentationSha256',
  ]) || value.contract !== 'MonitoringPresentationContext/v1'
    || value.audience !== 'requester') return null;
  const issued = issueMonitoringPresentationContext(value);
  return issued?.presentationSha256 === value.presentationSha256 ? issued : null;
}

function deliveryBody(value) {
  return {
    contract: 'MonitoringDeliveryBinding/v1',
    monitoringId: value.monitoringId,
    deliveryRequired: value.deliveryRequired,
    route: value.route,
    originBindingSha256: value.originBindingSha256,
    recipientResolution: 'none',
    broadcast: false,
    presentationSha256: value.presentationSha256,
  };
}

function normalizedDeliveryOrigin(origin, required) {
  if (!required) return {
    channel: null, conversationId: null, messageId: null,
    principal: null, messageThreadId: null, sourceRef: null,
    sourceSha256: null, sourceGeneration: null, archiveRowId: null,
    executionKey: null, executionSeq: null, intentRecordSha256: null,
    authoritySha256: null, originProofSha256: null,
  };
  if (!plainObject(origin)) return null;
  const normalized = {
    channel: text(origin.channel, 40) || null,
    conversationId: text(origin.conversationId, 240) || null,
    messageId: text(origin.messageId, 160) || null,
    principal: origin.principal == null ? null : text(origin.principal, 160) || null,
    messageThreadId: origin.messageThreadId == null
      ? null : Number(origin.messageThreadId),
    sourceRef: origin.sourceRef == null ? null : text(origin.sourceRef, 300) || null,
    sourceSha256: origin.sourceSha256 == null ? null : text(origin.sourceSha256, 64) || null,
    sourceGeneration: origin.sourceGeneration == null
      ? null : Number(origin.sourceGeneration),
    archiveRowId: origin.archiveRowId == null ? null : Number(origin.archiveRowId),
    executionKey: origin.executionKey == null ? null : text(origin.executionKey, 300) || null,
    executionSeq: origin.executionSeq == null ? null : Number(origin.executionSeq),
    intentRecordSha256: origin.intentRecordSha256 == null
      ? null : text(origin.intentRecordSha256, 64) || null,
    authoritySha256: origin.authoritySha256 == null
      ? null : text(origin.authoritySha256, 64) || null,
    originProofSha256: origin.originProofSha256 == null
      ? null : text(origin.originProofSha256, 64),
  };
  if (!normalized.channel || !normalized.conversationId || !normalized.messageId
    || (normalized.messageThreadId != null
      && (!Number.isSafeInteger(normalized.messageThreadId)
        || normalized.messageThreadId < 1))
    || (normalized.sourceGeneration != null
      && (!Number.isSafeInteger(normalized.sourceGeneration)
        || normalized.sourceGeneration < 1))
    || (normalized.archiveRowId != null
      && (!Number.isSafeInteger(normalized.archiveRowId)
        || normalized.archiveRowId < 1))
    || (normalized.executionSeq != null
      && (!Number.isSafeInteger(normalized.executionSeq)
        || normalized.executionSeq < 1))) return null;
  return normalized;
}

function originBindingSha256(origin, required) {
  const normalized = normalizedDeliveryOrigin(origin, required);
  if (!normalized) return null;
  return sha256({
    contract: 'MonitoringOriginBinding/v1',
    route: required ? 'direct_telegram' : 'none',
    origin: normalized,
  });
}

export function issueMonitoringDeliveryBinding({
  monitoringId, deliveryRequired = false, origin = undefined,
  originBindingSha256: suppliedOriginBindingSha256 = null, presentationSha256,
} = /** @type {any} */ ({})) {
  const required = deliveryRequired === true;
  const normalizedOrigin = origin === undefined ? null : normalizedDeliveryOrigin(origin, required);
  const computedOriginBindingSha256 = normalizedOrigin
    ? originBindingSha256(normalizedOrigin, required) : null;
  const normalizedOriginBindingSha256 = computedOriginBindingSha256
    || (SHA256.test(String(suppliedOriginBindingSha256 || ''))
      ? String(suppliedOriginBindingSha256) : null);
  if (!normalizedOriginBindingSha256
    || (computedOriginBindingSha256 && suppliedOriginBindingSha256 != null
      && computedOriginBindingSha256 !== suppliedOriginBindingSha256)
    || !SHA256.test(String(presentationSha256 || ''))) return null;
  if (required && normalizedOrigin && (normalizedOrigin.channel !== 'telegram'
    || !/^telegram:.+$/u.test(normalizedOrigin.conversationId)
    || !/^telegram:[1-9][0-9]*$/u.test(String(normalizedOrigin.principal || ''))
    || !normalizedOrigin.executionKey
    || !Number.isSafeInteger(normalizedOrigin.executionSeq)
    || !SHA256.test(String(normalizedOrigin.intentRecordSha256 || ''))
    || !SHA256.test(String(normalizedOrigin.authoritySha256 || ''))
    || !normalizedOrigin.sourceRef
    || !SHA256.test(String(normalizedOrigin.sourceSha256 || ''))
    || !Number.isSafeInteger(normalizedOrigin.sourceGeneration)
    || !Number.isSafeInteger(normalizedOrigin.archiveRowId)
    || !SHA256.test(String(normalizedOrigin.originProofSha256 || '')))) return null;
  const body = deliveryBody({
    monitoringId: text(monitoringId, 100), deliveryRequired: required,
    route: required ? 'direct_telegram' : 'none',
    originBindingSha256: normalizedOriginBindingSha256,
    presentationSha256: String(presentationSha256),
  });
  if (!/^monitoring:[a-f0-9]{64}$/u.test(body.monitoringId)) return null;
  const deliverySha256 = sha256(body);
  return freeze({ ...body, deliverySha256 });
}

export function normalizeMonitoringDeliveryBinding(value) {
  if (!exactKeys(value, [
    'contract', 'monitoringId', 'deliveryRequired', 'route', 'originBindingSha256',
    'recipientResolution', 'broadcast', 'presentationSha256', 'deliverySha256',
  ]) || value.contract !== 'MonitoringDeliveryBinding/v1'
    || value.recipientResolution !== 'none' || value.broadcast !== false
    || typeof value.deliveryRequired !== 'boolean'
    || value.route !== (value.deliveryRequired ? 'direct_telegram' : 'none')
    || !SHA256.test(String(value.originBindingSha256 || ''))
    || !SHA256.test(String(value.presentationSha256 || ''))
    || !SHA256.test(String(value.deliverySha256 || ''))) return null;
  const body = deliveryBody(value);
  return /^monitoring:[a-f0-9]{64}$/u.test(String(value.monitoringId || ''))
    && sha256(body) === value.deliverySha256
    ? freeze({ ...body, deliverySha256: value.deliverySha256 }) : null;
}

function contractBody(value) {
  return {
    contract: 'MonitoringContract/v2',
    monitoringId: value.monitoringId,
    sourceUnderstandingSha256: value.sourceUnderstandingSha256,
    conditions: value.conditions,
    temporalCondition: value.temporalCondition,
    definitionOfDone: value.definitionOfDone,
    cadenceMs: value.cadenceMs,
    endsAt: value.endsAt,
    discoveryQuery: value.discoveryQuery,
    readBindings: value.readBindings,
    presentation: value.presentation,
    delivery: value.delivery,
    createdAt: value.createdAt,
  };
}

function monitoringContractInputsValid({
  sourceUnderstandingSha256, sourceTextSha256, normalizedConditions,
  boundConditions, normalizedDefinition, definitionOfDone, temporalCondition,
  normalizedTemporal, discoveryQuery, normalizedDiscovery, normalizedBindings,
  createdMs, cadence, endsMs,
}) {
  return SHA256.test(String(sourceUnderstandingSha256 || ''))
    && SHA256.test(String(sourceTextSha256 || ''))
    && normalizedConditions.length > 0 && normalizedConditions.length <= 8
    && !normalizedConditions.some((item) => !item)
    && boundConditions.size === normalizedConditions.length
    && !normalizedConditions.some((conditionId) => !boundConditions.has(conditionId))
    && Boolean(normalizedDefinition) && normalizedDefinition === definitionOfDone
    && (temporalCondition == null || (Boolean(normalizedTemporal)
      && normalizedTemporal === temporalCondition))
    && (discoveryQuery == null || (Boolean(normalizedDiscovery)
      && normalizedDiscovery === discoveryQuery))
    && normalizedBindings.length > 0
    && new Set(normalizedBindings.map((item) => item.entityId)).size
      === normalizedBindings.length
    && new Set(normalizedBindings.map((item) => item.requirementId)).size
      === normalizedBindings.length
    && Number.isFinite(createdMs) && Number.isFinite(cadence)
    && cadence >= 15_000 && cadence <= 24 * 60 * 60_000
    && (endsMs == null || (Number.isFinite(endsMs) && endsMs > createdMs));
}

export function issueMonitoringContract({
  sourceUnderstandingSha256, conditions = [], temporalCondition = null,
  definitionOfDone = null, cadenceMs, endsAt = null, discoveryQuery = null,
  readBindings = [], createdAt, requesterLabel, sourceTextSha256, deliveryRequired,
  origin, originBindingSha256: suppliedOriginBindingSha256 = null,
} = /** @type {any} */ ({})) {
  const normalizedBindings = normalizedBindingList(readBindings);
  const createdMs = Date.parse(createdAt || '');
  const endsMs = endsAt == null ? null : Date.parse(endsAt);
  const cadence = Number(cadenceMs);
  const normalizedConditions = Array.isArray(conditions) ? conditions.map((item) => {
    const reference = monitoringConditionReference(item);
    if (reference) return reference;
    const issued = plainObject(item)
      ? monitoringCondition(item.statement, item.role) : monitoringCondition(item, 'guard');
    return issued && (!plainObject(item) || item.conditionId == null
      || item.conditionId === issued.conditionId)
      ? monitoringConditionReference(issued.conditionId) : null;
  }) : [];
  const boundConditions = new Set((normalizedBindings || []).map((item) => item?.conditionId));
  const normalizedTemporal = temporalCondition == null ? null : text(temporalCondition, 1_000);
  const normalizedDefinition = text(definitionOfDone, 1_200);
  const normalizedDiscovery = discoveryQuery == null ? null : text(discoveryQuery, 500);
  if (!normalizedBindings || !monitoringContractInputsValid({
    sourceUnderstandingSha256, sourceTextSha256, normalizedConditions,
    boundConditions, normalizedDefinition, definitionOfDone, temporalCondition,
    normalizedTemporal, discoveryQuery, normalizedDiscovery, normalizedBindings,
    createdMs, cadence, endsMs,
  })) return null;
  const required = deliveryRequired === true;
  const normalizedOrigin = origin === undefined ? null : normalizedDeliveryOrigin(origin, required);
  const computedOriginBindingSha256 = normalizedOrigin
    ? originBindingSha256(normalizedOrigin, required) : null;
  const normalizedOriginBindingSha256 = computedOriginBindingSha256
    || (SHA256.test(String(suppliedOriginBindingSha256 || ''))
      ? String(suppliedOriginBindingSha256) : null);
  if (!normalizedOriginBindingSha256
    || (computedOriginBindingSha256 && suppliedOriginBindingSha256 != null
      && computedOriginBindingSha256 !== suppliedOriginBindingSha256)) return null;
  const identity = {
    sourceUnderstandingSha256: String(sourceUnderstandingSha256),
    createdAt: new Date(createdMs).toISOString(),
    bindingSha256s: normalizedBindings.map((item) => item.bindingSha256),
    originBindingSha256: normalizedOriginBindingSha256,
    conditions: normalizedConditions,
    temporalCondition: normalizedTemporal,
    definitionOfDone: normalizedDefinition,
    cadenceMs: cadence,
    endsAt: endsMs == null ? null : new Date(endsMs).toISOString(),
    discoveryQuery: normalizedDiscovery,
  };
  const monitoringId = `monitoring:${sha256(identity)}`;
  const presentation = issueMonitoringPresentationContext({
    monitoringId, requesterLabel, sourceTextSha256,
  });
  const delivery = presentation ? issueMonitoringDeliveryBinding({
    monitoringId, deliveryRequired, origin,
    originBindingSha256: normalizedOriginBindingSha256,
    presentationSha256: presentation.presentationSha256,
  }) : null;
  if (!presentation || !delivery) return null;
  const body = contractBody({
    contract: 'MonitoringContract/v2', monitoringId,
    sourceUnderstandingSha256: String(sourceUnderstandingSha256),
    conditions: normalizedConditions,
    temporalCondition: normalizedTemporal,
    definitionOfDone: normalizedDefinition,
    cadenceMs: cadence,
    endsAt: endsMs == null ? null : new Date(endsMs).toISOString(),
    discoveryQuery: normalizedDiscovery,
    readBindings: bindingDictionary(normalizedBindings),
    presentation,
    delivery,
    createdAt: new Date(createdMs).toISOString(),
  });
  if (Buffer.byteLength(canonical(body), 'utf8') > 10_500) return null;
  const contractSha256 = sha256(body);
  return freeze({ ...body, contractSha256 });
}

export function normalizeMonitoringContract(value) {
  if (!exactKeys(value, [
    'contract', 'monitoringId', 'sourceUnderstandingSha256', 'conditions',
    'temporalCondition', 'definitionOfDone', 'cadenceMs', 'endsAt', 'discoveryQuery',
    'readBindings', 'presentation', 'delivery', 'createdAt', 'contractSha256',
  ]) || value.contract !== 'MonitoringContract/v2'
    || !plainObject(value.readBindings) || !Array.isArray(value.conditions)) return null;
  const presentation = normalizeMonitoringPresentationContext(value.presentation);
  const delivery = normalizeMonitoringDeliveryBinding(value.delivery);
  if (!presentation || !delivery
    || presentation.monitoringId !== value.monitoringId
    || delivery.monitoringId !== value.monitoringId
    || delivery.presentationSha256 !== presentation.presentationSha256) return null;
  const normalized = issueMonitoringContract({
    sourceUnderstandingSha256: value.sourceUnderstandingSha256,
    conditions: value.conditions,
    temporalCondition: value.temporalCondition,
    definitionOfDone: value.definitionOfDone,
    cadenceMs: value.cadenceMs,
    endsAt: value.endsAt,
    discoveryQuery: value.discoveryQuery,
    readBindings: value.readBindings,
    createdAt: value.createdAt,
    requesterLabel: presentation.requesterLabel,
    sourceTextSha256: presentation.sourceTextSha256,
    deliveryRequired: delivery.deliveryRequired,
    originBindingSha256: delivery.originBindingSha256,
  });
  return normalized?.monitoringId === value.monitoringId
    && normalized?.presentation.presentationSha256 === value.presentation?.presentationSha256
    && normalized?.delivery.deliverySha256 === value.delivery?.deliverySha256
    && normalized?.contractSha256 === value.contractSha256 ? normalized : null;
}

export function monitoringDeliveryMatchesAuthority(contractValue, authority) {
  const contract = normalizeMonitoringContract(contractValue);
  const expected = originBindingSha256(authority, true);
  return Boolean(contract && expected
    && contract.delivery.deliveryRequired === true
    && contract.delivery.route === 'direct_telegram'
    && contract.delivery.originBindingSha256 === expected);
}

export function monitoringStateFitsDurableBudget(state, maxBytes = 12_000) {
  if (!plainObject(state) || !Number.isSafeInteger(maxBytes) || maxBytes < 1) return false;
  try { return Buffer.byteLength(JSON.stringify(state), 'utf8') <= maxBytes; }
  catch { return false; }
}

function lifecycleBody(value) {
  return {
    contract: 'MonitoringLifecycleBinding/v1',
    monitoringId: value.monitoringId,
    monitoringContractSha256: value.monitoringContractSha256,
    episodeId: value.episodeId,
    episodeKey: value.episodeKey,
    issuedEpisodeRevision: value.issuedEpisodeRevision,
    workId: value.workId,
    originRowId: value.originRowId,
    originKey: value.originKey,
    generation: value.generation,
    originAuthority: value.originAuthority,
    originBindingSha256: value.originBindingSha256,
  };
}

function lifecycleOriginAuthority(startAuthority, deliveryOrigin, required) {
  const start = normalizedStartAuthority(startAuthority);
  const route = required ? normalizedDeliveryOrigin(deliveryOrigin, true) : null;
  if (!start || (required && !route)) return null;
  const result = {
    ...start,
    principal: required ? route.principal : null,
    messageThreadId: required ? route.messageThreadId : null,
    archiveRowId: required ? route.archiveRowId : null,
    originProofSha256: required ? route.originProofSha256 : null,
  };
  if (required && (start.executionKey !== route.executionKey
    || start.executionSeq !== route.executionSeq
    || start.intentRecordSha256 !== route.intentRecordSha256
    || start.authoritySha256 !== route.authoritySha256
    || start.channel !== route.channel || start.conversationId !== route.conversationId
    || start.messageId !== route.messageId || start.sourceRef !== route.sourceRef
    || start.sourceSha256 !== route.sourceSha256
    || start.sourceGeneration !== route.sourceGeneration)) return null;
  return result;
}

function normalizeLifecycleOriginAuthority(value, required) {
  if (!exactKeys(value, [
    'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
    'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
    'sourceSha256', 'sourceGeneration', 'principal', 'messageThreadId',
    'archiveRowId', 'originProofSha256',
  ])) return null;
  const start = normalizedStartAuthority(Object.fromEntries([
    'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
    'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
    'sourceSha256', 'sourceGeneration',
  ].map((key) => [key, value[key]])));
  if (!start) return null;
  if (required && (!/^telegram:[1-9][0-9]*$/u.test(String(value.principal || ''))
    || (value.messageThreadId != null && (!Number.isSafeInteger(value.messageThreadId)
      || value.messageThreadId < 1))
    || !Number.isSafeInteger(value.archiveRowId) || value.archiveRowId < 1
    || !SHA256.test(String(value.originProofSha256 || '')))) return null;
  if (!required && [value.principal, value.messageThreadId, value.archiveRowId,
    value.originProofSha256].some((item) => item !== null)) return null;
  return freeze({
    ...start,
    principal: required ? String(value.principal) : null,
    messageThreadId: required ? value.messageThreadId : null,
    archiveRowId: required ? value.archiveRowId : null,
    originProofSha256: required ? String(value.originProofSha256) : null,
  });
}

function normalizedStartAuthority(value) {
  if (!exactKeys(value, [
    'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
    'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
    'sourceSha256', 'sourceGeneration',
  ])) return null;
  const result = {
    executionKey: text(value.executionKey, 300),
    executionSeq: Number(value.executionSeq),
    intentRecordSha256: String(value.intentRecordSha256 || ''),
    authoritySha256: String(value.authoritySha256 || ''),
    channel: text(value.channel, 40),
    conversationId: text(value.conversationId, 240),
    messageId: text(value.messageId, 160),
    sourceKind: text(value.sourceKind, 80),
    sourceRef: text(value.sourceRef, 300),
    sourceSha256: String(value.sourceSha256 || ''),
    sourceGeneration: Number(value.sourceGeneration),
  };
  return result.executionKey && Number.isSafeInteger(result.executionSeq)
    && result.executionSeq >= 1 && SHA256.test(result.intentRecordSha256)
    && SHA256.test(result.authoritySha256) && result.channel
    && result.conversationId && result.messageId && result.sourceKind && result.sourceRef
    && SHA256.test(result.sourceSha256) && Number.isSafeInteger(result.sourceGeneration)
    && result.sourceGeneration >= 1 ? result : null;
}

export function issueMonitoringLifecycleBinding({
  monitoring, episodeId, episodeKey, issuedEpisodeRevision, workId,
  originRowId, originKey, startAuthority, deliveryOrigin = null, generation = 1,
} = /** @type {any} */ ({})) {
  const contract = normalizeMonitoringContract(monitoring);
  const required = contract?.delivery.deliveryRequired === true;
  const normalizedEpisodeKey = text(episodeKey, 160);
  const normalizedOriginKey = originKey == null ? null : text(originKey, 500) || null;
  const normalizedAuthority = normalizedStartAuthority(startAuthority);
  const normalizedRouteOrigin = required
    ? normalizedDeliveryOrigin(deliveryOrigin, true) : null;
  const originAuthority = lifecycleOriginAuthority(
    normalizedAuthority, normalizedRouteOrigin, required,
  );
  const lifecycleOriginBindingSha256 = required
    ? originBindingSha256(normalizedRouteOrigin, true)
    : originBindingSha256(null, false);
  if (!contract || !Number.isSafeInteger(episodeId) || episodeId < 1
    || !normalizedEpisodeKey
    || !Number.isSafeInteger(issuedEpisodeRevision) || issuedEpisodeRevision < 0
    || !Number.isSafeInteger(workId) || workId < 1
    || !Number.isSafeInteger(generation) || generation < 1 || generation > 1_000
    || !Number.isSafeInteger(originRowId) || originRowId < 1
    || !normalizedOriginKey || !originAuthority
    || lifecycleOriginBindingSha256 !== contract.delivery.originBindingSha256
    || (required && (!normalizedRouteOrigin
      || normalizedAuthority.executionKey !== normalizedRouteOrigin.executionKey
      || normalizedAuthority.executionSeq !== normalizedRouteOrigin.executionSeq
      || normalizedAuthority.intentRecordSha256 !== normalizedRouteOrigin.intentRecordSha256
      || normalizedAuthority.authoritySha256 !== normalizedRouteOrigin.authoritySha256
      || normalizedAuthority.channel !== 'telegram'
      || normalizedAuthority.conversationId !== normalizedRouteOrigin.conversationId
      || normalizedAuthority.messageId !== normalizedRouteOrigin.messageId
      || normalizedAuthority.sourceRef !== normalizedRouteOrigin.sourceRef
      || normalizedAuthority.sourceSha256 !== normalizedRouteOrigin.sourceSha256
      || normalizedAuthority.sourceGeneration !== normalizedRouteOrigin.sourceGeneration))) return null;
  const body = lifecycleBody({
    monitoringId: contract.monitoringId,
    monitoringContractSha256: contract.contractSha256,
    deliverySha256: contract.delivery.deliverySha256,
    presentationSha256: contract.presentation.presentationSha256,
    episodeId, episodeKey: normalizedEpisodeKey, issuedEpisodeRevision,
    workId, originRowId, originKey: normalizedOriginKey,
    generation, originAuthority,
    originBindingSha256: lifecycleOriginBindingSha256,
  });
  const lifecycleSha256 = sha256(body);
  return freeze({ ...body, lifecycleSha256 });
}

export function normalizeMonitoringLifecycleBinding(value, monitoring, expected = null) {
  if (!exactKeys(value, [
    'contract', 'monitoringId', 'monitoringContractSha256',
    'episodeId', 'episodeKey', 'issuedEpisodeRevision',
    'workId', 'originRowId', 'originKey', 'generation', 'originAuthority',
    'originBindingSha256',
    'lifecycleSha256',
  ]) || value.contract !== 'MonitoringLifecycleBinding/v1' || !plainObject(expected)) return null;
  const required = monitoring?.delivery?.deliveryRequired === true;
  const originAuthority = normalizeLifecycleOriginAuthority(value.originAuthority, required);
  const deliveryOrigin = required && originAuthority ? {
    channel: originAuthority.channel,
    conversationId: originAuthority.conversationId,
    messageId: originAuthority.messageId,
    principal: originAuthority.principal,
    messageThreadId: originAuthority.messageThreadId,
    sourceRef: originAuthority.sourceRef,
    sourceSha256: originAuthority.sourceSha256,
    sourceGeneration: originAuthority.sourceGeneration,
    archiveRowId: originAuthority.archiveRowId,
    executionKey: originAuthority.executionKey,
    executionSeq: originAuthority.executionSeq,
    intentRecordSha256: originAuthority.intentRecordSha256,
    authoritySha256: originAuthority.authoritySha256,
    originProofSha256: originAuthority.originProofSha256,
  } : null;
  const issued = issueMonitoringLifecycleBinding({
    monitoring, episodeId: value.episodeId, episodeKey: value.episodeKey,
    issuedEpisodeRevision: value.issuedEpisodeRevision, workId: value.workId,
    originRowId: value.originRowId, originKey: value.originKey,
    startAuthority: originAuthority && Object.fromEntries([
      'executionKey', 'executionSeq', 'intentRecordSha256', 'authoritySha256',
      'channel', 'conversationId', 'messageId', 'sourceKind', 'sourceRef',
      'sourceSha256', 'sourceGeneration',
    ].map((key) => [key, originAuthority[key]])),
    deliveryOrigin,
    generation: value.generation,
  });
  if (!issued || issued.lifecycleSha256 !== value.lifecycleSha256
    || value.episodeId !== expected.episodeId
    || value.episodeKey !== expected.episodeKey
    || value.workId !== expected.workId
    || value.originRowId !== (expected.originRowId ?? null)
    || value.originKey !== (expected.originKey ?? null)
    || (Object.hasOwn(expected, 'minimumEpisodeRevision')
      && (!Number.isSafeInteger(expected.minimumEpisodeRevision)
        || expected.minimumEpisodeRevision < value.issuedEpisodeRevision))) return null;
  return issued;
}

function outcomeReceiptBody(value) {
  return {
    contract: 'MonitoringOutcomeReceipt/v1',
    executionKey: value.executionKey,
    intentRecordSha256: value.intentRecordSha256,
    outputSha256: value.outputSha256,
    completionRecordSha256: value.completionRecordSha256,
    finishedAt: value.finishedAt,
    outcomeSha256: value.outcomeSha256,
    factIds: value.factIds,
  };
}

export function issueMonitoringOutcomeReceipt({
  outcome, executionKey, intentRecordSha256, outputSha256,
  completionRecordSha256, finishedAt,
} = /** @type {any} */ ({})) {
  if (!plainObject(outcome)
    || Object.keys(outcome).sort().join(',') !== 'factIds,terminal,verdict'
    || !['notify', 'silent'].includes(outcome.verdict)
    || typeof outcome.terminal !== 'boolean'
    || (outcome.verdict === 'notify') !== outcome.terminal
    || !Array.isArray(outcome.factIds) || !outcome.factIds.length
    || outcome.factIds.length > 8
    || outcome.factIds.some((id) => !FACT_ID.test(String(id || '')))
    || new Set(outcome.factIds).size !== outcome.factIds.length
    || !text(executionKey, 300)
    || !SHA256.test(String(intentRecordSha256 || ''))
    || !SHA256.test(String(outputSha256 || ''))
    || !SHA256.test(String(completionRecordSha256 || ''))
    || !Number.isFinite(Date.parse(finishedAt || ''))) return null;
  const decision = {
    verdict: outcome.verdict, terminal: outcome.terminal,
    factIds: outcome.factIds.map(String),
  };
  const body = outcomeReceiptBody({
    executionKey: String(executionKey),
    intentRecordSha256: String(intentRecordSha256),
    outputSha256: String(outputSha256),
    completionRecordSha256: String(completionRecordSha256),
    finishedAt: new Date(finishedAt).toISOString(),
    outcomeSha256: sha256(decision), factIds: decision.factIds,
  });
  const outcomeReceiptSha256 = sha256(body);
  return freeze({ ...body, outcomeReceiptSha256 });
}

export function normalizeMonitoringOutcomeReceipt(value, outcome) {
  if (!exactKeys(value, [
    'contract', 'executionKey', 'intentRecordSha256', 'outputSha256',
    'completionRecordSha256', 'finishedAt', 'outcomeSha256', 'factIds',
    'outcomeReceiptSha256',
  ]) || value.contract !== 'MonitoringOutcomeReceipt/v1') return null;
  const issued = issueMonitoringOutcomeReceipt({
    outcome, executionKey: value.executionKey,
    intentRecordSha256: value.intentRecordSha256,
    outputSha256: value.outputSha256,
    completionRecordSha256: value.completionRecordSha256,
    finishedAt: value.finishedAt,
  });
  return issued?.outcomeReceiptSha256 === value.outcomeReceiptSha256
    && value.outcomeSha256 === issued.outcomeSha256
    && canonical(value.factIds) === canonical(issued.factIds) ? issued : null;
}

function pendingBody(value) {
  return {
    contract: 'MonitoringPendingDelivery/v2',
    monitoringContractSha256: value.monitoringContractSha256,
    lifecycleSha256: value.lifecycleSha256,
    lifecycleGeneration: value.lifecycleGeneration,
    outcomeRef: value.outcomeRef,
    factRefs: value.factRefs,
    factSetSha256: value.factSetSha256,
    rendererVersion: value.rendererVersion,
    textSha256: value.textSha256,
    text: value.text,
    episodeId: value.episodeId,
    workId: value.workId,
    dedupeKey: value.dedupeKey,
    preparedAt: value.preparedAt,
    expiresAt: value.expiresAt,
    attempts: value.attempts,
  };
}

const MONITORING_RENDERER_VERSION = 'monitoring-nl-v1';

function factReference(fact) {
  const normalized = normalizeMonitoringMeasurementFact(fact);
  if (!normalized) return null;
  return freeze({
    bindingSha256: normalized.bindingSha256,
    requirementId: normalized.requirementId,
    executionKey: normalized.executionKey,
    completionRecordSha256: normalized.completionRecordSha256,
    factId: normalized.factId,
  });
}

function normalizeFactReference(value) {
  if (!exactKeys(value, [
    'bindingSha256', 'requirementId', 'executionKey',
    'completionRecordSha256', 'factId',
  ]) || !SHA256.test(String(value.bindingSha256 || ''))
    || !REQUIREMENT_ID.test(String(value.requirementId || ''))
    || !text(value.executionKey, 300)
    || !SHA256.test(String(value.completionRecordSha256 || ''))
    || !FACT_ID.test(String(value.factId || ''))) return null;
  return freeze({
    bindingSha256: String(value.bindingSha256),
    requirementId: String(value.requirementId),
    executionKey: String(value.executionKey),
    completionRecordSha256: String(value.completionRecordSha256),
    factId: String(value.factId),
  });
}

function factReferenceDictionary(references) {
  return freeze({
    contract: 'MonitoringFactReferenceDictionary/v1',
    fields: [...FACT_REFERENCE_FIELDS],
    entries: references.map((reference) => (
      FACT_REFERENCE_FIELDS.map((field) => reference[field])
    )),
  });
}

function unpackFactReferenceDictionary(value) {
  if (!exactKeys(value, ['contract', 'fields', 'entries'])
    || value.contract !== 'MonitoringFactReferenceDictionary/v1'
    || !Array.isArray(value.fields)
    || canonical(value.fields) !== canonical(FACT_REFERENCE_FIELDS)
    || !Array.isArray(value.entries) || !value.entries.length || value.entries.length > 8) {
    return null;
  }
  const references = value.entries.map((entry) => (
    Array.isArray(entry) && entry.length === FACT_REFERENCE_FIELDS.length
      ? normalizeFactReference(Object.fromEntries(
        FACT_REFERENCE_FIELDS.map((field, index) => [field, entry[index]]),
      )) : null
  ));
  return references.some((reference) => !reference) ? null : references;
}

export function monitoringPendingFactReferences(pendingValue) {
  const references = unpackFactReferenceDictionary(pendingValue?.factRefs);
  return references ? freeze([...references]) : null;
}

function outcomeReference(receipt) {
  if (!receipt) return null;
  return freeze({
    executionKey: receipt.executionKey,
    completionRecordSha256: receipt.completionRecordSha256,
    outcomeReceiptSha256: receipt.outcomeReceiptSha256,
    outcomeSha256: receipt.outcomeSha256,
  });
}

function factsMatchMonitoringContract(contract, facts) {
  const bindings = monitoringContractReadBindings(contract);
  return Boolean(contract && bindings && Array.isArray(facts)
    && facts.length === bindings.length
    && facts.every((fact, index) => {
      const binding = bindings[index];
      return fact?.bindingSha256 === binding.bindingSha256
        && fact?.requirementId === binding.requirementId
        && fact?.entityId === binding.entityId
        && fact?.conditionId === binding.conditionId
        && fact?.operand === binding.operand
        && fact?.sourceField === binding.sourceField
        && fact?.valueType === binding.valueType
        && fact?.quantityKind === binding.quantityKind
        && fact?.unit === binding.expectedUnit;
    }));
}

export function issueMonitoringPendingDelivery({
  monitoring, lifecycle, lifecycleSha256 = null, outcome, outcomeReceipt,
  facts, text: renderedText,
  episodeId, workId, dedupeKey, preparedAt = new Date().toISOString(), attempts = 0,
} = /** @type {any} */ ({})) {
  const contract = normalizeMonitoringContract(monitoring);
  const normalizedLifecycle = normalizeMonitoringLifecycleBinding(lifecycle, contract, {
    episodeId, episodeKey: lifecycle?.episodeKey, workId,
    originRowId: lifecycle?.originRowId, originKey: lifecycle?.originKey,
  });
  const normalizedFacts = Array.isArray(facts)
    ? facts.map(normalizeMonitoringMeasurementFact) : [];
  const decision = normalizeMonitoringOutcomeDecision(outcome, normalizedFacts);
  const normalizedOutcomeReceipt = decision
    ? normalizeMonitoringOutcomeReceipt(outcomeReceipt, decision) : null;
  const outputText = text(renderedText, 4_000);
  const preparedMs = Date.parse(preparedAt);
  if (!contract || !normalizedLifecycle || !normalizedOutcomeReceipt
    || (lifecycleSha256 != null && lifecycleSha256 !== normalizedLifecycle.lifecycleSha256)
    || contract.delivery.deliveryRequired !== true
    || contract.delivery.route !== 'direct_telegram'
    || normalizedFacts.some((fact) => !fact)
    || !factsMatchMonitoringContract(contract, normalizedFacts) || !decision
    || decision.verdict !== 'notify' || !outputText
    || renderMonitoringTerminalText(normalizedFacts) !== outputText
    || !Number.isSafeInteger(episodeId) || episodeId < 1
    || !Number.isSafeInteger(workId) || workId < 1
    || !text(dedupeKey, 240) || !Number.isFinite(preparedMs)
    || !Number.isSafeInteger(attempts) || attempts < 0 || attempts >= 100) return null;
  const factReferenceItems = normalizedFacts.map(factReference);
  if (factReferenceItems.some((item) => !item)
    || new Set(factReferenceItems.map((item) => item.executionKey)).size
      !== factReferenceItems.length
    || new Set(factReferenceItems.map((item) => item.completionRecordSha256)).size
      !== factReferenceItems.length) {
    return null;
  }
  const bindings = monitoringContractReadBindings(contract);
  if (!bindings) return null;
  const expiresMs = Math.min(
    ...normalizedFacts.map((fact, index) => (
      Date.parse(fact.readAt) + bindings[index].maxAgeMs
    )),
    contract.endsAt == null ? Number.POSITIVE_INFINITY : Date.parse(contract.endsAt),
  );
  if (!Number.isFinite(expiresMs) || expiresMs < preparedMs
    || canonicalMonitoringText(outputText, 4_000) !== outputText) return null;
  const factRefs = factReferenceDictionary(factReferenceItems);
  const factSetSha256 = sha256(factRefs);
  const body = pendingBody({
    contract: 'MonitoringPendingDelivery/v2',
    monitoringContractSha256: contract.contractSha256,
    lifecycleSha256: normalizedLifecycle.lifecycleSha256,
    lifecycleGeneration: normalizedLifecycle.generation,
    outcomeRef: outcomeReference(normalizedOutcomeReceipt),
    factRefs, factSetSha256, rendererVersion: MONITORING_RENDERER_VERSION,
    textSha256: sha256(outputText),
    text: outputText, episodeId, workId,
    dedupeKey: text(dedupeKey, 240), preparedAt: new Date(preparedMs).toISOString(),
    expiresAt: new Date(expiresMs).toISOString(),
    attempts,
  });
  if (Buffer.byteLength(canonical(body), 'utf8') > 6_000) return null;
  const pendingSha256 = sha256(body);
  return freeze({ ...body, pendingSha256 });
}

function pendingReferenceAuthorityValid(value, contract, expected, factReferenceItems, outcomeRef) {
  return plainObject(expected) && Boolean(contract) && factReferenceItems.length > 0
    && new Set(factReferenceItems.map((item) => item.executionKey)).size
      === factReferenceItems.length
    && new Set(factReferenceItems.map((item) => item.completionRecordSha256)).size
      === factReferenceItems.length
    && exactKeys(outcomeRef, [
      'executionKey', 'completionRecordSha256', 'outcomeReceiptSha256', 'outcomeSha256',
    ])
    && Boolean(text(outcomeRef.executionKey, 300))
    && SHA256.test(String(outcomeRef.completionRecordSha256 || ''))
    && SHA256.test(String(outcomeRef.outcomeReceiptSha256 || ''))
    && SHA256.test(String(outcomeRef.outcomeSha256 || ''))
    && contract.delivery.deliveryRequired === true
    && contract.delivery.route === 'direct_telegram'
    && value.monitoringContractSha256 === contract.contractSha256
    && SHA256.test(String(value.lifecycleSha256 || ''))
    && value.lifecycleSha256 === expected.lifecycleSha256
    && Number.isSafeInteger(value.lifecycleGeneration) && value.lifecycleGeneration >= 1
    && (!Object.hasOwn(expected, 'lifecycleGeneration')
      || value.lifecycleGeneration === expected.lifecycleGeneration);
}

function pendingReferencePayloadValid(value, expected) {
  const preparedAt = Date.parse(value.preparedAt || '');
  const expiresAt = Date.parse(value.expiresAt || '');
  return value.factSetSha256 === sha256(value.factRefs)
    && value.textSha256 === sha256(String(value.text || ''))
    && value.rendererVersion === MONITORING_RENDERER_VERSION
    && canonicalMonitoringText(value.text, 4_000) === value.text
    && value.episodeId === expected.episodeId && value.workId === expected.workId
    && String(value.dedupeKey || '') === String(expected.dedupeKey || '')
    && Number.isSafeInteger(value.episodeId) && value.episodeId >= 1
    && Number.isSafeInteger(value.workId) && value.workId >= 1
    && Boolean(text(value.dedupeKey, 240)) && Number.isFinite(preparedAt)
    && new Date(preparedAt).toISOString() === value.preparedAt
    && Number.isFinite(expiresAt) && new Date(expiresAt).toISOString() === value.expiresAt
    && expiresAt >= preparedAt
    && Number.isSafeInteger(value.attempts) && value.attempts >= 0 && value.attempts < 100;
}

export function normalizeMonitoringPendingDeliveryReference(
  value, monitoring = null, expected = null,
) {
  if (!exactKeys(value, [
    'contract', 'monitoringContractSha256', 'lifecycleSha256',
    'lifecycleGeneration', 'outcomeRef', 'factRefs', 'factSetSha256',
    'rendererVersion', 'textSha256', 'text', 'episodeId', 'workId', 'dedupeKey',
    'preparedAt', 'expiresAt', 'attempts', 'pendingSha256',
  ]) || value.contract !== 'MonitoringPendingDelivery/v2') return null;
  const contract = normalizeMonitoringContract(monitoring);
  const factReferenceItems = unpackFactReferenceDictionary(value.factRefs);
  const outcomeRef = value.outcomeRef;
  if (!factReferenceItems
    || !pendingReferenceAuthorityValid(
      value, contract, expected, factReferenceItems, outcomeRef,
    )
    || !pendingReferencePayloadValid(value, expected)) return null;
  const body = pendingBody(value);
  if (sha256(body) !== value.pendingSha256) return null;
  return freeze({ ...body, factRefs: factReferenceDictionary(factReferenceItems),
    pendingSha256: value.pendingSha256 });
}

export function normalizeMonitoringPendingDelivery(value, monitoring = null, expected = null) {
  const reference = normalizeMonitoringPendingDeliveryReference(value, monitoring, expected);
  if (!reference || !plainObject(expected) || !Object.hasOwn(expected, 'now')) return null;
  const contract = normalizeMonitoringContract(monitoring);
  const facts = Array.isArray(expected.facts)
    ? expected.facts.map(normalizeMonitoringMeasurementFact) : [];
  const outcome = normalizeMonitoringOutcomeDecision(expected.outcome, facts);
  const outcomeReceipt = outcome
    ? normalizeMonitoringOutcomeReceipt(expected.outcomeReceipt, outcome) : null;
  const nowMs = Date.parse(expected.now);
  const exactFactReferenceItems = facts.map(factReference);
  const exactFactRefs = factReferenceDictionary(exactFactReferenceItems);
  const referenceFactItems = monitoringPendingFactReferences(reference);
  const bindings = monitoringContractReadBindings(contract);
  if (!contract || facts.some((fact) => !fact) || !factsMatchMonitoringContract(contract, facts)
    || !outcome || outcome.verdict !== 'notify' || outcome.terminal !== true
    || !outcomeReceipt || !Number.isFinite(nowMs)
    || new Date(nowMs).toISOString() !== expected.now
    || nowMs > Date.parse(reference.expiresAt)
    || (contract.endsAt != null && nowMs > Date.parse(contract.endsAt))
    || Date.parse(reference.preparedAt) > nowMs + 30_000
    || !bindings || !referenceFactItems || facts.some((fact, index) => {
      const readAt = Date.parse(fact.readAt);
      return readAt > nowMs + 30_000
        || nowMs - readAt > bindings[index].maxAgeMs;
    })
    || canonical(exactFactRefs) !== canonical(reference.factRefs)
    || reference.factSetSha256 !== sha256(exactFactRefs)
    || reference.outcomeRef.executionKey !== outcomeReceipt.executionKey
    || reference.outcomeRef.completionRecordSha256
      !== outcomeReceipt.completionRecordSha256
    || reference.outcomeRef.outcomeReceiptSha256 !== outcomeReceipt.outcomeReceiptSha256
    || reference.outcomeRef.outcomeSha256 !== outcomeReceipt.outcomeSha256
    || canonical(outcome.factIds) !== canonical(referenceFactItems.map((item) => item.factId))
    || renderMonitoringTerminalText(facts) !== reference.text) return null;
  return reference;
}

function monitoringDeliveryTimeBoundaries(pending, monitoring) {
  const expiresAt = pending?.expiresAt;
  const endsAt = monitoring?.endsAt;
  const expiresMs = typeof expiresAt === 'string' ? Date.parse(expiresAt) : Number.NaN;
  const endsMs = endsAt == null ? Number.POSITIVE_INFINITY
    : typeof endsAt === 'string' ? Date.parse(endsAt) : Number.NaN;
  if (!Number.isFinite(expiresMs) || new Date(expiresMs).toISOString() !== expiresAt
    || !(endsAt == null || (Number.isFinite(endsMs)
      && new Date(endsMs).toISOString() === endsAt))) return null;
  const horizonMs = Math.min(expiresMs, endsMs);
  return Object.freeze({
    horizonAt: new Date(horizonMs).toISOString(),
    latestScheduledWake: new Date(horizonMs - 120_000).toISOString(),
    latestWireStart: new Date(horizonMs - 90_000).toISOString(),
    wireNotAfter: new Date(horizonMs - 30_000).toISOString(),
    horizonMs,
  });
}

export function monitoringDeliveryTransportLimits(pending, monitoring) {
  const boundaries = monitoringDeliveryTimeBoundaries(pending, monitoring);
  return boundaries ? Object.freeze({
    horizonAt: boundaries.horizonAt,
    latestWireStart: boundaries.latestWireStart,
    wireNotAfter: boundaries.wireNotAfter,
  }) : null;
}

export function monitoringDeliveryRetrySchedule(pending, monitoring, {
  now, requestedBackoffMs, notBefore = null,
} = /** @type {any} */ ({})) {
  const boundaries = monitoringDeliveryTimeBoundaries(pending, monitoring);
  const nowMs = typeof now === 'string' ? Date.parse(now) : Number.NaN;
  const requestedMs = Number(requestedBackoffMs);
  const notBeforeMs = notBefore == null ? Number.NEGATIVE_INFINITY
    : typeof notBefore === 'string' ? Date.parse(notBefore) : Number.NaN;
  if (!boundaries || !Number.isFinite(nowMs) || new Date(nowMs).toISOString() !== now
    || !Number.isFinite(requestedMs) || requestedMs < 0
    || !(notBefore == null || (Number.isFinite(notBeforeMs)
      && new Date(notBeforeMs).toISOString() === notBefore))) return null;
  const latestWakeMs = Date.parse(boundaries.latestScheduledWake);
  const remainingMs = latestWakeMs - nowMs;
  if (remainingMs < 30_000) return null;
  const delayMs = Math.max(
    15_000,
    Math.min(Math.trunc(requestedMs), Math.floor(remainingMs / 2)),
  );
  const wakeMs = Math.max(nowMs + delayMs, notBeforeMs);
  if (!Number.isFinite(wakeMs) || wakeMs > latestWakeMs) return null;
  return Object.freeze({
    horizonAt: boundaries.horizonAt,
    latestScheduledWake: boundaries.latestScheduledWake,
    latestWireStart: boundaries.latestWireStart,
    wireNotAfter: boundaries.wireNotAfter,
    wakeAt: new Date(wakeMs).toISOString(),
  });
}

export function monitoringDeliveryWireCurrent(pending, monitoring, {
  now, boundary,
} = /** @type {any} */ ({})) {
  const boundaries = monitoringDeliveryTimeBoundaries(pending, monitoring);
  const nowMs = typeof now === 'string' ? Date.parse(now) : Number.NaN;
  if (!boundaries || !['start', 'child'].includes(boundary)
    || !Number.isFinite(nowMs) || new Date(nowMs).toISOString() !== now) return false;
  const limit = boundary === 'start'
    ? boundaries.latestWireStart : boundaries.wireNotAfter;
  return nowMs <= Date.parse(limit);
}
