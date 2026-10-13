import { createHash, randomBytes } from 'node:crypto';
import { executionLedgerSchemaManifest } from '../executionledger.js';
import {
  turnUnderstandingAuthorityRecord,
  turnUnderstandingAuthorityStoreStatus,
} from '../turnauthority.js';

export const WORK_COMPLETION_CONTRACT_VERSION = 3;

const SHA256 = /^[a-f0-9]{64}$/u;
const REQUIREMENT_ID = /^r(?:[1-9]|1[0-2])$/u;
const RECEIPT_ID = /^s[1-9][0-9]*$/u;
const CONTRACT_REF = /^work-completion-contract-v3:[a-f0-9]{64}$/u;
const CLAIM_REF = /^work-completion-claim-v3:[a-f0-9]{64}$/u;
const RETRY_TTL_MS = 6 * 60 * 60_000;
const RETRY_MAX_ATTEMPTS = 4;
const MUTATING_EFFECTS = new Set([
  'home', 'voice', 'external', 'workspace', 'persistent', 'runtime',
]);
const OFFICIAL_PROOF_FLOORS = new Set([
  'source_read', 'accepted_result', 'verified_effect', 'delivery_receipt',
  'root_receipt',
]);
// material_readback bestaat nog in het materiaalcontract. Het wordt uitsluitend
// als strengere readback-eis geïnterpreteerd; het verleent geen aparte authority.
const COMPATIBLE_PROOF_FLOORS = new Set([
  ...OFFICIAL_PROOF_FLOORS, 'material_readback',
]);
const OPERATION_TOOL = new Map([
  ['workspace.file.write\0write', 'schrijf_bestand'],
  ['workspace.file.read\0read', 'lees_bestand'],
  ['workspace.program.execute\0execute', 'voer_uit'],
  ['work.episode.manage\0afronden', 'werk_bijhouden'],
]);

function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const hashJson = (value) => sha256(canonicalJson(value));
const clone = (value) => JSON.parse(canonicalJson(value));
const plainObject = (value) => value !== null && typeof value === 'object'
  && !Array.isArray(value);
const validSha = (value) => typeof value === 'string' && SHA256.test(value);
const positiveInteger = (value) => Number.isSafeInteger(value) && value >= 1;
const nonnegativeInteger = (value) => Number.isSafeInteger(value) && value >= 0;
const validText = (value, maximum = 1_200) => typeof value === 'string'
  && value.length >= 1 && value.length <= maximum;

function exactKeys(value, keys) {
  return plainObject(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...keys].sort());
}

function exactStringList(value, {
  pattern = null, allowEmpty = true, maximum = 64,
} = {}) {
  if (!Array.isArray(value) || value.length > maximum
    || (!allowEmpty && value.length === 0)
    || value.some((item) => typeof item !== 'string' || (pattern && !pattern.test(item)))
    || new Set(value).size !== value.length) return null;
  return [...value];
}

function parseJson(value, label, { canonical = false } = {}) {
  let parsed;
  try { parsed = JSON.parse(String(value)); } catch {
    throw new Error(`invalid ${label} JSON`);
  }
  if (canonical && canonicalJson(parsed) !== String(value)) {
    throw new Error(`noncanonical ${label} JSON`);
  }
  return parsed;
}

function normalizeSql(value) {
  return String(value || '').replace(/\bmain\./gu, '')
    .replace(/;\s*$/u, '').replace(/\s+/gu, ' ').trim();
}

function sqliteNow(database) {
  const value = database.prepare(
    "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now') AS now",
  ).get()?.now;
  if (!Number.isFinite(Date.parse(String(value || '')))) {
    throw new Error('SQLite host clock is unavailable');
  }
  return value;
}

function validOperationReference(value) {
  return exactKeys(value, ['capabilityId', 'operationId', 'type'])
    && /^[a-z][a-z0-9_.:-]{0,119}$/u.test(value.capabilityId)
    && /^[a-z][a-z0-9_.:-]{0,119}$/u.test(value.operationId)
    && /^[a-z][a-z0-9_:-]{0,79}$/u.test(value.type);
}

function normalizedExecution(value) {
  if (!exactKeys(value, [
    'mode', 'materializable', 'target', 'proofOperations', 'recoveryOperations',
    'fileArguments',
  ]) || !['direct', 'iterative'].includes(value.mode)
    || !exactStringList(value.materializable, { maximum: 64 })
    || (value.target !== null && !plainObject(value.target))
    || !Array.isArray(value.proofOperations)
    || value.proofOperations.some((entry) => !validOperationReference(entry))
    || !Array.isArray(value.recoveryOperations)
    || value.recoveryOperations.some((entry) => !validOperationReference(entry))
    || !(value.fileArguments === null || Array.isArray(value.fileArguments)
      || plainObject(value.fileArguments))) return null;
  if (value.mode === 'direct' && (value.materializable.length
    || value.target !== null || value.proofOperations.length
    || value.recoveryOperations.length)) return null;
  if (value.mode === 'iterative' && (!plainObject(value.target)
    || !validText(value.target.field, 120)
    || value.target.boundary !== 'workspace'
    || value.target.sameDirectory !== true
    || !positiveInteger(value.target.artifactLimit)
    || value.target.artifactLimit > 12
    || !value.proofOperations.length)) return null;
  return clone(value);
}

const PLAN_KEYS = Object.freeze([
  'requirementId', 'capabilityId', 'operationId', 'toolName', 'args', 'effect',
  'idempotency', 'proofFloor', 'supportsDeferred', 'execution',
  'proofOperations', 'recoveryOperations', 'semanticBinding',
]);
const BINDING_KEYS = Object.freeze([
  'understandingSha256', 'requirementId', 'addressesRequirementIds',
  'basisReceiptIds', 'capabilityId', 'operationId', 'role', 'executionType',
]);

function normalizedSemanticBinding(value, plan, { production = false } = {}) {
  const execution = normalizedExecution(plan.execution);
  const addresses = exactStringList(value?.addressesRequirementIds, {
    pattern: REQUIREMENT_ID, allowEmpty: false, maximum: 12,
  });
  const basis = exactStringList(value?.basisReceiptIds, {
    pattern: RECEIPT_ID, maximum: 64,
  });
  const productionExecutionType = null;
  const compatibleExecutionType = value?.executionType === productionExecutionType
    || (!production && value?.executionType === execution?.mode);
  if (!execution || !exactKeys(value, BINDING_KEYS)
    || !validSha(value.understandingSha256)
    || value.requirementId !== plan.requirementId
    || !addresses || !addresses.includes(plan.requirementId)
    || !basis
    || value.capabilityId !== plan.capabilityId
    || value.operationId !== plan.operationId
    || !/^[a-z][a-z0-9_:-]{0,79}$/u.test(String(value.role || ''))
    || !compatibleExecutionType) return null;
  return clone(value);
}

function normalizedPlan(value, options = {}) {
  if (!exactKeys(value, PLAN_KEYS)
    || !REQUIREMENT_ID.test(String(value.requirementId || ''))
    || !/^[a-z][a-z0-9_.:-]{0,119}$/u.test(String(value.capabilityId || ''))
    || !/^[a-z][a-z0-9_.:-]{0,119}$/u.test(String(value.operationId || ''))
    || !validText(value.toolName, 180) || !plainObject(value.args)
    || !validText(value.effect, 80) || !validText(value.idempotency, 80)
    || !exactStringList(value.proofFloor, { maximum: 64 })
    || value.proofFloor.some((proof) => !COMPATIBLE_PROOF_FLOORS.has(proof))
    || typeof value.supportsDeferred !== 'boolean') return null;
  const execution = normalizedExecution(value.execution);
  if (!execution || canonicalJson(value.proofOperations)
      !== canonicalJson(execution.proofOperations)
    || canonicalJson(value.recoveryOperations)
      !== canonicalJson(execution.recoveryOperations)
    || !normalizedSemanticBinding(value.semanticBinding, value, options)) return null;
  return Object.fromEntries(PLAN_KEYS.map((key) => [key, clone(value[key])]));
}

function normalizedDecision(value, plans) {
  if (!exactKeys(value, ['kind', 'requirementIds', 'replyDraft'])
    || !['act', 'inspect'].includes(value.kind)
    || value.replyDraft !== null
    || !exactStringList(value.requirementIds, {
      pattern: REQUIREMENT_ID, allowEmpty: false, maximum: 12,
    })
    || canonicalJson(value.requirementIds)
      !== canonicalJson(plans.map(({ requirementId }) => requirementId))) return null;
  return clone(value);
}

export function createWorkCompletionPlanSeal(value) {
  if (!exactKeys(value, ['decision', 'plans'])
    || !Array.isArray(value.plans) || !value.plans.length || value.plans.length > 12) {
    throw new TypeError('production plan seal requires full decision and plans');
  }
  const plans = value.plans.map((plan) => {
    const normalized = normalizedPlan(plan);
    if (!normalized) throw new TypeError('invalid governed production plan/semanticBinding');
    return normalized;
  });
  if (new Set(plans.map(({ requirementId }) => requirementId)).size !== plans.length) {
    throw new TypeError('duplicate plan requirementId');
  }
  const decision = normalizedDecision(value.decision, plans);
  if (!decision) throw new TypeError('invalid full production decision');
  const projection = { decision, plans };
  return Object.freeze({
    contract: 'AgentInterpretationRetryPlanProjection/v1',
    ...projection,
    sha256: hashJson(projection),
  });
}

function normalizePlanSeal(value, { production = false } = {}) {
  if (!exactKeys(value, ['contract', 'decision', 'plans', 'sha256'])
    || value.contract !== 'AgentInterpretationRetryPlanProjection/v1'
    || !validSha(value.sha256) || !Array.isArray(value.plans)
    || !value.plans.length || value.plans.length > 12) return null;
  const plans = value.plans.map((plan) => normalizedPlan(plan, { production }));
  if (plans.some((plan) => !plan)
    || new Set(plans.map(({ requirementId }) => requirementId)).size !== plans.length) {
    return null;
  }
  const decision = normalizedDecision(value.decision, plans);
  if (!decision) return null;
  const projection = { decision, plans };
  const normalized = {
    contract: value.contract, ...projection, sha256: hashJson(projection),
  };
  return canonicalJson(normalized) === canonicalJson(value) ? normalized : null;
}

function validTurnUnderstandingRecord(value) {
  const keys = [
    'contract', 'sourceSha256', 'sourceKind', 'sourceTextSha256', 'dialogueAct',
    'goal', 'conditions', 'temporal', 'durability', 'requestedEffects',
    'deniedEffects', 'actions', 'firstDecision', 'decisionProjection',
    'monitoring', 'sha256',
  ];
  if (!exactKeys(value, keys) || value.contract !== 'TurnUnderstandingRecord/v1'
    || !validSha(value.sha256) || !validSha(value.sourceSha256)
    || !validSha(value.sourceTextSha256) || value.sourceKind !== 'model'
    || !plainObject(value.goal) || !Array.isArray(value.conditions)
    || !Array.isArray(value.actions) || !plainObject(value.temporal)
    || !plainObject(value.firstDecision) || !plainObject(value.decisionProjection)
    || !Array.isArray(value.requestedEffects) || !Array.isArray(value.deniedEffects)) {
    return false;
  }
  if (!exactKeys(value.goal, ['statement', 'outcome', 'beneficiary'])
    || !exactKeys(value.temporal, ['mode', 'notBefore', 'dueBy', 'condition'])
    || !exactKeys(value.firstDecision, [
      'kind', 'requirementIds', 'replyDraftSha256',
    ]) || !(value.firstDecision.replyDraftSha256 === null
      || validSha(value.firstDecision.replyDraftSha256))
    || !exactStringList(value.firstDecision.requirementIds, {
      pattern: REQUIREMENT_ID, allowEmpty: false, maximum: 12,
    })) return false;
  const actionKeys = [
    'requirementId', 'capabilityId', 'operationId', 'polarity', 'effect',
    'authorization', 'sourceSpans', 'boundaryReasons',
  ];
  if (value.actions.some((action) => !exactKeys(action, actionKeys)
    || !REQUIREMENT_ID.test(String(action.requirementId || ''))
    || !validText(action.capabilityId, 120) || !validText(action.operationId, 120)
    || action.polarity !== 'request' || action.authorization !== 'allowed'
    || !validText(action.effect, 80) || !Array.isArray(action.sourceSpans)
    || !Array.isArray(action.boundaryReasons))) return false;
  if (new Set(value.actions.map(({ requirementId }) => requirementId)).size
      !== value.actions.length) return false;
  const conditionKeys = [
    'statement', 'role', 'state', 'requirementIds', 'afterRequirementIds',
  ];
  if (value.conditions.some((condition) => !exactKeys(condition, conditionKeys)
    || !exactStringList(condition.requirementIds, { pattern: REQUIREMENT_ID })
    || !exactStringList(condition.afterRequirementIds, { pattern: REQUIREMENT_ID })
    || !['action', 'completion', 'guard', 'recovery'].includes(condition.role)
    || !['required', 'met', 'unmet', 'unknown'].includes(condition.state))) return false;
  const { sha256: claimed, ...body } = value;
  return claimed === hashJson(body);
}

function productionProjection(record, seal, work) {
  if (!validTurnUnderstandingRecord(record)) return null;
  const normalizedSeal = normalizePlanSeal(seal, { production: true });
  if (!normalizedSeal
    || record.firstDecision.kind !== normalizedSeal.decision.kind
    || record.firstDecision.replyDraftSha256 !== null
    || canonicalJson(record.firstDecision.requirementIds)
      !== canonicalJson(normalizedSeal.decision.requirementIds)) return null;
  const selected = new Set(record.firstDecision.requirementIds);
  const actions = new Map(record.actions.map((action) => [action.requirementId, action]));
  if (selected.size !== normalizedSeal.plans.length
    || [...selected].some((id) => !actions.has(id))) return null;
  for (const plan of normalizedSeal.plans) {
    const action = actions.get(plan.requirementId);
    if (!action || action.capabilityId !== plan.capabilityId
      || action.operationId !== plan.operationId || action.effect !== plan.effect
      || plan.semanticBinding.understandingSha256 !== record.sourceSha256
      || plan.semanticBinding.role !== 'primary'
      || plan.semanticBinding.executionType !== null
      || plan.semanticBinding.addressesRequirementIds.some((id) => !selected.has(id))) {
      return null;
    }
  }
  const settlementPlans = normalizedSeal.plans.filter((plan) => (
    plan.capabilityId === 'work.episode.manage'
      || plan.toolName === 'werk_bijhouden'
  ));
  const settlement = settlementPlans.length === 1 ? settlementPlans[0] : null;
  if (!settlement || settlement !== normalizedSeal.plans.at(-1)
    || settlement.capabilityId !== 'work.episode.manage'
    || settlement.operationId !== 'afronden'
    || settlement.toolName !== 'werk_bijhouden'
    || settlement.effect !== 'persistent' || settlement.idempotency !== 'cas'
    || settlement.execution.mode !== 'direct'
    || settlement.args.bewerking !== 'afronden'
    || settlement.args.id !== work.id
    || settlement.args.verwachte_revisie !== work.revision
    || !validText(settlement.args.resultaat, 2_000)) return null;
  const proofPlans = normalizedSeal.plans.slice(0, -1);
  if (!proofPlans.length || proofPlans.some((plan) => (
    plan.toolName === 'werk_bijhouden'
      || (plan.effect !== 'read' && !MUTATING_EFFECTS.has(plan.effect))
  ))) return null;
  const active = record.conditions.filter((condition) => (
    ['action', 'completion'].includes(condition.role)
      && ['required', 'unmet'].includes(condition.state)
  ));
  if (active.some((condition) => [...condition.requirementIds,
    ...condition.afterRequirementIds].some((id) => !selected.has(id)))) return null;
  return { seal: normalizedSeal, proofPlans, settlementPlan: settlement };
}

const DEPENDENCY_TABLES = Object.freeze([
  'timeline', 'messages', 'turn_tool_executions', 'eva_work_items',
  'eva_goal_episodes', 'eva_episode_events', 'eva_episode_origins',
  'eva_interpretation_retry_handoffs', 'eva_delivery_receipts',
]);

const DEPENDENCY_COLUMNS = Object.freeze({
  timeline: [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['kind', 'TEXT', 1, 0],
    ['content', 'TEXT', 1, 0], ['meta', 'TEXT', 0, 0], ['outcome_key', 'TEXT', 0, 0],
  ],
  messages: [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['channel', 'TEXT', 1, 0],
    ['conversation_id', 'TEXT', 0, 0], ['sender', 'TEXT', 1, 0],
    ['role', 'TEXT', 1, 0], ['content', 'TEXT', 1, 0],
  ],
  eva_work_items: [
    ['id', 'INTEGER', 0, 1], ['created_ts', 'TEXT', 1, 0],
    ['updated_ts', 'TEXT', 1, 0], ['source', 'TEXT', 1, 0], ['owner', 'TEXT', 0, 0],
    ['title', 'TEXT', 1, 0], ['goal', 'TEXT', 1, 0], ['next_step', 'TEXT', 0, 0],
    ['status', 'TEXT', 1, 0], ['evidence', 'TEXT', 0, 0],
    ['last_action', 'TEXT', 0, 0], ['result', 'TEXT', 0, 0],
    ['visible_to_family', 'INTEGER', 1, 0], ['definition_of_done', 'TEXT', 0, 0],
    ['current_step', 'TEXT', 0, 0], ['state_json', 'TEXT', 0, 0],
    ['wait_json', 'TEXT', 0, 0], ['priority', 'INTEGER', 1, 0],
    ['next_wake_ts', 'TEXT', 0, 0], ['revision', 'INTEGER', 1, 0],
    ['lease_owner', 'TEXT', 0, 0], ['lease_until', 'TEXT', 0, 0],
    ['attempt_count', 'INTEGER', 1, 0], ['last_error', 'TEXT', 0, 0],
    ['interrupted_ts', 'TEXT', 0, 0],
  ],
  eva_goal_episodes: [
    ['id', 'INTEGER', 0, 1], ['episode_key', 'TEXT', 1, 0], ['goal_id', 'INTEGER', 1, 0],
    ['project_id', 'INTEGER', 1, 0], ['work_item_id', 'INTEGER', 0, 0],
    ['owner_actor', 'TEXT', 1, 0], ['origin_kind', 'TEXT', 1, 0],
    ['origin_channel', 'TEXT', 0, 0], ['origin_conversation_id', 'TEXT', 0, 0],
    ['origin_message_id', 'TEXT', 0, 0], ['requester', 'TEXT', 0, 0],
    ['requested_outcome', 'TEXT', 1, 0], ['delivery_policy', 'TEXT', 1, 0],
    ['delivery_target', 'TEXT', 0, 0], ['state', 'TEXT', 1, 0],
    ['next_action', 'TEXT', 0, 0], ['checkpoint', 'TEXT', 0, 0],
    ['proof_summary', 'TEXT', 0, 0], ['wake_ts', 'TEXT', 0, 0],
    ['lease_owner', 'TEXT', 0, 0], ['lease_until', 'TEXT', 0, 0],
    ['created_ts', 'TEXT', 1, 0], ['updated_ts', 'TEXT', 1, 0],
    ['closed_ts', 'TEXT', 0, 0], ['revision', 'INTEGER', 1, 0],
  ],
  eva_episode_events: [
    ['id', 'INTEGER', 0, 1], ['episode_id', 'INTEGER', 1, 0], ['ts', 'TEXT', 1, 0],
    ['kind', 'TEXT', 1, 0], ['session_ref', 'TEXT', 0, 0],
    ['transition_key', 'TEXT', 0, 0], ['payload_json', 'TEXT', 1, 0],
  ],
  eva_episode_origins: [
    ['id', 'INTEGER', 0, 1], ['episode_id', 'INTEGER', 1, 0],
    ['origin_key', 'TEXT', 1, 0], ['origin_kind', 'TEXT', 1, 0],
    ['origin_channel', 'TEXT', 0, 0], ['origin_conversation_id', 'TEXT', 0, 0],
    ['origin_message_id', 'TEXT', 0, 0], ['requester', 'TEXT', 0, 0],
    ['attached_ts', 'TEXT', 1, 0],
  ],
  eva_interpretation_retry_handoffs: [
    ['handoff_key', 'TEXT', 1, 1], ['contract', 'TEXT', 1, 0],
    ['origin_channel', 'TEXT', 1, 0], ['origin_conversation_id', 'TEXT', 1, 0],
    ['origin_message_id', 'TEXT', 1, 0], ['raw_original_text_sha256', 'TEXT', 1, 0],
    ['original_text_sha256', 'TEXT', 1, 0], ['requester_sha256', 'TEXT', 1, 0],
    ['supersession_scope_sha256', 'TEXT', 1, 0],
    ['supersession_token_sha256', 'TEXT', 1, 0], ['work_item_id', 'INTEGER', 1, 0],
    ['episode_id', 'INTEGER', 1, 0], ['binding_sha256', 'TEXT', 1, 0],
    ['created_ts', 'TEXT', 1, 0],
  ],
  eva_delivery_receipts: [
    ['id', 'INTEGER', 0, 1], ['episode_id', 'INTEGER', 1, 0],
    ['origin_conversation_id', 'TEXT', 0, 0], ['origin_message_id', 'TEXT', 0, 0],
    ['delivered_conversation_id', 'TEXT', 0, 0], ['delivered_channel', 'TEXT', 1, 0],
    ['delivery_target', 'TEXT', 0, 0], ['result_text', 'TEXT', 1, 0],
    ['proof_summary', 'TEXT', 0, 0], ['idempotency_key', 'TEXT', 1, 0],
    ['delivered_ts', 'TEXT', 1, 0], ['transport_json', 'TEXT', 1, 0],
  ],
});

const DEPENDENCY_TABLE_SQL = Object.freeze({
  timeline: `CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT,
    outcome_key TEXT
  )`,
  messages: `CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    channel TEXT NOT NULL,
    conversation_id TEXT,
    sender TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
  )`,
  eva_work_items: `CREATE TABLE eva_work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    source TEXT NOT NULL,
    owner TEXT,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    next_step TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    evidence TEXT,
    last_action TEXT,
    result TEXT,
    visible_to_family INTEGER NOT NULL DEFAULT 1,
    definition_of_done TEXT,
    current_step TEXT,
    state_json TEXT,
    wait_json TEXT,
    priority INTEGER NOT NULL DEFAULT 50,
    next_wake_ts TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    interrupted_ts TEXT
  )`,
  eva_goal_episodes: `CREATE TABLE eva_goal_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_key TEXT NOT NULL UNIQUE,
    goal_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    work_item_id INTEGER,
    owner_actor TEXT NOT NULL DEFAULT 'eva',
    origin_kind TEXT NOT NULL,
    origin_channel TEXT,
    origin_conversation_id TEXT,
    origin_message_id TEXT,
    requester TEXT,
    requested_outcome TEXT NOT NULL,
    delivery_policy TEXT NOT NULL DEFAULT 'required',
    delivery_target TEXT,
    state TEXT NOT NULL DEFAULT 'committed',
    next_action TEXT,
    checkpoint TEXT,
    proof_summary TEXT,
    wake_ts TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    closed_ts TEXT,
    revision INTEGER NOT NULL DEFAULT 0
  )`,
  eva_episode_events: `CREATE TABLE eva_episode_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    session_ref TEXT,
    transition_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
  )`,
  eva_episode_origins: `CREATE TABLE eva_episode_origins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL,
    origin_key TEXT NOT NULL UNIQUE,
    origin_kind TEXT NOT NULL,
    origin_channel TEXT,
    origin_conversation_id TEXT,
    origin_message_id TEXT,
    requester TEXT,
    attached_ts TEXT NOT NULL
  )`,
  eva_interpretation_retry_handoffs: `CREATE TABLE eva_interpretation_retry_handoffs (
    handoff_key TEXT NOT NULL PRIMARY KEY,
    contract TEXT NOT NULL,
    origin_channel TEXT NOT NULL,
    origin_conversation_id TEXT NOT NULL,
    origin_message_id TEXT NOT NULL,
    raw_original_text_sha256 TEXT NOT NULL,
    original_text_sha256 TEXT NOT NULL,
    requester_sha256 TEXT NOT NULL,
    supersession_scope_sha256 TEXT NOT NULL,
    supersession_token_sha256 TEXT NOT NULL,
    work_item_id INTEGER NOT NULL UNIQUE,
    episode_id INTEGER NOT NULL UNIQUE,
    binding_sha256 TEXT NOT NULL,
    created_ts TEXT NOT NULL,
    UNIQUE(contract, origin_channel, origin_conversation_id, origin_message_id)
  )`,
  eva_delivery_receipts: `CREATE TABLE eva_delivery_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL,
    origin_conversation_id TEXT,
    origin_message_id TEXT,
    delivered_conversation_id TEXT,
    delivered_channel TEXT NOT NULL,
    delivery_target TEXT,
    result_text TEXT NOT NULL,
    proof_summary TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    delivered_ts TEXT NOT NULL,
    transport_json TEXT NOT NULL DEFAULT '{}'
  )`,
});

const DEPENDENCY_INDEXES = Object.freeze({
  timeline: {
    idx_timeline_ts: [0, 0, 'c', ['ts'],
      'CREATE INDEX idx_timeline_ts ON timeline(ts)'],
    idx_timeline_kind: [0, 0, 'c', ['kind'],
      'CREATE INDEX idx_timeline_kind ON timeline(kind)'],
    idx_timeline_understanding_conversation: [0, 1, 'c', ['kind', null, 'id'],
      "CREATE INDEX idx_timeline_understanding_conversation ON timeline(kind, json_extract(meta, '$.conversationId'), id) WHERE kind='turn_understanding'"],
    idx_timeline_contribution_outcome_key: [1, 1, 'c', ['outcome_key'],
      "CREATE UNIQUE INDEX idx_timeline_contribution_outcome_key ON timeline(outcome_key) WHERE kind='contribution' AND outcome_key IS NOT NULL"],
  },
  messages: {
    idx_messages_ts: [0, 0, 'c', ['ts'],
      'CREATE INDEX idx_messages_ts ON messages(ts)'],
    idx_messages_conversation: [0, 0, 'c', ['conversation_id', 'id'],
      'CREATE INDEX idx_messages_conversation ON messages(conversation_id, id)'],
  },
  eva_work_items: {
    idx_eva_work_items_status: [0, 0, 'c', ['status', 'updated_ts'],
      'CREATE INDEX idx_eva_work_items_status ON eva_work_items(status, updated_ts)'],
    idx_eva_work_wake: [0, 0, 'c', ['status', 'next_wake_ts', 'priority'],
      'CREATE INDEX idx_eva_work_wake ON eva_work_items(status, next_wake_ts, priority)'],
  },
  eva_goal_episodes: {
    idx_eva_goal_episodes_work: [0, 0, 'c', ['work_item_id', 'state'],
      'CREATE INDEX idx_eva_goal_episodes_work ON eva_goal_episodes(work_item_id, state)'],
    idx_eva_goal_episode_one_owner: [1, 1, 'c', ['work_item_id'],
      'CREATE UNIQUE INDEX idx_eva_goal_episode_one_owner ON eva_goal_episodes(work_item_id) WHERE work_item_id IS NOT NULL'],
    idx_eva_goal_episodes_delivery: [0, 0, 'c', ['delivery_policy', 'state', 'updated_ts'],
      'CREATE INDEX idx_eva_goal_episodes_delivery ON eva_goal_episodes(delivery_policy, state, updated_ts)'],
    sqlite_autoindex_eva_goal_episodes_1: [1, 0, 'u', ['episode_key'], ''],
  },
  eva_episode_events: {
    idx_eva_episode_events_episode: [0, 0, 'c', ['episode_id', 'id'],
      'CREATE INDEX idx_eva_episode_events_episode ON eva_episode_events(episode_id, id)'],
    idx_eva_episode_transition_key: [1, 1, 'c', ['episode_id', 'transition_key'],
      'CREATE UNIQUE INDEX idx_eva_episode_transition_key ON eva_episode_events(episode_id, transition_key) WHERE transition_key IS NOT NULL'],
  },
  eva_episode_origins: {
    idx_eva_episode_origins_episode: [0, 0, 'c', ['episode_id', 'attached_ts'],
      'CREATE INDEX idx_eva_episode_origins_episode ON eva_episode_origins(episode_id, attached_ts)'],
    idx_eva_episode_origins_conversation: [0, 0, 'c', ['origin_conversation_id', 'episode_id'],
      'CREATE INDEX idx_eva_episode_origins_conversation ON eva_episode_origins(origin_conversation_id, episode_id)'],
    sqlite_autoindex_eva_episode_origins_1: [1, 0, 'u', ['origin_key'], ''],
  },
  eva_interpretation_retry_handoffs: {
    idx_eva_interpretation_retry_origin: [0, 0, 'c', [
      'origin_channel', 'origin_conversation_id', 'origin_message_id',
    ], 'CREATE INDEX idx_eva_interpretation_retry_origin ON eva_interpretation_retry_handoffs( origin_channel, origin_conversation_id, origin_message_id )'],
    sqlite_autoindex_eva_interpretation_retry_handoffs_1: [1, 0, 'pk', ['handoff_key'], ''],
    sqlite_autoindex_eva_interpretation_retry_handoffs_2: [1, 0, 'u', ['work_item_id'], ''],
    sqlite_autoindex_eva_interpretation_retry_handoffs_3: [1, 0, 'u', ['episode_id'], ''],
    sqlite_autoindex_eva_interpretation_retry_handoffs_4: [1, 0, 'u', [
      'contract', 'origin_channel', 'origin_conversation_id', 'origin_message_id',
    ], ''],
  },
  eva_delivery_receipts: {
    sqlite_autoindex_eva_delivery_receipts_1: [1, 0, 'u', ['idempotency_key'], ''],
  },
});

const HANDOFF_TRIGGERS = Object.freeze({
  trg_interpretation_retry_handoff_insert_guard: `CREATE TRIGGER
    trg_interpretation_retry_handoff_insert_guard
    BEFORE INSERT ON eva_interpretation_retry_handoffs
    BEGIN
      SELECT CASE WHEN EXISTS (
        SELECT 1 FROM eva_interpretation_retry_handoffs AS prior
        WHERE prior.handoff_key = NEW.handoff_key
          OR prior.work_item_id = NEW.work_item_id
          OR prior.episode_id = NEW.episode_id
          OR (prior.contract = NEW.contract
            AND prior.origin_channel = NEW.origin_channel
            AND prior.origin_conversation_id = NEW.origin_conversation_id
            AND prior.origin_message_id = NEW.origin_message_id)
      ) THEN RAISE(ABORT, 'ChatInterpretationRetry/v1-registryidentiteit is onveranderlijk') END;
      SELECT CASE WHEN NEW.contract IS NULL
        OR NEW.contract <> 'ChatInterpretationRetry/v1'
        OR NEW.handoff_key IS NULL
        OR length(NEW.handoff_key) <> 64
        OR NEW.handoff_key GLOB '*[^0-9a-f]*'
        OR NEW.origin_channel IS NULL
        OR NEW.origin_channel NOT IN ('telegram', 'voice', 'web')
        OR NEW.origin_conversation_id IS NULL
        OR length(NEW.origin_conversation_id) < 1
        OR length(NEW.origin_conversation_id) > 240
        OR NEW.origin_message_id IS NULL
        OR length(NEW.origin_message_id) < 1
        OR length(NEW.origin_message_id) > 160
        OR NEW.raw_original_text_sha256 IS NULL
        OR length(NEW.raw_original_text_sha256) <> 64
        OR NEW.raw_original_text_sha256 GLOB '*[^0-9a-f]*'
        OR NEW.original_text_sha256 IS NULL
        OR length(NEW.original_text_sha256) <> 64
        OR NEW.original_text_sha256 GLOB '*[^0-9a-f]*'
        OR NEW.requester_sha256 IS NULL
        OR length(NEW.requester_sha256) <> 64
        OR NEW.requester_sha256 GLOB '*[^0-9a-f]*'
        OR NEW.supersession_scope_sha256 IS NULL
        OR length(NEW.supersession_scope_sha256) <> 64
        OR NEW.supersession_scope_sha256 GLOB '*[^0-9a-f]*'
        OR NEW.supersession_token_sha256 IS NULL
        OR length(NEW.supersession_token_sha256) <> 64
        OR NEW.supersession_token_sha256 GLOB '*[^0-9a-f]*'
        OR typeof(NEW.work_item_id) <> 'integer'
        OR NEW.work_item_id < 1
        OR typeof(NEW.episode_id) <> 'integer'
        OR NEW.episode_id < 1
        OR NEW.binding_sha256 IS NULL
        OR length(NEW.binding_sha256) <> 64
        OR NEW.binding_sha256 GLOB '*[^0-9a-f]*'
        OR NEW.created_ts IS NULL
        OR length(NEW.created_ts) < 20
        OR length(NEW.created_ts) > 40
      THEN RAISE(ABORT, 'ongeldige ChatInterpretationRetry/v1-registryrij') END;
    END`,
  trg_interpretation_retry_handoff_immutable_update: `CREATE TRIGGER
    trg_interpretation_retry_handoff_immutable_update
    BEFORE UPDATE ON eva_interpretation_retry_handoffs
    BEGIN
      SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
    END`,
  trg_interpretation_retry_handoff_immutable_delete: `CREATE TRIGGER
    trg_interpretation_retry_handoff_immutable_delete
    BEFORE DELETE ON eva_interpretation_retry_handoffs
    BEGIN
      SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
    END`,
});

function schemaVersions(database) {
  return {
    main: Number(database.pragma('main.schema_version', { simple: true })),
    temp: Number(database.pragma('temp.schema_version', { simple: true })),
  };
}

function dependencyManifest(database) {
  const turnAuthority = turnUnderstandingAuthorityStoreStatus(database);
  if (!turnAuthority.available) {
    throw new Error(`turn understanding authority unavailable: ${
      'reason' in turnAuthority ? turnAuthority.reason : 'unavailable'
    }`);
  }
  const manifest = [];
  for (const table of DEPENDENCY_TABLES) {
    if (table === 'turn_tool_executions') {
      manifest.push({ table, executionLedger: executionLedgerSchemaManifest(database) });
      continue;
    }
    const master = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(table);
    if (!master || normalizeSql(master.sql) !== normalizeSql(DEPENDENCY_TABLE_SQL[table])) {
      throw new Error(`production table manifest mismatch for ${table}`);
    }
    const columns = database.pragma(`main.table_info('${table}')`).map((column) => ({
      cid: Number(column.cid), name: column.name, type: String(column.type).toUpperCase(),
      notnull: Number(column.notnull), dflt_value: column.dflt_value, pk: Number(column.pk),
    }));
    const shape = columns.map(({ name, type, notnull, pk }) => [name, type, notnull, pk]);
    if (canonicalJson(shape) !== canonicalJson(DEPENDENCY_COLUMNS[table])) {
      throw new Error(`production schema manifest mismatch for ${table}`);
    }
    const indexes = database.pragma(`main.index_list('${table}')`).map((index) => {
      const xinfo = database.pragma(`main.index_xinfo('${index.name}')`);
      const keyed = xinfo.filter((entry) => Number(entry.key) === 1);
      const auxiliary = xinfo.filter((entry) => Number(entry.key) === 0);
      const expected = DEPENDENCY_INDEXES[table]?.[index.name];
      const sql = normalizeSql(database.prepare(`SELECT sql FROM main.sqlite_master
        WHERE type='index' AND name=?`).get(index.name)?.sql || '');
      if (!expected || Number(index.unique) !== expected[0]
        || Number(index.partial) !== expected[1] || index.origin !== expected[2]
        || canonicalJson(keyed.map(({ name }) => name)) !== canonicalJson(expected[3])
        || keyed.some((entry, position) => Number(entry.seqno) !== position
          || Number(entry.desc) !== 0 || entry.coll !== 'BINARY')
        || auxiliary.length !== 1 || Number(auxiliary[0].cid) !== -1
        || auxiliary[0].name !== null || normalizeSql(expected[4]) !== sql) {
        throw new Error(`production index manifest mismatch for ${table}.${index.name}`);
      }
      return { name: index.name, unique: Number(index.unique), partial: Number(index.partial),
        origin: index.origin, sql };
    });
    if (canonicalJson(indexes.map(({ name }) => name).sort())
      !== canonicalJson(Object.keys(DEPENDENCY_INDEXES[table]).sort())) {
      throw new Error(`production schema manifest mismatch for ${table} indexes`);
    }
    manifest.push({ table, sql: normalizeSql(master.sql), columns,
      indexes: indexes.sort((left, right) => left.name.localeCompare(right.name)) });
  }
  const triggers = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='eva_interpretation_retry_handoffs'
    ORDER BY name`).all();
  if (canonicalJson(triggers.map(({ name }) => name))
    !== canonicalJson(Object.keys(HANDOFF_TRIGGERS).sort())) {
    throw new Error('production handoff trigger manifest mismatch');
  }
  for (const trigger of triggers) {
    if (normalizeSql(trigger.sql) !== normalizeSql(HANDOFF_TRIGGERS[trigger.name])) {
      throw new Error(`production handoff trigger mismatch: ${trigger.name}`);
    }
  }
  return {
    tables: manifest,
    turnUnderstandingAuthority: turnAuthority,
    handoffTriggers: triggers.map((trigger) => ({
      name: trigger.name, sql: normalizeSql(trigger.sql),
    })),
  };
}

export function inspectWorkCompletionHostSchema(database) {
  if (!database || typeof database.prepare !== 'function') {
    throw new TypeError('host schema inspection requires synchronous SQLite');
  }
  const manifest = dependencyManifest(database);
  const body = {
    contract: 'WorkCompletionHostSchema/v3',
    dependencies: DEPENDENCY_TABLES,
    manifest,
  };
  return { ...body, mainSchemaVersion: schemaVersions(database).main,
    sha256: hashJson(body) };
}

const OWNED_SPECS = Object.freeze([
  {
    name: 'eva_work_completion_contracts_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      contract_ref TEXT NOT NULL UNIQUE,
      contract_sha256 TEXT NOT NULL UNIQUE,
      work_item_id INTEGER NOT NULL,
      episode_id INTEGER NOT NULL,
      generation INTEGER NOT NULL,
      supersedes_ref TEXT,
      source_timeline_id INTEGER NOT NULL,
      plan_sha256 TEXT NOT NULL,
      body_json TEXT NOT NULL,
      UNIQUE(work_item_id,episode_id,generation)`,
    unique: [['contract_ref'], ['contract_sha256'], [
      'work_item_id', 'episode_id', 'generation',
    ]],
  },
  {
    name: 'eva_work_completion_execution_bindings_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      binding_ref TEXT NOT NULL UNIQUE,
      binding_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL,
      execution_key TEXT NOT NULL,
      receipt_id TEXT NOT NULL,
      requirement_id TEXT NOT NULL,
      lane TEXT NOT NULL,
      execution_type TEXT,
      execution_sequence INTEGER NOT NULL,
      intent_record_sha256 TEXT NOT NULL,
      body_json TEXT NOT NULL,
      UNIQUE(contract_ref,execution_key),
      UNIQUE(contract_ref,receipt_id)`,
    unique: [['binding_ref'], ['binding_sha256'], ['contract_ref', 'execution_key'],
      ['contract_ref', 'receipt_id']],
  },
  {
    name: 'eva_work_completion_execution_observations_v3',
    columns: `observation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      observation_ref TEXT NOT NULL UNIQUE,
      observation_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL,
      execution_key TEXT NOT NULL,
      receipt_id TEXT NOT NULL,
      status TEXT NOT NULL,
      row_sha256 TEXT NOT NULL,
      body_json TEXT NOT NULL,
      UNIQUE(contract_ref,execution_key,row_sha256)`,
    unique: [['observation_ref'], ['observation_sha256'], [
      'contract_ref', 'execution_key', 'row_sha256',
    ]],
  },
  {
    name: 'eva_work_completion_proof_ready_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      proof_ready_ref TEXT NOT NULL UNIQUE,
      proof_ready_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL UNIQUE,
      body_json TEXT NOT NULL`,
    unique: [['proof_ready_ref'], ['proof_ready_sha256'], ['contract_ref']],
  },
  {
    name: 'eva_work_completion_claims_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      claim_ref TEXT NOT NULL UNIQUE,
      claim_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL,
      claim_generation INTEGER NOT NULL,
      claimant_sha256 TEXT NOT NULL,
      fence_token_sha256 TEXT NOT NULL UNIQUE,
      execution_key TEXT NOT NULL UNIQUE,
      lease_started_at TEXT NOT NULL,
      lease_until TEXT NOT NULL,
      supersedes_claim_ref TEXT,
      body_json TEXT NOT NULL,
      UNIQUE(contract_ref,claim_generation)`,
    unique: [['claim_ref'], ['claim_sha256'], ['fence_token_sha256'],
      ['execution_key'], ['contract_ref', 'claim_generation']],
  },
  {
    name: 'eva_work_completion_settlements_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      settlement_ref TEXT NOT NULL UNIQUE,
      settlement_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL UNIQUE,
      claim_ref TEXT NOT NULL UNIQUE,
      body_json TEXT NOT NULL`,
    unique: [['settlement_ref'], ['settlement_sha256'], ['contract_ref'], ['claim_ref']],
  },
  {
    name: 'eva_work_completion_receipts_v3',
    columns: `id INTEGER PRIMARY KEY AUTOINCREMENT,
      receipt_ref TEXT NOT NULL UNIQUE,
      receipt_sha256 TEXT NOT NULL UNIQUE,
      contract_ref TEXT NOT NULL UNIQUE,
      settlement_ref TEXT NOT NULL UNIQUE,
      body_json TEXT NOT NULL`,
    unique: [['receipt_ref'], ['receipt_sha256'], ['contract_ref'], ['settlement_ref']],
  },
]);

function ownedTableSql(spec) {
  return `CREATE TABLE main.${spec.name} (${spec.columns})`;
}

function ownedTriggerSql(spec, operation) {
  return `CREATE TRIGGER main.${spec.name}_no_${operation}
    BEFORE ${operation.toUpperCase()} ON ${spec.name}
    BEGIN SELECT RAISE(ABORT,'${spec.name} is append-only'); END`;
}

function ownedCollisionTriggerSql(spec) {
  const collision = spec.unique.map((columns) => `(${columns.map((column) => (
    `NEW.${column}=${column}`
  )).join(' AND ')})`).join(' OR ');
  return `CREATE TRIGGER main.${spec.name}_no_replace
    BEFORE INSERT ON ${spec.name}
    WHEN EXISTS (SELECT 1 FROM ${spec.name} WHERE ${collision})
    BEGIN SELECT RAISE(ABORT,'${spec.name} is append-only'); END`;
}

function expectedOwnedObjects() {
  const expected = new Map();
  for (const spec of OWNED_SPECS) {
    expected.set(spec.name, ['table', normalizeSql(ownedTableSql(spec))]);
    for (const operation of ['update', 'delete']) {
      expected.set(`${spec.name}_no_${operation}`, [
        'trigger', normalizeSql(ownedTriggerSql(spec, operation)),
      ]);
    }
    expected.set(`${spec.name}_no_replace`, [
      'trigger', normalizeSql(ownedCollisionTriggerSql(spec)),
    ]);
  }
  return expected;
}

const EXPECTED_OWNED_OBJECTS = expectedOwnedObjects();

function initializeOwnedSchema(database) {
  const existing = database.prepare(`SELECT name FROM main.sqlite_master
    WHERE name LIKE 'eva_work_completion_%_v3%'
      OR tbl_name LIKE 'eva_work_completion_%_v3%'`).all();
  if (existing.length) return;
  database.transaction(() => {
    for (const spec of OWNED_SPECS) {
      database.exec(ownedTableSql(spec));
      database.exec(ownedTriggerSql(spec, 'update'));
      database.exec(ownedTriggerSql(spec, 'delete'));
      database.exec(ownedCollisionTriggerSql(spec));
    }
  }).immediate();
}

function ownedSchemaProblem(database) {
  const rows = database.prepare(`SELECT type,name,sql FROM main.sqlite_master
    WHERE (name LIKE 'eva_work_completion_%_v3%'
      OR tbl_name LIKE 'eva_work_completion_%_v3%')
      AND type IN ('table','trigger') ORDER BY name`).all();
  if (rows.length !== EXPECTED_OWNED_OBJECTS.size) {
    return 'owned v3 schema is incomplete or has an unexpected object';
  }
  for (const row of rows) {
    const expected = EXPECTED_OWNED_OBJECTS.get(row.name);
    if (!expected || row.type !== expected[0]
      || normalizeSql(row.sql) !== expected[1]) return `owned schema mismatch: ${row.name}`;
  }
  for (const spec of OWNED_SPECS) {
    const actual = database.pragma(`main.index_list('${spec.name}')`)
      .map((index) => database.pragma(`main.index_xinfo('${index.name}')`)
        .filter((entry) => Number(entry.key) === 1).map(({ name }) => name));
    const sorted = actual.map((entry) => canonicalJson(entry)).sort();
    const expected = spec.unique.map((entry) => canonicalJson(entry)).sort();
    if (canonicalJson(sorted) !== canonicalJson(expected)) {
      return `owned index manifest mismatch: ${spec.name}`;
    }
  }
  return null;
}

function tempShadow(database) {
  const protectedNames = new Set([
    ...DEPENDENCY_TABLES,
    ...Object.keys(HANDOFF_TRIGGERS),
    ...OWNED_SPECS.map(({ name }) => name),
  ]);
  return database.prepare(`SELECT name,tbl_name FROM temp.sqlite_master
    ORDER BY type,name`).all().find((row) => (
    protectedNames.has(row.name) || protectedNames.has(row.tbl_name)
  )) || null;
}

function queryRow(database, table, column, value) {
  return database.prepare(`SELECT * FROM main.${table} WHERE ${column}=?`).get(value) || null;
}

function insertBody(database, table, columns, values) {
  return database.prepare(`INSERT INTO main.${table} (${columns.join(',')})
    VALUES (${columns.map(() => '?').join(',')})`).run(...values);
}

function rowReceipt(row) {
  return { row: clone(row), sha256: hashJson(row) };
}

function retryOrigin(episode) {
  return {
    channel: String(episode.origin_channel || ''),
    conversationId: String(episode.origin_conversation_id || ''),
    messageId: String(episode.origin_message_id || ''),
    requester: String(episode.requester || ''),
    deliveryPolicy: String(episode.delivery_policy || ''),
    deliveryTarget: String(episode.delivery_target || ''),
  };
}

function retryOriginSha256(episode) {
  return sha256(JSON.stringify(retryOrigin(episode)));
}

function exactHandoffIdentity(row, retry, work, episode) {
  if (!row || row.contract !== 'ChatInterpretationRetry/v1'
    || !validSha(row.handoff_key) || !validSha(row.binding_sha256)
    || row.work_item_id !== work.id || row.episode_id !== episode.id
    || row.origin_channel !== episode.origin_channel
    || row.origin_conversation_id !== episode.origin_conversation_id
    || row.origin_message_id !== episode.origin_message_id
    || row.requester_sha256 !== sha256(String(episode.requester || ''))
    || row.raw_original_text_sha256 !== retry.rawOriginalTextSha256
    || row.original_text_sha256 !== retry.originalTextSha256
    || row.supersession_scope_sha256 !== sha256(retry.supersessionScope)
    || row.supersession_token_sha256 !== sha256(retry.supersessionToken)) return false;
  const expectedHandoff = hashJson({
    contract: row.contract,
    channel: row.origin_channel,
    conversationId: row.origin_conversation_id,
    messageId: row.origin_message_id,
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
  });
  const expectedBinding = hashJson({
    handoffKey: row.handoff_key,
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
    requesterSha256: row.requester_sha256,
    supersessionScopeSha256: row.supersession_scope_sha256,
    supersessionTokenSha256: row.supersession_token_sha256,
    workItemId: work.id,
    episodeId: episode.id,
  });
  return row.handoff_key === expectedHandoff && row.binding_sha256 === expectedBinding;
}

function validExecutionIntent(row, expected = null) {
  if (!row || row.execution_verification !== 'verified'
    || !validText(row.execution_key, 512) || !validText(row.turn_id, 512)
    || !validText(row.tool, 180) || !validSha(row.semantic_hash)
    || !validSha(row.intent_record_sha256)
    || !positiveInteger(Number(row.execution_seq))) return null;
  let args;
  let binding;
  try {
    args = row.args == null ? null : parseJson(row.args, 'execution args', { canonical: true });
    binding = row.semantic_binding == null
      ? null : parseJson(row.semantic_binding, 'execution binding', { canonical: true });
  } catch { return null; }
  if (expected && (row.turn_id !== expected.turnId || row.tool !== expected.tool
    || canonicalJson(args) !== canonicalJson(expected.args)
    || canonicalJson(binding) !== canonicalJson(expected.binding))) return null;
  const semanticHash = hashJson({
    turnId: row.turn_id, tool: row.tool, args, binding,
  });
  if (row.semantic_hash !== semanticHash) return null;
  const intentBody = {
    turnId: row.turn_id, tool: row.tool, args, binding,
    semanticHash: row.semantic_hash,
  };
  const intentRecordSha256 = row.execution_contract === 'ExecutionRecord/v1'
    ? hashJson(intentBody)
    : row.execution_contract === 'ExecutionRecord/v2'
      ? hashJson({
        executionContract: row.execution_contract,
        executionSeq: Number(row.execution_seq),
        ...intentBody,
      })
      : null;
  return row.intent_record_sha256 === intentRecordSha256
    ? { row, args, binding } : null;
}

function validExecutionOutput(row) {
  const intent = validExecutionIntent(row);
  if (!intent || row.status !== 'done' || typeof row.output !== 'string'
    || !validSha(row.output_sha256) || row.output_sha256 !== sha256(row.output)
    || !validSha(row.completion_record_sha256)
    || !Number.isFinite(Date.parse(row.started_ts))
    || !Number.isFinite(Date.parse(row.finished_ts))) return null;
  let output;
  try { output = JSON.parse(row.output); } catch { return null; }
  const candidateMaterial = output?.data?.material_sha256
    || output?.artifact_receipt?.sha256 || null;
  const materialSha256 = validSha(candidateMaterial) ? candidateMaterial : null;
  if ((row.material_sha256 || null) !== materialSha256) return null;
  const completionBody = {
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  };
  const completionRecordSha256 = row.execution_contract === 'ExecutionRecord/v1'
    ? hashJson(completionBody)
    : row.execution_contract === 'ExecutionRecord/v2'
      ? hashJson({
        executionContract: row.execution_contract,
        executionSeq: Number(row.execution_seq),
        ...completionBody,
      })
      : null;
  return row.completion_record_sha256 === completionRecordSha256
    ? { ...intent, output } : null;
}

function validIngressExecution(database, origin, retry) {
  if (!retry.supersessionScope || !retry.supersessionToken) return null;
  const identity = {
    channel: origin.channel,
    conversationId: origin.conversationId,
    messageId: origin.messageId,
  };
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  const row = queryRow(database, 'turn_tool_executions', 'execution_key', executionKey);
  const turnAuthority = turnUnderstandingAuthorityRecord(database, row);
  const intent = validExecutionIntent(row);
  const completed = validExecutionOutput(row);
  const prepared = completed?.output;
  if (!turnAuthority || !intent || !completed || row.tool !== '@turn/understanding'
    || row.turn_id !== `ingress:${origin.channel}:${origin.conversationId}:${origin.messageId}`
    || !plainObject(intent.args)
    || intent.args.channel !== origin.channel
    || intent.args.conversationId !== origin.conversationId
    || String(intent.args.messageId || '') !== origin.messageId
    || intent.args.requester !== origin.requester
    || intent.args.originalText !== retry.originalText
    || intent.args.syntheticTest !== false
    || intent.args.supersessionScope !== retry.supersessionScope
    || intent.args.supersessionToken !== retry.supersessionToken
    || !validText(intent.args.sessionRef, 512)
    || prepared?.contract !== 'PreparedUnderstandingTurn/v1'
    || prepared.channel !== origin.channel
    || prepared.conversationId !== origin.conversationId
    || prepared.originMessageId !== origin.messageId
    || prepared.workItemId == null || prepared.goalEpisodeId == null
    || prepared.syntheticTest !== false) return null;
  const candidates = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding'
      AND json_valid(args)
      AND json_extract(args,'$.supersessionScope')=?
    ORDER BY execution_seq DESC`).all(retry.supersessionScope);
  const latest = candidates.find((candidate) => (
    candidate.status === 'done'
      && turnUnderstandingAuthorityRecord(database, candidate)
      && validExecutionOutput(candidate)
  ));
  if (!latest) return null;
  let latestArgs;
  try { latestArgs = JSON.parse(latest.args); } catch { return null; }
  return latestArgs.supersessionToken === retry.supersessionToken
    ? { row: rowReceipt(row), args: clone(intent.args), prepared: clone(prepared) } : null;
}

function currentSourceAuthority(database, work, episode, retry) {
  const origin = retryOrigin(episode);
  const ingress = validIngressExecution(database, origin, retry);
  if (!ingress) return null;
  const timelines = database.prepare(`SELECT * FROM main.timeline
    WHERE kind='turn_understanding' AND json_valid(meta)
      AND json_extract(meta,'$.conversationId')=?
      AND json_extract(meta,'$.interpretationSucceeded')=1
    ORDER BY id DESC LIMIT 2`).all(origin.conversationId);
  if (!timelines.length) return null;
  const timeline = timelines[0];
  let meta;
  try { meta = JSON.parse(timeline.meta); } catch { return null; }
  const record = meta.understanding;
  if (!validTurnUnderstandingRecord(record)
    || record.sourceSha256 !== retry.understandingSha256
    || record.sourceTextSha256 !== retry.originalTextSha256
    || !validText(meta.turnId, 512)
    || meta.conversationId !== origin.conversationId
    || meta.interpretationSucceeded !== true) return null;
  const messages = database.prepare(`SELECT * FROM main.messages
    WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 2`)
    .all(origin.conversationId);
  const message = messages[0];
  if (!message || message.channel !== origin.channel
    || message.sender !== origin.requester
    || sha256(message.content) !== record.sourceTextSha256) return null;
  if (messages[1]?.id > message.id || timelines[1]?.id > timeline.id) return null;
  return {
    timeline: rowReceipt(timeline), message: rowReceipt(message),
    understanding: clone(record), turnId: meta.turnId, ingress,
  };
}

function normalizedInterpretationRetry(raw) {
  if (!plainObject(raw)) return null;
  return {
    contract: raw.contract,
    handoffKey: String(raw.handoffKey || ''),
    rawOriginalTextSha256: String(raw.rawOriginalTextSha256 || ''),
    originalText: typeof raw.originalText === 'string' ? raw.originalText : '',
    originalTextSha256: String(raw.originalTextSha256 || ''),
    originSha256: String(raw.originSha256 || ''),
    createdAt: String(raw.createdAt || ''), deadlineAt: String(raw.deadlineAt || ''),
    attempts: Number(raw.attempts), phase: String(raw.phase || ''),
    planSha256: String(raw.planSha256 || ''),
    understandingSha256: String(raw.understandingSha256 || ''),
    semanticAuthoritySha256: String(raw.semanticAuthoritySha256 || ''),
    materialAuthoritySha256: String(raw.materialAuthoritySha256 || ''),
    supersessionScope: String(raw.supersessionScope || ''),
    supersessionToken: String(raw.supersessionToken || ''),
    supersessionSha256: String(raw.supersessionSha256 || ''),
  };
}

function invalidInterpretationRetry(retry, episode, planSeal, {
  createdMs, deadlineMs, nowMs,
}) {
  return retry.contract !== 'ChatInterpretationRetry/v1'
    || !validSha(retry.handoffKey) || !validSha(retry.rawOriginalTextSha256)
    || !retry.originalText.trim() || sha256(retry.originalText) !== retry.originalTextSha256
    || retry.originSha256 !== retryOriginSha256(episode)
    || !Number.isFinite(createdMs) || !Number.isFinite(deadlineMs)
    || deadlineMs !== createdMs + RETRY_TTL_MS
    || nowMs < createdMs || nowMs >= deadlineMs
    || !nonnegativeInteger(retry.attempts) || retry.attempts >= RETRY_MAX_ATTEMPTS
    || retry.phase !== 'understood' || retry.planSha256 !== planSeal.sha256
    || !validSha(retry.understandingSha256)
    || !validSha(retry.semanticAuthoritySha256)
    || !validSha(retry.materialAuthoritySha256)
    || !retry.supersessionScope || !retry.supersessionToken
    || sha256(`${retry.supersessionScope}\n${retry.supersessionToken}`)
      !== retry.supersessionSha256;
}

function retryAuthority(database, workItemId, episodeId, planSeal, {
  requireExecuting = true, expectedWorkRevision = null,
} = {}) {
  const work = queryRow(database, 'eva_work_items', 'id', workItemId);
  const episode = queryRow(database, 'eva_goal_episodes', 'id', episodeId);
  if (!work || !episode || episode.work_item_id !== work.id
    || (requireExecuting && (work.status !== 'open' || episode.state !== 'executing'))
    || ['abandoned'].includes(episode.state)) return null;
  let state;
  try { state = JSON.parse(String(work.state_json || '')); } catch { return null; }
  const raw = state?.interpretation_retry;
  const retry = normalizedInterpretationRetry(raw);
  if (!retry) return null;
  const createdMs = Date.parse(retry.createdAt);
  const deadlineMs = Date.parse(retry.deadlineAt);
  const nowMs = Date.parse(sqliteNow(database));
  if (invalidInterpretationRetry(retry, episode, planSeal, {
    createdMs, deadlineMs, nowMs,
  })) return null;
  const handoff = queryRow(database, 'eva_interpretation_retry_handoffs',
    'handoff_key', retry.handoffKey);
  if (!exactHandoffIdentity(handoff, retry, work, episode)) return null;
  const origins = database.prepare(`SELECT * FROM main.eva_episode_origins
    WHERE episode_id=? AND origin_kind='user'
      AND coalesce(origin_channel,'')=?
      AND coalesce(origin_conversation_id,'')=?
      AND coalesce(origin_message_id,'')=? ORDER BY id`).all(
    episode.id, episode.origin_channel, episode.origin_conversation_id,
    episode.origin_message_id,
  );
  if (origins.length !== 1 || origins[0].requester !== episode.requester) return null;
  const source = currentSourceAuthority(database, work, episode, retry);
  const projectionWork = expectedWorkRevision === null
    ? work : { ...work, revision: expectedWorkRevision };
  const projection = source && productionProjection(
    source.understanding, planSeal, projectionWork,
  );
  if (!source || !projection
    || source.ingress.prepared.workItemId !== work.id
    || source.ingress.prepared.goalEpisodeId !== episode.id) return null;
  const latestEvent = database.prepare(`SELECT * FROM main.eva_episode_events
    WHERE episode_id=? ORDER BY id DESC LIMIT 1`).get(episode.id);
  if (!latestEvent) return null;
  return {
    contract: 'WorkCompletionAuthoritySnapshot/v3',
    work: rowReceipt(work), episode: rowReceipt(episode),
    episodeEventHead: rowReceipt(latestEvent), origin: rowReceipt(origins[0]),
    handoff: rowReceipt(handoff), retry: clone(retry), source,
    planSeal: projection.seal, proofPlans: projection.proofPlans,
    settlementPlan: projection.settlementPlan,
  };
}

function contractRow(database, ref) {
  return database.prepare(`SELECT * FROM main.eva_work_completion_contracts_v3
    WHERE contract_ref=?`).get(ref) || null;
}

function contractValue(database, ref) {
  const row = contractRow(database, ref);
  if (!row) return null;
  const body = parseJson(row.body_json, 'completion contract', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionContract/v3'
    || row.contract_sha256 !== digest
    || row.contract_ref !== `work-completion-contract-v3:${digest}`
    || body.workItemId !== row.work_item_id || body.episodeId !== row.episode_id
    || body.generation !== row.generation || body.supersedesContractRef !== row.supersedes_ref
    || body.source.timeline.row.id !== row.source_timeline_id
    || body.planSealSha256 !== row.plan_sha256
    || body.authoritySnapshotSha256 !== hashJson(body.authoritySnapshot)
    || body.planSealSha256 !== body.planSeal.sha256) {
    throw new Error('invalid durable work completion contract');
  }
  const rows = database.prepare(`SELECT * FROM main.eva_work_completion_contracts_v3
    WHERE work_item_id=? AND episode_id=? ORDER BY generation`).all(
    body.workItemId, body.episodeId,
  );
  const through = rows.filter((entry) => entry.generation <= body.generation);
  if (through.length !== body.generation || through.some((entry, index) => (
    entry.generation !== index + 1
      || entry.supersedes_ref !== (index ? through[index - 1].contract_ref : null)
  ))) throw new Error('completion generation chain is not contiguous');
  return { ...body, contractRef: row.contract_ref, contractSha256: digest,
    id: Number(row.id) };
}

function latestContract(database, workItemId, episodeId) {
  const row = database.prepare(`SELECT contract_ref FROM
    main.eva_work_completion_contracts_v3 WHERE work_item_id=? AND episode_id=?
    ORDER BY generation DESC LIMIT 1`).get(workItemId, episodeId);
  return row ? contractValue(database, row.contract_ref) : null;
}

function currentContractAuthority(database, contract, { requireExecuting = true } = {}) {
  const latest = latestContract(database, contract.workItemId, contract.episodeId);
  if (!latest || latest.contractRef !== contract.contractRef) {
    throw new Error('completion contract is superseded');
  }
  const authority = retryAuthority(
    database, contract.workItemId, contract.episodeId, contract.planSeal,
    {
      requireExecuting,
      expectedWorkRevision: requireExecuting
        ? null : contract.authoritySnapshot.work.row.revision,
    },
  );
  if (!authority
    || authority.source.timeline.row.id !== contract.source.timeline.row.id
    || authority.source.understanding.sourceSha256
      !== contract.source.understanding.sourceSha256
    || authority.retry.handoffKey !== contract.authoritySnapshot.retry.handoffKey
    || authority.retry.supersessionToken
      !== contract.authoritySnapshot.retry.supersessionToken) {
    throw new Error('completion authority is stale, expired or superseded');
  }
  return authority;
}

function classifyExecution(contract, row, latestClaim = null) {
  const intent = validExecutionIntent(row);
  const binding = intent?.binding;
  if (!intent || row.turn_id !== contract.executionTurnId
    || !exactKeys(binding, BINDING_KEYS)
    || binding.understandingSha256 !== contract.understandingSha256
    || !REQUIREMENT_ID.test(String(binding.requirementId || ''))
    || !exactStringList(binding.addressesRequirementIds, {
      pattern: REQUIREMENT_ID, allowEmpty: false, maximum: 12,
    }) || !binding.addressesRequirementIds.includes(binding.requirementId)
    || binding.addressesRequirementIds.some((id) => (
      !contract.planSeal.decision.requirementIds.includes(id)
    ))
    || !exactStringList(binding.basisReceiptIds, {
      pattern: RECEIPT_ID, maximum: 64,
    })) return null;
  const plan = contract.planSeal.plans.find((candidate) => (
    candidate.requirementId === binding.requirementId
  ));
  if (!plan) return null;
  const direct = plan.execution.mode === 'direct'
    && binding.role === 'primary' && binding.executionType === null
    && row.tool === plan.toolName
    && canonicalJson(intent.args) === canonicalJson(plan.args)
    && binding.capabilityId === plan.capabilityId
    && binding.operationId === plan.operationId;
  if (direct) {
    const settlement = plan.requirementId === contract.settlementPlan.requirementId;
    if (settlement && (!latestClaim || row.execution_key !== latestClaim.executionKey)) {
      return null;
    }
    return { plan, lane: settlement ? 'settlement' : 'direct',
      executionType: null, intent };
  }
  if (plan.execution.mode !== 'iterative'
    || !['proof', 'recovery'].includes(binding.role)
    || typeof binding.executionType !== 'string') return null;
  const references = binding.role === 'proof'
    ? plan.proofOperations : plan.recoveryOperations;
  const reference = references.find((candidate) => (
    candidate.capabilityId === binding.capabilityId
      && candidate.operationId === binding.operationId
      && candidate.type === binding.executionType
  ));
  if (!reference) return null;
  const expectedTool = OPERATION_TOOL.get(
    `${reference.capabilityId}\0${reference.operationId}`,
  );
  if (!expectedTool || row.tool !== expectedTool) return null;
  const targetField = plan.execution.target?.field;
  const target = targetField ? plan.args[targetField] : null;
  if (reference.type === 'write'
    && (intent.args?.[targetField] !== target
      || (Object.hasOwn(plan.args, 'inhoud')
        && intent.args?.inhoud !== plan.args.inhoud))) return null;
  if (reference.type === 'readback'
    && canonicalJson(intent.args) !== canonicalJson({ [targetField]: target })) return null;
  return { plan, lane: binding.role, executionType: binding.executionType,
    reference, intent };
}

function bindingValue(row) {
  const body = parseJson(row.body_json, 'execution binding', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionExecutionBinding/v3'
    || row.binding_sha256 !== digest
    || row.binding_ref !== `work-completion-execution-binding-v3:${digest}`
    || row.contract_ref !== body.completionContractRef
    || row.execution_key !== body.executionKey
    || row.receipt_id !== body.receiptId
    || row.requirement_id !== body.requirementId
    || row.lane !== body.lane
    || (row.execution_type || null) !== (body.executionType || null)
    || row.execution_sequence !== body.executionSequence
    || row.intent_record_sha256 !== body.intentRecordSha256) {
    throw new Error('invalid durable execution binding');
  }
  return { ...body, bindingRef: row.binding_ref,
    bindingSha256: digest, id: Number(row.id) };
}

function observationValue(row) {
  const body = parseJson(row.body_json, 'execution observation', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionExecutionObservation/v3'
    || row.observation_sha256 !== digest
    || row.observation_ref !== `work-completion-execution-observation-v3:${digest}`
    || row.contract_ref !== body.completionContractRef
    || row.execution_key !== body.executionKey || row.receipt_id !== body.receiptId
    || row.status !== body.status || row.row_sha256 !== body.rowSha256) {
    throw new Error('invalid durable execution observation');
  }
  return { ...body, observationRef: row.observation_ref,
    observationSha256: digest, observationSequence: Number(row.observation_sequence) };
}

function latestClaimValue(database, contractRef) {
  const row = database.prepare(`SELECT * FROM main.eva_work_completion_claims_v3
    WHERE contract_ref=? ORDER BY claim_generation DESC LIMIT 1`).get(contractRef);
  if (!row) return null;
  const body = parseJson(row.body_json, 'settlement claim', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionSettlementClaim/v3'
    || row.claim_sha256 !== digest || row.claim_ref !== `work-completion-claim-v3:${digest}`
    || row.contract_ref !== body.completionContractRef
    || row.claim_generation !== body.claimGeneration
    || row.claimant_sha256 !== body.claimantSha256
    || row.fence_token_sha256 !== body.fenceTokenSha256
    || row.execution_key !== body.executionKey
    || row.lease_started_at !== body.leaseStartedAt
    || row.lease_until !== body.leaseUntil
    || row.supersedes_claim_ref !== body.supersedesClaimRef) {
    throw new Error('invalid durable settlement claim');
  }
  return { ...body, claimRef: row.claim_ref, claimSha256: digest, id: Number(row.id) };
}

function syncExecutionLedger(database, contract) {
  const latestClaim = latestClaimValue(database, contract.contractRef);
  const rows = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE turn_id=? ORDER BY execution_seq`).all(
    contract.executionTurnId,
  );
  const existingRows = database.prepare(`SELECT * FROM
    main.eva_work_completion_execution_bindings_v3 WHERE contract_ref=? ORDER BY id`)
    .all(contract.contractRef);
  const existingByKey = new Map(existingRows.map((row) => [row.execution_key,
    bindingValue(row)]));
  let nextReceipt = existingRows.length + 1;
  for (const row of rows) {
    const classification = classifyExecution(contract, row, latestClaim);
    const relatedBinding = (() => {
      try { return row.semantic_binding ? JSON.parse(row.semantic_binding) : null; }
      catch { return null; }
    })();
    const related = relatedBinding?.understandingSha256 === contract.understandingSha256
      && contract.planSeal.decision.requirementIds.includes(
        relatedBinding?.requirementId,
      );
    if (!classification) {
      if (related) throw new Error(`unrecognized governed execution ${row.execution_key}`);
      continue;
    }
    let binding = existingByKey.get(row.execution_key);
    if (!binding) {
      const body = {
        contract: 'WorkCompletionExecutionBinding/v3',
        completionContractRef: contract.contractRef,
        executionKey: row.execution_key,
        receiptId: `s${nextReceipt}`,
        requirementId: classification.plan.requirementId,
        lane: classification.lane,
        executionType: classification.executionType,
        executionSequence: Number(row.execution_seq),
        intentRecordSha256: row.intent_record_sha256,
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_execution_bindings_v3', [
        'binding_ref', 'binding_sha256', 'contract_ref', 'execution_key',
        'receipt_id', 'requirement_id', 'lane', 'execution_type',
        'execution_sequence',
        'intent_record_sha256', 'body_json',
      ], [`work-completion-execution-binding-v3:${digest}`, digest,
        contract.contractRef, row.execution_key, body.receiptId,
        body.requirementId, body.lane, body.executionType, body.executionSequence,
        body.intentRecordSha256, canonicalJson(body)]);
      binding = body;
      existingByKey.set(row.execution_key, body);
      nextReceipt += 1;
    } else if (binding.executionSequence !== Number(row.execution_seq)
      || binding.intentRecordSha256 !== row.intent_record_sha256
      || binding.requirementId !== classification.plan.requirementId
      || binding.lane !== classification.lane
      || (binding.executionType || null) !== (classification.executionType || null)) {
      throw new Error('governed execution identity changed');
    }
    const rowBody = { ...row };
    const observationBody = {
      contract: 'WorkCompletionExecutionObservation/v3',
      completionContractRef: contract.contractRef,
      executionKey: row.execution_key,
      receiptId: binding.receiptId,
      status: row.status,
      rowSha256: hashJson(rowBody),
    };
    const digest = hashJson(observationBody);
    const existingObservation = database.prepare(`SELECT * FROM
      main.eva_work_completion_execution_observations_v3
      WHERE contract_ref=? AND execution_key=? AND row_sha256=?`).get(
      contract.contractRef, row.execution_key, observationBody.rowSha256,
    );
    if (existingObservation) {
      observationValue(existingObservation);
    } else {
      database.prepare(`INSERT INTO
        main.eva_work_completion_execution_observations_v3
        (observation_ref,observation_sha256,contract_ref,execution_key,receipt_id,
         status,row_sha256,body_json) VALUES (?,?,?,?,?,?,?,?)`).run(
        `work-completion-execution-observation-v3:${digest}`, digest,
        contract.contractRef, row.execution_key, binding.receiptId, row.status,
        observationBody.rowSha256, canonicalJson(observationBody),
      );
    }
  }
  return database.prepare(`SELECT * FROM
    main.eva_work_completion_execution_bindings_v3 WHERE contract_ref=? ORDER BY id`)
    .all(contract.contractRef).map((row) => bindingValue(row));
}

function actionResultState(execution) {
  const result = execution.output;
  const complete = result?.contract === 'ActionResult/v1'
    && result.tool === execution.row.tool
    && result.ok === true && result.verified === true
    && result.needs_followup === false && result.partial === false
    && result.effect_uncertain !== true;
  const safeFailure = result?.contract === 'ActionResult/v1'
    && result.tool === execution.row.tool && result.ok === false
    && result.verified === false && result.changed === false
    && result.effect_uncertain === false;
  return { complete, safeFailure, result };
}

function currentBoundExecutions(database, contract) {
  const bindings = syncExecutionLedger(database, contract);
  const byReceipt = new Map();
  const values = bindings.map((binding) => {
    const row = queryRow(database, 'turn_tool_executions', 'execution_key',
      binding.executionKey);
    const intent = validExecutionIntent(row);
    if (!intent || row.intent_record_sha256 !== binding.intentRecordSha256) {
      throw new Error('bound execution intent changed');
    }
    const output = row.status === 'done' ? validExecutionOutput(row) : null;
    const value = { binding, row, intent, output,
      receiptNumber: Number(binding.receiptId.slice(1)) };
    byReceipt.set(binding.receiptId, value);
    return value;
  });
  for (const value of values) {
    const basis = value.intent.binding.basisReceiptIds;
    for (const id of basis) {
      const predecessor = byReceipt.get(id);
      if (!predecessor || predecessor.receiptNumber >= value.receiptNumber) {
        throw new Error(`execution ${value.binding.receiptId} has a stale basis receipt`);
      }
    }
    if (value.binding.lane === 'recovery') {
      if (!basis.length || !basis.some((id) => {
        const predecessor = byReceipt.get(id);
        return predecessor?.output
          && actionResultState(predecessor.output).safeFailure;
      })) throw new Error('recovery execution lacks an exact failed basis receipt');
    }
  }
  return values;
}

function successfulExecution(value) {
  if (!value.output) return null;
  const state = actionResultState(value.output);
  return state.complete ? { ...value, result: state.result } : null;
}

function validateDirectPlan(plan, executions) {
  const matches = executions.filter((entry) => (
    entry.binding.requirementId === plan.requirementId
      && entry.binding.lane === 'direct'
  ));
  if (matches.length !== 1) return null;
  const success = successfulExecution(matches[0]);
  if (!success) return null;
  if (plan.effect === 'read' && success.result.changed !== false) return null;
  if (plan.toolName === 'lees_bestand') {
    const receipt = success.result.artifact_receipt;
    if (!receipt || receipt.complete_read !== true
      || receipt.path !== plan.args.pad || !validSha(receipt.sha256)) return null;
  }
  return success;
}

function exactMaterialReadback(plan, successful) {
  const writes = successful.filter((entry) => (
    entry.binding.lane === 'proof' && entry.binding.executionType === 'write'
      && entry.row.tool === 'schrijf_bestand'
  ));
  const readbacks = successful.filter((entry) => (
    entry.binding.lane === 'proof' && entry.binding.executionType === 'readback'
      && entry.row.tool === 'lees_bestand'
  ));
  if (!writes.length || !readbacks.length) return null;
  const targetField = plan.execution.target.field;
  const target = plan.args[targetField];
  const write = [...writes].reverse().find((candidate) => (
    candidate.intent.args?.[targetField] === target
      && validSha(candidate.result.data?.material_sha256)
      && candidate.result.data.material_sha256
        === sha256(String(candidate.intent.args?.inhoud ?? ''))
  ));
  if (!write) return null;
  const readback = readbacks.find((candidate) => (
    candidate.receiptNumber > write.receiptNumber
      && candidate.intent.args?.[targetField] === target
      && candidate.result.artifact_receipt?.path === target
      && candidate.result.artifact_receipt?.complete_read === true
      && candidate.result.artifact_receipt?.sha256
        === write.result.data.material_sha256
      && candidate.result.artifact_receipt?.bytes
        === Buffer.byteLength(String(write.intent.args.inhoud), 'utf8')
  ));
  return readback ? { write, readback } : null;
}

function validateIterativePlan(plan, executions) {
  const matches = executions.filter((entry) => (
    entry.binding.requirementId === plan.requirementId
  ));
  if (!matches.length || matches.some((entry) => (
    entry.output && entry.output.output?.effect_uncertain === true
  ))) return null;
  const successful = matches.map(successfulExecution).filter(Boolean);
  for (const reference of plan.proofOperations) {
    if (!successful.some((entry) => entry.binding.lane === 'proof'
      && entry.binding.executionType === reference.type
      && entry.intent.binding.capabilityId === reference.capabilityId
      && entry.intent.binding.operationId === reference.operationId)) return null;
  }
  const material = exactMaterialReadback(plan, successful);
  if ((plan.effect === 'workspace' || plan.proofFloor.includes('material_readback'))
    && !material) return null;
  return {
    matches, successful, material,
    final: successful.at(-1) || null,
  };
}

function proofFloorSatisfied(plan, proof) {
  const successful = proof.successful || [proof];
  return plan.proofFloor.every((floor) => {
    if (floor === 'source_read') {
      return successful.some((entry) => entry.row.tool === 'lees_bestand'
        && entry.result.artifact_receipt?.complete_read === true);
    }
    if (floor === 'accepted_result') return successful.length > 0;
    if (floor === 'verified_effect') {
      return successful.some((entry) => entry.result.verified === true
        && entry.result.effect_uncertain !== true
        && (entry.result.changed === true
          || (entry.row.tool === 'schrijf_bestand'
            && validSha(entry.result.data?.material_sha256))));
    }
    if (floor === 'material_readback') return Boolean(proof.material);
    if (floor === 'root_receipt') {
      return successful.some((entry) => validSha(
        entry.result.data?.receipt_sha256 || entry.result.data?.receipt?.sha256,
      ));
    }
    // Aflevering is geen uitvoeringbewijs. Een proofplan met deze floor kan
    // pas in een latere, nog niet geïntegreerde transportgrens finaliseren.
    return floor !== 'delivery_receipt';
  });
}

function proofSet(database, contract) {
  const executions = currentBoundExecutions(database, contract);
  if (executions.some((entry) => entry.row.status === 'started'
    && entry.binding.lane !== 'settlement')) {
    return { ready: false, reason: 'effect_reconciliation_required', executions };
  }
  const proofs = [];
  const positions = new Map();
  for (const plan of contract.proofPlans) {
    const proof = plan.execution.mode === 'direct'
      ? validateDirectPlan(plan, executions)
      : validateIterativePlan(plan, executions);
    if (!proof || !proofFloorSatisfied(plan, proof)) {
      return { ready: false, reason: `missing_exact_proof:${plan.requirementId}`,
        executions };
    }
    const successful = proof.successful || [proof];
    const final = successful.at(-1);
    positions.set(plan.requirementId, final.receiptNumber);
    proofs.push({
      requirementId: plan.requirementId,
      completionRecordSha256: final.row.completion_record_sha256,
      outputSha256: final.row.output_sha256,
      finalReceiptId: final.binding.receiptId,
      executionKeys: successful.map(({ row }) => row.execution_key),
      materialReadback: proof.material ? {
        writeReceiptId: proof.material.write.binding.receiptId,
        readbackReceiptId: proof.material.readback.binding.receiptId,
        materialSha256: proof.material.write.result.data.material_sha256,
      } : null,
    });
  }
  for (const condition of contract.source.understanding.conditions) {
    if (!['required', 'unmet'].includes(condition.state)
      || !['action', 'completion'].includes(condition.role)) continue;
    for (const requirementId of condition.requirementIds) {
      if (requirementId === contract.settlementPlan.requirementId) continue;
      const own = positions.get(requirementId);
      if (!own || condition.afterRequirementIds.some((prior) => (
        prior !== contract.settlementPlan.requirementId
          && (!positions.get(prior) || positions.get(prior) >= own)
      ))) return { ready: false, reason: 'condition_order_unproven', executions };
    }
  }
  const observationHead = Number(database.prepare(`SELECT coalesce(max(
    observation_sequence),0) AS head FROM
    main.eva_work_completion_execution_observations_v3 WHERE contract_ref=?`)
    .get(contract.contractRef).head);
  return { ready: true, proofs, observationHead, executions };
}

function proofReadyValue(database, contractRef) {
  const row = database.prepare(`SELECT * FROM main.eva_work_completion_proof_ready_v3
    WHERE contract_ref=?`).get(contractRef);
  if (!row) return null;
  const body = parseJson(row.body_json, 'proof ready', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionProofReady/v3'
    || row.proof_ready_sha256 !== digest
    || row.proof_ready_ref !== `work-completion-proof-ready-v3:${digest}`
    || body.completionContractRef !== contractRef) {
    throw new Error('invalid durable proof-ready record');
  }
  const contract = contractValue(database, contractRef);
  const current = proofSet(database, contract);
  if (!current.ready || canonicalJson(current.proofs) !== canonicalJson(body.proofs)
    || current.observationHead < body.observationHead) {
    throw new Error('proof-ready dependencies changed');
  }
  return { ...body, proofReadyRef: row.proof_ready_ref,
    proofReadySha256: digest, id: Number(row.id) };
}

function settlementValue(database, contractRef) {
  const row = database.prepare(`SELECT * FROM main.eva_work_completion_settlements_v3
    WHERE contract_ref=?`).get(contractRef);
  if (!row) return null;
  const body = parseJson(row.body_json, 'settlement result', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionSettlementResult/v3'
    || row.settlement_sha256 !== digest
    || row.settlement_ref !== `work-completion-settlement-v3:${digest}`
    || row.claim_ref !== body.claimRef
    || row.contract_ref !== body.completionContractRef) {
    throw new Error('invalid durable settlement result');
  }
  const claim = latestClaimValue(database, contractRef);
  const work = queryRow(database, 'eva_work_items', 'id', body.workItemId);
  const episode = queryRow(database, 'eva_goal_episodes', 'id', body.episodeId);
  const execution = queryRow(database, 'turn_tool_executions', 'execution_key',
    body.executionKey);
  if (!claim || claim.claimRef !== body.claimRef
    || hashJson(work) !== body.workRowSha256
    || hashJson(episode) !== body.episodeRowSha256
    || execution?.completion_record_sha256 !== body.completionRecordSha256) {
    throw new Error('settlement result dependencies changed');
  }
  return { ...body, settlementRef: row.settlement_ref,
    settlementSha256: digest, id: Number(row.id) };
}

function validSettlementTransition(database, contract, claim) {
  const execution = queryRow(database, 'turn_tool_executions', 'execution_key',
    claim.executionKey);
  const expected = {
    turnId: contract.executionTurnId,
    tool: contract.settlementPlan.toolName,
    args: contract.settlementPlan.args,
    binding: contract.settlementPlan.semanticBinding,
  };
  const completed = validExecutionOutput(execution);
  const result = completed && actionResultState(completed);
  const work = queryRow(database, 'eva_work_items', 'id', contract.workItemId);
  const episode = queryRow(database, 'eva_goal_episodes', 'id', contract.episodeId);
  const event = database.prepare(`SELECT * FROM main.eva_episode_events
    WHERE episode_id=? ORDER BY id DESC LIMIT 1`).get(contract.episodeId);
  const baselineWork = contract.authoritySnapshot.work.row;
  const baselineEpisode = contract.authoritySnapshot.episode.row;
  const data = completed?.output?.data;
  if (!completed || !validExecutionIntent(execution, expected)
    || !result.complete || completed.output.changed !== true
    || completed.output.effect_uncertain === true
    || data?.proofAccepted !== true || data?.selfReviewInstead !== false
    || canonicalJson(data?.item) !== canonicalJson(work)
    || work.revision !== baselineWork.revision + 1
    || episode.revision !== baselineEpisode.revision + 1
    || work.result !== contract.settlementPlan.args.resultaat
    || event?.episode_id !== episode.id
    || event.id <= contract.authoritySnapshot.episodeEventHead.row.id
    || event.session_ref !== contract.executionTurnId) return null;
  if (contract.deliveryRequired) {
    if (work.status !== 'open' || episode.state !== 'delivery_pending'
      || event.kind !== 'final_delivery_armed') return null;
  } else if (work.status !== 'done' || episode.state !== 'closed'
    || event.kind !== 'state:closed') return null;
  return { execution, completed, work, episode, event };
}

function receiptValue(database, contractRef) {
  const row = database.prepare(`SELECT * FROM main.eva_work_completion_receipts_v3
    WHERE contract_ref=?`).get(contractRef);
  if (!row) return null;
  const body = parseJson(row.body_json, 'completion receipt', { canonical: true });
  const digest = hashJson(body);
  if (body.contract !== 'WorkCompletionReceipt/v3'
    || row.receipt_sha256 !== digest
    || row.receipt_ref !== `work-completion-receipt-v3:${digest}`
    || row.contract_ref !== body.completionContractRef
    || row.settlement_ref !== body.settlementRef) {
    throw new Error('invalid durable completion receipt');
  }
  const contract = contractValue(database, contractRef);
  const settlement = settlementValue(database, contractRef);
  const work = queryRow(database, 'eva_work_items', 'id', contract.workItemId);
  const episode = queryRow(database, 'eva_goal_episodes', 'id', contract.episodeId);
  const latest = latestContract(database, contract.workItemId, contract.episodeId);
  if (!settlement || settlement.settlementRef !== body.settlementRef
    || hashJson(work) !== body.terminalWorkSha256
    || hashJson(episode) !== body.terminalEpisodeSha256) {
    throw new Error('completion receipt dependencies changed');
  }
  return { ...body, receiptRef: row.receipt_ref, receiptSha256: digest,
    current: latest?.contractRef === contractRef,
    id: Number(row.id) };
}

function requestShape(value, keys) {
  return exactKeys(value, keys) ? clone(value) : null;
}

export function createWorkCompletionStore(database) {
  if (!database || typeof database.prepare !== 'function'
    || typeof database.transaction !== 'function') {
    throw new TypeError('createWorkCompletionStore requires a synchronous SQLite database');
  }
  let creationProblem = null;
  let pinnedDependency = null;
  try {
    pinnedDependency = inspectWorkCompletionHostSchema(database);
    initializeOwnedSchema(database);
  } catch (error) { creationProblem = error.message; }
  const capabilities = {
    contract: new WeakMap(), reconcile: new WeakMap(), claim: new WeakMap(),
    execution: new WeakMap(), settlement: new WeakMap(), final: new WeakMap(),
  };
  const executionIntents = new WeakMap();

  function structuralProblem() {
    if (creationProblem) return creationProblem;
    const shadow = tempShadow(database);
    if (shadow) return `TEMP schema shadows protected ${shadow.name}`;
    const owned = ownedSchemaProblem(database);
    if (owned) return owned;
    try {
      const current = inspectWorkCompletionHostSchema(database);
      if (current.sha256 !== pinnedDependency.sha256) {
        return 'production dependency manifest changed';
      }
    } catch (error) { return error.message; }
    return null;
  }

  function requireReady() {
    const problem = structuralProblem();
    if (problem) throw new Error(`work completion v3 unavailable: ${problem}`);
  }

  function status() {
    const reason = structuralProblem();
    return {
      ready: !reason,
      reason,
      contract: 'WorkCompletionStoreStatus/v3',
      // Deze module is opzettelijk nog niet vanuit agent.js bereikbaar.
      productionReachable: false,
      finalDeliveryIntegrated: false,
    };
  }

  function issue(kind, contractName, normalized, validate) {
    requireReady();
    if (!normalized) return null;
    try { validate(normalized); } catch { return null; }
    const capability = Object.freeze({ contract: contractName });
    capabilities[kind].set(capability, {
      canonical: canonicalJson(normalized), validate,
    });
    return capability;
  }

  function consume(kind, capability, contractName, normalized) {
    requireReady();
    const issued = capabilities[kind].get(capability);
    if (!issued || capability?.contract !== contractName) {
      throw new Error('operation requires an exact opaque one-shot capability');
    }
    capabilities[kind].delete(capability);
    if (canonicalJson(normalized) !== issued.canonical) {
      throw new Error('capability request changed before consumption');
    }
    issued.validate(normalized);
    return normalized;
  }

  function normalizedContractRequest(value) {
    const normalized = requestShape(value, ['workItemId', 'episodeId', 'planSeal']);
    if (!normalized || !positiveInteger(normalized.workItemId)
      || !positiveInteger(normalized.episodeId)) return null;
    const seal = normalizePlanSeal(normalized.planSeal, { production: true });
    return seal ? { ...normalized, planSeal: seal } : null;
  }

  function validateContractRequest(value) {
    const authority = retryAuthority(
      database, value.workItemId, value.episodeId, value.planSeal,
    );
    if (!authority) throw new Error('no exact active production completion authority');
  }

  function issueContractCapability(value) {
    const normalized = normalizedContractRequest(value);
    return issue('contract', 'WorkCompletionContractCapability/v3', normalized,
      validateContractRequest);
  }

  function bindContract(capability, value) {
    const normalized = normalizedContractRequest(value);
    return database.transaction(() => {
      const request = consume('contract', capability,
        'WorkCompletionContractCapability/v3', normalized);
      const authority = retryAuthority(
        database, request.workItemId, request.episodeId, request.planSeal,
      );
      const latest = latestContract(database, request.workItemId, request.episodeId);
      const generation = (latest?.generation || 0) + 1;
      if (latest && latest.source.timeline.row.id === authority.source.timeline.row.id
        && latest.planSealSha256 === request.planSeal.sha256) {
        return { kind: 'existing', value: latest };
      }
      const body = {
        contract: 'WorkCompletionContract/v3',
        workItemId: request.workItemId,
        episodeId: request.episodeId,
        generation,
        supersedesContractRef: latest?.contractRef || null,
        source: authority.source,
        understandingSha256: authority.source.understanding.sourceSha256,
        authoritySnapshot: authority,
        authoritySnapshotSha256: hashJson(authority),
        planSeal: request.planSeal,
        planSealSha256: request.planSeal.sha256,
        executionTurnId: authority.source.turnId,
        proofPlans: authority.proofPlans,
        settlementPlan: authority.settlementPlan,
        deliveryRequired: authority.episode.row.delivery_policy === 'required',
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_contracts_v3', [
        'contract_ref', 'contract_sha256', 'work_item_id', 'episode_id',
        'generation', 'supersedes_ref', 'source_timeline_id', 'plan_sha256',
        'body_json',
      ], [`work-completion-contract-v3:${digest}`, digest, body.workItemId,
        body.episodeId, generation, body.supersedesContractRef,
        body.source.timeline.row.id, body.planSealSha256, canonicalJson(body)]);
      return { kind: 'created', value: contractValue(
        database, `work-completion-contract-v3:${digest}`,
      ) };
    }).immediate();
  }

  function normalizedContractRefRequest(value) {
    const normalized = requestShape(value, ['contractRef']);
    return normalized && CONTRACT_REF.test(normalized.contractRef) ? normalized : null;
  }

  function validateReconcile(value) {
    const contract = contractValue(database, value.contractRef);
    if (!contract) throw new Error('unknown completion contract');
    currentContractAuthority(database, contract);
  }

  function issueReconciliationCapability(value) {
    return issue('reconcile', 'WorkCompletionReconciliationCapability/v3',
      normalizedContractRefRequest(value), validateReconcile);
  }

  function reconcile(capability, value) {
    const normalized = normalizedContractRefRequest(value);
    return database.transaction(() => {
      const request = consume('reconcile', capability,
        'WorkCompletionReconciliationCapability/v3', normalized);
      const contract = contractValue(database, request.contractRef);
      currentContractAuthority(database, contract);
      const existing = proofReadyValue(database, contract.contractRef);
      if (existing) return { kind: 'existing', value: existing };
      const proof = proofSet(database, contract);
      if (!proof.ready) return { kind: 'pending', value: {
        contract: 'WorkCompletionProofPending/v3',
        completionContractRef: contract.contractRef,
        reason: proof.reason,
        action: { kind: proof.reason === 'effect_reconciliation_required'
          ? 'reconcile_effect' : 'execute_exact_issued_proof', mayExecute: false },
      } };
      const body = {
        contract: 'WorkCompletionProofReady/v3',
        completionContractRef: contract.contractRef,
        completionContractSha256: contract.contractSha256,
        proofs: proof.proofs,
        proofsSha256: hashJson(proof.proofs),
        observationHead: proof.observationHead,
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_proof_ready_v3', [
        'proof_ready_ref', 'proof_ready_sha256', 'contract_ref', 'body_json',
      ], [`work-completion-proof-ready-v3:${digest}`, digest,
        contract.contractRef, canonicalJson(body)]);
      return { kind: 'created', value: proofReadyValue(database, contract.contractRef) };
    }).immediate();
  }

  function normalizedClaimRequest(value) {
    const normalized = requestShape(value, [
      'contractRef', 'claimantSha256', 'expectedClaimGeneration',
      'requestedLeaseMs',
    ]);
    if (!normalized || !CONTRACT_REF.test(normalized.contractRef)
      || !validSha(normalized.claimantSha256)
      || !nonnegativeInteger(normalized.expectedClaimGeneration)
      || !positiveInteger(normalized.requestedLeaseMs)
      || normalized.requestedLeaseMs > 300_000) return null;
    return normalized;
  }

  function validateClaimRequest(value) {
    const contract = contractValue(database, value.contractRef);
    if (!contract) throw new Error('unknown completion contract');
    currentContractAuthority(database, contract);
    if (!proofReadyValue(database, contract.contractRef)) {
      throw new Error('completion proof is not ready');
    }
    if (settlementValue(database, contract.contractRef)) {
      throw new Error('completion settlement is already durable');
    }
    const latest = latestClaimValue(database, contract.contractRef);
    const nowMs = Date.parse(sqliteNow(database));
    const live = latest && Date.parse(latest.leaseUntil) > nowMs;
    const replay = live && latest.claimantSha256 === value.claimantSha256
      && latest.claimGeneration === value.expectedClaimGeneration + 1;
    if (live && !replay) throw new Error('settlement lease is live');
    if (!replay && (latest?.claimGeneration || 0) !== value.expectedClaimGeneration) {
      throw new Error('settlement claim generation is stale');
    }
  }

  function issueSettlementClaimCapability(value) {
    return issue('claim', 'WorkCompletionSettlementClaimCapability/v3',
      normalizedClaimRequest(value), validateClaimRequest);
  }

  function claimSettlement(capability, value) {
    const normalized = normalizedClaimRequest(value);
    return database.transaction(() => {
      const request = consume('claim', capability,
        'WorkCompletionSettlementClaimCapability/v3', normalized);
      validateClaimRequest(request);
      const contract = contractValue(database, request.contractRef);
      const latest = latestClaimValue(database, contract.contractRef);
      const startedAt = sqliteNow(database);
      if (latest && Date.parse(latest.leaseUntil) > Date.parse(startedAt)
        && latest.claimantSha256 === request.claimantSha256
        && latest.claimGeneration === request.expectedClaimGeneration + 1) {
        return { kind: 'existing', value: latest };
      }
      const generation = (latest?.claimGeneration || 0) + 1;
      const leaseUntil = new Date(
        Date.parse(startedAt) + request.requestedLeaseMs,
      ).toISOString();
      const fenceTokenSha256 = sha256(randomBytes(32));
      const executionKey = sha256(canonicalJson({
        contractRef: contract.contractRef,
        claimGeneration: generation,
        claimantSha256: request.claimantSha256,
        fenceTokenSha256,
        settlementPlanSha256: hashJson(contract.settlementPlan),
      }));
      const body = {
        contract: 'WorkCompletionSettlementClaim/v3',
        completionContractRef: contract.contractRef,
        claimGeneration: generation,
        claimantSha256: request.claimantSha256,
        fenceTokenSha256,
        executionKey,
        leaseStartedAt: startedAt,
        leaseUntil,
        supersedesClaimRef: latest?.claimRef || null,
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_claims_v3', [
        'claim_ref', 'claim_sha256', 'contract_ref', 'claim_generation',
        'claimant_sha256', 'fence_token_sha256', 'execution_key',
        'lease_started_at', 'lease_until', 'supersedes_claim_ref', 'body_json',
      ], [`work-completion-claim-v3:${digest}`, digest, contract.contractRef,
        generation, request.claimantSha256, fenceTokenSha256, executionKey,
        startedAt, leaseUntil, body.supersedesClaimRef, canonicalJson(body)]);
      return { kind: 'created', value: latestClaimValue(database, contract.contractRef) };
    }).immediate();
  }

  function normalizedExecutionRequest(value) {
    const normalized = requestShape(value, ['claimRef', 'claimantSha256']);
    return normalized && CLAIM_REF.test(normalized.claimRef)
      && validSha(normalized.claimantSha256) ? normalized : null;
  }

  function claimByRef(ref) {
    const row = database.prepare(`SELECT contract_ref FROM
      main.eva_work_completion_claims_v3 WHERE claim_ref=?`).get(ref);
    const claim = row ? latestClaimValue(database, row.contract_ref) : null;
    return claim?.claimRef === ref ? claim : null;
  }

  function validateExecutionRequest(value) {
    const claim = claimByRef(value.claimRef);
    if (!claim || claim.claimantSha256 !== value.claimantSha256
      || Date.parse(claim.leaseUntil) <= Date.parse(sqliteNow(database))) {
      throw new Error('settlement execution fence is stale or expired');
    }
    const contract = contractValue(database, claim.completionContractRef);
    currentContractAuthority(database, contract);
  }

  function issueSettlementExecutionCapability(value) {
    return issue('execution', 'WorkCompletionSettlementExecutionCapability/v3',
      normalizedExecutionRequest(value), validateExecutionRequest);
  }

  function consumeSettlementExecutionCapability(capability, value) {
    const normalized = normalizedExecutionRequest(value);
    const request = consume('execution', capability,
      'WorkCompletionSettlementExecutionCapability/v3', normalized);
    const claim = claimByRef(request.claimRef);
    const contract = contractValue(database, claim.completionContractRef);
    const intent = Object.freeze({
      contract: 'WorkCompletionSettlementExecutionIntent/v3',
      claimRef: claim.claimRef,
      claimGeneration: claim.claimGeneration,
      executionKey: claim.executionKey,
      turnId: contract.executionTurnId,
      tool: contract.settlementPlan.toolName,
      args: clone(contract.settlementPlan.args),
      semanticBinding: clone(contract.settlementPlan.semanticBinding),
      leaseUntil: claim.leaseUntil,
      fenceSha256: hashJson({
        claimRef: claim.claimRef, claimGeneration: claim.claimGeneration,
        executionKey: claim.executionKey, fenceTokenSha256: claim.fenceTokenSha256,
      }),
    });
    executionIntents.set(intent, {
      claimRef: claim.claimRef, contractRef: contract.contractRef,
      canonical: canonicalJson(intent),
    });
    return intent;
  }

  function settlementExecutionFenceCurrent(intent) {
    try {
      requireReady();
      const issued = executionIntents.get(intent);
      if (!issued || canonicalJson(intent) !== issued.canonical) return false;
      const claim = claimByRef(issued.claimRef);
      if (!claim || claim.completionContractRef !== issued.contractRef
        || claim.executionKey !== intent.executionKey
        || Date.parse(claim.leaseUntil) <= Date.parse(sqliteNow(database))) return false;
      const contract = contractValue(database, issued.contractRef);
      currentContractAuthority(database, contract);
      return true;
    } catch { return false; }
  }

  function normalizedSettlementRequest(value) {
    const normalized = requestShape(value, ['contractRef', 'claimRef']);
    return normalized && CONTRACT_REF.test(normalized.contractRef)
      && CLAIM_REF.test(normalized.claimRef) ? normalized : null;
  }

  function validateSettlementRequest(value) {
    const contract = contractValue(database, value.contractRef);
    if (!contract) throw new Error('unknown completion contract');
    currentContractAuthority(database, contract, { requireExecuting: false });
    const existing = settlementValue(database, contract.contractRef);
    if (existing) {
      if (existing.claimRef !== value.claimRef) throw new Error('settlement replay conflict');
      return;
    }
    const claim = latestClaimValue(database, contract.contractRef);
    if (!claim || claim.claimRef !== value.claimRef
      || Date.parse(claim.leaseUntil) <= Date.parse(sqliteNow(database))) {
      throw new Error('settlement claim fence is stale or expired');
    }
    if (!validSettlementTransition(database, contract, claim)) {
      throw new Error('settlement execution or work transition is not exact');
    }
  }

  function issueSettlementResultCapability(value) {
    return issue('settlement', 'WorkCompletionSettlementResultCapability/v3',
      normalizedSettlementRequest(value), validateSettlementRequest);
  }

  function recordSettlementResult(capability, value) {
    const normalized = normalizedSettlementRequest(value);
    return database.transaction(() => {
      const request = consume('settlement', capability,
        'WorkCompletionSettlementResultCapability/v3', normalized);
      validateSettlementRequest(request);
      const existing = settlementValue(database, request.contractRef);
      if (existing) return { kind: 'existing', value: existing };
      const contract = contractValue(database, request.contractRef);
      const claim = latestClaimValue(database, request.contractRef);
      const transition = validSettlementTransition(database, contract, claim);
      const body = {
        contract: 'WorkCompletionSettlementResult/v3',
        completionContractRef: contract.contractRef,
        completionContractSha256: contract.contractSha256,
        claimRef: claim.claimRef,
        claimGeneration: claim.claimGeneration,
        executionKey: claim.executionKey,
        completionRecordSha256: transition.execution.completion_record_sha256,
        workItemId: contract.workItemId,
        episodeId: contract.episodeId,
        workRowSha256: hashJson(transition.work),
        episodeRowSha256: hashJson(transition.episode),
        state: contract.deliveryRequired ? 'delivery_proof_pending' : 'terminal',
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_settlements_v3', [
        'settlement_ref', 'settlement_sha256', 'contract_ref', 'claim_ref',
        'body_json',
      ], [`work-completion-settlement-v3:${digest}`, digest,
        contract.contractRef, claim.claimRef, canonicalJson(body)]);
      return { kind: 'created', value: settlementValue(database, contract.contractRef) };
    }).immediate();
  }

  function validateFinalRequest(value) {
    const contract = contractValue(database, value.contractRef);
    if (!contract) throw new Error('unknown completion contract');
    currentContractAuthority(database, contract, { requireExecuting: false });
    const settlement = settlementValue(database, contract.contractRef);
    if (!settlement) throw new Error('settlement result is not durable');
    if (contract.deliveryRequired) {
      throw new Error('durable HTTP client-ACK/Telegram delivery proof is not integrated');
    }
    const work = queryRow(database, 'eva_work_items', 'id', contract.workItemId);
    const episode = queryRow(database, 'eva_goal_episodes', 'id', contract.episodeId);
    if (work.status !== 'done' || episode.state !== 'closed'
      || hashJson(work) !== settlement.workRowSha256
      || hashJson(episode) !== settlement.episodeRowSha256) {
      throw new Error('non-delivery settlement is not terminal');
    }
  }

  function issueFinalCapability(value) {
    return issue('final', 'WorkCompletionFinalCapability/v3',
      normalizedContractRefRequest(value), validateFinalRequest);
  }

  function finalize(capability, value) {
    const normalized = normalizedContractRefRequest(value);
    return database.transaction(() => {
      const request = consume('final', capability,
        'WorkCompletionFinalCapability/v3', normalized);
      validateFinalRequest(request);
      const existing = receiptValue(database, request.contractRef);
      if (existing) return { kind: 'existing', value: existing };
      const contract = contractValue(database, request.contractRef);
      const settlement = settlementValue(database, request.contractRef);
      const work = queryRow(database, 'eva_work_items', 'id', contract.workItemId);
      const episode = queryRow(database, 'eva_goal_episodes', 'id', contract.episodeId);
      const body = {
        contract: 'WorkCompletionReceipt/v3',
        completionContractRef: contract.contractRef,
        completionContractSha256: contract.contractSha256,
        generation: contract.generation,
        proofReadyRef: proofReadyValue(database, contract.contractRef).proofReadyRef,
        settlementRef: settlement.settlementRef,
        terminalWorkSha256: hashJson(work),
        terminalEpisodeSha256: hashJson(episode),
        deliveryResultRef: null,
      };
      const digest = hashJson(body);
      insertBody(database, 'eva_work_completion_receipts_v3', [
        'receipt_ref', 'receipt_sha256', 'contract_ref', 'settlement_ref',
        'body_json',
      ], [`work-completion-receipt-v3:${digest}`, digest,
        contract.contractRef, settlement.settlementRef, canonicalJson(body)]);
      return { kind: 'created', value: receiptValue(database, contract.contractRef) };
    }).immediate();
  }

  function pendingState(contract) {
    const latest = latestContract(database, contract.workItemId, contract.episodeId);
    if (latest?.contractRef !== contract.contractRef) return {
      state: 'superseded', current: false,
      action: { kind: 'none', mayExecute: false },
    };
    if (receiptValue(database, contract.contractRef)) return null;
    const settlement = settlementValue(database, contract.contractRef);
    if (settlement) return contract.deliveryRequired ? {
      state: 'delivery_proof_pending', current: true,
      action: {
        kind: 'await_durable_http_client_ack_or_telegram_reply_proof',
        mayExecute: false,
      },
    } : {
      state: 'settlement_recorded', current: true,
      action: { kind: 'finalize', mayExecute: false },
    };
    const proof = proofReadyValue(database, contract.contractRef);
    if (!proof) {
      const current = proofSet(database, contract);
      return {
        state: current.ready ? 'proof_executed' : current.reason
          === 'effect_reconciliation_required'
          ? 'proof_effect_reconciliation' : 'contract_bound',
        current: true,
        action: { kind: current.ready ? 'persist_proof_ready'
          : current.reason === 'effect_reconciliation_required'
            ? 'reconcile_effect' : 'execute_exact_issued_proof', mayExecute: false },
      };
    }
    const claim = latestClaimValue(database, contract.contractRef);
    if (!claim) return {
      state: 'proof_ready', current: true,
      action: { kind: 'claim_settlement', mayExecute: false },
    };
    const row = queryRow(database, 'turn_tool_executions', 'execution_key',
      claim.executionKey);
    return {
      state: row?.status === 'done' ? 'settlement_executed'
        : row?.status === 'started' ? 'settlement_effect_reconciliation'
          : 'settlement_claimed',
      current: true,
      action: { kind: row?.status === 'done' ? 'record_settlement_result'
        : row?.status === 'started' ? 'reconcile_settlement_effect'
          : 'consume_settlement_execution_fence', mayExecute: false,
        claimRef: claim.claimRef, claimGeneration: claim.claimGeneration },
    };
  }

  function pendingReconciliations() {
    requireReady();
    return database.prepare(`SELECT contract_ref FROM
      main.eva_work_completion_contracts_v3 ORDER BY work_item_id,episode_id,generation`)
      .all().flatMap(({ contract_ref: ref }) => {
        try {
          const contract = contractValue(database, ref);
          const pending = pendingState(contract);
          return pending ? [{ contractRef: ref, generation: contract.generation,
            ...pending }] : [];
        } catch { return []; }
      });
  }

  return Object.freeze({
    status,
    issueContractCapability,
    bindContract,
    issueReconciliationCapability,
    reconcile,
    issueSettlementClaimCapability,
    claimSettlement,
    issueSettlementExecutionCapability,
    consumeSettlementExecutionCapability,
    settlementExecutionFenceCurrent,
    issueSettlementResultCapability,
    recordSettlementResult,
    issueFinalCapability,
    finalize,
    contract: (ref) => { requireReady(); try {
      return CONTRACT_REF.test(String(ref || '')) ? contractValue(database, ref) : null;
    } catch { return null; } },
    proofReady: (ref) => { requireReady(); try {
      return CONTRACT_REF.test(String(ref || '')) ? proofReadyValue(database, ref) : null;
    } catch { return null; } },
    settlementClaim: (ref) => { requireReady(); try {
      return CONTRACT_REF.test(String(ref || '')) ? latestClaimValue(database, ref) : null;
    } catch { return null; } },
    settlementResult: (ref) => { requireReady(); try {
      return CONTRACT_REF.test(String(ref || '')) ? settlementValue(database, ref) : null;
    } catch { return null; } },
    // Geen callback-based delivery-authority: deze blijft bewust fail-disabled.
    deliveryResult: () => null,
    receipt: (ref) => { requireReady(); try {
      return CONTRACT_REF.test(String(ref || '')) ? receiptValue(database, ref) : null;
    } catch { return null; } },
    pendingReconciliations,
  });
}
